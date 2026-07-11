import pytest

from robinhood_pump_bot.scanner import RobinhoodRpcClient, TokenScanner
from robinhood_pump_bot.storage import Storage


class SplittingRpc(RobinhoodRpcClient):
    def __init__(self):
        super().__init__(min_interval_seconds=0)
        self.raw_ranges = []

    async def _get_logs_raw(self, from_block, to_block):
        self.raw_ranges.append((from_block, to_block))
        if from_block == 1 and to_block == 4:
            raise RuntimeError({"code": -32000, "message": "logs matched by query exceeds limit of 10000"})
        return [{"address": f"0x{from_block:040x}", "transactionHash": "0xhash", "blockNumber": hex(from_block)}]


@pytest.mark.asyncio
async def test_get_logs_splits_range_when_rpc_log_limit_is_hit():
    rpc = SplittingRpc()

    logs = await rpc.get_logs(1, 4)

    assert rpc.raw_ranges == [(1, 4), (1, 2), (3, 4)]
    assert len(logs) == 2


class RateLimitedRpc(RobinhoodRpcClient):
    def __init__(self):
        super().__init__(min_interval_seconds=0)
        self.calls = 0

    async def _throttle(self):
        return None

    async def _sleep(self, seconds: float):
        return None


@pytest.mark.asyncio
async def test_rpc_retries_429_without_failing(monkeypatch):
    rpc = RateLimitedRpc()

    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def json(self, content_type=None):
            rpc.calls += 1
            if rpc.calls == 1:
                return {"error": {"code": 429, "message": "Too Many Requests"}}
            return {"result": "0x1"}

    class Session:
        def post(self, *args, **kwargs):
            return Response()

    async def fake_sleep(_):
        return None

    monkeypatch.setattr("robinhood_pump_bot.scanner.asyncio.sleep", fake_sleep)
    rpc.session = Session()

    assert await rpc.call("eth_blockNumber", []) == "0x1"
    assert rpc.calls == 2


class FakeRpc:
    def __init__(self):
        self.ranges = []

    async def block_number(self):
        return 1_000

    async def get_logs(self, from_block, to_block):
        self.ranges.append((from_block, to_block))
        return []


@pytest.mark.asyncio
async def test_discover_starts_near_latest_on_first_run(tmp_path):
    storage = Storage(tmp_path / "bot.db")
    rpc = FakeRpc()
    scanner = TokenScanner(storage, rpc, relay=None, dex=None)

    await scanner.discover_new_tokens(lookback_blocks=120)

    assert rpc.ranges == [(880, 899)]
    assert storage.get_kv("last_scanned_block") == 899
