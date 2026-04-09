"""
系统资源监控模块
提供CPU/内存/磁盘/运行时间等指标
"""
import psutil
import os
import time
from datetime import datetime, timedelta
from typing import Dict
import logging

logger = logging.getLogger(__name__)


class SystemMonitor:
    """系统资源监控器"""
    
    def __init__(self):
        self.start_time = time.time()
        self.pid = os.getpid()
        try:
            self.process = psutil.Process(self.pid)
        except Exception as e:
            logger.warning(f"无法获取进程信息: {e}")
            self.process = None
    
    def get_stats(self) -> Dict:
        """获取系统资源统计"""
        try:
            # 进程资源
            if self.process:
                proc_info = {
                    "cpu_percent": round(self.process.cpu_percent(), 1),
                    "memory_mb": round(self.process.memory_info().rss / 1024 / 1024, 1),
                    "memory_percent": round(self.process.memory_percent(), 1),
                    "threads": self.process.num_threads(),
                }
            else:
                proc_info = {
                    "cpu_percent": 0,
                    "memory_mb": 0,
                    "memory_percent": 0,
                    "threads": 0,
                }
            
            # 系统资源
            sys_info = {
                "disk_percent": psutil.disk_usage('.').percent,
                "load_avg": list(psutil.getloadavg()) if hasattr(psutil, 'getloadavg') else [0, 0, 0],
            }
            
            # 运行时间
            uptime_seconds = time.time() - self.start_time
            uptime_str = self._format_duration(uptime_seconds)
            
            return {
                "status": "healthy" if proc_info["memory_percent"] < 80 else "warning",
                "uptime_seconds": int(uptime_seconds),
                "uptime": uptime_str,
                **proc_info,
                **sys_info,
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"获取系统资源失败: {e}")
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    @staticmethod
    def _format_duration(seconds: float) -> str:
        """格式化持续时间"""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds/60)}m"
        elif seconds < 86400:
            hours = int(seconds / 3600)
            minutes = int((seconds % 3600) / 60)
            return f"{hours}h{minutes}m"
        else:
            days = int(seconds / 86400)
            hours = int((seconds % 86400) / 3600)
            return f"{days}d{hours}h"


# 单例
_system_monitor = None

def get_system_monitor() -> SystemMonitor:
    """获取系统监控器实例"""
    global _system_monitor
    if _system_monitor is None:
        _system_monitor = SystemMonitor()
    return _system_monitor


def get_system_stats() -> Dict:
    """便捷获取系统统计"""
    return get_system_monitor().get_stats()


# 使用示例
if __name__ == "__main__":
    import json
    monitor = SystemMonitor()
    stats = monitor.get_stats()
    print(json.dumps(stats, indent=2, ensure_ascii=False))
