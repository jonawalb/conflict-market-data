"""Go/no-go: how many contract ladders can actually support hazard estimation?"""
import sqlite3, re, collections, os, datetime as dt

DB = os.path.expanduser("~/Library/Application Support/BettingOnWar/bow_market_data.sqlite")
c = sqlite3.connect(DB); c.row_factory = sqlite3.Row

DEADLINE = re.compile(
    r"\b(by|before|through|continues through|in)\s+"
    r"((?:january|february|march|april|may|june|july|august|september|october|november|december)"
    r"\s+\d{1,2}(?:,?\s*\d{4})?|end of \w+|\d{4}|q[1-4]\s*\d{4})", re.I)

def stem(q):
    """Strip the deadline clause to recover the event family."""
    s = DEADLINE.sub("", q or "")
    s = re.sub(r"\b(by|before|through)\b.*$", "", s, flags=re.I)
    s = re.sub(r"[\?\.,]+$", "", s).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

rows = c.execute("""SELECT market_id, question, end_date, closed, volume_num, condition_id FROM markets WHERE escalation=1""").fetchall()

fam = collections.defaultdict(list)
for r in rows:
    k = stem(r["question"])
    if len(k) > 10:
        fam[k].append(r)

# price availability per market
have_price = {r[0] for r in c.execute("SELECT DISTINCT market_id FROM prices")}
have_trade = {r[0] for r in c.execute("SELECT DISTINCT market_id FROM trades")}

def rungs_with_distinct_deadlines(ms):
    return len({(m["end_date"] or "")[:10] for m in ms if m["end_date"]})

ladders = []
for k, ms in fam.items():
    nd = rungs_with_distinct_deadlines(ms)
    if nd >= 3:
        ladders.append((k, ms, nd))
ladders.sort(key=lambda x: -sum(m["volume_num"] or 0 for m in x[1]))

print(f"LADDER FAMILIES (>=3 distinct deadlines): {len(ladders)}")
print(f"  markets inside them: {sum(len(m) for _,m,_ in ladders)}")
print()

def bucket(ms):
    res = sum(1 for m in ms if m["closed"])
    return res, len(ms) - res

tot_resolved_fam = 0
print(f"{'rungs':>5} {'resolved':>9} {'open':>5} {'volume':>14} {'w/price':>8} {'w/trade':>8}  family")
for k, ms, nd in ladders[:18]:
    res, opn = bucket(ms)
    vol = sum(m["volume_num"] or 0 for m in ms)
    wp = sum(1 for m in ms if m["market_id"] in have_price)
    wt = sum(1 for m in ms if m["market_id"] in have_trade)
    if res >= 3: tot_resolved_fam += 1
    print(f"{nd:>5} {res:>9} {opn:>5} ${vol:>13,.0f} {wp:>8} {wt:>8}  {k[:48]}")

print()
allres = sum(1 for k,ms,nd in ladders if sum(1 for m in ms if m['closed'])>=3)
print(f"families with >=3 RESOLVED rungs (usable for calibration): {allres}")
withprice = sum(1 for k,ms,nd in ladders if sum(1 for m in ms if m['market_id'] in have_price)>=3)
withtrade = sum(1 for k,ms,nd in ladders if sum(1 for m in ms if m['market_id'] in have_trade)>=3)
print(f"families with >=3 rungs having ANY price data collected: {withprice}")
print(f"families with >=3 rungs having ANY trade data collected: {withtrade}")
