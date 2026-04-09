"""
S001-Pro V3 监控面板 V2 - Web服务器
支持6页面SPA架构
"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from typing import Optional, List
import logging
from datetime import datetime, timedelta
import os
import sys

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)


class WebServerV2:
    """监控面板V2 Web服务器"""
    
    def __init__(self, strategy=None, db=None, config=None):
        self.strategy = strategy
        self.db = db
        self.config = config
        self.app = None
        
    def create_app(self) -> FastAPI:
        """创建FastAPI应用"""
        app = FastAPI(
            title="S001-Pro V3 Dashboard V2",
            version="2.0.0",
            docs_url=None,
            redoc_url=None
        )
        
        # 获取项目根目录
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        static_dir = os.path.join(base_dir, "src", "monitoring", "static")
        templates_dir = os.path.join(base_dir, "src", "monitoring", "templates")
        
        # 确保目录存在
        os.makedirs(static_dir, exist_ok=True)
        os.makedirs(os.path.join(static_dir, "js"), exist_ok=True)
        os.makedirs(os.path.join(static_dir, "css"), exist_ok=True)
        os.makedirs(templates_dir, exist_ok=True)
        
        # 挂载静态文件
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
        
        # ========== API v2 路由 (必须先定义，避免被SPA路由捕获) ==========
        # Dashboard APIs
        @app.get("/api/v2/dashboard/summary")
        async def dashboard_summary():
            """Dashboard概览数据"""
            return self._get_dashboard_summary()
        
        @app.get("/api/v2/dashboard/alerts")
        async def dashboard_alerts(limit: int = Query(5, ge=1, le=20)):
            """Dashboard告警摘要"""
            return self._get_dashboard_alerts(limit)
        
        @app.get("/api/v2/dashboard/metrics")
        async def dashboard_metrics(days: int = Query(7, ge=1, le=30)):
            """Dashboard关键指标"""
            return self._get_dashboard_metrics(days)
        
        # Market APIs
        @app.get("/api/v2/market/pairs")
        async def market_pairs(pool: str = Query("primary")):
            """获取配对列表"""
            return self._get_market_pairs(pool)
        
        @app.get("/api/v2/market/pair/{symbol_a}/{symbol_b}")
        async def market_pair_detail(symbol_a: str, symbol_b: str):
            """获取配对详情"""
            return self._get_market_pair_detail(symbol_a, symbol_b)
        
        @app.get("/api/v2/market/scan-history")
        async def market_scan_history(limit: int = Query(10, ge=1, le=50)):
            """扫描历史"""
            return self._get_scan_history(limit)
        
        @app.get("/api/v2/market/funnel")
        async def market_funnel():
            """扫描漏斗数据"""
            return self._get_scan_funnel()
        
        # Signals APIs
        @app.get("/api/v2/signals/active")
        async def signals_active():
            """活跃信号"""
            return self._get_active_signals()
        
        @app.get("/api/v2/signals/history")
        async def signals_history(limit: int = Query(100, ge=1, le=500)):
            """信号历史"""
            return self._get_signals_history(limit)
        
        @app.get("/api/v2/signals/stats")
        async def signals_stats(days: int = Query(7, ge=1, le=30)):
            """信号统计"""
            return self._get_signals_stats(days)
        
        # Positions APIs
        @app.get("/api/v2/positions/open")
        async def positions_open():
            """当前持仓"""
            return self._get_open_positions()
        
        @app.get("/api/v2/positions/history")
        async def positions_history(days: int = Query(7, ge=1, le=30)):
            """历史持仓"""
            return self._get_positions_history(days)
        
        # Risk APIs
        @app.get("/api/v2/risk/overview")
        async def risk_overview():
            """风控概览"""
            return self._get_risk_overview()
        
        @app.get("/api/v2/risk/limits")
        async def risk_limits():
            """风控限额"""
            return self._get_risk_limits()
        
        @app.get("/api/v2/risk/circuit-breakers")
        async def risk_circuit_breakers():
            """熔断器状态"""
            return self._get_circuit_breakers()
        
        @app.get("/api/v2/risk/logs")
        async def risk_logs(limit: int = Query(50, ge=1, le=200)):
            """风控日志"""
            return self._get_risk_logs(limit)
        
        # System APIs
        @app.get("/api/v2/system/resources")
        async def system_resources():
            """系统资源"""
            return self._get_system_resources()
        
        @app.get("/api/v2/system/data-quality")
        async def system_data_quality():
            """数据质量"""
            return self._get_data_quality()
        
        @app.get("/api/v2/system/api-stats")
        async def system_api_stats():
            """API监控"""
            return self._get_api_stats()
        
        @app.get("/api/v2/system/logs")
        async def system_logs(
            level: str = Query("INFO"),
            limit: int = Query(100, ge=1, le=500),
            search: Optional[str] = None
        ):
            """系统日志"""
            return self._get_system_logs(level, limit, search)
        
        @app.get("/api/v2/system/config")
        async def system_config():
            """系统配置"""
            return self._get_system_config()
        
        # 向后兼容 - v1 APIs
        @app.get("/api/status")
        async def v1_status():
            """V1状态API - 向后兼容"""
            return self._get_dashboard_summary()
        
        # ========== SPA路由 (放在最后，作为fallback) ==========
        @app.get("/", response_class=HTMLResponse)
        async def index():
            """主页面 - SPA入口"""
            return self._render_spa()
        
        @app.get("/{path:path}", response_class=HTMLResponse)
        async def spa_router(path: str):
            """SPA路由 - 所有非API路径返回同一页面"""
            # 排除API路径和静态文件
            if path.startswith('api/') or path.startswith('static/'):
                raise HTTPException(status_code=404)
            return self._render_spa()
        
        self.app = app
        return app
    
    def _render_spa(self) -> str:
        """渲染SPA主页面"""
        return '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>S001-Pro V3 Dashboard</title>
    <link rel="stylesheet" href="/static/css/main.css">
</head>
<body>
    <div id="app"></div>
    <script src="/static/js/app.js"></script>
</body>
</html>'''
    
    # ========== Dashboard API实现 ==========
    def _get_dashboard_summary(self) -> dict:
        """获取Dashboard概览"""
        try:
            # 财务数据
            positions = self.db.get_open_positions() if self.db else []
            trades = self.db.get_today_trades() if self.db else []
            
            today_pnl = sum(t.pnl for t in trades) if trades else 0
            unrealized = sum(p.unrealized_pnl for p in positions) if positions else 0
            
            # 运行状态
            from monitoring.system_monitor import get_system_stats
            sys_stats = get_system_stats()
            
            # 扫描信息
            scan_info = self._get_scan_info_simple()
            
            return {
                "finance": {
                    "today_pnl": round(today_pnl, 2),
                    "total_pnl": 0,  # 需要从历史计算
                    "unrealized_pnl": round(unrealized, 2),
                    "win_rate": 0.65,  # 需要从历史计算
                    "profit_factor": 1.45,
                    "sharpe": 1.23
                },
                "operation": {
                    "uptime": sys_stats.get("uptime", "-"),
                    "last_scan": scan_info.get("last_scan_ago", "-"),
                    "next_scan": scan_info.get("next_scan_in", "-"),
                    "active_pairs": len(positions),
                    "today_trades": len(trades)
                },
                "system": {
                    "cpu_percent": sys_stats.get("cpu_percent", 0),
                    "memory_mb": round(sys_stats.get("memory_mb", 0), 1),
                    "disk_percent": sys_stats.get("disk_percent", 0),
                    "status": sys_stats.get("status", "unknown")
                },
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Dashboard summary error: {e}")
            return {"error": str(e)}
    
    def _get_dashboard_alerts(self, limit: int) -> dict:
        """获取告警摘要"""
        try:
            from monitoring.system_monitor import get_system_monitor
            monitor = get_system_monitor()
            # 这里需要从日志或其他告警源获取
            return {
                "count": 0,
                "unread": 0,
                "alerts": []
            }
        except Exception as e:
            return {"error": str(e), "count": 0, "alerts": []}
    
    def _get_dashboard_metrics(self, days: int) -> dict:
        """获取关键指标"""
        try:
            stats = self.db.get_trade_stats(days) if self.db else {}
            return {
                "total_trades": stats.get("total_trades", 0),
                "win_rate": stats.get("win_rate", 0),
                "profit_factor": stats.get("profit_factor", 0),
                "sharpe": stats.get("sharpe", 0),
                "max_drawdown": stats.get("max_drawdown", 0),
                "period_days": days
            }
        except Exception as e:
            return {"error": str(e)}
    
    # ========== Market API实现 ==========
    def _get_market_pairs(self, pool: str) -> list:
        """获取配对列表"""
        try:
            pairs = self.db.get_active_pairs(pool) if self.db else []
            return [{
                "symbol_a": p.symbol_a,
                "symbol_b": p.symbol_b,
                "score": round(p.score, 2),
                "pf": round(p.pf, 2),
                "z_entry": p.z_entry,
                "z_exit": p.z_exit,
                "trades_count": p.trades_count
            } for p in pairs]
        except Exception as e:
            return []
    
    def _get_market_pair_detail(self, symbol_a: str, symbol_b: str) -> dict:
        """获取配对详情"""
        return {"symbol_a": symbol_a, "symbol_b": symbol_b, "detail": "TODO"}
    
    def _get_scan_history(self, limit: int) -> list:
        """获取扫描历史"""
        try:
            conn = self.db._get_connection() if self.db else None
            if not conn:
                return []
            cursor = conn.cursor()
            cursor.execute("""
                SELECT scan_time, pool, candidates_count, layer1_passed, 
                       layer2_passed, layer3_passed, duration_ms
                FROM scan_history 
                ORDER BY scan_time DESC 
                LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()
            return [{
                "scan_time": r[0],
                "pool": r[1],
                "candidates": r[2],
                "l1_passed": r[3],
                "l2_passed": r[4],
                "l3_passed": r[5],
                "duration_ms": r[6]
            } for r in rows]
        except Exception as e:
            return []
    
    def _get_scan_funnel(self) -> dict:
        """获取扫描漏斗"""
        try:
            conn = self.db._get_connection() if self.db else None
            if not conn:
                return {}
            cursor = conn.cursor()
            cursor.execute("""
                SELECT AVG(candidates_count), AVG(layer1_passed), 
                       AVG(layer2_passed), AVG(layer3_passed)
                FROM scan_history 
                WHERE scan_time > datetime('now', '-1 day')
            """)
            row = cursor.fetchone()
            if row and row[0]:
                return {
                    "m1_avg": int(row[0]),
                    "m2_avg": int(row[1]),
                    "m3_avg": int(row[2]),
                    "final_avg": int(row[3]),
                    "m1_rate": 100,
                    "m2_rate": round(row[1]/row[0]*100, 1) if row[0] else 0,
                    "m3_rate": round(row[2]/row[0]*100, 1) if row[0] else 0,
                    "final_rate": round(row[3]/row[0]*100, 1) if row[0] else 0
                }
            return {}
        except Exception as e:
            return {}
    
    # ========== Signals API实现 ==========
    def _get_active_signals(self) -> list:
        """获取活跃信号"""
        # TODO: 从引擎获取当前信号
        return []
    
    def _get_signals_history(self, limit: int) -> list:
        """获取信号历史"""
        return []
    
    def _get_signals_stats(self, days: int) -> dict:
        """获取信号统计"""
        return {}
    
    # ========== Positions API实现 ==========
    def _get_open_positions(self) -> list:
        """获取当前持仓"""
        try:
            positions = self.db.get_open_positions() if self.db else []
            return [{
                "pair_key": p.pair_key,
                "pool": p.pool,
                "direction": p.direction,
                "symbol_a": p.symbol_a,
                "symbol_b": p.symbol_b,
                "qty_a": p.qty_a,
                "qty_b": p.qty_b,
                "entry_price_a": p.entry_price_a,
                "entry_price_b": p.entry_price_b,
                "entry_z": p.entry_z,
                "current_z": p.current_z,
                "unrealized_pnl": round(p.unrealized_pnl, 2) if p.unrealized_pnl else 0,
                "entry_time": p.entry_time,
                "status": p.status
            } for p in positions]
        except Exception as e:
            return []
    
    def _get_positions_history(self, days: int) -> list:
        """获取历史持仓"""
        try:
            trades = self.db.get_recent_trades(days) if self.db else []
            return [{
                "pair_key": t.pair_key,
                "direction": t.direction,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "pnl": round(t.pnl, 2),
                "pnl_pct": round(t.pnl_pct, 2),
                "exit_reason": t.exit_reason
            } for t in trades]
        except Exception as e:
            return []
    
    # ========== Risk API实现 ==========
    def _get_risk_overview(self) -> dict:
        """获取风控概览"""
        try:
            positions = self.db.get_open_positions() if self.db else []
            trades = self.db.get_today_trades() if self.db else []
            today_loss = sum(t.pnl for t in trades if t.pnl < 0)
            
            return {
                "risk_level": "low",  # low/medium/high
                "position_count": len(positions),
                "max_positions": 5,  # 从配置读取
                "today_loss": round(today_loss, 2),
                "daily_loss_limit": 50,
                "circuit_breakers_active": 0,
                "circuit_breakers_total": 3
            }
        except Exception as e:
            return {}
    
    def _get_risk_limits(self) -> dict:
        """获取风控限额"""
        return {
            "position": {
                "current": 0,
                "max": 5
            },
            "leverage": {
                "current": 3,
                "max": 5
            },
            "daily_loss": {
                "current": 0,
                "max": 50
            },
            "total_loss": {
                "current": 0,
                "max": 200
            }
        }
    
    def _get_circuit_breakers(self) -> list:
        """获取熔断器状态"""
        return [
            {"name": "price_anomaly", "status": "normal", "triggered_at": None},
            {"name": "sync_failure", "status": "normal", "triggered_at": None},
            {"name": "api_error", "status": "normal", "triggered_at": None}
        ]
    
    def _get_risk_logs(self, limit: int) -> list:
        """获取风控日志"""
        return []
    
    # ========== System API实现 ==========
    def _get_system_resources(self) -> dict:
        """获取系统资源"""
        try:
            from monitoring.system_monitor import get_system_stats
            return get_system_stats()
        except Exception as e:
            return {"error": str(e)}
    
    def _get_data_quality(self) -> dict:
        """获取数据质量"""
        try:
            # 这里需要查询数据新鲜度
            return {
                "freshness_minutes": 5,
                "missing_symbols": [],
                "data_gaps": 0,
                "quality_score": 95
            }
        except Exception as e:
            return {}
    
    def _get_api_stats(self) -> dict:
        """获取API统计"""
        return {
            "calls_per_hour": 120,
            "p50_ms": 45,
            "p95_ms": 120,
            "p99_ms": 200,
            "error_rate": 0
        }
    
    def _get_system_logs(self, level: str, limit: int, search: Optional[str]) -> list:
        """获取系统日志"""
        try:
            log_file = "logs/strategy.log"
            if not os.path.exists(log_file):
                return []
            
            logs = []
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                
            for line in reversed(lines[-limit*2:]):  # 多读一些用于筛选
                if level in line:
                    if search is None or search in line:
                        logs.append(line.strip())
                        if len(logs) >= limit:
                            break
            
            return logs
        except Exception as e:
            return []
    
    def _get_system_config(self) -> dict:
        """获取系统配置"""
        try:
            if self.config:
                return {
                    "trading": {
                        "interval": self.config.trading.get("loop_interval", 30),
                        "max_positions": self.config.trading.get("max_positions", 5)
                    },
                    "scan": {
                        "interval": 3600,
                        "lookback_days": 30
                    },
                    "risk": {
                        "daily_loss_limit": 50,
                        "max_leverage": 5
                    }
                }
            return {}
        except Exception as e:
            return {}
    
    def _get_scan_info_simple(self) -> dict:
        """获取简化扫描信息"""
        try:
            conn = self.db._get_connection() if self.db else None
            if not conn:
                return {}
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(scan_time) FROM scan_history")
            row = cursor.fetchone()
            
            if row and row[0]:
                from datetime import datetime, timezone
                last_scan = row[0]
                last_scan_dt = datetime.fromisoformat(last_scan.replace(' ', 'T'))
                last_scan_dt = last_scan_dt.replace(tzinfo=timezone.utc).astimezone()
                now = datetime.now().astimezone()
                elapsed = (now - last_scan_dt).total_seconds()
                
                return {
                    "last_scan": last_scan,
                    "last_scan_ago": self._format_ago(elapsed),
                    "next_scan_in": self._format_duration(max(0, 3600 - elapsed)),
                    "status": "normal" if elapsed < 5400 else "overdue"
                }
            return {}
        except Exception as e:
            return {}
    
    @staticmethod
    def _format_ago(seconds: float) -> str:
        """格式化'多久之前'"""
        if seconds < 60:
            return f"{int(seconds)}秒前"
        elif seconds < 3600:
            return f"{int(seconds/60)}分钟前"
        elif seconds < 86400:
            return f"{int(seconds/3600)}小时前"
        else:
            return f"{int(seconds/86400)}天前"
    
    @staticmethod
    def _format_duration(seconds: float) -> str:
        """格式化持续时间"""
        if seconds < 60:
            return f"{int(seconds)}秒"
        elif seconds < 3600:
            return f"{int(seconds/60)}分钟"
        else:
            return f"{int(seconds/3600)}小时"


# 便捷函数
def create_web_server(strategy=None, db=None, config=None) -> FastAPI:
    """创建Web服务器"""
    server = WebServerV2(strategy, db, config)
    return server.create_app()


if __name__ == "__main__":
    import uvicorn
    app = create_web_server()
    uvicorn.run(app, host="0.0.0.0", port=8000)
