#!/usr/bin/env python3
"""Rebuild the analysis database from committed increments.

Increments are append-only and may overlap; every writer uses INSERT OR IGNORE
against a natural primary key, so replaying them in any order converges to the
same database. That property is what makes the pipeline safe to run from
several machines at once.

Usage:
    python3 scripts/rebuild_db.py                     # -> ./bow_market_data.sqlite
    python3 scripts/rebuild_db.py --out /path/db.sqlite --since 2026-08
    python3 scripts/rebuild_db.py --out /path/db.sqlite --incremental

--incremental keeps an existing database in sync: increments already replayed into
it (listed in its replayed_increments table) are skipped, so each call replays only
what arrived since the last one. run_collect.sh calls it before every local run.
The registry is merged without overwriting volume, liquidity, or last_seen, which
the registry does not carry.
"""

import argparse
import gzip
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Iterator, List

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from bow import db

logger = logging.getLogger("rebuild")

INCREMENTS = REPO / "data" / "increments"
REGISTRY = REPO / "data" / "registry" / "markets.json.gz"


def iter_records(paths: List[Path]) -> Iterator[Dict]:
    for path in paths:
        try:
            with gzip.open(path, "rt") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        yield json.loads(line)
        except (OSError, EOFError, json.JSONDecodeError) as exc:
            logger.warning("skipping unreadable increment %s: %s", path.name, exc)


def merge_registry(conn: sqlite3.Connection, rows: List[Dict]) -> None:
    """Add registry markets; refresh the fields the registry owns on existing rows.

    db.upsert_markets would also overwrite volume_num, liquidity_num and last_seen
    with the placeholders the registry needs, wiping the values a locally collected
    database holds.
    """
    conn.executemany(
        """INSERT INTO markets (market_id, condition_id, slug, question, event_title,
            category, start_date, end_date, created_at, closed, active, token_yes,
            token_no, volume_num, liquidity_num, escalation, tracked)
        VALUES (:market_id, :condition_id, :slug, :question, :event_title, :category,
            :start_date, :end_date, :created_at, :closed, :active, :token_yes,
            :token_no, 0.0, 0.0, :escalation, :tracked)
        ON CONFLICT(market_id) DO UPDATE SET
            closed=excluded.closed, active=excluded.active,
            escalation=excluded.escalation, tracked=excluded.tracked,
            end_date=excluded.end_date,
            token_yes=COALESCE(excluded.token_yes, markets.token_yes),
            token_no=COALESCE(excluded.token_no, markets.token_no)""",
        rows,
    )
    conn.commit()


def insert(conn: sqlite3.Connection, table: str, rows: List[Dict]) -> int:
    if not rows:
        return 0
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    usable = [c for c in cols if c in rows[0]]
    placeholders = ",".join("?" for _ in usable)
    sql = (f"INSERT OR IGNORE INTO {table} ({','.join(usable)}) "
           f"VALUES ({placeholders})")
    before = conn.total_changes
    conn.executemany(sql, [[r.get(c) for c in usable] for r in rows])
    conn.commit()
    return conn.total_changes - before


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=REPO / "bow_market_data.sqlite")
    parser.add_argument("--since", default=None,
                        help="only replay increments from this YYYY or YYYY-MM onward")
    parser.add_argument("--batch", type=int, default=20000)
    parser.add_argument("--incremental", action="store_true",
                        help="skip increments already replayed into --out")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(levelname)-7s %(message)s")

    paths = sorted(INCREMENTS.rglob("*.jsonl.gz"))
    if args.since:
        key = args.since.replace("-", "")
        paths = [p for p in paths if p.name >= key]
    if not paths:
        logger.error("no increments found under %s", INCREMENTS)
        return 1

    conn = db.connect(args.out)
    if args.incremental:
        done = {r[0] for r in conn.execute("SELECT path FROM replayed_increments")}
        paths = [p for p in paths if str(p.relative_to(INCREMENTS)) not in done]
    logger.info("replaying %d increments into %s", len(paths), args.out)

    if REGISTRY.exists():
        with gzip.open(REGISTRY, "rt") as handle:
            registry = json.load(handle)
        # The registry deliberately omits volatile numerics to stay byte-stable;
        # restore them so the row satisfies the insert. Real values arrive from
        # the increments, which carry the market rows as they were observed.
        for row in registry:
            row.setdefault("volume_num", 0.0)
            row.setdefault("liquidity_num", 0.0)
            row.setdefault("last_seen", None)
        merge_registry(conn, registry)
        logger.info("registry: %d markets", len(registry))

    counts: Dict[str, int] = {}
    # One file at a time, so a file is marked replayed only once all its rows are in.
    for path in paths:
        buffers: Dict[str, List[Dict]] = {}
        for record in iter_records([path]):
            table = record.pop("_t", None)
            if not table:
                continue
            buf = buffers.setdefault(table, [])
            buf.append(record)
            if len(buf) >= args.batch:
                counts[table] = counts.get(table, 0) + insert(conn, table, buf)
                buf.clear()
        for table, buf in buffers.items():
            if buf:
                counts[table] = counts.get(table, 0) + insert(conn, table, buf)
        conn.execute("INSERT OR REPLACE INTO replayed_increments VALUES (?, datetime('now'))",
                     (str(path.relative_to(INCREMENTS)),))
        conn.commit()

    for table, n in sorted(counts.items()):
        logger.info("  %-10s +%d rows", table, n)
    if not args.incremental:  # full-table counts take minutes on a large database
        logger.info("database totals:")
        for key, value in db.summary(conn).items():
            logger.info("    %-16s %s", key, f"{value:,}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
