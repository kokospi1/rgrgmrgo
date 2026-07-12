from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from .models import BotConfig


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init()
        self._migrate()

    def _init(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scanned_tokens (
                address TEXT PRIMARY KEY,
                first_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS alerted_tokens (
                address TEXT PRIMARY KEY,
                alerted_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS watchlist (
                address TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                initial_price REAL,
                all_time_high REAL,
                prev_price REAL,
                last_alerted_at TEXT,
                alert_count INTEGER NOT NULL DEFAULT 0,
                early_warned INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                key TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(provider, key)
            );
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after the initial schema without breaking existing DBs."""
        existing = {row[1] for row in self.conn.execute("PRAGMA table_info(watchlist)")}
        new_cols = {
            "all_time_high": "REAL",
            "prev_price": "REAL",
            "last_alerted_at": "TEXT",
            "alert_count": "INTEGER NOT NULL DEFAULT 0",
            "early_warned": "INTEGER NOT NULL DEFAULT 0",
        }
        for col, typedef in new_cols.items():
            if col not in existing:
                self.conn.execute(f"ALTER TABLE watchlist ADD COLUMN {col} {typedef}")
        self.conn.commit()

    # ------------------------------------------------------------------ tokens

    def claim_token_for_scan(self, address: str, reason: str = "") -> bool:
        address = address.lower()
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO scanned_tokens(address, last_reason) VALUES (?, ?)",
            (address, reason),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def was_alerted(self, address: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM alerted_tokens WHERE address=?", (address.lower(),)).fetchone()
        return row is not None

    def mark_alerted(self, address: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO alerted_tokens(address) VALUES (?)", (address.lower(),))
        self.conn.commit()

    # ---------------------------------------------------------------- watchlist

    def add_to_watchlist(self, address: str, data: str, created_at: str, initial_price: Optional[float] = None) -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO watchlist
               (address, data, initial_price, created_at)
               VALUES (?, ?, ?, ?)""",
            (address.lower(), data, initial_price, created_at),
        )
        self.conn.commit()

    def in_watchlist(self, address: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM watchlist WHERE address=?", (address.lower(),)).fetchone()
        return row is not None

    def get_watchlist(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT address, data, initial_price, all_time_high, prev_price,
                      last_alerted_at, alert_count, early_warned, created_at
               FROM watchlist ORDER BY added_at"""
        ).fetchall()
        return [dict(r) for r in rows]

    def update_watchlist_state(
        self,
        address: str,
        *,
        initial_price: Optional[float] = None,
        all_time_high: Optional[float] = None,
        prev_price: Optional[float] = None,
        last_alerted_at: Optional[str] = None,
        alert_count: Optional[int] = None,
        early_warned: Optional[bool] = None,
    ) -> None:
        """Update any subset of mutable watchlist columns in one call."""
        updates: list[str] = []
        params: list[Any] = []
        if initial_price is not None:
            updates.append("initial_price=?"); params.append(initial_price)
        if all_time_high is not None:
            updates.append("all_time_high=?"); params.append(all_time_high)
        if prev_price is not None:
            updates.append("prev_price=?"); params.append(prev_price)
        if last_alerted_at is not None:
            updates.append("last_alerted_at=?"); params.append(last_alerted_at)
        if alert_count is not None:
            updates.append("alert_count=?"); params.append(alert_count)
        if early_warned is not None:
            updates.append("early_warned=?"); params.append(int(early_warned))
        if not updates:
            return
        params.append(address.lower())
        self.conn.execute(f"UPDATE watchlist SET {', '.join(updates)} WHERE address=?", params)
        self.conn.commit()

    # Keep the old single-field setter as a thin wrapper for backwards compat.
    def set_watchlist_initial_price(self, address: str, initial_price: float) -> None:
        self.update_watchlist_state(address, initial_price=initial_price)

    def remove_from_watchlist(self, address: str) -> None:
        self.conn.execute("DELETE FROM watchlist WHERE address=?", (address.lower(),))
        self.conn.commit()

    def watchlist_size(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM watchlist").fetchone()
        return int(row["c"]) if row else 0

    # ----------------------------------------------------------------- api keys

    def add_api_key(self, provider: str, key: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO api_keys(provider, key) VALUES (?, ?)", (provider, key))
        self.conn.commit()

    def remove_api_key(self, provider: str, key_id: int) -> None:
        self.conn.execute("DELETE FROM api_keys WHERE provider=? AND id=?", (provider, key_id))
        self.conn.commit()

    def get_api_key_values(self, provider: str) -> list[str]:
        return [r["key"] for r in self.conn.execute("SELECT key FROM api_keys WHERE provider=? ORDER BY id", (provider,))]

    def list_api_keys(self, provider: str) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT id, key, created_at FROM api_keys WHERE provider=? ORDER BY id", (provider,)).fetchall()
        return [{"id": r["id"], "masked": self._mask(r["key"]), "created_at": r["created_at"]} for r in rows]

    @staticmethod
    def _mask(key: str) -> str:
        if len(key) <= 8:
            return key[:2] + "***"
        return key[:4] + "***" + key[-4:]

    # ------------------------------------------------------------------ config

    def save_config(self, cfg: BotConfig) -> None:
        for key, value in asdict(cfg).items():
            self.conn.execute("INSERT OR REPLACE INTO config(key, value) VALUES (?, ?)", (key, json.dumps(value)))
        self.conn.commit()

    def load_config(self) -> BotConfig:
        cfg = BotConfig()
        rows = self.conn.execute("SELECT key, value FROM config").fetchall()
        data = asdict(cfg)
        for row in rows:
            if row["key"] in data:
                data[row["key"]] = json.loads(row["value"])
        return BotConfig(**data)

    # ----------------------------------------------------------------------- kv

    def set_kv(self, key: str, value: Any) -> None:
        self.conn.execute("INSERT OR REPLACE INTO kv(key, value) VALUES (?, ?)", (key, json.dumps(value)))
        self.conn.commit()

    def get_kv(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return default if row is None else json.loads(row["value"])
