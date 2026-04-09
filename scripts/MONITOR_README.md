# S001-Pro 监控报告脚本

## 功能

每10分钟自动收集 S001-Pro 实盘交易状态并发送到 Telegram：

- ✅ 进程状态检查
- ✅ 当前持仓监控
- ✅ 今日交易统计
- ✅ 盈亏计算
- ✅ 扫描状态
- ✅ 系统资源监控
- 🚨 进程停止告警

## 配置方法

### 方法1: 环境变量 (推荐)

```bash
export TELEGRAM_BOT_TOKEN="your_bot_token"
export TELEGRAM_CHAT_ID="your_chat_id"
```

### 方法2: config/monitor.json

编辑 `config/monitor.json` 文件：

```json
{
  "telegram": {
    "bot_token": "your_bot_token",
    "chat_id": "your_chat_id"
  }
}
```

### 方法3: config.yaml

编辑 `config/config.yaml` 文件：

```yaml
notification:
  telegram:
    enabled: true
    bot_token: "your_bot_token"
    chat_id: "your_chat_id"
```

## 使用方法

### 手动运行

```bash
cd ~/S001-Pro-V3
source venv/bin/activate
python scripts/monitor_and_report.py
```

### 添加到 crontab (每10分钟)

```bash
# 编辑 crontab
crontab -e

# 添加以下行 (请根据实际路径修改)
*/10 * * * * cd /home/ubuntu/strategies/S001-Pro && source venv/bin/activate && python scripts/monitor_and_report.py >> logs/monitor_cron.log 2>&1
```

### macOS 本地测试

```bash
cd ~/S001-Pro-V3
source venv/bin/activate
python scripts/monitor_and_report.py
```

## 报告示例

```
📊 S001-Pro 实盘监控报告
⏰ 2026-04-09 21:25:32

【进程状态】
🟢 状态: 运行中
🔢 进程数: 10

【持仓概况】
📈 持仓数量: 2
💰 未实现盈亏: +12.34 USDT

【当前持仓】
1. BTC-USDT/ETH-USDT | long | Z:2.34 | 🟢 +8.56
2. ADA-USDT/DOT-USDT | short | Z:-2.12 | 🟢 +3.78

【今日交易】
📊 总成交: 5 笔
💵 实现盈亏: +23.45 USDT
🟢 盈利: 4 笔 | 🔴 亏损: 1 笔

【扫描状态】
🔄 上次扫描: 2026-04-09 18:49:26
📋 配对数量: 28
⏱️ 耗时: 26370ms

【系统状态】
🖥️ CPU: 12.5% | MEM: 45.2%
💾 内存使用: 512.3 MB
⏲️ 运行时间: 2h30m
```

## 告警说明

当检测到以下情况时会发送告警：

1. 🔴 **进程停止** - S001-Pro 进程未运行
2. ⚠️ **数据库连接失败** - 无法读取策略数据库
3. ❌ **Telegram 发送失败** - 消息发送异常

## 文件说明

| 文件 | 说明 |
|------|------|
| `scripts/monitor_and_report.py` | 主监控脚本 |
| `config/monitor.json` | 监控配置文件 |
| `data/strategy.db` | 策略状态数据库 |
| `logs/monitor_cron.log` | Cron 运行日志 |

## 故障排除

### Telegram 配置未加载

检查环境变量是否正确设置：
```bash
echo $TELEGRAM_BOT_TOKEN
echo $TELEGRAM_CHAT_ID
```

### 数据库连接失败

检查数据库文件是否存在：
```bash
ls -la data/strategy.db
```

### 进程检测不准确

脚本使用 `pgrep` 检测 Python 进程，可能需要根据实际进程名调整匹配模式。
