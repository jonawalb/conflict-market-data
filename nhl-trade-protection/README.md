# Tied Hands, Turned Around: trade protection and trade demands in NHL contracts

Paper, data and code. The paper is `paper/main.pdf` (LaTeX source in `paper/`, bibliography in `paper/references.bib`).

```
data/        clean CSVs (each with a source/notes column or documented in METHODS_NOTES.md)
  clause_prevalence_by_season.csv   Task A
  trades_by_season.csv, trades.csv  Task B
  protected_player_cases.csv        Task D (25 cases, sourced row by row)
  contract_seasons.csv, contracts.csv, player_season_panel.csv, salary_cap_by_season.csv, ...
  raw/        cached CapWages extract and NHL rosters
analysis/    01-07 python scripts; figures/; output/ (tables, regression log)
notes/       research memos with URLs for every case and CBA claim
METHODS_NOTES.md   data gaps, judgment calls, unverified items
```

Rebuild: `bash analysis/run_all.sh` (set `FETCH=1` to re-download). Requires pandas, statsmodels, scipy, matplotlib, and TeX Live.
