"""Immutable configuration for the Betting on War data collector."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple

# The code lives in the paper folder (iCloud Drive), but the database must not:
# SQLite in WAL mode on a syncing volume can corrupt. Override with BOW_DATA_DIR.
DEFAULT_DATA_DIR = Path.home() / "Library" / "Application Support" / "BettingOnWar"

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Search phrases used to discover the tracked universe. Deliberately broad at the
# discovery stage; the tight escalation filter is applied in discover.classify().
SEARCH_QUERIES: Tuple[str, ...] = (
    "israel strike iran", "us strike iran", "iran nuclear", "us forces enter iran",
    "strait of hormuz", "iran blockade", "iran ceasefire", "iran retaliation",
    "russia ukraine ceasefire", "ukraine peace deal", "russia nato", "ukraine territory",
    "china taiwan invade", "taiwan blockade", "china military",
    "north korea missile", "north korea nuclear",
    "israel hezbollah", "israel hamas ceasefire", "israel lebanon", "israel syria",
    "venezuela military", "us venezuela",
    "military strike", "airstrike", "war between", "us military action",
    "missile launch", "invasion", "regime change", "coup", "assassination",
    "nuclear weapon test", "us troops", "peacekeepers", "no fly zone",
)

# A market must match one of these to enter the tight escalation panel.
# Word-boundary anchored: a bare "war" substring would otherwise pull in
# "Warnock", "Warren", "warning", and "reward".
ESCALATION_PATTERNS: Tuple[str, ...] = (
    r"\bwars?\b", r"\bwarfare\b", r"\bstrikes?\b", r"\bstriking\b",
    r"\bairstrikes?\b", r"\binvad\w*", r"\binvasion\w*", r"\bmilitary\b",
    r"\btroops?\b", r"\bceasefire\w*", r"\bcease-fire\w*", r"\bmissiles?\b",
    r"\bnuclear\b", r"\bbomb\w*", r"\bblockad\w*", r"\bforces enter\b",
    r"\battacks?\b", r"\boccup(?:y|ies|ation)\w*", r"\bannex\w*",
    r"\bno.fly zone\b", r"\bpeacekeep\w*", r"\bhostilit\w*",
    r"\bshoot(?:s|ing)? down\b", r"\bmilitary engagement\b",
)

# Labour actions, sport, and metaphorical "war" collide with the patterns above.
EXCLUDE_PATTERNS: Tuple[str, ...] = (
    # "strike" anywhere alongside any labour-dispute context word.
    r"(?=.*\bstrikes?\b)(?=.*\b(?:air ?canada|airlines?|pilots?|flight attendants?|"
    r"unions?|labou?r|workers?|employees?|teamsters|uaw|longshore|dockworkers?|"
    r"autoworkers?|hollywood|screen actors|samsung|starbucks|boeing|ups|amazon|"
    r"rail|transit|teachers?|nurses?|port|picket|wage|contract talks)\b)",
    r"\bstrike\s+(?:end|ends|ended|vote|authorization)\b",
    r"\b(?:mlb|nba|nfl|nhl|ncaa|ufc|boxing|esports|premier league)\b",
    # Esports titles are a live false-positive source: "Counter-Strike" carries
    # "strike", and team names supply the rest ("Nuclear TigeRES vs CYBERSHOKE").
    r"\b(?:counter[- ]?strike|cs ?2|cs:?go|dota|valorant|league of legends|"
    r"overwatch|rainbow six|call of duty|apex legends|starcraft|rocket league)\b",
    r"\bvs\.?\s+\w+.*\b(?:bo[135]|map \d|round \d)\b",
    r"\b(?:price|trade|tariff|bidding|culture|meme|format|streaming)\s+war\b",
    r"\bstar wars\b", r"\bnuclear (?:power plant|energy|reactor)\b",
    r"\bcall of duty\b", r"\bworld war (?:i|ii|1|2)\b(?!.*\b(?:begin|start)\b)",
)


@dataclass(frozen=True)
class Config:
    """Collector configuration. Immutable by design."""

    root: Path
    db_path: Path
    log_path: Path
    request_timeout: int = 60
    max_retries: int = 4
    retry_backoff: float = 2.0
    inter_request_sleep: float = 0.12
    # Keep collecting this many days past a market's end date so the closing tape
    # is captured before the CLOB history endpoint goes dark on resolution.
    grace_days: int = 7
    # data-api hard-caps paging at ~10k trades per market.
    trade_page_limit: int = 500
    trade_max_offset: int = 9500
    # Depth buckets (in cents from mid) summarised from each book snapshot.
    depth_buckets: Tuple[int, ...] = field(default=(1, 5, 10))

    @staticmethod
    def default(root: Path) -> "Config":
        data_dir = Path(os.environ.get("BOW_DATA_DIR", DEFAULT_DATA_DIR)).expanduser()
        data_dir.mkdir(parents=True, exist_ok=True)
        return Config(
            root=root,
            db_path=data_dir / "bow_market_data.sqlite",
            log_path=data_dir / "collector.log",
        )
