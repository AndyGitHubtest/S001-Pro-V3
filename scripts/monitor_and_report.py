#!/usr/bin/env python3
"""
S001-Pro 实盘交易监控报告脚本
每10分钟运行一次，收集状态并发送到 Telegram
"""

import os
import sys
import json
import sqlite3
import subprocess
import requests
from datetime import datetime, timedelta
from pathlib import Path

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 配置文件路径
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
STATE_DB_PATH = PROJECT_ROOT / "data" / "strategy.db"
LOG_DIR = PROJECT_ROOT / "logs"

# Telegram 配置 (优先级: 环境变量 > monitor.json > config.yaml)
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""


def load_telegram_config():
    """加载 Telegram 配置 (多来源)"""
    global TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    
    # 1. 尝试从环境变量读取
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
    
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        print("  从环境变量加载 Telegram 配置")
        return True
    
    # 2. 尝试从 monitor.json 读取
    monitor_config_path = PROJECT_ROOT / "config" / "monitor.json"
    if monitor_config_path.exists():
        try:
            with open(monitor_config_path, 'r') as f:
                cfg = json.load(f)
                if cfg.get("telegram", {}).get("bot_token"):
                    TELEGRAM_BOT_TOKEN = cfg["telegram"]["bot_token"]
                    TELEGRAM_CHAT_ID = str(cfg["telegram"].get("chat_id", ""))
                    print("  从 monitor.json 加载 Telegram 配置")
                    return True
        except Exception as e:
            print(f"  读取 monitor.json 失败: {e}")
    
    # 3. 尝试从 config.yaml 读取
    if CONFIG_PATH.exists():
        try:
            import yaml
            with open(CONFIG_PATH, 'r') as f:
                cfg = yaml.safe_load(f)
                notify_cfg = cfg.get("notification", {}).get("telegram", {})
                if notify_cfg.get("bot_token"):
                    TELEGRAM_BOT_TOKEN = notify_cfg["bot_token"]
                    TELEGRAM_CHAT_ID = str(notify_cfg.get("chat_id", ""))
                    print("  从 config.yaml 加载 Telegram 配置")
                    return True
        except Exception as e:
            print(f"  读取 config.yaml 失败: {e}")
    
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def check_process_status():
    """检查 S001-Pro 进程状态"""
    try:
        # 检查是否有 Python 进程运行 S001-Pro
        result = subprocess.run(
            ["pgrep", "-f", "S001-Pro|s001_pro|main.py"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            pids = result.stdout.strip().split('\n')
            return {
                "status": "running",
                "pids": [p for p in pids if p],
                "count": len([p for p in pids if p])
            }
        else:
            return {
                "status": "stopped",
                "pids": [],
                "count": 0
            }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "pids": [],
            "count": 0
        }


def get_db_connection():
    """获取数据库连接"""
    if not STATE_DB_PATH.exists():
        return None
    try:
        return sqlite3.connect(str(STATE_DB_PATH))
    except Exception as e:
        print(f"数据库连接失败: {e}")
        return None


def get_open_positions(conn):
    """获取当前持仓"""
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                pair_key, pool, direction, entry_z, current_z,
                entry_price_a, entry_price_b, 
                unrealized_pnl, entry_time, status
            FROM positions 
            WHERE status = 'open'
            ORDER BY entry_time DESC
        """)
        rows = cursor.fetchall()
        
        positions = []
        for row in rows:
            positions.append({
                "pair_key": row[0],
                "pool": row[1],
                "direction": row[2],
                "entry_z": row[3],
                "current_z": row[4],
                "entry_price_a": row[5],
                "entry_price_b": row[6],
                "unrealized_pnl": row[7] or 0,
                "entry_time": row[8],
                "status": row[9]
            })
        return positions
    except Exception as e:
        print(f"获取持仓失败: {e}")
        return []


def get_today_trades(conn):
    """获取今日交易"""
    try:
        cursor = conn.cursor()
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute("""
            SELECT 
                pair_key, direction, entry_time, exit_time,
                pnl, pnl_pct, exit_reason
            FROM trades 
            WHERE DATE(entry_time) = ?
            ORDER BY entry_time DESC
        """, (today,))
        rows = cursor.fetchall()
        
        trades = []
        for row in rows:
            trades.append({
                "pair_key": row[0],
                "direction": row[1],
                "entry_time": row[2],
                "exit_time": row[3],
                "pnl": row[4] or 0,
                "pnl_pct": row[5] or 0,
                "exit_reason": row[6]
            })
        return trades
    except Exception as e:
        print(f"获取今日交易失败: {e}")
        return []


def get_scan_info(conn):
    """获取扫描信息"""
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT scan_time, pool, layer3_passed, duration_ms 
            FROM scan_history 
            ORDER BY scan_time DESC 
            LIMIT 1
        """)
        row = cursor.fetchone()
        
        if row:
            return {
                "last_scan": row[0],
                "pool": row[1],
                "pairs_count": row[2],
                "duration_ms": row[3]
            }
        return None
    except Exception as e:
        print(f"获取扫描信息失败: {e}")
        return None


def format_message(process_status, positions, trades, scan_info, system_status):
    """格式化 Telegram 消息"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # 计算盈亏统计
    total_unrealized = sum(p["unrealized_pnl"] for p in positions)
    total_realized = sum(t["pnl"] for t in trades)
    win_count = len([t for t in trades if t["pnl"] > 0])
    loss_count = len([t for t in trades if t["pnl"] <= 0])
    
    # 进程状态表情
    if process_status["status"] == "running":
        status_emoji = "🟢"
        status_text = "运行中"
    elif process_status["status"] == "stopped":
        status_emoji = "🔴"
        status_text = "已停止"
    else:
        status_emoji = "⚠️"
        status_text = f"错误: {process_status.get('error', 'Unknown')}"
    
    message = f"""📊 <b>S001-Pro 实盘监控报告</b>
⏰ <code>{now}</code>

<b>【进程状态】</b>
{status_emoji} 状态: {status_text}
🔢 进程数: {process_status.get('count', 0)}

<b>【持仓概况】</b>
📈 持仓数量: {len(positions)}
💰 未实现盈亏: <code>{total_unrealized:+.2f}</code> USDT
"""
    
    # 添加持仓详情
    if positions:
        message += "\n<b>【当前持仓】</b>\n"
        for i, pos in enumerate(positions[:5], 1):  # 最多显示5个
            pnl_emoji = "🟢" if pos["unrealized_pnl"] >= 0 else "🔴"
            message += f"{i}. {pos['pair_key']} | {pos['direction']} | Z:{pos['current_z']:.2f} | {pnl_emoji} {pos['unrealized_pnl']:+.2f}\n"
    
    # 添加今日交易统计
    message += f"""
<b>【今日交易】</b>
📊 总成交: {len(trades)} 笔
💵 实现盈亏: <code>{total_realized:+.2f}</code> USDT
🟢 盈利: {win_count} 笔 | 🔴 亏损: {loss_count} 笔
"""
    
    # 添加扫描信息
    if scan_info:
        message += f"""
<b>【扫描状态】</b>
🔄 上次扫描: {scan_info['last_scan']}
📋 配对数量: {scan_info['pairs_count']}
⏱️ 耗时: {scan_info['duration_ms']}ms
"""
    
    message += f"""
<b>【系统状态】</b>
🖥️ CPU: {system_status.get('cpu_percent', 'N/A')}% | MEM: {system_status.get('memory_percent', 'N/A')}%
💾 内存使用: {system_status.get('memory_mb', 'N/A')} MB
⏲️ 运行时间: {system_status.get('uptime', 'N/A')}
"""
    
    return message


def send_telegram_message(message):
    """发送 Telegram 消息"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram 配置不完整，跳过发送")
        print(f"Token: {'已设置' if TELEGRAM_BOT_TOKEN else '未设置'}")
        print(f"Chat ID: {'已设置' if TELEGRAM_CHAT_ID else '未设置'}")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        
        response = requests.post(url, json=payload, timeout=30)
        
        if response.status_code == 200:
            print("✅ Telegram 消息发送成功")
            return True
        else:
            print(f"❌ Telegram 发送失败: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"❌ Telegram 发送异常: {e}")
        return False


def send_alert(message):
    """发送告警消息"""
    alert_message = f"""🚨 <b>S001-Pro 告警</b>

{message}

⏰ <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>
"""
    return send_telegram_message(alert_message)


def get_system_status():
    """获取系统状态"""
    try:
        import psutil
        return {
            "cpu_percent": psutil.cpu_percent(interval=1),
            "memory_percent": psutil.virtual_memory().percent,
            "memory_mb": round(psutil.virtual_memory().used / 1024 / 1024, 1),
            "uptime": "N/A"
        }
    except Exception as e:
        return {
            "cpu_percent": "N/A",
            "memory_percent": "N/A",
            "memory_mb": "N/A",
            "uptime": "N/A",
            "error": str(e)
        }


def main():
    """主函数"""
    print(f"\n{'='*60}")
    print(f"S001-Pro 监控报告 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")
    
    # 加载配置
    print("[0/5] 加载 Telegram 配置...")
    config_loaded = load_telegram_config()
    print(f"  配置状态: {'✅ 已加载' if config_loaded else '⚠️ 未配置'}")
    
    # 检查进程状态
    print("[1/5] 检查进程状态...")
    process_status = check_process_status()
    print(f"  状态: {process_status['status']}, 进程数: {process_status['count']}")
    
    # 如果进程停止，发送告警
    if process_status["status"] == "stopped":
        print("  ⚠️ 警告: S001-Pro 进程已停止!")
        send_alert("🔴 <b>进程已停止</b>\n\nS001-Pro 实盘交易进程未运行，请立即检查!")
        return 1
    
    # 获取系统状态
    print("[2/5] 获取系统状态...")
    system_status = get_system_status()
    print(f"  CPU: {system_status.get('cpu_percent', 'N/A')}%, MEM: {system_status.get('memory_percent', 'N/A')}%")
    
    # 连接数据库
    print("[3/5] 连接数据库...")
    conn = get_db_connection()
    if not conn:
        print("  ❌ 数据库连接失败")
        send_alert("⚠️ <b>数据库连接失败</b>\n\n无法连接到策略数据库，请检查文件权限!")
        return 1
    print("  ✅ 数据库连接成功")
    
    try:
        # 获取持仓
        print("[4/5] 获取持仓信息...")
        positions = get_open_positions(conn)
        print(f"  当前持仓: {len(positions)}")
        
        # 获取今日交易
        print("[5/5] 获取今日交易...")
        trades = get_today_trades(conn)
        print(f"  今日交易: {len(trades)}")
        
        # 获取扫描信息
        scan_info = get_scan_info(conn)
        
        # 格式化并发送消息
        print("\n[发送报告到 Telegram]...")
        message = format_message(process_status, positions, trades, scan_info, system_status)
        send_telegram_message(message)
        
        print("\n✅ 监控报告完成")
        return 0
        
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n用户中断")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 监控脚本异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
