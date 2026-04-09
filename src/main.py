"""
S001-Pro V3 主入口
职责: 初始化 + 主循环 +  graceful shutdown
"""

import sys
import time
import signal
import logging
import argparse
from datetime import datetime
from threading import Thread

from config import get_config
from database import get_db, DatabaseManager
from scanner import Scanner
from engine import Engine
from trader import Trader
from monitor import Monitor

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    handlers=[
        logging.FileHandler('data/strategy.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class Strategy:
    """策略主类"""
    
    def __init__(self):
        self.cfg = get_config()
        self.db = get_db()
        self.scanner = Scanner()
        self.engine = Engine()
        self.trader = Trader()
        self.monitor = Monitor()
        
        self.running = False
        self.web_thread = None
        
        # 统计
        self.stats = {
            'ticks': 0,
            'trades_today': 0,
            'start_time': None
        }
    
    def initialize(self):
        """初始化策略"""
        logger.info(f"=" * 60)
        logger.info(f"S001-Pro V3 Starting...")
        logger.info(f"Version: {self.cfg.version}")
        logger.info(f"=" * 60)
        
        # 1. 验证配置
        errors = self.cfg.validate()
        if errors:
            logger.error("Configuration errors:")
            for e in errors:
                logger.error(f"  - {e}")
            sys.exit(1)
        
        # 2. 初始化数据库
        logger.info("Initializing database...")
        db_manager = DatabaseManager(self.cfg.database.state_db)
        
        # 3. 初始化引擎
        logger.info("Initializing engine...")
        self.engine.initialize()
        
        # 4. 同步持仓
        logger.info("Syncing positions with exchange...")
        sync_result = self.trader.sync_positions()
        if not sync_result['synced']:
            logger.warning(f"Position discrepancies found: {len(sync_result['discrepancies'])}")
            for d in sync_result['discrepancies']:
                logger.warning(f"  - {d}")
        
        # 5. 执行首次扫描
        logger.info("Running initial scan...")
        self._run_scan()
        
        self.stats['start_time'] = datetime.now()
        logger.info("Initialization completed successfully")
    
    def _run_scan(self):
        """执行扫描"""
        try:
            # 扫描主池
            primary_pairs = self.scanner.scan("primary")
            logger.info(f"Primary pool: {len(primary_pairs)} pairs")
            
            # 扫描次池
            secondary_pairs = self.scanner.scan("secondary")
            logger.info(f"Secondary pool: {len(secondary_pairs)} pairs")
            
            # 发送通知
            self.monitor.notify_event('scan_completed', {
                'primary_count': len(primary_pairs),
                'secondary_count': len(secondary_pairs),
                'timestamp': datetime.now().isoformat()
            })
            
        except Exception as e:
            logger.error(f"Scan failed: {e}")
            self.monitor.notify_event('error', {
                'type': 'scan_failed',
                'message': str(e)
            })
    
    def _trading_loop(self):
        """交易主循环"""
        logger.info("Starting trading loop...")
        
        last_scan_time = time.time()
        scan_interval = 3600  # 每小时扫描一次
        
        while self.running:
            try:
                loop_start = time.time()
                
                # 1. 检查是否需要重新扫描
                if time.time() - last_scan_time > scan_interval:
                    logger.info("Scheduled scan triggered")
                    self._run_scan()
                    last_scan_time = time.time()
                
                # 2. 处理主池
                self.engine.process_tick("primary")
                
                # 3. 处理次池 (每5个tick处理一次)
                if self.stats['ticks'] % 5 == 0:
                    self.engine.process_tick("secondary")
                
                # 4. 更新统计
                self.stats['ticks'] += 1
                
                # 5. 计算循环耗时，动态调整sleep
                loop_duration = time.time() - loop_start
                sleep_time = max(0, self.cfg.trading.loop_interval - loop_duration)
                
                if sleep_time > 0:
                    time.sleep(sleep_time)
                
                # 每100个tick打印状态
                if self.stats['ticks'] % 100 == 0:
                    self._print_status()
                    
            except Exception as e:
                logger.error(f"Error in trading loop: {e}", exc_info=True)
                self.monitor.notify_event('error', {
                    'type': 'trading_loop_error',
                    'message': str(e)
                })
                time.sleep(5)  # 错误后等待5秒继续
    
    def _print_status(self):
        """打印状态"""
        positions = self.engine.position_mgr.get_all_positions()
        primary_count = self.engine.position_mgr.get_position_count("primary")
        secondary_count = self.engine.position_mgr.get_position_count("secondary")
        
        unrealized = sum(p.unrealized_pnl or 0 for p in positions)
        
        logger.info(f"Status | Ticks: {self.stats['ticks']} | "
                   f"Positions: {len(positions)} (P:{primary_count} S:{secondary_count}) | "
                   f"Unrealized: {unrealized:+.2f} USDT")
    
    def _start_web_server(self):
        """启动Web服务器 (在独立线程)"""
        try:
            self.monitor.start_web()
        except Exception as e:
            logger.error(f"Web server error: {e}")
    
    def run(self):
        """运行策略"""
        self.running = True
        
        # 启动Web服务器
        logger.info(f"Starting web dashboard on {self.cfg.web['host']}:{self.cfg.web['port']}")
        self.web_thread = Thread(target=self._start_web_server, daemon=True)
        self.web_thread.start()
        
        # 启动交易循环
        try:
            self._trading_loop()
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
        finally:
            self.shutdown()
    
    def shutdown(self):
        """优雅关闭"""
        logger.info("Shutting down strategy...")
        self.running = False
        
        # 保存状态
        logger.info("Saving state...")
        # 数据库会自动保存，这里可以添加额外的清理
        
        # 打印最终统计
        duration = datetime.now() - self.stats['start_time'] if self.stats['start_time'] else None
        logger.info(f"Strategy stopped. Total ticks: {self.stats['ticks']}")
        if duration:
            logger.info(f"Runtime: {duration}")
        
        logger.info("Shutdown completed")


def signal_handler(signum, frame):
    """信号处理"""
    logger.info(f"Received signal {signum}")
    sys.exit(0)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='S001-Pro V3 Strategy')
    parser.add_argument('--config', '-c', default='config/config.yaml', help='Config file path')
    parser.add_argument('--scan-only', action='store_true', help='Run scan only and exit')
    parser.add_argument('--dry-run', action='store_true', help='Dry run mode (no real trading)')
    
    args = parser.parse_args()
    
    # 注册信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 创建策略实例
    strategy = Strategy()
    
    # 初始化
    strategy.initialize()
    
    if args.scan_only:
        logger.info("Scan-only mode, exiting")
        return
    
    # 运行
    strategy.run()


if __name__ == "__main__":
    main()
