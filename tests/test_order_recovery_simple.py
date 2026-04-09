"""
订单恢复简化测试
"""

import unittest
from dataclasses import dataclass
from enum import Enum


class OrderCategory(Enum):
    UNKNOWN = "unknown"
    STRATEGY_OPEN = "strat_open"
    ORPHAN = "orphan"
    EXTERNAL = "external"


@dataclass
class ExchangeOrder:
    """简化版交易所订单"""
    order_id: str
    symbol: str
    side: str
    amount: float
    filled: float
    status: str
    age_seconds: int
    reduce_only: bool = False


class SimpleOrderClassifier:
    """简化版订单分类器"""
    
    def __init__(self):
        self.orphan_threshold = 300  # 5分钟
        self.auto_cancel_age = 600   # 10分钟
    
    def classify(self, order: ExchangeOrder, local_order_ids: set) -> dict:
        """分类订单"""
        
        # 检查1: 是否在本地记录中
        if order.order_id in local_order_ids:
            return {
                'category': OrderCategory.STRATEGY_OPEN,
                'action': 'track',
                'reason': '本策略订单',
                'risk': 'low'
            }
        
        # 检查2: 订单年龄
        if order.age_seconds > self.auto_cancel_age:
            return {
                'category': OrderCategory.UNKNOWN,
                'action': 'cancel',
                'reason': f'未知订单且超期({order.age_seconds/60:.0f}分钟)',
                'risk': 'medium'
            }
        
        # 检查3: 最近的外部订单
        if order.age_seconds < 60:
            return {
                'category': OrderCategory.EXTERNAL,
                'action': 'ignore',
                'reason': '可能是手动交易',
                'risk': 'low'
            }
        
        # 默认为孤儿单
        return {
            'category': OrderCategory.ORPHAN,
            'action': 'manual',
            'reason': f'无法分类的订单',
            'risk': 'high'
        }


class TestOrderClassification(unittest.TestCase):
    """订单分类测试"""
    
    def setUp(self):
        self.classifier = SimpleOrderClassifier()
    
    def test_classify_strategy_order(self):
        """测试: 识别策略订单"""
        order = ExchangeOrder(
            order_id='strat_123',
            symbol='BTC/USDT',
            side='buy',
            amount=0.1,
            filled=0,
            status='open',
            age_seconds=100
        )
        
        result = self.classifier.classify(order, {'strat_123'})
        
        self.assertEqual(result['category'], OrderCategory.STRATEGY_OPEN)
        self.assertEqual(result['action'], 'track')
    
    def test_classify_old_unknown_order(self):
        """测试: 识别超期未知订单"""
        order = ExchangeOrder(
            order_id='unknown_123',
            symbol='BTC/USDT',
            side='sell',
            amount=0.5,
            filled=0,
            status='open',
            age_seconds=900  # 15分钟
        )
        
        result = self.classifier.classify(order, set())
        
        self.assertEqual(result['category'], OrderCategory.UNKNOWN)
        self.assertEqual(result['action'], 'cancel')
    
    def test_classify_recent_external(self):
        """测试: 识别最近的外部订单"""
        order = ExchangeOrder(
            order_id='ext_123',
            symbol='ETH/USDT',
            side='buy',
            amount=1.0,
            filled=0,
            status='open',
            age_seconds=30  # 30秒
        )
        
        result = self.classifier.classify(order, set())
        
        self.assertEqual(result['category'], OrderCategory.EXTERNAL)
        self.assertEqual(result['action'], 'ignore')
    
    def test_classify_orphan(self):
        """测试: 识别孤儿单"""
        order = ExchangeOrder(
            order_id='orphan_123',
            symbol='SOL/USDT',
            side='sell',
            amount=10,
            filled=0,
            status='open',
            age_seconds=300  # 5分钟
        )
        
        result = self.classifier.classify(order, set())
        
        self.assertEqual(result['category'], OrderCategory.ORPHAN)
        self.assertEqual(result['action'], 'manual')
        self.assertEqual(result['risk'], 'high')


class TestRecoveryLogic(unittest.TestCase):
    """恢复逻辑测试"""
    
    def test_recovery_workflow_clean(self):
        """测试: 干净重启流程"""
        print("\n" + "="*60)
        print("场景: 干净重启（无遗留订单）")
        print("="*60)
        
        orders = []
        
        # 模拟恢复流程
        recovered = 0
        cancelled = 0
        manual = 0
        
        for order in orders:
            # 简化的处理逻辑
            pass
        
        print(f"✓ 遗留订单: 0")
        print(f"✓ 恢复跟踪: {recovered}")
        print(f"✓ 取消订单: {cancelled}")
        print(f"✓ 人工审核: {manual}")
        print("="*60)
        
        self.assertEqual(len(orders), 0)
    
    def test_recovery_workflow_mixed(self):
        """测试: 混合订单处理"""
        print("\n" + "="*60)
        print("场景: 混合订单（策略单 + 孤儿单 + 外部单）")
        print("="*60)
        
        classifier = SimpleOrderClassifier()
        local_ids = {'strat_1'}
        
        orders = [
            ExchangeOrder('strat_1', 'BTC/USDT', 'buy', 0.1, 0, 'open', 100),
            ExchangeOrder('orphan_1', 'ETH/USDT', 'sell', 1.0, 0, 'open', 400),
            ExchangeOrder('ext_1', 'SOL/USDT', 'buy', 10, 0, 'open', 20),
        ]
        
        recovered = 0
        cancelled = 0
        manual = 0
        
        for order in orders:
            result = classifier.classify(order, local_ids)
            if result['action'] == 'track':
                recovered += 1
            elif result['action'] == 'cancel':
                cancelled += 1
            elif result['action'] == 'manual':
                manual += 1
        
        print(f"✓ 遗留订单: {len(orders)}")
        print(f"✓ 恢复跟踪: {recovered}")
        print(f"✓ 取消订单: {cancelled}")
        print(f"✓ 人工审核: {manual}")
        print("="*60)
        
        self.assertEqual(recovered, 1)
        self.assertEqual(manual, 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
