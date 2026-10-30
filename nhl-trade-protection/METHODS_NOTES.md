# Methods notes: data gaps, judgment calls, and unverified items

Compiled 2026-10-30. Read this with `paper/main.pdf`, Appendix B.

## Data sources and what was (not) reachable
- **CapWages** (`capwages.com/players/*`) was the only cap-data source the sandbox could reach. PuckPedia, Spotrac, ProSportsTransactions and the Wayback Machine (CapFriendly archive) returned 403 errors or dropped the connection. All 7,971 player slugs in the CapWages sitemap were fetched, and 7,965 returned data. The raw extract is in `data/raw/capwages_players.jsonl.gz`.
- **NHL public API** (`api-web.nhle.com`) supplied season rosters, used only to check coverage.
- **Hockey-Reference** was reachable but not needed. Its transaction pages for these seasons returned 404.
- **Clause flags were not independently audited** contract by contract. They are as reported by CapWages, which inherits much of its history from CapFriendly. Spot checks against news reports for the case players (Kesler, Heatley, Tkachuk, Larkin, Hellebuyck, Nurse and others) matched.

## Coverage (the most important caveat)
- Share of NHL-rostered players matched to a contract: 18% (2005-06), 35% (2006-07), 53% (2007-08), 69% (2008-09), 75% (2009-10), 80-89% (2010-13), 91% (2013-14), and 94-100% from 2014-15 onward.
- **Main analyses therefore use 2013-14 onward** (prevalence, hazards). Season-level trade regressions use 2009-10 to 2025-26 (N=17). Earlier seasons appear in the figures with shading and should not be read as trend evidence. The rise from 2005 to 2012 is partly real and partly a coverage artifact.
- External checks: (i) deadline-day trade counts from CapWages trade IDs vs the NHL Stats Pack: r = 0.96, mean absolute error 1.1, and the inferred deadline date equals the official one in every season; (ii) 2026-27: 306 protected contracts and 120 NMCs here, vs 312 and 111 in Deseret News (Aug 2026); (iii) 2024-25: 272 here vs about 260-267 implied by Toronto Star figures (definitions differ).

## Judgment calls
- **Clause hierarchy:** when a season lists several clauses (for example "M-NTC, NMC"), the strongest is coded: NMC > M-NMC > NTC > M-NTC.
- **List sizes** are parsed from free text using the first "N-team (no-)trade list" mention. Lists that change by season are approximated by the first number.
- **Star** = cap hit of at least 7.5% of the upper limit. **Star trade** = a trade moving at least one star.
- **Trades** count trade IDs that moved at least one player under contract. Pick-only trades are excluded, and three-team trades may appear as two IDs.
- **2012-13 cap:** $70.2M (the full-season-equivalent limit) is used instead of the nominal $60M CBA figure.
- **2027-28 cap:** $113.5M is the May 2026 *projection*. It is used only to scale contracts that start in 2027-28 and for the "45% of projected league-wide cap" statement.
- **Pick value chart** = expected career NHL GP by overall slot, 2005-14 drafts (missing players count as 0 GP), smoothed by a power curve and normalised to pick #16. Unknown future pick slots use defaults: 1st=16, 2nd=48, 3rd=80, 4th=112, 7th=208. The 2005-14 drafts had 30-pick rounds, so second-round defaults are slightly conservative.
- **Realised GP** = games played by incoming players over the trade season and the next two. It is computed only for trades completed by 2023-24.
- **Hedonic model:** statsmodels warns of a rank-deficient design (signing-year fixed effects plus the 2026 dummy in model 3). The protection coefficients are identified, but treat model 3's interaction cautiously (n = 12 contracts signed in 2026-27).

## Case dataset: unverified or conflicting items (see `notes/cases_research.md` for URLs)
- **Tkachuk:** reports conflict on whether a *formal* request was made. The Fourth Period said it "may not have been made"; Staios (June 22) and Tkachuk (June 23) then said it was. The paper reports both and treats timing as disputed. Origin of pick No. 9: unverified.
- **Hughes:** no clause and no formal request. Coded as "anticipated exit / informal protection", not as a clause case. The signal date (2025 offseason) is approximate.
- **Larkin:** unresolved at 2026-10-30, so the spell is censored at 148 days. Fox News reported the full NTC covers only the next two seasons, and the later clause structure is unverified. No specific vetoed deal is documented. A claim that he was traded to NJ is uncorroborated and was *not* used.
- **Hellebuyck:** the private request date is unknown, so days are measured from the public statement (Aug 27). One summary said 4 years remain; CapWages and the contract math give 5.
- **Kessel:** the request is disputed (Rutherford says yes, Kessel says "mistaken"). **Luongo:** the request status is ambiguous (he denied "demanding"). **Heatley:** clause described as both partial and full.
- **Approximate request dates** (flagged in `notes`): Nash, St. Louis, Kesler, Spezza, Kessel, Keith, Karlsson, Laine, S. Jones, Tkachuk, Hughes.
- **From memory, verify before publication:** Iginla's 1st became #28; Duchene's OTT 1st became 2019 #4 (Byram); AAVs for Sundin and St. Louis as coded (CapWages agrees).
- **Not included (insufficient documentation):** Pronger 2006 (no clause documented), Hayes 2023 (no request documented). The agent did not research Carter, O'Reilly, Trouba, E. Kane, Drouin, Murray, Palmieri or Chychrun.
- **Werenski 2026:** the veto of a DAL deal is reported but disputed. It is coded as team-initiated.

## Literature
- No peer-reviewed estimate of NTC/NMC salary effects in the NHL was found. Pedace & Hall (2012, MLB) is the only direct empirical precedent. **Run a final Google Scholar check** for "no-movement clause" and "no-trade clause" hockey before claiming the gap in print; Scholar itself was not queried.
- A few BibTeX details (some end pages and publishers) come from memory and are flagged in `notes/cba_lit_research.md`.

## Not done / possible extensions
- NBA/NFL comparison cases were not coded; the paper uses the NBA only institutionally.
- No causal design for the clause price. The Article 11.8 eligibility threshold (age 27 / 7 accrued seasons) is a promising regression discontinuity.
- GAR/WAR-based return values (Evolving-Hockey, etc.) are paywalled or unreachable, so a games-played proxy was used instead.
