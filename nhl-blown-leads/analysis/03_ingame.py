"""In-game score states, 2005-06 onward: how safe is each lead?

Every exact score (e.g. 3-1) can be reached at most once per game, so each game contributes
at most one observation per lead state. For each state we record when it was first reached,
how it was reached (leader extended the lead vs. trailer cut it), and the outcome for the
leading team: won / lead later lost (game tied at some point) / lost the game.

Outputs: data/lead_states.csv, analysis/output/ingame_*.csv
"""
import gzip, json
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis/output"

def secs(t):
    m, s = t.split(":")
    return int(m) * 60 + int(s)

rows, bad = [], 0
lines = []
f = gzip.open(ROOT / "data/raw/goals_2005plus.jsonl.gz", "rt")
try:
    for _l in f:
        lines.append(_l)
except EOFError:          # tolerate a partially written cache
    pass
for line in lines:
    try:
        g = json.loads(line)
    except json.JSONDecodeError:
        continue
    if g["home_final"] is None or g["away_final"] is None:
        continue
    goals = [x for x in g["goals"] if x["ptype"] != "SO"]
    goals.sort(key=lambda x: (x["period"], secs(x["t"])))
    # consistency: running scores must step by one goal each
    h = a = 0; ok = True; tl = []
    for x in goals:
        if (x["home"], x["away"]) == (h + 1, a):
            h += 1; scorer = "H"
        elif (x["home"], x["away"]) == (h, a + 1):
            a += 1; scorer = "A"
        else:
            ok = False; break
        el = (x["period"] - 1) * 1200 + secs(x["t"])
        tl.append((el, h, a, scorer, x["period"], x.get("mod"), x.get("strength")))
    if not ok:
        bad += 1; continue
    hf, af = g["home_final"], g["away_final"]
    winner = "H" if hf > af else "A"
    reg_len = 3600
    seen = set()
    tied_after = []  # times the game was tied (after a goal)
    for el, h, a, sc, per, mod, stg in tl:
        if h == a:
            tied_after.append(el)
    for i, (el, h, a, sc, per, mod, stg) in enumerate(tl):
        if h == a or (h, a) in seen:
            continue
        seen.add((h, a))
        lead_side = "H" if h > a else "A"
        L, T = max(h, a), min(h, a)
        if el >= reg_len:          # OT goals end the game (or are OT in playoffs) -> skip
            continue
        lost_lead = any(t > el for t in tied_after)
        rows.append(dict(game_id=g["game_id"], season=g["season"], game_type=g["game_type"],
                         state=f"{L}-{T}", L=L, T=T, diff=L - T, elapsed=el, remaining=reg_len - el,
                         period=per, leader_home=int(lead_side == "H"),
                         how="extended" if sc == lead_side else "cut",
                         leader=g["home"] if lead_side == "H" else g["away"],
                         trailer=g["away"] if lead_side == "H" else g["home"],
                         win=int(winner == lead_side), lost_lead=int(lost_lead),
                         lost_game=int(winner != lead_side),
                         went_ot=int((g["last_ptype"] or "REG") != "REG")))
ls = pd.DataFrame(rows)
print(f"games parsed: {ls.game_id.nunique()}  inconsistent (dropped): {bad}  state-observations: {len(ls)}")

# team strength: season points % from final scores (regular season)
games = pd.read_csv(ROOT / "data/raw/games_all.csv")
# map team abbrev -> not available in games_all (ids); use goal file's abbrevs instead
recs = []
for line in lines:
    try:
        g = json.loads(line)
    except json.JSONDecodeError:
        continue
    if g["game_type"] != 2 or g["home_final"] is None:
        continue
    ot = (g["last_ptype"] or "REG") != "REG"
    for team, gf, ga in [(g["home"], g["home_final"], g["away_final"]), (g["away"], g["away_final"], g["home_final"])]:
        recs.append(dict(season=g["season"], team=team, pts=2 if gf > ga else (1 if ot else 0)))
pp = pd.DataFrame(recs).groupby(["season", "team"]).pts.mean().div(2).rename("ptspct").reset_index()
ls = ls.merge(pp.rename(columns={"team": "leader", "ptspct": "p_leader"}), on=["season", "leader"], how="left")
ls = ls.merge(pp.rename(columns={"team": "trailer", "ptspct": "p_trailer"}), on=["season", "trailer"], how="left")
ls["strength_diff"] = ls.p_leader - ls.p_trailer
ls.to_csv(ROOT / "data/lead_states.csv", index=False)

# ======================================================== summaries
STATES = ["1-0", "2-1", "3-2", "4-3", "2-0", "3-1", "4-2", "5-3", "3-0", "4-1", "5-2", "4-0", "5-1"]
reg = ls[ls.game_type == 2]
def table(d, by=None):
    keys = ["state"] + ([by] if by else [])
    t = (d.groupby(keys).agg(n=("win", "size"), win=("win", "mean"), lost_lead=("lost_lead", "mean"),
                              lost_game=("lost_game", "mean"), mean_remaining_min=("remaining", lambda x: x.mean() / 60))
           .reset_index())
    t["diff"] = t.state.str.split("-").map(lambda x: int(x[0]) - int(x[1]))
    return t[t.state.isin(STATES)]
t_all = table(reg).set_index("state").reindex(STATES).reset_index()
t_all.to_csv(OUT / "ingame_states_regular.csv", index=False)
print("\nREGULAR SEASON, all times\n", t_all.round(3).to_string(index=False))
t_per = table(reg, "period")
t_per.to_csv(OUT / "ingame_states_by_period.csv", index=False)

# ======================================================== models: holding lead size and time fixed
reg = reg.copy()
reg["rem_min"] = reg.remaining / 60
reg["total"] = reg["L"] + reg["T"]
reg["state_c"] = pd.Categorical(reg.state)
# expected outcome from lead size and time only (plus home, strength) -> residual by state
base = smf.logit("lost_game ~ C(diff, Treatment(1)) * (rem_min + I(rem_min**2)) + leader_home + strength_diff",
                 data=reg[reg["diff"] <= 4].dropna()).fit(disp=0)
reg4 = reg[reg["diff"] <= 4].dropna().copy()
reg4["p_lost_hat"] = base.predict(reg4)
base_ll = smf.logit("lost_lead ~ C(diff, Treatment(1)) * (rem_min + I(rem_min**2)) + leader_home + strength_diff",
                    data=reg4).fit(disp=0)
reg4["p_lostlead_hat"] = base_ll.predict(reg4)
res = (reg4.groupby("state").agg(n=("win", "size"), lost_game=("lost_game", "mean"), exp_lost_game=("p_lost_hat", "mean"),
                                 lost_lead=("lost_lead", "mean"), exp_lost_lead=("p_lostlead_hat", "mean"))
            .reset_index())
res["excess_lost_game"] = res.lost_game - res.exp_lost_game
res["ratio_lost_game"] = res.lost_game / res.exp_lost_game
res["excess_lost_lead"] = res.lost_lead - res.exp_lost_lead
res["ratio_lost_lead"] = res.lost_lead / res.exp_lost_lead
res = res[res.state.isin(STATES) & (res.n >= 200)].sort_values("ratio_lost_game", ascending=False)
res.to_csv(OUT / "ingame_danger_ranking.csv", index=False)
print("\nDANGER RANKING (observed vs expected given lead size, time left, home, strength)\n", res.round(3).to_string(index=False))

# does the absolute score matter at a fixed lead? (2-goal leads: 2-0 vs 3-1 vs 4-2)
for d_, states in [(1, ["1-0", "2-1", "3-2", "4-3"]), (2, ["2-0", "3-1", "4-2", "5-3"])]:
    sub = reg[reg.state.isin(states)].dropna().copy()
    m = smf.logit(f"lost_game ~ C(state, Treatment('{states[0]}')) + rem_min + I(rem_min**2) + leader_home + strength_diff",
                  data=sub).fit(disp=0, cov_type="cluster", cov_kwds={"groups": sub.game_id})
    print(f"\n{d_}-goal leads, logit P(leader loses), ref {states[0]}:")
    for k in m.params.index:
        if k.startswith("C(state"):
            print(f"  {k[-5:-1]}: {m.params[k]:+.3f} (se {m.bse[k]:.3f}, p={m.pvalues[k]:.3f})  odds x{np.exp(m.params[k]):.2f}")
    m.summary2().tables[1].to_csv(OUT / f"ingame_logit_{d_}goal.csv")

# how the state was reached: momentum
mom = (reg[reg.state.isin(["2-1", "3-1", "3-2", "4-2", "4-1"])].groupby(["state", "how"])
       .agg(n=("win", "size"), lost_game=("lost_game", "mean"), lost_lead=("lost_lead", "mean"),
            rem=("remaining", lambda x: x.mean() / 60)).reset_index())
mom.to_csv(OUT / "ingame_momentum.csv", index=False)
print("\nHOW REACHED\n", mom.round(3).to_string(index=False))
sub = reg[reg.state.isin(["3-1", "2-1", "3-2", "4-2", "4-1"])].dropna().copy()
mm = smf.logit("lost_game ~ C(how) + C(state) + rem_min + I(rem_min**2) + leader_home + strength_diff",
               data=sub).fit(disp=0, cov_type="cluster", cov_kwds={"groups": sub.game_id})
print("momentum (cut vs extended), logit:", round(mm.params["C(how)[T.extended]"], 3), "se", round(mm.bse["C(how)[T.extended]"], 3))

# playoffs separately
po = ls[ls.game_type == 3]
t_po = table(po).set_index("state").reindex(STATES).reset_index()
t_po.to_csv(OUT / "ingame_states_playoffs.csv", index=False)
print("\nPLAYOFFS\n", t_po.round(3).to_string(index=False))

# third-period view: state at the start of the 3rd period is the classic framing
# score entering the 3rd period (regular season): the classic framing
ent = []
for line in lines:
    try:
        g = json.loads(line)
    except json.JSONDecodeError:
        continue
    if g["game_type"] != 2 or g["home_final"] is None:
        continue
    h = sum(1 for x in g["goals"] if x["period"] <= 2 and x["team"] == g["home"])
    a = sum(1 for x in g["goals"] if x["period"] <= 2 and x["team"] == g["away"])
    if h == a:
        continue
    lead_home = h > a
    won = (g["home_final"] > g["away_final"]) == lead_home
    later = [x for x in g["goals"] if x["period"] >= 3 and x["ptype"] != "SO"]
    ent.append(dict(state=f"{max(h, a)}-{min(h, a)}", diff=abs(h - a), win=int(won),
                    reached_ot=int((g["last_ptype"] or "REG") != "REG")))
ent = pd.DataFrame(ent)
e = (ent.groupby("state").agg(n=("win", "size"), win=("win", "mean"), ot=("reached_ot", "mean"), diff=("diff", "first"))
        .reset_index())
e = e[e.state.isin(STATES)].sort_values(["diff", "state"])
e.to_csv(OUT / "ingame_entering_third.csv", index=False)
print("\nENTERING 3RD PERIOD (regular season)\n", e.round(3).to_string(index=False))
