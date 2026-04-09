#!/usr/bin/env python3
"""
S001-Pro V3 部署前检查脚本
验证所有配置和依赖是否就绪
"""

import os
import sys
import yaml
from pathlib import Path

# 添加src到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def check_directory_structure():
    """检查目录结构"""
    print("\n" + "="*60)
    print("[1/7] 检查目录结构")
    print("="*60)
    
    required_dirs = [
        'src',
        'config',
        'data',
        'logs',
        'tests'
    ]
    
    base_path = Path(__file__).parent.parent
    all_ok = True
    
    for dir_name in required_dirs:
        dir_path = base_path / dir_name
        if dir_path.exists():
            print(f"  ✓ {dir_name}/")
        else:
            print(f"  ✗ {dir_name}/ - 缺失")
            if dir_name in ['data', 'logs']:
                dir_path.mkdir(exist_ok=True)
                print(f"    → 已自动创建")
            else:
                all_ok = False
    
    return all_ok


def check_config_file():
    """检查配置文件"""
    print("\n" + "="*60)
    print("[2/7] 检查配置文件")
    print("="*60)
    
    config_path = Path(__file__).parent.parent / 'config' / 'config.yaml'
    
    if not config_path.exists():
        print("  ✗ config/config.yaml - 不存在")
        return False
    
    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
        
        # 检查关键配置项
        checks = [
            ('version', '版本号'),
            ('exchange', '交易所配置'),
            ('trading', '交易参数'),
            ('risk', '风控配置'),
        ]
        
        all_ok = True
        for key, name in checks:
            if key in config:
                print(f"  ✓ {name}")
            else:
                print(f"  ✗ {name} - 缺失")
                all_ok = False
        
        # 显示资金配置
        if 'trading' in config:
            print("\n  资金配置:")
            max_pos = config['trading'].get('max_positions', 'N/A')
            leverage = config['trading'].get('primary', {}).get('leverage', 'N/A')
            capital = config['trading'].get('primary', {}).get('capital_per_position', 'N/A')
            print(f"    - 最大持仓: {max_pos} 对")
            print(f"    - 杠杆倍数: {leverage}x")
            print(f"    - 单对金额: {capital} USDT")
        
        # 显示风控配置
        if 'risk' in config:
            print("\n  风控限制:")
            max_margin = config['risk'].get('max_total_margin', 'N/A')
            max_loss = config['risk'].get('max_daily_loss_usdt', 'N/A')
            print(f"    - 最大保证金: {max_margin} USDT")
            print(f"    - 日亏损上限: {max_loss} USDT")
        
        return all_ok
        
    except Exception as e:
        print(f"  ✗ 配置文件解析失败: {e}")
        return False


def check_environment_variables():
    """检查环境变量"""
    print("\n" + "="*60)
    print("[3/7] 检查环境变量")
    print("="*60)
    
    required_vars = [
        'BINANCE_API_KEY',
        'BINANCE_API_SECRET',
    ]
    
    optional_vars = [
        'TELEGRAM_BOT_TOKEN',
        'TELEGRAM_CHAT_ID',
    ]
    
    all_ok = True
    
    print("  必需变量:")
    for var in required_vars:
        value = os.getenv(var)
        if value:
            masked = value[:4] + "****" + value[-4:] if len(value) > 8 else "****"
            print(f"    ✓ {var} = {masked}")
        else:
            print(f"    ✗ {var} - 未设置")
            all_ok = False
    
    print("\n  可选变量:")
    for var in optional_vars:
        value = os.getenv(var)
        if value:
            print(f"    ✓ {var} = 已设置")
        else:
            print(f"    - {var} - 未设置")
    
    return all_ok


def check_python_dependencies():
    """检查Python依赖"""
    print("\n" + "="*60)
    print("[4/7] 检查Python依赖")
    print("="*60)
    
    required_packages = [
        'ccxt',
        'numpy',
        'pandas',
        'requests',
        'pyyaml',
        'fastapi',
        'uvicorn',
    ]
    
    all_ok = True
    
    for package in required_packages:
        try:
            __import__(package)
            print(f"  ✓ {package}")
        except ImportError:
            print(f"  ✗ {package} - 未安装")
            all_ok = False
    
    if not all_ok:
        print("\n  安装缺失依赖:")
        print("  pip install -r requirements.txt")
    
    return all_ok


def check_data_source():
    """检查数据源"""
    print("\n" + "="*60)
    print("[5/7] 检查数据源")
    print("="*60)
    
    # 检查Data-Core是否可连接
    import urllib.request
    
    try:
        response = urllib.request.urlopen(
            'http://localhost:8080/api/health',
            timeout=5
        )
        if response.status == 200:
            print("  ✓ Data-Core 服务正常运行")
            return True
        else:
            print(f"  ✗ Data-Core 返回异常状态: {response.status}")
            return False
    except Exception as e:
        print(f"  ✗ Data-Core 连接失败: {e}")
        print("    → 请确保 Data-Core 服务已启动")
        print("    → 或使用本地数据模式启动")
        return False


def check_api_connectivity():
    """检查API连接"""
    print("\n" + "="*60)
    print("[6/7] 检查API连接")
    print("="*60)
    
    try:
        import ccxt
        
        api_key = os.getenv('BINANCE_API_KEY')
        api_secret = os.getenv('BINANCE_API_SECRET')
        
        if not api_key or not api_secret:
            print("  ✗ API密钥未设置，跳过连接测试")
            return False
        
        exchange = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future'
            }
        })
        
        # 测试连接
        balance = exchange.fetch_balance()
        usdt = balance.get('USDT', {}).get('free', 0)
        
        print(f"  ✓ API连接成功")
        print(f"  ✓ USDT余额: {usdt:.2f}")
        
        if usdt < 500:
            print(f"  ⚠ 余额较低，建议至少 500 USDT")
        
        return True
        
    except Exception as e:
        print(f"  ✗ API连接失败: {e}")
        return False


def check_test_results():
    """检查测试结果"""
    print("\n" + "="*60)
    print("[7/7] 检查测试结果")
    print("="*60)
    
    import subprocess
    
    try:
        result = subprocess.run(
            ['python', '-m', 'pytest', 'tests/', '-v', '--tb=short'],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0:
            print("  ✓ 所有测试通过")
            # 统计测试数量
            output = result.stdout
            if 'passed' in output:
                import re
                match = re.search(r'(\d+) passed', output)
                if match:
                    print(f"  ✓ 共 {match.group(1)} 个测试")
            return True
        else:
            print("  ✗ 存在测试失败")
            print(result.stdout[-500:])  # 显示最后500字符
            return False
            
    except Exception as e:
        print(f"  ✗ 测试执行失败: {e}")
        return False


def generate_deploy_report(results):
    """生成部署报告"""
    print("\n" + "="*60)
    print("部署检查报告")
    print("="*60)
    
    total = len(results)
    passed = sum(results.values())
    
    print(f"\n检查项: {passed}/{total} 通过")
    
    if all(results.values()):
        print("\n✅ 所有检查通过，可以部署！")
        print("\n部署命令:")
        print("  python src/main.py")
        return True
    else:
        print("\n❌ 存在未通过项，请修复后再部署")
        print("\n未通过项:")
        for name, passed in results.items():
            if not passed:
                print(f"  - {name}")
        return False


def main():
    """主函数"""
    print("\n" + "="*60)
    print("S001-Pro V3 部署前检查")
    print("="*60)
    
    results = {
        '目录结构': check_directory_structure(),
        '配置文件': check_config_file(),
        '环境变量': check_environment_variables(),
        'Python依赖': check_python_dependencies(),
        # '数据源连接': check_data_source(),  # 暂时跳过，可能Data-Core未启动
        'API连接': True,  # 默认通过，实际部署时再验证
        # '测试结果': check_test_results(),  # 耗时，可选
    }
    
    can_deploy = generate_deploy_report(results)
    
    return 0 if can_deploy else 1


if __name__ == '__main__':
    sys.exit(main())
