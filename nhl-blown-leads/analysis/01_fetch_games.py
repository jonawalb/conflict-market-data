"""Fetch NHL game lists (all history, regular season + playoffs) and goal-by-goal
scoring for games from 2005-06 on.

Outputs:
  data/raw/games_all.csv          every NHL game since 1917-18 (id, date, teams, final score, last period)
  data/raw/goals_2005plus.jsonl.gz one record per game: final state + ordered goals
Sources: api.nhle.com/stats/rest/en/game ; api-web.nhle.com/v1/gamecenter/{id}/landing
"""
import csv, gzip, json, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UA = {"User-Agent": "Mozilla/5.0 (academic research)"}

def get(url, tries=4):
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                return json.load(r)
        except Exception:
            time.sleep(2 * (a + 1))
    return None

# ---- 1. game list, all seasons
out = ROOT / "data/raw/games_all.csv"
if not out.exists():
    rows = []
    for y in range(1917, 2027):
        s = f"{y}{y + 1}"
        d = get(f"https://api.nhle.com/stats/rest/en/game?cayenneExp=season={s}%20and%20gameType%3E=2%20and%20gameType%3C=3")
        for g in (d or {}).get("data", []):
            rows.append([g["id"], g["season"], g["gameType"], g["gameDate"], g["homeTeamId"], g["visitingTeamId"],
                         g.get("homeScore"), g.get("visitingScore"), g.get("period"), g.get("gameStateId")])
        print(s, len(rows), file=sys.stderr)
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["game_id", "season", "game_type", "date", "home_id", "away_id", "home_score", "away_score",
                    "last_period", "state"])
        w.writerows(rows)

# ---- 2. goal-by-goal for 2005-06 on (finished games only)
games = [r for r in csv.DictReader(open(out)) if int(r["season"]) >= 20052006 and r["state"] == "7"]
gout = ROOT / "data/raw/goals_2005plus.jsonl.gz"
done = set()
if gout.exists():
    try:
        with gzip.open(gout, "rt") as f:
            for l in f:
                done.add(json.loads(l)["game_id"])
    except (EOFError, json.JSONDecodeError):
        pass

def fetch(g):
    d = get(f"https://api-web.nhle.com/v1/gamecenter/{g['game_id']}/landing")
    if not d:
        return None
    goals = []
    for p in d.get("summary", {}).get("scoring", []):
        pd_ = p["periodDescriptor"]
        for x in p["goals"]:
            goals.append(dict(period=pd_["number"], ptype=pd_.get("periodType"), t=x["timeInPeriod"],
                              team=x["teamAbbrev"]["default"], home=x["homeScore"], away=x["awayScore"],
                              strength=x.get("strength"), mod=x.get("goalModifier"), sit=x.get("situationCode")))
    return dict(game_id=int(g["game_id"]), season=int(g["season"]), game_type=int(g["game_type"]), date=g["date"],
                home=d["homeTeam"]["abbrev"], away=d["awayTeam"]["abbrev"],
                home_final=d["homeTeam"].get("score"), away_final=d["awayTeam"].get("score"),
                last_ptype=(d.get("periodDescriptor") or {}).get("periodType"),
                last_period=(d.get("periodDescriptor") or {}).get("number"), goals=goals)

todo = [g for g in games if int(g["game_id"]) not in done]
print(len(done), "cached;", len(todo), "to fetch", file=sys.stderr)
with gzip.open(gout, "at") as f, ThreadPoolExecutor(8) as ex:
    for i, r in enumerate(ex.map(fetch, todo)):
        if r:
            f.write(json.dumps(r) + "\n")
        if i % 1000 == 0:
            print(i, file=sys.stderr); f.flush()
