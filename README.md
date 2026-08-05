# Polymarket conflict-market collector

Continuously archives geopolitical prediction-market data from Polymarket that
**the platform stops serving once a contract resolves**. Runs unattended on
GitHub Actions; no server, no API keys, no paid services.

Built to support research on whether prediction-market prices can be turned into
a usable measure of *when* a conflict is likely to begin, not merely whether.

---

## Why this exists

Three properties of the Polymarket API, each verified directly against live
endpoints, together determine the whole design:

| Endpoint | Survives resolution? | Consequence |
|---|---|---|
| `clob/prices-history` | **No** — returns `{"history":[]}` | Price paths for closed markets are unrecoverable from the API |
| `data-api/trades` | Yes, but capped near 10,000 most recent trades | Heavy markets lose their early tape between runs |
| `clob/book` | **Never served historically at all** | Order book depth exists only if snapshotted live |

A contract that traded $16.9M returns zero price points once it resolves. Order
book depth — the direct empirical analogue of how many dollars it takes to move
a price — has no historical endpoint in any form. Every hour this is not
running is an hour permanently lost.

## What it captures

- **Prices** — minute-level bars for the last 24h plus hourly bars back to market inception
- **Trades** — every fill with second-resolution timestamp, price, size, side, and the pseudonymous wallet that placed it
- **Order books** — best bid/ask, spread, and depth within 1c/5c/10c of the mid
- **Registry** — the tracked market universe with an escalation classifier

## How it runs

| Workflow | Schedule | Purpose |
|---|---|---|
| `collect-books` | hourly | Order book snapshots — the irrecoverable data |
| `collect-hot`   | hourly | Full collection on the top-volume markets, so the ~10k trade cap never binds during a crisis |
| `collect-full`  | 4×/day, sharded | Prices and the trade tape |
| `refresh-registry` | daily | Rediscover and reclassify the market universe |
| `tests` | on push | Classifier and round-trip regression tests |

Runners have no persistent disk, so each run rebuilds just enough state from the
repository — the registry and a per-market high-water mark — collects into a
throwaway database, and commits a compressed increment. The full database is
never committed.

```
data/
  registry/markets.json.gz   poll list (byte-stable; only commits on real change)
  state/high_water.json      newest timestamp already captured, per market
  increments/YYYY/MM/*.jsonl.gz   append-only observations
```

## Using the data

```bash
git clone <this repo> && cd <repo>
python3 scripts/rebuild_db.py            # increments -> bow_market_data.sqlite
```

Increments are append-only and may overlap. Every writer uses `INSERT OR IGNORE`
against a natural key, so replaying them in any order converges on the same
database — which is what makes it safe to run this from CI and a laptop at once.
`test_roundtrip.py` asserts that property on every push.

```sql
-- wallets that moved the most money, across markets
SELECT wallet, COUNT(*) trades, COUNT(DISTINCT market_id) markets,
       ROUND(SUM(size * price)) notional
FROM trades GROUP BY wallet ORDER BY notional DESC LIMIT 20;

-- contract ladders: same event, several deadlines
SELECT question, end_date, volume_num FROM markets
WHERE escalation = 1 AND question LIKE '%strike Iran%' ORDER BY end_date;
```

## Auditing what was actually captured

```bash
python3 scripts/coverage_report.py --days 7
```

Scheduled jobs do not run on time — Actions delays or drops cron runs under
load, laptops sleep, networks fail. The result is an irregularly sampled
series, which matters because order book depth enters downstream estimation as
a covariate. This reports observed sampling intervals, worst gaps per market,
and how close the busiest markets are to the trade cap, so ragged sampling can
be stated as a measured property rather than an unexamined assumption.

## Running locally

```bash
python3 bow_collect.py status     # database summary
python3 bow_collect.py book       # order books only (~3 min)
python3 bow_collect.py full       # everything (~2-3 h on first run)
python3 test_classify.py && python3 test_roundtrip.py
```

Local runs store the database at `~/Library/Application Support/BettingOnWar/`
(override with `BOW_DATA_DIR`) — deliberately outside any cloud-synced folder,
since SQLite in WAL mode on a syncing volume can corrupt.

Anything collected locally exists only on that machine until exported:

```bash
python3 scripts/export_local.py --skip-raw-books   # SQLite -> increments
```

**Do not run the launchd agents from a checkout in `~/Desktop`, `~/Documents`,
or `~/Downloads`.** macOS TCC denies launchd access to those directories and
every run dies with `Operation not permitted` — visibly only in the agent's
stderr file, so it looks like nothing is happening at all. `deploy/install-launchd.sh`
installs against whatever directory it is run from; clone somewhere unprotected
(`~/Library/Application Support/BettingOnWar/collector` works) and install from
there. `run_collect.sh` fast-forwards that clone before each run, so it tracks
the repository instead of silently drifting.

The optional launchd agents in `deploy/` run the same collector on a Mac. They
are redundant with the GitHub Actions workflows; use one or the other unless you
want the belt-and-braces of both writing to the same increment format.

## Cost and growth

No API keys and no paid services — every endpoint is public and unauthenticated.

Book increments run ~25 KB/hour (~220 MB/year). Trade volume varies with market
activity. When the repository approaches ~1 GB, consolidate a year of increments
into a GitHub Release asset and prune; the rebuild script accepts `--since` so
partial histories still work.

Actions minutes are unlimited on public repositories. On a private repository
the hourly schedule alone would exceed the 2,000 free minutes per month.

## Known limits

Document these in any analysis that uses the data.

- **Left-censoring.** Collection began 2026-08-05. Markets that closed earlier have no minute-level price history and at most their final ~10,000 trades. Earlier history exists on-chain and can be reconstructed separately; it is not in this repository.
- **Trade cap.** Markets turning over more than ~10,000 trades between runs lose the middle of their tape. The `runs` table records coverage so this is auditable rather than invisible.
- **`escalation` is a candidate flag, not a validated panel.** It is regex over question and event title, tested against 21 cases including real false positives ("Warnock" matching `war`; airline strikes matching `strike`). Hand-validate before analysis and report precision.
- **Wallets are pseudonymous, not anonymous.** A `proxyWallet` is stable across markets and time. Treat any linkage work as a human-subjects question first.
- **Scheduled workflows can be disabled.** GitHub disables them after 60 days without repository activity, and commits from the Actions token do not reliably reset that timer. Push a manual commit monthly, or run the workflows from a personal access token instead of `GITHUB_TOKEN`. This fails silently — if collection goes quiet, check the Actions tab first.
- **Cron is not punctual.** Scheduled runs are routinely late by 10-60 minutes and are sometimes dropped. Treat the series as irregularly sampled and quantify it with `scripts/coverage_report.py`.
- **Selection into listing.** These markets exist only where someone expected trading volume, so quiet crises are invisible. This bounds what any measure built on them can claim.

## Layout

```
bow/                package: config, api, db, discover, collect
bow_collect.py      local CLI
scripts/
  ci_collect.py     stateless CI entry point
  rebuild_db.py     increments -> SQLite
  ladder_check.py   contract-ladder viability report
  coverage_report.py  observed sampling cadence and gap audit
  export_local.py   local SQLite -> repository increments
  consolidate.py    bundle old months into a Release when the repo grows
test_classify.py    classifier + book summariser regression tests
test_roundtrip.py   export -> rebuild losslessness and idempotence
deploy/             optional macOS launchd agents
.github/workflows/  scheduled collection
```

## Licence

MIT for the code. Collected data is derived from public Polymarket endpoints;
check their terms before redistributing it.
