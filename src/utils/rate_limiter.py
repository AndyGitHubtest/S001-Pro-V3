"""
API速率限制器
智能限流，防止触发交易所限制
"""
import time
import threading
from typing import Dict, Optional, Callable
from dataclasses import dataclass
from enum import Enum
from collections import deque
import logging

logger = logging.getLogger(__name__)


class LimitType(Enum):
    """限流类型"""
    GLOBAL = "global"       # 全局限流
    ENDPOINT = "endpoint"   # 接口级别
    SYMBOL = "symbol"       # 币种级别
    IP = "ip"              # IP级别


@dataclass
class RateLimitConfig:
    """速率限制配置"""
    requests_per_second: float = 10.0
    requests_per_minute: float = 600.0
    burst_size: int = 20
    adaptive: bool = True
    cooldown_seconds: float = 60.0


class TokenBucket:
    """
    令牌桶算法
    
    平滑流量，支持突发
    """
    
    def __init__(self, rate: float, capacity: int):
        """
        Args:
            rate: 令牌生成速率 (个/秒)
            capacity: 桶容量 (最大突发)
        """
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_update = time.time()
        self.lock = threading.Lock()
    
    def acquire(self, tokens: int = 1, timeout: float = None) -> bool:
        """
        获取令牌
        
        Args:
            tokens: 需要的令牌数
            timeout: 最大等待时间
            
        Returns:
            是否获取成功
        """
        start_time = time.time()
        
        while True:
            with self.lock:
                self._add_tokens()
                
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return True
                
                # 计算需要等待的时间
                tokens_needed = tokens - self.tokens
                wait_time = tokens_needed / self.rate
            
            # 检查超时
            if timeout is not None:
                elapsed = time.time() - start_time
                if elapsed + wait_time > timeout:
                    return False
            
            # 等待
            time.sleep(min(wait_time, 0.1))
    
    def try_acquire(self, tokens: int = 1) -> bool:
        """非阻塞获取令牌"""
        with self.lock:
            self._add_tokens()
            
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False
    
    def _add_tokens(self):
        """添加令牌"""
        now = time.time()
        elapsed = now - self.last_update
        self.last_update = now
        
        # 计算新增令牌
        new_tokens = elapsed * self.rate
        self.tokens = min(self.capacity, self.tokens + new_tokens)


class AdaptiveRateLimiter:
    """
    自适应速率限制器
    
    根据API响应动态调整速率
    """
    
    def __init__(self, config: RateLimitConfig = None):
        self.config = config or RateLimitConfig()
        
        # 多级别令牌桶
        self.buckets: Dict[str, TokenBucket] = {
            'global': TokenBucket(
                self.config.requests_per_second,
                self.config.burst_size
            )
        }
        
        # 端点级别桶
        self.endpoint_buckets: Dict[str, TokenBucket] = {}
        
        # 响应时间记录
        self.response_times: deque = deque(maxlen=100)
        self.error_count = 0
        self.last_adjustment = time.time()
        
        # 自适应锁
        self.adaptive_lock = threading.Lock()
    
    def acquire(self, endpoint: str = None, priority: int = 0) -> bool:
        """
        获取请求许可
        
        Args:
            endpoint: API端点
            priority: 优先级 (0-9, 9最高)
            
        Returns:
            是否允许请求
        """
        # 高优先级请求可以抢占
        timeout = 10.0 if priority >= 5 else 5.0
        
        # 检查全局限流
        if not self.buckets['global'].acquire(timeout=timeout):
            logger.warning(f"全局限流触发，请求被拒绝 (priority={priority})")
            return False
        
        # 检查端点限流
        if endpoint:
            bucket = self._get_endpoint_bucket(endpoint)
            if not bucket.acquire(timeout=timeout / 2):
                logger.warning(f"端点限流触发 [{endpoint}]")
                # 归还全局令牌
                self.buckets['global'].tokens += 1
                return False
        
        return True
    
    def _get_endpoint_bucket(self, endpoint: str) -> TokenBucket:
        """获取端点令牌桶"""
        if endpoint not in self.endpoint_buckets:
            # 端点级别更严格的限流
            self.endpoint_buckets[endpoint] = TokenBucket(
                rate=self.config.requests_per_second / 2,
                capacity=self.config.burst_size // 2
            )
        return self.endpoint_buckets[endpoint]
    
    def report_response(self, endpoint: str, response_time_ms: float, 
                       status_code: int = 200):
        """
        报告API响应
        
        用于自适应调整
        """
        self.response_times.append(response_time_ms)
        
        if status_code != 200:
            self.error_count += 1
        
        # 自适应调整
        if self.config.adaptive:
            self._adaptive_adjust()
    
    def _adaptive_adjust(self):
        """自适应调整速率"""
        now = time.time()
        
        # 每60秒调整一次
        if now - self.last_adjustment < 60:
            return
        
        with self.adaptive_lock:
            if now - self.last_adjustment < 60:
                return
            
            self.last_adjustment = now
            
            if len(self.response_times) < 10:
                return
            
            avg_time = sum(self.response_times) / len(self.response_times)
            error_rate = self.error_count / max(len(self.response_times), 1)
            
            # 根据响应调整
            bucket = self.buckets['global']
            old_rate = bucket.rate
            
            if error_rate > 0.1:  # 错误率>10%
                # 降低速率
                new_rate = old_rate * 0.8
                logger.warning(f"自适应限流: 错误率高({error_rate:.1%})，"
                              f"降低速率 {old_rate:.1f} -> {new_rate:.1f}")
            elif avg_time > 500:  # 平均响应>500ms
                # 稍微降低
                new_rate = old_rate * 0.9
                logger.info(f"自适应限流: 响应慢({avg_time:.0f}ms)，"
                           f"降低速率 {old_rate:.1f} -> {new_rate:.1f}")
            elif error_rate < 0.01 and avg_time < 100:
                # 提高速率
                new_rate = min(old_rate * 1.1, self.config.requests_per_second * 1.5)
                logger.info(f"自适应限流: 系统健康，"
                           f"提高速率 {old_rate:.1f} -> {new_rate:.1f}")
            else:
                return
            
            bucket.rate = new_rate
            self.error_count = 0
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            'global_rate': self.buckets['global'].rate,
            'global_tokens': self.buckets['global'].tokens,
            'endpoint_count': len(self.endpoint_buckets),
            'avg_response_time': sum(self.response_times) / len(self.response_times) 
                               if self.response_times else 0,
            'error_count': self.error_count,
            'adaptive_enabled': self.config.adaptive
        }


class BinanceRateLimiter:
    """
    币安专用限流器
    
    针对币安API限制优化
    """
    
    # 币安限制 (U本位合约)
    LIMITS = {
        'global': {'rate': 20, 'burst': 50},  # 全局20/s
        'order': {'rate': 5, 'burst': 10},    # 下单5/s
        'cancel': {'rate': 5, 'burst': 10},   # 撤单5/s
        'market_data': {'rate': 50, 'burst': 100},  # 行情50/s
        'account': {'rate': 10, 'burst': 20},  # 账户10/s
    }
    
    def __init__(self):
        self.limiters: Dict[str, AdaptiveRateLimiter] = {}
        
        # 初始化各类别限流器
        for category, limits in self.LIMITS.items():
            config = RateLimitConfig(
                requests_per_second=limits['rate'],
                burst_size=limits['burst'],
                adaptive=True
            )
            self.limiters[category] = AdaptiveRateLimiter(config)
    
    def acquire(self, category: str = 'global', endpoint: str = None) -> bool:
        """
        获取许可
        
        Args:
            category: 类别 (global/order/cancel/market_data/account)
            endpoint: 具体端点
        """
        limiter = self.limiters.get(category, self.limiters['global'])
        return limiter.acquire(endpoint)
    
    def pre_request(self, method: str, path: str):
        """
        请求前调用
        
        自动分类限流
        """
        # 根据路径分类
        if 'order' in path.lower():
            category = 'order'
        elif 'cancel' in path.lower():
            category = 'cancel'
        elif 'account' in path.lower() or 'balance' in path.lower():
            category = 'account'
        elif 'ticker' in path.lower() or 'klines' in path.lower():
            category = 'market_data'
        else:
            category = 'global'
        
        return self.acquire(category, path)
    
    def post_request(self, method: str, path: str, 
                    response_time_ms: float, status_code: int):
        """请求后调用"""
        category = self._get_category(path)
        limiter = self.limiters.get(category, self.limiters['global'])
        limiter.report_response(path, response_time_ms, status_code)
    
    def _get_category(self, path: str) -> str:
        """获取路径类别"""
        if 'order' in path.lower():
            return 'order'
        elif 'cancel' in path.lower():
            return 'cancel'
        elif 'account' in path.lower():
            return 'account'
        elif 'ticker' in path.lower() or 'klines' in path.lower():
            return 'market_data'
        return 'global'
    
    def get_stats(self) -> Dict:
        """获取所有统计"""
        return {
            category: limiter.get_stats()
            for category, limiter in self.limiters.items()
        }


# 便捷函数
_rate_limiter: Optional[AdaptiveRateLimiter] = None

def get_rate_limiter() -> AdaptiveRateLimiter:
    """获取全局限流器"""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = AdaptiveRateLimiter()
    return _rate_limiter


def rate_limited(endpoint: str = None, priority: int = 0):
    """
    限流装饰器
    
    Usage:
        @rate_limited(endpoint='fetch_ticker', priority=5)
        def fetch_data():
            pass
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            limiter = get_rate_limiter()
            if limiter.acquire(endpoint, priority):
                return func(*args, **kwargs)
            else:
                raise Exception(f"Rate limit exceeded for {endpoint}")
        return wrapper
    return decorator


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    print("="*60)
    print("API速率限制器测试")
    print("="*60)
    
    # 测试令牌桶
    print("\n1. 令牌桶测试")
    bucket = TokenBucket(rate=10, capacity=5)
    
    success = 0
    for i in range(15):
        if bucket.try_acquire():
            success += 1
            print(f"  请求{i+1}: ✅ 成功 (tokens={bucket.tokens:.1f})")
        else:
            print(f"  请求{i+1}: ❌ 失败 (tokens={bucket.tokens:.1f})")
        time.sleep(0.05)
    
    print(f"\n成功率: {success}/15")
    
    # 测试自适应限流
    print("\n2. 自适应限流测试")
    limiter = AdaptiveRateLimiter(RateLimitConfig(
        requests_per_second=10,
        burst_size=5,
        adaptive=True
    ))
    
    for i in range(20):
        if limiter.acquire(priority=i % 10):
            # 模拟响应
            response_time = 50 if i < 10 else 600  # 后10次变慢
            status = 200 if i < 15 else 429  # 后5次报错
            limiter.report_response('test', response_time, status)
            print(f"  请求{i+1}: ✅ 响应{response_time}ms")
        else:
            print(f"  请求{i+1}: ❌ 被拒绝")
        time.sleep(0.1)
    
    # 统计
    stats = limiter.get_stats()
    print(f"\n当前速率: {stats['global_rate']:.1f}/s")
    print(f"平均响应: {stats['avg_response_time']:.0f}ms")
    print(f"错误次数: {stats['error_count']}")
    
    # 币安限流器
    print("\n3. 币安限流器测试")
    binance_limiter = BinanceRateLimiter()
    
    test_paths = [
        '/fapi/v1/ticker/price',
        '/fapi/v1/order',
        '/fapi/v1/cancel',
        '/fapi/v2/account',
        '/fapi/v1/klines'
    ]
    
    for path in test_paths:
        result = binance_limiter.pre_request('GET', path)
        print(f"  {path}: {'✅' if result else '❌'}")
    
    print("\n" + "="*60)
