"""
数据备份管理器
自动备份策略数据，防止数据丢失
"""
import os
import shutil
import gzip
import hashlib
import json
import time
import sqlite3
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass
from pathlib import Path
import threading
import logging

logger = logging.getLogger(__name__)


@dataclass
class BackupConfig:
    """备份配置"""
    backup_dir: str = "backups"
    full_backup_interval_hours: int = 24      # 全量备份间隔
    incremental_backup_interval_hours: int = 1  # 增量备份间隔
    retention_days: int = 30                   # 保留天数
    compress: bool = True                      # 是否压缩
    verify: bool = True                        # 是否验证
    max_backups: int = 50                      # 最大备份数


class BackupManager:
    """
    备份管理器
    
    功能:
    1. 定时全量备份
    2. 增量备份
    3. 自动压缩
    4. 备份验证
    5. 自动清理
    6. 备份恢复
    """
    
    def __init__(self, config: BackupConfig = None):
        self.config = config or BackupConfig()
        self.backup_dir = Path(self.config.backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        
        # 备份记录
        self.backup_history: List[Dict] = []
        self.last_full_backup: Optional[datetime] = None
        self.last_incremental_backup: Optional[datetime] = None
        
        # 运行状态
        self.running = False
        self.backup_thread: Optional[threading.Thread] = None
        
        # 加载历史
        self._load_history()
    
    def backup_database(self, db_path: str, backup_name: str = None) -> str:
        """
        备份SQLite数据库
        
        Args:
            db_path: 数据库路径
            backup_name: 备份名称
            
        Returns:
            备份文件路径
        """
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"数据库不存在: {db_path}")
        
        # 生成备份名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = backup_name or f"db_backup_{timestamp}"
        
        # 备份路径
        backup_path = self.backup_dir / f"{backup_name}.db"
        
        # 执行备份 (使用SQLite在线备份)
        try:
            source = sqlite3.connect(db_path)
            dest = sqlite3.connect(str(backup_path))
            
            with dest:
                source.backup(dest)
            
            source.close()
            dest.close()
            
            # 压缩
            if self.config.compress:
                backup_path = self._compress_file(backup_path)
            
            # 验证
            if self.config.verify:
                self._verify_backup(backup_path, db_path)
            
            # 记录
            self._record_backup(backup_name, str(backup_path), "full", db_path)
            
            logger.info(f"数据库备份完成: {backup_path}")
            return str(backup_path)
            
        except Exception as e:
            logger.error(f"数据库备份失败: {e}")
            raise
    
    def backup_directory(self, source_dir: str, backup_name: str = None) -> str:
        """
        备份目录
        
        Args:
            source_dir: 源目录
            backup_name: 备份名称
            
        Returns:
            备份文件路径
        """
        if not os.path.exists(source_dir):
            raise FileNotFoundError(f"目录不存在: {source_dir}")
        
        # 生成备份名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = backup_name or f"dir_backup_{timestamp}"
        
        # 备份路径
        backup_path = self.backup_dir / f"{backup_name}.tar.gz"
        
        # 创建压缩包
        try:
            shutil.make_archive(
                base_name=str(self.backup_dir / backup_name),
                format='gztar',
                root_dir=source_dir
            )
            
            # 记录
            actual_path = str(self.backup_dir / f"{backup_name}.tar.gz")
            self._record_backup(backup_name, actual_path, "directory", source_dir)
            
            logger.info(f"目录备份完成: {actual_path}")
            return actual_path
            
        except Exception as e:
            logger.error(f"目录备份失败: {e}")
            raise
    
    def backup_config(self, config_files: List[str]) -> str:
        """
        备份配置文件
        
        Args:
            config_files: 配置文件列表
            
        Returns:
            备份文件路径
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"config_backup_{timestamp}"
        backup_path = self.backup_dir / f"{backup_name}.tar.gz"
        
        # 创建临时目录
        temp_dir = self.backup_dir / f"temp_{timestamp}"
        temp_dir.mkdir(exist_ok=True)
        
        try:
            # 复制配置文件
            for config_file in config_files:
                if os.path.exists(config_file):
                    shutil.copy2(config_file, temp_dir)
            
            # 打包
            shutil.make_archive(
                base_name=str(self.backup_dir / backup_name),
                format='gztar',
                root_dir=str(temp_dir)
            )
            
            # 清理临时目录
            shutil.rmtree(temp_dir)
            
            actual_path = str(self.backup_dir / f"{backup_name}.tar.gz")
            self._record_backup(backup_name, actual_path, "config", str(config_files))
            
            logger.info(f"配置备份完成: {actual_path}")
            return actual_path
            
        except Exception as e:
            logger.error(f"配置备份失败: {e}")
            raise
    
    def _compress_file(self, file_path: Path) -> Path:
        """压缩文件"""
        compressed_path = file_path.with_suffix('.db.gz')
        
        with open(file_path, 'rb') as f_in:
            with gzip.open(compressed_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        
        # 删除原文件
        file_path.unlink()
        
        return compressed_path
    
    def _verify_backup(self, backup_path: str, original_path: str):
        """验证备份"""
        try:
            # 检查文件存在
            if not os.path.exists(backup_path):
                raise Exception("备份文件不存在")
            
            # 检查文件大小
            backup_size = os.path.getsize(backup_path)
            original_size = os.path.getsize(original_path)
            
            if backup_size < original_size * 0.1:  # 备份小于原文件10%
                raise Exception(f"备份文件过小: {backup_size} vs {original_size}")
            
            # 如果是数据库，尝试连接
            if backup_path.endswith('.db') or backup_path.endswith('.db.gz'):
                if backup_path.endswith('.gz'):
                    # 解压验证
                    with gzip.open(backup_path, 'rb') as f:
                        # 简单验证头部
                        header = f.read(16)
                        if not header.startswith(b'SQLite format 3'):
                            raise Exception("数据库格式验证失败")
                else:
                    conn = sqlite3.connect(backup_path)
                    conn.execute("SELECT name FROM sqlite_master LIMIT 1")
                    conn.close()
            
            logger.debug(f"备份验证通过: {backup_path}")
            
        except Exception as e:
            logger.error(f"备份验证失败: {e}")
            raise
    
    def _record_backup(self, name: str, path: str, backup_type: str, source: str):
        """记录备份"""
        record = {
            'name': name,
            'path': path,
            'type': backup_type,
            'source': source,
            'timestamp': datetime.now().isoformat(),
            'size': os.path.getsize(path)
        }
        
        self.backup_history.append(record)
        self._save_history()
    
    def _save_history(self):
        """保存备份历史"""
        history_file = self.backup_dir / "backup_history.json"
        with open(history_file, 'w') as f:
            json.dump(self.backup_history, f, indent=2)
    
    def _load_history(self):
        """加载备份历史"""
        history_file = self.backup_dir / "backup_history.json"
        if history_file.exists():
            with open(history_file, 'r') as f:
                self.backup_history = json.load(f)
    
    def cleanup_old_backups(self):
        """清理旧备份"""
        if not self.backup_history:
            return
        
        cutoff_date = datetime.now() - timedelta(days=self.config.retention_days)
        
        to_remove = []
        for record in self.backup_history:
            backup_date = datetime.fromisoformat(record['timestamp'])
            if backup_date < cutoff_date:
                to_remove.append(record)
        
        # 保留最近的max_backups个
        sorted_history = sorted(self.backup_history, 
                               key=lambda x: x['timestamp'], 
                               reverse=True)
        to_remove.extend(sorted_history[self.config.max_backups:])
        
        # 删除文件
        for record in to_remove:
            try:
                if os.path.exists(record['path']):
                    os.remove(record['path'])
                    logger.info(f"删除旧备份: {record['path']}")
            except Exception as e:
                logger.error(f"删除备份失败: {e}")
        
        # 更新历史
        self.backup_history = [r for r in self.backup_history if r not in to_remove]
        self._save_history()
        
        logger.info(f"清理完成，删除{len(to_remove)}个旧备份")
    
    def restore_backup(self, backup_path: str, restore_path: str) -> bool:
        """
        恢复备份
        
        Args:
            backup_path: 备份文件路径
            restore_path: 恢复目标路径
            
        Returns:
            是否成功
        """
        try:
            logger.info(f"开始恢复备份: {backup_path} -> {restore_path}")
            
            # 解压如果需要
            if backup_path.endswith('.gz'):
                with gzip.open(backup_path, 'rb') as f_in:
                    with open(restore_path, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
            elif backup_path.endswith('.tar.gz'):
                shutil.unpack_archive(backup_path, restore_path)
            else:
                shutil.copy2(backup_path, restore_path)
            
            logger.info(f"备份恢复完成: {restore_path}")
            return True
            
        except Exception as e:
            logger.error(f"备份恢复失败: {e}")
            return False
    
    def get_backup_list(self) -> List[Dict]:
        """获取备份列表"""
        return sorted(self.backup_history, 
                     key=lambda x: x['timestamp'], 
                     reverse=True)
    
    def get_stats(self) -> Dict:
        """获取统计"""
        total_size = sum(r['size'] for r in self.backup_history)
        
        return {
            'total_backups': len(self.backup_history),
            'total_size_mb': total_size / 1024 / 1024,
            'backup_dir': str(self.backup_dir),
            'retention_days': self.config.retention_days,
            'last_backup': self.backup_history[-1]['timestamp'] if self.backup_history else None
        }


class AutoBackupService:
    """
    自动备份服务
    
    定时执行备份
    """
    
    def __init__(self, backup_manager: BackupManager):
        self.manager = backup_manager
        self.running = False
        self.thread: Optional[threading.Thread] = None
    
    def start(self, db_paths: List[str], config_files: List[str]):
        """启动自动备份服务"""
        if self.running:
            return
        
        self.running = True
        self.thread = threading.Thread(
            target=self._backup_loop,
            args=(db_paths, config_files),
            daemon=True
        )
        self.thread.start()
        logger.info("自动备份服务启动")
    
    def stop(self):
        """停止自动备份服务"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("自动备份服务停止")
    
    def _backup_loop(self, db_paths: List[str], config_files: List[str]):
        """备份循环"""
        while self.running:
            try:
                now = datetime.now()
                
                # 检查是否需要全量备份
                if (self.manager.last_full_backup is None or
                    now - self.manager.last_full_backup >= 
                    timedelta(hours=self.manager.config.full_backup_interval_hours)):
                    
                    logger.info("执行定时全量备份")
                    
                    # 备份数据库
                    for db_path in db_paths:
                        if os.path.exists(db_path):
                            self.manager.backup_database(db_path)
                    
                    # 备份配置
                    if config_files:
                        self.manager.backup_config(config_files)
                    
                    self.manager.last_full_backup = now
                
                # 清理旧备份
                self.manager.cleanup_old_backups()
                
            except Exception as e:
                logger.error(f"自动备份错误: {e}")
            
            # 每小时检查一次
            time.sleep(3600)


# 便捷函数
def create_backup_manager(backup_dir: str = "backups") -> BackupManager:
    """创建备份管理器"""
    config = BackupConfig(backup_dir=backup_dir)
    return BackupManager(config)


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    print("="*60)
    print("备份管理器测试")
    print("="*60)
    
    # 创建管理器
    manager = create_backup_manager("test_backups")
    
    # 创建测试数据库
    print("\n1. 创建测试数据库")
    test_db = "test_strategy.db"
    conn = sqlite3.connect(test_db)
    conn.execute("CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY, data TEXT)")
    conn.execute("INSERT INTO test (data) VALUES ('test data')")
    conn.commit()
    conn.close()
    print(f"  数据库: {test_db}")
    
    # 备份数据库
    print("\n2. 备份数据库")
    backup_path = manager.backup_database(test_db)
    print(f"  备份路径: {backup_path}")
    
    # 创建测试配置
    print("\n3. 备份配置文件")
    test_config = ["config/strategy.yaml", "config/monitor.json"]
    # 创建虚拟配置
    os.makedirs("config", exist_ok=True)
    with open("config/strategy.yaml", "w") as f:
        f.write("test: config")
    
    config_backup = manager.backup_config(test_config)
    print(f"  配置备份: {config_backup}")
    
    # 查看备份列表
    print("\n4. 备份列表")
    backups = manager.get_backup_list()
    for b in backups:
        print(f"  - {b['name']} ({b['type']}, {b['size']} bytes)")
    
    # 统计
    print("\n5. 备份统计")
    stats = manager.get_stats()
    print(f"  总备份数: {stats['total_backups']}")
    print(f"  总大小: {stats['total_size_mb']:.2f} MB")
    
    # 恢复测试
    print("\n6. 恢复测试")
    restore_path = "restored_strategy.db"
    success = manager.restore_backup(backup_path, restore_path)
    print(f"  恢复结果: {'✅ 成功' if success else '❌ 失败'}")
    
    # 验证恢复
    if success and os.path.exists(restore_path):
        conn = sqlite3.connect(restore_path)
        cursor = conn.execute("SELECT * FROM test")
        row = cursor.fetchone()
        print(f"  数据验证: {row}")
        conn.close()
    
    # 清理
    print("\n7. 清理测试文件")
    for f in [test_db, restore_path]:
        if os.path.exists(f):
            os.remove(f)
    shutil.rmtree("config", ignore_errors=True)
    shutil.rmtree("test_backups", ignore_errors=True)
    
    print("\n" + "="*60)
