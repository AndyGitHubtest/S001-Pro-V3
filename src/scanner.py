"""
S001-Pro V3 扫描器模块
职责: 三层筛选 + 评分排名 + 参数优化
输出: Top N配对 + 最优参数 → 写入数据库
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime
import logging
from scipy import stats

from config import get_config, Layer1Config, Layer2Config, Layer3Config, ScoringConfig
from database import get_db, PairRecord

logger = logging.getLogger(__name__)


@dataclass
class PairMetrics:
    """配对指标集合"""
    symbol_a: str
    symbol_b: str
    
    # Layer 1
    corr_median: float = 0.0
    coint_p: float = 1.0
    adf_p: float = 1.0
    
    # Layer 2
    half_life: float = 999.0
    corr_std: float = 1.0
    hurst: float = 1.0
    
    # Layer 3
    zscore_max: float = 0.0
    spread_std: float = 0.0
    volume_min: int = 0
    bid_ask_max: float = 1.0
    
    # 评分
    score: float = 0.0
    
    # 优化参数
    z_entry: float = 2.5
    z_exit: float = 0.5
    z_stop: float = 4.5
    
    # 回测结果
    pf: float = 0.0
    sharpe: float = 0.0
    total_return: float = 0.0
    max_dd: float = 1.0
    trades_count: int = 0


class Scanner:
    """配对扫描器"""
    
    def __init__(self):
        self.cfg = get_config()
        self.db = get_db()
        self.layer1 = self.cfg.layer1
        self.layer2 = self.cfg.layer2
        self.layer3 = self.cfg.layer3
        self.scoring = self.cfg.scoring
        self.opt = self.cfg.optimization
    
    def scan(self, pool: str = "primary") -> List[PairRecord]:
        """
        主扫描流程
        1. 获取候选币种
        2. 生成配对
        3. 三层筛选
        4. 评分排名
        5. 参数优化
        6. 保存结果
        """
        logger.info(f"Starting scan for pool '{pool}'")
        start_time = datetime.now()
        
        # 1. 获取候选币种 (从Data-Core)
        symbols = self._fetch_symbols()
        logger.info(f"Fetched {len(symbols)} symbols from Data-Core")
        
        # 2. 生成配对
        candidates = self._generate_pairs(symbols)
        logger.info(f"Generated {len(candidates)} candidate pairs")
        
        # 3-5. 三层筛选 + 评分 + 优化
        results = self._process_pairs(candidates, pool)
        
        # 6. 保存到数据库
        pair_records = [self._to_pair_record(m, pool) for m in results]
        self.db.save_pairs(pool, pair_records)
        
        # 记录扫描历史
        duration = (datetime.now() - start_time).total_seconds() * 1000
        self.db.log_scan(
            pool=pool,
            candidates=len(candidates),
            l1=len(candidates),  # 简化，实际应该记录每层通过数
            l2=len(results) * 2,  # 估算
            l3=len(results),
            top_n=len(results),
            top_score=results[0].score if results else 0,
            avg_score=np.mean([r.score for r in results]) if results else 0,
            duration_ms=int(duration)
        )
        
        logger.info(f"Scan completed: {len(results)} pairs saved, duration: {duration:.0f}ms")
        return pair_records
    
    def _fetch_symbols(self) -> List[str]:
        """从共享数据库获取候选币种列表"""
        try:
            # 调试信息
            logger.info(f"DB klines_db_path: {self.db.klines_db_path}")
            
            conn = self.db._get_klines_connection()
            if conn is None:
                logger.warning("K线数据库未配置，使用默认币种")
                return ["BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT",
                        "ARB/USDT", "OP/USDT", "MATIC/USDT", "LINK/USDT"]
            
            logger.info(f"K线数据库已连接")
            cursor = conn.cursor()
            
            # 获取流动性最好的币种 (限制数量以控制扫描时间)
            # 按数据量排序，优先选择交易活跃的币种
            cursor.execute("""
                SELECT symbol, COUNT(*) as cnt FROM klines 
                WHERE ts > (SELECT MAX(ts) - 86400000 FROM klines)
                GROUP BY symbol
                ORDER BY cnt DESC
                LIMIT 20
            """)
            rows = cursor.fetchall()
            symbols = [row[0] for row in rows]
            
            logger.info(f"从共享数据库获取到 {len(symbols)} 个币种")
            return symbols
            
        except Exception as e:
            logger.error(f"获取币种列表失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # 失败时返回默认币种
            return ["BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT"]
    
    def _generate_pairs(self, symbols: List[str]) -> List[Tuple[str, str]]:
        """生成配对组合"""
        pairs = []
        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                pairs.append((symbols[i], symbols[j]))
        return pairs
    
    def _process_pairs(self, candidates: List[Tuple[str, str]], pool: str) -> List[PairMetrics]:
        """处理所有配对: 筛选 + 评分 + 优化"""
        results = []
        
        for sym_a, sym_b in candidates:
            try:
                metrics = self._analyze_pair(sym_a, sym_b, pool)
                if metrics:
                    results.append(metrics)
            except Exception as e:
                logger.warning(f"Failed to analyze {sym_a}-{sym_b}: {e}")
                continue
        
        # 按评分排序
        results.sort(key=lambda x: x.score, reverse=True)
        
        # 应用互斥限制
        results = self._apply_exclusion(results)
        
        # 取Top N
        pool_cfg = self.cfg.trading.primary if pool == "primary" else self.cfg.trading.secondary
        return results[:pool_cfg.top_n]
    
    def _analyze_pair(self, sym_a: str, sym_b: str, pool: str) -> Optional[PairMetrics]:
        """
        分析单个配对
        返回: PairMetrics (通过所有筛选) 或 None (未通过)
        """
        # 加载历史数据
        data = self._load_data(sym_a, sym_b, pool)
        if data is None or len(data) < 120:
            return None
        
        m = PairMetrics(symbol_a=sym_a, symbol_b=sym_b)
        
        # ========== Layer 1: Statistical Foundation ==========
        m.corr_median = self._calc_median_correlation(data)
        if m.corr_median < self.layer1.corr_median_min:
            return None
        
        m.coint_p = self._cointegration_test(data)
        if m.coint_p > self.layer1.coint_p_max:
            return None
        
        m.adf_p = self._adf_test(data)
        if m.adf_p > self.layer1.adf_p_max:
            return None
        
        # ========== Layer 2: Stability ==========
        m.half_life = self._calc_half_life(data)
        if m.half_life > self.layer2.half_life_max:
            return None
        
        m.corr_std = self._calc_rolling_correlation_std(data)
        if m.corr_std > self.layer2.corr_std_max:
            return None
        
        m.hurst = self._calc_hurst_exponent(data)
        if m.hurst > self.layer2.hurst_max:
            return None
        
        # ========== Layer 3: Tradeability ==========
        m.zscore_max = self._calc_max_zscore(data)
        if m.zscore_max < self.layer3.zscore_max_min:
            return None
        
        m.spread_std = self._calc_spread_std(data)
        if m.spread_std < self.layer3.spread_std_min:
            return None
        
        m.volume_min = self._get_min_volume(sym_a, sym_b)
        if m.volume_min < self.layer3.volume_min:
            return None
        
        m.bid_ask_max = self._get_max_bid_ask_spread(sym_a, sym_b)
        if m.bid_ask_max > self.layer3.bid_ask_max:
            return None
        
        # ========== Scoring ==========
        m.score = self._calc_score(m)
        
        # ========== Parameter Optimization ==========
        m = self._optimize_params(m, data, pool)
        
        # 检查回测结果
        if m.pf < self.cfg.output['min_pf'] or m.total_return <= 0:
            return None
        
        return m
    
    def _load_data(self, sym_a: str, sym_b: str, pool: str) -> Optional[pd.DataFrame]:
        """从共享数据库加载配对历史数据"""
        try:
            conn = self.db._get_klines_connection()
            if conn is None:
                logger.warning("K线数据库未配置，无法加载数据")
                return None
            
            # 获取timeframe
            timeframe = self.cfg.trading.primary.timeframe if pool == "primary" else self.cfg.trading.secondary.timeframe
            
            # 查询两个币种的数据 (Data-Core schema: ts, interval)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT ts, close FROM klines 
                WHERE symbol = ? AND interval = ?
                ORDER BY ts DESC
                LIMIT 1000
            """, (sym_a, timeframe))
            rows_a = cursor.fetchall()
            
            cursor.execute("""
                SELECT ts, close FROM klines 
                WHERE symbol = ? AND interval = ?
                ORDER BY ts DESC
                LIMIT 1000
            """, (sym_b, timeframe))
            rows_b = cursor.fetchall()
            
            if len(rows_a) < 120 or len(rows_b) < 120:
                logger.debug(f"{sym_a}-{sym_b}: 数据不足 ({len(rows_a)}/{len(rows_b)})")
                return None
            
            # 转换为DataFrame并对齐时间戳 (ts是毫秒时间戳)
            df_a = pd.DataFrame(rows_a, columns=['ts', 'a'])
            df_b = pd.DataFrame(rows_b, columns=['ts', 'b'])
            
            # 合并并取交集
            df = pd.merge(df_a, df_b, on='ts', how='inner')
            df = df.sort_values('ts')
            
            if len(df) < 120:
                logger.debug(f"{sym_a}-{sym_b}: 对齐后数据不足 ({len(df)})")
                return None
            
            return df[['a', 'b']].astype(float)
            
        except Exception as e:
            logger.error(f"加载数据失败 {sym_a}-{sym_b}: {e}")
            return None
    
    def _calc_median_correlation(self, data: pd.DataFrame, window: int = 120) -> float:
        """计算滚动相关系数的中位数"""
        log_a = np.log(data['a'])
        log_b = np.log(data['b'])
        
        corrs = []
        for i in range(window, len(data)):
            corr = np.corrcoef(log_a[i-window:i], log_b[i-window:i])[0, 1]
            corrs.append(corr)
        
        return float(np.median(corrs)) if corrs else 0.0
    
    def _cointegration_test(self, data: pd.DataFrame) -> float:
        """Engle-Granger协整检验"""
        log_a = np.log(data['a'])
        log_b = np.log(data['b'])
        
        # OLS回归
        slope, intercept, r_value, p_value, std_err = stats.linregress(log_b, log_a)
        
        # 对残差做ADF检验
        residual = log_a - (slope * log_b + intercept)
        adf_result = self._adf_statistic(residual)
        
        # 返回p-value (简化处理)
        return adf_result
    
    def _adf_test(self, data: pd.DataFrame) -> float:
        """ADF检验 - 残差平稳性"""
        log_a = np.log(data['a'])
        log_b = np.log(data['b'])
        
        slope, intercept, _, _, _ = stats.linregress(log_b, log_a)
        residual = log_a - (slope * log_b + intercept)
        
        return self._adf_statistic(residual)
    
    def _adf_statistic(self, series: np.ndarray) -> float:
        """计算ADF统计量 (简化版)"""
        # 使用一阶差分
        diff = np.diff(series)
        lag = series[:-1]
        
        # 回归: diff = alpha * lag + error
        slope, _, _, p_value, _ = stats.linregress(lag, diff)
        
        # 返回p-value
        return p_value
    
    def _calc_half_life(self, data: pd.DataFrame) -> float:
        """计算OU过程半衰期"""
        log_a = np.log(data['a'])
        log_b = np.log(data['b'])
        
        slope, intercept, _, _, _ = stats.linregress(log_b, log_a)
        spread = log_a - (slope * log_b + intercept)
        
        # OU过程: d(spread) = -theta * spread * dt + dW
        delta = np.diff(spread)
        lag = spread[:-1]
        
        # 回归: delta = -theta * lag
        theta, _, _, _, _ = stats.linregress(lag, delta)
        theta = -theta
        
        if theta <= 0:
            return 999.0
        
        hl = np.log(2) / theta
        return float(hl)
    
    def _calc_rolling_correlation_std(self, data: pd.DataFrame, window: int = 120) -> float:
        """计算滚动相关系数的标准差"""
        log_a = np.log(data['a'])
        log_b = np.log(data['b'])
        
        corrs = []
        for i in range(window, len(data)):
            corr = np.corrcoef(log_a[i-window:i], log_b[i-window:i])[0, 1]
            corrs.append(corr)
        
        return float(np.std(corrs)) if corrs else 1.0
    
    def _calc_hurst_exponent(self, data: pd.DataFrame) -> float:
        """计算赫斯特指数 (方差时间法)"""
        log_a = np.log(data['a'])
        log_b = np.log(data['b'])
        
        slope, intercept, _, _, _ = stats.linregress(log_b, log_a)
        spread = log_a - (slope * log_b + intercept)
        
        # 方差时间法
        lags = [4, 8, 16, 32, 64]
        vars = []
        
        for lag in lags:
            if lag >= len(spread):
                break
            # 计算lag步长的方差
            diff = spread[lag:] - spread[:-lag]
            vars.append(np.var(diff))
        
        if len(vars) < 3:
            return 0.5
        
        # 检查方差是否有效 (必须>0才能取log)
        vars = np.array(vars)
        if np.any(vars <= 0) or np.any(np.isnan(vars)):
            return 0.5  # 数据质量不足，返回中性值
        
        # 回归: log(var) = 2H * log(lag) + C
        log_lags = np.log(lags[:len(vars)])
        log_vars = np.log(vars)
        
        H, _, _, _, _ = stats.linregress(log_lags, log_vars)
        H = H / 2
        
        return float(np.clip(H, 0, 1))
    
    def _calc_max_zscore(self, data: pd.DataFrame) -> float:
        """计算历史最大Z-Score"""
        log_a = np.log(data['a'])
        log_b = np.log(data['b'])
        
        slope, intercept, _, _, _ = stats.linregress(log_b, log_a)
        spread = log_a - (slope * log_b + intercept)
        
        z_scores = (spread - np.mean(spread)) / np.std(spread)
        return float(np.max(np.abs(z_scores)))
    
    def _calc_spread_std(self, data: pd.DataFrame) -> float:
        """计算价差标准差"""
        log_a = np.log(data['a'])
        log_b = np.log(data['b'])
        
        spread = log_a - log_b
        return float(np.std(spread))
    
    def _get_min_volume(self, sym_a: str, sym_b: str) -> int:
        """获取最小日成交量"""
        # TODO: 从Data-Core获取
        return 10_000_000  # 模拟数据
    
    def _get_max_bid_ask_spread(self, sym_a: str, sym_b: str) -> float:
        """获取最大买卖价差百分比"""
        # TODO: 从Data-Core获取
        return 0.0001  # 模拟数据 0.01%
    
    def _calc_score(self, m: PairMetrics) -> float:
        """计算综合评分"""
        # 成交量评分 (对数缩放)
        min_vol = self.layer3.volume_min
        max_vol = 30_000_000
        avg_vol = m.volume_min
        
        if avg_vol <= min_vol:
            volume_score = 0.0
        else:
            volume_score = min(1.0, np.log(avg_vol / min_vol) / np.log(max_vol / min_vol))
        
        score = (
            self.scoring.w_coint * (1 - m.coint_p) +
            self.scoring.w_corr * m.corr_median +
            self.scoring.w_halflife * (1 / max(1, m.half_life)) +
            self.scoring.w_zmax * (m.zscore_max / 4.0) +
            self.scoring.w_stability * (1 - m.corr_std) +
            self.scoring.w_volume * volume_score
        )
        
        return float(score)
    
    def _optimize_params(self, m: PairMetrics, data: pd.DataFrame, pool: str) -> PairMetrics:
        """参数优化: 粗筛 + 精筛"""
        coarse = self.opt.coarse
        
        best_pf = 0.0
        best_params = None
        
        # 粗筛
        z_entries = np.arange(coarse['z_entry']['min'], 
                              coarse['z_entry']['max'] + 0.01, 
                              coarse['z_entry']['step'])
        z_exits = np.arange(coarse['z_exit']['min'],
                           coarse['z_exit']['max'] + 0.01,
                           coarse['z_exit']['step'])
        stop_offsets = np.arange(coarse['stop_offset']['min'],
                                 coarse['stop_offset']['max'] + 0.01,
                                 coarse['stop_offset']['step'])
        
        for z_e in z_entries:
            for z_x in z_exits:
                for z_s_offset in stop_offsets:
                    z_s = z_e + z_s_offset
                    if z_s > 7:
                        continue
                    
                    stats = self._backtest(data, z_e, z_x, z_s)
                    
                    if stats['pf'] > best_pf:
                        best_pf = stats['pf']
                        best_params = (z_e, z_x, z_s)
                        best_stats = stats
        
        # 精筛
        if best_params and best_pf >= self.opt.early_exit['min_pf_to_refine']:
            z_e, z_x, z_s = best_params
            fine_range = self.opt.fine['range']
            fine_step = self.opt.fine['step']
            
            fine_entries = np.arange(max(2, z_e - fine_range), 
                                     min(6, z_e + fine_range) + 0.01, 
                                     fine_step)
            fine_exits = np.arange(max(0.25, z_x - fine_range/2),
                                   min(2, z_x + fine_range/2) + 0.01,
                                   fine_step)
            
            for z_e_f in fine_entries:
                for z_x_f in fine_exits:
                    # stop保持粗筛结果附近的几个值
                    for z_s_f in [z_s - 0.5, z_s, z_s + 0.5]:
                        if z_s_f <= z_e_f or z_s_f > 7:
                            continue
                        
                        stats = self._backtest(data, z_e_f, z_x_f, z_s_f)
                        
                        if stats['pf'] > best_pf:
                            best_pf = stats['pf']
                            best_params = (z_e_f, z_x_f, z_s_f)
                            best_stats = stats
        
        if best_params:
            m.z_entry, m.z_exit, m.z_stop = best_params
            m.pf = best_stats['pf']
            m.sharpe = best_stats['sharpe']
            m.total_return = best_stats['return']
            m.max_dd = best_stats['max_dd']
            m.trades_count = best_stats['trades']
        
        return m
    
    def _backtest(self, data: pd.DataFrame, z_entry: float, z_exit: float, 
                  z_stop: float) -> Dict:
        """简化回测"""
        log_a = np.log(data['a'])
        log_b = np.log(data['b'])
        
        slope, intercept, _, _, _ = stats.linregress(log_b, log_a)
        spread = log_a - (slope * log_b + intercept)
        
        lookback = 120
        trades = []
        in_position = False
        entry_idx = 0
        
        for i in range(lookback, len(spread)):
            window = spread[i-lookback:i]
            z = (spread[i] - np.mean(window)) / np.std(window)
            
            if not in_position:
                if abs(z) > z_entry:
                    in_position = True
                    entry_idx = i
                    entry_z = z
            else:
                # 检查出场
                pnl = spread[i] - spread[entry_idx]
                if entry_z < 0:  # 做多价差
                    pnl = -pnl
                
                if abs(z) < z_exit or abs(z) > z_stop:
                    trades.append(pnl)
                    in_position = False
        
        if not trades:
            return {'pf': 0, 'sharpe': 0, 'return': 0, 'max_dd': 1, 'trades': 0}
        
        wins = [t for t in trades if t > 0]
        losses = [t for t in trades if t < 0]
        
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 1e-10
        
        pf = gross_profit / gross_loss
        total_return = sum(trades)
        
        # 简单Sharpe
        returns = trades
        sharpe = np.mean(returns) / (np.std(returns) + 1e-10) * np.sqrt(252)
        
        # Max DD (简化)
        cumulative = np.cumsum(trades)
        max_dd = 0
        peak = 0
        for c in cumulative:
            if c > peak:
                peak = c
            dd = peak - c
            if dd > max_dd:
                max_dd = dd
        
        return {
            'pf': pf,
            'sharpe': sharpe,
            'return': total_return,
            'max_dd': max_dd if max_dd > 0 else 0,
            'trades': len(trades)
        }
    
    def _apply_exclusion(self, results: List[PairMetrics]) -> List[PairMetrics]:
        """应用互斥限制"""
        if self.cfg.exclusion['mode'] != 'soft':
            return results
        
        max_per_symbol = self.cfg.exclusion['max_per_symbol']
        symbol_count = {}
        filtered = []
        
        for m in results:
            count_a = symbol_count.get(m.symbol_a, 0)
            count_b = symbol_count.get(m.symbol_b, 0)
            
            if count_a >= max_per_symbol or count_b >= max_per_symbol:
                continue
            
            symbol_count[m.symbol_a] = count_a + 1
            symbol_count[m.symbol_b] = count_b + 1
            filtered.append(m)
        
        return filtered
    
    def _to_pair_record(self, m: PairMetrics, pool: str) -> PairRecord:
        """转换为数据库记录"""
        return PairRecord(
            pool=pool,
            symbol_a=m.symbol_a,
            symbol_b=m.symbol_b,
            score=m.score,
            corr_median=m.corr_median,
            coint_p=m.coint_p,
            adf_p=m.adf_p,
            half_life=m.half_life,
            corr_std=m.corr_std,
            hurst=m.hurst,
            zscore_max=m.zscore_max,
            spread_std=m.spread_std,
            volume_min=m.volume_min,
            z_entry=m.z_entry,
            z_exit=m.z_exit,
            z_stop=m.z_stop,
            pf=m.pf,
            sharpe=m.sharpe,
            total_return=m.total_return,
            max_dd=m.max_dd,
            trades_count=m.trades_count
        )


if __name__ == "__main__":
    # 测试扫描器
    scanner = Scanner()
    results = scanner.scan("primary")
    print(f"\nScan completed: {len(results)} pairs")
    for r in results[:5]:
        print(f"  {r.symbol_a}-{r.symbol_b}: score={r.score:.3f}, "
              f"PF={r.pf:.2f}, z_entry={r.z_entry:.2f}")
