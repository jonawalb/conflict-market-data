# HANDOFF: "Tied Hands, Turned Around" (NHL trade protection paper)

This file carries everything from the cloud session (2026-10-30) into a local terminal Claude Code session.

- Repo: `jonawalb/conflict-market-data`
- Branch: `claude/sharp-hamilton-9rv41a`
- Project folder: `nhl-trade-protection/`

The rest of the repo is an unrelated Polymarket collector. Don't touch it, and don't push to `main`.

---

## 0. Paste this into the new terminal session to start

> I'm continuing an academic paper project from another Claude session. Read `nhl-trade-protection/HANDOFF.md` first, then `nhl-trade-protection/METHODS_NOTES.md`. Work only inside `nhl-trade-protection/` on branch `claude/sharp-hamilton-9rv41a`. Start with the "Next steps" list in section 7 of the handoff, items 1 and 2 first. Don't fabricate numbers or facts: mark anything unverifiable as missing and say so. Rebuild with `bash analysis/run_all.sh` and recompile the PDF after each change.

---

## 1. Setup on a local machine

```bash
git clone https://github.com/jonawalb/conflict-market-data.git
cd conflict-market-data
git checkout claude/sharp-hamilton-9rv41a
cd nhl-trade-protection
pip install pandas statsmodels scipy matplotlib
# LaTeX: needs pdflatex + bibtex (TeX Live / MacTeX) with natbib, booktabs, enumitem, xurl, hyperref, xcolor
bash analysis/run_all.sh            # rebuild from cached raw data, about 1 min
FETCH=1 bash analysis/run_all.sh    # re-download everything (NHL API + about 8,000 CapWages pages, about 15-30 min)
```

Output: `paper/main.pdf` (30 pages).

---

## 2. The original brief (author: "Dubya"; condensed but complete)

**Paper target:** about 8,000 words for an IR/game-theory applied sports-labor paper, with a formal model, original empirics and case studies. The author wants it publishable (JQAS or similar) and good enough to post publicly for visibility.

**Origin quote** (author, Oct 29, 2026): *"From an IR standpoint, there's no longer a credible commitment with a long term deal on the player's end. The team with the contract risks having to make major concessions because of the high price tag AND the fact that the other team knows the player wants out so they can rip them off in the deal."*

**Thesis.** An NTC/NMC plus a long deal is supposed to be a mutual commitment. Once a protected player asks out, the clause turns into a weapon against his own team:
1. Commitment problem (Fearon 1995; Powell 2006).
2. Hands-tying, reversed (Schelling 1960; Fearon 1997).
3. Veto player and agenda control (Tsebelis 2002): a narrow list shrinks the buyer set, and the surplus goes to the acquirer.
4. Audience costs (Fearon 1994; Tomz 2007): a public request removes the team's ability to bluff.
5. Sunk costs and recapture (the Larkin/Detroit wait).
6. Contract-design implications.

**Conceptual rule:** asking for a trade does NOT breach the clause. What breaks is the *implicit* commitment. Use "defection from implicit commitment" or "exit demand," never "breaking the clause."

**Ground rules:**
- No fabrication; mark missing data.
- Report conflicting accounts instead of choosing silently. Example: Tkachuk formal vs. not, framed as a typology of formal / informal / public / private / anticipated exit.
- Cite news inline for every case claim.
- Model: intuition first, then the math.

**Requested deliverables (all done in v1):**
- `data/` CSVs with source/notes.
- `analysis/` scripts, figures and regressions.
- `paper/` LaTeX, PDF and BibTeX.
- `METHODS_NOTES.md`.

**Suggested outline (about 8,000 words):**

| Section | Words |
|---|---|
| Intro | 700 |
| Institutions | 800 |
| Theory | 1,200 |
| Model | 1,800 |
| Empirics | 1,500 |
| Cases | 1,400 |
| Implications | 400 |
| Conclusion | 200 |

Appendix: proofs, codebook and case table.

**Literature to engage:**
- **IR:** Schelling, Fearon 94/95/97, Powell 06, Tomz 07, Putnam 88, Tsebelis 02.
- **Economics:** Williamson 79/85, Klein-Crawford-Alchian 78, Rubinstein 82, Hart-Moore, Arkes-Blumer 85.
- **Sports:** Rottenberg 56, Kahn 00, Fort-Quirk 95.
- **CBA:** Article 11.8.

---

## 3. What exists now (file map)

```
nhl-trade-protection/
  HANDOFF.md, README.md, METHODS_NOTES.md (data gaps, judgment calls, unverified items)
  notes/cases_research.md   case research with a URL for every claim (VERIFIED/PARTIAL/UNVERIFIED)
  notes/cba_lit_research.md CBA text quotes, cap table, deadline stats, literature + BibTeX
  data/raw/capwages_players.jsonl.gz   scraped contracts/transactions/NHL stats, 7,965 players
  data/raw/capwages_player_slugs.txt, data/raw/nhl_rosters.csv
  data/contract_seasons.csv     player x contract x season (2005-06 .. 2034-35 committed)
  data/contracts.csv            one row per contract (term, sign year, age, clause flags, list size)
  data/player_season_panel.csv  hazard-model panel (traded in season, clause, age, yrs left, prior pts)
  data/player_stats_nhl.csv     NHL gp/pts by player-season
  data/clause_prevalence_by_season.csv   Task A
  data/trades.csv, trade_players.csv, trades_by_season.csv   Task B (player trades; NO picks)
  data/nhl_deadline_day_official.csv     NHL Stats Pack deadline-day counts (validation)
  data/salary_cap_by_season.csv          incl. 2027-28 PROJECTED 113.5
  data/pick_value_chart.csv              empirical expected career GP by pick (2005-14 drafts)
  data/protected_player_cases.csv        Task D, 25 cases, sourced row by row
  analysis/common.py, 01_fetch_rosters.py, 02_fetch_capwages.py, 03_build_panels.py,
           04_pick_value.py, 05_model_numerics.py, cases_input.py (hand-coded cases), 06_cases.py,
           07_analysis.py, run_all.sh
  analysis/figures/*.pdf, analysis/output/*.tex|csv, analysis/output/analysis_log.txt (all key numbers)
  paper/main.tex, paper/sections/01_intro ... 08_conclusion, appendix.tex, paper/references.bib, paper/main.pdf
```

**Data-access notes from the cloud sandbox** (a local machine may do better):
- **Worked:** CapWages (`capwages.com/players/<slug>`, where the `__NEXT_DATA__` JSON carries per-season clause flags, clause text, trade IDs and stats), the NHL API (`api-web.nhle.com`, which needs a User-Agent header), Hockey-Reference (reachable but unused) and WebSearch/WebFetch.
- **Blocked:**
  - PuckPedia: 403.
  - Spotrac: 403.
  - ProSportsTransactions: 403.
  - Wayback Machine (CapFriendly archive): connection reset.

  Try these locally, since they are needed for next steps 3 and 8.
- **Check before publishing:** CapWages' terms of service on redistributing the raw extract. If needed, remove `data/raw/capwages_players.jsonl.gz` from the public repo and keep only the scripts and aggregates.

---

## 4. Key results in v1 (from `analysis/output/analysis_log.txt`)

**Data validation**
- Roster coverage (share of NHL-rostered players matched to a contract) is 91% in 2013-14 and 94-100% from 2014-15 on. It is 75% in 2009-10 and under 50% before 2007-08.
- The main sample is 2013-14 onward.
- Deadline-day trade counts vs. the official NHL Stats Pack: r = 0.96, mean absolute error 1.1 trades. The inferred deadline date matches the official one in every season.
- 2026-27 counts are 306 protected contracts and 120 NMCs, against Deseret News's independent count of 312 and 111.

**Prevalence**

| Season | Protected contracts | Per team | Share of cap dollars | Share of stars |
|---|---|---|---|---|
| 2013-14 | 158 | 5.3 | 31% | 62% |
| 2026-27 | 306 | 9.6 | 49% | 77% |

- NMCs rose from 68 to 120.
- "Stars" means a cap hit of at least 7.5% of the cap.
- The median modified list blocks 10 teams.

**Forward overhang** (contracts already signed as of 2026-10-30)
- Protected players hold $1.65bn of 2027-28 cap hits, which is 45% of the projected league-wide cap (32 × $113.5M).
- Protected share of committed dollars: 55% in 2027-28, 67% in 2028-29, 75% in 2029-30, 80% in 2030-31. These shares are partly mechanical, since short unprotected deals for those years aren't signed yet.

**Trades**
- About 90-135 player trades per year.
- The share of trades involving a protected player rose from about 15-17% in the 2010s to 29.8% in 2024-25 and 25.7% in 2025-26.
- Correlation with prevalence is 0.91. In the season OLS, the coefficient is 1.34 with p = 0.003, but N = 17.
- Total and star trade volume show no relationship with prevalence once controls are added.

**Trade hazard** (linear probability model, 2013-14 to 2025-26, age ≥ 26, cap hit ≥ 3%, N = 3,947)
- Base traded rate: 19.5% unprotected vs. 10.7% protected.
- Full NTC: −7.1 percentage points. NMC: −8.1 percentage points. Modified NTC: −3.0 (p ≈ 0.05).
- Against a mean of 14%, full protection roughly halves the trade rate.
- Among stars, NMC: −8.2 percentage points.

**Hedonic** (496 new skater contracts, age ≥ 25.5, term ≥ 3, cap hit ≥ 5%)
- Protection carries a +8.6% higher log cap share (SE 0.020). This is a premium, not a discount, and is almost certainly selection.
- The premium sits with full NTC/NMC (+9.7%).
- No 2026-27 change: the interaction is 0.008 (n = 12).

**Signing cohorts** (share of new deals with a full NTC/NMC at some point)
- 45-50% in 2009-13, 74% in 2024-25, 82% in 2025-26.
- The new CBA (effective 2026-09-16) shows no retreat yet, with tiny n.

**Model** (illustrative only, NOT calibrated: Uniform[0,1], R = 0.20 private / 0.05 public, β = 0.5)
- Return to the seller by number of approved teams m:

  | m | Return |
  |---|---|
  | 1 | 0.36 |
  | 3 | 0.51 |
  | 21 (a 10-team no-trade list) | 0.909 |
  | 31 (open market) | 0.937 |

- Loss from going public: 0.084 at m = 1, 0.035 at m = 2, 0.007 at m = 3, and 0 for m ≥ 5. Publicity and veto scope are complements.

**Cases** (20 demand cases, 19 resolved, Larkin censored at 148 days)
- Protected vs. no-clause returns: pick capital 0.84 vs. 1.27 mid-first-round equivalents; games played by returned players over 3 seasons 263 vs. 354.
- Public-from-start vs. private-first: pick capital 0.44 vs. 1.48 (supports the audience-cost proposition), but returned players' games go the other way, 309 vs. 256.
- Spearman correlation of list size with pick capital is 0.07 (n = 11), and with returned players' games 0.63 (p = 0.10, n = 8).
- Tkachuk (4-team list, best pick package) is an outlier against the model.

---

## 5. Verified case facts as of 2026-10-30 (sources in `notes/cases_research.md` and `paper/references.bib`)

**Hellebuyck (WPG → NJD)**
- Traded Oct 23, 2026 for Dawson Mercer, Nick Bjugstad, G Nico Daws, the rights to Alexander Command, a 2029 2nd and a 2027 7th.
- NMC, $8.5M through 2030-31.
- Private request early in the offseason; public Aug 27 via his agent's X account.
- Suspended Sept 16 for not reporting to camp. Buffalo talks failed.
- Verified with NHL.com, TSN and CP24.

**Larkin (DET)**
- Not traded.
- Requested June 4, 2026 (Friedman). NTC, $8.7M through 2030-31, an 8-year/$69.6M deal from 2023.
- List grew from MIN/FLA/VGK to 10 teams by about Oct 20 (Daily Faceoff), adding DAL, COL, TB, WSH, CAR, NJ and UTA.
- Yzerman removed July 15. Horcoff named interim GM Sept 15.
- Stripped of the captaincy Sept 11. Placed on non-roster IR and booed at the Oct 2 opener.
- Suspended without pay Oct 29 (ClickOnDetroit).
- A rumor of a trade to NJ is UNVERIFIED and was not used.

**Hughes (VAN → MIN, Dec 12, 2025)**
- Return: Rossi, Buium, Öhgren and a 2026 1st.
- No clause and no formal request; he told Vancouver he wouldn't re-sign. Agent: Vancouver acted "as if we had a no-trade clause, almost."
- No extension as of Oct 26, 2026. $7.85M through 2026-27.

**Tkachuk (OTT → FLA, June 21, 2026)**
- Return: No. 9 and No. 25 (2026), a top-10-protected 2029 1st and a 2027 2nd.
- NMC, $8.214M, 2 years left. 4-team list: FLA, MIN, VGK, CAR.
- Dispute: The Fourth Period said a formal request "may not have been made." Staios (June 22) and Tkachuk (June 23) then confirmed a request. The timing remains disputed.
- Staios quotes: "the dynamic of a no-trade clause changes how the transaction works"; "pointed at one team at the end"; "not like he was available to every team."
- No extension; not eligible until July 1, 2027.

**Nurse (EDM → SJS, July 1, 2026)**
- Return: Mukhamadullin and Zack Sharp.
- NMC, $9.25M. Initial list PIT, PHI and BOS, later adding SJ.

**Werenski (CBJ)**
- Reportedly vetoed a deal to Dallas around July 1, 2026. It was team-initiated with no formal request (PARTIAL).

**Olympics**
- USA beat Canada 2-1 in OT on Feb 22, 2026.
- Hellebuyck, Hughes, Tkachuk and Larkin were all on the roster.

**Bettman and the NHLPA**
- Bettman, Oct 1, 2026: "we've gotten past the way no-trade clauses were originally intended."
- NHLPA (Hainsey): "an attack on players' CBA-negotiated rights."

**CBA facts**
- **Article 11.8:** clause allowed for Group 3 UFA eligibility (7 accrued seasons or age 27). A clause may be signed earlier but takes effect only at eligibility. A clause not yet in effect doesn't bind an acquirer unless it agrees in writing; one in effect stays with the contract after a trade. An NMC blocks trade, loan and waivers, but not buyouts.
- **Expansion drafts:** NMC players had to be protected in 2017 and 2021.
- **Contract term limits:** 2005 CBA none; 2013 CBA 8 years (own player) / 7; 2025 CBA 7/6, effective **Sept 16, 2026**, not July 1. Teams rushed 8-year deals through Sept 15.
- **2025 CBA also:** 84-game season from 2026-27, playoff cap compliance, and a ban on double retention.
- **Cap table:** in `data/salary_cap_by_season.csv`. For 2012-13 the code uses $70.2M (the full-season equivalent); the nominal limit was $60M.

**Historical cases** (all in `data/protected_player_cases.csv` with URLs and flags)
- Heatley 2009: vetoed EDM, traded to SJ.
- Nash 2012 → NYR.
- St. Louis 2014: NYR only.
- Kesler 2014: list of CHI and ANA → ANA.
- Spezza 2014: 10-team no-trade list, blocked NSH → DAL.
- Luongo 2012-14: ambiguous request → FLA.
- Kessel 2019: request disputed, vetoed MIN → ARI.
- Tarasenko 2021-23: 625 days → NYR.
- Keith 2021 → EDM.
- Karlsson 2023 → PIT.
- Laine 2024 → MTL.
- Seth Jones 2025 → FLA.
- Marner 2025: refused to waive, then sign-and-trade to VGK.
- Sundin 2008: refused to waive.
- Iginla 2013 and Kane 2023: chose their destinations.
- No-clause comparisons: Duchene, Dubois, Eichel and Hughes.

**From memory, still to verify:**
- Iginla's 2013 1st became #28.
- Duchene's Ottawa 1st became the 2019 #4 (Byram).
- The Cheveldayoff "a contract is a contract" line may be a headline, not a direct quote.
- Luongo's Florida-only stance rests on a single source.

**Literature gap:**
- Pedace & Hall (2012, *Industrial Relations*, MLB) is the only empirical NTC paper found. There is no NHL estimate.
- Adjacent work: Kahane 2024 (*Econ Letters*), Fumarco et al. 2024 (*OEP*), Marburger 2009 (*JSE*), and Gerstle 2025 (a student note on NBA no-trade clauses).
- Google Scholar was NOT queried; do that before claiming the gap.

---

## 6. Referee-style review of v1 (summary)

**Verdict:** major revision at JQAS or *Journal of Sports Economics*; a reject-and-resubmit at a top journal.

**Major problems:**
1. **No identification.** The hazard and hedonic results are confounded by selection: teams give clauses to players they plan to keep and to players with leverage.
2. **The core mechanism is barely tested.** The list-size vs. return evidence rests on about 11-19 cases with crude returns:
   - Default pick slots.
   - Games played instead of a quality measure.
   - No normalization by what was given up.
   - The Tkachuk outlier.
3. **The case sample is selected on the dependent variable.** It came from memory plus research; it needs a systematic request census with a coding protocol and a second coder.
4. **The theory is thin.**
   - Propositions 1-2 are textbook order statistics.
   - The m=1 vs. m≥2 split is an artifact of bolting Nash bargaining onto the one-buyer case.
   - The list is exogenous, and list choice is not correlated with valuations.
   - "Bluffing" is invoked without private information.
   - Delay should be modeled as a war of attrition.
   - The IR framing is mostly analogy unless it yields a distinct prediction.
5. **The "calibrated" 3% / half claim is NOT calibrated.** Relabel it as illustrative or estimate the valuation distribution.
6. **Mechanical headlines.**
   - The protected-trade share just tracks prevalence; the per-protected-player trade rate over time is the real test.
   - The overhang needs a historical benchmark: the same statistic as of each past October.
7. **Season regression (N = 17, 6 covariates) is uninformative.** Cut it or move it to an appendix.
8. **Data audit needed.**
   - Check a random ~100 contract-seasons against PuckPedia or CapFriendly.
   - Handle retained salary, buyouts, and three-team trades that appear as two IDs.

**Minor problems:**
- About 13k words against an 8k target; pick a venue and cut.
- The abstract oversells.
- JEL codes and a robustness table are missing.
- The war-of-attrition literature (Alesina-Drazen 1991) and holdout literature are missing.
- Check the CapWages terms of service.
- Use the real author name for submission.

---

## 7. Next steps (prioritized)

1. **Within-contract event study (data in hand).**
   - Use clause activation dates (signed pre-eligibility, effective later) and NMC→M-NTC conversions inside the same contract.
   - Estimate trade hazard before vs. after, with contract fixed effects.
   - Replaces the confounded hazard regression as the main causal result.
2. **Per-protected-player trade rate by season, plus a historical overhang benchmark (data in hand).**
   - Is the protected-player hazard rising in 2023-26? That is the "reversal" signature and the best potential headline.
   - For each past October, compute the share of next-season league cap committed to protected players.
3. **All star trades with picks.**
   - CapWages trade logs lack picks. Get them from the PuckPedia trade tracker, nhltradetracker.com or Wikipedia "20XX–YY NHL transactions" pages; download manually if blocked.
   - Value both sides with a quality metric (GAR/WAR or contract surplus) and normalize by the outgoing player.
   - Compare protected vs. unprotected sellers. This is the real test of the veto mechanism.
4. **Regression discontinuity at Article 11.8 eligibility** (age 27 / 7 accrued seasons) for the price of a clause.
5. **Systematic census of trade requests, 2005-2026.**
   - Documented search protocol, inclusion rules and a double-coded subsample.
   - Enables the contagion test (Proposition 5), which is untested now.
6. **Theory rework.**
   - Endogenous list choice by the player.
   - Valuations correlated with list membership (could explain Tkachuk).
   - A signaling version of the publicity choice.
   - A war-of-attrition model of delay (Larkin, with the suspension as a cost-raising move).
7. **Quick fixes.**
   - Relabel the "calibrated example" as illustrative in the abstract, intro and model.
   - Cut or move the season regressions.
   - Add a robustness table: star cutoffs, an alternative pick chart, dropping goalies.
   - Add JEL codes.
   - Trim toward the chosen venue (JQAS: data and identification first, IR compressed).
8. **Verification.**
   - Audit clause flags.
   - Verify the "from memory" facts in section 5.
   - Run a Google Scholar literature check.
   - Recheck the Larkin status, since it may change any day.

**Housekeeping:**
- Commit with clear messages.
- Never push to `main`.
- Do not include model identifiers in commits or files.
- Recompile `paper/main.pdf` after each change.
