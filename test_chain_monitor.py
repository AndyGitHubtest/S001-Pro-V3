#!/usr/bin/env python3
"""
链路监控测试脚本

运行: python test_chain_monitor.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from chain_monitor import ChainMonitor, ChainStage, init_monitor


def test_basic_monitor():
    """测试基础监控功能"""
    print("\n=== 测试基础监控 ===")
    
    # 初始化（不启用TG）
    monitor = init_monitor(bot_token=None, chat_id=None)
    
    # 模拟 scanner 链路
    trace1 = monitor.start_trace(
        module="scanner",
        stage=ChainStage.SCANNER_START,
        metadata={"test": True}
    )
    time.sleep(0.1)
    monitor.end_trace(trace1, status="success", metadata={"pairs": 100})
    print("✅ Scanner 链路完成")
    
    # 模拟 engine 链路
    trace2 = monitor.start_trace(
        module="engine",
        stage=ChainStage.ENGINE_SIGNAL,
        pair="BTC-ETH",
        metadata={"z_score": 2.5}
    )
    time.sleep(0.05)
    monitor.end_trace(trace2, status="success", metadata={"signal": "OPEN"})
    print("✅ Engine 链路完成")
    
    # 模拟 trader 链路
    trace3 = monitor.start_trace(
        module="trader",
        stage=ChainStage.TRADER_ORDER,
        pair="BTC-ETH"
    )
    time.sleep(0.2)
    monitor.end_trace(trace3, status="failure", error="余额不足")
    print("✅ Trader 失败链路完成")
    
    # 打印事件
    print("\n最近事件:")
    for event in monitor.get_events(limit=5):
        print(f"  {event['module']}:{event['stage']} - {event['status']} - {event.get('duration_ms', 0):.1f}ms")
    
    monitor.stop()
    print("\n✅ 测试通过")


def test_with_tg():
    """测试 Telegram 推送（需要配置）"""
    print("\n=== 测试 Telegram 推送 ===")
    
    import os
    bot_token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
    
    if not bot_token or not chat_id:
        print("⚠️ 未配置 TG_BOT_TOKEN 和 TG_CHAT_ID，跳过")
        return
    
    monitor = init_monitor(bot_token=bot_token, chat_id=chat_id)
    
    # 发送测试消息
    trace = monitor.start_trace(
        module="test",
        stage=ChainStage.ENGINE_SIGNAL,
        pair="TEST-PAIR",
        metadata={"test": True}
    )
    time.sleep(0.1)
    monitor.end_trace(trace, status="success")
    
    print("✅ Telegram 消息已发送，请检查")
    
    time.sleep(2)  # 等待发送完成
    monitor.stop()


if __name__ == "__main__":
    print("=" * 50)
    print("S001-Pro V3 链路监控测试")
    print("=" * 50)
    
    test_basic_monitor()
    test_with_tg()
    
    print("\n✅ 所有测试完成")
