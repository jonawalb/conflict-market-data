#!/usr/bin/env python3
"""Round-trip test: database -> increment -> rebuilt database.

The whole CI design rests on one claim: increments are a lossless, replayable
representation of collected data. If that breaks, the repository stops being a
usable archive and nothing downstream would notice until analysis. This test
exercises it offline, with no network.

Run: python3 test_roundtrip.py
"""

import gzip
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bow import db

MARKETS = [{
    "market_id": "m1", "condition_id": "0xabc", "slug": "test-market",
    "question": "Will X strike Y by January 31?", "event_title": "Test event",
    "category": "geopolitics", "start_date": "2026-01-01T00:00:00Z",
    "end_date": "2026-01-31T00:00:00Z", "created_at": "2026-01-01T00:00:00Z",
    "closed": 0, "active": 1, "token_yes": "tok_yes", "token_no": "tok_no",
    "volume_num": 1234.5, "liquidity_num": 99.0, "escalation": 1, "tracked": 1,
    "last_seen": "2026-08-05T00:00:00Z",
}]
PRICES = [{"t": 1780000000, "p": 0.42}, {"t": 1780000060, "p": 0.43}]
TRADES = [{
    "transactionHash": "0xdead", "proxyWallet": "0xwallet1", "side": "BUY",
    "outcome": "Yes", "timestamp": 1780000010, "price": 0.42, "size": 100.0,
}, {
    "transactionHash": "0xdead", "proxyWallet": "0xwallet2", "side": "SELL",
    "outcome": "Yes", "timestamp": 1780000010, "price": 0.42, "size": 100.0,
}]
BOOK = {
    "token_id": "tok_yes", "snap_ts": 1780000100, "market_id": "m1",
    "book_ts": 1780000099, "best_bid": 0.41, "best_ask": 0.43, "mid": 0.42,
    "spread": 0.02, "bid_size_total": 500.0, "ask_size_total": 400.0,
    "bid_depth_1c": 100.0, "ask_depth_1c": 80.0, "bid_depth_5c": 300.0,
    "ask_depth_5c": 250.0, "bid_depth_10c": 500.0, "ask_depth_10c": 400.0,
    "n_bid_levels": 5, "n_ask_levels": 4, "raw_json": '{"bids":[],"asks":[]}',
}
TABLES = ("markets", "prices", "trades", "books", "runs")


def build_source(path: Path) -> sqlite3.Connection:
    conn = db.connect(path)
    db.upsert_markets(conn, MARKETS)
    db.insert_prices(conn, "tok_yes", "m1", PRICES, fidelity=1)
    db.insert_trades(conn, "0xabc", "m1", TRADES)
    db.insert_book(conn, BOOK)
    db.record_run(conn, {
        "run_id": "r1", "started": "2026-08-05T00:00:00Z",
        "finished": "2026-08-05T00:01:00Z", "mode": "full", "n_markets": 1,
        "n_prices": 2, "n_trades": 2, "n_books": 1, "api_calls": 5,
        "errors": 0, "note": "test",
    })
    return conn


def export(conn: sqlite3.Connection, path: Path) -> int:
    written = 0
    with gzip.open(path, "wt") as handle:
        for table in TABLES:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            for row in conn.execute(f"SELECT * FROM {table}"):
                handle.write(json.dumps({"_t": table, **dict(zip(cols, row))}))
                handle.write("\n")
                written += 1
    return written


def replay(increment: Path, target: Path, times: int = 1) -> sqlite3.Connection:
    conn = db.connect(target)
    for _ in range(times):
        buckets = {}
        with gzip.open(increment, "rt") as handle:
            for line in handle:
                record = json.loads(line)
                buckets.setdefault(record.pop("_t"), []).append(record)
        for table, rows in buckets.items():
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            usable = [c for c in cols if c in rows[0]]
            conn.executemany(
                f"INSERT OR IGNORE INTO {table} ({','.join(usable)}) "
                f"VALUES ({','.join('?' for _ in usable)})",
                [[r.get(c) for c in usable] for r in rows],
            )
        conn.commit()
    return conn


def snapshot(conn: sqlite3.Connection) -> dict:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="bow-roundtrip-"))
    failures = 0
    try:
        source = build_source(tmp / "source.sqlite")
        before = snapshot(source)
        assert before["trades"] == 2, "both sides of a shared tx hash must persist"

        increment = tmp / "inc.jsonl.gz"
        rows = export(source, increment)
        print(f"exported {rows} rows -> {increment.stat().st_size} bytes")

        rebuilt = replay(increment, tmp / "rebuilt.sqlite")
        after = snapshot(rebuilt)
        if before != after:
            failures += 1
            print(f"  FAIL row counts differ: {before} vs {after}")
        else:
            print(f"round-trip: counts match {before}")

        # Replaying the same increment repeatedly must be a no-op.
        replayed = replay(increment, tmp / "idem.sqlite", times=3)
        if snapshot(replayed) != before:
            failures += 1
            print(f"  FAIL replay is not idempotent: {snapshot(replayed)}")
        else:
            print("idempotence: 3x replay produces identical counts")

        # Field-level check on the row most likely to lose precision.
        src = source.execute(
            "SELECT mid, spread, bid_depth_5c, n_bid_levels FROM books").fetchone()
        dst = rebuilt.execute(
            "SELECT mid, spread, bid_depth_5c, n_bid_levels FROM books").fetchone()
        if tuple(src) != tuple(dst):
            failures += 1
            print(f"  FAIL book row changed: {tuple(src)} vs {tuple(dst)}")
        else:
            print("field fidelity: book measures preserved exactly")

        trade_src = source.execute(
            "SELECT wallet, price, size FROM trades ORDER BY wallet").fetchall()
        trade_dst = rebuilt.execute(
            "SELECT wallet, price, size FROM trades ORDER BY wallet").fetchall()
        if [tuple(r) for r in trade_src] != [tuple(r) for r in trade_dst]:
            failures += 1
            print("  FAIL trade rows changed")
        else:
            print("field fidelity: wallet-level trades preserved exactly")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("ALL PASS" if failures == 0 else f"{failures} FAILURES")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
