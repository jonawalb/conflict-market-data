"""Build contract-season, trade, and season-level panels from the CapWages cache.

Outputs (data/):
  contract_seasons.csv           one row per player x contract x season
  trades.csv                     one row per trade (tradeId), players involved
  clause_prevalence_by_season.csv
  trades_by_season.csv
  player_season_panel.csv        player-season panel for the trade-hazard models
"""
import collections
import pandas as pd
from common import (DATA, load_players, money, parse_date, season_of, classify_clause,
                    parse_list, season_start)

FIRST, LAST = "2005-06", "2026-27"
LAST_COMMITTED = "2034-35"     # keep future seasons of signed contracts for the forward look
SEASONS = [f"{y}-{str(y + 1)[-2:]}" for y in range(2005, 2027)]

cap = pd.read_csv(DATA / "salary_cap_by_season.csv").set_index("season")

cs_rows, tx_rows, stat_rows = [], [], []
for p in load_players():
    pid = p.get("nhlId")
    for ci, c in enumerate(p.get("contracts") or []):
        signed = parse_date(c.get("signingDate"))
        ltype, ln = parse_list(c.get("clauseDetails"))
        for d in c.get("details") or []:
            s = d.get("season")
            if not s or s < FIRST or s > LAST_COMMITTED:
                continue
            cs_rows.append(dict(
                slug=p["slug"], name=p["name"], nhl_id=pid, pos=p.get("pos"), born=p.get("born"),
                contract_idx=ci, contract_type=(c.get("type") or "").strip(),
                signing_team=c.get("signingTeam"), signing_date=signed,
                length=c.get("length"), total_value=money(c.get("value")),
                season=s, cap_hit=money(d.get("capHit")), clause_raw=d.get("clause") or "",
                clause=classify_clause(d.get("clause")), clause_details=c.get("clauseDetails") or "",
                list_type=ltype, list_n=ln))
    for t in p.get("transactions") or []:
        if t.get("type") == "TRADE" and t.get("tradeId"):
            dt = parse_date(t.get("date"))
            tx_rows.append(dict(trade_id=t["tradeId"], date=dt, slug=p["slug"], name=p["name"],
                                nhl_id=pid, to_team=t.get("team"), details=t.get("details")))
    for st in p.get("stats") or []:
        stat_rows.append(dict(slug=p["slug"], **st))

cs = pd.DataFrame(cs_rows)
# A player can hold two contracts listing the same season (rare data overlaps); keep the
# one with the latest signing date.
cs = (cs.sort_values(["slug", "season", "signing_date"])
        .drop_duplicates(["slug", "season"], keep="last"))
cs["cap_upper"] = cs.season.map(cap.cap_upper_limit_musd) * 1e6
cs["cap_pct"] = cs.cap_hit / cs.cap_upper
cs["protected"] = cs.clause != "none"
cs["full"] = cs.clause.isin(["NTC", "NMC"])
cs["nmc_any"] = cs.clause.isin(["NMC", "M-NMC"])
# Rank cap hits within each signing-era season to define a "core" denominator:
# the league's top (10 x number of teams) cap hits in each season.
cs_all = cs.copy()
cs = cs[cs.season <= LAST].copy()
cs["rank_in_season"] = cs.groupby("season").cap_hit.rank(ascending=False, method="first")
cs["core"] = cs.rank_in_season <= cs.season.map(cap.teams) * 10
cs_all.to_csv(DATA / "contract_seasons.csv", index=False)

# ---------------------------------------------------------------- one row per contract
born_all = pd.to_datetime(cs_all.born, format="%b. %d, %Y", errors="coerce")
cs_all["born_dt"] = born_all
con = (cs_all.sort_values("season").groupby(["slug", "contract_idx"])
       .agg(name=("name", "first"), pos=("pos", "first"), born_dt=("born_dt", "first"),
            contract_type=("contract_type", "first"), signing_team=("signing_team", "first"),
            signing_date=("signing_date", "first"), length=("length", "first"),
            first_season=("season", "min"), last_season=("season", "max"),
            cap_hit=("cap_hit", "first"), any_prot=("protected", "max"), any_full=("full", "max"),
            any_nmc=("nmc_any", "max"), first_clause=("clause", "first"),
            clause_details=("clause_details", "first"), list_type=("list_type", "first"), list_n=("list_n", "first"))
       .reset_index())
con["term"] = pd.to_numeric(con.length.astype(str).str.extract(r"(\d+)")[0], errors="coerce")
sd = pd.to_datetime(con.signing_date, errors="coerce")
con["sign_year"] = [season_of(d.date()) if pd.notna(d) else None for d in sd]
con["age_at_signing"] = (sd - con.born_dt).dt.days / 365.25
con["cap_pct"] = con.cap_hit / (con.first_season.map(cap.cap_upper_limit_musd) * 1e6)
con.drop(columns="born_dt").to_csv(DATA / "contracts.csv", index=False)

# ---------------------------------------------------------------- prevalence
def agg(g):
    core = g[g.core]
    nhl = g[g.cap_hit >= 1.0e6]
    star = g[g.cap_pct >= 0.075]
    out = dict(
        contracts=len(g), contracts_ge_1m=len(nhl),
        n_protected=int(g.protected.sum()),
        n_NMC=int((g.clause == "NMC").sum()), n_M_NMC=int((g.clause == "M-NMC").sum()),
        n_NTC=int((g.clause == "NTC").sum()), n_M_NTC=int((g.clause == "M-NTC").sum()),
        share_protected_ge_1m=nhl.protected.mean(),
        share_full_ge_1m=nhl.full.mean(),
        share_protected_core=core.protected.mean(),
        share_full_core=core.full.mean(),
        share_nmc_core=core.nmc_any.mean(),
        n_star_contracts=len(star),
        share_protected_star=star.protected.mean() if len(star) else float("nan"),
        protected_cap_dollars_musd=g.loc[g.protected, "cap_hit"].sum() / 1e6,
        total_cap_dollars_musd=g.cap_hit.sum() / 1e6,
        median_block_list_n=g.loc[(g.clause == "M-NTC") & (g.list_type == "block"), "list_n"].median(),
        median_approve_list_n=g.loc[(g.clause == "M-NTC") & (g.list_type == "approve"), "list_n"].median(),
        n_mntc_list_parsed=int(((g.clause == "M-NTC") & g.list_n.notna()).sum()),
    )
    return pd.Series(out)

prev = cs.groupby("season").apply(agg, include_groups=False).reset_index()
prev = prev.merge(cap[["cap_upper_limit_musd", "teams"]], left_on="season", right_index=True)
prev["protected_per_team"] = prev.n_protected / prev.teams
prev["share_cap_dollars_protected"] = prev.protected_cap_dollars_musd / prev.total_cap_dollars_musd
prev["share_league_cap_protected"] = prev.protected_cap_dollars_musd / (prev.cap_upper_limit_musd * prev.teams)
prev["source"] = "CapWages player contract pages (capwages.com/players/*), scraped 2026-10-30; see METHODS_NOTES.md"
prev.to_csv(DATA / "clause_prevalence_by_season.csv", index=False)

# ---------------------------------------------------------------- trades
tx = pd.DataFrame(tx_rows).dropna(subset=["date"])
tx["season"] = tx.date.map(season_of)
tx = tx[(tx.season >= FIRST) & (tx.season <= LAST)]
tx = tx.drop_duplicates(["trade_id", "slug"])
key = cs.set_index(["slug", "season"])
def lookup(r):
    try:
        k = key.loc[(r.slug, r.season)]
        return pd.Series(dict(clause=k.clause, cap_hit=k.cap_hit, cap_pct=k.cap_pct))
    except KeyError:
        return pd.Series(dict(clause="none", cap_hit=None, cap_pct=None))
tx = pd.concat([tx.reset_index(drop=True), tx.apply(lookup, axis=1).reset_index(drop=True)], axis=1)
tx["protected"] = tx.clause != "none"
tx.to_csv(DATA / "trade_players.csv", index=False)

tr = (tx.groupby("trade_id")
        .agg(date=("date", "first"), season=("season", "first"), n_players=("slug", "nunique"),
             players=("name", lambda s: "; ".join(sorted(set(s)))),
             any_protected=("protected", "any"), max_cap_pct=("cap_pct", "max"),
             protected_players=("name", lambda s: "; ".join(sorted(set(s[tx.loc[s.index, "protected"]])))))
        .reset_index())
tr["teams"] = tr.trade_id.str.split("-").str[3:].str.join("-")
tr["star"] = tr.max_cap_pct >= 0.075
# Deadline day: the single date in Feb-Apr with the most trades in each season.
late = tr[pd.to_datetime(tr.date).dt.month.isin([2, 3, 4])]
dl = late.groupby(["season", "date"]).size().reset_index(name="n").sort_values("n").groupby("season").tail(1)
deadline = dict(zip(dl.season, dl.date))
tr["deadline_day"] = tr.apply(lambda r: deadline.get(r.season) == r.date, axis=1)
tr.to_csv(DATA / "trades.csv", index=False)

tbs = (tr.groupby("season")
         .agg(trades=("trade_id", "size"), trades_with_protected=("any_protected", "sum"),
              star_trades=("star", "sum"), deadline_day_trades=("deadline_day", "sum"),
              players_moved=("n_players", "sum"))
         .reset_index())
tbs["deadline_date_inferred"] = tbs.season.map(deadline)
tbs["share_trades_with_protected"] = tbs.trades_with_protected / tbs.trades
tbs["source"] = ("CapWages player transaction logs (TRADE entries with tradeId); counts trades that moved "
                 ">=1 player under contract; pick-only trades are not captured")
tbs.to_csv(DATA / "trades_by_season.csv", index=False)

# ---------------------------------------------------------------- player-season panel
traded = tx.groupby(["slug", "season"]).size().rename("n_trades").reset_index()
ps = cs.merge(traded, on=["slug", "season"], how="left")
ps["traded"] = ps.n_trades.fillna(0).gt(0).astype(int)
st = pd.DataFrame(stat_rows)
if len(st):
    st["gp"] = pd.to_numeric(st.gp, errors="coerce"); st["p"] = pd.to_numeric(st.p, errors="coerce")
    st = st[st.season.str.match(r"^\d{4}-\d{2}$", na=False)]
    st = st.groupby(["slug", "season"]).agg(gp=("gp", "sum"), pts=("p", "sum")).reset_index()
    st.to_csv(DATA / "player_stats_nhl.csv", index=False)
    st["season_next"] = st.season.map(lambda s: f"{int(s[:4]) + 1}-{str(int(s[:4]) + 2)[-2:]}")
    prev_st = st.rename(columns={"gp": "gp_prev", "pts": "pts_prev"})[["slug", "season_next", "gp_prev", "pts_prev"]]
    ps = ps.merge(prev_st, left_on=["slug", "season"], right_on=["slug", "season_next"], how="left").drop(columns="season_next")
born = pd.to_datetime(ps.born, format="%b. %d, %Y", errors="coerce")
# age on Sept 15 of the season's first year
ps["age"] = (pd.to_datetime(ps.season.str[:4] + "-09-15") - born).dt.days / 365.25
# years remaining on the contract after this season
last_season = cs.groupby(["slug", "contract_idx"]).season.max().rename("contract_last_season")
ps = ps.merge(last_season, left_on=["slug", "contract_idx"], right_index=True, how="left")
ps["years_left"] = ps.contract_last_season.map(season_start) - ps.season.map(season_start)
ps.to_csv(DATA / "player_season_panel.csv", index=False)
print(prev[["season", "contracts_ge_1m", "n_protected", "share_protected_core", "share_full_core"]].to_string())
print(tbs.to_string())
