import pytest

from robinhood_pump_bot.blockscout import BlockscoutClient
from robinhood_pump_bot.rate_limiter import ApiKeyPool


class FakeHttp:
    def __init__(self):
        self.calls = []

    async def get(self, url, params=None):
        self.calls.append((url, params))
        if url.endswith("/tokens/0xtoken/holders"):
            return {"items": [{"value": "300"}, {"value": "200"}, {"value": "100"}]}
        if params and params.get("action") == "tokenbalance":
            return {"status": "1", "result": "250"}
        return {}


@pytest.mark.asyncio
async def test_blockscout_uses_robinhood_chain_id_in_rest_route():
    http = FakeHttp()
    client = BlockscoutClient(http, chain_id=4663)

    await client.token_info("0xtoken")

    assert http.calls[0][0] == "https://api.blockscout.com/4663/api/v2/tokens/0xtoken"


@pytest.mark.asyncio
async def test_blockscout_uses_chain_id_param_for_etherscan_compatible_route():
    http = FakeHttp()
    client = BlockscoutClient(http, chain_id=4663)

    balance = await client.token_balance("0xtoken", "0xholder")

    assert balance == 250
    assert http.calls[0][0] == "https://api.blockscout.com/v2/api"
    assert http.calls[0][1]["chain_id"] == 4663
    assert http.calls[0][1]["contractaddress"] == "0xtoken"


@pytest.mark.asyncio
async def test_blockscout_concentration_metrics():
    http = FakeHttp()
    client = BlockscoutClient(http, chain_id=4663)

    dev, top10 = await client.concentration_metrics("0xtoken", "0xdev", total_supply_raw=1000)

    assert dev == 25
    assert top10 == 60
