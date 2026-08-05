#!/usr/bin/env python3
"""Validate on-chain decoding against the Polymarket API.

A silently wrong decoder is the worst outcome here: reconstructed history would
look plausible and be wrong, and nothing downstream would catch it. So the
decoder is checked against fills the API also reports, where the API is ground
truth.

The offline cases run with no network. Pass --live to additionally re-derive a
real transaction from Polygon and compare it against the API.

    python3 test_chain.py
    python3 test_chain.py --live
"""

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bow.chain import ORDER_FILLED, PolygonClient, decode_order_filled

# Captured from tx 0x5c6e28949d5a176d35e1d9536a71841c416afd5ef164ac74f3025778fce01c0f.
# The API reports this fill as price 0.999, size 7, SELL, outcome "No".
SELL_LOG = {
    "topics": [
        ORDER_FILLED,
        "0x7279571fec3f1f2d3e90b7a7e4b452a50ee0b0b932bf0ddf381df2555d18d489",
        "0x000000000000000000000000155e851465fb898fe6fde319bdc3a56032ddeda1",
        "0x0000000000000000000000004bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
    ],
    "data": "0x"
            + f"{34465433013244737261777301958314575283534514587255327061285941306189159190135:064x}"
            + f"{0:064x}" + f"{7000000:064x}" + f"{6993000:064x}" + f"{0:064x}",
    "transactionHash": "0x5c6e",
    "blockNumber": "0x4e959d2",
    "logIndex": "0x1",
}

# The counterparty leg of the same transaction: the buy side.
BUY_LOG = {
    "topics": [
        ORDER_FILLED,
        "0xed43d23570ecf94bfc5f7e36ebadaee80d062f22c32f48a37299f4a092626805",
        "0x0000000000000000000000001521b47bf0c41f6b7fd3ad41cdec566812c8f23e",
        "0x000000000000000000000000155e851465fb898fe6fde319bdc3a56032ddeda1",
    ],
    "data": "0x" + f"{0:064x}"
            + f"{34465433013244737261777301958314575283534514587255327061285941306189159190135:064x}"
            + f"{6993000:064x}" + f"{7000000:064x}" + f"{0:064x}",
    "transactionHash": "0x5c6e",
    "blockNumber": "0x4e959d2",
    "logIndex": "0x0",
}

TOKEN_NO = "34465433013244737261777301958314575283534514587255327061285941306189159190135"


def check(label, actual, expected, failures):
    ok = actual == expected or (
        isinstance(expected, float) and abs(actual - expected) < 1e-9)
    if not ok:
        failures.append(label)
        print(f"  FAIL {label}: expected {expected!r}, got {actual!r}")
    return ok


def test_offline() -> int:
    failures = []
    sell = decode_order_filled(SELL_LOG)
    check("sell.price", round(sell["price"], 4), 0.999, failures)
    check("sell.size", sell["size"], 7.0, failures)
    check("sell.side", sell["side"], "SELL", failures)
    check("sell.token_id", sell["token_id"], TOKEN_NO, failures)
    check("sell.wallet", sell["wallet"], "0x155e851465fb898fe6fde319bdc3a56032ddeda1",
          failures)

    buy = decode_order_filled(BUY_LOG)
    check("buy.price", round(buy["price"], 4), 0.999, failures)
    check("buy.size", buy["size"], 7.0, failures)
    check("buy.side", buy["side"], "BUY", failures)
    check("buy.token_id", buy["token_id"], TOKEN_NO, failures)

    # Token-for-token fills carry no price and must be dropped, not guessed at.
    both = dict(SELL_LOG)
    both["data"] = "0x" + f"{123:064x}" + f"{456:064x}" + f"{1:064x}" + f"{1:064x}" + f"{0:064x}"
    if decode_order_filled(both) is not None:
        failures.append("token-for-token should decode to None")
        print("  FAIL token-for-token fill should be dropped")

    if decode_order_filled({"topics": [], "data": "0x"}) is not None:
        failures.append("malformed should decode to None")
        print("  FAIL malformed log should be dropped")

    print(f"offline decoding: {'PASS' if not failures else f'{len(failures)} FAILURES'}")
    return len(failures)


def test_live() -> int:
    failures = []
    tx = "0x5c6e28949d5a176d35e1d9536a71841c416afd5ef164ac74f3025778fce01c0f"
    client = PolygonClient()
    receipt = client.call("eth_getTransactionReceipt", [tx])
    if not receipt:
        print("  FAIL could not fetch receipt")
        return 1

    chain_trades = [
        t for t in (decode_order_filled(l) for l in receipt["logs"]
                    if l["topics"] and l["topics"][0].lower() == ORDER_FILLED)
        if t
    ]
    req = urllib.request.Request(
        f"https://data-api.polymarket.com/trades?market="
        f"0x23fd2b26c4e095465ba0d2ebce8d5eda57009ddc59aad8b68ab19ca968b41eed&limit=100",
        headers={"User-Agent": "Mozilla/5.0"})
    api_trades = [t for t in json.loads(urllib.request.urlopen(req, timeout=40).read())
                  if t["transactionHash"] == tx]

    print(f"  chain: {len(chain_trades)} fills | api: {len(api_trades)} rows")
    for api in api_trades:
        match = next((c for c in chain_trades
                      if abs(c["price"] - float(api["price"])) < 1e-6
                      and abs(c["size"] - float(api["size"])) < 1e-6
                      and c["side"] == api["side"]), None)
        if match:
            print(f"    matched {api['side']:4s} {api['size']} @ {api['price']}")
        else:
            failures.append(f"no chain match for {api['side']} {api['size']}")
            print(f"    FAIL no chain match for {api['side']} {api['size']} @ {api['price']}")

    print(f"live cross-check: {'PASS' if not failures else f'{len(failures)} FAILURES'}")
    return len(failures)


if __name__ == "__main__":
    total = test_offline()
    if "--live" in sys.argv:
        total += test_live()
    print("ALL PASS" if total == 0 else f"{total} FAILURES")
    sys.exit(1 if total else 0)
