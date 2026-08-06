#!/usr/bin/env python3
"""Recover hidden trade history by crawling wallets instead of markets.

The market endpoint caps at ~10,000 recent fills, which on a busy contract
hides most of its life. But the *wallet* endpoint has its own budget: asking
"what has this address traded?" returns that wallet's full history across every
market, including fills inside the window the market endpoint refuses to show.

So the tape is reachable from the side. Collect the wallets visible in a
market's recent tape, ask each what else it did, and fills below the market's
floor come back. Because each wallet answer spans all markets at once, one pass
over a wallet set fills gaps in many markets simultaneously — which is why this
beats scanning the blockchain, where ~99.99% of what you download is discarded.

Two further tricks are used to widen the visible tape before crawling:
`side=BUY` and `side=SELL` each get their own ~10,000-row budget, so splitting
by side roughly doubles the starting window (measured: 8 days of visible tape
becomes 18).

COVERAGE IS NOT PROVABLY COMPLETE. A wallet that traded only inside the hidden
window and never appears in any visible tape cannot be discovered this way.
Treat the result as substantially better coverage, not a guaranteed census, and
report it that way. `--report` quantifies what was actually recovered.

    python3 scripts/wallet_crawl.py --ladders-only --limit 5 --report
    python3 scripts/wallet_crawl.py --ladders-only
"""

import argparse
import datetime as dt
import json
import logging
import os
import re
import sqlite3
import sys
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Set

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from bow import db

logger = logging.getLogger("crawl")

DATA_API = "https://data-api.polymarket.com/trades"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/126"}
DEFAULT_DB = Path(
    os.environ.get("BOW_DATA_DIR",
                   Path.home() / "Library" / "Application Support" / "BettingOnWar")
) / "bow_market_data.sqlite"
CHECKPOINT = REPO / "data" / "state" / "wallet_crawl.json"

PAGE = 500
MARKET_OFFSET_CAP = 9500


def fetch(url: str, retries: int = 3) -> Optional[list]:
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, headers=UA), timeout=45) as resp:
                payload = json.loads(resp.read())
            return payload if isinstance(payload, list) else []
        except Exception:
            time.sleep(0.6 * (attempt + 1))
    return None


def market_tape(condition_id: str) -> List[dict]:
    """Pull the visible tape, split by side to double the reachable window."""
    seen: Dict[str, dict] = {}
    for side in ("BUY", "SELL"):
        offset = 0
        while offset <= MARKET_OFFSET_CAP:
            batch = fetch(f"{DATA_API}?market={condition_id}&limit={PAGE}"
                          f"&offset={offset}&side={side}")
            if not batch:
                break
            for trade in batch:
                seen[f"{trade.get('transactionHash')}|{trade.get('proxyWallet')}"
                     f"|{trade.get('side')}|{trade.get('timestamp')}"] = trade
            if len(batch) < PAGE:
                break
            offset += PAGE
    return list(seen.values())


def wallet_history(wallet: str) -> List[dict]:
    out: List[dict] = []
    offset = 0
    while offset <= MARKET_OFFSET_CAP:
        batch = fetch(f"{DATA_API}?user={wallet}&limit={PAGE}&offset={offset}")
        if not batch:
            break
        out.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return out


def select_markets(conn: sqlite3.Connection, args) -> List[sqlite3.Row]:
    rows = conn.execute(
        "SELECT * FROM markets WHERE escalation=1 AND condition_id IS NOT NULL "
        "ORDER BY volume_num DESC").fetchall()
    if args.ladders_only:
        fam = defaultdict(list)
        for row in rows:
            stem = re.sub(r"\s*\b(by|before|through|continues through)\b.*$", "",
                          row["question"] or "", flags=re.I).strip().lower()
            if len(stem) > 10:
                fam[stem].append(row)
        rows = [r for g in fam.values()
                if len({(m["end_date"] or "")[:10] for m in g}) >= 3 for r in g]
        rows.sort(key=lambda r: -(r["volume_num"] or 0))
    return rows[: args.limit] if args.limit else rows


def store(conn: sqlite3.Connection, trades: List[dict],
          by_condition: Dict[str, str]) -> int:
    """Insert trades, mapping each back to the market it belongs to."""
    grouped: Dict[str, List[dict]] = defaultdict(list)
    for trade in trades:
        cond = trade.get("conditionId")
        if cond in by_condition:
            grouped[cond].append(trade)
    total = 0
    for cond, rows in grouped.items():
        total += db.insert_trades(conn, cond, by_condition[cond], rows)
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--ladders-only", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-wallets", type=int, default=0,
                        help="cap wallets crawled this run (0 = no cap)")
    parser.add_argument("--report", action="store_true",
                        help="report coverage gained per market")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)-7s %(message)s")
    if not args.db.exists():
        logger.error("no database at %s", args.db)
        return 1

    conn = db.connect(args.db)
    markets = select_markets(conn, args)
    by_condition = {m["condition_id"]: m["market_id"] for m in markets}
    logger.info("targeting %d markets", len(markets))

    state = json.loads(CHECKPOINT.read_text()) if CHECKPOINT.exists() else {}
    done: Set[str] = set(state.get("wallets_done", []))
    logger.info("%d wallets already crawled in earlier runs", len(done))

    # Phase 1 — widen each market's visible tape and harvest wallets.
    wallets: Set[str] = set()
    floors: Dict[str, int] = {}
    before = db.summary(conn)["trades"]
    for index, market in enumerate(markets, 1):
        tape = market_tape(market["condition_id"])
        if tape:
            store(conn, tape, by_condition)
            wallets |= {t["proxyWallet"] for t in tape if t.get("proxyWallet")}
            floors[market["condition_id"]] = min(t["timestamp"] for t in tape)
        if index % 10 == 0 or index == len(markets):
            logger.info("  tape %d/%d | %s wallets harvested",
                        index, len(markets), f"{len(wallets):,}")

    # Wallets already in the database traded these markets too and may hold
    # fills the visible tapes never showed.
    for (w,) in conn.execute("SELECT DISTINCT wallet FROM trades WHERE wallet IS NOT NULL"):
        if w:
            wallets.add(w)
    todo = sorted(wallets - done)
    if args.max_wallets:
        todo = todo[: args.max_wallets]
    logger.info("phase 2: crawling %s wallets", f"{len(todo):,}")

    # Phase 2 — ask each wallet what else it did.
    recovered = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for index, trades in enumerate(pool.map(wallet_history, todo), 1):
            if trades:
                recovered += store(conn, trades, by_condition)
            if index % 200 == 0 or index == len(todo):
                rate = index / max(time.time() - started, 1)
                remaining = (len(todo) - index) / max(rate, 0.01) / 60
                logger.info("  %s/%s wallets | +%s trades | %.1f/s | ~%.0f min left",
                            f"{index:,}", f"{len(todo):,}", f"{recovered:,}",
                            rate, remaining)
                state["wallets_done"] = sorted(done | set(todo[:index]))
                CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
                CHECKPOINT.write_text(json.dumps(state))

    state["wallets_done"] = sorted(done | set(todo))
    CHECKPOINT.write_text(json.dumps(state))

    after = db.summary(conn)["trades"]
    logger.info("crawl complete: +%s trades (%s -> %s)",
                f"{after - before:,}", f"{before:,}", f"{after:,}")

    if args.report:
        logger.info("\ncoverage gained below each market's visible floor:")
        for market in markets[:20]:
            floor = floors.get(market["condition_id"])
            if not floor:
                continue
            below = conn.execute(
                "SELECT COUNT(*), MIN(ts) FROM trades WHERE market_id=? AND ts<?",
                (market["market_id"], floor)).fetchone()
            if below[0]:
                logger.info("  +%6d below floor, back to %s  %s", below[0],
                            dt.datetime.fromtimestamp(below[1], dt.UTC).date(),
                            (market["question"] or "")[:44])
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
