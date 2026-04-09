"""
S001-Pro V3 数据库操作封装
所有数据库交互集中管理，确保数据流转清晰
"""

import sqlite3
import json
import logging
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass
from datetime import datetime
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class PairRecord:
    """配对记录"""
    pool: str
    symbol_a: str
    symbol_b: str
    score: float
    corr_median: float
    coint_p: float
    adf_p: float
    half_life: float
    corr_std: float
    hurst: float
    zscore_max: float
    spread_std: float
    volume_min: int
    z_entry: float
    z_exit: float
    z_stop: float
    pf: float
    sharpe: float
    total_return: float
    max_dd: float
    trades_count: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class PositionRecord:
    """持仓记录"""
    pair_key: str
    pool: str
    symbol_a: str
    symbol_b: str
    direction: str
    entry_z: float
    entry_price_a: float
    entry_price_b: float
    entry_time: str
    qty_a: float
    qty_b: float
    notional: float
    current_z: Optional[float] = None
    unrealized_pnl: float = 0.0
    z_entry: float = 0.0
    z_exit: float = 0.0
    z_stop: float = 0.0
    status: str = "open"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class TradeRecord:
    """交易记录"""
    pair_key: str
    pool: str
    symbol_a: str
    symbol_b: str
    direction: str
    entry_time: str
    entry_price_a: float
    entry_price_b: float
    entry_z: float
    exit_time: Optional[str] = None
    exit_price_a: Optional[float] = None
    exit_price_b: Optional[float] = None
    exit_z: Optional[float] = None
    exit_reason: Optional[str] = None
    qty_a: float = 0.0
    qty_b: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    fee_a: float = 0.0
    fee_b: float = 0.0
    created_at: Optional[str] = None


class DatabaseManager:
    """数据库管理器 - 所有数据库操作的唯一入口
    
    支持双数据库架构:
    - klines_db: 共享K线数据库 (Data-Core提供，只读)
    - state_db: 策略状态数据库 (私有，读写)
    """
    
    def __init__(self, state_db_path: str = "data/strategy.db", 
                 klines_db_path: str = None):
        self.state_db_path = state_db_path
        self.klines_db_path = klines_db_path
        self._klines_conn = None  # 共享数据库连接
        self._init_database()
    
    def _get_connection(self) -> sqlite3.Connection:
        """获取策略数据库连接 (读写)"""
        conn = sqlite3.connect(self.state_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _get_klines_connection(self) -> sqlite3.Connection:
        """获取K线数据库连接 (只读)"""
        if self._klines_conn is None and self.klines_db_path:
            try:
                self._klines_conn = sqlite3.connect(
                    self.klines_db_path, 
                    check_same_thread=False,
                    uri=True  # 支持URI模式
                )
                self._klines_conn.row_factory = sqlite3.Row
                # 设置为只读模式
                self._klines_conn.execute("PRAGMA query_only = ON")
            except Exception as e:
                logger.error(f"K线数据库连接失败: {e}")
                raise
        return self._klines_conn
    
    def _init_database(self):
        """初始化数据库表结构"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # pairs 表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pairs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pool TEXT NOT NULL,
                    symbol_a TEXT NOT NULL,
                    symbol_b TEXT NOT NULL,
                    score REAL NOT NULL,
                    corr_median REAL,
                    coint_p REAL,
                    adf_p REAL,
                    half_life REAL,
                    corr_std REAL,
                    hurst REAL,
                    zscore_max REAL,
                    spread_std REAL,
                    volume_min INTEGER,
                    z_entry REAL,
                    z_exit REAL,
                    z_stop REAL,
                    pf REAL,
                    sharpe REAL,
                    total_return REAL,
                    max_dd REAL,
                    trades_count INTEGER,
                    active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(pool, symbol_a, symbol_b)
                )
            """)
            
            # positions 表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pair_key TEXT NOT NULL UNIQUE,
                    pool TEXT NOT NULL,
                    symbol_a TEXT NOT NULL,
                    symbol_b TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry_z REAL NOT NULL,
                    entry_price_a REAL NOT NULL,
                    entry_price_b REAL NOT NULL,
                    entry_time TIMESTAMP NOT NULL,
                    qty_a REAL NOT NULL,
                    qty_b REAL NOT NULL,
                    notional REAL NOT NULL,
                    current_z REAL,
                    unrealized_pnl REAL DEFAULT 0,
                    z_entry REAL,
                    z_exit REAL,
                    z_stop REAL,
                    status TEXT DEFAULT 'open',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # trades 表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pair_key TEXT NOT NULL,
                    pool TEXT NOT NULL,
                    symbol_a TEXT NOT NULL,
                    symbol_b TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry_time TIMESTAMP NOT NULL,
                    entry_price_a REAL NOT NULL,
                    entry_price_b REAL NOT NULL,
                    entry_z REAL,
                    exit_time TIMESTAMP,
                    exit_price_a REAL,
                    exit_price_b REAL,
                    exit_z REAL,
                    exit_reason TEXT,
                    qty_a REAL NOT NULL,
                    qty_b REAL NOT NULL,
                    pnl REAL,
                    pnl_pct REAL,
                    fee_a REAL DEFAULT 0,
                    fee_b REAL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # metrics 表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    pool TEXT NOT NULL,
                    trades_count INTEGER DEFAULT 0,
                    win_count INTEGER DEFAULT 0,
                    loss_count INTEGER DEFAULT 0,
                    gross_profit REAL DEFAULT 0,
                    gross_loss REAL DEFAULT 0,
                    net_pnl REAL DEFAULT 0,
                    pf REAL,
                    win_rate REAL,
                    avg_win REAL,
                    avg_loss REAL,
                    max_positions INTEGER DEFAULT 0,
                    avg_position_time REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(date, pool)
                )
            """)
            
            # scan_history 表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scan_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    pool TEXT NOT NULL,
                    candidates_count INTEGER,
                    layer1_passed INTEGER,
                    layer2_passed INTEGER,
                    layer3_passed INTEGER,
                    top_n INTEGER,
                    top_score REAL,
                    avg_score REAL,
                    duration_ms INTEGER
                )
            """)
            
            # 创建索引
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pairs_pool_score ON pairs(pool, score DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pairs_symbols ON pairs(symbol_a, symbol_b)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_pair ON trades(pair_key)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(entry_time)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_date ON metrics(date)")
            
            conn.commit()
            logger.info("Database initialized successfully")
    
    # ========== Pairs 操作 ==========
    
    def save_pairs(self, pool: str, pairs: List[PairRecord]):
        """保存配对列表（事务批量写入）"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # 标记旧数据为inactive
            cursor.execute(
                "UPDATE pairs SET active = 0 WHERE pool = ?",
                (pool,)
            )
            
            # 批量插入新数据
            for p in pairs:
                cursor.execute("""
                    INSERT OR REPLACE INTO pairs 
                    (pool, symbol_a, symbol_b, score, corr_median, coint_p, adf_p,
                     half_life, corr_std, hurst, zscore_max, spread_std, volume_min,
                     z_entry, z_exit, z_stop, pf, sharpe, total_return, max_dd, trades_count, active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """, (
                    p.pool, p.symbol_a, p.symbol_b, p.score,
                    p.corr_median, p.coint_p, p.adf_p,
                    p.half_life, p.corr_std, p.hurst,
                    p.zscore_max, p.spread_std, p.volume_min,
                    p.z_entry, p.z_exit, p.z_stop,
                    p.pf, p.sharpe, p.total_return, p.max_dd, p.trades_count
                ))
            
            conn.commit()
            logger.info(f"Saved {len(pairs)} pairs to pool '{pool}'")
    
    def get_active_pairs(self, pool: str) -> List[PairRecord]:
        """获取指定池的活跃配对"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM pairs 
                WHERE pool = ? AND active = 1
                ORDER BY score DESC
            """, (pool,))
            
            rows = cursor.fetchall()
            return [self._row_to_pair(row) for row in rows]
    
    def _row_to_pair(self, row: sqlite3.Row) -> PairRecord:
        """数据库行转PairRecord"""
        return PairRecord(
            pool=row['pool'],
            symbol_a=row['symbol_a'],
            symbol_b=row['symbol_b'],
            score=row['score'],
            corr_median=row['corr_median'],
            coint_p=row['coint_p'],
            adf_p=row['adf_p'],
            half_life=row['half_life'],
            corr_std=row['corr_std'],
            hurst=row['hurst'],
            zscore_max=row['zscore_max'],
            spread_std=row['spread_std'],
            volume_min=row['volume_min'],
            z_entry=row['z_entry'],
            z_exit=row['z_exit'],
            z_stop=row['z_stop'],
            pf=row['pf'],
            sharpe=row['sharpe'],
            total_return=row['total_return'],
            max_dd=row['max_dd'],
            trades_count=row['trades_count'],
            created_at=row['created_at'],
            updated_at=row['updated_at']
        )
    
    # ========== Positions 操作 ==========
    
    def open_position(self, pos: PositionRecord):
        """开仓 - 写入新持仓"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO positions 
                (pair_key, pool, symbol_a, symbol_b, direction, entry_z,
                 entry_price_a, entry_price_b, entry_time, qty_a, qty_b, notional,
                 z_entry, z_exit, z_stop, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
            """, (
                pos.pair_key, pos.pool, pos.symbol_a, pos.symbol_b, pos.direction,
                pos.entry_z, pos.entry_price_a, pos.entry_price_b, pos.entry_time,
                pos.qty_a, pos.qty_b, pos.notional,
                pos.z_entry, pos.z_exit, pos.z_stop
            ))
            conn.commit()
            logger.info(f"Position opened: {pos.pair_key}")
    
    def update_position(self, pair_key: str, current_z: float, unrealized_pnl: float):
        """更新持仓状态（每tick）"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE positions 
                SET current_z = ?, unrealized_pnl = ?, updated_at = CURRENT_TIMESTAMP
                WHERE pair_key = ? AND status = 'open'
            """, (current_z, unrealized_pnl, pair_key))
            conn.commit()
    
    def close_position(self, pair_key: str, trade: TradeRecord):
        """平仓 - 删除持仓记录，写入交易记录"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # 删除持仓
            cursor.execute("DELETE FROM positions WHERE pair_key = ?", (pair_key,))
            
            # 写入交易记录
            cursor.execute("""
                INSERT INTO trades 
                (pair_key, pool, symbol_a, symbol_b, direction, entry_time,
                 entry_price_a, entry_price_b, entry_z, exit_time, exit_price_a, exit_price_b,
                 exit_z, exit_reason, qty_a, qty_b, pnl, pnl_pct, fee_a, fee_b)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.pair_key, trade.pool, trade.symbol_a, trade.symbol_b, trade.direction,
                trade.entry_time, trade.entry_price_a, trade.entry_price_b, trade.entry_z,
                trade.exit_time, trade.exit_price_a, trade.exit_price_b, trade.exit_z,
                trade.exit_reason, trade.qty_a, trade.qty_b, trade.pnl, trade.pnl_pct,
                trade.fee_a, trade.fee_b
            ))
            
            conn.commit()
            logger.info(f"Position closed: {pair_key}, PnL: {trade.pnl:.2f}")
    
    def delete_position(self, pair_key: str):
        """
        删除持仓记录 - 用于同步时清除交易所已平但本地仍存在的幽灵持仓
        铁律: 以交易所为准
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM positions WHERE pair_key = ? AND status = 'open'
            """, (pair_key,))
            conn.commit()
            logger.warning(f"🗑️ Deleted ghost position from DB: {pair_key}")
    
    def get_open_positions(self, pool: Optional[str] = None) -> List[PositionRecord]:
        """获取当前持仓"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            if pool:
                cursor.execute("""
                    SELECT * FROM positions WHERE status = 'open' AND pool = ?
                """, (pool,))
            else:
                cursor.execute("SELECT * FROM positions WHERE status = 'open'")
            
            rows = cursor.fetchall()
            return [self._row_to_position(row) for row in rows]
    
    def _row_to_position(self, row: sqlite3.Row) -> PositionRecord:
        """数据库行转PositionRecord"""
        return PositionRecord(
            pair_key=row['pair_key'],
            pool=row['pool'],
            symbol_a=row['symbol_a'],
            symbol_b=row['symbol_b'],
            direction=row['direction'],
            entry_z=row['entry_z'],
            entry_price_a=row['entry_price_a'],
            entry_price_b=row['entry_price_b'],
            entry_time=row['entry_time'],
            qty_a=row['qty_a'],
            qty_b=row['qty_b'],
            notional=row['notional'],
            current_z=row['current_z'],
            unrealized_pnl=row['unrealized_pnl'],
            z_entry=row['z_entry'],
            z_exit=row['z_exit'],
            z_stop=row['z_stop'],
            status=row['status'],
            created_at=row['created_at'],
            updated_at=row['updated_at']
        )
    
    # ========== Trades 查询 ==========
    
    def get_today_trades(self, pool: Optional[str] = None) -> List[TradeRecord]:
        """获取今日交易"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            if pool:
                cursor.execute("""
                    SELECT * FROM trades 
                    WHERE date(entry_time) = date('now') AND pool = ?
                    ORDER BY entry_time DESC
                """, (pool,))
            else:
                cursor.execute("""
                    SELECT * FROM trades 
                    WHERE date(entry_time) = date('now')
                    ORDER BY entry_time DESC
                """)
            
            rows = cursor.fetchall()
            return [self._row_to_trade(row) for row in rows]
    
    def get_trade_stats(self, days: int = 30) -> Dict[str, Any]:
        """获取交易统计"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as win_count,
                    SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as loss_count,
                    SUM(pnl) as total_pnl,
                    SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) as gross_profit,
                    SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END) as gross_loss,
                    AVG(pnl) as avg_pnl,
                    AVG(CASE WHEN pnl > 0 THEN pnl END) as avg_win,
                    AVG(CASE WHEN pnl < 0 THEN pnl END) as avg_loss
                FROM trades 
                WHERE entry_time >= date('now', '-{} days')
            """.format(days))
            
            row = cursor.fetchone()
            return {
                'total_trades': row['total_trades'] or 0,
                'win_count': row['win_count'] or 0,
                'loss_count': row['loss_count'] or 0,
                'total_pnl': row['total_pnl'] or 0,
                'gross_profit': row['gross_profit'] or 0,
                'gross_loss': row['gross_loss'] or 0,
                'avg_pnl': row['avg_pnl'] or 0,
                'avg_win': row['avg_win'] or 0,
                'avg_loss': row['avg_loss'] or 0,
                'win_rate': (row['win_count'] or 0) / row['total_trades'] if row['total_trades'] else 0,
                'pf': abs(row['gross_profit'] or 0) / abs(row['gross_loss'] or 1) if row['gross_loss'] else 0
            }
    
    def _row_to_trade(self, row: sqlite3.Row) -> TradeRecord:
        """数据库行转TradeRecord"""
        return TradeRecord(
            pair_key=row['pair_key'],
            pool=row['pool'],
            symbol_a=row['symbol_a'],
            symbol_b=row['symbol_b'],
            direction=row['direction'],
            entry_time=row['entry_time'],
            entry_price_a=row['entry_price_a'],
            entry_price_b=row['entry_price_b'],
            entry_z=row['entry_z'],
            exit_time=row['exit_time'],
            exit_price_a=row['exit_price_a'],
            exit_price_b=row['exit_price_b'],
            exit_z=row['exit_z'],
            exit_reason=row['exit_reason'],
            qty_a=row['qty_a'],
            qty_b=row['qty_b'],
            pnl=row['pnl'],
            pnl_pct=row['pnl_pct'],
            fee_a=row['fee_a'],
            fee_b=row['fee_b'],
            created_at=row['created_at']
        )
    
    # ========== Metrics 操作 ==========
    
    def update_daily_metrics(self, pool: str):
        """更新每日统计"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            today = datetime.now().strftime('%Y-%m-%d')
            
            # 统计今日交易
            cursor.execute("""
                SELECT 
                    COUNT(*) as trades,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
                    SUM(pnl) as net_pnl,
                    SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) as gross_profit,
                    SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END) as gross_loss
                FROM trades 
                WHERE date(entry_time) = ? AND pool = ?
            """, (today, pool))
            
            row = cursor.fetchone()
            
            cursor.execute("""
                INSERT OR REPLACE INTO metrics 
                (date, pool, trades_count, win_count, loss_count, 
                 gross_profit, gross_loss, net_pnl)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                today, pool,
                row['trades'] or 0, row['wins'] or 0, row['losses'] or 0,
                row['gross_profit'] or 0, row['gross_loss'] or 0,
                row['net_pnl'] or 0
            ))
            
            conn.commit()
    
    # ========== Shutdown Snapshot ==========
    
    def save_shutdown_snapshot(self, snapshot: Dict):
        """保存停机快照 (用于重启恢复)"""
        import json
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 使用单行表存储JSON快照
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shutdown_snapshot (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    data TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                INSERT OR REPLACE INTO shutdown_snapshot (id, data) VALUES (1, ?)
            """, (json.dumps(snapshot, default=str),))
            conn.commit()
            logger.info(f"停机快照已保存: {len(snapshot.get('open_positions', []))} 个持仓")
    
    def get_shutdown_snapshot(self) -> Optional[Dict]:
        """获取停机快照"""
        import json
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT data FROM shutdown_snapshot WHERE id = 1")
                row = cursor.fetchone()
                if row:
                    return json.loads(row[0])
        except Exception as e:
            logger.warning(f"获取停机快照失败: {e}")
        return None
    
    def get_recent_trades(self, days: int = 7) -> List[TradeRecord]:
        """获取最近N天的交易记录"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM trades 
                WHERE entry_time >= datetime('now', ? || ' days')
                ORDER BY entry_time DESC
            """, (f"-{days}",))
            rows = cursor.fetchall()
            return [self._row_to_trade(row) for row in rows]
    
    # ========== Scan History ==========
    
    def log_scan(self, pool: str, candidates: int, l1: int, l2: int, l3: int, 
                 top_n: int, top_score: float, avg_score: float, duration_ms: int):
        """记录扫描历史"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO scan_history 
                (pool, candidates_count, layer1_passed, layer2_passed, layer3_passed,
                 top_n, top_score, avg_score, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (pool, candidates, l1, l2, l3, top_n, top_score, avg_score, duration_ms))
            conn.commit()


# 全局数据库实例
_db: Optional[DatabaseManager] = None


def get_db() -> DatabaseManager:
    """获取全局数据库实例 (支持双数据库)"""
    global _db
    if _db is None:
        from config import get_config
        cfg = get_config()
        # 传入两个数据库路径
        _db = DatabaseManager(
            state_db_path=cfg.database.state_db,
            klines_db_path=getattr(cfg.database, 'klines_db', None)
        )
    return _db


if __name__ == "__main__":
    # 测试数据库
    db = DatabaseManager("data/test_strategy.db")
    
    # 测试写入
    test_pair = PairRecord(
        pool="primary",
        symbol_a="BTC/USDT",
        symbol_b="ETH/USDT",
        score=0.85,
        corr_median=0.78,
        coint_p=0.05,
        adf_p=0.03,
        half_life=12.5,
        corr_std=0.08,
        hurst=0.45,
        zscore_max=2.8,
        spread_std=0.002,
        volume_min=5000000,
        z_entry=2.5,
        z_exit=0.5,
        z_stop=4.0,
        pf=1.5,
        sharpe=1.2,
        total_return=0.15,
        max_dd=0.08,
        trades_count=12
    )
    
    db.save_pairs("primary", [test_pair])
    
    # 测试读取
    pairs = db.get_active_pairs("primary")
    print(f"Loaded {len(pairs)} pairs")
    print(f"First pair: {pairs[0].symbol_a}-{pairs[0].symbol_b}, score: {pairs[0].score}")
    
    print("\nDatabase test passed!")
