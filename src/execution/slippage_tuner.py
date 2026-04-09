"""
滑点参数调优器
基于真实成交数据校准滑点模型
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
from scipy import stats
import logging

logger = logging.getLogger(__name__)


@dataclass
class SlippageStats:
    """滑点统计"""
    mean_slippage_bps: float      # 平均滑点 (基点)
    median_slippage_bps: float    # 中位数滑点
    p90_slippage_bps: float       # 90分位滑点
    p99_slippage_bps: float       # 99分位滑点
    std_slippage_bps: float       # 标准差
    sample_size: int              # 样本数
    
    # 按交易量分组
    by_volume: Dict[str, Dict]    # 不同量级的滑点统计


class SlippageAnalyzer:
    """
    滑点分析器
    
    分析历史成交数据，计算真实滑点
    """
    
    def __init__(self):
        self.trades: List[Dict] = []
    
    def add_trade(self, symbol: str, side: str, quantity: float,
                 expected_price: float, actual_price: float,
                 order_type: str = "market", timestamp: datetime = None):
        """
        添加成交记录
        
        Args:
            symbol: 交易对
            side: buy/sell
            quantity: 数量
            expected_price: 预期价格 (下单时)
            actual_price: 实际成交价格
            order_type: 订单类型
            timestamp: 时间戳
        """
        # 计算滑点 (基点)
        if side == "buy":
            slippage_bps = (actual_price - expected_price) / expected_price * 10000
        else:  # sell
            slippage_bps = (expected_price - actual_price) / expected_price * 10000
        
        trade = {
            'symbol': symbol,
            'side': side,
            'quantity': quantity,
            'notional': quantity * expected_price,
            'expected_price': expected_price,
            'actual_price': actual_price,
            'slippage_bps': slippage_bps,
            'slippage_pct': slippage_bps / 100,
            'order_type': order_type,
            'timestamp': timestamp or datetime.now()
        }
        
        self.trades.append(trade)
    
    def analyze(self) -> SlippageStats:
        """分析滑点统计"""
        if not self.trades:
            return SlippageStats(0, 0, 0, 0, 0, 0, {})
        
        slippages = [t['slippage_bps'] for t in self.trades]
        notionals = [t['notional'] for t in self.trades]
        
        # 基础统计
        stats_result = SlippageStats(
            mean_slippage_bps=np.mean(slippages),
            median_slippage_bps=np.median(slippages),
            p90_slippage_bps=np.percentile(slippages, 90),
            p99_slippage_bps=np.percentile(slippages, 99),
            std_slippage_bps=np.std(slippages),
            sample_size=len(slippages),
            by_volume=self._analyze_by_volume()
        )
        
        return stats_result
    
    def _analyze_by_volume(self) -> Dict[str, Dict]:
        """按交易量分组分析"""
        # 定义量级
        tiers = {
            'small': (0, 1000),      # < $1k
            'medium': (1000, 10000),  # $1k-$10k
            'large': (10000, 50000),  # $10k-$50k
            'xlarge': (50000, float('inf'))  # > $50k
        }
        
        results = {}
        
        for tier_name, (min_notional, max_notional) in tiers.items():
            tier_trades = [
                t for t in self.trades
                if min_notional <= t['notional'] < max_notional
            ]
            
            if tier_trades:
                slippages = [t['slippage_bps'] for t in tier_trades]
                results[tier_name] = {
                    'count': len(tier_trades),
                    'mean_bps': np.mean(slippages),
                    'p90_bps': np.percentile(slippages, 90),
                    'avg_notional': np.mean([t['notional'] for t in tier_trades])
                }
        
        return results
    
    def get_recommended_params(self) -> Dict:
        """获取推荐参数"""
        stats = self.analyze()
        
        return {
            'base_slippage_bps': max(stats.median_slippage_bps, 5),  # 至少5bps
            'max_slippage_bps': stats.p90_slippage_bps,
            'extreme_slippage_bps': stats.p99_slippage_bps,
            'by_volume_tier': {
                tier: {
                    'base_bps': max(data['mean_bps'], 5),
                    'max_bps': data['p90_bps']
                }
                for tier, data in stats.by_volume.items()
            }
        }
    
    def compare_with_model(self, model_slippage_func) -> Dict:
        """
        与现有模型对比
        
        Args:
            model_slippage_func: 模型滑点函数
            
        Returns:
            对比结果
        """
        errors = []
        
        for trade in self.trades:
            # 模型预测的滑点
            model_slippage = model_slippage_func(
                trade['quantity'],
                trade['expected_price'],
                trade['order_type']
            )
            
            # 实际滑点
            actual_slippage = trade['slippage_bps']
            
            # 误差
            error = abs(model_slippage - actual_slippage)
            errors.append(error)
        
        return {
            'mae': np.mean(errors),  # 平均绝对误差
            'rmse': np.sqrt(np.mean([e**2 for e in errors])),  # 均方根误差
            'max_error': max(errors)
        }


class SlippageTuner:
    """
    滑点调优器
    
    基于历史数据自动调整滑点参数
    """
    
    def __init__(self, analyzer: SlippageAnalyzer = None):
        self.analyzer = analyzer or SlippageAnalyzer()
        
        # 当前参数
        self.current_params = {
            'base_slippage_bps': 5.0,
            'volume_threshold': 10000,  # $10k
            'large_order_multiplier': 2.0
        }
    
    def tune_from_history(self, trade_history: List[Dict]) -> Dict:
        """
        基于历史交易调优
        
        Args:
            trade_history: 历史成交记录列表
            
        Returns:
            优化后的参数
        """
        # 加载历史数据
        for trade in trade_history:
            self.analyzer.add_trade(**trade)
        
        # 分析
        stats = self.analyzer.analyze()
        
        if stats.sample_size < 30:
            logger.warning(f"样本数不足 ({stats.sample_size})，使用默认参数")
            return self.current_params
        
        # 计算优化参数
        new_params = self._optimize_params(stats)
        
        # 验证改进
        improvement = self._validate_improvement(new_params)
        
        logger.info(f"滑点参数调优完成: {improvement}")
        
        return new_params
    
    def _optimize_params(self, stats: SlippageStats) -> Dict:
        """优化参数"""
        # 基础滑点使用中位数+1σ
        base_slippage = stats.median_slippage_bps + stats.std_slippage_bps * 0.5
        
        # 确保至少5bps
        base_slippage = max(base_slippage, 5.0)
        
        # 根据交易量调整
        volume_multipliers = {}
        for tier, data in stats.by_volume.items():
            if tier == 'small':
                volume_multipliers[tier] = 1.0
            elif tier == 'medium':
                volume_multipliers[tier] = max(1.2, data['mean_bps'] / base_slippage)
            elif tier == 'large':
                volume_multipliers[tier] = max(1.5, data['mean_bps'] / base_slippage)
            else:  # xlarge
                volume_multipliers[tier] = max(2.0, data['mean_bps'] / base_slippage)
        
        return {
            'base_slippage_bps': round(base_slippage, 2),
            'volume_multipliers': volume_multipliers,
            'max_slippage_bps': round(stats.p99_slippage_bps, 2),
            'confidence_interval': {
                'lower': round(stats.median_slippage_bps - stats.std_slippage_bps, 2),
                'upper': round(stats.median_slippage_bps + stats.std_slippage_bps, 2)
            }
        }
    
    def _validate_improvement(self, new_params: Dict) -> Dict:
        """验证参数改进"""
        # 这里可以添加回测验证逻辑
        return {
            'params_updated': True,
            'old_base': self.current_params['base_slippage_bps'],
            'new_base': new_params['base_slippage_bps'],
            'change_pct': (new_params['base_slippage_bps'] - 
                          self.current_params['base_slippage_bps']) / 
                         self.current_params['base_slippage_bps'] * 100
        }
    
    def generate_report(self) -> str:
        """生成调优报告"""
        stats = self.analyzer.analyze()
        params = self.analyzer.get_recommended_params()
        
        report = []
        report.append("="*60)
        report.append("滑点参数调优报告")
        report.append("="*60)
        report.append(f"\n样本数: {stats.sample_size}")
        report.append(f"统计期间: {self.analyzer.trades[0]['timestamp'] if self.analyzer.trades else 'N/A'}")
        report.append(f"\n滑点统计 (基点 bps):")
        report.append(f"  平均: {stats.mean_slippage_bps:.2f}")
        report.append(f"  中位数: {stats.median_slippage_bps:.2f}")
        report.append(f"  P90: {stats.p90_slippage_bps:.2f}")
        report.append(f"  P99: {stats.p99_slippage_bps:.2f}")
        report.append(f"  标准差: {stats.std_slippage_bps:.2f}")
        
        report.append(f"\n按交易量分级:")
        for tier, data in stats.by_volume.items():
            report.append(f"  {tier}: {data['mean_bps']:.2f} bps (n={data['count']})")
        
        report.append(f"\n推荐参数:")
        report.append(f"  基础滑点: {params['base_slippage_bps']:.2f} bps")
        report.append(f"  最大滑点: {params['max_slippage_bps']:.2f} bps")
        report.append(f"  极端滑点: {params['extreme_slippage_bps']:.2f} bps")
        
        report.append("="*60)
        
        return "\n".join(report)


# 便捷函数
def analyze_trades(trades: List[Dict]) -> SlippageStats:
    """便捷分析交易滑点"""
    analyzer = SlippageAnalyzer()
    
    for trade in trades:
        analyzer.add_trade(**trade)
    
    return analyzer.analyze()


def tune_slippage_params(trades: List[Dict]) -> Dict:
    """便捷调优滑点参数"""
    tuner = SlippageTuner()
    return tuner.tune_from_history(trades)


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    print("="*60)
    print("滑点参数调优测试")
    print("="*60)
    
    # 生成模拟交易数据
    np.random.seed(42)
    
    trades = []
    base_date = datetime.now() - timedelta(days=30)
    
    for i in range(100):
        # 模拟不同量级
        if i < 40:
            quantity = np.random.uniform(0.01, 0.1)
            price = 50000
            tier_factor = 1.0
        elif i < 70:
            quantity = np.random.uniform(0.1, 0.5)
            price = 50000
            tier_factor = 1.3
        elif i < 90:
            quantity = np.random.uniform(0.5, 1.0)
            price = 50000
            tier_factor = 1.8
        else:
            quantity = np.random.uniform(1.0, 2.0)
            price = 50000
            tier_factor = 2.5
        
        # 基础滑点5bps + 量级因子 + 随机波动
        base_slippage = 0.0005
        actual_slippage = base_slippage * tier_factor + np.random.normal(0, 0.0002)
        
        expected_price = price
        actual_price = price * (1 + actual_slippage)
        
        trades.append({
            'symbol': 'BTC/USDT',
            'side': 'buy',
            'quantity': quantity,
            'expected_price': expected_price,
            'actual_price': actual_price,
            'order_type': 'market',
            'timestamp': base_date + timedelta(hours=i)
        })
    
    # 测试1: 分析滑点
    print("\n1. 滑点分析")
    stats = analyze_trades(trades)
    print(f"  样本数: {stats.sample_size}")
    print(f"  平均滑点: {stats.mean_slippage_bps:.2f} bps")
    print(f"  P90滑点: {stats.p90_slippage_bps:.2f} bps")
    
    # 测试2: 调优参数
    print("\n2. 参数调优")
    params = tune_slippage_params(trades)
    print(f"  基础滑点: {params['base_slippage_bps']:.2f} bps")
    print(f"  量级乘数:")
    for tier, mult in params['volume_multipliers'].items():
        print(f"    {tier}: {mult:.2f}x")
    
    # 测试3: 生成报告
    print("\n3. 调优报告")
    tuner = SlippageTuner()
    tuner.tune_from_history(trades)
    print(tuner.generate_report())
    
    print("\n" + "="*60)
