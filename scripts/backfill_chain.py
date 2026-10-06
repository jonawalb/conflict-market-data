#!/usr/bin/env python3
"""Recover trades the data-api does not serve, from Polygon event logs.

Two cases, both found by scripts/backfill_tapes.py's completeness check:

  amm   Pre-order-book markets (2022) traded against a fixed-product market maker
        (FPMM). Its FPMMBuy/FPMMSell events are filtered by the market maker's
        address, so each market is a cheap, exact query.
  clob  Order-book markets the data-api has dropped (seen for 2023). OrderFilled
        does not index the token id, so a block range is scanned and filtered
        locally; one scan serves every target market at once, and the range is
        sharded across jobs.

Unlike bow.chain.scan_range, nothing here skips a block range after repeated RPC
failure: a range that cannot be read raises, so a gap can never pass as a
complete tape. Only maker-side OrderFilled events are kept; the event a match
emits for the taker order (taker = the exchange contract) restates the same
volume.

    python3 scripts/backfill_chain.py amm  --meta out/meta.jsonl --ids ids.csv --out out
    python3 scripts/backfill_chain.py clob --meta out/meta.jsonl --ids ids.csv --out out \
        --from-date 2023-02-01 --to-date 2024-01-03 --shard 0 --shards 24
"""

import argparse
import csv
import datetime as dt
import gzip
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from Crypto.Hash import keccak

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bow.chain import (EXCHANGES, ORDER_FILLED, USDC_DECIMALS, ChainError,  # noqa: E402
                       PolygonClient, decode_order_filled)
from backfill_tapes import GAMMA, get, q  # noqa: E402

logger = logging.getLogger("backfill_chain")


def topic(signature: str) -> str:
    k = keccak.new(digest_bits=256)
    k.update(signature.encode())
    return "0x" + k.hexdigest()


# The OrderFilled constant in bow.chain was verified against real logs; recomputing
# it here proves the hashing and the signature-writing convention before they are
# trusted for the FPMM events.
assert topic("OrderFilled(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)") == ORDER_FILLED
FPMM_BUY = topic("FPMMBuy(address,uint256,uint256,uint256,uint256)")
FPMM_SELL = topic("FPMMSell(address,uint256,uint256,uint256,uint256)")
EXCHANGE_SET = {e.lower() for e in EXCHANGES}
COLS = ["market_id", "ts", "price", "size", "side", "outcome", "wallet", "counterparty", "tx_hash", "block",
        "log_index", "source", "usdc", "fee"]


def get_logs(client: PolygonClient, address: str, a: int, b: int, chunk: int,
             topics: List[Any] = None) -> List[Dict[str, Any]]:
    """Every log in [a, b] for `address`; narrows chunks on failure and raises if a range stays unreadable."""
    out: List[Dict[str, Any]] = []
    start = a
    while start <= b:
        end = min(start + chunk - 1, b)
        flt = {"fromBlock": hex(start), "toBlock": hex(end), "address": address}
        if topics:
            flt["topics"] = topics
        try:
            out.extend(client.call("eth_getLogs", [flt]) or [])
        except ChainError:
            if chunk <= 50:
                raise
            chunk = max(chunk // 2, 50)
            continue
        start = end + 1
    return out


class BlockTimes:
    def __init__(self, client: PolygonClient) -> None:
        self.client, self.cache = client, {}

    def __call__(self, block: int) -> int:
        if block not in self.cache:
            self.cache[block] = self.client.block_time(block)
        return self.cache[block]


def parse_ts(value: Any) -> int:
    return int(dt.datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T")).timestamp())


def load(meta_path: str, ids_path: str) -> List[Dict[str, Any]]:
    with open(ids_path, newline="") as fh:
        ids = {row["market_id"] for row in csv.DictReader(fh)}
    with open(meta_path) as fh:
        recs = [json.loads(line) for line in fh]
    return [r for r in recs if r["market_id"] in ids]


def write(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["block"], r["log_index"])))


# ---------------------------------------------------------------- amm

def amm_market(client: PolygonClient, times: BlockTimes, rec: Dict[str, Any], out: Path) -> Dict[str, Any]:
    m = get(q(GAMMA, f"markets/{rec['market_id']}"))
    addr = (m.get("marketMakerAddress") or "").lower()
    stats: Dict[str, Any] = {"market_id": rec["market_id"], "fpmm": addr, "volumeNum": m.get("volumeNum"),
                             "enableOrderBook": m.get("enableOrderBook")}
    if not addr:
        stats["error"] = "no market maker address"
        return stats
    starts = [parse_ts(v) for v in (m.get("createdAt"), m.get("startDate")) if v]
    end = parse_ts(m.get("closedTime") or m.get("endDate"))
    a, b = client.block_at_time(min(starts) - 86400), client.block_at_time(end + 2 * 86400)
    logs = get_logs(client, addr, a, b, chunk=10000)
    stats["blocks"] = [a, b]
    stats["topic0_counts"] = dict(Counter(lg["topics"][0] for lg in logs if lg.get("topics")))
    outcomes = json.loads(m.get("outcomes") or "[]")  # FPMM outcomeIndex = position in the condition's outcomes
    rows, usdc, shares = [], 0, 0
    for lg in logs:
        t0 = lg["topics"][0] if lg.get("topics") else None
        if t0 not in (FPMM_BUY, FPMM_SELL):
            continue
        words = [int(lg["data"][2 + i:2 + i + 64], 16) for i in range(0, len(lg["data"]) - 2, 64)]
        cash, fee, tokens = words[:3]
        idx = int(lg["topics"][2], 16)
        if tokens == 0:
            continue
        buy = t0 == FPMM_BUY
        block = int(lg["blockNumber"], 16)
        rows.append({"market_id": rec["market_id"], "ts": times(block),
                     "price": (cash - fee) / tokens if buy else (cash + fee) / tokens,
                     "size": tokens / USDC_DECIMALS, "side": "BUY" if buy else "SELL",
                     "outcome": outcomes[idx] if idx < len(outcomes) else str(idx),
                     "wallet": "0x" + lg["topics"][1][-40:], "counterparty": addr,
                     "tx_hash": lg["transactionHash"], "block": block, "log_index": int(lg["logIndex"], 16),
                     "source": "fpmm", "usdc": cash / USDC_DECIMALS, "fee": fee / USDC_DECIMALS})
        usdc += cash
        shares += tokens
    write(out / "chain_trades" / f"{rec['market_id']}.csv.gz", rows)
    vol = float(m.get("volumeNum") or 0)
    stats.update(n_trades=len(rows), usdc_volume=round(usdc / USDC_DECIMALS, 2),
                 share_volume=round(shares / USDC_DECIMALS, 2),
                 usdc_ratio=round(usdc / USDC_DECIMALS / vol, 6) if vol else None)
    return stats


def cmd_amm(args: argparse.Namespace) -> None:
    out = Path(args.out)
    client = PolygonClient()
    times = BlockTimes(client)
    recs = load(args.meta, args.ids)
    with open(out / "amm_stats.jsonl", "w") as fh:
        for i, rec in enumerate(recs, 1):
            t0 = time.time()
            try:
                stats = amm_market(client, times, rec, out)
            except (ChainError, RuntimeError, KeyError, ValueError) as exc:
                stats = {"market_id": rec["market_id"], "error": str(exc)}
            stats["seconds"] = round(time.time() - t0, 1)
            fh.write(json.dumps(stats) + "\n")
            fh.flush()
            logger.info("[%d/%d] %s", i, len(recs), stats)


# ---------------------------------------------------------------- clob

def cmd_clob(args: argparse.Namespace) -> None:
    out = Path(args.out)
    client = PolygonClient()
    times = BlockTimes(client)
    token_map = {}
    for rec in load(args.meta, args.ids):
        for outcome, tok in rec["tokens"].items():
            token_map[str(tok)] = (rec["market_id"], outcome)
    t_from = int(dt.datetime.fromisoformat(args.from_date).replace(tzinfo=dt.timezone.utc).timestamp())
    t_to = int(dt.datetime.fromisoformat(args.to_date).replace(tzinfo=dt.timezone.utc).timestamp())
    span = (t_to - t_from) / args.shards
    s_from, s_to = int(t_from + args.shard * span), int(t_from + (args.shard + 1) * span)
    a = client.block_at_time(s_from, tolerance=5)
    b = client.block_at_time(s_to, tolerance=5) - 1 if args.shard < args.shards - 1 else client.block_at_time(t_to, tolerance=5)
    logger.info("shard %d/%d: %s .. %s = blocks %d-%d, %d tokens", args.shard, args.shards,
                dt.datetime.utcfromtimestamp(s_from), dt.datetime.utcfromtimestamp(s_to), a, b, len(token_map))
    t0 = time.time()
    rows, n_logs, n_taker = [], 0, 0
    chunk = args.chunk
    start = a
    while start <= b:
        end = min(start + chunk - 1, b)
        try:
            logs = []
            for ex in EXCHANGES:
                logs.extend(get_logs(client, ex, start, end, chunk=end - start + 1, topics=[ORDER_FILLED]))
        except ChainError:
            if chunk <= 50:
                raise
            chunk = max(chunk // 2, 50)
            continue
        n_logs += len(logs)
        for lg in logs:
            tr = decode_order_filled(lg)
            if not tr or tr["token_id"] not in token_map:
                continue
            if tr["counterparty"].lower() in EXCHANGE_SET:
                n_taker += 1
                continue
            mid, outcome = token_map[tr["token_id"]]
            rows.append({"market_id": mid, "ts": times(tr["block"]), "price": tr["price"], "size": tr["size"],
                         "side": tr["side"], "outcome": outcome, "wallet": tr["wallet"],
                         "counterparty": tr["counterparty"], "tx_hash": tr["tx_hash"], "block": tr["block"],
                         "log_index": tr["log_index"], "source": "orderfilled", "usdc": tr["price"] * tr["size"],
                         "fee": ""})
        start = end + 1
        if chunk < args.chunk:
            chunk = min(chunk * 2, args.chunk)
    write(out / "chain_clob" / f"shard_{args.shard:02d}.csv.gz", rows)
    stats = {"shard": args.shard, "blocks": [a, b], "n_logs": n_logs, "n_kept": len(rows), "n_taker_summary": n_taker,
             "seconds": round(time.time() - t0, 1), "rpc_calls": client.calls}
    (out / f"clob_stats_{args.shard:02d}.json").write_text(json.dumps(stats))
    logger.info("%s", stats)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("amm", "clob"):
        p = sub.add_parser(name)
        p.add_argument("--meta", required=True)
        p.add_argument("--ids", required=True)
        p.add_argument("--out", required=True)
    c = sub.choices["clob"]
    c.add_argument("--from-date", required=True)
    c.add_argument("--to-date", required=True)
    c.add_argument("--shard", type=int, default=0)
    c.add_argument("--shards", type=int, default=1)
    c.add_argument("--chunk", type=int, default=2000)
    args = ap.parse_args()
    (cmd_amm if args.cmd == "amm" else cmd_clob)(args)


if __name__ == "__main__":
    main()
