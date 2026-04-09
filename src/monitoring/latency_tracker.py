"""
延迟监控器
追踪API延迟、WebSocket延迟、数据处理延迟
"""
import time
import numpy as np
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field
from collections import deque
from enum import Enum
import threading
import logging

logger = logging.getLogger(__name__)


class LatencyType(Enum):
    """延迟类型"""
    API_REST = "api_rest"           # REST API延迟
    API_WEBSOCKET = "api_websocket" # WebSocket延迟
    DATABASE = "database"           # 数据库延迟
    CALCULATION = "calculation"     # 计算延迟
    SIGNAL_GENERATION = "signal"    # 信号生成延迟
    ORDER_PLACEMENT = "order"       # 下单延迟
    DATA_PROCESSING = "processing"  # 数据处理延迟


@dataclass
class LatencyRecord:
    """延迟记录"""
    timestamp: float
    latency_ms: float
    operation: str
    context: Dict = field(default_factory=dict)


class LatencyStatistics:
    """
    延迟统计
    
    计算P50/P90/P99/Max等分位数
    """
    
    def __init__(self, max_samples: int = 10000):
        self.max_samples = max_samples
        self.records: deque = deque(maxlen=max_samples)
        self.lock = threading.Lock()
        
    def add(self, latency_ms: float, operation: str = "", context: Dict = None):
        """添加延迟记录"""
        record = LatencyRecord(
            timestamp=time.time(),
            latency_ms=latency_ms,
            operation=operation,
            context=context or {}
        )
        
        with self.lock:
            self.records.append(record)
    
    def get_statistics(self, window_seconds: int = None) -> Dict:
        """
        获取延迟统计
        
        Args:
            window_seconds: 最近N秒的数据，None表示全部
            
        Returns:
            统计信息字典
        """
        with self.lock:
            if not self.records:
                return {
                    'count': 0,
                    'p50_ms': 0,
                    'p90_ms': 0,
                    'p99_ms': 0,
                    'max_ms': 0,
                    'min_ms': 0,
                    'avg_ms': 0,
                    'std_ms': 0
                }
            
            # 过滤时间窗口
            if window_seconds:
                cutoff_time = time.time() - window_seconds
                latencies = [r.latency_ms for r in self.records 
                           if r.timestamp >= cutoff_time]
            else:
                latencies = [r.latency_ms for r in self.records]
            
            if not latencies:
                return {'count': 0}
            
            arr = np.array(latencies)
            
            return {
                'count': len(arr),
                'p50_ms': round(np.percentile(arr, 50), 2),
                'p90_ms': round(np.percentile(arr, 90), 2),
                'p99_ms': round(np.percentile(arr, 99), 2),
                'max_ms': round(np.max(arr), 2),
                'min_ms': round(np.min(arr), 2),
                'avg_ms': round(np.mean(arr), 2),
                'std_ms': round(np.std(arr), 2)
            }
    
    def get_records(self, n: int = 100) -> List[LatencyRecord]:
        """获取最近N条记录"""
        with self.lock:
            return list(self.records)[-n:]
    
    def clear(self):
        """清空记录"""
        with self.lock:
            self.records.clear()


class LatencyTracker:
    """
    延迟追踪器
    
    追踪多种类型的延迟，提供实时监控
    """
    
    def __init__(self):
        self.stats: Dict[LatencyType, LatencyStatistics] = {
            lat_type: LatencyStatistics() 
            for lat_type in LatencyType
        }
        
        # 告警阈值 (毫秒)
        self.alert_thresholds = {
            LatencyType.API_REST: 500,
            LatencyType.API_WEBSOCKET: 100,
            LatencyType.DATABASE: 100,
            LatencyType.CALCULATION: 50,
            LatencyType.SIGNAL_GENERATION: 20,
            LatencyType.ORDER_PLACEMENT: 200,
            LatencyType.DATA_PROCESSING: 30
        }
        
        # 告警回调
        self.alert_callbacks: List[Callable] = []
        
    def record(self, lat_type: LatencyType, latency_ms: float, 
              operation: str = "", context: Dict = None):
        """
        记录延迟
        
        Args:
            lat_type: 延迟类型
            latency_ms: 延迟毫秒数
            operation: 操作名称
            context: 上下文信息
        """
        self.stats[lat_type].add(latency_ms, operation, context)
        
        # 检查是否超过阈值
        threshold = self.alert_thresholds.get(lat_type, 1000)
        if latency_ms > threshold:
            self._trigger_alert(lat_type, latency_ms, operation, threshold)
    
    def time_operation(self, lat_type: LatencyType, operation: str = ""):
        """
        上下文管理器，自动计时
        
        Usage:
            with tracker.time_operation(LatencyType.API_REST, "fetch_klines"):
                data = exchange.fetch_klines()
        """
        return LatencyTimer(self, lat_type, operation)
    
    def _trigger_alert(self, lat_type: LatencyType, latency_ms: float,
                      operation: str, threshold: float):
        """触发告警"""
        message = (f"🐌 延迟告警 [{lat_type.value}]\n"
                  f"操作: {operation}\n"
                  f"延迟: {latency_ms:.2f}ms\n"
                  f"阈值: {threshold}ms\n"
                  f"超标: {(latency_ms/threshold - 1)*100:.1f}%")
        
        logger.warning(message)
        
        # 执行告警回调
        for callback in self.alert_callbacks:
            try:
                callback(lat_type, latency_ms, operation)
            except Exception as e:
                logger.error(f"告警回调失败: {e}")
    
    def register_alert_callback(self, callback: Callable):
        """注册告警回调"""
        self.alert_callbacks.append(callback)
    
    def get_report(self, lat_type: LatencyType = None, 
                  window_seconds: int = 3600) -> Dict:
        """
        生成延迟报告
        
        Args:
            lat_type: 指定类型，None表示全部
            window_seconds: 时间窗口
            
        Returns:
            报告字典
        """
        if lat_type:
            return {
                lat_type.value: self.stats[lat_type].get_statistics(window_seconds)
            }
        
        return {
            lat_type.value: stats.get_statistics(window_seconds)
            for lat_type, stats in self.stats.items()
        }
    
    def print_report(self, window_seconds: int = 3600):
        """打印延迟报告"""
        report = self.get_report(window_seconds=window_seconds)
        
        print("\n" + "="*80)
        print(f"延迟报告 (最近{window_seconds//60}分钟)")
        print("="*80)
        
        for lat_type, stats in report.items():
            if stats.get('count', 0) > 0:
                print(f"\n{lat_type}:")
                print(f"  样本数: {stats['count']}")
                print(f"  P50: {stats['p50_ms']}ms")
                print(f"  P90: {stats['p90_ms']}ms")
                print(f"  P99: {stats['p99_ms']}ms")
                print(f"  Max: {stats['max_ms']}ms")
                print(f"  Avg: {stats['avg_ms']}ms")
        
        print("="*80)
    
    def get_slow_operations(self, lat_type: LatencyType, 
                           n: int = 10) -> List[LatencyRecord]:
        """获取最慢的N个操作"""
        records = self.stats[lat_type].get_records(n=1000)
        return sorted(records, key=lambda r: r.latency_ms, reverse=True)[:n]
    
    def reset(self, lat_type: LatencyType = None):
        """重置统计"""
        if lat_type:
            self.stats[lat_type].clear()
        else:
            for stats in self.stats.values():
                stats.clear()


class LatencyTimer:
    """延迟计时器上下文管理器"""
    
    def __init__(self, tracker: LatencyTracker, lat_type: LatencyType, 
                 operation: str = ""):
        self.tracker = tracker
        self.lat_type = lat_type
        self.operation = operation
        self.start_time = None
        
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.start_time:
            elapsed_ms = (time.time() - self.start_time) * 1000
            self.tracker.record(self.lat_type, elapsed_ms, self.operation)


# 全局追踪器实例
_global_tracker: Optional[LatencyTracker] = None


def get_tracker() -> LatencyTracker:
    """获取全局追踪器"""
    global _global_tracker
    if _global_tracker is None:
        _global_tracker = LatencyTracker()
    return _global_tracker


def record_latency(lat_type: LatencyType, latency_ms: float, 
                  operation: str = "", context: Dict = None):
    """便捷函数：记录延迟"""
    get_tracker().record(lat_type, latency_ms, operation, context)


def time_operation(lat_type: LatencyType, operation: str = ""):
    """便捷函数：计时上下文"""
    return get_tracker().time_operation(lat_type, operation)


class LatencyMonitorService:
    """
    延迟监控服务
    
    定期输出延迟报告，监控异常
    """
    
    def __init__(self, interval_seconds: int = 60):
        self.interval = interval_seconds
        self.tracker = get_tracker()
        self.running = False
        self.thread: Optional[threading.Thread] = None
        
    def start(self):
        """启动监控服务"""
        if self.running:
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        logger.info(f"延迟监控服务启动，间隔{self.interval}秒")
        
    def stop(self):
        """停止监控服务"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("延迟监控服务停止")
        
    def _monitor_loop(self):
        """监控循环"""
        while self.running:
            try:
                # 生成报告
                report = self.tracker.get_report(window_seconds=self.interval)
                
                # 记录到日志
                for lat_type, stats in report.items():
                    if stats.get('count', 0) > 0:
                        logger.info(
                            f"[Latency] {lat_type}: "
                            f"avg={stats['avg_ms']}ms, "
                            f"p90={stats['p90_ms']}ms, "
                            f"p99={stats['p99_ms']}ms, "
                            f"n={stats['count']}"
                        )
                
                # 检查异常
                self._check_anomalies(report)
                
            except Exception as e:
                logger.error(f"监控循环错误: {e}")
            
            # 等待下一个周期
            time.sleep(self.interval)
    
    def _check_anomalies(self, report: Dict):
        """检查异常延迟"""
        for lat_type, stats in report.items():
            if stats.get('count', 0) < 10:
                continue
            
            # P99超过阈值的2倍
            p99 = stats.get('p99_ms', 0)
            threshold = self.tracker.alert_thresholds.get(
                LatencyType(lat_type), 1000
            )
            
            if p99 > threshold * 2:
                logger.warning(
                    f"🐌 延迟异常! {lat_type} P99={p99}ms "
                    f"超过阈值{threshold}ms的2倍"
                )


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    # 获取追踪器
    tracker = get_tracker()
    
    # 模拟记录延迟
    np.random.seed(42)
    
    for i in range(1000):
        # API调用延迟 (正态分布，均值100ms，标准差30ms)
        api_latency = max(10, np.random.normal(100, 30))
        tracker.record(LatencyType.API_REST, api_latency, "fetch_klines")
        
        # WebSocket延迟 (正态分布，均值30ms，标准差10ms)
        ws_latency = max(5, np.random.normal(30, 10))
        tracker.record(LatencyType.API_WEBSOCKET, ws_latency, "on_message")
        
        # 偶尔出现高延迟
        if i % 100 == 0:
            tracker.record(LatencyType.API_REST, 2000, "fetch_klines_slow")
    
    # 打印报告
    tracker.print_report(window_seconds=3600)
    
    # 获取最慢的操作
    print("\n最慢的API调用:")
    slow_ops = tracker.get_slow_operations(LatencyType.API_REST, 5)
    for op in slow_ops:
        print(f"  {op.operation}: {op.latency_ms:.2f}ms")
    
    # 使用上下文管理器
    print("\n使用上下文管理器计时:")
    with tracker.time_operation(LatencyType.CALCULATION, "heavy_compute"):
        time.sleep(0.1)  # 模拟计算
    
    stats = tracker.get_report(LatencyType.CALCULATION)
    print(f"计算延迟: {stats['calculation']['avg_ms']}ms")
