#!/bin/bash
# S001-Pro V3 本地虚拟环境启动脚本
# 确保使用隔离的Python环境

set -e

echo "================================"
echo "  S001-Pro V3 本地启动"
echo "================================"
echo ""

# 检查Python
echo "[1/4] 检查Python..."
if ! command -v python3 &> /dev/null; then
    echo "错误: Python3 未安装"
    exit 1
fi

PYTHON_VERSION=$(python3 --version)
echo "  ✓ ${PYTHON_VERSION}"

# 创建/激活虚拟环境
echo ""
echo "[2/4] 准备虚拟环境..."
if [ ! -d "venv" ]; then
    echo "  创建虚拟环境..."
    python3 -m venv venv
fi

source venv/bin/activate
echo "  ✓ 虚拟环境已激活"
echo "  ✓ Python路径: $(which python)"

# 安装依赖
echo ""
echo "[3/4] 检查依赖..."
if [ -f "requirements.txt" ]; then
    pip install -q -r requirements.txt
    echo "  ✓ 依赖已安装/更新"
else
    echo "  警告: requirements.txt 不存在"
fi

# 检查环境变量
echo ""
echo "[4/4] 检查环境变量..."
if [ -z "$BINANCE_API_KEY" ]; then
    echo "  ⚠ BINANCE_API_KEY 未设置"
    echo "    export BINANCE_API_KEY='你的密钥'"
else
    echo "  ✓ BINANCE_API_KEY 已设置"
fi

if [ -z "$BINANCE_API_SECRET" ]; then
    echo "  ⚠ BINANCE_API_SECRET 未设置"
    echo "    export BINANCE_API_SECRET='你的密钥'"
else
    echo "  ✓ BINANCE_API_SECRET 已设置"
fi

# 显示配置
echo ""
echo "================================"
echo "  启动配置"
echo "================================"
echo "  虚拟环境: venv/"
echo "  Python: $(python --version)"
echo "  pip: $(pip --version | cut -d' ' -f1,2)"
echo ""
echo "  按 Enter 启动策略..."
read

# 启动策略
echo ""
echo "启动 S001-Pro V3..."
echo "================================"
python src/main.py
