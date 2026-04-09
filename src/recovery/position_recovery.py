"""
持仓恢复系统
三层对账机制: 本地状态 ↔ 交易所状态 ↔ 策略预期
"""
import json
import os
import asyncio
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
import logging

logger = logging.getLogger(__name__)


class RecoveryLevel(Enum):
    """恢复等级"""
    A_AUTO = "A"        # 完全一致，自动恢复
    B_SEMI = "B"        # 有幽灵订单，半自动
    C_TAKEOVER = "C"    # 持仓不一致，人工接管
    D_EMERGENCY = "D"   # 严重错误，紧急处理


class SystemMode(Enum):
    """系统模式"""
    RECOVERY = "RECOVERY"    # 恢复中
    SAFE = "SAFE"            # 安全模式(只平仓)
    TRADING = "TRADING"      # 正常交易
    LOCKED = "LOCKED"        # 锁定(需人工)


@dataclass
class PositionSnapshot:
    """持仓快照"""
    symbol: str
    side: str
    quantity: float
    entry_price: float
    unrealized_pnl: float
    timestamp: str
    order_ids: List[str]  # 关联订单ID


@dataclass
class RecoveryResult:
    """恢复结果"""
    success: bool
    mode: SystemMode
    level: RecoveryLevel
    message: str
    issues: List[str]
    actions_taken: List[str]


class PositionRecovery:
    """
    持仓恢复系统
    
    功能:
    1. 本地/交易所状态对比
    2. 三层对账 (持仓/订单/保护)
    3. 自动/半自动/人工接管
    4. 幽灵订单检测与清理
    """
    
    def __init__(self, exchange, state_dir: str = "data/recovery"):
        self.exchange = exchange
        self.state_dir = state_dir
        os.makedirs(state_dir, exist_ok=True)
        
        self.system_mode = SystemMode.RECOVERY
        self.recovery_level = None
        
        # 状态缓存
        self.local_positions: Dict[str, PositionSnapshot] = {}
        self.exchange_positions: Dict[str, Dict] = {}
        self.exchange_orders: List[Dict] = []
        
        # 恢复统计
        self.ghost_orders_cancelled = 0
        self.positions_synced = 0
    
    async def run_recovery(self) -> RecoveryResult:
        """
        执行恢复流程
        
        Returns:
            RecoveryResult
        """
        logger.info("="*60)
        logger.info("🔄 持仓恢复系统启动")
        logger.info("="*60)
        
        issues = []
        actions = []
        
        try:
            # Step 1: 获取交易所状态
            logger.info("Step 1: 获取交易所状态...")
            await self._fetch_exchange_state()
            logger.info(f"  交易所持仓: {len(self.exchange_positions)} 对")
            logger.info(f"  交易所订单: {len(self.exchange_orders)} 个")
            
            # Step 2: 加载本地状态
            logger.info("Step 2: 加载本地状态...")
            self._load_local_state()
            logger.info(f"  本地持仓: {len(self.local_positions)} 对")
            
            # Step 3: 三层对账
            logger.info("Step 3: 执行三层对账...")
            
            # Layer 1: 持仓对账
            position_issues = self._reconcile_positions()
            issues.extend(position_issues)
            logger.info(f"  持仓问题: {len(position_issues)} 个")
            
            # Layer 2: 订单对账
            order_issues = self._reconcile_orders()
            issues.extend(order_issues)
            logger.info(f"  订单问题: {len(order_issues)} 个")
            
            # Layer 3: 保护订单对账
            protection_issues = self._reconcile_protection()
            issues.extend(protection_issues)
            logger.info(f"  保护订单问题: {len(protection_issues)} 个")
            
            # Step 4: 确定恢复等级
            self.recovery_level = self._determine_level(
                position_issues, order_issues, protection_issues
            )
            logger.info(f"Step 4: 恢复等级 = {self.recovery_level.value}")
            
            # Step 5: 执行恢复策略
            logger.info("Step 5: 执行恢复策略...")
            message = await self._execute_strategy(actions)
            logger.info(f"  结果: {message}")
            
            return RecoveryResult(
                success=True,
                mode=self.system_mode,
                level=self.recovery_level,
                message=message,
                issues=issues,
                actions_taken=actions
            )
            
        except Exception as e:
            logger.error(f"恢复过程出错: {e}")
            return RecoveryResult(
                success=False,
                mode=SystemMode.LOCKED,
                level=RecoveryLevel.D_EMERGENCY,
                message=f"恢复失败: {e}",
                issues=issues + [str(e)],
                actions_taken=actions
            )
    
    async def _fetch_exchange_state(self):
        """获取交易所状态"""
        # 获取持仓
        try:
            positions = await asyncio.to_thread(
                self.exchange.fetch_positions
            )
            
            for pos in positions:
                if pos.get('contracts', 0) != 0:
                    self.exchange_positions[pos['symbol']] = {
                        'symbol': pos['symbol'],
                        'side': 'long' if pos['contracts'] > 0 else 'short',
                        'qty': abs(pos['contracts']),
                        'entry_price': pos.get('entryPrice', 0),
                        'unrealized_pnl': pos.get('unrealizedPnl', 0)
                    }
        except Exception as e:
            logger.error(f"获取持仓失败: {e}")
        
        # 获取订单
        try:
            self.exchange_orders = await asyncio.to_thread(
                self.exchange.fetch_open_orders
            )
        except Exception as e:
            logger.error(f"获取订单失败: {e}")
            self.exchange_orders = []
    
    def _load_local_state(self):
        """加载本地状态"""
        state_file = os.path.join(self.state_dir, "positions.json")
        
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r') as f:
                    data = json.load(f)
                
                for sym, pos_data in data.get('positions', {}).items():
                    self.local_positions[sym] = PositionSnapshot(**pos_data)
                
                logger.info(f"加载了 {len(self.local_positions)} 个本地持仓")
            except Exception as e:
                logger.error(f"加载本地状态失败: {e}")
    
    def _reconcile_positions(self) -> List[str]:
        """
        持仓对账
        
        Returns:
            问题列表
        """
        issues = []
        
        # 检查本地 vs 交易所
        for sym, local in self.local_positions.items():
            exchange_pos = self.exchange_positions.get(sym)
            
            if not exchange_pos:
                issues.append(f"{sym}: 孤儿持仓 (本地有,交易所无)")
                continue
            
            # 检查方向
            if exchange_pos['side'] != local.side:
                issues.append(f"{sym}: 方向不一致 本地={local.side} 交易所={exchange_pos['side']}")
            
            # 检查数量 (允许1%误差)
            qty_diff_pct = abs(exchange_pos['qty'] - local.quantity) / max(local.quantity, 0.0001)
            if qty_diff_pct > 0.01:
                issues.append(f"{sym}: 数量不一致 本地={local.quantity:.4f} 交易所={exchange_pos['qty']:.4f}")
        
        # 检查幽灵持仓 (交易所有,本地无)
        for sym, exch_pos in self.exchange_positions.items():
            if sym not in self.local_positions:
                issues.append(f"{sym}: 幽灵持仓 (交易所{exch_pos['side']} {exch_pos['qty']:.4f},本地无)")
        
        return issues
    
    def _reconcile_orders(self) -> List[str]:
        """
        订单对账 (检测幽灵订单)
        
        Returns:
            问题列表
        """
        issues = []
        
        for order in self.exchange_orders:
            client_oid = order.get('clientOrderId', '')
            is_reduce = order.get('reduceOnly', False)
            
            # 策略订单以 s001_ 开头
            is_strategy = client_oid.startswith('s001_')
            
            # 幽灵订单: 非策略订单且非保护订单
            if not is_strategy and not is_reduce:
                issues.append(f"{order['symbol']}: 幽灵订单 {order['side']} {order['amount']} (ID: {client_oid or 'N/A'})")
        
        return issues
    
    def _reconcile_protection(self) -> List[str]:
        """
        保护订单对账
        
        Returns:
            问题列表
        """
        issues = []
        
        for sym, pos in self.exchange_positions.items():
            # 查找该持仓的保护订单
            protection_orders = [
                o for o in self.exchange_orders
                if o['symbol'] == sym and o.get('reduceOnly', False)
            ]
            
            protection_qty = sum(o['amount'] for o in protection_orders)
            
            # 保护数量应 >= 持仓数量
            if protection_qty < pos['qty'] * 0.99:
                missing_pct = (pos['qty'] - protection_qty) / pos['qty'] * 100
                issues.append(f"{sym}: 保护订单不足 持仓={pos['qty']:.4f} 保护={protection_qty:.4f} 缺少={missing_pct:.1f}%")
        
        return issues
    
    def _determine_level(self, position_issues: List[str],
                        order_issues: List[str],
                        protection_issues: List[str]) -> RecoveryLevel:
        """确定恢复等级"""
        # Level D: 严重问题
        if len(position_issues) > 3 or any('方向不一致' in i for i in position_issues):
            return RecoveryLevel.D_EMERGENCY
        
        # Level C: 持仓问题
        if position_issues:
            return RecoveryLevel.C_TAKEOVER
        
        # Level B: 只有幽灵订单
        if order_issues or protection_issues:
            return RecoveryLevel.B_SEMI
        
        # Level A: 一切正常
        return RecoveryLevel.A_AUTO
    
    async def _execute_strategy(self, actions: List[str]) -> str:
        """执行恢复策略"""
        
        if self.recovery_level == RecoveryLevel.A_AUTO:
            self.system_mode = SystemMode.TRADING
            actions.append("自动恢复完成")
            return "✅ 状态完全一致，自动恢复正常交易"
        
        elif self.recovery_level == RecoveryLevel.B_SEMI:
            # 清理幽灵订单
            cancelled = await self._cancel_ghost_orders()
            self.ghost_orders_cancelled = cancelled
            actions.append(f"清理幽灵订单: {cancelled}个")
            
            self.system_mode = SystemMode.TRADING
            return f"⚠️ 清理了 {cancelled} 个幽灵订单，恢复正常交易"
        
        elif self.recovery_level == RecoveryLevel.C_TAKEOVER:
            # 同步持仓到本地
            await self._sync_positions_to_local()
            actions.append("持仓已同步到本地")
            
            self.system_mode = SystemMode.SAFE
            return f"⚠️ 持仓已同步，进入安全模式(仅平仓)"
        
        elif self.recovery_level == RecoveryLevel.D_EMERGENCY:
            self.system_mode = SystemMode.LOCKED
            actions.append("系统已锁定")
            return "🚨 严重错误，系统已锁定，需人工处理"
        
        return "未知恢复等级"
    
    async def _cancel_ghost_orders(self) -> int:
        """清理幽灵订单"""
        cancelled = 0
        
        for order in self.exchange_orders:
            client_oid = order.get('clientOrderId', '')
            is_reduce = order.get('reduceOnly', False)
            
            if not client_oid.startswith('s001_') and not is_reduce:
                try:
                    await asyncio.to_thread(
                        self.exchange.cancel_order,
                        order['id'],
                        order['symbol']
                    )
                    cancelled += 1
                    logger.info(f"取消幽灵订单: {order['symbol']} {order['side']} {order['amount']}")
                except Exception as e:
                    logger.error(f"取消订单失败 {order['id']}: {e}")
        
        return cancelled
    
    async def _sync_positions_to_local(self):
        """将交易所持仓同步到本地"""
        for sym, pos in self.exchange_positions.items():
            snapshot = PositionSnapshot(
                symbol=sym,
                side=pos['side'],
                quantity=pos['qty'],
                entry_price=pos['entry_price'],
                unrealized_pnl=pos['unrealized_pnl'],
                timestamp=datetime.now().isoformat(),
                order_ids=[]
            )
            self.local_positions[sym] = snapshot
            self.positions_synced += 1
        
        # 保存到文件
        self._save_local_state()
    
    def _save_local_state(self):
        """保存本地状态"""
        state_file = os.path.join(self.state_dir, "positions.json")
        
        data = {
            'timestamp': datetime.now().isoformat(),
            'positions': {
                sym: asdict(pos) for sym, pos in self.local_positions.items()
            }
        }
        
        try:
            with open(state_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"保存本地状态失败: {e}")
    
    def save_snapshot(self):
        """手动保存持仓快照"""
        self._save_local_state()
        logger.info("持仓快照已保存")
    
    def get_recovery_report(self) -> Dict:
        """获取恢复报告"""
        return {
            'timestamp': datetime.now().isoformat(),
            'mode': self.system_mode.value,
            'level': self.recovery_level.value if self.recovery_level else None,
            'exchange_positions': len(self.exchange_positions),
            'local_positions': len(self.local_positions),
            'exchange_orders': len(self.exchange_orders),
            'ghost_orders_cancelled': self.ghost_orders_cancelled,
            'positions_synced': self.positions_synced
        }


# 便捷函数
async def run_position_recovery(exchange, state_dir: str = "data/recovery") -> RecoveryResult:
    """便捷运行恢复"""
    recovery = PositionRecovery(exchange, state_dir)
    return await recovery.run_recovery()


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    print("="*60)
    print("持仓恢复系统测试")
    print("="*60)
    
    # 模拟交易所
    class MockExchange:
        def fetch_positions(self):
            return [
                {'symbol': 'BTC/USDT', 'contracts': 0.1, 'entryPrice': 50000, 'unrealizedPnl': 100},
                {'symbol': 'ETH/USDT', 'contracts': -1.0, 'entryPrice': 3000, 'unrealizedPnl': -50}
            ]
        
        def fetch_open_orders(self):
            return [
                {'symbol': 'BTC/USDT', 'side': 'sell', 'amount': 0.1, 'clientOrderId': 's001_stop_001', 'reduceOnly': True},
                {'symbol': 'ETH/USDT', 'side': 'buy', 'amount': 1.0, 'clientOrderId': '', 'reduceOnly': False}  # 幽灵订单
            ]
        
        def cancel_order(self, order_id, symbol):
            print(f"  取消订单: {symbol} {order_id}")
            return True
    
    # 创建恢复系统
    exchange = MockExchange()
    recovery = PositionRecovery(exchange)
    
    # 模拟本地状态 (缺少ETH持仓)
    recovery.local_positions['BTC/USDT'] = PositionSnapshot(
        symbol='BTC/USDT',
        side='long',
        quantity=0.1,
        entry_price=50000,
        unrealized_pnl=100,
        timestamp='2026-04-09T00:00:00',
        order_ids=[]
    )
    
    # 运行恢复
    async def test():
        result = await recovery.run_recovery()
        
        print("\n" + "="*60)
        print("恢复结果:")
        print("="*60)
        print(f"成功: {result.success}")
        print(f"模式: {result.mode.value}")
        print(f"等级: {result.level.value}")
        print(f"消息: {result.message}")
        print(f"\n问题 ({len(result.issues)}):")
        for issue in result.issues:
            print(f"  - {issue}")
        print(f"\n执行动作 ({len(result.actions_taken)}):")
        for action in result.actions_taken:
            print(f"  - {action}")
    
    asyncio.run(test())
    
    print("\n" + "="*60)
