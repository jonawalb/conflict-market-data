#!/usr/bin/env python3
"""Rebuild trade history for closed markets from Polygon event logs.

The Polymarket API serves no price history for resolved markets and caps the
trade tape at ~10,000 fills, which leaves most of the contract ladders needed
for hazard estimation without usable data. Those trades are still on-chain.

For each market this maps its active date window to a block range, scans both
exchange contracts for `OrderFilled`, keeps the fills matching that market's
token ids, and writes them into the same `trades` table the live collector
uses. Progress is checkpointed per market, so an interrupted run resumes rather
than restarting.

PERFORMANCE, measured 2026-08-05 — read before launching a bulk run.

The public archive endpoint sustains ~72 blocks/s for this contract, because
`OrderFilled` does not index the token id: every fill on the exchange must be
downloaded and filtered locally, and roughly 0.01% is kept. A 2,000-block
window returns ~112,000 logs; 10,000 blocks exceeds the response limit outright.

That puts one market's ~1.7M block window at about **6.7 hours**, and the full
ladder set at 60+ hours of continuous hammering on a shared public node.

So this is the right tool for *targeted* work — one market, one crisis window,
one week of interest — and the wrong tool for bulk backfill. For the full
ladder set use a source that filters server-side (Dune's decoded Polymarket
tables, or a Goldsky subgraph), which turns the same job into a query.

    python3 scripts/reconstruct.py --market-id 994900        # targeted: hours
    python3 scripts/reconstruct.py --ladders-only --limit 2  # gauge before committing
"""

import argparse
import datetime as dt
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from bow import db
from bow.chain import ChainError, PolygonClient, scan_range

logger = logging.getLogger("reconstruct")

DEFAULT_DB = Path(
    os.environ.get("BOW_DATA_DIR",
                   Path.home() / "Library" / "Application Support" / "BettingOnWar")
) / "bow_market_data.sqlite"
CHECKPOINT = REPO / "data" / "state" / "reconstruct.json"


def load_checkpoint() -> Dict[str, dict]:
    if CHECKPOINT.exists():
        return json.loads(CHECKPOINT.read_text())
    return {}


def save_checkpoint(state: Dict[str, dict]) -> None:
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT.write_text(json.dumps(state, indent=0, sort_keys=True))


def parse_ts(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        return int(dt.datetime.fromisoformat(
            str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def select_markets(conn: sqlite3.Connection, args) -> List[sqlite3.Row]:
    if args.market_id:
        return conn.execute("SELECT * FROM markets WHERE market_id=?",
                            (args.market_id,)).fetchall()
    sql = ("SELECT * FROM markets WHERE escalation=1 AND token_yes IS NOT NULL "
           "AND start_date IS NOT NULL AND end_date IS NOT NULL")
    rows = conn.execute(sql + " ORDER BY volume_num DESC").fetchall()
    if args.ladders_only:
        # A ladder needs several deadlines on one event; markets whose question
        # stem is unique cannot contribute to a term structure.
        import re
        from collections import defaultdict
        fam = defaultdict(list)
        for row in rows:
            stem = re.sub(r"\s*\b(by|before|through|continues through)\b.*$", "",
                          row["question"] or "", flags=re.I).strip().lower()
            if len(stem) > 10:
                fam[stem].append(row)
        rows = [r for group in fam.values() if len(group) >= 3 for r in group]
        rows.sort(key=lambda r: -(r["volume_num"] or 0))
    if args.min_volume:
        rows = [r for r in rows if (r["volume_num"] or 0) >= args.min_volume]
    return rows[: args.limit] if args.limit else rows


def reconstruct_market(client: PolygonClient, conn: sqlite3.Connection,
                       market: sqlite3.Row, state: Dict[str, dict],
                       pad_days: float) -> dict:
    market_id = str(market["market_id"])
    tokens = {t for t in (market["token_yes"], market["token_no"]) if t}
    start_ts = parse_ts(market["start_date"]) or parse_ts(market["created_at"])
    end_ts = parse_ts(market["end_date"])
    if not (tokens and start_ts and end_ts):
        return {"status": "skipped", "reason": "missing dates or tokens"}

    pad = int(pad_days * 86400)
    from_block = client.block_at_time(start_ts - pad)
    to_block = client.block_at_time(end_ts + pad)

    prior = state.get(market_id, {})
    resume = prior.get("next_block")
    if prior.get("status") == "done":
        return {"status": "already done"}
    if resume and from_block <= resume <= to_block:
        from_block = resume

    logger.info("  blocks %s-%s (%s)", f"{from_block:,}", f"{to_block:,}",
                f"{to_block - from_block:,} wide")

    started = time.time()
    total = 0
    cursor = from_block
    step = 50_000
    while cursor <= to_block:
        window_end = min(cursor + step - 1, to_block)
        try:
            trades, _ = scan_range(client, cursor, window_end, tokens)
        except ChainError as exc:
            logger.warning("  chunk %d-%d failed: %s", cursor, window_end, exc)
            trades = []
        if trades:
            rows = [{
                "transactionHash": t["tx_hash"], "proxyWallet": t["wallet"],
                "side": t["side"], "outcome": "Yes" if t["token_id"] == market["token_yes"] else "No",
                "timestamp": client.block_time(t["block"]),
                "price": round(t["price"], 6), "size": t["size"],
            } for t in trades]
            total += db.insert_trades(conn, market["condition_id"], market_id, rows)
        cursor = window_end + 1
        state[market_id] = {"next_block": cursor, "status": "partial",
                            "trades": prior.get("trades", 0) + total}
        save_checkpoint(state)

    state[market_id] = {"next_block": to_block + 1, "status": "done",
                        "trades": prior.get("trades", 0) + total}
    save_checkpoint(state)
    return {"status": "done", "trades": total, "seconds": time.time() - started,
            "blocks": to_block - from_block}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--market-id", default=None)
    parser.add_argument("--ladders-only", action="store_true",
                        help="only markets in a 3+ deadline family")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-volume", type=float, default=0)
    parser.add_argument("--pad-days", type=float, default=0.5,
                        help="widen the block window either side of the market's dates")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)-7s %(message)s")

    if not args.db.exists():
        logger.error("no database at %s", args.db)
        return 1

    conn = db.connect(args.db)
    client = PolygonClient()
    state = load_checkpoint()
    markets = select_markets(conn, args)
    logger.info("reconstructing %d markets", len(markets))

    grand_total = 0
    started = time.time()
    for index, market in enumerate(markets, 1):
        logger.info("[%d/%d] %s ($%s)", index, len(markets),
                    (market["question"] or "")[:56],
                    f"{market['volume_num'] or 0:,.0f}")
        try:
            result = reconstruct_market(client, conn, market, state, args.pad_days)
        except Exception as exc:
            logger.warning("  failed: %s", exc)
            continue
        if result.get("trades"):
            grand_total += result["trades"]
            logger.info("  +%s trades in %.0fs", f"{result['trades']:,}",
                        result.get("seconds", 0))
        else:
            logger.info("  %s", result.get("status"))
        logger.info("  running total: %s trades, %.0f min elapsed, %d rpc calls",
                    f"{grand_total:,}", (time.time() - started) / 60, client.calls)

    logger.info("done: +%s trades across %d markets in %.0f min",
                f"{grand_total:,}", len(markets), (time.time() - started) / 60)
    for key, value in db.summary(conn).items():
        logger.info("  %-16s %s", key, f"{value:,}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
