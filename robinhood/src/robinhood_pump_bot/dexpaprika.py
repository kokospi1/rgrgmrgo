from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from .http_client import HttpJsonClient

log = logging.getLogger(__name__)


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:  # noqa: BLE001
        return None


def _parse_iso_unix(value: Any) -> Optional[int]:
    """Parse an RFC3339 timestamp (e.g. '2026-07-11T04:55:22Z') into a unix timestamp."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:  # noqa: BLE001
        return None


class DexPaprikaClient:
    """Primary data source for Robinhood Chain token metrics.

    Public beta, no API key required. Docs: https://docs.dexpaprika.com
    The token endpoint returns latest price, FDV (market cap proxy), liquidity and
    per-timeframe volume/txn metrics, but NOT a ready-made price-change percent.
    The pump percentage is therefore computed as growth from the token's initial
    (launch) price, obtained via ``launch_price`` from the oldest pool's OHLCV.
    """

    BASE = "https://api.dexpaprika.com"

    def __init__(self, http: HttpJsonClient, network: str = "robinhood"):
        self.http = http
        self.network = network

    async def token_summary(self, token_address: str) -> Optional[dict[str, Any]]:
        """Return normalized metrics for a token, or None if unavailable."""
        url = f"{self.BASE}/networks/{self.network}/tokens/{token_address}"
        try:
            data = await self.http.get(url)
        except Exception as exc:  # noqa: BLE001
            log.debug("DexPaprika unavailable for %s: %s", token_address, exc)
            return None
        if not isinstance(data, dict):
            return None
        summary = data.get("summary") or {}
        price = _to_float(summary.get("price_usd"))
        market_cap = _to_float(summary.get("fdv")) or _to_float(data.get("fdv"))
        liquidity = _to_float(summary.get("liquidity_usd"))
        pools = summary.get("pools")
        # No data yet (token not indexed by any pool) -> treat as unavailable.
        if price is None and market_cap is None and liquidity is None and not pools:
            return None
        return {
            "symbol": data.get("symbol"),
            "name": data.get("name"),
            "price_usd": price,
            "market_cap_usd": market_cap,
            "liquidity_usd": liquidity,
            "explorer": data.get("explorer"),
            "pools": pools,
        }

    async def token_pools(self, token_address: str) -> list[dict[str, Any]]:
        """List the liquidity pools a token trades in (each has id + created_at)."""
        url = f"{self.BASE}/networks/{self.network}/tokens/{token_address}/pools"
        try:
            data = await self.http.get(url, params={"limit": 10})
        except Exception as exc:  # noqa: BLE001
            log.debug("DexPaprika token_pools unavailable for %s: %s", token_address, exc)
            return []
        if isinstance(data, dict):
            return data.get("pools") or []
        return data if isinstance(data, list) else []

    async def _pool_ohlcv(self, pool_id: str, start_unix: int, interval: str = "5m", limit: int = 100) -> list[dict[str, Any]]:
        url = f"{self.BASE}/networks/{self.network}/pools/{pool_id}/ohlcv"
        try:
            data = await self.http.get(url, params={"start": start_unix, "interval": interval, "limit": limit})
        except Exception as exc:  # noqa: BLE001
            log.debug("DexPaprika ohlcv unavailable for pool %s: %s", pool_id, exc)
            return []
        return data if isinstance(data, list) else []

    async def launch_price(self, token_address: str) -> Optional[float]:
        """Return the token's initial (launch) USD price.

        This is the ``open`` of the very first candle of the token's oldest pool,
        i.e. the price at the moment liquidity was first added / the token started
        trading. Used to measure the pump from the token's real starting price
        (not merely the first price the bot happened to observe)."""
        pools = await self.token_pools(token_address)
        if not pools:
            return None
        # Oldest pool first (earliest liquidity == the launch pool).
        pools_with_ts = [(p, _parse_iso_unix(p.get("created_at"))) for p in pools]
        pools_with_ts = [(p, ts) for p, ts in pools_with_ts if ts is not None and p.get("id")]
        if not pools_with_ts:
            return None
        pools_with_ts.sort(key=lambda pt: pt[1])
        oldest_pool, created_unix = pools_with_ts[0]
        # 1m candles are not populated on this network, so 5m is the finest usable
        # granularity. Start a little before creation so the first candle is included.
        candles = await self._pool_ohlcv(oldest_pool["id"], max(0, created_unix - 600), interval="5m", limit=100)
        if not candles:
            return None
        return _to_float(candles[0].get("open"))
