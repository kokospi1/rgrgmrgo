from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class BotConfig:
    max_age_minutes: int = 30
    min_pump_percent: float = 50.0
    dev_share_filter_enabled: bool = False
    max_dev_share_percent: float = 20.0
    top10_filter_enabled: bool = False
    max_top10_share_percent: float = 60.0
    scan_interval_seconds: int = 30
    lookback_blocks: int = 20
    paused: bool = False


@dataclass
class TokenMetrics:
    price_usd: Optional[float] = None
    price_change_percent: Optional[float] = None
    market_cap_usd: Optional[float] = None
    liquidity_usd: Optional[float] = None
    developer_share_percent: Optional[float] = None
    top10_share_percent: Optional[float] = None


@dataclass
class TokenCandidate:
    address: str
    symbol: str = "UNKNOWN"
    name: str = "Unknown Token"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    creator: Optional[str] = None
    total_supply: Optional[float] = None
    total_supply_raw: Optional[int] = None
    decimals: int = 18
    metrics: TokenMetrics = field(default_factory=TokenMetrics)
    dex_url: Optional[str] = None
    tx_hash: Optional[str] = None

    @property
    def age_minutes(self) -> float:
        return max(0.0, (datetime.now(timezone.utc) - self.created_at).total_seconds() / 60)

    def to_json(self) -> str:
        """Serialize immutable discovery fields for the watchlist (metrics are refreshed live)."""
        return json.dumps({
            "address": self.address,
            "symbol": self.symbol,
            "name": self.name,
            "created_at": self.created_at.isoformat(),
            "creator": self.creator,
            "total_supply": self.total_supply,
            "total_supply_raw": self.total_supply_raw,
            "decimals": self.decimals,
            "dex_url": self.dex_url,
            "tx_hash": self.tx_hash,
        })

    @classmethod
    def from_json(cls, raw: str) -> "TokenCandidate":
        d = json.loads(raw)
        created = d.get("created_at")
        created_at = datetime.fromisoformat(created) if created else datetime.now(timezone.utc)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return cls(
            address=d["address"],
            symbol=d.get("symbol", "UNKNOWN"),
            name=d.get("name", "Unknown Token"),
            created_at=created_at,
            creator=d.get("creator"),
            total_supply=d.get("total_supply"),
            total_supply_raw=d.get("total_supply_raw"),
            decimals=d.get("decimals", 18),
            metrics=TokenMetrics(),
            dex_url=d.get("dex_url"),
            tx_hash=d.get("tx_hash"),
        )
