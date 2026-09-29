from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


def _find_first(obj: Any, *keys: str) -> Any:
    if isinstance(obj, dict):
        for key in keys:
            if key in obj:
                return obj[key]
        for value in obj.values():
            found = _find_first(value, *keys)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_first(item, *keys)
            if found is not None:
                return found
    return None


class RiskClient:
    def __init__(self, base_url: str, enabled: bool):
        self.base_url = base_url.rstrip("/")
        self.enabled = enabled

    async def summary(self, mint: str) -> dict[str, Any]:
        if not self.enabled:
            return {"available": False, "reason": "disabled"}
        url = f"{self.base_url}/v1/tokens/{mint}/report"
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                r = await client.get(url)
                r.raise_for_status()
                raw = r.json()
        except Exception as exc:
            log.debug("RugCheck failed for %s: %s", mint, exc)
            return {"available": False, "reason": str(exc)}

        risks = raw.get("risks") or []
        score = raw.get("score")
        if score is None:
            score = _find_first(raw, "riskScore", "score")
        mint_auth = _find_first(raw, "mintAuthority")
        freeze_auth = _find_first(raw, "freezeAuthority")
        rugged = bool(_find_first(raw, "rugged") or False)

        return {
            "available": True,
            "score": float(score) if score is not None else None,
            "risks": risks,
            "mint_authority": mint_auth,
            "freeze_authority": freeze_auth,
            "rugged": rugged,
            "raw": raw,
        }


def evaluate_gates(token: dict[str, Any], rules: dict[str, Any]) -> tuple[bool, float, list[str]]:
    gates = rules.get("gates", {})
    score_rules = rules.get("scoring", {})
    thresholds = score_rules.get("thresholds", {})
    weights = score_rules.get("weights", {})

    reasons: list[str] = []

    market_cap = token.get("market_cap_sol")
    initial_buy = token.get("initial_buy")
    v_tokens = token.get("v_tokens") or 0
    risk_score = token.get("risk_score")
    liquidity = token.get("liquidity_usd") or 0
    mint_auth = token.get("mint_authority")
    freeze_auth = token.get("freeze_authority")

    if market_cap is None:
        reasons.append("missing market cap")
    else:
        if market_cap > float(gates.get("max_market_cap_sol", 10**9)):
            reasons.append("market cap above hard ceiling")
        if market_cap < float(gates.get("min_market_cap_sol", 0)):
            reasons.append("market cap below hard floor")

    if initial_buy is not None and v_tokens:
        ratio = float(initial_buy) / float(v_tokens)
        token["initial_buy_ratio"] = ratio
        if ratio < float(gates.get("min_initial_buy_ratio", 0)):
            reasons.append("initial buy ratio below floor")

    if gates.get("require_rugcheck", False):
        if risk_score is None:
            reasons.append("rugcheck unavailable")
        elif risk_score > float(gates.get("max_rugcheck_score", 100)):
            reasons.append("rugcheck score above ceiling")

    if liquidity < float(gates.get("min_liquidity_usd", 0)):
        reasons.append("liquidity below floor")

    if token.get("rugcheck_degraded"):
        if gates.get("require_rugcheck", False) or gates.get("require_mint_authority_disabled", False) or gates.get("require_freeze_authority_disabled", False):
            reasons.append("risk source unavailable for required hard gates")

    if gates.get("require_mint_authority_disabled", False) and not token.get("rugcheck_degraded") and mint_auth:
        reasons.append("mint authority active")

    if gates.get("require_freeze_authority_disabled", False) and not token.get("rugcheck_degraded") and freeze_auth:
        reasons.append("freeze authority active")

    # Transparent score: 100 max, additive subscores.
    score = 0.0
    m_ideal_low = float(thresholds.get("market_cap_ideal_low", 30))
    m_ideal_high = float(thresholds.get("market_cap_ideal_high", 150))
    if market_cap is not None:
        if m_ideal_low <= market_cap <= m_ideal_high:
            score += float(weights.get("market_cap", 0))
        else:
            score += float(weights.get("market_cap", 0)) * 0.5

    if initial_buy is not None and v_tokens:
        ratio = float(initial_buy) / float(v_tokens)
        target = float(thresholds.get("initial_buy_ratio_ideal", 0.02))
        score += float(weights.get("initial_buy", 0)) * min(ratio / target if target else 0, 1)

    if risk_score is not None:
        max_risk = float(gates.get("max_rugcheck_score", 100))
        rug_component = max(0.0, 1 - (float(risk_score) / max(max_risk, 1)))
        score += float(weights.get("rugcheck", 0)) * rug_component

    liq_target = float(thresholds.get("liquidity_ideal_usd", 0))
    if liq_target:
        score += float(weights.get("liquidity", 0)) * min(float(liquidity) / liq_target, 1)
    else:
        score += float(weights.get("liquidity", 0))

    return not reasons, round(min(score, 100), 2), reasons
