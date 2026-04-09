# S001-Pro V3 最终设计规格书

## 1. 架构概览

### 1.1 系统架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                     Data-Core Service                           │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐      │
│  │ 币安API │───▶│  1m数据 │───▶│  合并   │───▶│ 5m/15m  │      │
│  └─────────┘    │  入库   │    │ N分钟   │    │ /30m    │      │
│                 └─────────┘    └─────────┘    └─────────┘      │
│                      ▲                                          │
│                      │ 每小时推送                                │
└──────────────────────┼──────────────────────────────────────────┘
                       │ HTTP/文件
┌──────────────────────┼──────────────────────────────────────────┐
│                      ▼                                          │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Scanner模块 (Hourly)                                    │   │
│  │  1. 接收Top 30候选配对                                    │   │
│  │  2. 加载60天历史数据                                      │   │
│  │  3. 参数优化网格搜索                                       │   │
│  │     ├─ 粗筛: 125组合 (z_entry[2-6/1], z_exit[0.25-2/0.5]) │   │
│  │     ├─ 精筛: 27组合 (最优值±0.5, 步长0.25)                │   │
│  │  4. 回测验证 (30天IS + 7天OS)                             │   │
│  │  5. 硬性过滤: PF>=1.3 AND 净利润>0                        │   │
│  │  6. 输出: pairs_optimized.json                            │   │
│  └────────────────────┬────────────────────────────────────┘   │
│                       │                                         │
│  ┌────────────────────▼────────────────────────────────────┐   │
│  │  Engine模块 (5s循环)                                      │   │
│  │  ┌─────────┐   ┌─────────────┐   ┌──────────────┐       │   │
│  │  │DataReader│──▶│SignalGenerator│──▶│PositionManager│       │   │
│  │  │(读取K线) │   │(Z-Score计算)  │   │(持仓状态)     │       │   │
│  │  └─────────┘   └─────────────┘   └──────────────┘       │   │
│  │  策略: 15m主信号 + 5m确认 + 30m过滤(|Z|<1)                │   │
│  └────────────────────┬────────────────────────────────────┘   │
│                       │                                         │
│  ┌────────────────────▼────────────────────────────────────┐   │
│  │  Trader模块                                               │   │
│  │  ├─ 双边同步下单                                          │   │
│  │  ├─ 成交确认+回滚                                         │   │
│  │  └─ 账户状态同步                                          │   │
│  └────────────────────┬────────────────────────────────────┘   │
│                       │                                         │
│  ┌────────────────────▼────────────────────────────────────┐   │
│  │  Monitor模块                                              │   │
│  │  ├─ FastAPI Web面板 (:8000)                               │   │
│  │  └─ Telegram实时通知                                       │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 模块职责

| 模块 | 职责 | 代码行目标 |
|------|------|-----------|
| scanner.py | 配对筛选+回测+参数优化 | ~250行 |
| engine.py | 数据读取+信号生成+持仓管理 | ~250行 |
| trader.py | 下单执行+账户同步 | ~150行 |
| monitor.py | Web面板+TG通知 | ~150行 |
| config.py | 配置中心 | ~50行 |
| main.py | 主控循环 | ~50行 |

**总计**: ~900行

---

## 2. 核心流程

### 2.1 每小时扫描流程

```python
def hourly_scan():
    # 1. 从Data-Core获取候选币种列表 (全市场)
    symbols = fetch_symbols_from_data_core()  # 所有USDT永续合约
    
    # 2. 初级筛选 (Pre-filter) - 过滤垃圾币种
    filtered_symbols = []
    for sym in symbols:
        # 2.1 稳定币剔除
        if is_stablecoin(sym):  # USDC, FDUSD, TUSD, DAI, BUSD...
            continue
        
        # 2.2 交易量检查 (24h成交量 >= 500万USDT)
        volume_24h = get_24h_volume(sym)
        if volume_24h < 5_000_000:
            continue
        
        # 2.3 死币检查 (最近30天波动率 < 15%)
        volatility = calc_30d_volatility(sym)
        if volatility < 0.15:
            continue
        
        # 2.4 退市币检查 (数据完整性)
        if not has_complete_data(sym, min_days=30):
            continue
        
        # 2.5 新币检查 (上市时间 >= 60天)
        if is_new_listing(sym, max_days=60):
            continue
        
        filtered_symbols.append(sym)
    
    # 3. 生成候选配对 (从过滤后的币种)
    candidates = generate_pairs(filtered_symbols)  # 两两组合
    
    # 4. 三层配对筛选 + 评分排名
    scored_pairs = []
    
    for pair in candidates:
        data = load_history(pair.symbol_a, pair.symbol_b, days=60)
        
        # ========== Layer 1: 基础质量筛选 (统计基础) ==========
        corr_median = calc_median_correlation(data, window=120)
        if corr_median < 0.65:
            continue
        
        coint_p = cointegration_test(data)
        if coint_p > 0.1:
            continue
        
        adf_p = adf_test(data)
        if adf_p > 0.1:
            continue
        
        # ========== Layer 2: 稳定性筛选 (均值回归特性) ==========
        hl = calc_half_life(data)
        if hl > 48:
            continue
        
        corr_std = calc_rolling_correlation_std(data, window=120)
        if corr_std > 0.12:
            continue
        
        hurst = calc_hurst_exponent(data)
        if hurst > 0.6:
            continue
        
        # ========== Layer 3: 可交易性筛选 (实际交易条件) ==========
        z_max = calc_max_zscore(data)
        if z_max < 2.2:
            continue
        
        spread_std = calc_spread_std(data)
        if spread_std < 0.001:  # 阈值可配置
            continue
        
        volume_a = get_daily_volume(pair.symbol_a)
        volume_b = get_daily_volume(pair.symbol_b)
        if volume_a < 3_000_000 or volume_b < 3_000_000:
            continue
        
        spread_pct_a = get_bid_ask_spread(pair.symbol_a)
        spread_pct_b = get_bid_ask_spread(pair.symbol_b)
        if spread_pct_a > 0.0002 or spread_pct_b > 0.0002:
            continue
        
        # ========== 计算综合评分 ==========
        # 成交量评分 (3M=0分, 30M=1分, log缩放)
        min_vol = 3_000_000
        max_vol = 30_000_000
        avg_volume = (volume_a + volume_b) / 2
        volume_score = min(1.0, max(0.0, (np.log(avg_volume) - np.log(min_vol)) / (np.log(max_vol) - np.log(min_vol))))
        
        score = (
            0.30 * (1 - coint_p) +           # 协整强度
            0.20 * corr_median +              # 相关性
            0.15 * (1 / hl) +                 # 均值回归速度
            0.15 * (z_max / 4.0) +            # 历史偏离幅度 (归一化到4)
            0.10 * (1 - corr_std) +           # 相关性稳定性
            0.10 * volume_score               # 流动性
        )
        
        scored_pairs.append({
            'pair': pair,
            'score': score,
            'metrics': {
                'corr_median': corr_median,
                'coint_p': coint_p,
                'adf_p': adf_p,
                'half_life': hl,
                'corr_std': corr_std,
                'hurst': hurst,
                'z_max': z_max,
                'spread_std': spread_std,
                'volume_min': min(volume_a, volume_b),
                'spread_pct_max': max(spread_pct_a, spread_pct_b)
            }
        })
    
    # 按评分排序，取Top 30
    scored_pairs.sort(key=lambda x: x['score'], reverse=True)
    quality_pairs = [item['pair'] for item in scored_pairs[:30]]  # 15m专用池
    
    # 5. 参数优化 (仅对质量配对)
    results = []
    # 注: quality_pairs无数量限制，通常200-500对，全部进入参数优化
    for pair in quality_pairs:
        # 5.1 加载配对历史数据
        data = load_history(pair.symbol_a, pair.symbol_b, days=60)
        
        # 5.2 粗筛参数网格
        best_pf = 0
        best_params = None
        
        for z_entry in [2.0, 3.0, 4.0, 5.0, 6.0]:  # 5档
            for z_exit in [0.25, 0.5, 1.0, 1.5, 2.0]:  # 5档
                for z_stop in [z_entry+1, z_entry+2, z_entry+3]:  # 3档
                    if z_stop > 7: continue
                    
                    # 4. 回测
                    stats = backtest(data, z_entry, z_exit, z_stop)
                    
                    # 5. 硬性过滤
                    if stats.pf >= 1.3 and stats.net_profit > 0:
                        if stats.pf > best_pf:
                            best_pf = stats.pf
                            best_params = (z_entry, z_exit, z_stop)
        
        # 6. 精筛（如果粗筛有结果）
        if best_params:
            z_e, z_x, z_s = best_params
            for ze in [z_e-0.5, z_e-0.25, z_e, z_e+0.25, z_e+0.5]:
                for zx in [z_x-0.25, z_x, z_x+0.25]:
                    for zs in [z_s-0.5, z_s, z_s+0.5]:
                        if not (2 <= ze <= 6): continue
                        if not (0.25 <= zx <= 2): continue
                        if not (3 <= zs <= 7): continue
                        if zs <= ze: continue
                        
                        stats = backtest(data, ze, zx, zs)
                        if stats.pf >= 1.3 and stats.net_profit > 0:
                            if stats.pf > best_pf:
                                best_pf = stats.pf
                                best_params = (ze, zx, zs)
        
        # 7. 记录结果
        if best_params:
            results.append({
                'symbol_a': pair.symbol_a,
                'symbol_b': pair.symbol_b,
                'z_entry': best_params[0],
                'z_exit': best_params[1],
                'z_stop': best_params[2],
                'pf': best_pf,
                'profit': stats.net_profit,
                'sharpe': stats.sharpe
            })
    
    # 8. 保存优化结果
    save_pairs_optimized(results)
```

### 2.2 交易循环（每5秒）

```python
def trading_loop():
    # 1. 加载优化后的配对+参数
    pairs = load_pairs_optimized()
    
    # 2. 恢复持仓状态
    positions = load_positions()
    
    while True:
        for pair in pairs:
            # 3. 获取三周期数据
            klines_5m = data_reader.get_klines(pair, '5m', 120)
            klines_15m = data_reader.get_klines(pair, '15m', 120)
            klines_30m = data_reader.get_klines(pair, '30m', 60)
            
            # 4. 计算三周期Z-Score
            z_5m = calc_zscore(klines_5m)
            z_15m = calc_zscore(klines_15m)
            z_30m = calc_zscore(klines_30m)
            
            # 5. 信号判断（15m主+5m确认+30m过滤）
            params = pair['params']
            
            # 过滤器: |Z_30m| < 1.0
            if abs(z_30m) >= 1.0:
                continue
            
            pair_key = f"{pair['symbol_a']}-{pair['symbol_b']}"
            
            if pair_key in positions:
                # 6. 检查出场
                pos = positions[pair_key]
                if should_exit(z_15m, pos, params):
                    trader.close_position(pos)
                    del positions[pair_key]
            else:
                # 7. 检查进场（15m信号+5m确认）
                if should_enter(z_15m, z_5m, params):
                    pos = trader.open_position(pair, direction(z_15m))
                    positions[pair_key] = pos
        
        # 8. 保存状态
        save_positions(positions)
        
        # 9. 推送通知（如有成交）
        notify_trades()
        
        time.sleep(5)
```

---

## 3. 数据结构

### 3.1 pairs_optimized.json

```json
{
  "updated_at": "2024-01-15T14:00:00Z",
  "data_source": "data-core-v1",
  "pairs": [
    {
      "symbol_a": "BTC/USDT",
      "symbol_b": "ETH/USDT",
      "params": {
        "z_entry": 2.5,
        "z_exit": 0.75,
        "z_stop": 4.5
      },
      "backtest_stats": {
        "pf": 1.85,
        "sharpe": 1.42,
        "profit": 125.5,
        "trades": 23,
        "win_rate": 0.61
      }
    }
  ]
}
```

### 3.2 positions.json

```json
{
  "updated_at": "2024-01-15T14:05:30Z",
  "positions": {
    "BTC/USDT-ETH/USDT": {
      "symbol_a": "BTC/USDT",
      "symbol_b": "ETH/USDT",
      "direction": "long_spread",
      "entry_z": 2.52,
      "entry_price_a": 43250.5,
      "entry_price_b": 2650.25,
      "qty_a": 0.01,
      "qty_b": 0.15,
      "entry_time": "2024-01-15T13:45:00Z",
      "current_z": 1.85,
      "unrealized_pnl": 8.5,
      "status": "open"
    }
  }
}
```

### 3.3 数据库索引策略

Data-Core的SQLite数据库应建立以下索引，加速Scanner查询：

```sql
-- 主索引：按币种和时间倒序查询
CREATE INDEX idx_klines_symbol_timeframe_time 
ON klines(symbol, timeframe, timestamp DESC);

-- 覆盖索引：查询最新N条K线
CREATE INDEX idx_klines_lookup 
ON klines(symbol, timeframe, timestamp DESC, open, high, low, close, volume);

-- 24h成交量查询索引
CREATE INDEX idx_market_stats_volume 
ON market_stats(symbol, volume_24h DESC, timestamp);
```

**查询优化示例**:

```python
# 优化前：全表扫描
SELECT * FROM klines WHERE symbol = 'BTC/USDT' ORDER BY timestamp DESC LIMIT 500;

# 优化后：索引扫描 (O(log n) vs O(n))
# 使用 idx_klines_symbol_timeframe_time 索引
```

**性能提升**:
- 无索引：查询500根K线 ~500ms
- 有索引：查询500根K线 ~5ms
- **100倍加速**

---

## 4. 硬性约束

### 4.1 参数约束

| 参数 | 范围 | 步长 | 约束 |
|------|------|------|------|
| z_entry | 2.0 - 6.0 | 0.25 | - |
| z_exit | 0.25 - 2.0 | 0.25 | - |
| z_stop | 3.0 - 7.0 | 0.5 | **必须 > z_entry** |

### 4.2 回测过滤

```python
def is_valid_strategy(stats):
    return stats['pf'] >= 1.3 and stats['net_profit'] > 0
```

### 4.3 多周期约束

```python
def can_trade(z_5m, z_15m, z_30m, params):
    # 30m过滤器
    if abs(z_30m) >= 1.0:
        return False
    
    # 15m主信号
    if abs(z_15m) < params['z_entry']:
        return False
    
    # 5m确认（同向）
    if z_15m * z_5m <= 0:  # 不同号或为零
        return False
    
    return True
```

---

## 5. 关键算法

### 5.1 Z-Score计算

```python
def calc_zscore(prices_a, prices_b, lookback=120):
    """计算价差Z-Score"""
    # 对数价格
    log_a = np.log(prices_a)
    log_b = np.log(prices_b)
    
    # OLS回归求beta
    beta = np.cov(log_a, log_b)[0, 1] / np.var(log_b)
    
    # 历史价差
    spread = log_a - beta * log_b
    
    # 当前价差
    current_spread = log_a[-1] - beta * log_b[-1]
    
    # Z-Score
    mean = np.mean(spread[-lookback:])
    std = np.std(spread[-lookback:])
    zscore = (current_spread - mean) / std
    
    return zscore, beta
```

### 5.2 简化回测

```python
def backtest(data, z_entry, z_exit, z_stop):
    """
    简化回测，返回统计指标
    """
    trades = []
    in_position = False
    entry_price = None
    
    for i in range(120, len(data)):
        window_a = data['a'][i-120:i]
        window_b = data['b'][i-120:i]
        z, _ = calc_zscore(window_a, window_b)
        
        if not in_position:
            if abs(z) > z_entry:
                in_position = True
                entry_price = (data['a'][i], data['b'][i])
                entry_z = z
                direction = 1 if z < 0 else -1  # z<0做多价差
        else:
            # 检查出场
            profit = calc_pnl(entry_price, (data['a'][i], data['b'][i]), direction)
            
            if abs(z) < z_exit:  # 止盈
                trades.append(profit)
                in_position = False
            elif abs(z) > z_stop:  # 止损
                trades.append(profit)
                in_position = False
    
    # 统计
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t < 0]
    
    pf = sum(wins) / abs(sum(losses)) if losses else 0
    
    return {
        'pf': pf,
        'net_profit': sum(trades),
        'trades': len(trades),
        'win_rate': len(wins) / len(trades) if trades else 0
    }
```

---

## 6. 配置示例

```yaml
# config.yaml
trading:
  max_positions: 5
  capital_per_pair: 100
  max_hold_hours: 12

timeframes:
  primary: 15m    # 主信号周期
  confirm: 5m     # 确认周期
  filter: 30m     # 过滤周期
  filter_threshold: 1.0  # |Z_30m| < 1.0

data:
  core_api: "http://localhost:8080/api"
  pairs_endpoint: "/pairs/top30"
  klines_endpoint: "/klines"

optimization:
  coarse_entry: [2.0, 3.0, 4.0, 5.0, 6.0]
  coarse_exit: [0.25, 0.5, 1.0, 1.5, 2.0]
  coarse_stop_offset: [1, 2, 3]  # z_entry + offset
  fine_range: 0.5
  fine_step: 0.25
  min_pf: 1.3
  min_profit: 0

notification:
  telegram_bot_token: ""  # 从环境变量覆盖
  telegram_chat_id: ""

web:
  host: "0.0.0.0"
  port: 8000
  refresh_interval: 5
```

---

## 7. 部署架构

```
服务器
├── Data-Core Service (systemd)
│   └── 每小时推送Top 30
│
├── S001-V3 Strategy (systemd)
│   ├── Scanner: 每小时优化参数
│   ├── Engine: 5秒交易循环
│   └── Web: :8000 监控面板
│
└── Nginx (可选)
    └── 反向代理Web面板
```

---

## 8. 状态机

### 配对生命周期

```
Data-Core推送
      │
      ▼
Scanner优化参数
      │
      ▼
PF>=1.3 & Profit>0 ?
      │
   是 ▼           否 ▼
加入监控池      丢弃
      │
      ▼
Engine信号检查
      │
      ▼
满足进场条件 ?
      │
   是 ▼           否 ▼
Trader开仓      继续监控
      │
      ▼
持仓中
      │
      ▼
出场条件满足 ?
      │
   是 ▼           否 ▼
Trader平仓      检查超时
      │            │
      ▼            ▼
  完成       超时强制平仓
```

---

**文档版本**: v1.0
**生成时间**: 2024-01-15
**状态**: 待Build
