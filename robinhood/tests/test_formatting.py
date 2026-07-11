from datetime import datetime, timezone, timedelta

from robinhood_pump_bot.models import TokenCandidate, TokenMetrics
from robinhood_pump_bot.formatting import format_alert


def test_alert_contains_required_fields():
    token = TokenCandidate(
        address="0xabc",
        symbol="ABC",
        name="ABC Token",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=12),
        creator="0xcreator",
        total_supply=1000,
        metrics=TokenMetrics(price_usd=1.23, price_change_percent=74, market_cap_usd=123456, liquidity_usd=18900, developer_share_percent=8.2, top10_share_percent=41.5),
        dex_url="https://dexscreener.com/robinhood/0xabc",
    )

    text = format_alert(token)

    assert "ТИКЕР" in text
    assert "ABC" in text
    assert "СКОЛЬКО ТОКЕН АКТИВЕН" in text
    assert "МАРКЕТ КАП" in text
    assert "ЛИКВИДНОСТЬ" in text
    assert "КОЛ-ВО ТОКЕНОВ У РАЗРАБА" in text
    assert "КОНТРАКТ ТОКЕНА" in text
    assert "ССЫЛКА НА ДЕКСЕ" in text
