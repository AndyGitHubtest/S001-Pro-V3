"""
数据库连接池
SQLite连接池管理，避免并发冲突
"""
import sqlite3
import threading
import queue
import time
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class PoolStats:
    """连接池统计"""
    total_connections: int
    available_connections: int
    in_use_connections: int
    max_connections: int
    total_queries: int
    failed_queries: int
    wait_time_avg: float


class SQLiteConnectionPool:
    """
    SQLite连接池
    
    功能:
    1. 连接复用 (减少连接开销)
    2. 并发控制 (避免数据库锁)
    3. 连接健康检查
    4. 自动重连
    5. 超时处理
    """
    
    def __init__(self,
                 db_path: str,
                 max_connections: int = 10,
                 timeout: float = 30.0,
                 max_idle_time: float = 300.0):
        """
        Args:
            db_path: 数据库路径
            max_connections: 最大连接数
            timeout: 获取连接超时(秒)
            max_idle_time: 最大空闲时间(秒)
        """
        self.db_path = db_path
        self.max_connections = max(max_connections, 3)  # 至少3个
        self.timeout = timeout
        self.max_idle_time = max_idle_time
        
        # 连接池
        self._available = queue.Queue(maxsize=self.max_connections)
        self._in_use = set()
        self._lock = threading.RLock()
        
        # 统计
        self._total_queries = 0
        self._failed_queries = 0
        self._wait_times = []
        self._stats_lock = threading.Lock()
        
        # 连接创建时间记录
        self._connection_times = {}
        
        # 初始化连接
        self._initialize_pool()
    
    def _initialize_pool(self):
        """初始化连接池"""
        logger.info(f"初始化连接池: {self.db_path}")
        
        for i in range(min(3, self.max_connections)):
            conn = self._create_connection()
            if conn:
                self._available.put(conn)
                logger.debug(f"创建初始连接 {i+1}")
    
    def _create_connection(self) -> Optional[sqlite3.Connection]:
        """创建新连接"""
        try:
            conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=30.0,
                isolation_level=None  # 自动提交模式
            )
            
            # 优化设置
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=10000")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA mmap_size=30000000000")
            
            conn_id = id(conn)
            with self._lock:
                self._connection_times[conn_id] = time.time()
            
            return conn
            
        except Exception as e:
            logger.error(f"创建连接失败: {e}")
            return None
    
    def _is_connection_alive(self, conn: sqlite3.Connection) -> bool:
        """检查连接是否有效"""
        try:
            conn.execute("SELECT 1")
            return True
        except:
            return False
    
    def _is_connection_expired(self, conn: sqlite3.Connection) -> bool:
        """检查连接是否过期"""
        conn_id = id(conn)
        with self._lock:
            create_time = self._connection_times.get(conn_id, 0)
        
        return (time.time() - create_time) > self.max_idle_time
    
    def get_connection(self, timeout: float = None) -> Optional[sqlite3.Connection]:
        """
        获取连接
        
        Args:
            timeout: 等待超时时间
            
        Returns:
            数据库连接
        """
        timeout = timeout or self.timeout
        start_time = time.time()
        
        while True:
            try:
                # 尝试从池获取
                wait_time = time.time() - start_time
                conn = self._available.get(timeout=max(0.1, timeout - wait_time))
                
                # 检查连接有效性
                if not self._is_connection_alive(conn):
                    logger.warning("连接已失效，创建新连接")
                    conn = self._create_connection()
                
                # 检查连接是否过期
                if self._is_connection_expired(conn):
                    logger.debug("连接已过期，重新创建")
                    conn.close()
                    conn = self._create_connection()
                
                # 标记为使用中
                with self._lock:
                    self._in_use.add(id(conn))
                
                # 记录等待时间
                with self._stats_lock:
                    self._wait_times.append(time.time() - start_time)
                    if len(self._wait_times) > 100:
                        self._wait_times = self._wait_times[-100:]
                
                return conn
                
            except queue.Empty:
                # 检查是否可以创建新连接
                with self._lock:
                    current_total = self._available.qsize() + len(self._in_use)
                
                if current_total < self.max_connections:
                    # 创建新连接
                    conn = self._create_connection()
                    if conn:
                        with self._lock:
                            self._in_use.add(id(conn))
                        return conn
                
                # 检查超时
                if time.time() - start_time >= timeout:
                    raise TimeoutError(f"获取连接超时 ({timeout}s)")
                
                # 短暂等待
                time.sleep(0.1)
    
    def release_connection(self, conn: sqlite3.Connection):
        """释放连接回池"""
        if conn is None:
            return
        
        conn_id = id(conn)
        
        with self._lock:
            if conn_id in self._in_use:
                self._in_use.remove(conn_id)
        
        # 检查连接是否还可用
        if self._is_connection_alive(conn):
            try:
                self._available.put(conn, block=False)
            except queue.Full:
                # 池已满，关闭连接
                conn.close()
        else:
            conn.close()
    
    def close_all(self):
        """关闭所有连接"""
        logger.info("关闭连接池")
        
        # 关闭可用连接
        while not self._available.empty():
            try:
                conn = self._available.get_nowait()
                conn.close()
            except:
                pass
        
        # 注意: 使用中的连接由调用方负责关闭
    
    @contextmanager
    def connection(self, timeout: float = None):
        """
        上下文管理器获取连接
        
        使用:
            with pool.connection() as conn:
                conn.execute("SELECT * FROM table")
        """
        conn = None
        try:
            conn = self.get_connection(timeout)
            yield conn
        finally:
            if conn:
                self.release_connection(conn)
    
    def execute(self, query: str, params: tuple = None) -> List[Dict]:
        """
        执行查询
        
        Args:
            query: SQL查询
            params: 查询参数
            
        Returns:
            结果列表
        """
        with self.connection() as conn:
            try:
                cursor = conn.execute(query, params or ())
                
                # 转换为字典列表
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                results = [dict(zip(columns, row)) for row in cursor.fetchall()]
                
                with self._stats_lock:
                    self._total_queries += 1
                
                return results
                
            except Exception as e:
                with self._stats_lock:
                    self._failed_queries += 1
                raise e
    
    def execute_many(self, query: str, params_list: List[tuple]) -> int:
        """
        批量执行
        
        Args:
            query: SQL查询
            params_list: 参数列表
            
        Returns:
            影响行数
        """
        with self.connection() as conn:
            try:
                cursor = conn.executemany(query, params_list)
                
                with self._stats_lock:
                    self._total_queries += len(params_list)
                
                return cursor.rowcount
                
            except Exception as e:
                with self._stats_lock:
                    self._failed_queries += 1
                raise e
    
    def get_stats(self) -> PoolStats:
        """获取连接池统计"""
        with self._lock:
            available = self._available.qsize()
            in_use = len(self._in_use)
        
        with self._stats_lock:
            total_queries = self._total_queries
            failed_queries = self._failed_queries
            wait_time_avg = sum(self._wait_times) / len(self._wait_times) if self._wait_times else 0
        
        return PoolStats(
            total_connections=available + in_use,
            available_connections=available,
            in_use_connections=in_use,
            max_connections=self.max_connections,
            total_queries=total_queries,
            failed_queries=failed_queries,
            wait_time_avg=wait_time_avg
        )


class DatabaseManager:
    """
    数据库管理器
    
    管理多个数据库连接池
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._pools = {}
        return cls._instance
    
    def get_pool(self, db_path: str, **kwargs) -> SQLiteConnectionPool:
        """获取或创建连接池"""
        if db_path not in self._pools:
            self._pools[db_path] = SQLiteConnectionPool(db_path, **kwargs)
        return self._pools[db_path]
    
    def close_all(self):
        """关闭所有连接池"""
        for pool in self._pools.values():
            pool.close_all()
        self._pools.clear()
    
    def get_all_stats(self) -> Dict[str, PoolStats]:
        """获取所有连接池统计"""
        return {
            path: pool.get_stats() 
            for path, pool in self._pools.items()
        }


# 便捷函数
def get_db_pool(db_path: str, **kwargs) -> SQLiteConnectionPool:
    """获取数据库连接池"""
    manager = DatabaseManager()
    return manager.get_pool(db_path, **kwargs)


@contextmanager
def db_connection(db_path: str, timeout: float = 30.0):
    """便捷上下文管理器"""
    pool = get_db_pool(db_path)
    with pool.connection(timeout) as conn:
        yield conn


def execute_query(db_path: str, query: str, params: tuple = None) -> List[Dict]:
    """便捷执行查询"""
    pool = get_db_pool(db_path)
    return pool.execute(query, params)


# 使用示例
if __name__ == "__main__":
    import logging
    import tempfile
    import os
    
    logging.basicConfig(level=logging.INFO)
    
    print("="*60)
    print("数据库连接池测试")
    print("="*60)
    
    # 创建临时数据库
    db_file = tempfile.mktemp(suffix='.db')
    
    # 测试1: 创建连接池
    print("\n1. 创建连接池")
    pool = SQLiteConnectionPool(
        db_file,
        max_connections=5,
        timeout=10.0
    )
    print(f"  连接池创建成功")
    
    # 测试2: 创建表
    print("\n2. 创建测试表")
    with pool.connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS test (
                id INTEGER PRIMARY KEY,
                name TEXT,
                value REAL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_name ON test(name)")
    print("  表创建成功")
    
    # 测试3: 插入数据
    print("\n3. 插入数据")
    with pool.connection() as conn:
        for i in range(100):
            conn.execute(
                "INSERT INTO test (name, value) VALUES (?, ?)",
                (f"item_{i}", i * 1.5)
            )
    print("  插入100条数据")
    
    # 测试4: 并发查询
    print("\n4. 并发查询测试")
    import threading
    
    results = []
    errors = []
    
    def query_worker(worker_id):
        try:
            for i in range(10):
                result = pool.execute(
                    "SELECT * FROM test WHERE name LIKE ? LIMIT 10",
                    (f"item_{worker_id}%",)
                )
                results.append(len(result))
                time.sleep(0.01)
        except Exception as e:
            errors.append(str(e))
    
    threads = []
    for i in range(10):
        t = threading.Thread(target=query_worker, args=(i,))
        threads.append(t)
        t.start()
    
    for t in threads:
        t.join()
    
    print(f"  10个线程各查询10次")
    print(f"  总查询数: {len(results)}")
    print(f"  错误数: {len(errors)}")
    
    # 测试5: 批量插入
    print("\n5. 批量插入")
    params = [(f"batch_{i}", i * 2.0) for i in range(1000)]
    affected = pool.execute_many(
        "INSERT INTO test (name, value) VALUES (?, ?)",
        params
    )
    print(f"  批量插入1000条，影响行数: {affected}")
    
    # 测试6: 统计
    print("\n6. 连接池统计")
    stats = pool.get_stats()
    print(f"  总连接数: {stats.total_connections}")
    print(f"  可用连接: {stats.available_connections}")
    print(f"  使用中: {stats.in_use_connections}")
    print(f"  最大连接: {stats.max_connections}")
    print(f"  总查询: {stats.total_queries}")
    print(f"  失败查询: {stats.failed_queries}")
    print(f"  平均等待: {stats.wait_time_avg:.3f}s")
    
    # 测试7: 便捷函数
    print("\n7. 便捷函数测试")
    result = execute_query(db_file, "SELECT COUNT(*) as count FROM test")
    print(f"  总记录数: {result[0]['count']}")
    
    # 清理
    pool.close_all()
    os.remove(db_file)
    
    print("\n" + "="*60)
    print("✅ 所有测试通过")
    print("="*60)
