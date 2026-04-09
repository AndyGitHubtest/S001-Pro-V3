# S001-Pro V3 部署指南

## 📋 前置条件

- Python 3.10+
- 1000+ USDT (推荐)
- Binance API密钥 (永续合约)

---

## 🚀 快速部署 (Mac 本地)

### 1. 克隆仓库

```bash
git clone <your-repo-url>
cd S001-Pro-V3
```

### 2. 配置环境变量

```bash
export BINANCE_API_KEY='你的API密钥'
export BINANCE_API_SECRET='你的API密钥'

# 可选: Telegram通知
export TELEGRAM_BOT_TOKEN='你的Bot Token'
export TELEGRAM_CHAT_ID='你的Chat ID'
```

### 3. 运行部署检查

```bash
python scripts/deploy_check.py
```

### 4. 启动策略

```bash
./start.sh
```

---

## 🖥️ 服务器部署 (Ubuntu)

### 1. 上传代码

```bash
# 本地打包
cd ~/S001-Pro-V3
git push origin main

# 服务器拉取
ssh ubuntu@43.160.192.48
cd ~/strategies
git clone https://github.com/AndyGitHubtest/S001-Pro-V3.git
cd S001-Pro-V3
```

### 2. 安装依赖

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. 配置环境变量

编辑 `~/.bashrc`:

```bash
export BINANCE_API_KEY='你的API密钥'
export BINANCE_API_SECRET='你的API密钥'
```

```bash
source ~/.bashrc
```

### 4. 配置systemd服务

```bash
sudo cp scripts/s001-pro-v3.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable s001-pro-v3
```

**编辑服务文件，填入API密钥:**

```bash
sudo nano /etc/systemd/system/s001-pro-v3.service
# 修改 Environment=BINANCE_API_KEY=...
# 修改 Environment=BINANCE_API_SECRET=...
```

### 5. 启动服务

```bash
sudo systemctl start s001-pro-v3
sudo systemctl status s001-pro-v3
```

### 6. 查看日志

```bash
# 实时日志
sudo journalctl -u s001-pro-v3 -f

# 日志文件
tail -f logs/strategy.log
```

---

## ⚙️ 资金配置 (1000 USDT)

当前配置适合小资金账户:

| 参数 | 值 | 说明 |
|------|-----|------|
| 杠杆 | 5x | 保守杠杆 |
| 单对金额 | 100 USDT | 每对100U保证金 |
| 最大持仓 | 3对 | 最多300U保证金 |
| 日损上限 | 50 USDT | 5%止损线 |
| 熔断机制 | 3单亏损 | 连续3单亏损暂停60分钟 |

---

## 🔍 监控

### Web面板

启动后访问: http://localhost:8000

### 日志

- 策略日志: `logs/strategy.log`
- 错误日志: `logs/strategy_error.log`

### Telegram通知

配置后接收:
- 开仓通知
- 平仓通知
- 错误告警

---

## ⚠️ 风险提示

1. **小资金管理**: 1000U为小资金，建议严格按配置运行
2. **API安全**: 不要将API密钥提交到Git仓库
3. **网络稳定**: 确保服务器网络稳定，避免断连导致裸仓
4. **首次运行**: 建议先观察1-2天，确认稳定后再长期运行

---

## 🆘 故障排查

### 策略无法启动

```bash
# 检查依赖
pip install -r requirements.txt

# 检查配置
python scripts/deploy_check.py

# 检查日志
tail -100 logs/strategy_error.log
```

### API连接失败

```bash
# 检查API密钥
env | grep BINANCE

# 检查网络
curl https://api.binance.com/api/v3/ping
```

### 订单异常

重启后会自动执行订单恢复流程，检查日志中的 `[Recovery]` 部分。

---

## 📞 支持

有问题查看:
1. `logs/strategy.log` - 详细运行日志
2. `config/config.yaml` - 配置参数
3. 测试: `python -m pytest tests/ -v`
