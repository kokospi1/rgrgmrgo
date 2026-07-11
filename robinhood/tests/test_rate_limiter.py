import pytest

from robinhood_pump_bot.rate_limiter import ApiKeyPool


@pytest.mark.asyncio
async def test_api_key_pool_rotates_when_per_second_limit_reached():
    pool = ApiKeyPool(["k1", "k2"], per_second_limit=1, per_hour_limit=100, clock=lambda: 1000.0)

    first = await pool.acquire()
    second = await pool.acquire()

    assert first == "k1"
    assert second == "k2"


@pytest.mark.asyncio
async def test_api_key_pool_marks_key_exhausted_and_uses_next():
    pool = ApiKeyPool(["k1", "k2"], per_second_limit=5, per_hour_limit=100, clock=lambda: 1000.0)
    pool.mark_exhausted("k1", seconds=60)

    assert await pool.acquire() == "k2"


@pytest.mark.asyncio
async def test_api_key_pool_hourly_limit_rotates():
    pool = ApiKeyPool(["k1", "k2"], per_second_limit=10, per_hour_limit=1, clock=lambda: 1000.0)

    assert await pool.acquire() == "k1"
    assert await pool.acquire() == "k2"
