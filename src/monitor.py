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
        elif event == 'scan_completed':
            pairs = data.get('pairs', [])
            funnel = data.get('funnel', {})
            duration = data.get('duration_s', 0)
            
            # 漏斗摘要
            msg = f"""📊 <b>扫描完成</b> ({duration:.0f}s)
            
<b>漏斗:</b>
  候选 {funnel.get('candidates', '?')} → L1 {funnel.get('l1', '?')} → L2 {funnel.get('l2', '?')} → L3 {funnel.get('l3', '?')} → 回测 {funnel.get('backtest', '?')} → <b>最终 {len(pairs)}</b>
"""
            # Top配对详情
            if pairs:
                msg += "\n<b>入池配对:</b>\n"
                for i, p in enumerate(pairs[:15], 1):
                    sym = f"{p.get('symbol_a','')}-{p.get('symbol_b','')}"
                    score = p.get('score', 0)
                    pf = p.get('pf', 0)
                    ze = p.get('z_entry', 0)
                    zx = p.get('z_exit', 0)
                    trades = p.get('trades_count', 0)
                    msg += f"  {i}. {sym} | S={score:.2f} PF={pf:.1f} E={ze:.1f} X={zx:.1f} N={trades}\n"
            else:
                msg += "\n⚠️ 无配对通过筛选"
            
            msg += f"\n⏰ {datetime.now().strftime('%H:%M:%S')}"
            return msg
        
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
        
        # ====== P0 新增 API ======
        @app.get("/api/system")
        async def get_system():
            """获取系统资源信息"""
            from monitoring.system_monitor import get_system_stats
            return get_system_stats()
        
        @app.get("/api/scan_info")
        async def get_scan_info():
            """获取扫描信息"""
            return self._get_scan_info()
        
        @app.get("/api/alerts")
        async def get_alerts(limit: int = 10):
            """获取最近告警"""
            return self._get_recent_alerts(limit)
        
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
    
    def _get_scan_info(self) -> Dict:
        """获取扫描信息"""
        try:
            # 从数据库获取最后扫描时间
            conn = self.db._get_connection()
            cursor = conn.cursor()
            
            # 获取最后扫描记录 (含漏斗各层数据)
            cursor.execute("""
                SELECT scan_time, pool, layer3_passed, duration_ms,
                       candidates_count, layer1_passed, layer2_passed, top_n
                FROM scan_history 
                ORDER BY scan_time DESC 
                LIMIT 1
            """)
            row = cursor.fetchone()
            
            if row:
                last_scan = row[0]
                # 处理可能的不同时间格式 - 统一使用UTC
                if isinstance(last_scan, str):
                    # 数据库时间是UTC，需要加上时区信息
                    last_scan_dt = datetime.fromisoformat(last_scan.replace(' ', 'T'))
                    # 转换为本地时间进行比较
                    from datetime import timezone
                    last_scan_dt = last_scan_dt.replace(tzinfo=timezone.utc).astimezone()
                else:
                    last_scan_dt = datetime.fromtimestamp(last_scan)
                now = datetime.now().astimezone()  # 带时区的当前时间
                elapsed = (now - last_scan_dt).total_seconds()
                
                # 计算下次扫描时间
                scan_interval = 3600  # 1小时
                next_scan_in = max(0, scan_interval - elapsed)
                
                return {
                    'last_scan': last_scan,
                    'last_scan_ago': self._format_ago(elapsed),
                    'next_scan_in': self._format_duration(next_scan_in),
                    'scan_interval_minutes': scan_interval / 60,
                    'last_pairs_count': row[7] if len(row) > 7 else row[2],
                    'last_duration_ms': row[3],
                    'candidates_count': row[4] if len(row) > 4 else None,
                    'layer1_passed': row[5] if len(row) > 5 else None,
                    'layer2_passed': row[6] if len(row) > 6 else None,
                    'layer3_passed': row[2],
                    'status': 'normal' if elapsed < scan_interval * 1.5 else 'overdue'
                }
            else:
                return {
                    'last_scan': None,
                    'status': 'never',
                    'message': '暂无扫描记录'
                }
                
        except Exception as e:
            logger.error(f"获取扫描信息失败: {e}")
            return {
                'status': 'error',
                'error': str(e)
            }
    
    def _get_recent_alerts(self, limit: int = 10) -> Dict:
        """获取最近告警"""
        try:
            # 从日志文件读取最近的警告/错误
            import os
            log_file = 'logs/strategy.log'
            
            alerts = []
            if os.path.exists(log_file):
                with open(log_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                    
                # 从后往前找 WARNING/ERROR/CRITICAL
                for line in reversed(lines[-500:]):  # 最近500行
                    if 'WARNING' in line or 'ERROR' in line or 'CRITICAL' in line:
                        # 解析日志
                        parts = line.split('|')
                        if len(parts) >= 4:
                            timestamp = parts[0].strip()
                            level = parts[1].strip()
                            message = '|'.join(parts[3:]).strip()
                            
                            alerts.append({
                                'timestamp': timestamp,
                                'level': level.lower(),
                                'message': message[:200]  # 截断
                            })
                            
                            if len(alerts) >= limit:
                                break
            
            return {
                'count': len(alerts),
                'unread': len([a for a in alerts if a['level'] in ['error', 'critical']]),
                'alerts': alerts
            }
            
        except Exception as e:
            logger.error(f"获取告警失败: {e}")
            return {
                'count': 0,
                'error': str(e),
                'alerts': []
            }
    
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
    
    def _render_dashboard(self) -> str:
        """渲染主面板 — 专业暗色主题，入池配对完整详情，5秒自动刷新"""
        return '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>S001-Pro V3 | Statistical Arbitrage</title>
<style>
:root{--bg:#0b0f19;--card:#111827;--border:#1e293b;--text:#e2e8f0;--dim:#64748b;--green:#10b981;--red:#ef4444;--amber:#f59e0b;--cyan:#06b6d4;--purple:#8b5cf6;--blue:#3b82f6}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Inter','SF Pro',system-ui,sans-serif;background:var(--bg);color:var(--text);font-size:13px;line-height:1.5}
.wrap{max-width:1400px;margin:0 auto;padding:16px}
/* Header */
.hdr{display:flex;justify-content:space-between;align-items:center;padding:16px 0;border-bottom:1px solid var(--border);margin-bottom:16px}
.hdr h1{font-size:18px;font-weight:700;background:linear-gradient(135deg,var(--green),var(--cyan));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hdr .meta{text-align:right;font-size:11px;color:var(--dim)}
.hdr .meta .live{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--green);margin-right:4px;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
/* Summary Cards */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:16px}
.stat{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;text-align:center}
.stat .val{font-size:22px;font-weight:700;margin:4px 0}
.stat .lbl{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:1px}
/* Cards */
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px;margin-bottom:12px}
.card .title{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid var(--border)}
.card .title h2{font-size:14px;font-weight:600}
.card .title .badge{font-size:11px;background:rgba(16,185,129,.15);color:var(--green);padding:2px 8px;border-radius:12px}
/* Table */
table{width:100%;border-collapse:collapse;font-size:12px}
thead th{text-align:left;padding:8px 6px;color:var(--dim);font-weight:500;font-size:10px;text-transform:uppercase;letter-spacing:.5px;border-bottom:2px solid var(--border);position:sticky;top:0;background:var(--card)}
tbody td{padding:7px 6px;border-bottom:1px solid rgba(30,41,59,.5)}
tbody tr:hover{background:rgba(30,41,59,.4)}
tbody tr:nth-child(even){background:rgba(15,23,42,.3)}
/* Colors */
.g{color:var(--green)}.r{color:var(--red)}.y{color:var(--amber)}.c{color:var(--cyan)}.p{color:var(--purple)}.b{color:var(--blue)}.d{color:var(--dim)}
.tag{display:inline-block;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:600}
.tag-long{background:rgba(16,185,129,.15);color:var(--green)}
.tag-short{background:rgba(239,68,68,.15);color:var(--red)}
/* Funnel */
.funnel{display:flex;align-items:center;gap:4px;font-size:12px;flex-wrap:wrap}
.funnel .stage{background:var(--border);padding:4px 10px;border-radius:6px;text-align:center;min-width:60px}
.funnel .stage .num{font-size:16px;font-weight:700;color:#fff}
.funnel .stage .lbl{font-size:9px;color:var(--dim)}
.funnel .arrow{color:var(--dim);font-size:16px}
/* Empty */
.empty{text-align:center;padding:24px;color:var(--dim);font-size:12px}
/* Tooltip */
th[title]{cursor:help;border-bottom:1px dotted var(--dim)}
/* Responsive */
.scroll{overflow-x:auto}
@media(max-width:768px){.stats{grid-template-columns:repeat(3,1fr)}}
</style>
</head>
<body>
<div class="wrap">
  <!-- Header -->
  <div class="hdr">
    <h1>S001-Pro V3 Statistical Arbitrage</h1>
    <div class="meta"><span class="live"></span>LIVE<br><span id="clock">-</span></div>
  </div>

  <!-- Summary Stats -->
  <div class="stats" id="stats-bar">
    <div class="stat"><div class="lbl">持仓数</div><div class="val" id="s-pos">-</div></div>
    <div class="stat"><div class="lbl">入池配对</div><div class="val c" id="s-pairs">-</div></div>
    <div class="stat"><div class="lbl">今日盈亏</div><div class="val" id="s-pnl">-</div></div>
    <div class="stat"><div class="lbl">未实现</div><div class="val" id="s-unreal">-</div></div>
    <div class="stat"><div class="lbl">上次扫描</div><div class="val d" id="s-scan" style="font-size:14px">-</div></div>
    <div class="stat"><div class="lbl">下次扫描</div><div class="val d" id="s-next" style="font-size:14px">-</div></div>
  </div>

  <!-- Funnel -->
  <div class="card">
    <div class="title"><h2>🔬 扫描漏斗</h2><span class="badge" id="scan-dur">-</span></div>
    <div class="funnel" id="funnel"><div class="empty">等待首次扫描...</div></div>
  </div>

  <!-- Pairs Table -->
  <div class="card">
    <div class="title"><h2>🎯 入池配对</h2><span class="badge" id="pair-badge">0对</span></div>
    <div class="scroll" id="pairs-wrap"><div class="empty">等待扫描结果...</div></div>
  </div>

  <!-- Positions -->
  <div class="card">
    <div class="title"><h2>📊 当前持仓</h2><span class="badge" id="pos-badge">0</span></div>
    <div class="scroll" id="pos-wrap"><div class="empty">暂无持仓 — 等待信号触发</div></div>
  </div>

  <!-- Trades -->
  <div class="card">
    <div class="title"><h2>📋 交易记录</h2></div>
    <div class="scroll" id="trades-wrap"><div class="empty">暂无交易</div></div>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
const clr = v => v >= 0 ? 'g' : 'r';
const sign = v => (v >= 0 ? '+' : '') + v.toFixed(2);

async function fetchData() {
  try {
    $('clock').textContent = new Date().toLocaleString('zh-CN', {hour12:false});

    // === Status ===
    const st = await (await fetch('/api/status')).json();
    $('s-pos').textContent = st.open_positions;
    $('s-pnl').textContent = sign(st.today_pnl);
    $('s-pnl').className = 'val ' + clr(st.today_pnl);
    $('s-unreal').textContent = sign(st.unrealized_pnl);
    $('s-unreal').className = 'val ' + clr(st.unrealized_pnl);

    // === Scan Info ===
    const sc = await (await fetch('/api/scan_info')).json();
    if (sc.last_scan) {
      $('s-scan').textContent = sc.last_scan_ago || '-';
      $('s-next').textContent = sc.next_scan_in || '-';
      $('scan-dur').textContent = sc.last_duration_ms ? (sc.last_duration_ms/1000).toFixed(0) + 's' : '-';
      // Funnel from scan_history
      if (sc.last_pairs_count !== undefined) {
        $('funnel').innerHTML =
          mkStage(sc.candidates_count||'?','候选') + arr() +
          mkStage(sc.layer1_passed||'?','L1统计') + arr() +
          mkStage(sc.layer2_passed||'?','L2稳定') + arr() +
          mkStage(sc.layer3_passed||'?','L3机会') + arr() +
          mkStage(sc.last_pairs_count,'✅入池');
      }
    }

    // === Pairs ===
    const pairs = await (await fetch('/api/pairs?pool=primary')).json();
    $('s-pairs').textContent = pairs.length;
    $('pair-badge').textContent = pairs.length + '对';
    if (pairs.length > 0) {
      let h = '<table><thead><tr>' +
        '<th>#</th>' +
        '<th>配对</th>' +
        '<th title="综合评分 0~1，越高越优质">Score</th>' +
        '<th title="盈亏比 Profit Factor，总盈利÷总亏损，≥1.3入池">PF</th>' +
        '<th title="夏普比率，收益÷风险，越高越稳">Sharpe</th>' +
        '<th title="回测总收益率">Return</th>' +
        '<th title="最大回撤">DD</th>' +
        '<th title="Z-Score入场阈值">Z入场</th>' +
        '<th title="Z-Score出场止盈">Z出场</th>' +
        '<th title="Z-Score止损">Z止损</th>' +
        '<th title="回测交易次数">N</th>' +
        '<th title="滚动相关系数中位数，越高两币越像">Corr</th>' +
        '<th title="半衰期(K线根数)，越小回归越快">HL</th>' +
        '<th title="赫斯特指数，<0.5=均值回归(好)">Hurst</th>' +
        '</tr></thead><tbody>';
      pairs.forEach((p, i) => {
        const pfCls = p.pf >= 2.0 ? 'g' : p.pf >= 1.5 ? 'c' : p.pf >= 1.3 ? 'y' : 'r';
        const hurstCls = (p.hurst||0) < 0.45 ? 'g' : (p.hurst||0) < 0.55 ? 'y' : 'r';
        h += '<tr>' +
          '<td class="d">' + (i+1) + '</td>' +
          '<td><b>' + p.symbol_a.replace('/USDT','') + '</b><span class="d"> / ' + p.symbol_b.replace('/USDT','') + '</span></td>' +
          '<td><b>' + p.score.toFixed(3) + '</b></td>' +
          '<td class="' + pfCls + '"><b>' + p.pf.toFixed(2) + '</b></td>' +
          '<td>' + (p.sharpe||0).toFixed(2) + '</td>' +
          '<td class="' + clr(p.total_return||0) + '">' + (p.total_return||0).toFixed(4) + '</td>' +
          '<td class="r">' + (p.max_dd||0).toFixed(4) + '</td>' +
          '<td class="b">' + p.z_entry.toFixed(1) + '</td>' +
          '<td class="g">' + p.z_exit.toFixed(1) + '</td>' +
          '<td class="r">' + (p.z_stop||0).toFixed(1) + '</td>' +
          '<td>' + p.trades_count + '</td>' +
          '<td>' + (p.corr_median||0).toFixed(3) + '</td>' +
          '<td>' + (p.half_life||0).toFixed(1) + '</td>' +
          '<td class="' + hurstCls + '">' + (p.hurst||0).toFixed(3) + '</td>' +
          '</tr>';
      });
      h += '</tbody></table>';
      $('pairs-wrap').innerHTML = h;
    } else {
      $('pairs-wrap').innerHTML = '<div class="empty">⏳ 等待扫描完成...</div>';
    }

    // === Positions ===
    const pos = await (await fetch('/api/positions')).json();
    $('pos-badge').textContent = pos.length;
    if (pos.length > 0) {
      let h = '<table><thead><tr><th>配对</th><th>方向</th><th>入场Z</th><th>当前Z</th><th>未实现PnL</th><th>入场时间</th></tr></thead><tbody>';
      pos.forEach(p => {
        const dir = p.direction.includes('long') ? '<span class="tag tag-long">LONG</span>' : '<span class="tag tag-short">SHORT</span>';
        h += '<tr><td><b>' + p.pair_key + '</b></td>' +
          '<td>' + dir + '</td>' +
          '<td>' + p.entry_z.toFixed(2) + '</td>' +
          '<td class="' + clr(p.current_z||0) + '"><b>' + (p.current_z||0).toFixed(2) + '</b></td>' +
          '<td class="' + clr(p.unrealized_pnl||0) + '"><b>' + sign(p.unrealized_pnl||0) + '</b></td>' +
          '<td class="d">' + (p.entry_time||'').substring(0,16) + '</td></tr>';
      });
      h += '</tbody></table>';
      $('pos-wrap').innerHTML = h;
    } else {
      $('pos-wrap').innerHTML = '<div class="empty">暂无持仓 — 等待Z-Score触发入场信号</div>';
    }

    // === Trades ===
    const trades = await (await fetch('/api/trades?limit=15')).json();
    if (trades.length > 0) {
      let h = '<table><thead><tr><th>配对</th><th>方向</th><th>PnL</th><th>PnL%</th><th>原因</th><th>入场</th><th>出场</th></tr></thead><tbody>';
      trades.forEach(t => {
        h += '<tr><td><b>' + t.pair_key + '</b></td>' +
          '<td>' + (t.direction||'-') + '</td>' +
          '<td class="' + clr(t.pnl) + '"><b>' + sign(t.pnl) + '</b></td>' +
          '<td class="' + clr(t.pnl_pct) + '">' + t.pnl_pct.toFixed(2) + '%</td>' +
          '<td>' + (t.exit_reason||'-') + '</td>' +
          '<td class="d">' + (t.entry_time||'').substring(5,16) + '</td>' +
          '<td class="d">' + (t.exit_time||'').substring(5,16) + '</td></tr>';
      });
      h += '</tbody></table>';
      $('trades-wrap').innerHTML = h;
    }

  } catch(e) { console.error('Dashboard error:', e); }
}

function mkStage(num, label) {
  return '<div class="stage"><div class="num">' + num + '</div><div class="lbl">' + label + '</div></div>';
}
function arr() { return '<span class="arrow">→</span>'; }

fetchData();
setInterval(fetchData, 5000);
</script>
</body>
</html>'''


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
