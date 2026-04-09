"""
Walk-Forward Analysis (WFA) 验证框架
防止数据泄露和过拟合
"""
import numpy as np
import pandas as pd
from typing import List, Dict, Tuple, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


@dataclass
class WFAConfig:
    """WFA配置"""
    train_window: int = 60  # 训练窗口（天）
    test_window: int = 7    # 测试窗口（天）
    step_size: int = 7      # 滚动步长（天）
    min_train_days: int = 30  # 最小训练天数


@dataclass
class WFAResult:
    """WFA结果"""
    fold_id: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_sharpe: float
    test_sharpe: float
    train_pf: float
    test_pf: float
    is_better: bool  # 测试集是否比训练集好（异常检测）


class WalkForwardValidator:
    """
    Walk-Forward分析验证器
    
    核心原则:
    1. 严格的时间顺序，不能使用未来数据
    2. 在每个滚动窗口重新训练参数
    3. 统计IS/OS差异，检测过拟合
    """
    
    def __init__(self, config: WFAConfig = None):
        self.cfg = config or WFAConfig()
        self.results: List[WFAResult] = []
        
    def split_windows(self, data: pd.DataFrame) -> List[Tuple[pd.DataFrame, pd.DataFrame]]:
        """
        切分滚动窗口
        
        Returns:
            [(train_data, test_data), ...]
        """
        windows = []
        total_days = len(data)
        
        # 计算窗口数量
        start_idx = self.cfg.min_train_days
        
        while start_idx + self.cfg.test_window < total_days:
            # 训练集: [start_idx - train_window, start_idx)
            train_start = max(0, start_idx - self.cfg.train_window)
            train_data = data.iloc[train_start:start_idx]
            
            # 测试集: [start_idx, start_idx + test_window)
            test_end = min(start_idx + self.cfg.test_window, total_days)
            test_data = data.iloc[start_idx:test_end]
            
            if len(train_data) >= self.cfg.min_train_days and len(test_data) > 0:
                windows.append((train_data, test_data))
            
            start_idx += self.cfg.step_size
        
        logger.info(f"WFA窗口切分完成: {len(windows)}个窗口")
        return windows
    
    def validate_strategy(self, 
                         data: pd.DataFrame,
                         strategy_fn: Callable,
                         param_optimizer: Callable) -> Dict:
        """
        执行Walk-Forward验证
        
        Args:
            data: 完整历史数据
            strategy_fn: 策略函数(data, params) -> trades
            param_optimizer: 参数优化函数(train_data) -> params
            
        Returns:
            验证结果统计
        """
        self.results = []
        windows = self.split_windows(data)
        
        for fold_id, (train_data, test_data) in enumerate(windows):
            logger.info(f"WFA Fold {fold_id + 1}/{len(windows)}")
            
            # 在训练集上优化参数
            params = param_optimizer(train_data)
            
            # 在训练集上回测（IS）
            train_trades = strategy_fn(train_data, params)
            train_metrics = self._calc_metrics(train_trades)
            
            # 在测试集上回测（OS）
            test_trades = strategy_fn(test_data, params)
            test_metrics = self._calc_metrics(test_trades)
            
            # 检测过拟合
            is_better = test_metrics['sharpe'] > train_metrics['sharpe'] * 1.2
            
            result = WFAResult(
                fold_id=fold_id,
                train_start=train_data.index[0],
                train_end=train_data.index[-1],
                test_start=test_data.index[0],
                test_end=test_data.index[-1],
                train_sharpe=train_metrics['sharpe'],
                test_sharpe=test_metrics['sharpe'],
                train_pf=train_metrics['profit_factor'],
                test_pf=test_metrics['profit_factor'],
                is_better=is_better
            )
            self.results.append(result)
            
            logger.info(f"  Train Sharpe: {result.train_sharpe:.2f}, "
                       f"Test Sharpe: {result.test_sharpe:.2f}")
        
        return self._generate_report()
    
    def _calc_metrics(self, trades: List[Dict]) -> Dict:
        """计算回测指标"""
        if not trades:
            return {'sharpe': 0, 'profit_factor': 0, 'max_dd': 0}
        
        pnls = [t['pnl'] for t in trades]
        returns = np.array(pnls)
        
        # 夏普比率
        if len(returns) > 1 and returns.std() > 0:
            sharpe = returns.mean() / returns.std() * np.sqrt(252)
        else:
            sharpe = 0
        
        # 盈亏比
        profits = sum(r for r in returns if r > 0)
        losses = abs(sum(r for r in returns if r < 0))
        pf = profits / losses if losses > 0 else float('inf')
        
        # 最大回撤
        cumulative = np.cumsum(returns)
        running_max = np.maximum.accumulate(cumulative)
        drawdown = running_max - cumulative
        max_dd = np.max(drawdown) if len(drawdown) > 0 else 0
        
        return {
            'sharpe': sharpe,
            'profit_factor': pf,
            'max_dd': max_dd
        }
    
    def _generate_report(self) -> Dict:
        """生成WFA报告"""
        if not self.results:
            return {}
        
        train_sharpes = [r.train_sharpe for r in self.results]
        test_sharpes = [r.test_sharpe for r in self.results]
        
        report = {
            'fold_count': len(self.results),
            'avg_train_sharpe': np.mean(train_sharpes),
            'avg_test_sharpe': np.mean(test_sharpes),
            'sharpe_decay': (np.mean(train_sharpes) - np.mean(test_sharpes)) / 
                          abs(np.mean(train_sharpes)) * 100,
            'consistency_ratio': sum(1 for r in self.results 
                                   if r.test_sharpe > 0) / len(self.results),
            'overfitting_detected': np.mean(test_sharpes) < np.mean(train_sharpes) * 0.5,
            'details': self.results
        }
        
        # 过拟合判定
        if report['overfitting_detected']:
            logger.error(f"⚠️  检测到严重过拟合！Sharpe衰减: {report['sharpe_decay']:.1f}%")
        elif report['sharpe_decay'] > 30:
            logger.warning(f"⚠️  疑似过拟合，Sharpe衰减: {report['sharpe_decay']:.1f}%")
        else:
            logger.info(f"✅ WFA通过，Sharpe衰减: {report['sharpe_decay']:.1f}%")
        
        return report
    
    def plot_results(self, save_path: str = None):
        """绘制WFA结果"""
        try:
            import matplotlib.pyplot as plt
            
            fig, axes = plt.subplots(2, 1, figsize=(12, 8))
            
            # Sharpe对比
            fold_ids = [r.fold_id for r in self.results]
            train_sharpes = [r.train_sharpe for r in self.results]
            test_sharpes = [r.test_sharpe for r in self.results]
            
            axes[0].plot(fold_ids, train_sharpes, 'b-o', label='Train (IS)')
            axes[0].plot(fold_ids, test_sharpes, 'r-s', label='Test (OS)')
            axes[0].axhline(y=0, color='k', linestyle='--', alpha=0.3)
            axes[0].set_xlabel('Fold ID')
            axes[0].set_ylabel('Sharpe Ratio')
            axes[0].set_title('Walk-Forward Analysis: Sharpe Decay')
            axes[0].legend()
            axes[0].grid(True, alpha=0.3)
            
            # PF对比
            train_pfs = [r.train_pf for r in self.results]
            test_pfs = [r.test_pf for r in self.results]
            
            axes[1].plot(fold_ids, train_pfs, 'b-o', label='Train (IS)')
            axes[1].plot(fold_ids, test_pfs, 'r-s', label='Test (OS)')
            axes[1].axhline(y=1, color='k', linestyle='--', alpha=0.3)
            axes[1].set_xlabel('Fold ID')
            axes[1].set_ylabel('Profit Factor')
            axes[1].set_title('Walk-Forward Analysis: Profit Factor')
            axes[1].legend()
            axes[1].grid(True, alpha=0.3)
            
            plt.tight_layout()
            
            if save_path:
                plt.savefig(save_path, dpi=150, bbox_inches='tight')
                logger.info(f"WFA图表已保存: {save_path}")
            else:
                plt.show()
                
        except ImportError:
            logger.warning("matplotlib未安装，无法绘图")


class DataLeakageDetector:
    """
    数据泄露检测器
    
    检测常见的数据泄露模式:
    1. 使用未来均值/标准差
    2. 使用全量数据计算参数
    3. 特征工程泄露目标变量
    """
    
    @staticmethod
    def check_rolling_calculation(df: pd.DataFrame, 
                                   calc_fn: Callable,
                                   window: int) -> bool:
        """
        检查滚动计算是否使用未来数据
        
        Returns:
            True if no leakage, False otherwise
        """
        n = len(df)
        test_point = n // 2
        
        # 使用前半部分数据计算
        train_df = df.iloc[:test_point]
        result_train = calc_fn(train_df, window)
        
        # 使用全量数据计算
        result_full = calc_fn(df, window)
        
        # 如果前半部分的结果不同，说明使用了未来数据
        if isinstance(result_train, (pd.Series, np.ndarray)):
            # 比较前test_point个值
            if len(result_train) > 0 and len(result_full) > 0:
                diff = np.abs(result_train.iloc[-1] - result_full.iloc[test_point-1])
                if diff > 1e-10:
                    logger.error(f"检测到数据泄露！diff={diff}")
                    return False
        
        return True
    
    @staticmethod
    def validate_no_forward_looking(df: pd.DataFrame,
                                    feature_cols: List[str],
                                    target_col: str) -> Dict:
        """
        验证特征不包含未来信息
        
        检查方法:
        1. 特征是否与未来目标值相关
        2. 特征是否包含shift(0)之后的信息
        """
        results = {}
        
        for col in feature_cols:
            # 检查特征是否与未来目标相关
            corr_current = df[col].corr(df[target_col])
            corr_future = df[col].corr(df[target_col].shift(-1))
            
            if abs(corr_future) > abs(corr_current) * 1.5:
                results[col] = {
                    'status': 'LEAKAGE_DETECTED',
                    'corr_current': corr_current,
                    'corr_future': corr_future,
                    'message': f'{col}与未来目标相关性过高，可能泄露'
                }
            else:
                results[col] = {'status': 'OK'}
        
        return results


# 使用示例
if __name__ == "__main__":
    # 配置
    config = WFAConfig(
        train_window=60,   # 60天训练
        test_window=7,     # 7天测试
        step_size=7        # 每周滚动
    )
    
    # 创建验证器
    validator = WalkForwardValidator(config)
    
    # 示例数据
    dates = pd.date_range('2023-01-01', '2024-01-01', freq='D')
    np.random.seed(42)
    data = pd.DataFrame({
        'price_a': np.cumsum(np.random.randn(len(dates)) * 0.02 + 0.0001) + 100,
        'price_b': np.cumsum(np.random.randn(len(dates)) * 0.02 + 0.0001) + 100,
    }, index=dates)
    
    # 示例策略函数
    def example_strategy(data, params):
        """示例策略：简单的均值回归"""
        trades = []
        spread = data['price_a'] - data['price_b']
        
        for i in range(120, len(spread)):
            window = spread.iloc[i-120:i]
            zscore = (spread.iloc[i] - window.mean()) / window.std()
            
            if zscore > params.get('z_entry', 2.0):
                trades.append({'pnl': -0.001})  # 模拟亏损
            elif zscore < -params.get('z_entry', 2.0):
                trades.append({'pnl': 0.002})   # 模拟盈利
        
        return trades
    
    # 示例参数优化
    def example_optimizer(train_data):
        """示例优化器"""
        return {'z_entry': 2.0, 'z_exit': 0.5}
    
    # 执行WFA
    report = validator.validate_strategy(
        data=data,
        strategy_fn=example_strategy,
        param_optimizer=example_optimizer
    )
    
    # 打印报告
    print("\n" + "="*60)
    print("Walk-Forward Analysis Report")
    print("="*60)
    print(f"Fold Count: {report['fold_count']}")
    print(f"Avg Train Sharpe: {report['avg_train_sharpe']:.2f}")
    print(f"Avg Test Sharpe: {report['avg_test_sharpe']:.2f}")
    print(f"Sharpe Decay: {report['sharpe_decay']:.1f}%")
    print(f"Consistency Ratio: {report['consistency_ratio']:.1%}")
    print(f"Overfitting Detected: {report['overfitting_detected']}")
    print("="*60)
