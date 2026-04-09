# 链路监控配置文件
# 定义每个模块的监控点

MONITOR_POINTS = {
    "scanner": {
        "stages": [
            ("scanner_start", "扫描器启动"),
            ("scanner_fetch", "获取K线数据"),
            ("scanner_calc", "计算协整/ADF"),
            ("scanner_filter", "流动性筛选"),
            ("scanner_score", "评分计算"),
            ("scanner_optimize", "参数优化"),
            ("scanner_select", "精选配对"),
            ("scanner_done", "扫描完成"),
        ],
        "metrics": ["duration", "pairs_found", "pairs_selected"]
    },
    "engine": {
        "stages": [
            ("engine_start", "引擎启动"),
            ("engine_price", "价格更新"),
            ("engine_spread", "价差计算"),
            ("engine_kalman", "Kalman滤波"),
            ("engine_zscore", "Z-Score计算"),
            ("engine_signal", "信号生成"),
            ("engine_confirm", "多周期确认"),
            ("engine_decision", "交易决策"),
            ("engine_done", "引擎完成"),
        ],
        "metrics": ["duration", "z_score", "signal_type"]
    },
    "trader": {
        "stages": [
            ("trader_start", "交易启动"),
            ("trader_validate", "风控检查"),
            ("trader_balance", "余额查询"),
            ("trader_hedge", "对冲计算"),
            ("trader_order_a", "下单A"),
            ("trader_order_b", "下单B"),
            ("trader_confirm_a", "确认A"),
            ("trader_confirm_b", "确认B"),
            ("trader_position", "持仓更新"),
            ("trader_done", "交易完成"),
        ],
        "metrics": ["duration", "fill_rate", "slippage"]
    },
    "monitor": {
        "stages": [
            ("monitor_start", "监控启动"),
            ("monitor_account", "账户查询"),
            ("monitor_position", "持仓查询"),
            ("monitor_pnl", "盈亏计算"),
            ("monitor_alert", "告警检查"),
            ("monitor_report", "报告生成"),
            ("monitor_done", "监控完成"),
        ],
        "metrics": ["duration", "equity", "positions_count"]
    }
}

# Telegram 消息模板
TG_TEMPLATES = {
    "start": """
⏳ <b>{module_icon} {module}</b> | <code>{stage_name}</code>
交易对: <code>{pair}</code>
时间: {time}
🆔 <code>{trace_id}</code>
""",
    "success": """
✅ <b>{module_icon} {module}</b> | <code>{stage_name}</code>
交易对: <code>{pair}</code>
耗时: <code>{duration_ms:.1f}ms</code>
{metrics}
🆔 <code>{trace_id}</code>
""",
    "failure": """
❌ <b>{module_icon} {module}</b> | <code>{stage_name}</code>
交易对: <code>{pair}</code>
耗时: <code>{duration_ms:.1f}ms</code>

<b>错误:</b>
<pre>{error}</pre>

🆔 <code>{trace_id}</code>
""",
    "summary": """
📊 <b>链路监控汇总</b> ({time})

{modules_stats}

异常: {error_count} | 警告: {warning_count}
"""
}

# 模块图标
MODULE_ICONS = {
    "scanner": "🔍",
    "engine": "⚙️", 
    "trader": "💰",
    "monitor": "📊"
}

# 阶段中文名
STAGE_NAMES = {}
for module, config in MONITOR_POINTS.items():
    for stage_key, stage_name in config["stages"]:
        STAGE_NAMES[stage_key] = stage_name
