"""Numerical illustrations of the formal model (Section 4 of the paper).

Acquirer valuations v_i ~ U[0,1]. Retention value R. With m >= 2 approved buyers the
incumbent runs a competitive sale and earns E[max(v_(2:m), R)]; with m = 1 it bargains
bilaterally (Nash, bargaining weight beta) and earns R + beta * E[(v - R)^+].
Outputs: analysis/output/model_table.csv, analysis/figures/model_return.pdf
"""
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from common import FIG, OUT


def second_order_cdf(x, m):
    """CDF of the second-highest of m iid U[0,1] draws."""
    x = np.clip(x, 0, 1)
    return x ** m + m * x ** (m - 1) * (1 - x)


def payoff(m, R, beta=0.5):
    if m == 1:
        return R + beta * (1 - R) ** 2 / 2          # E[(v-R)^+] for U[0,1]
    # E[max(Y, R)] = R + int_R^1 (1 - G(y)) dy
    y = np.linspace(R, 1, 4001)
    return R + np.trapezoid(1 - second_order_cdf(y, m), y)


def wait_value(m, R, rho, delta, m_next):
    """Value of waiting one period: reconciliation w.p. rho (value 1 normalised to
    the committed player's worth V), otherwise sell next period at delta * payoff."""
    return rho * 1.0 + (1 - rho) * delta * payoff(m_next, R)


if __name__ == "__main__":
    N = 31
    rows = []
    for m in [1, 2, 3, 5, 10, 16, 21, 31]:
        rows.append(dict(m=m, blocked_k=N - m,
                         private_R020=payoff(m, 0.20), public_R005=payoff(m, 0.05),
                         audience_loss=payoff(m, 0.20) - payoff(m, 0.05)))
    t = pd.DataFrame(rows).round(3)
    t.to_csv(OUT / "model_table.csv", index=False)
    print(t.to_string(index=False))
    lines = [r"\begin{tabular}{rrccc}\toprule",
             r"Approved $m$ & Blocked $k$ & Private ($R=0.20$) & Public ($R=0.05$) & Loss from publicity\\\midrule"]
    lines += [f"{r.m} & {r.blocked_k} & {r.private_R020:.3f} & {r.public_R005:.3f} & {r.audience_loss:.3f}\\\\"
              for r in t.itertuples()]
    lines.append(r"\bottomrule\end{tabular}")
    open(OUT / "model_table.tex", "w").write("\n".join(lines))
    print("beta* (m=2 beats m=1 iff beta < beta*) at R=0:", round(payoff(2, 0) / payoff(1, 0, 1.0), 3))

    plt.rcParams.update({"font.family": "serif", "font.size": 9})
    fig, ax = plt.subplots(figsize=(5.5, 3.2))
    ms = np.arange(1, 32)
    for R, lab, ls in [(0.20, "Private demand (R = 0.20)", "-"), (0.05, "Public demand (R = 0.05)", "--")]:
        ax.plot(ms, [payoff(m, R) for m in ms], ls, color="#1f4e79", lw=1.5, label=lab)
    for x, txt in [(3, "Larkin-type\n3-team list"), (21, "10-team\nno-trade list")]:
        ax.axvline(x, color="#999999", lw=0.6, ls=":")
        ax.text(x + 0.4, 0.12, txt, fontsize=7, color="#555555")
    ax.set_xlabel("Approved destinations m (= N − k)")
    ax.set_ylabel("Incumbent's expected return\n(share of top valuation)")
    ax.set_ylim(0, 1); ax.spines[["top", "right"]].set_visible(False); ax.legend(frameon=False, loc="lower right")
    fig.tight_layout(); fig.savefig(FIG / "model_return.pdf")
