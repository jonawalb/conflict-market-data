"""Discovery of the tracked market universe.

Two passes: a keyword search sweep and a scan of currently open high-volume
events. Markets are classified into the tight military-escalation panel and
flagged for tracking while they remain open (plus a grace window).
"""

import datetime as dt
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from .api import ApiError, PolymarketClient
from .config import ESCALATION_PATTERNS, EXCLUDE_PATTERNS, SEARCH_QUERIES, Config

logger = logging.getLogger(__name__)

_ESCALATION_RE = re.compile("|".join(ESCALATION_PATTERNS), re.IGNORECASE)
_EXCLUDE_RE = re.compile("|".join(EXCLUDE_PATTERNS), re.IGNORECASE)


def classify(question: str, event_title: str) -> bool:
    """Return True if the market belongs in the military-escalation panel."""
    text = f"{question} {event_title}"
    if _EXCLUDE_RE.search(text):
        return False
    return bool(_ESCALATION_RE.search(text))


def _parse_tokens(raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not raw:
        return None, None
    try:
        tokens = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None, None
    if not isinstance(tokens, list) or not tokens:
        return None, None
    return tokens[0], tokens[1] if len(tokens) > 1 else None


def _is_still_tracked(market: Dict[str, Any], grace_days: int) -> bool:
    """Track while open, and for a grace window past the end date.

    The grace window matters: the CLOB history endpoint returns nothing once a
    market resolves, so the final days of the tape must be captured live.
    """
    if not market.get("closed"):
        return True
    end_date = market.get("endDate") or market.get("end_date")
    if not end_date:
        return False
    try:
        end = dt.datetime.fromisoformat(str(end_date).replace("Z", "+00:00"))
    except ValueError:
        return False
    age = dt.datetime.now(dt.timezone.utc) - end
    return age.days <= grace_days


def _to_row(market: Dict[str, Any], event: Dict[str, Any], cfg: Config) -> Optional[Dict[str, Any]]:
    market_id = market.get("id")
    if not market_id:
        return None
    question = market.get("question") or ""
    event_title = event.get("title") or ""
    token_yes, token_no = _parse_tokens(market.get("clobTokenIds"))
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    escalation = classify(question, event_title)
    tracked = escalation and _is_still_tracked(market, cfg.grace_days)
    return {
        "market_id": str(market_id),
        "condition_id": market.get("conditionId"),
        "slug": market.get("slug") or event.get("slug"),
        "question": question,
        "event_title": event_title,
        "category": market.get("category") or event.get("category"),
        "start_date": market.get("startDate"),
        "end_date": market.get("endDate"),
        "created_at": market.get("createdAt"),
        "closed": 1 if market.get("closed") else 0,
        "active": 1 if market.get("active") else 0,
        "token_yes": token_yes,
        "token_no": token_no,
        "volume_num": market.get("volumeNum") or 0.0,
        "liquidity_num": market.get("liquidityNum") or 0.0,
        "escalation": 1 if escalation else 0,
        "tracked": 1 if tracked else 0,
        "last_seen": now,
    }


def discover(client: PolymarketClient, cfg: Config) -> List[Dict[str, Any]]:
    """Sweep both discovery paths and return deduplicated market rows."""
    found: Dict[str, Dict[str, Any]] = {}

    for query in SEARCH_QUERIES:
        try:
            events = client.search_events(query)
        except Exception as exc:
            # Discovery runs ~35 queries; one bad response must not cost the
            # whole run. A scheduled run died here on a ConnectionResetError.
            logger.warning("search failed for %r: %s", query, exc)
            continue
        for event in events:
            for market in event.get("markets") or []:
                row = _to_row(market, event, cfg)
                if row:
                    found[row["market_id"]] = row
    logger.info("keyword sweep: %d markets", len(found))

    for offset in (0, 500, 1000):
        try:
            events = client.open_events(limit=500, offset=offset)
        except Exception as exc:
            logger.warning("open-events scan failed at offset %d: %s", offset, exc)
            break
        if not events:
            break
        for event in events:
            for market in event.get("markets") or []:
                row = _to_row(market, event, cfg)
                if row and row["market_id"] not in found:
                    found[row["market_id"]] = row

    rows = list(found.values())
    tracked = sum(r["tracked"] for r in rows)
    escalation = sum(r["escalation"] for r in rows)
    logger.info(
        "discovery complete: %d markets seen, %d escalation, %d tracked",
        len(rows), escalation, tracked,
    )
    return rows


def apply_includes(
    client: PolymarketClient, cfg: Config, rows: List[Dict[str, Any]], include_ids: List[str],
) -> List[Dict[str, Any]]:
    """Force-track markets named in data/registry/include.csv.

    Research frames hold contracts the keyword sweep misses or the escalation
    filter rejects. An included market is kept in the registry and tracked while
    open (plus the grace window), whatever the classifier says; `escalation` keeps
    the classifier's verdict. Markets discovery did not see are fetched by id.
    """
    by_id = {r["market_id"]: r for r in rows}
    fetched = failed = 0
    for market_id in include_ids:
        row = by_id.get(market_id)
        if row is None:
            try:
                market = client.market_by_id(market_id)
            except Exception as exc:
                logger.warning("include: fetch failed for %s: %s", market_id, exc)
                market = None
            if not market:
                failed += 1
                continue
            events = market.get("events") or [{}]
            row = _to_row(market, events[0], cfg)
            if row is None:
                failed += 1
                continue
            by_id[market_id] = row
            fetched += 1
        row["included"] = 1
        still = _is_still_tracked({"closed": row["closed"], "endDate": row["end_date"]}, cfg.grace_days)
        row["tracked"] = 1 if still else 0
    logger.info("include list: %d ids, %d fetched by id, %d not found, %d tracked",
                len(include_ids), fetched, failed,
                sum(1 for i in include_ids if by_id.get(i, {}).get("tracked")))
    return list(by_id.values())
