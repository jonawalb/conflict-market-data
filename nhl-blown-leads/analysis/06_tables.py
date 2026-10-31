"""LaTeX tables for the paper."""
import pandas as pd
from scipy import stats
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
O = ROOT / "analysis/output"

def tex(rows, header, colfmt):
    return "\n".join([r"\begin{tabular}{" + colfmt + r"}\toprule", header + r"\\\midrule"] +
                     [" & ".join(r) + r"\\" for r in rows] + [r"\bottomrule\end{tabular}"])

t = pd.read_csv(O / "ingame_states_regular.csv")
d = pd.read_csv(O / "ingame_danger_ranking.csv").set_index("state")
rows = []
for _, r in t.iterrows():
    dd = d.loc[r.state] if r.state in d.index else None
    rows.append([r.state, f"{int(r.n):,}", f"{r.mean_remaining_min:.0f}", f"{r.lost_lead*100:.1f}", f"{r.lost_game*100:.1f}",
                 f"{dd.exp_lost_game*100:.1f}" if dd is not None else "", f"{dd.ratio_lost_game:.2f}" if dd is not None else ""])
open(O / "tab_ingame.tex", "w").write(tex(rows, r"Score & Games & Min.\ left & Lead lost (\%) & Leader lost (\%) & Expected (\%) & Obs./Exp.", "lrrrrrr"))

rows = []
for lab, f in [("All eras (1939--2026)", "series_states_all.csv"), ("1987--2026", "series_states_1987plus.csv")]:
    s = pd.read_csv(O / f)
    rows.append([r"\multicolumn{7}{l}{\emph{" + lab + "}}"])
    for _, r in s.iterrows():
        p = stats.binomtest(int(r.blown), int(r.n), r.expected_blow_model).pvalue
        rows.append([f"Up {r.state}", f"{int(r.n)}", f"{int(r.blown)}", f"{r.blow_rate*100:.1f}", f"{(1-r.p_coin)*100:.1f}",
                     f"{r.expected_blow_model*100:.1f}", f"{p:.2f}"])
body = []
for r in rows:
    body.append(r[0] if len(r) == 1 else " & ".join(r))
open(O / "tab_series.tex", "w").write("\n".join([r"\begin{tabular}{lrrrrrr}\toprule",
    r"Lead & Series & Blown & Blown (\%) & Coin-flip (\%) & Model (\%) & $p$\\\midrule"] + [b + r"\\" for b in body] + [r"\bottomrule\end{tabular}"]))
print(open(O / "tab_series.tex").read())

# lookup: P(leader loses) by lead size and minutes remaining when the lead score was reached
ls = pd.read_csv(ROOT / "data/lead_states.csv")
reg = ls[(ls.game_type == 2) & (ls["diff"] <= 3)].copy()
bins = [0, 300, 600, 1200, 2400, 3600]
labels = ["0--5", "5--10", "10--20", "20--40", "40--60"]
reg["tb"] = pd.cut(reg.remaining, bins=bins, labels=labels, include_lowest=True)
lk = reg.pivot_table(index="diff", columns="tb", values="lost_game", aggfunc="mean", observed=False) * 100
lk = lk[labels[::-1]]
lk.to_csv(O / "lookup_lost_by_time.csv")
rows = [[f"{d}-goal lead"] + [f"{v:.1f}" for v in lk.loc[d]] for d in lk.index]
open(O / "tab_lookup.tex", "w").write(tex(rows, "Minutes left & " + " & ".join(labels[::-1]), "lrrrrr"))
print(lk.round(1))

tp = pd.read_csv(O / "ingame_states_playoffs.csv")
rows = [[r.state, f"{int(r.n):,}", f"{r.mean_remaining_min:.0f}", f"{r.lost_lead*100:.1f}", f"{r.lost_game*100:.1f}"] for _, r in tp.iterrows()]
open(O / "tab_ingame_playoffs.tex", "w").write(tex(rows, r"Score & Games & Min.\ left & Lead lost (\%) & Leader lost (\%)", "lrrrr"))
