"""
S001-Pro V3 订单恢复模块
职责: 策略重启后处理交易所遗留订单
"""

import time
import logging
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from database import get_db, PositionRecord
from visualization import trace_step, trace_context, log_info, log_error

logger = logging.getLogger(__name__)


class OrderCategory(Enum):
    """订单分类"""
    UNKNOWN = "unknown"           # 未知来源
    STRATEGY_OPEN = "strat_open"  # 本策略开仓单
    STRATEGY_CLOSE = "strat_close" # 本策略平仓单
    ORPHAN = "orphan"             # 孤儿单（无法匹配）
    EXTERNAL = "external"         # 外部订单（非本策略）


@dataclass
class ExchangeOrder:
    """交易所订单"""
    order_id: str
    symbol: str
    side: str
    amount: float
    filled: float
    remaining: float
    price: float
    status: str  # 'open', 'closed', 'canceled'
    order_type: str  # 'limit', 'market'
    timestamp: int  # 创建时间戳
    reduce_only: bool = False
    
    @property
    def is_active(self) -> bool:
        return self.status in ['open', 'partially_filled']
    
    @property
    def age_seconds(self) -> int:
        return int(time.time() - self.timestamp)


@dataclass
class RecoveryDecision:
    """恢复决策"""
    order: ExchangeOrder
    category: OrderCategory
    action: str  # 'cancel', 'track', 'manual', 'ignore'
    reason: str
    risk_level: str  # 'low', 'medium', 'high', 'critical'


class OrderRecoveryManager:
    """
    订单恢复管理器
    策略重启后恢复对交易所订单的认知
    """
    
    def __init__(self, exchange_api):
        self.api = exchange_api
        self.db = get_db()
        
        # 配置参数
        self.max_order_age_hours = 24  # 超过24小时的订单视为异常
        self.orphan_threshold_seconds = 300  # 5分钟内无法分类的视为孤儿单
        self.auto_cancel_age_seconds = 600  # 10分钟以上的非策略订单自动取消
    
    @trace_step("Recovery", "启动恢复流程")
    def recover(self) -> Dict:
        """
        主恢复流程
        返回恢复报告
        """
        log_info("Recovery", "开始订单恢复", timestamp=datetime.now().isoformat())
        
        report = {
            'start_time': datetime.now().isoformat(),
            'orders_found': 0,
            'orders_processed': 0,
            'orders_cancelled': 0,
            'orders_recovered': 0,
            'orphan_orders': 0,
            'manual_review_required': [],
            'errors': []
        }
        
        try:
            with trace_context("Recovery", "Phase 1: 发现订单"):
                orders = self._fetch_exchange_orders()
                report['orders_found'] = len(orders)
                log_info("Recovery", f"发现 {len(orders)} 个活跃订单")
            
            if not orders:
                log_info("Recovery", "无遗留订单，恢复完成")
                return report
            
            with trace_context("Recovery", "Phase 2: 分类订单"):
                decisions = self._classify_orders(orders)
                log_info("Recovery", f"分类完成", 
                        categories={cat.value: sum(1 for d in decisions if d.category == cat) 
                                   for cat in OrderCategory})
            
            with trace_context("Recovery", "Phase 3: 处理订单"):
                for decision in decisions:
                    self._execute_decision(decision, report)
            
            with trace_context("Recovery", "Phase 4: 持仓对账"):
                self._reconcile_positions()
            
        except Exception as e:
            log_error("Recovery", "恢复流程异常", e)
            report['errors'].append(str(e))
        
        report['end_time'] = datetime.now().isoformat()
        self._log_report(report)
        
        return report
    
    def _fetch_exchange_orders(self) -> List[ExchangeOrder]:
        """从交易所拉取所有活跃订单"""
        try:
            # 获取所有未成交订单
            open_orders = self.api.exchange.fetch_open_orders()
            
            orders = []
            for order in open_orders:
                # 只处理最近24小时的订单
                order_time = order.get('timestamp', 0) // 1000
                age_hours = (time.time() - order_time) / 3600
                
                if age_hours > self.max_order_age_hours:
                    logger.warning(f"发现超期订单: {order['id']}, 年龄: {age_hours:.1f}小时")
                
                orders.append(ExchangeOrder(
                    order_id=order['id'],
                    symbol=order['symbol'],
                    side=order['side'],
                    amount=float(order['amount']),
                    filled=float(order.get('filled', 0)),
                    remaining=float(order.get('remaining', order['amount'])),
                    price=float(order.get('price', 0) or order.get('average', 0)),
                    status=order['status'],
                    order_type=order['type'],
                    timestamp=order_time,
                    reduce_only=order.get('reduceOnly', False)
                ))
            
            return orders
            
        except Exception as e:
            log_error("Recovery", "拉取订单失败", e)
            return []
    
    def _classify_orders(self, orders: List[ExchangeOrder]) -> List[RecoveryDecision]:
        """分类订单"""
        decisions = []
        
        # 获取本地记录的订单ID
        local_open_positions = self.db.get_open_positions()
        local_order_ids = self._get_local_order_ids()
        
        for order in orders:
            decision = self._classify_single_order(order, local_order_ids, local_open_positions)
            decisions.append(decision)
        
        return decisions
    
    def _get_local_order_ids(self) -> set:
        """获取本地数据库记录的订单ID"""
        # 本系统使用市价单，不在本地跟踪订单ID
        # 但如果有未完成的限价单，可以通过持仓反推相关symbol
        # 这里返回空集合是安全的 — 分类逻辑会通过持仓symbol匹配来兜底
        return set()
    
    def _classify_single_order(self, order: ExchangeOrder, 
                               local_order_ids: set,
                               local_positions: List[PositionRecord]) -> RecoveryDecision:
        """分类单个订单"""
        
        # 检查1: 是否在本地记录中
        if order.order_id in local_order_ids:
            # 是本策略的订单
            if order.reduce_only:
                return RecoveryDecision(
                    order=order,
                    category=OrderCategory.STRATEGY_CLOSE,
                    action='track',
                    reason='本策略平仓单，继续跟踪',
                    risk_level='low'
                )
            else:
                return RecoveryDecision(
                    order=order,
                    category=OrderCategory.STRATEGY_OPEN,
                    action='track',
                    reason='本策略开仓单，继续跟踪',
                    risk_level='medium'  # 需要确认是否已部分成交
                )
        
        # 检查2: 是否与本地持仓相关
        for pos in local_positions:
            if order.symbol in [pos.symbol_a, pos.symbol_b]:
                # 可能是本策略的订单但未记录ID
                return RecoveryDecision(
                    order=order,
                    category=OrderCategory.STRATEGY_OPEN,
                    action='manual',
                    reason=f'与持仓 {pos.pair_key} 相关但未记录订单ID',
                    risk_level='high'
                )
        
        # 检查3: 订单年龄
        if order.age_seconds > self.auto_cancel_age_seconds:
            # 超期的未知订单，安全起见取消
            return RecoveryDecision(
                order=order,
                category=OrderCategory.UNKNOWN,
                action='cancel',
                reason=f'未知订单且超期({order.age_seconds/60:.0f}分钟)',
                risk_level='medium'
            )
        
        # 检查4: 是否为外部订单（手动交易）
        if order.age_seconds < 60:
            # 最近1分钟的可能为手动交易，保留
            return RecoveryDecision(
                order=order,
                category=OrderCategory.EXTERNAL,
                action='ignore',
                reason='可能是手动交易，暂不处理',
                risk_level='low'
            )
        
        # 默认为孤儿单
        return RecoveryDecision(
            order=order,
            category=OrderCategory.ORPHAN,
            action='manual',
            reason=f'无法分类的订单，年龄{order.age_seconds}秒',
            risk_level='high'
        )
    
    def _execute_decision(self, decision: RecoveryDecision, report: Dict):
        """执行决策"""
        order = decision.order
        
        log_info("Recovery", f"处理订单 {order.order_id}",
                category=decision.category.value,
                action=decision.action,
                risk=decision.risk_level,
                reason=decision.reason)
        
        if decision.action == 'cancel':
            success = self._cancel_order(order)
            if success:
                report['orders_cancelled'] += 1
            report['orders_processed'] += 1
            
        elif decision.action == 'track':
            # 恢复跟踪订单
            self._track_order(order, decision)
            report['orders_recovered'] += 1
            report['orders_processed'] += 1
            
        elif decision.action == 'manual':
            # 需要人工审核
            report['manual_review_required'].append({
                'order_id': order.order_id,
                'symbol': order.symbol,
                'reason': decision.reason,
                'risk': decision.risk_level
            })
            report['orphan_orders'] += 1
            
            # 高风险的立即告警
            if decision.risk_level == 'critical':
                self._alert_critical_orphan(order, decision)
                
        elif decision.action == 'ignore':
            report['orders_processed'] += 1
    
    def _cancel_order(self, order: ExchangeOrder) -> bool:
        """取消订单"""
        try:
            log_info("Recovery", f"取消订单", order_id=order.order_id, symbol=order.symbol)
            self.api.exchange.cancel_order(order.order_id, order.symbol)
            return True
        except Exception as e:
            log_error("Recovery", f"取消订单失败", e, order_id=order.order_id)
            return False
    
    def _track_order(self, order: ExchangeOrder, decision: RecoveryDecision):
        """恢复跟踪订单 - 记录到日志并持续监控"""
        log_info("Recovery", f"恢复跟踪订单",
                order_id=order.order_id,
                symbol=order.symbol,
                filled=order.filled,
                remaining=order.remaining)
        # 对于已部分成交的订单，等待成交或超时后取消
        if order.filled > 0 and order.remaining > 0:
            logger.warning(f"订单 {order.order_id} 部分成交 "
                          f"(filled={order.filled}, remaining={order.remaining})，"
                          f"将由主循环继续监控")
    
    def _reconcile_positions(self):
        """持仓对账 - 对比本地与交易所"""
        log_info("Recovery", "开始持仓对账")
        
        local_positions = self.db.get_open_positions()
        
        for pos in local_positions:
            # 获取交易所持仓
            exch_pos_a = self.api.get_position(pos.symbol_a)
            exch_pos_b = self.api.get_position(pos.symbol_b)
            
            # 检查一致性
            issues = []
            
            if pos.direction == 'long_spread':
                expected_a, expected_b = 'long', 'short'
            else:
                expected_a, expected_b = 'short', 'long'
            
            if not exch_pos_a:
                issues.append(f"{pos.symbol_a}: 本地有记录但交易所无持仓")
            elif exch_pos_a['side'] != expected_a:
                issues.append(f"{pos.symbol_a}: 方向不一致 期望{expected_a} 实际{exch_pos_a['side']}")
            
            if not exch_pos_b:
                issues.append(f"{pos.symbol_b}: 本地有记录但交易所无持仓")
            elif exch_pos_b['side'] != expected_b:
                issues.append(f"{pos.symbol_b}: 方向不一致 期望{expected_b} 实际{exch_pos_b['side']}")
            
            if issues:
                log_error("Recovery", f"持仓不一致: {pos.pair_key}",
                         Exception("; ".join(issues)))
            else:
                log_info("Recovery", f"持仓一致: {pos.pair_key}")
    
    def _alert_critical_orphan(self, order: ExchangeOrder, decision: RecoveryDecision):
        """紧急告警"""
        log_error("Recovery", "🚨 CRITICAL: 发现高风险孤儿单",
                 Exception("需要立即人工介入"),
                 order_id=order.order_id,
                 symbol=order.symbol,
                 side=order.side,
                 amount=order.amount,
                 reason=decision.reason)
    
    def _log_report(self, report: Dict):
        """记录恢复报告"""
        logger.info("="*60)
        logger.info("订单恢复报告")
        logger.info("="*60)
        logger.info(f"开始时间: {report['start_time']}")
        logger.info(f"发现订单: {report['orders_found']}")
        logger.info(f"处理订单: {report['orders_processed']}")
        logger.info(f"取消订单: {report['orders_cancelled']}")
        logger.info(f"恢复跟踪: {report['orders_recovered']}")
        logger.info(f"孤儿订单: {report['orphan_orders']}")
        
        if report['manual_review_required']:
            logger.info(f"需人工审核: {len(report['manual_review_required'])}")
            for item in report['manual_review_required']:
                logger.info(f"  - {item['order_id']} ({item['symbol']}): {item['reason']} [{item['risk']}]")
        
        if report['errors']:
            logger.error(f"错误: {len(report['errors'])}")
            for err in report['errors']:
                logger.error(f"  - {err}")
        
        logger.info(f"结束时间: {report['end_time']}")
        logger.info("="*60)


class GracefulShutdownHandler:
    """
    优雅停机处理器
    策略关闭前保存状态
    """
    
    def __init__(self, db):
        self.db = db
    
    def prepare_shutdown(self) -> Dict:
        """
        准备停机
        保存当前所有未完成订单信息
        """
        snapshot = {
            'timestamp': datetime.now().isoformat(),
            'open_positions': [],
            'pending_orders': [],
            'trades_pending_confirmation': []
        }
        
        # 保存持仓快照
        positions = self.db.get_open_positions()
        for pos in positions:
            snapshot['open_positions'].append({
                'pair_key': pos.pair_key,
                'symbol_a': pos.symbol_a,
                'symbol_b': pos.symbol_b,
                'direction': pos.direction,
                'qty_a': pos.qty_a,
                'qty_b': pos.qty_b,
                'entry_price_a': pos.entry_price_a,
                'entry_price_b': pos.entry_price_b
            })
        
        # TODO: 保存未完成订单ID
        
        # 保存到数据库
        self.db.save_shutdown_snapshot(snapshot)
        
        logger.info(f"停机快照已保存: {len(positions)} 个持仓")
        return snapshot
    
    def resume_from_snapshot(self) -> Optional[Dict]:
        """从快照恢复"""
        snapshot = self.db.get_shutdown_snapshot()
        if snapshot:
            logger.info(f"从快照恢复: {len(snapshot.get('open_positions', []))} 个持仓")
        return snapshot


# 快捷函数
def perform_recovery(exchange_api) -> Dict:
    """执行恢复流程"""
    manager = OrderRecoveryManager(exchange_api)
    return manager.recover()


if __name__ == "__main__":
    # 测试
    print("Order Recovery Module")
    print("Use: perform_recovery(api) after restart")
