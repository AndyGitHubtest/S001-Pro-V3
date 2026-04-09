#!/usr/bin/env python3
"""Quick check of klines DB data characteristics"""
import sqlite3

conn = sqlite3.connect('/home/ubuntu/projects/data-core/data/klines.db')
cur = conn.cursor()

# Check intervals
cur.execute("SELECT DISTINCT interval FROM klines LIMIT 10")
print("Intervals:", [r[0] for r in cur.fetchall()])

# Raw sample
cur.execute("SELECT symbol, interval, ts, open, high, low, close, volume FROM klines ORDER BY ts DESC LIMIT 5")
print("\nSample rows:")
for r in cur.fetchall():
    print(f"  {r[0]:20s} | int={r[1]} | C={r[6]} | V={r[7]}")

# Volume for key symbols
print("\n=== 24h SUM(volume) ===")
for sym in ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT"]:
    cur.execute("""SELECT COALESCE(SUM(volume),0), COUNT(*) FROM klines 
                   WHERE symbol=? AND interval='1m' 
                   AND ts > (SELECT MAX(ts) - 86400000 FROM klines)""", (sym,))
    row = cur.fetchone()
    print(f"  {sym:15s}: sum_vol={row[0]:>15,.0f}  rows={row[1]}")

# Check if volume is in base or quote currency
print("\n=== Volume unit check (BTC last 3 rows) ===")
cur.execute("""SELECT volume, close, volume * close as vol_usdt 
               FROM klines WHERE symbol='BTC/USDT' AND interval='1m' 
               ORDER BY ts DESC LIMIT 3""")
for r in cur.fetchall():
    print(f"  vol_raw={r[0]:>12,.4f}  close={r[1]:>10,.2f}  vol*close={r[2]:>15,.0f}")

# Spread proxy
print("\n=== (high-low)/close spread proxy ===")
for sym in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
    cur.execute("""SELECT AVG((high-low)/NULLIF(close,0)) FROM (
                   SELECT high,low,close FROM klines 
                   WHERE symbol=? AND interval='1m' ORDER BY ts DESC LIMIT 100)""", (sym,))
    spread = cur.fetchone()[0]
    if spread:
        print(f"  {sym:15s}: {spread:.6f} ({spread*100:.4f}%)")

conn.close()
