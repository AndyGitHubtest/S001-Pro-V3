"""
滑点与手续费模型
提供更真实的回测和实盘成本估算
"""
import numpy as np
from typing import Dict, List, Tuple
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"


class FeeType(Enum):
    MAKER = "maker"  # 限价单成交
    TAKER = "taker"  # 市价单成交


@dataclass
class ExecutionCost:
    """执行成本"""
    fee: float           # 手续费
    slippage: float      # 滑点
    total_cost: float    # 总成本
    fill_price: float    # 实际成交价格
    expected_price: float  # 预期价格


@dataclass  
class MarketDepth:
    """市场深度"""
    price: float
    volume: float


class SlippageModel:
    """
    滑点模型
    
    基于市场深度模拟滑点:
    - 小单 (< 1%深度): 滑点小
    - 中单 (1-5%深度): 中等滑点  
    - 大单 (> 5%深度): 滑点大
    """
    
    def __init__(self, base_slippage: float = 0.0005):
        """
        Args:
            base_slippage: 基础滑点 (默认0.05%)
        """
        self.base_slippage = base_slippage
        
    def estimate_slippage(self, 
                         order_qty: float,
                         orderbook: Dict[str, List[MarketDepth]],
                         side: str) -> float:
        """
        估算滑点
        
        Args:
            order_qty: 订单数量
            orderbook: {'bids': [...], 'asks': [...]}
            side: 'buy' or 'sell'
            
        Returns:
            滑点百分比 (正数表示不利滑点)
        """
        if side == 'buy':
            levels = orderbook.get('asks', [])
        else:
            levels = orderbook.get('bids', [])
        
        if not levels:
            return self.base_slippage * 3  # 无深度，假设大滑点
        
        # 计算订单需要吃掉的深度
        remaining_qty = order_qty
        total_cost = 0
        total_filled = 0
        
        for level in levels:
            if remaining_qty <= 0:
                break
            
            fill_qty = min(remaining_qty, level.volume)
            total_cost += fill_qty * level.price
            total_filled += fill_qty
            remaining_qty -= fill_qty
        
        if total_filled < order_qty:
            # 深度不足，需要更高价格成交
            logger.warning(f"市场深度不足，订单量{order_qty}，可用深度{total_filled}")
            return self.base_slippage * 5  # 深度不足，大滑点
        
        # 计算平均成交价格
        avg_price = total_cost / total_filled
        best_price = levels[0].price
        
        # 滑点 = (平均价 - 最优价) / 最优价
        if side == 'buy':
            slippage = (avg_price - best_price) / best_price
        else:
            slippage = (best_price - avg_price) / best_price
        
        return max(slippage, self.base_slippage)  # 至少基础滑点
    
    def simulate_fill(self,
                     order_qty: float,
                     order_price: float,
                     orderbook: Dict[str, List[MarketDepth]],
                     order_type: OrderType,
                     side: str) -> Tuple[float, float]:
        """
        模拟成交
        
        Returns:
            (fill_price, fill_qty)
        """
        if order_type == OrderType.MARKET:
            # 市价单立即成交，但有滑点
            slippage = self.estimate_slippage(order_qty, orderbook, side)
            fill_price = order_price * (1 + slippage) if side == 'buy' else order_price * (1 - slippage)
            return fill_price, order_qty
        
        else:  # LIMIT
            # 限价单可能部分成交或不成交
            if side == 'buy' and order_price >= orderbook['asks'][0].price:
                # 买单价格 >= 卖一价，会成交
                slippage = self.estimate_slippage(order_qty, orderbook, side)
                fill_price = order_price
                return fill_price, order_qty
            elif side == 'sell' and order_price <= orderbook['bids'][0].price:
                # 卖单价格 <= 买一价，会成交
                fill_price = order_price
                return fill_price, order_qty
            else:
                # 限价单未触及市场，不成交
                return 0, 0


class FeeModel:
    """
    手续费模型
    
    币安费率结构:
    - 普通用户: Maker 0.1%, Taker 0.1%
    - VIP 1: Maker 0.09%, Taker 0.1%
    - VIP 2: Maker 0.08%, Taker 0.1%
    - VIP 3: Maker 0.042%, Taker 0.06%
    - VIP 4: Maker 0.036%, Taker 0.054%
    - VIP 5: Maker 0.024%, Taker 0.048%
    - VIP 6: Maker 0.015%, Taker 0.03%
    - VIP 7: Maker 0.005%, Taker 0.02%
    - VIP 8: Maker 0.005%, Taker 0.02%
    - VIP 9: Maker 0.005%, Taker 0.02%
    """
    
    FEE_TIERS = {
        0: {'maker': 0.001, 'taker': 0.001},   # 普通用户
        1: {'maker': 0.0009, 'taker': 0.001},
        2: {'maker': 0.0008, 'taker': 0.001},
        3: {'maker': 0.00042, 'taker': 0.0006},
        4: {'maker': 0.00036, 'taker': 0.00054},
        5: {'maker': 0.00024, 'taker': 0.00048},
        6: {'maker': 0.00015, 'taker': 0.0003},
        7: {'maker': 0.00005, 'taker': 0.0002},
        8: {'maker': 0.00005, 'taker': 0.0002},
        9: {'maker': 0.00005, 'taker': 0.0002},
    }
    
    def __init__(self, vip_level: int = 0, use_bnb_discount: bool = False):
        """
        Args:
            vip_level: VIP等级 (0-9)
            use_bnb_discount: 是否使用BNB折扣 (额外25% off)
        """
        self.vip_level = vip_level
        self.use_bnb_discount = use_bnb_discount
        self.fees = self.FEE_TIERS.get(vip_level, self.FEE_TIERS[0])
        
        if use_bnb_discount:
            self.fees['maker'] *= 0.75
            self.fees['taker'] *= 0.75
    
    def calculate_fee(self, notional_value: float, fee_type: FeeType) -> float:
        """
        计算手续费
        
        Args:
            notional_value: 名义价值 (price * qty)
            fee_type: maker or taker
            
        Returns:
            手续费金额
        """
        rate = self.fees.get(fee_type.value, 0.001)
        return notional_value * rate
    
    def estimate_execution_cost(self,
                               order_qty: float,
                               order_price: float,
                               order_type: OrderType,
                               slippage_model: SlippageModel = None,
                               orderbook: Dict = None) -> ExecutionCost:
        """
        估算完整执行成本
        
        Returns:
            ExecutionCost对象
        """
        notional = order_qty * order_price
        
        # 确定费率类型
        if order_type == OrderType.LIMIT:
            fee_type = FeeType.MAKER
        else:
            fee_type = FeeType.TAKER
        
        # 计算手续费
        fee = self.calculate_fee(notional, fee_type)
        
        # 计算滑点
        if slippage_model and orderbook:
            side = 'buy' if order_qty > 0 else 'sell'
            slippage_pct = slippage_model.estimate_slippage(abs(order_qty), orderbook, side)
        else:
            # 默认滑点
            slippage_pct = 0.0005 if order_type == OrderType.LIMIT else 0.001
        
        slippage_cost = notional * slippage_pct
        
        # 实际成交价格
        if order_qty > 0:  # 买单
            fill_price = order_price * (1 + slippage_pct)
        else:  # 卖单
            fill_price = order_price * (1 - slippage_pct)
        
        return ExecutionCost(
            fee=fee,
            slippage=slippage_cost,
            total_cost=fee + slippage_cost,
            fill_price=fill_price,
            expected_price=order_price
        )


class BacktestCostModel:
    """
    回测成本模型
    
    为回测提供真实的成本估算
    """
    
    def __init__(self, 
                 fee_model: FeeModel = None,
                 slippage_model: SlippageModel = None,
                 use_realistic_slippage: bool = True):
        self.fee_model = fee_model or FeeModel()
        self.slippage_model = slippage_model or SlippageModel()
        self.use_realistic_slippage = use_realistic_slippage
        
        # 统计信息
        self.total_fees = 0
        self.total_slippage = 0
        self.trade_count = 0
    
    def simulate_trade(self,
                      qty: float,
                      price: float,
                      side: str,
                      order_type: OrderType = OrderType.LIMIT,
                      market_depth: Dict = None) -> Dict:
        """
        模拟交易并计算成本
        
        Returns:
            {
                'fill_price': 实际成交价格,
                'fee': 手续费,
                'slippage': 滑点成本,
                'total_cost': 总成本,
                'net_pnl': 净盈亏 (扣除成本后)
            }
        """
        notional = abs(qty) * price
        
        # 计算成本
        if market_depth and self.use_realistic_slippage:
            cost = self.fee_model.estimate_execution_cost(
                qty, price, order_type, 
                self.slippage_model, market_depth
            )
        else:
            # 简化模型
            cost = self.fee_model.estimate_execution_cost(qty, price, order_type)
        
        # 更新统计
        self.total_fees += cost.fee
        self.total_slippage += cost.slippage
        self.trade_count += 1
        
        return {
            'fill_price': cost.fill_price,
            'fee': cost.fee,
            'slippage': cost.slippage,
            'total_cost': cost.total_cost,
            'expected_price': price,
            'price_impact': abs(cost.fill_price - price) / price
        }
    
    def get_stats(self) -> Dict:
        """获取成本统计"""
        if self.trade_count == 0:
            return {}
        
        return {
            'total_trades': self.trade_count,
            'total_fees': self.total_fees,
            'total_slippage': self.total_slippage,
            'total_cost': self.total_fees + self.total_slippage,
            'avg_cost_per_trade': (self.total_fees + self.total_slippage) / self.trade_count,
            'avg_cost_pct': (self.total_fees + self.total_slippage) / self.trade_count / 10000  # 假设平均名义价值10000
        }
    
    def reset_stats(self):
        """重置统计"""
        self.total_fees = 0
        self.total_slippage = 0
        self.trade_count = 0


# 预定义成本模型
COST_MODELS = {
    'conservative': BacktestCostModel(  # 保守估计
        fee_model=FeeModel(vip_level=0),
        slippage_model=SlippageModel(base_slippage=0.001)
    ),
    'realistic': BacktestCostModel(  # 现实估计
        fee_model=FeeModel(vip_level=3, use_bnb_discount=True),
        slippage_model=SlippageModel(base_slippage=0.0005)
    ),
    'optimistic': BacktestCostModel(  # 乐观估计
        fee_model=FeeModel(vip_level=6, use_bnb_discount=True),
        slippage_model=SlippageModel(base_slippage=0.0002)
    ),
    'zero': BacktestCostModel(  # 零成本（用于对比测试）
        fee_model=FeeModel(vip_level=9),
        slippage_model=SlippageModel(base_slippage=0)
    )
}


def get_cost_model(model_name: str = 'realistic') -> BacktestCostModel:
    """获取预定义成本模型"""
    return COST_MODELS.get(model_name, COST_MODELS['realistic'])


# 使用示例
if __name__ == "__main__":
    # 创建成本模型
    model = get_cost_model('realistic')
    
    # 模拟交易
    trades = [
        {'qty': 0.1, 'price': 50000, 'side': 'buy', 'type': OrderType.LIMIT},
        {'qty': -0.1, 'price': 50100, 'side': 'sell', 'type': OrderType.LIMIT},
        {'qty': 0.1, 'price': 50000, 'side': 'buy', 'type': OrderType.MARKET},
        {'qty': -0.1, 'price': 50100, 'side': 'sell', 'type': OrderType.MARKET},
    ]
    
    print("=" * 80)
    print("成本模型测试")
    print("=" * 80)
    
    for i, trade in enumerate(trades, 1):
        result = model.simulate_trade(
            qty=trade['qty'],
            price=trade['price'],
            side=trade['side'],
            order_type=trade['type']
        )
        
        print(f"\nTrade {i}: {trade['side'].upper()} {abs(trade['qty'])} BTC @ {trade['price']}")
        print(f"  Order Type: {trade['type'].value}")
        print(f"  Expected Price: ${result['expected_price']:.2f}")
        print(f"  Fill Price: ${result['fill_price']:.2f}")
        print(f"  Fee: ${result['fee']:.4f}")
        print(f"  Slippage: ${result['slippage']:.4f}")
        print(f"  Total Cost: ${result['total_cost']:.4f}")
        print(f"  Price Impact: {result['price_impact']:.4%}")
    
    # 打印统计
    stats = model.get_stats()
    print("\n" + "=" * 80)
    print("统计信息")
    print("=" * 80)
    print(f"总交易次数: {stats['total_trades']}")
    print(f"总手续费: ${stats['total_fees']:.4f}")
    print(f"总滑点: ${stats['total_slippage']:.4f}")
    print(f"总成本: ${stats['total_cost']:.4f}")
    print(f"平均每笔成本: ${stats['avg_cost_per_trade']:.4f}")
    print(f"平均成本率: {stats['avg_cost_pct']:.4%}")
    print("=" * 80)
