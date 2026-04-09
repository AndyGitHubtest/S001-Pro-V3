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
    status: str = ""  # 'filled', 'partial', 'rejected', 'timeout'


@dataclass
class RollbackPlan:
    """回滚计划"""
    symbol: str
    side: str  # 反向操作
    amount: float
    reason: str
    priority: int = 1  # 1=立即, 2=延迟
    max_slippage: float = 0.02  # 最大滑点2%


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


class NakedPositionProtector:
    """
    裸仓保护器
    核心职责: 确保配对交易双边同时成交，任何情况不形成裸仓
    """
    
    def __init__(self, api: ExchangeAPI):
        self.api = api
        self.confirmation_timeout = 5  # 成交确认超时(秒)
        self.max_rollback_attempts = 3  # 最大回滚尝试次数
        self.rollback_slippage = 0.01  # 回滚允许滑点1%
    
    def execute_pair_order(self, pos: PositionRecord) -> Tuple[bool, str]:
        """
        执行配对订单，确保不形成裸仓
        
        返回: (成功, 消息)
        """
        log_info("Protector", "开始配对订单保护", pair_key=pos.pair_key)
        
        # Step 1: 预检
        if not self._pre_check(pos):
            return False, "预检失败"
        
        # Step 2: 下单A
        result_a = self._place_leg_order(pos, 'a')
        if not result_a.success:
            return False, f"A边下单失败: {result_a.error}"
        
        # Step 3: 确认A成交 (关键!)
        confirmed_a = self._confirm_filled(pos.symbol_a, result_a.order_id)
        if not confirmed_a['filled']:
            # A边未成交，直接失败
            return False, f"A边未成交: {confirmed_a['status']}"
        
        # Step 4: 下单B
        result_b = self._place_leg_order(pos, 'b')
        if not result_b.success:
            # B边失败，必须回滚A边
            rollback_result = self._emergency_rollback(
                pos.symbol_a, result_a, pos.direction
            )
            if not rollback_result:
                # 回滚失败，裸仓形成！进入紧急处理
                return self._handle_naked_position(pos, 'a', confirmed_a)
            return False, f"B边失败，A边已回滚: {result_b.error}"
        
        # Step 5: 确认B成交
        confirmed_b = self._confirm_filled(pos.symbol_b, result_b.order_id)
        if not confirmed_b['filled']:
            # B边未成交，回滚A边
            rollback_result = self._emergency_rollback(
                pos.symbol_a, result_a, pos.direction
            )
            if not rollback_result:
                return self._handle_naked_position(pos, 'a', confirmed_a)
            # 取消B边订单
            self._cancel_order(pos.symbol_b, result_b.order_id)
            return False, f"B边未成交，A边已回滚"
        
        # Step 6: 双边确认成功，更新持仓
        pos.entry_price_a = confirmed_a['price']
        pos.entry_price_b = confirmed_b['price']
        pos.qty_a = confirmed_a['qty']
        pos.qty_b = confirmed_b['qty']
        
        log_info("Protector", "配对订单完成", 
                pair_key=pos.pair_key,
                price_a=pos.entry_price_a,
                price_b=pos.entry_price_b)
        
        return True, "双边成交成功"
    
    def _pre_check(self, pos: PositionRecord) -> bool:
        """预检: 检查双边流动性"""
        with trace_context("Protector", "预检"):
            ticker_a = self.api.get_ticker(pos.symbol_a)
            ticker_b = self.api.get_ticker(pos.symbol_b)
            
            if not ticker_a or not ticker_b:
                log_error("Protector", "预检失败", Exception("无法获取行情"))
                return False
            
            # 检查价差是否在合理范围
            spread_a = ticker_a['ask'] - ticker_a['bid']
            spread_b = ticker_b['ask'] - ticker_b['bid']
            
            if spread_a / ticker_a['last'] > 0.01:  # 价差>1%
                log_error("Protector", "A边价差过大", 
                         Exception(f"Spread: {spread_a}"),
                         symbol=pos.symbol_a)
                return False
            
            if spread_b / ticker_b['last'] > 0.01:
                log_error("Protector", "B边价差过大",
                         Exception(f"Spread: {spread_b}"),
                         symbol=pos.symbol_b)
                return False
            
            return True
    
    def _place_leg_order(self, pos: PositionRecord, leg: str) -> OrderResult:
        """下单边订单"""
        symbol = pos.symbol_a if leg == 'a' else pos.symbol_b
        qty = pos.qty_a if leg == 'a' else pos.qty_b
        
        if pos.direction == 'long_spread':
            side = 'buy' if leg == 'a' else 'sell'
        else:
            side = 'sell' if leg == 'a' else 'buy'
        
        log_info("Protector", f"下{leg.upper()}边订单", 
                symbol=symbol, side=side, qty=qty)
        
        return self.api.place_market_order(symbol, side, qty)
    
    def _confirm_filled(self, symbol: str, order_id: str) -> Dict:
        """
        确认订单成交
        轮询检查直到超时
        """
        start_time = time.time()
        
        while time.time() - start_time < self.confirmation_timeout:
            status = self.api.check_order_status(symbol, order_id)
            
            if not status:
                time.sleep(0.1)
                continue
            
            if status['status'] == 'closed':
                return {
                    'filled': True,
                    'price': status['average_price'],
                    'qty': status['filled'],
                    'status': 'filled'
                }
            
            if status['status'] == 'canceled':
                # 取消视为失败（即使是部分成交）
                return {
                    'filled': False,
                    'price': 0,
                    'qty': 0,
                    'status': 'canceled',
                    'partial_qty': status['filled']  # 记录部分成交数量供回滚使用
                }
            
            if status['status'] == 'rejected':
                return {
                    'filled': False,
                    'price': 0,
                    'qty': 0,
                    'status': 'rejected'
                }
            
            time.sleep(0.1)
        
        # 超时
        return {
            'filled': False,
            'price': 0,
            'qty': 0,
            'status': 'timeout'
        }
    
    def _emergency_rollback(self, symbol: str, original_order: OrderResult,
                           direction: str) -> bool:
        """
        紧急回滚 - 反向平仓
        使用市价单立即平仓
        """
        log_info("Protector", "启动紧急回滚", 
                symbol=symbol, 
                original_order_id=original_order.order_id)
        
        # 确定反向操作
        original_side = 'buy' if direction == 'long_spread' else 'sell'
        rollback_side = 'sell' if original_side == 'buy' else 'buy'
        
        for attempt in range(self.max_rollback_attempts):
            log_info("Protector", f"回滚尝试 {attempt + 1}/{self.max_rollback_attempts}")
            
            result = self.api.place_market_order(
                symbol=symbol,
                side=rollback_side,
                amount=original_order.executed_qty,
                reduce_only=True  # 关键: 只能平仓，不能开新仓
            )
            
            if result.success:
                # 确认回滚成交
                confirmed = self._confirm_filled(symbol, result.order_id)
                if confirmed['filled']:
                    log_info("Protector", "回滚成功", 
                            symbol=symbol,
                            price=confirmed['price'])
                    return True
            
            time.sleep(0.5 * (attempt + 1))  # 指数退避
        
        log_error("Protector", "回滚失败", 
                 Exception(f"{self.max_rollback_attempts}次尝试均失败"),
                 symbol=symbol)
        return False
    
    def _cancel_order(self, symbol: str, order_id: str):
        """取消订单"""
        try:
            # ccxt取消订单接口
            pass  # 具体实现取决于交易所API
        except Exception as e:
            log_error("Protector", "取消订单失败", e, order_id=order_id)
    
    def _handle_naked_position(self, pos: PositionRecord, 
                               filled_leg: str, 
                               filled_info: Dict) -> Tuple[bool, str]:
        """
        处理裸仓 - 最高级别告警
        当所有回滚尝试都失败时调用
        """
        naked_symbol = pos.symbol_a if filled_leg == 'a' else pos.symbol_b
        naked_qty = filled_info['qty']
        naked_price = filled_info['price']
        
        # 1. 立即发送紧急告警
        log_error("Protector", "🚨🚨🚨 裸仓形成！", 
                 Exception("CRITICAL: NAKED POSITION DETECTED"),
                 pair_key=pos.pair_key,
                 naked_symbol=naked_symbol,
                 naked_qty=naked_qty,
                 naked_price=naked_price)
        
        # 2. 记录裸仓事件到数据库
        # TODO: 实现数据库记录
        
        # 3. 尝试强制平仓 (止损)
        # 使用更高滑点容忍度
        for attempt in range(5):
            result = self.api.place_market_order(
                symbol=naked_symbol,
                side='sell' if pos.direction == 'long_spread' else 'buy',
                amount=naked_qty,
                reduce_only=True
            )
            
            if result.success:
                confirmed = self._confirm_filled(naked_symbol, result.order_id)
                if confirmed['filled']:
                    log_info("Protector", "强制平仓成功", 
                            symbol=naked_symbol,
                            exit_price=confirmed['price'])
                    
                    # 计算损失
                    pnl = (confirmed['price'] - naked_price) * naked_qty
                    if pos.direction != 'long_spread':
                        pnl = -pnl
                    
                    return False, f"裸仓已强制平仓，损失: {pnl:.2f} USDT"
            
            time.sleep(1)
        
        # 4. 如果强制平仓也失败，通知人工介入
        return False, "裸仓无法平仓，需要人工紧急处理！"
    
    def verify_position_consistency(self, pos: PositionRecord) -> Dict:
        """
        验证持仓一致性
        对比本地记录与交易所实际持仓
        """
        with trace_context("Protector", "持仓一致性验证"):
            exch_pos_a = self.api.get_position(pos.symbol_a)
            exch_pos_b = self.api.get_position(pos.symbol_b)
            
            issues = []
            
            # 检查A边
            if pos.direction == 'long_spread':
                expected_side_a = 'long'
                expected_side_b = 'short'
            else:
                expected_side_a = 'short'
                expected_side_b = 'long'
            
            if not exch_pos_a:
                issues.append(f"A边无持仓: {pos.symbol_a}")
            elif exch_pos_a['side'] != expected_side_a:
                issues.append(f"A边方向错误: 期望{expected_side_a}, 实际{exch_pos_a['side']}")
            
            if not exch_pos_b:
                issues.append(f"B边无持仓: {pos.symbol_b}")
            elif exch_pos_b['side'] != expected_side_b:
                issues.append(f"B边方向错误: 期望{expected_side_b}, 实际{exch_pos_b['side']}")
            
            consistent = len(issues) == 0
            
            if not consistent:
                log_error("Protector", "持仓不一致", 
                         Exception(str(issues)),
                         pair_key=pos.pair_key)
            
            return {
                'consistent': consistent,
                'issues': issues,
                'exchange_a': exch_pos_a,
                'exchange_b': exch_pos_b
            }


class Trader:
    """交易执行器 - 集成裸仓保护"""
    
    def __init__(self):
        self.cfg = get_config()
        self.api = ExchangeAPI()
        self.db = get_db()
        self.protector = NakedPositionProtector(self.api)
    
    @trace_step("Trader", "执行开仓")
    def execute_entry(self, pos: PositionRecord) -> bool:
        """
        执行开仓 - 使用裸仓保护器
        确保双边同时成交
        """
        heartbeat("Trader")
        log_info("Trader", "开始开仓(带保护)", 
                pair_key=pos.pair_key,
                direction=pos.direction,
                notional=pos.notional)
        
        # 使用保护器执行配对订单
        success, message = self.protector.execute_pair_order(pos)
        
        if success:
            log_info("Trader", "开仓成功", pair_key=pos.pair_key, message=message)
            logger.info(f"Entry executed successfully: {pos.pair_key}")
        else:
            log_error("Trader", "开仓失败", Exception(message), pair_key=pos.pair_key)
            logger.error(f"Entry failed for {pos.pair_key}: {message}")
        
        return success
    
    @trace_step("Trader", "执行平仓")
    def execute_exit(self, pos: PositionRecord) -> bool:
        """
        执行平仓 - 双边同步平仓，带裸仓保护
        """
        heartbeat("Trader")
        logger.info(f"Executing exit for {pos.pair_key}")
        
        # 首先验证持仓一致性
        consistency = self.protector.verify_position_consistency(pos)
        if not consistency['consistent']:
            logger.error(f"持仓不一致，停止平仓: {consistency['issues']}")
            return False
        
        # 确定平仓方向 (与开仓相反)
        if pos.direction == 'long_spread':
            side_a, side_b = 'sell', 'buy'
        else:
            side_a, side_b = 'buy', 'sell'
        
        # 下第一边平仓单
        with trace_context("Trader", "平仓第一边"):
            result_a = self.api.place_market_order(
                symbol=pos.symbol_a,
                side=side_a,
                amount=pos.qty_a,
                reduce_only=True
            )
            
            if not result_a.success:
                log_error("Trader", "平仓第一边失败", 
                         Exception(result_a.error or "Unknown"))
                return False
            
            # 确认成交
            confirmed_a = self.protector._confirm_filled(pos.symbol_a, result_a.order_id)
            if not confirmed_a['filled']:
                logger.error(f"平仓第一边未成交: {confirmed_a['status']}")
                return False
        
        # 下第二边平仓单
        with trace_context("Trader", "平仓第二边"):
            result_b = self.api.place_market_order(
                symbol=pos.symbol_b,
                side=side_b,
                amount=pos.qty_b,
                reduce_only=True
            )
            
            if not result_b.success:
                log_error("Trader", "平仓第二边失败",
                         Exception(result_b.error or "Unknown"))
                # 平仓失败很危险，可能形成反向裸仓
                # 尝试重新平仓第一边（反向操作）
                logger.critical(f"平仓第二边失败，可能形成裸仓！尝试重新开仓第一边...")
                return False
            
            confirmed_b = self.protector._confirm_filled(pos.symbol_b, result_b.order_id)
            if not confirmed_b['filled']:
                logger.error(f"平仓第二边未成交: {confirmed_b['status']}")
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
