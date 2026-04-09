"""
重试处理器
网络超时自动重试，指数退避
"""
import time
import random
import logging
from typing import Callable, Optional, Type, Tuple, List
from functools import wraps
from enum import Enum

logger = logging.getLogger(__name__)


class RetryStrategy(Enum):
    """重试策略"""
    FIXED = "fixed"           # 固定间隔
    EXPONENTIAL = "exponential"  # 指数退避
    LINEAR = "linear"         # 线性增长
    RANDOM = "random"         # 随机间隔


class CircuitBreakerState(Enum):
    """断路器状态"""
    CLOSED = "closed"      # 正常
    OPEN = "open"          # 断开
    HALF_OPEN = "half_open"  # 半开


class RetryConfig:
    """重试配置"""
    
    def __init__(self,
                 max_retries: int = 3,
                 base_delay: float = 1.0,
                 max_delay: float = 60.0,
                 strategy: RetryStrategy = RetryStrategy.EXPONENTIAL,
                 exponential_base: float = 2.0,
                 jitter: bool = True,
                 retry_exceptions: Tuple[Type[Exception], ...] = None,
                 on_retry: Callable = None,
                 on_giveup: Callable = None):
        """
        Args:
            max_retries: 最大重试次数
            base_delay: 基础延迟(秒)
            max_delay: 最大延迟(秒)
            strategy: 重试策略
            exponential_base: 指数基数
            jitter: 是否添加随机抖动
            retry_exceptions: 需要重试的异常类型
            on_retry: 重试回调
            on_giveup: 放弃回调
        """
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.strategy = strategy
        self.exponential_base = exponential_base
        self.jitter = jitter
        self.retry_exceptions = retry_exceptions or (Exception,)
        self.on_retry = on_retry
        self.on_giveup = on_giveup


class CircuitBreaker:
    """
    断路器模式
    
    防止级联故障
    """
    
    def __init__(self,
                 failure_threshold: int = 5,
                 recovery_timeout: float = 60.0,
                 half_open_max_calls: int = 3):
        """
        Args:
            failure_threshold: 触发断路的失败次数
            recovery_timeout: 恢复超时(秒)
            half_open_max_calls: 半开状态最大尝试次数
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0
        self.half_open_calls = 0
    
    def can_execute(self) -> bool:
        """检查是否可以执行"""
        if self.state == CircuitBreakerState.CLOSED:
            return True
        
        if self.state == CircuitBreakerState.OPEN:
            # 检查是否可以进入半开状态
            if time.time() - self.last_failure_time >= self.recovery_timeout:
                logger.info("断路器进入半开状态")
                self.state = CircuitBreakerState.HALF_OPEN
                self.half_open_calls = 0
                return True
            return False
        
        if self.state == CircuitBreakerState.HALF_OPEN:
            if self.half_open_calls < self.half_open_max_calls:
                self.half_open_calls += 1
                return True
            return False
        
        return True
    
    def record_success(self):
        """记录成功"""
        self.failure_count = 0
        
        if self.state == CircuitBreakerState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.half_open_max_calls:
                logger.info("断路器关闭，服务恢复")
                self.state = CircuitBreakerState.CLOSED
                self.success_count = 0
                self.half_open_calls = 0
    
    def record_failure(self):
        """记录失败"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.state == CircuitBreakerState.HALF_OPEN:
            logger.warning("半开状态失败，断路器打开")
            self.state = CircuitBreakerState.OPEN
        elif self.failure_count >= self.failure_threshold:
            logger.error(f"失败次数达阈值({self.failure_threshold})，断路器打开")
            self.state = CircuitBreakerState.OPEN


class RetryHandler:
    """
    重试处理器
    
    执行带重试的函数
    """
    
    def __init__(self, config: RetryConfig = None):
        self.config = config or RetryConfig()
        self.circuit_breaker = CircuitBreaker()
        
        # 统计
        self.total_attempts = 0
        self.success_count = 0
        self.failure_count = 0
        self.retry_count = 0
    
    def execute(self, func: Callable, *args, **kwargs):
        """
        执行函数，带重试
        
        Args:
            func: 要执行的函数
            *args, **kwargs: 函数参数
            
        Returns:
            函数返回值
            
        Raises:
            最后一次异常
        """
        # 检查断路器
        if not self.circuit_breaker.can_execute():
            raise Exception("Circuit breaker is OPEN")
        
        last_exception = None
        
        for attempt in range(self.config.max_retries + 1):
            self.total_attempts += 1
            
            try:
                result = func(*args, **kwargs)
                
                # 成功
                self.circuit_breaker.record_success()
                self.success_count += 1
                
                if attempt > 0:
                    logger.info(f"函数执行成功 (第{attempt+1}次尝试)")
                
                return result
                
            except self.config.retry_exceptions as e:
                last_exception = e
                self.circuit_breaker.record_failure()
                
                if attempt < self.config.max_retries:
                    self.retry_count += 1
                    
                    # 计算延迟
                    delay = self._calculate_delay(attempt)
                    
                    logger.warning(
                        f"函数执行失败 (尝试{attempt+1}/{self.config.max_retries+1}): "
                        f"{str(e)[:50]}...，{delay:.1f}秒后重试"
                    )
                    
                    # 重试回调
                    if self.config.on_retry:
                        try:
                            self.config.on_retry(attempt, e, delay)
                        except:
                            pass
                    
                    time.sleep(delay)
                else:
                    # 放弃
                    self.failure_count += 1
                    logger.error(f"函数执行失败，已达最大重试次数: {str(e)[:100]}")
                    
                    if self.config.on_giveup:
                        try:
                            self.config.on_giveup(attempt, e)
                        except:
                            pass
        
        raise last_exception
    
    def _calculate_delay(self, attempt: int) -> float:
        """计算重试延迟"""
        if self.config.strategy == RetryStrategy.FIXED:
            delay = self.config.base_delay
        
        elif self.config.strategy == RetryStrategy.EXPONENTIAL:
            delay = self.config.base_delay * (self.config.exponential_base ** attempt)
        
        elif self.config.strategy == RetryStrategy.LINEAR:
            delay = self.config.base_delay * (attempt + 1)
        
        elif self.config.strategy == RetryStrategy.RANDOM:
            delay = random.uniform(self.config.base_delay, 
                                  self.config.base_delay * self.config.exponential_base ** attempt)
        
        else:
            delay = self.config.base_delay
        
        # 限制最大延迟
        delay = min(delay, self.config.max_delay)
        
        # 添加抖动
        if self.config.jitter:
            jitter = random.uniform(0, delay * 0.1)
            delay += jitter
        
        return delay
    
    def get_stats(self) -> dict:
        """获取统计"""
        return {
            'total_attempts': self.total_attempts,
            'success_count': self.success_count,
            'failure_count': self.failure_count,
            'retry_count': self.retry_count,
            'success_rate': self.success_count / max(self.total_attempts, 1),
            'circuit_breaker_state': self.circuit_breaker.state.value
        }


def with_retry(max_retries: int = 3,
               base_delay: float = 1.0,
               max_delay: float = 60.0,
               strategy: RetryStrategy = RetryStrategy.EXPONENTIAL,
               retry_exceptions: Tuple[Type[Exception], ...] = None):
    """
    重试装饰器
    
    Usage:
        @with_retry(max_retries=3, base_delay=1.0)
        def fetch_data():
            return api.fetch_data()
    """
    config = RetryConfig(
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
        strategy=strategy,
        retry_exceptions=retry_exceptions
    )
    
    def decorator(func):
        handler = RetryHandler(config)
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            return handler.execute(func, *args, **kwargs)
        
        # 附加统计方法
        wrapper.get_retry_stats = handler.get_stats
        return wrapper
    
    return decorator


class ExchangeRetryHandler:
    """
    交易所专用重试处理器
    
    针对交易所API错误优化
    """
    
    # 需要重试的HTTP状态码
    RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
    
    # 需要重试的错误关键字
    RETRY_ERROR_KEYWORDS = [
        'timeout', 'connection', 'network', 'reset',
        'rate limit', 'too many requests', 'temporarily unavailable'
    ]
    
    def __init__(self):
        self.config = RetryConfig(
            max_retries=5,
            base_delay=1.0,
            max_delay=30.0,
            strategy=RetryStrategy.EXPONENTIAL,
            exponential_base=2.0,
            jitter=True
        )
        self.handler = RetryHandler(self.config)
    
    def execute(self, func: Callable, *args, **kwargs):
        """执行交易所API调用"""
        return self.handler.execute(func, *args, **kwargs)
    
    def should_retry(self, exception: Exception) -> bool:
        """判断是否应该重试"""
        error_str = str(exception).lower()
        
        # 检查关键字
        for keyword in self.RETRY_ERROR_KEYWORDS:
            if keyword in error_str:
                return True
        
        # 检查状态码
        for code in self.RETRY_STATUS_CODES:
            if str(code) in error_str:
                return True
        
        return False
    
    def get_stats(self) -> dict:
        """获取统计"""
        return self.handler.get_stats()


# 便捷函数
def retry_on_network_error(max_retries: int = 3):
    """网络错误重试装饰器"""
    return with_retry(
        max_retries=max_retries,
        base_delay=1.0,
        retry_exceptions=(ConnectionError, TimeoutError, Exception)
    )


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    print("="*60)
    print("重试处理器测试")
    print("="*60)
    
    # 测试1: 成功重试
    print("\n1. 成功重试测试")
    attempt_count = 0
    
    @with_retry(max_retries=3, base_delay=0.5)
    def flaky_function():
        global attempt_count
        attempt_count += 1
        if attempt_count < 3:
            raise ConnectionError(f"模拟网络错误 (尝试{attempt_count})")
        return "Success!"
    
    attempt_count = 0
    result = flaky_function()
    print(f"结果: {result}")
    print(f"统计: {flaky_function.get_retry_stats()}")
    
    # 测试2: 指数退避
    print("\n2. 指数退避测试")
    config = RetryConfig(
        max_retries=5,
        base_delay=1.0,
        strategy=RetryStrategy.EXPONENTIAL,
        exponential_base=2.0
    )
    handler = RetryHandler(config)
    
    delays = []
    for i in range(5):
        delay = handler._calculate_delay(i)
        delays.append(f"{delay:.1f}s")
    
    print(f"重试延迟: {' -> '.join(delays)}")
    
    # 测试3: 断路器
    print("\n3. 断路器测试")
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=2.0)
    
    for i in range(10):
        if breaker.can_execute():
            if i < 5:  # 前5次失败
                print(f"调用{i+1}: 执行，结果=失败")
                breaker.record_failure()
            else:
                print(f"调用{i+1}: 执行，结果=成功")
                breaker.record_success()
        else:
            print(f"调用{i+1}: 被拒绝 (断路器{breaker.state.value})")
        
        if i == 6:  # 第7次后等待恢复
            print("等待断路器恢复...")
            time.sleep(2.5)
    
    # 测试4: 交易所重试
    print("\n4. 交易所重试测试")
    ex_handler = ExchangeRetryHandler()
    
    # 模拟错误判断
    test_errors = [
        ConnectionError("Network timeout"),
        Exception("429 Too Many Requests"),
        Exception("Order not found"),
        Exception("502 Bad Gateway"),
    ]
    
    for error in test_errors:
        should_retry = ex_handler.should_retry(error)
        print(f"  {error}: {'✅ 重试' if should_retry else '❌ 不重试'}")
    
    print("\n" + "="*60)
