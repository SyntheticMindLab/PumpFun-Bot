from __future__ import annotations

import base64
import logging
import asyncio
from dataclasses import dataclass

import httpx
from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from solders.transaction import VersionedTransaction

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradeResult:
    signature: str
    action: str
    amount: float


class SafetyError(RuntimeError):
    pass


class Trader:
    def __init__(
        self,
        rpc_url: str,
        private_key_base58: str | None,
        max_trade_sol: float,
        max_position_sol: float,
        max_open_positions: int,
        max_daily_loss_sol: float,
        max_slippage_pct: float,
        max_priority_fee_sol: float,
        db,
    ):
        self.rpc_url = rpc_url
        self.private_key_base58 = private_key_base58
        self.max_trade_sol = max_trade_sol
        self.max_position_sol = max_position_sol
        self.max_open_positions = max_open_positions
        self.max_daily_loss_sol = max_daily_loss_sol
        self.max_slippage_pct = max_slippage_pct
        self.max_priority_fee_sol = max_priority_fee_sol
        self.db = db

    @property
    def public_key(self) -> str | None:
        if not self.private_key_base58:
            return None
        return str(Keypair.from_base58_string(self.private_key_base58).pubkey())

    def _enforce(self, action: str, amount: float, denominated_in_sol: bool) -> None:
        if action == "buy":
            if not denominated_in_sol:
                raise SafetyError("BUY must be denominated in SOL.")
            if amount <= 0:
                raise SafetyError("Buy amount must be positive.")
            if amount > self.max_trade_sol:
                raise SafetyError(f"Buy exceeds MAX_TRADE_SOL={self.max_trade_sol}.")
            if amount > self.max_position_sol:
                raise SafetyError(f"Buy exceeds MAX_POSITION_SOL={self.max_position_sol}.")
            if len(self.db.open_positions()) >= self.max_open_positions:
                raise SafetyError(f"MAX_OPEN_POSITIONS={self.max_open_positions} reached.")
        elif action == "sell":
            if denominated_in_sol:
                raise SafetyError("SELL must be denominated in token percentage.")
            if amount <= 0 or amount > 100:
                raise SafetyError("Sell amount must be between 0 and 100 percent.")

        pnl = self.db.daily_realized_pnl()
        if pnl <= -abs(self.max_daily_loss_sol):
            raise SafetyError("24h realized loss limit reached.")

    async def execute(
        self,
        action: str,
        mint: str,
        amount: float,
        denominated_in_sol: bool,
        slippage_pct: float,
        priority_fee_sol: float,
        mode: str,
    ) -> TradeResult:
        self._enforce(action, amount, denominated_in_sol)

        if slippage_pct <= 0 or slippage_pct > self.max_slippage_pct:
            raise SafetyError("Slippage is outside the configured hard limit.")
        if priority_fee_sol < 0 or priority_fee_sol > self.max_priority_fee_sol:
            raise SafetyError("Priority fee is outside the configured hard limit.")

        if mode == "PAPER":
            return TradeResult(signature=f"PAPER-{mint[:8]}-{action}", action=action, amount=amount)

        if not self.private_key_base58:
            raise SafetyError("WALLET_PRIVATE_KEY_BASE58 is not configured.")
        if not self.rpc_url:
            raise SafetyError("SOLANA_RPC_URL is not configured.")

        api_amount = amount if action == "buy" else f"{amount:g}%"
        payload = {
            "publicKey": self.public_key,
            "action": action,
            "mint": mint,
            "amount": api_amount,
            "denominatedInSol": "true" if denominated_in_sol else "false",
            "slippage": slippage_pct,
            "priorityFee": priority_fee_sol,
            "pool": "auto",
        }

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://pumpportal.fun/api/trade-local",
                json=payload,
            )
            if response.status_code != 200:
                raise RuntimeError(
                    f"PumpPortal trade-local failed: {response.status_code} {response.text[:500]}"
                )
            raw_tx = response.content

        signed = self._sign_transaction(raw_tx)
        signature = await self._send_transaction(signed)
        return TradeResult(signature=signature, action=action, amount=amount)

    def _sign_transaction(self, raw_tx: bytes) -> bytes:
        keypair = Keypair.from_base58_string(self.private_key_base58 or "")
        tx = VersionedTransaction.from_bytes(raw_tx)
        signature = keypair.sign_message(to_bytes_versioned(tx.message))
        signed = VersionedTransaction.populate(tx.message, [signature])
        return bytes(signed)

    async def _send_transaction(self, signed_tx: bytes) -> str:
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [
                base64.b64encode(signed_tx).decode(),
                {
                    "encoding": "base64",
                    "skipPreflight": False,
                    "preflightCommitment": "confirmed",
                    "maxRetries": 3,
                },
            ],
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(self.rpc_url, json=body)
            response.raise_for_status()
            data = response.json()
        if "error" in data:
            raise RuntimeError(f"Solana RPC sendTransaction failed: {data['error']}")
        return str(data["result"])

    async def wait_for_confirmation(self, signature: str, timeout_seconds: int = 20) -> bool:
        """Wait until a sent transaction reaches confirmed/finalized status."""
        if signature.startswith("PAPER-"):
            return False
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignatureStatuses",
                "params": [[signature], {"searchTransactionHistory": True}],
            }
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(self.rpc_url, json=body)
                response.raise_for_status()
                data = response.json()
            if "error" in data:
                raise RuntimeError(f"Solana RPC getSignatureStatuses failed: {data['error']}")
            status = (data.get("result", {}).get("value") or [None])[0]
            if status:
                if status.get("err"):
                    raise SafetyError(f"Transaction {signature} failed on-chain: {status['err']}")
                confirmation = status.get("confirmationStatus")
                if confirmation in {"confirmed", "finalized"}:
                    return True
            await asyncio.sleep(1)
        raise TimeoutError(f"Timed out waiting for transaction confirmation: {signature}")

    async def balance_sol(self) -> float | None:
        if not self.public_key or not self.rpc_url:
            return None
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [self.public_key, {"commitment": "confirmed"}],
        }
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(self.rpc_url, json=body)
            response.raise_for_status()
            data = response.json()
        if "error" in data:
            raise RuntimeError(f"Solana RPC getBalance failed: {data['error']}")
        return float(data["result"]["value"]) / 1_000_000_000
