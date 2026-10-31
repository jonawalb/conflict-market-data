"""Playoff series states: how often does each series lead hold?

Reconstructs every best-of-seven NHL playoff series from game results (1939 onward),
decoded from game IDs (SSSS03RRSG: round RR, series S, game G). A series is kept if its
winner reached exactly four wins and no game was tied.

Baselines for "how dangerous is this lead":
  coinflip  each game 50/50
  model     per-game win prob from a logistic fit on regular-season points % (log5-style)
            and home ice, estimated on all playoff games; the probability that the leader
            closes out is computed by recursion over remaining games with the actual
            home/away schedule pattern of the era (2-2-1-1-1 since 1987, else 2-2-1-1-1 too).
Outputs: data/series.csv, data/series_states.csv, analysis/output/series_states.tex
"""
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
g = pd.read_csv(ROOT / "data/raw/games_all.csv")
g = g[g.state == 7]

# ---- regular-season points % (W=2, T/OTL=1) by team-season, from final scores
rs = g[g.game_type == 2].copy()
def team_rows(df):
    h = pd.DataFrame(dict(season=df.season, team=df.home_id, gf=df.home_score, ga=df.away_score, lp=df.last_period))
    a = pd.DataFrame(dict(season=df.season, team=df.away_id, gf=df.away_score, ga=df.home_score, lp=df.last_period))
    t = pd.concat([h, a])
    t["pts"] = np.where(t.gf > t.ga, 2, np.where(t.gf == t.ga, 1, np.where(t.lp > 3, 1, 0)))
    # post-2005 shootout games: final score already includes SO winner; loser in OT/SO gets 1
    return t
tr = team_rows(rs)
ptspct = tr.groupby(["season", "team"]).pts.mean().div(2).rename("ptspct").reset_index()

po = g[g.game_type == 3].copy()
po["gid"] = po.game_id.astype(str)
po["round"] = po.gid.str[6:8].astype(int)
po["series"] = po.gid.str[8].astype(int)
po["game_no"] = po.gid.str[9].astype(int)
po = po.sort_values(["season", "round", "series", "game_no"])

recs, games_long = [], []
for (season, rnd, ser), s in po.groupby(["season", "round", "series"]):
    teams = sorted(set(s.home_id) | set(s.away_id))
    if len(teams) != 2 or (s.home_score == s.away_score).any():
        continue
    a, b = teams
    wins = {a: 0, b: 0}
    seq = []
    for _, r in s.iterrows():
        w = r.home_id if r.home_score > r.away_score else r.away_id
        wins[w] += 1
        seq.append((w, r.home_id))
    if max(wins.values()) != 4 or len(seq) > 7:
        continue
    winner = a if wins[a] == 4 else b
    recs.append(dict(season=season, round=rnd, series=ser, team_a=a, team_b=b, winner=winner,
                     games=len(seq), seq="".join("A" if w == a else "B" for w, _ in seq),
                     a_home_g1=int(seq[0][1] == a)))
    for i, (w, h) in enumerate(seq):
        games_long.append(dict(season=season, round=rnd, series=ser, game_no=i + 1, team=a, opp=b,
                               home=int(h == a), win=int(w == a)))
ser = pd.DataFrame(recs)
ser = ser.merge(ptspct.rename(columns={"team": "team_a", "ptspct": "pa"}), on=["season", "team_a"], how="left")
ser = ser.merge(ptspct.rename(columns={"team": "team_b", "ptspct": "pb"}), on=["season", "team_b"], how="left")
ser.to_csv(ROOT / "data/series.csv", index=False)

# ---- per-game model: P(team wins) ~ logit(pts% diff) + home
gl = pd.DataFrame(games_long)
gl = gl.merge(ptspct.rename(columns={"team": "team", "ptspct": "p_team"}), on=["season", "team"], how="left")
gl = gl.merge(ptspct.rename(columns={"team": "opp", "ptspct": "p_opp"}), on=["season", "opp"], how="left")
gl["diff"] = gl.p_team - gl.p_opp
gl["home_s"] = 2 * gl.home - 1
gm = smf.logit("win ~ 0 + diff + home_s", data=gl.dropna()).fit(disp=0)
B_DIFF, B_HOME = gm.params["diff"], gm.params["home_s"]

def p_game(diff, home):
    return 1 / (1 + np.exp(-(B_DIFF * diff + B_HOME * (1 if home else -1))))

HOME_PATTERN = [1, 1, 0, 0, 1, 0, 1]   # 2-2-1-1-1 for the team with home ice in game 1

@lru_cache(None)
def p_close(wa, wb, diff, a_home_g1):
    """P(team A wins series) from state (wa, wb) with per-game probs from the model."""
    if wa == 4: return 1.0
    if wb == 4: return 0.0
    k = wa + wb
    home = HOME_PATTERN[k] == 1 if a_home_g1 else HOME_PATTERN[k] == 0
    p = p_game(diff, home)
    return p * p_close(wa + 1, wb, diff, a_home_g1) + (1 - p) * p_close(wa, wb + 1, diff, a_home_g1)

@lru_cache(None)
def p_coin(wa, wb):
    if wa == 4: return 1.0
    if wb == 4: return 0.0
    return 0.5 * p_coin(wa + 1, wb) + 0.5 * p_coin(wa, wb + 1)

rows = []
for _, r in ser.iterrows():
    wa = wb = 0
    diff = (r.pa - r.pb) if pd.notna(r.pa) and pd.notna(r.pb) else 0.0
    for ch in r.seq[:-1]:
        wa += ch == "A"; wb += ch == "B"
        if wa == wb:
            continue
        lead_a = wa > wb
        state = f"{max(wa, wb)}-{min(wa, wb)}"
        p_model_a = p_close(wa, wb, round(diff, 3), int(r.a_home_g1))
        rows.append(dict(season=r.season, era="1987+" if r.season >= 19871988 else "pre-1987", state=state,
                         leader_won=int((r.winner == r.team_a) == lead_a),
                         p_model=p_model_a if lead_a else 1 - p_model_a,
                         p_coin=p_coin(max(wa, wb), min(wa, wb))))
st = pd.DataFrame(rows)
st.to_csv(ROOT / "data/series_states.csv", index=False)

order = ["1-0", "2-0", "2-1", "3-0", "3-1", "3-2"]
def summarize(d):
    out = (d.groupby("state").agg(n=("leader_won", "size"), leader_won=("leader_won", "mean"),
                                  blown=("leader_won", lambda x: int((1 - x).sum())),
                                  p_coin=("p_coin", "mean"), p_model=("p_model", "mean"))
             .reindex(order).reset_index())
    out["blow_rate"] = 1 - out.leader_won
    out["expected_blow_model"] = 1 - out.p_model
    out["blow_ratio_vs_model"] = out.blow_rate / out.expected_blow_model
    return out
all_ = summarize(st); mod = summarize(st[st.era == "1987+"])
all_.to_csv(ROOT / "analysis/output/series_states_all.csv", index=False)
mod.to_csv(ROOT / "analysis/output/series_states_1987plus.csv", index=False)
print(f"{len(ser)} best-of-7 series; per-game model b_diff={B_DIFF:.2f} b_home={B_HOME:.3f}")
print("ALL ERAS\n", all_.round(3).to_string(index=False))
print("1987+\n", mod.round(3).to_string(index=False))
