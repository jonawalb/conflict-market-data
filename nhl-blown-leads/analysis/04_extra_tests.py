"""Follow-up tests: does game scoring level matter at a fixed lead and time?
Entering the 3rd period, and at every first-reached lead state."""
import gzip, json
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis/output"
ls = pd.read_csv(ROOT / "data/lead_states.csv")
reg = ls[(ls.game_type == 2)].dropna(subset=["strength_diff"]).copy()
reg["rem_min"] = reg.remaining / 60
reg["total"] = reg["L"] + reg["T"]
log = open(OUT / "extra_tests.txt", "w")
def say(*a):
    print(*a); print(*a, file=log)

for d in [1, 2]:
    sub = reg[reg["diff"] == d]
    m = smf.logit("lost_game ~ total + rem_min + I(rem_min**2) + leader_home + strength_diff", data=sub).fit(
        disp=0, cov_type="cluster", cov_kwds={"groups": sub.game_id})
    say(f"[first-reached, {d}-goal leads] effect of each extra goal already scored on P(leader loses): "
        f"logit {m.params['total']:+.3f} (se {m.bse['total']:.3f}, p={m.pvalues['total']:.3f}), N={int(m.nobs)}")

# entering the 3rd period
rows = []
f = gzip.open(ROOT / "data/raw/goals_2005plus.jsonl.gz", "rt")
for line in f:
    g = json.loads(line)
    if g["home_final"] is None:
        continue
    h = sum(1 for x in g["goals"] if x["period"] <= 2 and x["team"] == g["home"])
    a = sum(1 for x in g["goals"] if x["period"] <= 2 and x["team"] == g["away"])
    if h == a:
        continue
    lead_home = h > a
    rows.append(dict(game_type=g["game_type"], season=g["season"], diff=abs(h - a), total=h + a,
                     leader_home=int(lead_home), state=f"{max(h, a)}-{min(h, a)}",
                     lost=int((g["home_final"] > g["away_final"]) != lead_home)))
e = pd.DataFrame(rows)
e.to_csv(ROOT / "data/entering_third.csv", index=False)
for gt, lab in [(2, "regular season"), (3, "playoffs")]:
    for d in [1, 2]:
        sub = e[(e.game_type == gt) & (e["diff"] == d)]
        m = smf.logit("lost ~ total + leader_home + C(season)", data=sub).fit(disp=0)
        say(f"[entering 3rd, {lab}, {d}-goal lead] each extra goal in first 40 min: logit {m.params['total']:+.3f} "
            f"(se {m.bse['total']:.3f}, p={m.pvalues['total']:.3f}); N={len(sub)}; "
            f"loss rate low-scoring(total<=2 or 3)={sub[sub.total <= d + 1].lost.mean():.3f} "
            f"high-scoring(total>=5)={sub[sub.total >= 5].lost.mean():.3f}")
log.close()
