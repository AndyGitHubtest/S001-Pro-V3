#!/usr/bin/env python3
"""
S001-Pro V3 全流程链路监控模块 - 精简版
"""

import time
import json
import uuid
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import threading
import requests


class ChainStage(Enum):
    """链路阶段"""
    SCANNER_START = "scanner_start"
    SCANNER_FETCH = "scanner_fetch"
    SCANNER_CALC = "scanner_calc"
    SCANNER_FILTER = "scanner_filter"
    SCANNER_DONE = "scanner_done"
    ENGINE_START = "engine_start"
    ENGINE_PRICE = "engine_price"
    ENGINE_SIGNAL = "engine_signal"
    ENGINE_DECISION = "engine_decision"
    TRADER_START = "trader_start"
    TRADER_VALIDATE = "trader_validate"
    TRADER_ORDER = "trader_order"
    TRADER_CONFIRM = "trader_confirm"
    MONITOR_START = "monitor_start"
    MONITOR_ACCOUNT = "monitor_account"
    MONITOR_POSITION = "monitor_position"
    ERROR = "error"


@dataclass
class ChainEvent:
    trace_id: str
    stage: ChainStage
    module: str
    pair: Optional[str]
    status: str
    timestamp: float
    duration_ms: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


class ChainMonitor:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, bot_token: str = None, chat_id: str = None):
        if self._initialized:
            return
        self._initialized = True
        
        self.logger = logging.getLogger("ChainMonitor")
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enable_tg = bool(bot_token and chat_id)
        self._events: List[ChainEvent] = []
        self._active_traces: Dict = {}
        self._lock = threading.RLock()
        
        self.logger.info("[ChainMonitor] 初始化完成")
    
    def _send_tg(self, message: str):
        """发送 Telegram 消息"""
        if not self.enable_tg:
            return
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            requests.post(url, json={
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML"
            }, timeout=5)
        except Exception as e:
            self.logger.error(f"TG发送失败: {e}")
    
    def start_trace(self, module: str, stage: ChainStage, 
                    pair: str = None, metadata: dict = None) -> str:
        """开始追踪"""
        trace_id = str(uuid.uuid4())[:12]
        
        event = ChainEvent(
            trace_id=trace_id,
            stage=stage,
            module=module,
            pair=pair,
            status="start",
            timestamp=time.time(),
            metadata=metadata or {}
        )
        
        with self._lock:
            self._active_traces[trace_id] = {
                "start_time": event.timestamp,
                "module": module,
                "stage": stage.value,
                "pair": pair
            }
            self._events.append(event)
        
        # 低优先级通知
        self._send_tg(self._format_msg(event, is_start=True))
        return trace_id
    
    def end_trace(self, trace_id: str, status: str = "success",
                  metadata: dict = None, error: str = None):
        """结束追踪"""
        with self._lock:
            trace_info = self._active_traces.pop(trace_id, None)
        
        if not trace_info:
            return
        
        duration_ms = (time.time() - trace_info["start_time"]) * 1000
        
        event = ChainEvent(
            trace_id=trace_id,
            stage=ChainStage(trace_info["stage"]),
            module=trace_info["module"],
            pair=trace_info.get("pair"),
            status=status,
            timestamp=time.time(),
            duration_ms=duration_ms,
            metadata=metadata or {},
            error=error
        )
        
        with self._lock:
            self._events.append(event)
        
        # 发送通知
        self._send_tg(self._format_msg(event, is_start=False))
        
        # 失败告警
        if status == "failure":
            self._alert(f"🚨 <b>链路异常</b>\n{event.module}:{event.stage.value}\n错误: {error}")
    
    def _format_msg(self, event: ChainEvent, is_start: bool) -> str:
        """格式化消息"""
        icons = {"start": "⏳", "success": "✅", "failure": "❌"}
        mod_icons = {"scanner": "🔍", "engine": "⚙️", "trader": "💰", "monitor": "📊"}
        
        icon = icons.get(event.status, "⚪")
        mod_icon = mod_icons.get(event.module, "📦")
        
        lines = [f"{icon} <b>{mod_icon} {event.module.upper()}</b>"]
        lines.append(f"阶段: <code>{event.stage.value}</code>")
        
        if event.pair:
            lines.append(f"交易对: <code>{event.pair}</code>")
        
        if event.duration_ms:
            lines.append(f"耗时: <code>{event.duration_ms:.1f}ms</code>")
        
        if event.error:
            lines.append(f"错误: <code>{event.error[:100]}</code>")
        
        lines.append(f"🆔 <code>{event.trace_id}</code>")
        return "\n".join(lines)
    
    def _alert(self, message: str):
        """发送告警"""
        self._send_tg(message)
    
    def get_events(self, limit: int = 100) -> List[dict]:
        """获取最近事件"""
        with self._lock:
            return [self._event_to_dict(e) for e in self._events[-limit:]]
    
    def _event_to_dict(self, event: ChainEvent) -> dict:
        return {
            "trace_id": event.trace_id,
            "stage": event.stage.value,
            "module": event.module,
            "pair": event.pair,
            "status": event.status,
            "duration_ms": event.duration_ms,
            "error": event.error,
            "time": datetime.fromtimestamp(event.timestamp).strftime("%H:%M:%S")
        }


# 全局实例
_monitor = None

def init_monitor(bot_token: str, chat_id: str):
    """初始化监控器"""
    global _monitor
    _monitor = ChainMonitor(bot_token, chat_id)
    return _monitor

def get_monitor() -> ChainMonitor:
    """获取监控器"""
    global _monitor
    if _monitor is None:
        _monitor = ChainMonitor()
    return _monitor


def trace(module: str, stage: ChainStage, pair: str = None):
    """装饰器：监控函数执行"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            monitor = get_monitor()
            trace_id = monitor.start_trace(module, stage, pair)
            try:
                result = func(*args, **kwargs)
                monitor.end_trace(trace_id, "success")
                return result
            except Exception as e:
                monitor.end_trace(trace_id, "failure", error=str(e))
                raise
        return wrapper
    return decorator
