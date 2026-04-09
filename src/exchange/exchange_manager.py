"""
交易所管理器
多交易所failover，自动故障转移
"""
import time
import random
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class ExchangeStatus(Enum):
    """交易所状态"""
    HEALTHY = "healthy"         # 健康
    DEGRADED = "degraded"       # 降级
    UNAVAILABLE = "unavailable" # 不可用
    ERROR = "error"             # 错误


@dataclass
class ExchangeHealth:
    """交易所健康状态"""
    name: str
    status: ExchangeStatus
    latency_ms: float
    last_success: float
    failure_count: int
    success_rate: float


class ExchangeManager:
    """
    交易所管理器
    
    功能:
    1. 多交易所配置
    2. 健康检查
    3. 自动故障转移
    4. 负载均衡
    5. 持仓同步
    """
    
    def __init__(self):
        self.exchanges: Dict[str, any] = {}  # name -> client
        self.health: Dict[str, ExchangeHealth] = {}
        
        # 配置
        self.primary_exchange: Optional[str] = None
        self.backup_exchanges: List[str] = []
        
        # 故障转移阈值
        self.max_failures = 3
        self.health_check_interval = 30  # 30秒检查一次
        self.recovery_timeout = 300      # 5分钟后尝试恢复
        
        # 统计
        self.request_count = 0
        self.failover_count = 0
        
    def add_exchange(self, name: str, client, is_primary: bool = False):
        """
        添加交易所
        
        Args:
            name: 交易所名称
            client: 交易所客户端
            is_primary: 是否主交易所
        """
        self.exchanges[name] = client
        self.health[name] = ExchangeHealth(
            name=name,
            status=ExchangeStatus.HEALTHY,
            latency_ms=0,
            last_success=time.time(),
            failure_count=0,
            success_rate=1.0
        )
        
        if is_primary:
            self.primary_exchange = name
        else:
            self.backup_exchanges.append(name)
        
        logger.info(f"添加交易所: {name} (primary={is_primary})")
    
    def get_exchange(self, name: str = None) -> Optional[any]:
        """
        获取交易所客户端
        
        Args:
            name: 指定名称，None则获取当前可用交易所
            
        Returns:
            交易所客户端
        """
        if name:
            return self.exchanges.get(name)
        
        # 获取当前最优交易所
        return self._get_best_exchange()
    
    def _get_best_exchange(self) -> Optional[any]:
        """获取当前最优交易所"""
        # 优先主交易所
        if self.primary_exchange:
            health = self.health.get(self.primary_exchange)
            if health and health.status in [ExchangeStatus.HEALTHY, ExchangeStatus.DEGRADED]:
                return self.exchanges[self.primary_exchange]
        
        # 查找可用的备份交易所
        for name in self.backup_exchanges:
            health = self.health.get(name)
            if health and health.status == ExchangeStatus.HEALTHY:
                logger.info(f"切换到备份交易所: {name}")
                self.failover_count += 1
                return self.exchanges[name]
        
        # 降级使用任何可用的
        for name, health in self.health.items():
            if health.status != ExchangeStatus.UNAVAILABLE:
                return self.exchanges[name]
        
        logger.error("无可用交易所!")
        return None
    
    def execute_with_failover(self, operation: str, *args, **kwargs):
        """
        带故障转移的操作执行
        
        Args:
            operation: 操作名称 (如 'fetch_ticker', 'create_order')
            *args, **kwargs: 操作参数
            
        Returns:
            操作结果
        """
        self.request_count += 1
        
        # 获取可用交易所
        exchange = self._get_best_exchange()
        if not exchange:
            raise Exception("无可用交易所")
        
        name = self._get_exchange_name(exchange)
        
        try:
            # 执行操作
            start_time = time.time()
            method = getattr(exchange, operation)
            result = method(*args, **kwargs)
            
            # 更新健康状态
            latency_ms = (time.time() - start_time) * 1000
            self._update_health(name, success=True, latency_ms=latency_ms)
            
            return result
            
        except Exception as e:
            logger.error(f"交易所操作失败 [{name}.{operation}]: {e}")
            
            # 更新健康状态
            self._update_health(name, success=False)
            
            # 尝试故障转移
            if self.health[name].failure_count >= self.max_failures:
                logger.warning(f"交易所{name}失败次数过多，标记为不可用")
                self.health[name].status = ExchangeStatus.UNAVAILABLE
                
                # 递归重试 (使用其他交易所)
                return self.execute_with_failover(operation, *args, **kwargs)
            
            raise
    
    def _update_health(self, name: str, success: bool, latency_ms: float = 0):
        """更新健康状态"""
        health = self.health[name]
        
        if success:
            health.last_success = time.time()
            health.failure_count = max(0, health.failure_count - 1)
            health.latency_ms = latency_ms
            
            # 恢复状态
            if health.status == ExchangeStatus.UNAVAILABLE:
                if time.time() - health.last_success > self.recovery_timeout:
                    health.status = ExchangeStatus.HEALTHY
                    logger.info(f"交易所{name}恢复为健康状态")
        else:
            health.failure_count += 1
            
            # 降级状态
            if health.failure_count >= self.max_failures:
                health.status = ExchangeStatus.ERROR
            elif health.failure_count >= self.max_failures // 2:
                health.status = ExchangeStatus.DEGRADED
        
        # 计算成功率 (简单滑动窗口)
        health.success_rate = max(0, 1.0 - health.failure_count / self.max_failures)
    
    def _get_exchange_name(self, exchange) -> str:
        """根据客户端获取名称"""
        for name, client in self.exchanges.items():
            if client == exchange:
                return name
        return "unknown"
    
    def health_check(self) -> Dict[str, ExchangeHealth]:
        """
        健康检查
        
        Returns:
            各交易所健康状态
        """
        for name, client in self.exchanges.items():
            try:
                start_time = time.time()
                client.fetch_time()  # 简单健康检查
                latency_ms = (time.time() - start_time) * 1000
                
                self._update_health(name, success=True, latency_ms=latency_ms)
                
            except Exception as e:
                logger.warning(f"健康检查失败 [{name}]: {e}")
                self._update_health(name, success=False)
        
        return self.health.copy()
    
    def get_health_report(self) -> Dict:
        """生成健康报告"""
        health_data = self.health_check()
        
        return {
            'timestamp': time.time(),
            'primary': self.primary_exchange,
            'backups': self.backup_exchanges,
            'exchanges': {
                name: {
                    'status': h.status.value,
                    'latency_ms': h.latency_ms,
                    'failure_count': h.failure_count,
                    'success_rate': h.success_rate,
                    'last_success': h.last_success
                }
                for name, h in health_data.items()
            },
            'request_count': self.request_count,
            'failover_count': self.failover_count
        }
    
    def sync_positions_across_exchanges(self) -> Dict:
        """
        跨交易所同步持仓
        
        Returns:
            持仓对比报告
        """
        positions = {}
        
        for name, client in self.exchanges.items():
            try:
                ex_positions = client.fetch_positions()
                positions[name] = {
                    p['symbol']: float(p['contracts'])
                    for p in ex_positions
                    if float(p['contracts']) != 0
                }
            except Exception as e:
                logger.error(f"获取持仓失败 [{name}]: {e}")
                positions[name] = {}
        
        # 对比持仓
        comparison = {}
        all_symbols = set()
        for pos in positions.values():
            all_symbols.update(pos.keys())
        
        for symbol in all_symbols:
            symbol_positions = {
                name: pos.get(symbol, 0)
                for name, pos in positions.items()
            }
            
            values = list(symbol_positions.values())
            if len(set(values)) > 1:  # 有不一致的
                comparison[symbol] = {
                    'positions': symbol_positions,
                    'consistent': False,
                    'max_diff': max(values) - min(values)
                }
            else:
                comparison[symbol] = {
                    'positions': symbol_positions,
                    'consistent': True,
                    'max_diff': 0
                }
        
        return {
            'positions': positions,
            'comparison': comparison,
            'inconsistent_count': sum(1 for c in comparison.values() if not c['consistent'])
        }


class BinanceOKXFailover:
    """
    币安+OKX双交易所failover方案
    """
    
    @staticmethod
    def create_manager(binance_api_key: str = None,
                      binance_secret: str = None,
                      okx_api_key: str = None,
                      okx_secret: str = None,
                      okx_password: str = None) -> ExchangeManager:
        """
        创建币安+OKX管理器
        
        Args:
            binance_api_key: 币安API Key
            binance_secret: 币安Secret
            okx_api_key: OKX API Key
            okx_secret: OKX Secret
            okx_password: OKX密码
            
        Returns:
            ExchangeManager
        """
        import ccxt
        
        manager = ExchangeManager()
        
        # 添加币安 (主交易所)
        try:
            binance = ccxt.binanceusdm({
                'apiKey': binance_api_key,
                'secret': binance_secret,
                'enableRateLimit': True,
                'options': {'defaultType': 'future'}
            })
            manager.add_exchange('binance', binance, is_primary=True)
        except Exception as e:
            logger.error(f"币安初始化失败: {e}")
        
        # 添加OKX (备份)
        if okx_api_key:
            try:
                okx = ccxt.okx({
                    'apiKey': okx_api_key,
                    'secret': okx_secret,
                    'password': okx_password,
                    'enableRateLimit': True,
                })
                manager.add_exchange('okx', okx, is_primary=False)
            except Exception as e:
                logger.error(f"OKX初始化失败: {e}")
        
        return manager


# 便捷函数
def create_failover_manager(primary_client, backup_clients: List) -> ExchangeManager:
    """创建failover管理器"""
    manager = ExchangeManager()
    manager.add_exchange('primary', primary_client, is_primary=True)
    
    for i, client in enumerate(backup_clients):
        manager.add_exchange(f'backup_{i}', client, is_primary=False)
    
    return manager


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    # 模拟交易所客户端
    class MockExchange:
        def __init__(self, name, fail_rate=0):
            self.name = name
            self.fail_rate = fail_rate
            
        def fetch_ticker(self, symbol):
            if random.random() < self.fail_rate:
                raise Exception(f"{self.name} failed")
            return {'symbol': symbol, 'last': random.uniform(100, 200)}
        
        def fetch_time(self):
            if random.random() < self.fail_rate:
                raise Exception(f"{self.name} failed")
            return time.time()
        
        def create_order(self, symbol, type, side, amount):
            if random.random() < self.fail_rate:
                raise Exception(f"{self.name} failed")
            return {'id': f"{self.name}_{random.randint(1000, 9999)}"}
    
    # 创建管理器
    manager = ExchangeManager()
    
    # 添加交易所
    binance = MockExchange('binance', fail_rate=0.1)  # 10%失败率
    okx = MockExchange('okx', fail_rate=0.05)         # 5%失败率
    
    manager.add_exchange('binance', binance, is_primary=True)
    manager.add_exchange('okx', okx, is_primary=False)
    
    # 测试故障转移
    print("="*60)
    print("多交易所Failover测试")
    print("="*60)
    
    success_count = 0
    failover_triggered = 0
    
    for i in range(20):
        try:
            result = manager.execute_with_failover('fetch_ticker', 'BTC/USDT')
            success_count += 1
            print(f"请求{i+1}: ✅ 成功")
        except Exception as e:
            print(f"请求{i+1}: ❌ 失败 - {e}")
    
    # 健康报告
    print("\n" + "="*60)
    print("健康报告")
    print("="*60)
    
    report = manager.get_health_report()
    
    for name, health in report['exchanges'].items():
        print(f"\n{name}:")
        print(f"  状态: {health['status']}")
        print(f"  延迟: {health['latency_ms']:.2f}ms")
        print(f"  成功率: {health['success_rate']:.1%}")
        print(f"  失败次数: {health['failure_count']}")
    
    print(f"\n总请求: {report['request_count']}")
    print(f"故障转移: {report['failover_count']}次")
