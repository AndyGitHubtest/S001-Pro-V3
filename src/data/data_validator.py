"""
数据验证器
过滤Bad Tick、异常值、缺失数据
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class DataQualityIssue(Enum):
    """数据质量问题类型"""
    BAD_TICK = "bad_tick"           # 错价
    PRICE_SPIKE = "price_spike"     # 价格突变
    MISSING_BAR = "missing_bar"     # 缺失K线
    TIMESTAMP_GAP = "timestamp_gap" # 时间戳断层
    VOLUME_ANOMALY = "volume_anomaly" # 成交量异常
    ZERO_PRICE = "zero_price"       # 零价格
    NEGATIVE_PRICE = "negative_price" # 负价格


@dataclass
class ValidationResult:
    """验证结果"""
    is_valid: bool
    issues: List[Dict]
    cleaned_data: Optional[pd.DataFrame] = None
    removed_count: int = 0


class DataValidator:
    """
    数据验证器
    
    功能:
    1. Bad Tick过滤 (3σ原则)
    2. 价格突变检测
    3. 缺失数据检测
    4. 时间戳连续性检查
    5. 零/负价格过滤
    """
    
    def __init__(self,
                 price_spike_threshold: float = 0.05,  # 5%价格突变
                 volume_spike_threshold: float = 5.0,   # 5倍成交量突变
                 zscore_threshold: float = 3.0,         # 3σ原则
                 max_missing_ratio: float = 0.1):       # 最大缺失比例10%
        """
        Args:
            price_spike_threshold: 价格突变阈值 (默认5%)
            volume_spike_threshold: 成交量突变阈值 (默认5倍)
            zscore_threshold: Z-Score阈值 (默认3σ)
            max_missing_ratio: 最大允许缺失比例
        """
        self.price_spike_threshold = price_spike_threshold
        self.volume_spike_threshold = volume_spike_threshold
        self.zscore_threshold = zscore_threshold
        self.max_missing_ratio = max_missing_ratio
        
        # 统计信息
        self.validation_stats = {
            'total_validations': 0,
            'valid_count': 0,
            'invalid_count': 0,
            'issues_by_type': {issue.value: 0 for issue in DataQualityIssue}
        }
    
    def validate_klines(self, df: pd.DataFrame, 
                       symbol: str = "unknown") -> ValidationResult:
        """
        验证K线数据
        
        Args:
            df: K线数据 DataFrame
            symbol: 币种名称
            
        Returns:
            ValidationResult
        """
        self.validation_stats['total_validations'] += 1
        
        if df.empty:
            logger.warning(f"[{symbol}] 数据为空")
            return ValidationResult(is_valid=False, issues=[], removed_count=0)
        
        original_count = len(df)
        issues = []
        cleaned_df = df.copy()
        
        # 1. 检查零价格和负价格
        zero_neg_mask = self._detect_zero_negative_prices(cleaned_df)
        if zero_neg_mask.any():
            count = zero_neg_mask.sum()
            issues.append({
                'type': DataQualityIssue.ZERO_PRICE.value,
                'count': int(count),
                'message': f'发现{count}个零/负价格数据'
            })
            self.statistics_add_issue(DataQualityIssue.ZERO_PRICE, count)
            cleaned_df = cleaned_df[~zero_neg_mask]
        
        # 2. 检测价格突变 (Bad Tick)
        bad_tick_mask = self._detect_bad_ticks(cleaned_df)
        if bad_tick_mask.any():
            count = bad_tick_mask.sum()
            issues.append({
                'type': DataQualityIssue.BAD_TICK.value,
                'count': int(count),
                'message': f'发现{count}个错价 (3σ外)'
            })
            self.statistics_add_issue(DataQualityIssue.BAD_TICK, count)
            cleaned_df = cleaned_df[~bad_tick_mask]
        
        # 3. 检测价格异常波动
        spike_mask = self._detect_price_spikes(cleaned_df)
        if spike_mask.any():
            count = spike_mask.sum()
            issues.append({
                'type': DataQualityIssue.PRICE_SPIKE.value,
                'count': int(count),
                'message': f'发现{count}个价格突变 (>{self.price_spike_threshold*100}%)'
            })
            self.statistics_add_issue(DataQualityIssue.PRICE_SPIKE, count)
            cleaned_df = cleaned_df[~spike_mask]
        
        # 4. 检测成交量异常
        volume_mask = self._detect_volume_anomaly(cleaned_df)
        if volume_mask.any():
            count = volume_mask.sum()
            issues.append({
                'type': DataQualityIssue.VOLUME_ANOMALY.value,
                'count': int(count),
                'message': f'发现{count}个成交量异常'
            })
            self.statistics_add_issue(DataQualityIssue.VOLUME_ANOMALY, count)
            cleaned_df = cleaned_df[~volume_mask]
        
        # 5. 检查时间戳连续性
        timestamp_issues = self._check_timestamp_continuity(cleaned_df)
        if timestamp_issues:
            issues.extend(timestamp_issues)
        
        # 6. 检查缺失比例
        removed_count = original_count - len(cleaned_df)
        missing_ratio = removed_count / original_count
        
        if missing_ratio > self.max_missing_ratio:
            logger.error(f"[{symbol}] 数据缺失比例过高: {missing_ratio:.2%}")
            return ValidationResult(
                is_valid=False,
                issues=issues,
                removed_count=removed_count
            )
        
        is_valid = len(cleaned_df) >= 100  # 至少保留100条数据
        
        if is_valid:
            self.validation_stats['valid_count'] += 1
        else:
            self.validation_stats['invalid_count'] += 1
        
        if issues:
            logger.warning(f"[{symbol}] 数据验证发现{len(issues)}类问题，"
                          f"移除{removed_count}条，保留{len(cleaned_df)}条")
        
        return ValidationResult(
            is_valid=is_valid,
            issues=issues,
            cleaned_data=cleaned_df if is_valid else None,
            removed_count=removed_count
        )
    
    def _detect_zero_negative_prices(self, df: pd.DataFrame) -> pd.Series:
        """检测零价格和负价格"""
        price_cols = ['open', 'high', 'low', 'close']
        mask = pd.Series(False, index=df.index)
        
        for col in price_cols:
            if col in df.columns:
                mask |= (df[col] <= 0) | df[col].isna()
        
        return mask
    
    def _detect_bad_ticks(self, df: pd.DataFrame) -> pd.Series:
        """
        检测错价 (Bad Tick)
        
        使用3σ原则:
        1. 计算收益率
        2. 标记Z-Score > 3σ的数据
        """
        if len(df) < 30:
            return pd.Series(False, index=df.index)
        
        # 计算对数收益率
        returns = np.log(df['close'] / df['close'].shift(1))
        
        # 计算滚动均值和标准差
        rolling_mean = returns.rolling(window=30, min_periods=10).mean()
        rolling_std = returns.rolling(window=30, min_periods=10).std()
        
        # Z-Score
        z_scores = (returns - rolling_mean) / rolling_std
        
        # 标记异常 (|Z| > 3)
        mask = z_scores.abs() > self.zscore_threshold
        
        return mask.fillna(False)
    
    def _detect_price_spikes(self, df: pd.DataFrame) -> pd.Series:
        """
        检测价格突变
        
        检查:
        1. 单根K线涨跌幅超过阈值
        2. 跳空超过阈值
        """
        if len(df) < 2:
            return pd.Series(False, index=df.index)
        
        # 计算K线涨跌幅
        body_change = (df['close'] - df['open']) / df['open']
        
        # 计算跳空 (当前open vs 上一根close)
        gap_change = (df['open'] - df['close'].shift(1)) / df['close'].shift(1)
        
        # 标记异常
        mask = (body_change.abs() > self.price_spike_threshold) | \
               (gap_change.abs() > self.price_spike_threshold)
        
        return mask.fillna(False)
    
    def _detect_volume_anomaly(self, df: pd.DataFrame) -> pd.Series:
        """
        检测成交量异常
        
        检查成交量是否突然放大/缩小
        """
        if len(df) < 30 or 'volume' not in df.columns:
            return pd.Series(False, index=df.index)
        
        # 成交量MA30
        volume_ma = df['volume'].rolling(window=30, min_periods=10).mean()
        
        # 成交量比值
        volume_ratio = df['volume'] / volume_ma
        
        # 标记异常 (>5倍或<0.2倍)
        mask = (volume_ratio > self.volume_spike_threshold) | \
               (volume_ratio < 0.2)
        
        return mask.fillna(False)
    
    def _check_timestamp_continuity(self, df: pd.DataFrame) -> List[Dict]:
        """
        检查时间戳连续性
        
        检查:
        1. 时间戳是否排序
        2. 时间间隔是否一致
        3. 是否存在大的时间断层
        """
        issues = []
        
        if 'timestamp' not in df.columns and 'ts' not in df.columns:
            return issues
        
        ts_col = 'timestamp' if 'timestamp' in df.columns else 'ts'
        timestamps = pd.to_datetime(df[ts_col])
        
        # 检查是否排序
        if not timestamps.is_monotonic_increasing:
            issues.append({
                'type': DataQualityIssue.TIMESTAMP_GAP.value,
                'count': 1,
                'message': '时间戳未按升序排列'
            })
        
        # 计算时间间隔
        time_diffs = timestamps.diff().dropna()
        
        if len(time_diffs) > 0:
            # 获取众数间隔
            mode_diff = time_diffs.mode()
            if len(mode_diff) > 0:
                expected_diff = mode_diff[0]
                
                # 查找大的时间断层 (>2倍预期间隔)
                gaps = time_diffs > expected_diff * 2
                gap_count = gaps.sum()
                
                if gap_count > 0:
                    issues.append({
                        'type': DataQualityIssue.MISSING_BAR.value,
                        'count': int(gap_count),
                        'message': f'发现{gap_count}个时间断层'
                    })
                    self.statistics_add_issue(DataQualityIssue.MISSING_BAR, gap_count)
        
        return issues
    
    def statistics_add_issue(self, issue_type: DataQualityIssue, count: int):
        """添加统计信息"""
        self.validation_stats['issues_by_type'][issue_type.value] += count
    
    def get_statistics(self) -> Dict:
        """获取验证统计"""
        return self.validation_stats.copy()
    
    def reset_statistics(self):
        """重置统计"""
        self.validation_stats = {
            'total_validations': 0,
            'valid_count': 0,
            'invalid_count': 0,
            'issues_by_type': {issue.value: 0 for issue in DataQualityIssue}
        }


class DataQualityMonitor:
    """
    数据质量监控器
    
    实时监控数据质量，发现问题立即告警
    """
    
    def __init__(self, validator: DataValidator = None):
        self.validator = validator or DataValidator()
        self.alert_threshold = {
            'bad_tick_ratio': 0.05,      # 5%错价率告警
            'missing_ratio': 0.1,        # 10%缺失率告警
            'consecutive_errors': 3      # 连续3次错误告警
        }
        self.consecutive_errors = 0
        
    def monitor_realtime(self, df: pd.DataFrame, symbol: str) -> bool:
        """
        实时监控数据质量
        
        Returns:
            True if data quality is acceptable
        """
        result = self.validator.validate_klines(df, symbol)
        
        if not result.is_valid:
            self.consecutive_errors += 1
            
            # 检查是否需要告警
            if self.consecutive_errors >= self.alert_threshold['consecutive_errors']:
                self._send_alert(symbol, result)
            
            return False
        
        # 检查Bad Tick比例
        if result.issues:
            bad_tick_count = sum(i['count'] for i in result.issues 
                               if i['type'] == DataQualityIssue.BAD_TICK.value)
            bad_tick_ratio = bad_tick_count / len(df)
            
            if bad_tick_ratio > self.alert_threshold['bad_tick_ratio']:
                logger.warning(f"[{symbol}] Bad Tick比例过高: {bad_tick_ratio:.2%}")
        
        # 重置连续错误计数
        self.consecutive_errors = 0
        return True
    
    def _send_alert(self, symbol: str, result: ValidationResult):
        """发送告警"""
        from src.notifications.telegram_notifier import TelegramNotifier
        
        notifier = TelegramNotifier()
        message = f"🚨 数据质量告警 [{symbol}]\n\n"
        message += f"状态: 连续{self.consecutive_errors}次验证失败\n"
        message += f"移除数据: {result.removed_count}条\n"
        message += f"问题类型:\n"
        
        for issue in result.issues:
            message += f"  - {issue['type']}: {issue['count']}\n"
        
        notifier.send_message(message)


# 便捷函数
def validate_klines(df: pd.DataFrame, symbol: str = "unknown") -> ValidationResult:
    """便捷验证函数"""
    validator = DataValidator()
    return validator.validate_klines(df, symbol)


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    # 创建测试数据
    np.random.seed(42)
    n = 200
    
    # 正常价格
    prices = 100 + np.cumsum(np.random.randn(n) * 0.5)
    
    # 插入错价
    prices[50] *= 1.2  # 20%跳涨 (Bad Tick)
    prices[100] = 0    # 零价格
    prices[150] *= 0.5  # 50%跳跌
    
    df = pd.DataFrame({
        'timestamp': pd.date_range('2024-01-01', periods=n, freq='1min'),
        'open': prices * 0.999,
        'high': prices * 1.001,
        'low': prices * 0.998,
        'close': prices,
        'volume': np.random.rand(n) * 1000
    })
    
    # 验证
    validator = DataValidator()
    result = validator.validate_klines(df, "TEST/USDT")
    
    print("\n" + "="*60)
    print("数据验证结果")
    print("="*60)
    print(f"原始数据: {len(df)}条")
    print(f"是否有效: {result.is_valid}")
    print(f"移除数据: {result.removed_count}条")
    print(f"保留数据: {len(result.cleaned_data) if result.cleaned_data is not None else 0}条")
    print(f"\n发现问题:")
    for issue in result.issues:
        print(f"  - {issue['type']}: {issue['message']}")
    print("="*60)
    
    # 统计
    stats = validator.get_statistics()
    print("\n统计信息:")
    print(f"总验证次数: {stats['total_validations']}")
    print(f"有效次数: {stats['valid_count']}")
    print(f"无效次数: {stats['invalid_count']}")
    print(f"问题分布:")
    for issue_type, count in stats['issues_by_type'].items():
        if count > 0:
            print(f"  - {issue_type}: {count}")
