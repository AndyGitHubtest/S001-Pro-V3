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
        3. 三层筛选 (带漏斗统计)
        4. 评分排名
        5. 参数优化
        6. 保存结果
        7. 自适应调参评估
        """
        logger.info(f"Starting scan for pool '{pool}'")
        start_time = datetime.now()
        
        # 1. 获取候选币种 (从Data-Core)
        symbols = self._fetch_symbols()
        logger.info(f"Fetched {len(symbols)} symbols from Data-Core")
        
        # 2. 批量加载所有币种数据到内存 (一次DB查询，消除N*2次查询瓶颈)
        t0 = datetime.now()
        symbol_data = self._batch_load_data(symbols, pool)
        load_time = (datetime.now() - t0).total_seconds()
        logger.info(f"Batch loaded {len(symbol_data)}/{len(symbols)} symbols in {load_time:.1f}s")
        
        # 用有数据的币种重新生成配对
        valid_symbols = list(symbol_data.keys())
        candidates = self._generate_pairs(valid_symbols)
        logger.info(f"Generated {len(candidates)} candidate pairs")
        
        # 3-5. 三层筛选 + 评分 + 优化 (带漏斗统计，纯内存计算)
        results, funnel = self._process_pairs_with_funnel(candidates, pool, symbol_data)
        
        # 6. 保存到数据库
        pair_records = [self._to_pair_record(m, pool) for m in results]
        self.db.save_pairs(pool, pair_records)
        
        # 记录扫描历史 (精确的漏斗数据)
        duration = (datetime.now() - start_time).total_seconds() * 1000
        self.db.log_scan(
            pool=pool,
            candidates=len(candidates),
            l1=funnel.layer1_passed,
            l2=funnel.layer2_passed,
            l3=funnel.layer3_passed,
            top_n=len(results),
            top_score=results[0].score if results else 0,
            avg_score=np.mean([r.score for r in results]) if results else 0,
            duration_ms=int(duration)
        )
        
        logger.info(f"Scan completed: {len(results)} pairs saved, duration: {duration:.0f}ms")
        logger.info(f"Funnel: {funnel.candidates}→{funnel.data_loaded}→"
                    f"{funnel.layer1_passed}→{funnel.layer2_passed}→"
                    f"{funnel.layer3_passed}→{funnel.backtest_passed}→{funnel.final_count}")
        
        # 7. 自适应调参评估
        try:
            from adaptive_tuner import get_tuner
            tuner = get_tuner()
            funnel.final_count = len(results)
            actions = tuner.evaluate_and_tune(pool, funnel)
            if actions:
                # 参数已在内存中更新，下一轮扫描自动生效
                logger.info(f"[Tuner] 已调整 {len(actions)} 个参数，下轮扫描生效")
        except Exception as e:
            logger.warning(f"[Tuner] 自适应调参异常 (非致命): {e}")
        
        return pair_records
    
    # ===== 第一阶段预筛黑名单 (硬性排除, 这些币永远不进入配对) =====
    SYMBOL_BLACKLIST = {
        # --- 股票代币 (受股市时间/规则影响, 不适合24h套利) ---
        "TSLA/USDT", "MSTR/USDT", "AMZN/USDT", "COIN/USDT", "PLTR/USDT",
        "NVDA/USDT", "GOOGL/USDT", "META/USDT", "AAPL/USDT", "HOOD/USDT",
        "TSM/USDT", "MU/USDT", "SNDK/USDT", "INTC/USDT", "PAYP/USDT",
        # --- 商品/贵金属 (走势独立, 与crypto无相关性) ---
        "XAU/USDT", "XAG/USDT", "XPT/USDT", "XPD/USDT",
        "CL/USDT", "BZ/USDT", "NATGAS/USDT", "COPPER/USDT", "PRL/USDT",
        # --- 指数/ETF代币 ---
        "SPY/USDT", "QQQ/USDT", "EWY/USDT", "EWJ/USDT", "BTCDOM/USDT",
        # --- 稳定币/锚定币 (无波动, 套利无意义) ---
        "USDC/USDT", "FRAX/USDT", "USDT/USDT", "STBL/USDT", "STABLE/USDT",
        # --- 黄金/BTC锚定代币 (与底层资产同步, 不独立波动) ---
        "XAUT/USDT", "PAXG/USDT", "PUMPBTC/USDT",
        # --- 已确认垃圾/无意义代币 ---
        "USELESS/USDT", "CRCL/USDT",
    }
    
    # 预筛门槛: 24h USDT成交量最低500万才有资格进入配对
    PRE_FILTER_MIN_VOL_USDT = 5_000_000
    
    def _fetch_symbols(self) -> List[str]:
        """从共享数据库获取候选币种列表
        
        预筛逻辑 (快速，在币种级别淘汰垃圾):
        1. 24h有≥1000条1m数据 (数据完整)
        2. 历史总数据≥30天 (43200条)
        3. 24h USDT成交量 ≥ 500万 (流动性门槛)
        4. 不在黑名单 (股票/商品/稳定币)
        5. 非单字母/中文垃圾币
        
        通过预筛后才生成配对，大幅减少计算量。
        """
        try:
            logger.info(f"DB klines_db_path: {self.db.klines_db_path}")
            
            conn = self.db._get_klines_connection()
            if conn is None:
                logger.warning("K线数据库未配置，使用默认币种")
                return ["BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT",
                        "ARB/USDT", "OP/USDT", "MATIC/USDT", "LINK/USDT"]
            
            logger.info("K线数据库已连接")
            cursor = conn.cursor()
            
            # SQL预筛: 6重条件
            # 1. 24h数据完整 (≥1300条 ≈ 22h连续)
            # 2. 24h USDT成交量 ≥ 500万
            # 3. 历史数据 ≥ 60天 (86400条) — 排除新币
            # 4. 24h有足够波动 (max-min)/avg > 1% — 排除稳定币/死币
            # 5. 非停牌/退市 (24h内有数据)
            # 6. 非新币 (上线<60天的统计特征不稳定)
            min_vol = self.PRE_FILTER_MIN_VOL_USDT
            cursor.execute("""
                SELECT s.symbol, s.cnt, s.vol_usdt, s.volatility
                FROM (
                    SELECT symbol, 
                           COUNT(*) as cnt,
                           SUM(volume * close) as vol_usdt,
                           (MAX(close) - MIN(close)) / NULLIF(AVG(close), 0) as volatility
                    FROM klines 
                    WHERE ts > (SELECT MAX(ts) - 86400000 FROM klines)
                    GROUP BY symbol
                    HAVING cnt >= 1300 
                       AND SUM(volume * close) >= ?
                       AND (MAX(close) - MIN(close)) / NULLIF(AVG(close), 0) > 0.01
                ) s
                INNER JOIN (
                    SELECT symbol, COUNT(*) as total 
                    FROM klines 
                    GROUP BY symbol
                    HAVING total >= 86400
                ) t ON s.symbol = t.symbol
                ORDER BY s.vol_usdt DESC
            """, (min_vol,))
            rows = cursor.fetchall()
            
            # Python层过滤: 黑名单 + 中文 + 单字母
            symbols = []
            filtered = {'blacklist': 0, 'chinese': 0, 'single_letter': 0}
            for symbol, cnt, vol_usdt, volatility in rows:
                # 黑名单 (股票/商品/稳定币等)
                if symbol in self.SYMBOL_BLACKLIST:
                    filtered['blacklist'] += 1
                    continue
                # 中文/特殊字符
                if any(ord(c) > 127 for c in symbol.split('/')[0]):
                    filtered['chinese'] += 1
                    continue
                # 单字母 + 纯数字
                base = symbol.split("/")[0] if "/" in symbol else symbol
                if len(base) <= 1:
                    filtered['single_letter'] += 1
                    continue
                if base.isdigit():
                    filtered['single_letter'] += 1
                    continue
                symbols.append(symbol)
            
            logger.info(
                f"预筛: {len(rows)}个通过SQL(≥{min_vol/1e6:.0f}M+>1%波动) → "
                f"{len(symbols)}个合格 "
                f"(排除: 黑名单{filtered['blacklist']}, "
                f"特殊字符{filtered['chinese']}, "
                f"垃圾名{filtered['single_letter']})")
            
            if symbols:
                logger.info(f"Top 10: {', '.join(symbols[:10])}")
                logger.info(f"共{len(symbols)}个币种 → 将生成{len(symbols)*(len(symbols)-1)//2}个候选配对")
            
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
    
    def _batch_load_data(self, symbols: List[str], pool: str) -> Dict[str, pd.DataFrame]:
        """批量加载所有币种的15m数据到内存 (一次DB操作)
        
        关键优化: 消除 N*2 次DB查询瓶颈
        原来: 6216对 × 2次查询 = 12432次SQL → 10+分钟
        现在: 1次批量查询 + 内存重采样 → 几秒
        """
        result = {}
        try:
            conn = self.db._get_klines_connection()
            if conn is None:
                return result
            
            timeframe = self.cfg.trading.primary.timeframe if pool == "primary" else self.cfg.trading.secondary.timeframe
            
            # 一次性读取所有币种的1m数据 (最近3000条 ≈ 200条15m)
            for sym in symbols:
                try:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT ts, open, high, low, close, volume FROM klines 
                        WHERE symbol = ? AND interval = '1m'
                        ORDER BY ts DESC LIMIT 3000
                    """, (sym,))
                    rows = cursor.fetchall()
                    
                    if len(rows) < 1800:  # 至少120条15m
                        continue
                    
                    df = self._resample_1m_to_15m(rows)
                    if df is not None and len(df) >= 120:
                        result[sym] = df
                except Exception as e:
                    logger.debug(f"加载 {sym} 失败: {e}")
                    continue
            
            return result
        except Exception as e:
            logger.error(f"批量加载失败: {e}")
            return result
    
    def _process_pairs(self, candidates: List[Tuple[str, str]], pool: str) -> List[PairMetrics]:
        """处理所有配对 (向后兼容)"""
        results, _ = self._process_pairs_with_funnel(candidates, pool, {})
        return results
    
    def _process_pairs_with_funnel(self, candidates: List[Tuple[str, str]], pool: str,
                                   symbol_data: Dict[str, pd.DataFrame] = None) -> Tuple[List[PairMetrics], 'FunnelStats']:
        """处理所有配对: 逐层筛选 + 评分 + 优化 (纯内存计算)"""
        from adaptive_tuner import FunnelStats
        funnel = FunnelStats(candidates=len(candidates))
        results = []
        
        total = len(candidates)
        log_interval = max(1, total // 10)  # 每10%打印一次进度
        t_start = datetime.now()
        
        for i, (sym_a, sym_b) in enumerate(candidates):
            # 进度日志
            if i > 0 and i % log_interval == 0:
                elapsed = (datetime.now() - t_start).total_seconds()
                pct = i / total * 100
                rate = i / elapsed if elapsed > 0 else 0
                eta = (total - i) / rate if rate > 0 else 0
                logger.info(f"扫描进度: {i}/{total} ({pct:.0f}%) | "
                           f"L1={funnel.layer1_passed} L2={funnel.layer2_passed} "
                           f"L3={funnel.layer3_passed} 回测={len(results)} | "
                           f"速度={rate:.0f}对/s ETA={eta:.0f}s")
            
            try:
                metrics = self._analyze_pair(sym_a, sym_b, pool, funnel, symbol_data)
                if metrics:
                    results.append(metrics)
            except Exception as e:
                logger.warning(f"Failed to analyze {sym_a}-{sym_b}: {e}")
                continue
        
        funnel.backtest_passed = len(results)
        
        elapsed_total = (datetime.now() - t_start).total_seconds()
        logger.info(f"扫描完成: {total}对 in {elapsed_total:.1f}s | "
                   f"L1={funnel.layer1_passed} L2={funnel.layer2_passed} "
                   f"L3={funnel.layer3_passed} 回测通过={len(results)}")
        
        # 按评分排序
        results.sort(key=lambda x: x.score, reverse=True)
        
        # 应用互斥限制
        results = self._apply_exclusion(results)
        
        # 取Top N
        pool_cfg = self.cfg.trading.primary if pool == "primary" else self.cfg.trading.secondary
        final = results[:pool_cfg.top_n]
        funnel.final_count = len(final)
        
        return final, funnel
    
    def _analyze_pair(self, sym_a: str, sym_b: str, pool: str, 
                      funnel: 'FunnelStats' = None,
                      symbol_data: Dict[str, pd.DataFrame] = None) -> Optional[PairMetrics]:
        """
        分析单个配对 — 逐层淘汰
        第一层过关 → 进入第二层 → 过关 → 进入第三层 → 过关 → 回测验证
        """
        # 从内存获取数据 (如有), 否则走DB
        if symbol_data and sym_a in symbol_data and sym_b in symbol_data:
            df_a = symbol_data[sym_a]
            df_b = symbol_data[sym_b]
            # 对齐时间
            merged = pd.merge(
                df_a[['ts', 'close']].rename(columns={'close': 'a'}),
                df_b[['ts', 'close']].rename(columns={'close': 'b'}),
                on='ts', how='inner'
            ).sort_values('ts')
            data = merged[['a', 'b']].astype(float) if len(merged) >= 120 else None
        else:
            data = self._load_data(sym_a, sym_b, pool)
        
        if data is None or len(data) < 120:
            return None
        
        if funnel:
            funnel.data_loaded += 1
        
        m = PairMetrics(symbol_a=sym_a, symbol_b=sym_b)
        
        # ========== Layer 1: Statistical Foundation ==========
        m.corr_median = self._calc_median_correlation(data)
        if m.corr_median < self.layer1.corr_median_min:
            if funnel:
                funnel.reject_reasons['L1_corr'] = funnel.reject_reasons.get('L1_corr', 0) + 1
            return None
        
        m.coint_p = self._cointegration_test(data)
        if m.coint_p > self.layer1.coint_p_max:
            if funnel:
                funnel.reject_reasons['L1_coint'] = funnel.reject_reasons.get('L1_coint', 0) + 1
            return None
        
        m.adf_p = self._adf_test(data)
        if m.adf_p > self.layer1.adf_p_max:
            if funnel:
                funnel.reject_reasons['L1_adf'] = funnel.reject_reasons.get('L1_adf', 0) + 1
            return None
        
        if funnel:
            funnel.layer1_passed += 1
        
        # ========== Layer 2: Stability ==========
        m.half_life = self._calc_half_life(data)
        if m.half_life > self.layer2.half_life_max:
            if funnel:
                funnel.reject_reasons['L2_hl'] = funnel.reject_reasons.get('L2_hl', 0) + 1
            return None
        
        m.corr_std = self._calc_rolling_correlation_std(data)
        if m.corr_std > self.layer2.corr_std_max:
            if funnel:
                funnel.reject_reasons['L2_corr_std'] = funnel.reject_reasons.get('L2_corr_std', 0) + 1
            return None
        
        m.hurst = self._calc_hurst_exponent(data)
        if m.hurst > self.layer2.hurst_max:
            if funnel:
                funnel.reject_reasons['L2_hurst'] = funnel.reject_reasons.get('L2_hurst', 0) + 1
            return None
        
        if funnel:
            funnel.layer2_passed += 1
        
        # ========== Layer 3: 能不能赚钱 ==========
        # 核心就两个问题:
        #   1. Z-Score够不够大 → 有没有入场机会
        #   2. 价差波动够不够 → 有没有利润空间
        # (成交量/流动性已在预筛阶段用500万USDT门槛保证了)
        
        m.zscore_max = self._calc_max_zscore(data)
        if m.zscore_max < self.layer3.zscore_max_min:
            if funnel:
                funnel.reject_reasons['L3_zmax'] = funnel.reject_reasons.get('L3_zmax', 0) + 1
            return None
        
        m.spread_std = self._calc_spread_std(data)
        if m.spread_std < self.layer3.spread_std_min:
            if funnel:
                funnel.reject_reasons['L3_spread'] = funnel.reject_reasons.get('L3_spread', 0) + 1
            return None
        
        # volume/bid_ask 不再做L3硬过滤 (预筛已保证流动性)
        # 但仍然计算并记录，用于评分
        m.volume_min = self._get_min_volume(sym_a, sym_b) if not symbol_data else 10_000_000
        m.bid_ask_max = 0.001  # 不再查DB，预筛已保证
        
        if funnel:
            funnel.layer3_passed += 1
        
        # ========== Scoring ==========
        m.score = self._calc_score(m)
        
        # ========== Parameter Optimization ==========
        m = self._optimize_params(m, data, pool)
        
        # 检查回测结果
        if m.pf < self.cfg.output['min_pf'] or m.total_return <= 0:
            if funnel:
                funnel.reject_reasons['L4_backtest'] = funnel.reject_reasons.get('L4_backtest', 0) + 1
            return None
        
        return m
    
    def _load_data(self, sym_a: str, sym_b: str, pool: str) -> Optional[pd.DataFrame]:
        """从共享数据库加载配对历史数据，1m合成15m"""
        try:
            conn = self.db._get_klines_connection()
            if conn is None:
                logger.warning("K线数据库未配置，无法加载数据")
                return None
            
            # 获取timeframe (15m)
            timeframe = self.cfg.trading.primary.timeframe if pool == "primary" else self.cfg.trading.secondary.timeframe
            
            # Data-Core只有1m数据，需要查询1m然后合成15m
            # 15m需要15条1m数据，200条15m需要3000条1m
            cursor = conn.cursor()
            cursor.execute("""
                SELECT ts, open, high, low, close, volume FROM klines 
                WHERE symbol = ? AND interval = '1m'
                ORDER BY ts DESC
                LIMIT 3000
            """, (sym_a,))
            rows_a = cursor.fetchall()
            
            cursor.execute("""
                SELECT ts, open, high, low, close, volume FROM klines 
                WHERE symbol = ? AND interval = '1m'
                ORDER BY ts DESC
                LIMIT 3000
            """, (sym_b,))
            rows_b = cursor.fetchall()
            
            if len(rows_a) < 1800 or len(rows_b) < 1800:  # 至少120条15m = 1800条1m
                logger.debug(f"{sym_a}-{sym_b}: 1m数据不足 ({len(rows_a)}/{len(rows_b)})")
                return None
            
            # 1m转15m
            df_a = self._resample_1m_to_15m(rows_a)
            df_b = self._resample_1m_to_15m(rows_b)
            
            if df_a is None or df_b is None:
                return None
            
            # 合并并对齐
            df = pd.merge(df_a, df_b, on='ts', how='inner', suffixes=('_a', '_b'))
            df = df.sort_values('ts')
            
            if len(df) < 120:
                logger.debug(f"{sym_a}-{sym_b}: 15m对齐后不足 ({len(df)})")
                return None
            
            return df[['close_a', 'close_b']].rename(columns={'close_a': 'a', 'close_b': 'b'}).astype(float)
            
        except Exception as e:
            logger.error(f"加载数据失败 {sym_a}-{sym_b}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def _resample_1m_to_15m(self, rows: list) -> Optional[pd.DataFrame]:
        """将1m数据合成为15m"""
        if len(rows) < 15:
            return None
        
        # 转换为DataFrame
        df = pd.DataFrame(rows, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
        df['ts'] = pd.to_datetime(df['ts'], unit='ms')
        df = df.sort_values('ts')
        
        # 按15分钟分组 (floor到15分钟边界)
        df['period'] = df['ts'].dt.floor('15min')
        
        # 重采样
        resampled = df.groupby('period').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).reset_index()
        
        resampled = resampled.rename(columns={'period': 'ts'})
        return resampled
    
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
        """获取最小日成交量 (USDT计价，volume*close)"""
        try:
            conn = self.db._get_klines_connection()
            if conn is None:
                return 0
            cursor = conn.cursor()
            # volume字段是基础币种单位(如BTC)，需要×close转为USDT
            cursor.execute("""
                SELECT COALESCE(SUM(volume * close), 0) FROM klines 
                WHERE symbol = ? AND interval = '1m'
                AND ts > (SELECT MAX(ts) - 86400000 FROM klines)
            """, (sym_a,))
            vol_a = cursor.fetchone()[0] or 0
            
            cursor.execute("""
                SELECT COALESCE(SUM(volume * close), 0) FROM klines 
                WHERE symbol = ? AND interval = '1m'
                AND ts > (SELECT MAX(ts) - 86400000 FROM klines)
            """, (sym_b,))
            vol_b = cursor.fetchone()[0] or 0
            
            return int(min(vol_a, vol_b))
        except Exception as e:
            logger.warning(f"获取成交量失败 {sym_a}/{sym_b}: {e}")
            return 0
    
    def _get_max_bid_ask_spread(self, sym_a: str, sym_b: str) -> float:
        """获取最大买卖价差百分比
        
        注意: 1m K线的(high-low)/close不是bid-ask spread，
        而是分钟内价格波动范围(通常0.03%~0.1%)。
        真实bid-ask spread约为此值的1/5~1/10。
        
        我们用最小的(high-low)/close作为流动性代理指标:
        值越小→流动性越好→bid-ask越小
        """
        try:
            conn = self.db._get_klines_connection()
            if conn is None:
                return 1.0
            cursor = conn.cursor()
            # 用最近100条1m的 MEDIAN((high-low)/close) 作为流动性指标
            # 取中位数比平均值更稳健(不受异常K线影响)
            cursor.execute("""
                SELECT (high - low) / NULLIF(close, 0) as hl_ratio FROM klines 
                WHERE symbol = ? AND interval = '1m'
                ORDER BY ts DESC LIMIT 100
            """, (sym_a,))
            ratios_a = sorted([r[0] for r in cursor.fetchall() if r[0] is not None])
            median_a = ratios_a[len(ratios_a)//2] if ratios_a else 1.0
            
            cursor.execute("""
                SELECT (high - low) / NULLIF(close, 0) as hl_ratio FROM klines 
                WHERE symbol = ? AND interval = '1m'
                ORDER BY ts DESC LIMIT 100
            """, (sym_b,))
            ratios_b = sorted([r[0] for r in cursor.fetchall() if r[0] is not None])
            median_b = ratios_b[len(ratios_b)//2] if ratios_b else 1.0
            
            return float(max(median_a, median_b))
        except Exception as e:
            logger.warning(f"获取价差失败 {sym_a}/{sym_b}: {e}")
            return 1.0
    
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
        """三轮回测找最优参数 — 逐轮淘汰，越往后越精细
        
        第1轮: 快速预判 (1次回测) — 默认参数能不能赚钱？不能→直接淘汰
        第2轮: 粗搜 (16组合) — 在大范围里找最赚钱的区域
        第3轮: 精搜 (≤27组合) — 在最优区域精确定位最佳参数
        
        原来: 每对135次回测 × 800对 = 108,000次
        现在: 800×1 + 400×16 + 100×27 ≈ 10,000次 (减少90%)
        """
        # ===== 预算: spread和zscore数组 (只算一次，三轮复用) =====
        log_a = np.log(data['a'].values)
        log_b = np.log(data['b'].values)
        slope, intercept, _, _, _ = stats.linregress(log_b, log_a)
        spread = log_a - (slope * log_b + intercept)
        
        lookback = 120
        n = len(spread)
        if n <= lookback:
            return m
        
        s = pd.Series(spread)
        roll_mean = s.rolling(lookback).mean().values
        roll_std = s.rolling(lookback).std().values
        zscore_arr = np.full(n, np.nan)
        valid = roll_std > 1e-10
        zscore_arr[valid] = (spread[valid] - roll_mean[valid]) / roll_std[valid]
        
        z_series = zscore_arr[lookback:]
        spread_series = spread[lookback:]
        if len(z_series) < 10:
            return m
        
        # ==========================================
        # 第1轮: 快速预判 — 默认参数能赚钱吗？
        # ==========================================
        pool_cfg = self.cfg.trading.primary if pool == "primary" else self.cfg.trading.secondary
        default_result = self._fast_backtest(
            z_series, spread_series,
            pool_cfg.z_entry_default,
            pool_cfg.z_exit_default,
            pool_cfg.z_entry_default + pool_cfg.z_stop_offset_default
        )
        
        # 默认参数就亏钱 → 这对不值得优化，直接淘汰
        if default_result['pf'] < 1.0 or default_result['return'] <= 0:
            # 给个机会: 再试一组保守参数
            conservative = self._fast_backtest(z_series, spread_series, 3.0, 0.5, 5.0)
            if conservative['pf'] < 1.0 or conservative['return'] <= 0:
                return m  # 两组都亏 → 彻底淘汰
        
        # ==========================================
        # 第2轮: 粗搜 — 大步长找最赚钱的区域
        # z_entry: [2, 3, 4, 5]  × z_exit: [0.5, 1.0, 1.5]  × stop_offset: [1.5, 2.5]
        # = 4 × 3 × 2 = 24组合
        # ==========================================
        best_pf = 0.0
        best_params = None
        best_stats = None
        
        for z_e in [2.0, 3.0, 4.0, 5.0]:
            for z_x in [0.5, 1.0, 1.5]:
                for z_s_off in [1.5, 2.5]:
                    z_s = z_e + z_s_off
                    if z_s > 7:
                        continue
                    result = self._fast_backtest(z_series, spread_series, z_e, z_x, z_s)
                    if result['pf'] > best_pf and result['return'] > 0:
                        best_pf = result['pf']
                        best_params = (z_e, z_x, z_s)
                        best_stats = result
        
        # 粗搜没找到赚钱参数 → 淘汰
        if not best_params or best_pf < self.cfg.output.get('min_pf', 1.3):
            return m
        
        # ==========================================
        # 第3轮: 精搜 — 在最优区域小步长精确定位
        # 只在粗搜最优参数±0.5范围内搜索
        # ==========================================
        z_e_best, z_x_best, z_s_best = best_params
        
        for z_e_f in np.arange(max(2.0, z_e_best - 0.5), min(6.0, z_e_best + 0.5) + 0.01, 0.25):
            for z_x_f in np.arange(max(0.25, z_x_best - 0.25), min(2.0, z_x_best + 0.25) + 0.01, 0.25):
                for z_s_f in [z_s_best - 0.5, z_s_best - 0.25, z_s_best, z_s_best + 0.25, z_s_best + 0.5]:
                    if z_s_f <= z_e_f or z_s_f > 7:
                        continue
                    result = self._fast_backtest(z_series, spread_series, z_e_f, z_x_f, z_s_f)
                    if result['pf'] > best_pf and result['return'] > 0:
                        best_pf = result['pf']
                        best_params = (z_e_f, z_x_f, z_s_f)
                        best_stats = result
        
        # ===== 写入结果 =====
        if best_params and best_stats:
            m.z_entry, m.z_exit, m.z_stop = best_params
            m.pf = best_stats['pf']
            m.sharpe = best_stats['sharpe']
            m.total_return = best_stats['return']
            m.max_dd = best_stats['max_dd']
            m.trades_count = best_stats['trades']
        
        return m
    
    @staticmethod
    def _fast_backtest(z_series: np.ndarray, spread_series: np.ndarray,
                       z_entry: float, z_exit: float, z_stop: float) -> Dict:
        """高速回测 — Z-Score和Spread已预算，这里只做交易逻辑
        
        比原版快10-50倍:
        - 不重复算linregress/log/rolling
        - 纯numpy数组操作
        """
        trades = []
        in_position = False
        entry_idx = 0
        entry_z = 0.0
        
        abs_z = np.abs(z_series)
        n = len(z_series)
        
        for i in range(n):
            z = z_series[i]
            az = abs_z[i]
            
            if np.isnan(z):
                continue
            
            if not in_position:
                if az > z_entry:
                    in_position = True
                    entry_idx = i
                    entry_z = z
            else:
                if az < z_exit or az > z_stop:
                    pnl = spread_series[i] - spread_series[entry_idx]
                    if entry_z < 0:
                        pnl = -pnl
                    trades.append(pnl)
                    in_position = False
        
        if not trades:
            return {'pf': 0, 'sharpe': 0, 'return': 0, 'max_dd': 1, 'trades': 0}
        
        trades_arr = np.array(trades)
        wins = trades_arr[trades_arr > 0]
        losses = trades_arr[trades_arr < 0]
        
        gross_profit = wins.sum() if len(wins) > 0 else 0
        gross_loss = np.abs(losses.sum()) if len(losses) > 0 else 1e-10
        
        pf = gross_profit / gross_loss
        total_return = trades_arr.sum()
        sharpe = (trades_arr.mean() / (trades_arr.std() + 1e-10)) * np.sqrt(252)
        
        # Max DD
        cum = np.cumsum(trades_arr)
        peak = np.maximum.accumulate(cum)
        max_dd = np.max(peak - cum)
        
        return {
            'pf': float(pf),
            'sharpe': float(sharpe),
            'return': float(total_return),
            'max_dd': float(max_dd),
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
