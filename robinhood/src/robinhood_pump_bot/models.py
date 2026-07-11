from __future__ import annotations

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
