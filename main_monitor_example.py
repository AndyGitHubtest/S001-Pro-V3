#!/usr/bin/env python3
"""
S001-Pro V3 带全流程监控的主程序示例

集成点:
1. 初始化 ChainMonitor
2. 各模块嵌入监控点
3. Telegram 实时推送
4. 异常告警
"""

import os
import sys
import json
import time
import logging
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent / "src"))

from chain_monitor import ChainMonitor, ChainStage, init_monitor, get_monitor
from scanner_monitor import create_monitored_scanner
from trader_monitor import create_monitored_trader


def setup_logging():
    """配置日志"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler("logs/monitor.log"),
            logging.StreamHandler()
        ]
    )


def load_config():
    """加载配置"""
    config_path = Path("config/monitor.json")
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    return {}


def init_chain_monitor(config: dict):
    """
    初始化链路监控器
    
    配置格式:
    {
        "telegram": {
            "bot_token": "xxx",
            "chat_id": "xxx"
        },
        "enable_tg": true
    }
    """
    tg_config = config.get("telegram", {})
    bot_token = tg_config.get("bot_token", os.getenv("TG_BOT_TOKEN"))
    chat_id = tg_config.get("chat_id", os.getenv("TG_CHAT_ID"))
    
    if not bot_token or not chat_id:
        logging.warning("[Main] Telegram 配置缺失，监控将仅记录到本地")
    
    monitor = init_monitor(
        bot_token=bot_token,
        chat_id=chat_id
    )
    
    logging.info(f"[Main] ChainMonitor 初始化完成")
    logging.info(f"[Main] Telegram 推送: {'启用' if monitor.enable_tg else '禁用'}")
    
    return monitor


def run_scanner_cycle(scanner, monitor: ChainMonitor):
    """运行扫描周期（带监控）"""
    logging.info("[Main] 启动扫描周期...")
    
    try:
        # 全流程扫描
        pairs = scanner.scan_and_optimize()
        
        logging.info(f"[Main] 扫描完成，发现 {len(pairs)} 个配对")
        return pairs
        
    except Exception as e:
        logging.error(f"[Main] 扫描失败: {e}")
        raise


def run_engine_cycle(pairs, monitor: ChainMonitor):
    """运行引擎周期（带监控）"""
    from engine import SignalEngine  # 假设已有
    
    trace_id = monitor.start_trace(
        module="engine",
        stage=ChainStage.ENGINE_START,
        metadata={"pairs_count": len(pairs)}
    )
    
    try:
        engine = SignalEngine()
        signals = []
        
        for pair_data in pairs:
            pair = pair_data.get("pair", "unknown")
            
            # 价格更新监控
            price_trace = monitor.start_trace(
                module="engine",
                stage=ChainStage.ENGINE_PRICE,
                pair=pair
            )
            prices = engine.fetch_prices(pair)
            monitor.end_trace(price_trace, status="success")
            
            # 信号计算监控
            signal_trace = monitor.start_trace(
                module="engine",
                stage=ChainStage.ENGINE_SIGNAL,
                pair=pair
            )
            signal = engine.calculate_signal(pair, prices)
            monitor.end_trace(
                signal_trace,
                status="success",
                metadata={"z_score": signal.get("z_score"), "action": signal.get("action")}
            )
            
            if signal.get("action") in ["OPEN", "CLOSE"]:
                signals.append({"pair": pair, **signal})
        
        monitor.end_trace(
            trace_id,
            status="success",
            metadata={"signals_found": len(signals)}
        )
        
        return signals
        
    except Exception as e:
        monitor.end_trace(trace_id, status="failure", error=str(e))
        raise


def run_trader_cycle(signals, trader, monitor: ChainMonitor):
    """运行交易周期（带监控）"""
    trace_id = monitor.start_trace(
        module="trader",
        stage=ChainStage.TRADER_START,
        metadata={"signals_count": len(signals)}
    )
    
    executed = []
    
    try:
        for signal in signals:
            pair = signal["pair"]
            
            # 执行配对交易
            result_a, result_b = trader.execute_pair_trade(
                pair=pair,
                side_a=signal["side_a"],
                qty_a=signal["qty_a"],
                side_b=signal["side_b"],
                qty_b=signal["qty_b"],
                symbol_a=signal["symbol_a"],
                symbol_b=signal["symbol_b"]
            )
            
            if result_a.success and result_b.success:
                executed.append({
                    "pair": pair,
                    "order_a": result_a.order_id,
                    "order_b": result_b.order_id
                })
        
        monitor.end_trace(
            trace_id,
            status="success",
            metadata={"executed": len(executed)}
        )
        
        return executed
        
    except Exception as e:
        monitor.end_trace(trace_id, status="failure", error=str(e))
        raise


def run_monitor_cycle(monitor: ChainMonitor):
    """运行监控周期（带自我监控）"""
    self_trace = monitor.start_trace(
        module="monitor",
        stage=ChainStage.MONITOR_START
    )
    
    try:
        # 账户查询
        account_trace = monitor.start_trace(
            module="monitor",
            stage=ChainStage.MONITOR_ACCOUNT
        )
        # 查询账户...
        monitor.end_trace(account_trace, status="success")
        
        # 持仓查询
        position_trace = monitor.start_trace(
            module="monitor",
            stage=ChainStage.MONITOR_POSITION
        )
        # 查询持仓...
        monitor.end_trace(position_trace, status="success")
        
        # 发送汇总报告
        monitor.send_summary()
        
        monitor.end_trace(self_trace, status="success")
        
    except Exception as e:
        monitor.end_trace(self_trace, status="failure", error=str(e))
        raise


def main():
    """主函数"""
    print("=" * 60)
    print("S001-Pro V3 - 全流程链路监控版")
    print("=" * 60)
    
    # 1. 设置日志
    setup_logging()
    
    # 2. 加载配置
    config = load_config()
    
    # 3. 初始化监控器
    monitor = init_chain_monitor(config)
    
    # 4. 初始化模块
    db_path = config.get("database", "data/klines.db")
    scanner = create_monitored_scanner(db_path, monitor=monitor)
    
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    trader = create_monitored_trader(api_key, api_secret, monitor=monitor)
    
    print(f"[Main] 初始化完成，开始运行...")
    print(f"[Main] 按 Ctrl+C 停止")
    
    # 5. 主循环
    cycle = 0
    while True:
        try:
            cycle += 1
            print(f"\n[Main] ===== 周期 {cycle} =====")
            
            # Scanner: 扫描配对
            pairs = run_scanner_cycle(scanner, monitor)
            
            # Engine: 计算信号
            signals = run_engine_cycle(pairs, monitor)
            
            # Trader: 执行交易
            if signals:
                executed = run_trader_cycle(signals, trader, monitor)
                print(f"[Main] 执行 {len(executed)} 笔交易")
            
            # Monitor: 监控报告
            run_monitor_cycle(monitor)
            
            # 打印链路事件
            recent_events = monitor.get_events(limit=10)
            print(f"\n[Main] 最近链路事件:")
            for event in recent_events:
                status_icon = "✅" if event["status"] == "success" else "❌" if event["status"] == "failure" else "⏳"
                print(f"  {status_icon} {event['module']}:{event['stage']} - {event.get('duration_ms', 0):.1f}ms")
            
            # 等待下一周期
            time.sleep(config.get("cycle_interval", 60))
            
        except KeyboardInterrupt:
            print("\n[Main] 收到停止信号")
            break
        except Exception as e:
            logging.error(f"[Main] 周期异常: {e}")
            time.sleep(10)
    
    # 6. 清理
    monitor.stop()
    print("[Main] 已停止")


if __name__ == "__main__":
    main()
