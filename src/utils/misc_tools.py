"""
其他工具函数
P2-18: 各种辅助功能
"""
import os
import sys
import time
import random
import string
import hashlib
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Union
import logging

logger = logging.getLogger(__name__)


# ==================== 字符串工具 ====================

def generate_id(length: int = 16) -> str:
    """生成随机ID"""
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choices(chars, k=length))


def safe_filename(name: str) -> str:
    """生成安全文件名"""
    # 替换不安全字符
    unsafe = '<>:"/\\|?*'
    for char in unsafe:
        name = name.replace(char, '_')
    return name


def truncate_string(s: str, max_length: int = 100, suffix: str = "...") -> str:
    """截断字符串"""
    if len(s) <= max_length:
        return s
    return s[:max_length - len(suffix)] + suffix


# ==================== 时间工具 ====================

def timestamp_ms() -> int:
    """获取当前时间戳(毫秒)"""
    return int(time.time() * 1000)


def format_duration(seconds: float) -> str:
    """格式化持续时间"""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f}m"
    else:
        return f"{seconds/3600:.1f}h"


def parse_time_string(time_str: str) -> Optional[datetime]:
    """解析时间字符串"""
    formats = [
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d',
        '%Y%m%d',
        '%Y/%m/%d %H:%M:%S',
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(time_str, fmt)
        except ValueError:
            continue
    
    return None


def get_time_range(period: str) -> tuple:
    """
    获取时间范围
    
    Args:
        period: '1d', '7d', '30d', '1m', '3m', '1y'
    
    Returns:
        (start_time, end_time)
    """
    end = datetime.now()
    
    if period == '1d':
        start = end - timedelta(days=1)
    elif period == '7d':
        start = end - timedelta(days=7)
    elif period == '30d':
        start = end - timedelta(days=30)
    elif period == '1m':
        start = end - timedelta(days=30)
    elif period == '3m':
        start = end - timedelta(days=90)
    elif period == '1y':
        start = end - timedelta(days=365)
    else:
        start = end - timedelta(days=7)
    
    return start, end


# ==================== 数值工具 ====================

def round_to_tick(price: float, tick_size: float) -> float:
    """按tick size舍入价格"""
    return round(price / tick_size) * tick_size


def format_number(n: Union[int, float], decimals: int = 2) -> str:
    """格式化数字"""
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.{decimals}f}B"
    elif n >= 1_000_000:
        return f"{n/1_000_000:.{decimals}f}M"
    elif n >= 1_000:
        return f"{n/1_000:.{decimals}f}K"
    else:
        return f"{n:.{decimals}f}"


def calculate_percentage_change(old: float, new: float) -> float:
    """计算百分比变化"""
    if old == 0:
        return 0
    return (new - old) / old * 100


def clamp(value: float, min_val: float, max_val: float) -> float:
    """限制值在范围内"""
    return max(min_val, min(max_val, value))


# ==================== 数据结构工具 ====================

def merge_dicts(base: Dict, update: Dict) -> Dict:
    """深度合并字典"""
    result = base.copy()
    
    for key, value in update.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_dicts(result[key], value)
        else:
            result[key] = value
    
    return result


def flatten_dict(d: Dict, parent_key: str = '', sep: str = '.') -> Dict:
    """扁平化字典"""
    items = []
    
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep).items())
        else:
            items.append((new_key, v))
    
    return dict(items)


def chunk_list(lst: List, chunk_size: int) -> List[List]:
    """分块列表"""
    return [lst[i:i+chunk_size] for i in range(0, len(lst), chunk_size)]


# ==================== 文件工具 ====================

def ensure_dir(path: str):
    """确保目录存在"""
    os.makedirs(path, exist_ok=True)


def get_file_size(path: str) -> int:
    """获取文件大小"""
    try:
        return os.path.getsize(path)
    except:
        return 0


def format_file_size(size_bytes: int) -> str:
    """格式化文件大小"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def read_json_file(path: str, default: Any = None) -> Any:
    """安全读取JSON文件"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"读取文件失败 {path}: {e}")
        return default


def write_json_file(path: str, data: Any, indent: int = 2):
    """安全写入JSON文件"""
    ensure_dir(os.path.dirname(path))
    
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)


# ==================== 网络工具 ====================

def is_valid_ip(ip: str) -> bool:
    """验证IP地址"""
    parts = ip.split('.')
    if len(parts) != 4:
        return False
    
    for part in parts:
        try:
            num = int(part)
            if num < 0 or num > 255:
                return False
        except ValueError:
            return False
    
    return True


def mask_ip(ip: str) -> str:
    """脱敏IP地址"""
    parts = ip.split('.')
    if len(parts) != 4:
        return ip
    
    return f"{parts[0]}.{parts[1]}.*.*"


# ==================== 加密工具 ====================

def hash_string(s: str, algorithm: str = 'md5') -> str:
    """计算字符串哈希"""
    if algorithm == 'md5':
        return hashlib.md5(s.encode()).hexdigest()
    elif algorithm == 'sha256':
        return hashlib.sha256(s.encode()).hexdigest()
    else:
        raise ValueError(f"不支持的算法: {algorithm}")


def generate_signature(params: Dict, secret: str) -> str:
    """生成API签名"""
    # 按key排序并拼接
    sorted_params = sorted(params.items())
    sign_str = '&'.join(f"{k}={v}" for k, v in sorted_params)
    sign_str += f"&secret={secret}"
    
    return hashlib.sha256(sign_str.encode()).hexdigest()


# ==================== 系统工具 ====================

def get_system_info() -> Dict:
    """获取系统信息"""
    import platform
    
    return {
        'platform': platform.platform(),
        'python_version': platform.python_version(),
        'processor': platform.processor(),
        'machine': platform.machine(),
    }


def get_memory_usage() -> Dict:
    """获取内存使用 (简化版)"""
    try:
        import psutil
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        
        return {
            'rss_mb': mem_info.rss / 1024 / 1024,
            'vms_mb': mem_info.vms / 1024 / 1024,
            'percent': process.memory_percent()
        }
    except ImportError:
        return {'error': 'psutil not installed'}


# ==================== 重试装饰器 ====================

def retry(max_attempts: int = 3, delay: float = 1.0, exceptions: tuple = (Exception,)):
    """
    重试装饰器
    
    使用:
        @retry(max_attempts=3)
        def my_function():
            pass
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts - 1:
                        raise
                    logger.warning(f"{func.__name__} 失败 (尝试 {attempt+1}/{max_attempts}): {e}")
                    time.sleep(delay * (attempt + 1))
            return None
        return wrapper
    return decorator


# ==================== 缓存装饰器 ====================

def simple_cache(ttl_seconds: float = 60):
    """
    简单缓存装饰器
    
    使用:
        @simple_cache(ttl_seconds=60)
        def get_data():
            return expensive_query()
    """
    cache = {}
    
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = str(args) + str(sorted(kwargs.items()))
            now = time.time()
            
            if key in cache:
                result, timestamp = cache[key]
                if now - timestamp < ttl_seconds:
                    return result
            
            result = func(*args, **kwargs)
            cache[key] = (result, now)
            return result
        
        wrapper.cache = cache
        wrapper.clear_cache = lambda: cache.clear()
        
        return wrapper
    return decorator


# ==================== 便捷导入 ====================

import functools

# 使用示例
if __name__ == "__main__":
    print("="*60)
    print("其他工具函数测试")
    print("="*60)
    
    # 字符串工具
    print("\n1. 字符串工具")
    print(f"  生成ID: {generate_id()}")
    print(f"  安全文件名: {safe_filename('file<name>.txt')}")
    print(f"  截断: {truncate_string('这是一个很长的字符串', 10)}")
    
    # 时间工具
    print("\n2. 时间工具")
    print(f"  时间戳: {timestamp_ms()}")
    print(f"  格式化: {format_duration(3665)}")
    start, end = get_time_range('7d')
    print(f"  时间范围: {start} ~ {end}")
    
    # 数值工具
    print("\n3. 数值工具")
    print(f"  格式化数字: {format_number(1234567.89)}")
    print(f"  百分比变化: {calculate_percentage_change(100, 120):.1f}%")
    print(f"  限制范围: {clamp(150, 0, 100)}")
    
    # 字典工具
    print("\n4. 字典工具")
    d1 = {'a': 1, 'b': {'c': 2}}
    d2 = {'b': {'d': 3}, 'e': 4}
    merged = merge_dicts(d1, d2)
    print(f"  合并: {merged}")
    print(f"  扁平化: {flatten_dict(merged)}")
    
    # 列表工具
    print("\n5. 列表工具")
    lst = list(range(10))
    chunks = chunk_list(lst, 3)
    print(f"  分块: {chunks}")
    
    # 文件工具
    print("\n6. 文件工具")
    print(f"  格式化大小: {format_file_size(1234567890)}")
    
    # 加密工具
    print("\n7. 加密工具")
    print(f"  MD5: {hash_string('hello')}")
    print(f"  SHA256: {hash_string('hello', 'sha256')[:16]}...")
    
    # 系统工具
    print("\n8. 系统工具")
    info = get_system_info()
    print(f"  Python: {info['python_version']}")
    print(f"  平台: {info['platform'][:30]}...")
    
    # 装饰器测试
    print("\n9. 装饰器测试")
    
    @retry(max_attempts=2, delay=0.1)
    def test_retry():
        if random.random() < 0.5:
            raise Exception("随机错误")
        return "成功"
    
    result = test_retry()
    print(f"  重试结果: {result}")
    
    @simple_cache(ttl_seconds=5)
    def test_cache(x):
        print(f"    (执行计算 {x})")
        return x * 2
    
    print("  缓存测试:")
    print(f"    第一次: {test_cache(5)}")
    print(f"    第二次(缓存): {test_cache(5)}")
    
    print("\n" + "="*60)
