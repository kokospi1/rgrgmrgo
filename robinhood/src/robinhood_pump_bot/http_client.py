from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional
from urllib.parse import urlencode

import aiohttp

from .rate_limiter import ApiKeyPool

log = logging.getLogger(__name__)


class HttpJsonClient:
    def __init__(self, session: aiohttp.ClientSession, pool: ApiKeyPool, key_mode: str, header_name: str = "x-api-key", query_name: str = "apikey"):
        self.session = session
        self.pool = pool
        self.key_mode = key_mode  # header|query|none
        self.header_name = header_name
        self.query_name = query_name

    async def get(self, url: str, params: Optional[dict[str, Any]] = None, timeout: int = 20) -> Any:
        return await self.request("GET", url, params=params, timeout=timeout)

    async def post(self, url: str, json_body: Optional[dict[str, Any]] = None, timeout: int = 20) -> Any:
        return await self.request("POST", url, json_body=json_body, timeout=timeout)

    async def request(self, method: str, url: str, params: Optional[dict[str, Any]] = None, json_body: Optional[dict[str, Any]] = None, timeout: int = 20) -> Any:
        params = dict(params or {})
        last_error: Exception | None = None
        for _ in range(5):
            key = await self.pool.acquire()
            headers = {}
            if key and self.key_mode == "header":
                headers[self.header_name] = key
            if key and self.key_mode == "query":
                params[self.query_name] = key
            try:
                async with self.session.request(method, url, params=params, json=json_body, headers=headers, timeout=timeout) as resp:
                    text = await resp.text()
                    if resp.status in (401, 403, 429):
                        self.pool.mark_exhausted(key, seconds=60 if resp.status == 429 else 3600)
                        last_error = RuntimeError(f"{resp.status}: {text[:200]}")
                        continue
                    resp.raise_for_status()
                    if not text:
                        return None
                    return await resp.json(content_type=None)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                log.warning("HTTP %s %s failed: %s", method, url, exc)
                await asyncio.sleep(0.2)
        raise last_error or RuntimeError("request failed")
