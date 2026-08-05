#!/usr/bin/env python3
"""Betting on War data collector.

Captures Polymarket geopolitical/military-escalation market data that is
destroyed on market resolution: minute-level prices, the wallet-level trade
tape, and order book depth snapshots.

Usage:
    python3 bow_collect.py full     # discover + prices + trades + books
    python3 bow_collect.py book     # book snapshots only (cheap, run hourly)
    python3 bow_collect.py discover # refresh the market registry only
    python3 bow_collect.py status   # print database summary
"""

import argparse
import datetime as dt
import logging
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bow import db
from bow.api import PolymarketClient
from bow.collect import collect_market
from bow.config import Config
from bow.discover import discover

logger = logging.getLogger("bow")


def setup_logging(log_path: Path, verbose: bool) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(log_path), logging.StreamHandler(sys.stdout)]
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def run(mode: str, cfg: Config, limit: int) -> int:
    started = dt.datetime.now(dt.timezone.utc)
    run_id = f"{started:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    conn = db.connect(cfg.db_path)
    client = PolymarketClient(cfg)

    if mode in ("full", "discover"):
        rows = discover(client, cfg)
        if rows:
            db.upsert_markets(conn, rows)

    totals = {"prices": 0, "trades": 0, "books": 0, "errors": 0}
    markets = []

    if mode in ("full", "book", "trades"):
        markets = db.tracked_markets(conn)
        if limit:
            markets = markets[:limit]
        do_books = mode in ("full", "book")
        do_prices = mode == "full"
        do_trades = mode in ("full", "trades")

        logger.info("collecting %d tracked markets (mode=%s)", len(markets), mode)
        for index, market in enumerate(markets, 1):
            counts = collect_market(
                client, conn, market, cfg, do_books, do_trades, do_prices
            )
            for key, value in counts.items():
                totals[key] += value
            if index % 25 == 0 or index == len(markets):
                logger.info(
                    "  [%d/%d] +%d prices +%d trades +%d books (%d errors)",
                    index, len(markets), totals["prices"], totals["trades"],
                    totals["books"], totals["errors"],
                )

    finished = dt.datetime.now(dt.timezone.utc)
    db.record_run(conn, {
        "run_id": run_id,
        "started": started.isoformat(),
        "finished": finished.isoformat(),
        "mode": mode,
        "n_markets": len(markets),
        "n_prices": totals["prices"],
        "n_trades": totals["trades"],
        "n_books": totals["books"],
        "api_calls": client.calls,
        "errors": totals["errors"] + client.failures,
        "note": "",
    })

    elapsed = (finished - started).total_seconds()
    logger.info(
        "run %s done in %.0fs: +%d prices, +%d trades, +%d books, %d api calls",
        run_id, elapsed, totals["prices"], totals["trades"], totals["books"],
        client.calls,
    )
    print_status(conn)
    conn.close()
    return 0


def print_status(conn) -> None:
    stats = db.summary(conn)
    logger.info("database totals:")
    for key, value in stats.items():
        logger.info("    %-16s %s", key, f"{value:,}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Betting on War data collector")
    parser.add_argument(
        "mode", choices=["full", "book", "trades", "discover", "status"],
        nargs="?", default="full",
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--limit", type=int, default=0, help="cap markets (testing)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    cfg = Config.default(args.root)
    setup_logging(cfg.log_path, args.verbose)

    if args.mode == "status":
        conn = db.connect(cfg.db_path)
        print_status(conn)
        conn.close()
        return 0

    try:
        return run(args.mode, cfg, args.limit)
    except KeyboardInterrupt:
        logger.warning("interrupted by user")
        return 130


if __name__ == "__main__":
    sys.exit(main())
