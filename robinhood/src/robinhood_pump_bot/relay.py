from __future__ import annotations

import logging
from typing import Optional

from .http_client import HttpJsonClient

log = logging.getLogger(__name__)

ZERO_NATIVE = "0x0000000000000000000000000000000000000000"


class RelayClient:
    BASE = "https://api.relay.link"

    def __init__(self, http: HttpJsonClient, chain_id: int = 4663):
        self.http = http
        self.chain_id = chain_id

    async def token_price(self, token_address: str) -> Optional[float]:
        try:
            data = await self.http.get(f"{self.BASE}/currencies/token/price", {"address": token_address, "chainId": self.chain_id})
            price = data.get("price") if isinstance(data, dict) else None
            return float(price) if price is not None else None
        except Exception as exc:  # noqa: BLE001
            log.debug("Relay price unavailable for %s: %s", token_address, exc)
            return None

    async def supports_chain(self) -> bool:
        try:
            data = await self.http.get(f"{self.BASE}/chains")
            chains = data.get("chains", data) if isinstance(data, dict) else data
            return any(str(c.get("id") or c.get("chainId")) == str(self.chain_id) for c in chains)
        except Exception:
            return False
