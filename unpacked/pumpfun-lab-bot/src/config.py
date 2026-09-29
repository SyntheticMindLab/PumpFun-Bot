from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_allowed_user_ids: set[int]
    telegram_chat_id: str | None

    solana_rpc_url: str
    wallet_private_key_base58: str | None

    pumpportal_api_key: str | None

    mode: str
    auto_live: bool
    database_path: Path
    rules_path: Path
    log_level: str

    max_trade_sol: float
    max_position_sol: float
    max_open_positions: int
    max_daily_loss_sol: float
    max_slippage_pct: float
    max_priority_fee_sol: float

    max_watched_tokens: int
    rugcheck_enabled: bool
    rugcheck_base_url: str
    dexscreener_base_url: str
    outcome_window_minutes: int
    price_poll_seconds: int

    take_profit_enabled: bool
    tp_ladder: list[list[float]]
    trailing_stop_pct: float
    time_stop_minutes: int
    reactive_exit_drop_pct: float
    call_outs_enabled: bool
    pumpfun_tagline: str
    pumpfun_callout_mode: str
    pumpfun_callout_profile_dir: Path
    pumpfun_callout_headless: bool
    pumpfun_callout_create_url: str
    pumpfun_callout_accept_terms: bool
    pumpfun_callout_timeout_ms: int
    pumpfun_callout_template: str
    telegram_trade_name: str
    telegram_profile_url: str
    telegram_callout_footer: str

    rules: dict[str, Any]


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required.")

    ids = {
        int(item.strip())
        for item in os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").split(",")
        if item.strip()
    }

    db_path = Path(os.getenv("DATABASE_PATH", "data/journal.db"))
    rules_path = Path(os.getenv("RULES_PATH", "config/rules.yaml"))
    db_path.parent.mkdir(parents=True, exist_ok=True)

    rules = yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
    ladder = json.loads(os.getenv("TP_LADDER_JSON", "[[1.5,0.25],[2.0,0.25],[3.0,0.25]]"))

    mode = os.getenv("MODE", "CONFIRM").upper()
    if mode not in {"PAPER", "CONFIRM", "LIVE"}:
        raise ValueError("MODE must be PAPER, CONFIRM, or LIVE.")

    return Settings(
        telegram_bot_token=token,
        telegram_allowed_user_ids=ids,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
        solana_rpc_url=os.getenv("SOLANA_RPC_URL", "").strip(),
        wallet_private_key_base58=os.getenv("WALLET_PRIVATE_KEY_BASE58") or None,
        pumpportal_api_key=os.getenv("PUMPPORTAL_API_KEY") or None,
        mode=mode,
        auto_live=_env_bool("AUTO_LIVE", False),
        database_path=db_path,
        rules_path=rules_path,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        max_trade_sol=_env_float("MAX_TRADE_SOL", 0.01),
        max_position_sol=_env_float("MAX_POSITION_SOL", 0.03),
        max_open_positions=_env_int("MAX_OPEN_POSITIONS", 3),
        max_daily_loss_sol=_env_float("MAX_DAILY_LOSS_SOL", 0.03),
        max_slippage_pct=_env_float("MAX_SLIPPAGE_PCT", 10),
        max_priority_fee_sol=_env_float("MAX_PRIORITY_FEE_SOL", 0.0001),
        max_watched_tokens=_env_int("MAX_WATCHED_TOKENS", 20),
        rugcheck_enabled=_env_bool("RUGCHECK_ENABLED", True),
        rugcheck_base_url=os.getenv("RUGCHECK_BASE_URL", "https://api.rugcheck.xyz").rstrip("/"),
        dexscreener_base_url=os.getenv("DEXSCREENER_BASE_URL", "https://api.dexscreener.com").rstrip("/"),
        outcome_window_minutes=_env_int("OUTCOME_WINDOW_MINUTES", 30),
        price_poll_seconds=_env_int("PRICE_POLL_SECONDS", 8),
        take_profit_enabled=_env_bool("TAKE_PROFIT_ENABLED", True),
        tp_ladder=ladder,
        trailing_stop_pct=_env_float("TRAILING_STOP_PCT", 20),
        time_stop_minutes=_env_int("TIME_STOP_MINUTES", 30),
        reactive_exit_drop_pct=_env_float("REACTIVE_EXIT_DROP_PCT", 25),
        call_outs_enabled=_env_bool("CALL_OUTS_ENABLED", True),
        pumpfun_tagline=os.getenv("PUMPFUN_TAGLINE", "BUILDING IN PUBLIC // SYNTHETICMIND"),
        pumpfun_callout_mode=os.getenv("PUMPFUN_CALLOUT_MODE", "PLAYWRIGHT").upper(),
        pumpfun_callout_profile_dir=Path(os.getenv("PUMPFUN_CALLOUT_PROFILE_DIR", "data/pumpfun-profile")),
        pumpfun_callout_headless=_env_bool("PUMPFUN_CALLOUT_HEADLESS", True),
        pumpfun_callout_create_url=os.getenv("PUMPFUN_CALLOUT_CREATE_URL", "https://pump.fun/create"),
        pumpfun_callout_accept_terms=_env_bool("PUMPFUN_CALLOUT_ACCEPT_TERMS", False),
        pumpfun_callout_timeout_ms=_env_int("PUMPFUN_CALLOUT_TIMEOUT_MS", 30000),
        pumpfun_callout_template=os.getenv(
            "PUMPFUN_CALLOUT_TEMPLATE",
            "{tagline}\n\n🟣 APED · ${symbol}\n🎯 in for {amount_sol} SOL\n\nMint: {mint}\n{pump_url}\nPosition disclosed: bought by {display_name}.",
        ),
        telegram_trade_name=os.getenv("TELEGRAM_TRADE_NAME", "Zepto"),
        telegram_profile_url=os.getenv("TELEGRAM_PROFILE_URL", "https://pump.fun/profile/"),
        telegram_callout_footer=os.getenv(
            "TELEGRAM_CALLOUT_FOOTER",
            "every fill called live — nothing cherry-picked",
        ),
        rules=rules,
    )
