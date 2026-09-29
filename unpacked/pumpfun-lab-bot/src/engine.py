from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .callout import PumpFunCalloutPublisher
from .exits import ExitEngine
from .market import MarketClient
from .risk import RiskClient, evaluate_gates
from .trader import SafetyError, Trader
from .telegram_feed import buy_message, sell_message

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, settings, db):
        self.settings = settings
        self.db = db
        self.mode = settings.mode
        self.kill_switch = False
        self.stream = None
        self.telegram = None
        self.market = MarketClient(settings.dexscreener_base_url)
        self.risk = RiskClient(settings.rugcheck_base_url, settings.rugcheck_enabled)
        self.exit_engine = ExitEngine(settings)
        self.callouts = PumpFunCalloutPublisher(settings, db)
        self.trader = Trader(
            settings.solana_rpc_url,
            settings.wallet_private_key_base58,
            settings.max_trade_sol,
            settings.max_position_sol,
            settings.max_open_positions,
            settings.max_daily_loss_sol,
            settings.max_slippage_pct,
            settings.max_priority_fee_sol,
            db,
        )

    def set_stream(self, stream) -> None:
        self.stream = stream

    def set_telegram(self, telegram) -> None:
        self.telegram = telegram

    def status_text(self) -> str:
        return (
            f"MODE={self.mode}\n"
            f"KILL_SWITCH={'ACTIVE' if self.kill_switch else 'ARMED'}\n"
            f"OPEN_POSITIONS={len(self.db.open_positions())}/{self.settings.max_open_positions}\n"
            f"WATCHES={len(self.db.active_watches())}/{self.settings.max_watched_tokens}\n"
            f"24H_REALIZED_PNL={self.db.daily_realized_pnl():.6f} SOL\n"
            f"WALLET={self.trader.public_key or 'NOT CONFIGURED'}"
        )

    async def handle_pump_event(self, event: dict[str, Any]) -> None:
        mint = event.get("mint")
        if not mint:
            return
        tx_type = event.get("txType", "event")
        self.db.journal(mint, f"pumpportal:{tx_type}", event)
        if tx_type == "create":
            await self.on_new_token(event)
        elif tx_type in {"buy", "sell"}:
            await self.on_trade_event(event)

    async def on_new_token(self, event: dict[str, Any]) -> None:
        mint = str(event["mint"])
        token = {
            "mint": mint,
            "name": event.get("name"),
            "symbol": event.get("symbol"),
            "creator": event.get("traderPublicKey"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "market_cap_sol": self._float(event.get("marketCapSol")),
            "initial_buy": self._float(event.get("initialBuy")),
            "v_sol": self._float(event.get("vSolInBondingCurve")),
            "v_tokens": self._float(event.get("vTokensInBondingCurve")),
            "price_sol": self._bonding_price(event),
            "raw": event,
        }

        risk = await self.risk.summary(mint)
        token.update(
            {
                "risk_score": risk.get("score"),
                "risk_status": "AVAILABLE" if risk.get("available") else "UNAVAILABLE",
                "mint_authority": risk.get("mint_authority"),
                "freeze_authority": risk.get("freeze_authority"),
                "rugged": risk.get("rugged"),
                "rugcheck_degraded": not risk.get("available"),
            }
        )

        try:
            snap = await self.market.best_snapshot(mint)
        except Exception as exc:
            log.debug("Market snapshot failed: %s", exc)
            snap = {}

        token["liquidity_usd"] = snap.get("liquidity_usd")
        passed, score, reasons = evaluate_gates(token, self.settings.rules)
        token["gate_passed"] = passed
        token["score"] = score
        self.db.upsert_token(token)

        if self.telegram:
            await self.telegram.send(self._discovery_message(token, passed, score, reasons))

        if passed:
            try:
                await self.watch_token(mint)
            except SafetyError as exc:
                log.warning("Auto-watch skipped for %s: %s", mint, exc)

            if self.mode == "LIVE" and self.settings.auto_live and not self.kill_switch:
                try:
                    await self.open_position_if_allowed(mint, reason=f"gate pass score={score}")
                except Exception as exc:
                    log.warning("Auto-entry skipped for %s: %s", mint, exc)

    def _discovery_message(
        self,
        token: dict[str, Any],
        passed: bool,
        score: float,
        reasons: list[str],
    ) -> str:
        label = "PASS" if passed else "BLOCK"
        reason_line = f"reasons: {', '.join(reasons)}\n" if reasons else ""
        return (
            f"[DISCOVERY {label}]\n"
            f"{token.get('name') or '?'} ${token.get('symbol') or '?'}\n"
            f"{token['mint']}\n"
            f"MC={token.get('market_cap_sol')} SOL | score={score}\n"
            f"LIQ={token.get('liquidity_usd')} USD | RISK={token.get('risk_score')}\n"
            f"{reason_line}"
            f"Pump.fun: https://pump.fun/coin/{token['mint']}"
        )

    async def check_token(self, mint: str) -> str:
        risk = await self.risk.summary(mint)
        snap = await self.market.best_snapshot(mint)
        row = self.db.fetchone("SELECT * FROM tokens WHERE mint=?", (mint,))
        lines = [f"MINT={mint}"]
        if row:
            lines += [
                f"NAME={row['name']}",
                f"SYMBOL={row['symbol']}",
                f"DISCOVERY_MC={row['market_cap_sol']} SOL",
                f"DISCOVERY_SCORE={row['score']}",
            ]
        lines += [
            f"PRICE_SOL={snap.get('price_sol')}",
            f"LIQUIDITY_USD={snap.get('liquidity_usd')}",
            f"RUGCHECK_SCORE={risk.get('score')}",
            f"MINT_AUTHORITY={risk.get('mint_authority')}",
            f"FREEZE_AUTHORITY={risk.get('freeze_authority')}",
            f"RUGGED={risk.get('rugged')}",
        ]
        return "\n".join(lines)

    async def watch_token(self, mint: str) -> None:
        active = self.db.active_watches()
        existing = any(row["mint"] == mint for row in active)
        if not existing and len(active) >= self.settings.max_watched_tokens:
            raise SafetyError("MAX_WATCHED_TOKENS reached.")
        until = (
            datetime.now(timezone.utc)
            + timedelta(minutes=self.settings.outcome_window_minutes)
        ).isoformat()
        self.db.add_watch(mint, until)
        self.db.journal(mint, "watch_added", {"until": until})
        if self.stream and self.settings.pumpportal_api_key:
            await self.stream.add_token(mint)

        snap = await self.market.best_snapshot(mint)
        if snap.get("price_sol"):
            self.db.update_outcome(mint, float(snap["price_sol"]))

    async def unwatch_token(self, mint: str) -> None:
        self.db.remove_watch(mint)
        if self.stream:
            await self.stream.remove_token(mint)

    async def on_trade_event(self, event: dict[str, Any]) -> None:
        mint = str(event["mint"])
        price = self._bonding_price(event)
        if price is not None:
            self.db.execute("UPDATE tokens SET price_sol=? WHERE mint=?", (price, mint))
            self.db.update_outcome(mint, price)
        for row in self.db.open_positions():
            if row["mint"] == mint and price:
                await self.handle_exit(row, price)

    async def open_position_if_allowed(self, mint: str, reason: str) -> str:
        row = self.db.fetchone("SELECT * FROM tokens WHERE mint=?", (mint,))
        if not row:
            raise SafetyError("Token not found.")
        price = row["price_sol"]
        if not price:
            snap = await self.market.best_snapshot(mint)
            price = snap.get("price_sol")
        if not price or price <= 0:
            raise SafetyError("No usable price.")
        amount = min(self.settings.max_trade_sol, self.settings.max_position_sol)
        return await self._request_or_execute("buy", mint, amount, True, reason, price)

    async def manual_trade(self, action: str, mint: str, amount: float) -> str:
        if action == "buy":
            row = self.db.fetchone("SELECT price_sol FROM tokens WHERE mint=?", (mint,))
            price = row["price_sol"] if row else None
            return await self._request_or_execute("buy", mint, amount, True, "manual /buy", price)
        if action == "sell":
            position = self.db.fetchone(
                "SELECT * FROM positions WHERE mint=? AND status='OPEN' ORDER BY opened_at DESC LIMIT 1",
                (mint,),
            )
            if not position:
                raise SafetyError("No open position for this mint.")
            snap = await self.market.best_snapshot(mint)
            price = snap.get("price_sol")
            if not price:
                price = position["last_price_sol"] or position["entry_price_sol"]
            result_text = await self._request_or_execute(
                "sell", mint, amount, False, "manual /sell", price, int(position["id"])
            )
            if self.mode != "CONFIRM":
                await self._apply_sell_fill(
                    position, amount, float(price), "manual /sell", None
                )
                await self._send_sell_feed(position, amount, float(price))
            return result_text
        raise ValueError("Unsupported action.")

    async def _request_or_execute(
        self,
        action: str,
        mint: str,
        amount: float,
        denominated_in_sol: bool,
        reason: str,
        price: float | None,
        position_id: int | None = None,
    ) -> str:
        if self.kill_switch:
            raise SafetyError("Kill switch is active.")

        # Confirm mode always journals a pending trade. It never signs/sends by itself.
        if self.mode == "CONFIRM":
            expires = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
            trade_id = self.db.create_pending_trade(
                action, mint, amount, denominated_in_sol, reason, expires, position_id
            )
            self.db.journal(
                mint,
                "trade_pending",
                {"id": trade_id, "action": action, "amount": amount, "reason": reason},
            )
            return f"CONFIRM required. Pending trade #{trade_id}. Use /confirm {trade_id}"

        result = await self.trader.execute(
            action=action,
            mint=mint,
            amount=amount,
            denominated_in_sol=denominated_in_sol,
            slippage_pct=self.settings.max_slippage_pct,
            priority_fee_sol=min(self.settings.max_priority_fee_sol, 0.00005),
            mode=self.mode,
        )

        if action == "buy" and not result.signature.startswith("PAPER-"):
            await self.trader.wait_for_confirmation(result.signature, timeout_seconds=25)

        if action == "buy":
            if not price:
                snap = await self.market.best_snapshot(mint)
                price = snap.get("price_sol")
            if not price:
                raise SafetyError(
                    "Transaction was sent, but an entry price was unavailable for journaling."
                )
            self.db.create_position(
                {
                    "mint": mint,
                    "mode": self.mode,
                    "entry_price_sol": price,
                    "quantity_tokens": amount / price,
                    "invested_sol": amount,
                    "buy_signature": result.signature,
                }
            )

        self.db.journal(
            mint,
            f"trade_executed:{action}",
            {"signature": result.signature, "amount": amount, "reason": reason},
        )

        token = self.db.fetchone("SELECT name,symbol FROM tokens WHERE mint=?", (mint,))

        if action == "buy":
            callout_status = None
            callout_url = None
            if not result.signature.startswith("PAPER-"):
                try:
                    callout = await self.callouts.publish_after_buy(
                        mint=mint,
                        name=token["name"] if token else None,
                        symbol=token["symbol"] if token else None,
                        amount_sol=amount,
                        signature=result.signature,
                    )
                    callout_status = callout.status
                    callout_url = callout.url
                    if self.telegram:
                        await self.telegram.send(
                            f"PUMP.FUN CALLOUT {callout.status}\n{callout.url or callout.message}",
                            parse_mode="Markdown",
                        )
                except Exception as exc:
                    callout_status = "FAILED"
                    log.error("Post-buy Pump.fun callout failed for %s: %s", mint, exc)
                    if self.telegram:
                        await self.telegram.send(
                            f"PUMP.FUN CALLOUT FAILED\n{mint}\n{exc}"
                        )

            bank = None
            try:
                bank = await self.trader.balance_sol()
            except Exception as exc:
                log.debug("Wallet balance lookup failed after buy: %s", exc)

            if self.telegram:
                msg = buy_message(
                    name=token["name"] if token else None,
                    symbol=token["symbol"] if token else None,
                    mint=mint,
                    amount_sol=amount,
                    bank_sol=bank,
                    trade_name=self.settings.telegram_trade_name,
                    profile_url=self.settings.telegram_profile_url,
                    footer=(
                        "callout published — every fill called live"
                        if callout_status == "PUBLISHED"
                        else "callout pending/failed — every fill called live"
                    ),
                )
                await self.telegram.send(msg, parse_mode="Markdown")

        return (
            f"Executed {action} {amount} on {mint}\n"
            f"Signature: {result.signature}"
        )

    async def confirm_trade(self, trade_id: int) -> str:
        pending = self.db.get_pending_trade(trade_id)
        if not pending:
            return "Pending trade not found or already handled."
        expires = datetime.fromisoformat(pending["expires_at"])
        if expires < datetime.now(timezone.utc):
            self.db.mark_pending(trade_id, "EXPIRED")
            return "Pending trade expired."

        old_mode = self.mode
        self.db.mark_pending(trade_id, "CONFIRMING")
        try:
            self.mode = "LIVE"
            reference_price = None
            snap = await self.market.best_snapshot(pending["mint"])
            reference_price = snap.get("price_sol")
            result = await self._request_or_execute(
                pending["action"],
                pending["mint"],
                float(pending["amount"]),
                bool(pending["denominated_in_sol"]),
                f"confirmed #{trade_id}: {pending['reason']}",
                reference_price,
                None,
            )
            if pending["action"] == "sell" and pending["position_id"]:
                row = self.db.fetchone(
                    "SELECT * FROM positions WHERE id=? AND status='OPEN'",
                    (pending["position_id"],),
                )
                if row and reference_price:
                    await self._apply_sell_fill(
                        row,
                        float(pending["amount"]),
                        float(reference_price),
                        pending["reason"],
                        result,
                    )
                    await self._send_sell_feed(row, float(pending["amount"]), float(reference_price))
            self.db.mark_pending(trade_id, "EXECUTED")
            return result
        except Exception:
            self.db.mark_pending(trade_id, "FAILED")
            raise
        finally:
            self.mode = old_mode

    async def handle_exit(self, position, price: float) -> None:
        if self.db.has_pending_exit(position["mint"]):
            return

        entry = float(position["entry_price_sol"])
        if entry <= 0:
            return
        multiple = price / entry
        peak = max(float(position["peak_multiple"] or 1), multiple)

        try:
            markers = json.loads(position["tp_markers"] or "[]")
        except Exception:
            markers = []

        decision = self.exit_engine.decide(
            {
                "entry_price_sol": entry,
                "opened_at": position["opened_at"],
                "peak_multiple": peak,
                "tp_markers": markers,
            },
            price,
        )

        self.db.update_position(
            position["id"],
            last_price_sol=price,
            peak_multiple=peak,
        )

        if not decision.should_exit:
            return

        fraction = min(max(decision.fraction_pct, 0), float(position["remaining_pct"]))
        if fraction <= 0:
            return

        result_text = await self._request_or_execute(
            "sell",
            position["mint"],
            fraction,
            False,
            decision.reason,
            price,
            int(position["id"]),
        )

        if self.mode == "CONFIRM":
            if self.telegram:
                await self.telegram.send(
                    f"EXIT PENDING\n{position['mint']}\n{decision.reason}\n{result_text}"
                )
            return

        result = None
        await self._apply_sell_fill(
            position,
            fraction,
            price,
            decision.reason,
            result,
        )
        await self._send_sell_feed(position, fraction, price)

    async def _send_sell_feed(self, position, fraction: float, price: float) -> None:
        if not self.telegram:
            return
        entry = float(position["entry_price_sol"] or 0)
        if entry <= 0 or price <= 0:
            return
        multiple = price / entry
        pnl_pct = (multiple - 1.0) * 100.0
        pnl_sol = float(position["invested_sol"] or 0) * (fraction / 100.0) * (multiple - 1.0)
        token = self.db.fetchone("SELECT name,symbol FROM tokens WHERE mint=?", (position["mint"],))
        try:
            bank = await self.trader.balance_sol()
        except Exception as exc:
            log.debug("Wallet balance lookup failed after sell: %s", exc)
            bank = None
        await self.telegram.send(
            sell_message(
                name=token["name"] if token else None,
                symbol=token["symbol"] if token else None,
                mint=position["mint"],
                pnl_sol=pnl_sol,
                pnl_pct=pnl_pct,
                bank_sol=bank,
                trade_name=self.settings.telegram_trade_name,
                profile_url=self.settings.telegram_profile_url,
                footer=self.settings.telegram_callout_footer,
            ),
            parse_mode="Markdown",
        )

    async def _apply_sell_fill(
        self,
        position,
        fraction: float,
        price: float,
        reason: str,
        trade_result,
    ) -> None:
        invested = float(position["invested_sol"])
        entry = float(position["entry_price_sol"])
        multiple = price / entry
        realized = invested * (fraction / 100) * multiple
        remaining = max(0, float(position["remaining_pct"]) - fraction)

        try:
            markers = json.loads(position["tp_markers"] or "[]")
        except Exception:
            markers = []

        if reason.startswith("TP "):
            try:
                marker = float(reason.split()[1].rstrip("x"))
                if marker not in markers:
                    markers.append(marker)
            except Exception:
                pass

        new_realized = float(position["realized_sol"] or 0) + realized
        self.db.update_position(
            position["id"],
            remaining_pct=remaining,
            realized_sol=new_realized,
            tp_markers=json.dumps(markers),
            last_price_sol=price,
        )

        if remaining <= 0:
            signature = getattr(trade_result, "signature", None)
            self.db.close_position(position["id"], new_realized, reason, signature)

    async def start_price_loop(self) -> None:
        while True:
            try:
                for row in self.db.open_positions():
                    snap = await self.market.best_snapshot(row["mint"])
                    price = snap.get("price_sol")
                    if price:
                        self.db.update_outcome(row["mint"], float(price))
                        await self.handle_exit(row, float(price))
                await self._expire_watches()
                await self._cleanup_expired_pending()
                await self._sleep()
            except Exception as exc:
                log.debug("Price loop: %s", exc)
                await self._sleep()

    async def _expire_watches(self) -> None:
        now = datetime.now(timezone.utc)
        for row in self.db.active_watches():
            until = row["target_outcome_until"]
            if until and datetime.fromisoformat(until) <= now:
                self.db.remove_watch(row["mint"])
                if self.stream:
                    await self.stream.remove_token(row["mint"])

    async def _cleanup_expired_pending(self) -> None:
        rows = self.db.fetchall(
            "SELECT id,expires_at FROM pending_trades WHERE status='PENDING'"
        )
        now = datetime.now(timezone.utc)
        for row in rows:
            if datetime.fromisoformat(row["expires_at"]) <= now:
                self.db.mark_pending(row["id"], "EXPIRED")

    async def _sleep(self) -> None:
        import asyncio
        await asyncio.sleep(self.settings.price_poll_seconds)

    @staticmethod
    def _float(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _bonding_price(self, event: dict[str, Any]) -> float | None:
        vsol = self._float(event.get("vSolInBondingCurve"))
        vtokens = self._float(event.get("vTokensInBondingCurve"))
        if vsol is not None and vtokens and vtokens > 0:
            return vsol / vtokens
        return None
