"""
S001-Pro V3 引擎模块
职责: 数据读取 + 信号生成 + 持仓管理
"""

import numpy as np
import pandas as pd
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
from datetime import datetime
import logging
import sqlite3

from config import get_config
from database import get_db, PositionRecord
from visualization import (
    trace_step, trace_context, log_info, log_error, heartbeat
)

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    """交易信号"""
    pair_key: str
    zscore_5m: float
    zscore_15m: float
    zscore_30m: float
    action: str  # 'enter_long', 'enter_short', 'exit', 'hold'
    confidence: float = 0.0


class DataReader:
    """数据读取器 - 从Data-Core读取K线"""
    
    def __init__(self):
        self.cfg = get_config()
        self.cache = {}  # symbol -> {timeframe: df}
    
    def get_klines(self, symbol: str, timeframe: str, limit: int = 200) -> Optional[pd.DataFrame]:
        """获取K线数据 - 从Data-Core 1m数据重采样"""
        cache_key = f"{symbol}-{timeframe}"

        # 检查缓存
        if cache_key in self.cache:
            df = self.cache[cache_key]
            if len(df) >= limit:
                return df.tail(limit).copy()

        # Data-Core只存储1m数据，需要重采样
        try:
            conn = sqlite3.connect(self.cfg.database.klines_db)

            # 计算需要的1m数据量 (重采样需要更多原始数据)
            # 5m需要5x, 15m需要15x, 30m需要30x，再加一些余量
            multiplier = {'5m': 5, '15m': 15, '30m': 30, '1h': 60, '4h': 240}.get(timeframe, 1)
            required_1m = limit * multiplier * 2  # 2倍余量确保数据充足

            query = """
                SELECT ts, open, high, low, close, volume
                FROM klines
                WHERE symbol = ? AND interval = '1m'
                ORDER BY ts DESC
                LIMIT ?
            """
            df = pd.read_sql_query(query, conn, params=(symbol, required_1m))
            conn.close()

            if df.empty:
                logger.warning(f"No 1m data for {symbol}")
                return None

            # 转换为DataFrame并重采样
            df = df.rename(columns={'ts': 'timestamp'})
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            df = df.sort_index()

            # 如果需要重采样
            if timeframe != '1m':
                df = self._resample_ohlcv(df, timeframe)

            df = df.reset_index()
            df = df.tail(limit)  # 只返回需要的数量

            self.cache[cache_key] = df
            return df

        except Exception as e:
            logger.error(f"Failed to read klines for {symbol}: {e}")
            return None

    def _resample_ohlcv(self, df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        """将1m数据重采样为目标周期"""
        # 映射timeframe到pandas resample规则
        rule = {'5m': '5T', '15m': '15T', '30m': '30T', '1h': '1H', '4h': '4H'}.get(timeframe, '1T')

        resampled = df.resample(rule).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()

        return resampled
    
    def get_latest_price(self, symbol: str) -> Optional[float]:
        """获取最新价格"""
        df = self.get_klines(symbol, '5m', 1)
        if df is not None and not df.empty:
            return float(df['close'].iloc[-1])
        return None
    
    def clear_cache(self):
        """清除缓存"""
        self.cache.clear()


class SignalGenerator:
    """信号生成器"""
    
    def __init__(self):
        self.cfg = get_config()
    
    def calc_zscore(self, prices_a: np.ndarray, prices_b: np.ndarray, 
                    lookback: int = 120) -> Tuple[float, float]:
        """
        计算Z-Score
        返回: (zscore, beta)
        """
        if len(prices_a) < lookback or len(prices_b) < lookback:
            return 0.0, 1.0
        
        # 对数价格
        log_a = np.log(prices_a)
        log_b = np.log(prices_b)
        
        # OLS回归求beta
        beta = np.cov(log_a, log_b)[0, 1] / np.var(log_b)
        
        # 历史价差
        spread = log_a - beta * log_b
        
        # 当前价差
        current_spread = spread[-1]
        
        # Z-Score
        mean = np.mean(spread[-lookback:])
        std = np.std(spread[-lookback:])
        
        if std < 1e-10:
            return 0.0, beta
        
        zscore = (current_spread - mean) / std
        return float(zscore), float(beta)
    
    def generate_signal(self, pair_key: str, data_5m: pd.DataFrame, 
                        data_15m: pd.DataFrame, data_30m: pd.DataFrame,
                        params: Dict) -> Signal:
        """
        生成交易信号
        策略: 15m主信号 + 5m确认 + 30m过滤
        """
        # 计算三周期Z-Score
        z_5m, _ = self.calc_zscore(data_5m['close'].values, 
                                   self._get_pair_data(data_5m, pair_key), 120)
        z_15m, _ = self.calc_zscore(data_15m['close'].values,
                                    self._get_pair_data(data_15m, pair_key), 120)
        z_30m, _ = self.calc_zscore(data_30m['close'].values,
                                    self._get_pair_data(data_30m, pair_key), 60)
        
        # 30m过滤器
        filter_cfg = self.cfg.trading.filter
        if abs(z_30m) >= filter_cfg['threshold']:
            return Signal(pair_key, z_5m, z_15m, z_30m, 'hold', 0.0)
        
        # 15m主信号检查
        z_entry = params['z_entry']
        z_exit = params['z_exit']
        
        action = 'hold'
        confidence = 0.0
        
        if abs(z_15m) > z_entry:
            # 潜在进场信号，检查5m确认
            if z_15m * z_5m > 0:  # 同向
                action = 'enter_long' if z_15m < 0 else 'enter_short'
                confidence = min(abs(z_15m) / z_entry, 2.0) * 0.5
        elif abs(z_15m) < z_exit:
            action = 'exit'
            confidence = 1.0 - abs(z_15m) / z_exit
        
        return Signal(pair_key, z_5m, z_15m, z_30m, action, confidence)
    
    def _get_pair_data(self, df: pd.DataFrame, pair_key: str) -> np.ndarray:
        """获取配对的另一个品种数据 (简化)"""
        # 实际应该从Data-Core获取配对数据
        # 这里简化处理
        return df['close'].values * 0.5  # 模拟
    
    def should_exit(self, current_z: float, entry_z: float, 
                    params: Dict) -> Tuple[bool, str]:
        """
        检查是否应该出场
        返回: (should_exit, reason)
        """
        z_exit = params['z_exit']
        z_stop = params['z_stop']
        
        # 止盈
        if abs(current_z) < z_exit:
            return True, 'take_profit'
        
        # 止损
        if abs(current_z) > z_stop:
            return True, 'stop_loss'
        
        # 方向反转检查
        if entry_z * current_z < 0 and abs(current_z) < abs(entry_z) * 0.5:
            return True, 'reversal'
        
        return False, ''


class PositionManager:
    """持仓管理器"""
    
    def __init__(self, trader_execute_callback=None, trader_exit_callback=None):
        self.cfg = get_config()
        self.db = get_db()
        self.positions = {}  # pair_key -> PositionRecord
        self.trader_execute = trader_execute_callback  # 真实开仓回调
        self.trader_exit = trader_exit_callback  # 真实平仓回调
    
    def load_positions(self):
        """从数据库加载持仓"""
        records = self.db.get_open_positions()
        self.positions = {r.pair_key: r for r in records}
        logger.info(f"Loaded {len(self.positions)} open positions")
    
    def can_open(self, pool: str) -> bool:
        """检查是否可以开新仓"""
        # 检查全局持仓限制
        total_positions = len(self.positions)
        if total_positions >= self.cfg.trading.max_positions:
            return False
        
        # 检查池持仓限制
        pool_positions = sum(1 for p in self.positions.values() if p.pool == pool)
        pool_limit = self.cfg.trading.primary.top_n if pool == 'primary' else self.cfg.trading.secondary.top_n
        
        # 池内持仓不超过总配对数的一半
        if pool_positions >= pool_limit // 2:
            return False
        
        return True
    
    def has_position(self, pair_key: str) -> bool:
        """检查是否已有持仓"""
        return pair_key in self.positions
    
    def get_position(self, pair_key: str) -> Optional[PositionRecord]:
        """获取持仓信息"""
        return self.positions.get(pair_key)
    
    def open_position(self, pair_key: str, pool: str, symbol_a: str, symbol_b: str,
                      direction: str, entry_z: float, price_a: float, price_b: float,
                      qty_a: float, qty_b: float, notional: float, params: Dict) -> bool:
        """开仓 - 先执行真实下单，成功后更新数据库
        
        Returns:
            bool: 是否成功开仓
        """
        # 创建PositionRecord
        pos = PositionRecord(
            pair_key=pair_key,
            pool=pool,
            symbol_a=symbol_a,
            symbol_b=symbol_b,
            direction=direction,
            entry_z=entry_z,
            entry_price_a=price_a,
            entry_price_b=price_b,
            entry_time=datetime.now().isoformat(),
            qty_a=qty_a,
            qty_b=qty_b,
            notional=notional,
            current_z=entry_z,
            unrealized_pnl=0.0,
            z_entry=params['z_entry'],
            z_exit=params['z_exit'],
            z_stop=params['z_stop'],
            status='pending'  # 先标记为pending
        )
        
        # 第1步: 执行真实下单 (如果有trader回调)
        if self.trader_execute:
            logger.info(f"Executing real order for {pair_key}...")
            success = self.trader_execute(pos)
            if not success:
                logger.error(f"❌ Real order failed for {pair_key}, aborting")
                return False
            logger.info(f"✅ Real order executed for {pair_key}")
        else:
            logger.warning(f"⚠️ No trader_execute callback, skipping real order for {pair_key}")
        
        # 第2步: 真实下单成功后，更新数据库
        pos.status = 'open'
        self.db.open_position(pos)
        self.positions[pair_key] = pos
        
        logger.info(f"✅ Position opened: {pair_key} {direction} at Z={entry_z:.2f}")
        return True
    
    def update_position(self, pair_key: str, current_z: float, 
                        price_a: float, price_b: float) -> float:
        """更新持仓状态，返回未实现盈亏"""
        pos = self.positions.get(pair_key)
        if not pos:
            return 0.0
        
        # 计算未实现盈亏
        if pos.direction == 'long_spread':
            # 做多价差: 买A卖B
            pnl_a = (price_a - pos.entry_price_a) * pos.qty_a
            pnl_b = (pos.entry_price_b - price_b) * pos.qty_b
        else:
            # 做空价差: 卖A买B
            pnl_a = (pos.entry_price_a - price_a) * pos.qty_a
            pnl_b = (price_b - pos.entry_price_b) * pos.qty_b
        
        unrealized_pnl = pnl_a + pnl_b
        
        # 更新数据库
        self.db.update_position(pair_key, current_z, unrealized_pnl)
        
        # 更新内存
        pos.current_z = current_z
        pos.unrealized_pnl = unrealized_pnl
        
        return unrealized_pnl
    
    def close_position(self, pair_key: str, exit_price_a: float, 
                       exit_price_b: float, exit_z: float, reason: str) -> float:
        """平仓 - 先执行真实平仓，成功后更新数据库
        
        Returns:
            float: 实现盈亏
        """
        pos = self.positions.get(pair_key)
        if not pos:
            return 0.0
        
        # 第1步: 执行真实平仓 (如果有trader回调)
        if self.trader_exit:
            logger.info(f"Executing real exit for {pair_key}...")
            success = self.trader_exit(pos)
            if not success:
                logger.error(f"❌ Real exit failed for {pair_key}")
                return 0.0
            logger.info(f"✅ Real exit executed for {pair_key}")
        else:
            logger.warning(f"⚠️ No trader_exit callback, skipping real exit for {pair_key}")
        
        # 第2步: 计算实现盈亏
        if pos.direction == 'long_spread':
            pnl_a = (exit_price_a - pos.entry_price_a) * pos.qty_a
            pnl_b = (pos.entry_price_b - exit_price_b) * pos.qty_b
        else:
            pnl_a = (pos.entry_price_a - exit_price_a) * pos.qty_a
            pnl_b = (exit_price_b - pos.entry_price_b) * pos.qty_b
        
        realized_pnl = pnl_a + pnl_b
        
        # 第3步: 创建交易记录
        from database import TradeRecord
        trade = TradeRecord(
            pair_key=pair_key,
            pool=pos.pool,
            symbol_a=pos.symbol_a,
            symbol_b=pos.symbol_b,
            direction=pos.direction,
            entry_time=pos.entry_time,
            entry_price_a=pos.entry_price_a,
            entry_price_b=pos.entry_price_b,
            entry_z=pos.entry_z,
            exit_time=datetime.now().isoformat(),
            exit_price_a=exit_price_a,
            exit_price_b=exit_price_b,
            exit_z=exit_z,
            exit_reason=reason,
            qty_a=pos.qty_a,
            qty_b=pos.qty_b,
            pnl=realized_pnl,
            pnl_pct=realized_pnl / pos.notional * 100 if pos.notional > 0 else 0
        )
        
        # 第4步: 更新数据库
        self.db.close_position(pair_key, trade)
        
        # 第5步: 移除内存持仓
        del self.positions[pair_key]
        
        logger.info(f"✅ Position closed: {pair_key} PnL={realized_pnl:.2f} reason={reason}")
        return realized_pnl
    
    def get_all_positions(self) -> List[PositionRecord]:
        """获取所有持仓"""
        return list(self.positions.values())
    
    def get_position_count(self, pool: Optional[str] = None) -> int:
        """获取持仓数量"""
        if pool:
            return sum(1 for p in self.positions.values() if p.pool == pool)
        return len(self.positions)


class Engine:
    """引擎 - 整合数据、信号、持仓"""
    
    def __init__(self, trader=None):
        self.data_reader = DataReader()
        self.signal_gen = SignalGenerator()
        # 如果提供了trader，设置下单回调
        entry_callback = trader.execute_entry if trader else None
        exit_callback = trader.execute_exit if trader else None
        self.position_mgr = PositionManager(
            trader_execute_callback=entry_callback,
            trader_exit_callback=exit_callback
        )
        self.cfg = get_config()
        self.trader = trader
    
    def initialize(self):
        """初始化"""
        self.position_mgr.load_positions()
        logger.info("Engine initialized")
    
    @trace_step("Engine", "处理Tick")
    def process_tick(self, pool: str = "primary"):
        """处理一个tick"""
        heartbeat("Engine")
        
        # 获取活跃配对
        from database import get_db
        db = get_db()
        pairs = db.get_active_pairs(pool)
        
        log_info("Engine", "开始处理Tick", pool=pool, pair_count=len(pairs))
        
        processed = 0
        for pair in pairs:
            pair_key = f"{pair.symbol_a}-{pair.symbol_b}"
            
            with trace_context("Engine", f"处理配对", pair_key=pair_key):
                # 读取数据
                tf = self.cfg.trading.primary.timeframe if pool == 'primary' else self.cfg.trading.secondary.timeframe
                data = self.data_reader.get_klines(pair.symbol_a, tf, 200)
                
                if data is None or len(data) < 120:
                    log_info("Engine", "数据不足跳过", 
                            pair_key=pair_key, 
                            data_len=len(data) if data is not None else 0)
                    continue
                
                # 检查持仓
                if self.position_mgr.has_position(pair_key):
                    self._manage_position(pair_key, pair, data)
                else:
                    self._check_entry(pair_key, pair, data, pool)
                
                processed += 1
        
        log_info("Engine", "Tick处理完成", pool=pool, processed=processed)
    
    def _manage_position(self, pair_key: str, pair: 'PairRecord', data: pd.DataFrame):
        """管理现有持仓"""
        pos = self.position_mgr.get_position(pair_key)
        if not pos:
            return
        
        # 计算当前Z-Score
        prices_a = data['close'].values
        prices_b = prices_a * 0.5  # 简化
        current_z, _ = self.signal_gen.calc_zscore(prices_a, prices_b)
        
        # 获取当前价格
        current_price_a = prices_a[-1]
        current_price_b = prices_b[-1]
        
        # 更新持仓
        unrealized_pnl = self.position_mgr.update_position(
            pair_key, current_z, current_price_a, current_price_b
        )
        
        # 检查出场
        params = {'z_exit': pos.z_exit, 'z_stop': pos.z_stop}
        should_exit, reason = self.signal_gen.should_exit(current_z, pos.entry_z, params)
        
        if should_exit:
            self.position_mgr.close_position(
                pair_key, current_price_a, current_price_b, current_z, reason
            )
    
    def _check_entry(self, pair_key: str, pair: 'PairRecord', 
                     data: pd.DataFrame, pool: str):
        """检查进场条件"""
        # 检查是否可以开新仓
        if not self.position_mgr.can_open(pool):
            return
        
        # 计算信号
        prices_a = data['close'].values
        prices_b = prices_a * 0.5  # 简化
        
        # 简化信号生成
        z_score, beta = self.signal_gen.calc_zscore(prices_a, prices_b)
        
        params = {
            'z_entry': pair.z_entry,
            'z_exit': pair.z_exit,
            'z_stop': pair.z_stop
        }
        
        # 检查进场条件
        if abs(z_score) > params['z_entry']:
            # 确定方向
            direction = 'long_spread' if z_score < 0 else 'short_spread'
            
            # 计算仓位
            price_a = prices_a[-1]
            price_b = prices_b[-1]
            
            # 简化仓位计算
            notional = self.cfg.trading.min_per_pair
            qty_a = notional / 2 / price_a
            qty_b = notional / 2 / price_b
            
            # 开仓
            self.position_mgr.open_position(
                pair_key=pair_key,
                pool=pool,
                symbol_a=pair.symbol_a,
                symbol_b=pair.symbol_b,
                direction=direction,
                entry_z=z_score,
                price_a=price_a,
                price_b=price_b,
                qty_a=qty_a,
                qty_b=qty_b,
                notional=notional,
                params=params
            )


if __name__ == "__main__":
    engine = Engine()
    engine.initialize()
    print("Engine test passed")
