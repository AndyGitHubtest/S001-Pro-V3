"""
Web监控面板
实时展示策略运行状态
"""
import os
import json
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import asdict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import uvicorn
import logging

logger = logging.getLogger(__name__)

# HTML模板
DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>S001-Pro V3 监控面板</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f1419;
            color: #e0e0e0;
            padding: 20px;
        }
        .header {
            text-align: center;
            margin-bottom: 30px;
            padding: 20px;
            background: linear-gradient(135deg, #1a1f2e 0%, #2d3748 100%);
            border-radius: 12px;
            border: 1px solid #374151;
        }
        .header h1 {
            font-size: 28px;
            color: #60a5fa;
            margin-bottom: 10px;
        }
        .status-badge {
            display: inline-block;
            padding: 6px 16px;
            border-radius: 20px;
            font-size: 14px;
            font-weight: 600;
        }
        .status-running { background: #065f46; color: #34d399; }
        .status-stopped { background: #7f1d1d; color: #f87171; }
        .status-warning { background: #92400e; color: #fbbf24; }
        
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .card {
            background: #1a1f2e;
            border-radius: 12px;
            padding: 20px;
            border: 1px solid #374151;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .card:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 40px rgba(0,0,0,0.3);
        }
        .card-title {
            font-size: 14px;
            color: #9ca3af;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 12px;
        }
        .metric-value {
            font-size: 32px;
            font-weight: 700;
            color: #f3f4f6;
        }
        .metric-unit {
            font-size: 16px;
            color: #6b7280;
            margin-left: 4px;
        }
        .metric-change {
            font-size: 14px;
            margin-top: 8px;
        }
        .positive { color: #34d399; }
        .negative { color: #f87171; }
        .neutral { color: #9ca3af; }
        
        .section-title {
            font-size: 18px;
            color: #60a5fa;
            margin: 30px 0 15px 0;
            padding-bottom: 10px;
            border-bottom: 1px solid #374151;
        }
        
        .positions-table {
            width: 100%;
            border-collapse: collapse;
        }
        .positions-table th {
            text-align: left;
            padding: 12px;
            font-size: 12px;
            color: #9ca3af;
            text-transform: uppercase;
            border-bottom: 1px solid #374151;
        }
        .positions-table td {
            padding: 12px;
            border-bottom: 1px solid #1f2937;
        }
        .positions-table tr:hover {
            background: #1f2937;
        }
        
        .log-container {
            background: #0d1117;
            border-radius: 8px;
            padding: 15px;
            height: 300px;
            overflow-y: auto;
            font-family: 'Consolas', 'Monaco', monospace;
            font-size: 12px;
            line-height: 1.6;
        }
        .log-entry {
            padding: 2px 0;
            border-bottom: 1px solid #1f2937;
        }
        .log-time { color: #6b7280; }
        .log-level-info { color: #60a5fa; }
        .log-level-warning { color: #fbbf24; }
        .log-level-error { color: #f87171; }
        .log-message { color: #e0e0e0; }
        
        .connection-status {
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
            z-index: 1000;
        }
        .conn-connected { background: #065f46; color: #34d399; }
        .conn-disconnected { background: #7f1d1d; color: #f87171; }
        
        .last-update {
            text-align: center;
            color: #6b7280;
            font-size: 12px;
            margin-top: 30px;
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .updating {
            animation: pulse 1s infinite;
        }
    </style>
</head>
<body>
    <div id="connection-status" class="connection-status conn-disconnected">
        ● 断开连接
    </div>
    
    <div class="header">
        <h1>📊 S001-Pro V3 监控面板</h1>
        <span id="system-status" class="status-badge status-stopped">初始化中...</span>
    </div>
    
    <div class="grid">
        <div class="card">
            <div class="card-title">今日盈亏</div>
            <div class="metric-value">
                <span id="daily-pnl">0.00</span>
                <span class="metric-unit">USDT</span>
            </div>
            <div id="daily-pnl-change" class="metric-change neutral">--</div>
        </div>
        
        <div class="card">
            <div class="card-title">总收益率</div>
            <div class="metric-value">
                <span id="total-return">0.00</span>
                <span class="metric-unit">%</span>
            </div>
            <div id="total-return-change" class="metric-change neutral">--</div>
        </div>
        
        <div class="card">
            <div class="card-title">持仓数量</div>
            <div class="metric-value">
                <span id="position-count">0</span>
                <span class="metric-unit">对</span>
            </div>
            <div class="metric-change neutral">最大5对</div>
        </div>
        
        <div class="card">
            <div class="card-title">运行时间</div>
            <div class="metric-value">
                <span id="uptime">00:00:00</span>
            </div>
            <div class="metric-change neutral">稳定运行</div>
        </div>
    </div>
    
    <h2 class="section-title">📈 当前持仓</h2>
    <div class="card">
        <table class="positions-table">
            <thead>
                <tr>
                    <th>交易对</th>
                    <th>方向</th>
                    <th>数量</th>
                    <th>入场价</th>
                    <th>当前价</th>
                    <th>未实现盈亏</th>
                </tr>
            </thead>
            <tbody id="positions-tbody">
                <tr>
                    <td colspan="6" style="text-align: center; color: #6b7280;">暂无持仓</td>
                </tr>
            </tbody>
        </table>
    </div>
    
    <h2 class="section-title">📝 实时日志</h2>
    <div class="card">
        <div id="log-container" class="log-container">
            <div class="log-entry">
                <span class="log-time">--:--:--</span>
                <span class="log-message">等待连接...</span>
            </div>
        </div>
    </div>
    
    <div class="last-update">
        最后更新: <span id="last-update">--</span>
    </div>
    
    <script>
        let ws;
        let reconnectInterval;
        
        function connect() {
            ws = new WebSocket(`ws://${window.location.host}/ws`);
            
            ws.onopen = function() {
                console.log('WebSocket连接成功');
                document.getElementById('connection-status').className = 'connection-status conn-connected';
                document.getElementById('connection-status').textContent = '● 已连接';
                clearInterval(reconnectInterval);
            };
            
            ws.onmessage = function(event) {
                const data = JSON.parse(event.data);
                updateDashboard(data);
            };
            
            ws.onclose = function() {
                console.log('WebSocket断开');
                document.getElementById('connection-status').className = 'connection-status conn-disconnected';
                document.getElementById('connection-status').textContent = '● 断开连接';
                
                // 重连
                reconnectInterval = setInterval(connect, 5000);
            };
            
            ws.onerror = function(error) {
                console.error('WebSocket错误:', error);
            };
        }
        
        function updateDashboard(data) {
            // 系统状态
            const statusEl = document.getElementById('system-status');
            if (data.system_status === 'running') {
                statusEl.className = 'status-badge status-running';
                statusEl.textContent = '🟢 运行中';
            } else if (data.system_status === 'warning') {
                statusEl.className = 'status-badge status-warning';
                statusEl.textContent = '🟡 警告';
            } else {
                statusEl.className = 'status-badge status-stopped';
                statusEl.textContent = '🔴 停止';
            }
            
            // 今日盈亏
            const dailyPnl = data.daily_pnl || 0;
            document.getElementById('daily-pnl').textContent = dailyPnl.toFixed(2);
            const pnlChange = document.getElementById('daily-pnl-change');
            if (dailyPnl > 0) {
                pnlChange.className = 'metric-change positive';
                pnlChange.textContent = `+${dailyPnl.toFixed(2)} USDT`;
            } else if (dailyPnl < 0) {
                pnlChange.className = 'metric-change negative';
                pnlChange.textContent = `${dailyPnl.toFixed(2)} USDT`;
            }
            
            // 总收益
            document.getElementById('total-return').textContent = (data.total_return || 0).toFixed(2);
            
            // 持仓数量
            document.getElementById('position-count').textContent = (data.positions || []).length;
            
            // 运行时间
            document.getElementById('uptime').textContent = data.uptime || '00:00:00';
            
            // 持仓表格
            const tbody = document.getElementById('positions-tbody');
            if (data.positions && data.positions.length > 0) {
                tbody.innerHTML = data.positions.map(pos => `
                    <tr>
                        <td>${pos.symbol}</td>
                        <td style="color: ${pos.side === 'long' ? '#34d399' : '#f87171'}">${pos.side === 'long' ? '做多' : '做空'}</td>
                        <td>${pos.quantity.toFixed(4)}</td>
                        <td>${pos.entry_price.toFixed(2)}</td>
                        <td>${pos.current_price.toFixed(2)}</td>
                        <td style="color: ${pos.unrealized_pnl >= 0 ? '#34d399' : '#f87171'}">${pos.unrealized_pnl.toFixed(2)} USDT</td>
                    </tr>
                `).join('');
            } else {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: #6b7280;">暂无持仓</td></tr>';
            }
            
            // 日志
            if (data.logs && data.logs.length > 0) {
                const logContainer = document.getElementById('log-container');
                data.logs.forEach(log => {
                    const entry = document.createElement('div');
                    entry.className = 'log-entry';
                    const levelClass = `log-level-${log.level.toLowerCase()}`;
                    entry.innerHTML = `
                        <span class="log-time">${log.time}</span>
                        <span class="${levelClass}">[${log.level}]</span>
                        <span class="log-message">${log.message}</span>
                    `;
                    logContainer.appendChild(entry);
                    logContainer.scrollTop = logContainer.scrollHeight;
                });
                
                // 限制日志数量
                while (logContainer.children.length > 100) {
                    logContainer.removeChild(logContainer.firstChild);
                }
            }
            
            // 最后更新时间
            document.getElementById('last-update').textContent = new Date().toLocaleString('zh-CN');
        }
        
        // 启动连接
        connect();
    </script>
</body>
</html>
"""


class WebDashboard:
    """
    Web监控面板
    
    功能:
    1. 实时状态展示
    2. WebSocket实时推送
    3. 日志实时查看
    4. 手动控制接口
    """
    
    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        self.host = host
        self.port = port
        self.app = FastAPI(title="S001-Pro V3 Dashboard")
        
        # 状态数据
        self.system_status = "stopped"
        self.daily_pnl = 0.0
        self.total_return = 0.0
        self.positions: List[Dict] = []
        self.uptime = "00:00:00"
        self.logs: List[Dict] = []
        
        # WebSocket连接管理
        self.connections: List[WebSocket] = []
        
        # 启动时间
        self.start_time = datetime.now()
        
        self._setup_routes()
    
    def _setup_routes(self):
        """设置路由"""
        
        @self.app.get("/", response_class=HTMLResponse)
        async def index():
            return DASHBOARD_HTML
        
        @self.app.get("/api/status")
        async def get_status():
            """获取状态API"""
            return self._get_status_data()
        
        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket实时推送"""
            await websocket.accept()
            self.connections.append(websocket)
            
            try:
                # 发送初始数据
                await websocket.send_json(self._get_status_data())
                
                while True:
                    # 保持连接并等待消息
                    data = await websocket.receive_text()
                    if data == "ping":
                        await websocket.send_text("pong")
                    
            except WebSocketDisconnect:
                self.connections.remove(websocket)
            except Exception as e:
                logger.error(f"WebSocket错误: {e}")
                if websocket in self.connections:
                    self.connections.remove(websocket)
    
    def _get_status_data(self) -> Dict:
        """获取状态数据"""
        # 计算运行时间
        elapsed = datetime.now() - self.start_time
        hours, remainder = divmod(int(elapsed.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        self.uptime = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        
        return {
            "system_status": self.system_status,
            "daily_pnl": self.daily_pnl,
            "total_return": self.total_return,
            "positions": self.positions,
            "uptime": self.uptime,
            "logs": self.logs[-10:],  # 最近10条日志
            "timestamp": datetime.now().isoformat()
        }
    
    def update_status(self, **kwargs):
        """更新状态"""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        
        # 广播更新
        asyncio.create_task(self._broadcast_update())
    
    def add_position(self, symbol: str, side: str, quantity: float,
                    entry_price: float, current_price: float = None):
        """添加持仓"""
        position = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "entry_price": entry_price,
            "current_price": current_price or entry_price,
            "unrealized_pnl": 0.0
        }
        
        # 更新或添加
        existing = next((p for p in self.positions if p["symbol"] == symbol), None)
        if existing:
            existing.update(position)
        else:
            self.positions.append(position)
        
        self.update_status()
    
    def remove_position(self, symbol: str):
        """移除持仓"""
        self.positions = [p for p in self.positions if p["symbol"] != symbol]
        self.update_status()
    
    def add_log(self, level: str, message: str):
        """添加日志"""
        log_entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message
        }
        self.logs.append(log_entry)
        
        # 限制日志数量
        if len(self.logs) > 1000:
            self.logs = self.logs[-1000:]
        
        # 广播更新
        asyncio.create_task(self._broadcast_update())
    
    async def _broadcast_update(self):
        """广播更新到所有连接"""
        data = self._get_status_data()
        disconnected = []
        
        for conn in self.connections:
            try:
                await conn.send_json(data)
            except:
                disconnected.append(conn)
        
        # 清理断开连接
        for conn in disconnected:
            if conn in self.connections:
                self.connections.remove(conn)
    
    def run(self):
        """运行面板"""
        logger.info(f"启动监控面板: http://{self.host}:{self.port}")
        uvicorn.run(self.app, host=self.host, port=self.port, log_level="warning")
    
    def run_in_thread(self):
        """在线程中运行"""
        import threading
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        return thread


# 便捷函数
_dashboard: Optional[WebDashboard] = None

def get_dashboard() -> WebDashboard:
    """获取全局面板实例"""
    global _dashboard
    if _dashboard is None:
        _dashboard = WebDashboard()
    return _dashboard


def start_dashboard(host: str = "0.0.0.0", port: int = 8080) -> WebDashboard:
    """启动监控面板"""
    dashboard = WebDashboard(host=host, port=port)
    dashboard.run_in_thread()
    return dashboard


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    print("="*60)
    print("Web监控面板测试")
    print("="*60)
    print("\n启动面板: http://localhost:8080")
    print("按Ctrl+C停止\n")
    
    dashboard = WebDashboard(port=8080)
    
    # 模拟数据更新
    import time
    import random
    
    def simulate_updates():
        """模拟更新"""
        dashboard.system_status = "running"
        
        while True:
            time.sleep(2)
            
            # 更新盈亏
            dashboard.daily_pnl += random.uniform(-10, 15)
            dashboard.total_return += random.uniform(-0.1, 0.2)
            
            # 添加日志
            levels = ["INFO", "WARNING", "ERROR"]
            messages = [
                "扫描完成，发现4对交易机会",
                "下单成功 BTC/USDT",
                "持仓同步完成",
                "Z-Score信号触发",
            ]
            dashboard.add_log(
                random.choice(levels),
                random.choice(messages)
            )
            
            # 偶尔更新持仓
            if random.random() < 0.3:
                dashboard.add_position(
                    f"COIN{random.randint(1, 5)}/USDT",
                    random.choice(["long", "short"]),
                    random.uniform(0.1, 1.0),
                    random.uniform(100, 50000)
                )
    
    # 启动模拟线程
    import threading
    sim_thread = threading.Thread(target=simulate_updates, daemon=True)
    sim_thread.start()
    
    # 运行面板
    dashboard.run()
