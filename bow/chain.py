"""On-chain reconstruction of Polymarket trades from Polygon event logs.

The Polymarket API is a lossy view of the blockchain: it truncates at ~10,000
trades per market and serves no price history at all once a market resolves.
The underlying trades are permanent public records on Polygon, so history the
API has discarded can still be rebuilt from event logs.

Decoding is validated against the API in `test_chain.py`: an on-chain
`OrderFilled` for a known transaction decodes to exactly the price, size, side
and outcome the API reports for the same fill.

Event, on both exchange contracts:

    OrderFilled(
        bytes32 indexed orderHash,
        address indexed maker,
        address indexed taker,
        uint256 makerAssetId,      data[0]
        uint256 takerAssetId,      data[1]
        uint256 makerAmountFilled, data[2]
        uint256 takerAmountFilled, data[3]
        uint256 fee                data[4]
    )

Asset id 0 is USDC. Whichever side is 0 tells you the direction: a maker whose
takerAssetId is 0 is selling outcome tokens for cash, and one whose
makerAssetId is 0 is buying them.
"""

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Verified working keyless archive endpoint (2026-08-05). Others prune logs
# after ~2 days (publicnode), immediately (drpc), or now require an API key
# (polygon-rpc.com, Ankr). Ordering matters: first entry is the archive node.
RPC_ENDPOINTS = (
    "https://rpc-mainnet.matic.quiknode.pro",
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.drpc.org",
)

EXCHANGES = (
    "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",  # CTF Exchange
    "0xC5d563A36AE78145C45a50134d48A1215220f80a",  # NegRisk CTF Exchange
)

ORDER_FILLED = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"

USDC_DECIMALS = 10 ** 6
POLYGON_BLOCK_SECONDS = 2.1

UA = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 Chrome/126"}


class ChainError(RuntimeError):
    """Raised when the RPC layer fails after exhausting every endpoint."""


class PolygonClient:
    """Minimal JSON-RPC client that fails over between public endpoints."""

    def __init__(self, endpoints: Iterable[str] = RPC_ENDPOINTS,
                 timeout: int = 60, sleep: float = 0.05) -> None:
        self._endpoints = list(endpoints)
        self._timeout = timeout
        self._sleep = sleep
        self.calls = 0
        self._block_cache: Dict[int, int] = {}

    def call(self, method: str, params: List[Any]) -> Any:
        payload = json.dumps(
            {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()
        last = ""
        for attempt in range(3):
            for url in self._endpoints:
                try:
                    request = urllib.request.Request(url, data=payload, headers=UA)
                    with urllib.request.urlopen(request, timeout=self._timeout) as resp:
                        body = json.loads(resp.read())
                    self.calls += 1
                    time.sleep(self._sleep)
                    if "error" in body:
                        last = str(body["error"].get("message", ""))[:120]
                        continue
                    return body.get("result")
                except (urllib.error.URLError, TimeoutError,
                        json.JSONDecodeError, OSError) as exc:
                    last = str(exc)[:120]
            time.sleep(1.5 * (attempt + 1))
        raise ChainError(f"{method} failed on all endpoints: {last}")

    def head(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def block_time(self, block: int) -> int:
        if block in self._block_cache:
            return self._block_cache[block]
        result = self.call("eth_getBlockByNumber", [hex(block), False])
        if not result:
            raise ChainError(f"no block {block}")
        ts = int(result["timestamp"], 16)
        self._block_cache[block] = ts
        return ts

    def block_at_time(self, target_ts: int, tolerance: int = 120) -> int:
        """Find the block nearest a unix timestamp by bisection.

        Polygon block times drift, so an arithmetic estimate is only a starting
        point; this narrows to within `tolerance` seconds.
        """
        lo, hi = 1, self.head()
        lo_ts, hi_ts = self.block_time(lo), self.block_time(hi)
        if target_ts <= lo_ts:
            return lo
        if target_ts >= hi_ts:
            return hi
        while lo < hi - 1:
            # Interpolate rather than plain-bisect: converges in far fewer calls.
            span = max(hi_ts - lo_ts, 1)
            guess = lo + int((hi - lo) * (target_ts - lo_ts) / span)
            guess = min(max(guess, lo + 1), hi - 1)
            guess_ts = self.block_time(guess)
            if abs(guess_ts - target_ts) <= tolerance:
                return guess
            if guess_ts < target_ts:
                lo, lo_ts = guess, guess_ts
            else:
                hi, hi_ts = guess, guess_ts
        return lo

    def order_filled_logs(self, from_block: int, to_block: int,
                          address: str) -> List[Dict[str, Any]]:
        return self.call("eth_getLogs", [{
            "fromBlock": hex(from_block), "toBlock": hex(to_block),
            "address": address, "topics": [ORDER_FILLED],
        }]) or []


def decode_order_filled(log: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Decode one OrderFilled log into a trade.

    Returns None for malformed logs and for token-for-token fills, which carry
    no price because neither leg is cash.
    """
    data = log.get("data", "0x")[2:]
    if len(data) < 64 * 4 or len(log.get("topics", [])) < 4:
        return None
    words = [int(data[i:i + 64], 16) for i in range(0, len(data), 64)]
    maker_asset, taker_asset, maker_amount, taker_amount = words[:4]

    if taker_asset == 0 and maker_asset != 0:
        token_id, size_raw, cash_raw, side = maker_asset, maker_amount, taker_amount, "SELL"
    elif maker_asset == 0 and taker_asset != 0:
        token_id, size_raw, cash_raw, side = taker_asset, taker_amount, maker_amount, "BUY"
    else:
        return None

    if size_raw == 0:
        return None

    return {
        "token_id": str(token_id),
        "price": cash_raw / size_raw,
        "size": size_raw / USDC_DECIMALS,
        "side": side,
        "wallet": "0x" + log["topics"][2][-40:],
        "counterparty": "0x" + log["topics"][3][-40:],
        "tx_hash": log["transactionHash"],
        "block": int(log["blockNumber"], 16),
        "log_index": int(log.get("logIndex", "0x0"), 16),
    }


def scan_range(client: PolygonClient, from_block: int, to_block: int,
               tokens: Optional[set] = None,
               chunk: int = 2000) -> Tuple[List[Dict[str, Any]], int]:
    """Scan a block range for OrderFilled logs, optionally filtered to tokens.

    The token id is in the event's data rather than its topics, so the node
    cannot filter by market; the range is pulled and filtered here. Oversized
    chunks are halved and retried, which is what keeps busy periods from
    timing out the request.
    """
    found: List[Dict[str, Any]] = []
    scanned = 0
    start = from_block
    while start <= to_block:
        end = min(start + chunk - 1, to_block)
        try:
            logs: List[Dict[str, Any]] = []
            for exchange in EXCHANGES:
                logs.extend(client.order_filled_logs(start, end, exchange))
        except ChainError:
            if chunk <= 100:
                logger.warning("skipping blocks %d-%d after repeated failure", start, end)
                start = end + 1
                scanned += end - start + 1
                continue
            chunk = max(chunk // 2, 100)
            logger.debug("narrowing chunk to %d at block %d", chunk, start)
            continue
        for log in logs:
            trade = decode_order_filled(log)
            if trade and (tokens is None or trade["token_id"] in tokens):
                found.append(trade)
        scanned += end - start + 1
        start = end + 1
    return found, scanned
