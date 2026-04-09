/**
 * S001-Pro V3 Dashboard V2 - SPA主应用
 */

// ========== 配置 ==========
const CONFIG = {
    API_BASE: '/api/v2',
    REFRESH_INTERVAL: 5000,  // 5秒刷新
    DEFAULT_PAGE: 'dashboard'
};

// ========== 路由定义 ==========
const ROUTES = {
    'dashboard': { title: '总览', icon: '📊', component: DashboardPage },
    'market': { title: '配对池', icon: '🔍', component: MarketPage },
    'signals': { title: '信号', icon: '📡', component: SignalsPage },
    'positions': { title: '持仓', icon: '📈', component: PositionsPage },
    'risk': { title: '风控', icon: '🛡️', component: RiskPage },
    'system': { title: '系统', icon: '⚙️', component: SystemPage }
};

// ========== 工具函数 ==========
const Utils = {
    // API请求封装
    async fetch(url, options = {}) {
        try {
            const response = await fetch(url, options);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return await response.json();
        } catch (error) {
            console.error('API Error:', error);
            return { error: error.message };
        }
    },
    
    // 格式化数字
    formatNumber(num, decimals = 2) {
        if (num === null || num === undefined) return '-';
        return num.toFixed(decimals);
    },
    
    // 格式化金额
    formatCurrency(num) {
        if (num === null || num === undefined) return '-';
        const formatted = Math.abs(num).toFixed(2);
        const sign = num >= 0 ? '+' : '-';
        return `${sign}$${formatted}`;
    },
    
    // 格式化百分比
    formatPercent(num) {
        if (num === null || num === undefined) return '-';
        return `${(num * 100).toFixed(1)}%`;
    },
    
    // 格式化时间
    formatTime(isoString) {
        if (!isoString) return '-';
        const date = new Date(isoString);
        return date.toLocaleTimeString('zh-CN');
    },
    
    // 获取CSS类名
    getValueClass(value, threshold = 0) {
        if (value > threshold) return 'positive';
        if (value < threshold) return 'negative';
        return '';
    },
    
    // 防抖
    debounce(fn, delay) {
        let timeout;
        return (...args) => {
            clearTimeout(timeout);
            timeout = setTimeout(() => fn(...args), delay);
        };
    }
};

// ========== API服务 ==========
const API = {
    // Dashboard
    async getDashboardSummary() {
        return Utils.fetch(`${CONFIG.API_BASE}/dashboard/summary`);
    },
    
    async getDashboardAlerts(limit = 5) {
        return Utils.fetch(`${CONFIG.API_BASE}/dashboard/alerts?limit=${limit}`);
    },
    
    async getDashboardMetrics(days = 7) {
        return Utils.fetch(`${CONFIG.API_BASE}/dashboard/metrics?days=${days}`);
    },
    
    // Market
    async getMarketPairs(pool = 'primary') {
        return Utils.fetch(`${CONFIG.API_BASE}/market/pairs?pool=${pool}`);
    },
    
    async getMarketScanHistory(limit = 10) {
        return Utils.fetch(`${CONFIG.API_BASE}/market/scan-history?limit=${limit}`);
    },
    
    async getMarketFunnel() {
        return Utils.fetch(`${CONFIG.API_BASE}/market/funnel`);
    },
    
    // Signals
    async getActiveSignals() {
        return Utils.fetch(`${CONFIG.API_BASE}/signals/active`);
    },
    
    async getSignalsHistory(limit = 100) {
        return Utils.fetch(`${CONFIG.API_BASE}/signals/history?limit=${limit}`);
    },
    
    // Positions
    async getOpenPositions() {
        return Utils.fetch(`${CONFIG.API_BASE}/positions/open`);
    },
    
    async getPositionsHistory(days = 7) {
        return Utils.fetch(`${CONFIG.API_BASE}/positions/history?days=${days}`);
    },
    
    // Risk
    async getRiskOverview() {
        return Utils.fetch(`${CONFIG.API_BASE}/risk/overview`);
    },
    
    async getRiskLimits() {
        return Utils.fetch(`${CONFIG.API_BASE}/risk/limits`);
    },
    
    async getCircuitBreakers() {
        return Utils.fetch(`${CONFIG.API_BASE}/risk/circuit-breakers`);
    },
    
    // System
    async getSystemResources() {
        return Utils.fetch(`${CONFIG.API_BASE}/system/resources`);
    },
    
    async getSystemLogs(level = 'INFO', limit = 100) {
        return Utils.fetch(`${CONFIG.API_BASE}/system/logs?level=${level}&limit=${limit}`);
    }
};

// ========== 页面组件 ==========

// Dashboard页面
function DashboardPage() {
    let refreshTimer = null;
    
    async function loadData() {
        const [summary, alerts, metrics] = await Promise.all([
            API.getDashboardSummary(),
            API.getDashboardAlerts(5),
            API.getDashboardMetrics(7)
        ]);
        
        render(summary, alerts, metrics);
    }
    
    function render(summary, alerts, metrics) {
        const finance = summary.finance || {};
        const operation = summary.operation || {};
        const system = summary.system || {};
        
        document.getElementById('app').innerHTML = `
            <div class="main-container">
                ${renderHeader('总览', '策略运行仪表盘')}
                
                <!-- 财务指标卡片 -->
                <div class="grid grid-4" style="margin-bottom: 24px;">
                    ${renderMetricCard('今日盈亏', finance.today_pnl, Utils.formatCurrency, true)}
                    ${renderMetricCard('未实现盈亏', finance.unrealized_pnl, Utils.formatCurrency, true)}
                    ${renderMetricCard('胜率', finance.win_rate, Utils.formatPercent)}
                    ${renderMetricCard('PF', finance.profit_factor, (v) => v?.toFixed(2) || '-')}
                </div>
                
                <!-- 中间区域 -->
                <div class="grid grid-2" style="margin-bottom: 24px;">
                    <!-- 告警列表 -->
                    <div class="card">
                        <div class="card-header">
                            <div class="card-title">
                                <span class="icon">🔔</span>
                                系统告警
                                ${alerts.count ? `<span class="badge badge-danger">${alerts.unread || 0}</span>` : ''}
                            </div>
                            <button class="btn btn-secondary" onclick="Router.navigate('system')">查看全部</button>
                        </div>
                        <div class="card-body">
                            ${renderAlerts(alerts.alerts || [])}
                        </div>
                    </div>
                    
                    <!-- 运行状态 -->
                    <div class="card">
                        <div class="card-header">
                            <div class="card-title">
                                <span class="icon">▶️</span>
                                运行状态
                            </div>
                        </div>
                        <div class="card-body">
                            <div class="info-row">
                                <span class="info-label">运行时间</span>
                                <span class="info-value">${operation.uptime || '-'}</span>
                            </div>
                            <div class="info-row">
                                <span class="info-label">上次扫描</span>
                                <span class="info-value">${operation.last_scan || '-'}</span>
                            </div>
                            <div class="info-row">
                                <span class="info-label">下次扫描</span>
                                <span class="info-value">${operation.next_scan || '-'}</span>
                            </div>
                            <div class="info-row">
                                <span class="info-label">活跃配对</span>
                                <span class="info-value">${operation.active_pairs || 0} 对</span>
                            </div>
                            <div class="info-row">
                                <span class="info-label">今日交易</span>
                                <span class="info-value">${operation.today_trades || 0} 次</span>
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- 系统健康 -->
                <div class="card">
                    <div class="card-header">
                        <div class="card-title">
                            <span class="icon">💻</span>
                            系统健康
                        </div>
                        <span class="badge badge-success">${system.status || 'unknown'}</span>
                    </div>
                    <div class="card-body">
                        <div class="grid grid-3">
                            <div>
                                <div class="info-row">
                                    <span class="info-label">CPU</span>
                                    <span class="info-value">${system.cpu_percent || 0}%</span>
                                </div>
                                <div class="progress-bar" style="margin-top: 8px;">
                                    <div class="progress-fill ${getProgressClass(system.cpu_percent)}" style="width: ${Math.min(system.cpu_percent || 0, 100)}%"></div>
                                </div>
                            </div>
                            <div>
                                <div class="info-row">
                                    <span class="info-label">内存</span>
                                    <span class="info-value">${system.memory_mb || 0} MB</span>
                                </div>
                                <div class="progress-bar" style="margin-top: 8px;">
                                    <div class="progress-fill ${getProgressClass(system.memory_percent)}" style="width: ${Math.min(system.memory_percent || 0, 100)}%"></div>
                                </div>
                            </div>
                            <div>
                                <div class="info-row">
                                    <span class="info-label">磁盘</span>
                                    <span class="info-value">${system.disk_percent || 0}%</span>
                                </div>
                                <div class="progress-bar" style="margin-top: 8px;">
                                    <div class="progress-fill ${getProgressClass(system.disk_percent)}" style="width: ${Math.min(system.disk_percent || 0, 100)}%"></div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }
    
    function renderMetricCard(title, value, formatter, colored = false) {
        const formatted = formatter(value);
        const valueClass = colored ? Utils.getValueClass(value, 0) : '';
        
        return `
            <div class="metric-card">
                <div class="metric-label">${title}</div>
                <div class="metric-value ${valueClass}">${formatted}</div>
                <div class="metric-change ${valueClass}">
                    ${value > 0 ? '↑' : value < 0 ? '↓' : '-'}
                </div>
            </div>
        `;
    }
    
    function renderAlerts(alerts) {
        if (!alerts || alerts.length === 0) {
            return `
                <div class="empty-state">
                    <div class="empty-state-icon">✅</div>
                    <div>暂无告警</div>
                </div>
            `;
        }
        
        return `
            <div class="alert-list">
                ${alerts.slice(0, 5).map(a => `
                    <div class="alert-item alert-item-${a.level || 'warning'}">
                        <span class="alert-icon">${a.level === 'error' ? '🔴' : '🟡'}</span>
                        <div class="alert-content">
                            <div>${a.message}</div>
                            <div class="alert-time">${a.timestamp}</div>
                        </div>
                    </div>
                `).join('')}
            </div>
        `;
    }
    
    function getProgressClass(value) {
        if (value > 80) return 'danger';
        if (value > 60) return 'warning';
        return '';
    }
    
    function renderHeader(title, subtitle) {
        return `
            <div class="page-header">
                <h1 class="page-title">${title}</h1>
                <div class="page-subtitle">${subtitle}</div>
            </div>
        `;
    }
    
    return {
        async mount() {
            await loadData();
            refreshTimer = setInterval(loadData, CONFIG.REFRESH_INTERVAL);
        },
        
        unmount() {
            if (refreshTimer) {
                clearInterval(refreshTimer);
                refreshTimer = null;
            }
        }
    };
}

// Market页面
function MarketPage() {
    async function loadData() {
        const [pairs, history, funnel] = await Promise.all([
            API.getMarketPairs('primary'),
            API.getMarketScanHistory(5),
            API.getMarketFunnel()
        ]);
        
        render(pairs, history, funnel);
    }
    
    function render(pairs, history, funnel) {
        document.getElementById('app').innerHTML = `
            <div class="main-container">
                ${renderHeader('配对池', '活跃配对与扫描历史')}
                
                <div class="tabs">
                    <div class="tab active" onclick="switchTab(this, 'primary')">主池</div>
                    <div class="tab" onclick="switchTab(this, 'secondary')">次池</div>
                    <div class="tab" onclick="switchTab(this, 'history')">扫描历史</div>
                </div>
                
                <div class="grid grid-2">
                    <!-- 配对列表 -->
                    <div class="card" style="grid-column: span 2;">
                        <div class="card-header">
                            <div class="card-title">
                                <span class="icon">🔍</span>
                                主池配对 (${pairs.length}对)
                            </div>
                        </div>
                        <div class="card-body">
                            ${renderPairsTable(pairs)}
                        </div>
                    </div>
                </div>
                
                <!-- 扫描漏斗 -->
                <div class="card" style="margin-top: 20px;">
                    <div class="card-header">
                        <div class="card-title">
                            <span class="icon">📊</span>
                            扫描漏斗 (24小时平均)
                        </div>
                    </div>
                    <div class="card-body">
                        ${renderFunnel(funnel)}
                    </div>
                </div>
            </div>
        `;
    }
    
    function renderPairsTable(pairs) {
        if (!pairs || pairs.length === 0) {
            return `<div class="empty-state">暂无活跃配对</div>`;
        }
        
        return `
            <div class="table-container">
                <table class="table">
                    <thead>
                        <tr>
                            <th>配对</th>
                            <th>评分</th>
                            <th>PF</th>
                            <th>Z入场</th>
                            <th>Z出场</th>
                            <th>交易次数</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${pairs.map(p => `
                            <tr>
                                <td><strong>${p.symbol_a}/${p.symbol_b}</strong></td>
                                <td>${p.score}</td>
                                <td>${p.pf}</td>
                                <td>${p.z_entry}</td>
                                <td>${p.z_exit}</td>
                                <td>${p.trades_count}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        `;
    }
    
    function renderFunnel(funnel) {
        if (!funnel || !funnel.m1_avg) {
            return `<div class="empty-state">暂无扫描数据</div>`;
        }
        
        return `
            <div class="funnel">
                <div class="funnel-stage">
                    <div class="funnel-label">M1筛选</div>
                    <div class="funnel-bar" style="width: 100%; background: var(--accent-blue);">
                        ${funnel.m1_avg} 对
                    </div>
                    <div class="funnel-rate">100%</div>
                </div>
                <div class="funnel-stage">
                    <div class="funnel-label">M2配对</div>
                    <div class="funnel-bar" style="width: ${funnel.m2_rate}%; background: var(--accent-green);">
                        ${funnel.m2_avg} 对
                    </div>
                    <div class="funnel-rate">${funnel.m2_rate}%</div>
                </div>
                <div class="funnel-stage">
                    <div class="funnel-label">M3精选</div>
                    <div class="funnel-bar" style="width: ${funnel.m3_rate}%; background: var(--accent-yellow);">
                        ${funnel.m3_avg} 对
                    </div>
                    <div class="funnel-rate">${funnel.m3_rate}%</div>
                </div>
            </div>
        `;
    }
    
    function renderHeader(title, subtitle) {
        return `
            <div class="page-header">
                <h1 class="page-title">${title}</h1>
                <div class="page-subtitle">${subtitle}</div>
            </div>
        `;
    }
    
    return {
        async mount() {
            await loadData();
        },
        unmount() {}
    };
}

// 其他页面（基础版）
function SignalsPage() {
    return {
        async mount() {
            document.getElementById('app').innerHTML = `
                <div class="main-container">
                    <div class="page-header">
                        <h1 class="page-title">📡 信号监控</h1>
                        <div class="page-subtitle">交易信号实时监控</div>
                    </div>
                    <div class="card">
                        <div class="card-body">
                            <div class="empty-state">
                                <div class="empty-state-icon">📡</div>
                                <div>信号监控功能开发中...</div>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        },
        unmount() {}
    };
}

function PositionsPage() {
    return {
        async mount() {
            const positions = await API.getOpenPositions();
            
            document.getElementById('app').innerHTML = `
                <div class="main-container">
                    <div class="page-header">
                        <h1 class="page-title">📈 持仓管理</h1>
                        <div class="page-subtitle">当前持仓与历史记录</div>
                    </div>
                    <div class="card">
                        <div class="card-header">
                            <div class="card-title">当前持仓 (${positions.length}对)</div>
                        </div>
                        <div class="card-body">
                            ${positions.length === 0 ? `
                                <div class="empty-state">
                                    <div class="empty-state-icon">📭</div>
                                    <div>暂无持仓</div>
                                </div>
                            ` : `
                                <div class="table-container">
                                    <table class="table">
                                        <thead>
                                            <tr>
                                                <th>配对</th>
                                                <th>方向</th>
                                                <th>进场Z</th>
                                                <th>当前Z</th>
                                                <th>盈亏</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            ${positions.map(p => `
                                                <tr>
                                                    <td>${p.pair_key}</td>
                                                    <td><span class="badge badge-${p.direction.includes('long') ? 'success' : 'danger'}">${p.direction}</span></td>
                                                    <td>${p.entry_z}</td>
                                                    <td>${p.current_z || '-'}</td>
                                                    <td class="${p.unrealized_pnl >= 0 ? 'positive' : 'negative'}">${Utils.formatCurrency(p.unrealized_pnl)}</td>
                                                </tr>
                                            `).join('')}
                                        </tbody>
                                    </table>
                                </div>
                            `}
                        </div>
                    </div>
                </div>
            `;
        },
        unmount() {}
    };
}

function RiskPage() {
    return {
        async mount() {
            const overview = await API.getRiskOverview();
            
            document.getElementById('app').innerHTML = `
                <div class="main-container">
                    <div class="page-header">
                        <h1 class="page-title">🛡️ 风控中心</h1>
                        <div class="page-subtitle">风险监控与熔断器状态</div>
                    </div>
                    <div class="grid grid-3">
                        <div class="card">
                            <div class="card-header">
                                <div class="card-title">仓位风控</div>
                            </div>
                            <div class="card-body">
                                <div class="info-row">
                                    <span class="info-label">当前持仓</span>
                                    <span class="info-value">${overview.position_count || 0} / ${overview.max_positions || 5}</span>
                                </div>
                                <div class="progress-bar" style="margin: 10px 0;">
                                    <div class="progress-fill" style="width: ${((overview.position_count || 0) / (overview.max_positions || 5)) * 100}%"></div>
                                </div>
                            </div>
                        </div>
                        <div class="card">
                            <div class="card-header">
                                <div class="card-title">资金风控</div>
                            </div>
                            <div class="card-body">
                                <div class="info-row">
                                    <span class="info-label">当日亏损</span>
                                    <span class="info-value ${(overview.today_loss || 0) < 0 ? 'negative' : ''}">${Utils.formatCurrency(overview.today_loss || 0)}</span>
                                </div>
                                <div class="info-row">
                                    <span class="info-label">日亏限制</span>
                                    <span class="info-value">$${overview.daily_loss_limit || 50}</span>
                                </div>
                            </div>
                        </div>
                        <div class="card">
                            <div class="card-header">
                                <div class="card-title">熔断器状态</div>
                            </div>
                            <div class="card-body">
                                <div class="info-row">
                                    <span class="info-label">活跃熔断</span>
                                    <span class="info-value">${overview.circuit_breakers_active || 0} / ${overview.circuit_breakers_total || 3}</span>
                                </div>
                                <div class="info-row">
                                    <span class="info-label">风险等级</span>
                                    <span class="badge badge-${overview.risk_level === 'low' ? 'success' : overview.risk_level === 'medium' ? 'warning' : 'danger'}">${overview.risk_level || 'unknown'}</span>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        },
        unmount() {}
    };
}

function SystemPage() {
    return {
        async mount() {
            const resources = await API.getSystemResources();
            
            document.getElementById('app').innerHTML = `
                <div class="main-container">
                    <div class="page-header">
                        <h1 class="page-title">⚙️ 系统监控</h1>
                        <div class="page-subtitle">系统资源与日志</div>
                    </div>
                    <div class="card">
                        <div class="card-header">
                            <div class="card-title">系统资源</div>
                        </div>
                        <div class="card-body">
                            <div class="grid grid-4">
                                <div class="metric-card">
                                    <div class="metric-label">运行时间</div>
                                    <div class="metric-value">${resources.uptime || '-'}</div>
                                </div>
                                <div class="metric-card">
                                    <div class="metric-label">CPU</div>
                                    <div class="metric-value">${resources.cpu_percent || 0}%</div>
                                </div>
                                <div class="metric-card">
                                    <div class="metric-label">内存</div>
                                    <div class="metric-value">${Math.round(resources.memory_mb || 0)}M</div>
                                </div>
                                <div class="metric-card">
                                    <div class="metric-label">磁盘</div>
                                    <div class="metric-value">${resources.disk_percent || 0}%</div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        },
        unmount() {}
    };
}

// ========== 路由管理 ==========
const Router = {
    currentPage: null,
    currentComponent: null,
    
    init() {
        // 解析当前路由
        const hash = window.location.hash.slice(1) || CONFIG.DEFAULT_PAGE;
        this.navigate(hash, false);
        
        // 监听hash变化
        window.addEventListener('hashchange', () => {
            const page = window.location.hash.slice(1) || CONFIG.DEFAULT_PAGE;
            this.navigate(page, false);
        });
    },
    
    navigate(page, updateHash = true) {
        // 检查页面是否存在
        if (!ROUTES[page]) {
            page = CONFIG.DEFAULT_PAGE;
        }
        
        // 卸载当前组件
        if (this.currentComponent && this.currentComponent.unmount) {
            this.currentComponent.unmount();
        }
        
        // 更新hash
        if (updateHash) {
            window.location.hash = page;
        }
        
        // 更新导航状态
        this.currentPage = page;
        this.updateNav();
        
        // 挂载新组件
        const Component = ROUTES[page].component;
        this.currentComponent = Component();
        this.currentComponent.mount();
    },
    
    updateNav() {
        document.querySelectorAll('.nav-item').forEach(el => {
            el.classList.remove('active');
            if (el.dataset.page === this.currentPage) {
                el.classList.add('active');
            }
        });
    }
};

// ========== 初始化应用 ==========
function initApp() {
    // 渲染导航栏
    document.body.innerHTML = `
        <nav class="navbar">
            <div class="navbar-content">
                <div class="navbar-brand">
                    📊 S001-Pro V3
                    <span class="version">V2.0</span>
                </div>
                <div class="navbar-nav">
                    ${Object.entries(ROUTES).map(([key, route]) => `
                        <a class="nav-item" data-page="${key}" onclick="Router.navigate('${key}')">
                            ${route.icon} ${route.title}
                        </a>
                    `).join('')}
                </div>
                <div class="navbar-status">
                    <div class="status-indicator">
                        <span class="status-dot"></span>
                        <span>运行中</span>
                    </div>
                </div>
            </div>
        </nav>
        <div id="app">
            <div class="loading">
                <div class="spinner"></div>
                加载中...
            </div>
        </div>
    `;
    
    // 初始化路由
    Router.init();
}

// 全局切换标签函数
window.switchTab = function(el, tab) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    el.classList.add('active');
};

// 启动应用
document.addEventListener('DOMContentLoaded', initApp);
