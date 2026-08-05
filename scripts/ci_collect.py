#!/usr/bin/env python3
"""Stateless collection entry point for GitHub Actions.

CI runners have no persistent disk, so this script reconstructs just enough
state from the repository to avoid re-fetching what previous runs already have:

  data/registry/markets.json.gz  which markets to poll
  data/state/high_water.json     newest timestamp already captured per market

It collects into a throwaway SQLite database, writes everything it found as a
compressed increment under data/increments/, and updates the state files. The
full database is never committed; `scripts/rebuild_db.py` reconstructs it from
the increments on demand.

Usage:
    python3 scripts/ci_collect.py --mode book
    python3 scripts/ci_collect.py --mode full --max-runtime 3000 --shard 0/4
"""

import argparse
import datetime as dt
import gzip
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from bow import db
from bow.api import PolymarketClient
from bow.collect import collect_market
from bow.config import Config
from bow.discover import discover

logger = logging.getLogger("ci")

DATA = REPO / "data"
REGISTRY = DATA / "registry" / "markets.json.gz"
STATE = DATA / "state" / "high_water.json"
INCREMENTS = DATA / "increments"

TABLES = ("markets", "prices", "trades", "books", "runs")

# Fields the registry needs to drive polling and classification. Volatile
# numerics (volume, liquidity, last_seen) are deliberately excluded: they change
# every run and would rewrite the whole registry blob each time. They still land
# in the increments, which is where time-varying data belongs.
REGISTRY_FIELDS = (
    "market_id", "condition_id", "slug", "question", "event_title", "category",
    "start_date", "end_date", "created_at", "closed", "active",
    "token_yes", "token_no", "escalation", "tracked",
)


def load_registry() -> List[Dict[str, Any]]:
    if not REGISTRY.exists():
        return []
    with gzip.open(REGISTRY, "rt") as handle:
        rows = json.load(handle)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for row in rows:  # restore the fields the registry intentionally omits
        row.setdefault("volume_num", 0.0)
        row.setdefault("liquidity_num", 0.0)
        row["last_seen"] = now
    return rows


def save_registry(rows: List[Dict[str, Any]]) -> None:
    """Persist the poll list.

    Only escalation markets are kept: the other ~9,000 discovered markets are
    never polled, and carrying them would add ~1.5 MB of git churn per refresh.
    mtime is pinned to 0 so an unchanged registry serialises to identical bytes
    and produces no commit at all.
    """
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    keep = [{k: r.get(k) for k in REGISTRY_FIELDS}
            for r in rows if r.get("escalation")]
    keep.sort(key=lambda r: str(r["market_id"]))
    payload = json.dumps(keep, separators=(",", ":"), sort_keys=True).encode()
    with open(REGISTRY, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9, mtime=0) as gz:
            gz.write(payload)


def load_state() -> Dict[str, Dict[str, int]]:
    if not STATE.exists():
        return {}
    return json.loads(STATE.read_text())


def save_state(state: Dict[str, Dict[str, int]]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=0, sort_keys=True))


def seed_db(conn: sqlite3.Connection, registry: List[Dict[str, Any]]) -> None:
    """Populate the throwaway database with the market registry only."""
    if registry:
        db.upsert_markets(conn, registry)


def export_increment(conn: sqlite3.Connection, mode: str, run_id: str) -> Optional[Path]:
    """Write every row in the throwaway database as one compressed JSONL file."""
    now = dt.datetime.now(dt.timezone.utc)
    out_dir = INCREMENTS / f"{now:%Y}" / f"{now:%m}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{now:%Y%m%dT%H%M%S}-{mode}-{run_id}.jsonl.gz"

    written = 0
    with gzip.open(path, "wt", compresslevel=9) as handle:
        for table in TABLES:
            # The registry is committed separately; only emit markets that the
            # run actually touched, to keep increments small.
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            for row in conn.execute(f"SELECT * FROM {table}"):
                record = dict(zip(cols, row))
                handle.write(json.dumps({"_t": table, **record}, separators=(",", ":")))
                handle.write("\n")
                written += 1
    if written == 0:
        path.unlink(missing_ok=True)
        return None
    logger.info("increment: %s (%d rows, %d bytes)", path.name, written,
                path.stat().st_size)
    return path


def update_state(conn: sqlite3.Connection, state: Dict[str, Dict[str, int]]) -> None:
    for market_id, ts in conn.execute(
        "SELECT market_id, MAX(ts) FROM trades GROUP BY market_id"
    ):
        if market_id:
            entry = state.setdefault(str(market_id), {})
            entry["trade"] = max(int(ts or 0), entry.get("trade", 0))
    for market_id, ts in conn.execute(
        "SELECT market_id, MAX(ts) FROM prices GROUP BY market_id"
    ):
        if market_id:
            entry = state.setdefault(str(market_id), {})
            entry["price"] = max(int(ts or 0), entry.get("price", 0))


def parse_shard(value: Optional[str]) -> tuple:
    if not value:
        return 0, 1
    index, total = value.split("/")
    return int(index), int(total)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["book", "full", "discover"], default="book")
    parser.add_argument("--max-runtime", type=int, default=3000,
                        help="seconds before the run stops cleanly and commits")
    parser.add_argument("--shard", default=None, help="i/N to split markets across runs")
    parser.add_argument("--store-raw-books", action="store_true",
                        help="keep full order book JSON (large; off by default in CI)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    started = time.time()
    run_id = f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%S}"
    workdir = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / f"bow-{run_id}"
    workdir.mkdir(parents=True, exist_ok=True)
    cfg = Config(root=workdir, db_path=workdir / "run.sqlite",
                 log_path=workdir / "run.log")

    conn = db.connect(cfg.db_path)
    client = PolymarketClient(cfg)
    registry = load_registry()
    state = load_state()
    logger.info("registry: %d markets, state: %d entries", len(registry), len(state))

    if args.mode == "discover" or not registry:
        registry = discover(client, cfg)
        save_registry(registry)
        logger.info("registry refreshed: %d markets", len(registry))
        if args.mode == "discover":
            export_increment(conn, args.mode, run_id)
            return 0

    seed_db(conn, registry)
    markets = db.tracked_markets(conn)

    index, total = parse_shard(args.shard)
    if total > 1:
        markets = [m for i, m in enumerate(markets) if i % total == index]
        logger.info("shard %d/%d -> %d markets", index, total, len(markets))

    do_books = args.mode in ("book", "full")
    do_prices = do_trades = args.mode == "full"

    totals = {"prices": 0, "trades": 0, "books": 0, "errors": 0}
    processed = 0
    for market in markets:
        if time.time() - started > args.max_runtime:
            logger.warning("runtime cap reached after %d markets; committing early",
                           processed)
            break
        counts = collect_market(
            client, conn, market, cfg, do_books, do_trades, do_prices,
            since=state.get(str(market["market_id"])),
            store_raw_books=args.store_raw_books,
        )
        for key, value in counts.items():
            totals[key] += value
        processed += 1
        if processed % 50 == 0:
            logger.info("  [%d/%d] +%d prices +%d trades +%d books",
                        processed, len(markets), totals["prices"],
                        totals["trades"], totals["books"])

    # Drop the seeded registry rows so the increment carries only new observations.
    conn.execute("DELETE FROM markets")
    db.record_run(conn, {
        "run_id": run_id,
        "started": dt.datetime.fromtimestamp(started, dt.timezone.utc).isoformat(),
        "finished": dt.datetime.now(dt.timezone.utc).isoformat(),
        "mode": args.mode, "n_markets": processed,
        "n_prices": totals["prices"], "n_trades": totals["trades"],
        "n_books": totals["books"], "api_calls": client.calls,
        "errors": totals["errors"] + client.failures, "note": "ci",
    })
    conn.commit()

    update_state(conn, state)
    save_state(state)
    export_increment(conn, args.mode, run_id)

    logger.info("done in %.0fs: %d markets, +%d prices, +%d trades, +%d books",
                time.time() - started, processed, totals["prices"],
                totals["trades"], totals["books"])
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
