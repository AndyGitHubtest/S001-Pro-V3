"""
IS/OS回测验证器
确保回测有样本外验证，防止数据泄露
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


@dataclass
class ISOSResult:
    """IS/OS验证结果"""
    is_sharpe: float
    os_sharpe: float
    is_pf: float
    os_pf: float
    is_drawdown: float
    os_drawdown: float
    sharpe_decay: float
    pf_decay: float
    overfitting_score: float
    is_reliable: bool


class ISOSValidator:
    """
    IS/OS回测验证器
    
    强制要求:
    1. 训练集和测试集时间分离
    2. 无未来数据泄露
    3. 统计IS/OS差异
    4. 过拟合评分
    
    使用方案:
    - 70%数据训练 (IS)
    - 30%数据测试 (OS)
    - 时间顺序严格分离
    """
    
    def __init__(self, 
                 is_ratio: float = 0.7,
                 min_is_days: int = 60,
                 min_os_days: int = 30):
        """
        Args:
            is_ratio: 训练集比例 (默认70%)
            min_is_days: 最小训练天数
            min_os_days: 最小测试天数
        """
        self.is_ratio = is_ratio
        self.min_is_days = min_is_days
        self.min_os_days = min_os_days
        
        # 过拟合阈值
        self.overfitting_thresholds = {
            'sharpe_decay': 0.30,    # Sharpe衰减>30%为过拟合
            'pf_decay': 0.30,        # PF衰减>30%为过拟合
            'return_correlation': 0.5  # IS/OS收益相关性<0.5为过拟合
        }
    
    def split_data(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        切分IS/OS数据
        
        严格按时间顺序:
        - IS: 前70%数据
        - OS: 后30%数据
        
        Returns:
            (is_data, os_data)
        """
        n = len(df)
        split_idx = int(n * self.is_ratio)
        
        # 确保最小天数
        if split_idx < self.min_is_days:
            raise ValueError(f"训练集数据不足: {split_idx} < {self.min_is_days}")
        
        if n - split_idx < self.min_os_days:
            raise ValueError(f"测试集数据不足: {n-split_idx} < {self.min_os_days}")
        
        is_data = df.iloc[:split_idx].copy()
        os_data = df.iloc[split_idx:].copy()
        
        logger.info(f"数据切分: IS={len(is_data)}条 ({self.is_ratio*100:.0f}%), "
                   f"OS={len(os_data)}条 ({(1-self.is_ratio)*100:.0f}%)")
        
        return is_data, os_data
    
    def validate_backtest(self, 
                         df: pd.DataFrame,
                         backtest_fn: Callable,
                         params: Dict) -> ISOSResult:
        """
        验证回测结果
        
        Args:
            df: 完整历史数据
            backtest_fn: 回测函数 (data, params) -> trades
            params: 策略参数
            
        Returns:
            ISOSResult
        """
        # 切分数据
        is_data, os_data = self.split_data(df)
        
        # IS回测
        logger.info("运行IS回测...")
        is_trades = backtest_fn(is_data, params)
        is_metrics = self._calc_metrics(is_trades)
        
        # OS回测 (关键：使用相同参数，但不同数据)
        logger.info("运行OS回测...")
        os_trades = backtest_fn(os_data, params)
        os_metrics = self._calc_metrics(os_trades)
        
        # 计算衰减
        sharpe_decay = self._calc_decay(is_metrics['sharpe'], os_metrics['sharpe'])
        pf_decay = self._calc_decay(is_metrics['pf'], os_metrics['pf'])
        
        # 过拟合评分 (0-1, 越高越差)
        overfitting_score = self._calc_overfitting_score(
            is_metrics, os_metrics, is_trades, os_trades
        )
        
        # 是否可靠
        is_reliable = (
            sharpe_decay < self.overfitting_thresholds['sharpe_decay'] and
            pf_decay < self.overfitting_thresholds['pf_decay'] and
            overfitting_score < 0.5
        )
        
        result = ISOSResult(
            is_sharpe=is_metrics['sharpe'],
            os_sharpe=os_metrics['sharpe'],
            is_pf=is_metrics['pf'],
            os_pf=os_metrics['pf'],
            is_drawdown=is_metrics['max_dd'],
            os_drawdown=os_metrics['max_dd'],
            sharpe_decay=sharpe_decay,
            pf_decay=pf_decay,
            overfitting_score=overfitting_score,
            is_reliable=is_reliable
        )
        
        self._log_result(result)
        
        return result
    
    def _calc_metrics(self, trades: List[Dict]) -> Dict:
        """计算回测指标"""
        if not trades:
            return {'sharpe': 0, 'pf': 0, 'max_dd': 0, 'returns': []}
        
        returns = [t.get('pnl', 0) for t in trades]
        returns_arr = np.array(returns)
        
        # Sharpe
        if len(returns_arr) > 1 and returns_arr.std() > 0:
            sharpe = returns_arr.mean() / returns_arr.std() * np.sqrt(252)
        else:
            sharpe = 0
        
        # Profit Factor
        profits = sum(r for r in returns if r > 0)
        losses = abs(sum(r for r in returns if r < 0))
        pf = profits / losses if losses > 0 else float('inf')
        
        # Max Drawdown
        cumulative = np.cumsum(returns_arr)
        running_max = np.maximum.accumulate(cumulative)
        drawdown = running_max - cumulative
        max_dd = np.max(drawdown) if len(drawdown) > 0 else 0
        
        return {
            'sharpe': sharpe,
            'pf': pf,
            'max_dd': max_dd,
            'returns': returns
        }
    
    def _calc_decay(self, is_value: float, os_value: float) -> float:
        """计算衰减比例"""
        if abs(is_value) < 1e-10:
            return 0
        
        decay = (is_value - os_value) / abs(is_value)
        
        # 如果OS比IS好，衰减为负 (这是好事)
        # 我们只关心IS高估的情况
        return max(0, decay)
    
    def _calc_overfitting_score(self, 
                               is_metrics: Dict, 
                               os_metrics: Dict,
                               is_trades: List[Dict],
                               os_trades: List[Dict]) -> float:
        """
        计算过拟合评分
        
        综合多个指标:
        1. Sharpe衰减
        2. PF衰减  
        3. 交易次数差异
        4. 收益分布差异
        
        Returns:
            0-1之间的评分，越高表示过拟合越严重
        """
        scores = []
        
        # 1. Sharpe衰减评分
        sharpe_decay = self._calc_decay(is_metrics['sharpe'], os_metrics['sharpe'])
        scores.append(min(sharpe_decay, 1.0))
        
        # 2. PF衰减评分
        pf_decay = self._calc_decay(is_metrics['pf'], os_metrics['pf'])
        scores.append(min(pf_decay, 1.0))
        
        # 3. 交易频率差异
        if is_trades and os_trades:
            is_freq = len(is_trades) / self.min_is_days
            os_freq = len(os_trades) / self.min_os_days
            freq_diff = abs(is_freq - os_freq) / max(is_freq, 1e-10)
            scores.append(min(freq_diff, 1.0))
        
        # 4. 收益为正比例差异
        if is_metrics['returns'] and os_metrics['returns']:
            is_win_rate = sum(1 for r in is_metrics['returns'] if r > 0) / len(is_metrics['returns'])
            os_win_rate = sum(1 for r in os_metrics['returns'] if r > 0) / len(os_metrics['returns'])
            wr_diff = abs(is_win_rate - os_win_rate)
            scores.append(wr_diff)
        
        return np.mean(scores) if scores else 0.5
    
    def _log_result(self, result: ISOSResult):
        """记录结果"""
        logger.info("="*60)
        logger.info("IS/OS验证结果")
        logger.info("="*60)
        logger.info(f"IS Sharpe:  {result.is_sharpe:.2f}")
        logger.info(f"OS Sharpe:  {result.os_sharpe:.2f}")
        logger.info(f"Sharpe衰减: {result.sharpe_decay:.1%}")
        logger.info(f"IS PF:      {result.is_pf:.2f}")
        logger.info(f"OS PF:      {result.os_pf:.2f}")
        logger.info(f"PF衰减:     {result.pf_decay:.1%}")
        logger.info(f"过拟合评分: {result.overfitting_score:.2f}")
        logger.info(f"结果可靠:   {'✅ 是' if result.is_reliable else '❌ 否'}")
        logger.info("="*60)
        
        if not result.is_reliable:
            logger.error("⚠️  检测到严重过拟合，建议:")
            logger.error("   1. 减少参数数量")
            logger.error("   2. 增加训练数据")
            logger.error("   3. 简化策略逻辑")


class WalkForwardISOS:
    """
    滚动IS/OS验证
    
    多次切分验证，更稳健
    """
    
    def __init__(self, n_splits: int = 5):
        self.n_splits = n_splits
        self.validator = ISOSValidator()
    
    def validate(self, 
                df: pd.DataFrame,
                backtest_fn: Callable,
                params: Dict) -> Dict:
        """
        多次滚动验证
        
        Returns:
            综合验证结果
        """
        results = []
        n = len(df)
        
        for i in range(self.n_splits):
            # 滚动切分
            start_idx = int(n * i / self.n_splits)
            end_idx = int(n * (i + 1) / self.n_splits)
            
            split_df = df.iloc[start_idx:end_idx]
            
            try:
                result = self.validator.validate_backtest(
                    split_df, backtest_fn, params
                )
                results.append(result)
            except Exception as e:
                logger.error(f"第{i+1}次验证失败: {e}")
        
        # 综合结果
        if not results:
            return {'reliable': False, 'reason': '所有验证失败'}
        
        reliable_count = sum(1 for r in results if r.is_reliable)
        
        return {
            'reliable': reliable_count >= len(results) * 0.6,  # 60%通过算可靠
            'reliable_ratio': reliable_count / len(results),
            'avg_sharpe_decay': np.mean([r.sharpe_decay for r in results]),
            'avg_pf_decay': np.mean([r.pf_decay for r in results]),
            'avg_overfitting_score': np.mean([r.overfitting_score for r in results]),
            'results': results
        }


# 便捷函数
def validate_isos(df: pd.DataFrame, 
                 backtest_fn: Callable,
                 params: Dict) -> ISOSResult:
    """便捷IS/OS验证"""
    validator = ISOSValidator()
    return validator.validate_backtest(df, backtest_fn, params)


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    # 创建测试数据
    np.random.seed(42)
    n = 500
    
    dates = pd.date_range('2023-01-01', periods=n, freq='D')
    
    # 生成模拟价格
    trend = np.linspace(0, 0.1, n)
    noise = np.random.randn(n) * 0.02
    prices = 100 * np.exp(np.cumsum(trend + noise))
    
    df = pd.DataFrame({
        'timestamp': dates,
        'open': prices * 0.999,
        'high': prices * 1.01,
        'low': prices * 0.99,
        'close': prices,
        'volume': np.random.rand(n) * 10000
    })
    
    # 模拟回测函数
    def mock_backtest(data, params):
        """模拟回测：随机交易"""
        trades = []
        for i in range(len(data) - 1):
            if np.random.rand() < 0.1:  # 10%概率交易
                pnl = np.random.randn() * 10
                trades.append({'pnl': pnl})
        return trades
    
    # 测试参数
    params = {'z_entry': 2.0, 'z_exit': 0.5}
    
    # 单次IS/OS验证
    print("\n" + "="*60)
    print("单次IS/OS验证")
    print("="*60)
    
    validator = ISOSValidator()
    result = validator.validate_backtest(df, mock_backtest, params)
    
    # 滚动验证
    print("\n" + "="*60)
    print("滚动IS/OS验证 (5次)")
    print("="*60)
    
    wf = WalkForwardISOS(n_splits=5)
    summary = wf.validate(df, mock_backtest, params)
    
    print(f"可靠比例: {summary['reliable_ratio']:.1%}")
    print(f"平均Sharpe衰减: {summary['avg_sharpe_decay']:.1%}")
    print(f"平均PF衰减: {summary['avg_pf_decay']:.1%}")
    print(f"综合可靠: {'✅ 是' if summary['reliable'] else '❌ 否'}")
