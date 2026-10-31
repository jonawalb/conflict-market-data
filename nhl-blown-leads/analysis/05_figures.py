"""Figures for the blown-leads paper."""
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIG = ROOT / "analysis/figures"
plt.rcParams.update({"font.family": "serif", "font.size": 9, "axes.spines.top": False, "axes.spines.right": False})
C = {"1-0": "#9ecae1", "2-1": "#4292c6", "3-2": "#08519c", "4-3": "#08306b",
     "2-0": "#fdae6b", "3-1": "#e6550d", "4-2": "#a63603", "5-3": "#7f2704",
     "3-0": "#a1d99b", "4-1": "#41ab5d", "5-2": "#006d2c"}

ls = pd.read_csv(ROOT / "data/lead_states.csv")
reg = ls[ls.game_type == 2].copy()
reg["bin"] = (reg.remaining // 300) * 5 + 2.5          # 5-minute bins of time remaining
fig, axes = plt.subplots(1, 3, figsize=(7.4, 2.8), sharey=False)
for ax, d, states in zip(axes, [1, 2, 3], [["1-0", "2-1", "3-2", "4-3"], ["2-0", "3-1", "4-2", "5-3"], ["3-0", "4-1", "5-2"]]):
    for s in states:
        sub = reg[reg.state == s].groupby("bin").agg(n=("lost_game", "size"), p=("lost_game", "mean")).reset_index()
        sub = sub[sub.n >= 150]
        ax.plot(sub.bin, sub.p * 100, marker="o", ms=2.5, lw=1.2, color=C[s], label=s)
    ax.set_title(f"{d}-goal leads", fontsize=9)
    ax.invert_xaxis(); ax.legend(frameon=False, fontsize=7)
axes[0].set_ylabel("Leader goes on to lose (%)"); fig.supxlabel("Minutes left in regulation when the score was reached", fontsize=9)
fig.tight_layout(); fig.savefig(FIG / "fig_ingame_curves.pdf"); plt.close(fig)

s = pd.read_csv(ROOT / "analysis/output/series_states_all.csv")
fig, ax = plt.subplots(figsize=(5.2, 2.8))
x = np.arange(len(s))
ax.bar(x - 0.2, s.blow_rate * 100, 0.4, color="#e6550d", label="Observed")
ax.bar(x + 0.2, s.expected_blow_model * 100, 0.4, color="#bdbdbd", label="Expected (team strength + home ice)")
for i, r in s.iterrows():
    ax.text(i - 0.2, r.blow_rate * 100 + 0.8, f"{int(r.blown)}/{int(r.n)}", ha="center", fontsize=6.5)
ax.set_xticks(x); ax.set_xticklabels([f"Up {st}" for st in s.state]); ax.set_ylabel("Series lead blown (%)")
ax.legend(frameon=False, fontsize=7); fig.tight_layout(); fig.savefig(FIG / "fig_series.pdf"); plt.close(fig)

# entering third
e = pd.read_csv(ROOT / "analysis/output/ingame_entering_third.csv")
fig, ax = plt.subplots(figsize=(5.2, 2.6))
cols = [C.get(st, "#999999") for st in e.state]
ax.bar(range(len(e)), (1 - e.win) * 100, color=cols)
ax.set_xticks(range(len(e))); ax.set_xticklabels(e.state, fontsize=7)
for i, r in e.reset_index().iterrows():
    ax.text(i, (1 - r.win) * 100 + 0.5, f"{(1 - r.win) * 100:.0f}%", ha="center", fontsize=6.5)
ax.set_ylabel("Leader loses (%)"); ax.set_xlabel("Score entering the 3rd period (regular season)")
fig.tight_layout(); fig.savefig(FIG / "fig_entering_third.pdf"); plt.close(fig)
print("ok")
