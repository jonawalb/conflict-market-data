"""SQLite storage layer.

Every write is idempotent: re-running the collector over an overlapping window
inserts nothing new. This lets the job be scheduled aggressively without
duplicating observations.
"""

import hashlib
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS markets (
    market_id     TEXT PRIMARY KEY,
    condition_id  TEXT,
    slug          TEXT,
    question      TEXT,
    event_title   TEXT,
    category      TEXT,
    start_date    TEXT,
    end_date      TEXT,
    created_at    TEXT,
    closed        INTEGER,
    active        INTEGER,
    token_yes     TEXT,
    token_no      TEXT,
    volume_num    REAL,
    liquidity_num REAL,
    escalation    INTEGER,   -- 1 if it passes the tight military-escalation filter
    tracked       INTEGER,   -- 1 while the collector should keep polling it
    first_seen    TEXT,
    last_seen     TEXT
);
CREATE INDEX IF NOT EXISTS idx_markets_tracked ON markets(tracked, escalation);

CREATE TABLE IF NOT EXISTS prices (
    token_id  TEXT NOT NULL,
    market_id TEXT,
    ts        INTEGER NOT NULL,
    price     REAL,
    fidelity  INTEGER NOT NULL,
    PRIMARY KEY (token_id, ts, fidelity)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_prices_market ON prices(market_id, ts);

CREATE TABLE IF NOT EXISTS trades (
    trade_key    TEXT PRIMARY KEY,
    condition_id TEXT,
    market_id    TEXT,
    ts           INTEGER,
    price        REAL,
    size         REAL,
    side         TEXT,
    outcome      TEXT,
    wallet       TEXT,
    tx_hash      TEXT
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_trades_market_ts ON trades(market_id, ts);
CREATE INDEX IF NOT EXISTS idx_trades_wallet ON trades(wallet, ts);

CREATE TABLE IF NOT EXISTS books (
    token_id       TEXT NOT NULL,
    snap_ts        INTEGER NOT NULL,
    market_id      TEXT,
    book_ts        INTEGER,
    best_bid       REAL,
    best_ask       REAL,
    mid            REAL,
    spread         REAL,
    bid_size_total REAL,
    ask_size_total REAL,
    bid_depth_1c   REAL,
    ask_depth_1c   REAL,
    bid_depth_5c   REAL,
    ask_depth_5c   REAL,
    bid_depth_10c  REAL,
    ask_depth_10c  REAL,
    n_bid_levels   INTEGER,
    n_ask_levels   INTEGER,
    raw_json       TEXT,
    PRIMARY KEY (token_id, snap_ts)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_books_market ON books(market_id, snap_ts);

CREATE TABLE IF NOT EXISTS runs (
    run_id     TEXT PRIMARY KEY,
    started    TEXT,
    finished   TEXT,
    mode       TEXT,
    n_markets  INTEGER,
    n_prices   INTEGER,
    n_trades   INTEGER,
    n_books    INTEGER,
    api_calls  INTEGER,
    errors     INTEGER,
    note       TEXT
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the database, creating the schema if needed."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def trade_key(trade: Dict[str, Any]) -> str:
    """Stable identity for a trade row.

    A transaction hash covers both sides of a fill, so the hash alone is not
    unique; the full economic tuple is.
    """
    payload = "|".join(
        str(trade.get(field, ""))
        for field in ("transactionHash", "proxyWallet", "side", "outcome",
                      "timestamp", "price", "size")
    )
    return hashlib.sha1(payload.encode()).hexdigest()


def upsert_markets(conn: sqlite3.Connection, rows: Iterable[Dict[str, Any]]) -> int:
    sql = """
    INSERT INTO markets (market_id, condition_id, slug, question, event_title,
        category, start_date, end_date, created_at, closed, active, token_yes,
        token_no, volume_num, liquidity_num, escalation, tracked, first_seen, last_seen)
    VALUES (:market_id, :condition_id, :slug, :question, :event_title, :category,
        :start_date, :end_date, :created_at, :closed, :active, :token_yes,
        :token_no, :volume_num, :liquidity_num, :escalation, :tracked,
        :last_seen, :last_seen)
    ON CONFLICT(market_id) DO UPDATE SET
        closed=excluded.closed, active=excluded.active,
        volume_num=excluded.volume_num, liquidity_num=excluded.liquidity_num,
        escalation=excluded.escalation, tracked=excluded.tracked,
        end_date=excluded.end_date, last_seen=excluded.last_seen,
        token_yes=COALESCE(excluded.token_yes, markets.token_yes),
        token_no=COALESCE(excluded.token_no, markets.token_no)
    """
    cursor = conn.executemany(sql, list(rows))
    conn.commit()
    return cursor.rowcount


def insert_prices(
    conn: sqlite3.Connection, token_id: str, market_id: str,
    history: List[Dict[str, Any]], fidelity: int,
) -> int:
    if not history:
        return 0
    rows = [
        (token_id, market_id, int(point["t"]), float(point["p"]), fidelity)
        for point in history
        if point.get("t") is not None and point.get("p") is not None
    ]
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO prices (token_id, market_id, ts, price, fidelity)"
        " VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return conn.total_changes - before


def insert_trades(
    conn: sqlite3.Connection, condition_id: str, market_id: str,
    trades: List[Dict[str, Any]],
) -> int:
    if not trades:
        return 0
    rows = []
    for trade in trades:
        try:
            rows.append((
                trade_key(trade), condition_id, market_id,
                int(trade["timestamp"]), float(trade["price"]), float(trade["size"]),
                trade.get("side"), trade.get("outcome"),
                trade.get("proxyWallet"), trade.get("transactionHash"),
            ))
        except (KeyError, TypeError, ValueError):
            logger.debug("skipping malformed trade: %s", trade)
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO trades (trade_key, condition_id, market_id, ts,"
        " price, size, side, outcome, wallet, tx_hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return conn.total_changes - before


def insert_book(conn: sqlite3.Connection, row: Dict[str, Any]) -> int:
    before = conn.total_changes
    conn.execute(
        """INSERT OR IGNORE INTO books (token_id, snap_ts, market_id, book_ts,
        best_bid, best_ask, mid, spread, bid_size_total, ask_size_total,
        bid_depth_1c, ask_depth_1c, bid_depth_5c, ask_depth_5c,
        bid_depth_10c, ask_depth_10c, n_bid_levels, n_ask_levels, raw_json)
        VALUES (:token_id,:snap_ts,:market_id,:book_ts,:best_bid,:best_ask,:mid,
        :spread,:bid_size_total,:ask_size_total,:bid_depth_1c,:ask_depth_1c,
        :bid_depth_5c,:ask_depth_5c,:bid_depth_10c,:ask_depth_10c,
        :n_bid_levels,:n_ask_levels,:raw_json)""",
        row,
    )
    conn.commit()
    return conn.total_changes - before


def tracked_markets(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM markets WHERE tracked=1 ORDER BY volume_num DESC"
    ).fetchall()


def record_run(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO runs (run_id, started, finished, mode, n_markets,
        n_prices, n_trades, n_books, api_calls, errors, note)
        VALUES (:run_id,:started,:finished,:mode,:n_markets,:n_prices,:n_trades,
        :n_books,:api_calls,:errors,:note)""",
        row,
    )
    conn.commit()


def summary(conn: sqlite3.Connection) -> Dict[str, Any]:
    def scalar(sql: str) -> Any:
        return conn.execute(sql).fetchone()[0]

    return {
        "markets": scalar("SELECT COUNT(*) FROM markets"),
        "tracked": scalar("SELECT COUNT(*) FROM markets WHERE tracked=1"),
        "escalation": scalar("SELECT COUNT(*) FROM markets WHERE escalation=1"),
        "price_points": scalar("SELECT COUNT(*) FROM prices"),
        "trades": scalar("SELECT COUNT(*) FROM trades"),
        "wallets": scalar("SELECT COUNT(DISTINCT wallet) FROM trades"),
        "book_snapshots": scalar("SELECT COUNT(*) FROM books"),
        "runs": scalar("SELECT COUNT(*) FROM runs"),
    }
