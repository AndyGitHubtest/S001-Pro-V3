# V3 模块架构详细设计

## 模块依赖图

```
                    ┌─────────────┐
                    │    main.py  │
                    └──────┬──────┘
                           │
       ┌───────────────────┼───────────────────┐
       ▼                   ▼                   ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   Config    │    │  DataReader │    │   Executor  │
│  (配置中心)  │    │  (数据访问)  │    │  (交易所)   │
└──────┬──────┘    └──────┬──────┘    └──────┬──────┘
       │                  │                  │
       ▼                  ▼                  ▼
┌───────────────────────────────────────────────────┐
│              PositionManager                       │
│           (持仓状态管理 - 核心)                     │
└──────────────────────┬────────────────────────────┘
                       │
                       ▼
┌───────────────────────────────────────────────────┐
│             SignalGenerator                        │
│          (信号生成 - 纯计算)                        │
└───────────────────────────────────────────────────┘
```

## 模块职责边界

### Config (配置中心)
- **职责**: 加载yaml，验证必填项，提供全局配置访问
- **无状态**: 纯数据类，初始化后只读
- **错误处理**: 缺少必填项立即抛出，Fail Fast

### DataReader (数据访问层)
- **职责**: 唯一接触SQLite的模块
- **缓存策略**: LRU缓存最近500根K线
- **线程安全**: 单线程无需锁，但连接需及时关闭

### SignalGenerator (信号生成器)
- **职责**: 纯数学计算，无IO操作
- **输入**: 两个价格序列 (numpy数组)
- **输出**: Z-Score值 + 信号类型
- **可测试**: 100%可单元测试，无需Mock

### PositionManager (持仓管理)
- **职责**: 持仓状态的唯一权威源
- **持久化**: 每个tick结束后保存到JSON
- **恢复**: 启动时从JSON恢复，再与交易所对账

### Executor (执行器)
- **职责**: 唯一接触交易所API的模块
- **原子性**: 双边订单同步下发
- **容错**: 单边失败立即回滚

## 数据结构设计

### 1. Position (持仓记录)

```python
@dataclass
class Position:
    # 配对标识
    symbol_a: str           # 例如 "BTC/USDT"
    symbol_b: str           # 例如 "ETH/USDT"
    
    # 方向
    direction: str          # "long_spread" (做多价差) 或 "short_spread" (做空价差)
    
    # 进场信息
    entry_z: float          # 进场时的Z-Score
    entry_price_a: float    # A品种进场价格
    entry_price_b: float    # B品种进场价格
    entry_time: str         # ISO格式时间戳
    
    # 仓位信息
    qty_a: float            # A品种数量
    qty_b: float            # B品种数量
    notional: float         # 名义价值 (USDT)
    
    # 当前状态
    current_z: float        # 当前Z-Score (每tick更新)
    unrealized_pnl: float   # 未实现盈亏
    
    # 简化：不分层，直接满仓
    status: str             # "open" | "closing"
```

### 2. State (全局状态)

```python
@dataclass
class State:
    positions: Dict[str, Position]   # key: "BTC/USDT-ETH/USDT"
    last_update: str                 # ISO格式时间戳
    daily_stats: Dict                # 日统计 (可选)
```

### 3. Signal (信号)

```python
@dataclass
class Signal:
    pair_key: str
    zscore: float
    action: str         # "enter_long" | "enter_short" | "exit" | "hold"
    confidence: float   # 0-1，基于历史稳定性
```

## 核心算法

### Z-Score 计算

```python
def calc_zscore(price_a: float, price_b: float,
                history_a: np.ndarray, history_b: np.ndarray) -> float:
    """
    计算价差Z-Score
    
    1. 计算历史价差序列: spread = ln(A) - beta * ln(B)
    2. 计算价差的均值和标准差
    3. 当前Z-Score = (current_spread - mean) / std
    """
    # 1. OLS回归计算beta
    log_a = np.log(history_a)
    log_b = np.log(history_b)
    beta = np.cov(log_a, log_b)[0, 1] / np.var(log_b)
    
    # 2. 历史价差
    spread = log_a - beta * log_b
    
    # 3. 当前价差
    current_spread = np.log(price_a) - beta * np.log(price_b)
    
    # 4. Z-Score
    zscore = (current_spread - np.mean(spread)) / np.std(spread)
    
    return zscore
```

### 信号判断

```python
def generate_signal(zscore: float, 
                    entry_threshold: float = 2.0,
                    exit_threshold: float = 0.5,
                    stop_threshold: float = 3.0) -> str:
    """
    基于Z-Score生成交易信号
    
    Returns:
        "enter_long":  Z < -entry (价差偏低，做多价差)
        "enter_short": Z > entry  (价差偏高，做空价差)
        "exit":       |Z| < exit (回归均值，平仓)
        "stop":       |Z| > stop (止损)
        "hold":       无信号
    """
    if zscore < -entry_threshold:
        return "enter_long"
    elif zscore > entry_threshold:
        return "enter_short"
    elif abs(zscore) < exit_threshold:
        return "exit"
    elif abs(zscore) > stop_threshold:
        return "stop"
    else:
        return "hold"
```

## 错误处理策略

### 1. 交易所API错误

| 错误类型 | 处理策略 | 重试 |
|---------|---------|------|
| Rate Limit (429) | 指数退避: 1s, 2s, 4s, 8s | 是，最多5次 |
| Network Error | 立即重试 | 是，最多3次 |
| Invalid Symbol | 记录错误，跳过该对 | 否 |
| Insufficient Balance | 进入保护模式，停止新开仓 | 否 |

### 2. 数据错误

| 错误类型 | 处理策略 |
|---------|---------|
| DB连接失败 | 等待5秒后重试，最多3次 |
| 缺失K线数据 | 跳过该品种，记录警告 |
| 价格异常 (<=0) | 跳过该tick，使用上次的有效价格 |

### 3. 状态错误

| 错误类型 | 处理策略 |
|---------|---------|
| JSON损坏 | 从备份恢复，备份也损坏则清空 |
| 状态不一致 | 以交易所为准，本地状态重置 |

## 日志规范

### 日志级别

```python
# DEBUG: 开发调试
logger.debug(f"Z-Score calculated: {zscore:.4f} for {pair_key}")

# INFO: 正常运行信息
logger.info(f"Position opened: {pair_key} at Z={zscore:.2f}")

# WARNING: 需要注意但不致命
logger.warning(f"API rate limit hit, backing off for {delay}s")

# ERROR: 需要处理但可恢复
logger.error(f"Order failed: {error}, retrying...")

# CRITICAL: 立即停止
logger.critical("Exchange API key invalid, shutting down")
sys.exit(1)
```

### 日志格式

```
2024-01-15 14:32:05 | INFO | executor | Position opened: BTC/USDT-ETH/USDT | qty_a=0.01, qty_b=0.15, entry_z=2.15
2024-01-15 14:35:12 | INFO | executor | Position closed: BTC/USDT-ETH/USDT | pnl=+2.35 USDT, duration=3m7s
```

## 性能目标

| 指标 | 目标 | 说明 |
|------|------|------|
| 单次循环耗时 | < 500ms | 5对的情况下 |
| Z-Score计算 | < 10ms | 120根K线 |
| 内存占用 | < 100MB | 包含缓存 |
| 启动时间 | < 3s | 从启动到第一单检查 |

---

**状态**: 草稿待审
**下一步**: 确认模块设计后，生成API接口文档
