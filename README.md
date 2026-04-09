# S001-Pro V3

极简模块化统计套利策略

## 架构

```
S001-Pro-V3/
├── src/
│   ├── main.py       # 入口 + 主循环
│   ├── config.py     # 配置中心
│   ├── database.py   # 数据库操作
│   ├── scanner.py    # 三层筛选 + 参数优化
│   ├── engine.py     # 数据读取 + 信号 + 持仓
│   ├── trader.py     # 交易执行
│   └── monitor.py    # Web面板 + TG通知
├── config/
│   └── config.yaml   # 配置文件
├── data/
│   ├── klines.db     # Data-Core数据 (只读)
│   ├── strategy.db   # 策略状态 (读写)
│   └── strategy.log  # 日志
└── web/              # 静态文件
```

## 核心流程

1. **Scanner** (每小时)
   - 初级筛选: 稳定币/死币/退市币/新币过滤
   - 三层质量筛选: 统计基础 → 稳定性 → 可交易性
   - 评分排名: 6维加权评分
   - 参数优化: 粗筛125 + 精筛27组合
   - 输出: Top 30配对 + 专属参数

2. **Engine** (每5秒)
   - 读取三周期数据 (5m/15m/30m)
   - 15m主信号 + 5m确认 + 30m过滤
   - 持仓管理 + 状态更新

3. **Trader**
   - 双边同步下单
   - 成交确认 + 错误回滚
   - 账户同步

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置
vim config/config.yaml

# 3. 运行
python src/main.py

# 4. 查看面板
open http://localhost:8000
```

## 配置说明

见 `config/config.yaml` 注释

## 数据流转

```
Data-Core → Scanner → strategy.db → Engine → Trader → Binance
                ↓                           ↓
           pairs表                     positions表
                                         ↓
                                      trades表
```

## License

Private
