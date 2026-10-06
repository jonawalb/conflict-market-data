#!/usr/bin/env python3
"""Backfill full trade tapes for closed markets from Polymarket's orderbook subgraph.

`reconstruct.py` scans Polygon logs itself, which costs hours per market because
`OrderFilled` does not index the token id. The public Goldsky orderbook subgraph
indexes the same events with the asset ids as filterable fields, so a market's
whole history becomes a paginated query.

Input is a CSV with at least `frame_row`, `slug`, `event_slug` columns (a research
frame of contract URLs). Two phases:

  resolve  slug -> market id, condition id, YES/NO token ids via Gamma
  pull     every OrderFilled event touching each token, one gzip CSV per market

Only maker-side fills are written as trades. A matched order also emits one
OrderFilled for the taker order with the exchange contract as `taker`; that event
re-reports volume already in the maker fills, so it is counted but not kept.

    python3 scripts/backfill_subgraph.py resolve --frame data/backfill_v6/contracts.csv --out out
    python3 scripts/backfill_subgraph.py pull --meta out/meta.jsonl --out out --shard 0 --shards 6
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
from typing import Any, Dict, Iterator, List, Optional

SUBGRAPH = ("https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw"
            "/subgraphs/orderbook-subgraph/0.0.1/gn")
GAMMA = "https://gamma-api.polymarket.com"
EXCHANGES = {
    "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",  # CTF Exchange
    "0xc5d563a36ae78145c45a50134d48a1215220f80a",  # NegRisk CTF Exchange
}
USDC = 10 ** 6
PAGE = 1000
PACE = 0.5  # seconds between subgraph pages; the public endpoint rate-limits
HEADERS = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 Chrome/126"}
TRADE_COLS = ["trade_key", "market_id", "ts", "price", "size", "side", "outcome", "wallet", "counterparty",
              "tx_hash"]

logger = logging.getLogger("backfill_subgraph")


def _request(req: urllib.request.Request, tries: int = 10) -> Any:
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            body = exc.read()[:300].decode(errors="replace")
            wait = int(exc.headers.get("Retry-After") or min(2 ** attempt * 3, 180))
            logger.warning("HTTP %s (%s); retry in %ss", exc.code, body.strip(), wait)
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            wait = min(2 ** attempt * 3, 180)
            logger.warning("request failed (%s); retry in %ss", exc, wait)
            time.sleep(wait)
    raise RuntimeError(f"giving up on {req.full_url}")


def gamma(path: str, **params: str) -> Any:
    url = f"{GAMMA}/{path}?{urllib.parse.urlencode(params)}"
    return _request(urllib.request.Request(url, headers=HEADERS))


def graphql(query: str) -> Dict[str, Any]:
    body = json.dumps({"query": query}).encode()
    out = _request(urllib.request.Request(SUBGRAPH, data=body, headers=HEADERS))
    if out.get("errors"):
        raise RuntimeError(f"subgraph error: {out['errors']}")
    return out["data"]


# ---------------------------------------------------------------- resolve

def _market_record(m: Dict[str, Any]) -> Dict[str, Any]:
    tokens = json.loads(m.get("clobTokenIds") or "[]")
    outcomes = json.loads(m.get("outcomes") or "[]")
    prices = json.loads(m.get("outcomePrices") or "[]")
    return {"market_id": str(m.get("id")), "condition_id": m.get("conditionId"), "question": m.get("question"),
            "market_slug": m.get("slug"), "tokens": dict(zip(outcomes, tokens)),
            "outcome_prices": dict(zip(outcomes, prices)), "closed": m.get("closed"),
            "volume": m.get("volumeNum"), "neg_risk": m.get("negRisk"),
            "start": m.get("startDate"), "end": m.get("endDate"), "closed_time": m.get("closedTime")}


def resolve_one(slug: str, event_slug: str) -> Optional[Dict[str, Any]]:
    hits = gamma("markets", slug=slug)
    if hits:
        return _market_record(hits[0])
    events = gamma("events", slug=event_slug)
    markets = events[0].get("markets", []) if events else []
    match = [m for m in markets if m.get("slug") == slug] or (markets if len(markets) == 1 else [])
    return _market_record(match[0]) if match else None


def cmd_resolve(args: argparse.Namespace) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(args.frame, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if args.limit:
        rows = rows[:args.limit]
    ok = 0
    with open(out / "meta.jsonl", "w") as fh:
        for row in rows:
            try:
                rec = resolve_one(row["slug"], row["event_slug"])
            except RuntimeError as exc:
                logger.error("resolve failed for %s: %s", row["slug"], exc)
                rec = None
            rec = {**(rec or {"market_id": None}), "frame_row": row["frame_row"], "slug": row["slug"]}
            ok += rec["market_id"] is not None
            fh.write(json.dumps(rec) + "\n")
            time.sleep(0.15)
    logger.info("resolved %d / %d", ok, len(rows))


# ---------------------------------------------------------------- pull

def fills_for_token(token: str, side_field: str) -> Iterator[Dict[str, Any]]:
    """Every OrderFilled event whose maker or taker asset is `token`, paginated by id."""
    last = ""
    while True:
        q = ("{ orderFilledEvents(first: %d, orderBy: id, orderDirection: asc, "
             "where: {%s: \"%s\", id_gt: \"%s\"}) { id transactionHash timestamp maker taker "
             "makerAssetId takerAssetId makerAmountFilled takerAmountFilled } }") % (PAGE, side_field, token, last)
        page = graphql(q)["orderFilledEvents"]
        time.sleep(PACE)
        yield from page
        if len(page) < PAGE:
            return
        last = page[-1]["id"]


def to_trade(ev: Dict[str, Any], token: str, outcome: str, market_id: str) -> Optional[Dict[str, Any]]:
    maker_asset, taker_asset = ev["makerAssetId"], ev["takerAssetId"]
    maker_amt, taker_amt = int(ev["makerAmountFilled"]), int(ev["takerAmountFilled"])
    if maker_asset == token and taker_asset == "0":
        size_raw, cash_raw, side = maker_amt, taker_amt, "SELL"
    elif taker_asset == token and maker_asset == "0":
        size_raw, cash_raw, side = taker_amt, maker_amt, "BUY"
    else:
        return None
    if size_raw == 0:
        return None
    return {"trade_key": ev["id"], "market_id": market_id, "ts": int(ev["timestamp"]), "price": cash_raw / size_raw,
            "size": size_raw / USDC, "side": side, "outcome": outcome, "wallet": ev["maker"],
            "counterparty": ev["taker"], "tx_hash": ev["transactionHash"]}


def pull_market(rec: Dict[str, Any], out: Path) -> Dict[str, Any]:
    stats = {"market_id": rec["market_id"], "frame_row": rec["frame_row"], "n_events": 0, "n_taker_summary": 0,
             "n_token_swap": 0, "n_trades": 0, "notional": 0.0, "first_ts": None, "last_ts": None}
    path = out / "tapes" / f"{rec['market_id']}.csv.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    with gzip.open(path, "wt", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=TRADE_COLS)
        w.writeheader()
        for outcome, token in rec["tokens"].items():
            for field in ("makerAssetId", "takerAssetId"):
                for ev in fills_for_token(token, field):
                    if ev["id"] in seen:
                        continue
                    seen.add(ev["id"])
                    stats["n_events"] += 1
                    if ev["taker"].lower() in EXCHANGES:
                        stats["n_taker_summary"] += 1
                        continue
                    t = to_trade(ev, token, outcome, rec["market_id"])
                    if t is None:
                        stats["n_token_swap"] += 1
                        continue
                    w.writerow(t)
                    stats["n_trades"] += 1
                    stats["notional"] += t["price"] * t["size"]
                    stats["first_ts"] = min(filter(None, [stats["first_ts"], t["ts"]]))
                    stats["last_ts"] = max(filter(None, [stats["last_ts"], t["ts"]]))
    return stats


def cmd_pull(args: argparse.Namespace) -> None:
    out = Path(args.out)
    with open(args.meta) as fh:
        recs = [json.loads(line) for line in fh]
    recs = [r for r in recs if r.get("market_id") and r.get("tokens")]
    # Balance shards by volume: heaviest markets dealt round-robin.
    recs.sort(key=lambda r: -(r.get("volume") or 0))
    mine = recs[args.shard::args.shards]
    logger.info("shard %d/%d: %d markets", args.shard, args.shards, len(mine))
    with open(out / f"pull_stats_{args.shard}.jsonl", "w") as fh:
        for i, rec in enumerate(mine, 1):
            t0 = time.time()
            try:
                stats = pull_market(rec, out)
            except RuntimeError as exc:
                logger.error("pull failed for %s: %s", rec["market_id"], exc)
                stats = {"market_id": rec["market_id"], "frame_row": rec["frame_row"], "error": str(exc)}
            stats["seconds"] = round(time.time() - t0, 1)
            fh.write(json.dumps(stats) + "\n")
            fh.flush()
            logger.info("[%d/%d] %s %s", i, len(mine), rec["market_id"], stats)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve")
    r.add_argument("--frame", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--limit", type=int, default=0)
    p = sub.add_parser("pull")
    p.add_argument("--meta", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--shard", type=int, default=0)
    p.add_argument("--shards", type=int, default=1)
    args = ap.parse_args()
    {"resolve": cmd_resolve, "pull": cmd_pull}[args.cmd](args)


if __name__ == "__main__":
    main()
