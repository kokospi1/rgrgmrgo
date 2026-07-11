from datetime import datetime, timezone, timedelta

from robinhood_pump_bot.models import TokenCandidate, TokenMetrics
from robinhood_pump_bot.scanner import TokenScanner


class FakeStorage:
    pass


class FakeRelay:
    async def token_price(self, address):
        return None


class FakeDex:
    async def token_pair(self, address):
        return None


class FakeBlockscout:
    async def token_info(self, address):
        return {"symbol": "BSC", "name": "Block Scout", "exchange_rate": "2", "circulating_market_cap": "1000"}

    async def concentration_metrics(self, address, creator, total_supply_raw):
        return 12.5, 55.0


def candidate():
    return TokenCandidate(
        address="0xabc",
        symbol="ABC",
        name="ABC Token",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        creator="0xcreator",
        total_supply=500,
        total_supply_raw=500_000,
        metrics=TokenMetrics(price_change_percent=55),
    )


async def test_scanner_enriches_holder_metrics_from_blockscout():
    scanner = TokenScanner(FakeStorage(), rpc=None, relay=FakeRelay(), dex=FakeDex(), blockscout=FakeBlockscout())

    token = await scanner.enrich_metrics(candidate())

    assert token.symbol == "BSC"
    assert token.metrics.price_usd == 2
    assert token.metrics.market_cap_usd == 1000
    assert token.metrics.developer_share_percent == 12.5
    assert token.metrics.top10_share_percent == 55.0
