from __future__ import annotations

from typing import Optional

from .models import BotConfig, TokenCandidate

# Alert type constants — returned as the second element of check_alert_trigger().
ALERT_PUMP = "PUMP"                  # global pump from launch >= threshold
ALERT_ACCELERATION = "ACCELERATION"  # local pump over last N candles >= threshold
ALERT_EARLY = "WATCH"                # early warning: pump >= early_warning_percent
ALERT_NEW_HIGH = "NEW HIGH"          # token recovered to a new all-time high


def _effective_pump_threshold(token: TokenCandidate, cfg: BotConfig) -> float:
    """Return the pump threshold adjusted for liquidity depth (feature 6)."""
    if not cfg.liq_tiers_enabled or token.metrics.liquidity_usd is None:
        return cfg.min_pump_percent
    liq = token.metrics.liquidity_usd
    if liq < cfg.liq_tier_low_usd:
        return cfg.liq_tier_low_pump
    if liq < cfg.liq_tier_mid_usd:
        return cfg.liq_tier_mid_pump
    return cfg.liq_tier_high_pump


def _passes_base_filters(token: TokenCandidate, cfg: BotConfig) -> bool:
    """Shared pre-conditions that every alert type must satisfy."""
    if token.age_minutes > cfg.max_age_minutes:
        return False
    if cfg.dev_share_filter_enabled:
        dev = token.metrics.developer_share_percent
        if dev is None or dev > cfg.max_dev_share_percent:
            return False
    if cfg.top10_filter_enabled:
        top10 = token.metrics.top10_share_percent
        if top10 is None or top10 > cfg.max_top10_share_percent:
            return False
    return True


def check_alert_trigger(
    token: TokenCandidate,
    cfg: BotConfig,
    *,
    all_time_high: Optional[float] = None,
    prev_price: Optional[float] = None,
    early_warned: bool = False,
) -> Optional[str]:
    """Return the alert type string if any trigger fires, otherwise None.

    Evaluation order (highest priority first):
      1. PUMP       — global pump from launch >= effective threshold
      2. ACCELERATION — local pump over last N candles >= threshold
      3. NEW HIGH   — price exceeded the previous all-time high after a dip
      4. WATCH      — early warning pump threshold (fires at most once per token)
    """
    if not _passes_base_filters(token, cfg):
        return None

    pump = token.metrics.price_change_percent  # vs launch price
    accel = token.metrics.acceleration_percent  # vs N candles ago
    price = token.metrics.price_usd

    # 1. Global pump from launch price.
    threshold = _effective_pump_threshold(token, cfg)
    if pump is not None and pump >= threshold:
        return ALERT_PUMP

    # 2. Acceleration: fast local move over the last N candles.
    if cfg.acceleration_enabled and accel is not None and accel >= cfg.min_acceleration_percent:
        return ALERT_ACCELERATION

    # 3. New all-time high after a meaningful correction.
    if cfg.new_high_enabled and price is not None and all_time_high is not None and all_time_high > 0:
        correction = (all_time_high - price) / all_time_high * 100
        if price > all_time_high and correction >= cfg.new_high_min_correction_percent:
            return ALERT_NEW_HIGH

    # 4. Early warning (fires only once per token).
    if (
        cfg.early_warning_enabled
        and not early_warned
        and pump is not None
        and pump >= cfg.early_warning_percent
    ):
        liq = token.metrics.liquidity_usd or 0.0
        if liq >= cfg.early_warning_min_liquidity:
            return ALERT_EARLY

    return None


# Legacy helper kept for backwards compatibility with existing tests.
def passes_filters(token: TokenCandidate, cfg: BotConfig) -> bool:
    return check_alert_trigger(token, cfg) is not None
