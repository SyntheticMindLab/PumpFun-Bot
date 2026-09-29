from __future__ import annotations


def esc(text: str | None) -> str:
    if not text:
        return "?"
    value = str(text)
    return value.replace("\\", "\\\\").replace("`", "\\`").replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("]", "\\]")


def trade_status(pnl_pct: float) -> str:
    if pnl_pct <= -50:
        return "🟥🟥🟥🟥🟥 REKT"
    if pnl_pct <= -30:
        return "🟥🟥🟥 REKT"
    if pnl_pct <= -15:
        return "🟥🟥 CUT IT"
    if pnl_pct < 0:
        return "🟥 CUT IT"
    if pnl_pct >= 200:
        return "🟢🟢 MOON"
    if pnl_pct >= 100:
        return "🟢🟢 COOKING"
    return "🟢 COOKING"


def token_links(mint: str) -> str:
    return (
        f"💊 [pump.fun](https://pump.fun/coin/{mint}) · "
        f"[chart](https://dexscreener.com/solana/{mint}) · "
        f"[gmgn](https://gmgn.ai/sol/token/{mint})"
    )


def profile_line(name: str, profile_url: str) -> str:
    if profile_url.strip():
        url = profile_url.rstrip("/")
        return f"🎪 [{esc(name)} on pump.fun]({url})"
    return f"🎪 {esc(name)} on pump.fun"


def buy_message(
    *,
    name: str | None,
    symbol: str | None,
    mint: str,
    amount_sol: float,
    bank_sol: float | None,
    trade_name: str,
    profile_url: str,
    footer: str,
) -> str:
    bank = f"{bank_sol:.2f}" if bank_sol is not None else "n/a"
    return (
        f"{esc(trade_name)}:\n"
        f" 🟣 APED · {esc(symbol or name or 'TOKEN')}\n"
        f" 🎯 in for {amount_sol:.3f} SOL\n\n"
        f"`{mint}`\n"
        f" {token_links(mint)}\n"
        f" 👛 bank {bank} SOL\n"
        f" {profile_line(trade_name, profile_url)} — {esc(footer)}"
    )


def sell_message(
    *,
    name: str | None,
    symbol: str | None,
    mint: str,
    pnl_sol: float,
    pnl_pct: float,
    bank_sol: float | None,
    trade_name: str,
    profile_url: str,
    footer: str,
) -> str:
    bank = f"{bank_sol:.2f}" if bank_sol is not None else "n/a"
    status = trade_status(pnl_pct)
    return (
        f"{esc(trade_name)}:\n"
        f" {status} · {esc(symbol or name or 'TOKEN')}\n"
        f" {pnl_sol:+.4f} SOL · {pnl_pct:+.1f}%\n\n"
        f"`{mint}`\n"
        f" {token_links(mint)}\n"
        f" 👛 bank {bank} SOL\n"
        f" {profile_line(trade_name, profile_url)} — {esc(footer)}"
    )
