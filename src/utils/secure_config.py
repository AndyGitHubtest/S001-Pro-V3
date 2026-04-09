"""
安全配置管理
API密钥加密存储与访问
"""
import os
import json
import base64
import hashlib
from typing import Dict, Optional, Any
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import logging

logger = logging.getLogger(__name__)


class SecureConfig:
    """
    安全配置管理器
    
    功能:
    1. API密钥加密存储
    2. 敏感信息保护
    3. 环境变量集成
    4. 安全读取
    """
    
    def __init__(self, key_file: str = None, password: str = None):
        """
        Args:
            key_file: 密钥文件路径
            password: 加密密码
        """
        self.key_file = key_file or os.path.expanduser("~/.s001_pro/.key")
        self.password = password or os.environ.get('S001_PRO_KEY')
        
        self._cipher = None
        self._init_cipher()
    
    def _init_cipher(self):
        """初始化加密器"""
        try:
            # 尝试加载现有密钥
            if os.path.exists(self.key_file):
                with open(self.key_file, 'rb') as f:
                    key = f.read()
            else:
                # 生成新密钥
                key = self._generate_key()
                os.makedirs(os.path.dirname(self.key_file), exist_ok=True)
                with open(self.key_file, 'wb') as f:
                    f.write(key)
                os.chmod(self.key_file, 0o600)  # 仅限所有者读写
            
            self._cipher = Fernet(key)
            logger.info("安全配置管理器初始化成功")
            
        except Exception as e:
            logger.error(f"初始化加密器失败: {e}")
            raise
    
    def _generate_key(self) -> bytes:
        """生成加密密钥"""
        if self.password:
            # 从密码派生密钥
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=os.urandom(16),
                iterations=100000,
            )
            key = base64.urlsafe_b64encode(kdf.derive(self.password.encode()))
        else:
            # 生成随机密钥
            key = Fernet.generate_key()
        
        return key
    
    def encrypt(self, data: str) -> str:
        """加密数据"""
        if not self._cipher:
            raise RuntimeError("加密器未初始化")
        
        encrypted = self._cipher.encrypt(data.encode())
        return base64.urlsafe_b64encode(encrypted).decode()
    
    def decrypt(self, encrypted_data: str) -> str:
        """解密数据"""
        if not self._cipher:
            raise RuntimeError("加密器未初始化")
        
        try:
            encrypted = base64.urlsafe_b64decode(encrypted_data.encode())
            decrypted = self._cipher.decrypt(encrypted)
            return decrypted.decode()
        except Exception as e:
            logger.error(f"解密失败: {e}")
            raise
    
    def save_secure_config(self, config: Dict, config_file: str = "config.enc"):
        """
        保存加密配置
        
        Args:
            config: 配置字典 (包含敏感信息)
            config_file: 配置文件路径
        """
        # 加密敏感字段
        encrypted_config = {}
        
        for key, value in config.items():
            if self._is_sensitive_key(key):
                encrypted_config[key] = {
                    'encrypted': True,
                    'value': self.encrypt(str(value))
                }
            else:
                encrypted_config[key] = {
                    'encrypted': False,
                    'value': value
                }
        
        # 保存文件
        with open(config_file, 'w') as f:
            json.dump(encrypted_config, f, indent=2)
        
        os.chmod(config_file, 0o600)
        logger.info(f"加密配置已保存: {config_file}")
    
    def load_secure_config(self, config_file: str = "config.enc") -> Dict:
        """
        加载加密配置
        
        Args:
            config_file: 配置文件路径
            
        Returns:
            解密后的配置
        """
        with open(config_file, 'r') as f:
            encrypted_config = json.load(f)
        
        config = {}
        
        for key, data in encrypted_config.items():
            if data.get('encrypted'):
                config[key] = self.decrypt(data['value'])
            else:
                config[key] = data['value']
        
        return config
    
    def _is_sensitive_key(self, key: str) -> bool:
        """判断是否为敏感字段"""
        sensitive_patterns = [
            'api_key', 'apikey', 'api-key',
            'secret', 'password', 'passwd', 'pwd',
            'token', 'private_key', 'seed',
            'passphrase', 'webhook_secret'
        ]
        
        key_lower = key.lower()
        return any(pattern in key_lower for pattern in sensitive_patterns)
    
    @staticmethod
    def mask_sensitive(value: str, visible_chars: int = 4) -> str:
        """脱敏显示"""
        if not value or len(value) <= visible_chars * 2:
            return '*' * len(value) if value else ''
        
        return value[:visible_chars] + '*' * (len(value) - visible_chars * 2) + value[-visible_chars:]
    
    def get_api_credentials(self, exchange_name: str, config_file: str = "config.enc") -> Dict:
        """
        获取API凭证
        
        Args:
            exchange_name: 交易所名称
            config_file: 配置文件
            
        Returns:
            {api_key, secret, ...}
        """
        config = self.load_secure_config(config_file)
        
        prefix = exchange_name.lower()
        
        return {
            'api_key': config.get(f'{prefix}_api_key'),
            'secret': config.get(f'{prefix}_secret'),
            'passphrase': config.get(f'{prefix}_passphrase')
        }


class EnvironmentConfig:
    """
    环境变量配置管理
    
    从环境变量读取配置，避免硬编码敏感信息
    """
    
    @staticmethod
    def load_from_env(prefix: str = "S001_") -> Dict:
        """从环境变量加载配置"""
        config = {}
        
        for key, value in os.environ.items():
            if key.startswith(prefix):
                config_key = key[len(prefix):].lower()
                config[config_key] = value
        
        return config
    
    @staticmethod
    def get_exchange_credentials(exchange: str) -> Dict:
        """
        从环境变量获取交易所凭证
        
        环境变量格式:
        - S001_BINANCE_API_KEY
        - S001_BINANCE_SECRET
        - S001_BINANCE_PASSPHRASE (OKX等)
        """
        prefix = f"S001_{exchange.upper()}_"
        
        return {
            'api_key': os.environ.get(f'{prefix}API_KEY'),
            'secret': os.environ.get(f'{prefix}SECRET'),
            'passphrase': os.environ.get(f'{prefix}PASSPHRASE')
        }
    
    @staticmethod
    def validate_env() -> bool:
        """验证必要环境变量"""
        required = [
            'S001_BINANCE_API_KEY',
            'S001_BINANCE_SECRET'
        ]
        
        missing = [var for var in required if not os.environ.get(var)]
        
        if missing:
            logger.warning(f"缺少环境变量: {missing}")
            return False
        
        return True


# 便捷函数
def encrypt_api_key(api_key: str, secret: str, output_file: str = "config.enc"):
    """便捷加密API密钥"""
    secure = SecureConfig()
    
    config = {
        'binance_api_key': api_key,
        'binance_secret': secret
    }
    
    secure.save_secure_config(config, output_file)
    print(f"✅ API密钥已加密保存: {output_file}")


def load_api_credentials(exchange: str = "binance", config_file: str = "config.enc") -> Dict:
    """便捷加载API凭证"""
    secure = SecureConfig()
    return secure.get_api_credentials(exchange, config_file)


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    print("="*60)
    print("安全配置管理测试")
    print("="*60)
    
    # 测试加密
    print("\n1. 测试加密/解密")
    secure = SecureConfig(password="test_password_123")
    
    original = "my_secret_api_key_12345"
    encrypted = secure.encrypt(original)
    decrypted = secure.decrypt(encrypted)
    
    print(f"  原文: {original}")
    print(f"  加密: {encrypted[:30]}...")
    print(f"  解密: {decrypted}")
    print(f"  验证: {'✅' if original == decrypted else '❌'}")
    
    # 测试脱敏
    print("\n2. 测试脱敏显示")
    test_values = [
        "1234567890abcdef",
        "short",
        ""
    ]
    for val in test_values:
        masked = SecureConfig.mask_sensitive(val)
        print(f"  {val} -> {masked}")
    
    # 测试环境变量
    print("\n3. 测试环境变量读取")
    os.environ['S001_TEST_KEY'] = 'test_value'
    os.environ['S001_BINANCE_API_KEY'] = 'test_api_key'
    
    env_config = EnvironmentConfig.load_from_env()
    print(f"  加载配置项: {list(env_config.keys())}")
    
    creds = EnvironmentConfig.get_exchange_credentials('binance')
    print(f"  Binance API Key: {SecureConfig.mask_sensitive(creds.get('api_key', ''))}")
    
    print("\n" + "="*60)
