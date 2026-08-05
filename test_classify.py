#!/usr/bin/env python3
"""Regression tests for the escalation classifier and book summariser.

Run: python3 test_classify.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bow.collect import summarise_book
from bow.discover import classify

# (question, expected_in_panel) — the False cases are all real false positives
# observed in live Polymarket data.
CASES = [
    ("Will the U.S. invade Iran before 2027?", True),
    ("US forces enter Iran by December 31?", True),
    ("Israel strikes Iran by June 30, 2026?", True),
    ("Israel x Iran ceasefire continues through July 31?", True),
    ("Will China invade Taiwan by end of 2026?", True),
    ("US x Venezuela military engagement by December 31?", True),
    ("Will Russia use a nuclear weapon in 2026?", True),
    ("China x Taiwan military clash before 2027?", True),
    ("Russia x Ukraine ceasefire by end of 2026?", True),
    ("US strike on Cuba by December 31?", True),
    # Substring traps: "Warnock"/"Warren"/"warning" all contain "war".
    ("Will Raphael Warnock win the 2028 Democratic presidential nomination?", False),
    ("Will Elizabeth Warren run for president?", False),
    ("Trump tariff warning issued?", False),
    ("Will the reward be claimed?", False),
    # Labour actions and metaphor.
    ("When will the NYC nurse strike end?", False),
    ("Air Canada strike before September?", False),
    ("Will there be a trade war with China?", False),
    ("Star Wars box office over $500M?", False),
    ("Will a new nuclear power plant open in 2026?", False),
    # Unrelated.
    ("Nvidia earnings beat?", False),
    ("Best Picture: All Quiet on the Western Front", False),
]


def test_classify() -> int:
    failures = 0
    for question, expected in CASES:
        actual = classify(question, "")
        if actual != expected:
            failures += 1
            print(f"  FAIL expected={expected} got={actual}: {question}")
    print(f"classify: {len(CASES) - failures}/{len(CASES)} passed")
    return failures


def test_summarise_book() -> int:
    failures = 0
    book = {
        "timestamp": "1700000000",
        "bids": [{"price": "0.40", "size": "100"}, {"price": "0.44", "size": "50"}],
        "asks": [{"price": "0.50", "size": "80"}, {"price": "0.46", "size": "20"}],
    }
    row = summarise_book(book, "tok", "mkt", 1700000001)
    checks = {
        "best_bid": 0.44,
        "best_ask": 0.46,
        "mid": 0.45,
        "n_bid_levels": 2,
        "n_ask_levels": 2,
        "bid_size_total": 150.0,
        "ask_size_total": 100.0,
    }
    for key, expected in checks.items():
        if abs((row[key] or 0) - expected) > 1e-9:
            failures += 1
            print(f"  FAIL {key}: expected {expected} got {row[key]}")
    # Depth within 1c of a 0.45 mid: only the 0.44 bid and 0.46 ask qualify.
    if row["bid_depth_1c"] != 50.0:
        failures += 1
        print(f"  FAIL bid_depth_1c: expected 50.0 got {row['bid_depth_1c']}")
    if row["ask_depth_1c"] != 20.0:
        failures += 1
        print(f"  FAIL ask_depth_1c: expected 20.0 got {row['ask_depth_1c']}")
    if summarise_book({"bids": [], "asks": []}, "t", "m", 1) is not None:
        failures += 1
        print("  FAIL empty book should return None")
    print(f"summarise_book: {'passed' if failures == 0 else f'{failures} failures'}")
    return failures


if __name__ == "__main__":
    total = test_classify() + test_summarise_book()
    print("ALL PASS" if total == 0 else f"{total} FAILURES")
    sys.exit(1 if total else 0)
