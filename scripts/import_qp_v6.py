#!/usr/bin/env python3
"""Load the QP-lambda v6 backfill (2026-10-06/07) into a local collector database.

The v6 build pulled complete tapes for three theaters with this repository's
backfill workflows and kept them outside it, in the paper's replication folder
(QP-lambda/v6/data). This copies all of it into the collector schema so the
local database holds the live collection and the backfill in one place. Nothing
is committed: the tapes are ~1.3 GB, and the importer reads them in place, never
writing to the source folder.

  markets        Gamma metadata from each tape set's meta.jsonl and the frame
                 sweeps (open contracts included). Existing rows keep every value
                 they have; only NULL fields are filled.
  trades         data-api taker records (trades/<id>.csv.gz without a `source`
                 column, trades/<id>.api.csv.gz, tapes/tapes_api/). Their
                 trade_key is bow.db.trade_key, so they de-duplicate against the
                 live collector's rows.
  chain_trades   Polygon fills: the raw scans under tapes_full/chain_raw/ and the
                 merged tapes (trades/<id>.csv.gz with a `source` column). Keyed by
                 tx_hash:log_index. Kept out of `trades`; see bow/db.py.
  prices         CLOB prices-history per outcome token (fidelity 60 or 720).
  market_frames  every row of the v6 frames, contract lists, frame sweeps and the
                 RA spreadsheets (source = qp_v6 | qp_v6_sweep | RA), each with its
                 own resolution label and the full row as JSON.
  tape_imports   one row per tape/price file: sha256, row and insert counts. A file
                 whose path and hash are already recorded is skipped, so re-runs
                 are cheap and insert nothing.

    python3 scripts/import_qp_v6.py import --qp ".../QP-lambda/v6/data" --db PATH
    python3 scripts/import_qp_v6.py include --qp ".../QP-lambda/v6/data" \\
        > data/registry/include.csv

`include` writes the still-open contracts of the v6 frames, which the registry
refresh then tracks (see bow.discover.apply_includes).
"""

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import logging
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from bow import db  # noqa: E402
from bow.config import DEFAULT_DATA_DIR  # noqa: E402
from bow.discover import classify  # noqa: E402

logger = logging.getLogger("import_qp_v6")

# tape directory -> theater. tapes/ is the first RU data-api pull (2026-10-06 morning).
TAPE_SETS = {"tapes_full": "russia_ukraine", "tapes_iran": "iran_israel",
             "tapes_gaza": "israel_gaza", "tapes": "russia_ukraine"}
FRAMES = [  # (file, theater, tape set whose meta.jsonl frame_rows refer to it)
    ("ru_frame_v6.csv", "russia_ukraine", "tapes_full"),
    ("ru_frame_v6_1.csv", "russia_ukraine", "tapes_full"),
    ("iran_frame_v6.csv", "iran_israel", "tapes_iran"),
    ("iran_frame_v6_1.csv", "iran_israel", "tapes_iran"),
    ("gaza_frame_v6.csv", "israel_gaza", "tapes_gaza"),
    ("gaza_frame_v6_1.csv", "israel_gaza", "tapes_gaza"),
    ("iran_contracts.csv", "iran_israel", "tapes_iran"),
    ("gaza_contracts.csv", "israel_gaza", "tapes_gaza"),
]
SWEEPS = {"sweep_iran": "iran_israel", "sweep_gaza": "israel_gaza"}
# RA files: (file, sheet, theater, header row, tape set + frame_rows prefix for row matching)
RA_SHEETS = [
    ("2022-2026 RUS-UA Dataset (RAW).xlsx", "Data", "russia_ukraine", 2, ("tapes_full", "")),
    ("20250929-20260929_polymarket_ukraine_war_dataset.xlsx", "Data", "russia_ukraine", 2, None),
    ("Polymarket TSM.xlsx", "Sheet1", "china_taiwan", 1, ("tapes_full", "CN")),
]
RA_CSV = [("RUS-UA Screenshot Log.csv", "russia_ukraine")]
BATCH = 50000


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def num(value: Any) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def read_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


# ---------------------------------------------------------------- markets

def load_meta(qp: Path) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """tape set -> market_id -> meta record."""
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for name in TAPE_SETS:
        path = qp / name / "meta.jsonl"
        if path.exists():
            out[name] = {r["market_id"]: r for r in read_jsonl(path)}
    return out


def load_sweeps(qp: Path) -> Dict[str, List[Tuple[str, Dict[str, Any]]]]:
    """sweep -> [(kept|excluded, record)]."""
    out: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    for name in SWEEPS:
        rows = []
        for status in ("kept", "excluded"):
            for path in sorted((qp / name).glob(f"*/{status}_*.jsonl.gz")):
                rows.extend((status, r) for r in read_jsonl(path))
        out[name] = rows
    return out


def market_row(market_id: str, question: str, event_title: Optional[str], slug: Optional[str],
               condition_id: Optional[str], tokens: List[str], closed: Any, start: Any, end: Any,
               volume: Any) -> Dict[str, Any]:
    closed = bool(closed)
    return {"market_id": market_id, "condition_id": condition_id, "slug": slug,
            "question": question, "event_title": event_title, "category": None,
            "start_date": start, "end_date": end, "created_at": None,
            "closed": int(closed), "active": int(not closed),
            "token_yes": tokens[0] if tokens else None,
            "token_no": tokens[1] if len(tokens) > 1 else None,
            "volume_num": num(volume) or 0.0, "liquidity_num": 0.0,
            "escalation": int(classify(question or "", event_title or "")), "tracked": 0,
            "first_seen": now()}


def import_markets(conn: sqlite3.Connection, meta: Dict, sweeps: Dict) -> Dict[str, int]:
    rows: Dict[str, Dict[str, Any]] = {}
    for recs in meta.values():
        for r in recs.values():
            rows[r["market_id"]] = market_row(
                r["market_id"], r.get("question"), None, r.get("market_slug"), r.get("condition_id"),
                list((r.get("tokens") or {}).values()), r.get("closed"), r.get("start"), r.get("end"),
                r.get("volume"))
    for recs in sweeps.values():
        for status, r in recs:
            if status != "kept":
                continue
            new = market_row(
                r["market_id"], r.get("question"), r.get("event_title"), r.get("slug"),
                r.get("condition_id"), json.loads(r.get("tokens") or "[]"), r.get("closed"),
                r.get("start"), r.get("end"), r.get("volume"))
            old = rows.get(r["market_id"])
            rows[r["market_id"]] = {**new, **{k: v for k, v in (old or {}).items() if v is not None}}
    before = conn.execute("SELECT COUNT(*) FROM markets").fetchone()[0]
    conn.executemany(
        """INSERT INTO markets (market_id, condition_id, slug, question, event_title, category,
            start_date, end_date, created_at, closed, active, token_yes, token_no, volume_num,
            liquidity_num, escalation, tracked, first_seen, last_seen)
        VALUES (:market_id, :condition_id, :slug, :question, :event_title, :category,
            :start_date, :end_date, :created_at, :closed, :active, :token_yes, :token_no,
            :volume_num, :liquidity_num, :escalation, :tracked, :first_seen, NULL)
        ON CONFLICT(market_id) DO UPDATE SET
            condition_id=COALESCE(markets.condition_id, excluded.condition_id),
            slug=COALESCE(markets.slug, excluded.slug),
            question=COALESCE(markets.question, excluded.question),
            event_title=COALESCE(markets.event_title, excluded.event_title),
            start_date=COALESCE(markets.start_date, excluded.start_date),
            end_date=COALESCE(markets.end_date, excluded.end_date),
            token_yes=COALESCE(markets.token_yes, excluded.token_yes),
            token_no=COALESCE(markets.token_no, excluded.token_no)""",
        list(rows.values()))
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM markets").fetchone()[0]
    return {"seen": len(rows), "inserted": after - before}


# ---------------------------------------------------------------- frames

class Matcher:
    """Resolve a frame or RA row to market ids: explicit id, slug, frame_rows, then the DB's slugs."""

    def __init__(self, meta: Dict, sweeps: Dict, conn: Optional[sqlite3.Connection] = None) -> None:
        self.conn = conn
        self.by_slug: Dict[str, str] = {}
        self.by_frame_row: Dict[Tuple[str, str], List[str]] = defaultdict(list)
        for name, recs in meta.items():
            for r in recs.values():
                if r.get("market_slug"):
                    self.by_slug.setdefault(r["market_slug"], r["market_id"])
                for fr in r.get("frame_rows") or []:
                    self.by_frame_row[(name, str(fr))].append(r["market_id"])
        for recs in sweeps.values():
            for _, r in recs:
                if r.get("slug"):
                    self.by_slug.setdefault(r["slug"], r["market_id"])

    def match(self, market_id: Any = None, slug: Any = None, url: Any = None,
              tape_set: Optional[str] = None, frame_row: Any = None) -> List[str]:
        if market_id not in (None, ""):
            return [str(market_id).split(".")[0]]
        if url and not slug:
            slug = str(url).rstrip("/").split("/")[-1].split("?")[0]
        if slug and slug in self.by_slug:
            return [self.by_slug[slug]]
        if tape_set and frame_row not in (None, ""):
            return sorted(set(self.by_frame_row.get((tape_set, str(frame_row)), [])))
        if slug and self.conn is not None:  # contracts outside the v6 tapes, seen by the live collector
            return [r[0] for r in self.conn.execute(
                "SELECT market_id FROM markets WHERE slug=? ORDER BY market_id", (slug,))]
        return []


def frame_rows_insert(conn: sqlite3.Connection, rows: List[Tuple]) -> int:
    before = conn.total_changes
    conn.executemany(
        "INSERT OR REPLACE INTO market_frames (frame, frame_row, source, theater, market_id, slug,"
        " question, resolution, data) VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return conn.total_changes - before


def frame_tuples(frame: str, frame_row: str, source: str, theater: str, ids: List[str],
                 slug: Any, question: Any, resolution: Any, data: Dict) -> List[Tuple]:
    payload = json.dumps(data, default=str, ensure_ascii=False, sort_keys=True)
    return [(frame, str(frame_row), source, theater, mid, slug or None, question or None,
             resolution or None, payload) for mid in (ids or [""])]


def import_frames(conn: sqlite3.Connection, qp: Path, ra: Path, matcher: Matcher,
                  sweeps: Dict) -> Dict[str, Dict[str, int]]:
    report: Dict[str, Dict[str, int]] = {}

    def done(frame: str, rows: List[Tuple]) -> None:
        frame_rows_insert(conn, rows)
        matched = {(r[1]) for r in rows if r[4]}
        report[frame] = {"rows": len({r[1] for r in rows}), "matched_rows": len(matched),
                         "market_links": sum(1 for r in rows if r[4])}

    for name, theater, tape_set in FRAMES:
        path = qp / name
        if not path.exists():
            logger.warning("frame missing: %s", path)
            continue
        rows: List[Tuple] = []
        with open(path, newline="") as fh:
            for r in csv.DictReader(fh):
                ids = matcher.match(r.get("market_id"), r.get("slug"), r.get("url"),
                                    tape_set, r.get("frame_row"))
                rows += frame_tuples(name, r["frame_row"], "qp_v6", theater, ids, r.get("slug"),
                                     r.get("question"), r.get("resolution"), r)
        done(name, rows)

    for name, recs in sweeps.items():
        rows = []
        for status, r in recs:
            data = {**r, "sweep_status": status}
            rows += frame_tuples(name, r["market_id"], "qp_v6_sweep", SWEEPS[name], [r["market_id"]],
                                 r.get("slug"), r.get("question"), r.get("label"), data)
        done(name, rows)

    try:
        import openpyxl
    except ImportError:
        logger.error("openpyxl not installed: RA spreadsheets skipped")
        openpyxl = None
    for fname, sheet, theater, header_row, rowmap in RA_SHEETS:
        path = ra / fname
        if openpyxl is None or not path.exists():
            logger.warning("RA file skipped: %s", path)
            continue
        ws = openpyxl.load_workbook(path, read_only=True, data_only=True)[sheet]
        header: List[str] = []
        rows = []
        for i, values in enumerate(ws.iter_rows(values_only=True), start=1):
            if i == header_row:
                header = [str(v).strip() if v is not None else f"col{j}" for j, v in enumerate(values)]
                continue
            if i < header_row or not header:
                continue
            rec = {h: v for h, v in zip(header, values) if v is not None}
            url = rec.get("Contract URL")
            if not url:
                continue
            ids = matcher.match(url=url)
            if not ids and rowmap:
                ids = matcher.match(tape_set=rowmap[0], frame_row=f"{rowmap[1]}{i}")
            rows += frame_tuples(f"{fname}:{sheet}", str(i), "RA", theater, ids, None,
                                 rec.get("Question Text"), rec.get("Resolution Outcome"), rec)
        done(f"{fname}:{sheet}", rows)

    for fname, theater in RA_CSV:
        path = ra / fname
        if not path.exists():
            logger.warning("RA file skipped: %s", path)
            continue
        rows = []
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                ids = matcher.match(url=r.get("Contract URL"))
                rows += frame_tuples(fname, r["Excel Row"], "RA", theater, ids, None,
                                     r.get("Question Text"), None, r)
        done(fname, rows)
    return report


# ---------------------------------------------------------------- tapes

def already(conn: sqlite3.Connection, key: str, digest: str) -> bool:
    row = conn.execute("SELECT sha256 FROM tape_imports WHERE path=?", (key,)).fetchone()
    return bool(row and row[0] == digest)


def log_file(conn: sqlite3.Connection, key: str, digest: str, kind: str, source: str,
             theater: str, market_id: Optional[str], n: int, ins: int) -> None:
    conn.execute("INSERT OR REPLACE INTO tape_imports VALUES (?,?,?,?,?,?,?,?,?)",
                 (key, digest, kind, source, theater, market_id, n, ins, now()))


def read_csv_gz(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    with gzip.open(path, "rt", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), list(reader)


def insert_many(conn: sqlite3.Connection, sql: str, rows: List[Tuple]) -> int:
    before = conn.total_changes
    for i in range(0, len(rows), BATCH):
        conn.executemany(sql, rows[i:i + BATCH])
    return conn.total_changes - before


TRADES_SQL = ("INSERT OR IGNORE INTO trades (trade_key, condition_id, market_id, ts, price, size,"
              " side, outcome, wallet, tx_hash) VALUES (?,?,?,?,?,?,?,?,?,?)")
CHAIN_SQL = ("INSERT OR IGNORE INTO chain_trades (trade_key, market_id, ts, price, size, side, outcome,"
             " wallet, counterparty, tx_hash, block, log_index, source, usdc, fee)"
             " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
PRICES_SQL = ("INSERT OR IGNORE INTO prices (token_id, market_id, ts, price, fidelity)"
              " VALUES (?,?,?,?,?)")


def api_rows(rows: List[Dict[str, str]], cond: Dict[str, str]) -> List[Tuple]:
    return [(r["trade_key"], cond.get(r["market_id"]), r["market_id"], int(r["ts"]), float(r["price"]),
             float(r["size"]), r.get("side") or None, r.get("outcome") or None,
             r.get("wallet") or None, r.get("tx_hash") or None) for r in rows]


def chain_rows(rows: List[Dict[str, str]]) -> List[Tuple]:
    out = []
    for r in rows:
        key = r.get("trade_key") or f"{r['tx_hash']}:{r['log_index']}"
        log_index = r.get("log_index") or key.rsplit(":", 1)[-1]
        out.append((key, r["market_id"], int(r["ts"]), float(r["price"]), float(r["size"]),
                    r.get("side") or None, r.get("outcome") or None, r.get("wallet") or None,
                    r.get("counterparty") or None, r.get("tx_hash") or None,
                    int(r["block"]) if r.get("block") else None, int(log_index),
                    r.get("source") or None, num(r.get("usdc")), num(r.get("fee"))))
    return out


def price_rows(rows: List[Dict[str, str]], tokens: Dict[Tuple[str, str], str]) -> List[Tuple]:
    """The first RU pull (tapes/prices) has no token column; its outcome maps to one via meta."""
    out = []
    for r in rows:
        token = r.get("token") or tokens.get((r["market_id"], r.get("outcome")))
        if token and r.get("t") and r.get("p") and r.get("fidelity"):
            out.append((token, r["market_id"], int(r["t"]), float(r["p"]), int(float(r["fidelity"]))))
    return out


def import_tapes(conn: sqlite3.Connection, qp: Path, meta: Dict, force: bool) -> Dict[str, Any]:
    cond = {mid: r.get("condition_id") for recs in meta.values() for mid, r in recs.items()}
    tokens = {(mid, outcome): tok for recs in meta.values() for mid, r in recs.items()
              for outcome, tok in (r.get("tokens") or {}).items()}
    totals: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    jobs: List[Tuple[Path, str, str]] = []  # (file, theater, kind hint)
    for name, theater in TAPE_SETS.items():
        base = qp / name
        # raw chain scans first, so their full columns (block, usdc, fee) win over the merged copies
        jobs += [(f, theater, "chain") for f in sorted((base / "chain_raw").rglob("*.csv.gz"))]
        jobs += [(f, theater, "trades") for f in sorted((base / "trades").glob("*.csv.gz"))]
        jobs += [(f, theater, "trades") for f in sorted((base / "tapes_api").glob("*.csv.gz"))]
        jobs += [(f, theater, "prices") for f in sorted((base / "prices").glob("*.csv.gz"))]
    logger.info("%d tape and price files", len(jobs))
    for i, (path, theater, hint) in enumerate(jobs, 1):
        key = str(path.relative_to(qp.parent.parent))
        digest = sha256(path)
        if not force and already(conn, key, digest):
            totals["skipped"]["files"] += 1
            continue
        cols, rows = read_csv_gz(path)
        if hint == "prices":
            kind, source, sql, tuples = "prices", "clob", PRICES_SQL, price_rows(rows, tokens)
        elif hint == "chain" or "source" in cols:
            kind, source, sql, tuples = "chain", "chain", CHAIN_SQL, chain_rows(rows)
        else:
            kind, source, sql, tuples = "trades", "data-api", TRADES_SQL, api_rows(rows, cond)
        ins = insert_many(conn, sql, tuples)
        mid = path.name.split(".")[0] if hint != "chain" else None
        log_file(conn, key, digest, kind, source, theater, mid, len(tuples), ins)
        conn.commit()
        totals[f"{theater}:{kind}"]["files"] += 1
        totals[f"{theater}:{kind}"]["rows"] += len(tuples)
        totals[f"{theater}:{kind}"]["inserted"] += ins
        if i % 500 == 0:
            logger.info("  [%d/%d] %s", i, len(jobs), {k: dict(v) for k, v in totals.items()})
    return {k: dict(v) for k, v in totals.items()}


# ---------------------------------------------------------------- include list

def open_contracts(qp: Path) -> List[Dict[str, str]]:
    meta, sweeps = load_meta(qp), load_sweeps(qp)
    out: Dict[str, Dict[str, str]] = {}
    for name in ("tapes_full", "tapes_iran", "tapes_gaza"):
        for r in meta.get(name, {}).values():
            if not r.get("closed"):
                theater = TAPE_SETS[name]
                if name == "tapes_full" and all(str(f).startswith("CN") for f in r.get("frame_rows") or ["x"]):
                    theater = "china_taiwan"
                out[r["market_id"]] = {"market_id": r["market_id"], "theater": theater,
                                       "source": f"qp_v6/{name}", "question": r.get("question") or ""}
    for name, recs in sweeps.items():
        for status, r in recs:
            if status == "kept" and not r.get("closed"):
                out.setdefault(r["market_id"], {"market_id": r["market_id"], "theater": SWEEPS[name],
                                                "source": f"qp_v6/{name}", "question": r.get("question") or ""})
    return sorted(out.values(), key=lambda r: int(r["market_id"]))


def cmd_include(args: argparse.Namespace) -> int:
    rows = open_contracts(Path(args.qp))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["market_id", "theater", "source", "question"], lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    sys.stdout.write(buf.getvalue())
    logger.info("%d open contracts", len(rows))
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    qp, ra = Path(args.qp), Path(args.ra) if args.ra else Path(args.qp) / "source"
    conn = db.connect(Path(args.db))
    meta, sweeps = load_meta(qp), load_sweeps(qp)
    report: Dict[str, Any] = {"started": now(), "db": args.db, "qp": str(qp), "ra": str(ra)}
    report["markets"] = import_markets(conn, meta, sweeps)
    logger.info("markets: %s", report["markets"])
    report["frames"] = import_frames(conn, qp, ra, Matcher(meta, sweeps, conn), sweeps)
    for frame, stats in report["frames"].items():
        logger.info("frame %-60s %s", frame, stats)
    report["tapes"] = import_tapes(conn, qp, meta, args.force)
    report["finished"] = now()
    logger.info("tapes: %s", json.dumps(report["tapes"], indent=1))
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=1))
    conn.close()
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("--qp", required=True, help="QP-lambda/v6/data")
    imp.add_argument("--ra", default=None, help="RA spreadsheet folder (default: <qp>/source)")
    imp.add_argument("--db", default=str(DEFAULT_DATA_DIR / "bow_market_data.sqlite"))
    imp.add_argument("--force", action="store_true", help="re-read files already imported")
    imp.add_argument("--report", default=None, help="write a JSON summary here")
    inc = sub.add_parser("include")
    inc.add_argument("--qp", required=True)
    args = ap.parse_args()
    return cmd_import(args) if args.cmd == "import" else cmd_include(args)


if __name__ == "__main__":
    sys.exit(main())
