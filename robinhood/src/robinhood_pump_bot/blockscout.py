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
    except (TypeError, ValueError):
        return None


class BlockscoutClient:
    """Blockscout PRO API client.

    Correct Robinhood Chain REST route:
    https://api.blockscout.com/4663/api/v2/...?...&apikey=KEY

    Correct Etherscan-compatible v2 route:
    https://api.blockscout.com/v2/api?chain_id=4663&...&apikey=KEY
    """

    def __init__(self, http: HttpJsonClient, chain_id: int = 4663):
        self.http = http
        self.chain_id = chain_id
        self.rest_base = f"https://api.blockscout.com/{chain_id}/api/v2"
        self.compat_base = "https://api.blockscout.com/v2/api"
        self.explorer_base = "https://robinhoodchain.blockscout.com/api/v2"

    async def stats(self) -> Optional[dict[str, Any]]:
        return await self._get_first_ok(f"{self.rest_base}/stats", f"{self.explorer_base}/stats")

    async def address_transactions(self, address: str) -> Optional[dict[str, Any]]:
        return await self._get_first_ok(f"{self.rest_base}/addresses/{address}/transactions")

    async def token_info(self, token_address: str) -> Optional[dict[str, Any]]:
        return await self._get_first_ok(
            f"{self.rest_base}/tokens/{token_address}",
            f"{self.explorer_base}/tokens/{token_address}",
        )

    async def token_type(self, token_address: str) -> Optional[str]:
        """Return the token standard as reported by Blockscout, e.g. 'ERC-20',
        'ERC-721', 'ERC-1155'. Returns None if the token isn't indexed yet."""
        info = await self.token_info(token_address)
        if not info:
            return None
        t = info.get("type")
        return str(t).upper() if t else None

    async def token_holders(self, token_address: str) -> Optional[dict[str, Any]]:
        return await self._get_first_ok(
            f"{self.rest_base}/tokens/{token_address}/holders",
            f"{self.explorer_base}/tokens/{token_address}/holders",
        )

    async def token_balance(self, token_address: str, holder: str) -> Optional[float]:
        data = await self._get_first_ok(
            self.compat_base,
            params={
                "chain_id": self.chain_id,
                "module": "account",
                "action": "tokenbalance",
                "contractaddress": token_address,
                "address": holder,
            },
        )
        if not data or data.get("status") != "1":
            return None
        return _to_float(data.get("result"))

    async def concentration_metrics(self, token_address: str, creator: Optional[str], total_supply_raw: Optional[int]) -> tuple[Optional[float], Optional[float]]:
        if not total_supply_raw:
            return None, None
        dev_share = None
        if creator:
            balance = await self.token_balance(token_address, creator)
            if balance is not None:
                dev_share = balance / total_supply_raw * 100

        top10_share = None
        holders = await self.token_holders(token_address)
        items = holders.get("items", []) if holders else []
        if items:
            top10_total = sum((_to_float(item.get("value")) or 0) for item in items[:10])
            top10_share = top10_total / total_supply_raw * 100
        return dev_share, top10_share

    async def _get_first_ok(self, *urls: str, params: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
        for url in urls:
            try:
                data = await self.http.get(url, params=params)
                if isinstance(data, dict):
                    return data
            except Exception as exc:  # noqa: BLE001
                log.debug("Blockscout unavailable via %s: %s", url, exc)
        return None
