from __future__ import annotations

from .models import BotConfig, TokenCandidate


def passes_filters(token: TokenCandidate, cfg: BotConfig) -> bool:
    if token.age_minutes > cfg.max_age_minutes:
        return False
    pump = token.metrics.price_change_percent
    if pump is None or pump < cfg.min_pump_percent:
        return False
    if cfg.dev_share_filter_enabled:
        dev_share = token.metrics.developer_share_percent
        if dev_share is None or dev_share > cfg.max_dev_share_percent:
            return False
    if cfg.top10_filter_enabled:
        top10 = token.metrics.top10_share_percent
        if top10 is None or top10 > cfg.max_top10_share_percent:
            return False
    return True
