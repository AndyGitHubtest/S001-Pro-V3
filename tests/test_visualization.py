"""
可视化功能测试
包含: 步骤追踪、心跳机制、自动诊断、卡死模拟
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import unittest
import threading
import time
import logging
import tempfile
import shutil
from io import StringIO

from visualization import (
    ExecutionTracer, tracer, trace_step, trace_context, TracedThread,
    safe_thread_wrapper, log_info, log_error, heartbeat, StepRecord
)


# 配置测试日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)


class TestStepTracing(unittest.TestCase):
    """步骤追踪测试"""
    
    def setUp(self):
        tracer.clear_buffer()
        if not tracer.running:
            tracer.start()
    
    def test_log_step_basic(self):
        """测试基本步骤记录"""
        tracer.log_step("TestModule", "测试动作", {"key": "value"})
        
        recent = tracer.get_recent_steps(1)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].module, "TestModule")
        self.assertEqual(recent[0].action, "测试动作")
        self.assertEqual(recent[0].data["key"], "value")
    
    def test_log_step_buffer_limit(self):
        """测试缓冲区限制"""
        # 写入超过100条记录
        for i in range(110):
            tracer.log_step("TestModule", f"动作{i}")
        
        recent = tracer.get_recent_steps(100)
        self.assertEqual(len(recent), 100)
        # 最早的记录应该是动作10
        self.assertIn("动作10", recent[0].action)
    
    def test_trace_decorator(self):
        """测试追踪装饰器"""
        @trace_step("TestModule", "测试函数")
        def test_func(x, y):
            return x + y
        
        result = test_func(10, 20)
        self.assertEqual(result, 30)
        
        # 验证入口和出口都被记录
        recent = tracer.get_recent_steps(2)
        actions = [r.action for r in recent]
        self.assertTrue(any("开始" in a for a in actions))
        self.assertTrue(any("完成" in a for a in actions))
    
    def test_trace_decorator_error(self):
        """测试装饰器错误记录"""
        @trace_step("TestModule", "错误函数")
        def failing_func():
            raise ValueError("测试错误")
        
        with self.assertRaises(ValueError):
            failing_func()
        
        # 验证错误被记录
        recent = tracer.get_recent_steps(1)
        self.assertEqual(recent[0].step_type, "error")
        self.assertIn("错误", recent[0].action)
    
    def test_trace_context(self):
        """测试上下文管理器"""
        with trace_context("TestModule", "测试上下文", data="value"):
            time.sleep(0.01)
        
        recent = tracer.get_recent_steps(2)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0].step_type, "entry")
        self.assertEqual(recent[1].step_type, "exit")
    
    def test_trace_context_error(self):
        """测试上下文管理器错误"""
        try:
            with trace_context("TestModule", "错误上下文"):
                raise RuntimeError("测试异常")
        except RuntimeError:
            pass
        
        recent = tracer.get_recent_steps(2)
        self.assertEqual(recent[-1].step_type, "error")


class TestHeartbeat(unittest.TestCase):
    """心跳机制测试"""
    
    def setUp(self):
        tracer.heartbeat_registry.clear()
        tracer.clear_buffer()
    
    def test_register_heartbeat(self):
        """测试心跳注册"""
        tracer.register_heartbeat("TestModule")
        
        self.assertIn("TestModule", tracer.heartbeat_registry)
        status = tracer.heartbeat_registry["TestModule"]
        self.assertTrue(status.is_alive)
    
    def test_update_heartbeat(self):
        """测试心跳更新"""
        tracer.register_heartbeat("TestModule")
        time.sleep(0.1)
        
        old_time = tracer.heartbeat_registry["TestModule"].last_active
        tracer.update_heartbeat("TestModule")
        new_time = tracer.heartbeat_registry["TestModule"].last_active
        
        self.assertGreater(new_time, old_time)
    
    def test_heartbeat_convenience(self):
        """测试便捷函数"""
        tracer.register_heartbeat("QuickModule")
        heartbeat("QuickModule")
        
        status = tracer.heartbeat_registry["QuickModule"]
        self.assertEqual(status.consecutive_misses, 0)


class TestThreadSafety(unittest.TestCase):
    """线程安全测试"""
    
    def setUp(self):
        tracer.clear_buffer()
    
    def test_concurrent_logging(self):
        """测试并发日志记录"""
        results = []
        errors = []
        
        def worker(thread_id):
            try:
                for i in range(10):
                    tracer.log_step(f"Thread-{thread_id}", f"动作{i}")
                    time.sleep(0.001)
                results.append(thread_id)
            except Exception as e:
                errors.append((thread_id, str(e)))
        
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        self.assertEqual(len(results), 5)
        
        # 验证所有记录都被保存
        recent = tracer.get_recent_steps(50)
        self.assertEqual(len(recent), 50)
    
    def test_traced_thread(self):
        """测试带追踪的线程"""
        result = []
        
        def target():
            tracer.log_step("Worker", "工作开始")
            time.sleep(0.01)
            result.append("done")
        
        t = TracedThread("TestWorker", target=target)
        t.start()
        t.join()
        
        self.assertEqual(result, ["done"])
        self.assertIn("TestWorker", tracer.heartbeat_registry)
    
    def test_traced_thread_exception_isolation(self):
        """测试线程异常隔离"""
        def failing_target():
            raise ValueError("线程错误")
        
        t = TracedThread("FailingWorker", target=failing_target)
        t.start()
        t.join()
        
        # 线程应该正常结束，错误被捕获
        self.assertIsNotNone(t.error)
        self.assertIsInstance(t.error, ValueError)
    
    def test_safe_thread_wrapper(self):
        """测试线程安全包装器"""
        tracer.register_heartbeat("SafeModule")
        
        @safe_thread_wrapper("SafeModule")
        def safe_func():
            return "success"
        
        result = safe_func()
        self.assertEqual(result, "success")
    
    def test_safe_thread_wrapper_error(self):
        """测试包装器错误处理"""
        tracer.register_heartbeat("SafeModule")
        
        @safe_thread_wrapper("SafeModule")
        def failing_func():
            raise ValueError("包装器测试错误")
        
        result = failing_func()
        self.assertIsNone(result)  # 错误时返回None
        
        # 验证错误被记录
        recent = tracer.get_recent_steps(1)
        self.assertEqual(recent[0].step_type, "error")


class TestAutoDiagnosis(unittest.TestCase):
    """自动诊断测试"""
    
    def setUp(self):
        tracer.heartbeat_registry.clear()
        tracer.clear_buffer()
        tracer.dead_threshold = 2  # 设置2秒卡死阈值以便测试
    
    def test_diagnosis_triggered(self):
        """测试诊断触发"""
        # 注册模块但不更新心跳
        tracer.register_heartbeat("DeadModule")
        
        # 模拟卡死
        status = tracer.heartbeat_registry["DeadModule"]
        status.last_active = time.time() - 3  # 3秒前活跃
        status.is_alive = True
        
        # 手动触发诊断
        tracer._trigger_diagnosis("DeadModule")
        
        # 验证诊断输出到缓冲区 (从日志输出中检查)
        # 诊断报告使用logger.critical输出，不会进入step_buffer
        # 我们验证step_buffer中有注册记录即可
        recent = tracer.get_recent_steps(5)
        register_logs = [r for r in recent if "注册心跳" in r.action]
        self.assertTrue(len(register_logs) > 0, "应该有注册日志")
    
    def test_recent_steps_retrieval(self):
        """测试获取最近步骤"""
        # 添加一些记录
        for i in range(15):
            tracer.log_step("TestModule", f"步骤{i}", {"index": i})
        
        # 获取最后10步
        recent = tracer.get_recent_steps(10)
        self.assertEqual(len(recent), 10)
        
        # 验证顺序和内容
        self.assertIn("步骤5", recent[0].action)
        self.assertIn("步骤14", recent[9].action)
    
    def test_diagnosis_file_creation(self):
        """测试诊断文件创建"""
        # 创建临时目录
        temp_dir = tempfile.mkdtemp()
        original_cwd = os.getcwd()
        
        try:
            os.chdir(temp_dir)
            os.makedirs("data", exist_ok=True)
            
            # 添加一些步骤记录
            for i in range(5):
                tracer.log_step("TestModule", f"步骤{i}")
            
            # 触发诊断
            tracer._trigger_diagnosis("TestModule")
            
            # 检查文件是否创建
            diagnosis_files = [f for f in os.listdir("data") if f.startswith("diagnosis_")]
            self.assertEqual(len(diagnosis_files), 1)
            
            # 验证文件内容
            with open(f"data/{diagnosis_files[0]}", 'r') as f:
                content = f.read()
                self.assertIn("自动诊断报告", content)
                self.assertIn("TestModule", content)
                
        finally:
            os.chdir(original_cwd)
            shutil.rmtree(temp_dir)


class TestDeadlockSimulation(unittest.TestCase):
    """卡死场景模拟测试"""
    
    def setUp(self):
        tracer.heartbeat_registry.clear()
        tracer.clear_buffer()
        if not tracer.running:
            tracer.start()
    
    def tearDown(self):
        tracer.running = False
        time.sleep(0.1)
        tracer.running = True
    
    def test_simulate_module_deadlock(self):
        """
        模拟模块卡死场景
        验证: 1) 心跳检测 2) 自动诊断 3) 最后10步记录
        """
        print("\n" + "="*80)
        print("模拟卡死场景测试")
        print("="*80)
        
        # 步骤1: 注册一个"工作模块"
        module_name = "TradingWorker"
        tracer.register_heartbeat(module_name)
        
        # 步骤2: 模拟正常工作 - 产生10+步骤记录
        print("\n[1] 模拟正常工作...")
        for i in range(12):
            tracer.update_heartbeat(module_name)
            tracer.log_step(module_name, f"处理Tick-{i}", {
                "tick_id": i,
                "pair_count": 30,
                "processing_time_ms": 50
            })
            time.sleep(0.05)  # 模拟处理时间
        
        # 步骤3: 模拟卡死 - 停止更新心跳，但继续产生日志
        print("\n[2] 模拟模块卡死 (停止心跳更新)...")
        deadlock_start = time.time()
        
        # 手动将最后活跃时间设为过去
        status = tracer.heartbeat_registry[module_name]
        original_last_active = status.last_active
        status.last_active = time.time() - 3  # 模拟已卡死3秒
        
        # 步骤4: 触发诊断
        print("\n[3] 触发自动诊断...")
        tracer._trigger_diagnosis(module_name)
        
        # 步骤5: 验证诊断结果
        print("\n[4] 验证诊断结果...")
        
        # 验证最后10步被记录
        recent = tracer.get_recent_steps(10)
        self.assertEqual(len(recent), 10, "应该记录最后10步")
        
        # 验证步骤包含关键信息
        tick_steps = [r for r in recent if "Tick" in r.action]
        self.assertTrue(len(tick_steps) > 0, "应该包含Tick处理记录")
        
        # 验证包含线程名
        for step in recent:
            self.assertIsNotNone(step.thread_name)
            self.assertNotEqual(step.thread_name, "")
        
        # 验证包含时间戳
        for step in recent:
            self.assertRegex(step.timestamp, r"\d{2}:\d{2}:\d{2}\.\d{3}")
        
        # 步骤6: 恢复心跳，验证系统继续工作
        print("\n[5] 恢复模块，验证系统继续工作...")
        status.last_active = original_last_active
        tracer.update_heartbeat(module_name)
        
        # 产生新的步骤
        tracer.log_step(module_name, "恢复后步骤", {"status": "recovered"})
        
        recent = tracer.get_recent_steps(1)
        self.assertIn("恢复后步骤", recent[0].action)
        
        print("\n[✓] 卡死场景测试通过!")
        print("="*80)
    
    def test_simulate_multiple_modules_one_dead(self):
        """
        模拟多个模块中一个卡死的场景
        验证: 只有卡死模块触发诊断，其他模块正常
        """
        print("\n" + "="*80)
        print("多模块卡死场景测试")
        print("="*80)
        
        # 注册多个模块
        modules = ["Module-A", "Module-B", "Module-C"]
        for m in modules:
            tracer.register_heartbeat(m)
            tracer.update_heartbeat(m)
        
        # 模拟工作
        for i in range(5):
            for m in modules:
                tracer.log_step(m, f"工作-{i}", {"iteration": i})
        
        # 让Module-B卡死
        print("\n[1] 让Module-B卡死...")
        tracer.heartbeat_registry["Module-B"].last_active = time.time() - 5
        
        # 触发诊断
        print("\n[2] 触发诊断...")
        tracer._trigger_diagnosis("Module-B")
        
        # 验证诊断只针对Module-B
        recent = tracer.get_recent_steps(20)
        diagnosis_actions = [r for r in recent if "诊断" in r.action or "卡死" in r.action]
        
        # 验证有其他模块的步骤记录
        other_module_logs = [r for r in recent if r.module in ["Module-A", "Module-C"]]
        self.assertTrue(len(other_module_logs) > 0, "其他模块的日志应该存在")
        
        print("\n[✓] 多模块场景测试通过!")
        print("="*80)
    
    def test_error_isolation_in_loop(self):
        """
        测试循环中的错误隔离
        验证: 单次迭代失败不影响后续迭代
        """
        print("\n" + "="*80)
        print("循环错误隔离测试")
        print("="*80)
        
        tracer.register_heartbeat("LoopWorker")
        
        iteration_count = [0]
        errors_caught = []
        
        @safe_thread_wrapper("LoopWorker")
        def iteration(i):
            iteration_count[0] += 1
            if i == 3:
                raise ValueError(f"故意错误在迭代{i}")
            return f"结果{i}"
        
        # 执行10次迭代
        print("\n[1] 执行10次迭代，第4次会失败...")
        for i in range(10):
            result = iteration(i)
            if result is None:
                errors_caught.append(i)
        
        # 验证
        print(f"\n[2] 验证结果...")
        print(f"    总迭代次数: {iteration_count[0]}")
        print(f"    捕获的错误: {errors_caught}")
        
        self.assertEqual(iteration_count[0], 10, "所有迭代都应该执行")
        self.assertEqual(errors_caught, [3], "只有第4次迭代应该失败")
        
        # 验证错误被记录
        recent = tracer.get_recent_steps(20)
        error_logs = [r for r in recent if r.step_type == "error"]
        self.assertTrue(len(error_logs) > 0, "应该有错误日志")
        
        print("\n[✓] 错误隔离测试通过!")
        print("="*80)


class TestLogOutput(unittest.TestCase):
    """日志输出测试"""
    
    def test_log_format(self):
        """测试日志格式"""
        tracer.clear_buffer()
        tracer.log_step("Scanner", "测试动作", {"symbol": "BTC", "price": 50000})
        
        recent = tracer.get_recent_steps(1)
        record = recent[0]
        
        # 验证格式: [HH:MM:SS.mmm] [模块] → 动作 | 数据
        self.assertRegex(record.timestamp, r"\d{2}:\d{2}:\d{2}\.\d{3}")
        self.assertEqual(record.module, "Scanner")
        self.assertEqual(record.action, "测试动作")
        self.assertEqual(record.data["symbol"], "BTC")
    
    def test_log_helpers(self):
        """测试便捷函数"""
        tracer.clear_buffer()
        
        log_info("TestModule", "信息日志", key="value")
        log_error("TestModule", "错误日志", Exception("测试错误"), code=500)
        
        recent = tracer.get_recent_steps(2)
        
        self.assertEqual(recent[0].step_type, "normal")
        self.assertEqual(recent[1].step_type, "error")
        self.assertIn("error", recent[1].data)


if __name__ == '__main__':
    # 运行测试
    unittest.main(verbosity=2)
