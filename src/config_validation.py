"""
配置验证器
使用Pydantic验证配置，防止错误配置导致的问题
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, validator, root_validator
from enum import Enum
import yaml
import json
import logging

logger = logging.getLogger(__name__)


class TradingMode(str, Enum):
    """交易模式"""
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class LogLevel(str, Enum):
    """日志级别"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class RiskConfig(BaseModel):
    """风控配置"""
    max_positions: int = Field(default=5, ge=1, le=20, description="最大持仓数")
    max_drawdown_pct: float = Field(default=10.0, ge=1.0, le=50.0, description="最大回撤%")
    daily_loss_limit: float = Field(default=100.0, ge=0, description="日亏损限制")
    max_leverage: float = Field(default=5.0, ge=1.0, le=20.0, description="最大杠杆")
    position_size_pct: float = Field(default=10.0, ge=1.0, le=100.0, description="仓位占比%")
    
    @validator('max_drawdown_pct')
    def validate_drawdown(cls, v):
        if v > 20:
            logger.warning(f"最大回撤设置较高: {v}%，建议不超过20%")
        return v
    
    @validator('daily_loss_limit')
    def validate_loss_limit(cls, v):
        if v == 0:
            logger.warning("日亏损限制为0，无限制")
        return v


class SignalConfig(BaseModel):
    """信号配置"""
    timeframe: str = Field(default="15m", regex=r"^\d+[mhd]$")
    z_entry: float = Field(default=2.0, ge=1.0, le=5.0)
    z_exit: float = Field(default=0.5, ge=0.1, le=2.0)
    z_stop: float = Field(default=3.0, ge=2.0, le=5.0)
    lookback_days: int = Field(default=90, ge=30, le=365)
    
    @validator('z_exit')
    def validate_exit(cls, v, values):
        if 'z_entry' in values and v >= values['z_entry']:
            raise ValueError(f"z_exit({v})必须小于z_entry({values['z_entry']})")
        return v
    
    @validator('z_stop')
    def validate_stop(cls, v, values):
        if 'z_entry' in values and v <= values['z_entry']:
            raise ValueError(f"z_stop({v})必须大于z_entry({values['z_entry']})")
        return v


class ExchangeConfig(BaseModel):
    """交易所配置"""
    name: str = Field(..., description="交易所名称")
    api_key: Optional[str] = Field(None, description="API Key")
    secret: Optional[str] = Field(None, description="API Secret")
    passphrase: Optional[str] = Field(None, description="密码短语(OKX等)")
    sandbox: bool = Field(default=False, description="是否测试网")
    testnet: bool = Field(default=False, description="是否测试网络")
    
    @validator('name')
    def validate_name(cls, v):
        supported = ['binance', 'okx', 'bybit', 'bitget']
        if v.lower() not in supported:
            raise ValueError(f"不支持的交易所: {v}，支持: {supported}")
        return v.lower()
    
    @root_validator
    def validate_credentials(cls, values):
        mode = values.get('sandbox', False) or values.get('testnet', False)
        if not mode:  # 实盘需要凭证
            if not values.get('api_key') or not values.get('secret'):
                raise ValueError("实盘交易需要提供api_key和secret")
        return values


class DatabaseConfig(BaseModel):
    """数据库配置"""
    path: str = Field(default="data/strategy.db")
    max_connections: int = Field(default=10, ge=1, le=100)
    timeout: float = Field(default=30.0, ge=1.0, le=300.0)


class NotificationConfig(BaseModel):
    """通知配置"""
    enabled: bool = Field(default=True)
    telegram_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    notify_on_trade: bool = Field(default=True)
    notify_on_error: bool = Field(default=True)
    notify_on_daily_report: bool = Field(default=False)


class MonitoringConfig(BaseModel):
    """监控配置"""
    enabled: bool = Field(default=True)
    check_interval: int = Field(default=60, ge=10, le=3600)
    memory_warning_threshold: float = Field(default=70.0, ge=50.0, le=90.0)
    disk_warning_threshold: float = Field(default=80.0, ge=50.0, le=95.0)


class StrategyConfig(BaseModel):
    """策略配置"""
    name: str = Field(default="S001-Pro-V3")
    version: str = Field(default="3.0.0")
    mode: TradingMode = Field(default=TradingMode.PAPER)
    log_level: LogLevel = Field(default=LogLevel.INFO)
    
    # 子配置
    risk: RiskConfig = Field(default_factory=RiskConfig)
    signal: SignalConfig = Field(default_factory=SignalConfig)
    exchange: ExchangeConfig
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    notification: NotificationConfig = Field(default_factory=NotificationConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    
    @validator('mode', pre=True)
    def validate_mode(cls, v):
        if isinstance(v, str):
            return TradingMode(v.lower())
        return v
    
    @root_validator
    def validate_live_mode(cls, values):
        mode = values.get('mode')
        if mode == TradingMode.LIVE:
            # 实盘模式额外检查
            risk = values.get('risk')
            if risk and risk.max_positions > 10:
                logger.warning("实盘模式建议最大持仓不超过10")
        return values


class ConfigValidator:
    """
    配置验证器
    
    验证并加载配置文件
    """
    
    def __init__(self, config_path: str = None):
        self.config_path = config_path
        self.config: Optional[StrategyConfig] = None
        self.errors: List[str] = []
    
    def load_from_yaml(self, path: str) -> StrategyConfig:
        """从YAML加载配置"""
        with open(path, 'r') as f:
            data = yaml.safe_load(f)
        
        return self.validate(data)
    
    def load_from_json(self, path: str) -> StrategyConfig:
        """从JSON加载配置"""
        with open(path, 'r') as f:
            data = json.load(f)
        
        return self.validate(data)
    
    def validate(self, data: Dict) -> StrategyConfig:
        """
        验证配置数据
        
        Args:
            data: 配置字典
            
        Returns:
            验证后的配置对象
            
        Raises:
            ValueError: 配置验证失败
        """
        try:
            self.config = StrategyConfig(**data)
            self.errors = []
            logger.info("配置验证通过")
            return self.config
            
        except Exception as e:
            self.errors = [str(e)]
            logger.error(f"配置验证失败: {e}")
            raise ValueError(f"配置验证失败: {e}")
    
    def validate_partial(self, data: Dict) -> tuple:
        """
        部分验证，返回错误列表
        
        Returns:
            (是否通过, 错误列表)
        """
        try:
            StrategyConfig(**data)
            return True, []
        except Exception as e:
            errors = str(e).split('\n')
            return False, errors
    
    def get_safe_config(self) -> Dict:
        """获取安全配置 (隐藏敏感信息)"""
        if not self.config:
            return {}
        
        data = self.config.dict()
        
        # 隐藏敏感信息
        if 'exchange' in data:
            if data['exchange'].get('api_key'):
                data['exchange']['api_key'] = '***'
            if data['exchange'].get('secret'):
                data['exchange']['secret'] = '***'
            if data['exchange'].get('passphrase'):
                data['exchange']['passphrase'] = '***'
        
        if 'notification' in data:
            if data['notification'].get('telegram_token'):
                data['notification']['telegram_token'] = '***'
        
        return data
    
    def generate_template(self, mode: TradingMode = TradingMode.PAPER) -> str:
        """生成配置模板"""
        config = StrategyConfig(
            mode=mode,
            exchange=ExchangeConfig(name="binance", sandbox=True)
        )
        
        return yaml.dump(config.dict(), default_flow_style=False, allow_unicode=True)


def load_and_validate_config(path: str) -> StrategyConfig:
    """便捷加载配置"""
    validator = ConfigValidator()
    
    if path.endswith('.yaml') or path.endswith('.yml'):
        return validator.load_from_yaml(path)
    elif path.endswith('.json'):
        return validator.load_from_json(path)
    else:
        raise ValueError(f"不支持的配置文件格式: {path}")


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    print("="*60)
    print("配置验证测试")
    print("="*60)
    
    # 测试1: 有效配置
    print("\n1. 有效配置测试")
    valid_config = {
        'mode': 'paper',
        'exchange': {
            'name': 'binance',
            'sandbox': True
        },
        'risk': {
            'max_positions': 5,
            'max_drawdown_pct': 15
        },
        'signal': {
            'z_entry': 2.5,
            'z_exit': 0.5,
            'z_stop': 3.5
        }
    }
    
    try:
        config = StrategyConfig(**valid_config)
        print(f"  ✅ 验证通过")
        print(f"     模式: {config.mode}")
        print(f"     交易所: {config.exchange.name}")
        print(f"     最大持仓: {config.risk.max_positions}")
    except Exception as e:
        print(f"  ❌ 验证失败: {e}")
    
    # 测试2: 无效配置 (参数矛盾)
    print("\n2. 无效配置测试 (z_exit >= z_entry)")
    invalid_config = {
        'mode': 'paper',
        'exchange': {'name': 'binance', 'sandbox': True},
        'signal': {
            'z_entry': 2.0,
            'z_exit': 2.5,  # 错误: 大于z_entry
            'z_stop': 1.5   # 错误: 小于z_entry
        }
    }
    
    try:
        config = StrategyConfig(**invalid_config)
        print(f"  ✅ 验证通过 (意外)")
    except Exception as e:
        print(f"  ✅ 正确识别错误: {str(e)[:80]}...")
    
    # 测试3: 实盘模式缺少凭证
    print("\n3. 实盘模式验证 (缺少API凭证)")
    live_config = {
        'mode': 'live',
        'exchange': {'name': 'binance'}  # 缺少api_key和secret
    }
    
    try:
        config = StrategyConfig(**live_config)
        print(f"  ❌ 验证通过 (意外)")
    except Exception as e:
        print(f"  ✅ 正确识别错误: {str(e)[:80]}...")
    
    # 测试4: 生成模板
    print("\n4. 配置模板生成")
    validator = ConfigValidator()
    template = validator.generate_template()
    print(f"  模板预览:\n{template[:500]}...")
    
    print("\n" + "="*60)
