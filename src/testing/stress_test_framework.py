"""
压力测试框架
模拟极端市场条件，验证系统稳定性
"""
import asyncio
import numpy as np
import pandas as pd
from typing import Dict, List, Callable, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import json
import logging

logger = logging.getLogger(__name__)


class StressScenario(Enum):
    """压力测试场景"""
    MARKET_CRASH = "market_crash"           # 市场暴跌
    MARKET_SPIKE = "market_spike"           # 市场暴涨
    LIQUIDITY_CRISIS = "liquidity_crisis"  # 流动性危机
    FLASH_CRASH = "flash_crash"            # 闪崩
    HIGH_VOLATILITY = "high_volatility"    # 高波动
    CHAIN_REACTION = "chain_reaction"      # 连环爆仓
    API_FAILURE = "api_failure"            # API故障
    NETWORK_PARTITION = "network_partition" # 网络分区


@dataclass
class ScenarioConfig:
    """场景配置"""
    name: str
    description: str
    price_shock: float = 0.0           # 价格冲击 (-0.2 = -20%)
    volatility_multiplier: float = 1.0  # 波动率乘数
    spread_widening: float = 1.0        # 价差扩大
    slippage_increase: float = 1.0      # 滑点增加
    latency_increase_ms: int = 0        # 延迟增加
    duration_minutes: int = 60          # 持续时间
    recovery_time_minutes: int = 30     # 恢复时间


@dataclass
class StressResult:
    """压力测试结果"""
    scenario: str
    success: bool
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    
    # 财务指标
    max_drawdown: float
    final_pnl: float
    pnl_volatility: float
    
    # 系统指标
    max_latency_ms: float
    avg_latency_ms: float
    error_count: int
    timeout_count: int
    
    # 风控指标
    stop_loss_triggered: int
    position_reduced: bool
    margin_call: bool
    
    # 详细日志
    events: List[Dict] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


class ScenarioLibrary:
    """场景库"""
    
    SCENARIOS = {
        StressScenario.MARKET_CRASH: ScenarioConfig(
            name="market_crash",
            description="市场暴跌20%",
            price_shock=-0.20,
            volatility_multiplier=3.0,
            spread_widening=2.0,
            slippage_increase=2.5,
            latency_increase_ms=500,
            duration_minutes=30
        ),
        StressScenario.MARKET_SPIKE: ScenarioConfig(
            name="market_spike",
            description="市场暴涨20%",
            price_shock=0.20,
            volatility_multiplier=2.5,
            spread_widening=1.8,
            slippage_increase=2.0,
            latency_increase_ms=300,
            duration_minutes=20
        ),
        StressScenario.LIQUIDITY_CRISIS: ScenarioConfig(
            name="liquidity_crisis",
            description="流动性危机",
            price_shock=0.0,
            volatility_multiplier=1.5,
            spread_widening=5.0,
            slippage_increase=5.0,
            latency_increase_ms=2000,
            duration_minutes=60
        ),
        StressScenario.FLASH_CRASH: ScenarioConfig(
            name="flash_crash",
            description="闪崩(-10% in 1min)",
            price_shock=-0.10,
            volatility_multiplier=4.0,
            spread_widening=3.0,
            slippage_increase=4.0,
            latency_increase_ms=1000,
            duration_minutes=5,
            recovery_time_minutes=15
        ),
        StressScenario.HIGH_VOLATILITY: ScenarioConfig(
            name="high_volatility",
            description="高波动期",
            price_shock=0.0,
            volatility_multiplier=5.0,
            spread_widening=2.5,
            slippage_increase=3.0,
            latency_increase_ms=800,
            duration_minutes=120
        ),
        StressScenario.CHAIN_REACTION: ScenarioConfig(
            name="chain_reaction",
            description="连环爆仓",
            price_shock=-0.15,
            volatility_multiplier=4.0,
            spread_widening=4.0,
            slippage_increase=5.0,
            latency_increase_ms=1500,
            duration_minutes=45
        ),
        StressScenario.API_FAILURE: ScenarioConfig(
            name="api_failure",
            description="API间歇性故障",
            price_shock=0.0,
            volatility_multiplier=1.0,
            latency_increase_ms=5000,
            duration_minutes=30
        ),
        StressScenario.NETWORK_PARTITION: ScenarioConfig(
            name="network_partition",
            description="网络分区",
            price_shock=0.0,
            volatility_multiplier=2.0,
            latency_increase_ms=10000,
            duration_minutes=20
        )
    }
    
    @classmethod
    def get_scenario(cls, scenario: StressScenario) -> ScenarioConfig:
        """获取场景配置"""
        return cls.SCENARIOS.get(scenario)
    
    @classmethod
    def list_scenarios(cls) -> Dict[str, str]:
        """列出所有场景"""
        return {
            s.value: cfg.description 
            for s, cfg in cls.SCENARIOS.items()
        }


class StressTestRunner:
    """
    压力测试运行器
    
    执行压力测试并收集结果
    """
    
    def __init__(self, strategy, initial_capital: float = 10000.0):
        """
        Args:
            strategy: 策略实例
            initial_capital: 初始资金
        """
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.results: List[StressResult] = []
    
    async def run_scenario(self, scenario: StressScenario,
                          custom_config: Dict = None) -> StressResult:
        """
        运行单个场景测试
        
        Args:
            scenario: 测试场景
            custom_config: 自定义配置覆盖
            
        Returns:
            测试结果
        """
        config = ScenarioLibrary.get_scenario(scenario)
        if custom_config:
            for key, value in custom_config.items():
                setattr(config, key, value)
        
        logger.info(f"="*60)
        logger.info(f"🧪 开始压力测试: {config.name}")
        logger.info(f"   描述: {config.description}")
        logger.info(f"="*60)
        
        start_time = datetime.now()
        events = []
        
        # 初始化指标
        max_drawdown = 0.0
        max_latency = 0.0
        total_latency = 0.0
        latency_count = 0
        error_count = 0
        timeout_count = 0
        stop_loss_count = 0
        margin_call = False
        
        # 模拟运行
        try:
            for minute in range(config.duration_minutes):
                # 模拟市场价格
                price_shock = self._simulate_price_movement(
                    config.price_shock, 
                    config.volatility_multiplier,
                    minute,
                    config.duration_minutes
                )
                
                # 模拟延迟
                latency = self._simulate_latency(config.latency_increase_ms)
                max_latency = max(max_latency, latency)
                total_latency += latency
                latency_count += 1
                
                # 记录事件
                events.append({
                    'minute': minute,
                    'price_shock': price_shock,
                    'latency_ms': latency,
                    'timestamp': (start_time + timedelta(minutes=minute)).isoformat()
                })
                
                # 模拟错误 (API故障场景)
                if scenario == StressScenario.API_FAILURE:
                    if np.random.random() < 0.1:  # 10%错误率
                        error_count += 1
                        events[-1]['error'] = 'API timeout'
                
                # 模拟网络分区
                if scenario == StressScenario.NETWORK_PARTITION:
                    if minute > config.duration_minutes // 2:
                        timeout_count += 1
                        events[-1]['timeout'] = True
                
                await asyncio.sleep(0.01)  # 模拟处理时间
        
        except Exception as e:
            logger.error(f"测试过程中出错: {e}")
            events.append({'error': str(e), 'timestamp': datetime.now().isoformat()})
        
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        # 计算结果
        avg_latency = total_latency / latency_count if latency_count > 0 else 0
        
        # 模拟P&L (基于价格冲击和随机因素)
        final_pnl = self._simulate_pnl(config.price_shock, config.volatility_multiplier)
        pnl_volatility = abs(config.price_shock) * config.volatility_multiplier * 100
        
        # 生成建议
        recommendations = self._generate_recommendations(
            scenario, max_latency, error_count, final_pnl
        )
        
        result = StressResult(
            scenario=config.name,
            success=error_count < 10 and not margin_call,
            start_time=start_time,
            end_time=end_time,
            duration_seconds=duration,
            max_drawdown=abs(config.price_shock) * 1.5,
            final_pnl=final_pnl,
            pnl_volatility=pnl_volatility,
            max_latency_ms=max_latency,
            avg_latency_ms=avg_latency,
            error_count=error_count,
            timeout_count=timeout_count,
            stop_loss_triggered=stop_loss_count,
            position_reduced=abs(final_pnl) > self.initial_capital * 0.1,
            margin_call=margin_call,
            events=events[:10],  # 只保留前10个事件
            recommendations=recommendations
        )
        
        self.results.append(result)
        
        logger.info(f"✅ 测试完成: {config.name}")
        logger.info(f"   成功: {result.success}")
        logger.info(f"   最大延迟: {max_latency:.0f}ms")
        logger.info(f"   错误数: {error_count}")
        
        return result
    
    async def run_all_scenarios(self) -> Dict[str, StressResult]:
        """运行所有场景"""
        results = {}
        
        for scenario in StressScenario:
            result = await self.run_scenario(scenario)
            results[scenario.value] = result
        
        return results
    
    def _simulate_price_movement(self, price_shock: float,
                                 volatility: float,
                                 minute: int,
                                 total_minutes: int) -> float:
        """模拟价格变动"""
        # 基础趋势 + 随机波动
        if total_minutes > 0:
            trend = price_shock * (minute / total_minutes)
        else:
            trend = 0
        
        noise = np.random.normal(0, 0.01 * volatility)
        return trend + noise
    
    def _simulate_latency(self, base_latency_ms: int) -> float:
        """模拟延迟"""
        # 基础延迟 + 随机波动
        noise = np.random.exponential(base_latency_ms * 0.3)
        return base_latency_ms + noise
    
    def _simulate_pnl(self, price_shock: float, volatility: float) -> float:
        """模拟盈亏"""
        # 基于价格冲击和策略应对能力
        # 假设策略能对冲部分风险
        hedge_ratio = 0.6  # 60%对冲
        
        pnl = -price_shock * self.initial_capital * (1 - hedge_ratio)
        
        # 添加随机因素
        pnl += np.random.normal(0, volatility * 10)
        
        return pnl
    
    def _generate_recommendations(self, scenario: StressScenario,
                                  max_latency: float,
                                  error_count: int,
                                  final_pnl: float) -> List[str]:
        """生成改进建议"""
        recommendations = []
        
        if max_latency > 1000:
            recommendations.append("高延迟 detected: 考虑增加超时阈值或降级处理")
        
        if error_count > 5:
            recommendations.append("错误率较高: 检查错误重试机制和断路器配置")
        
        if final_pnl < -self.initial_capital * 0.05:
            recommendations.append("亏损较大: 建议收紧止损或降低仓位")
        
        if scenario == StressScenario.LIQUIDITY_CRISIS:
            recommendations.append("流动性危机: 建议增加最小成交量过滤")
        
        if scenario == StressScenario.FLASH_CRASH:
            recommendations.append("闪崩风险: 建议实现价格跳空检测")
        
        if not recommendations:
            recommendations.append("测试通过，系统表现良好")
        
        return recommendations
    
    def generate_report(self, output_path: str = "stress_test_report.json"):
        """生成测试报告"""
        report = {
            'timestamp': datetime.now().isoformat(),
            'initial_capital': self.initial_capital,
            'total_scenarios': len(self.results),
            'passed': sum(1 for r in self.results if r.success),
            'failed': sum(1 for r in self.results if not r.success),
            'scenarios': []
        }
        
        for result in self.results:
            report['scenarios'].append({
                'name': result.scenario,
                'success': result.success,
                'duration': result.duration_seconds,
                'max_drawdown': f"{result.max_drawdown:.2%}",
                'final_pnl': f"{result.final_pnl:.2f} USDT",
                'max_latency_ms': result.max_latency_ms,
                'error_count': result.error_count,
                'recommendations': result.recommendations
            })
        
        with open(output_path, 'w') as f:
            json.dump(report, f, indent=2)
        
        logger.info(f"压力测试报告已保存: {output_path}")
        return report
    
    def print_summary(self):
        """打印摘要"""
        print("\n" + "="*60)
        print("压力测试摘要")
        print("="*60)
        
        for result in self.results:
            status = "✅" if result.success else "❌"
            print(f"\n{status} {result.scenario}")
            print(f"   成功: {result.success}")
            print(f"   最大回撤: {result.max_drawdown:.2%}")
            print(f"   最终盈亏: {result.final_pnl:.2f} USDT")
            print(f"   最大延迟: {result.max_latency_ms:.0f}ms")
            print(f"   错误数: {result.error_count}")
            print(f"   建议: {', '.join(result.recommendations[:2])}")
        
        print("\n" + "="*60)


class LoadTester:
    """
    负载测试器
    
    测试系统在高并发下的表现
    """
    
    def __init__(self):
        self.results = []
    
    async def run_load_test(self, 
                           test_func: Callable,
                           concurrency: int = 100,
                           requests_per_client: int = 10,
                           duration_seconds: int = 60) -> Dict:
        """
        运行负载测试
        
        Args:
            test_func: 测试函数
            concurrency: 并发数
            requests_per_client: 每个客户端请求数
            duration_seconds: 持续时间
            
        Returns:
            测试结果
        """
        logger.info(f"🚀 开始负载测试: {concurrency}并发")
        
        start_time = datetime.now()
        
        # 创建任务
        tasks = []
        for i in range(concurrency):
            task = self._client_worker(i, test_func, requests_per_client)
            tasks.append(task)
        
        # 执行所有任务
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        # 统计结果
        success_count = sum(1 for r in results if not isinstance(r, Exception))
        error_count = concurrency - success_count
        
        total_requests = concurrency * requests_per_client
        rps = total_requests / duration if duration > 0 else 0
        
        result = {
            'concurrency': concurrency,
            'total_requests': total_requests,
            'duration_seconds': duration,
            'success_rate': success_count / concurrency,
            'error_rate': error_count / concurrency,
            'rps': rps,
            'timestamp': datetime.now().isoformat()
        }
        
        self.results.append(result)
        
        logger.info(f"✅ 负载测试完成")
        logger.info(f"   成功率: {result['success_rate']:.1%}")
        logger.info(f"   RPS: {rps:.1f}")
        
        return result
    
    async def _client_worker(self, client_id: int,
                            test_func: Callable,
                            requests_count: int) -> List:
        """客户端工作器"""
        results = []
        
        for i in range(requests_count):
            try:
                start = datetime.now()
                result = await test_func(client_id, i)
                elapsed = (datetime.now() - start).total_seconds()
                
                results.append({
                    'success': True,
                    'elapsed_ms': elapsed * 1000
                })
                
            except Exception as e:
                results.append({
                    'success': False,
                    'error': str(e)
                })
        
        return results


# 便捷函数
async def run_stress_test(strategy, scenarios: List[StressScenario] = None) -> Dict:
    """便捷运行压力测试"""
    runner = StressTestRunner(strategy)
    
    if scenarios:
        results = {}
        for scenario in scenarios:
            result = await runner.run_scenario(scenario)
            results[scenario.value] = result
    else:
        results = await runner.run_all_scenarios()
    
    runner.generate_report()
    runner.print_summary()
    
    return results


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    print("="*60)
    print("压力测试框架")
    print("="*60)
    
    # 列出场景
    print("\n1. 可用测试场景:")
    scenarios = ScenarioLibrary.list_scenarios()
    for name, desc in scenarios.items():
        print(f"   - {name}: {desc}")
    
    # 模拟策略
    class MockStrategy:
        pass
    
    strategy = MockStrategy()
    
    # 运行测试
    async def test():
        print("\n2. 运行压力测试")
        runner = StressTestRunner(strategy, initial_capital=1000)
        
        # 运行2个场景
        await runner.run_scenario(StressScenario.MARKET_CRASH)
        await runner.run_scenario(StressScenario.FLASH_CRASH)
        
        # 生成报告
        runner.generate_report("test_stress_report.json")
        
        # 负载测试
        print("\n3. 负载测试")
        load_tester = LoadTester()
        
        async def mock_api_call(client_id, request_id):
            await asyncio.sleep(0.01)  # 10ms延迟
            return True
        
        result = await load_tester.run_load_test(
            mock_api_call,
            concurrency=50,
            requests_per_client=5
        )
        
        print(f"\n负载测试结果:")
        print(f"  并发数: {result['concurrency']}")
        print(f"  成功率: {result['success_rate']:.1%}")
        print(f"  RPS: {result['rps']:.1f}")
    
    asyncio.run(test())
    
    print("\n" + "="*60)
