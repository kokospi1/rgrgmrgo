from datetime import datetime, timezone, timedelta

from robinhood_pump_bot.models import BotConfig, TokenCandidate, TokenMetrics
from robinhood_pump_bot.filters import passes_filters


def candidate(age_minutes=10, pump=55, dev_share=90, top10=90):
    now = datetime.now(timezone.utc)
    return TokenCandidate(
        address="0xabc",
        symbol="ABC",
        name="ABC Token",
        created_at=now - timedelta(minutes=age_minutes),
        creator="0xcreator",
        total_supply=1000,
        metrics=TokenMetrics(price_usd=1, price_change_percent=pump, market_cap_usd=1000, liquidity_usd=500, developer_share_percent=dev_share, top10_share_percent=top10),
    )


def test_default_ignores_dev_and_top10_filters():
    cfg = BotConfig(max_age_minutes=30, min_pump_percent=50, dev_share_filter_enabled=False, top10_filter_enabled=False, max_dev_share_percent=1, max_top10_share_percent=1)

    assert passes_filters(candidate(), cfg) is True


def test_rejects_old_token():
    cfg = BotConfig(max_age_minutes=30, min_pump_percent=50)
    assert passes_filters(candidate(age_minutes=31), cfg) is False


def test_optional_dev_share_filter():
    cfg = BotConfig(max_age_minutes=30, min_pump_percent=50, dev_share_filter_enabled=True, max_dev_share_percent=10)
    assert passes_filters(candidate(dev_share=11), cfg) is False


def test_optional_top10_share_filter():
    cfg = BotConfig(max_age_minutes=30, min_pump_percent=50, top10_filter_enabled=True, max_top10_share_percent=50)
    assert passes_filters(candidate(top10=51), cfg) is False
