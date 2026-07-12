from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class BotConfig:
    # --- core filters ---
    age_filter_enabled: bool = True
    max_age_minutes: int = 30
    min_pump_percent: float = 50.0
    dev_share_filter_enabled: bool = False
    max_dev_share_percent: float = 20.0
    top10_filter_enabled: bool = False
    max_top10_share_percent: float = 60.0
    scan_interval_seconds: int = 30
    lookback_blocks: int = 20
    paused: bool = False

    # --- acceleration detector (feature 1) ---
    # Triggers an [ACCELERATION] alert when the price grew by this % over the
    # last `acceleration_candles` OHLCV candles (5 min each), regardless of the
    # global pump from launch price.
    acceleration_enabled: bool = True
    min_acceleration_percent: float = 20.0   # % rise over the last N candles
    acceleration_candles: int = 3            # how many 5-min candles to look back

    # --- early warning (feature 4) ---
    # Sends a [WATCH] alert before the main pump threshold is reached.
    early_warning_enabled: bool = True
    early_warning_percent: float = 15.0      # pump from launch that triggers WATCH
    early_warning_min_liquidity: float = 0.0  # optional: only WATCH if liq >= this

    # --- cooldown / multi-wave (feature 3) ---
    # After an alert the token is re-observed; further alerts fire after a cooldown.
    cooldown_enabled: bool = True
    cooldown_minutes: int = 10               # silence window after each alert
    max_alerts_per_token: int = 3            # max total alerts per token

    # --- new-high (feature 5) ---
    new_high_enabled: bool = True
    new_high_min_correction_percent: float = 10.0  # dip before recovery counts

    # --- dynamic liquidity tiers (feature 6) ---
    # Overrides min_pump_percent based on liquidity depth.
    liq_tiers_enabled: bool = False
    liq_tier_low_usd: float = 1_000.0        # below this: high pump threshold
    liq_tier_mid_usd: float = 10_000.0       # above this: low pump threshold
    liq_tier_low_pump: float = 100.0         # pump threshold for low-liq tokens
    liq_tier_mid_pump: float = 50.0          # pump threshold for mid-liq tokens
    liq_tier_high_pump: float = 30.0         # pump threshold for high-liq tokens


@dataclass
class TokenMetrics:
    price_usd: Optional[float] = None
    price_change_percent: Optional[float] = None   # pump from launch price
    acceleration_percent: Optional[float] = None   # pump over last N candles
    market_cap_usd: Optional[float] = None
    liquidity_usd: Optional[float] = None
    prev_liquidity_usd: Optional[float] = None     # for early-warning liq check
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
