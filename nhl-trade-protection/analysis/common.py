"""Shared helpers for the NHL trade-protection analysis."""
import gzip, json, re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
FIG = ROOT / "analysis/figures"
OUT = ROOT / "analysis/output"


def load_players():
    """Yield player dicts from the CapWages cache (tolerates a truncated tail)."""
    f = gzip.open(DATA / "raw/capwages_players.jsonl.gz", "rt")
    try:
        for line in f:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
    except EOFError:
        return


def money(s):
    if s in (None, ""):
        return None
    s = re.sub(r"[^\d.]", "", str(s))
    return float(s) if s else None


DATE_FORMATS = ["%b. %d, %Y", "%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%b. %d %Y"]


def parse_date(s):
    if not s:
        return None
    s = s.strip().replace("Sept.", "Sep.").replace("June.", "Jun.").replace("July.", "Jul.")
    if "T" in s:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    s2 = s.replace(".", "")
    for fmt in ["%b %d, %Y", "%B %d, %Y"]:
        try:
            return datetime.strptime(s2, fmt).date()
        except ValueError:
            pass
    return None


def season_of(d):
    """NHL league year: July 1 .. June 30, labelled 'YYYY-YY'."""
    y = d.year if d.month >= 7 else d.year - 1
    return f"{y}-{str(y + 1)[-2:]}"


def season_start(season):
    return int(season[:4])


CLAUSE_CLASSES = {
    "NMC": "NMC", "M-NMC": "M-NMC", "NTC": "NTC", "M-NTC": "M-NTC",
}


def classify_clause(raw):
    """Collapse CapWages clause strings into the strongest protection held.

    Returns one of: none, M-NTC, NTC, M-NMC, NMC (ordered weakest -> strongest)."""
    if not raw:
        return "none"
    parts = {p.strip() for p in raw.split(",")}
    for c in ["NMC", "M-NMC", "NTC", "M-NTC"]:
        if c in parts:
            return c
    return "none"


LIST_RE = re.compile(r"(\d{1,2})[\s-]*team[s]?\s*(no[\s-]*trade|trade|no[\s-]*move)?\s*list", re.I)


def parse_list(details):
    """Return (list_type, n) from free-text clause details, or (None, None).

    list_type 'block' = teams the player may block (no-trade list);
    'approve' = teams the player would accept (trade list)."""
    if not details:
        return None, None
    m = LIST_RE.search(details)
    if not m:
        m2 = re.search(r"list of (\d{1,2}) teams he (would not|will not|would|will)", details, re.I)
        if not m2:
            return None, None
        return ("block" if "not" in m2.group(2).lower() else "approve"), int(m2.group(1))
    kind = (m.group(2) or "").lower().replace(" ", "").replace("-", "")
    if kind == "trade":
        return "approve", int(m.group(1))
    return "block", int(m.group(1))
