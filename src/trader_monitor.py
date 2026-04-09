#!/usr/bin/env python3
"""
Trader 模块监控集成示例
展示如何在 trader.py 中嵌入细粒度监控点
"""

from typing import Dict, Optional, Tuple
from chain_monitor import ChainMonitor, ChainStage, get_monitor
from dataclasses import dataclass


@dataclass
class OrderResult:
    """订单结果"""
    success: bool
    order_id: str
    filled_qty: float
    avg_price: float
    error: Optional[str] = None


class MonitoredTrader:
    """带监控的交易器"""
    
    def __init__(self, api_key: str, api_secret: str, monitor: Optional[ChainMonitor] = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.monitor = monitor or get_monitor()
    
    def execute_pair_trade(
        self,
        pair: str,
        side_a: str,
        qty_a: float,
        side_b: str,
        qty_b: float,
        symbol_a: str,
        symbol_b: str
    ) -> Tuple[OrderResult, OrderResult]:
        """
        执行配对交易（全流程监控）
        """
        # 1. 交易启动
        trace_id = self.monitor.start_trace(
            module="trader",
            stage=ChainStage.TRADER_START,
            pair=pair,
            metadata={"symbol_a": symbol_a, "symbol_b": symbol_b}
        )
        
        try:
            # 2. 风控检查
            self._risk_check(pair, trace_id)
            
            # 3. 查询余额
            balance_a = self._check_balance(symbol_a, qty_a, trace_id)
            balance_b = self._check_balance(symbol_b, qty_b, trace_id)
            
            # 4. 计算对冲
            hedge_ratio = self._calculate_hedge(pair, symbol_a, symbol_b, trace_id)
            
            # 5. 下单 A
            result_a = self._place_order(
                symbol=symbol_a,
                side=side_a,
                qty=qty_a,
                pair=pair,
                leg="A",
                parent_trace_id=trace_id
            )
            
            # 6. 下单 B
            result_b = self._place_order(
                symbol=symbol_b,
                side=side_b,
                qty=qty_b,
                pair=pair,
                leg="B",
                parent_trace_id=trace_id
            )
            
            # 7. 确认成交
            if result_a.success and result_b.success:
                self._confirm_fill(result_a.order_id, pair, "A", trace_id)
                self._confirm_fill(result_b.order_id, pair, "B", trace_id)
                
                # 8. 更新持仓
                self._update_position(pair, result_a, result_b, trace_id)
                
                # 完成
                self.monitor.end_trace(
                    trace_id,
                    status="success",
                    metadata={
                        "symbol_a": symbol_a,
                        "symbol_b": symbol_b,
                        "fill_a": result_a.filled_qty,
                        "fill_b": result_b.filled_qty,
                        "hedge_ratio": hedge_ratio
                    }
                )
            else:
                # 部分成交/失败
                error_msg = f"A: {result_a.error or 'OK'}, B: {result_b.error or 'OK'}"
                self.monitor.end_trace(
                    trace_id,
                    status="failure",
                    error=error_msg
                )
                # 触发回滚逻辑...
                self._rollback(result_a, result_b, trace_id)
            
            return result_a, result_b
            
        except Exception as e:
            self.monitor.end_trace(
                trace_id,
                status="failure",
                error=str(e)
            )
            raise
    
    def _risk_check(self, pair: str, parent_trace_id: str):
        """风控检查监控"""
        trace_id = self.monitor.start_trace(
            module="trader",
            stage=ChainStage.TRADER_VALIDATE,
            pair=pair,
            metadata={"parent": parent_trace_id}
        )
        
        try:
            # 检查最大持仓、日亏损限制等...
            self.monitor.end_trace(trace_id, status="success")
        except Exception as e:
            self.monitor.end_trace(trace_id, status="failure", error=str(e))
            raise
    
    def _check_balance(self, symbol: str, qty: float, parent_trace_id: str) -> float:
        """余额检查监控"""
        trace_id = self.monitor.start_trace(
            module="trader",
            stage=ChainStage.TRADER_VALIDATE,
            pair=symbol,
            metadata={"parent": parent_trace_id, "required": qty}
        )
        
        try:
            # 查询余额...
            balance = 1000.0  # 模拟
            
            if balance < qty:
                raise ValueError(f"余额不足: {balance} < {qty}")
            
            self.monitor.end_trace(
                trace_id,
                status="success",
                metadata={"available": balance}
            )
            return balance
            
        except Exception as e:
            self.monitor.end_trace(trace_id, status="failure", error=str(e))
            raise
    
    def _calculate_hedge(self, pair: str, sym_a: str, sym_b: str, 
                         parent_trace_id: str) -> float:
        """对冲计算监控"""
        trace_id = self.monitor.start_trace(
            module="trader",
            stage=ChainStage.TRADER_VALIDATE,
            pair=pair,
            metadata={"parent": parent_trace_id}
        )
        
        try:
            # 计算对冲比例...
            hedge_ratio = 1.0
            
            self.monitor.end_trace(
                trace_id,
                status="success",
                metadata={"hedge_ratio": hedge_ratio}
            )
            return hedge_ratio
            
        except Exception as e:
            self.monitor.end_trace(trace_id, status="failure", error=str(e))
            raise
    
    def _place_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        pair: str,
        leg: str,
        parent_trace_id: str
    ) -> OrderResult:
        """下单监控"""
        trace_id = self.monitor.start_trace(
            module="trader",
            stage=ChainStage.TRADER_ORDER,
            pair=pair,
            metadata={
                "parent": parent_trace_id,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "leg": leg
            }
        )
        
        try:
            # 执行下单...
            # 模拟成功
            result = OrderResult(
                success=True,
                order_id=f"order_{symbol}_{int(time.time())}",
                filled_qty=qty,
                avg_price=50000.0
            )
            
            self.monitor.end_trace(
                trace_id,
                status="success",
                metadata={
                    "order_id": result.order_id,
                    "filled": result.filled_qty,
                    "price": result.avg_price
                }
            )
            return result
            
        except Exception as e:
            self.monitor.end_trace(trace_id, status="failure", error=str(e))
            return OrderResult(
                success=False,
                order_id="",
                filled_qty=0,
                avg_price=0,
                error=str(e)
            )
    
    def _confirm_fill(self, order_id: str, pair: str, leg: str, parent_trace_id: str):
        """成交确认监控"""
        trace_id = self.monitor.start_trace(
            module="trader",
            stage=ChainStage.TRADER_CONFIRM,
            pair=pair,
            metadata={"parent": parent_trace_id, "order_id": order_id, "leg": leg}
        )
        
        try:
            # 轮询确认成交...
            self.monitor.end_trace(trace_id, status="success")
        except Exception as e:
            self.monitor.end_trace(trace_id, status="failure", error=str(e))
            raise
    
    def _update_position(self, pair: str, result_a: OrderResult, 
                         result_b: OrderResult, parent_trace_id: str):
        """持仓更新监控"""
        trace_id = self.monitor.start_trace(
            module="trader",
            stage=ChainStage.TRADER_CONFIRM,
            pair=pair,
            metadata={"parent": parent_trace_id}
        )
        
        try:
            # 更新本地持仓状态...
            self.monitor.end_trace(
                trace_id,
                status="success",
                metadata={
                    "position_a": result_a.filled_qty,
                    "position_b": result_b.filled_qty
                }
            )
        except Exception as e:
            self.monitor.end_trace(trace_id, status="failure", error=str(e))
            raise
    
    def _rollback(self, result_a: OrderResult, result_b: OrderResult, 
                  parent_trace_id: str):
        """回滚逻辑"""
        trace_id = self.monitor.start_trace(
            module="trader",
            stage=ChainStage.ERROR,
            metadata={"parent": parent_trace_id, "action": "rollback"}
        )
        
        try:
            # 平掉已成交的腿...
            self.monitor.end_trace(trace_id, status="success")
        except Exception as e:
            self.monitor.end_trace(trace_id, status="failure", error=str(e))


import time


def create_monitored_trader(api_key: str, api_secret: str, **kwargs) -> MonitoredTrader:
    """创建带监控的交易器"""
    return MonitoredTrader(api_key, api_secret, **kwargs)
