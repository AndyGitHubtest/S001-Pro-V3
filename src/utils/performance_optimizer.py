"""
性能优化器
热点检测与优化建议
"""
import time
import functools
import logging
from typing import Dict, List, Callable, Any
from collections import defaultdict
from dataclasses import dataclass
import cProfile
import pstats
import io

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetric:
    """性能指标"""
    function_name: str
    call_count: int
    total_time: float
    avg_time: float
    max_time: float


class PerformanceProfiler:
    """
    性能分析器
    
    功能:
    1. 函数执行时间统计
    2. 热点函数识别
    3. 性能瓶颈分析
    4. 优化建议
    """
    
    def __init__(self):
        self.metrics: Dict[str, PerformanceMetric] = {}
        self._call_times: Dict[str, List[float]] = defaultdict(list)
    
    def profile(self, func: Callable) -> Callable:
        """
        装饰器: 分析函数性能
        
        使用:
            @profiler.profile
            def my_function():
                pass
        """
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            result = func(*args, **kwargs)
            elapsed = time.perf_counter() - start
            
            func_name = f"{func.__module__}.{func.__name__}"
            self._call_times[func_name].append(elapsed)
            
            return result
        
        return wrapper
    
    def analyze(self) -> List[PerformanceMetric]:
        """分析性能数据"""
        metrics = []
        
        for func_name, times in self._call_times.items():
            metric = PerformanceMetric(
                function_name=func_name,
                call_count=len(times),
                total_time=sum(times),
                avg_time=sum(times) / len(times),
                max_time=max(times)
            )
            metrics.append(metric)
        
        # 按总时间排序
        metrics.sort(key=lambda x: x.total_time, reverse=True)
        
        return metrics
    
    def get_hotspots(self, top_n: int = 10) -> List[PerformanceMetric]:
        """获取热点函数"""
        return self.analyze()[:top_n]
    
    def generate_report(self) -> str:
        """生成性能报告"""
        metrics = self.analyze()
        
        report = []
        report.append("="*80)
        report.append("性能分析报告")
        report.append("="*80)
        report.append(f"\n总函数数: {len(metrics)}")
        report.append(f"总调用次数: {sum(m.call_count for m in metrics)}")
        report.append(f"总执行时间: {sum(m.total_time for m in metrics):.3f}s")
        report.append("")
        
        # 热点函数
        report.append("热点函数 (Top 10):")
        report.append("-"*80)
        report.append(f"{'函数名':<50} {'调用':>8} {'总时间':>10} {'平均':>10}")
        report.append("-"*80)
        
        for m in metrics[:10]:
            name = m.function_name[-48:] if len(m.function_name) > 48 else m.function_name
            report.append(f"{name:<50} {m.call_count:>8} {m.total_time:>10.3f}s {m.avg_time*1000:>9.2f}ms")
        
        report.append("="*80)
        
        return '\n'.join(report)
    
    def reset(self):
        """重置统计"""
        self.metrics.clear()
        self._call_times.clear()


class CProfileAnalyzer:
    """
    cProfile分析器封装
    """
    
    def __init__(self):
        self.profiler = cProfile.Profile()
    
    def start(self):
        """开始分析"""
        self.profiler.enable()
    
    def stop(self):
        """停止分析"""
        self.profiler.disable()
    
    def get_stats(self, top_n: int = 20) -> str:
        """获取统计报告"""
        s = io.StringIO()
        ps = pstats.Stats(self.profiler, stream=s)
        ps.sort_stats('cumulative')
        ps.print_stats(top_n)
        return s.getvalue()


class OptimizationAdvisor:
    """
    优化建议器
    
    根据性能数据提供优化建议
    """
    
    @staticmethod
    def analyze_bottlenecks(metrics: List[PerformanceMetric]) -> List[Dict]:
        """分析瓶颈并提供建议"""
        suggestions = []
        
        for metric in metrics[:10]:
            # 高频调用
            if metric.call_count > 1000:
                suggestions.append({
                    'function': metric.function_name,
                    'issue': '高频调用',
                    'suggestion': '考虑缓存结果或批量处理',
                    'priority': 'high'
                })
            
            # 慢函数
            if metric.avg_time > 0.1:  # 100ms
                suggestions.append({
                    'function': metric.function_name,
                    'issue': '执行缓慢',
                    'suggestion': '考虑使用Numba/Cython优化或异步化',
                    'priority': 'high'
                })
            
            # 数据库操作
            if 'db' in metric.function_name.lower() or 'sql' in metric.function_name.lower():
                suggestions.append({
                    'function': metric.function_name,
                    'issue': '数据库操作',
                    'suggestion': '考虑批量查询、添加索引、使用缓存',
                    'priority': 'medium'
                })
            
            # API调用
            if 'api' in metric.function_name.lower() or 'fetch' in metric.function_name.lower():
                suggestions.append({
                    'function': metric.function_name,
                    'issue': 'API调用',
                    'suggestion': '考虑异步并发、缓存响应、减少调用频率',
                    'priority': 'medium'
                })
        
        return suggestions
    
    @staticmethod
    def generate_optimization_report(metrics: List[PerformanceMetric]) -> str:
        """生成优化报告"""
        suggestions = OptimizationAdvisor.analyze_bottlenecks(metrics)
        
        report = []
        report.append("="*80)
        report.append("性能优化建议")
        report.append("="*80)
        report.append(f"\n发现 {len(suggestions)} 个优化点:\n")
        
        # 按优先级分组
        priority_order = {'high': 0, 'medium': 1, 'low': 2}
        suggestions.sort(key=lambda x: priority_order.get(x['priority'], 3))
        
        for i, s in enumerate(suggestions, 1):
            emoji = {'high': '🔴', 'medium': '🟡', 'low': '🟢'}.get(s['priority'], '⚪')
            report.append(f"{i}. {emoji} [{s['priority'].upper()}] {s['function']}")
            report.append(f"   问题: {s['issue']}")
            report.append(f"   建议: {s['suggestion']}")
            report.append("")
        
        report.append("="*80)
        
        return '\n'.join(report)


# 全局性能分析器
_profiler = PerformanceProfiler()

def profile(func: Callable) -> Callable:
    """便捷装饰器"""
    return _profiler.profile(func)

def get_profiler() -> PerformanceProfiler:
    """获取全局分析器"""
    return _profiler

def print_performance_report():
    """打印性能报告"""
    print(_profiler.generate_report())


# 内存分析 (简单版)
class MemoryProfiler:
    """内存分析器"""
    
    @staticmethod
    def get_object_count() -> Dict[str, int]:
        """获取对象数量统计"""
        import gc
        
        count_by_type = defaultdict(int)
        for obj in gc.get_objects():
            count_by_type[type(obj).__name__] += 1
        
        # 排序并返回Top20
        sorted_counts = sorted(count_by_type.items(), key=lambda x: -x[1])
        return dict(sorted_counts[:20])
    
    @staticmethod
    def print_memory_report():
        """打印内存报告"""
        counts = MemoryProfiler.get_object_count()
        
        print("="*60)
        print("内存使用报告 (对象数量 Top 20)")
        print("="*60)
        for obj_type, count in counts.items():
            print(f"  {obj_type:<30} {count:>10}")
        print("="*60)


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    print("="*60)
    print("性能优化器测试")
    print("="*60)
    
    # 示例函数
    @profile
    def slow_function(n):
        """模拟慢函数"""
        time.sleep(0.01)
        return sum(range(n))
    
    @profile
    def fast_function():
        """模拟快函数"""
        return 42
    
    # 运行测试
    print("\n1. 运行测试函数")
    for i in range(10):
        slow_function(1000)
        fast_function()
    
    # 生成报告
    print("\n2. 性能报告")
    print_performance_report()
    
    # 优化建议
    print("\n3. 优化建议")
    metrics = get_profiler().analyze()
    print(OptimizationAdvisor.generate_optimization_report(metrics))
    
    # 内存报告
    print("\n4. 内存报告")
    MemoryProfiler.print_memory_report()
    
    print("\n" + "="*60)
