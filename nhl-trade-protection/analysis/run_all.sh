#!/usr/bin/env bash
# Rebuild everything from scratch. Steps 01-02 hit the network (NHL API, CapWages);
# skip them to rebuild from the cached raw files in data/raw/.
set -euo pipefail
cd "$(dirname "$0")"
if [[ "${FETCH:-0}" == "1" ]]; then
  python3 01_fetch_rosters.py
  python3 02_fetch_capwages.py
fi
python3 03_build_panels.py
python3 04_pick_value.py
python3 05_model_numerics.py
python3 06_cases.py
python3 07_analysis.py
cd ../paper && pdflatex -interaction=nonstopmode main.tex >/dev/null && bibtex main >/dev/null \
  && pdflatex -interaction=nonstopmode main.tex >/dev/null && pdflatex -interaction=nonstopmode main.tex >/dev/null
echo "done: paper/main.pdf"
