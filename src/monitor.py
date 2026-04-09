"""
S001-Pro V3 监控模块
职责: Web面板 + Telegram通知
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime
import json

from config import get_config
from database import get_db

logger = logging.getLogger(__name__)


class Notifier:
    """通知器基类"""
    
    def notify(self, event: str, data: Dict):
        raise NotImplementedError


class TelegramNotifier(Notifier):
    """Telegram通知"""
    
    def __init__(self):
        self.cfg = get_config()
        self.enabled = self.cfg.notification.enabled
        self.bot_token = self.cfg.notification.bot_token
        self.chat_id = self.cfg.notification.chat_id
        
        if self.enabled and (not self.bot_token or not self.chat_id):
            logger.warning("Telegram not fully configured")
            self.enabled = False
    
    def notify(self, event: str, data: Dict):
        """发送通知"""
        if not self.enabled:
            return
        
        if event not in self.cfg.notification.events:
            return
        
        message = self._format_message(event, data)
        self._send_telegram(message)
    
    def _format_message(self, event: str, data: Dict) -> str:
        """格式化消息"""
        if event == 'position_opened':
            return f"""
🟢 <b>开仓</b>
配对: {data['pair_key']}
方向: {data['direction']}
Z-Score: {data['entry_z']:.2f}
金额: {data['notional']:.2f} USDT
时间: {datetime.now().strftime('%H:%M:%S')}
"""
        elif event == 'position_closed':
            emoji = '🟢' if data['pnl'] > 0 else '🔴'
            return f"""
{emoji} <b>平仓</b>
配对: {data['pair_key']}
盈亏: {data['pnl']:+.2f} USDT ({data['pnl_pct']:+.2f}%)
原因: {data['reason']}
持仓时间: {data.get('hold_time', 'N/A')}
时间: {datetime.now().strftime('%H:%M:%S')}
"""
        elif event == 'error':
            return f"""
🔴 <b>错误</b>
类型: {data.get('type', 'Unknown')}
消息: {data.get('message', 'No message')}
时间: {datetime.now().strftime('%H:%M:%S')}
"""
        else:
            return f"[{event}] {json.dumps(data, default=str)}"
    
    def _send_telegram(self, message: str):
        """发送Telegram消息"""
        try:
            import requests
            
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                'chat_id': self.chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }
            
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code != 200:
                logger.error(f"Failed to send Telegram message: {response.text}")
                
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")


class WebDashboard:
    """Web面板 - 使用FastAPI"""
    
    def __init__(self):
        self.cfg = get_config()
        self.db = get_db()
        self.app = None
    
    def create_app(self):
        """创建FastAPI应用"""
        from fastapi import FastAPI
        from fastapi.staticfiles import StaticFiles
        from fastapi.responses import HTMLResponse, JSONResponse
        
        app = FastAPI(title="S001-Pro V3 Dashboard")
        
        # API路由
        @app.get("/api/status")
        async def get_status():
            """获取策略状态"""
            return self._get_status()
        
        @app.get("/api/positions")
        async def get_positions():
            """获取当前持仓"""
            positions = self.db.get_open_positions()
            return [self._position_to_dict(p) for p in positions]
        
        @app.get("/api/pairs")
        async def get_pairs(pool: str = "primary"):
            """获取活跃配对"""
            pairs = self.db.get_active_pairs(pool)
            return [self._pair_to_dict(p) for p in pairs]
        
        @app.get("/api/trades")
        async def get_trades(limit: int = 20):
            """获取最近交易"""
            trades = self.db.get_today_trades()
            return [self._trade_to_dict(t) for t in trades[:limit]]
        
        @app.get("/api/stats")
        async def get_stats(days: int = 7):
            """获取统计信息"""
            return self.db.get_trade_stats(days)
        
        # HTML页面
        @app.get("/", response_class=HTMLResponse)
        async def dashboard():
            """主面板"""
            return self._render_dashboard()
        
        self.app = app
        return app
    
    def _get_status(self) -> Dict:
        """获取策略状态"""
        positions = self.db.get_open_positions()
        
        # 计算今日盈亏
        today_trades = self.db.get_today_trades()
        today_pnl = sum(t.pnl for t in today_trades)
        
        # 未实现盈亏
        unrealized = sum(p.unrealized_pnl or 0 for p in positions)
        
        return {
            'status': 'running',
            'timestamp': datetime.now().isoformat(),
            'open_positions': len(positions),
            'today_trades': len(today_trades),
            'today_pnl': round(today_pnl, 2),
            'unrealized_pnl': round(unrealized, 2)
        }
    
    def _position_to_dict(self, pos) -> Dict:
        """持仓转字典"""
        return {
            'pair_key': pos.pair_key,
            'pool': pos.pool,
            'direction': pos.direction,
            'entry_z': round(pos.entry_z, 2),
            'current_z': round(pos.current_z, 2) if pos.current_z else None,
            'entry_price_a': pos.entry_price_a,
            'entry_price_b': pos.entry_price_b,
            'unrealized_pnl': round(pos.unrealized_pnl, 2) if pos.unrealized_pnl else 0,
            'entry_time': pos.entry_time,
            'status': pos.status
        }
    
    def _pair_to_dict(self, pair) -> Dict:
        """配对转字典"""
        return {
            'symbol_a': pair.symbol_a,
            'symbol_b': pair.symbol_b,
            'score': round(pair.score, 3),
            'pf': round(pair.pf, 2),
            'sharpe': round(pair.sharpe, 2) if pair.sharpe else None,
            'z_entry': pair.z_entry,
            'z_exit': pair.z_exit,
            'z_stop': pair.z_stop,
            'trades_count': pair.trades_count
        }
    
    def _trade_to_dict(self, trade) -> Dict:
        """交易转字典"""
        return {
            'pair_key': trade.pair_key,
            'direction': trade.direction,
            'entry_time': trade.entry_time,
            'exit_time': trade.exit_time,
            'pnl': round(trade.pnl, 2),
            'pnl_pct': round(trade.pnl_pct, 2),
            'exit_reason': trade.exit_reason
        }
    
    def _render_dashboard(self) -> str:
        """渲染主面板HTML"""
        return """
<!DOCTYPE html>
<html>
<head>
    <title>S001-Pro V3 Dashboard</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0a0e1a; color: #e0e0e0; padding: 20px; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; padding-bottom: 20px; border-bottom: 1px solid #1a2332; }
        .header h1 { color: #00d4aa; font-size: 24px; }
        .status { display: flex; gap: 20px; }
        .status-item { text-align: center; }
        .status-item .value { font-size: 24px; font-weight: bold; color: #fff; }
        .status-item .label { font-size: 12px; color: #888; margin-top: 4px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 20px; }
        .card { background: #111827; border-radius: 8px; padding: 20px; border: 1px solid #1a2332; }
        .card h2 { font-size: 16px; margin-bottom: 15px; color: #00d4aa; }
        .card h2 span { font-size: 12px; color: #666; margin-left: 10px; }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th { text-align: left; padding: 10px 5px; color: #888; font-weight: normal; border-bottom: 1px solid #1a2332; }
        td { padding: 10px 5px; border-bottom: 1px solid #1a2332; }
        tr:hover { background: #1a2332; }
        .positive { color: #00d4aa; }
        .negative { color: #ff4757; }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; }
        .badge-long { background: rgba(0, 212, 170, 0.2); color: #00d4aa; }
        .badge-short { background: rgba(255, 71, 87, 0.2); color: #ff4757; }
        .loading { text-align: center; padding: 40px; color: #666; }
    </style>
</head>
<body>
    <div class="header">
        <h1>📊 S001-Pro V3 Dashboard</h1>
        <div class="status" id="status">
            <div class="status-item">
                <div class="value" id="open-positions">-</div>
                <div class="label">持仓</div>
            </div>
            <div class="status-item">
                <div class="value" id="today-pnl">-</div>
                <div class="label">今日盈亏</div>
            </div>
            <div class="status-item">
                <div class="value" id="unrealized">-</div>
                <div class="label">未实现盈亏</div>
            </div>
        </div>
    </div>
    
    <div class="grid">
        <div class="card">
            <h2>当前持仓 <span>实时</span></h2>
            <div id="positions-container">
                <div class="loading">加载中...</div>
            </div>
        </div>
        
        <div class="card">
            <h2>活跃配对 <span>Top 30</span></h2>
            <div id="pairs-container">
                <div class="loading">加载中...</div>
            </div>
        </div>
        
        <div class="card">
            <h2>最近交易</h2>
            <div id="trades-container">
                <div class="loading">加载中...</div>
            </div>
        </div>
        
        <div class="card">
            <h2>统计指标 <span>7天</span></h2>
            <div id="stats-container">
                <div class="loading">加载中...</div>
            </div>
        </div>
    </div>
    
    <script>
        async function fetchData() {
            try {
                // 获取状态
                const statusRes = await fetch('/api/status');
                const status = await statusRes.json();
                document.getElementById('open-positions').textContent = status.open_positions;
                document.getElementById('today-pnl').textContent = (status.today_pnl >= 0 ? '+' : '') + status.today_pnl.toFixed(2);
                document.getElementById('today-pnl').className = 'value ' + (status.today_pnl >= 0 ? 'positive' : 'negative');
                document.getElementById('unrealized').textContent = (status.unrealized_pnl >= 0 ? '+' : '') + status.unrealized_pnl.toFixed(2);
                document.getElementById('unrealized').className = 'value ' + (status.unrealized_pnl >= 0 ? 'positive' : 'negative');
                
                // 获取持仓
                const positionsRes = await fetch('/api/positions');
                const positions = await positionsRes.json();
                renderPositions(positions);
                
                // 获取配对
                const pairsRes = await fetch('/api/pairs');
                const pairs = await pairsRes.json();
                renderPairs(pairs);
                
                // 获取交易
                const tradesRes = await fetch('/api/trades');
                const trades = await tradesRes.json();
                renderTrades(trades);
                
                // 获取统计
                const statsRes = await fetch('/api/stats');
                const stats = await statsRes.json();
                renderStats(stats);
                
            } catch (error) {
                console.error('Fetch error:', error);
            }
        }
        
        function renderPositions(positions) {
            const container = document.getElementById('positions-container');
            if (positions.length === 0) {
                container.innerHTML = '<div class="loading">暂无持仓</div>';
                return;
            }
            
            const html = '<table><thead><tr><th>配对</th><th>方向</th><th>进场Z</th><th>当前Z</th><th>盈亏</th></tr></thead><tbody>' +
                positions.map(p => '<tr>' +
                    '<td>' + p.pair_key + '</td>' +
                    '<td><span class="badge badge-' + (p.direction.includes('long') ? 'long' : 'short') + '">' + p.direction + '</span></td>' +
                    '<td>' + p.entry_z.toFixed(2) + '</td>' +
                    '<td>' + (p.current_z ? p.current_z.toFixed(2) : '-') + '</td>' +
                    '<td class="' + (p.unrealized_pnl >= 0 ? 'positive' : 'negative') + '">' + (p.unrealized_pnl >= 0 ? '+' : '') + p.unrealized_pnl.toFixed(2) + '</td>' +
                '</tr>').join('') +
                '</tbody></table>';
            container.innerHTML = html;
        }
        
        function renderPairs(pairs) {
            const container = document.getElementById('pairs-container');
            if (pairs.length === 0) {
                container.innerHTML = '<div class="loading">暂无配对</div>';
                return;
            }
            
            const html = '<table><thead><tr><th>配对</th><th>评分</th><th>PF</th><th>参数</th></tr></thead><tbody>' +
                pairs.slice(0, 10).map(p => '<tr>' +
                    '<td>' + p.symbol_a + '-' + p.symbol_b + '</td>' +
                    '<td>' + p.score.toFixed(3) + '</td>' +
                    '<td>' + p.pf.toFixed(2) + '</td>' +
                    '<td>' + p.z_entry.toFixed(1) + '/' + p.z_exit.toFixed(1) + '/' + p.z_stop.toFixed(1) + '</td>' +
                '</tr>').join('') +
                '</tbody></table>';
            container.innerHTML = html;
        }
        
        function renderTrades(trades) {
            const container = document.getElementById('trades-container');
            if (trades.length === 0) {
                container.innerHTML = '<div class="loading">暂无交易</div>';
                return;
            }
            
            const html = '<table><thead><tr><th>配对</th><th>盈亏</th><th>原因</th></tr></thead><tbody>' +
                trades.map(t => '<tr>' +
                    '<td>' + t.pair_key + '</td>' +
                    '<td class="' + (t.pnl >= 0 ? 'positive' : 'negative') + '">' + (t.pnl >= 0 ? '+' : '') + t.pnl.toFixed(2) + '</td>' +
                    '<td>' + t.exit_reason + '</td>' +
                '</tr>').join('') +
                '</tbody></table>';
            container.innerHTML = html;
        }
        
        function renderStats(stats) {
            const container = document.getElementById('stats-container');
            const html = '<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px;">' +
                '<div><div style="color: #888; font-size: 12px;">总交易</div><div style="font-size: 20px; font-weight: bold;">' + stats.total_trades + '</div></div>' +
                '<div><div style="color: #888; font-size: 12px;">胜率</div><div style="font-size: 20px; font-weight: bold;">' + (stats.win_rate * 100).toFixed(1) + '%</div></div>' +
                '<div><div style="color: #888; font-size: 12px;">盈亏比</div><div style="font-size: 20px; font-weight: bold;">' + stats.pf.toFixed(2) + '</div></div>' +
                '<div><div style="color: #888; font-size: 12px;">总盈亏</div><div style="font-size: 20px; font-weight: bold; color: ' + (stats.total_pnl >= 0 ? '#00d4aa' : '#ff4757') + '">' + (stats.total_pnl >= 0 ? '+' : '') + stats.total_pnl.toFixed(2) + '</div></div>' +
            '</div>';
            container.innerHTML = html;
        }
        
        // 初始加载和定时刷新
        fetchData();
        setInterval(fetchData, 5000);
    </script>
</body>
</html>
        """


class Monitor:
    """监控管理器"""
    
    def __init__(self):
        self.cfg = get_config()
        self.telegram = TelegramNotifier()
        self.dashboard = WebDashboard()
        self.db = get_db()
    
    def notify_event(self, event: str, data: Dict):
        """发送事件通知"""
        self.telegram.notify(event, data)
    
    def start_web(self):
        """启动Web面板"""
        import uvicorn
        
        app = self.dashboard.create_app()
        
        uvicorn.run(
            app,
            host=self.cfg.web['host'],
            port=self.cfg.web['port'],
            log_level="info"
        )


if __name__ == "__main__":
    monitor = Monitor()
    print("Monitor test passed")
