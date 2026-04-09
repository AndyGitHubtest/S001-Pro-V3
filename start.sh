#!/bin/bash
# S001-Pro V3 启动脚本
# 适用于 1000 USDT 小资金账户

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 获取脚本目录
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}  S001-Pro V3 启动脚本${NC}"
echo -e "${GREEN}================================${NC}"
echo ""

# 检查Python
echo "[1/5] 检查Python环境..."
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}错误: Python3 未安装${NC}"
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
echo "  ✓ Python版本: $PYTHON_VERSION"

# 检查虚拟环境
echo ""
echo "[2/5] 检查虚拟环境..."
if [ -d "venv" ]; then
    echo "  ✓ 虚拟环境已存在"
    source venv/bin/activate
else
    echo "  创建虚拟环境..."
    python3 -m venv venv
    source venv/bin/activate
    echo "  ✓ 虚拟环境创建完成"
fi

# 检查依赖
echo ""
echo "[3/5] 检查依赖..."
if [ -f "requirements.txt" ]; then
    pip install -q -r requirements.txt
    echo "  ✓ 依赖已安装"
else
    echo -e "${YELLOW}  警告: requirements.txt 不存在${NC}"
fi

# 检查环境变量
echo ""
echo "[4/5] 检查环境变量..."
if [ -z "$BINANCE_API_KEY" ]; then
    echo -e "${YELLOW}  警告: BINANCE_API_KEY 未设置${NC}"
    echo "  请设置环境变量:"
    echo "    export BINANCE_API_KEY='你的API密钥'"
fi

if [ -z "$BINANCE_API_SECRET" ]; then
    echo -e "${YELLOW}  警告: BINANCE_API_SECRET 未设置${NC}"
    echo "  请设置环境变量:"
    echo "    export BINANCE_API_SECRET='你的API密钥'"
fi

# 检查数据目录
echo ""
echo "[5/5] 检查数据目录..."
mkdir -p data logs
echo "  ✓ 数据目录已就绪"

# 显示配置摘要
echo ""
echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}  配置摘要${NC}"
echo -e "${GREEN}================================${NC}"
echo "  账户资金: ~1000 USDT"
echo "  杠杆倍数: 5x"
echo "  单对金额: 100 USDT"
echo "  最大持仓: 3对"
echo "  日损上限: 50 USDT"
echo ""

# 询问是否启动
echo "是否启动策略? (y/n)"
read -r response

if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
    echo ""
    echo -e "${GREEN}启动 S001-Pro V3...${NC}"
    echo ""
    
    # 启动策略
    python3 src/main.py
else
    echo ""
    echo "取消启动"
    exit 0
fi
