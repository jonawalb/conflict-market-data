#!/usr/bin/env python3
"""Backfill complete trade tapes and price histories for a list of markets.

Input is a CSV of contract URLs broken into `slug` and `event_slug` (plus a
`frame_row` id and an optional `expand` flag). Three phases:

  resolve  slug -> market id, condition id, outcome token ids via Gamma. Rows with
           expand=1 (event-level URLs) take every market in the event.
  trades   the data-api trade tape, complete. `/trades` refuses offsets past
           10,000, but each `start`/`end` window has its own offset budget, so a
           window that fills its budget is split in half until every window fits.
  prices   CLOB prices-history per outcome token, hourly (fidelity 60) or, where
           Polymarket has thinned the series, every 12 hours (fidelity 720).

The orderbook subgraph is not used: Polymarket paused it at the CLOB V2 migration
(2026-04-28) and it now serves stale data.

Checks that fail loudly rather than write a wrong tape:
  * every trade returned for a window must fall inside it (otherwise the API ignored
    start/end and the windowing would silently duplicate or drop trades);
  * the newest trades from an unwindowed request must all appear in the windowed
    tape (otherwise the window bounds are in the wrong units or too narrow).

    python3 scripts/backfill_tapes.py resolve --frame data/backfill_v6/contracts_all.csv --out out
    python3 scripts/backfill_tapes.py trades --meta out/meta.jsonl --out out --shard 0 --shards 4
    python3 scripts/backfill_tapes.py prices --meta out/meta.jsonl --out out --shard 0 --shards 4
"""

import argparse
import csv
import gzip
import json
import logging
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bow.db import trade_key  # noqa: E402  same key as the live collector, so tapes join on it

GAMMA = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
HEADERS = {"User-Agent": "Mozilla/5.0 Chrome/126"}
PAGE = 500
OFFSET_CAP = 10000            # data-api rejects offsets beyond this
EPOCH_START = 1577836800      # 2020-01-01; before Polymarket's first market
FIDELITIES = (60, 720)
TRADE_COLS = ["trade_key", "market_id", "ts", "price", "size", "side", "outcome", "wallet", "tx_hash", "asset"]

logger = logging.getLogger("backfill_tapes")


class ClientError(RuntimeError):
    """A 4xx other than 429: retrying will not help."""


def get(url: str, tries: int = 8) -> Any:
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            body = exc.read()[:200].decode(errors="replace").strip()
            if 400 <= exc.code < 500 and exc.code != 429:
                raise ClientError(f"HTTP {exc.code} {body} for {url}") from exc
            wait = min(2 ** attempt * 2, 120)
            logger.warning("HTTP %s (%s); retry in %ss", exc.code, body, wait)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            wait = min(2 ** attempt * 2, 120)
            logger.warning("request failed (%s); retry in %ss", exc, wait)
        time.sleep(wait)
    raise RuntimeError(f"giving up on {url}")


def q(base: str, path: str, **params: Any) -> str:
    return f"{base}/{path}?{urllib.parse.urlencode(params)}"


# ---------------------------------------------------------------- resolve

def market_record(m: Dict[str, Any]) -> Dict[str, Any]:
    outcomes = json.loads(m.get("outcomes") or "[]")
    return {"market_id": str(m["id"]), "condition_id": m.get("conditionId"), "question": m.get("question"),
            "market_slug": m.get("slug"), "tokens": dict(zip(outcomes, json.loads(m.get("clobTokenIds") or "[]"))),
            "outcome_prices": dict(zip(outcomes, json.loads(m.get("outcomePrices") or "[]"))),
            "closed": m.get("closed"), "volume": m.get("volumeNum"), "neg_risk": m.get("negRisk"),
            "start": m.get("startDate"), "end": m.get("endDate"), "closed_time": m.get("closedTime")}


def resolve_row(row: Dict[str, str]) -> List[Dict[str, Any]]:
    expand = row.get("expand", "0") == "1"
    if not expand:
        hits = get(q(GAMMA, "markets", slug=row["slug"]))
        if hits:
            return [market_record(hits[0])]
    events = get(q(GAMMA, "events", slug=row["event_slug"]))
    markets = events[0].get("markets", []) if events else []
    if expand:
        return [market_record(m) for m in markets]
    match = [m for m in markets if m.get("slug") == row["slug"]] or (markets if len(markets) == 1 else [])
    return [market_record(m) for m in match]


def cmd_resolve(args: argparse.Namespace) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(args.frame, newline="") as fh:
        rows = list(csv.DictReader(fh))
    by_id: Dict[str, Dict[str, Any]] = {}
    unresolved = []
    for row in rows:
        try:
            recs = resolve_row(row)
        except (RuntimeError, KeyError, ValueError) as exc:
            logger.error("resolve failed for %s: %s", row["slug"], exc)
            recs = []
        if not recs:
            unresolved.append(row)
        for rec in recs:
            prev = by_id.setdefault(rec["market_id"], {**rec, "frame_rows": []})
            prev["frame_rows"].append(row["frame_row"])
        time.sleep(0.1)
    with open(out / "meta.jsonl", "w") as fh:
        for rec in by_id.values():
            fh.write(json.dumps(rec) + "\n")
    with open(out / "unresolved.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(unresolved)
    logger.info("%d rows -> %d markets; %d rows unresolved", len(rows), len(by_id), len(unresolved))


# ---------------------------------------------------------------- trades

def window_trades(cid: str, a: int, b: int) -> Optional[List[Dict[str, Any]]]:
    """All trades with a <= ts <= b, or None when the window exceeds the offset budget."""
    got: List[Dict[str, Any]] = []
    offset = 0
    while True:
        if offset >= OFFSET_CAP:
            return None
        page = get(q(DATA_API, "trades", market=cid, start=a, end=b, limit=PAGE, offset=offset))
        if not page:
            return got
        bad = [t["timestamp"] for t in page if not a <= int(t["timestamp"]) <= b]
        if bad:
            raise RuntimeError(f"API ignored start/end: ts {bad[0]} outside [{a}, {b}]")
        got.extend(page)
        if len(page) < PAGE:
            return got
        offset += len(page)
        time.sleep(0.1)


def all_trades(cid: str, a: int, b: int, stats: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = window_trades(cid, a, b)
    stats["windows"] += 1
    if rows is not None:
        return rows
    if b - a < 60:
        raise RuntimeError(f"over {OFFSET_CAP} trades inside one minute at {a}; cannot split further")
    mid = (a + b) // 2
    return all_trades(cid, a, mid, stats) + all_trades(cid, mid + 1, b, stats)


def pull_trades(rec: Dict[str, Any], out: Path) -> Dict[str, Any]:
    cid, now = rec["condition_id"], int(time.time())
    stats: Dict[str, Any] = {"market_id": rec["market_id"], "windows": 0}
    raw = all_trades(cid, EPOCH_START, now, stats)
    newest = get(q(DATA_API, "trades", market=cid, limit=PAGE, offset=0))
    rows: Dict[str, Dict[str, Any]] = {}
    for t in raw:
        k = trade_key(t)
        rows[k] = {"trade_key": k, "market_id": rec["market_id"], "ts": int(t["timestamp"]),
                   "price": float(t["price"]), "size": float(t["size"]), "side": t.get("side"),
                   "outcome": t.get("outcome"), "wallet": t.get("proxyWallet"),
                   "tx_hash": t.get("transactionHash"), "asset": t.get("asset")}
    newest = [t for t in newest if int(t["timestamp"]) <= now]  # ignore fills after the windowed pull began
    missing = [t for t in newest if trade_key(t) not in rows]
    if missing:
        raise RuntimeError(f"{len(missing)} of the newest {len(newest)} trades absent from the windowed tape")
    path = out / "trades" / f"{rec['market_id']}.csv.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows.values(), key=lambda r: (r["ts"], r["trade_key"]))
    with gzip.open(path, "wt", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=TRADE_COLS)
        w.writeheader()
        w.writerows(ordered)
    stats.update(n_trades=len(ordered), n_raw=len(raw), notional=round(sum(r["price"] * r["size"] for r in ordered), 2),
                 first_ts=ordered[0]["ts"] if ordered else None, last_ts=ordered[-1]["ts"] if ordered else None,
                 newest_check=len(newest))
    return stats


# ---------------------------------------------------------------- prices

def pull_prices(rec: Dict[str, Any], out: Path) -> Dict[str, Any]:
    stats: Dict[str, Any] = {"market_id": rec["market_id"]}
    path = out / "prices" / f"{rec['market_id']}.csv.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["market_id", "outcome", "token", "fidelity", "t", "p"])
        for outcome, token in rec["tokens"].items():
            hist, used = [], None
            for fid in FIDELITIES:
                hist = (get(q(CLOB, "prices-history", market=token, interval="max", fidelity=fid)) or {}).get("history") or []
                time.sleep(0.1)
                if hist:
                    used = fid
                    break
            stats[f"points_{outcome}"], stats[f"fidelity_{outcome}"] = len(hist), used
            w.writerows([rec["market_id"], outcome, token, used, pt["t"], pt["p"]] for pt in hist)
    return stats


# ---------------------------------------------------------------- driver

def run_shard(args: argparse.Namespace, fn, label: str) -> None:
    out = Path(args.out)
    with open(args.meta) as fh:
        recs = [json.loads(line) for line in fh]
    recs.sort(key=lambda r: -(r.get("volume") or 0))       # heaviest markets dealt round-robin
    mine = recs[args.shard::args.shards]
    logger.info("%s shard %d/%d: %d markets", label, args.shard, args.shards, len(mine))
    with open(out / f"{label}_stats_{args.shard}.jsonl", "w") as fh:
        for i, rec in enumerate(mine, 1):
            t0 = time.time()
            try:
                stats = fn(rec, out)
            except (RuntimeError, KeyError, ValueError) as exc:
                logger.error("%s failed for %s: %s", label, rec["market_id"], exc)
                stats = {"market_id": rec["market_id"], "error": str(exc)}
            stats["seconds"] = round(time.time() - t0, 1)
            fh.write(json.dumps(stats) + "\n")
            fh.flush()
            logger.info("[%d/%d] %s", i, len(mine), stats)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve")
    r.add_argument("--frame", required=True)
    r.add_argument("--out", required=True)
    for name in ("trades", "prices"):
        p = sub.add_parser(name)
        p.add_argument("--meta", required=True)
        p.add_argument("--out", required=True)
        p.add_argument("--shard", type=int, default=0)
        p.add_argument("--shards", type=int, default=1)
    args = ap.parse_args()
    if args.cmd == "resolve":
        cmd_resolve(args)
    else:
        run_shard(args, {"trades": pull_trades, "prices": pull_prices}[args.cmd], args.cmd)


if __name__ == "__main__":
    main()
