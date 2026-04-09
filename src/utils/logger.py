"""
日志工具
带轮转的日志配置
"""
import os
import sys
import logging
import logging.handlers
from pathlib import Path
from typing import Optional
from datetime import datetime


def setup_logger(
    name: str = "s001_pro",
    log_dir: str = "logs",
    level: int = logging.INFO,
    console_output: bool = True,
    file_output: bool = True,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 10,
    format_string: str = None
) -> logging.Logger:
    """
    配置日志
    
    Args:
        name: 日志名称
        log_dir: 日志目录
        level: 日志级别
        console_output: 是否输出到控制台
        file_output: 是否输出到文件
        max_bytes: 单个日志文件最大大小
        backup_count: 保留的备份文件数
        format_string: 自定义格式
        
    Returns:
        Logger实例
    """
    # 创建logger
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # 清除现有处理器
    logger.handlers = []
    
    # 默认格式
    if format_string is None:
        format_string = (
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        )
    
    formatter = logging.Formatter(format_string)
    
    # 控制台输出
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    # 文件输出 (带轮转)
    if file_output:
        # 确保日志目录存在
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        
        # 主日志文件 (按大小轮转)
        main_log = log_path / "strategy.log"
        file_handler = logging.handlers.RotatingFileHandler(
            main_log,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        # 错误日志单独保存 (按天轮转)
        error_log = log_path / "error.log"
        error_handler = logging.handlers.TimedRotatingFileHandler(
            error_log,
            when='midnight',
            interval=1,
            backupCount=30,
            encoding='utf-8'
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        logger.addHandler(error_handler)
        
        # 交易日志单独保存
        trade_log = log_path / "trades.log"
        trade_handler = logging.handlers.RotatingFileHandler(
            trade_log,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        trade_handler.setLevel(logging.INFO)
        trade_handler.setFormatter(formatter)
        # 使用过滤器只记录交易相关日志
        trade_filter = logging.Filter()
        trade_filter.filter = lambda record: 'trade' in record.getMessage().lower() or 'order' in record.getMessage().lower()
        trade_handler.addFilter(trade_filter)
        logger.addHandler(trade_handler)
    
    return logger


class ColoredFormatter(logging.Formatter):
    """带颜色的日志格式"""
    
    COLORS = {
        'DEBUG': '\033[36m',     # 青色
        'INFO': '\033[32m',      # 绿色
        'WARNING': '\033[33m',   # 黄色
        'ERROR': '\033[31m',     # 红色
        'CRITICAL': '\033[35m',  # 紫色
        'RESET': '\033[0m'
    }
    
    def format(self, record):
        # 保存原始levelname
        original_levelname = record.levelname
        
        # 添加颜色
        if record.levelname in self.COLORS:
            record.levelname = f"{self.COLORS[record.levelname]}{record.levelname}{self.COLORS['RESET']}"
        
        result = super().format(record)
        
        # 恢复原始levelname
        record.levelname = original_levelname
        
        return result


def setup_colored_logger(name: str = "s001_pro", level: int = logging.INFO) -> logging.Logger:
    """配置带颜色的日志"""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # 清除现有处理器
    logger.handlers = []
    
    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    
    formatter = ColoredFormatter(
        "%(asctime)s | %(levelname)-18s | %(name)s | %(message)s"
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger


class StructuredLogFormatter(logging.Formatter):
    """结构化日志格式 (JSON)"""
    
    def format(self, record):
        import json
        
        log_data = {
            'timestamp': datetime.fromtimestamp(record.created).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
        }
        
        # 添加额外字段
        if hasattr(record, 'extra_data'):
            log_data.update(record.extra_data)
        
        # 添加异常信息
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        return json.dumps(log_data, ensure_ascii=False)


def setup_structured_logger(
    name: str = "s001_pro",
    log_dir: str = "logs",
    level: int = logging.INFO
) -> logging.Logger:
    """
    配置结构化日志
    
    输出JSON格式，便于ELK等系统解析
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # 清除现有处理器
    logger.handlers = []
    
    # 确保日志目录存在
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    
    # JSON日志文件
    json_log = log_path / "structured.log"
    handler = logging.handlers.RotatingFileHandler(
        json_log,
        maxBytes=10*1024*1024,  # 10MB
        backupCount=10,
        encoding='utf-8'
    )
    handler.setLevel(level)
    handler.setFormatter(StructuredLogFormatter())
    logger.addHandler(handler)
    
    # 控制台输出 (人类可读)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s"
    ))
    logger.addHandler(console_handler)
    
    return logger


class TradeLogger:
    """交易专用日志"""
    
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # 创建专用logger
        self.logger = logging.getLogger("s001_pro.trade")
        self.logger.setLevel(logging.INFO)
        
        # 清除现有处理器
        self.logger.handlers = []
        
        # 文件处理器
        log_file = self.log_dir / "trades.log"
        handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10*1024*1024,
            backupCount=30,
            encoding='utf-8'
        )
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(message)s"
        ))
        self.logger.addHandler(handler)
    
    def log_order(self, symbol: str, side: str, qty: float, price: float, 
                  order_type: str = "limit", status: str = "placed"):
        """记录订单"""
        self.logger.info(
            f"ORDER | {symbol} | {side} | qty={qty} | price={price} | "
            f"type={order_type} | status={status}"
        )
    
    def log_fill(self, symbol: str, side: str, qty: float, price: float, 
                 fee: float, pnl: float = 0):
        """记录成交"""
        self.logger.info(
            f"FILL | {symbol} | {side} | qty={qty} | price={price} | "
            f"fee={fee} | pnl={pnl}"
        )
    
    def log_position(self, symbol: str, qty: float, avg_price: float, 
                     unrealized_pnl: float):
        """记录持仓"""
        self.logger.info(
            f"POSITION | {symbol} | qty={qty} | avg_price={avg_price} | "
            f"unrealized_pnl={unrealized_pnl}"
        )


# 便捷函数
def get_logger(name: str = "s001_pro") -> logging.Logger:
    """获取logger"""
    return logging.getLogger(name)


def log_trade(symbol: str, side: str, qty: float, price: float):
    """便捷交易日志"""
    logger = get_logger("s001_pro.trade")
    logger.info(f"TRADE: {symbol} {side} {qty} @ {price}")


# 使用示例
if __name__ == "__main__":
    print("="*60)
    print("日志系统测试")
    print("="*60)
    
    # 测试1: 标准日志
    print("\n1. 标准日志")
    logger = setup_logger("test", log_dir="test_logs")
    logger.debug("Debug message")
    logger.info("Info message")
    logger.warning("Warning message")
    logger.error("Error message")
    print("  日志已写入 test_logs/")
    
    # 测试2: 彩色日志
    print("\n2. 彩色日志")
    colored_logger = setup_colored_logger("test_colored")
    colored_logger.info("This is info")
    colored_logger.warning("This is warning")
    colored_logger.error("This is error")
    
    # 测试3: 结构化日志
    print("\n3. 结构化日志 (JSON)")
    struct_logger = setup_structured_logger("test_struct", log_dir="test_logs")
    struct_logger.info("Structured log message", extra={'extra_data': {'key': 'value'}})
    
    # 读取JSON日志
    import json
    with open("test_logs/structured.log", "r") as f:
        for line in f:
            data = json.loads(line)
            print(f"  JSON: {json.dumps(data, indent=2, ensure_ascii=False)[:100]}...")
            break
    
    # 测试4: 交易日志
    print("\n4. 交易日志")
    trade_logger = TradeLogger("test_logs")
    trade_logger.log_order("BTC/USDT", "BUY", 0.1, 50000)
    trade_logger.log_fill("BTC/USDT", "BUY", 0.1, 50000, 2.5)
    trade_logger.log_position("BTC/USDT", 0.1, 50000, 100)
    
    # 清理
    import shutil
    shutil.rmtree("test_logs", ignore_errors=True)
    
    print("\n" + "="*60)
