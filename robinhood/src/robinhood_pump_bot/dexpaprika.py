from __future__ import annotations

import logging
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


class DexPaprikaClient:
    """Primary data source for Robinhood Chain token metrics.

    Public beta, no API key required. Docs: https://docs.dexpaprika.com
    The token endpoint returns latest price, FDV (market cap proxy), liquidity and
    per-timeframe volume/txn metrics, but NOT a ready-made price-change percent —
    so the pump percentage is computed from a stored base price (see scanner).
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
