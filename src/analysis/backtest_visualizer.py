"""
回测可视化
生成回测结果图表和HTML报告
"""
import json
import base64
from io import BytesIO
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)

# 尝试导入matplotlib
try:
    import matplotlib
    matplotlib.use('Agg')  # 无GUI后端
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    logger.warning("matplotlib未安装，使用简化版可视化")


try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False


@dataclass
class BacktestResult:
    """回测结果数据"""
    timestamps: List[datetime]
    equity_curve: List[float]
    trades: List[Dict]
    positions: List[Dict]
    signals: List[Dict]
    
    # 统计指标
    total_return: float
    max_drawdown: float
    sharpe_ratio: float
    win_rate: float
    profit_factor: float
    total_trades: int
    
    # 详细数据
    daily_returns: List[float] = None
    monthly_returns: List[float] = None


class BacktestVisualizer:
    """
    回测可视化器
    
    生成:
    1. 权益曲线图
    2. 回撤图
    3. 收益分布图
    4. 月度收益热力图
    5. 完整HTML报告
    """
    
    def __init__(self, result: BacktestResult):
        self.result = result
        self.figures = {}
    
    def generate_equity_curve(self, figsize: Tuple[int, int] = (12, 6)) -> str:
        """
        生成权益曲线图
        
        Returns:
            Base64编码的PNG图像
        """
        if not MATPLOTLIB_AVAILABLE:
            return self._generate_svg_equity()
        
        fig, ax = plt.subplots(figsize=figsize)
        
        # 绘制权益曲线
        ax.plot(self.result.timestamps, self.result.equity_curve, 
                linewidth=2, color='#2196F3', label='权益曲线')
        
        # 标记交易点
        for trade in self.result.trades:
            if 'timestamp' in trade and 'pnl' in trade:
                ts = trade['timestamp']
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts)
                
                color = '#4CAF50' if trade['pnl'] > 0 else '#F44336'
                marker = '^' if trade.get('side') == 'long' else 'v'
                
                ax.scatter([ts], [trade.get('equity', self.result.equity_curve[0])],
                          color=color, marker=marker, s=50, alpha=0.7)
        
        # 添加起始和终点标注
        ax.axhline(y=self.result.equity_curve[0], color='gray', 
                  linestyle='--', alpha=0.3, label='起始资金')
        
        ax.set_title('权益曲线', fontsize=14, fontweight='bold')
        ax.set_xlabel('时间')
        ax.set_ylabel('权益 (USDT)')
        ax.legend()
        ax.grid(True, alpha=0.3)
        
        # 格式化x轴日期
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        plt.xticks(rotation=45)
        
        plt.tight_layout()
        
        # 转换为base64
        img_str = self._fig_to_base64(fig)
        plt.close(fig)
        
        self.figures['equity_curve'] = img_str
        return img_str
    
    def generate_drawdown_chart(self, figsize: Tuple[int, int] = (12, 4)) -> str:
        """生成回撤图"""
        if not MATPLOTLIB_AVAILABLE or not NUMPY_AVAILABLE:
            return ""
        
        # 计算回撤
        equity = np.array(self.result.equity_curve)
        running_max = np.maximum.accumulate(equity)
        drawdowns = (equity - running_max) / running_max * 100
        
        fig, ax = plt.subplots(figsize=figsize)
        
        ax.fill_between(self.result.timestamps, drawdowns, 0,
                        color='#F44336', alpha=0.3, label='回撤')
        ax.plot(self.result.timestamps, drawdowns, 
                color='#F44336', linewidth=1)
        
        # 标注最大回撤
        max_dd_idx = np.argmin(drawdowns)
        ax.annotate(f'最大回撤: {drawdowns[max_dd_idx]:.1f}%',
                   xy=(self.result.timestamps[max_dd_idx], drawdowns[max_dd_idx]),
                   xytext=(10, -30), textcoords='offset points',
                   arrowprops=dict(arrowstyle='->', color='red'))
        
        ax.set_title('回撤曲线', fontsize=14, fontweight='bold')
        ax.set_xlabel('时间')
        ax.set_ylabel('回撤 (%)')
        ax.legend()
        ax.grid(True, alpha=0.3)
        
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        plt.xticks(rotation=45)
        
        plt.tight_layout()
        
        img_str = self._fig_to_base64(fig)
        plt.close(fig)
        
        self.figures['drawdown'] = img_str
        return img_str
    
    def generate_pnl_distribution(self, figsize: Tuple[int, int] = (10, 6)) -> str:
        """生成盈亏分布图"""
        if not MATPLOTLIB_AVAILABLE:
            return ""
        
        pnls = [t['pnl'] for t in self.result.trades if 'pnl' in t]
        
        if not pnls:
            return ""
        
        fig, ax = plt.subplots(figsize=figsize)
        
        # 直方图
        n, bins, patches = ax.hist(pnls, bins=30, alpha=0.7, color='#2196F3', edgecolor='black')
        
        # 为盈亏分别着色
        for i, (patch, bin_edge) in enumerate(zip(patches, bins[:-1])):
            if bin_edge < 0:
                patch.set_facecolor('#F44336')
            else:
                patch.set_facecolor('#4CAF50')
        
        # 添加统计信息
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        
        ax.axvline(x=0, color='black', linestyle='-', linewidth=2)
        ax.axvline(x=np.mean(pnls) if NUMPY_AVAILABLE else sum(pnls)/len(pnls), 
                  color='orange', linestyle='--', label='平均值')
        
        ax.set_title(f'盈亏分布 (胜率: {len(wins)/len(pnls)*100:.1f}%)', 
                    fontsize=14, fontweight='bold')
        ax.set_xlabel('盈亏 (USDT)')
        ax.set_ylabel('频次')
        ax.legend()
        ax.grid(True, alpha=0.3, axis='y')
        
        plt.tight_layout()
        
        img_str = self._fig_to_base64(fig)
        plt.close(fig)
        
        self.figures['pnl_distribution'] = img_str
        return img_str
    
    def generate_monthly_returns_heatmap(self, figsize: Tuple[int, int] = (10, 6)) -> str:
        """生成月度收益热力图"""
        if not MATPLOTLIB_AVAILABLE or not self.result.monthly_returns:
            return ""
        
        fig, ax = plt.subplots(figsize=figsize)
        
        # 这里简化处理，实际应该按年月组织数据
        returns = self.result.monthly_returns[:24]  # 最近24个月
        months = list(range(1, len(returns) + 1))
        
        colors = ['#F44336' if r < 0 else '#4CAF50' for r in returns]
        bars = ax.bar(months, [r * 100 for r in returns], color=colors, alpha=0.7)
        
        ax.axhline(y=0, color='black', linestyle='-', linewidth=1)
        ax.set_title('月度收益', fontsize=14, fontweight='bold')
        ax.set_xlabel('月份')
        ax.set_ylabel('收益率 (%)')
        ax.grid(True, alpha=0.3, axis='y')
        
        plt.tight_layout()
        
        img_str = self._fig_to_base64(fig)
        plt.close(fig)
        
        self.figures['monthly_returns'] = img_str
        return img_str
    
    def generate_all_charts(self) -> Dict[str, str]:
        """生成所有图表"""
        logger.info("生成回测可视化图表...")
        
        self.generate_equity_curve()
        self.generate_drawdown_chart()
        self.generate_pnl_distribution()
        self.generate_monthly_returns_heatmap()
        
        return self.figures
    
    def generate_html_report(self, output_path: str = "backtest_report.html") -> str:
        """
        生成完整HTML报告
        
        Returns:
            HTML内容
        """
        # 生成所有图表
        self.generate_all_charts()
        
        # 计算额外统计
        total_trades = len(self.result.trades)
        winning_trades = len([t for t in self.result.trades if t.get('pnl', 0) > 0])
        losing_trades = total_trades - winning_trades
        
        avg_win = np.mean([t['pnl'] for t in self.result.trades if t.get('pnl', 0) > 0]) if NUMPY_AVAILABLE else 0
        avg_loss = np.mean([t['pnl'] for t in self.result.trades if t.get('pnl', 0) < 0]) if NUMPY_AVAILABLE else 0
        
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>S001-Pro V3 回测报告</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f1419;
            color: #e0e0e0;
            line-height: 1.6;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }}
        .header {{
            text-align: center;
            padding: 30px;
            background: linear-gradient(135deg, #1a1f2e 0%, #2d3748 100%);
            border-radius: 12px;
            margin-bottom: 30px;
            border: 1px solid #374151;
        }}
        .header h1 {{
            font-size: 28px;
            color: #60a5fa;
            margin-bottom: 10px;
        }}
        .header p {{
            color: #9ca3af;
        }}
        
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }}
        .metric-card {{
            background: #1a1f2e;
            padding: 20px;
            border-radius: 8px;
            border: 1px solid #374151;
            text-align: center;
        }}
        .metric-label {{
            font-size: 12px;
            color: #9ca3af;
            text-transform: uppercase;
            margin-bottom: 8px;
        }}
        .metric-value {{
            font-size: 24px;
            font-weight: bold;
        }}
        .positive {{ color: #34d399; }}
        .negative {{ color: #f87171; }}
        .neutral {{ color: #9ca3af; }}
        
        .chart-section {{
            background: #1a1f2e;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            border: 1px solid #374151;
        }}
        .chart-title {{
            font-size: 18px;
            color: #60a5fa;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 1px solid #374151;
        }}
        .chart-image {{
            width: 100%;
            height: auto;
            border-radius: 4px;
        }}
        
        .trades-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }}
        .trades-table th {{
            text-align: left;
            padding: 12px;
            font-size: 12px;
            color: #9ca3af;
            text-transform: uppercase;
            border-bottom: 1px solid #374151;
        }}
        .trades-table td {{
            padding: 10px 12px;
            border-bottom: 1px solid #1f2937;
        }}
        .trades-table tr:hover {{
            background: #1f2937;
        }}
        
        .footer {{
            text-align: center;
            padding: 20px;
            color: #6b7280;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 S001-Pro V3 回测报告</h1>
            <p>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>
        
        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-label">总收益率</div>
                <div class="metric-value {'positive' if self.result.total_return > 0 else 'negative'}">
                    {self.result.total_return:+.2f}%
                </div>
            </div>
            <div class="metric-card">
                <div class="metric-label">最大回撤</div>
                <div class="metric-value negative">
                    {self.result.max_drawdown:.2f}%
                </div>
            </div>
            <div class="metric-card">
                <div class="metric-label">夏普比率</div>
                <div class="metric-value {'positive' if self.result.sharpe_ratio > 1 else 'neutral'}">
                    {self.result.sharpe_ratio:.2f}
                </div>
            </div>
            <div class="metric-card">
                <div class="metric-label">胜率</div>
                <div class="metric-value {'positive' if self.result.win_rate > 0.5 else 'neutral'}">
                    {self.result.win_rate*100:.1f}%
                </div>
            </div>
            <div class="metric-card">
                <div class="metric-label">盈亏比</div>
                <div class="metric-value {'positive' if self.result.profit_factor > 1 else 'negative'}">
                    {self.result.profit_factor:.2f}
                </div>
            </div>
            <div class="metric-card">
                <div class="metric-label">总交易数</div>
                <div class="metric-value neutral">
                    {self.result.total_trades}
                </div>
            </div>
        </div>
        
        <div class="chart-section">
            <div class="chart-title">📈 权益曲线</div>
            <img class="chart-image" src="data:image/png;base64,{self.figures.get('equity_curve', '')}" alt="权益曲线">
        </div>
        
        <div class="chart-section">
            <div class="chart-title">📉 回撤曲线</div>
            <img class="chart-image" src="data:image/png;base64,{self.figures.get('drawdown', '')}" alt="回撤曲线">
        </div>
        
        <div class="chart-section">
            <div class="chart-title">💰 盈亏分布</div>
            <img class="chart-image" src="data:image/png;base64,{self.figures.get('pnl_distribution', '')}" alt="盈亏分布">
        </div>
        
        <div class="chart-section">
            <div class="chart-title">📅 月度收益</div>
            <img class="chart-image" src="data:image/png;base64,{self.figures.get('monthly_returns', '')}" alt="月度收益">
        </div>
        
        <div class="chart-section">
            <div class="chart-title">📝 最近交易</div>
            <table class="trades-table">
                <thead>
                    <tr>
                        <th>时间</th>
                        <th>交易对</th>
                        <th>方向</th>
                        <th>数量</th>
                        <th>盈亏</th>
                    </tr>
                </thead>
                <tbody>
                    {self._generate_trades_html()}
                </tbody>
            </table>
        </div>
        
        <div class="footer">
            <p>S001-Pro V3 回测报告 | 数据仅供参考</p>
        </div>
    </div>
</body>
</html>
"""
        
        # 保存文件
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)
        
        logger.info(f"回测报告已保存: {output_path}")
        return html
    
    def _generate_trades_html(self) -> str:
        """生成交易表格HTML"""
        recent_trades = self.result.trades[-20:]  # 最近20笔
        
        rows = []
        for trade in recent_trades:
            pnl = trade.get('pnl', 0)
            pnl_class = 'positive' if pnl > 0 else 'negative'
            
            row = f"""
            <tr>
                <td>{trade.get('timestamp', 'N/A')}</td>
                <td>{trade.get('symbol', 'N/A')}</td>
                <td>{trade.get('side', 'N/A')}</td>
                <td>{trade.get('quantity', 0):.4f}</td>
                <td class="{pnl_class}">{pnl:+.2f}</td>
            </tr>
            """
            rows.append(row)
        
        return ''.join(rows)
    
    def _fig_to_base64(self, fig) -> str:
        """将figure转换为base64字符串"""
        buf = BytesIO()
        fig.savefig(buf, format='png', dpi=100, bbox_inches='tight')
        buf.seek(0)
        img_str = base64.b64encode(buf.read()).decode('utf-8')
        return img_str
    
    def _generate_svg_equity(self) -> str:
        """生成简化的SVG权益曲线"""
        # 简化版，用于无matplotlib环境
        return ""


class LightweightVisualizer:
    """
    轻量级可视化器
    
    无需matplotlib，生成简单HTML/文本报告
    """
    
    def __init__(self, result: BacktestResult):
        self.result = result
    
    def generate_text_report(self) -> str:
        """生成文本报告"""
        report = []
        report.append("="*60)
        report.append("S001-Pro V3 回测报告 (文本版)")
        report.append("="*60)
        report.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")
        
        # 核心指标
        report.append("【核心指标】")
        report.append(f"  总收益率: {self.result.total_return:+.2f}%")
        report.append(f"  最大回撤: {self.result.max_drawdown:.2f}%")
        report.append(f"  夏普比率: {self.result.sharpe_ratio:.2f}")
        report.append(f"  胜率: {self.result.win_rate*100:.1f}%")
        report.append(f"  盈亏比: {self.result.profit_factor:.2f}")
        report.append(f"  总交易: {self.result.total_trades}")
        report.append("")
        
        # 权益曲线摘要
        if self.result.equity_curve:
            report.append("【权益曲线】")
            report.append(f"  起始: {self.result.equity_curve[0]:.2f} USDT")
            report.append(f"  结束: {self.result.equity_curve[-1]:.2f} USDT")
            report.append(f"  峰值: {max(self.result.equity_curve):.2f} USDT")
            report.append(f"  谷值: {min(self.result.equity_curve):.2f} USDT")
            report.append("")
        
        # 最近交易
        if self.result.trades:
            report.append("【最近5笔交易】")
            for trade in self.result.trades[-5:]:
                pnl = trade.get('pnl', 0)
                symbol = trade.get('symbol', 'N/A')
                side = trade.get('side', 'N/A')
                report.append(f"  {symbol} {side}: {pnl:+.2f} USDT")
            report.append("")
        
        report.append("="*60)
        
        return '\n'.join(report)
    
    def generate_simple_html(self, output_path: str = "backtest_simple.html"):
        """生成简化HTML报告"""
        text_report = self.generate_text_report()
        
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>S001-Pro V3 回测报告</title>
    <style>
        body {{
            font-family: monospace;
            background: #0f1419;
            color: #e0e0e0;
            padding: 20px;
            white-space: pre-wrap;
        }}
    </style>
</head>
<body>
{text_report}
</body>
</html>
"""
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)
        
        return html


# 便捷函数
def visualize_backtest(result: BacktestResult, 
                      output_dir: str = "reports") -> Dict[str, str]:
    """便捷可视化回测结果"""
    import os
    os.makedirs(output_dir, exist_ok=True)
    
    if MATPLOTLIB_AVAILABLE:
        visualizer = BacktestVisualizer(result)
        visualizer.generate_html_report(f"{output_dir}/backtest_report.html")
        return visualizer.figures
    else:
        visualizer = LightweightVisualizer(result)
        visualizer.generate_simple_html(f"{output_dir}/backtest_report.html")
        return {}


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    print("="*60)
    print("回测可视化测试")
    print("="*60)
    
    # 创建模拟回测结果
    import random
    from datetime import timedelta
    
    start_date = datetime.now() - timedelta(days=90)
    timestamps = [start_date + timedelta(days=i) for i in range(90)]
    
    # 生成权益曲线
    equity = [1000.0]
    for i in range(89):
        change = random.uniform(-0.02, 0.025)
        equity.append(equity[-1] * (1 + change))
    
    # 生成交易
    trades = []
    for i in range(20):
        trades.append({
            'timestamp': (start_date + timedelta(days=i*4)).isoformat(),
            'symbol': random.choice(['BTC/USDT', 'ETH/USDT']),
            'side': random.choice(['long', 'short']),
            'quantity': random.uniform(0.1, 1.0),
            'pnl': random.uniform(-50, 80),
            'equity': equity[i*4]
        })
    
    result = BacktestResult(
        timestamps=timestamps,
        equity_curve=equity,
        trades=trades,
        positions=[],
        signals=[],
        total_return=(equity[-1] - equity[0]) / equity[0] * 100,
        max_drawdown=8.5,
        sharpe_ratio=1.45,
        win_rate=0.62,
        profit_factor=1.8,
        total_trades=20,
        monthly_returns=[0.03, -0.02, 0.05, 0.01]
    )
    
    print(f"\n回测数据:")
    print(f"  收益率: {result.total_return:.2f}%")
    print(f"  交易数: {result.total_trades}")
    
    # 生成可视化
    if MATPLOTLIB_AVAILABLE:
        print("\n生成图表...")
        visualizer = BacktestVisualizer(result)
        figures = visualizer.generate_all_charts()
        print(f"  生成图表数: {len(figures)}")
        
        # 生成HTML报告
        visualizer.generate_html_report("test_backtest_report.html")
        print("  报告已保存: test_backtest_report.html")
    else:
        print("\n使用轻量级可视化...")
        visualizer = LightweightVisualizer(result)
        print(visualizer.generate_text_report())
        visualizer.generate_simple_html("test_backtest_simple.html")
    
    print("\n" + "="*60)
