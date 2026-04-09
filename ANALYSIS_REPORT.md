================================================================================
S001-Pro-V3 COMPREHENSIVE SOURCE CODE ANALYSIS REPORT
Produced: 2026-04-09
Scope: ALL 56 Python source files + config.yaml
Focus: Production-readiness blocking issues
================================================================================

TABLE OF CONTENTS
  1. Executive Summary
  2. CRITICAL Blocking Issues (Must Fix Before Live Trading)
  3. File-by-File Analysis
  4. Missing Database Methods
  5. Mock/Simulated Data Inventory
  6. TODO/Placeholder Inventory
  7. Import Dependency Risks
  8. Architecture Concerns
  9. Recommendations

================================================================================
1. EXECUTIVE SUMMARY
================================================================================

Project: Statistical arbitrage trading system for Binance USDT perpetual futures
Stage:   ALPHA - NOT production ready
Files:   56 Python files, 1 YAML config
LOC:     ~15,000+ lines

CRITICAL blockers found:  12
HIGH-severity issues:     18
MEDIUM-severity issues:   22
TODOs/placeholders:       8
Mock/simulated data:      5 locations
Missing DB methods:       3

The system has a well-designed architecture but contains several critical issues
that would cause INCORRECT TRADING, DATA CORRUPTION, or FINANCIAL LOSS if
deployed to production.

================================================================================
2. CRITICAL BLOCKING ISSUES (Must Fix Before Live Trading)
================================================================================

CRITICAL-01: FAKE PAIR DATA IN ENGINE (engine.py:201, 486, 516)
  File: src/engine.py
  Lines: 201, 486, 516
  Issue: SignalGenerator._get_pair_data() returns SIMULATED data:
         "return df['close'].values * 0.5  # 模拟"
         This means ALL z-score calculations use fake B-leg prices.
         The engine never reads the second symbol's actual price data.
         _manage_position() line 486: "prices_b = prices_a * 0.5  # 简化"
         _check_entry() line 516: "prices_b = prices_a * 0.5  # 简化"
  Impact: ALL trading signals are meaningless. Every entry/exit decision
         is based on fabricated data. This is the #1 blocker.
  Fix:   Must read actual B-symbol klines from DataReader and align timestamps.

CRITICAL-02: SCANNER RETURNS FAKE VOLUME/SPREAD DATA (scanner.py:471-479)
  File: src/scanner.py
  Lines: 471-479
  Issue: _get_min_volume() returns hardcoded 10_000_000
         _get_max_bid_ask_spread() returns hardcoded 0.0001
         Both marked "# TODO: 从Data-Core获取" and "# 模拟数据"
  Impact: Layer 3 filtering is effectively disabled. Illiquid pairs
         with wide spreads will pass through, causing execution problems.
  Fix:   Query actual volume and orderbook spread from exchange or Data-Core.

CRITICAL-03: MISSING DATABASE METHODS (order_recovery.py:418,425)
  File: src/order_recovery.py
  Lines: 418, 425
  Issue: GracefulShutdownHandler calls self.db.save_shutdown_snapshot()
         and self.db.get_shutdown_snapshot() but these methods DO NOT EXIST
         in DatabaseManager (src/database.py).
  Impact: Strategy shutdown will CRASH. State will not be saved on restart.
         This means post-restart recovery is broken.
  Fix:   Implement save_shutdown_snapshot() and get_shutdown_snapshot() in
         DatabaseManager. Add a shutdown_snapshots table.

CRITICAL-04: MISSING DATABASE METHOD get_recent_trades()
  File: src/monitoring/web_server_v2.py line 400
  Issue: WebServerV2._get_positions_history() calls self.db.get_recent_trades(days)
         but DatabaseManager has no get_recent_trades() method. Only get_today_trades().
  Impact: Web dashboard history page will crash.
  Fix:   Add get_recent_trades(days) to DatabaseManager.

CRITICAL-05: _cancel_order IS EMPTY (trader.py:402-408)
  File: src/trader.py
  Lines: 402-408
  Issue: NakedPositionProtector._cancel_order() contains only "pass":
         "pass  # 具体实现取决于交易所API"
  Impact: When B-leg order fails and needs cancellation, nothing happens.
         Orders remain live on exchange, potentially creating unhedged risk.
  Fix:   Implement self.api.exchange.cancel_order(order_id, symbol).

CRITICAL-06: NAKED POSITION DB LOGGING NOT IMPLEMENTED (trader.py:430)
  File: src/trader.py
  Line: 430
  Issue: "# TODO: 实现数据库记录" in _handle_naked_position()
  Impact: If a naked position forms, there is NO persistent record.
         After restart, the system has no knowledge of the naked exposure.
  Fix:   Create a naked_positions table and log every incident.

CRITICAL-07: ORDER RECOVERY _get_local_order_ids IS STUB (order_recovery.py:177-181)
  File: src/order_recovery.py
  Lines: 177-181
  Issue: Returns empty set(). "# TODO: 实现具体查询"
  Impact: All exchange orders are classified as UNKNOWN/ORPHAN on restart.
         The recovery system cannot distinguish strategy orders from external.
  Fix:   Store order IDs in trades/positions tables, query them here.

CRITICAL-08: ORDER TRACKING NOT IMPLEMENTED (order_recovery.py:300-307)
  File: src/order_recovery.py
  Lines: 300-307
  Issue: _track_order() only logs, does not actually track.
         "# TODO: 将订单加入本地跟踪系统"
  Impact: Recovered orders are forgotten immediately after recovery.
  Fix:   Add order tracking state management.

CRITICAL-09: reduce_only NOT PASSED TO EXCHANGE (trader.py:131-153)
  File: src/trader.py
  Lines: 131-153
  Issue: place_market_order() accepts reduce_only parameter but NEVER
         passes it to the ccxt call. The create_market_buy_order() and
         create_market_sell_order() calls ignore reduce_only entirely.
  Impact: Rollback/emergency orders could OPEN NEW POSITIONS instead of
         just closing existing ones, doubling exposure.
  Fix:   Pass params={'reduceOnly': True} to ccxt order creation methods.

CRITICAL-10: DATA-CORE API CONFIG UNUSED
  File: config/config.yaml line 12-15, src/config.py
  Issue: config.yaml defines data_core.api_url but nothing in the codebase
         uses it. All data access goes through direct SQLite connections.
         If Data-Core is not running or klines.db path is wrong, scanner
         returns hardcoded fallback symbols (4-8 coins).
  Impact: System may trade with stale or missing data without warning.

CRITICAL-11: POSITION SYNC SQL MISMATCH (position_sync.py:164)
  File: src/position_sync.py
  Lines: 160-166
  Issue: HighFrequencyPositionSync queries "SELECT symbol, qty FROM positions
         WHERE qty != 0" but the positions table schema has columns named
         symbol_a, symbol_b, qty_a, qty_b - NOT symbol and qty.
  Impact: Position sync will fail with SQL errors if used.
  Fix:   Rewrite to work with the actual positions table schema.

CRITICAL-12: WEB SERVER V2 FIELD NAME MISMATCH (web_server_v2.py:378-393)
  File: src/monitoring/web_server_v2.py
  Lines: 378-393
  Issue: _get_open_positions() references p.id, p.quantity_a, p.quantity_b
         but PositionRecord has no 'id' field and uses qty_a, qty_b.
  Impact: Positions API endpoint will crash with AttributeError.

================================================================================
3. FILE-BY-FILE ANALYSIS
================================================================================

--- src/config.py (344 lines) ---
Purpose: Central configuration management with YAML loading and validation.
Issues:
  - Regex-based API key parsing is fragile (lines 156-181)
  - notification.events in YAML is a list but NotificationConfig expects it
    under telegram subsection - potential mismatch
  - validate() only checks 4 conditions, misses many ranges
  - No API key validation (empty strings pass silently if env vars absent)
Imports: yaml, os, dataclasses - all standard, safe

--- src/main.py (388 lines) ---
Purpose: Strategy entry point, initialization, main trading loop.
Issues:
  - Hardcoded log path 'data/strategy.log' (line 31) - directory may not exist
  - tracer.start() called at module level (line 37) before anything is configured
  - Signal handler calls sys.exit(0) which bypasses finally blocks (line 357)
  - --dry-run flag is accepted but never checked/used in any code path
  - --config flag is accepted but never passed to Config.from_yaml()
Imports: All local imports (config, database, scanner, engine, trader, monitor,
         visualization) - will fail if not run from src/ directory

--- src/engine.py (561 lines) ---
Purpose: Data reading, signal generation, position management.
Issues:
  - [CRITICAL-01] _get_pair_data returns fake data (line 201)
  - [CRITICAL-01] _manage_position uses fake B prices (line 486)
  - [CRITICAL-01] _check_entry uses fake B prices (line 516)
  - DataReader.get_klines() uses deprecated pandas resample rules ('5T','15T'
    etc.) - should be '5min','15min' in newer pandas versions
  - get_latest_price() reads 5m data for latest price - could be 5min stale
  - No beta hedging ratio in position sizing (lines 537-539)
  - Position notional always uses min_per_pair (line 537), ignoring leverage
Imports: numpy, pandas, sqlite3 - safe

--- src/scanner.py (697 lines) ---
Purpose: Three-layer pair filtering, scoring, parameter optimization.
Issues:
  - [CRITICAL-02] Fake volume and spread data
  - _adf_statistic() is a simplified version, not proper ADF test (line 370-380)
    Uses linregress p-value which is NOT the ADF critical value
  - Cointegration test uses same simplified ADF (line 345-358)
  - Backtest uses spread[-1] not properly computed returns (line 598)
  - scan_history logging estimates layer counts incorrectly (lines 104-106)
  - No parallelization - O(n^2) pair scanning is slow for 20+ symbols
  - Fallback to 4-8 hardcoded symbols if klines DB unavailable (lines 125-126)
Imports: scipy.stats - may need scipy installed

--- src/trader.py (733 lines) ---
Purpose: Exchange API, order execution, naked position protection, position sync.
Issues:
  - [CRITICAL-05] _cancel_order is empty pass
  - [CRITICAL-06] Naked position DB logging TODO
  - [CRITICAL-09] reduce_only not passed to exchange
  - ExchangeAPI.place_market_order() doesn't handle partial fills
  - No symbol format validation (BTC/USDT vs BTCUSDT)
  - sync_positions() references markPrice/entryPrice with dict access that
    may fail with KeyError (lines 677-678)
  - sync_positions() comments say "需要添加这个方法" for delete_position
    but it DOES exist in DatabaseManager (line 654)
Imports: ccxt - REQUIRED external dependency

--- src/monitor.py (689 lines) ---
Purpose: Telegram notifications, web dashboard, monitoring manager.
Issues:
  - HTML template has duplicate "unrealized" div (lines 462-468)
  - _get_recent_alerts reads from 'logs/strategy.log' but main.py writes
    to 'data/strategy.log' - path mismatch (line 294 vs main.py line 31)
  - WebDashboard._get_scan_info assumes UTC timezone but scanner stores
    local timestamps - timezone bugs likely
Imports: requests (for Telegram) - needs pip install
         fastapi, uvicorn - needs pip install

--- src/database.py (653 lines) ---
Purpose: SQLite database operations for pairs, positions, trades, metrics.
Issues:
  - Missing save_shutdown_snapshot() method [CRITICAL-03]
  - Missing get_shutdown_snapshot() method [CRITICAL-03]
  - Missing get_recent_trades(days) method [CRITICAL-04]
  - get_trade_stats() uses string format for SQL date (line 501-502) -
    potential SQL injection: "WHERE entry_time >= date('now', '-{} days')"
  - _get_connection() creates new connection each call, no pooling
  - _get_klines_connection() uses check_same_thread=False which is unsafe
    for SQLite with concurrent writes
  - klines_db accessed via getattr with default None (line 612) - fragile
Imports: sqlite3, numpy - safe

--- src/visualization.py (527 lines) ---
Purpose: Execution tracing, heartbeat monitoring, auto-diagnosis.
Issues:
  - Diagnosis files written to data/ without ensuring directory exists
  - Heartbeat monitor only logs every 60s, diagnosis check every 10s -
    dead_threshold of 120s means 2+ minutes of hung system before alert
  - No external notification (Telegram) on critical diagnosis events
Imports: All standard library - safe

--- src/order_recovery.py (441 lines) ---
Purpose: Post-restart order recovery and graceful shutdown.
Issues:
  - [CRITICAL-03] save_shutdown_snapshot/get_shutdown_snapshot missing
  - [CRITICAL-07] _get_local_order_ids returns empty set
  - [CRITICAL-08] _track_order is stub
  - [LINE 415] "# TODO: 保存未完成订单ID"
  - No actual order ID storage anywhere in the system
Imports: database, visualization - local only

--- src/position_sync.py (406 lines) ---
Purpose: High-frequency position synchronization between local and exchange.
Issues:
  - [CRITICAL-11] SQL queries reference non-existent columns
  - _update_local_position() writes to a positions table with different
    schema than DatabaseManager creates
  - Bare except on line 189
  - Not integrated into main.py - completely standalone module
Imports: Standard library only

--- src/config_validation.py (336 lines) ---
Purpose: Pydantic-based config validation (alternative to config.py).
Issues:
  - NOT USED by any other module - entirely disconnected from main system
  - Uses different config schema than config.py (e.g., SignalConfig vs
    PoolConfig, different field names)
  - Pydantic v1 syntax (validator, root_validator) - will break on v2
  - regex parameter in Field is deprecated in newer Pydantic
Imports: pydantic - needs pip install, version-sensitive

--- src/exchange/exchange_manager.py (442 lines) ---
Purpose: Multi-exchange failover management.
Issues:
  - NOT USED by main system - standalone module
  - execute_with_failover() has infinite recursion risk (line 173)
  - No rate limiting across failover retries
Imports: ccxt - external dependency

--- src/monitoring/system_monitor.py (111 lines) ---
Purpose: System resource monitoring (CPU, memory, disk).
Issues: Clean, well-written. No critical issues.
Imports: psutil - needs pip install

--- src/monitoring/web_server_v2.py (603 lines) ---
Purpose: V2 web dashboard with SPA architecture.
Issues:
  - [CRITICAL-04] get_recent_trades() missing
  - [CRITICAL-12] PositionRecord field name mismatch
  - Hardcoded values: win_rate=0.65, profit_factor=1.45, sharpe=1.23
    (lines 230-233) - dashboard shows fake performance metrics
  - _get_data_quality() returns hardcoded quality_score=95 (line 483)
  - _get_api_stats() returns all hardcoded values (lines 490-496)
  - _get_circuit_breakers() returns hardcoded normal status (lines 456-460)
  - SPA references /static/css/main.css and /static/js/app.js that likely
    don't exist (created as empty directories)
  - _get_risk_limits() returns all hardcoded values (lines 433-452)
Imports: fastapi - needs pip install

--- src/monitoring/latency_tracker.py (411 lines) ---
Purpose: API/WebSocket/DB latency tracking with percentile stats.
Issues: Well-designed, no critical issues. Not integrated into main flow.
Imports: numpy, threading - safe

--- src/monitoring/data_quality.py (445 lines) ---
Purpose: Real-time data quality monitoring.
Issues: Not integrated into main flow.
Imports: numpy, pandas - safe

--- src/monitoring/memory_monitor.py (352 lines) ---
Purpose: Memory monitoring with auto-GC.
Issues:
  - MemoryProfiler uses objgraph which is likely not installed
  - Not integrated into main flow
Imports: psutil (optional), objgraph (optional, likely missing)

--- src/monitoring/web_dashboard.py (619 lines) ---
Purpose: WebSocket-based real-time dashboard (V1).
Issues:
  - Duplicate functionality with monitor.py WebDashboard and web_server_v2.py
  - update_status() calls asyncio.create_task outside of async context
    (line 472) - will crash in threaded scenarios
Imports: fastapi, websockets, uvicorn - needs pip install

--- src/risk/risk_model.py (527 lines) ---
Purpose: VaR calculation, drawdown analysis, stress testing.
Issues:
  - Not integrated into main trading flow
  - Stress test survival threshold hardcoded to 1000 USDT (line 237)
  - Monte Carlo uses numpy.random without seed control
Imports: numpy, scipy.stats - needs scipy

--- src/data/data_validator.py (445 lines) ---
Purpose: Bad tick filtering, data quality validation.
Issues:
  - _send_alert() imports from src.notifications.telegram_notifier which
    DOES NOT EXIST (line 373) - will crash
  - Not integrated into main data pipeline
Imports: numpy, pandas - safe

--- src/data/websocket_client.py (499 lines) ---
Purpose: Binance WebSocket client for real-time data.
Issues:
  - Not integrated into main flow (system reads from SQLite, not WS)
  - KeyboardInterrupt handling via bare pass (line 481)
Imports: websockets - needs pip install

--- src/data/pair_maintenance.py (294 lines) ---
Purpose: Automatic delisting/illiquidity detection for trading pairs.
Issues: Not integrated into main flow.
Imports: asyncio - standard

--- src/execution/slippage_model.py (408 lines) ---
Purpose: Slippage and fee modeling for backtesting.
Issues:
  - Not integrated into scanner backtest or real execution
  - FEE_TIERS use Binance spot rates, not futures rates
Imports: numpy - safe

--- src/execution/slippage_tuner.py (388 lines) ---
Purpose: Slippage parameter calibration from real trade data.
Issues: Not integrated. Standalone analysis tool.
Imports: numpy, pandas, scipy - needs scipy

--- src/recovery/position_recovery.py (480 lines) ---
Purpose: Three-layer position recovery with ghost order detection.
Issues:
  - Async-based but main system is synchronous - integration unclear
  - Not called from main.py (order_recovery.py is used instead)
Imports: asyncio, json - safe

--- src/validation/walk_forward.py (375 lines) ---
Purpose: Walk-forward analysis to prevent overfitting.
Issues: Not integrated into scanner optimization.
Imports: numpy, pandas - safe

--- src/validation/is_os_validator.py (386 lines) ---
Purpose: In-sample/out-of-sample validation.
Issues: Not integrated into scanner optimization.
Imports: numpy, pandas - safe

--- src/chain_monitor.py (230 lines) ---
Purpose: Full-chain tracing with Telegram integration.
Issues: Not integrated into main modules. Example integration files exist.
Imports: requests - needs pip install

--- src/trader_monitor.py (306 lines) ---
Purpose: Example of trader with chain monitoring integration.
Issues: Example/reference code only, not used in production.

--- src/scanner_monitor.py (202 lines) ---
Purpose: Example of scanner with chain monitoring integration.
Issues: Example/reference code only, not used in production.

--- src/monitor_config.py (107 lines) ---
Purpose: Monitor point configuration and Telegram templates.
Issues: Configuration only, not actively used.

--- Utility modules (src/utils/) ---
All 9 utility files are standalone tools not integrated into main flow:
  - db_pool.py: Connection pooling (not used - database.py creates new conn)
  - rate_limiter.py: API rate limiting (not integrated into trader.py)
  - retry_handler.py: Retry logic (not used by any core module)
  - logger.py: Custom logger setup (not used - main.py sets up logging)
  - backup_manager.py: Database backup (not scheduled/integrated)
  - performance_optimizer.py: Profiling tools
  - multiprocess_engine.py: Parallel optimization
  - secure_config.py: Encrypted config storage
  - misc_tools.py: Miscellaneous utilities
  - code_refactor.py: Code quality analysis tool

================================================================================
4. MISSING DATABASE METHODS
================================================================================

Method                        Called From                  Status
--------------------------    -------------------------   --------
save_shutdown_snapshot()      order_recovery.py:418       MISSING
get_shutdown_snapshot()       order_recovery.py:425       MISSING
get_recent_trades(days)       web_server_v2.py:400        MISSING

================================================================================
5. MOCK/SIMULATED DATA INVENTORY
================================================================================

Location                    What's Mocked                  Severity
--------------------------  ----------------------------   --------
engine.py:201               B-leg price data (0.5x A)      CRITICAL
engine.py:486               B-leg position prices           CRITICAL
engine.py:516               B-leg entry check prices        CRITICAL
scanner.py:474              Min volume (10M constant)       CRITICAL
scanner.py:479              Bid-ask spread (0.01%)          CRITICAL
web_server_v2.py:230-233    win_rate, PF, sharpe            HIGH
web_server_v2.py:483-484    Data quality score (95)         MEDIUM
web_server_v2.py:490-496    API stats (all hardcoded)       MEDIUM
web_server_v2.py:456-460    Circuit breaker states          MEDIUM
web_server_v2.py:433-452    Risk limits                     MEDIUM
memory_monitor.py:137       Memory percent (50%)            LOW

================================================================================
6. TODO/PLACEHOLDER INVENTORY
================================================================================

File                        Line   Description
--------------------------  ----   -------------------------------------------
scanner.py                  473    Get volume from Data-Core
scanner.py                  478    Get bid-ask spread from Data-Core
order_recovery.py           180    Query local order IDs from DB
order_recovery.py           302    Add order to tracking system
order_recovery.py           415    Save pending order IDs on shutdown
trader.py                   430    Log naked position to database
trader.py                   402    Implement _cancel_order (pass only)
web_server_v2.py            362    Get active signals from engine

================================================================================
7. IMPORT DEPENDENCY RISKS
================================================================================

Required External Packages:
  ccxt          - Used by trader.py, exchange_manager.py
  fastapi       - Used by monitor.py, web_server_v2.py, web_dashboard.py
  uvicorn       - Used by main.py, monitor.py
  requests      - Used by monitor.py (Telegram), chain_monitor.py
  psutil        - Used by system_monitor.py, memory_monitor.py
  scipy         - Used by scanner.py, risk_model.py, slippage_tuner.py
  numpy         - Used everywhere
  pandas        - Used everywhere
  pyyaml        - Used by config.py
  websockets    - Used by websocket_client.py (not integrated)
  pydantic      - Used by config_validation.py (not integrated)

Missing/Broken Import:
  data_validator.py:373 imports from "src.notifications.telegram_notifier"
    - This module DOES NOT EXIST in the project

Path Issues:
  main.py imports modules without package prefix (e.g., "from config import")
  This only works if running from src/ directory or src/ is in sys.path.
  web_server_v2.py adds path manually (line 15) as workaround.

================================================================================
8. ARCHITECTURE CONCERNS
================================================================================

A. MANY UNINTEGRATED MODULES
   ~25 of 56 files are standalone utilities not connected to the main flow:
   - All src/utils/ files
   - All src/monitoring/ files except system_monitor.py
   - All src/data/ files (websocket, pair_maintenance, data_validator)
   - All src/execution/ files
   - All src/validation/ files
   - src/risk/risk_model.py
   - src/exchange/exchange_manager.py
   - src/recovery/position_recovery.py
   - src/chain_monitor.py + monitor examples
   - src/config_validation.py
   - src/position_sync.py

B. DUPLICATE FUNCTIONALITY
   - 3 different web dashboards (monitor.py, web_server_v2.py, web_dashboard.py)
   - 2 config validation systems (config.py.validate() and config_validation.py)
   - 2 order recovery systems (order_recovery.py and recovery/position_recovery.py)
   - 2 position sync mechanisms (trader.py.sync_positions and position_sync.py)

C. NO requirements.txt OR pyproject.toml
   No dependency specification file found.

D. THREADING MODEL
   Main loop is single-threaded with web server in daemon thread.
   No protection against database concurrent writes from web server
   and main thread.

E. NO TESTS FOR CORE LOGIC
   Tests exist but are basic/integration level. No unit tests for:
   - Z-score calculation correctness
   - Position PnL calculation
   - Naked position protection logic
   - Order recovery classification

================================================================================
9. RECOMMENDATIONS (Priority Order)
================================================================================

P0 - MUST FIX (Blocks any trading):
  1. Fix engine.py B-leg data: implement actual pair data fetching
  2. Fix scanner volume/spread: query real exchange data
  3. Implement missing DB methods (shutdown snapshots, recent trades)
  4. Implement _cancel_order in trader.py
  5. Pass reduce_only flag to ccxt calls
  6. Fix position_sync.py SQL to match actual schema
  7. Fix web_server_v2.py field name mismatches

P1 - HIGH (Causes issues in production):
  8. Implement order ID tracking in database
  9. Fix order recovery to use stored order IDs
  10. Fix log file path mismatch (logs/ vs data/)
  11. Fix main.py to use --config argument
  12. Remove hardcoded metrics from web dashboard
  13. Create requirements.txt with all dependencies
  14. Fix ADF test to use proper statsmodels implementation
  15. Fix pandas deprecated resample rules

P2 - MEDIUM (Improve reliability):
  16. Add database connection pooling
  17. Integrate rate limiter into trader
  18. Integrate data validator into scanner pipeline
  19. Add proper unit test coverage for core calculations
  20. Remove duplicate web dashboard implementations
  21. Add startup directory/file existence checks
  22. Integrate risk_model into trading loop

P3 - LOW (Nice to have):
  23. Integrate WebSocket client for real-time data
  24. Integrate walk-forward validation into scanner
  25. Integrate latency tracker into API calls
  26. Clean up unused utility modules
  27. Add proper package structure with __init__.py files

================================================================================
END OF REPORT
================================================================================
