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

    def set_kv(self, key: str, value: Any) -> None:
        self.conn.execute("INSERT OR REPLACE INTO kv(key, value) VALUES (?, ?)", (key, json.dumps(value)))
        self.conn.commit()

    def get_kv(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return default if row is None else json.loads(row["value"])
