"""
交易对维护系统
自动检测并剔除失效交易对
"""
import asyncio
import json
from datetime import datetime, timedelta
from typing import Dict, List, Set, Optional
from dataclasses import dataclass, asdict
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class PairStatus(Enum):
    """交易对状态"""
    ACTIVE = "active"           # 活跃
    SUSPICIOUS = "suspicious"   # 可疑
    DELISTED = "delisted"       # 已下架
    LOW_LIQUIDITY = "low_liquidity"  # 低流动性
    STALE_DATA = "stale_data"   # 数据陈旧


@dataclass
class PairHealth:
    """交易对健康度"""
    symbol: str
    status: PairStatus
    last_trade_time: Optional[datetime]
    daily_volume: float
    spread_pct: float
    data_freshness_minutes: int
    score: float  # 0-100
    issues: List[str]


class PairMaintenance:
    """
    交易对维护系统
    
    功能:
    1. 健康度检查
    2. 失效交易对检测
    3. 自动剔除
    4. 维护报告
    """
    
    def __init__(self, exchange, config: Dict = None):
        self.exchange = exchange
        self.config = config or {}
        
        # 默认阈值
        self.thresholds = {
            'min_daily_volume': self.config.get('min_daily_volume', 1000000),  # $1M
            'max_spread_pct': self.config.get('max_spread_pct', 0.005),        # 0.5%
            'max_stale_minutes': self.config.get('max_stale_minutes', 30),
            'min_health_score': self.config.get('min_health_score', 60)
        }
        
        # 维护记录
        self.maintenance_history: List[Dict] = []
        self.removed_pairs: Set[str] = set()
    
    async def check_pair_health(self, symbol: str) -> PairHealth:
        """检查单个交易对健康度"""
        issues = []
        score = 100
        
        try:
            # 获取ticker信息
            ticker = await asyncio.to_thread(
                self.exchange.fetch_ticker, symbol
            )
            
            # 检查成交量
            daily_volume = ticker.get('quoteVolume', 0)
            if daily_volume < self.thresholds['min_daily_volume']:
                issues.append(f"成交量过低: ${daily_volume:,.0f}")
                score -= 20
            
            # 检查价差
            bid = ticker.get('bid', 0)
            ask = ticker.get('ask', 0)
            if bid > 0 and ask > 0:
                spread_pct = (ask - bid) / ((ask + bid) / 2)
                if spread_pct > self.thresholds['max_spread_pct']:
                    issues.append(f"价差过大: {spread_pct:.2%}")
                    score -= 15
            else:
                spread_pct = 0
                issues.append("无法获取买卖价")
                score -= 10
            
            # 检查数据新鲜度
            timestamp = ticker.get('timestamp')
            if timestamp:
                last_trade = datetime.fromtimestamp(timestamp / 1000)
                freshness = (datetime.now() - last_trade).total_seconds() / 60
            else:
                last_trade = None
                freshness = 999
                issues.append("无时间戳数据")
                score -= 10
            
            if freshness > self.thresholds['max_stale_minutes']:
                issues.append(f"数据陈旧: {freshness:.0f}分钟")
                score -= 25
            
            # 确定状态
            if score >= 80:
                status = PairStatus.ACTIVE
            elif score >= self.thresholds['min_health_score']:
                status = PairStatus.SUSPICIOUS
            else:
                status = PairStatus.LOW_LIQUIDITY
            
            return PairHealth(
                symbol=symbol,
                status=status,
                last_trade_time=last_trade,
                daily_volume=daily_volume,
                spread_pct=spread_pct if bid > 0 and ask > 0 else 0,
                data_freshness_minutes=int(freshness),
                score=max(0, score),
                issues=issues
            )
            
        except Exception as e:
            logger.error(f"检查{symbol}健康度失败: {e}")
            return PairHealth(
                symbol=symbol,
                status=PairStatus.DELISTED,
                last_trade_time=None,
                daily_volume=0,
                spread_pct=0,
                data_freshness_minutes=999,
                score=0,
                issues=[f"检查失败: {e}"]
            )
    
    async def check_all_pairs(self, symbols: List[str]) -> Dict[str, PairHealth]:
        """检查所有交易对"""
        logger.info(f"开始检查 {len(symbols)} 个交易对...")
        
        results = {}
        for symbol in symbols:
            health = await self.check_pair_health(symbol)
            results[symbol] = health
            
            if health.status != PairStatus.ACTIVE:
                logger.warning(f"{symbol}: {health.status.value} (分数: {health.score})")
        
        return results
    
    def get_pairs_to_remove(self, health_results: Dict[str, PairHealth]) -> List[str]:
        """获取需要剔除的交易对"""
        to_remove = []
        
        for symbol, health in health_results.items():
            if health.status in [PairStatus.DELISTED, PairStatus.LOW_LIQUIDITY]:
                to_remove.append(symbol)
            elif health.status == PairStatus.STALE_DATA:
                to_remove.append(symbol)
        
        return to_remove
    
    async def run_maintenance(self, symbols: List[str]) -> Dict:
        """运行维护流程"""
        logger.info("="*60)
        logger.info("🔧 交易对维护开始")
        logger.info("="*60)
        
        # 检查所有交易对
        health_results = await self.check_all_pairs(symbols)
        
        # 确定需要剔除的
        to_remove = self.get_pairs_to_remove(health_results)
        
        # 执行剔除
        removed = []
        for symbol in to_remove:
            self.removed_pairs.add(symbol)
            removed.append({
                'symbol': symbol,
                'reason': health_results[symbol].status.value,
                'issues': health_results[symbol].issues
            })
            logger.info(f"剔除交易对: {symbol} ({health_results[symbol].status.value})")
        
        # 记录维护历史
        maintenance_record = {
            'timestamp': datetime.now().isoformat(),
            'total_checked': len(symbols),
            'active': sum(1 for h in health_results.values() if h.status == PairStatus.ACTIVE),
            'suspicious': sum(1 for h in health_results.values() if h.status == PairStatus.SUSPICIOUS),
            'removed': len(removed),
            'removed_pairs': removed
        }
        self.maintenance_history.append(maintenance_record)
        
        logger.info(f"维护完成: {len(symbols)} 检查, {len(removed)} 剔除")
        
        return {
            'health_results': health_results,
            'removed': removed,
            'record': maintenance_record
        }
    
    def generate_report(self) -> str:
        """生成维护报告"""
        report = []
        report.append("="*60)
        report.append("交易对维护报告")
        report.append("="*60)
        report.append(f"\n累计维护次数: {len(self.maintenance_history)}")
        report.append(f"累计剔除交易对: {len(self.removed_pairs)}")
        
        if self.maintenance_history:
            latest = self.maintenance_history[-1]
            report.append(f"\n最近维护: {latest['timestamp']}")
            report.append(f"  检查: {latest['total_checked']}")
            report.append(f"  活跃: {latest['active']}")
            report.append(f"  可疑: {latest['suspicious']}")
            report.append(f"  剔除: {latest['removed']}")
        
        if self.removed_pairs:
            report.append(f"\n已剔除交易对:")
            for symbol in list(self.removed_pairs)[-10:]:
                report.append(f"  - {symbol}")
        
        report.append("="*60)
        
        return '\n'.join(report)


# 便捷函数
async def maintain_pairs(exchange, symbols: List[str], config: Dict = None) -> Dict:
    """便捷维护交易对"""
    maintainer = PairMaintenance(exchange, config)
    return await maintainer.run_maintenance(symbols)


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    print("="*60)
    print("交易对维护系统测试")
    print("="*60)
    
    # 模拟交易所
    class MockExchange:
        def fetch_ticker(self, symbol):
            import random
            return {
                'symbol': symbol,
                'bid': 50000,
                'ask': 50025,
                'quoteVolume': random.uniform(500000, 2000000),
                'timestamp': int(datetime.now().timestamp() * 1000)
            }
    
    exchange = MockExchange()
    maintainer = PairMaintenance(exchange)
    
    # 测试检查
    async def test():
        symbols = ['BTC/USDT', 'ETH/USDT', 'XXX/USD']  # XXX会失败
        
        print("\n1. 检查交易对健康度")
        for symbol in symbols:
            health = await maintainer.check_pair_health(symbol)
            print(f"\n  {symbol}:")
            print(f"    状态: {health.status.value}")
            print(f"    分数: {health.score}/100")
            print(f"    成交量: ${health.daily_volume:,.0f}")
            if health.issues:
                print(f"    问题: {', '.join(health.issues[:2])}")
        
        # 完整维护
        print("\n2. 运行维护流程")
        result = await maintainer.run_maintenance(symbols)
        print(f"  检查: {result['record']['total_checked']}")
        print(f"  剔除: {result['record']['removed']}")
        
        # 报告
        print("\n3. 维护报告")
        print(maintainer.generate_report())
    
    asyncio.run(test())
    
    print("\n" + "="*60)
