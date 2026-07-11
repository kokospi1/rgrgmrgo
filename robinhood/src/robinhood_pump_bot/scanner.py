from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

from .blockscout import BlockscoutClient
from .dex import DexScreenerClient
from .dexpaprika import DexPaprikaClient
from .models import TokenCandidate, TokenMetrics
from .relay import RelayClient
from .storage import Storage

log = logging.getLogger(__name__)

ERC20_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_TOPIC = "0x" + "0" * 64

ERC20_ABI_CALLS = {
    "symbol": "0x95d89b41",
    "name": "0x06fdde03",
    "decimals": "0x313ce567",
    "totalSupply": "0x18160ddd",
}

MAX_LOG_BLOCKS_PER_TICK = 20


class RobinhoodRpcClient:
    def __init__(self, rpc_url: str = "https://rpc.mainnet.chain.robinhood.com", session: aiohttp.ClientSession | None = None, min_interval_seconds: float = 0.35):
        self.rpc_url = rpc_url
        self.session = session
        self.min_interval_seconds = min_interval_seconds
        self._id = 0
        self._last_request_at = 0.0
        self._request_lock = asyncio.Lock()

    async def call(self, method: str, params: list[Any]) -> Any:
        last_error: RuntimeError | None = None
        for attempt in range(6):
            await self._throttle()
            self._id += 1
            owns_session = self.session is None
            session = self.session or aiohttp.ClientSession()
            try:
                async with session.post(self.rpc_url, json={"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}, timeout=20) as resp:
                    data = await resp.json(content_type=None)
                    if "error" in data:
                        err = RuntimeError(data["error"])
                        if _is_rate_limit_error(err):
                            last_error = err
                            delay = min(20.0, 2.0 * (attempt + 1))
                            log.warning("Robinhood RPC 429/rate-limit on %s; sleeping %.1fs", method, delay)
                            await asyncio.sleep(delay)
                            continue
                        raise err
                    return data.get("result")
            finally:
                if owns_session:
                    await session.close()
        raise last_error or RuntimeError({"code": 429, "message": "Too Many Requests"})

    async def _throttle(self) -> None:
        async with self._request_lock:
            now = asyncio.get_running_loop().time()
            wait = self.min_interval_seconds - (now - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = asyncio.get_running_loop().time()

    async def block_number(self) -> int:
        return int(await self.call("eth_blockNumber", []), 16)

    async def get_logs(self, from_block: int, to_block: int) -> list[dict[str, Any]]:
        """Fetch mint Transfer logs, splitting ranges when Robinhood RPC hits its 10k log cap."""
        try:
            return await self._get_logs_raw(from_block, to_block)
        except RuntimeError as exc:
            if not _is_log_limit_error(exc):
                raise
            if from_block >= to_block:
                log.warning("RPC log cap hit for single block %s; skipping that block", from_block)
                return []
            mid = (from_block + to_block) // 2
            left = await self.get_logs(from_block, mid)
            await asyncio.sleep(self.min_interval_seconds)
            right = await self.get_logs(mid + 1, to_block)
            return [*left, *right]

    async def _get_logs_raw(self, from_block: int, to_block: int) -> list[dict[str, Any]]:
        return await self.call("eth_getLogs", [{"fromBlock": hex(from_block), "toBlock": hex(to_block), "topics": [ERC20_TRANSFER_TOPIC, ZERO_TOPIC]}])

    async def get_transaction(self, tx_hash: str) -> dict[str, Any]:
        return await self.call("eth_getTransactionByHash", [tx_hash])

    async def get_block(self, block_num_hex: str) -> dict[str, Any]:
        return await self.call("eth_getBlockByNumber", [block_num_hex, False])

    async def eth_call(self, to: str, data: str) -> Optional[str]:
        try:
            return await self.call("eth_call", [{"to": to, "data": data}, "latest"])
        except Exception:
            return None


class TokenScanner:
    def __init__(self, storage: Storage, rpc: RobinhoodRpcClient, relay: RelayClient, dex: DexScreenerClient, blockscout: BlockscoutClient | None = None, dexpaprika: DexPaprikaClient | None = None):
        self.storage = storage
        self.rpc = rpc
        self.relay = relay
        self.dex = dex
        self.blockscout = blockscout
        self.dexpaprika = dexpaprika

    async def discover_new_tokens(self, lookback_blocks: int) -> list[TokenCandidate]:
        latest = await self.rpc.block_number()
        default_start = max(0, latest - lookback_blocks)
        last_scanned = self.storage.get_kv("last_scanned_block")
        from_block = default_start if last_scanned is None else max(0, min(int(last_scanned) + 1, latest))
        if from_block > latest:
            return []

        span = min(max(1, lookback_blocks), MAX_LOG_BLOCKS_PER_TICK)
        to_block = min(latest, from_block + span - 1)
        logs = await self.rpc.get_logs(from_block, to_block)
        self.storage.set_kv("last_scanned_block", to_block)

        candidates: list[TokenCandidate] = []
        seen_in_batch: set[str] = set()
        for item in logs:
            token_addr = item.get("address", "").lower()
            if not token_addr or token_addr in seen_in_batch:
                continue
            seen_in_batch.add(token_addr)
            if not self.storage.claim_token_for_scan(token_addr, "mint_transfer_seen"):
                continue
            try:
                candidate = await self._build_candidate(token_addr, item)
                candidates.append(candidate)
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to build token candidate %s: %s", token_addr, exc)
        return candidates

    async def get_launch_price(self, token: TokenCandidate) -> Optional[float]:
        """Return the token's initial (launch) USD price, or None if not available yet."""
        if not self.dexpaprika:
            return None
        return await self.dexpaprika.launch_price(token.address)

    async def enrich_metrics(self, token: TokenCandidate) -> TokenCandidate:
        """Refresh live metrics. Price/marketcap/liquidity come from DexPaprika first,
        with DexScreener and the Relay price as fallbacks. The pump percentage is NOT
        derived here — it is computed by the monitor as growth from the token's
        initial (launch) price (see get_launch_price)."""
        if self.blockscout:
            info = await self.blockscout.token_info(token.address)
            if info:
                token.symbol = info.get("symbol") or token.symbol
                token.name = info.get("name") or token.name
                token.metrics.market_cap_usd = _to_float(info.get("circulating_market_cap")) or token.metrics.market_cap_usd
                token.metrics.price_usd = _to_float(info.get("exchange_rate")) or token.metrics.price_usd
            dev_share, top10_share = await self.blockscout.concentration_metrics(token.address, token.creator, token.total_supply_raw)
            token.metrics.developer_share_percent = dev_share
            token.metrics.top10_share_percent = top10_share

        # Primary source: DexPaprika (Robinhood Chain).
        if self.dexpaprika:
            summary = await self.dexpaprika.token_summary(token.address)
            if summary:
                token.symbol = summary.get("symbol") or token.symbol
                token.name = summary.get("name") or token.name
                if summary.get("price_usd") is not None:
                    token.metrics.price_usd = summary["price_usd"]
                if summary.get("market_cap_usd") is not None:
                    token.metrics.market_cap_usd = summary["market_cap_usd"]
                if summary.get("liquidity_usd") is not None:
                    token.metrics.liquidity_usd = summary["liquidity_usd"]

        # Fallback source: DexScreener.
        if token.metrics.price_usd is None or token.metrics.liquidity_usd is None:
            pair = await self.dex.token_pair(token.address)
            if pair:
                token.metrics.price_usd = token.metrics.price_usd or _to_float(pair.get("priceUsd"))
                token.metrics.market_cap_usd = token.metrics.market_cap_usd or _to_float(pair.get("marketCap") or pair.get("fdv"))
                if token.metrics.liquidity_usd is None:
                    token.metrics.liquidity_usd = _to_float((pair.get("liquidity") or {}).get("usd"))
                token.dex_url = token.dex_url or pair.get("url")

        if token.metrics.price_usd is None:
            token.metrics.price_usd = await self.relay.token_price(token.address)
        if token.metrics.market_cap_usd is None and token.metrics.price_usd and token.total_supply:
            token.metrics.market_cap_usd = token.metrics.price_usd * token.total_supply
        if not token.dex_url:
            token.dex_url = f"https://dexscreener.com/search?q={token.address}"
        return token

    async def _build_candidate(self, token_addr: str, log_item: dict[str, Any]) -> TokenCandidate:
        tx_hash = log_item.get("transactionHash")
        tx = await self.rpc.get_transaction(tx_hash) if tx_hash else {}
        block = await self.rpc.get_block(log_item.get("blockNumber", "latest"))
        created_at = datetime.fromtimestamp(int(block.get("timestamp", "0x0"), 16), tz=timezone.utc)
        creator = tx.get("from")

        symbol, name, decimals, supply = await asyncio.gather(
            self._read_string(token_addr, "symbol"),
            self._read_string(token_addr, "name"),
            self._read_uint(token_addr, "decimals"),
            self._read_uint(token_addr, "totalSupply"),
        )
        decimals_int = int(decimals or 18)
        total_supply = (float(supply) / (10 ** decimals_int)) if supply is not None else None
        return TokenCandidate(
            address=token_addr,
            symbol=symbol or "UNKNOWN",
            name=name or "Unknown Token",
            created_at=created_at,
            creator=creator,
            decimals=decimals_int,
            total_supply=total_supply,
            total_supply_raw=supply,
            tx_hash=tx_hash,
            metrics=TokenMetrics(),
        )

    async def _read_uint(self, address: str, fn: str) -> Optional[int]:
        raw = await self.rpc.eth_call(address, ERC20_ABI_CALLS[fn])
        if not raw or raw == "0x":
            return None
        return int(raw, 16)

    async def _read_string(self, address: str, fn: str) -> Optional[str]:
        raw = await self.rpc.eth_call(address, ERC20_ABI_CALLS[fn])
        return decode_abi_string(raw)


def _is_rate_limit_error(exc: RuntimeError) -> bool:
    msg = str(exc).lower()
    return "too many requests" in msg or "'code': 429" in msg or '"code": 429' in msg


def _is_log_limit_error(exc: RuntimeError) -> bool:
    msg = str(exc).lower()
    return "logs matched by query exceeds limit" in msg or "exceeds limit of 10000" in msg


def decode_abi_string(raw: Optional[str]) -> Optional[str]:
    if not raw or raw == "0x":
        return None
    data = bytes.fromhex(raw[2:])
    try:
        if len(data) >= 96:
            length = int.from_bytes(data[32:64], "big")
            return data[64:64 + length].decode("utf-8", errors="ignore").strip("\x00") or None
        return data.rstrip(b"\x00").decode("utf-8", errors="ignore") or None
    except Exception:
        return None


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None
