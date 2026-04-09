#!/usr/bin/env python3
import sqlite3
conn = sqlite3.connect("/home/ubuntu/projects/data-core/data/klines.db")
cur = conn.cursor()

cur.execute("""
    SELECT symbol, COUNT(*) as total, 
           (MAX(ts) - MIN(ts)) / 86400000.0 as days
    FROM klines
    GROUP BY symbol
    HAVING days < 60
    ORDER BY days ASC
""")
rows = cur.fetchall()
print("=== 数据<60天的币 (%d个) ===" % len(rows))
for sym, total, days in rows:
    print("  %-25s %6.1f days  %8d rows" % (sym, days, total))
conn.close()
