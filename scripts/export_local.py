#!/usr/bin/env python3
"""Export a local SQLite database into repository increments.

The local collector and the CI collector write to different places: a laptop
run fills a SQLite database, a CI run writes compressed increments to the repo.
Data collected locally therefore exists nowhere else until it is exported.

This converts a local database into the same increment format CI produces, so a
laptop backfill can be committed and replayed like any other increment. Rows are
chunked so no single file is awkward to handle, and the whole thing is
idempotent: re-exporting and replaying changes nothing.

    python3 scripts/export_local.py                     # export everything
    python3 scripts/export_local.py --since 1780000000  # only newer rows
    python3 scripts/export_local.py --skip-raw-books    # drop bulky book JSON
"""

import argparse
import datetime as dt
import gzip
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Iterator, List, Optional

REPO = Path(__file__).resolve().parent.parent
INCREMENTS = REPO / "data" / "increments"
DEFAULT_DB = Path(
    os.environ.get("BOW_DATA_DIR",
                   Path.home() / "Library" / "Application Support" / "BettingOnWar")
) / "bow_market_data.sqlite"

# markets are carried by the registry; runs are bookkeeping worth keeping.
TABLES = ("markets", "prices", "trades", "books", "runs")
TIME_COL = {"prices": "ts", "trades": "ts", "books": "snap_ts"}


def rows_for(conn: sqlite3.Connection, table: str,
             since: Optional[int]) -> Iterator[dict]:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    sql = f"SELECT * FROM {table}"
    params: List = []
    if since and table in TIME_COL:
        sql += f" WHERE {TIME_COL[table]} > ?"
        params.append(since)
    for row in conn.execute(sql, params):
        yield dict(zip(cols, row))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--since", type=int, default=None,
                        help="only rows with a timestamp greater than this")
    parser.add_argument("--chunk", type=int, default=150_000,
                        help="rows per increment file")
    parser.add_argument("--skip-raw-books", action="store_true",
                        help="omit full order book JSON (keeps depth summaries)")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"no database at {args.db}")
        return 1

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    stamp = f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%S}"
    out_dir = INCREMENTS / stamp[:4] / stamp[4:6]
    out_dir.mkdir(parents=True, exist_ok=True)

    part = 0
    written = 0
    total = 0
    handle = None
    paths: List[Path] = []

    def open_part():
        nonlocal handle, part
        path = out_dir / f"{stamp}-local-{part:03d}.jsonl.gz"
        paths.append(path)
        handle = gzip.open(path, "wt", compresslevel=9)

    open_part()
    try:
        for table in TABLES:
            for record in rows_for(conn, table, args.since):
                if table == "books" and args.skip_raw_books:
                    record["raw_json"] = None
                handle.write(json.dumps({"_t": table, **record},
                                        separators=(",", ":")))
                handle.write("\n")
                written += 1
                total += 1
                if written >= args.chunk:
                    handle.close()
                    print(f"  {paths[-1].name}: {written:,} rows, "
                          f"{paths[-1].stat().st_size/1e6:.1f} MB")
                    part += 1
                    written = 0
                    open_part()
    finally:
        if handle:
            handle.close()

    if written == 0 and len(paths) > 1:
        paths[-1].unlink(missing_ok=True)
        paths.pop()
    elif paths:
        print(f"  {paths[-1].name}: {written:,} rows, "
              f"{paths[-1].stat().st_size/1e6:.1f} MB")

    size = sum(p.stat().st_size for p in paths if p.exists())
    print(f"\nexported {total:,} rows into {len(paths)} increment(s), "
          f"{size/1e6:.1f} MB total")
    print("Replay with: python3 scripts/rebuild_db.py")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
