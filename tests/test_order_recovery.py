"""
订单恢复测试
验证策略重启后的订单处理
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import unittest
from unittest.mock import Mock, MagicMock, patch
import time

# Mock config before importing order_recovery
import sys
from unittest.mock import MagicMock

# Create mock modules
mock_config_module = MagicMock()
mock_config_module.get_config.return_value = MagicMock(
    database=MagicMock(state_db=':memory:'),
    notification=MagicMock(enabled=False)
)
mock_config_module.get_db.return_value = MagicMock(
    get_open_positions=Mock(return_value=[])
)

# Add to sys.modules
sys.modules['config'] = mock_config_module
sys.modules['database'] = MagicMock(get_db=mock_config_module.get_db)
sys.modules['visualization'] = MagicMock(
    trace_step=lambda *a, **k: lambda f: f,
    trace_context=MagicMock(),
    log_info=Mock(),
    log_error=Mock()
)

from order_recovery import (
    OrderRecoveryManager, ExchangeOrder, RecoveryDecision, 
    OrderCategory, GracefulShutdownHandler, perform_recovery
)


class MockExchange:
    """模拟交易所"""
    def __init__(self):
        self.open_orders = []
    
    def fetch_open_orders(self):
        return self.open_orders
    
    def cancel_order(self, order_id, symbol):
        self.open_orders = [o for o in self.open_orders if o['id'] != order_id]


class MockAPI:
    """模拟API"""
    def __init__(self):
        self.exchange = MockExchange()
    
    def get_position(self, symbol):
        return None  # 默认无持仓


class TestOrderRecovery(unittest.TestCase):
    """订单恢复测试"""
    
    @patch('order_recovery.get_db')
    def setUp(self, mock_get_db):
        self.api = MockAPI()
        mock_db = Mock()
        mock_db.get_open_positions.return_value = []
        mock_get_db.return_value = mock_db
        
        with patch('order_recovery.get_config') as mock_get_config:
            mock_cfg = Mock()
            mock_cfg.database.state_db = ':memory:'
            mock_get_config.return_value = mock_cfg
            self.manager = OrderRecoveryManager(self.api)
        
        self.manager.db = mock_db
        self.manager.db.get_local_order_ids = Mock(return_value=set())
    
    def test_fetch_empty_orders(self):
        """测试: 无遗留订单"""
        self.api.exchange.open_orders = []
        
        orders = self.manager._fetch_exchange_orders()
        
        self.assertEqual(len(orders), 0)
    
    def test_fetch_single_order(self):
        """测试: 拉取单个订单"""
        self.api.exchange.open_orders = [{
            'id': 'order123',
            'symbol': 'BTC/USDT',
            'side': 'buy',
            'amount': 0.1,
            'filled': 0,
            'remaining': 0.1,
            'price': 50000,
            'status': 'open',
            'type': 'limit',
            'timestamp': int(time.time() * 1000),
            'reduceOnly': False
        }]
        
        orders = self.manager._fetch_exchange_orders()
        
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].order_id, 'order123')
        self.assertEqual(orders[0].symbol, 'BTC/USDT')
    
    def test_classify_strategy_order(self):
        """测试: 识别本策略订单"""
        order = ExchangeOrder(
            order_id='known_order_123',
            symbol='BTC/USDT',
            side='buy',
            amount=0.1,
            filled=0,
            remaining=0.1,
            price=50000,
            status='open',
            order_type='limit',
            timestamp=int(time.time()),
            reduce_only=False
        )
        
        # 模拟本地记录中有此订单
        local_ids = {'known_order_123'}
        
        decision = self.manager._classify_single_order(order, local_ids, [])
        
        self.assertEqual(decision.category, OrderCategory.STRATEGY_OPEN)
        self.assertEqual(decision.action, 'track')
    
    def test_classify_orphan_order(self):
        """测试: 识别孤儿单"""
        order = ExchangeOrder(
            order_id='unknown_order',
            symbol='BTC/USDT',
            side='sell',
            amount=0.5,
            filled=0,
            remaining=0.5,
            price=51000,
            status='open',
            order_type='limit',
            timestamp=int(time.time()) - 600,  # 10分钟前
            reduce_only=False
        )
        
        decision = self.manager._classify_single_order(order, set(), [])
        
        self.assertEqual(decision.category, OrderCategory.ORPHAN)
        self.assertEqual(decision.action, 'manual')
        self.assertEqual(decision.risk_level, 'high')
    
    def test_classify_external_order(self):
        """测试: 识别外部订单（最近的手动交易）"""
        order = ExchangeOrder(
            order_id='external_order',
            symbol='ETH/USDT',
            side='buy',
            amount=1.0,
            filled=0,
            remaining=1.0,
            price=3000,
            status='open',
            order_type='limit',
            timestamp=int(time.time()) - 30,  # 30秒前
            reduce_only=False
        )
        
        decision = self.manager._classify_single_order(order, set(), [])
        
        self.assertEqual(decision.category, OrderCategory.EXTERNAL)
        self.assertEqual(decision.action, 'ignore')
    
    def test_cancel_order(self):
        """测试: 取消订单"""
        self.api.exchange.open_orders = [{
            'id': 'cancel_me',
            'symbol': 'BTC/USDT',
            'side': 'buy',
            'amount': 0.1,
            'filled': 0,
            'remaining': 0.1,
            'price': 50000,
            'status': 'open',
            'type': 'limit',
            'timestamp': int(time.time() * 1000),
            'reduceOnly': False
        }]
        
        order = self.manager._fetch_exchange_orders()[0]
        success = self.manager._cancel_order(order)
        
        self.assertTrue(success)
        self.assertEqual(len(self.api.exchange.open_orders), 0)
    
    def test_reconcile_consistent_positions(self):
        """测试: 持仓一致"""
        from database import PositionRecord
        
        pos = PositionRecord(
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
        
        self.manager.db.get_open_positions.return_value = [pos]
        
        # 模拟交易所持仓与本地一致
        self.api.get_position = Mock(side_effect=[
            {'symbol': 'BTC/USDT', 'side': 'long', 'size': 0.1},
            {'symbol': 'ETH/USDT', 'side': 'short', 'size': 1.0}
        ])
        
        # 执行对账
        self.manager._reconcile_positions()
        
        # 验证没有错误日志
        # (这里主要验证不抛出异常)
    
    def test_reconcile_inconsistent_positions(self):
        """测试: 持仓不一致"""
        from database import PositionRecord
        
        pos = PositionRecord(
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
        
        self.manager.db.get_open_positions.return_value = [pos]
        
        # 模拟交易所持仓方向错误
        self.api.get_position = Mock(side_effect=[
            {'symbol': 'BTC/USDT', 'side': 'short', 'size': 0.1},  # 应该是long
            {'symbol': 'ETH/USDT', 'side': 'short', 'size': 1.0}
        ])
        
        # 执行对账
        self.manager._reconcile_positions()
        
        # 验证会记录错误


class TestRecoveryScenarios(unittest.TestCase):
    """恢复场景测试"""
    
    def setUp(self):
        self.api = MockAPI()
        self.manager = OrderRecoveryManager(self.api)
        self.manager.db = Mock()
        self.manager.db.get_open_positions.return_value = []
    
    def test_scenario_clean_restart(self):
        """
        场景1: 干净重启（无遗留订单）
        期望: 快速完成，无操作
        """
        print("\n" + "="*60)
        print("场景测试: 干净重启")
        print("="*60)
        
        self.api.exchange.open_orders = []
        
        report = self.manager.recover()
        
        self.assertEqual(report['orders_found'], 0)
        self.assertEqual(report['orders_processed'], 0)
        print("✓ 结果: 无遗留订单，恢复完成")
        print("="*60)
    
    def test_scenario_strategy_orders_pending(self):
        """
        场景2: 策略订单未成交
        期望: 恢复跟踪
        """
        print("\n" + "="*60)
        print("场景测试: 策略订单未成交")
        print("="*60)
        
        # 模拟本地记录的订单
        self.manager._get_local_order_ids = Mock(return_value={'strat_order_1'})
        
        self.api.exchange.open_orders = [{
            'id': 'strat_order_1',
            'symbol': 'BTC/USDT',
            'side': 'buy',
            'amount': 0.1,
            'filled': 0,
            'remaining': 0.1,
            'price': 50000,
            'status': 'open',
            'type': 'limit',
            'timestamp': int(time.time() * 1000),
            'reduceOnly': False
        }]
        
        report = self.manager.recover()
        
        self.assertEqual(report['orders_found'], 1)
        self.assertEqual(report['orders_recovered'], 1)
        print("✓ 结果: 恢复跟踪1个策略订单")
        print("="*60)
    
    def test_scenario_orphan_orders_exist(self):
        """
        场景3: 存在孤儿单
        期望: 标记为人工审核
        """
        print("\n" + "="*60)
        print("场景测试: 存在孤儿单")
        print("="*60)
        
        self.api.exchange.open_orders = [{
            'id': 'orphan_order_1',
            'symbol': 'BTC/USDT',
            'side': 'sell',
            'amount': 0.5,
            'filled': 0,
            'remaining': 0.5,
            'price': 51000,
            'status': 'open',
            'type': 'limit',
            'timestamp': int((time.time() - 600) * 1000),  # 10分钟前
            'reduceOnly': False
        }]
        
        report = self.manager.recover()
        
        self.assertEqual(report['orders_found'], 1)
        self.assertEqual(report['orphan_orders'], 1)
        self.assertEqual(len(report['manual_review_required']), 1)
        print("✓ 结果: 发现1个孤儿单，标记人工审核")
        print("="*60)
    
    def test_scenario_mixed_orders(self):
        """
        场景4: 混合订单（策略单 + 孤儿单 + 外部单）
        期望: 分类处理
        """
        print("\n" + "="*60)
        print("场景测试: 混合订单")
        print("="*60)
        
        self.manager._get_local_order_ids = Mock(return_value={'strat_order_1'})
        
        self.api.exchange.open_orders = [
            {  # 策略单
                'id': 'strat_order_1',
                'symbol': 'BTC/USDT',
                'side': 'buy',
                'amount': 0.1,
                'filled': 0,
                'remaining': 0.1,
                'price': 50000,
                'status': 'open',
                'type': 'limit',
                'timestamp': int(time.time() * 1000),
                'reduceOnly': False
            },
            {  # 孤儿单
                'id': 'orphan_1',
                'symbol': 'ETH/USDT',
                'side': 'sell',
                'amount': 1.0,
                'filled': 0,
                'remaining': 1.0,
                'price': 3000,
                'status': 'open',
                'type': 'limit',
                'timestamp': int((time.time() - 600) * 1000),
                'reduceOnly': False
            },
            {  # 外部单（最近）
                'id': 'external_1',
                'symbol': 'SOL/USDT',
                'side': 'buy',
                'amount': 10,
                'filled': 0,
                'remaining': 10,
                'price': 100,
                'status': 'open',
                'type': 'limit',
                'timestamp': int((time.time() - 30) * 1000),
                'reduceOnly': False
            }
        ]
        
        report = self.manager.recover()
        
        self.assertEqual(report['orders_found'], 3)
        self.assertEqual(report['orders_recovered'], 1)  # 策略单
        self.assertEqual(report['orphan_orders'], 1)      # 孤儿单
        print("✓ 结果: 分类处理3个订单（1恢复 + 1人工 + 1忽略）")
        print("="*60)


class TestGracefulShutdown(unittest.TestCase):
    """优雅停机测试"""
    
    def setUp(self):
        self.db = Mock()
        self.handler = GracefulShutdownHandler(self.db)
    
    def test_save_snapshot(self):
        """测试: 保存停机快照"""
        from database import PositionRecord
        
        # 模拟持仓
        pos = PositionRecord(
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
        
        self.db.get_open_positions.return_value = [pos]
        
        snapshot = self.handler.prepare_shutdown()
        
        self.assertEqual(len(snapshot['open_positions']), 1)
        self.assertEqual(snapshot['open_positions'][0]['pair_key'], 'BTC-ETH')
        self.db.save_shutdown_snapshot.assert_called_once()
    
    def test_resume_from_snapshot(self):
        """测试: 从快照恢复"""
        mock_snapshot = {
            'timestamp': '2026-04-09T14:00:00',
            'open_positions': [{'pair_key': 'BTC-ETH'}]
        }
        
        self.db.get_shutdown_snapshot.return_value = mock_snapshot
        
        snapshot = self.handler.resume_from_snapshot()
        
        self.assertIsNotNone(snapshot)
        self.assertEqual(len(snapshot['open_positions']), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
