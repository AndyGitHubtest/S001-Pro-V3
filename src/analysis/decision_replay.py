"""
决策回放系统
记录决策上下文，支持策略回放任
"""
import json
import gzip
import hashlib
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
import sqlite3
import logging

logger = logging.getLogger(__name__)


@dataclass
class DecisionContext:
    """决策上下文"""
    timestamp: str
    decision_id: str
    symbol: str
    decision: str  # 'enter_long', 'enter_short', 'exit', 'hold'
    
    # 市场数据
    price: float
    volume: float
    
    # 信号数据
    zscore: float
    spread: float
    hedge_ratio: float
    
    # 技术指标
    technical_indicators: Dict[str, float]
    
    # 策略参数
    strategy_params: Dict[str, Any]
    
    # 市场状态
    market_regime: str  # 'trending', 'ranging', 'volatile'
    volatility: float
    
    # 账户状态
    position_before: float
    available_margin: float
    
    # 决策依据
    reason: str
    confidence: float  # 0-1
    
    # 结果 (后续填充)
    outcome: str = None  # 'success', 'failure', 'pending'
    realized_pnl: float = 0.0
    exit_timestamp: str = None


class DecisionRecorder:
    """
    决策记录器
    
    记录每次交易决策的完整上下文
    """
    
    def __init__(self, db_path: str = "data/decisions.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.conn = sqlite3.connect(str(self.db_path))
        self._init_table()
    
    def _init_table(self):
        """初始化数据库表"""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY,
                timestamp TEXT,
                symbol TEXT,
                decision TEXT,
                price REAL,
                volume REAL,
                zscore REAL,
                spread REAL,
                hedge_ratio REAL,
                technical_indicators TEXT,
                strategy_params TEXT,
                market_regime TEXT,
                volatility REAL,
                position_before REAL,
                available_margin REAL,
                reason TEXT,
                confidence REAL,
                outcome TEXT,
                realized_pnl REAL,
                exit_timestamp TEXT,
                raw_context TEXT
            )
        """)
        self.conn.commit()
    
    def record(self, context: DecisionContext):
        """记录决策"""
        try:
            self.conn.execute("""
                INSERT INTO decisions VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, (
                context.decision_id,
                context.timestamp,
                context.symbol,
                context.decision,
                context.price,
                context.volume,
                context.zscore,
                context.spread,
                context.hedge_ratio,
                json.dumps(context.technical_indicators),
                json.dumps(context.strategy_params),
                context.market_regime,
                context.volatility,
                context.position_before,
                context.available_margin,
                context.reason,
                context.confidence,
                context.outcome,
                context.realized_pnl,
                context.exit_timestamp,
                json.dumps(asdict(context))
            ))
            self.conn.commit()
            
            logger.debug(f"决策已记录: {context.decision_id}")
            
        except Exception as e:
            logger.error(f"记录决策失败: {e}")
    
    def update_outcome(self, decision_id: str, outcome: str, 
                      pnl: float, exit_time: str = None):
        """更新决策结果"""
        try:
            self.conn.execute("""
                UPDATE decisions 
                SET outcome = ?, realized_pnl = ?, exit_timestamp = ?
                WHERE id = ?
            """, (outcome, pnl, exit_time or datetime.now().isoformat(), decision_id))
            self.conn.commit()
            
        except Exception as e:
            logger.error(f"更新决策结果失败: {e}")
    
    def get_decision(self, decision_id: str) -> Optional[DecisionContext]:
        """获取单个决策"""
        cursor = self.conn.execute(
            "SELECT raw_context FROM decisions WHERE id = ?", (decision_id,)
        )
        row = cursor.fetchone()
        
        if row:
            data = json.loads(row[0])
            return DecisionContext(**data)
        return None
    
    def get_decisions(self, symbol: str = None, start_time: str = None,
                     end_time: str = None, limit: int = 100) -> List[DecisionContext]:
        """获取决策列表"""
        query = "SELECT raw_context FROM decisions WHERE 1=1"
        params = []
        
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time)
        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time)
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        cursor = self.conn.execute(query, params)
        rows = cursor.fetchall()
        
        return [DecisionContext(**json.loads(row[0])) for row in rows]
    
    def get_statistics(self) -> Dict:
        """获取决策统计"""
        stats = {}
        
        # 总决策数
        cursor = self.conn.execute("SELECT COUNT(*) FROM decisions")
        stats['total_decisions'] = cursor.fetchone()[0]
        
        # 各类型决策数
        cursor = self.conn.execute(
            "SELECT decision, COUNT(*) FROM decisions GROUP BY decision"
        )
        stats['by_decision'] = {row[0]: row[1] for row in cursor.fetchall()}
        
        # 胜率统计
        cursor = self.conn.execute(
            "SELECT outcome, COUNT(*) FROM decisions WHERE outcome IS NOT NULL GROUP BY outcome"
        )
        stats['by_outcome'] = {row[0]: row[1] for row in cursor.fetchall()}
        
        # 平均收益
        cursor = self.conn.execute(
            "SELECT AVG(realized_pnl) FROM decisions WHERE realized_pnl IS NOT NULL"
        )
        stats['avg_pnl'] = cursor.fetchone()[0] or 0
        
        return stats


class DecisionReplay:
    """
    决策回放器
    
    回放历史决策，分析策略表现
    """
    
    def __init__(self, recorder: DecisionRecorder):
        self.recorder = recorder
    
    def replay_decision(self, decision_id: str) -> Dict:
        """
        回放单个决策
        
        Returns:
            回放分析结果
        """
        decision = self.recorder.get_decision(decision_id)
        if not decision:
            return {'error': '决策不存在'}
        
        replay = {
            'decision_id': decision_id,
            'timestamp': decision.timestamp,
            'symbol': decision.symbol,
            'decision': decision.decision,
            'context': {
                'price': decision.price,
                'zscore': decision.zscore,
                'market_regime': decision.market_regime,
                'confidence': decision.confidence,
                'reason': decision.reason
            },
            'outcome': {
                'status': decision.outcome,
                'pnl': decision.realized_pnl,
                'exit_time': decision.exit_timestamp
            },
            'analysis': self._analyze_decision(decision)
        }
        
        return replay
    
    def _analyze_decision(self, decision: DecisionContext) -> Dict:
        """分析决策质量"""
        analysis = {
            'entry_timing_score': 0,
            'confidence_accuracy': 0,
            'regime_alignment': 0
        }
        
        # 入场时机评分
        if decision.zscore and abs(decision.zscore) > 2:
            analysis['entry_timing_score'] = min(abs(decision.zscore) / 3 * 100, 100)
        
        # 置信度准确度
        if decision.outcome:
            success = decision.outcome == 'success'
            confidence = decision.confidence
            
            if success and confidence > 0.7:
                analysis['confidence_accuracy'] = 100
            elif not success and confidence < 0.3:
                analysis['confidence_accuracy'] = 100
            else:
                analysis['confidence_accuracy'] = 50
        
        # 市场状态匹配度
        if decision.market_regime == 'ranging' and decision.decision in ['enter_long', 'enter_short']:
            analysis['regime_alignment'] = 90  # 震荡市适合做均值回归
        elif decision.market_regime == 'trending':
            analysis['regime_alignment'] = 60  # 趋势市均值回归效果一般
        
        return analysis
    
    def batch_replay(self, symbol: str = None, start_time: str = None,
                    end_time: str = None) -> Dict:
        """批量回放"""
        decisions = self.recorder.get_decisions(symbol, start_time, end_time, limit=1000)
        
        replays = []
        for decision in decisions:
            replay = self.replay_decision(decision.decision_id)
            replays.append(replay)
        
        # 汇总分析
        summary = {
            'total': len(replays),
            'by_decision': {},
            'win_rate': 0,
            'avg_pnl': 0,
            'score_distribution': {
                'excellent': 0,  # >80
                'good': 0,       # 60-80
                'average': 0,    # 40-60
                'poor': 0        # <40
            }
        }
        
        total_pnl = 0
        wins = 0
        
        for replay in replays:
            decision_type = replay['decision']
            if decision_type not in summary['by_decision']:
                summary['by_decision'][decision_type] = {
                    'count': 0, 'wins': 0, 'total_pnl': 0
                }
            
            summary['by_decision'][decision_type]['count'] += 1
            
            if replay['outcome']['status'] == 'success':
                summary['by_decision'][decision_type]['wins'] += 1
                wins += 1
            
            pnl = replay['outcome']['pnl'] or 0
            summary['by_decision'][decision_type]['total_pnl'] += pnl
            total_pnl += pnl
            
            # 评分分布
            score = replay['analysis'].get('entry_timing_score', 0)
            if score > 80:
                summary['score_distribution']['excellent'] += 1
            elif score > 60:
                summary['score_distribution']['good'] += 1
            elif score > 40:
                summary['score_distribution']['average'] += 1
            else:
                summary['score_distribution']['poor'] += 1
        
        if replays:
            summary['win_rate'] = wins / len(replays) * 100
            summary['avg_pnl'] = total_pnl / len(replays)
        
        return {
            'replays': replays[:100],  # 只返回前100个详情
            'summary': summary
        }
    
    def export_replay(self, output_path: str, symbol: str = None,
                     start_time: str = None, end_time: str = None):
        """导出回放到文件"""
        result = self.batch_replay(symbol, start_time, end_time)
        
        with open(output_path, 'w') as f:
            json.dump(result, f, indent=2, default=str)
        
        logger.info(f"回放已导出: {output_path}")


def create_decision_context(
    symbol: str,
    decision: str,
    price: float,
    zscore: float,
    spread: float,
    **kwargs
) -> DecisionContext:
    """便捷创建决策上下文"""
    return DecisionContext(
        timestamp=datetime.now().isoformat(),
        decision_id=hashlib.md5(f"{symbol}{time.time()}".encode()).hexdigest()[:16],
        symbol=symbol,
        decision=decision,
        price=price,
        volume=kwargs.get('volume', 0),
        zscore=zscore,
        spread=spread,
        hedge_ratio=kwargs.get('hedge_ratio', 1.0),
        technical_indicators=kwargs.get('technical_indicators', {}),
        strategy_params=kwargs.get('strategy_params', {}),
        market_regime=kwargs.get('market_regime', 'unknown'),
        volatility=kwargs.get('volatility', 0),
        position_before=kwargs.get('position_before', 0),
        available_margin=kwargs.get('available_margin', 0),
        reason=kwargs.get('reason', ''),
        confidence=kwargs.get('confidence', 0.5)
    )


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    print("="*60)
    print("决策回放系统测试")
    print("="*60)
    
    # 创建记录器
    recorder = DecisionRecorder("test_decisions.db")
    
    # 记录一些决策
    print("\n1. 记录决策")
    for i in range(10):
        context = create_decision_context(
            symbol="BTC/USDT",
            decision="enter_long" if i % 2 == 0 else "enter_short",
            price=50000 + i * 100,
            zscore=2.5 if i % 2 == 0 else -2.5,
            spread=100,
            confidence=0.8,
            reason=f"Z-Score触发 {i}"
        )
        recorder.record(context)
        
        # 模拟结果
        if i < 8:
            outcome = 'success' if i % 3 != 0 else 'failure'
            pnl = 100 if outcome == 'success' else -50
            recorder.update_outcome(context.decision_id, outcome, pnl)
    
    print(f"  已记录10个决策")
    
    # 统计
    print("\n2. 决策统计")
    stats = recorder.get_statistics()
    print(f"  总决策数: {stats['total_decisions']}")
    print(f"  按类型: {stats['by_decision']}")
    print(f"  按结果: {stats['by_outcome']}")
    print(f"  平均收益: {stats['avg_pnl']:.2f}")
    
    # 回放
    print("\n3. 决策回放")
    replay = DecisionReplay(recorder)
    result = replay.batch_replay()
    
    print(f"  回放数量: {result['summary']['total']}")
    print(f"  胜率: {result['summary']['win_rate']:.1f}%")
    print(f"  平均收益: {result['summary']['avg_pnl']:.2f}")
    print(f"  评分分布: {result['summary']['score_distribution']}")
    
    # 导出
    print("\n4. 导出回放")
    replay.export_replay("test_replay.json")
    
    # 清理
    import os
    os.remove("test_decisions.db")
    os.remove("test_replay.json")
    
    print("\n" + "="*60)
