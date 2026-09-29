from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import websockets

log = logging.getLogger(__name__)

NEW_TOKEN = "subscribeNewToken"
TOKEN_TRADE = "subscribeTokenTrade"
MIGRATION = "subscribeMigration"


class PumpPortalStream:
    def __init__(
        self,
        api_key: str | None,
        on_event: Callable[[dict[str, Any]], Awaitable[None]],
    ):
        self.api_key = api_key
        self.on_event = on_event
        self.ws = None
        self.running = True
        self.token_subscriptions: set[str] = set()

    @property
    def url(self) -> str:
        base = "wss://pumpportal.fun/api/data"
        if self.api_key:
            return f"{base}?api-key={self.api_key}"
        return base

    async def subscribe_new_tokens(self) -> None:
        if self.ws:
            await self.ws.send(json.dumps({"method": NEW_TOKEN}))

    async def subscribe_migrations(self) -> None:
        if self.ws:
            await self.ws.send(json.dumps({"method": MIGRATION}))

    async def add_token(self, mint: str) -> None:
        if mint in self.token_subscriptions:
            return
        self.token_subscriptions.add(mint)
        if self.ws and self.api_key:
            await self.ws.send(json.dumps({"method": TOKEN_TRADE, "keys": [mint]}))

    async def remove_token(self, mint: str) -> None:
        self.token_subscriptions.discard(mint)
        if self.ws and self.api_key:
            await self.ws.send(json.dumps({"method": "unsubscribeTokenTrade", "keys": [mint]}))

    async def run(self) -> None:
        delay = 1
        while self.running:
            try:
                log.info("Connecting PumpPortal websocket")
                async with websockets.connect(
                    self.url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_size=2_000_000,
                ) as ws:
                    self.ws = ws
                    delay = 1
                    await self.subscribe_new_tokens()
                    await self.subscribe_migrations()
                    if self.api_key and self.token_subscriptions:
                        await ws.send(json.dumps({
                            "method": TOKEN_TRADE,
                            "keys": sorted(self.token_subscriptions),
                        }))
                    async for raw in ws:
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        await self.on_event(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("PumpPortal websocket error: %s", exc)
                self.ws = None
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
