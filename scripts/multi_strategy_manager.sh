#!/bin/bash
# 多策略管理器
# 每个策略独立虚拟环境，完全隔离

set -e

# 配置
SERVER_IP="43.160.192.48"
SERVER_USER="ubuntu"
BASE_DIR="/home/ubuntu/strategies"

echo "================================"
echo "  多策略隔离管理器"
echo "================================"
echo ""
echo "策略隔离原则:"
echo "  - 每个策略独立虚拟环境"
echo "  - 每个策略独立数据目录"
echo "  - 每个策略独立配置"
echo "  - 策略之间互不干扰"
echo ""

# 显示用法
show_usage() {
    echo "用法:"
    echo "  $0 deploy <strategy_name> <git_url>     # 部署新策略"
    echo "  $0 start <strategy_name>                # 启动策略"
    echo "  $0 stop <strategy_name>                 # 停止策略"
    echo "  $0 status                               # 查看所有策略状态"
    echo "  $0 logs <strategy_name>                 # 查看策略日志"
    echo ""
    echo "示例:"
    echo "  $0 deploy S001-Pro-V3 https://github.com/AndyGitHubtest/S001-Pro-V3.git"
    echo "  $0 start S001-Pro-V3"
    echo "  $0 status"
    echo ""
}

# 部署策略
deploy_strategy() {
    local STRATEGY_NAME=$1
    local GIT_URL=$2
    local STRATEGY_DIR="${BASE_DIR}/${STRATEGY_NAME}"
    
    echo "[部署策略] ${STRATEGY_NAME}"
    echo "================================"
    
    # 检查参数
    if [ -z "$STRATEGY_NAME" ] || [ -z "$GIT_URL" ]; then
        echo "错误: 缺少参数"
        show_usage
        exit 1
    fi
    
    # 检查API密钥
    if [ -z "$BINANCE_API_KEY" ] || [ -z "$BINANCE_API_SECRET" ]; then
        echo "错误: 请设置环境变量"
        echo "  export BINANCE_API_KEY='你的API密钥'"
        echo "  export BINANCE_API_SECRET='你的API密钥'"
        exit 1
    fi
    
    echo "[1/7] 创建策略目录..."
    ssh ${SERVER_USER}@${SERVER_IP} "mkdir -p ${BASE_DIR}"
    
    echo ""
    echo "[2/7] 克隆代码..."
    ssh ${SERVER_USER}@${SERVER_IP} "cd ${BASE_DIR} && \
        if [ -d ${STRATEGY_NAME} ]; then \
            echo '目录已存在，执行git pull...'; \
            cd ${STRATEGY_NAME} && git pull; \
        else \
            git clone ${GIT_URL} ${STRATEGY_NAME}; \
        fi"
    
    echo ""
    echo "[3/7] 创建独立虚拟环境..."
    ssh ${SERVER_USER}@${SERVER_IP} "cd ${STRATEGY_DIR} && \
        python3 -m venv venv --clear && \
        echo '虚拟环境创建完成' && \
        ls -la venv/"
    
    echo ""
    echo "[4/7] 安装依赖到虚拟环境..."
    ssh ${SERVER_USER}@${SERVER_IP} "cd ${STRATEGY_DIR} && \
        source venv/bin/activate && \
        pip install -q --upgrade pip && \
        pip install -q wheel && \
        pip install -q -r requirements.txt && \
        echo '依赖安装完成' && \
        pip list | head -10"
    
    echo ""
    echo "[5/7] 创建策略数据目录..."
    ssh ${SERVER_USER}@${SERVER_IP} "cd ${STRATEGY_DIR} && \
        mkdir -p data logs config && \
        echo '目录结构:' && \
        ls -la"
    
    echo ""
    echo "[6/7] 配置策略环境..."
    # 创建策略专属环境变量文件
    ssh ${SERVER_USER}@${SERVER_IP} "cat > ${STRATEGY_DIR}/.env <<EOF
# ${STRATEGY_NAME} 专属环境变量
export BINANCE_API_KEY=${BINANCE_API_KEY}
export BINANCE_API_SECRET=${BINANCE_API_SECRET}
export TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-}
export TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID:-}
export STRATEGY_NAME=${STRATEGY_NAME}
export STRATEGY_DIR=${STRATEGY_DIR}
EOF"
    
    echo ""
    echo "[7/7] 创建systemd服务..."
    local SERVICE_NAME="strategy-$(echo ${STRATEGY_NAME} | tr '[:upper:]' '[:lower:]' | tr '_' '-').service"
    
    ssh ${SERVER_USER}@${SERVER_IP} "sudo tee /etc/systemd/system/${SERVICE_NAME} > /dev/null <<EOF
[Unit]
Description=${STRATEGY_NAME} Trading Strategy
After=network.target

[Service]
Type=simple
User=${SERVER_USER}
WorkingDirectory=${STRATEGY_DIR}

# 虚拟环境Python
ExecStart=${STRATEGY_DIR}/venv/bin/python ${STRATEGY_DIR}/src/main.py
ExecStop=/bin/kill -SIGTERM \$MAINPID

# 环境变量
Environment=PYTHONPATH=${STRATEGY_DIR}/src
Environment=BINANCE_API_KEY=${BINANCE_API_KEY}
Environment=BINANCE_API_SECRET=${BINANCE_API_SECRET}
Environment=TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-}
Environment=TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID:-}

# 重启策略
Restart=on-failure
RestartSec=30
StartLimitInterval=300
StartLimitBurst=3

# 资源限制
MemoryLimit=512M
CPUQuota=50%

# 日志
StandardOutput=append:${STRATEGY_DIR}/logs/strategy.log
StandardError=append:${STRATEGY_DIR}/logs/error.log

[Install]
WantedBy=multi-user.target
EOF"
    
    ssh ${SERVER_USER}@${SERVER_IP} "sudo systemctl daemon-reload"
    
    echo ""
    echo "================================"
    echo "  策略部署完成!"
    echo "================================"
    echo ""
    echo "策略信息:"
    echo "  名称: ${STRATEGY_NAME}"
    echo "  目录: ${STRATEGY_DIR}"
    echo "  虚拟环境: ${STRATEGY_DIR}/venv"
    echo "  服务: ${SERVICE_NAME}"
    echo ""
    echo "启动命令:"
    echo "  手动: ssh ${SERVER_USER}@${SERVER_IP} 'cd ${STRATEGY_DIR} && source venv/bin/activate && python src/main.py'"
    echo "  服务: sudo systemctl start ${SERVICE_NAME}"
    echo ""
    echo "查看日志:"
    echo "  tail -f ${STRATEGY_DIR}/logs/strategy.log"
    echo ""
}

# 启动策略
start_strategy() {
    local STRATEGY_NAME=$1
    local SERVICE_NAME="strategy-$(echo ${STRATEGY_NAME} | tr '[:upper:]' '[:lower:]' | tr '_' '-').service"
    
    echo "[启动策略] ${STRATEGY_NAME}"
    ssh ${SERVER_USER}@${SERVER_IP} "sudo systemctl start ${SERVICE_NAME} && \
        echo '启动成功' && \
        sudo systemctl status ${SERVICE_NAME} --no-pager -l"
}

# 停止策略
stop_strategy() {
    local STRATEGY_NAME=$1
    local SERVICE_NAME="strategy-$(echo ${STRATEGY_NAME} | tr '[:upper:]' '[:lower:]' | tr '_' '-').service"
    
    echo "[停止策略] ${STRATEGY_NAME}"
    ssh ${SERVER_USER}@${SERVER_IP} "sudo systemctl stop ${SERVICE_NAME} && \
        echo '停止成功'"
}

# 查看状态
show_status() {
    echo "[策略状态]"
    echo "================================"
    
    ssh ${SERVER_USER}@${SERVER_IP} "
        echo '已安装的策略:' && \
        ls -1 ${BASE_DIR} 2>/dev/null || echo '无' && \
        echo '' && \
        echo '运行中的策略服务:' && \
        systemctl list-units --type=service --state=running | grep strategy- || echo '无' && \
        echo '' && \
        echo '磁盘使用:' && \
        df -h ${BASE_DIR}"
}

# 查看日志
show_logs() {
    local STRATEGY_NAME=$1
    local STRATEGY_DIR="${BASE_DIR}/${STRATEGY_NAME}"
    
    echo "[查看日志] ${STRATEGY_NAME}"
    echo "================================"
    
    ssh ${SERVER_USER}@${SERVER_IP} "
        if [ -f ${STRATEGY_DIR}/logs/strategy.log ]; then \
            tail -100 ${STRATEGY_DIR}/logs/strategy.log; \
        else \
            echo '日志文件不存在'; \
        fi"
}

# 主命令处理
case "$1" in
    deploy)
        deploy_strategy "$2" "$3"
        ;;
    start)
        start_strategy "$2"
        ;;
    stop)
        stop_strategy "$2"
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs "$2"
        ;;
    *)
        show_usage
        exit 1
        ;;
esac
