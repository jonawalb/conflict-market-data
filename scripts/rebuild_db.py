#!/usr/bin/env python3
"""Rebuild the analysis database from committed increments.

Increments are append-only and may overlap; every writer uses INSERT OR IGNORE
against a natural primary key, so replaying them in any order converges to the
same database. That property is what makes the pipeline safe to run from
several machines at once.

Usage:
    python3 scripts/rebuild_db.py                     # -> ./bow_market_data.sqlite
    python3 scripts/rebuild_db.py --out /path/db.sqlite --since 2026-08
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
    logger.info("replaying %d increments into %s", len(paths), args.out)

    conn = db.connect(args.out)

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
        db.upsert_markets(conn, registry)
        logger.info("registry: %d markets", len(registry))

    buffers: Dict[str, List[Dict]] = {}
    counts: Dict[str, int] = {}
    for record in iter_records(paths):
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

    for table, n in sorted(counts.items()):
        logger.info("  %-10s +%d rows", table, n)
    logger.info("database totals:")
    for key, value in db.summary(conn).items():
        logger.info("    %-16s %s", key, f"{value:,}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
