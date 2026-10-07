#!/usr/bin/env python3
"""Sweep every Polymarket event by start date and keep the markets of one conflict theater.

An automated version of the research assistant's Russia-Ukraine pull: every event whose start
date falls in [from, to] is listed day by day from Gamma, and each market whose question matches
the theater's include terms (word-start, case-insensitive) is kept unless an exclusion rule
applies. Every exclusion is logged with its reason. Labels come from Gamma outcomePrices on
closed markets (1/0 -> YES/NO; anything else is recorded as unresolved or 50/50).

    python3 scripts/frame_sweep.py probe --day 2026-06-15
    python3 scripts/frame_sweep.py sweep --theater iran_israel --from 2022-01-01 --to 2026-10-07 \
        --out out --shard 0 --shards 8
"""

import argparse
import datetime as dt
import gzip
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_tapes import GAMMA, ClientError, get, q  # noqa: E402

logger = logging.getLogger("frame_sweep")
PAGE = 500

THEATERS = {
    # Pre-registered 2026-10-07 (v6/plan/PREREG_v6_confirmatory_48h.txt).
    "iran_israel": {
        "include": r"\b(iran|tehran|khamenei|irgc|israel|netanyahu|idf|hezbollah|lebanon|beirut|houthi|yemen|syria"
                   r"|damascus|hormuz|natanz|fordow|isfahan)",
        "partner": r"\b(iran|hezbollah|houthi|yemen|lebanon|syria)",
        "gaza": r"\b(hamas|gaza)",
    },
}
EXCLUDE_TAGS = {"sports", "esports", "games", "culture", "weather", "crypto", "soccer", "basketball", "football",
                "nba", "nfl", "mlb", "nhl", "tennis", "cricket", "ufc", "boxing", "f1", "golf", "entertainment",
                "movies", "music", "awards", "pop-culture"}
SPEECH = re.compile(r"\b(mention|say|says|said|tweet|tweets|post|posts)\b", re.I)
META = re.compile(r"polymarket|odds of|% chance|probability", re.I)


def events_on(day: dt.date, closed: Optional[bool]) -> Iterator[Dict[str, Any]]:
    a, b = day.isoformat() + "T00:00:00Z", (day + dt.timedelta(days=1)).isoformat() + "T00:00:00Z"
    offset = 0
    while True:
        params = dict(limit=PAGE, offset=offset, start_date_min=a, start_date_max=b, order="id", ascending="true")
        if closed is not None:
            params["closed"] = str(closed).lower()
        page = get(q(GAMMA, "events", **params))
        if not page:
            return
        yield from page
        if len(page) < PAGE:
            return
        offset += len(page)


def label(m: Dict[str, Any]) -> str:
    if not m.get("closed"):
        return "OPEN"
    try:
        outs = json.loads(m.get("outcomes") or "[]")
        prices = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
    except (ValueError, TypeError):
        return "UNKNOWN"
    lab = dict(zip(outs, prices))
    if lab.get("Yes", -1) >= 0.99 and lab.get("No", 1) <= 0.01:
        return "YES"
    if lab.get("No", -1) >= 0.99 and lab.get("Yes", 1) <= 0.01:
        return "NO"
    return "UNRESOLVED_OR_SPLIT"


def classify(ev: Dict[str, Any], m: Dict[str, Any], th: Dict[str, str]) -> Optional[str]:
    """None = keep; otherwise the exclusion reason."""
    qtext = m.get("question") or ""
    tags = {(t.get("slug") or "").lower() for t in ev.get("tags") or []}
    if tags & EXCLUDE_TAGS:
        return "tag:" + ",".join(sorted(tags & EXCLUDE_TAGS))
    if re.search(th["gaza"], qtext, re.I) and not re.search(th["partner"], qtext, re.I):
        return "gaza_without_partner"
    if SPEECH.search(qtext):
        return "speech_count"
    if META.search(qtext):
        return "meta_market"
    return None


def cmd_probe(args: argparse.Namespace) -> None:
    day = dt.date.fromisoformat(args.day)
    for closed in (None, True, False):
        evs = list(events_on(day, closed))
        starts = sorted({(e.get("startDate") or "")[:10] for e in evs})
        logger.info("closed=%s: %d events, start dates seen %s", closed, len(evs), starts[:5])


def cmd_sweep(args: argparse.Namespace) -> None:
    th = THEATERS[args.theater]
    inc = re.compile(th["include"], re.I)
    d0, d1 = dt.date.fromisoformat(args.from_), dt.date.fromisoformat(args.to)
    days = [d0 + dt.timedelta(days=i) for i in range((d1 - d0).days + 1)][args.shard::args.shards]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    kept, excl, n_events, n_markets, failed = [], [], 0, 0, []
    for i, day in enumerate(days, 1):
        try:
            evs = list(events_on(day, None))
        except (RuntimeError, ClientError) as exc:
            failed.append({"day": day.isoformat(), "error": str(exc)})
            continue
        n_events += len(evs)
        for ev in evs:
            for m in ev.get("markets") or []:
                n_markets += 1
                if not inc.search(m.get("question") or ""):
                    continue
                rec = {"market_id": str(m.get("id")), "question": m.get("question"), "slug": m.get("slug"),
                       "event_slug": ev.get("slug"), "event_title": ev.get("title"), "condition_id": m.get("conditionId"),
                       "tags": [t.get("slug") for t in ev.get("tags") or []], "closed": m.get("closed"),
                       "label": label(m), "volume": m.get("volumeNum"), "start": m.get("startDate") or ev.get("startDate"),
                       "end": m.get("endDate"), "closed_time": m.get("closedTime"), "neg_risk": m.get("negRisk"),
                       "enable_order_book": m.get("enableOrderBook"), "outcomes": m.get("outcomes"),
                       "tokens": m.get("clobTokenIds"), "event_start_day": day.isoformat()}
                reason = classify(ev, m, th)
                (excl if reason else kept).append({**rec, "reason": reason} if reason else rec)
        if i % 25 == 0:
            logger.info("%d/%d days; %d events, %d markets, %d kept, %d excluded", i, len(days), n_events, n_markets,
                        len(kept), len(excl))
        time.sleep(0.05)
    for name, rows in (("kept", kept), ("excluded", excl)):
        with gzip.open(out / f"{name}_{args.shard:02d}.jsonl.gz", "wt") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
    stats = {"shard": args.shard, "days": len(days), "events": n_events, "markets": n_markets, "kept": len(kept),
             "excluded": len(excl), "failed_days": failed}
    (out / f"sweep_stats_{args.shard:02d}.json").write_text(json.dumps(stats))
    logger.info("%s", stats)
    if failed:
        raise SystemExit(f"{len(failed)} days failed; see sweep_stats")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--day", required=True)
    s = sub.add_parser("sweep")
    s.add_argument("--theater", required=True, choices=sorted(THEATERS))
    s.add_argument("--from", dest="from_", required=True)
    s.add_argument("--to", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--shard", type=int, default=0)
    s.add_argument("--shards", type=int, default=1)
    args = ap.parse_args()
    {"probe": cmd_probe, "sweep": cmd_sweep}[args.cmd](args)


if __name__ == "__main__":
    main()
