"""
基础测试
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import unittest
import tempfile
import numpy as np

from config import Config
from database import DatabaseManager, PairRecord, PositionRecord


class TestConfig(unittest.TestCase):
    """配置测试"""
    
    def test_default_config(self):
        """测试默认配置"""
        cfg = Config()
        errors = cfg.validate()
        self.assertEqual(len(errors), 0, f"Config validation failed: {errors}")
    
    def test_scoring_weights_sum(self):
        """测试评分权重和为1"""
        cfg = Config()
        s = cfg.scoring
        total = s.w_coint + s.w_corr + s.w_halflife + s.w_zmax + s.w_stability + s.w_volume
        self.assertAlmostEqual(total, 1.0, places=2)


class TestDatabase(unittest.TestCase):
    """数据库测试"""
    
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.db = DatabaseManager(self.temp_db.name)
    
    def tearDown(self):
        import os
        os.unlink(self.temp_db.name)
    
    def test_save_and_load_pairs(self):
        """测试保存和读取配对"""
        pair = PairRecord(
            pool='primary',
            symbol_a='BTC/USDT',
            symbol_b='ETH/USDT',
            score=0.85,
            corr_median=0.78,
            coint_p=0.05,
            adf_p=0.03,
            half_life=12.5,
            corr_std=0.08,
            hurst=0.45,
            zscore_max=2.8,
            spread_std=0.002,
            volume_min=5000000,
            z_entry=2.5,
            z_exit=0.5,
            z_stop=4.0,
            pf=1.5,
            sharpe=1.2,
            total_return=0.15,
            max_dd=0.08,
            trades_count=12
        )
        
        self.db.save_pairs('primary', [pair])
        loaded = self.db.get_active_pairs('primary')
        
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].symbol_a, 'BTC/USDT')
        self.assertEqual(loaded[0].score, 0.85)


class TestScanner(unittest.TestCase):
    """扫描器测试"""
    
    def test_calc_half_life(self):
        """测试半衰期计算"""
        from scanner import Scanner
        
        # 生成OU过程数据
        np.random.seed(42)
        n = 500
        theta = 0.1  # 半衰期约6.9
        spread = np.zeros(n)
        
        for i in range(1, n):
            spread[i] = spread[i-1] - theta * spread[i-1] + np.random.randn() * 0.1
        
        # 创建模拟数据
        data = type('Data', (), {'a': np.exp(spread), 'b': np.ones(n)})()
        
        scanner = Scanner()
        hl = scanner._calc_half_life(data)
        
        # 半衰期应该在合理范围内
        self.assertGreater(hl, 0)
        self.assertLess(hl, 100)


class TestEngine(unittest.TestCase):
    """引擎测试"""
    
    def test_zscore_calculation(self):
        """测试Z-Score计算"""
        from engine import SignalGenerator
        
        gen = SignalGenerator()
        
        # 生成测试数据
        np.random.seed(42)
        n = 200
        beta = 0.5
        spread = np.random.randn(n) * 0.02
        
        prices_a = np.exp(np.cumsum(np.random.randn(n) * 0.01))
        prices_b = prices_a ** beta * np.exp(spread)
        
        zscore, calc_beta = gen.calc_zscore(prices_a, prices_b, 120)
        
        # Z-Score应该在合理范围内
        self.assertIsInstance(zscore, float)
        self.assertIsInstance(calc_beta, float)


if __name__ == '__main__':
    unittest.main()
