from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def init(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tokens (
                mint TEXT PRIMARY KEY,
                name TEXT,
                symbol TEXT,
                creator TEXT,
                created_at TEXT NOT NULL,
                market_cap_sol REAL,
                initial_buy REAL,
                v_sol REAL,
                v_tokens REAL,
                risk_score REAL,
                risk_status TEXT,
                liquidity_usd REAL,
                price_sol REAL,
                gate_passed INTEGER NOT NULL DEFAULT 0,
                score REAL,
                raw_json TEXT
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mint TEXT,
                event_type TEXT NOT NULL,
                ts TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS watches (
                mint TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                target_outcome_until TEXT
            );

            CREATE TABLE IF NOT EXISTS outcomes (
                mint TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                until_ts TEXT NOT NULL,
                first_price_sol REAL,
                max_price_sol REAL,
                min_price_sol REAL,
                last_price_sol REAL,
                final_multiple REAL,
                closed INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mint TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                entry_price_sol REAL NOT NULL,
                quantity_tokens REAL NOT NULL,
                invested_sol REAL NOT NULL,
                realized_sol REAL NOT NULL DEFAULT 0,
                remaining_pct REAL NOT NULL DEFAULT 100,
                peak_multiple REAL NOT NULL DEFAULT 1,
                last_price_sol REAL,
                exit_reason TEXT,
                buy_signature TEXT,
                sell_signature TEXT,
                tp_markers TEXT NOT NULL DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS pending_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                action TEXT NOT NULL,
                mint TEXT NOT NULL,
                amount REAL NOT NULL,
                denominated_in_sol INTEGER NOT NULL,
                reason TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                position_id INTEGER,
                status TEXT NOT NULL DEFAULT 'PENDING'
            );

            CREATE TABLE IF NOT EXISTS callouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mint TEXT NOT NULL,
                created_at TEXT NOT NULL,
                buy_signature TEXT,
                status TEXT NOT NULL,
                message TEXT NOT NULL,
                url TEXT,
                error TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_events_mint_ts ON events(mint, ts);
            CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
            CREATE INDEX IF NOT EXISTS idx_watches_active ON watches(active);
            """
        )
        self.conn.commit()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def journal(self, mint: str | None, event_type: str, payload: dict[str, Any]) -> None:
        self.execute(
            "INSERT INTO events(mint,event_type,ts,payload_json) VALUES(?,?,?,?)",
            (mint, event_type, utc_now(), json.dumps(payload, separators=(",", ":"))),
        )

    def upsert_token(self, token: dict[str, Any]) -> None:
        self.execute(
            """
            INSERT INTO tokens(
              mint,name,symbol,creator,created_at,market_cap_sol,initial_buy,
              v_sol,v_tokens,risk_score,risk_status,liquidity_usd,price_sol,
              gate_passed,score,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(mint) DO UPDATE SET
              name=excluded.name,
              symbol=excluded.symbol,
              creator=excluded.creator,
              market_cap_sol=excluded.market_cap_sol,
              initial_buy=excluded.initial_buy,
              v_sol=excluded.v_sol,
              v_tokens=excluded.v_tokens,
              risk_score=excluded.risk_score,
              risk_status=excluded.risk_status,
              liquidity_usd=excluded.liquidity_usd,
              price_sol=excluded.price_sol,
              gate_passed=excluded.gate_passed,
              score=excluded.score,
              raw_json=excluded.raw_json
            """,
            (
                token["mint"], token.get("name"), token.get("symbol"), token.get("creator"),
                token.get("created_at", utc_now()), token.get("market_cap_sol"),
                token.get("initial_buy"), token.get("v_sol"), token.get("v_tokens"),
                token.get("risk_score"), token.get("risk_status"), token.get("liquidity_usd"),
                token.get("price_sol"), int(bool(token.get("gate_passed", False))),
                token.get("score"), json.dumps(token.get("raw", {}), separators=(",", ":")),
            ),
        )

    def active_watches(self) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM watches WHERE active=1 ORDER BY created_at DESC")

    def add_watch(self, mint: str, until: str) -> None:
        now = utc_now()
        self.execute(
            """
            INSERT INTO watches(mint,created_at,active,target_outcome_until)
            VALUES(?,?,1,?)
            ON CONFLICT(mint) DO UPDATE SET active=1,target_outcome_until=excluded.target_outcome_until
            """,
            (mint, now, until),
        )
        self.execute(
            """
            INSERT INTO outcomes(mint,started_at,until_ts)
            VALUES(?,?,?)
            ON CONFLICT(mint) DO UPDATE SET started_at=excluded.started_at,until_ts=excluded.until_ts,closed=0
            """,
            (mint, now, until),
        )

    def remove_watch(self, mint: str) -> None:
        self.execute("UPDATE watches SET active=0 WHERE mint=?", (mint,))

    def open_positions(self) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM positions WHERE status='OPEN' ORDER BY opened_at")

    def daily_realized_pnl(self) -> float:
        row = self.fetchone(
            """
            SELECT COALESCE(SUM(realized_sol - invested_sol),0)
            FROM positions
            WHERE closed_at IS NOT NULL
              AND datetime(closed_at) >= datetime('now','-24 hours')
            """
        )
        return float(row[0] if row else 0.0)

    def create_position(self, data: dict[str, Any]) -> int:
        cur = self.execute(
            """
            INSERT INTO positions(
              mint,opened_at,mode,status,entry_price_sol,quantity_tokens,
              invested_sol,remaining_pct,peak_multiple,last_price_sol,buy_signature
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                data["mint"], utc_now(), data["mode"], "OPEN", data["entry_price_sol"],
                data["quantity_tokens"], data["invested_sol"], 100, 1, data["entry_price_sol"],
                data.get("buy_signature"),
            ),
        )
        return int(cur.lastrowid)

    def update_position(self, position_id: int, **fields: Any) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE positions SET {sets} WHERE id=?", tuple(fields.values()) + (position_id,))

    def close_position(
        self,
        position_id: int,
        realized_sol: float,
        reason: str,
        sell_signature: str | None,
    ) -> None:
        self.update_position(
            position_id,
            closed_at=utc_now(),
            status="CLOSED",
            realized_sol=realized_sol,
            remaining_pct=0,
            exit_reason=reason,
            sell_signature=sell_signature,
        )

    def create_pending_trade(
        self,
        action: str,
        mint: str,
        amount: float,
        denominated_in_sol: bool,
        reason: str,
        expires_at: str,
        position_id: int | None = None,
    ) -> int:
        cur = self.execute(
            """
            INSERT INTO pending_trades(
              created_at,action,mint,amount,denominated_in_sol,reason,expires_at,position_id
            )
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (utc_now(), action, mint, amount, int(denominated_in_sol), reason, expires_at, position_id),
        )
        return int(cur.lastrowid)

    def get_pending_trade(self, trade_id: int) -> sqlite3.Row | None:
        return self.fetchone(
            "SELECT * FROM pending_trades WHERE id=? AND status='PENDING'",
            (trade_id,),
        )

    def mark_pending(self, trade_id: int, status: str) -> None:
        self.execute("UPDATE pending_trades SET status=? WHERE id=?", (status, trade_id))

    def has_pending_exit(self, mint: str) -> bool:
        row = self.fetchone(
            "SELECT 1 FROM pending_trades WHERE mint=? AND action='sell' AND status='PENDING' LIMIT 1",
            (mint,),
        )
        return row is not None


    def record_callout(self, mint: str, buy_signature: str, status: str, message: str) -> int:
        cur = self.execute(
            "INSERT INTO callouts(mint,created_at,buy_signature,status,message) VALUES(?,?,?,?,?)",
            (mint, utc_now(), buy_signature, status, message),
        )
        return int(cur.lastrowid)

    def record_callout_result(
        self,
        mint: str,
        buy_signature: str,
        status: str,
        url: str | None,
        error: str | None,
    ) -> None:
        self.execute(
            """
            UPDATE callouts
            SET status=?,url=?,error=?
            WHERE id = (
                SELECT id FROM callouts
                WHERE mint=? AND buy_signature=?
                ORDER BY id DESC LIMIT 1
            )
            """,
            (status, url, error, mint, buy_signature),
        )

    def update_outcome(self, mint: str, price_sol: float) -> None:
        if price_sol <= 0:
            return
        row = self.fetchone("SELECT * FROM outcomes WHERE mint=?", (mint,))
        if not row:
            return
        first = float(row["first_price_sol"] or price_sol)
        max_price = max(float(row["max_price_sol"] or price_sol), price_sol)
        min_price = min(float(row["min_price_sol"] or price_sol), price_sol)
        final_multiple = price_sol / first if first else None
        now = datetime.now(timezone.utc)
        until = datetime.fromisoformat(row["until_ts"])
        closed = int(now >= until)
        self.execute(
            """
            UPDATE outcomes
            SET first_price_sol=?,max_price_sol=?,min_price_sol=?,last_price_sol=?,
                final_multiple=?,closed=?
            WHERE mint=?
            """,
            (first, max_price, min_price, price_sol, final_multiple, closed, mint),
        )
        if closed:
            self.execute("UPDATE watches SET active=0 WHERE mint=?", (mint,))
