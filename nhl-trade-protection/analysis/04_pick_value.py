"""Empirical draft-pick value chart: expected career NHL games played by overall pick.

Uses draft classes 2005-2014 (every pick has had >= 11 seasons to reach the NHL).
A pick whose player is absent from CapWages, or never played, counts as 0 GP, so
each overall slot has exactly one observation per draft year.
A smooth monotone curve (log-linear in pick number, fit by least squares on slot
means) is used to value picks in the case dataset.
Output: data/pick_value_chart.csv, analysis/figures/pick_value.pdf
"""
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from common import DATA, FIG, load_players

rows = []
for p in load_players():
    if str(p.get("draft_year")).isdigit() and str(p.get("drafted_overall")).isdigit():
        rows.append(dict(draft_year=int(p["draft_year"]), pick=int(p["drafted_overall"]),
                         gp=int(p.get("careerGamesPlayed") or 0), name=p["name"]))
d = pd.DataFrame(rows)
d = d[(d.draft_year >= 2005) & (d.draft_year <= 2014) & (d.pick <= 224)]
d = d.sort_values("gp").drop_duplicates(["draft_year", "pick"], keep="last")
years = range(2005, 2015)
grid = pd.MultiIndex.from_product([years, range(1, 225)], names=["draft_year", "pick"]).to_frame(index=False)
g = grid.merge(d, on=["draft_year", "pick"], how="left")
g["found"] = g.gp.notna()
g["gp"] = g.gp.fillna(0)
by = g.groupby("pick").agg(mean_gp=("gp", "mean"), share_found=("found", "mean"),
                           share_100gp=("gp", lambda x: (x >= 100).mean())).reset_index()
# smooth: gp(pick) = a * (pick + c)^(-b) + d, fit by nonlinear least squares on slot means
from scipy.optimize import curve_fit
f = lambda x, a, b, c, d: a * (x + c) ** (-b) + d
(a, b, c, d0), _ = curve_fit(f, by.pick, by.mean_gp, p0=[3000, 0.8, 3, 0], maxfev=20000)
by["fit_gp"] = f(by.pick, a, b, c, d0)
ref = float(by.loc[by.pick == 16, "fit_gp"].iloc[0])
by["value_mid1st_equiv"] = by.fit_gp / ref
by.to_csv(DATA / "pick_value_chart.csv", index=False)
print(f"fit: gp = {a:.1f}*(pick+{c:.2f})^-{b:.3f} + {d0:.1f}; coverage of top-60 slots found: "
      f"{g[g.pick <= 60].found.mean():.2%}")
print(by.loc[by.pick.isin([1, 2, 5, 9, 10, 16, 25, 32, 48, 64, 80, 96, 112, 150, 200]),
             ["pick", "mean_gp", "fit_gp", "value_mid1st_equiv", "share_100gp"]].round(2).to_string())

plt.rcParams.update({"font.family": "serif", "font.size": 9})
fig, ax = plt.subplots(figsize=(5.5, 3.2))
ax.scatter(by.pick, by.mean_gp, s=6, color="#8a8a8a", label="Mean career GP (2005–14 drafts)")
ax.plot(by.pick, by.fit_gp, color="#1f4e79", lw=1.6, label="Smoothed value curve")
ax.set_xlabel("Overall pick"); ax.set_ylabel("Career NHL games played")
ax.spines[["top", "right"]].set_visible(False); ax.legend(frameon=False)
fig.tight_layout(); fig.savefig(FIG / "pick_value.pdf")
