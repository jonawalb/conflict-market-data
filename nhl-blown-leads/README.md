# Is 3-1 the most dangerous lead in hockey?

Paper: `paper/main.pdf`. Data: NHL public APIs (collected 2026-10-31).

- `analysis/01_fetch_games.py`: every NHL game since 1917-18, plus goal-by-goal data for 27,230 games from 2005-06 to Oct 2026
- `02_series.py`: 801 best-of-seven playoff series (1939-2026), series-state blow rates vs. coin-flip and strength models
- `03_ingame.py`: 125,106 in-game lead states; danger ranking, same-lead comparisons, momentum test
- `04_extra_tests.py`: effect of scoring level; leads entering the 3rd period
- `05_figures.py`, `06_tables.py`: figures and LaTeX tables

Rebuild: `cd analysis && for s in 02 03 04 05 06; do python3 ${s}_*.py; done` (01 re-downloads, about 30 min).
