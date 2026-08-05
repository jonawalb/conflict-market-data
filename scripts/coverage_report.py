#!/usr/bin/env python3
"""Audit what the collector actually captured, versus what it intended to.

Scheduled jobs do not run on time. GitHub Actions delays or drops cron runs
under load, laptops sleep, and networks fail. The result is an irregularly
sampled series, which matters because order book depth enters the estimator as
a covariate: assuming hourly spacing when the true spacing is ragged would bias
anything computed from it.

This reports observed sampling intervals rather than assumed ones, so the gaps
can be stated as a measured property of the data instead of an unexamined
assumption.

    python3 scripts/coverage_report.py
    python3 scripts/coverage_report.py --db /path/to/db.sqlite --days 7
"""

import argparse
import datetime as dt
import os
import sqlite3
import statistics
import sys
from pathlib import Path

DEFAULT_DB = Path(
    os.environ.get("BOW_DATA_DIR",
                   Path.home() / "Library" / "Application Support" / "BettingOnWar")
) / "bow_market_data.sqlite"


def fmt_gap(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds/60:.1f}m"
    return f"{seconds/3600:.1f}h"


def book_cadence(conn: sqlite3.Connection, since: int) -> None:
    print("\nORDER BOOK SAMPLING (the irrecoverable series)")
    rows = conn.execute(
        "SELECT DISTINCT snap_ts FROM books WHERE snap_ts > ? ORDER BY snap_ts",
        (since,)).fetchall()
    stamps = [r[0] for r in rows]
    if len(stamps) < 3:
        print("  too few snapshots to assess cadence")
        return
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    # Snapshots inside one run share a moment; only inter-run gaps are cadence.
    gaps = [g for g in gaps if g > 120]
    if not gaps:
        print(f"  {len(stamps)} snapshot timestamps, all within a single run")
        return
    print(f"  distinct snapshot rounds : {len(gaps) + 1}")
    print(f"  median gap               : {fmt_gap(statistics.median(gaps))}")
    print(f"  mean gap                 : {fmt_gap(statistics.fmean(gaps))}")
    print(f"  worst gap                : {fmt_gap(max(gaps))}")
    over = [g for g in gaps if g > 7200]
    print(f"  gaps over 2h             : {len(over)} "
          f"({100*len(over)/len(gaps):.0f}% of intervals)")


def price_coverage(conn: sqlite3.Connection, since: int) -> None:
    print("\nPRICE SERIES (minute bars, fidelity=1)")
    row = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT market_id), MIN(ts), MAX(ts) "
        "FROM prices WHERE fidelity=1 AND ts > ?", (since,)).fetchone()
    count, markets, lo, hi = row
    if not count:
        print("  no minute bars in window")
        return
    span_min = max((hi - lo) / 60, 1)
    print(f"  bars                     : {count:,} across {markets} markets")
    print(f"  window                   : "
          f"{dt.datetime.fromtimestamp(lo, dt.UTC):%Y-%m-%d %H:%M} -> "
          f"{dt.datetime.fromtimestamp(hi, dt.UTC):%m-%d %H:%M} UTC")
    # A fully covered market would have one bar per minute per token.
    expected = span_min * markets * 2
    print(f"  observed / naive maximum : {100*count/expected:.1f}%")
    print("    (well under 100% is normal: closed and illiquid markets")
    print("     produce no bars, and both tokens rarely trade at once)")


def per_market_gaps(conn: sqlite3.Connection, since: int, limit: int) -> None:
    print(f"\nWORST PRICE GAPS, top {limit} markets by volume")
    markets = conn.execute(
        "SELECT market_id, question FROM markets WHERE tracked=1 "
        "ORDER BY volume_num DESC LIMIT ?", (limit,)).fetchall()
    for market_id, question in markets:
        stamps = [r[0] for r in conn.execute(
            "SELECT DISTINCT ts FROM prices WHERE market_id=? AND fidelity=1 "
            "AND ts > ? ORDER BY ts", (market_id, since))]
        if len(stamps) < 3:
            print(f"  {'(no data)':>10}  {(question or '')[:52]}")
            continue
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        worst = max(gaps)
        flag = "  <-- gap" if worst > 3600 else ""
        print(f"  {fmt_gap(worst):>10}  {(question or '')[:52]}{flag}")


def run_health(conn: sqlite3.Connection) -> None:
    print("\nRECENT RUNS")
    rows = conn.execute(
        "SELECT started, mode, n_markets, n_prices, n_trades, n_books, errors "
        "FROM runs ORDER BY started DESC LIMIT 8").fetchall()
    if not rows:
        print("  none recorded")
        return
    print(f"  {'started':17s} {'mode':9s} {'mkts':>5} {'prices':>8} "
          f"{'trades':>8} {'books':>6} {'err':>4}")
    for started, mode, n_m, n_p, n_t, n_b, err in rows:
        print(f"  {str(started)[:16]:17s} {str(mode):9s} {n_m or 0:>5} "
              f"{n_p or 0:>8,} {n_t or 0:>8,} {n_b or 0:>6,} {err or 0:>4}")


def trade_cap_risk(conn: sqlite3.Connection, since: int) -> None:
    print("\nTRADE-CAP EXPOSURE (endpoint serves only ~10,000 most recent)")
    rows = conn.execute(
        "SELECT m.question, COUNT(*) n FROM trades t "
        "JOIN markets m ON m.market_id = t.market_id "
        "WHERE t.ts > ? GROUP BY t.market_id ORDER BY n DESC LIMIT 6",
        (since,)).fetchall()
    if not rows:
        print("  no trades in window")
        return
    for question, n in rows:
        pct = 100 * n / 10000
        flag = "  <-- at risk" if pct > 60 else ""
        print(f"  {n:>7,} trades ({pct:>5.1f}% of cap)  "
              f"{(question or '')[:42]}{flag}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--days", type=float, default=2.0)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    if not args.db.exists():
        print(f"no database at {args.db}\nRun scripts/rebuild_db.py first.")
        return 1

    since = int((dt.datetime.now(dt.UTC)
                 - dt.timedelta(days=args.days)).timestamp())
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)

    print("=" * 66)
    print(f"COVERAGE REPORT  |  last {args.days:g} days  |  {args.db.name}")
    print("=" * 66)
    run_health(conn)
    book_cadence(conn, since)
    price_coverage(conn, since)
    trade_cap_risk(conn, since)
    per_market_gaps(conn, since, args.limit)
    print()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
