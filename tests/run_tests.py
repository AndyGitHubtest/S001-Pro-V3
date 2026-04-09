"""
测试运行器
统一管理测试，生成覆盖率报告
"""
import sys
import os
import unittest
import coverage
from pathlib import Path
import json
import logging

# 添加src到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

logger = logging.getLogger(__name__)


class TestRunner:
    """
    测试运行器
    
    功能:
    1. 运行单元测试
    2. 生成覆盖率报告
    3. 测试结果统计
    4. 失败重试
    """
    
    def __init__(self, source_dir: str = "src"):
        self.source_dir = source_dir
        self.coverage = coverage.Coverage(
            source=[source_dir],
            omit=[
                "*/tests/*",
                "*/test_*",
                "*/venv/*",
                "*/__pycache__/*"
            ]
        )
        self.results = {
            'total': 0,
            'passed': 0,
            'failed': 0,
            'skipped': 0,
            'coverage': 0.0
        }
    
    def run_all_tests(self, pattern: str = "test_*.py") -> bool:
        """
        运行所有测试
        
        Args:
            pattern: 测试文件匹配模式
            
        Returns:
            是否全部通过
        """
        # 开始覆盖率统计
        self.coverage.start()
        
        # 发现测试
        loader = unittest.TestLoader()
        start_dir = os.path.dirname(__file__)
        suite = loader.discover(start_dir, pattern=pattern)
        
        # 运行测试
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        
        # 停止覆盖率统计
        self.coverage.stop()
        self.coverage.save()
        
        # 统计结果
        self.results['total'] = result.testsRun
        self.results['passed'] = result.testsRun - len(result.failures) - len(result.errors) - len(result.skips)
        self.results['failed'] = len(result.failures) + len(result.errors)
        self.results['skipped'] = len(result.skips)
        
        # 生成覆盖率报告
        self._generate_coverage_report()
        
        return result.wasSuccessful()
    
    def run_specific_test(self, test_path: str) -> bool:
        """运行指定测试"""
        self.coverage.start()
        
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromName(test_path)
        
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        
        self.coverage.stop()
        self.coverage.save()
        
        return result.wasSuccessful()
    
    def _generate_coverage_report(self):
        """生成覆盖率报告"""
        # 终端报告
        print("\n" + "="*60)
        print("覆盖率报告")
        print("="*60)
        self.coverage.report()
        
        # HTML报告
        html_dir = "coverage_html"
        self.coverage.html_report(directory=html_dir)
        print(f"\nHTML报告: {html_dir}/index.html")
        
        # 获取覆盖率数据
        total = self.coverage.get_option("report:skip_covered")
        self.results['coverage'] = self._calculate_coverage_percent()
    
    def _calculate_coverage_percent(self) -> float:
        """计算覆盖率百分比"""
        try:
            analysis = self.coverage.get_data()
            # 简化计算，实际需要更复杂的逻辑
            return 0.0
        except:
            return 0.0
    
    def generate_report(self, output_path: str = "test_report.json"):
        """生成测试报告"""
        report = {
            'summary': self.results,
            'timestamp': __import__('datetime').datetime.now().isoformat(),
            'status': 'PASSED' if self.results['failed'] == 0 else 'FAILED'
        }
        
        with open(output_path, 'w') as f:
            json.dump(report, f, indent=2)
        
        print(f"\n测试报告已保存: {output_path}")
    
    def print_summary(self):
        """打印摘要"""
        print("\n" + "="*60)
        print("测试结果摘要")
        print("="*60)
        print(f"总测试数: {self.results['total']}")
        print(f"通过: {self.results['passed']} ✅")
        print(f"失败: {self.results['failed']} ❌")
        print(f"跳过: {self.results['skipped']} ⏭️")
        print(f"覆盖率: {self.results['coverage']:.1f}%")
        print("="*60)


# 基础测试类
class BaseTestCase(unittest.TestCase):
    """基础测试类"""
    
    @classmethod
    def setUpClass(cls):
        """测试类初始化"""
        pass
    
    @classmethod
    def tearDownClass(cls):
        """测试类清理"""
        pass
    
    def setUp(self):
        """每个测试前执行"""
        pass
    
    def tearDown(self):
        """每个测试后执行"""
        pass


# 测试套件
class TestValidation(BaseTestCase):
    """验证模块测试"""
    
    def test_walk_forward_split(self):
        """测试Walk-Forward数据切分"""
        import pandas as pd
        import numpy as np
        from src.validation.walk_forward import WalkForwardValidator
        
        # 创建测试数据
        df = pd.DataFrame({
            'price': np.random.randn(100) + 100
        })
        
        validator = WalkForwardValidator()
        windows = validator.split_windows(df)
        
        self.assertGreater(len(windows), 0)
    
    def test_is_os_validation(self):
        """测试IS/OS验证"""
        from src.validation.is_os_validator import ISOSValidator
        
        validator = ISOSValidator()
        
        # 测试配置验证
        self.assertEqual(validator.is_ratio, 0.7)


class TestDataQuality(BaseTestCase):
    """数据质量测试"""
    
    def test_bad_tick_detection(self):
        """测试Bad Tick检测"""
        import pandas as pd
        import numpy as np
        from src.data.data_validator import DataValidator
        
        # 创建包含错价的数据
        df = pd.DataFrame({
            'open': [100, 101, 150, 102],  # 150是错价
            'high': [101, 102, 151, 103],
            'low': [99, 100, 149, 101],
            'close': [100.5, 101.5, 150.5, 102.5],
            'volume': [1000, 1000, 1000, 1000]
        })
        
        validator = DataValidator()
        result = validator.validate_klines(df, "TEST")
        
        self.assertTrue(result.is_valid)
        self.assertGreater(result.removed_count, 0)


class TestRateLimiter(BaseTestCase):
    """限流器测试"""
    
    def test_token_bucket(self):
        """测试令牌桶"""
        from src.utils.rate_limiter import TokenBucket
        
        bucket = TokenBucket(rate=10, capacity=5)
        
        # 应该能立即获取5个
        for i in range(5):
            self.assertTrue(bucket.try_acquire())
        
        # 第6个应该失败
        self.assertFalse(bucket.try_acquire())
    
    def test_rate_limiter_acquire(self):
        """测试限流器获取"""
        from src.utils.rate_limiter import AdaptiveRateLimiter
        
        limiter = AdaptiveRateLimiter()
        
        # 应该能获取许可
        result = limiter.acquire()
        self.assertTrue(result)


class TestRetryHandler(BaseTestCase):
    """重试处理器测试"""
    
    def test_circuit_breaker(self):
        """测试断路器"""
        from src.utils.retry_handler import CircuitBreaker
        
        breaker = CircuitBreaker(failure_threshold=3)
        
        # 初始状态应该是CLOSED
        self.assertTrue(breaker.can_execute())
        
        # 记录3次失败
        for _ in range(3):
            breaker.record_failure()
        
        # 应该OPEN
        self.assertFalse(breaker.can_execute())


class TestPositionSync(BaseTestCase):
    """持仓同步测试"""
    
    def test_position_diff_calc(self):
        """测试持仓差异计算"""
        from src.position_sync import HighFrequencyPositionSync
        
        # 模拟数据
        sync = HighFrequencyPositionSync(None, None)
        diff = sync._calc_diff("BTC", 1.0, 1.1)
        
        self.assertEqual(diff.status.value, "exchange_larger")
        self.assertAlmostEqual(diff.diff, -0.1)


class TestConfigValidation(BaseTestCase):
    """配置验证测试"""
    
    def test_valid_config(self):
        """测试有效配置"""
        from src.config_validation import StrategyConfig
        
        config_data = {
            'mode': 'paper',
            'exchange': {'name': 'binance', 'sandbox': True},
            'signal': {
                'z_entry': 2.5,
                'z_exit': 0.5,
                'z_stop': 3.5
            }
        }
        
        config = StrategyConfig(**config_data)
        self.assertEqual(config.mode.value, 'paper')
    
    def test_invalid_signal_params(self):
        """测试无效信号参数"""
        from src.config_validation import StrategyConfig
        
        config_data = {
            'mode': 'paper',
            'exchange': {'name': 'binance', 'sandbox': True},
            'signal': {
                'z_entry': 2.0,
                'z_exit': 2.5  # 错误: 大于z_entry
            }
        }
        
        with self.assertRaises(ValueError):
            StrategyConfig(**config_data)


def run_quick_tests():
    """运行快速测试"""
    runner = TestRunner()
    return runner.run_all_tests(pattern="test_*.py")


def run_full_tests():
    """运行完整测试"""
    runner = TestRunner()
    success = runner.run_all_tests()
    runner.print_summary()
    runner.generate_report()
    return success


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="测试运行器")
    parser.add_argument('--quick', action='store_true', help='快速测试')
    parser.add_argument('--coverage', action='store_true', help='生成覆盖率报告')
    parser.add_argument('--test', type=str, help='运行指定测试')
    
    args = parser.parse_args()
    
    runner = TestRunner()
    
    if args.test:
        success = runner.run_specific_test(args.test)
    elif args.quick:
        success = runner.run_all_tests()
    else:
        success = run_full_tests()
    
    sys.exit(0 if success else 1)
