"""
S001-Pro V3 配置管理
所有参数集中配置，支持热加载
"""

import yaml
import os
from dataclasses import dataclass, field
from typing import Dict, List, Any


@dataclass
class Layer1Config:
    corr_median_min: float = 0.65
    coint_p_max: float = 0.1
    adf_p_max: float = 0.1


@dataclass
class Layer2Config:
    half_life_max: float = 48
    corr_std_max: float = 0.12
    hurst_max: float = 0.6


@dataclass
class Layer3Config:
    zscore_max_min: float = 2.2
    spread_std_min: float = 0.001
    volume_min: int = 3_000_000
    bid_ask_max: float = 0.0002


@dataclass
class ScoringConfig:
    w_coint: float = 0.30
    w_corr: float = 0.20
    w_halflife: float = 0.15
    w_zmax: float = 0.15
    w_stability: float = 0.10
    w_volume: float = 0.10


@dataclass
class PoolConfig:
    timeframe: str
    top_n: int
    capital_ratio: float
    z_entry_default: float
    z_exit_default: float
    z_stop_offset_default: float
    max_per_day: int = 0  # 仅次池使用
    capital_per_position: float = 100.0  # 单对金额
    leverage: int = 5  # 杠杆倍数


@dataclass
class OptimizationConfig:
    coarse: Dict[str, Any] = field(default_factory=dict)
    fine: Dict[str, Any] = field(default_factory=dict)
    early_exit: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskConfig:
    max_total_margin: float = 300.0
    max_single_position: float = 100.0
    max_leverage: int = 5
    max_daily_loss_usdt: float = 50.0
    max_position_loss_ratio: float = 0.02
    circuit_breaker: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": True,
        "consecutive_losses": 3,
        "cooldown_minutes": 60
    })


@dataclass
class ExchangeConfig:
    name: str = "binance"
    sandbox: bool = False
    api_key: str = ""
    api_secret: str = ""


@dataclass
class TradingConfig:
    primary: PoolConfig = field(default_factory=lambda: PoolConfig(
        timeframe="15m", top_n=30, capital_ratio=0.7,
        z_entry_default=2.5, z_exit_default=0.5, z_stop_offset_default=2.0,
        capital_per_position=100, leverage=5
    ))
    secondary: PoolConfig = field(default_factory=lambda: PoolConfig(
        timeframe="5m", top_n=10, capital_ratio=0.3,
        z_entry_default=2.0, z_exit_default=0.4, z_stop_offset_default=1.5,
        max_per_day=3, capital_per_position=100, leverage=5
    ))
    filter: Dict[str, Any] = field(default_factory=lambda: {"timeframe": "30m", "threshold": 1.0})
    max_positions: int = 5
    max_concurrent_per_pool: int = 2
    min_per_pair: float = 50.0
    max_per_pair_ratio: float = 0.20
    max_daily_loss_ratio: float = 0.05
    loop_interval: int = 5


@dataclass
class DatabaseConfig:
    klines_db: str = "data/klines.db"
    state_db: str = "data/strategy.db"
    cache_size: int = 500


@dataclass
class NotificationConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    events: List[str] = field(default_factory=lambda: ["position_opened", "position_closed", "error"])


@dataclass
class Config:
    version: str = "3.0.0"
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    data_core: Dict[str, Any] = field(default_factory=dict)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    layer1: Layer1Config = field(default_factory=Layer1Config)
    layer2: Layer2Config = field(default_factory=Layer2Config)
    layer3: Layer3Config = field(default_factory=Layer3Config)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    optimization: OptimizationConfig = field(default_factory=OptimizationConfig)
    exclusion: Dict[str, Any] = field(default_factory=lambda: {"mode": "soft", "max_per_symbol": 3})
    output: Dict[str, Any] = field(default_factory=lambda: {"min_pf": 1.3, "min_profit": 0})
    web: Dict[str, Any] = field(default_factory=lambda: {"host": "0.0.0.0", "port": 8000, "refresh": 5})
    notification: NotificationConfig = field(default_factory=NotificationConfig)
    logging: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str = "config/config.yaml") -> "Config":
        """从YAML文件加载配置"""
        import re
        
        with open(path, 'r') as f:
            data = yaml.safe_load(f)
        
        # 环境变量覆盖
        if os.getenv('TELEGRAM_BOT_TOKEN'):
            data['notification']['telegram']['bot_token'] = os.getenv('TELEGRAM_BOT_TOKEN')
        if os.getenv('TELEGRAM_CHAT_ID'):
            data['notification']['telegram']['chat_id'] = os.getenv('TELEGRAM_CHAT_ID')
        
        # 🔥 关键修复: 用正则表达式直接读取API Key，绕过YAML 6.0+的字符串问题
        # YAML 6.0+ 读取的字符串会导致ccxt签名错误
        with open(path, 'r') as f:
            content = f.read()
            
        api_key_match = re.search(r'api_key:\s*"([^"]*)"', content)
        api_secret_match = re.search(r'api_secret:\s*"([^"]*)"', content)
        
        if api_key_match and 'exchange' in data:
            key_from_regex = api_key_match.group(1)
            if key_from_regex and len(key_from_regex) > 10:  # 确保不是空字符串
                data['exchange']['api_key'] = key_from_regex
                
        if api_secret_match and 'exchange' in data:
            secret_from_regex = api_secret_match.group(1)
            if secret_from_regex and len(secret_from_regex) > 10:
                data['exchange']['api_secret'] = secret_from_regex
        
        return cls._from_dict(data)
    
    @classmethod
    def _from_dict(cls, data: Dict) -> "Config":
        """从字典创建配置对象"""
        config = cls()
        
        if 'version' in data:
            config.version = data['version']
        
        if 'exchange' in data:
            ex = data['exchange']
            # 强制转换为标准str，避免YAML 6.0+的字符串问题
            api_key = str(ex.get('api_key', '')) if ex.get('api_key') else ''
            api_secret = str(ex.get('api_secret', '')) if ex.get('api_secret') else ''
            config.exchange = ExchangeConfig(
                name=str(ex.get('name', 'binance')),
                sandbox=bool(ex.get('sandbox', False)),
                api_key=api_key,
                api_secret=api_secret
            )
        
        if 'data_core' in data:
            config.data_core = data['data_core']
        
        if 'database' in data:
            config.database = DatabaseConfig(**data['database'])
        
        if 'trading' in data:
            t = data['trading']
            config.trading = TradingConfig(
                primary=PoolConfig(**t.get('primary', {})),
                secondary=PoolConfig(**t.get('secondary', {})),
                filter=t.get('filter', {}),
                max_positions=t.get('max_positions', 5),
                max_concurrent_per_pool=t.get('max_concurrent_per_pool', 2),
                min_per_pair=t.get('min_per_pair', 50.0),
                max_per_pair_ratio=t.get('max_per_pair_ratio', 0.20),
                max_daily_loss_ratio=t.get('max_daily_loss_ratio', 0.05),
                loop_interval=t.get('loop_interval', 5)
            )
        
        if 'risk' in data:
            r = data['risk']
            config.risk = RiskConfig(
                max_total_margin=r.get('max_total_margin', 300.0),
                max_single_position=r.get('max_single_position', 100.0),
                max_leverage=r.get('max_leverage', 5),
                max_daily_loss_usdt=r.get('max_daily_loss_usdt', 50.0),
                max_position_loss_ratio=r.get('max_position_loss_ratio', 0.02),
                circuit_breaker=r.get('circuit_breaker', {})
            )
        
        if 'layer1' in data:
            config.layer1 = Layer1Config(**data['layer1'])
        
        if 'layer2' in data:
            config.layer2 = Layer2Config(**data['layer2'])
        
        if 'layer3' in data:
            config.layer3 = Layer3Config(**data['layer3'])
        
        if 'scoring' in data:
            config.scoring = ScoringConfig(**data['scoring'])
        
        if 'optimization' in data:
            opt = data['optimization']
            config.optimization = OptimizationConfig(
                coarse=opt.get('coarse', {}),
                fine=opt.get('fine', {}),
                early_exit=opt.get('early_exit', {})
            )
        
        if 'exclusion' in data:
            config.exclusion = data['exclusion']
        
        if 'output' in data:
            config.output = data['output']
        
        if 'web' in data:
            config.web = data['web']
        
        if 'notification' in data:
            n = data['notification']
            # 处理telegram嵌套结构
            telegram_cfg = n.get('telegram', {})
            if telegram_cfg:
                enabled = telegram_cfg.get('enabled', False)
                bot_token = telegram_cfg.get('bot_token', '')
                chat_id = telegram_cfg.get('chat_id', '')
            else:
                enabled = n.get('enabled', False)
                bot_token = n.get('bot_token', '')
                chat_id = n.get('chat_id', '')
            
            config.notification = NotificationConfig(
                enabled=enabled,
                bot_token=bot_token,
                chat_id=chat_id,
                events=n.get('events', ["position_opened", "position_closed", "error"])
            )
        
        if 'logging' in data:
            config.logging = data['logging']
        
        return config
    
    def validate(self) -> List[str]:
        """验证配置合法性，返回错误列表"""
        errors = []
        
        # 检查权重和是否为1
        s = self.scoring
        total_weight = s.w_coint + s.w_corr + s.w_halflife + s.w_zmax + s.w_stability + s.w_volume
        if abs(total_weight - 1.0) > 0.001:
            errors.append(f"Scoring weights sum to {total_weight}, expected 1.0")
        
        # 检查参数范围
        if self.layer1.corr_median_min < 0 or self.layer1.corr_median_min > 1:
            errors.append("layer1.corr_median_min must be in [0, 1]")
        
        if self.layer2.half_life_max <= 0:
            errors.append("layer2.half_life_max must be positive")
        
        if self.trading.max_positions <= 0:
            errors.append("trading.max_positions must be positive")
        
        return errors


# 全局配置实例
_config: Config = None


def get_config() -> Config:
    """获取全局配置实例"""
    global _config
    if _config is None:
        _config = Config.from_yaml()
    return _config


def reload_config() -> Config:
    """重新加载配置"""
    global _config
    _config = Config.from_yaml()
    return _config


if __name__ == "__main__":
    # 测试配置加载
    config = Config.from_yaml()
    errors = config.validate()
    
    if errors:
        print("Config validation errors:")
        for e in errors:
            print(f"  - {e}")
    else:
        print("Config loaded and validated successfully!")
        print(f"Version: {config.version}")
        print(f"Primary pool: {config.trading.primary.top_n} pairs")
        print(f"Secondary pool: {config.trading.secondary.top_n} pairs")
