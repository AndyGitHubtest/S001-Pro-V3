"""
风险评估模型
VaR (Value at Risk) + 压力测试
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    """风险等级"""
    LOW = "low"           # 低风险
    MEDIUM = "medium"     # 中风险
    HIGH = "high"         # 高风险
    CRITICAL = "critical" # 极高风险


@dataclass
class RiskMetrics:
    """风险指标"""
    # VaR指标
    var_95: float         # 95%置信度VaR
    var_99: float         # 99%置信度VaR
    cvar_95: float        # 条件VaR (CVaR)
    
    # 回撤指标
    max_drawdown: float   # 最大回撤
    current_drawdown: float  # 当前回撤
    
    # 波动率指标
    volatility: float     # 波动率
    sharpe_ratio: float   # 夏普比率
    sortino_ratio: float  # 索提诺比率
    
    # 风险等级
    level: RiskLevel
    score: float          # 风险评分 (0-100)


class VaRModel:
    """
    VaR (Value at Risk) 模型
    
    计算在给定置信水平下的最大可能损失
    """
    
    def __init__(self, confidence_levels: List[float] = None):
        """
        Args:
            confidence_levels: 置信水平列表，默认[0.95, 0.99]
        """
        self.confidence_levels = confidence_levels or [0.95, 0.99]
    
    def calculate_var(self, returns: np.ndarray, 
                     method: str = "historical") -> Dict[float, float]:
        """
        计算VaR
        
        Args:
            returns: 收益率数组
            method: 计算方法 (historical/parametric/monte_carlo)
            
        Returns:
            {置信水平: VaR值}
        """
        if len(returns) == 0:
            return {level: 0.0 for level in self.confidence_levels}
        
        var_results = {}
        
        if method == "historical":
            # 历史模拟法
            for level in self.confidence_levels:
                var = np.percentile(returns, (1 - level) * 100)
                var_results[level] = abs(var)
        
        elif method == "parametric":
            # 参数法 (假设正态分布)
            mean = np.mean(returns)
            std = np.std(returns)
            
            from scipy import stats
            for level in self.confidence_levels:
                z_score = stats.norm.ppf(1 - level)
                var = -(mean + z_score * std)
                var_results[level] = max(0, var)
        
        elif method == "monte_carlo":
            # 蒙特卡洛模拟
            mean = np.mean(returns)
            std = np.std(returns)
            
            # 模拟10000次
            simulations = np.random.normal(mean, std, 10000)
            
            for level in self.confidence_levels:
                var = np.percentile(simulations, (1 - level) * 100)
                var_results[level] = abs(var)
        
        return var_results
    
    def calculate_cvar(self, returns: np.ndarray, 
                      confidence: float = 0.95) -> float:
        """
        计算CVaR (条件VaR / Expected Shortfall)
        
        CVaR是超过VaR阈值时的平均损失
        """
        if len(returns) == 0:
            return 0.0
        
        var = np.percentile(returns, (1 - confidence) * 100)
        cvar = np.mean(returns[returns <= var])
        
        return abs(cvar)


class DrawdownAnalyzer:
    """回撤分析器"""
    
    @staticmethod
    def calculate_drawdowns(equity_curve: np.ndarray) -> Dict:
        """
        计算回撤
        
        Args:
            equity_curve: 权益曲线
            
        Returns:
            回撤统计
        """
        # 计算历史最高
        running_max = np.maximum.accumulate(equity_curve)
        
        # 计算回撤
        drawdowns = (equity_curve - running_max) / running_max
        
        # 最大回撤
        max_drawdown = np.min(drawdowns)
        
        # 当前回撤
        current_drawdown = drawdowns[-1]
        
        # 回撤持续时间
        is_drawdown = drawdowns < 0
        drawdown_periods = []
        current_period = 0
        
        for is_dd in is_drawdown:
            if is_dd:
                current_period += 1
            else:
                if current_period > 0:
                    drawdown_periods.append(current_period)
                current_period = 0
        
        avg_drawdown_days = np.mean(drawdown_periods) if drawdown_periods else 0
        max_drawdown_days = np.max(drawdown_periods) if drawdown_periods else 0
        
        return {
            'max_drawdown': abs(max_drawdown),
            'current_drawdown': abs(current_drawdown),
            'avg_drawdown_days': avg_drawdown_days,
            'max_drawdown_days': max_drawdown_days,
            'drawdown_series': drawdowns
        }


class StressTest:
    """
    压力测试
    
    模拟极端市场条件下的表现
    """
    
    def __init__(self):
        self.scenarios = {
            'market_crash': {
                'description': '市场暴跌 (-20%)',
                'price_shock': -0.20,
                'volatility_spike': 3.0
            },
            'market_spike': {
                'description': '市场暴涨 (+20%)',
                'price_shock': 0.20,
                'volatility_spike': 2.5
            },
            'liquidity_crisis': {
                'description': '流动性危机',
                'spread_widening': 5.0,
                'slippage_increase': 3.0
            },
            'flash_crash': {
                'description': '闪崩 (-10% in 1min)',
                'price_shock': -0.10,
                'time_horizon': '1min'
            }
        }
    
    def run_stress_test(self, positions: List[Dict], 
                       scenario: str) -> Dict:
        """
        运行压力测试
        
        Args:
            positions: 持仓列表
            scenario: 场景名称
            
        Returns:
            压力测试结果
        """
        if scenario not in self.scenarios:
            raise ValueError(f"未知场景: {scenario}")
        
        params = self.scenarios[scenario]
        
        # 计算冲击影响
        total_pnl = 0
        position_impacts = []
        
        for pos in positions:
            impact = self._calculate_position_impact(pos, params)
            total_pnl += impact['pnl']
            position_impacts.append(impact)
        
        return {
            'scenario': scenario,
            'description': params['description'],
            'total_pnl': total_pnl,
            'position_impacts': position_impacts,
            'survival': total_pnl > -1000  # 假设1000 USDT为生存阈值
        }
    
    def _calculate_position_impact(self, position: Dict, 
                                  params: Dict) -> Dict:
        """计算单个持仓影响"""
        quantity = position.get('quantity', 0)
        entry_price = position.get('entry_price', 0)
        side = position.get('side', 'long')
        
        # 价格冲击
        price_shock = params.get('price_shock', 0)
        
        # 计算P&L
        if side == 'long':
            pnl = quantity * entry_price * price_shock
        else:
            pnl = -quantity * entry_price * price_shock
        
        return {
            'symbol': position.get('symbol', 'unknown'),
            'pnl': pnl,
            'pnl_pct': price_shock * 100
        }
    
    def run_all_scenarios(self, positions: List[Dict]) -> Dict[str, Dict]:
        """运行所有场景"""
        results = {}
        
        for scenario_name in self.scenarios.keys():
            results[scenario_name] = self.run_stress_test(positions, scenario_name)
        
        return results


class RiskManager:
    """
    风险管理器
    
    综合风险评估与监控
    """
    
    def __init__(self):
        self.var_model = VaRModel()
        self.stress_test = StressTest()
        self.drawdown_analyzer = DrawdownAnalyzer()
        
        # 风险阈值
        self.thresholds = {
            'var_95_max': 0.05,           # 5%最大VaR
            'max_drawdown_max': 0.15,     # 15%最大回撤
            'daily_loss_limit': 100,       # 100 USDT日亏损限制
            'position_concentration': 0.5  # 50%最大持仓集中度
        }
        
        # 历史数据
        self.returns_history: List[float] = []
        self.equity_history: List[float] = []
    
    def update(self, daily_return: float, equity: float):
        """更新历史数据"""
        self.returns_history.append(daily_return)
        self.equity_history.append(equity)
        
        # 限制历史长度
        if len(self.returns_history) > 252:  # 1年
            self.returns_history = self.returns_history[-252:]
        if len(self.equity_history) > 252:
            self.equity_history = self.equity_history[-252:]
    
    def assess_risk(self, positions: List[Dict], 
                   current_equity: float) -> RiskMetrics:
        """
        综合风险评估
        
        Returns:
            RiskMetrics
        """
        # 计算VaR
        if len(self.returns_history) >= 30:
            returns_array = np.array(self.returns_history)
            var_results = self.var_model.calculate_var(returns_array)
            cvar = self.var_model.calculate_cvar(returns_array)
        else:
            var_results = {0.95: 0.0, 0.99: 0.0}
            cvar = 0.0
        
        # 计算回撤
        if len(self.equity_history) >= 2:
            dd_stats = self.drawdown_analyzer.calculate_drawdowns(
                np.array(self.equity_history)
            )
        else:
            dd_stats = {
                'max_drawdown': 0.0,
                'current_drawdown': 0.0
            }
        
        # 计算波动率和比率
        if len(self.returns_history) >= 2:
            volatility = np.std(self.returns_history) * np.sqrt(252)
            
            # 夏普比率
            avg_return = np.mean(self.returns_history) * 252
            sharpe = avg_return / volatility if volatility > 0 else 0
            
            # 索提诺比率
            downside_returns = [r for r in self.returns_history if r < 0]
            downside_std = np.std(downside_returns) * np.sqrt(252) if downside_returns else 1
            sortino = avg_return / downside_std if downside_std > 0 else 0
        else:
            volatility = 0.0
            sharpe = 0.0
            sortino = 0.0
        
        # 风险评分 (0-100)
        score = self._calculate_risk_score(
            var_results.get(0.95, 0),
            dd_stats['max_drawdown'],
            volatility
        )
        
        # 风险等级
        level = self._determine_risk_level(score)
        
        return RiskMetrics(
            var_95=var_results.get(0.95, 0.0),
            var_99=var_results.get(0.99, 0.0),
            cvar_95=cvar,
            max_drawdown=dd_stats['max_drawdown'],
            current_drawdown=dd_stats['current_drawdown'],
            volatility=volatility,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            level=level,
            score=score
        )
    
    def _calculate_risk_score(self, var_95: float, 
                             max_dd: float, 
                             volatility: float) -> float:
        """计算风险评分"""
        # VaR分数 (权重30%)
        var_score = min(var_95 * 100 / self.thresholds['var_95_max'] * 30, 30)
        
        # 回撤分数 (权重40%)
        dd_score = min(max_dd * 100 / self.thresholds['max_drawdown_max'] * 40, 40)
        
        # 波动率分数 (权重30%)
        vol_score = min(volatility * 100 / 0.5 * 30, 30)  # 假设50%为高波动
        
        return var_score + dd_score + vol_score
    
    def _determine_risk_level(self, score: float) -> RiskLevel:
        """确定风险等级"""
        if score < 25:
            return RiskLevel.LOW
        elif score < 50:
            return RiskLevel.MEDIUM
        elif score < 75:
            return RiskLevel.HIGH
        else:
            return RiskLevel.CRITICAL
    
    def check_limits(self, metrics: RiskMetrics, 
                    daily_pnl: float) -> List[str]:
        """
        检查风险限制
        
        Returns:
            警告列表
        """
        warnings = []
        
        if metrics.var_95 > self.thresholds['var_95_max']:
            warnings.append(f"VaR(95%)超标: {metrics.var_95:.2%}")
        
        if metrics.max_drawdown > self.thresholds['max_drawdown_max']:
            warnings.append(f"最大回撤超标: {metrics.max_drawdown:.2%}")
        
        if daily_pnl < -self.thresholds['daily_loss_limit']:
            warnings.append(f"日亏损超标: {daily_pnl:.2f} USDT")
        
        return warnings
    
    def generate_report(self, positions: List[Dict],
                       current_equity: float) -> Dict:
        """生成风险报告"""
        metrics = self.assess_risk(positions, current_equity)
        
        # 压力测试
        stress_results = self.stress_test.run_all_scenarios(positions)
        
        return {
            'timestamp': datetime.now().isoformat(),
            'metrics': {
                'var_95': f"{metrics.var_95:.2%}",
                'var_99': f"{metrics.var_99:.2%}",
                'cvar_95': f"{metrics.cvar_95:.2%}",
                'max_drawdown': f"{metrics.max_drawdown:.2%}",
                'current_drawdown': f"{metrics.current_drawdown:.2%}",
                'volatility': f"{metrics.volatility:.2%}",
                'sharpe_ratio': f"{metrics.sharpe_ratio:.2f}",
                'sortino_ratio': f"{metrics.sortino_ratio:.2f}",
                'risk_level': metrics.level.value,
                'risk_score': f"{metrics.score:.1f}/100"
            },
            'stress_test': stress_results,
            'warnings': self.check_limits(metrics, 0)
        }


# 便捷函数
def calculate_portfolio_var(returns: List[float], 
                           confidence: float = 0.95) -> float:
    """便捷计算组合VaR"""
    model = VaRModel([confidence])
    results = model.calculate_var(np.array(returns))
    return results.get(confidence, 0.0)


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    print("="*60)
    print("风险评估模型测试")
    print("="*60)
    
    # 生成模拟收益数据
    np.random.seed(42)
    returns = np.random.normal(0.001, 0.02, 100)  # 均值0.1%, 标准差2%
    equity = 1000 * np.cumprod(1 + returns)
    
    # 测试1: VaR计算
    print("\n1. VaR计算")
    var_model = VaRModel()
    
    for method in ["historical", "parametric", "monte_carlo"]:
        var_results = var_model.calculate_var(returns, method=method)
        print(f"\n  {method}:")
        print(f"    VaR(95%): {var_results[0.95]:.2%}")
        print(f"    VaR(99%): {var_results[0.99]:.2%}")
    
    # 测试2: 回撤分析
    print("\n2. 回撤分析")
    dd_analyzer = DrawdownAnalyzer()
    dd_stats = dd_analyzer.calculate_drawdowns(equity)
    print(f"  最大回撤: {dd_stats['max_drawdown']:.2%}")
    print(f"  当前回撤: {dd_stats['current_drawdown']:.2%}")
    print(f"  平均回撤天数: {dd_stats['avg_drawdown_days']:.1f}")
    
    # 测试3: 压力测试
    print("\n3. 压力测试")
    stress = StressTest()
    
    positions = [
        {'symbol': 'BTC/USDT', 'side': 'long', 'quantity': 0.1, 'entry_price': 50000},
        {'symbol': 'ETH/USDT', 'side': 'short', 'quantity': 1.0, 'entry_price': 3000}
    ]
    
    for scenario in stress.scenarios.keys():
        result = stress.run_stress_test(positions, scenario)
        print(f"\n  {result['description']}:")
        print(f"    总盈亏: {result['total_pnl']:.2f} USDT")
        print(f"    能否存活: {'✅' if result['survival'] else '❌'}")
    
    # 测试4: 综合风险评估
    print("\n4. 综合风险评估")
    risk_mgr = RiskManager()
    
    for ret, eq in zip(returns, equity):
        risk_mgr.update(ret, eq)
    
    metrics = risk_mgr.assess_risk(positions, equity[-1])
    
    print(f"  VaR(95%): {metrics.var_95:.2%}")
    print(f"  最大回撤: {metrics.max_drawdown:.2%}")
    print(f"  夏普比率: {metrics.sharpe_ratio:.2f}")
    print(f"  索提诺比率: {metrics.sortino_ratio:.2f}")
    print(f"  风险等级: {metrics.level.value.upper()}")
    print(f"  风险评分: {metrics.score:.1f}/100")
    
    # 生成报告
    print("\n5. 风险报告")
    report = risk_mgr.generate_report(positions, equity[-1])
    print(f"  报告生成时间: {report['timestamp']}")
    print(f"  警告数: {len(report['warnings'])}")
    
    print("\n" + "="*60)
