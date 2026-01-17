# ============================================================================
# 🤖 BOT V8 - 融合版本 Part 1: 导入和基础类
# ============================================================================
# 融合策略：
# ✅ 保留 v7 的智能系统（地图分类、停滞检测、Lane扩张）
# ✅ 借鉴 v1 的果断决策（不过度等待完美条件）
# ✅ 核心改进：评分式选址（而非硬性拒绝）
# ============================================================================

from __future__ import annotations

import time
from dataclasses import dataclass
from collections import deque
from typing import Deque, Dict, List, Optional, Set, Tuple

from game_message import (
    Action,
    Position,
    SpawnerProduceSporeAction,
    Spore,
    SporeCreateSpawnerAction,
    SporeMoveAction,
    SporeMoveToAction,
    SporeSplitAction,
    TeamGameState,
    TeamInfo,
)

# Cardinal directions (no diagonals)
DIRS: List[Position] = [
    Position(x=0, y=-1),  # UP
    Position(x=0, y=1),   # DOWN
    Position(x=-1, y=0),  # LEFT
    Position(x=1, y=0),   # RIGHT
]


@dataclass(frozen=True)
class Point:
    x: int
    y: int


def _in_bounds(x: int, y: int, width: int, height: int) -> bool:
    return 0 <= x < width and 0 <= y < height


def _pos_to_point(p: Position) -> Point:
    return Point(p.x, p.y)


def _manhattan(a: Point, b: Point) -> int:
    return abs(a.x - b.x) + abs(a.y - b.y)


# Global cache for top nutrient tiles
_GLOBAL_TOP_TILES_CACHE: Dict[Tuple[int, int], List[Point]] = {}


# ============================================================================
# 🧭 LANE / QUADRANT EXPANSION PLANNER (保留 v7)
# ============================================================================
class LanePlanner:
    """大地图扩张：用 4 象限 / 车道 (lane) 锁定目标，避免来回循环。

    设计目标：
    - 给普通扩张单位一个相对持久的方向目标（锁定 8~15 tick）
    - 每个象限至少有 1 个扩张者，减少全员抢同一块地造成的堆积/回撤
    - 停滞时更激进：锁更远的目标 + 延长锁定时间
    """

    def __init__(self) -> None:
        self._lane_targets: Dict[str, Tuple[Point, int]] = {}  # lane -> (target, expiry_tick)
        self._lane_by_spore_id: Dict[str, Tuple[str, int]] = {}  # sporeId -> (lane, expiry_tick)

    @staticmethod
    def _lane_of(pt: Point, center: Point) -> str:
        # NW / NE / SW / SE
        if pt.y < center.y:
            return "NW" if pt.x < center.x else "NE"
        return "SW" if pt.x < center.x else "SE"

    def assign_lane(self, spore_id: str, sp_pt: Point, center: Point, tick: int) -> str:
        # 维持一段时间，避免每 tick 改 lane 导致抖动
        cur = self._lane_by_spore_id.get(spore_id)
        if cur is not None:
            lane, expiry = cur
            if tick <= expiry:
                return lane

        lane = self._lane_of(sp_pt, center)
        self._lane_by_spore_id[spore_id] = (lane, tick + 25)  # 默认锁 25 tick，后面可被更新
        return lane

    def pick_lane_targets(
        self,
        top_tiles: List[Point],
        center: Point,
        width: int,
        height: int,
        tick: int,
        is_stagnant: bool,
        tile_value_fn,
        tile_owner_fn,
        threat_at_fn,
        my_team_id: int,
    ) -> Dict[str, Point]:
        """从 top nutrient tiles 中为每个象限选 1 个目标。

        轻量：只扫描 top_tiles 的前 ~120 个。
        """

        # 目标锁定时间：停滞时更久，避免摇摆
        ttl = 18 if is_stagnant else 10
        scan_k = 140 if is_stagnant else 110

        # 若已有 lane 目标且未过期，就复用
        out: Dict[str, Point] = {}
        for lane, (pt, expiry) in list(self._lane_targets.items()):
            if tick <= expiry:
                out[lane] = pt
            else:
                self._lane_targets.pop(lane, None)

        # 还缺的 lane 重新选
        need = [l for l in ("NW", "NE", "SW", "SE") if l not in out]
        if not need:
            return out

        # 给每个 lane 选一个"远 + 高价值 + 相对安全"的点
        best: Dict[str, Tuple[int, Point]] = {}
        for pt in top_tiles[:scan_k]:
            # 忽略已经是我方领地且很靠近中心的点，避免局部循环
            if tile_owner_fn(pt) == my_team_id and _manhattan(pt, center) <= 3:
                continue

            # 若格子威胁太高，直接跳过（硬约束）
            if threat_at_fn(pt) >= 8:
                continue

            lane = self._lane_of(pt, center)
            if lane not in need:
                continue

            tv = tile_value_fn(pt)
            dist = _manhattan(pt, center)
            # 大地图：偏好更远的目标，停滞时远距离更重要
            score = tv * 6 + dist * (22 if is_stagnant else 14)
            if tile_owner_fn(pt) != my_team_id:
                score += 120

            prev = best.get(lane)
            if prev is None or score > prev[0]:
                best[lane] = (score, pt)

        for lane in need:
            if lane in best:
                pt = best[lane][1]
                self._lane_targets[lane] = (pt, tick + ttl)
                out[lane] = pt

        return out


# ============================================================================
# 🗺️ MAP ANALYSIS CLASS (保留 v7)
# ============================================================================
class ImprovedMapAnalysis:
    """地图分析系统"""
    
    def __init__(self):
        self._map_type_cache: Dict[Tuple[int, int], str] = {}
    
    def analyze_map_type(
        self, 
        width: int, 
        height: int,
        neutral_spores: List[Tuple[Point, int]],
        my_spore_count: int,
        nutrient_grid: List[List[int]]
    ) -> str:
        """识别地图类型"""
        key = (width, height)
        if key in self._map_type_cache:
            return self._map_type_cache[key]
        
        area = width * height
        center_x, center_y = width // 2, height // 2
        center_neutrals = sum(
            1 for pt, _ in neutral_spores
            if abs(pt.x - center_x) <= 3 and abs(pt.y - center_y) <= 3
        )
        total_neutrals = len(neutral_spores)
        
        if area <= 225:
            if center_neutrals >= 5 or total_neutrals >= 8:
                map_type = "blocked_small"
                print(f"🗺️  Map: BLOCKED_SMALL (neutrals: {total_neutrals}, center: {center_neutrals})")
            else:
                map_type = "open_rush"
                print(f"🗺️  Map: OPEN_RUSH (area: {area})")
        elif area <= 900:
            map_type = "medium"
            print(f"🗺️  Map: MEDIUM (area: {area})")
        else:
            map_type = "large"
            print(f"🗺️  Map: LARGE (area: {area})")
        
        self._map_type_cache[key] = map_type
        return map_type
    
    def should_build_first_spawner_v8(
        self,
        map_type: str,
        tick: int,
        actionable_count: int,
        max_biomass: int,
        next_spawner_cost: int,
        total_spore_count: int,
        neutral_count: int,
        my_nutrients: int
    ) -> Tuple[bool, int, bool]:
        """🌟 v8 核心改进：更渐进、更果断的建造决策
        
        返回: (should_build, min_biomass_needed, is_emergency)
        
        关键改进：
        - 不再有"等完美条件"的硬门槛
        - 分阶段放宽要求
        - 紧急模式会强制建造
        """

        # === 紧急度判定 ===
        is_emergency = (
            tick >= 18 or  # 时间紧迫
            (tick >= 12 and actionable_count <= 2) or  # 行动力不足
            (tick >= 10 and max_biomass >= next_spawner_cost * 2)  # 有能力但拖延
        )

        # === 分地图类型决策 ===
        if map_type == "blocked_small":
            # 堵路图：稍微保守，但不能无限等
            if tick <= 8:
                # 早期：等待更好条件
                if actionable_count >= 2 and max_biomass >= next_spawner_cost + 2:
                    return (True, next_spawner_cost + 2, False)
            elif tick <= 15:
                # 中期：开始放宽
                if actionable_count >= 1 and max_biomass >= next_spawner_cost:
                    return (True, next_spawner_cost, False)
            else:
                # 后期：必须建
                if actionable_count >= 1 and max_biomass >= max(2, next_spawner_cost):
                    return (True, max(2, next_spawner_cost), True)
        
        elif map_type in ("medium", "large"):
            # 中/大图：激进建造
            if tick <= 5:
                # 超早期：有条件就建
                if actionable_count >= 1 and max_biomass >= next_spawner_cost:
                    return (True, next_spawner_cost, False)
            elif tick <= 12:
                # 早期：降低要求
                if actionable_count >= 1 and max_biomass >= max(3, next_spawner_cost):
                    return (True, max(3, next_spawner_cost), False)
            else:
                # 中期后：强制建造
                if actionable_count >= 1 and max_biomass >= max(2, next_spawner_cost):
                    return (True, max(2, next_spawner_cost), True)
        
        else:  # open_rush 或其他
            # 开阔图：平衡策略
            if tick <= 6:
                if actionable_count >= 2 and max_biomass >= next_spawner_cost + 1:
                    return (True, next_spawner_cost + 1, False)
            elif tick <= 12:
                if actionable_count >= 1 and max_biomass >= next_spawner_cost:
                    return (True, next_spawner_cost, False)
            else:
                if actionable_count >= 1 and max_biomass >= max(2, next_spawner_cost):
                    return (True, max(2, next_spawner_cost), True)
        
        return (False, 0, False)


print("✅ Part 1 加载完成：导入、基础类、LanePlanner、ImprovedMapAnalysis")
print("📌 下一步：请告诉我继续 Part 2 (ExpansionEnhancement + 评分式选址)")
# ============================================================================
# 🤖 BOT V8 - Part 2: ExpansionEnhancement + 评分式选址系统
# ============================================================================

# ============================================================================
# 🌍 EXPANSION ENHANCEMENT CLASS (保留 v7)
# ============================================================================
class ExpansionEnhancement:
    """扩张增强系统 - 解决 nutrient generation 停滞问题"""
    
    def __init__(self):
        self._last_territory_count: int = 0
        self._stagnation_ticks: int = 0
        self._last_growth_tick: int = 0
        self._last_growth_territory: int = 0
    
    def detect_expansion_stagnation(
        self,
        current_territory: int,
        tick: int,
        check_interval: int = 20
    ) -> bool:
        """检测扩张是否停滞"""
        if tick % check_interval == 0:
            if current_territory <= self._last_territory_count + 2:
                self._stagnation_ticks += check_interval
            else:
                self._stagnation_ticks = 0
            self._last_territory_count = current_territory
        
        return self._stagnation_ticks >= 40
    
    def calculate_expansion_bonus(
        self,
        pt: Point,
        my_center: Point,
        my_territory_count: int,
        total_tiles: int,
        is_stagnant: bool
    ) -> int:
        """计算扩张加分"""
        dist_from_center = _manhattan(pt, my_center)
        control_rate = my_territory_count / total_tiles if total_tiles > 0 else 0
        
        bonus = 0
        
        if control_rate < 0.3:
            bonus += dist_from_center * 8
        elif control_rate < 0.5:
            bonus += dist_from_center * 4
        
        if is_stagnant:
            bonus += 150
        
        return bonus
    
    def improved_2biomass_penalty(
        self,
        map_type: str,
        tick: int,
        my_tile_count: int,
        total_tiles: int,
        spawner_count: int,
        is_stagnant: bool,
        nutrient_generation: int
    ) -> int:
        """改进的 2-biomass 惩罚"""
        control_rate = my_tile_count / total_tiles if total_tiles > 0 else 0
        
        if is_stagnant:
            return 50
        
        if tick < 100:
            if nutrient_generation < 10:
                return 80
            elif map_type == "blocked_small":
                return 120
            else:
                return 150
        
        if tick < 300:
            if nutrient_generation < 20:
                return 150
            elif control_rate < 0.25:
                return 200
            else:
                return 350
        
        if tick < 600:
            if control_rate < 0.4:
                return 300
            else:
                return 500
        
        if spawner_count >= 2:
            return 800
        return 600
    
    def aggressive_spawner_production(
        self,
        tick: int,
        local_threat: int,
        spore_count: int,
        nutrients: int,
        my_territory_count: int,
        total_tiles: int,
        is_stagnant: bool,
        nutrient_generation: int
    ) -> int:
        """激进的 Spawner 生产策略"""
        control_rate = my_territory_count / total_tiles if total_tiles > 0 else 0
        
        if is_stagnant and nutrients >= 30:
            return min(nutrients // 3, 15)
        
        if tick < 150:
            if spore_count < 10:
                return max(8, nutrients // 6)
            elif spore_count < 20:
                return max(6, nutrients // 8)
            else:
                return max(4, nutrients // 10)
        
        if tick < 400:
            if nutrient_generation < 15:
                return max(5, nutrients // 10)
            elif nutrient_generation < 30:
                return max(6, nutrients // 8)
            else:
                return max(7, nutrients // 7)
        
        if tick < 700:
            if local_threat > 0:
                return max(local_threat + 3, 8)
            
            if control_rate < 0.4:
                return max(8, nutrients // 6)
            else:
                return max(6, nutrients // 8)
        
        if nutrients >= 2000:
            return min(15, nutrients // 5)
        elif nutrients >= 1000:
            return min(12, nutrients // 6)
        elif local_threat > 0:
            return max(local_threat + 3, 8)
        else:
            return max(7, nutrients // 8)


# ============================================================================
# 🎯 核心改进：评分式选址系统（替代 v7 的硬性拒绝）
# ============================================================================
class ImprovedSiteSelection:
    """🌟 v8 核心改进：评分式选址
    
    关键变化：
    - v7: 用 bool 判断是否"安全" → 不安全就拒绝
    - v8: 给每个候选点打分 → 选择最高分的，即使不完美
    
    优势：
    - 不会因为"找不到完美点"而无限等待
    - 在困难地图上也能找到"相对最优"的位置
    - 通过评分权重体现优先级
    """
    
    @staticmethod
    def score_spawner_site(
        pt: Point,
        tile_value_fn,
        tile_owner_fn,
        tile_biomass_fn,
        threat_at_fn,
        enemy_density_fn,
        min_enemy_dist_fn,
        adjacent_enemy_max_fn,
        builder_biomass: int,
        my_team_id: int,
        is_second_spawner: bool,
        spawner_positions: List[Point],
        tick: int,
        map_type: str,
        is_emergency: bool
    ) -> int:
        """
        综合评分函数
        
        返回：分数（越高越好）
        - 正分：优势因素（营养价值、控制权、距离敌人远）
        - 负分：劣势因素（威胁、敌人密度）
        """
        score = 0
        
        # === 1. 基础价值 ===
        nutrient_value = tile_value_fn(pt)
        score += nutrient_value * 5  # 营养价值权重
        
        # === 2. 控制权加分 ===
        owner = tile_owner_fn(pt)
        if owner == my_team_id:
            score += 100  # 已控制的地块更安全
        
        # 如果地块已有敌方单位，严重惩罚
        biomass = tile_biomass_fn(pt)
        if biomass > 0 and owner != my_team_id:
            score -= 500  # 被占领的地块
        
        # === 3. 威胁评估（软性惩罚，而非硬性拒绝）===
        threat = threat_at_fn(pt)
        
        if is_emergency:
            # 🚨 紧急模式：只要不是立即死亡就接受
            if threat >= builder_biomass:
                score -= 400  # 严重惩罚，但不完全拒绝
            else:
                score -= threat * 15  # 温和惩罚
        else:
            # ⚖️ 正常模式：更严格但仍是评分
            safety_margin = 2 if builder_biomass >= 6 else 1
            if threat >= builder_biomass - safety_margin:
                score -= 250  # 较重惩罚
            else:
                score -= threat * 25
        
        # === 4. 相邻威胁 ===
        adj_threat = adjacent_enemy_max_fn(pt)
        if is_emergency:
            score -= adj_threat * 10
        else:
            if adj_threat > 0 and builder_biomass <= adj_threat:
                score -= 200  # 相邻有强敌
            else:
                score -= adj_threat * 20
        
        # === 5. 敌人密度（软性约束）===
        enemy_dens = enemy_density_fn(pt, radius=5)
        
        if map_type in ("open_rush", "blocked_small", "medium"):
            # 小/中图更在意密度
            if tick <= 15:
                score -= enemy_dens * 40
            elif tick <= 30:
                score -= enemy_dens * 25
            else:
                score -= enemy_dens * 15  # 后期放宽
        else:
            # 大图：密度不那么重要
            score -= enemy_dens * 10
        
        # === 6. 与敌人的最小距离 ===
        min_dist = min_enemy_dist_fn(pt)
        score += min(min_dist * 8, 200)  # 距离加分，但有上限
        
        # === 7. 第二个 Spawner 的位置分散性 ===
        if is_second_spawner and spawner_positions:
            dist_to_first = _manhattan(pt, spawner_positions[0])
            score += dist_to_first * 6  # 鼓励分散
        
        # === 8. 特殊情况处理 ===
        # 如果已经有 spawner 在这里，拒绝
        if pt in spawner_positions:
            score -= 10000
        
        return score
    
    @staticmethod
    def select_best_site(
        candidate_tiles: List[Point],
        tile_value_fn,
        tile_owner_fn,
        tile_biomass_fn,
        threat_at_fn,
        enemy_density_fn,
        min_enemy_dist_fn,
        adjacent_enemy_max_fn,
        builder_biomass: int,
        my_team_id: int,
        is_second_spawner: bool,
        spawner_positions: List[Point],
        tick: int,
        map_type: str,
        is_emergency: bool
    ) -> Optional[Point]:
        """
        从候选地块中选择最佳位置
        
        🌟 关键改进：不再有硬性拒绝，而是选择得分最高的
        """
        best_site = None
        best_score = -10**9
        
        for pt in candidate_tiles:
            # 跳过已有 spawner 的位置（这是唯一的硬性约束）
            if pt in spawner_positions:
                continue
            
            score = ImprovedSiteSelection.score_spawner_site(
                pt=pt,
                tile_value_fn=tile_value_fn,
                tile_owner_fn=tile_owner_fn,
                tile_biomass_fn=tile_biomass_fn,
                threat_at_fn=threat_at_fn,
                enemy_density_fn=enemy_density_fn,
                min_enemy_dist_fn=min_enemy_dist_fn,
                adjacent_enemy_max_fn=adjacent_enemy_max_fn,
                builder_biomass=builder_biomass,
                my_team_id=my_team_id,
                is_second_spawner=is_second_spawner,
                spawner_positions=spawner_positions,
                tick=tick,
                map_type=map_type,
                is_emergency=is_emergency
            )
            
            if score > best_score:
                best_score = score
                best_site = pt
        
        # 🎯 只要有候选就返回最好的，不再要求"绝对安全"
        if best_site is not None:
            print(f"    🎯 Selected site {best_site} with score {best_score}")
        
        return best_site


# ============================================================================
# 🎯 核心改进：渐进式 Builder 选择
# ============================================================================
class ImprovedBuilderSelection:
    """🌟 v8 核心改进：渐进式选择建造者
    
    关键变化：
    - v7: 硬性要求"安全位置 + 足够生物量 + 近距离"
    - v8: 渐进式放宽，从理想到可接受
    
    优势：
    - 不会因为"找不到完美 builder"而停滞
    - 紧急模式会选择"最强的可用单位"
    """
    
    @staticmethod
    def select_builder_progressive(
        site: Point,
        actionable_spores: List,
        next_spawner_cost: int,
        avoid_ids: Set[str],
        used_spores: Set[str],
        threat_at_fn,
        tick: int,
        is_emergency: bool
    ) -> Optional:
        """
        渐进式选择：
        1. 理想：高生物量 + 近距离 + 安全
        2. 可接受：满足成本 + 相对近
        3. 紧急：任何满足成本的
        """
        
        candidates = []
        
        for sp in actionable_spores:
            if sp.id in avoid_ids or sp.id in used_spores:
                continue
            
            sp_pt = Point(sp.position.x, sp.position.y)
            dist = _manhattan(sp_pt, site)
            
            # 🔒 硬性要求：必须有足够的生物量
            if sp.biomass < next_spawner_cost:
                continue
            
            # 计算候选分数
            score = 0
            
            # 📏 距离：越近越好
            score -= dist * 100
            
            # 💪 生物量：有余量更好（但不强求）
            biomass_surplus = sp.biomass - next_spawner_cost
            score += biomass_surplus * 5
            
            # 🛡️ 安全性评估（软性）
            sp_threat = threat_at_fn(sp_pt)
            if is_emergency:
                # 🚨 紧急模式：只要不是立即威胁就行
                if sp_threat < sp.biomass:
                    score += 50
            else:
                # ⚖️ 正常模式：偏好更安全的位置
                if sp_threat == 0:
                    score += 100
                elif sp_threat < sp.biomass - 2:
                    score += 50
                else:
                    score -= 100  # 不太安全，但不拒绝
            
            candidates.append((score, sp))
        
        if not candidates:
            return None
        
        # 选择最高分的
        candidates.sort(key=lambda x: x[0], reverse=True)
        selected = candidates[0][1]
        
        print(f"    🔨 Selected builder: biomass={selected.biomass}, "
              f"position=({selected.position.x},{selected.position.y}), "
              f"score={candidates[0][0]:.1f}")
        
        return selected


print("✅ Part 2 加载完成：ExpansionEnhancement + 评分式选址系统")
print("📌 核心改进：")
print("   - ImprovedSiteSelection: 评分式选址（不再硬性拒绝）")
print("   - ImprovedBuilderSelection: 渐进式 builder 选择")
print("📌 下一步：请告诉我继续 Part 3 (主 Bot 类初始化)")
# ============================================================================
# 🤖 BOT V8 - Part 3: 主 Bot 类初始化和辅助方法
# ============================================================================

class Bot:
    """Coveo Blitz Bot v8.0 - 融合版本
    
    核心改进（v7 → v8）：
    ✅ 保留：智能地图分类、Lane 扩张、停滞检测
    ✅ 改进：评分式选址（不再硬性拒绝）
    ✅ 改进：渐进式 Spawner 建造决策
    ✅ 改进：渐进式 Builder 选择
    ✅ 简化：减少不必要的多重嵌套判断
    """

    def __init__(self) -> None:
        print("🚀 Initializing Bot v8.0 (Hybrid: v7 智能 + v1 果断)")

        # === 分析系统（保留 v7）===
        self.map_analyzer = ImprovedMapAnalysis()
        self.expansion_enhancer = ExpansionEnhancement()
        self.lane_planner = LanePlanner()
        
        # === 新增：选址和建造系统 ===
        self.site_selector = ImprovedSiteSelection()
        self.builder_selector = ImprovedBuilderSelection()
        
        self._map_type: str = ""

        # === Cache（保留 v7）===
        self._cached_map_key: Optional[Tuple[int, int]] = None
        self._top_tiles_cache: List[Point] = []
        self._top_tiles_k: int = 400

        # === Spawner planning（保留 v7）===
        self._planned_sites: Dict[str, Point] = {}
        self._builder_target_by_id: Dict[str, Point] = {}

        # === Timing / stats（保留 v7）===
        self._first_spawner_tick: Optional[int] = None
        self._tick_count: int = 0
        self._initial_spore_count: Optional[int] = None
        self._total_spore_count: Optional[int] = None

        # === 大地图循环检测（保留 v7）===
        self._pos_hist_by_id: Dict[str, Deque[Point]] = {}

        # === 轻量缓存（保留 v7）===
        self._cached_my_tile_count: int = 0
        self._cached_nutrient_generation: int = 0
        self._last_full_scan_tick: int = 0

    # ============================================================================
    # 辅助方法（保留 v7）
    # ============================================================================
    
    def enemy_density_in_radius(
        self,
        center: Point,
        enemy_map: Dict[Point, int],
        radius: int = 5,
    ) -> int:
        """统计某点半径内的敌人格子数"""
        c = 0
        for pt in enemy_map:
            if _manhattan(center, pt) <= radius:
                c += 1
        return c
    
    def _ensure_top_tiles_cache(
        self,
        width: int,
        height: int,
        nutrient_grid: List[List[int]]
    ) -> None:
        """确保营养地块缓存已建立"""
        key = (width, height)
        if key in _GLOBAL_TOP_TILES_CACHE and _GLOBAL_TOP_TILES_CACHE[key]:
            self._cached_map_key = key
            self._top_tiles_cache = _GLOBAL_TOP_TILES_CACHE[key]
            return

        if self._cached_map_key == key and self._top_tiles_cache:
            _GLOBAL_TOP_TILES_CACHE[key] = self._top_tiles_cache
            return

        pts: List[Point] = [
            Point(x, y)
            for y in range(height)
            for x in range(width)
        ]
        pts.sort(key=lambda p: nutrient_grid[p.y][p.x], reverse=True)
        pts = pts[: self._top_tiles_k]

        self._cached_map_key = key
        self._top_tiles_cache = pts
        _GLOBAL_TOP_TILES_CACHE[key] = pts
    
    def _get_enemy_positions(
        self,
        world_spores: List,
        my_team_id: int
    ) -> Tuple[Dict[Point, int], List[Tuple[Point, int]]]:
        """获取敌人位置
        
        返回：(enemy_biomass_at, enemy_list)
        - enemy_biomass_at: 敌人位置 -> 生物量映射
        - enemy_list: [(位置, 生物量)] 列表，按生物量降序
        """
        enemy_biomass_at: Dict[Point, int] = {}
        
        for sp in world_spores:
            if sp.teamId == my_team_id or sp.teamId == 0:
                continue
            
            pt = _pos_to_point(sp.position)
            enemy_biomass_at[pt] = max(enemy_biomass_at.get(pt, 0), sp.biomass)
        
        enemy_list = list(enemy_biomass_at.items())
        enemy_list.sort(key=lambda t: t[1], reverse=True)
        
        return enemy_biomass_at, enemy_list
    
    def _get_neutral_positions(
        self,
        world_spores: List
    ) -> List[Tuple[Point, int]]:
        """获取中立单位位置"""
        neutrals = []
        
        for sp in world_spores:
            if sp.teamId == 0:
                pt = _pos_to_point(sp.position)
                neutrals.append((pt, sp.biomass))
        
        return neutrals
    
    def _build_threat_map(
        self,
        enemy_biomass_at: Dict[Point, int],
        width: int,
        height: int
    ) -> Dict[Point, int]:
        """构建威胁地图
        
        威胁：
        - 敌人所在格子及相邻格子都有威胁值
        """
        threat_map: Dict[Point, int] = {}
        
        for ept, eb in enemy_biomass_at.items():
            # 敌人位置本身
            prev = threat_map.get(ept, 0)
            if eb > prev:
                threat_map[ept] = eb
            
            # 相邻格子
            for d in DIRS:
                nx, ny = ept.x + d.x, ept.y + d.y
                if _in_bounds(nx, ny, width, height):
                    npt = Point(nx, ny)
                    prev2 = threat_map.get(npt, 0)
                    if eb > prev2:
                        threat_map[npt] = eb
        
        return threat_map
    
    def _calculate_my_center(
        self,
        spawner_positions: List[Point],
        my_spores: List,
        width: int,
        height: int
    ) -> Point:
        """计算我方中心位置"""
        if spawner_positions:
            return spawner_positions[0]
        
        my_positions = [
            _pos_to_point(s.position) 
            for s in my_spores 
            if s.biomass >= 2
        ]
        
        if my_positions:
            avg_x = sum(p.x for p in my_positions) // len(my_positions)
            avg_y = sum(p.y for p in my_positions) // len(my_positions)
            return Point(avg_x, avg_y)
        
        return Point(width // 2, height // 2)
    
    def _update_position_history(
        self,
        my_spores: List
    ) -> None:
        """更新单位位置历史（用于检测循环）"""
        live_ids: Set[str] = set()
        
        for s in my_spores:
            live_ids.add(s.id)
            pt = _pos_to_point(s.position)
            dq = self._pos_hist_by_id.get(s.id)
            if dq is None:
                dq = deque(maxlen=4)
            dq.append(pt)
            self._pos_hist_by_id[s.id] = dq
        
        # 清理已死亡的 spores
        for sid in list(self._pos_hist_by_id.keys()):
            if sid not in live_ids:
                self._pos_hist_by_id.pop(sid, None)
    
    def _calculate_territory_metrics(
        self,
        width: int,
        height: int,
        ownership_grid: List[List[int]],
        nutrient_grid: List[List[int]],
        my_team_id: int,
        tick: int
    ) -> Tuple[int, int]:
        """计算领土指标
        
        返回：(my_tile_count, nutrient_generation)
        
        性能优化：每 5 tick 才做一次完整扫描
        """
        if tick <= 3 or (tick - self._last_full_scan_tick) >= 5:
            my_tile_count = 0
            nutrient_generation = 0
            
            for y in range(height):
                row_owner = ownership_grid[y]
                row_nut = nutrient_grid[y]
                for x in range(width):
                    if row_owner[x] == my_team_id:
                        my_tile_count += 1
                        nutrient_generation += row_nut[x]
            
            self._cached_my_tile_count = my_tile_count
            self._cached_nutrient_generation = nutrient_generation
            self._last_full_scan_tick = tick
        else:
            my_tile_count = self._cached_my_tile_count
            nutrient_generation = self._cached_nutrient_generation
        
        return my_tile_count, nutrient_generation
    
    def _min_enemy_dist(
        self,
        pt: Point,
        enemy_list: List[Tuple[Point, int]],
        sample_cap: int = 60
    ) -> int:
        """计算到最近敌人的距离"""
        if not enemy_list:
            return 999
        
        m = 999
        for ept, _ in enemy_list[:sample_cap]:
            d = _manhattan(pt, ept)
            if d < m:
                m = d
                if m == 0:
                    break
        return m
    
    def _adjacent_enemy_max(
        self,
        pt: Point,
        enemy_biomass_at: Dict[Point, int]
    ) -> int:
        """获取相邻格子的最大敌人生物量"""
        m = 0
        for d in DIRS:
            nx, ny = pt.x + d.x, pt.y + d.y
            npt = Point(nx, ny)
            m = max(m, enemy_biomass_at.get(npt, 0))
        return m


print("✅ Part 3 加载完成：主 Bot 类初始化和辅助方法")
print("📌 包含内容：")
print("   - Bot.__init__: 初始化所有系统")
print("   - 辅助方法：敌人检测、威胁地图、领土计算等")
print("📌 下一步：请告诉我继续 Part 4 (Spawner 建造核心逻辑)")

# ============================================================================
# 🤖 BOT V8 - Part 4: Spawner 建造核心逻辑 (修复版)
# ============================================================================
# 这部分是 Bot 类的方法，需要放在 class Bot: 内部

def _build_first_spawner(
    self,
    game_message,
    my_team,
    actionable_spores,
    spawner_pts,
    enemy_biomass_at,
    enemy_list,
    threat_map,
    tile_value_fn,
    tile_owner_fn,
    tile_biomass_fn,
    used_spores,
    builder_ids,
    actions
):
    """
    🌟 v8 核心改进：使用评分式选址建造第一个 Spawner
    
    关键变化：
    - 不再有"找不到完美点就等待"的问题
    - 使用评分系统选择"相对最优"的位置
    - 渐进式选择 builder
    """
    
    if not actionable_spores:
        return
    
    width = game_message.world.map.width
    height = game_message.world.map.height
    next_spawner_cost = my_team.nextSpawnerCost
    
    # === 1. 判断是否应该建造 ===
    should_build, min_needed, is_emergency = self.map_analyzer.should_build_first_spawner_v8(
        map_type=self._map_type,
        tick=self._tick_count,
        actionable_count=len(actionable_spores),
        max_biomass=max((s.biomass for s in actionable_spores), default=0),
        next_spawner_cost=next_spawner_cost,
        total_spore_count=len(my_team.spores),
        neutral_count=0,
        my_nutrients=my_team.nutrients
    )
    
    if not should_build:
        if self._tick_count % 10 == 0:
            print(f"⏳ [WAITING] Tick {self._tick_count}, Map: {self._map_type}, "
                  f"Actionable: {len(actionable_spores)}")
        return
    
    print(f"🏗️  [BUILDING] Tick {self._tick_count}, Map: {self._map_type}, "
          f"Min biomass: {min_needed}, Emergency: {is_emergency}")
    
    # === 2. 估计 builder 的生物量（用于评分）===
    if self._map_type == "blocked_small":
        hint_biomass = max(min_needed, 4)
    elif (self._total_spore_count or 3) <= 3:
        hint_biomass = max(min_needed, 3)
    else:
        hint_biomass = max(min_needed, 6)
    
    # === 3. 选择建造地点（评分式）===
    locked = self._planned_sites.get("first")
    
    # 检查已锁定的地点是否仍然有效
    if locked is not None:
        if locked in spawner_pts:
            locked = None
        elif tile_biomass_fn(locked) != 0 and tile_owner_fn(locked) != game_message.yourTeamId:
            locked = None
    
    # 需要重新选择地点
    if locked is None:
        candidate_pool = self._top_tiles_cache[:80]
        
        locked = self.site_selector.select_best_site(
            candidate_tiles=candidate_pool,
            tile_value_fn=tile_value_fn,
            tile_owner_fn=tile_owner_fn,
            tile_biomass_fn=tile_biomass_fn,
            threat_at_fn=lambda pt: threat_map.get(pt, 0),
            enemy_density_fn=lambda pt, radius=5: self.enemy_density_in_radius(pt, enemy_biomass_at, radius),
            min_enemy_dist_fn=lambda pt: self._min_enemy_dist(pt, enemy_list),
            adjacent_enemy_max_fn=lambda pt: self._adjacent_enemy_max(pt, enemy_biomass_at),
            builder_biomass=hint_biomass,
            my_team_id=game_message.yourTeamId,
            is_second_spawner=False,
            spawner_positions=spawner_pts,
            tick=self._tick_count,
            map_type=self._map_type,
            is_emergency=is_emergency
        )
        
        if locked is None and is_emergency and self._top_tiles_cache:
            print("⚠️  [EMERGENCY] No scored site, using highest nutrient tile")
            for pt in self._top_tiles_cache[:20]:
                if pt not in spawner_pts:
                    locked = pt
                    break
        
        if locked is not None:
            self._planned_sites["first"] = locked
            print(f"📍 [SITE] Selected spawner site at {locked} "
                  f"(value: {tile_value_fn(locked)})")
    
    if locked is None:
        print("🛑 [CANCEL] No valid site for first spawner")
        return
    
    # === 4. 选择 Builder（渐进式）===
    builder = self.builder_selector.select_builder_progressive(
        site=locked,
        actionable_spores=actionable_spores,
        next_spawner_cost=min_needed,
        avoid_ids=set(),
        used_spores=used_spores,
        threat_at_fn=lambda pt: threat_map.get(pt, 0),
        tick=self._tick_count,
        is_emergency=is_emergency
    )
    
    # 紧急模式：降低要求
    if builder is None and is_emergency:
        print("⚠️  [FALLBACK] No ideal builder, trying with lower biomass...")
        builder = self.builder_selector.select_builder_progressive(
            site=locked,
            actionable_spores=actionable_spores,
            next_spawner_cost=2,
            avoid_ids=set(),
            used_spores=used_spores,
            threat_at_fn=lambda pt: threat_map.get(pt, 0),
            tick=self._tick_count,
            is_emergency=True
        )
    
    # 最后手段：选最强的
    if builder is None and is_emergency and actionable_spores:
        print("🚨 [EMERGENCY FALLBACK] Using strongest available spore!")
        available = [s for s in actionable_spores if s.id not in used_spores]
        if available:
            builder = max(available, key=lambda s: s.biomass)
    
    if builder is None:
        print("❌ [ERROR] No builder found!")
        return
    
    # === 5. 执行建造动作 ===
    builder_ids.add(builder.id)
    self._builder_target_by_id[builder.id] = locked
    
    bpt = Point(builder.position.x, builder.position.y)
    
    if bpt == locked:
        if builder.biomass >= next_spawner_cost:
            actions.append(SporeCreateSpawnerAction(sporeId=builder.id))
            used_spores.add(builder.id)
            print(f"✅ [SPAWNER] Built at tick {self._tick_count}")
        else:
            print(f"⚠️  Builder at site but insufficient biomass")
    else:
        actions.append(
            SporeMoveToAction(
                sporeId=builder.id,
                position=Position(x=locked.x, y=locked.y)
            )
        )
        used_spores.add(builder.id)
        print(f"🚀 [MOVING] Builder heading to site")


def _build_second_spawner(
    self,
    game_message,
    my_team,
    actionable_spores,
    spawner_pts,
    enemy_biomass_at,
    enemy_list,
    threat_map,
    tile_value_fn,
    tile_owner_fn,
    tile_biomass_fn,
    used_spores,
    builder_ids,
    actions,
    my_tile_count,
    total_tiles
):
    """建造第二个 Spawner"""
    
    if len(my_team.spawners) != 1:
        return
    
    if not actionable_spores:
        return
    
    next_spawner_cost = my_team.nextSpawnerCost
    nutrients = my_team.nutrients
    control_rate = my_tile_count / total_tiles if total_tiles > 0 else 0
    spawner_age = self._tick_count - self._first_spawner_tick if self._first_spawner_tick else 0
    
    # === 判断是否应该建造 ===
    should_build = False
    
    if self._map_type == "large":
        should_build = (
            (control_rate > 0.12 and nutrients >= 15) or
            (nutrients >= 20 and spawner_age > 12) or
            (spawner_age > 25)
        )
    elif (self._total_spore_count or 3) <= 3:
        should_build = (
            (control_rate > 0.15 and nutrients >= 10) or
            (nutrients >= 15 and spawner_age > 10)
        )
    else:
        should_build = (
            (control_rate > 0.22 and nutrients >= 18) or
            (nutrients >= 25 and spawner_age > 15)
        )
    
    if not should_build:
        return
    
    print(f"🏗️  [SECOND SPAWNER] Attempting to build")
    
    min_needed2 = next_spawner_cost + 2
    hint_biomass2 = max(min_needed2, 6)
    
    # === 选择地点 ===
    locked2 = self._planned_sites.get("second")
    
    if locked2 is not None:
        if locked2 in spawner_pts:
            locked2 = None
        elif tile_biomass_fn(locked2) != 0 and tile_owner_fn(locked2) != game_message.yourTeamId:
            locked2 = None
    
    if locked2 is None:
        candidate_pool = self._top_tiles_cache[:80]
        
        locked2 = self.site_selector.select_best_site(
            candidate_tiles=candidate_pool,
            tile_value_fn=tile_value_fn,
            tile_owner_fn=tile_owner_fn,
            tile_biomass_fn=tile_biomass_fn,
            threat_at_fn=lambda pt: threat_map.get(pt, 0),
            enemy_density_fn=lambda pt, radius=5: self.enemy_density_in_radius(pt, enemy_biomass_at, radius),
            min_enemy_dist_fn=lambda pt: self._min_enemy_dist(pt, enemy_list),
            adjacent_enemy_max_fn=lambda pt: self._adjacent_enemy_max(pt, enemy_biomass_at),
            builder_biomass=hint_biomass2,
            my_team_id=game_message.yourTeamId,
            is_second_spawner=True,
            spawner_positions=spawner_pts,
            tick=self._tick_count,
            map_type=self._map_type,
            is_emergency=False
        )
        
        if locked2 is not None:
            self._planned_sites["second"] = locked2
    
    if locked2 is None:
        return
    
    # === 选择 Builder ===
    builder2 = self.builder_selector.select_builder_progressive(
        site=locked2,
        actionable_spores=actionable_spores,
        next_spawner_cost=min_needed2,
        avoid_ids=builder_ids,
        used_spores=used_spores,
        threat_at_fn=lambda pt: threat_map.get(pt, 0),
        tick=self._tick_count,
        is_emergency=False
    )
    
    if builder2 is None:
        return
    
    # === 执行建造 ===
    builder_ids.add(builder2.id)
    self._builder_target_by_id[builder2.id] = locked2
    
    bpt2 = Point(builder2.position.x, builder2.position.y)
    
    if bpt2 == locked2:
        actions.append(SporeCreateSpawnerAction(sporeId=builder2.id))
        used_spores.add(builder2.id)
        print(f"✅ [SECOND SPAWNER] Built at {locked2}")
    else:
        actions.append(
            SporeMoveToAction(
                sporeId=builder2.id,
                position=Position(x=locked2.x, y=locked2.y)
            )
        )
        used_spores.add(builder2.id)
        print(f"🚀 [SECOND SPAWNER] Builder moving")


print("✅ Part 4 修复版加载完成")
print("📌 这两个方法需要添加到 Bot 类内部（注意缩进）")
print("📌 准备好继续 Part 5 了吗？")
# ============================================================================
# 🤖 BOT V8 - Part 5: Spawner 生产 + 战斗/防御逻辑 (修复版)
# ============================================================================
# 这部分是 Bot 类的方法，需要放在 class Bot: 内部

def _manage_spawner_production(
    self,
    my_team,
    spawner_pts,
    threat_map,
    enemy_biomass_at,
    width,
    height,
    my_tile_count,
    nutrient_generation,
    is_stagnant,
    used_spawners,
    actions
):
    """
    管理 Spawner 生产
    
    策略：
    - 使用 v7 的激进生产算法
    - 根据威胁、停滞状态动态调整
    
    返回：剩余营养值
    """
    
    nutrients = my_team.nutrients
    
    # 营养储备
    if (self._total_spore_count or 3) <= 3:
        reserve_nutrients = 2
    else:
        reserve_nutrients = 3 if len(my_team.spawners) >= 2 else 4
    
    for spawner in my_team.spawners:
        if spawner.id in used_spawners:
            continue
        
        spt = Point(spawner.position.x, spawner.position.y)
        
        # 计算局部威胁
        local_threat = max(
            threat_map.get(spt, 0),
            self._adjacent_enemy_max(spt, enemy_biomass_at)
        )
        
        # 检查周围威胁
        for d in DIRS:
            nx, ny = spt.x + d.x, spt.y + d.y
            if _in_bounds(nx, ny, width, height):
                local_threat = max(local_threat, threat_map.get(Point(nx, ny), 0))
        
        # 🌟 使用激进生产策略
        desired = self.expansion_enhancer.aggressive_spawner_production(
            tick=self._tick_count,
            local_threat=local_threat,
            spore_count=len(my_team.spores),
            nutrients=nutrients,
            my_territory_count=my_tile_count,
            total_tiles=width * height,
            is_stagnant=is_stagnant,
            nutrient_generation=nutrient_generation
        )
        
        # 检查是否有足够营养
        if nutrients - desired < reserve_nutrients:
            continue
        
        if nutrients >= desired and desired >= 2:
            actions.append(
                SpawnerProduceSporeAction(
                    spawnerId=spawner.id,
                    biomass=desired
                )
            )
            used_spawners.add(spawner.id)
            nutrients -= desired
            
            if self._tick_count % 10 == 0:
                print(f"🏭 Spawner produced: biomass={desired}, remaining={nutrients}")
    
    return nutrients


def _find_combat_targets(
    self,
    actionable_spores,
    enemy_list,
    neutral_spores,
    builder_ids,
    used_spores,
    tile_value_fn,
    survival_mode
):
    """
    寻找软目标（可以轻松击败的敌人）
    
    返回：[(target_pt, profit, attacker_id, dist), ...]
    """
    
    if survival_mode:
        return []
    
    targets = []
    
    # === 敌方单位 ===
    for enemy_pt, enemy_bio in enemy_list:
        attackers = [
            s for s in actionable_spores
            if s.id not in builder_ids
            and s.id not in used_spores
            and s.biomass > enemy_bio + 1
        ]
        
        if attackers:
            nearest = min(
                attackers,
                key=lambda s: _manhattan(Point(s.position.x, s.position.y), enemy_pt)
            )
            dist = _manhattan(Point(nearest.position.x, nearest.position.y), enemy_pt)
            
            if dist <= 10:
                profit = tile_value_fn(enemy_pt) + enemy_bio
                targets.append((enemy_pt, profit, nearest.id, dist))
    
    # === 中立单位 ===
    for neutral_pt, neutral_bio in neutral_spores:
        attackers = [
            s for s in actionable_spores
            if s.id not in builder_ids
            and s.id not in used_spores
            and s.biomass > neutral_bio
        ]
        
        if attackers:
            nearest = min(
                attackers,
                key=lambda s: _manhattan(Point(s.position.x, s.position.y), neutral_pt)
            )
            dist = _manhattan(Point(nearest.position.x, nearest.position.y), neutral_pt)
            
            if dist <= 8:
                profit = tile_value_fn(neutral_pt) + neutral_bio // 2
                targets.append((neutral_pt, profit, nearest.id, dist))
    
    # 按收益/距离排序
    targets.sort(key=lambda t: t[1] / (t[3] + 1), reverse=True)
    return targets[:5]


def _assign_defenders(
    self,
    actionable_spores,
    spawner_pts,
    threat_map,
    enemy_biomass_at,
    builder_ids,
    hunter_ids,
    used_spores
):
    """
    为 Spawner 分配防御单位
    
    返回：defender_ids
    """
    
    defender_ids = set()
    
    if not spawner_pts or not actionable_spores:
        return defender_ids
    
    for spt in spawner_pts:
        direct_threat = threat_map.get(spt, 0)
        adjacent_threat = self._adjacent_enemy_max(spt, enemy_biomass_at)
        
        # 直接威胁
        if direct_threat > 0:
            remaining = [
                s for s in actionable_spores
                if s.id not in builder_ids
                and s.id not in hunter_ids
                and s.id not in used_spores
                and s.biomass >= direct_threat + 2
            ]
            
            if remaining:
                defender = min(
                    remaining,
                    key=lambda s: _manhattan(Point(s.position.x, s.position.y), spt)
                )
                defender_ids.add(defender.id)
                print(f"🛡️  Defender assigned (threat: {direct_threat})")
        
        # 相邻威胁
        elif adjacent_threat > 0:
            remaining = [
                s for s in actionable_spores
                if s.id not in builder_ids
                and s.id not in hunter_ids
                and s.id not in defender_ids
                and s.id not in used_spores
                and s.biomass >= 4
            ]
            
            if remaining and len(defender_ids) < len(spawner_pts):
                defender = min(
                    remaining,
                    key=lambda s: _manhattan(Point(s.position.x, s.position.y), spt)
                )
                defender_ids.add(defender.id)
    
    return defender_ids


def _attempt_splits(
    self,
    my_team,
    big_spores,
    enemy_biomass_at,
    threat_map,
    tile_value_fn,
    tile_owner_fn,
    tile_biomass_fn,
    width,
    height,
    my_team_id,
    used_spores,
    builder_ids,
    actions,
    survival_mode,
    time_left_fn
):
    """
    尝试 Split 操作（扩大单位数量）
    """
    
    if survival_mode:
        return
    
    if len(my_team.spores) >= 18:
        return
    
    if time_left_fn() <= 0.010:
        return
    
    split_candidates = sorted(big_spores, key=lambda s: s.biomass, reverse=True)[:3]
    
    for sp in split_candidates:
        if sp.id in used_spores or sp.id in builder_ids:
            continue
        
        pt = Point(sp.position.x, sp.position.y)
        best_dir = None
        best_score = -10**18
        
        for d in DIRS:
            nx, ny = pt.x + d.x, pt.y + d.y
            if not _in_bounds(nx, ny, width, height):
                continue
            
            npt = Point(nx, ny)
            
            # 目标格子必须是空的
            if tile_biomass_fn(npt) != 0:
                continue
            
            # 不能有强敌
            enemy_here = enemy_biomass_at.get(npt, 0)
            if enemy_here >= sp.biomass:
                continue
            
            if threat_map.get(npt, 0) >= sp.biomass:
                continue
            
            # 评分
            score = tile_value_fn(npt)
            
            if tile_owner_fn(npt) != my_team_id:
                score += 30
            
            score -= threat_map.get(npt, 0) * 40
            score -= self._adjacent_enemy_max(npt, enemy_biomass_at) * 30
            
            if score > best_score:
                best_score = score
                best_dir = d
        
        # 执行 Split
        if best_dir is not None and best_score >= 35:
            moving_biomass = sp.biomass // 2
            
            if 2 <= moving_biomass < sp.biomass:
                actions.append(
                    SporeSplitAction(
                        sporeId=sp.id,
                        biomassForMovingSpore=moving_biomass,
                        direction=best_dir
                    )
                )
                used_spores.add(sp.id)
                print(f"✂️  Split: {moving_biomass} moving, {sp.biomass - moving_biomass} staying")
                break


print("✅ Part 5 修复版加载完成")
print("📌 这些方法需要添加到 Bot 类内部（注意缩进）")
print("📌 准备好继续 Part 6（最后一部分）了吗？")
# ============================================================================
# 🤖 BOT V8 - Part 6: 主决策循环 (修复版 - 最终部分)
# ============================================================================
# 这部分是 Bot 类的方法，需要放在 class Bot: 内部

def _emergency_merge(
    self,
    actionable_sorted,
    my_spores,
    threat_map,
    enemy_biomass_at,
    tile_owner_fn,
    tile_biomass_fn,
    width,
    height,
    my_team_id,
    builder_ids,
    used_spores,
    actions,
    time_left_fn
):
    """紧急合并：让受威胁的小单位向附近友军靠拢"""
    
    if not actionable_sorted or time_left_fn() <= 0.005:
        return
    
    my_spore_pts = [(Point(s.position.x, s.position.y), s) for s in my_spores]
    
    def _step_towards(src, dst):
        """计算朝向目标的一步"""
        best = None
        best_cost = 10
        best_dist = _manhattan(src, dst)
        
        for d in DIRS:
            nx, ny = src.x + d.x, src.y + d.y
            if not _in_bounds(nx, ny, width, height):
                continue
            
            npt = Point(nx, ny)
            nd = _manhattan(npt, dst)
            
            if nd >= best_dist:
                continue
            
            cost = 0 if (tile_owner_fn(npt) == my_team_id and tile_biomass_fn(npt) >= 1) else 1
            
            if cost < best_cost:
                best_cost = cost
                best = d
        
        return best
    
    # 找到受威胁的单位
    endangered = []
    for s in actionable_sorted:
        if s.id in used_spores or s.id in builder_ids:
            continue
        
        pt = Point(s.position.x, s.position.y)
        if threat_map.get(pt, 0) > 0 or self._adjacent_enemy_max(pt, enemy_biomass_at) > 0 or s.biomass <= 2:
            endangered.append(s)
        
        if len(endangered) >= 3:
            break
    
    # 为受威胁单位寻找合并目标
    for s in endangered:
        if time_left_fn() <= 0.003 or s.id in used_spores or s.id in builder_ids:
            continue
        
        src = Point(s.position.x, s.position.y)
        best_buddy = None
        best_key = (10**9, -10**9)
        
        for bpt, b in my_spore_pts:
            if b.id == s.id or b.id in builder_ids:
                continue
            
            d = _manhattan(src, bpt)
            if d == 0 or d > 3:
                continue
            
            key = (d, -b.biomass)
            if key < best_key:
                best_key = key
                best_buddy = bpt
        
        if best_buddy is None:
            continue
        
        step = _step_towards(src, best_buddy)
        if step is None:
            continue
        
        npt = Point(src.x + step.x, src.y + step.y)
        if threat_map.get(npt, 0) >= s.biomass:
            continue
        
        move_cost = 0 if (tile_owner_fn(npt) == my_team_id and tile_biomass_fn(npt) >= 1) else 1
        if s.biomass == 2 and move_cost == 1:
            continue
        
        actions.append(SporeMoveAction(sporeId=s.id, direction=step))
        used_spores.add(s.id)


def _manage_spore_movement(
    self,
    actionable_sorted,
    defender_ids,
    hunter_ids,
    builder_ids,
    my_center,
    lane_targets,
    threat_map,
    enemy_biomass_at,
    my_biomass_at,
    tile_value_fn,
    tile_owner_fn,
    tile_biomass_fn,
    width,
    height,
    my_team_id,
    penalty_2biomass,
    is_stagnant,
    survival_mode,
    spawner_pts,
    used_spores,
    actions,
    time_left_fn
):
    """管理所有 Spore 的移动"""
    
    defend_center = spawner_pts[0] if spawner_pts else None
    
    for sp in actionable_sorted:
        if time_left_fn() <= 0.002:
            break
        
        if sp.id in used_spores or sp.id in builder_ids:
            continue
        
        is_defender = sp.id in defender_ids
        is_hunter = sp.id in hunter_ids
        
        sp_pt = Point(sp.position.x, sp.position.y)
        
        # Lane 目标
        lane_target = None
        if not is_defender and not is_hunter and (self._map_type in ("medium", "large") or is_stagnant):
            lane = self.lane_planner.assign_lane(sp.id, sp_pt, my_center, self._tick_count)
            lane_target = lane_targets.get(lane)
        
        # 上一格（防抖）
        hist = self._pos_hist_by_id.get(sp.id)
        prev_pt = None
        if hist is not None and len(hist) >= 2:
            prev_pt = hist[-2]
        
        # Pioneer 模式
        is_pioneer = False
        pioneer_2_cost_penalty = penalty_2biomass
        
        if (
            sp.biomass == 2
            and lane_target is not None
            and not survival_mode
            and not is_defender
            and not is_hunter
            and (len(spawner_pts) >= 1 or len(actionable_sorted) >= 3)
            and _manhattan(sp_pt, lane_target) >= 5
        ):
            is_pioneer = True
            pioneer_2_cost_penalty = max(20, penalty_2biomass // 3)
        
        # Defender 静止条件
        if is_defender and defend_center is not None:
            if _manhattan(sp_pt, defend_center) <= 2:
                if threat_map.get(sp_pt, 0) == 0 and self._adjacent_enemy_max(sp_pt, enemy_biomass_at) == 0:
                    continue
        
        # 移动决策：贪心搜索
        best_dir = None
        best_score = -10**18
        prefer_zero_cost = (sp.biomass == 2 and not is_pioneer)
        passes = (0, 1) if prefer_zero_cost else (1,)
        
        for pass_id in passes:
            for d in DIRS:
                nx, ny = sp_pt.x + d.x, sp_pt.y + d.y
                if not _in_bounds(nx, ny, width, height):
                    continue
                
                npt = Point(nx, ny)
                
                if threat_map.get(npt, 0) >= sp.biomass:
                    continue
                
                enemy_here = enemy_biomass_at.get(npt, 0)
                if enemy_here > 0 and sp.biomass <= enemy_here:
                    continue
                
                move_cost = 0 if (tile_owner_fn(npt) == my_team_id and tile_biomass_fn(npt) >= 1) else 1
                if pass_id == 0 and move_cost != 0:
                    continue
                
                score = 0
                
                # 角色评分
                if is_hunter:
                    if enemy_here > 0:
                        score += 450 + (sp.biomass - enemy_here) * 5
                    score += tile_value_fn(npt)
                
                elif is_defender and defend_center is not None:
                    score -= _manhattan(npt, defend_center) * 35
                    score += (30 if tile_owner_fn(npt) == my_team_id else 0)
                    score += tile_value_fn(npt) // 2
                
                else:
                    # 普通扩张模式
                    score += tile_value_fn(npt) * 4
                    
                    if tile_owner_fn(npt) != my_team_id:
                        score += 120
                    
                    # Lane 方向奖励
                    if lane_target is not None:
                        base = 18 if not is_stagnant else 26
                        d0 = _manhattan(sp_pt, lane_target)
                        d1 = _manhattan(npt, lane_target)
                        score += (d0 - d1) * base
                        
                        outward = _manhattan(npt, my_center) - _manhattan(sp_pt, my_center)
                        score += outward * (12 if not is_stagnant else 18)
                    
                    # 避免局部循环
                    if self._map_type in ("medium", "large") and tile_owner_fn(npt) == my_team_id and _manhattan(npt, my_center) <= 3:
                        score -= 90
                
                # 免费路径
                if move_cost == 0:
                    score += 15
                
                # 战斗奖励
                if enemy_here > 0 and sp.biomass > enemy_here:
                    score += 450 + (sp.biomass - enemy_here) * 5
                
                # 威胁惩罚
                score -= threat_map.get(npt, 0) * 30
                score -= self._adjacent_enemy_max(npt, enemy_biomass_at) * 20
                
                # 2-biomass 惩罚
                if sp.biomass == 2 and move_cost == 1:
                    score -= pioneer_2_cost_penalty
                
                # 回头路惩罚
                if prev_pt is not None and npt == prev_pt:
                    if threat_map.get(sp_pt, 0) == 0 and self._adjacent_enemy_max(sp_pt, enemy_biomass_at) == 0:
                        score -= 220
                
                # 避免堆积
                if my_biomass_at.get(npt, 0) > 0:
                    score -= 20
                
                if score > best_score:
                    best_score = score
                    best_dir = d
            
            if best_dir is not None:
                break
        
        # 执行移动
        if best_dir is not None:
            actions.append(SporeMoveAction(sporeId=sp.id, direction=best_dir))
            used_spores.add(sp.id)
        else:
            # 兜底：MoveTo
            if is_defender and defend_center is not None:
                actions.append(
                    SporeMoveToAction(
                        sporeId=sp.id,
                        position=Position(x=defend_center.x, y=defend_center.y)
                    )
                )
                used_spores.add(sp.id)
            elif lane_target is not None and threat_map.get(lane_target, 0) < sp.biomass:
                actions.append(
                    SporeMoveToAction(
                        sporeId=sp.id,
                        position=Position(x=lane_target.x, y=lane_target.y)
                    )
                )
                used_spores.add(sp.id)


def get_next_move(self, game_message):
    """🌟 v8 主决策循环 - 整合所有系统"""
    
    actions = []
    self._tick_count = game_message.tick
    
    # 时间预算
    TICK_BUDGET_SEC = 0.085
    tick_start = time.perf_counter()
    
    def time_left():
        return TICK_BUDGET_SEC - (time.perf_counter() - tick_start)
    
    def out_of_time():
        return time_left() <= 0.0
    
    # 错误日志
    if game_message.lastTickErrors:
        print(f"⚠️  Tick {self._tick_count} Errors:", game_message.lastTickErrors)
    
    # 基础信息
    world = game_message.world
    width, height = world.map.width, world.map.height
    my_team_id = game_message.yourTeamId
    my_team = world.teamInfos[my_team_id]
    
    nutrient_grid = world.map.nutrientGrid
    ownership_grid = world.ownershipGrid
    biomass_grid = world.biomassGrid
    
    # 初始化统计
    if self._total_spore_count is None and self._tick_count == 1:
        self._total_spore_count = len(my_team.spores)
        self._initial_spore_count = len([s for s in my_team.spores if s.biomass >= 2])
    
    # 缓存营养地块
    self._ensure_top_tiles_cache(width, height, nutrient_grid)
    
    def tile_value(pt):
        return nutrient_grid[pt.y][pt.x]
    
    def tile_owner(pt):
        return ownership_grid[pt.y][pt.x]
    
    def tile_biomass(pt):
        return biomass_grid[pt.y][pt.x]
    
    # 索引单位
    enemy_biomass_at, enemy_list = self._get_enemy_positions(world.spores, my_team_id)
    neutral_spores = self._get_neutral_positions(world.spores)
    
    my_biomass_at = {}
    for sp in my_team.spores:
        pt = Point(sp.position.x, sp.position.y)
        my_biomass_at[pt] = max(my_biomass_at.get(pt, 0), sp.biomass)
    
    # 地图分析
    if not self._map_type:
        self._map_type = self.map_analyzer.analyze_map_type(
            width=width,
            height=height,
            neutral_spores=neutral_spores,
            my_spore_count=len(my_team.spores),
            nutrient_grid=nutrient_grid
        )
    
    # 计算领土指标
    my_tile_count, nutrient_generation = self._calculate_territory_metrics(
        width, height, ownership_grid, nutrient_grid, my_team_id, self._tick_count
    )
    
    is_stagnant = self.expansion_enhancer.detect_expansion_stagnation(
        current_territory=my_tile_count,
        tick=self._tick_count
    )
    
    if is_stagnant and self._tick_count % 20 == 0:
        print(f"🚨 [STAGNANT] Territory: {my_tile_count}, Gen: {nutrient_generation}")
    
    # 威胁地图
    threat_map = self._build_threat_map(enemy_biomass_at, width, height)
    
    # 计算中心
    spawner_pts = [Point(s.position.x, s.position.y) for s in my_team.spawners]
    my_center = self._calculate_my_center(spawner_pts, my_team.spores, width, height)
    
    # Lane 目标
    lane_targets = {}
    if self._map_type in ("medium", "large") or is_stagnant:
        lane_targets = self.lane_planner.pick_lane_targets(
            top_tiles=self._top_tiles_cache,
            center=my_center,
            width=width,
            height=height,
            tick=self._tick_count,
            is_stagnant=is_stagnant,
            tile_value_fn=tile_value,
            tile_owner_fn=tile_owner,
            threat_at_fn=lambda pt: threat_map.get(pt, 0),
            my_team_id=my_team_id
        )
    
    # 动态惩罚
    penalty_2biomass = self.expansion_enhancer.improved_2biomass_penalty(
        map_type=self._map_type,
        tick=self._tick_count,
        my_tile_count=my_tile_count,
        total_tiles=width * height,
        spawner_count=len(my_team.spawners),
        is_stagnant=is_stagnant,
        nutrient_generation=nutrient_generation
    )
    
    # 行动追踪
    used_spores = set()
    used_spawners = set()
    builder_ids = set()
    
    # 分类单位
    actionable_spores = [s for s in my_team.spores if s.biomass >= 2]
    big_spores = [s for s in my_team.spores if s.biomass >= 6]
    
    self._update_position_history(my_team.spores)
    
    survival_mode = (
        (len(my_team.spawners) == 0 and len(my_team.spores) <= 2) or
        (len(my_team.spawners) > 0 and len(my_team.spores) <= 1)
    )
    
    if len(my_team.spawners) > 0 and self._first_spawner_tick is None:
        self._first_spawner_tick = self._tick_count
    
    # ===== 阶段 1: 建造 Spawner =====
    if not out_of_time() and len(my_team.spawners) == 0:
        self._build_first_spawner(
            game_message, my_team, actionable_spores, spawner_pts,
            enemy_biomass_at, enemy_list, threat_map,
            tile_value, tile_owner, tile_biomass,
            used_spores, builder_ids, actions
        )
    
    if not out_of_time() and len(my_team.spawners) == 1:
        self._build_second_spawner(
            game_message, my_team, actionable_spores, spawner_pts,
            enemy_biomass_at, enemy_list, threat_map,
            tile_value, tile_owner, tile_biomass,
            used_spores, builder_ids, actions,
            my_tile_count, width * height
        )
    
    if out_of_time():
        return actions
    
    # ===== 阶段 2: Spawner 生产 =====
    self._manage_spawner_production(
        my_team, spawner_pts, threat_map, enemy_biomass_at,
        width, height, my_tile_count, nutrient_generation,
        is_stagnant, used_spawners, actions
    )
    
    if out_of_time():
        return actions
    
    # ===== 阶段 3: 战斗和防御 =====
    soft_targets = self._find_combat_targets(
        actionable_spores, enemy_list, neutral_spores,
        builder_ids, used_spores, tile_value, survival_mode
    )
    
    hunter_ids = set()
    for target_pt, profit, attacker_id, dist in soft_targets:
        if out_of_time() or attacker_id in used_spores:
            break
        if profit > 20:
            hunter_ids.add(attacker_id)
            actions.append(
                SporeMoveToAction(
                    sporeId=attacker_id,
                    position=Position(x=target_pt.x, y=target_pt.y)
                )
            )
            used_spores.add(attacker_id)
    
    defender_ids = self._assign_defenders(
        actionable_spores, spawner_pts, threat_map, enemy_biomass_at,
        builder_ids, hunter_ids, used_spores
    )
    
    if out_of_time():
        return actions
    
    # ===== 阶段 4: Split =====
    self._attempt_splits(
        my_team, big_spores, enemy_biomass_at, threat_map,
        tile_value, tile_owner, tile_biomass,
        width, height, my_team_id,
        used_spores, builder_ids, actions,
        survival_mode, time_left
    )
    
    if out_of_time():
        return actions
    
    # ===== 阶段 5: 紧急合并 =====
    actionable_sorted = sorted(actionable_spores, key=lambda s: s.biomass, reverse=True)
    
    self._emergency_merge(
        actionable_sorted, my_team.spores, threat_map, enemy_biomass_at,
        tile_owner, tile_biomass, width, height, my_team_id,
        builder_ids, used_spores, actions, time_left
    )
    
    if out_of_time():
        return actions
    
    # ===== 阶段 6: 移动 =====
    self._manage_spore_movement(
        actionable_sorted, defender_ids, hunter_ids, builder_ids,
        my_center, lane_targets, threat_map, enemy_biomass_at, my_biomass_at,
        tile_value, tile_owner, tile_biomass,
        width, height, my_team_id, penalty_2biomass, is_stagnant, survival_mode,
        spawner_pts, used_spores, actions, time_left
    )
    
    return actions


print("=" * 70)
print("✅ Bot v8.0 Part 6 (最终部分) 修复版加载完成！")
print("=" * 70)
print()
print("📋 完整代码结构：")
print("   Part 1: 导入 + 基础类 + LanePlanner + MapAnalysis")
print("   Part 2: ExpansionEnhancement + 评分式选址")
print("   Part 3: Bot 类初始化 + 辅助方法")
print("   Part 4: _build_first_spawner + _build_second_spawner")
print("   Part 5: 生产 + 战斗 + 防御 + Split")
print("   Part 6: 紧急合并 + 移动 + get_next_move (主循环)")
print()
print("🔧 组装提示：")
print("   - Part 1-2: 直接复制（独立的类）")
print("   - Part 3-6: 放在 class Bot: 内部（注意缩进）")
print()
print("🚀 祝比赛顺利！")

# ===========================
# Bind standalone functions into Bot methods
# ===========================
Bot._build_first_spawner = _build_first_spawner
Bot._build_second_spawner = _build_second_spawner

Bot._manage_spawner_production = _manage_spawner_production
Bot._find_combat_targets = _find_combat_targets
Bot._assign_defenders = _assign_defenders
Bot._attempt_splits = _attempt_splits

Bot._emergency_merge = _emergency_merge
Bot._manage_spore_movement = _manage_spore_movement

Bot.get_next_move = get_next_move
