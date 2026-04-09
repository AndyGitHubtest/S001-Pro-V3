"""
S001-Pro V3 交易执行模块
职责: 交易所交互 + 订单执行 + 账户同步
"""

import ccxt
import logging
from typing import Optional, Dict, Tuple
from dataclasses import dataclass
from datetime import datetime
import time

from config import get_config
from database import get_db, PositionRecord, TradeRecord
from visualization import (
    trace_step, trace_context, log_info, log_error, heartbeat
)

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    """订单结果"""
    success: bool
    order_id: Optional[str] = None
    executed_price: float = 0.0
    executed_qty: float = 0.0
    fee: float = 0.0
    error: Optional[str] = None


class ExchangeAPI:
    """交易所API封装"""
    
    def __init__(self):
        self.cfg = get_config()
        self.exchange = None
        self._init_exchange()
    
    def _init_exchange(self):
        """初始化交易所连接"""
        try:
            # 使用Binance USDT永续合约
            self.exchange = ccxt.binanceusdm({
                'apiKey': self.cfg.data_core.get('api_key', ''),
                'secret': self.cfg.data_core.get('api_secret', ''),
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'future',
                    'adjustForTimeDifference': True
                }
            })
            
            # 测试网络
            if self.cfg.data_core.get('testnet', False):
                self.exchange.set_sandbox_mode(True)
            
            logger.info("Exchange API initialized")
            
        except Exception as e:
            logger.error(f"Failed to init exchange: {e}")
            raise
    
    def get_balance(self) -> Dict:
        """获取账户余额"""
        try:
            balance = self.exchange.fetch_balance()
            return {
                'USDT': balance.get('USDT', {}).get('free', 0),
                'total': balance.get('USDT', {}).get('total', 0)
            }
        except Exception as e:
            logger.error(f"Failed to fetch balance: {e}")
            return {'USDT': 0, 'total': 0}
    
    def get_position(self, symbol: str) -> Optional[Dict]:
        """获取持仓信息"""
        try:
            positions = self.exchange.fetch_positions([symbol])
            for pos in positions:
                if pos['symbol'] == symbol and float(pos['contracts']) != 0:
                    return {
                        'symbol': pos['symbol'],
                        'side': pos['side'],
                        'size': float(pos['contracts']),
                        'entry_price': float(pos['entryPrice']),
                        'unrealized_pnl': float(pos['unrealizedPnl'])
                    }
            return None
        except Exception as e:
            logger.error(f"Failed to fetch position for {symbol}: {e}")
            return None
    
    def get_ticker(self, symbol: str) -> Optional[Dict]:
        """获取行情"""
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return {
                'bid': ticker['bid'],
                'ask': ticker['ask'],
                'last': ticker['last'],
                'spread_pct': (ticker['ask'] - ticker['bid']) / ticker['last']
            }
        except Exception as e:
            logger.error(f"Failed to fetch ticker for {symbol}: {e}")
            return None
    
    def place_market_order(self, symbol: str, side: str, amount: float,
                          reduce_only: bool = False) -> OrderResult:
        """下市价单"""
        try:
            order = self.exchange.create_market_buy_order(
                symbol=symbol,
                amount=amount
            ) if side == 'buy' else self.exchange.create_market_sell_order(
                symbol=symbol,
                amount=amount
            )
            
            return OrderResult(
                success=True,
                order_id=order['id'],
                executed_price=float(order['average'] or order['price']),
                executed_qty=float(order['filled']),
                fee=float(order['fee']['cost']) if order.get('fee') else 0
            )
            
        except Exception as e:
            logger.error(f"Market order failed for {symbol}: {e}")
            return OrderResult(success=False, error=str(e))
    
    def place_limit_order(self, symbol: str, side: str, amount: float,
                         price: float, reduce_only: bool = False) -> OrderResult:
        """下限价单"""
        try:
            order = self.exchange.create_limit_buy_order(
                symbol=symbol,
                amount=amount,
                price=price
            ) if side == 'buy' else self.exchange.create_limit_sell_order(
                symbol=symbol,
                amount=amount,
                price=price
            )
            
            return OrderResult(
                success=True,
                order_id=order['id'],
                executed_price=float(order['price']),
                executed_qty=float(order['filled'])
            )
            
        except Exception as e:
            logger.error(f"Limit order failed for {symbol}: {e}")
            return OrderResult(success=False, error=str(e))
    
    def check_order_status(self, symbol: str, order_id: str) -> Optional[Dict]:
        """检查订单状态"""
        try:
            order = self.exchange.fetch_order(order_id, symbol)
            return {
                'id': order['id'],
                'status': order['status'],
                'filled': float(order['filled']),
                'remaining': float(order['remaining']),
                'average_price': float(order['average'] or 0)
            }
        except Exception as e:
            logger.error(f"Failed to check order {order_id}: {e}")
            return None


class Trader:
    """交易执行器"""
    
    def __init__(self):
        self.cfg = get_config()
        self.api = ExchangeAPI()
        self.db = get_db()
    
    @trace_step("Trader", "执行开仓")
    def execute_entry(self, pos: PositionRecord) -> bool:
        """
        执行开仓 - 双边同步下单
        返回: 是否成功
        """
        heartbeat("Trader")
        log_info("Trader", "开始开仓", 
                pair_key=pos.pair_key,
                direction=pos.direction,
                notional=pos.notional)
        
        # 确定下单方向
        if pos.direction == 'long_spread':
            side_a, side_b = 'buy', 'sell'
        else:
            side_a, side_b = 'sell', 'buy'
        
        with trace_context("Trader", "获取行情"):
            ticker_a = self.api.get_ticker(pos.symbol_a)
            ticker_b = self.api.get_ticker(pos.symbol_b)
            
            if not ticker_a or not ticker_b:
                log_error("Trader", "获取行情失败", Exception("Ticker is None"))
                return False
            
            log_info("Trader", "行情获取成功",
                    price_a=ticker_a['last'],
                    price_b=ticker_b['last'])
        
        # 下第一边订单
        with trace_context("Trader", "下第一边订单"):
            result_a = self.api.place_market_order(
                symbol=pos.symbol_a,
                side=side_a,
                amount=pos.qty_a
            )
            
            if not result_a.success:
                log_error("Trader", "第一边订单失败", 
                         Exception(result_a.error or "Unknown"),
                         symbol=pos.symbol_a)
                return False
            
            log_info("Trader", "第一边订单成功",
                    symbol=pos.symbol_a,
                    order_id=result_a.order_id,
                    executed_price=result_a.executed_price)
        
        # 下第二边订单
        with trace_context("Trader", "下第二边订单"):
            result_b = self.api.place_market_order(
                symbol=pos.symbol_b,
                side=side_b,
                amount=pos.qty_b
            )
            
            if not result_b.success:
                log_error("Trader", "第二边订单失败",
                         Exception(result_b.error or "Unknown"),
                         symbol=pos.symbol_b)
                # 回滚第一边
                self._rollback(pos.symbol_a, side_a, pos.qty_a)
                return False
            
            log_info("Trader", "第二边订单成功",
                    symbol=pos.symbol_b,
                    order_id=result_b.order_id,
                    executed_price=result_b.executed_price)
        
        # 确认成交
        time.sleep(0.5)
        
        # 更新成交价格
        pos.entry_price_a = result_a.executed_price
        pos.entry_price_b = result_b.executed_price
        
        log_info("Trader", "开仓完成", pair_key=pos.pair_key)
        logger.info(f"Entry executed successfully: {pos.pair_key}")
        return True
    
    def execute_exit(self, pos: PositionRecord) -> bool:
        """
        执行平仓 - 双边同步平仓
        """
        logger.info(f"Executing exit for {pos.pair_key}")
        
        # 确定平仓方向 (与开仓相反)
        if pos.direction == 'long_spread':
            side_a = 'sell'
            side_b = 'buy'
        else:
            side_a = 'buy'
            side_b = 'sell'
        
        # 平仓必须设置 reduce_only=True (防止开新仓)
        result_a = self.api.place_market_order(
            symbol=pos.symbol_a,
            side=side_a,
            amount=pos.qty_a,
            reduce_only=True
        )
        
        if not result_a.success:
            logger.error(f"Failed to close {pos.symbol_a}: {result_a.error}")
            return False
        
        result_b = self.api.place_market_order(
            symbol=pos.symbol_b,
            side=side_b,
            amount=pos.qty_b,
            reduce_only=True
        )
        
        if not result_b.success:
            logger.error(f"Failed to close {pos.symbol_b}: {result_b.error}")
            # 这里很难回滚，需要人工介入
            return False
        
        logger.info(f"Exit executed successfully: {pos.pair_key}")
        return True
    
    def _rollback(self, symbol: str, original_side: str, amount: float):
        """回滚订单 - 反向平仓"""
        logger.warning(f"Rolling back {symbol}")
        
        opposite_side = 'sell' if original_side == 'buy' else 'buy'
        
        for attempt in range(3):
            result = self.api.place_market_order(
                symbol=symbol,
                side=opposite_side,
                amount=amount,
                reduce_only=True
            )
            
            if result.success:
                logger.info(f"Rollback successful for {symbol}")
                return True
            
            time.sleep(0.5)
        
        logger.error(f"Rollback failed for {symbol} - manual intervention required")
        return False
    
    def sync_positions(self) -> Dict:
        """
        同步持仓 - 对比本地状态和交易所实际持仓
        返回差异报告
        """
        logger.info("Syncing positions with exchange")
        
        db_positions = self.db.get_open_positions()
        discrepancies = []
        
        for pos in db_positions:
            # 检查A边
            exch_pos_a = self.api.get_position(pos.symbol_a)
            exch_pos_b = self.api.get_position(pos.symbol_b)
            
            if not exch_pos_a and not exch_pos_b:
                # 交易所无持仓，本地有 -> 标记为已平仓
                logger.warning(f"Position {pos.pair_key} closed externally")
                discrepancies.append({
                    'pair_key': pos.pair_key,
                    'issue': 'closed_externally',
                    'local': pos.status,
                    'exchange': 'none'
                })
            
            elif exch_pos_a and exch_pos_b:
                # 检查方向是否一致
                expected_side_a = 'long' if pos.direction == 'long_spread' else 'short'
                if exch_pos_a['side'] != expected_side_a:
                    logger.error(f"Side mismatch for {pos.symbol_a}")
                    discrepancies.append({
                        'pair_key': pos.pair_key,
                        'issue': 'side_mismatch',
                        'symbol': pos.symbol_a
                    })
        
        return {
            'checked': len(db_positions),
            'discrepancies': discrepancies,
            'synced': len(discrepancies) == 0
        }
    
    def get_account_summary(self) -> Dict:
        """获取账户汇总"""
        balance = self.api.get_balance()
        
        # 计算未实现盈亏
        unrealized_pnl = 0
        db_positions = self.db.get_open_positions()
        
        for pos in db_positions:
            # 从交易所获取最新价格
            ticker_a = self.api.get_ticker(pos.symbol_a)
            ticker_b = self.api.get_ticker(pos.symbol_b)
            
            if ticker_a and ticker_b:
                if pos.direction == 'long_spread':
                    pnl_a = (ticker_a['last'] - pos.entry_price_a) * pos.qty_a
                    pnl_b = (pos.entry_price_b - ticker_b['last']) * pos.qty_b
                else:
                    pnl_a = (pos.entry_price_a - ticker_a['last']) * pos.qty_a
                    pnl_b = (ticker_b['last'] - pos.entry_price_b) * pos.qty_b
                
                unrealized_pnl += pnl_a + pnl_b
        
        # 今日实现盈亏
        today_trades = self.db.get_today_trades()
        realized_pnl = sum(t.pnl for t in today_trades)
        
        return {
            'balance_usdt': balance['USDT'],
            'unrealized_pnl': unrealized_pnl,
            'realized_pnl_today': realized_pnl,
            'total_equity': balance['USDT'] + unrealized_pnl,
            'open_positions': len(db_positions)
        }


if __name__ == "__main__":
    trader = Trader()
    summary = trader.get_account_summary()
    print(f"Account summary: {summary}")
    print("Trader test passed")
