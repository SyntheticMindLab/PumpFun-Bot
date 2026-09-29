from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

log = logging.getLogger(__name__)


def _utc_plus(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


class TelegramBot:
    def __init__(self, settings, db, engine):
        self.settings = settings
        self.db = db
        self.engine = engine
        self.app: Application | None = None

    def allowed(self, update: Update) -> bool:
        if not self.settings.telegram_allowed_user_ids:
            return True
        user = update.effective_user
        return bool(user and user.id in self.settings.telegram_allowed_user_ids)

    async def _reply(self, update: Update, text: str) -> None:
        if update.message:
            await update.message.reply_text(text, parse_mode=None)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update): return
        await self._reply(
            update,
            "SyntheticMind Pump Lab Bot\n"
            "/status /check <mint> /watch <mint> /unwatch <mint>\n"
            "/mode /mode paper|confirm|live\n"
            "/kill /arm /pending /confirm <id>\n"
            "/outcomes\n"
            "/buy <mint> <sol> /sell <mint> <percent>\n"
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update): return
        st = self.engine.status_text()
        await self._reply(update, st)

    async def check(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update): return
        if not context.args:
            await self._reply(update, "Usage: /check <mint>")
            return
        result = await self.engine.check_token(context.args[0])
        await self._reply(update, result)

    async def watch(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update): return
        if not context.args:
            await self._reply(update, "Usage: /watch <mint>")
            return
        await self.engine.watch_token(context.args[0])
        await self._reply(update, f"Watching {context.args[0]}")

    async def unwatch(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update): return
        if not context.args:
            await self._reply(update, "Usage: /unwatch <mint>")
            return
        await self.engine.unwatch_token(context.args[0])
        await self._reply(update, f"Unwatched {context.args[0]}")

    async def mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update): return
        if context.args:
            mode = context.args[0].upper()
            if mode not in {"PAPER", "CONFIRM", "LIVE"}:
                await self._reply(update, "Mode must be PAPER, CONFIRM, or LIVE.")
                return
            if mode == "LIVE" and not self.settings.auto_live:
                await self._reply(update, "LIVE is disabled by AUTO_LIVE=false. Set AUTO_LIVE=true and restart.")
                return
            self.engine.mode = mode
        await self._reply(update, f"MODE={self.engine.mode}")


    async def outcomes(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return
        rows = self.db.fetchall(
            """
            SELECT mint, first_price_sol, max_price_sol, min_price_sol,
                   last_price_sol, final_multiple, closed
            FROM outcomes
            ORDER BY started_at DESC
            LIMIT 10
            """
        )
        if not rows:
            await self._reply(update, "No outcome records yet.")
            return
        lines = ["OUTCOME TRACKER"]
        for r in rows:
            lines.append(
                f"{r['mint'][:8]}... first={r['first_price_sol']} "
                f"max={r['max_price_sol']} last={r['last_price_sol']} "
                f"multiple={r['final_multiple']} closed={r['closed']}"
            )
        await self._reply(update, "\n".join(lines))

    async def kill(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update): return
        self.engine.kill_switch = True
        await self._reply(update, "KILL SWITCH: ACTIVE. New trades blocked.")

    async def arm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update): return
        self.engine.kill_switch = False
        await self._reply(update, "KILL SWITCH: ARMED. Hard limits remain active.")

    async def pending(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update): return
        rows = self.db.fetchall("SELECT * FROM pending_trades WHERE status='PENDING' ORDER BY id DESC LIMIT 10")
        if not rows:
            await self._reply(update, "No pending trades.")
            return
        lines = [f"#{r['id']} {r['action']} {r['mint']} amount={r['amount']} reason={r['reason']}" for r in rows]
        await self._reply(update, "\n".join(lines))

    async def confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update): return
        if not context.args:
            await self._reply(update, "Usage: /confirm <id>")
            return
        try:
            trade_id = int(context.args[0])
            result = await self.engine.confirm_trade(trade_id)
        except Exception as exc:
            result = f"Confirm failed: {exc}"
        await self._reply(update, result)

    async def buy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update): return
        if len(context.args) != 2:
            await self._reply(update, "Usage: /buy <mint> <sol>")
            return
        mint, sol = context.args
        try:
            result = await self.engine.manual_trade("buy", mint, float(sol))
        except Exception as exc:
            result = f"Buy failed: {exc}"
        await self._reply(update, result)

    async def sell(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update): return
        if len(context.args) != 2:
            await self._reply(update, "Usage: /sell <mint> <percent>")
            return
        mint, pct = context.args
        try:
            result = await self.engine.manual_trade("sell", mint, float(pct))
        except Exception as exc:
            result = f"Sell failed: {exc}"
        await self._reply(update, result)

    async def send(self, text: str, parse_mode: str | None = None) -> None:
        if not self.app or not self.settings.telegram_chat_id:
            return
        try:
            await self.app.bot.send_message(
                chat_id=self.settings.telegram_chat_id,
                text=text,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
        except Exception as exc:
            log.warning("Telegram send failed: %s", exc)

    def build(self) -> Application:
        app = Application.builder().token(self.settings.telegram_bot_token).build()
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CommandHandler("status", self.status))
        app.add_handler(CommandHandler("check", self.check))
        app.add_handler(CommandHandler("watch", self.watch))
        app.add_handler(CommandHandler("unwatch", self.unwatch))
        app.add_handler(CommandHandler("mode", self.mode))
        app.add_handler(CommandHandler("kill", self.kill))
        app.add_handler(CommandHandler("arm", self.arm))
        app.add_handler(CommandHandler("pending", self.pending))
        app.add_handler(CommandHandler("outcomes", self.outcomes))
        app.add_handler(CommandHandler("confirm", self.confirm))
        app.add_handler(CommandHandler("buy", self.buy))
        app.add_handler(CommandHandler("sell", self.sell))
        self.app = app
        return app
