"""
裸仓保护测试
验证配对交易的单边成交防护机制
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import unittest
from unittest.mock import Mock, MagicMock
import time

from trader import NakedPositionProtector, OrderResult, ExchangeAPI
from database import PositionRecord


class TestNakedPositionProtection(unittest.TestCase):
    """裸仓保护测试"""
    
    def setUp(self):
        """设置测试环境"""
        self.api = Mock(spec=ExchangeAPI)
        self.protector = NakedPositionProtector(self.api)
        
        # 创建测试持仓
        self.pos = PositionRecord(
            pair_key="BTC-ETH",
            pool="primary",
            symbol_a="BTC/USDT",
            symbol_b="ETH/USDT",
            direction="long_spread",
            entry_z=2.5,
            entry_price_a=50000.0,
            entry_price_b=3000.0,
            entry_time="2026-04-09T14:00:00",
            qty_a=0.1,
            qty_b=1.0,
            notional=5000.0,
            status='open'
        )
    
    def test_pre_check_pass(self):
        """测试预检通过"""
        # 模拟正常行情
        self.api.get_ticker.side_effect = [
            {'bid': 49900, 'ask': 50100, 'last': 50000, 'spread_pct': 0.004},
            {'bid': 2990, 'ask': 3010, 'last': 3000, 'spread_pct': 0.006}
        ]
        
        result = self.protector._pre_check(self.pos)
        self.assertTrue(result)
    
    def test_pre_check_fail_high_spread(self):
        """测试预检失败 - 价差过大"""
        # 模拟过大价差
        self.api.get_ticker.side_effect = [
            {'bid': 48000, 'ask': 52000, 'last': 50000, 'spread_pct': 0.08},  # 8%价差
            {'bid': 2990, 'ask': 3010, 'last': 3000, 'spread_pct': 0.006}
        ]
        
        result = self.protector._pre_check(self.pos)
        self.assertFalse(result)
    
    def test_confirm_filled_success(self):
        """测试成交确认 - 成功"""
        self.api.check_order_status.return_value = {
            'status': 'closed',
            'filled': 0.1,
            'average_price': 50000.0
        }
        
        result = self.protector._confirm_filled("BTC/USDT", "order123")
        
        self.assertTrue(result['filled'])
        self.assertEqual(result['price'], 50000.0)
        self.assertEqual(result['qty'], 0.1)
    
    def test_confirm_filled_timeout(self):
        """测试成交确认 - 超时"""
        # 模拟订单一直未成交
        self.api.check_order_status.return_value = {
            'status': 'open',
            'filled': 0,
            'average_price': 0
        }
        
        # 减少超时时间以便测试
        self.protector.confirmation_timeout = 0.1
        
        result = self.protector._confirm_filled("BTC/USDT", "order123")
        
        self.assertFalse(result['filled'])
        self.assertEqual(result['status'], 'timeout')
    
    def test_emergency_rollback_success(self):
        """测试紧急回滚 - 成功"""
        original_order = OrderResult(
            success=True,
            order_id="orig123",
            executed_qty=0.1
        )
        
        # 模拟回滚订单成功
        self.api.place_market_order.return_value = OrderResult(
            success=True,
            order_id="rollback123"
        )
        
        self.api.check_order_status.return_value = {
            'status': 'closed',
            'filled': 0.1,
            'average_price': 50100.0
        }
        
        result = self.protector._emergency_rollback(
            "BTC/USDT", original_order, "long_spread"
        )
        
        self.assertTrue(result)
        # 验证使用了reduce_only
        call_args = self.api.place_market_order.call_args
        self.assertTrue(call_args[1]['reduce_only'])
    
    def test_emergency_rollback_fail(self):
        """测试紧急回滚 - 失败"""
        original_order = OrderResult(
            success=True,
            order_id="orig123",
            executed_qty=0.1
        )
        
        # 模拟回滚订单一直失败
        self.api.place_market_order.return_value = OrderResult(
            success=False,
            error="Insufficient margin"
        )
        
        result = self.protector._emergency_rollback(
            "BTC/USDT", original_order, "long_spread"
        )
        
        self.assertFalse(result)
    
    def test_execute_pair_order_success(self):
        """测试配对订单执行 - 双边成功"""
        # 预检
        self.api.get_ticker.side_effect = [
            {'bid': 49900, 'ask': 50100, 'last': 50000},
            {'bid': 2990, 'ask': 3010, 'last': 3000}
        ]
        
        # A边订单
        self.api.place_market_order.side_effect = [
            OrderResult(success=True, order_id="order_a123"),
            OrderResult(success=True, order_id="order_b123")
        ]
        
        # 确认成交
        self.api.check_order_status.side_effect = [
            {'status': 'closed', 'filled': 0.1, 'average_price': 50000.0},
            {'status': 'closed', 'filled': 1.0, 'average_price': 3000.0}
        ]
        
        success, message = self.protector.execute_pair_order(self.pos)
        
        self.assertTrue(success)
        self.assertIn("成功", message)
        self.assertEqual(self.pos.entry_price_a, 50000.0)
        self.assertEqual(self.pos.entry_price_b, 3000.0)
    
    def test_execute_pair_order_b_fails_rollback_success(self):
        """测试B边失败，回滚A边成功"""
        # 预检
        self.api.get_ticker.side_effect = [
            {'bid': 49900, 'ask': 50100, 'last': 50000},
            {'bid': 2990, 'ask': 3010, 'last': 3000}
        ]
        
        # A边成功，B边失败
        self.api.place_market_order.side_effect = [
            OrderResult(success=True, order_id="order_a123"),  # A边成功
            OrderResult(success=False, error="API timeout"),    # B边失败
            OrderResult(success=True, order_id="rollback123")   # 回滚成功
        ]
        
        # A边确认成交
        self.api.check_order_status.side_effect = [
            {'status': 'closed', 'filled': 0.1, 'average_price': 50000.0},
            {'status': 'closed', 'filled': 0.1, 'average_price': 50100.0}  # 回滚成交
        ]
        
        success, message = self.protector.execute_pair_order(self.pos)
        
        self.assertFalse(success)
        self.assertIn("B边失败", message)
        self.assertIn("回滚", message)
    
    def test_execute_pair_order_b_fails_rollback_fail(self):
        """测试B边失败，回滚A边也失败 - 裸仓形成"""
        # 预检
        self.api.get_ticker.side_effect = [
            {'bid': 49900, 'ask': 50100, 'last': 50000},
            {'bid': 2990, 'ask': 3010, 'last': 3000}
        ]
        
        # A边成功，B边失败，回滚也失败
        self.api.place_market_order.side_effect = [
            OrderResult(success=True, order_id="order_a123"),   # A边成功
            OrderResult(success=False, error="API timeout"),     # B边失败
            OrderResult(success=False, error="Network error"),   # 回滚失败1
            OrderResult(success=False, error="Network error"),   # 回滚失败2
            OrderResult(success=False, error="Network error"),   # 回滚失败3
            OrderResult(success=True, order_id="force_close")    # 强制平仓成功
        ]
        
        # A边确认成交，回滚确认失败，强制平仓确认成功
        self.api.check_order_status.side_effect = [
            {'status': 'closed', 'filled': 0.1, 'average_price': 50000.0},  # A边成交
            {'status': 'open', 'filled': 0},    # 回滚未成交
            {'status': 'open', 'filled': 0},    # 回滚未成交
            {'status': 'open', 'filled': 0},    # 回滚未成交
            {'status': 'closed', 'filled': 0.1, 'average_price': 50200.0}   # 强制平仓成交
        ]
        
        success, message = self.protector.execute_pair_order(self.pos)
        
        self.assertFalse(success)
        self.assertIn("裸仓已强制平仓", message)
    
    def test_verify_position_consistency_pass(self):
        """测试持仓一致性验证 - 通过"""
        self.api.get_position.side_effect = [
            {'symbol': 'BTC/USDT', 'side': 'long', 'size': 0.1},   # A边多头
            {'symbol': 'ETH/USDT', 'side': 'short', 'size': 1.0}   # B边空头
        ]
        
        result = self.protector.verify_position_consistency(self.pos)
        
        self.assertTrue(result['consistent'])
        self.assertEqual(len(result['issues']), 0)
    
    def test_verify_position_consistency_fail_wrong_side(self):
        """测试持仓一致性验证 - 方向错误"""
        self.api.get_position.side_effect = [
            {'symbol': 'BTC/USDT', 'side': 'short', 'size': 0.1},  # A边方向错误
            {'symbol': 'ETH/USDT', 'side': 'short', 'size': 1.0}
        ]
        
        result = self.protector.verify_position_consistency(self.pos)
        
        self.assertFalse(result['consistent'])
        self.assertTrue(any('方向错误' in issue for issue in result['issues']))
    
    def test_verify_position_consistency_fail_missing(self):
        """测试持仓一致性验证 - 缺失持仓"""
        self.api.get_position.side_effect = [
            None,  # A边无持仓
            {'symbol': 'ETH/USDT', 'side': 'short', 'size': 1.0}
        ]
        
        result = self.protector.verify_position_consistency(self.pos)
        
        self.assertFalse(result['consistent'])
        self.assertTrue(any('无持仓' in issue for issue in result['issues']))


class TestNakedPositionScenario(unittest.TestCase):
    """裸仓场景模拟测试"""
    
    def setUp(self):
        self.api = Mock(spec=ExchangeAPI)
        self.protector = NakedPositionProtector(self.api)
        
        self.pos = PositionRecord(
            pair_key="BTC-ETH",
            pool="primary",
            symbol_a="BTC/USDT",
            symbol_b="ETH/USDT",
            direction="long_spread",
            entry_z=2.5,
            entry_price_a=50000.0,
            entry_price_b=3000.0,
            entry_time="2026-04-09T14:00:00",
            qty_a=0.1,
            qty_b=1.0,
            notional=5000.0,
            status='open'
        )
    
    def test_scenario_network_timeout_during_second_leg(self):
        """
        场景1: 第二边下单时网络超时
        期望: 回滚第一边，不形成裸仓
        """
        print("\n" + "="*60)
        print("场景测试: 第二边网络超时")
        print("="*60)
        
        # 预检通过
        self.api.get_ticker.side_effect = [
            {'bid': 49900, 'ask': 50100, 'last': 50000},
            {'bid': 2990, 'ask': 3010, 'last': 3000}
        ]
        
        # A边成功，B边超时
        self.api.place_market_order.side_effect = [
            OrderResult(success=True, order_id="order_a123"),
            OrderResult(success=False, error="Network timeout after 30s"),
            OrderResult(success=True, order_id="rollback123")  # 回滚成功
        ]
        
        # A边确认成交，回滚确认成交
        self.api.check_order_status.side_effect = [
            {'status': 'closed', 'filled': 0.1, 'average_price': 50000.0},
            {'status': 'closed', 'filled': 0.1, 'average_price': 50100.0}
        ]
        
        success, message = self.protector.execute_pair_order(self.pos)
        
        self.assertFalse(success)
        self.assertIn("B边失败", message)
        print(f"✓ 结果: 开仓失败，消息: {message}")
        print("✓ 裸仓防护成功: A边已回滚")
        print("="*60)
    
    def test_scenario_partial_fill_on_first_leg(self):
        """
        场景2: 第一边部分成交（订单被取消）
        期望: 检测到取消，回滚已成交部分
        """
        print("\n" + "="*60)
        print("场景测试: 第一边部分成交")
        print("="*60)
        
        self.api.get_ticker.side_effect = [
            {'bid': 49900, 'ask': 50100, 'last': 50000},
            {'bid': 2990, 'ask': 3010, 'last': 3000}
        ]
        
        # A边下单成功但被取消（部分成交）
        self.api.place_market_order.side_effect = [
            OrderResult(success=True, order_id="order_a123"),
            OrderResult(success=True, order_id="rollback123")  # 回滚
        ]
        
        # 第一边只成交了50%就被取消
        self.api.check_order_status.side_effect = [
            {'status': 'canceled', 'filled': 0.05, 'average_price': 50000.0},
            {'status': 'closed', 'filled': 0.05, 'average_price': 50100.0}  # 回滚成交
        ]
        
        success, message = self.protector.execute_pair_order(self.pos)
        
        self.assertFalse(success)
        print(f"✓ 结果: 开仓失败，消息: {message}")
        print("✓ 部分成交处理: 订单被取消，拒绝继续")
        print("="*60)
    
    def test_scenario_exchange_api_rate_limit(self):
        """
        场景3: 交易所API限流
        期望: 等待后重试，或在超时前回滚
        """
        print("\n" + "="*60)
        print("场景测试: API限流")
        print("="*60)
        
        self.api.get_ticker.side_effect = [
            {'bid': 49900, 'ask': 50100, 'last': 50000},
            {'bid': 2990, 'ask': 3010, 'last': 3000}
        ]
        
        # A边成功，B边限流
        self.api.place_market_order.side_effect = [
            OrderResult(success=True, order_id="order_a123"),
            OrderResult(success=False, error="Rate limit exceeded"),
            OrderResult(success=True, order_id="rollback123")
        ]
        
        self.api.check_order_status.side_effect = [
            {'status': 'closed', 'filled': 0.1, 'average_price': 50000.0},
            {'status': 'closed', 'filled': 0.1, 'average_price': 50100.0}
        ]
        
        success, message = self.protector.execute_pair_order(self.pos)
        
        self.assertFalse(success)
        print(f"✓ 结果: 开仓失败，消息: {message}")
        print("✓ API限流处理: 回滚第一边")
        print("="*60)


if __name__ == '__main__':
    unittest.main(verbosity=2)
