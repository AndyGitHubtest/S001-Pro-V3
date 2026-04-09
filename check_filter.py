#!/usr/bin/env python3
"""分析DB中需要过滤的币种类型"""
import sqlite3, statistics
conn = sqlite3.connect("/home/ubuntu/projects/data-core/data/klines.db")
cur = conn.cursor()

# 所有有24h数据的币，取raw数据后python算std
cur.execute("""
    SELECT symbol, SUM(volume * close) as vol_usdt, 
           COUNT(*) as cnt, AVG(close) as avg_price,
           MIN(close) as min_p, MAX(close) as max_p
    FROM klines 
    WHERE ts > (SELECT MAX(ts) - 86400000 FROM klines)
    GROUP BY symbol
    HAVING cnt >= 100
    ORDER BY vol_usdt DESC
""")
rows = cur.fetchall()

print("=== 疑似稳定币 (price接近1且24h range<0.5%) ===")
for sym, vol, cnt, avg_p, min_p, max_p in rows:
    if avg_p and min_p and max_p and avg_p > 0:
        rng = (max_p - min_p) / avg_p
        if 0.9 < avg_p < 1.1 and rng < 0.005:
            print("  %-25s price=%.4f range=%.3f%% vol=%s" % (sym, avg_p, rng*100, "{:,.0f}".format(vol or 0)))

print()
print("=== 疑似黄金/商品/指数代币 ===")
keywords = ['XAU', 'XAG', 'XPT', 'XPD', 'CL/', 'BZ/', 'NATGAS', 'BTCDOM', 'SPY', 'QQQ', 'EWY', 'EWJ', 'COPPER', 'PRL']
for sym, vol, cnt, avg_p, min_p, max_p in rows:
    for kw in keywords:
        if kw in sym:
            print("  %-25s vol=%s" % (sym, "{:,.0f}".format(vol or 0)))
            break

print()
print("=== 疑似股票代币 ===")
stocks = ['TSLA', 'MSTR', 'AMZN', 'COIN/', 'PLTR', 'NVDA', 'GOOGL', 'META/', 'AAPL', 'HOOD', 'TSM/', 'MU/', 'SNDK', 'INTC', 'PAYP']
for sym, vol, cnt, avg_p, min_p, max_p in rows:
    for kw in stocks:
        if kw in sym:
            print("  %-25s vol=%s" % (sym, "{:,.0f}".format(vol or 0)))
            break

print()
print("=== 中文名/特殊字符币种 ===")
for sym, vol, cnt, avg_p, min_p, max_p in rows:
    if any(ord(c) > 127 for c in sym.split('/')[0]):
        print("  %-25s vol=%s" % (sym, "{:,.0f}".format(vol or 0)))

print()
print("=== 单字母币种 ===")
for sym, vol, cnt, avg_p, min_p, max_p in rows:
    base = sym.split('/')[0]
    if len(base) <= 1:
        print("  %-25s vol=%s" % (sym, "{:,.0f}".format(vol or 0)))

print()
print("=== 超低波动(24h range<0.5%且vol>5M) ===")
for sym, vol, cnt, avg_p, min_p, max_p in rows:
    if avg_p and min_p and max_p and avg_p > 0 and vol and vol > 5000000:
        rng = (max_p - min_p) / avg_p
        if rng < 0.005:
            print("  %-25s range=%.3f%% avg=%.2f vol=%s" % (sym, rng*100, avg_p, "{:,.0f}".format(vol)))

print()
print("=== 24h数据不足(cnt<500且vol>1M, 可能退市/暂停) ===")
for sym, vol, cnt, avg_p, min_p, max_p in rows:
    if cnt < 500 and vol and vol > 1000000:
        print("  %-25s cnt=%d vol=%s" % (sym, cnt, "{:,.0f}".format(vol)))

print()
print("=== BTC挂钩币(price>50000, 非BTC本身) ===")
for sym, vol, cnt, avg_p, min_p, max_p in rows:
    if avg_p and avg_p > 50000 and 'BTC' not in sym:
        print("  %-25s price=%.2f vol=%s" % (sym, avg_p, "{:,.0f}".format(vol or 0)))

conn.close()
