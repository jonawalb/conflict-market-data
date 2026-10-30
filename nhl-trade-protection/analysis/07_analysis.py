"""Main empirical analysis: prevalence trends, trade correlations, player-level trade
hazards, the price of protection, signing cohorts, and the case-level tests.

Writes figures to analysis/figures/ and tables (CSV + LaTeX) to analysis/output/.
"""
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import statsmodels.formula.api as smf
from scipy import stats
from common import DATA, FIG, OUT, parse_date, season_of

plt.rcParams.update({"font.family": "serif", "font.size": 9, "axes.spines.top": False,
                     "axes.spines.right": False})
BLUE, ORANGE, GREY, GREEN = "#1f4e79", "#c55a11", "#8a8a8a", "#548235"
GOOD_COVERAGE = "2013-14"     # >= 90% of NHL roster players matched to a contract
OK_COVERAGE = "2009-10"       # >= 75%
log = open(OUT / "analysis_log.txt", "w")
def say(*a):
    print(*a); print(*a, file=log)

cap = pd.read_csv(DATA / "salary_cap_by_season.csv")
prev = pd.read_csv(DATA / "clause_prevalence_by_season.csv")
tbs = pd.read_csv(DATA / "trades_by_season.csv")
off = pd.read_csv(DATA / "nhl_deadline_day_official.csv")
ps = pd.read_csv(DATA / "player_season_panel.csv", low_memory=False)
cases = pd.read_csv(DATA / "protected_player_cases.csv")

def latex_table(df, path, fmt=None, col_format=None):
    fmt = fmt or {}
    cols = df.columns
    lines = [r"\begin{tabular}{" + (col_format or "l" + "r" * (len(cols) - 1)) + r"}\toprule",
             " & ".join(str(c) for c in cols) + r"\\\midrule"]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if isinstance(v, (float, np.floating)):
                cells.append("" if np.isnan(v) else fmt.get(c, "{:.2f}").format(v))
            else:
                cells.append(str(v))
        lines.append(" & ".join(cells) + r"\\")
    lines.append(r"\bottomrule\end{tabular}")
    open(path, "w").write("\n".join(lines).replace("%", r"\%").replace("_", r"\_").replace(r"\\%", r"\%"))

# ======================================================== 1. prevalence figure
s = prev.season
x = np.arange(len(s))
fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.2, 3.0))
nmc = prev.n_NMC + prev.n_M_NMC
a1.stackplot(x, nmc, prev.n_NTC, prev.n_M_NTC, colors=[BLUE, "#5b8fc7", "#b4cbe6"],
             labels=["NMC", "Full NTC", "Modified NTC"])
a1.axvspan(-0.5, list(s).index(GOOD_COVERAGE) - 0.5, color="#eeeeee", zorder=0)
a1.text(0.3, 290, "partial\ncoverage", fontsize=7, color=GREY)
a1.set_xticks(x[::3]); a1.set_xticklabels(s[::3], rotation=45, ha="right")
a1.set_ylabel("Contracts with trade protection"); a1.legend(frameon=False, loc="upper left", fontsize=7,
                                                             bbox_to_anchor=(0.0, 0.85))
a1.set_title("(a) Protected contracts in force", fontsize=9)
a2.plot(x, prev.share_cap_dollars_protected, color=BLUE, lw=1.5, label="Share of cap dollars")
a2.plot(x, prev.share_protected_star, color=ORANGE, lw=1.5, ls="--", label="Share of star contracts (≥7.5% of cap)")
a2.plot(x, prev.share_protected_ge_1m, color=GREY, lw=1.2, ls=":", label="Share of contracts ≥ $1M")
a2.axvspan(-0.5, list(s).index(GOOD_COVERAGE) - 0.5, color="#eeeeee", zorder=0)
a2.set_ylim(0, 0.85); a2.set_xticks(x[::3]); a2.set_xticklabels(s[::3], rotation=45, ha="right")
a2.legend(frameon=False, fontsize=7, loc="lower right"); a2.set_title("(b) Protected share", fontsize=9)
fig.tight_layout(); fig.savefig(FIG / "fig_prevalence.pdf"); plt.close(fig)

g = prev.set_index("season")
say("== Prevalence ==")
for ss in ["2009-10", "2013-14", "2019-20", "2022-23", "2025-26", "2026-27"]:
    say(ss, "protected", int(g.loc[ss, "n_protected"]), "NMC", int(g.loc[ss, "n_NMC"]),
        "cap$ share %.3f" % g.loc[ss, "share_cap_dollars_protected"],
        "star share %.3f" % g.loc[ss, "share_protected_star"],
        "per team %.1f" % g.loc[ss, "protected_per_team"])

t1 = prev[prev.season >= OK_COVERAGE][["season", "n_protected", "n_NMC", "n_NTC", "n_M_NTC", "protected_per_team",
                                       "share_cap_dollars_protected", "share_protected_star", "median_block_list_n"]].copy()
t1.columns = ["Season", "Protected", "NMC", "NTC", "M-NTC", "Per team", "Share cap $", "Share stars", "Median NT list"]
for c in ["Protected", "NMC", "NTC", "M-NTC"]:
    t1[c] = t1[c].astype(int).astype(str)
latex_table(t1, OUT / "tab_prevalence.tex", fmt={"Per team": "{:.1f}", "Median NT list": "{:.0f}"})

# ======================================================== 2. trades by season
d = tbs.merge(off[["season", "trades"]].rename(columns={"trades": "official_deadline_trades"}), on="season", how="left")
d = d.merge(prev[["season", "share_protected_core", "share_cap_dollars_protected", "n_protected", "share_protected_star"]], on="season")
d = d.merge(cap, on="season")
d["cap_growth"] = d.cap_upper_limit_musd.pct_change()
d.loc[d.season == "2008-09", "cap_growth"] = 56.7 / 50.3 - 1
d["flat_cap"] = d.season.isin(["2020-21", "2021-22", "2022-23"]).astype(int)
d["short_season"] = (d.regular_season_games < 82).astype(int)
d["expansion_year"] = d.season.isin(["2016-17", "2017-18", "2020-21", "2021-22"]).astype(int)  # pre/post draft
v = d.dropna(subset=["official_deadline_trades"])
v = v[v.season >= OK_COVERAGE]
r_dl = stats.pearsonr(v.deadline_day_trades, v.official_deadline_trades)
say("\n== Validation: CapWages-derived vs official deadline-day trades ==")
say("r = %.2f (p=%.3f), N=%d, mean abs diff = %.1f" % (r_dl[0], r_dl[1], len(v),
    (v.deadline_day_trades - v.official_deadline_trades).abs().mean()))

full = d[(d.season >= OK_COVERAGE) & (d.season <= "2025-26")].copy()
full["t"] = np.arange(len(full))
fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.2, 3.0))
xx = np.arange(len(full))
a1.bar(xx, full.trades, color="#b4cbe6", label="All trades (≥1 player)")
a1.bar(xx, full.trades_with_protected, color=BLUE, label="…involving a protected player")
a1.set_xticks(xx[::2]); a1.set_xticklabels(full.season[::2], rotation=45, ha="right")
a1.legend(frameon=False, fontsize=7, loc="upper left"); a1.set_title("(a) Trades per league year", fontsize=9)
a2.scatter(full.share_cap_dollars_protected, full.trades, color=BLUE, s=14)
for _, r in full.iterrows():
    a2.annotate(r.season[2:4] + "/" + r.season[-2:], (r.share_cap_dollars_protected, r.trades), fontsize=6,
                xytext=(2, 2), textcoords="offset points", color=GREY)
a2.set_xlabel("Share of cap dollars under protection"); a2.set_ylabel("Trades")
a2.set_title("(b) Protection and trade volume", fontsize=9)
fig.tight_layout(); fig.savefig(FIG / "fig_trades.pdf"); plt.close(fig)

say("\n== Season-level correlations (%s..2025-26, N=%d) ==" % (OK_COVERAGE, len(full)))
for y in ["trades", "star_trades", "deadline_day_trades", "share_trades_with_protected"]:
    r = stats.pearsonr(full.share_cap_dollars_protected, full[y])
    say(f"corr(share cap$ protected, {y}) = {r[0]:.2f} (p={r[1]:.3f})")

def fit_season(y, data):
    m = smf.ols(f"{y} ~ share_cap_dollars_protected + cap_growth + flat_cap + short_season + expansion_year + t",
                data=data).fit(cov_type="HC3")
    return m

season_models = {y: fit_season(y, full) for y in ["trades", "star_trades", "deadline_day_trades", "share_trades_with_protected"]}
rows = []
labels = {"share_cap_dollars_protected": "Protected share of cap \\$", "cap_growth": "Cap growth (pct.)",
          "flat_cap": "Flat-cap era", "short_season": "Shortened season", "expansion_year": "Expansion draft year",
          "t": "Linear trend", "Intercept": "Constant"}
for k, lab in labels.items():
    row = {"": lab}
    row2 = {"": ""}
    for y, m in season_models.items():
        row[y] = f"{m.params[k]:.2f}" + ("$^{*}$" if m.pvalues[k] < 0.05 else "")
        row2[y] = f"({m.bse[k]:.2f})"
    rows += [row, row2]
rows.append({"": "N", **{y: str(int(m.nobs)) for y, m in season_models.items()}})
rows.append({"": "$R^2$", **{y: f"{m.rsquared:.2f}" for y, m in season_models.items()}})
t2 = pd.DataFrame(rows)
t2.columns = ["", "All trades", "Star trades", "Deadline-day", "Share w/ protected"]
lines = [r"\begin{tabular}{lcccc}\toprule", " & ".join(t2.columns) + r"\\\midrule"]
lines += [" & ".join(r) + r"\\" for r in t2.astype(str).values.tolist()]
lines.append(r"\bottomrule\end{tabular}")
open(OUT / "tab_season_reg.tex", "w").write("\n".join(lines))
for y, m in season_models.items():
    say(f"season OLS {y}: b(protected share)={m.params['share_cap_dollars_protected']:.2f} "
        f"se={m.bse['share_cap_dollars_protected']:.2f} p={m.pvalues['share_cap_dollars_protected']:.3f}")
full.to_csv(OUT / "season_panel.csv", index=False)

# ======================================================== 3. player-season trade hazard
p = ps[(ps.season >= GOOD_COVERAGE) & (ps.season <= "2025-26")].copy()
p = p[(p.cap_pct >= 0.03) & (p.age >= 26)]
p["ppg_prev"] = np.where(p.gp_prev > 0, p.pts_prev / p.gp_prev, np.nan)
p["goalie"] = (p.pos == "G").astype(int)
p["clause_cat"] = pd.Categorical(p.clause.replace({"M-NMC": "NMC"}), categories=["none", "M-NTC", "NTC", "NMC"])
p["yl"] = p.years_left.clip(upper=5)
p["star"] = (p.cap_pct >= 0.075).astype(int)
p = p.dropna(subset=["age", "cap_pct"])
pm = p.dropna(subset=["gp_prev"]).copy()
pm["ppg_prev"] = pm.ppg_prev.fillna(0)
f1 = "traded ~ C(clause_cat) + cap_pct + I(cap_pct**2) + age + I(age**2) + C(yl) + goalie + C(season)"
f2 = f1 + " + ppg_prev + gp_prev"
m1 = smf.ols(f1, data=p).fit(cov_type="cluster", cov_kwds={"groups": p.slug})
m2 = smf.ols(f2, data=pm).fit(cov_type="cluster", cov_kwds={"groups": pm.slug})
ps_star = pm[pm.star == 1]
m3 = smf.ols(f2, data=ps_star).fit(cov_type="cluster", cov_kwds={"groups": ps_star.slug})
m4 = smf.logit(f2.replace("C(season)", "C(season)"), data=pm).fit(disp=0, cov_type="cluster",
                                                                    cov_kwds={"groups": pm.slug})
say("\n== Player-season LPM: P(traded) (seasons %s..2025-26, age>=26, cap>=3%%) ==" % GOOD_COVERAGE)
say("base rate traded: %.3f (none) vs %.3f (any protection); N=%d" %
    (p[p.clause == "none"].traded.mean(), p[p.clause != "none"].traded.mean(), len(p)))
keys = ["C(clause_cat)[T.M-NTC]", "C(clause_cat)[T.NTC]", "C(clause_cat)[T.NMC]"]
names = {"C(clause_cat)[T.M-NTC]": "Modified NTC", "C(clause_cat)[T.NTC]": "Full NTC", "C(clause_cat)[T.NMC]": "NMC"}
rows = []
for k in keys + ["cap_pct", "ppg_prev"]:
    lab_ = {**names, "cap_pct": "Cap hit (share of cap)", "ppg_prev": "Points per game, prior season"}[k]
    row, row2 = {"": lab_}, {"": ""}
    for lab, m in [("(1) LPM", m1), ("(2) LPM", m2), ("(3) LPM, stars", m3), ("(4) Logit", m4)]:
        if k in m.params:
            row[lab] = f"{m.params[k]:.3f}" + ("$^{*}$" if m.pvalues[k] < 0.05 else "")
            row2[lab] = f"({m.bse[k]:.3f})"
        else:
            row[lab] = row2[lab] = ""
    rows += [row, row2]
rows.append({"": "Controls", **{l: "Age, yrs left, G, season FE" for l in ["(1) LPM", "(2) LPM", "(3) LPM, stars", "(4) Logit"]}})
rows.append({"": "N (player-seasons)", **{l: str(int(m.nobs)) for l, m in
             [("(1) LPM", m1), ("(2) LPM", m2), ("(3) LPM, stars", m3), ("(4) Logit", m4)]}})
rows.append({"": "Mean of DV", **{l: f"{m.model.endog.mean():.3f}" for l, m in
             [("(1) LPM", m1), ("(2) LPM", m2), ("(3) LPM, stars", m3), ("(4) Logit", m4)]}})
t3 = pd.DataFrame(rows)
t3 = t3[t3[""] != "Controls"]
lines = [r"\begin{tabular}{lcccc}\toprule", " & ".join(t3.columns) + r"\\\midrule"]
lines += [" & ".join(r) + r"\\" for r in t3.astype(str).values.tolist()]
lines.append(r"\bottomrule\end{tabular}")
open(OUT / "tab_hazard.tex", "w").write("\n".join(lines))
for lab, m in [("m1", m1), ("m2", m2), ("m3 stars", m3), ("m4 logit", m4)]:
    say(lab, {names[k]: (round(m.params[k], 3), round(m.pvalues[k], 3)) for k in keys if k in m.params}, "N", int(m.nobs))

# ======================================================== 4. price of protection (hedonic) and signing cohorts
con = pd.read_csv(DATA / "contracts.csv")
pst = pd.read_csv(DATA / "player_stats_nhl.csv")
prev_season = lambda ss: f"{int(ss[:4]) - 1}-{ss[2:4]}" if isinstance(ss, str) else None
con["platform_season"] = con.sign_year.map(prev_season)
con = con.merge(pst.rename(columns={"season": "platform_season", "gp": "gp_prev", "pts": "pts_prev"}),
                on=["slug", "platform_season"], how="left")
elig = con[(con.age_at_signing >= 25.5) & (con.term >= 3) & (con.cap_pct >= 0.05)
           & ~con.contract_type.str.contains("Entry", na=False) & (con.sign_year >= OK_COVERAGE)].copy()
coh = (elig.groupby("sign_year")
       .agg(n=("slug", "size"), any_protection=("any_prot", "mean"), full_ntc_or_nmc=("any_full", "mean"),
            nmc=("any_nmc", "mean"), mean_term=("term", "mean")).reset_index())
coh.to_csv(OUT / "signing_cohorts.csv", index=False)
say("\n== Signing cohorts (age>=25.5 at signing, term>=3, cap>=5%) ==")
say(coh.round(3).to_string(index=False))
# 2026-27 split at the new CBA effective date (Sept 16, 2026)
e26 = elig[elig.sign_year == "2026-27"].copy()
e26["post_cba"] = pd.to_datetime(e26.signing_date) >= "2026-09-16"
say("2026-27 signings pre/post new CBA:", e26.groupby("post_cba").agg(n=("slug", "size"), prot=("any_prot", "mean"),
    full=("any_full", "mean"), nmc=("any_nmc", "mean"), term=("term", "mean")).round(3).to_dict("index"))
# modified-list sizes for new contracts by signing year (block lists)
lists = elig[(elig.first_clause == "M-NTC") & elig.list_n.notna()].groupby("sign_year").list_n.median()
say("median parsed list size (M-NTC first season) by sign year:", lists.to_dict())

fig, ax = plt.subplots(figsize=(5.5, 3.0))
xx = np.arange(len(coh))
ax.plot(xx, coh.any_protection, color=BLUE, lw=1.5, marker="o", ms=3, label="Any protection")
ax.plot(xx, coh.full_ntc_or_nmc, color=ORANGE, lw=1.5, marker="s", ms=3, ls="--", label="Full NTC or NMC at some point")
ax.plot(xx, coh.nmc, color=GREEN, lw=1.2, marker="^", ms=3, ls=":", label="NMC at some point")
ax.set_xticks(xx); ax.set_xticklabels(coh.sign_year, rotation=45, ha="right", fontsize=7)
ax.set_ylim(0, 1.05); ax.set_ylabel("Share of new multi-year deals"); ax.legend(frameon=False, fontsize=7, loc="lower right")
fig.tight_layout(); fig.savefig(FIG / "fig_cohorts.pdf"); plt.close(fig)

h = elig[(elig.pos != "G") & (elig.gp_prev > 20)].copy()
h["ppg_prev"] = h.pts_prev / h.gp_prev
h["log_cap_pct"] = np.log(h.cap_pct)
h["log_term"] = np.log(h.term)
h["D"] = (h.pos == "D").astype(int)
h["any_prot"] = h.any_prot.astype(int); h["any_full"] = h.any_full.astype(int)
h["post2026"] = (h.sign_year == "2026-27").astype(int)
base = "log_cap_pct ~ {} + age_at_signing + I(age_at_signing**2) + log_term + ppg_prev + D + ppg_prev:D + gp_prev + C(sign_year)"
mh1 = smf.ols(base.format("any_prot"), data=h).fit(cov_type="HC3")
mh2 = smf.ols(base.format("any_full + any_prot"), data=h).fit(cov_type="HC3")
mh3 = smf.ols(base.format("any_prot + any_prot:post2026"), data=h).fit(cov_type="HC3")
say("\n== Hedonic: log(cap share) on protection, new skater contracts (N=%d) ==" % int(mh1.nobs))
for lab, m, ks in [("any", mh1, ["any_prot"]), ("full", mh2, ["any_full", "any_prot"]),
                   ("2026 interaction", mh3, ["any_prot", "any_prot:post2026"])]:
    say(lab, {k: (round(m.params[k], 3), round(m.bse[k], 3), round(m.pvalues[k], 3)) for k in ks if k in m.params},
        "R2 %.2f" % m.rsquared)
rows = []
hn = {"any_prot": "Any protection", "any_full": "Full NTC/NMC (vs. modified)",
      "any_prot:post2026": "Any protection $\\times$ signed 2026-27", "ppg_prev": "Points/game, platform season",
      "log_term": "log(term in seasons)", "age_at_signing": "Age at signing"}
for k, lab in hn.items():
    row, row2 = {"": lab}, {"": ""}
    for cl, m in [("(1)", mh1), ("(2)", mh2), ("(3)", mh3)]:
        if k in m.params:
            row[cl] = f"{m.params[k]:.3f}" + ("$^{*}$" if m.pvalues[k] < 0.05 else "")
            row2[cl] = f"({m.bse[k]:.3f})"
        else:
            row[cl] = row2[cl] = ""
    rows += [row, row2]
rows.append({"": "N (contracts)", **{cl: str(int(m.nobs)) for cl, m in [("(1)", mh1), ("(2)", mh2), ("(3)", mh3)]}})
rows.append({"": "$R^2$", **{cl: f"{m.rsquared:.2f}" for cl, m in [("(1)", mh1), ("(2)", mh2), ("(3)", mh3)]}})
t4 = pd.DataFrame(rows)
lines = [r"\begin{tabular}{lccc}\toprule", " & ".join(t4.columns) + r"\\\midrule"]
lines += [" & ".join(r) + r"\\" for r in t4.astype(str).values.tolist()]
lines.append(r"\bottomrule\end{tabular}")
open(OUT / "tab_hedonic.tex", "w").write("\n".join(lines))

# ======================================================== 4b. forward look: protection already committed
cs_all = pd.read_csv(DATA / "contract_seasons.csv", low_memory=False)
fwd = (cs_all[(cs_all.season >= "2026-27") & (cs_all.season <= "2031-32")]
       .groupby("season").apply(lambda g: pd.Series(dict(
           contracts=len(g), protected=int(g.protected.sum()), nmc=int(g.clause.isin(["NMC", "M-NMC"]).sum()),
           full=int(g.full.sum()), committed_musd=g.cap_hit.sum() / 1e6,
           protected_musd=g.loc[g.protected, "cap_hit"].sum() / 1e6)), include_groups=False).reset_index())
fwd["protected_share_of_committed"] = fwd.protected_musd / fwd.committed_musd
fwd.to_csv(OUT / "forward_commitments.csv", index=False)
say("\n== Forward commitments (contracts signed as of 2026-10-30) ==")
say(fwd.round(3).to_string(index=False))
t5 = fwd[["season", "protected", "nmc", "full", "protected_musd", "committed_musd", "protected_share_of_committed"]].copy()
t5.columns = ["Season", "Protected", "NMC", "Full NTC/NMC", "Protected \\$M", "All committed \\$M", "Protected share"]
for c in ["Protected", "NMC", "Full NTC/NMC"]:
    t5[c] = t5[c].astype(int).astype(str)
latex_table(t5, OUT / "tab_forward.tex", fmt={"Protected \\$M": "{:,.0f}", "All committed \\$M": "{:,.0f}"})

# ======================================================== 5. case-level tests
say("\n== Case dataset ==")
dem = cases[cases.category.isin(["protected_demand", "protected_ambiguous", "protected_disputed", "no_clause_demand"])].copy()
dem["protected"] = ~dem.category.eq("no_clause_demand")
dem["public_from_start"] = dem.public.str.startswith("public")
done = dem[dem.censored == 0]
say("N demand cases:", len(dem), "| resolved:", len(done), "| censored:", int((dem.censored == 1).sum()))
for col in ["pick_value", "realized_gp_3yr", "days_to_trade", "n_first_round_picks"]:
    a = done[done.protected][col].dropna(); b = done[~done.protected][col].dropna()
    say(f"{col}: protected mean={a.mean():.2f} (n={len(a)}) | no-clause mean={b.mean():.2f} (n={len(b)})")
mm = done.dropna(subset=["approved_m_initial"])
for col in ["pick_value", "realized_gp_3yr", "days_to_trade"]:
    q = mm.dropna(subset=[col])
    if len(q) >= 4:
        rho = stats.spearmanr(q.approved_m_initial, q[col])
        say(f"Spearman(approved m, {col}) = {rho[0]:.2f} (p={rho[1]:.2f}, n={len(q)})")
for col in ["pick_value", "realized_gp_3yr", "days_to_trade"]:
    a = done[done.public_from_start][col].dropna(); b = done[~done.public_from_start][col].dropna()
    say(f"{col}: public-from-start mean={a.mean():.2f} (n={len(a)}) | private-first mean={b.mean():.2f} (n={len(b)})")
rho = stats.spearmanr(done.years_left, done.days_to_trade)
say(f"Spearman(years left, days to trade) = {rho[0]:.2f} (p={rho[1]:.2f}, n={len(done)})")

fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.2, 3.0))
for _, r in done.iterrows():
    col = BLUE if r.protected else ORANGE
    if pd.notna(r.approved_m_initial) and pd.notna(r.pick_value):
        a1.scatter(r.approved_m_initial, r.pick_value, color=col, s=16)
        a1.annotate(" ".join(r.player.split()[1:]), (r.approved_m_initial, r.pick_value), fontsize=6, xytext=(3, 2),
                    textcoords="offset points")
    a2.scatter(r.years_left, r.days_to_trade, color=col, s=16, marker="o" if r.protected else "s")
    a2.annotate(" ".join(r.player.split()[1:]), (r.years_left, r.days_to_trade), fontsize=6, xytext=(3, 2), textcoords="offset points")
larkin = dem[dem.censored == 1]
for _, r in larkin.iterrows():
    a2.scatter(r.years_left, r.days_to_trade, facecolors="none", edgecolors=BLUE, s=22)
    a2.annotate(" ".join(r.player.split()[1:]) + " (unresolved)", (r.years_left, r.days_to_trade), fontsize=6, xytext=(3, 2),
                textcoords="offset points")
a1.set_xscale("log"); a1.set_xlabel("Approved destinations at request (log scale)"); a1.set_ylabel("Pick capital (mid-1st equivalents)")
a1.set_title("(a) List size and pick return", fontsize=9)
a2.set_xlabel("Contract years remaining after the request season"); a2.set_ylabel("Days from request to trade")
a2.set_title("(b) Time to resolution", fontsize=9)
from matplotlib.lines import Line2D
a2.legend(handles=[Line2D([], [], marker="o", color=BLUE, ls="", label="Protected"),
                   Line2D([], [], marker="s", color=ORANGE, ls="", label="No clause")], frameon=False, fontsize=7)
fig.tight_layout(); fig.savefig(FIG / "fig_cases.pdf"); plt.close(fig)

tc = cases[["player", "team", "season", "clause", "request_type", "public", "approved_m", "destination",
            "days_to_trade", "n_first_round_picks", "pick_value", "realized_gp_3yr", "verification"]].copy()
tc.to_csv(OUT / "case_table.csv", index=False)
log.close()

# ======================================================== 6. forward figure and case table (LaTeX)
fig, ax = plt.subplots(figsize=(5.5, 3.0))
xx = np.arange(len(fwd))
ax.bar(xx, fwd.protected_musd / 1e3, color=BLUE, label="Under NTC/NMC protection")
ax.bar(xx, (fwd.committed_musd - fwd.protected_musd) / 1e3, bottom=fwd.protected_musd / 1e3, color="#b4cbe6",
       label="Unprotected")
for i, r in fwd.iterrows():
    ax.text(i, r.committed_musd / 1e3 + 0.05, f"{r.protected_share_of_committed:.0%}", ha="center", fontsize=7)
ax.set_xticks(xx); ax.set_xticklabels(fwd.season); ax.set_ylabel("Cap dollars already committed ($bn)")
ax.legend(frameon=False, fontsize=7); fig.tight_layout(); fig.savefig(FIG / "fig_forward.pdf"); plt.close(fig)

ct = cases.copy()
ct["Return"] = ct.apply(lambda r: "" if pd.isna(r.pick_value) else f"{r.pick_value:.2f}", axis=1)
ct["GP3"] = ct.realized_gp_3yr.map(lambda v: "" if pd.isna(v) else f"{int(v)}")
ct["Days"] = ct.apply(lambda r: "" if pd.isna(r.days_to_trade) else (f"{int(r.days_to_trade)}" + ("+" if r.censored == 1 else "")), axis=1)
ct["m"] = ct.approved_m.fillna("").astype(str).str.replace("->", r"$\to$", regex=False)
ct["Clause"] = ct.clause.str.replace(r" \(.*\)", "", regex=True).str.replace("none in effect", "none").str.replace("none", "none")
ct["Dest."] = ct.destination.str.replace(r" \(.*", "", regex=True).str.replace("UNRESOLVED", "unresolved")
lines = [r"\begin{tabular}{llllrrrrl}\toprule",
         r"Player & Team & Season & Clause & $m$ & Days & Picks & GP3 & Outcome\\\midrule"]
for _, r in ct.iterrows():
    lines.append(" & ".join([r.player, r.team, r.season, r.Clause.replace("_", " "), r.m, r.Days, r.Return, r.GP3,
                             r["Dest."].replace("&", r"\&")]) + r"\\")
lines.append(r"\bottomrule\end{tabular}")
open(OUT / "tab_cases.tex", "w").write("\n".join(lines))
