"""
S001-Pro V3 执行可视化模块
职责: 步骤追踪 + 心跳机制 + 自动诊断 + 错误隔离
"""

import functools
import threading
import time
import logging
import sys
import traceback
from typing import Optional, Dict, List, Any, Callable
from datetime import datetime
from collections import deque
from dataclasses import dataclass, field
from contextlib import contextmanager
import json
import os

# 配置专用logger
logger = logging.getLogger(__name__)


@dataclass
class StepRecord:
    """步骤记录"""
    timestamp: str
    module: str
    action: str
    data: Dict[str, Any]
    thread_name: str
    step_type: str = "normal"  # normal, entry, exit, error


@dataclass
class HeartbeatStatus:
    """心跳状态"""
    module: str
    last_active: float
    is_alive: bool
    consecutive_misses: int = 0


class ExecutionTracer:
    """执行追踪器 - 单例模式"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        self.step_buffer = deque(maxlen=100)  # 环形缓冲区，保留最近100步
        self.heartbeat_registry = {}  # module -> HeartbeatStatus
        self.heartbeat_thread = None
        self.diagnosis_thread = None
        self.running = False
        self.diagnosis_interval = 10  # 诊断检查间隔(秒)
        self.dead_threshold = 120  # 卡死阈值(秒)
        
        # 线程安全的锁
        self.buffer_lock = threading.Lock()
        self.heartbeat_lock = threading.Lock()
    
    def start(self):
        """启动追踪系统"""
        if self.running:
            return
        
        self.running = True
        
        # 启动心跳监控线程
        self.heartbeat_thread = threading.Thread(
            target=self._heartbeat_monitor,
            name="HeartbeatMonitor",
            daemon=True
        )
        self.heartbeat_thread.start()
        
        # 启动自动诊断线程
        self.diagnosis_thread = threading.Thread(
            target=self._diagnosis_monitor,
            name="DiagnosisMonitor",
            daemon=True
        )
        self.diagnosis_thread.start()
        
        self._log_system("可视化追踪系统启动", {"pid": os.getpid()})
    
    def stop(self):
        """停止追踪系统"""
        self.running = False
        self._log_system("可视化追踪系统停止", {})
    
    def _log_system(self, action: str, data: Dict):
        """系统级日志"""
        self.log_step("SYSTEM", action, data, "system")
    
    def log_step(self, module: str, action: str, data: Dict = None, 
                 step_type: str = "normal"):
        """
        记录步骤
        格式: [时间戳] [模块名] → 动作描述 | 关键数据
        """
        if data is None:
            data = {}
        
        timestamp = datetime.now()
        timestamp_str = timestamp.strftime("%H:%M:%S.") + f"{timestamp.microsecond // 1000:03d}"
        thread_name = threading.current_thread().name
        
        record = StepRecord(
            timestamp=timestamp_str,
            module=module,
            action=action,
            data=data,
            thread_name=thread_name,
            step_type=step_type
        )
        
        # 线程安全地写入缓冲区
        with self.buffer_lock:
            self.step_buffer.append(record)
        
        # 格式化输出
        data_str = ", ".join([f"{k}={v}" for k, v in data.items()])
        if data_str:
            log_msg = f"[{timestamp_str}] [{module}] → {action} | {data_str}"
        else:
            log_msg = f"[{timestamp_str}] [{module}] → {action}"
        
        # 根据类型使用不同日志级别
        if step_type == "error":
            logger.error(log_msg)
        elif step_type == "entry":
            logger.debug(f"▶️ {log_msg}")
        elif step_type == "exit":
            logger.debug(f"◀️ {log_msg}")
        else:
            logger.info(log_msg)
    
    def register_heartbeat(self, module: str):
        """注册模块心跳"""
        with self.heartbeat_lock:
            self.heartbeat_registry[module] = HeartbeatStatus(
                module=module,
                last_active=time.time(),
                is_alive=True
            )
        self._log_system(f"模块注册心跳", {"module": module})
    
    def update_heartbeat(self, module: str):
        """更新模块心跳"""
        with self.heartbeat_lock:
            if module in self.heartbeat_registry:
                status = self.heartbeat_registry[module]
                status.last_active = time.time()
                status.is_alive = True
                status.consecutive_misses = 0
    
    def _heartbeat_monitor(self):
        """心跳监控线程"""
        while self.running:
            time.sleep(60)  # 每分钟报告一次
            
            current_time = time.time()
            
            with self.heartbeat_lock:
                for module, status in self.heartbeat_registry.items():
                    inactive_seconds = current_time - status.last_active
                    
                    if inactive_seconds < 60:
                        # 正常
                        logger.info(f"💓 [HEARTBEAT] {module} 运行中 | 最后活动{inactive_seconds:.0f}秒前")
                    else:
                        # 异常
                        status.consecutive_misses += 1
                        logger.warning(f"🚨 [ALERT] {module} 已卡死 {inactive_seconds:.0f}秒！")
                        
                        # 触发诊断
                        if inactive_seconds > self.dead_threshold:
                            self._trigger_diagnosis(module)
    
    def _diagnosis_monitor(self):
        """诊断监控线程"""
        while self.running:
            time.sleep(self.diagnosis_interval)
            
            current_time = time.time()
            
            with self.heartbeat_lock:
                for module, status in self.heartbeat_registry.items():
                    inactive_seconds = current_time - status.last_active
                    
                    if inactive_seconds > self.dead_threshold and status.is_alive:
                        status.is_alive = False
                        self._trigger_diagnosis(module)
    
    def _trigger_diagnosis(self, dead_module: str):
        """触发自动诊断"""
        logger.critical(f"🔴 [DIAGNOSIS] 模块 {dead_module} 卡死超过{self.dead_threshold}秒，输出诊断信息")
        
        # 获取最后10步操作
        recent_steps = self.get_recent_steps(10)
        
        # 格式化诊断报告
        diagnosis_report = [
            "=" * 80,
            "🔴 自动诊断报告",
            f"卡死模块: {dead_module}",
            f"诊断时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"进程PID: {os.getpid()}",
            "-" * 80,
            "最后10步操作记录:",
        ]
        
        for i, step in enumerate(recent_steps, 1):
            data_str = json.dumps(step.data, default=str) if step.data else "{}"
            diagnosis_report.append(
                f"  {i}. [{step.timestamp}] [{step.module}] "
                f"{step.action} | {data_str} | 线程:{step.thread_name}"
            )
        
        diagnosis_report.extend([
            "-" * 80,
            "当前活动线程:",
        ])
        
        # 列出所有活动线程
        for thread in threading.enumerate():
            diagnosis_report.append(f"  - {thread.name} (daemon={thread.daemon})")
        
        diagnosis_report.append("=" * 80)
        
        # 输出诊断报告
        for line in diagnosis_report:
            logger.critical(line)
        
        # 同时写入专门的诊断文件
        self._write_diagnosis_file(dead_module, diagnosis_report)
    
    def _write_diagnosis_file(self, module: str, report: List[str]):
        """写入诊断文件"""
        try:
            diagnosis_file = f"data/diagnosis_{module}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            with open(diagnosis_file, 'w') as f:
                f.write('\n'.join(report))
            logger.info(f"诊断报告已保存: {diagnosis_file}")
        except Exception as e:
            logger.error(f"保存诊断报告失败: {e}")
    
    def get_recent_steps(self, n: int = 10) -> List[StepRecord]:
        """获取最近N步操作"""
        with self.buffer_lock:
            return list(self.step_buffer)[-n:]
    
    def clear_buffer(self):
        """清空缓冲区"""
        with self.buffer_lock:
            self.step_buffer.clear()


# 全局追踪器实例
tracer = ExecutionTracer()


def trace_step(module: str, action: str = None):
    """
    步骤追踪装饰器
    用法:
        @trace_step("Scanner", "配对筛选")
        def filter_pairs(...):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            func_name = action or func.__name__
            
            # 记录入口
            tracer.log_step(
                module=module,
                action=f"开始: {func_name}",
                data={"args": str(args), "kwargs": str(kwargs)},
                step_type="entry"
            )
            
            try:
                # 执行函数
                result = func(*args, **kwargs)
                
                # 记录出口
                result_summary = str(result)[:100] if result else "None"
                tracer.log_step(
                    module=module,
                    action=f"完成: {func_name}",
                    data={"result": result_summary},
                    step_type="exit"
                )
                
                return result
                
            except Exception as e:
                # 记录错误
                tracer.log_step(
                    module=module,
                    action=f"错误: {func_name}",
                    data={"error": str(e), "traceback": traceback.format_exc()[:200]},
                    step_type="error"
                )
                raise
        
        return wrapper
    return decorator


@contextmanager
def trace_context(module: str, action: str, **kwargs):
    """
    步骤追踪上下文管理器
    用法:
        with trace_context("Engine", "处理Tick", pair_key="BTC-ETH"):
            ...
    """
    tracer.log_step(
        module=module,
        action=f"开始: {action}",
        data=kwargs,
        step_type="entry"
    )
    
    try:
        yield
        tracer.log_step(
            module=module,
            action=f"完成: {action}",
            data={},
            step_type="exit"
        )
    except Exception as e:
        tracer.log_step(
            module=module,
            action=f"错误: {action}",
            data={"error": str(e)},
            step_type="error"
        )
        raise


class TracedThread(threading.Thread):
    """
    带追踪的线程类
    自动捕获异常，防止崩溃传播
    """
    
    def __init__(self, module: str, target: Callable = None, **kwargs):
        super().__init__(**kwargs)
        self.module = module
        self._target = target
        self.error = None
    
    def run(self):
        """运行线程，带异常隔离"""
        tracer.register_heartbeat(self.module)
        
        try:
            if self._target:
                self._target()
        except Exception as e:
            self.error = e
            tracer.log_step(
                module=self.module,
                action="线程崩溃",
                data={
                    "error": str(e),
                    "traceback": traceback.format_exc()
                },
                step_type="error"
            )
            logger.critical(f"🚨 [{self.module}] 线程崩溃但已被隔离: {e}")
            # 不重新抛出，防止进程退出


def safe_thread_wrapper(module: str):
    """
    线程安全包装器装饰器
    将任何函数包装为带错误隔离的版本
    用法:
        @safe_thread_wrapper("ModuleName")
        def my_func():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            tracer.update_heartbeat(module)
            
            try:
                return func(*args, **kwargs)
            except Exception as e:
                tracer.log_step(
                    module=module,
                    action="执行异常",
                    data={"error": str(e), "function": func.__name__},
                    step_type="error"
                )
                logger.error(f"[{module}] 函数 {func.__name__} 异常: {e}")
                # 返回None表示失败，不抛出
                return None
        
        return wrapper
    return decorator


# 快捷函数
def log_info(module: str, action: str, **data):
    """记录信息步骤"""
    tracer.log_step(module, action, data)


def log_error(module: str, action: str, error: Exception, **data):
    """记录错误步骤"""
    data['error'] = str(error)
    data['error_type'] = type(error).__name__
    tracer.log_step(module, action, data, "error")


def heartbeat(module: str):
    """发送心跳"""
    tracer.update_heartbeat(module)


if __name__ == "__main__":
    # 测试可视化功能
    import tempfile
    import os
    
    # 配置日志
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s | %(levelname)s | %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # 启动追踪
    tracer.start()
    
    # 测试1: 基本步骤追踪
    print("\n" + "="*80)
    print("测试1: 基本步骤追踪")
    print("="*80)
    
    @trace_step("TestModule", "测试函数")
    def test_function(x, y):
        time.sleep(0.1)
        return x + y
    
    result = test_function(10, 20)
    print(f"结果: {result}")
    
    # 测试2: 上下文管理器
    print("\n" + "="*80)
    print("测试2: 上下文管理器")
    print("="*80)
    
    with trace_context("TestModule", "测试上下文", operation="calculation"):
        time.sleep(0.1)
        print("上下文内执行")
    
    # 测试3: 心跳机制
    print("\n" + "="*80)
    print("测试3: 心跳机制 (等待1分钟或按Ctrl+C跳过)")
    print("="*80)
    
    tracer.register_heartbeat("TestWorker")
    
    def worker():
        for i in range(3):
            tracer.update_heartbeat("TestWorker")
            log_info("TestWorker", f"处理任务{i}", task_id=i)
            time.sleep(2)
    
    t = TracedThread("TestWorker", target=worker)
    t.start()
    
    # 等待或超时
    try:
        time.sleep(65)  # 等待心跳触发
    except KeyboardInterrupt:
        print("\n跳过等待")
    
    # 测试4: 错误隔离
    print("\n" + "="*80)
    print("测试4: 错误隔离")
    print("="*80)
    
    @safe_thread_wrapper("SafeModule")
    def failing_function():
        raise ValueError("测试错误")
    
    result = failing_function()
    print(f"错误被隔离，返回: {result}")
    
    # 测试5: 获取最近步骤
    print("\n" + "="*80)
    print("测试5: 最近步骤")
    print("="*80)
    
    recent = tracer.get_recent_steps(5)
    for step in recent:
        print(f"  [{step.timestamp}] {step.module}: {step.action}")
    
    # 停止追踪
    tracer.stop()
    print("\n✅ 所有测试完成")
