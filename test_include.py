#!/usr/bin/env python3
"""Offline test of the registry include list (bow.discover.apply_includes).

An included market must survive save_registry even when the classifier rejects
it, be tracked only while open (plus the grace window), and be fetched by id when
discovery did not see it.

Run: python3 test_include.py
"""

import datetime as dt
import gzip
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

from bow.config import Config
from bow.discover import apply_includes

import ci_collect


class FakeClient:
    def __init__(self, markets):
        self.markets = markets
        self.asked = []

    def market_by_id(self, market_id):
        self.asked.append(market_id)
        return self.markets.get(market_id)


def iso(days: int) -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)).isoformat()


def main() -> int:
    cfg = Config(root=Path("."), db_path=Path("unused"), log_path=Path("unused"))
    discovered = [
        {"market_id": "1", "question": "Will X strike Y?", "escalation": 1, "tracked": 1,
         "closed": 0, "end_date": iso(30)},
        {"market_id": "2", "question": "Who will be head of state in Iran?", "escalation": 0,
         "tracked": 0, "closed": 0, "end_date": iso(60)},
    ]
    client = FakeClient({
        "3": {"id": "3", "question": "Strait of Hormuz traffic returns to normal?", "closed": False,
              "endDate": iso(20), "clobTokenIds": '["a", "b"]', "events": [{"title": "Hormuz"}]},
        "4": {"id": "4", "question": "Old resolved contract?", "closed": True,
              "endDate": iso(-90), "events": [{"title": "Old"}]},
    })
    rows = apply_includes(client, cfg, discovered, ["2", "3", "4", "5"])
    by_id = {r["market_id"]: r for r in rows}
    failures = 0

    def check(cond: bool, msg: str) -> None:
        nonlocal failures
        print(("ok   " if cond else "FAIL ") + msg)
        failures += 0 if cond else 1

    check(client.asked == ["3", "4", "5"], "only undiscovered ids are fetched")
    check(by_id["2"]["tracked"] == 1 and by_id["2"]["escalation"] == 0,
          "discovered non-escalation market is tracked, classifier verdict kept")
    check(by_id["3"]["tracked"] == 1 and by_id["3"]["token_yes"] == "a", "fetched open market is tracked")
    check(by_id["4"]["tracked"] == 0, "resolved market past the grace window is not tracked")
    check("5" not in by_id, "unknown id is skipped")
    check(by_id["1"].get("included") is None, "markets not on the list are untouched")

    tmp = Path(tempfile.mkdtemp(prefix="bow-include-"))
    ci_collect.REGISTRY = tmp / "markets.json.gz"
    ci_collect.save_registry(rows)
    with gzip.open(ci_collect.REGISTRY, "rt") as fh:
        saved = {r["market_id"] for r in json.load(fh)}
    check(saved == {"1", "2", "3", "4"}, "registry keeps escalation and included markets")

    print("ALL PASS" if not failures else f"{failures} FAILURES")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
