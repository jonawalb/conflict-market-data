"""Fetch NHL season rosters (2005-06 .. 2026-27) from the public NHL API.

Builds the player universe used to look up contracts on CapWages.
Output: data/raw/nhl_rosters.csv (season, team, nhl_id, first, last, pos)
"""
import csv, json, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/raw/nhl_rosters.csv"
TEAMS = ["ANA","ARI","PHX","ATL","BOS","BUF","CGY","CAR","CHI","COL","CBJ","DAL","DET","EDM",
         "FLA","LAK","MIN","MTL","NSH","NJD","NYI","NYR","OTT","PHI","PIT","SJS","SEA","STL",
         "TBL","TOR","UTA","VAN","VGK","WSH","WPG"]
SEASONS = [f"{y}{y+1}" for y in range(2005, 2027)]

def get(team, season):
    url = f"https://api-web.nhle.com/v1/roster/{team}/{season}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research)"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return team, season, json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return team, season, None
        except Exception:
            time.sleep(2 ** attempt)
    return team, season, None

rows = []
with ThreadPoolExecutor(8) as ex:
    for team, season, d in ex.map(lambda a: get(*a), [(t, s) for t in TEAMS for s in SEASONS]):
        if not d:
            continue
        for grp in ("forwards", "defensemen", "goalies"):
            for p in d.get(grp, []):
                rows.append([f"{season[:4]}-{season[6:]}", team, p["id"], p["firstName"]["default"],
                             p["lastName"]["default"], p.get("positionCode", ""), p.get("birthDate", "")])
rows.sort()
with open(OUT, "w", newline="") as f:
    w = csv.writer(f); w.writerow(["season","team","nhl_id","first","last","pos","birth_date"]); w.writerows(rows)
print(len(rows), "roster rows;", len({r[2] for r in rows}), "unique players", file=sys.stderr)
