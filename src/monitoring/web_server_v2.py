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
        """完整监控面板 — 暗色主题，入池配对全字段，持仓/交易/漏斗，5秒自动刷新"""
        return '''<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>S001-Pro V3 | Statistical Arbitrage</title>
<style>
:root{--bg:#0b0f19;--c:#111827;--bd:#1e293b;--t:#e2e8f0;--dm:#64748b;--g:#10b981;--r:#ef4444;--y:#f59e0b;--cy:#06b6d4;--bl:#3b82f6}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Inter',system-ui,sans-serif;background:var(--bg);color:var(--t);font-size:13px}
.w{max-width:1440px;margin:0 auto;padding:12px}
.hdr{display:flex;justify-content:space-between;align-items:center;padding:14px 0;border-bottom:1px solid var(--bd);margin-bottom:14px}
.hdr h1{font-size:17px;font-weight:700;background:linear-gradient(135deg,var(--g),var(--cy));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hdr .m{text-align:right;font-size:11px;color:var(--dm)}.hdr .m b{color:var(--g)}
.hdr .lv{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--g);margin-right:3px;animation:p 2s infinite}
@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}
.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;margin-bottom:12px}
.st{background:var(--c);border:1px solid var(--bd);border-radius:8px;padding:12px;text-align:center}
.st .v{font-size:20px;font-weight:700;margin:2px 0}.st .l{font-size:9px;color:var(--dm);text-transform:uppercase;letter-spacing:.8px}
.cd{background:var(--c);border:1px solid var(--bd);border-radius:8px;padding:14px;margin-bottom:10px}
.cd .tt{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--bd)}
.cd .tt h2{font-size:13px;font-weight:600}.cd .tt .bg{font-size:10px;background:rgba(16,185,129,.15);color:var(--g);padding:2px 7px;border-radius:10px}
.sc{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:11.5px}
thead th{text-align:left;padding:7px 5px;color:var(--dm);font-weight:500;font-size:9.5px;text-transform:uppercase;letter-spacing:.4px;border-bottom:2px solid var(--bd);position:sticky;top:0;background:var(--c);white-space:nowrap}
tbody td{padding:6px 5px;border-bottom:1px solid rgba(30,41,59,.4);white-space:nowrap}
tbody tr:hover{background:rgba(30,41,59,.5)}
.g{color:var(--g)}.r{color:var(--r)}.y{color:var(--y)}.cy{color:var(--cy)}.bl{color:var(--bl)}.dm{color:var(--dm)}
.tg{display:inline-block;padding:1px 5px;border-radius:3px;font-size:9px;font-weight:600}
.tg-l{background:rgba(16,185,129,.15);color:var(--g)}.tg-s{background:rgba(239,68,68,.15);color:var(--r)}
.fn{display:flex;align-items:center;gap:3px;flex-wrap:wrap}.fn .sg{background:var(--bd);padding:3px 8px;border-radius:5px;text-align:center;min-width:55px}.fn .sg .n{font-size:15px;font-weight:700;color:#fff}.fn .sg .lb{font-size:8px;color:var(--dm)}.fn .ar{color:var(--dm);font-size:14px}
.em{text-align:center;padding:20px;color:var(--dm);font-size:12px}
th[title]{cursor:help}
</style></head><body><div class="w">
<div class="hdr"><h1>S001-Pro V3 Statistical Arbitrage</h1><div class="m"><span class="lv"></span><b>LIVE</b><br><span id="ck">-</span></div></div>
<div class="row" id="sb">
 <div class="st"><div class="l">持仓</div><div class="v" id="v1">-</div></div>
 <div class="st"><div class="l">入池配对</div><div class="v cy" id="v2">-</div></div>
 <div class="st"><div class="l">今日盈亏</div><div class="v" id="v3">-</div></div>
 <div class="st"><div class="l">未实现</div><div class="v" id="v4">-</div></div>
 <div class="st"><div class="l">上次扫描</div><div class="v dm" id="v5" style="font-size:13px">-</div></div>
 <div class="st"><div class="l">下次扫描</div><div class="v dm" id="v6" style="font-size:13px">-</div></div>
</div>
<div class="cd"><div class="tt"><h2>🔬 扫描漏斗</h2><span class="bg" id="sd">-</span></div><div class="fn" id="fl"><div class="em">等待扫描...</div></div></div>
<div class="cd"><div class="tt"><h2>🎯 入池配对</h2><span class="bg" id="pb">0对</span></div><div class="sc" id="pt"><div class="em">等待扫描...</div></div></div>
<div class="cd"><div class="tt"><h2>📊 当前持仓</h2><span class="bg" id="pn">0</span></div><div class="sc" id="ps"><div class="em">暂无持仓</div></div></div>
<div class="cd"><div class="tt"><h2>📋 交易记录</h2></div><div class="sc" id="tr"><div class="em">暂无交易</div></div></div>
</div>
<script>
const $=id=>document.getElementById(id),C=v=>v>=0?'g':'r',S=v=>(v>=0?'+':'')+v.toFixed(2);
function ms(n,l){return '<div class="sg"><div class="n">'+n+'</div><div class="lb">'+l+'</div></div>'}
function ar(){return '<span class="ar">→</span>'}
async function F(){try{
$('ck').textContent=new Date().toLocaleString('zh-CN',{hour12:false});
// status
const s=await(await fetch('/api/v2/dashboard/summary')).json();
$('v1').textContent=s.operation?.active_pairs||0;
$('v3').textContent=S(s.finance?.today_pnl||0);$('v3').className='v '+C(s.finance?.today_pnl||0);
$('v4').textContent=S(s.finance?.unrealized_pnl||0);$('v4').className='v '+C(s.finance?.unrealized_pnl||0);
$('v5').textContent=s.operation?.last_scan||'-';$('v6').textContent=s.operation?.next_scan||'-';
// pairs
const P=await(await fetch('/api/v2/market/pairs?pool=primary')).json();
$('v2').textContent=P.length;$('pb').textContent=P.length+'对';
if(P.length>0){let h='<table><thead><tr>'+
'<th>#</th><th>配对 A / B</th>'+
'<th title="综合评分0~1">Score</th>'+
'<th title="盈亏比=总盈利÷总亏损 ≥1.3入池">PF</th>'+
'<th title="夏普比率=收益÷风险">Sharpe</th>'+
'<th title="回测总收益">Return</th>'+
'<th title="最大回撤">MaxDD</th>'+
'<th title="Z-Score进场阈值">Z入场</th>'+
'<th title="Z-Score止盈阈值">Z止盈</th>'+
'<th title="Z-Score止损阈值">Z止损</th>'+
'<th title="回测交易笔数">笔数</th>'+
'<th title="滚动相关系数中位数">Corr</th>'+
'<th title="协整p值 越小越好">Coint</th>'+
'<th title="半衰期(K线根数) 越小回归越快">HL</th>'+
'<th title="赫斯特指数 <0.5=均值回归">Hurst</th>'+
'<th title="最后更新时间">更新</th>'+
'</tr></thead><tbody>';
P.forEach((p,i)=>{
const pf=Math.min(p.pf,99.99);
const pc=pf>=2?'g':pf>=1.5?'cy':pf>=1.3?'y':'r';
const hc=(p.hurst||0)<0.45?'g':(p.hurst||0)<0.55?'y':'r';
const rc=p.total_return>0?'g':'r';
h+='<tr><td class="dm">'+(i+1)+'</td>'+
'<td><b>'+p.symbol_a.replace('/USDT','')+'</b> <span class="dm">/ '+p.symbol_b.replace('/USDT','')+'</span></td>'+
'<td><b>'+p.score.toFixed(3)+'</b></td>'+
'<td class="'+pc+'"><b>'+pf.toFixed(2)+'</b></td>'+
'<td>'+(p.sharpe?Math.min(p.sharpe,99).toFixed(2):'-')+'</td>'+
'<td class="'+rc+'">'+(p.total_return||0).toFixed(4)+'</td>'+
'<td class="r">'+(p.max_dd||0).toFixed(4)+'</td>'+
'<td class="bl"><b>'+p.z_entry.toFixed(1)+'</b></td>'+
'<td class="g">'+p.z_exit.toFixed(1)+'</td>'+
'<td class="r">'+(p.z_stop||0).toFixed(1)+'</td>'+
'<td>'+p.trades_count+'</td>'+
'<td>'+(p.corr_median||0).toFixed(3)+'</td>'+
'<td>'+(p.coint_p||0).toFixed(4)+'</td>'+
'<td>'+(p.half_life||0).toFixed(1)+'</td>'+
'<td class="'+hc+'">'+(p.hurst||0).toFixed(3)+'</td>'+
'<td class="dm" style="font-size:10px">'+(p.updated_at||'').substring(5,16)+'</td></tr>';
});h+='</tbody></table>';$('pt').innerHTML=h;
}else{$('pt').innerHTML='<div class="em">⏳ 等待扫描完成...</div>';}
// scan funnel
const sc=await(await fetch('/api/v2/market/scan-history?limit=1')).json();
if(sc.length>0){const f=sc[0];
$('sd').textContent=(f.duration_ms/1000).toFixed(0)+'s';
$('fl').innerHTML=ms(f.candidates||'?','候选')+ar()+ms(f.l1_passed||'?','L1统计')+ar()+ms(f.l2_passed||'?','L2稳定')+ar()+ms(f.l3_passed||'?','L3机会')+ar()+ms(f.l3_passed||'?','✅入池');}
// positions
const po=await(await fetch('/api/v2/positions/open')).json();
$('pn').textContent=po.length;
if(po.length>0){let h='<table><thead><tr><th>配对</th><th>池</th><th>方向</th><th>入场Z</th><th>当前Z</th><th>Z止盈</th><th>Z止损</th><th>数量A</th><th>数量B</th><th>名义</th><th>未实现PnL</th><th>入场时间</th></tr></thead><tbody>';
po.forEach(p=>{
const d=p.direction&&p.direction.includes('long')?'<span class="tg tg-l">LONG</span>':'<span class="tg tg-s">SHORT</span>';
h+='<tr><td><b>'+p.pair_key+'</b></td><td>'+p.pool+'</td><td>'+d+'</td>'+
'<td>'+((p.entry_z||0).toFixed(2))+'</td>'+
'<td class="'+C(p.current_z||0)+'"><b>'+((p.current_z||0).toFixed(2))+'</b></td>'+
'<td class="g">'+((p.z_exit||0).toFixed(2))+'</td>'+
'<td class="r">'+((p.z_stop||0).toFixed(2))+'</td>'+
'<td>'+((p.qty_a||0).toFixed(4))+'</td>'+
'<td>'+((p.qty_b||0).toFixed(4))+'</td>'+
'<td>'+(p.notional||'-')+'</td>'+
'<td class="'+C(p.unrealized_pnl||0)+'"><b>'+S(p.unrealized_pnl||0)+'</b></td>'+
'<td class="dm">'+(p.entry_time||'').substring(0,16)+'</td></tr>';});
h+='</tbody></table>';$('ps').innerHTML=h;
}else{$('ps').innerHTML='<div class="em">暂无持仓 — 等待Z-Score触发</div>';}
// trades
const T=await(await fetch('/api/v2/positions/history?days=7')).json();
if(T.length>0){let h='<table><thead><tr><th>配对</th><th>方向</th><th>PnL</th><th>PnL%</th><th>原因</th><th>入场</th><th>出场</th></tr></thead><tbody>';
T.forEach(t=>{h+='<tr><td><b>'+t.pair_key+'</b></td><td>'+(t.direction||'-')+'</td>'+
'<td class="'+C(t.pnl)+'"><b>'+S(t.pnl)+'</b></td>'+
'<td class="'+C(t.pnl_pct)+'">'+(t.pnl_pct||0).toFixed(2)+'%</td>'+
'<td>'+(t.exit_reason||'-')+'</td>'+
'<td class="dm">'+(t.entry_time||'').substring(5,16)+'</td>'+
'<td class="dm">'+(t.exit_time||'').substring(5,16)+'</td></tr>';});
h+='</tbody></table>';$('tr').innerHTML=h;}
}catch(e){console.error(e);}}
F();setInterval(F,5000);
</script></body></html>'''
    
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
        """获取配对列表 — 完整数据"""
        try:
            pairs = self.db.get_active_pairs(pool) if self.db else []
            return [{
                "symbol_a": p.symbol_a,
                "symbol_b": p.symbol_b,
                "score": round(p.score, 3),
                "pf": round(p.pf, 2),
                "sharpe": round(p.sharpe, 2) if p.sharpe else 0,
                "total_return": round(p.total_return, 4) if p.total_return else 0,
                "max_dd": round(p.max_dd, 4) if p.max_dd else 0,
                "z_entry": round(p.z_entry, 2),
                "z_exit": round(p.z_exit, 2),
                "z_stop": round(p.z_stop, 2),
                "trades_count": p.trades_count,
                "corr_median": round(p.corr_median, 3) if p.corr_median else 0,
                "coint_p": round(p.coint_p, 4) if p.coint_p else 0,
                "half_life": round(p.half_life, 1) if p.half_life else 0,
                "hurst": round(p.hurst, 3) if p.hurst else 0,
                "updated_at": p.updated_at,
            } for p in pairs]
        except Exception as e:
            logger.error(f"get_market_pairs error: {e}")
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
                "qty_a": round(p.qty_a, 6) if p.qty_a else 0,
                "qty_b": round(p.qty_b, 6) if p.qty_b else 0,
                "entry_price_a": p.entry_price_a,
                "entry_price_b": p.entry_price_b,
                "notional": round(p.notional, 2) if p.notional else 0,
                "entry_z": round(p.entry_z, 3) if p.entry_z else 0,
                "current_z": round(p.current_z, 3) if p.current_z else 0,
                "z_entry": round(p.z_entry, 2) if p.z_entry else 0,
                "z_exit": round(p.z_exit, 2) if p.z_exit else 0,
                "z_stop": round(p.z_stop, 2) if p.z_stop else 0,
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
