#!/bin/bash
# S001-Pro V3 服务器部署脚本
# 一键部署到 Ubuntu 服务器

set -e

# 配置
SERVER_IP="43.160.192.48"
SERVER_USER="ubuntu"
REPO_URL="https://github.com/AndyGitHubtest/S001-Pro-V3.git"
PROJECT_DIR="/home/ubuntu/strategies/S001-Pro-V3"

echo "================================"
echo "  S001-Pro V3 服务器部署"
echo "================================"
echo ""

# 检查环境变量
if [ -z "$BINANCE_API_KEY" ] || [ -z "$BINANCE_API_SECRET" ]; then
    echo "错误: 请设置环境变量"
    echo "  export BINANCE_API_KEY='你的API密钥'"
    echo "  export BINANCE_API_SECRET='你的API密钥'"
    exit 1
fi

echo "[1/6] 连接服务器并创建目录..."
ssh ${SERVER_USER}@${SERVER_IP} "mkdir -p ~/strategies"

echo ""
echo "[2/6] 克隆/更新代码..."
ssh ${SERVER_USER}@${SERVER_IP} "cd ~/strategies && \
    if [ -d S001-Pro-V3 ]; then \
        cd S001-Pro-V3 && git pull; \
    else \
        git clone ${REPO_URL}; \
    fi"

echo ""
echo "[3/6] 安装Python依赖..."
ssh ${SERVER_USER}@${SERVER_IP} "cd ${PROJECT_DIR} && \
    python3 -m venv venv && \
    source venv/bin/activate && \
    pip install -q --upgrade pip && \
    pip install -q -r requirements.txt"

echo ""
echo "[4/6] 配置环境变量..."
ssh ${SERVER_USER}@${SERVER_IP} "echo 'export BINANCE_API_KEY=${BINANCE_API_KEY}' >> ~/.bashrc && \
    echo 'export BINANCE_API_SECRET=${BINANCE_API_SECRET}' >> ~/.bashrc && \
    echo 'export TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-}' >> ~/.bashrc && \
    echo 'export TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID:-}' >> ~/.bashrc"

echo ""
echo "[5/6] 创建数据目录..."
ssh ${SERVER_USER}@${SERVER_IP} "cd ${PROJECT_DIR} && mkdir -p data logs"

echo ""
echo "[6/6] 配置systemd服务..."
# 创建服务文件
ssh ${SERVER_USER}@${SERVER_IP} "sudo tee /etc/systemd/system/s001-pro-v3.service > /dev/null <<EOF
[Unit]
Description=S001-Pro V3 Statistical Arbitrage Strategy
After=network.target

[Service]
Type=simple
User=${SERVER_USER}
WorkingDirectory=${PROJECT_DIR}
Environment=PYTHONPATH=${PROJECT_DIR}/src
Environment=BINANCE_API_KEY=${BINANCE_API_KEY}
Environment=BINANCE_API_SECRET=${BINANCE_API_SECRET}
Environment=TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-}
Environment=TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID:-}

ExecStart=${PROJECT_DIR}/venv/bin/python src/main.py
ExecStop=/bin/kill -SIGTERM \$MAINPID
Restart=on-failure
RestartSec=30

MemoryLimit=512M
CPUQuota=50%

StandardOutput=append:${PROJECT_DIR}/logs/strategy.log
StandardError=append:${PROJECT_DIR}/logs/strategy_error.log

[Install]
WantedBy=multi-user.target
EOF"

echo ""
echo "================================"
echo "  部署完成!"
echo "================================"
echo ""
echo "启动命令:"
echo "  ssh ${SERVER_USER}@${SERVER_IP}"
echo "  cd ${PROJECT_DIR}"
echo "  source venv/bin/activate"
echo "  python src/main.py"
echo ""
echo "或使用systemd:"
echo "  sudo systemctl start s001-pro-v3"
echo "  sudo systemctl status s001-pro-v3"
echo ""
echo "查看日志:"
echo "  tail -f ${PROJECT_DIR}/logs/strategy.log"
echo ""
