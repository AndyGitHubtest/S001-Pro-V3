"""
内存监控器
监控内存使用，防止OOM，自动触发GC
"""
import os
import gc
import time
import threading
from typing import Dict, Optional, Callable
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)

# 尝试导入psutil
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logger.warning("psutil未安装，内存监控功能受限")


@dataclass
class MemoryStats:
    """内存统计"""
    timestamp: float
    rss_mb: float          # 实际使用内存
    vms_mb: float          # 虚拟内存
    percent: float         # 内存使用率
    available_mb: float    # 可用内存
    gc_count: int          # GC次数
    gc_objects: int        # GC对象数


class MemoryMonitor:
    """
    内存监控器
    
    功能:
    1. 实时监控内存使用
    2. 内存告警
    3. 自动GC触发
    4. OOM预防
    """
    
    def __init__(self,
                 warning_threshold: float = 70.0,  # 70%告警
                 critical_threshold: float = 85.0,  # 85%严重
                 auto_gc_threshold: float = 75.0,   # 75%自动GC
                 check_interval: float = 10.0):     # 10秒检查
        """
        Args:
            warning_threshold: 告警阈值(%)
            critical_threshold: 严重阈值(%)
            auto_gc_threshold: 自动GC阈值(%)
            check_interval: 检查间隔(秒)
        """
        self.warning_threshold = warning_threshold
        self.critical_threshold = critical_threshold
        self.auto_gc_threshold = auto_gc_threshold
        self.check_interval = check_interval
        
        self.running = False
        self.monitor_thread: Optional[threading.Thread] = None
        
        # 统计
        self.history: list = []
        self.max_history = 1000
        self.gc_triggered = 0
        self.alert_count = 0
        
        # 告警回调
        self.alert_callbacks: list = []
        
        # 进程信息
        if PSUTIL_AVAILABLE:
            self.process = psutil.Process(os.getpid())
        else:
            self.process = None
    
    def start(self):
        """启动监控"""
        if self.running:
            return
        
        self.running = True
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True
        )
        self.monitor_thread.start()
        logger.info(f"内存监控启动，检查间隔{self.check_interval}秒")
    
    def stop(self):
        """停止监控"""
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        logger.info("内存监控停止")
    
    def _monitor_loop(self):
        """监控循环"""
        while self.running:
            try:
                stats = self._collect_stats()
                self._check_thresholds(stats)
                self._store_stats(stats)
            except Exception as e:
                logger.error(f"内存监控错误: {e}")
            
            time.sleep(self.check_interval)
    
    def _collect_stats(self) -> MemoryStats:
        """收集内存统计"""
        if PSUTIL_AVAILABLE and self.process:
            # 使用psutil获取详细内存信息
            mem_info = self.process.memory_info()
            system_mem = psutil.virtual_memory()
            
            return MemoryStats(
                timestamp=time.time(),
                rss_mb=mem_info.rss / 1024 / 1024,
                vms_mb=mem_info.vms / 1024 / 1024,
                percent=system_mem.percent,
                available_mb=system_mem.available / 1024 / 1024,
                gc_count=len(gc.get_stats()),
                gc_objects=len(gc.get_objects())
            )
        else:
            # 简化版
            import sys
            return MemoryStats(
                timestamp=time.time(),
                rss_mb=0,
                vms_mb=0,
                percent=50.0,  # 模拟值
                available_mb=0,
                gc_count=gc.get_count()[0],
                gc_objects=0
            )
    
    def _check_thresholds(self, stats: MemoryStats):
        """检查阈值"""
        # 自动GC
        if stats.percent > self.auto_gc_threshold:
            self._trigger_gc()
        
        # 告警
        if stats.percent > self.critical_threshold:
            self._trigger_alert('CRITICAL', stats)
        elif stats.percent > self.warning_threshold:
            self._trigger_alert('WARNING', stats)
    
    def _trigger_gc(self):
        """触发垃圾回收"""
        self.gc_triggered += 1
        
        # 收集前内存
        if PSUTIL_AVAILABLE and self.process:
            mem_before = self.process.memory_info().rss / 1024 / 1024
        else:
            mem_before = 0
        
        # 执行GC
        gc.collect()
        
        # 收集后内存
        if PSUTIL_AVAILABLE and self.process:
            mem_after = self.process.memory_info().rss / 1024 / 1024
            freed = mem_before - mem_after
            logger.info(f"自动GC触发，释放内存: {freed:.1f}MB")
        else:
            logger.info("自动GC触发")
    
    def _trigger_alert(self, level: str, stats: MemoryStats):
        """触发告警"""
        self.alert_count += 1
        
        message = (f"🚨 内存告警 [{level}]\n"
                  f"使用率: {stats.percent:.1f}%\n"
                  f"RSS: {stats.rss_mb:.1f}MB\n"
                  f"可用: {stats.available_mb:.1f}MB")
        
        logger.warning(message)
        
        # 执行回调
        for callback in self.alert_callbacks:
            try:
                callback(level, stats)
            except Exception as e:
                logger.error(f"告警回调失败: {e}")
    
    def _store_stats(self, stats: MemoryStats):
        """存储统计"""
        self.history.append(stats)
        
        # 限制历史记录
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]
    
    def register_alert_callback(self, callback: Callable):
        """注册告警回调"""
        self.alert_callbacks.append(callback)
    
    def get_current_stats(self) -> Optional[MemoryStats]:
        """获取当前内存统计"""
        return self._collect_stats()
    
    def get_stats_report(self) -> Dict:
        """生成统计报告"""
        if not self.history:
            return {}
        
        rss_values = [s.rss_mb for s in self.history if s.rss_mb > 0]
        percent_values = [s.percent for s in self.history]
        
        return {
            'current': self.get_current_stats(),
            'average_rss_mb': sum(rss_values) / len(rss_values) if rss_values else 0,
            'max_rss_mb': max(rss_values) if rss_values else 0,
            'average_percent': sum(percent_values) / len(percent_values),
            'max_percent': max(percent_values),
            'gc_triggered': self.gc_triggered,
            'alert_count': self.alert_count,
            'data_points': len(self.history)
        }
    
    def force_gc(self):
        """强制垃圾回收"""
        gc.collect()
        logger.info("强制GC执行")


class MemoryProfiler:
    """
    内存分析器
    
    分析内存使用热点
    """
    
    @staticmethod
    def get_top_memory_objects(n: int = 20) -> list:
        """获取占用内存最多的对象"""
        if not PSUTIL_AVAILABLE:
            return []
        
        import objgraph
        
        # 获取最常见的类型
        stats = objgraph.typestats()
        
        # 排序
        sorted_stats = sorted(stats.items(), key=lambda x: x[1], reverse=True)
        
        return sorted_stats[:n]
    
    @staticmethod
    def get_object_growth() -> Dict:
        """获取对象增长情况"""
        if not PSUTIL_AVAILABLE:
            return {}
        
        gc.collect()
        
        # 获取增长最多的类型
        import objgraph
        growth = objgraph.growth(limit=10)
        
        return {
            type_name: count
            for type_name, count, _ in growth
        }


# 便捷函数
_memory_monitor: Optional[MemoryMonitor] = None

def get_memory_monitor() -> MemoryMonitor:
    """获取全局内存监控器"""
    global _memory_monitor
    if _memory_monitor is None:
        _memory_monitor = MemoryMonitor()
    return _memory_monitor


def start_memory_monitoring():
    """启动内存监控"""
    monitor = get_memory_monitor()
    monitor.start()
    return monitor


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    print("="*60)
    print("内存监控测试")
    print("="*60)
    
    if not PSUTIL_AVAILABLE:
        print("\n⚠️  psutil未安装，安装命令: pip install psutil")
        print("="*60)
        exit(0)
    
    # 创建监控器
    monitor = MemoryMonitor(
        warning_threshold=50.0,
        critical_threshold=70.0,
        auto_gc_threshold=60.0,
        check_interval=2.0
    )
    
    # 注册告警回调
    def on_alert(level, stats):
        print(f"\n🚨 收到告警: {level}")
        print(f"   使用率: {stats.percent:.1f}%")
    
    monitor.register_alert_callback(on_alert)
    
    # 启动监控
    print("\n1. 启动内存监控 (2秒间隔)")
    monitor.start()
    
    # 模拟内存分配
    print("\n2. 模拟内存分配")
    big_list = []
    for i in range(5):
        # 分配100MB
        big_list.append(bytearray(100 * 1024 * 1024))
        print(f"  分配 {i+1}00MB, 当前内存: {monitor.get_current_stats().rss_mb:.1f}MB")
        time.sleep(2)
    
    # 释放内存
    print("\n3. 释放内存")
    big_list.clear()
    time.sleep(3)
    
    # 报告
    print("\n4. 内存统计报告")
    report = monitor.get_stats_report()
    print(f"  平均RSS: {report['average_rss_mb']:.1f}MB")
    print(f"  最大RSS: {report['max_rss_mb']:.1f}MB")
    print(f"  GC触发: {report['gc_triggered']}次")
    print(f"  告警: {report['alert_count']}次")
    
    # 停止
    monitor.stop()
    
    print("\n" + "="*60)
