#!/usr/bin/env python3
import sqlite3
conn = sqlite3.connect("/home/ubuntu/projects/data-core/data/klines.db")
cur = conn.cursor()

cur.execute("""
    SELECT symbol, SUM(volume * close) as vol_usdt, COUNT(*) as cnt
    FROM klines 
    WHERE ts > (SELECT MAX(ts) - 86400000 FROM klines)
    GROUP BY symbol
    HAVING cnt >= 1000
    ORDER BY vol_usdt DESC
""")
rows = cur.fetchall()

print("Total symbols with 24h data:", len(rows))
print()

tiers = [
    (1_000_000_000, "1B+"),
    (100_000_000, "100M+"),
    (10_000_000, "10M+"),
    (3_000_000, "3M+"),
    (1_000_000, "1M+"),
    (0, "< 1M"),
]
for threshold, label in tiers:
    count = sum(1 for _, v, _ in rows if v and v >= threshold)
    print("  %8s: %3d symbols" % (label, count))

print()
print("Top 60 by 24h volume:")
for i, (sym, vol, cnt) in enumerate(rows[:60]):
    v = "%15s" % "{:,.0f}".format(vol) if vol else "0"
    print("  %3d. %-20s %s USDT" % (i+1, sym, v))

conn.close()
