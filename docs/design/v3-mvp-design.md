# S001-Pro V3 MVP 设计文档

## 1. 问题定义 (Problem Definition)

### 1.1 当前痛点
- **代码膨胀**: V2 代码量 ~3000 行，模块间耦合严重
- **过度工程**: async/await 全栈，调试困难
- **维护成本**: 一个小改动需要理解整个调用链
- **隐式复杂性**: 热重载、动态配置、复杂状态机

### 1.2 核心诉求
用**最简单的方式**描述统计套利本质逻辑：
> "当两个相关品种的价格偏离(Z-Score > 2)时进场，回归均值(Z-Score < 0.5)时出场"

### 1.3 设计原则
1. **极简主义**: 代码 < 800 行
2. **显式优于隐式**: 无魔法，无黑盒
3. **同步优先**: 单线程，无 async
4. **配置即代码**: 改配置 → 重启生效

---

## 2. MVP 边界 (Scope Definition)

### 2.1 包含功能 (Must Have)

| 模块 | 功能 | 代码行目标 |
|------|------|-----------|
| PairFilter | 配对筛选 (相关性+协整+回归) | ~180行 |
| Backtester | 简化回测，优化进出场参数 | ~150行 |
| DataReader | 从SQLite读取K线 | ~80行 |
| SignalGenerator | Z-Score计算，信号产生 | ~100行 |
| PositionManager | 持仓状态管理，持久化 | ~120行 |
| Executor | 下单执行，错误处理 | ~150行 |
| WebMonitor | FastAPI + 前端监控面板 | ~200行 |
| Notifier | Telegram通知推送 | ~50行 |
| Config | 配置加载，参数校验 | ~50行 |
| Main | 主循环，初始化 | ~80行 |

**总代码行目标**: ~1160行

### 2.2 明确排除 (Won't Have)

| 功能 | 排除原因 | 替代方案 |
|------|---------|---------|
| 热重载 | 线程安全问题 | 改配置→重启服务 |
| 复杂风控 | 简单规则足够 | 仅保留最大仓位限制 |
| 多层建仓 | 简化出场逻辑 | 单次满仓进出 |

### 2.3 核心设计决策

| 决策项 | 方案 | 说明 |
|--------|------|------|
| **时间周期** | 5m/15m/30m 三周期 | 1m数据合并生成 |
| **推送频率** | 每小时推送新配对 | 独立数据服务负责 |
| **回测周期** | 30天样本内+7天样本外 | 快速验证参数 |
| **配对数量** | Top 20 | 每小时筛选后更新 |
| **持仓方向** | 单向 | 简化逻辑 |
| **建仓方式** | 单次满仓 | 取消分层 |
| **通知方式** | Telegram实时推送 | 策略事件通知 |

---

## 3. 技术架构

### 3.1 架构图

```
┌─────────────────────────────────────────────┐
│                 Main Loop                   │
│         (每5秒执行一次循环)                  │
└──────────────┬──────────────────────────────┘
               │
    ┌──────────┴──────────┐
    ▼                     ▼
┌──────────┐        ┌──────────┐
│  Load    │        │  Load    │
│  Config  │        │  State   │
└────┬─────┘        └────┬─────┘
     │                   │
     ▼                   ▼
┌─────────────────────────────────┐
│      DataReader (SQLite)        │
│  - 读取最新价格                  │
│  - 维护内存缓存 (500根)          │
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│     SignalGenerator             │
│  - 计算 Z-Score                 │
│  - 生成 Entry/Exit 信号          │
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│      PositionManager            │
│  - 检查当前持仓                  │
│  - 判断开平仓条件                │
│  - 保存状态到JSON                │
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│         Executor                │
│  - 下单 (ccxt)                  │
│  - 确认成交                      │
│  - 错误处理                      │
└─────────────────────────────────┘
```

### 3.2 数据流

```
┌─────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│  SQLite │────▶│  Prices  │────▶│  Z-Score │────▶│  Signal  │
│  (DB)   │     │ (内存)   │     │ (计算)   │     │ (判断)   │
└─────────┘     └──────────┘     └──────────┘     └────┬─────┘
                                                       │
                              ┌────────────────────────┘
                              ▼
                    ┌─────────────────┐
                    │  PositionState  │
                    │  (JSON文件)     │
                    └────────┬────────┘
                             │
              ┌──────────────┴──────────────┐
              ▼                              ▼
       ┌─────────────┐                ┌─────────────┐
       │  Open Order │                │ Close Order │
       │  (Binance)  │                │  (Binance)  │
       └─────────────┘                └─────────────┘
```

---

## 4. 模块设计

### 4.1 PairFilter (简化版配对筛选)

**职责**: 从全市场筛选出适合统计套利的配对 (V2 M2+M3的简化版)

**简化策略**:
- 不做全市场扫描 (耗时)，改为基于用户提供的基础币种列表
- 只做**单层筛选** (去掉初筛二筛分层)
- 保留**核心统计检验**: 相关性 + 协整 + 历史回归

**筛选逻辑**:

```python
class PairFilter:
    def filter_pairs(self, candidates: List[Tuple[str, str]], 
                     data_reader: DataReader) -> List[Dict]:
        """
        输入: 候选配对列表 [(A,B), (C,D), ...]
        输出: 通过筛选的配对 + 质量评分
        """
        results = []
        
        for sym_a, sym_b in candidates:
            # 1. 加载历史数据
            hist_a = data_reader.get_history(sym_a, lookback=500)
            hist_b = data_reader.get_history(sym_b, lookback=500)
            
            # 2. 基础检查
            if len(hist_a) < 500 or len(hist_b) < 500:
                continue
            
            # 3. 相关系数 (快速检查)
            corr = np.corrcoef(np.log(hist_a), np.log(hist_b))[0, 1]
            if corr < 0.85:  # V2是0.8初筛+0.85二筛，V3简化为单阈值
                continue
            
            # 4. 价差标准差 (确保有波动)
            spread = np.log(hist_a) - np.log(hist_b)
            if np.std(spread) < 0.002:
                continue
            
            # 5. 均值回归速度 (半衰期)
            hl = self._calc_half_life(spread)
            if hl < 5 or hl > 60:  # 太快或太慢都不好
                continue
            
            # 6. Z-Score穿越频率 (确认有交易机会)
            z_crosses = self._count_z_crosses(spread, threshold=2.0)
            if z_crosses < 3:  # 500根内至少穿越3次
                continue
            
            # 7. 简单评分 (0-100)
            score = self._calc_score(corr, hl, z_crosses)
            
            results.append({
                'symbol_a': sym_a,
                'symbol_b': sym_b,
                'correlation': corr,
                'half_life': hl,
                'z_crosses': z_crosses,
                'score': score
            })
        
        # 按评分排序，返回Top N
        results.sort(key=lambda x: x['score'], reverse=True)
        return results[:self.config.max_pairs]
```

**与V2的区别**:

| 特性 | V2 | V3简化版 |
|------|-----|---------|
| 筛选层数 | 初筛+二筛 两层 | 单层 |
| 相关系数 | 0.8初筛+0.85二筛 | 单阈值0.85 |
| 半衰期 | OU过程计算 | 简化OLS计算 |
| 评分维度 | 10+指标 | 3-4核心指标 |
| 互斥限制 | 单币最多5对 | 可选，默认不限制 |
| 输出数量 | 全部通过二筛的 | Top N (可配置) |

### 4.2 DataReader

**职责**: 从SQLite读取K线数据，维护内存缓存

**核心方法**:
```python
class DataReader:
    def __init__(self, db_path: str, cache_size: int = 500):
        self.conn = sqlite3.connect(db_path)
        self.cache = {}  # symbol -> deque of (timestamp, close)
    
    def get_latest(self, symbols: List[str]) -> Dict[str, float]:
        """获取最新价格"""
        pass
    
    def get_history(self, symbol: str, lookback: int) -> np.ndarray:
        """获取历史收盘价序列"""
        pass
```

### 4.2 SignalGenerator

**职责**: 计算Z-Score，生成交易信号

**核心方法**:
```python
class SignalGenerator:
    def __init__(self, lookback: int = 120):
        self.lookback = lookback
    
    def calc_zscore(self, price_a: float, price_b: float, 
                    history_a: np.ndarray, history_b: np.ndarray) -> float:
        """计算当前Z-Score"""
        pass
    
    def should_enter(self, zscore: float, threshold: float) -> bool:
        """是否满足进场条件"""
        pass
    
    def should_exit(self, zscore: float, threshold: float) -> bool:
        """是否满足出场条件"""
        pass
```

### 4.3 PositionManager

**职责**: 管理持仓状态，持久化到JSON

**数据结构**:
```python
@dataclass
class Position:
    symbol_a: str
    symbol_b: str
    direction: str  # 'long_spread' or 'short_spread'
    entry_z: float
    entry_price_a: float
    entry_price_b: float
    quantity: float
    entry_time: str
    layers_filled: int = 0  # 简化：不分层，直接满仓
```

**核心方法**:
```python
class PositionManager:
    def __init__(self, state_file: str):
        self.positions = {}
        self.state_file = state_file
    
    def load_state(self):
        """从JSON加载持仓"""
        pass
    
    def save_state(self):
        """保存到JSON"""
        pass
    
    def can_open(self, symbol_a: str, symbol_b: str) -> bool:
        """检查是否可以开仓"""
        pass
    
    def add_position(self, pos: Position):
        """添加新持仓"""
        pass
    
    def remove_position(self, pair_key: str):
        """移除持仓"""
        pass
```

### 4.4 Executor

**职责**: 执行下单，处理错误

**核心方法**:
```python
class Executor:
    def __init__(self, exchange: ccxt.Exchange, config: dict):
        self.exchange = exchange
        self.config = config
    
    def open_position(self, pos: Position) -> bool:
        """开仓：同步下双边订单"""
        pass
    
    def close_position(self, pos: Position) -> bool:
        """平仓：同步平双边"""
        pass
    
    def check_position_exists(self, symbol: str) -> bool:
        """检查交易所是否有持仓"""
        pass
```

---

## 5. 配置设计

### 5.1 config.yaml 结构

```yaml
# 交易参数
trading:
  z_entry: 2.0          # 进场Z-Score阈值
  z_exit: 0.5           # 出场Z-Score阈值
  z_stop: 3.0           # 止损Z-Score阈值
  max_positions: 5      # 最大持仓对数
  capital_per_pair: 100 # 每对投入USDT

# 数据参数
data:
  db_path: "data/klines.db"
  lookback: 120         # Z-Score计算回看周期
  cache_size: 500       # 内存缓存K线数

# 交易所参数
exchange:
  name: "binance"
  sandbox: false
  api_key: ""           # 从环境变量覆盖
  api_secret: ""        # 从环境变量覆盖

# 运行参数
runtime:
  loop_interval: 5      # 主循环间隔(秒)
  log_level: "INFO"
```

---

## 6. 主循环逻辑

```python
def main():
    # 1. 加载配置
    config = load_config("config/config.yaml")
    
    # 2. 初始化组件
    data_reader = DataReader(config.data.db_path)
    signal_gen = SignalGenerator(config.data.lookback)
    pos_manager = PositionManager("data/positions.json")
    executor = Executor(connect_exchange(config.exchange), config)
    
    # 3. 加载配对列表
    pairs = load_pairs("config/pairs_v2.json")
    
    # 4. 恢复持仓状态
    pos_manager.load_state()
    executor.sync_positions(pos_manager.positions)  # 与交易所对账
    
    # 5. 主循环
    while True:
        try:
            # 获取最新价格
            prices = data_reader.get_latest(all_symbols)
            
            for pair in pairs:
                z = signal_gen.calc_zscore(
                    prices[pair.a], prices[pair.b],
                    data_reader.get_history(pair.a, config.data.lookback),
                    data_reader.get_history(pair.b, config.data.lookback)
                )
                
                pair_key = f"{pair.a}-{pair.b}"
                
                # 检查是否有持仓
                if pos_manager.has_position(pair_key):
                    pos = pos_manager.get(pair_key)
                    
                    # 检查止损
                    if abs(z) > config.trading.z_stop:
                        executor.close_position(pos)
                        pos_manager.remove_position(pair_key)
                    # 检查止盈
                    elif signal_gen.should_exit(z, config.trading.z_exit):
                        executor.close_position(pos)
                        pos_manager.remove_position(pair_key)
                else:
                    # 尝试开仓
                    if pos_manager.can_open() and signal_gen.should_enter(z, config.trading.z_entry):
                        pos = create_position(pair, z, prices)
                        if executor.open_position(pos):
                            pos_manager.add_position(pos)
            
            # 保存状态
            pos_manager.save_state()
            
        except Exception as e:
            logger.error(f"Main loop error: {e}")
        
        time.sleep(config.runtime.loop_interval)
```

---

## 7. 风险与陷阱

### 7.1 已知风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 单边成交 | 裸仓风险 | 下单后检查双边成交，否则市价回滚 |
| 交易所API故障 | 无法下单 | 指数退避重试，超过3次进入保护模式 |
| 状态丢失 | 重复开仓 | JSON持久化 + 启动时对账 |
| Z-Score假突破 | 频繁止损 | 回看周期120根，避免噪声 |

### 7.2 设计陷阱

1. **浮点数比较**: Z-Score判断使用明确的阈值，避免浮点误差
2. **时间戳对齐**: 确保两个品种的K线时间戳一致
3. **价格单位**: 统一使用float，避免decimal转换开销
4. **异常传播**: 主循环捕获所有异常，避免进程崩溃

---

## 8. 测试策略

### 8.1 单元测试
- DataReader: 模拟SQLite数据，验证读取逻辑
- SignalGenerator: 固定价格序列，验证Z-Score计算
- PositionManager: 临时JSON文件，验证状态持久化
- Executor: Mock交易所，验证下单流程

### 8.2 集成测试
- 端到端主循环测试（使用测试网）
- 持仓恢复测试（模拟重启场景）
- 错误恢复测试（模拟API失败）

### 8.3 实盘验证
- 10单真实交易验证
- PnL计算准确性验证
- 状态一致性验证

---

## 9. 文档清单

| 文档 | 状态 | 路径 |
|------|------|------|
| MVP设计文档 | ✅ 草稿 | docs/design/v3-mvp-design.md |
| 架构文档 | 📝 待补充 | docs/architecture/module-design.md |
| API接口文档 | 📝 待补充 | docs/api/interfaces.md |
| 部署文档 | 📝 待补充 | docs/deployment.md |

---

## 10. 待确认事项

**请在Review阶段确认以下问题：**

1. [ ] **配对数量上限**: 建议5-10对，是否确认？
2. [ ] **Z-Score阈值**: entry=2.0, exit=0.5, stop=3.0 是否合适？
3. [ ] **主循环间隔**: 5秒是否足够？
4. [ ] **数据回看周期**: 120根1mK线（约2小时）是否合适？
5. [ ] **是否支持双向持仓**: 建议单向简化，确认？
6. [ ] **分层建仓**: 建议V3取消分层，直接满仓，确认？

---

**下一步**: 确认以上问题后，进入详细架构设计阶段。
