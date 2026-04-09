"""
高频持仓同步器
实时对账本地持仓与交易所持仓
"""
import time
import threading
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class SyncStatus(Enum):
    """同步状态"""
    SYNCED = "synced"           # 已同步
    LOCAL_LARGER = "local_larger"  # 本地持仓更大
    EXCHANGE_LARGER = "exchange_larger"  # 交易所持仓更大
    MISMATCH = "mismatch"       # 不匹配
    ERROR = "error"             # 错误


@dataclass
class PositionDiff:
    """持仓差异"""
    symbol: str
    local_qty: float
    exchange_qty: float
    diff: float
    diff_pct: float
    status: SyncStatus


class HighFrequencyPositionSync:
    """
    高频持仓同步器
    
    特性:
    1. 每秒自动对账
    2. 事件驱动对账 (下单、撤单、成交)
    3. 差异自动修复
    4. 实时告警
    """
    
    def __init__(self, 
                 exchange_client,
                 db_connection,
                 sync_interval: float = 1.0,  # 每秒对账
                 diff_threshold: float = 0.001,  # 差异阈值0.1%
                 auto_fix: bool = True):
        """
        Args:
            exchange_client: 交易所客户端
            db_connection: 数据库连接
            sync_interval: 对账间隔(秒)
            diff_threshold: 差异阈值
            auto_fix: 是否自动修复差异
        """
        self.exchange = exchange_client
        self.db = db_connection
        self.sync_interval = sync_interval
        self.diff_threshold = diff_threshold
        self.auto_fix = auto_fix
        
        self.running = False
        self.sync_thread: Optional[threading.Thread] = None
        
        # 统计
        self.sync_count = 0
        self.mismatch_count = 0
        self.fix_count = 0
        
        # 回调
        self.mismatch_callbacks: List[Callable] = []
        self.fix_callbacks: List[Callable] = []
        
    def start(self):
        """启动同步服务"""
        if self.running:
            return
        
        self.running = True
        self.sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        self.sync_thread.start()
        logger.info(f"持仓同步服务启动，间隔{self.sync_interval}秒")
        
    def stop(self):
        """停止同步服务"""
        self.running = False
        if self.sync_thread:
            self.sync_thread.join(timeout=5)
        logger.info("持仓同步服务停止")
        
    def _sync_loop(self):
        """同步循环"""
        while self.running:
            try:
                self.sync_all_positions()
            except Exception as e:
                logger.error(f"同步循环错误: {e}")
            
            time.sleep(self.sync_interval)
    
    def sync_all_positions(self) -> List[PositionDiff]:
        """
        同步所有持仓
        
        Returns:
            差异列表
        """
        self.sync_count += 1
        
        # 获取本地持仓
        local_positions = self._get_local_positions()
        
        # 获取交易所持仓
        try:
            exchange_positions = self._get_exchange_positions()
        except Exception as e:
            logger.error(f"获取交易所持仓失败: {e}")
            return []
        
        # 对比
        diffs = self._compare_positions(local_positions, exchange_positions)
        
        # 处理差异
        if diffs:
            self.mismatch_count += 1
            for diff in diffs:
                self._handle_mismatch(diff)
        
        return diffs
    
    def sync_position(self, symbol: str) -> Optional[PositionDiff]:
        """
        同步单个持仓
        
        Args:
            symbol: 币种
            
        Returns:
            差异信息，无差异返回None
        """
        local_qty = self._get_local_position(symbol)
        
        try:
            exchange_qty = self._get_exchange_position(symbol)
        except Exception as e:
            logger.error(f"获取{symbol}交易所持仓失败: {e}")
            return None
        
        diff = self._calc_diff(symbol, local_qty, exchange_qty)
        
        if diff.status != SyncStatus.SYNCED:
            self._handle_mismatch(diff)
        
        return diff
    
    def _get_local_positions(self) -> Dict[str, float]:
        """获取本地所有持仓"""
        # 从数据库读取
        cursor = self.db.cursor()
        cursor.execute("SELECT symbol, qty FROM positions WHERE qty != 0")
        rows = cursor.fetchall()
        return {row[0]: float(row[1]) for row in rows}
    
    def _get_local_position(self, symbol: str) -> float:
        """获取本地单个持仓"""
        cursor = self.db.cursor()
        cursor.execute("SELECT qty FROM positions WHERE symbol = ?", (symbol,))
        row = cursor.fetchone()
        return float(row[0]) if row else 0.0
    
    def _get_exchange_positions(self) -> Dict[str, float]:
        """获取交易所所有持仓"""
        positions = self.exchange.fetch_positions()
        return {
            p['symbol']: float(p['contracts']) 
            for p in positions 
            if float(p['contracts']) != 0
        }
    
    def _get_exchange_position(self, symbol: str) -> float:
        """获取交易所单个持仓"""
        try:
            position = self.exchange.fetch_position(symbol)
            return float(position['contracts']) if position else 0.0
        except:
            return 0.0
    
    def _compare_positions(self, local: Dict[str, float], 
                          exchange: Dict[str, float]) -> List[PositionDiff]:
        """对比持仓"""
        diffs = []
        all_symbols = set(local.keys()) | set(exchange.keys())
        
        for symbol in all_symbols:
            local_qty = local.get(symbol, 0.0)
            exchange_qty = exchange.get(symbol, 0.0)
            
            diff = self._calc_diff(symbol, local_qty, exchange_qty)
            if diff.status != SyncStatus.SYNCED:
                diffs.append(diff)
        
        return diffs
    
    def _calc_diff(self, symbol: str, local_qty: float, 
                  exchange_qty: float) -> PositionDiff:
        """计算差异"""
        diff = local_qty - exchange_qty
        
        if abs(diff) < 1e-10:
            status = SyncStatus.SYNCED
        elif abs(diff) / max(abs(exchange_qty), 1e-10) < self.diff_threshold:
            status = SyncStatus.SYNCED  # 小差异视为同步
        elif local_qty > exchange_qty:
            status = SyncStatus.LOCAL_LARGER
        else:
            status = SyncStatus.EXCHANGE_LARGER
        
        diff_pct = abs(diff) / max(abs(exchange_qty), 1e-10) * 100
        
        return PositionDiff(
            symbol=symbol,
            local_qty=local_qty,
            exchange_qty=exchange_qty,
            diff=diff,
            diff_pct=diff_pct,
            status=status
        )
    
    def _handle_mismatch(self, diff: PositionDiff):
        """处理持仓差异"""
        logger.warning(
            f"持仓差异 [{diff.symbol}]: "
            f"本地={diff.local_qty:.6f}, "
            f"交易所={diff.exchange_qty:.6f}, "
            f"差异={diff.diff:.6f} ({diff.diff_pct:.2f}%)"
        )
        
        # 执行回调
        for callback in self.mismatch_callbacks:
            try:
                callback(diff)
            except Exception as e:
                logger.error(f"差异回调失败: {e}")
        
        # 自动修复
        if self.auto_fix:
            self._fix_mismatch(diff)
    
    def _fix_mismatch(self, diff: PositionDiff):
        """修复持仓差异"""
        try:
            # 以交易所为准，更新本地
            self._update_local_position(diff.symbol, diff.exchange_qty)
            
            self.fix_count += 1
            logger.info(f"已修复持仓差异 [{diff.symbol}]: 更新为{diff.exchange_qty:.6f}")
            
            # 执行回调
            for callback in self.fix_callbacks:
                try:
                    callback(diff)
                except Exception as e:
                    logger.error(f"修复回调失败: {e}")
                    
        except Exception as e:
            logger.error(f"修复持仓差异失败 [{diff.symbol}]: {e}")
    
    def _update_local_position(self, symbol: str, qty: float):
        """更新本地持仓"""
        cursor = self.db.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO positions (symbol, qty, updated_at) VALUES (?, ?, ?)",
            (symbol, qty, time.time())
        )
        self.db.commit()
    
    def register_mismatch_callback(self, callback: Callable):
        """注册差异回调"""
        self.mismatch_callbacks.append(callback)
    
    def register_fix_callback(self, callback: Callable):
        """注册修复回调"""
        self.fix_callbacks.append(callback)
    
    def get_statistics(self) -> Dict:
        """获取同步统计"""
        return {
            'sync_count': self.sync_count,
            'mismatch_count': self.mismatch_count,
            'fix_count': self.fix_count,
            'mismatch_rate': self.mismatch_count / max(self.sync_count, 1),
            'last_sync_time': time.time()
        }
    
    def force_sync(self):
        """强制立即同步"""
        return self.sync_all_positions()


class OrderEventSync:
    """
    订单事件驱动同步
    
    在订单事件发生时立即同步
    """
    
    def __init__(self, position_sync: HighFrequencyPositionSync):
        self.position_sync = position_sync
        
    def on_order_fill(self, order: Dict):
        """订单成交回调"""
        symbol = order.get('symbol')
        if symbol:
            logger.info(f"订单成交，立即同步 [{symbol}]")
            self.position_sync.sync_position(symbol)
    
    def on_order_cancel(self, order: Dict):
        """订单撤销回调"""
        symbol = order.get('symbol')
        if symbol:
            logger.info(f"订单撤销，立即同步 [{symbol}]")
            self.position_sync.sync_position(symbol)
    
    def on_position_change(self, symbol: str):
        """持仓变化回调"""
        logger.info(f"持仓变化，立即同步 [{symbol}]")
        self.position_sync.sync_position(symbol)


# 便捷函数
def create_position_sync(exchange_client, db_connection, **kwargs):
    """创建持仓同步器"""
    return HighFrequencyPositionSync(exchange_client, db_connection, **kwargs)


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    # 模拟交易所客户端
    class MockExchange:
        def fetch_positions(self):
            return [
                {'symbol': 'BTC/USDT', 'contracts': 0.1},
                {'symbol': 'ETH/USDT', 'contracts': 1.5},
            ]
        
        def fetch_position(self, symbol):
            positions = self.fetch_positions()
            for p in positions:
                if p['symbol'] == symbol:
                    return p
            return None
    
    # 模拟数据库
    import sqlite3
    db = sqlite3.connect(':memory:')
    db.execute("CREATE TABLE positions (symbol TEXT PRIMARY KEY, qty REAL, updated_at REAL)")
    db.execute("INSERT INTO positions VALUES ('BTC/USDT', 0.1, ?)", (time.time(),))
    db.execute("INSERT INTO positions VALUES ('ETH/USDT', 1.0, ?)", (time.time(),))  # 差异
    db.commit()
    
    # 创建同步器
    exchange = MockExchange()
    sync = HighFrequencyPositionSync(
        exchange_client=exchange,
        db_connection=db,
        sync_interval=1.0,
        auto_fix=True
    )
    
    # 注册回调
    def on_mismatch(diff):
        print(f"差异检测: {diff.symbol}, 差异={diff.diff}")
    
    def on_fix(diff):
        print(f"差异修复: {diff.symbol}")
    
    sync.register_mismatch_callback(on_mismatch)
    sync.register_fix_callback(on_fix)
    
    # 执行同步
    print("="*60)
    print("持仓同步测试")
    print("="*60)
    
    diffs = sync.sync_all_positions()
    
    print(f"\n发现{len(diffs)}个差异:")
    for diff in diffs:
        print(f"  {diff.symbol}: 本地={diff.local_qty}, 交易所={diff.exchange_qty}, "
              f"差异={diff.diff:.6f}")
    
    # 统计
    stats = sync.get_statistics()
    print(f"\n统计:")
    print(f"  同步次数: {stats['sync_count']}")
    print(f"  差异次数: {stats['mismatch_count']}")
    print(f"  修复次数: {stats['fix_count']}")
    print(f"  差异率: {stats['mismatch_rate']:.2%}")
    print("="*60)
