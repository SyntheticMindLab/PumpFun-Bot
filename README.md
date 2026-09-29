# PumpFun Lab Bot

A single-user Telegram-controlled Solana/Pump.fun trading bot that grows through four cumulative versions:

| Version | Included |
|---|---|
| v0.1 | Discovery stream, SQLite journal, `/start` `/status` `/check` |
| v0.2 | YAML gates, scoring, Telegram alerts, `/watch`, outcome journal |
| v0.3 | CONFIRM/LIVE execution, hard limits, kill switch, Telegram trade feed |
| v0.4 | TP ladder, trailing stop, time stop, reactive exits, optional auto-LIVE, Pump.fun callout feed |

## Current architecture

- PumpPortal websocket for new-token discovery and watched-token trade events.
- SQLite journal for tokens, events, watches, positions and pending confirmations.
- YAML rules for hard gates and scoring.
- RugCheck summary for token-risk context.
- DexScreener token-pair lookup for market/price snapshots.
- Telegram long polling for the operator interface.
- PumpPortal Local Transaction API for locally signed trades.

PumpPortal's current docs describe `wss://pumpportal.fun/api/data` for realtime streams, with `subscribeNewToken` free and `subscribeTokenTrade` metered; since May 1, 2026, token/account trade streams require a PumpPortal API key and linked wallet. The same docs recommend one websocket connection for subscriptions. PumpPortal's Local Transaction API returns a transaction for your wallet to sign and send. PumpPortal currently lists a 0.5% Local Transaction API fee in addition to network/bonding-curve fees. See the links below.

## Safety defaults

The default `.env.example` starts in `CONFIRM` mode.

Live trading requires all of these:
1. `MODE=LIVE` or an approved `/confirm`.
2. `AUTO_LIVE=true` for automatic entry.
3. A configured Solana RPC and wallet private key.
4. All hard limits passing.
5. Kill switch not active.

The bot deliberately fails closed when a hard limit is violated.

### Hard limits

- `MAX_TRADE_SOL`
- `MAX_POSITION_SOL`
- `MAX_OPEN_POSITIONS`
- `MAX_DAILY_LOSS_SOL`
- `MAX_SLIPPAGE_PCT`
- `MAX_PRIORITY_FEE_SOL`
- `MAX_WATCHED_TOKENS`

## Install

Python 3.11+ is recommended.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
python -m playwright install chromium
```

Copy `.env.example` to `.env`, then fill in the Telegram and Solana settings.

## Telegram setup

Create a bot with BotFather and copy the bot token into `TELEGRAM_BOT_TOKEN`.

Optional but recommended:
- `TELEGRAM_ALLOWED_USER_IDS`: comma-separated Telegram user IDs that may control the bot.
- `TELEGRAM_CHAT_ID`: chat where alerts/trade feeds should be delivered.

The Telegram Bot API supports HTTPS requests and long polling via `getUpdates`; this repo uses long polling through `python-telegram-bot`.

## PumpPortal setup

For discovery only, `subscribeNewToken` does not require a PumpPortal key.

For watched-token trade streams, set `PUMPPORTAL_API_KEY`. PumpPortal currently documents those trade streams as metered at 0.01 SOL per 10,000 streamed events and says the linked wallet must be funded with at least 0.02 SOL.

For live trading, this repo uses the Local Transaction API endpoint:

`https://pumpportal.fun/api/trade-local`

The bot signs the returned Solana transaction locally before sending it through your configured RPC.

## Commands

```text
/start
/status
/check <mint>
/watch <mint>
/unwatch <mint>

/mode
/mode paper
/mode confirm
/mode live

/kill
/arm
/pending
/outcomes
/confirm <id>

/buy <mint> <sol>
/sell <mint> <percent>
```

### CONFIRM mode

Example:

```text
/buy <mint> 0.005
```

The bot creates a pending trade:

```text
CONFIRM required. Pending trade #12.
```

Then:

```text
/confirm 12
```

The transaction is signed and sent only after the confirmation step and hard-limit checks.

#
## Telegram trade feed

After a **confirmed buy**, the bot publishes the Pump.fun Callout first (when enabled and the signed-in Pump.fun session is ready), then sends a compact Telegram fill message in this style:

```text
Zepto:
 🟣 APED · BANTU
 🎯 in for 0.202 SOL

`A8ovRFh7tUAR4ST2rkBbashtEGLHLz9LmYxQfF5cpump`
 💊 [pump.fun](https://pump.fun/coin/A8ovRFh7tUAR4ST2rkBbashtEGLHLz9LmYxQfF5cpump) · [chart](https://dexscreener.com/solana/A8ovRFh7tUAR4ST2rkBbashtEGLHLz9LmYxQfF5cpump) · [gmgn](https://gmgn.ai/sol/token/A8ovRFh7tUAR4ST2rkBbashtEGLHLz9LmYxQfF5cpump)
 👛 bank 47.07 SOL
 🎪 [Zepto on pump.fun](YOUR_PROFILE_URL) — every fill called live — nothing cherry-picked
```

After an exit, the bot formats the fill using the realized tranche P/L:

```text
Zepto:
 🟥🟥🟥 REKT · BANTU
 -0.1316 SOL · -65.2%

`A8ovRFh7tUAR4ST2rkBbashtEGLHLz9LmYxQfF5cpump`
 💊 [pump.fun](...) · [chart](...) · [gmgn](...)
 👛 bank 47.07 SOL
 🎪 Zepto on pump.fun — every fill called live
```

The exact footer and Pump.fun profile URL are configurable through `TELEGRAM_CALLOUT_FOOTER` and `TELEGRAM_PROFILE_URL`.

## v0.4 exits

The exit engine supports:
- TP ladder: 1.5x / 2.0x / 3.0x by default
- trailing stop
- time stop
- reactive peak-to-current drawdown exit

Adjust the ladder and thresholds in `config/rules.yaml` and `.env`.

## Pump.fun callout (v0.4)

After a **real buy transaction reaches `confirmed` status**, the bot automatically opens Pump.fun's native `Create -> Callout` flow in a persistent Playwright browser profile, selects the exact mint, fills the configured callout text, and posts it. The published Pump.fun callout URL is then sent to Telegram and journaled in SQLite.

This is **not** a hidden API endpoint and it does not modify a coin's tagline/metadata. The current public Pump.fun Callout feature is a web-app feature; the public captured API surface available to us does not document a create-callout POST endpoint. A browser-driven native UI flow is therefore used.

Before enabling it, review Pump.fun's Callout Terms. The default template explicitly discloses that the bot bought the token and includes the mint and amount. The bot does not bypass CAPTCHA, login controls, or anti-bot systems. If Pump.fun presents such a check, the callout is marked failed and the trade remains journaled.

### First-time Pump.fun session

1. Install dependencies and the Chromium browser.
2. Set `PUMPFUN_CALLOUT_HEADLESS=false`.
3. Run:

```bash
python scripts/pumpfun_login.py
```

4. Sign in to Pump.fun in the opened browser.
5. Press ENTER in the terminal.
6. Change `PUMPFUN_CALLOUT_HEADLESS=true` for normal bot operation (or leave it false if you want to watch the browser).
7. After reviewing Pump.fun's terms, set:

```text
PUMPFUN_CALLOUT_ACCEPT_TERMS=true
```

The saved browser profile lives in `data/pumpfun-profile/` and is ignored by git. Never commit it.

## Run

```bash
python -m src.main
```

## Tests

```bash
pytest -q
```

## Repository structure

```text
pumpfun-lab-bot/
├── .env.example
├── .gitignore
├── README.md
├── requirements.txt
├── config/
│   └── rules.yaml
├── src/
│   ├── config.py
│   ├── db.py
│   ├── engine.py
│   ├── exits.py
│   ├── market.py
│   ├── pumpportal.py
│   ├── risk.py
│   ├── telegram_bot.py
│   └── trader.py
└── tests/
    ├── test_exit.py
    └── test_risk.py
```

## Official/current references

- PumpPortal realtime data and fees: https://pumpportal.fun/data-api/bonk-fun-data-api/
- PumpPortal Local Transaction API: https://pumpportal.fun/local-trading-api/trading-api/
- PumpPortal setup/security: https://www.pumpportal.fun/trading-api/setup/
- DEX Screener API reference: https://docs.dexscreener.com/api/reference
- Telegram Bot API: https://core.telegram.org/bots/api

## Risk note

This software can submit transactions on Solana mainnet when live mode is enabled. Meme-token trading is highly speculative and can result in rapid loss of funds. Review `config/rules.yaml` and `.env` before enabling live mode, and never commit `.env` or a private key to GitHub.
