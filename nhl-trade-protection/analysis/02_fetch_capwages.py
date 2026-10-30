"""Fetch contract and transaction histories for every player in the CapWages sitemap.

CapWages (capwages.com) carries per-season contract details including the
clause flag (NTC, M-NTC, NMC, M-NMC, ...) and a transaction log with trade IDs.
Only the fields used downstream are kept.
Output: data/raw/capwages_players.jsonl.gz (one JSON object per player)
Re-runnable: players already in the output are skipped.
"""
import gzip, json, re, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLUGS = [l.strip().rsplit("/", 1)[-1] for l in open(ROOT / "data/raw/capwages_player_slugs.txt") if l.strip()]
OUT = ROOT / "data/raw/capwages_players.jsonl.gz"
KEEP = ["name", "slug", "nhlId", "pos", "born", "nationality", "draft_year", "drafted_overall",
        "careerGamesPlayed", "contracts", "transactions", "stats"]
STAT_KEYS = ["season", "team", "league", "gp", "g", "a", "p"]

done = set()
if OUT.exists():
    with gzip.open(OUT, "rt") as f:
        done = {json.loads(l)["slug"] for l in f}

def fetch(slug):
    url = f"https://capwages.com/players/{slug}"
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (academic research)"})
            with urllib.request.urlopen(req, timeout=40) as r:
                h = r.read().decode("utf-8", "replace")
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', h, re.S)
            p = json.loads(m.group(1))["props"]["pageProps"].get("player")
            if not p:
                return None
            p = {k: p.get(k) for k in KEEP}
            p["slug"] = slug
            p["stats"] = [{k: s.get(k) for k in STAT_KEYS} for s in p["stats"] or []
                          if s.get("league") == "NHL"]
            for c in p["contracts"] or []:   # drop bulky free text not used downstream
                c.pop("description", None)
            return p
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(3 * (attempt + 1))
        except Exception:
            time.sleep(3 * (attempt + 1))
    print("FAILED", slug, file=sys.stderr)
    return None

todo = [s for s in SLUGS if s not in done]
print(f"{len(done)} cached, {len(todo)} to fetch", file=sys.stderr)
with gzip.open(OUT, "at") as f, ThreadPoolExecutor(8) as ex:
    for i, p in enumerate(ex.map(fetch, todo)):
        if p:
            f.write(json.dumps(p) + "\n")
        if i % 500 == 0:
            print(i, file=sys.stderr); f.flush()
