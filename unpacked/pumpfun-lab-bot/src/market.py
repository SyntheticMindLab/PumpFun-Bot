from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class MarketClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def token_pairs(self, mint: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}/tokens/v1/solana/{mint}"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
        return [p for p in data if p.get("chainId") == "solana"]

    async def best_snapshot(self, mint: str) -> dict[str, Any]:
        try:
            pairs = await self.token_pairs(mint)
        except Exception as exc:
            log.debug("DexScreener lookup failed for %s: %s", mint, exc)
            return {}
        if not pairs:
            return {}
        pairs.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)
        p = pairs[0]
        price_native = p.get("priceNative")
        return {
            "price_sol": float(price_native) if price_native else None,
            "price_usd": float(p["priceUsd"]) if p.get("priceUsd") else None,
            "liquidity_usd": float((p.get("liquidity") or {}).get("usd") or 0),
            "market_cap_usd": float(p["marketCap"]) if p.get("marketCap") else None,
            "fdv_usd": float(p["fdv"]) if p.get("fdv") else None,
            "pair_address": p.get("pairAddress"),
            "dex_id": p.get("dexId"),
            "url": p.get("url"),
        }
