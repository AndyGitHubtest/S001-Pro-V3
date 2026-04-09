"""
数据质量监控器
实时监控数据质量，检测异常并告警
"""
import time
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import threading
import logging

logger = logging.getLogger(__name__)


class DataQualityAlert(Enum):
    """数据质量告警类型"""
    MISSING_DATA = "missing_data"      # 数据缺失
    DELAYED_DATA = "delayed_data"      # 数据延迟
    STALE_DATA = "stale_data"          # 数据过期
    PRICE_ANOMALY = "price_anomaly"    # 价格异常
    VOLUME_ANOMALY = "volume_anomaly"  # 成交量异常
    GAP_DETECTED = "gap_detected"      # 价格断层
    DATA_SOURCE_ERROR = "source_error" # 数据源错误


@dataclass
class QualityMetrics:
    """数据质量指标"""
    symbol: str
    timestamp: datetime
    last_update_ms: float        # 上次更新时间(ms前)
    data_freshness_ms: float     # 数据新鲜度
    missing_bars_1h: int         # 1小时内缺失K线数
    missing_bars_24h: int        # 24小时内缺失K线数
    price_change_1m: float       # 1分钟价格变化
    price_change_5m: float       # 5分钟价格变化
    volume_vs_avg: float         # 成交量vs平均值
    is_anomaly: bool             # 是否异常


class DataQualityMonitor:
    """
    数据质量监控器
    
    实时监控:
    1. 数据新鲜度 (< 2分钟)
    2. 缺失K线检测
    3. 价格异常波动
    4. 成交量异常
    5. 数据源可用性
    """
    
    def __init__(self, 
                 max_data_delay_ms: float = 120000,  # 2分钟
                 max_missing_bars_1h: int = 2,
                 max_missing_bars_24h: int = 10,
                 price_anomaly_threshold: float = 0.05,  # 5%
                 volume_anomaly_threshold: float = 5.0):  # 5倍
        """
        Args:
            max_data_delay_ms: 最大数据延迟(毫秒)
            max_missing_bars_1h: 1小时最大缺失K线数
            max_missing_bars_24h: 24小时最大缺失K线数
            price_anomaly_threshold: 价格异常阈值
            volume_anomaly_threshold: 成交量异常阈值
        """
        self.max_data_delay_ms = max_data_delay_ms
        self.max_missing_bars_1h = max_missing_bars_1h
        self.max_missing_bars_24h = max_missing_bars_24h
        self.price_anomaly_threshold = price_anomaly_threshold
        self.volume_anomaly_threshold = volume_anomaly_threshold
        
        # 数据缓存
        self.data_cache: Dict[str, pd.DataFrame] = {}
        self.last_update: Dict[str, float] = {}
        
        # 告警回调
        self.alert_callbacks: List[Callable] = []
        
        # 运行状态
        self.running = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.monitor_interval = 60  # 每分钟检查一次
        
        # 统计
        self.alert_count = 0
        self.check_count = 0
    
    def update_data(self, symbol: str, df: pd.DataFrame):
        """
        更新数据
        
        Args:
            symbol: 币种
            df: K线数据
        """
        self.data_cache[symbol] = df.copy()
        self.last_update[symbol] = time.time()
    
    def check_symbol(self, symbol: str) -> List[Dict]:
        """
        检查单个币种数据质量
        
        Returns:
            告警列表
        """
        alerts = []
        
        if symbol not in self.data_cache:
            alerts.append({
                'type': DataQualityAlert.DATA_SOURCE_ERROR.value,
                'symbol': symbol,
                'message': '无数据缓存',
                'severity': 'error'
            })
            return alerts
        
        df = self.data_cache[symbol]
        
        if df.empty:
            alerts.append({
                'type': DataQualityAlert.DATA_SOURCE_ERROR.value,
                'symbol': symbol,
                'message': '数据为空',
                'severity': 'error'
            })
            return alerts
        
        now = time.time()
        
        # 1. 检查数据延迟
        last_update = self.last_update.get(symbol, 0)
        delay_ms = (now - last_update) * 1000
        
        if delay_ms > self.max_data_delay_ms:
            alerts.append({
                'type': DataQualityAlert.DELAYED_DATA.value,
                'symbol': symbol,
                'message': f'数据延迟{delay_ms/1000:.1f}秒',
                'severity': 'warning',
                'value': delay_ms
            })
        
        # 2. 检查数据新鲜度
        if 'timestamp' in df.columns:
            last_bar_time = pd.to_datetime(df['timestamp'].iloc[-1])
            freshness_ms = (datetime.now() - last_bar_time).total_seconds() * 1000
            
            if freshness_ms > self.max_data_delay_ms:
                alerts.append({
                    'type': DataQualityAlert.STALE_DATA.value,
                    'symbol': symbol,
                    'message': f'数据过期{freshness_ms/1000:.1f}秒',
                    'severity': 'warning',
                    'value': freshness_ms
                })
        
        # 3. 检查缺失K线
        if len(df) >= 2 and 'timestamp' in df.columns:
            timestamps = pd.to_datetime(df['timestamp'])
            intervals = timestamps.diff().dropna()
            
            if len(intervals) > 0:
                # 假设1分钟K线
                expected_interval = timedelta(minutes=1)
                
                # 1小时内缺失
                recent_df = df[df['timestamp'] >= datetime.now() - timedelta(hours=1)]
                missing_1h = self._count_missing_bars(recent_df, expected_interval)
                
                if missing_1h > self.max_missing_bars_1h:
                    alerts.append({
                        'type': DataQualityAlert.MISSING_DATA.value,
                        'symbol': symbol,
                        'message': f'1小时内缺失{missing_1h}根K线',
                        'severity': 'warning',
                        'value': missing_1h
                    })
        
        # 4. 检查价格异常
        if len(df) >= 2 and 'close' in df.columns:
            last_price = df['close'].iloc[-1]
            prev_price = df['close'].iloc[-2]
            
            if prev_price > 0:
                price_change = abs(last_price - prev_price) / prev_price
                
                if price_change > self.price_anomaly_threshold:
                    alerts.append({
                        'type': DataQualityAlert.PRICE_ANOMALY.value,
                        'symbol': symbol,
                        'message': f'价格异常波动{price_change:.2%}',
                        'severity': 'warning',
                        'value': price_change
                    })
        
        # 5. 检查成交量异常
        if 'volume' in df.columns and len(df) >= 20:
            recent_volume = df['volume'].iloc[-1]
            avg_volume = df['volume'].iloc[-20:].mean()
            
            if avg_volume > 0:
                volume_ratio = recent_volume / avg_volume
                
                if volume_ratio > self.volume_anomaly_threshold or volume_ratio < 0.2:
                    alerts.append({
                        'type': DataQualityAlert.VOLUME_ANOMALY.value,
                        'symbol': symbol,
                        'message': f'成交量异常(比值{volume_ratio:.2f})',
                        'severity': 'info',
                        'value': volume_ratio
                    })
        
        return alerts
    
    def _count_missing_bars(self, df: pd.DataFrame, 
                           expected_interval: timedelta) -> int:
        """统计缺失K线数"""
        if len(df) < 2:
            return 0
        
        timestamps = pd.to_datetime(df['timestamp'])
        intervals = timestamps.diff().dropna()
        
        # 计算预期K线数
        time_range = timestamps.iloc[-1] - timestamps.iloc[0]
        expected_bars = int(time_range / expected_interval)
        actual_bars = len(df)
        
        return max(0, expected_bars - actual_bars)
    
    def check_all(self) -> Dict[str, List[Dict]]:
        """
        检查所有币种
        
        Returns:
            {symbol: [alerts], ...}
        """
        self.check_count += 1
        all_alerts = {}
        
        for symbol in self.data_cache.keys():
            alerts = self.check_symbol(symbol)
            if alerts:
                all_alerts[symbol] = alerts
                
                # 触发告警
                for alert in alerts:
                    self._trigger_alert(alert)
        
        return all_alerts
    
    def _trigger_alert(self, alert: Dict):
        """触发告警"""
        self.alert_count += 1
        
        logger.warning(f"[DataQuality] {alert['symbol']}: {alert['message']}")
        
        for callback in self.alert_callbacks:
            try:
                callback(alert)
            except Exception as e:
                logger.error(f"告警回调失败: {e}")
    
    def register_alert_callback(self, callback: Callable):
        """注册告警回调"""
        self.alert_callbacks.append(callback)
    
    def start_monitoring(self):
        """启动监控线程"""
        if self.running:
            return
        
        self.running = True
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop, 
            daemon=True
        )
        self.monitor_thread.start()
        logger.info(f"数据质量监控启动，间隔{self.monitor_interval}秒")
    
    def stop_monitoring(self):
        """停止监控线程"""
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        logger.info("数据质量监控停止")
    
    def _monitor_loop(self):
        """监控循环"""
        while self.running:
            try:
                self.check_all()
            except Exception as e:
                logger.error(f"监控循环错误: {e}")
            
            time.sleep(self.monitor_interval)
    
    def get_quality_report(self) -> Dict:
        """生成质量报告"""
        report = {
            'timestamp': datetime.now().isoformat(),
            'symbols_monitored': len(self.data_cache),
            'check_count': self.check_count,
            'alert_count': self.alert_count,
            'alert_rate': self.alert_count / max(self.check_count, 1),
            'symbol_details': {}
        }
        
        for symbol, df in self.data_cache.items():
            last_update = self.last_update.get(symbol, 0)
            delay_ms = (time.time() - last_update) * 1000
            
            report['symbol_details'][symbol] = {
                'data_points': len(df),
                'last_update_ms': delay_ms,
                'alerts': self.check_symbol(symbol)
            }
        
        return report
    
    def get_statistics(self) -> Dict:
        """获取统计信息"""
        return {
            'check_count': self.check_count,
            'alert_count': self.alert_count,
            'symbols_monitored': len(self.data_cache),
            'monitor_interval': self.monitor_interval
        }


class DataFreshnessChecker:
    """
    数据新鲜度检查器
    
    专门检查数据是否及时更新
    """
    
    def __init__(self, max_delay_seconds: float = 120):
        self.max_delay = max_delay_seconds
        self.timestamps: Dict[str, float] = {}
    
    def update(self, symbol: str, timestamp: float = None):
        """更新时间戳"""
        self.timestamps[symbol] = timestamp or time.time()
    
    def check_freshness(self, symbol: str) -> Dict:
        """检查新鲜度"""
        if symbol not in self.timestamps:
            return {
                'fresh': False,
                'delay_seconds': float('inf'),
                'message': '无时间戳记录'
            }
        
        delay = time.time() - self.timestamps[symbol]
        
        return {
            'fresh': delay <= self.max_delay,
            'delay_seconds': delay,
            'message': f'延迟{delay:.1f}秒' if delay > self.max_delay else '正常'
        }
    
    def check_all(self) -> Dict[str, Dict]:
        """检查所有币种"""
        return {symbol: self.check_freshness(symbol) 
                for symbol in self.timestamps.keys()}


# 便捷函数
def create_quality_monitor(**kwargs) -> DataQualityMonitor:
    """创建数据质量监控器"""
    return DataQualityMonitor(**kwargs)


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    # 创建监控器
    monitor = DataQualityMonitor()
    
    # 注册告警回调
    def on_alert(alert):
        print(f"🚨 告警: [{alert['severity'].upper()}] {alert['message']}")
    
    monitor.register_alert_callback(on_alert)
    
    # 创建测试数据
    np.random.seed(42)
    n = 100
    
    # 正常数据
    normal_df = pd.DataFrame({
        'timestamp': pd.date_range(end=datetime.now(), periods=n, freq='1min'),
        'open': np.random.rand(n) * 100,
        'high': np.random.rand(n) * 100,
        'low': np.random.rand(n) * 100,
        'close': np.random.rand(n) * 100,
        'volume': np.random.rand(n) * 1000
    })
    
    # 延迟数据 (5分钟前)
    delayed_df = pd.DataFrame({
        'timestamp': pd.date_range(end=datetime.now() - timedelta(minutes=5), periods=n, freq='1min'),
        'open': np.random.rand(n) * 100,
        'high': np.random.rand(n) * 100,
        'low': np.random.rand(n) * 100,
        'close': np.random.rand(n) * 100,
        'volume': np.random.rand(n) * 1000
    })
    
    # 价格异常数据
    anomaly_df = normal_df.copy()
    anomaly_df.loc[n-1, 'close'] = anomaly_df.loc[n-2, 'close'] * 1.1  # 10%跳涨
    
    # 更新监控器
    monitor.update_data("BTC/USDT", normal_df)
    monitor.update_data("ETH/USDT", delayed_df)
    monitor.update_data("SOL/USDT", anomaly_df)
    
    # 手动检查
    print("="*60)
    print("数据质量检查")
    print("="*60)
    
    all_alerts = monitor.check_all()
    
    for symbol, alerts in all_alerts.items():
        print(f"\n{symbol}:")
        for alert in alerts:
            print(f"  [{alert['severity'].upper()}] {alert['message']}")
    
    # 报告
    print("\n" + "="*60)
    print("监控统计")
    print("="*60)
    stats = monitor.get_statistics()
    print(f"监控币种: {stats['symbols_monitored']}")
    print(f"检查次数: {stats['check_count']}")
    print(f"告警次数: {stats['alert_count']}")
