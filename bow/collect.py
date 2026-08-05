"""Per-market collection of prices, trades, and order book snapshots."""

import json
import logging
import sqlite3
import time
from typing import Any, Dict, List, Optional

from .api import PolymarketClient
from .config import Config
from . import db

logger = logging.getLogger(__name__)


def summarise_book(
    book: Dict[str, Any], token_id: str, market_id: str, snap_ts: int
) -> Optional[Dict[str, Any]]:
    """Reduce a raw order book to the liquidity measures the theory needs.

    Depth within N cents of the mid is the direct empirical analogue of price
    impact per dollar, which is the model's manipulation-cost parameter.
    """
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids and not asks:
        return None

    def level(entry: Dict[str, Any]) -> tuple:
        return float(entry["price"]), float(entry["size"])

    try:
        bid_levels = sorted((level(b) for b in bids), key=lambda x: -x[0])
        ask_levels = sorted((level(a) for a in asks), key=lambda x: x[0])
    except (KeyError, TypeError, ValueError):
        logger.debug("unparseable book for %s", token_id)
        return None

    best_bid = bid_levels[0][0] if bid_levels else None
    best_ask = ask_levels[0][0] if ask_levels else None
    mid = ((best_bid + best_ask) / 2) if (best_bid is not None and best_ask is not None) else None
    spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None

    def depth(levels: List[tuple], cents: int, is_bid: bool) -> Optional[float]:
        if mid is None:
            return None
        bound = mid - cents / 100.0 if is_bid else mid + cents / 100.0
        return sum(
            size for price, size in levels
            if (price >= bound if is_bid else price <= bound)
        )

    return {
        "token_id": token_id,
        "snap_ts": snap_ts,
        "market_id": market_id,
        "book_ts": int(book["timestamp"]) if str(book.get("timestamp", "")).isdigit() else None,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread": spread,
        "bid_size_total": sum(s for _, s in bid_levels),
        "ask_size_total": sum(s for _, s in ask_levels),
        "bid_depth_1c": depth(bid_levels, 1, True),
        "ask_depth_1c": depth(ask_levels, 1, False),
        "bid_depth_5c": depth(bid_levels, 5, True),
        "ask_depth_5c": depth(ask_levels, 5, False),
        "bid_depth_10c": depth(bid_levels, 10, True),
        "ask_depth_10c": depth(ask_levels, 10, False),
        "n_bid_levels": len(bid_levels),
        "n_ask_levels": len(ask_levels),
        "raw_json": json.dumps({"bids": bids, "asks": asks}, separators=(",", ":")),
    }


def collect_prices(
    client: PolymarketClient, conn: sqlite3.Connection, market: sqlite3.Row,
    since_ts: Optional[int] = None,
) -> int:
    """Pull the finest available bars, plus a coarse full-life series as backup.

    interval=1d/fidelity=1 yields ~1-minute bars for the last 24h. Running at
    least daily therefore stitches a continuous minute-level series that cannot
    be reconstructed after the market resolves.
    """
    inserted = 0
    for token_id in (market["token_yes"], market["token_no"]):
        if not token_id:
            continue
        for interval, fidelity in (("1d", 1), ("max", 60)):
            history = client.price_history(token_id, interval=interval, fidelity=fidelity)
            if since_ts is not None:
                history = [h for h in history if int(h.get("t", 0)) > since_ts]
            if history:
                inserted += db.insert_prices(
                    conn, token_id, market["market_id"], history, fidelity
                )
    return inserted


def collect_trades(
    client: PolymarketClient, conn: sqlite3.Connection, market: sqlite3.Row,
    cfg: Config, since_ts: Optional[int] = None,
) -> int:
    """Page the trade tape newest-first, stopping once we reach known trades.

    The endpoint caps paging near offset 10000, so on very heavy markets each run
    captures the recent window; frequent runs are what build full coverage.

    ``since_ts`` is the high-water mark from a previous run. It exists for the
    stateless CI path, where the database starts empty each time and the
    "no new rows" stopping rule would otherwise never fire.
    """
    condition_id = market["condition_id"]
    if not condition_id:
        return 0

    inserted = 0
    consecutive_known = 0
    offset = 0
    while offset <= cfg.trade_max_offset:
        batch = client.trades(condition_id, cfg.trade_page_limit, offset)
        if not batch:
            break
        if since_ts is not None:
            fresh = [t for t in batch if int(t.get("timestamp", 0)) > since_ts]
            inserted += db.insert_trades(conn, condition_id, market["market_id"], fresh)
            # The tape is newest-first, so a partial page means we've reached
            # everything the previous run already captured.
            if len(fresh) < len(batch):
                break
            offset += cfg.trade_page_limit
            continue
        added = db.insert_trades(conn, condition_id, market["market_id"], batch)
        inserted += added
        # Two full pages of nothing new means we have caught up with storage.
        consecutive_known = consecutive_known + 1 if added == 0 else 0
        if consecutive_known >= 2:
            break
        offset += cfg.trade_page_limit
    return inserted


def collect_books(
    client: PolymarketClient, conn: sqlite3.Connection, market: sqlite3.Row,
    store_raw: bool = True,
) -> int:
    """Snapshot both sides of the book. Not recoverable retrospectively."""
    snap_ts = int(time.time())
    inserted = 0
    for token_id in (market["token_yes"], market["token_no"]):
        if not token_id:
            continue
        book = client.book(token_id)
        if not book:
            continue
        row = summarise_book(book, token_id, market["market_id"], snap_ts)
        if row and not store_raw:
            row["raw_json"] = None
        if row:
            inserted += db.insert_book(conn, row)
    return inserted


def collect_market(
    client: PolymarketClient, conn: sqlite3.Connection, market: sqlite3.Row,
    cfg: Config, do_books: bool, do_trades: bool, do_prices: bool,
    since: Optional[Dict[str, int]] = None, store_raw_books: bool = True,
) -> Dict[str, int]:
    """Collect one market. Failures are contained to this market."""
    counts = {"prices": 0, "trades": 0, "books": 0, "errors": 0}
    try:
        since = since or {}
        if do_books:
            counts["books"] = collect_books(client, conn, market, store_raw_books)
        if do_prices:
            counts["prices"] = collect_prices(client, conn, market, since.get("price"))
        if do_trades:
            counts["trades"] = collect_trades(client, conn, market, cfg, since.get("trade"))
    except Exception as exc:  # keep one bad market from killing the run
        counts["errors"] = 1
        logger.warning(
            "collection failed for %s (%s): %s",
            market["market_id"], (market["question"] or "")[:60], exc,
        )
    return counts
