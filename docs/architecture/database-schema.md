# S001-Pro V3 数据库Schema

## 数据流转图

```
┌─────────────────────────────────────────────────────────────────┐
│  Data-Core (外部)                                                │
│  ├── klines.db (只读)                                            │
│  │   └── ohlcv数据 (1m/5m/15m/30m)                               │
│  └── API                                                         │
│      └── /pairs, /klines/:symbol                                 │
└─────────────────────────┬───────────────────────────────────────┘
                          │ HTTP/读取
┌─────────────────────────▼───────────────────────────────────────┐
│  S001-V3 Strategy                                                │
│                                                                  │
│  ┌──────────────┐        ┌──────────────┐                       │
│  │  scanner.py  │───────▶│ strategy.db  │                       │
│  │  扫描+优化    │ 写入   │  (读写)       │                       │
│  └──────────────┘        │              │                       │
│                          │  ├─ pairs    │  筛选后的配对+参数     │
│  ┌──────────────┐        │  ├─ positions│  当前持仓            │
│  │  engine.py   │◀───────│  ├─ trades   │  历史交易            │
│  │  信号+持仓    │ 读写   │  └─ metrics  │  统计指标            │
│  └──────────────┘        └──────────────┘                       │
│                                                                  │
│  ┌──────────────┐        ┌──────────────┐                       │
│  │  trader.py   │───────▶│  Binance API │                       │
│  │  执行        │        │  (交易所)     │                       │
│  └──────────────┘        └──────────────┘                       │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## strategy.db Schema

### 1. pairs 表 (筛选后的配对)

```sql
CREATE TABLE IF NOT EXISTS pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pool TEXT NOT NULL,           -- 'primary' | 'secondary'
    symbol_a TEXT NOT NULL,
    symbol_b TEXT NOT NULL,
    score REAL NOT NULL,          -- 综合评分
    
    -- Layer 1 指标
    corr_median REAL,
    coint_p REAL,
    adf_p REAL,
    
    -- Layer 2 指标
    half_life REAL,
    corr_std REAL,
    hurst REAL,
    
    -- Layer 3 指标
    zscore_max REAL,
    spread_std REAL,
    volume_min INTEGER,
    
    -- 优化后的参数
    z_entry REAL,
    z_exit REAL,
    z_stop REAL,
    
    -- 回测表现
    pf REAL,                      -- Profit Factor
    sharpe REAL,
    total_return REAL,
    max_dd REAL,
    trades_count INTEGER,
    
    -- 时间戳
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(pool, symbol_a, symbol_b)
);

-- 索引
CREATE INDEX idx_pairs_pool_score ON pairs(pool, score DESC);
CREATE INDEX idx_pairs_symbols ON pairs(symbol_a, symbol_b);
```

### 2. positions 表 (当前持仓)

```sql
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_key TEXT NOT NULL UNIQUE, -- "BTC/USDT-ETH/USDT"
    pool TEXT NOT NULL,
    
    symbol_a TEXT NOT NULL,
    symbol_b TEXT NOT NULL,
    direction TEXT NOT NULL,       -- 'long_spread' | 'short_spread'
    
    -- 进场信息
    entry_z REAL NOT NULL,
    entry_price_a REAL NOT NULL,
    entry_price_b REAL NOT NULL,
    entry_time TIMESTAMP NOT NULL,
    
    -- 仓位
    qty_a REAL NOT NULL,
    qty_b REAL NOT NULL,
    notional REAL NOT NULL,        -- 名义价值
    
    -- 当前状态
    current_z REAL,
    unrealized_pnl REAL DEFAULT 0,
    
    -- 参数
    z_entry REAL,
    z_exit REAL,
    z_stop REAL,
    
    status TEXT DEFAULT 'open',    -- 'open' | 'closing'
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 索引
CREATE INDEX idx_positions_status ON positions(status);
CREATE INDEX idx_positions_pool ON positions(pool);
```

### 3. trades 表 (历史交易记录)

```sql
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_key TEXT NOT NULL,
    pool TEXT NOT NULL,
    
    symbol_a TEXT NOT NULL,
    symbol_b TEXT NOT NULL,
    direction TEXT NOT NULL,
    
    -- 进场
    entry_time TIMESTAMP NOT NULL,
    entry_price_a REAL NOT NULL,
    entry_price_b REAL NOT NULL,
    entry_z REAL,
    
    -- 出场
    exit_time TIMESTAMP,
    exit_price_a REAL,
    exit_price_b REAL,
    exit_z REAL,
    exit_reason TEXT,              -- 'take_profit' | 'stop_loss' | 'timeout'
    
    -- 盈亏
    qty_a REAL NOT NULL,
    qty_b REAL NOT NULL,
    pnl REAL,                      -- 盈亏金额
    pnl_pct REAL,                  -- 盈亏百分比
    
    -- 费用
    fee_a REAL DEFAULT 0,
    fee_b REAL DEFAULT 0,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 索引
CREATE INDEX idx_trades_pair ON trades(pair_key);
CREATE INDEX idx_trades_time ON trades(entry_time);
CREATE INDEX idx_trades_pool ON trades(pool);
```

### 4. metrics 表 (每日统计)

```sql
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,     -- '2024-01-15'
    pool TEXT NOT NULL,
    
    -- 交易统计
    trades_count INTEGER DEFAULT 0,
    win_count INTEGER DEFAULT 0,
    loss_count INTEGER DEFAULT 0,
    
    -- 盈亏
    gross_profit REAL DEFAULT 0,
    gross_loss REAL DEFAULT 0,
    net_pnl REAL DEFAULT 0,
    
    -- 表现
    pf REAL,                       -- Profit Factor
    win_rate REAL,
    avg_win REAL,
    avg_loss REAL,
    
    -- 持仓
    max_positions INTEGER DEFAULT 0,
    avg_position_time REAL,        -- 平均持仓时间(小时)
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 索引
CREATE INDEX idx_metrics_date ON metrics(date);
```

### 5. scan_history 表 (扫描历史)

```sql
CREATE TABLE IF NOT EXISTS scan_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    pool TEXT NOT NULL,
    
    -- 输入
    candidates_count INTEGER,      -- 候选配对数
    
    -- 各层过滤结果
    layer1_passed INTEGER,         -- Layer 1通过数
    layer2_passed INTEGER,         -- Layer 2通过数
    layer3_passed INTEGER,         -- Layer 3通过数
    
    -- 输出
    top_n INTEGER,                 -- 最终选出数
    top_score REAL,                -- 最高分
    avg_score REAL,                -- 平均分
    
    -- 耗时
    duration_ms INTEGER            -- 扫描耗时(毫秒)
);
```

---

## 数据流详情

### 1. Scanner 写入流程

```python
# 1. 清空旧数据 (软删除或标记)
UPDATE pairs SET active = 0 WHERE pool = 'primary';

# 2. 插入新筛选结果
INSERT INTO pairs (pool, symbol_a, symbol_b, score, ...)
VALUES ('primary', 'BTC/USDT', 'ETH/USDT', 0.92, ...);

# 3. 记录扫描历史
INSERT INTO scan_history (pool, candidates_count, layer1_passed, ...)
VALUES ('primary', 500, 200, 150, 80, 30, 0.92, 0.75, 15000);
```

### 2. Engine 读写流程

```python
# 读取当前持仓
SELECT * FROM positions WHERE status = 'open';

# 更新持仓状态 (每tick)
UPDATE positions 
SET current_z = ?, unrealized_pnl = ?, updated_at = ?
WHERE pair_key = ?;

# 开新仓
INSERT INTO positions (...)
VALUES (...);
```

### 3. Trader 写入流程

```python
# 平仓后记录交易
INSERT INTO trades (...)
VALUES (...);

# 删除持仓记录
DELETE FROM positions WHERE pair_key = ?;

# 更新每日统计
INSERT OR REPLACE INTO metrics (date, pool, trades_count, ...)
VALUES (date('now'), 'primary', 
        (SELECT COUNT(*) FROM trades WHERE date(entry_time) = date('now')),
        ...);
```

---

## 查询示例

### 当前持仓盈亏
```sql
SELECT 
    pair_key,
    direction,
    entry_z,
    current_z,
    unrealized_pnl,
    (julianday('now') - julianday(entry_time)) * 24 as hold_hours
FROM positions 
WHERE status = 'open'
ORDER BY unrealized_pnl DESC;
```

### 今日交易统计
```sql
SELECT 
    pool,
    COUNT(*) as trades,
    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
    SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
    SUM(pnl) as total_pnl,
    AVG(pnl) as avg_pnl
FROM trades 
WHERE date(entry_time) = date('now')
GROUP BY pool;
```

### 配对表现排名
```sql
SELECT 
    pair_key,
    COUNT(*) as trades,
    SUM(pnl) as total_pnl,
    AVG(pnl) as avg_pnl,
    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as win_rate
FROM trades 
WHERE entry_time >= date('now', '-30 days')
GROUP BY pair_key
ORDER BY total_pnl DESC
LIMIT 10;
```

---

## 优势

1. **数据持久化**: 重启不丢失，可追溯历史
2. **查询灵活**: SQL分析任意维度
3. **并发安全**: SQLite WAL模式支持读写并发
4. **监控友好**: Web面板直接查询数据库展示
5. **审计完整**: 所有操作有记录，可复盘
