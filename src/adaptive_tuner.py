"""
S001-Pro V3 自适应扫描参数调优器
职责: 定期评估扫描漏斗通过率，自动调整筛选参数，
      确保有足够优质配对可交易，同时不放水进垃圾对。

核心逻辑:
- 每轮扫描后检查漏斗各层通过率
- 目标: 最终通过 5~15 对配对 (可配置)
- 太少 → 找到最严的瓶颈层，渐进放宽
- 太多 → 找到最松的层，渐进收紧
- 每个参数有绝对安全边界 (hard_min/hard_max)，永远不会越过
- 每次调整幅度受限 (max_step)，防止剧烈波动
- 调整历史写入数据库，可追溯
"""

import logging
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from datetime import datetime
import json

from config import get_config
from database import get_db

logger = logging.getLogger(__name__)


@dataclass
class ParamBound:
    """参数边界定义"""
    name: str           # 参数名 (如 "layer1.corr_median_min")
    current: float      # 当前值
    hard_min: float     # 绝对下限 (再低就是垃圾)
    hard_max: float     # 绝对上限 (再高就没对了)
    relax_step: float   # 每次放宽步长
    tighten_step: float # 每次收紧步长
    direction: str      # "min"=阈值越小越宽松, "max"=阈值越大越宽松
    layer: int          # 所属层 (1/2/3/4)
    description: str    # 描述


@dataclass
class FunnelStats:
    """漏斗统计"""
    candidates: int = 0
    data_loaded: int = 0     # 数据加载成功
    layer1_passed: int = 0   # L1 统计基础
    layer2_passed: int = 0   # L2 稳定性
    layer3_passed: int = 0   # L3 可交易性
    backtest_passed: int = 0 # 回测通过 (PF >= 1.3)
    final_count: int = 0     # 最终配对数
    
    # 每层细分淘汰原因
    reject_reasons: Dict[str, int] = field(default_factory=dict)


@dataclass
class TuneAction:
    """调参动作"""
    param_name: str
    old_value: float
    new_value: float
    reason: str
    layer: int
    timestamp: str = ""


class AdaptiveTuner:
    """自适应扫描参数调优器"""
    
    # ===== 目标配置 =====
    TARGET_MIN_PAIRS = 5     # 最少需要这么多配对
    TARGET_MAX_PAIRS = 15    # 超过这个就收紧
    TARGET_IDEAL = 10        # 理想数量
    
    # ===== 调整策略 =====
    MAX_ADJUSTMENTS_PER_ROUND = 2  # 每轮最多调2个参数
    COOLDOWN_ROUNDS = 3            # 调整后至少等3轮再调
    CONSECUTIVE_FAILS_TO_ACT = 2   # 连续N轮不达标才调
    
    def __init__(self):
        self.cfg = get_config()
        self.db = get_db()
        
        # 调整历史
        self.adjustment_history: List[TuneAction] = []
        self.rounds_since_last_adjust = 0
        self.consecutive_low_count = 0
        self.consecutive_high_count = 0
        
        # 构建参数边界表
        self.param_bounds = self._build_param_bounds()
        
        # 初始化数据库表
        self._init_db()
    
    def _build_param_bounds(self) -> List[ParamBound]:
        """构建参数边界表 — 每个可调参数的安全范围"""
        cfg = self.cfg
        return [
            # ========== Layer 1: 统计基础 ==========
            ParamBound(
                name="layer1.corr_median_min",
                current=cfg.layer1.corr_median_min,
                hard_min=0.40,    # 低于0.4的相关性基本没意义
                hard_max=0.80,    # 高于0.8太严格，几乎没对
                relax_step=0.03,  # 每次放宽0.03
                tighten_step=0.02,
                direction="min",  # 值越小越宽松
                layer=1,
                description="滚动相关系数中位数下限"
            ),
            ParamBound(
                name="layer1.coint_p_max",
                current=cfg.layer1.coint_p_max,
                hard_min=0.01,    # 低于0.01太严格
                hard_max=0.30,    # 高于0.3的协整太弱
                relax_step=0.03,
                tighten_step=0.02,
                direction="max",  # 值越大越宽松
                layer=1,
                description="协整检验p值上限"
            ),
            ParamBound(
                name="layer1.adf_p_max",
                current=cfg.layer1.adf_p_max,
                hard_min=0.01,
                hard_max=0.25,    # 高于0.25的ADF太弱
                relax_step=0.03,
                tighten_step=0.02,
                direction="max",
                layer=1,
                description="ADF检验p值上限"
            ),
            
            # ========== Layer 2: 稳定性 ==========
            ParamBound(
                name="layer2.half_life_max",
                current=cfg.layer2.half_life_max,
                hard_min=12,      # 低于12太快，可能噪声
                hard_max=96,      # 高于96太慢，不实用
                relax_step=6,
                tighten_step=4,
                direction="max",
                layer=2,
                description="半衰期上限(根K线数)"
            ),
            ParamBound(
                name="layer2.corr_std_max",
                current=cfg.layer2.corr_std_max,
                hard_min=0.05,    # 低于0.05太严格
                hard_max=0.25,    # 高于0.25不稳定
                relax_step=0.02,
                tighten_step=0.01,
                direction="max",
                layer=2,
                description="滚动相关系数标准差上限"
            ),
            ParamBound(
                name="layer2.hurst_max",
                current=cfg.layer2.hurst_max,
                hard_min=0.35,    # 低于0.35太严格
                hard_max=0.65,    # 高于0.65趋向随机游走
                relax_step=0.03,
                tighten_step=0.02,
                direction="max",
                layer=2,
                description="赫斯特指数上限"
            ),
            
            # ========== Layer 3: 可交易性 ==========
            ParamBound(
                name="layer3.zscore_max_min",
                current=cfg.layer3.zscore_max_min,
                hard_min=1.5,     # 低于1.5，Z-Score太小无利可图
                hard_max=3.5,     # 高于3.5太严格
                relax_step=0.2,
                tighten_step=0.1,
                direction="min",
                layer=3,
                description="历史最大Z-Score下限"
            ),
            ParamBound(
                name="layer3.volume_min",
                current=cfg.layer3.volume_min,
                hard_min=500_000,     # 低于50万太小
                hard_max=10_000_000,  # 高于1000万太严格
                relax_step=500_000,
                tighten_step=250_000,
                direction="min",
                layer=3,
                description="最小日成交量(USDT)"
            ),
            ParamBound(
                name="layer3.bid_ask_max",
                current=cfg.layer3.bid_ask_max,
                hard_min=0.00005,     # 低于0.005%太严格
                hard_max=0.005,       # 高于0.5%滑点太大
                relax_step=0.0001,
                tighten_step=0.00005,
                direction="max",
                layer=3,
                description="最大买卖价差百分比"
            ),
            
            # ========== Layer 4: 回测门槛 ==========
            ParamBound(
                name="output.min_pf",
                current=cfg.output.get('min_pf', 1.3) if isinstance(cfg.output, dict) else 1.3,
                hard_min=1.05,    # 低于1.05几乎不赚钱
                hard_max=2.0,     # 高于2.0太严格
                relax_step=0.05,
                tighten_step=0.05,
                direction="min",
                layer=4,
                description="最低Profit Factor"
            ),
        ]
    
    def _init_db(self):
        """初始化调参历史表"""
        conn = self.db._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tuner_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                pool TEXT,
                final_count INTEGER,
                target_min INTEGER,
                target_max INTEGER,
                action_taken TEXT,
                params_before TEXT,
                params_after TEXT,
                funnel_stats TEXT,
                reason TEXT
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tuner_funnel_detail (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                pool TEXT,
                candidates INTEGER,
                data_loaded INTEGER,
                l1_passed INTEGER,
                l2_passed INTEGER,
                l3_passed INTEGER,
                backtest_passed INTEGER,
                final_count INTEGER,
                reject_detail TEXT
            )
        """)
        conn.commit()
    
    def evaluate_and_tune(self, pool: str, funnel: FunnelStats) -> List[TuneAction]:
        """
        核心方法: 评估漏斗并决定是否调参
        
        Args:
            pool: 池名称
            funnel: 本轮扫描的漏斗统计
            
        Returns:
            调参动作列表 (可能为空)
        """
        self.rounds_since_last_adjust += 1
        actions = []
        
        # 1. 记录漏斗详情
        self._log_funnel(pool, funnel)
        
        final = funnel.final_count
        
        logger.info(f"[Tuner] 漏斗评估: {pool} | "
                    f"候选={funnel.candidates} → "
                    f"数据={funnel.data_loaded} → "
                    f"L1={funnel.layer1_passed} → "
                    f"L2={funnel.layer2_passed} → "
                    f"L3={funnel.layer3_passed} → "
                    f"回测={funnel.backtest_passed} → "
                    f"最终={final}")
        
        # 2. 检查是否在目标范围内
        if self.TARGET_MIN_PAIRS <= final <= self.TARGET_MAX_PAIRS:
            # 正常范围，重置计数器
            self.consecutive_low_count = 0
            self.consecutive_high_count = 0
            logger.info(f"[Tuner] ✅ 配对数 {final} 在目标范围 [{self.TARGET_MIN_PAIRS}, {self.TARGET_MAX_PAIRS}]，无需调整")
            self._log_history(pool, final, actions, "in_range")
            return actions
        
        # 3. 配对太少
        if final < self.TARGET_MIN_PAIRS:
            self.consecutive_low_count += 1
            self.consecutive_high_count = 0
            
            logger.warning(f"[Tuner] ⚠️ 配对数 {final} < 目标 {self.TARGET_MIN_PAIRS}，"
                          f"连续 {self.consecutive_low_count} 轮")
            
            if (self.consecutive_low_count >= self.CONSECUTIVE_FAILS_TO_ACT and 
                self.rounds_since_last_adjust >= self.COOLDOWN_ROUNDS):
                actions = self._relax_bottleneck(funnel)
                if actions:
                    self.rounds_since_last_adjust = 0
                    self.consecutive_low_count = 0
        
        # 4. 配对太多
        elif final > self.TARGET_MAX_PAIRS:
            self.consecutive_high_count += 1
            self.consecutive_low_count = 0
            
            logger.info(f"[Tuner] 📈 配对数 {final} > 目标 {self.TARGET_MAX_PAIRS}，"
                       f"连续 {self.consecutive_high_count} 轮")
            
            if (self.consecutive_high_count >= self.CONSECUTIVE_FAILS_TO_ACT and
                self.rounds_since_last_adjust >= self.COOLDOWN_ROUNDS):
                actions = self._tighten_loosest(funnel)
                if actions:
                    self.rounds_since_last_adjust = 0
                    self.consecutive_high_count = 0
        
        # 5. 应用调整
        if actions:
            self._apply_actions(actions)
        
        self._log_history(pool, final, actions, 
                         "too_few" if final < self.TARGET_MIN_PAIRS else "too_many")
        
        return actions
    
    def _relax_bottleneck(self, funnel: FunnelStats) -> List[TuneAction]:
        """
        找到最严的瓶颈层，放宽其参数
        
        瓶颈定义: 淘汰率最高的层
        """
        actions = []
        
        # 计算每层淘汰率
        layer_stats = self._calc_layer_kill_rates(funnel)
        
        if not layer_stats:
            logger.warning("[Tuner] 无法计算层级淘汰率")
            return actions
        
        # 按淘汰率排序 (最高的优先)
        layer_stats.sort(key=lambda x: x[1], reverse=True)
        
        adjusted = 0
        for layer_num, kill_rate, input_count, output_count in layer_stats:
            if adjusted >= self.MAX_ADJUSTMENTS_PER_ROUND:
                break
            
            if kill_rate < 0.3:
                # 这层淘汰率不高，不需要调
                continue
            
            # 找到这层最可以放宽的参数
            layer_params = [p for p in self.param_bounds if p.layer == layer_num]
            
            for param in layer_params:
                if adjusted >= self.MAX_ADJUSTMENTS_PER_ROUND:
                    break
                
                new_val = self._calc_relaxed_value(param)
                if new_val is not None and new_val != param.current:
                    action = TuneAction(
                        param_name=param.name,
                        old_value=param.current,
                        new_value=new_val,
                        reason=f"L{layer_num}淘汰率{kill_rate:.0%}({input_count}→{output_count}), "
                               f"放宽{param.description}",
                        layer=layer_num,
                        timestamp=datetime.now().isoformat()
                    )
                    actions.append(action)
                    adjusted += 1
                    logger.info(f"[Tuner] 🔧 放宽 {param.name}: {param.current} → {new_val} "
                               f"(L{layer_num}淘汰率{kill_rate:.0%})")
        
        return actions
    
    def _tighten_loosest(self, funnel: FunnelStats) -> List[TuneAction]:
        """
        配对太多时，收紧最松的层
        
        最松 = 通过率最高的层
        """
        actions = []
        
        layer_stats = self._calc_layer_kill_rates(funnel)
        if not layer_stats:
            return actions
        
        # 按淘汰率排序 (最低的优先 = 最松)
        layer_stats.sort(key=lambda x: x[1])
        
        adjusted = 0
        for layer_num, kill_rate, input_count, output_count in layer_stats:
            if adjusted >= self.MAX_ADJUSTMENTS_PER_ROUND:
                break
            
            if kill_rate > 0.7:
                # 这层已经很严了，不再收紧
                continue
            
            layer_params = [p for p in self.param_bounds if p.layer == layer_num]
            
            for param in layer_params:
                if adjusted >= self.MAX_ADJUSTMENTS_PER_ROUND:
                    break
                
                new_val = self._calc_tightened_value(param)
                if new_val is not None and new_val != param.current:
                    action = TuneAction(
                        param_name=param.name,
                        old_value=param.current,
                        new_value=new_val,
                        reason=f"L{layer_num}通过率过高({1-kill_rate:.0%}), "
                               f"收紧{param.description}",
                        layer=layer_num,
                        timestamp=datetime.now().isoformat()
                    )
                    actions.append(action)
                    adjusted += 1
                    logger.info(f"[Tuner] 🔧 收紧 {param.name}: {param.current} → {new_val}")
        
        return actions
    
    def _calc_layer_kill_rates(self, funnel: FunnelStats) -> List[Tuple[int, float, int, int]]:
        """
        计算每层的淘汰率
        
        Returns: [(layer_num, kill_rate, input, output), ...]
        """
        results = []
        
        # L1: data_loaded → layer1_passed
        if funnel.data_loaded > 0:
            rate = 1 - funnel.layer1_passed / funnel.data_loaded
            results.append((1, rate, funnel.data_loaded, funnel.layer1_passed))
        
        # L2: layer1_passed → layer2_passed
        if funnel.layer1_passed > 0:
            rate = 1 - funnel.layer2_passed / funnel.layer1_passed
            results.append((2, rate, funnel.layer1_passed, funnel.layer2_passed))
        
        # L3: layer2_passed → layer3_passed
        if funnel.layer2_passed > 0:
            rate = 1 - funnel.layer3_passed / funnel.layer2_passed
            results.append((3, rate, funnel.layer2_passed, funnel.layer3_passed))
        
        # L4 (回测): layer3_passed → backtest_passed
        if funnel.layer3_passed > 0:
            rate = 1 - funnel.backtest_passed / funnel.layer3_passed
            results.append((4, rate, funnel.layer3_passed, funnel.backtest_passed))
        
        return results
    
    def _calc_relaxed_value(self, param: ParamBound) -> Optional[float]:
        """计算放宽后的值 (带安全边界)"""
        if param.direction == "min":
            # "min"类参数：值越小越宽松
            new_val = param.current - param.relax_step
            new_val = max(new_val, param.hard_min)
        else:
            # "max"类参数：值越大越宽松
            new_val = param.current + param.relax_step
            new_val = min(new_val, param.hard_max)
        
        # 如果已经到边界了，返回None
        if abs(new_val - param.current) < 1e-10:
            logger.info(f"[Tuner] {param.name} 已到安全边界 {new_val}，不再放宽")
            return None
        
        return round(new_val, 6)
    
    def _calc_tightened_value(self, param: ParamBound) -> Optional[float]:
        """计算收紧后的值 (带安全边界)"""
        if param.direction == "min":
            # "min"类参数：值越大越严格
            new_val = param.current + param.tighten_step
            new_val = min(new_val, param.hard_max)
        else:
            # "max"类参数：值越小越严格
            new_val = param.current - param.tighten_step
            new_val = max(new_val, param.hard_min)
        
        if abs(new_val - param.current) < 1e-10:
            return None
        
        return round(new_val, 6)
    
    def _apply_actions(self, actions: List[TuneAction]):
        """应用调参动作到配置"""
        cfg = self.cfg
        
        for action in actions:
            parts = action.param_name.split(".")
            
            if parts[0] == "layer1":
                setattr(cfg.layer1, parts[1], action.new_value)
            elif parts[0] == "layer2":
                setattr(cfg.layer2, parts[1], action.new_value)
            elif parts[0] == "layer3":
                setattr(cfg.layer3, parts[1], action.new_value)
            elif parts[0] == "output":
                cfg.output[parts[1]] = action.new_value
            
            # 更新 param_bounds 中的 current
            for pb in self.param_bounds:
                if pb.name == action.param_name:
                    pb.current = action.new_value
                    break
            
            logger.info(f"[Tuner] ✅ 已应用: {action.param_name} = {action.new_value}")
        
        # 同步更新scanner中的引用
        self._sync_scanner_params()
    
    def _sync_scanner_params(self):
        """同步参数到Scanner实例 (内存中的引用)"""
        cfg = self.cfg
        # Scanner在初始化时引用了 cfg.layer1/2/3，
        # 由于是同一个Config对象，setattr已经生效
        logger.info("[Tuner] 参数已同步到Scanner")
    
    def _log_funnel(self, pool: str, funnel: FunnelStats):
        """记录漏斗详情到数据库"""
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO tuner_funnel_detail 
                (pool, candidates, data_loaded, l1_passed, l2_passed, 
                 l3_passed, backtest_passed, final_count, reject_detail)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                pool, funnel.candidates, funnel.data_loaded,
                funnel.layer1_passed, funnel.layer2_passed,
                funnel.layer3_passed, funnel.backtest_passed,
                funnel.final_count,
                json.dumps(funnel.reject_reasons, default=str)
            ))
            conn.commit()
        except Exception as e:
            logger.error(f"[Tuner] 记录漏斗失败: {e}")
    
    def _log_history(self, pool: str, final_count: int, 
                     actions: List[TuneAction], reason: str):
        """记录调参历史"""
        try:
            params_before = {a.param_name: a.old_value for a in actions}
            params_after = {a.param_name: a.new_value for a in actions}
            action_desc = "; ".join(
                f"{a.param_name}: {a.old_value}→{a.new_value}" for a in actions
            ) if actions else "无调整"
            
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO tuner_history 
                (pool, final_count, target_min, target_max, action_taken,
                 params_before, params_after, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                pool, final_count, self.TARGET_MIN_PAIRS, self.TARGET_MAX_PAIRS,
                action_desc,
                json.dumps(params_before, default=str),
                json.dumps(params_after, default=str),
                reason
            ))
            conn.commit()
        except Exception as e:
            logger.error(f"[Tuner] 记录历史失败: {e}")
    
    def get_current_params(self) -> Dict:
        """获取当前所有可调参数的快照"""
        return {
            pb.name: {
                "current": pb.current,
                "hard_min": pb.hard_min,
                "hard_max": pb.hard_max,
                "layer": pb.layer,
                "description": pb.description,
                "headroom_relax": self._calc_headroom(pb, "relax"),
                "headroom_tighten": self._calc_headroom(pb, "tighten"),
            }
            for pb in self.param_bounds
        }
    
    def _calc_headroom(self, param: ParamBound, direction: str) -> float:
        """计算参数距离边界的余量百分比"""
        if direction == "relax":
            if param.direction == "min":
                total_range = param.hard_max - param.hard_min
                remaining = param.current - param.hard_min
            else:
                total_range = param.hard_max - param.hard_min
                remaining = param.hard_max - param.current
        else:  # tighten
            if param.direction == "min":
                total_range = param.hard_max - param.hard_min
                remaining = param.hard_max - param.current
            else:
                total_range = param.hard_max - param.hard_min
                remaining = param.current - param.hard_min
        
        if total_range == 0:
            return 0.0
        return round(remaining / total_range * 100, 1)
    
    def get_tuner_status(self) -> Dict:
        """获取调优器状态 (用于监控面板)"""
        return {
            "rounds_since_adjust": self.rounds_since_last_adjust,
            "consecutive_low": self.consecutive_low_count,
            "consecutive_high": self.consecutive_high_count,
            "target_range": [self.TARGET_MIN_PAIRS, self.TARGET_MAX_PAIRS],
            "total_adjustments": len(self.adjustment_history),
            "params": self.get_current_params()
        }


# ===== 全局实例 =====
_tuner: Optional[AdaptiveTuner] = None


def get_tuner() -> AdaptiveTuner:
    """获取全局调优器实例"""
    global _tuner
    if _tuner is None:
        _tuner = AdaptiveTuner()
    return _tuner


if __name__ == "__main__":
    # 测试
    logging.basicConfig(level=logging.INFO)
    
    tuner = AdaptiveTuner()
    print("\n当前参数状态:")
    for name, info in tuner.get_current_params().items():
        print(f"  {name}: {info['current']} "
              f"[{info['hard_min']} ~ {info['hard_max']}] "
              f"放宽余量:{info['headroom_relax']}% "
              f"收紧余量:{info['headroom_tighten']}%")
    
    # 模拟漏斗数据
    funnel = FunnelStats(
        candidates=190,
        data_loaded=150,
        layer1_passed=80,
        layer2_passed=20,
        layer3_passed=5,
        backtest_passed=2,
        final_count=2
    )
    
    print(f"\n模拟漏斗: 190→150→80→20→5→2")
    actions = tuner.evaluate_and_tune("primary", funnel)
    
    if actions:
        print(f"\n调参动作:")
        for a in actions:
            print(f"  {a.param_name}: {a.old_value} → {a.new_value} ({a.reason})")
    else:
        print("\n无调参动作 (需要连续多轮低于目标才触发)")
