from __future__ import annotations

import logging
from typing import Any, Optional

from .http_client import HttpJsonClient

log = logging.getLogger(__name__)


class DexScreenerClient:
    BASE = "https://api.dexscreener.com/latest/dex/tokens"

    def __init__(self, http: HttpJsonClient):
        self.http = http

    async def token_pair(self, token_address: str) -> Optional[dict[str, Any]]:
        try:
            data = await self.http.get(f"{self.BASE}/{token_address}")
            pairs = data.get("pairs") or []
            if not pairs:
                return None
            # Prefer explicit Robinhood chain if DexScreener adds it; otherwise use highest liquidity.
            pairs.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)
            for p in pairs:
                if str(p.get("chainId", "")).lower() in {"robinhood", "robinhoodchain", "4663"}:
                    return p
            return pairs[0]
        except Exception as exc:  # noqa: BLE001
            log.debug("DexScreener unavailable for %s: %s", token_address, exc)
            return None
