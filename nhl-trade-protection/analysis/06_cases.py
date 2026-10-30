"""Build the protected-player case dataset and the return-value measures.

Return measures (both crude, see METHODS_NOTES.md):
  pick_value   sum of incoming picks in mid-first-round equivalents (pick #16 = 1),
               from data/pick_value_chart.csv
  realized_gp  NHL games played by incoming players in the three seasons after the
               trade (only for trades completed by 2023-24, so the window is complete)
Output: data/protected_player_cases.csv
"""
from datetime import date
import pandas as pd
from common import DATA, load_players, season_of
from cases_input import CASES

chart = pd.read_csv(DATA / "pick_value_chart.csv").set_index("pick").value_mid1st_equiv
cs = pd.read_csv(DATA / "contract_seasons.csv")
stats = {}
for p in load_players():
    stats[p["slug"]] = {}
    for s in p.get("stats") or []:
        if s.get("season"):
            stats[p["slug"]][s["season"]] = stats[p["slug"]].get(s["season"], 0) + (int(s["gp"]) if str(s.get("gp")).isdigit() else 0)

SLUG_OF = {"Mats Sundin": "mats-sundin", "Dany Heatley": "dany-heatley", "Rick Nash": "rick-nash"}

def find_slug(name):
    base = name.lower().replace(".", "").replace(" ", "-")
    return base if base in stats else None

TODAY = date(2026, 10, 30)
rows = []
for c in CASES:
    r = dict(c)
    slug = find_slug(c["player"])
    r["capwages_slug"] = slug or ""
    # AAV from CapWages for the season of the request/trade where available
    hit = cs[(cs.slug == slug) & (cs.season == c["season"])]
    r["cap_hit_capwages_musd"] = round(hit.cap_hit.iloc[0] / 1e6, 3) if len(hit) else None
    r["cap_pct_capwages"] = round(hit.cap_pct.iloc[0], 4) if len(hit) else None
    start = c["request_date"] or c["public_date"]
    r["days_measured_from"] = "request" if c["request_date"] else ("public" if c["public_date"] else "")
    demand = c["category"] in ("protected_demand", "protected_ambiguous", "protected_disputed", "no_clause_demand")
    if not demand:
        r["days_to_trade"], r["censored"] = None, None
    elif start and c["trade_date"]:
        r["days_to_trade"] = (date.fromisoformat(c["trade_date"]) - date.fromisoformat(start)).days
        r["censored"] = 0
    elif start and c["destination"].startswith("UNRESOLVED"):
        r["days_to_trade"] = (TODAY - date.fromisoformat(start)).days
        r["censored"] = 1
    else:
        r["days_to_trade"], r["censored"] = None, None
    picks = [p for p in c["picks_in"].split(";") if p]
    r["n_first_round_picks"] = sum(p.startswith("1:") for p in picks)
    r["pick_value"] = round(sum(chart.get(int(p.split(":")[1]), 0) for p in picks), 3) if c["trade_date"] else None
    players = [p for p in c["players_in"].split(";") if p]
    r["n_players_in"] = len(players)
    missing = [p for p in players if p not in stats]
    r["players_not_found"] = ";".join(missing)
    if c["trade_date"] and c["trade_date"] <= "2024-06-30":
        s0 = int(season_of(date.fromisoformat(c["trade_date"]))[:4])
        window = [f"{y}-{str(y + 1)[-2:]}" for y in range(s0, s0 + 3)]
        r["realized_gp_3yr"] = sum(stats.get(p, {}).get(w, 0) for p in players for w in window)
    else:
        r["realized_gp_3yr"] = None
    m = str(c["approved_m"]).split("->")[0].strip()
    r["approved_m_initial"] = int(m) if m.isdigit() else None
    rows.append(r)

out = pd.DataFrame(rows)
out.to_csv(DATA / "protected_player_cases.csv", index=False)
print(out[["player", "category", "approved_m_initial", "days_to_trade", "censored", "n_first_round_picks",
           "pick_value", "n_players_in", "realized_gp_3yr", "cap_hit_capwages_musd", "players_not_found"]].to_string())
