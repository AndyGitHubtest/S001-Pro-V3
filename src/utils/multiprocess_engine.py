"""
多进程引擎
绕过GIL限制，提升CPU密集型任务性能
"""
import os
import time
import multiprocessing as mp
from typing import List, Dict, Callable, Any, Optional
from dataclasses import dataclass
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np
import logging

logger = logging.getLogger(__name__)


@dataclass
class TaskResult:
    """任务结果"""
    task_id: str
    success: bool
    result: Any
    error: str = None
    execution_time: float = 0


class MultiprocessEngine:
    """
    多进程计算引擎
    
    适用场景:
    1. 大规模回测计算
    2. 参数优化搜索
    3. 多币种并行分析
    4. 数据预处理
    
    优势:
    - 绕过Python GIL
    - 充分利用多核CPU
    - 进程隔离，稳定性高
    """
    
    def __init__(self, max_workers: int = None):
        """
        Args:
            max_workers: 最大进程数，默认CPU核心数
        """
        self.max_workers = max_workers or mp.cpu_count()
        self.executor: Optional[ProcessPoolExecutor] = None
        
        # 统计
        self.tasks_submitted = 0
        self.tasks_completed = 0
        self.tasks_failed = 0
    
    def start(self):
        """启动引擎"""
        if self.executor is None:
            self.executor = ProcessPoolExecutor(max_workers=self.max_workers)
            logger.info(f"多进程引擎启动，工作进程数: {self.max_workers}")
    
    def stop(self):
        """停止引擎"""
        if self.executor:
            self.executor.shutdown(wait=True)
            self.executor = None
            logger.info("多进程引擎停止")
    
    def map_tasks(self, func: Callable, 
                  tasks: List[Any],
                  chunksize: int = 1) -> List[TaskResult]:
        """
        批量执行任务
        
        Args:
            func: 执行函数
            tasks: 任务参数列表
            chunksize: 每个进程处理的任务数
            
        Returns:
            任务结果列表
        """
        if not self.executor:
            self.start()
        
        results = []
        
        try:
            # 提交所有任务
            futures = {
                self.executor.submit(func, task): i 
                for i, task in enumerate(tasks)
            }
            
            self.tasks_submitted += len(tasks)
            
            # 收集结果
            for future in as_completed(futures):
                task_idx = futures[future]
                
                try:
                    result = future.result(timeout=300)  # 5分钟超时
                    results.append(TaskResult(
                        task_id=str(task_idx),
                        success=True,
                        result=result,
                        execution_time=0
                    ))
                    self.tasks_completed += 1
                    
                except Exception as e:
                    results.append(TaskResult(
                        task_id=str(task_idx),
                        success=False,
                        result=None,
                        error=str(e)
                    ))
                    self.tasks_failed += 1
                    logger.error(f"任务执行失败 [{task_idx}]: {e}")
        
        except Exception as e:
            logger.error(f"批量任务执行错误: {e}")
        
        return results
    
    def parallel_backtest(self, 
                         backtest_func: Callable,
                         param_grid: List[Dict],
                         data: Any) -> List[TaskResult]:
        """
        并行回测
        
        Args:
            backtest_func: 回测函数
            param_grid: 参数网格
            data: 共享数据 (会被序列化到每个进程)
            
        Returns:
            回测结果列表
        """
        # 包装任务
        def task_wrapper(params):
            start_time = time.time()
            try:
                result = backtest_func(data, params)
                return {
                    'params': params,
                    'result': result,
                    'time': time.time() - start_time
                }
            except Exception as e:
                return {
                    'params': params,
                    'error': str(e),
                    'time': time.time() - start_time
                }
        
        results = self.map_tasks(task_wrapper, param_grid)
        
        logger.info(f"并行回测完成: {len(results)}组参数")
        return results
    
    def parallel_scan(self,
                     scan_func: Callable,
                     symbols: List[str],
                     **common_kwargs) -> Dict[str, Any]:
        """
        并行扫描多币种
        
        Args:
            scan_func: 扫描函数
            symbols: 币种列表
            **common_kwargs: 共享参数
            
        Returns:
            扫描结果字典
        """
        def task_wrapper(symbol):
            try:
                return scan_func(symbol, **common_kwargs)
            except Exception as e:
                logger.error(f"扫描失败 [{symbol}]: {e}")
                return None
        
        results = self.map_tasks(task_wrapper, symbols)
        
        return {
            symbols[i]: r.result 
            for i, r in enumerate(results) 
            if r.success and r.result is not None
        }
    
    def get_stats(self) -> Dict:
        """获取统计"""
        return {
            'max_workers': self.max_workers,
            'tasks_submitted': self.tasks_submitted,
            'tasks_completed': self.tasks_completed,
            'tasks_failed': self.tasks_failed,
            'success_rate': self.tasks_completed / max(self.tasks_submitted, 1)
        }


class WorkerPool:
    """
    工作进程池
    
    维持常驻进程，减少进程创建开销
    """
    
    def __init__(self, pool_size: int = None):
        self.pool_size = pool_size or mp.cpu_count()
        self.pool: Optional[mp.Pool] = None
    
    def start(self):
        """启动进程池"""
        if self.pool is None:
            self.pool = mp.Pool(processes=self.pool_size)
            logger.info(f"工作进程池启动，大小: {self.pool_size}")
    
    def stop(self):
        """停止进程池"""
        if self.pool:
            self.pool.close()
            self.pool.join()
            self.pool = None
            logger.info("工作进程池停止")
    
    def apply_async(self, func: Callable, args: tuple = (), 
                   callback: Callable = None) -> mp.pool.AsyncResult:
        """异步执行任务"""
        if not self.pool:
            self.start()
        return self.pool.apply_async(func, args, callback=callback)
    
    def map(self, func: Callable, iterable: List, chunksize: int = 1):
        """批量映射"""
        if not self.pool:
            self.start()
        return self.pool.map(func, iterable, chunksize=chunksize)
    
    def starmap(self, func: Callable, iterable: List):
        """多参数映射"""
        if not self.pool:
            self.start()
        return self.pool.starmap(func, iterable)


# 共享内存管理器
class SharedMemoryManager:
    """
    共享内存管理
    
    在进程间共享大数据 (如K线数据)
    """
    
    def __init__(self):
        self.shared_data = {}
    
    def share_numpy_array(self, name: str, arr: np.ndarray):
        """共享numpy数组"""
        # 创建共享内存
        shared_arr = mp.Array('d', arr.size)
        shared_arr[:] = arr.flatten()
        
        self.shared_data[name] = {
            'array': shared_arr,
            'shape': arr.shape,
            'dtype': arr.dtype
        }
    
    def get_shared_array(self, name: str) -> Optional[np.ndarray]:
        """获取共享数组"""
        if name not in self.shared_data:
            return None
        
        info = self.shared_data[name]
        return np.array(info['array']).reshape(info['shape'])


# 便捷函数
def parallel_map(func: Callable, 
                items: List,
                max_workers: int = None) -> List[Any]:
    """便捷并行映射"""
    engine = MultiprocessEngine(max_workers=max_workers)
    results = engine.map_tasks(func, items)
    engine.stop()
    
    return [r.result for r in results if r.success]


def parallel_backtest(backtest_func: Callable,
                     param_grid: List[Dict],
                     data: Any,
                     max_workers: int = None) -> List[Dict]:
    """便捷并行回测"""
    engine = MultiprocessEngine(max_workers=max_workers)
    results = engine.parallel_backtest(backtest_func, param_grid, data)
    engine.stop()
    
    return [r.result for r in results if r.success]


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    print("="*60)
    print("多进程引擎测试")
    print("="*60)
    
    # 测试1: 基础并行计算
    print("\n1. 基础并行计算")
    
    def cpu_intensive_task(n):
        """CPU密集型任务"""
        result = 0
        for i in range(n):
            result += i ** 2
        return result
    
    tasks = [1000000] * 8  # 8个任务
    
    # 串行执行
    start = time.time()
    serial_results = [cpu_intensive_task(t) for t in tasks]
    serial_time = time.time() - start
    print(f"  串行执行: {serial_time:.2f}秒")
    
    # 并行执行
    engine = MultiprocessEngine()
    start = time.time()
    parallel_results = parallel_map(cpu_intensive_task, tasks, max_workers=4)
    parallel_time = time.time() - start
    print(f"  并行执行: {parallel_time:.2f}秒")
    print(f"  加速比: {serial_time/parallel_time:.2f}x")
    
    engine.stop()
    
    # 测试2: 并行回测
    print("\n2. 并行回测")
    
    def mock_backtest(data, params):
        """模拟回测"""
        time.sleep(0.1)  # 模拟计算
        return {
            'sharpe': params['z_entry'] * 0.5 + np.random.rand(),
            'pf': params['z_exit'] * 2 + np.random.rand()
        }
    
    # 生成参数网格
    param_grid = [
        {'z_entry': z, 'z_exit': e}
        for z in [2.0, 2.5, 3.0]
        for e in [0.3, 0.5, 0.7]
    ]
    
    start = time.time()
    backtest_results = parallel_backtest(
        mock_backtest, param_grid, data=None, max_workers=4
    )
    backtest_time = time.time() - start
    
    print(f"  参数组合数: {len(param_grid)}")
    print(f"  执行时间: {backtest_time:.2f}秒")
    print(f"  平均每个: {backtest_time/len(param_grid):.3f}秒")
    
    # 测试3: 进程池
    print("\n3. 进程池")
    pool = WorkerPool(pool_size=4)
    
    results = pool.map(cpu_intensive_task, [500000] * 4)
    print(f"  任务结果: {results}")
    
    pool.stop()
    
    print("\n" + "="*60)
