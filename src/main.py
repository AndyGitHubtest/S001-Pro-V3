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
from visualization import (
    tracer, trace_step, trace_context, TracedThread,
    safe_thread_wrapper, log_info, log_error, heartbeat
)

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

# 启动可视化追踪
tracer.start()


class Strategy:
    """策略主类"""
    
    def __init__(self):
        self.cfg = get_config()
        self.db = get_db()
        self.scanner = Scanner()
        # 先创建Trader，再传给Engine
        self.trader = Trader()
        self.engine = Engine(trader=self.trader)
        self.monitor = Monitor()
        
        self.running = False
        self.web_thread = None
        
        # 统计
        self.stats = {
            'ticks': 0,
            'trades_today': 0,
            'start_time': None
        }
    
    @trace_step("Strategy", "初始化")
    def initialize(self):
        """初始化策略"""
        logger.info(f"=" * 60)
        logger.info(f"S001-Pro V3 Starting...")
        logger.info(f"Version: {self.cfg.version}")
        logger.info(f"=" * 60)
        
        # 注册主模块心跳
        tracer.register_heartbeat("Strategy")
        
        # 1. 验证配置
        log_info("Strategy", "验证配置")
        errors = self.cfg.validate()
        if errors:
            log_error("Strategy", "配置验证失败", Exception(str(errors)))
            for e in errors:
                logger.error(f"  - {e}")
            sys.exit(1)
        log_info("Strategy", "配置验证通过", error_count=0)
        
        # 2. 初始化数据库
        with trace_context("Strategy", "初始化数据库"):
            db_manager = DatabaseManager(self.cfg.database.state_db)
            log_info("Strategy", "数据库初始化完成", db_path=self.cfg.database.state_db)
        
        # 3. 恢复遗留订单（重启后的关键步骤）
        with trace_context("Strategy", "恢复遗留订单"):
            from order_recovery import perform_recovery
            recovery_report = perform_recovery(self.trader.api)
            log_info("Strategy", "订单恢复完成",
                    orders_found=recovery_report['orders_found'],
                    orders_cancelled=recovery_report['orders_cancelled'],
                    orders_recovered=recovery_report['orders_recovered'],
                    orphan_orders=recovery_report['orphan_orders'])
            
            if recovery_report['manual_review_required']:
                log_error("Strategy", "存在需要人工审核的订单",
                         Exception("请检查恢复报告"),
                         count=len(recovery_report['manual_review_required']))
        
        # 4. 初始化引擎
        with trace_context("Strategy", "初始化引擎"):
            self.engine.initialize()
            log_info("Strategy", "引擎初始化完成")
        
        # 4. 同步持仓
        with trace_context("Strategy", "同步持仓"):
            sync_result = self.trader.sync_positions()
            log_info("Strategy", "持仓同步完成", 
                    synced=sync_result['synced'], 
                    discrepancies=len(sync_result['discrepancies']))
            if not sync_result['synced']:
                for d in sync_result['discrepancies']:
                    logger.warning(f"  - {d}")
        
        # 5. 执行首次扫描
        with trace_context("Strategy", "首次扫描"):
            self._run_scan()
        
        self.stats['start_time'] = datetime.now()
        log_info("Strategy", "初始化完成", 
                start_time=self.stats['start_time'].isoformat())
        logger.info("Initialization completed successfully")
    
    @trace_step("Strategy", "执行扫描")
    def _run_scan(self):
        """执行扫描 — 单池模式"""
        log_info("Strategy", "开始扫描")
        
        try:
            with trace_context("Strategy", "扫描配对"):
                pairs = self.scanner.scan("primary")
                log_info("Strategy", "扫描完成", pair_count=len(pairs))
                logger.info(f"Scan result: {len(pairs)} pairs")
            
            # 推送TG通知 — 带漏斗数据和配对详情
            funnel = getattr(self.scanner, '_last_funnel', None)
            results = getattr(self.scanner, '_last_results', [])
            duration_s = getattr(self.scanner, '_last_duration_s', 0)
            
            self.monitor.notify_event('scan_completed', {
                'pairs': [
                    {
                        'symbol_a': m.symbol_a, 'symbol_b': m.symbol_b,
                        'score': m.score, 'pf': m.pf, 
                        'z_entry': m.z_entry, 'z_exit': m.z_exit,
                        'trades_count': m.trades_count
                    } for m in results
                ],
                'funnel': {
                    'candidates': funnel.candidates if funnel else 0,
                    'l1': funnel.layer1_passed if funnel else 0,
                    'l2': funnel.layer2_passed if funnel else 0,
                    'l3': funnel.layer3_passed if funnel else 0,
                    'backtest': funnel.backtest_passed if funnel else 0,
                },
                'duration_s': duration_s,
                'timestamp': datetime.now().isoformat()
            })
            
        except Exception as e:
            log_error("Strategy", "扫描失败", e)
            logger.error(f"Scan failed: {e}")
            self.monitor.notify_event('error', {
                'type': 'scan_failed',
                'message': str(e)
            })
    
    @safe_thread_wrapper("Strategy")
    def _trading_loop_iteration(self, last_scan_time_ref: list, scan_interval: int):
        """单次交易循环迭代"""
        loop_start = time.time()
        
        # 更新心跳
        heartbeat("Strategy")
        
        # 1. 检查是否需要重新扫描
        if time.time() - last_scan_time_ref[0] > scan_interval:
            log_info("Strategy", "触发定时扫描")
            self._run_scan()
            last_scan_time_ref[0] = time.time()
        
        # 2. 处理交易信号
        with trace_context("Strategy", "处理Tick"):
            self.engine.process_tick("primary")
        
        # 4. 更新统计
        self.stats['ticks'] += 1
        
        # 5. 计算循环耗时
        loop_duration = time.time() - loop_start
        
        return loop_duration
    
    def _get_last_scan_time(self) -> float:
        """从数据库获取上次扫描时间戳"""
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(scan_time) FROM scan_history")
            result = cursor.fetchone()
            if result and result[0]:
                from datetime import datetime
                dt = datetime.fromisoformat(result[0].replace(' ', 'T'))
                return dt.timestamp()
        except Exception as e:
            logger.warning(f"无法读取上次扫描时间: {e}")
        return time.time()  # 如果失败，使用当前时间
    
    def _trading_loop(self):
        """交易主循环 - 带错误隔离"""
        logger.info("Starting trading loop...")
        tracer.register_heartbeat("TradingLoop")
        
        # 从数据库读取上次扫描时间，确保定时准确
        last_scan_time = [self._get_last_scan_time()]
        scan_interval = 3600  # 每小时扫描一次
        logger.info(f"Last scan time from DB: {datetime.fromtimestamp(last_scan_time[0]).isoformat()}")
        consecutive_errors = 0
        max_consecutive_errors = 10
        
        while self.running:
            try:
                # 使用安全包装器执行单次迭代
                loop_duration = self._trading_loop_iteration(last_scan_time, scan_interval)
                
                if loop_duration is None:
                    # 迭代失败
                    consecutive_errors += 1
                    log_error("Strategy", "交易循环迭代失败", 
                             Exception(f"连续错误 {consecutive_errors}/{max_consecutive_errors}"))
                    
                    if consecutive_errors >= max_consecutive_errors:
                        logger.critical("连续错误过多，停止策略")
                        self.monitor.notify_event('error', {
                            'type': 'too_many_errors',
                            'message': f'连续{consecutive_errors}次迭代失败'
                        })
                        break
                    
                    time.sleep(5)
                    continue
                
                # 成功，重置错误计数
                consecutive_errors = 0
                
                # 动态调整sleep
                sleep_time = max(0, self.cfg.trading.loop_interval - loop_duration)
                if sleep_time > 0:
                    time.sleep(sleep_time)
                
                # 每100个tick打印状态
                if self.stats['ticks'] % 100 == 0:
                    self._print_status()
                    
            except Exception as e:
                # 最终防线：捕获所有异常
                log_error("Strategy", "交易循环异常", e)
                logger.error(f"Error in trading loop: {e}", exc_info=True)
                self.monitor.notify_event('error', {
                    'type': 'trading_loop_error',
                    'message': str(e)
                })
                time.sleep(5)  # 错误后等待5秒继续
    
    @trace_step("Strategy", "打印状态")
    def _print_status(self):
        """打印状态"""
        positions = self.engine.position_mgr.get_all_positions()
        unrealized = sum(p.unrealized_pnl or 0 for p in positions)
        
        status_msg = f"Status | Ticks: {self.stats['ticks']} | "
        status_msg += f"Positions: {len(positions)} | "
        status_msg += f"Unrealized: {unrealized:+.2f} USDT"
        
        logger.info(status_msg)
        log_info("Strategy", "状态报告", 
                ticks=self.stats['ticks'],
                total_positions=len(positions),
                primary=primary_count,
                secondary=secondary_count,
                unrealized=round(unrealized, 2))
    
    def _start_web_server(self):
        """启动Web服务器 V2 (在独立线程)"""
        try:
            # 使用新的V2 Web服务器
            from monitoring.web_server_v2 import create_web_server
            import uvicorn
            
            app = create_web_server(strategy=self, db=self.db, config=self.cfg)
            uvicorn.run(
                app,
                host=self.cfg.web.get('host', '0.0.0.0'),
                port=self.cfg.web.get('port', 8000),
                log_level='warning'
            )
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
    
    @trace_step("Strategy", "优雅关闭")
    def shutdown(self):
        """优雅关闭"""
        logger.info("Shutting down strategy...")
        log_info("Strategy", "开始关闭流程")
        
        self.running = False
        
        # 保存停机快照（用于重启后恢复）
        with trace_context("Strategy", "保存停机快照"):
            from order_recovery import GracefulShutdownHandler
            handler = GracefulShutdownHandler(self.db)
            snapshot = handler.prepare_shutdown()
            log_info("Strategy", "停机快照已保存",
                    positions=len(snapshot['open_positions']))
        
        # 停止可视化追踪
        tracer.stop()
        
        # 打印最终统计
        duration = datetime.now() - self.stats['start_time'] if self.stats['start_time'] else None
        
        log_info("Strategy", "最终统计", 
                total_ticks=self.stats['ticks'],
                runtime=str(duration) if duration else "N/A")
        
        logger.info(f"Strategy stopped. Total ticks: {self.stats['ticks']}")
        if duration:
            logger.info(f"Runtime: {duration}")
        
        logger.info("Shutdown completed")
        log_info("Strategy", "关闭完成")


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
