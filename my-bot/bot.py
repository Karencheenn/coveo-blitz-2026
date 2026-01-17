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
# 🧭 LANE / QUADRANT EXPANSION PLANNER
# ============================================================================
class LanePlanner:
    """大地图扩张：用 4 象限 / 车道 (lane) 锁定目标，避免来回循环。

    设计目标：
    - 给普通扩张单位一个相对持久的方向目标（锁定 8~15 tick）
    - 每个象限至少有 1 个扩张者，减少全员抢同一块地造成的堆叠/回撤
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

        # 给每个 lane 选一个“远 + 高价值 + 相对安全”的点
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
# 🗺️ MAP ANALYSIS CLASS
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
    
    def should_build_first_spawner_v2(
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
        """返回: (should_build, min_biomass_needed, is_emergency)

        v7 目标：减少“等完美条件”的停滞。
        - 小图仍谨慎，但不再把 tick/actionable 设成硬门槛
        - 中/大图尽早启动 spawner，引擎先转起来，再谈完美选址
        """

        # === 紧迫度（越高越应该立刻建）===
        # 时间压力
        urgency = tick
        # 行动力越少越危险（尤其没 spawner 时）
        if actionable_count <= 1:
            urgency += 12
        elif actionable_count <= 2:
            urgency += 6
        # 如果最强 biomass 很大，说明有能力建/推进
        urgency += max(0, (max_biomass - 4))

        # blocked_small：neutral 堵路时，允许稍晚，但不要无限等
        if map_type == "blocked_small":
            urgency -= 4
            if neutral_count >= 6:
                urgency -= 3

        # 触发 emergency 的阈值：20 tick 后必须开始放宽
        is_emergency = tick >= 20

        # === 分阶段放宽 ===
        # 严格：tick<=8
        # 中等：9-15
        # 宽松：16-25
        # 强制：26+
        if urgency >= 26:
            # 强制：只要有行动单位，就必须尝试建
            return (actionable_count >= 1, max(2, next_spawner_cost), True)

        if urgency >= 16:
            # 宽松：允许 min_needed 接近 cost
            if actionable_count >= 1 and max_biomass >= max(2, next_spawner_cost):
                return (True, max(2, next_spawner_cost), is_emergency)

        # 中等：一般期望在 5~12 tick 就能启动
        if tick >= 3 and actionable_count >= 1 and max_biomass >= next_spawner_cost:
            # 中/大图更激进：min_needed = cost 或 cost+1
            if map_type in ("medium", "large"):
                return (True, max(2, next_spawner_cost), False)
            # 小图/堵图：略保守一点
            return (True, max(3, next_spawner_cost + 1), False)

        # 兜底：迟迟不满足时，15 tick 后启动
        if tick >= 15 and actionable_count >= 1 and max_biomass >= max(2, next_spawner_cost):
            return (True, max(2, next_spawner_cost), True)

        return (False, 0, False)


# ============================================================================
# 🌍 EXPANSION ENHANCEMENT CLASS
# ============================================================================
class ExpansionEnhancement:
    """扩张增强系统 - 解决 nutrient generation 停滞问题"""
    
    def __init__(self):
        self._last_territory_count: int = 0
        self._stagnation_ticks: int = 0

        # 为了避免大地图“局部循环”，我们引入一个更敏感的停滞检测：
        # - territory 长时间几乎不增长
        # - 或者扩张速度明显低于期望
        # 注意：这里仍然保持轻量级，只在 interval 触发。
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
# 🤖 MAIN BOT CLASS
# ============================================================================
class Bot:
    """Coveo Blitz Bot v7.0
    
    主要改进：
    ✅ 智能地图分类
    ✅ 扩张停滞检测
    ✅ 动态 2-biomass 策略
    ✅ 激进生产模式
    ✅ Lane(象限)扩张：减少大地图局部循环
    ✅ Progressive Spawner：不再等“完美条件”才建
    ✅ 轻量缓存：降低每 tick 全图扫描
    """

    def __init__(self) -> None:
        print("🚀 Initializing Bot v7.0 (Lane Expansion + Progressive Spawner)")

        # 分析系统
        self.map_analyzer = ImprovedMapAnalysis()
        self.expansion_enhancer = ExpansionEnhancement()
        self.lane_planner = LanePlanner()
        self._map_type: str = ""

        # Cache
        self._cached_map_key: Optional[Tuple[int, int]] = None
        self._top_tiles_cache: List[Point] = []
        self._top_tiles_k: int = 400

        # Spawner planning
        self._planned_sites: Dict[str, Point] = {}
        self._builder_target_by_id: Dict[str, Point] = {}

        # Timing / stats
        self._first_spawner_tick: Optional[int] = None
        self._tick_count: int = 0
        self._initial_spore_count: Optional[int] = None
        self._total_spore_count: Optional[int] = None

        # 大地图循环检测：记录每只 spore 的最近位置
        self._pos_hist_by_id: Dict[str, Deque[Point]] = {}

        # 轻量缓存：避免每 tick O(W*H) 全图扫描
        self._cached_my_tile_count: int = 0
        self._cached_nutrient_generation: int = 0
        self._last_full_scan_tick: int = 0
# ----------------------
    # Helper methods
    # ----------------------
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

    # ----------------------
    # Core decision loop
    # ----------------------
    def get_next_move(self, game_message: TeamGameState) -> List[Action]:
        actions: List[Action] = []
        self._tick_count = game_message.tick

        # Tick 时间预算
        TICK_BUDGET_SEC = 0.085
        tick_start = time.perf_counter()

        def time_left() -> float:
            return TICK_BUDGET_SEC - (time.perf_counter() - tick_start)

        def out_of_time() -> bool:
            return time_left() <= 0.0

        if game_message.lastTickErrors:
            print(f"⚠️  Tick {self._tick_count} Errors:", game_message.lastTickErrors)

        world = game_message.world
        width, height = world.map.width, world.map.height
        my_team_id = game_message.yourTeamId

        my_team: TeamInfo = world.teamInfos[my_team_id]
        nutrients: int = my_team.nutrients
        next_spawner_cost: int = my_team.nextSpawnerCost

        nutrient_grid = world.map.nutrientGrid
        ownership_grid = world.ownershipGrid
        biomass_grid = world.biomassGrid

        # 初次记录 spore 数量
        if self._total_spore_count is None and self._tick_count == 1:
            self._total_spore_count = len(my_team.spores)
            self._initial_spore_count = len([s for s in my_team.spores if s.biomass >= 2])
            print(
                f"📊 [INIT] Total spores: {self._total_spore_count}, "
                f"Actionable: {self._initial_spore_count}"
            )

        # ---------- Nutrient cache ----------
        def _ensure_top_tiles_cache() -> None:
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

        _ensure_top_tiles_cache()

        def tile_value(pt: Point) -> int:
            return nutrient_grid[pt.y][pt.x]

        def tile_owner(pt: Point) -> int:
            return ownership_grid[pt.y][pt.x]

        def tile_biomass(pt: Point) -> int:
            return biomass_grid[pt.y][pt.x]

        # ---------- Index units ----------
        enemy_biomass_at: Dict[Point, int] = {}
        my_biomass_at: Dict[Point, int] = {}
        neutral_spores: List[Tuple[Point, int]] = []

        for sp in world.spores:
            pt = _pos_to_point(sp.position)
            if sp.teamId == my_team_id:
                my_biomass_at[pt] = max(my_biomass_at.get(pt, 0), sp.biomass)
            elif sp.teamId == 0:
                neutral_spores.append((pt, sp.biomass))
            else:
                enemy_biomass_at[pt] = max(enemy_biomass_at.get(pt, 0), sp.biomass)

        enemy_list: List[Tuple[Point, int]] = list(enemy_biomass_at.items())
        enemy_list.sort(key=lambda t: t[1], reverse=True)

        def adjacent_enemy_max(pt: Point) -> int:
            m = 0
            for d in DIRS:
                nx, ny = pt.x + d.x, pt.y + d.y
                if _in_bounds(nx, ny, width, height):
                    m = max(m, enemy_biomass_at.get(Point(nx, ny), 0))
            return m

        # ---------- 🗺️ Map Type Analysis (First tick only) ----------
        if not self._map_type:
            self._map_type = self.map_analyzer.analyze_map_type(
                width=width,
                height=height,
                neutral_spores=neutral_spores,
                my_spore_count=len(my_team.spores),
                nutrient_grid=nutrient_grid
            )

        # ---------- 🌍 Calculate expansion metrics ----------
        # 性能：全图扫描 O(W*H) 代价不小，尤其大地图。
        # 我们每 5 tick 才做一次完整扫描，其余 tick 复用缓存。
        if self._tick_count <= 3 or (self._tick_count - self._last_full_scan_tick) >= 5:
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
            self._last_full_scan_tick = self._tick_count
        else:
            my_tile_count = self._cached_my_tile_count
            nutrient_generation = self._cached_nutrient_generation

        # 检测停滞
        is_stagnant = self.expansion_enhancer.detect_expansion_stagnation(
            current_territory=my_tile_count,
            tick=self._tick_count
        )

        if is_stagnant and self._tick_count % 20 == 0:
            print(f"🚨 [STAGNANT] Tick {self._tick_count}: Territory growth stalled! "
                  f"(Territory: {my_tile_count}, Gen: {nutrient_generation})")

        # 计算我方中心
        spawner_pts: List[Point] = [_pos_to_point(s.position) for s in my_team.spawners]
        if spawner_pts:
            my_center = spawner_pts[0]
        else:
            my_positions = [_pos_to_point(s.position) for s in my_team.spores if s.biomass >= 2]
            if my_positions:
                avg_x = sum(p.x for p in my_positions) // len(my_positions)
                avg_y = sum(p.y for p in my_positions) // len(my_positions)
                my_center = Point(avg_x, avg_y)
            else:
                my_center = Point(width // 2, height // 2)

        # ---------- Threat map ----------
        threat_map: Dict[Point, int] = {}
        for ept, eb in enemy_biomass_at.items():
            prev = threat_map.get(ept, 0)
            if eb > prev:
                threat_map[ept] = eb
            for d in DIRS:
                nx, ny = ept.x + d.x, ept.y + d.y
                if _in_bounds(nx, ny, width, height):
                    npt = Point(nx, ny)
                    prev2 = threat_map.get(npt, 0)
                    if eb > prev2:
                        threat_map[npt] = eb

        def threat_at(pt: Point) -> int:
            return threat_map.get(pt, 0)

        # ---------- 🧭 Lane targets (用于大地图扩张方向锁定) ----------
        # 注意：我们只在 medium/large 或停滞时启用 lane 目标。
        lane_targets: Dict[str, Point] = {}
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
                threat_at_fn=threat_at,
                my_team_id=my_team_id,
            )

        # ---------- 🎯 Calculate dynamic 2-biomass penalty ----------
        penalty_2biomass = self.expansion_enhancer.improved_2biomass_penalty(
            map_type=self._map_type,
            tick=self._tick_count,
            my_tile_count=my_tile_count,
            total_tiles=width * height,
            spawner_count=len(my_team.spawners),
            is_stagnant=is_stagnant,
            nutrient_generation=nutrient_generation
        )

        # ---------- One-action-per-unit guards ----------
        used_spores: Set[str] = set()
        used_spawners: Set[str] = set()

        def add_action_for_spore(spore_id: str, action: Action) -> None:
            if spore_id in used_spores:
                return
            used_spores.add(spore_id)
            actions.append(action)

        def add_action_for_spawner(spawner_id: str, action: Action) -> None:
            if spawner_id in used_spawners:
                return
            used_spawners.add(spawner_id)
            actions.append(action)

        # ---------- Partition units ----------
        actionable_spores: List[Spore] = [s for s in my_team.spores if s.biomass >= 2]
        big_spores: List[Spore] = [s for s in my_team.spores if s.biomass >= 6]

        # ---------- 🔁 循环检测：更新位置历史 ----------
        # 目的：大地图常见问题是“左右来回抖动”。我们记录最近 4 个位置，用于惩罚回头路。
        live_ids: Set[str] = set()
        for s in my_team.spores:
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

        total_spores_now = len(my_team.spores)
        survival_mode = (
            (len(my_team.spawners) == 0 and total_spores_now <= 2)
            or (len(my_team.spawners) > 0 and total_spores_now <= 1)
        )

        if len(my_team.spawners) > 0 and self._first_spawner_tick is None:
            self._first_spawner_tick = self._tick_count
            print(f"🏗️  [SUCCESS] First spawner built at tick {self._tick_count}")

        if self._tick_count == 20 and len(my_team.spawners) == 0:
            print(f"⚠️  [TICK 20 WARNING] Still no spawner!")
            print(f"   Map type: {self._map_type}")
            print(f"   Nutrients: {nutrients}, Next cost: {next_spawner_cost}")
            print(f"   Actionable spores: {len(actionable_spores)}")
            print(f"   Neutral count: {len(neutral_spores)}")

        # ---------- Map control ----------
        def get_control_rate() -> float:
            total_tiles = width * height
            return my_tile_count / total_tiles if total_tiles > 0 else 0.0

        # ---------- Spawner placement helpers ----------
        SITE_POOL_K = 60
        ENEMY_DIST_CAP = 40
        ENEMY_SAMPLE_CAP = 60
        SITE_SAFETY_MARGIN = 1

        def min_enemy_dist(pt: Point) -> int:
            if not enemy_list:
                return 999
            m = 999
            for ept, _ in enemy_list[:ENEMY_SAMPLE_CAP]:
                d = _manhattan(pt, ept)
                if d < m:
                    m = d
                    if m == 0:
                        break
            return m

        def is_safe_site_for_builder(pt: Point, builder_biomass: int, lenient: bool = False) -> bool:
            if builder_biomass <= 0:
                return False

            if lenient or (self._total_spore_count or 3) <= 3:
                margin = 0
            else:
                margin = SITE_SAFETY_MARGIN

            if threat_at(pt) >= builder_biomass - margin:
                return False

            # 大地图不要把“敌人密度”当作硬过滤，否则很容易找不到点而停滞。
            # 仅在小/中图且敌人较多时启用，并且阈值随 tick 放宽。
            if not lenient and self._map_type in ("open_rush", "blocked_small", "medium") and len(enemy_biomass_at) >= 6:
                radius = 5 if self._map_type != "blocked_small" else 4
                density = self.enemy_density_in_radius(pt, enemy_biomass_at, radius=radius)
                # 越往后越宽松（允许更高密度）
                dens_th = 6 if self._tick_count <= 12 else (8 if self._tick_count <= 20 else 10)
                if density >= dens_th:
                    return False

            if lenient:
                return True

            for d in DIRS:
                nx, ny = pt.x + d.x, pt.y + d.y
                if _in_bounds(nx, ny, width, height):
                    npt = Point(nx, ny)
                    eb = enemy_biomass_at.get(npt, 0)
                    if eb > 0 and builder_biomass <= eb:
                        return False
                    if threat_at(npt) >= builder_biomass - margin:
                        return False
            return True

        def site_still_valid(site: Point, builder_biomass: int) -> bool:
            if site in spawner_pts:
                return False
            if tile_biomass(site) != 0 and tile_owner(site) != my_team_id:
                return False
            return is_safe_site_for_builder(site, builder_biomass)

        def score_site(pt: Point, is_second: bool) -> int:
            tv = tile_value(pt)
            owner = tile_owner(pt)
            dist_enemy = min(min_enemy_dist(pt), ENEMY_DIST_CAP)

            score = tv * 3
            score += 40 if owner == my_team_id else 10
            score += dist_enemy * 8

            if is_second and spawner_pts:
                score += _manhattan(pt, spawner_pts[0]) * 6
            return score

        def pick_best_site(is_second: bool, builder_biomass_hint: int, lenient: bool = False) -> Optional[Point]:
            pool = self._top_tiles_cache[:SITE_POOL_K]
            best: Optional[Point] = None
            best_score = -10**18

            for pt in pool:
                if pt in spawner_pts:
                    continue
                if not is_safe_site_for_builder(pt, builder_biomass_hint, lenient=lenient):
                    continue
                s = score_site(pt, is_second=is_second)
                if s > best_score:
                    best_score = s
                    best = pt
            return best

        def pick_builder_spore(
            site: Point,
            min_biomass_needed: int,
            avoid_ids: Set[str],
            lenient: bool = False,
        ) -> Optional[Spore]:
            best: Optional[Spore] = None
            best_score = 10**18

            for sp in actionable_spores:
                if sp.id in avoid_ids or sp.id in used_spores:
                    continue
                if sp.biomass < min_biomass_needed:
                    continue

                if not lenient and (self._total_spore_count or 3) > 3:
                    if threat_at(_pos_to_point(sp.position)) >= sp.biomass:
                        continue

                d = _manhattan(_pos_to_point(sp.position), site)
                score = d * 100 - sp.biomass * 5
                if score < best_score:
                    best_score = score
                    best = sp

            return best

        builder_ids: Set[str] = set()
# ---------- 🏗️ FIRST SPAWNER LOGIC ----------
        if not out_of_time() and len(my_team.spawners) == 0 and actionable_spores:
            should_build, min_needed, is_emergency = self.map_analyzer.should_build_first_spawner_v2(
                map_type=self._map_type,
                tick=self._tick_count,
                actionable_count=len(actionable_spores),
                max_biomass=max((s.biomass for s in actionable_spores), default=0),
                next_spawner_cost=next_spawner_cost,
                total_spore_count=len(my_team.spores),
                neutral_count=len(neutral_spores),
                my_nutrients=nutrients
            )

            if should_build:
                print(f"🏗️  [BUILDING] Tick {self._tick_count}, Map: {self._map_type}, "
                      f"Min biomass: {min_needed}, Emergency: {is_emergency}")

                if self._map_type == "blocked_small":
                    hint_biomass = max(min_needed, 4)
                elif (self._total_spore_count or 3) <= 3:
                    hint_biomass = max(min_needed, 3)
                else:
                    hint_biomass = max(min_needed, 6)

                locked = self._planned_sites.get("first")
                if locked is None or not site_still_valid(locked, hint_biomass):
                    locked = pick_best_site(is_second=False, builder_biomass_hint=hint_biomass, lenient=is_emergency)
                    if locked is None and self._top_tiles_cache:
                        print("⚠️  [FALLBACK] No safe site found, trying lenient mode...")
                        locked = pick_best_site(is_second=False, builder_biomass_hint=2, lenient=True)
                    if locked is not None:
                        self._planned_sites["first"] = locked
                        print(f"📍 [SITE] Selected spawner site at {locked} (value: {tile_value(locked)})")

                if locked is not None:
                    builder = pick_builder_spore(
                        site=locked,
                        min_biomass_needed=min_needed,
                        avoid_ids=set(),
                        lenient=is_emergency,
                    )

                    if builder is None and is_emergency:
                        print("⚠️  [FALLBACK] No ideal builder, trying with lower biomass...")
                        builder = pick_builder_spore(
                            site=locked,
                            min_biomass_needed=2,
                            avoid_ids=set(),
                            lenient=True,
                        )

                    if builder is None and is_emergency and actionable_spores:
                        print("🚨 [EMERGENCY FALLBACK] Using strongest available spore!")
                        builder = max(actionable_spores, key=lambda s: s.biomass)

                    if builder is not None:
                        builder_ids.add(builder.id)
                        self._builder_target_by_id[builder.id] = locked

                        bpt = _pos_to_point(builder.position)
                        if bpt == locked:
                            if builder.biomass >= next_spawner_cost:
                                add_action_for_spore(builder.id, SporeCreateSpawnerAction(sporeId=builder.id))
                                print(
                                    f"✅ [SPAWNER] Built at tick {self._tick_count} "
                                    f"with {builder.biomass} biomass (cost: {next_spawner_cost})"
                                )
                            else:
                                print(
                                    f"⚠️  Builder at site but only has {builder.biomass} "
                                    f"biomass (need {next_spawner_cost})"
                                )
                        else:
                            add_action_for_spore(
                                builder.id,
                                SporeMoveToAction(
                                    sporeId=builder.id,
                                    position=Position(x=locked.x, y=locked.y),
                                ),
                            )
                            print(
                                f"🚀 [MOVING] Builder (biomass {builder.biomass}) "
                                f"heading to site at {locked}"
                            )
                    else:
                        print("❌ [ERROR] No builder found! Actionable spores:", len(actionable_spores))
                else:
                    print("🛑 [CANCEL] No valid site for first spawner")
            else:
                if self._tick_count % 10 == 0:
                    print(f"⏳ [WAITING] Tick {self._tick_count}, Map: {self._map_type}, "
                          f"Neutrals: {len(neutral_spores)}, Actionable: {len(actionable_spores)}")

        if out_of_time():
            return actions

        # ---------- 🏗️ SECOND SPAWNER ----------
        def should_build_second_spawner() -> bool:
            if len(my_team.spawners) != 1:
                return False

            control_rate = get_control_rate()
            spawner_age = self._tick_count - self._first_spawner_tick if self._first_spawner_tick else 0

            # v7：大地图要更早开始第二个 spawner，不然很容易“守着一块地不出门”。
            if self._map_type == "large":
                return (control_rate > 0.12 and nutrients >= 15) or (nutrients >= 20 and spawner_age > 12) or (spawner_age > 25)

            if (self._total_spore_count or 3) <= 3:
                return (control_rate > 0.15 and nutrients >= 10) or (nutrients >= 15 and spawner_age > 10)

            # medium / small
            return (control_rate > 0.22 and nutrients >= 18) or (nutrients >= 25 and spawner_age > 15)

        if not out_of_time() and should_build_second_spawner() and actionable_spores:
            min_needed2 = next_spawner_cost + 2
            hint_biomass2 = max(min_needed2, 6)

            locked2 = self._planned_sites.get("second")
            if locked2 is None or not site_still_valid(locked2, hint_biomass2):
                locked2 = pick_best_site(is_second=True, builder_biomass_hint=hint_biomass2)
                if locked2 is not None:
                    self._planned_sites["second"] = locked2

            if locked2 is not None:
                builder2 = pick_builder_spore(
                    site=locked2,
                    min_biomass_needed=min_needed2,
                    avoid_ids=builder_ids,
                )
                if builder2 is not None:
                    builder_ids.add(builder2.id)
                    self._builder_target_by_id[builder2.id] = locked2

                    bpt2 = _pos_to_point(builder2.position)
                    if bpt2 == locked2 and is_safe_site_for_builder(bpt2, builder2.biomass):
                        add_action_for_spore(builder2.id, SporeCreateSpawnerAction(sporeId=builder2.id))
                    else:
                        add_action_for_spore(
                            builder2.id,
                            SporeMoveToAction(
                                sporeId=builder2.id,
                                position=Position(x=locked2.x, y=locked2.y),
                            ),
                        )

        if out_of_time():
            return actions

        # ---------- ⚔️ Combat targeting ----------
        def find_soft_targets() -> List[Tuple[Point, int, str, int]]:
            targets: List[Tuple[Point, int, str, int]] = []

            for enemy_pt, enemy_bio in enemy_list:
                attackers = [
                    s for s in actionable_spores
                    if s.id not in builder_ids
                    and s.id not in used_spores
                    and s.biomass > enemy_bio + 1
                ]
                if attackers:
                    nearest = min(attackers, key=lambda s: _manhattan(_pos_to_point(s.position), enemy_pt))
                    dist = _manhattan(_pos_to_point(nearest.position), enemy_pt)
                    if dist <= 10:
                        profit = tile_value(enemy_pt) + enemy_bio
                        targets.append((enemy_pt, profit, nearest.id, dist))

            for neutral_pt, neutral_bio in neutral_spores:
                attackers = [
                    s for s in actionable_spores
                    if s.id not in builder_ids
                    and s.id not in used_spores
                    and s.biomass > neutral_bio
                ]
                if attackers:
                    nearest = min(attackers, key=lambda s: _manhattan(_pos_to_point(s.position), neutral_pt))
                    dist = _manhattan(_pos_to_point(nearest.position), neutral_pt)
                    if dist <= 8:
                        profit = tile_value(neutral_pt) + neutral_bio // 2
                        targets.append((neutral_pt, profit, nearest.id, dist))

            targets.sort(key=lambda t: t[1] / (t[3] + 1), reverse=True)
            return targets[:5]

        hunter_ids: Set[str] = set()
        soft_targets = [] if survival_mode else find_soft_targets()

        for target_pt, profit, attacker_id, dist in soft_targets:
            if out_of_time():
                break
            if attacker_id not in used_spores and profit > 20:
                hunter_ids.add(attacker_id)
                add_action_for_spore(
                    attacker_id,
                    SporeMoveToAction(
                        sporeId=attacker_id,
                        position=Position(x=target_pt.x, y=target_pt.y),
                    ),
                )

        if out_of_time():
            return actions

        # ---------- 🛡️ Defense around spawners ----------
        defender_ids: Set[str] = set()

        if spawner_pts and actionable_spores:
            for spt in spawner_pts:
                if out_of_time():
                    break

                direct_threat = threat_at(spt)
                adjacent_threat = adjacent_enemy_max(spt)

                if direct_threat > 0:
                    remaining = [
                        s for s in actionable_spores
                        if s.id not in builder_ids
                        and s.id not in hunter_ids
                        and s.id not in used_spores
                        and s.biomass >= direct_threat + 2
                    ]
                    if remaining:
                        defender = min(remaining, key=lambda s: _manhattan(_pos_to_point(s.position), spt))
                        defender_ids.add(defender.id)

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
                        defender = min(remaining, key=lambda s: _manhattan(_pos_to_point(s.position), spt))
                        defender_ids.add(defender.id)

        if out_of_time():
            return actions

        # ---------- 🏭 AGGRESSIVE SPAWNER PRODUCTION ----------
        if (self._total_spore_count or 3) <= 3:
            reserve_nutrients = 2
        else:
            reserve_nutrients = 3 if len(my_team.spawners) >= 2 else 4

        for spawner in my_team.spawners:
            if out_of_time():
                break
            if spawner.id in used_spawners:
                continue

            spt = _pos_to_point(spawner.position)
            local_threat = max(threat_at(spt), adjacent_enemy_max(spt))
            for d in DIRS:
                nx, ny = spt.x + d.x, spt.y + d.y
                if _in_bounds(nx, ny, width, height):
                    local_threat = max(local_threat, threat_at(Point(nx, ny)))

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

            if nutrients - desired < reserve_nutrients:
                continue

            if nutrients >= desired and desired >= 2:
                add_action_for_spawner(
                    spawner.id,
                    SpawnerProduceSporeAction(spawnerId=spawner.id, biomass=desired),
                )
                nutrients -= desired

        if out_of_time():
            return actions
# ---------- ✂️ Split logic ----------
        if (not survival_mode) and len(my_team.spores) < 18 and time_left() > 0.010:
            split_candidates = sorted(big_spores, key=lambda s: s.biomass, reverse=True)[:3]

            for sp in split_candidates:
                if out_of_time():
                    break
                if sp.id in used_spores or sp.id in builder_ids:
                    continue

                pt = _pos_to_point(sp.position)
                best_dir: Optional[Position] = None
                best_score = -10**18

                for d in DIRS:
                    nx, ny = pt.x + d.x, pt.y + d.y
                    if not _in_bounds(nx, ny, width, height):
                        continue
                    npt = Point(nx, ny)

                    if tile_biomass(npt) != 0:
                        continue

                    enemy_here = enemy_biomass_at.get(npt, 0)
                    if enemy_here >= sp.biomass:
                        continue
                    if threat_at(npt) >= sp.biomass:
                        continue

                    score = tile_value(npt)
                    if tile_owner(npt) != my_team_id:
                        score += 30
                    score -= threat_at(npt) * 40
                    score -= adjacent_enemy_max(npt) * 30

                    if score > best_score:
                        best_score = score
                        best_dir = d

                if best_dir is not None and best_score >= 35:
                    moving_biomass = sp.biomass // 2
                    if 2 <= moving_biomass < sp.biomass:
                        add_action_for_spore(
                            sp.id,
                            SporeSplitAction(
                                sporeId=sp.id,
                                biomassForMovingSpore=moving_biomass,
                                direction=best_dir,
                            ),
                        )
                        break

        if out_of_time():
            return actions

        # ---------- 📐 BFS parameters ----------
        def get_bfs_params() -> Dict[str, int]:
            if self._tick_count < 100:
                return {"depth": 7, "nodes": 250, "spore_limit": 10}
            if time_left() > 0.040:
                return {"depth": 6, "nodes": 200, "spore_limit": 8}
            return {"depth": 4, "nodes": 120, "spore_limit": 4}

        bfs_params = get_bfs_params()
        BFS_MAX_DEPTH = bfs_params["depth"]
        BFS_MAX_NODES = bfs_params["nodes"]
        BFS_SPORE_LIMIT = bfs_params["spore_limit"]

        def _approx_step_cost(to_pt: Point) -> int:
            if tile_owner(to_pt) == my_team_id and tile_biomass(to_pt) >= 1:
                return 0
            if tile_biomass(to_pt) > 0:
                return 2
            return 1

        # ---------- 🎯 IMPROVED SCORING FUNCTION ----------
        def _score_tile_for_spore(
            sp: Spore,
            start_pt: Point,
            pt: Point,
            dist: int,
            path_cost: int,
            is_defender: bool,
            is_hunter: bool,
            defend_center: Optional[Point],
            lane_target: Optional[Point],
            prev_pt: Optional[Point],
            pioneer_2_cost_penalty: int,
        ) -> int:
            tv = tile_value(pt)
            owner = tile_owner(pt)

            enemy_here = enemy_biomass_at.get(pt, 0)
            thr = threat_at(pt)

            if thr >= sp.biomass:
                return -10**9

            score = 0

            # ⚔️ 战斗加分
            if enemy_here > 0:
                if sp.biomass <= enemy_here:
                    return -10**9
                score += 450 + (sp.biomass - enemy_here) * 5

            # 🛡️ 威胁惩罚（降低）
            score -= thr * 30  # 从 45 降低
            score -= adjacent_enemy_max(pt) * 20  # 从 25 降低

            # 🎯 角色特定评分
            if is_hunter:
                if enemy_here > 0:
                    score += 300
                score += tv
            elif is_defender and defend_center is not None:
                score += (30 if owner == my_team_id else 0)
                score += tv // 2
                score -= _manhattan(pt, defend_center) * 35
            else:
                # 🌟 普通扩张模式 - 关键改进
                score += tv * 4  # 从 3 提高到 4
                
                if owner != my_team_id:
                    score += 120  # 从 80 提高 - 更吸引未控制地块
                
                # 🌍 扩张加分
                expansion_bonus = self.expansion_enhancer.calculate_expansion_bonus(
                    pt=pt,
                    my_center=my_center,
                    my_territory_count=my_tile_count,
                    total_tiles=width * height,
                    is_stagnant=is_stagnant
                )
                score += expansion_bonus

                # 🧭 Lane 方向奖励：让单位持续朝“象限目标”推进，避免大地图原地兜圈
                if lane_target is not None:
                    # 越靠近目标越好；停滞时放大系数
                    base = 18 if not is_stagnant else 26
                    d0 = _manhattan(start_pt, lane_target)
                    d1 = _manhattan(pt, lane_target)
                    score += (d0 - d1) * base

                    # 鼓励“向外走”，不要一直围绕中心打转
                    outward = _manhattan(pt, my_center) - _manhattan(start_pt, my_center)
                    score += outward * (12 if not is_stagnant else 18)

                # 在中/大图：过于靠近中心的自家地块给一点惩罚，减少局部循环
                if self._map_type in ("medium", "large") and owner == my_team_id and _manhattan(pt, my_center) <= 3:
                    score -= 90

            # 📏 距离惩罚（降低）
            score -= dist * 18  # 从 25 降低
            score -= path_cost * 25  # 从 35 降低

            # 🚀 免费路径奖励
            if path_cost == 0 and dist > 0:
                score += (BFS_MAX_DEPTH - dist) * 8

            # 🎯 2-biomass 惩罚（使用动态版本）
            if sp.biomass == 2 and _approx_step_cost(pt) == 1:
                score -= pioneer_2_cost_penalty

            # 🔁 回头路惩罚：若候选点是“上一个位置”，通常表示在抖动
            # 但如果当前位置有威胁（需要撤退/换位），就不要强压。
            if prev_pt is not None and pt == prev_pt:
                if threat_at(start_pt) == 0 and adjacent_enemy_max(start_pt) == 0:
                    score -= 220

            if my_biomass_at.get(pt, 0) > 0:
                score -= 20

            return score

        def limited_bfs_first_step(
            sp: Spore,
            is_defender: bool,
            is_hunter: bool,
            defend_center: Optional[Point],
            lane_target: Optional[Point],
            prev_pt: Optional[Point],
            pioneer_2_cost_penalty: int,
        ) -> Optional[Position]:
            start = _pos_to_point(sp.position)
            q: Deque[Tuple[Point, int, Optional[Position], int]] = deque()
            q.append((start, 0, None, 0))

            visited: Set[Point] = {start}
            best_dir: Optional[Position] = None
            best_score: int = -10**18

            nodes = 0
            while q:
                if out_of_time():
                    break
                pt, depth, first_dir, path_cost = q.popleft()
                nodes += 1
                if nodes > BFS_MAX_NODES:
                    break

                if depth > 0 and first_dir is not None:
                    s = _score_tile_for_spore(
                        sp,
                        start,
                        pt,
                        depth,
                        path_cost,
                        is_defender,
                        is_hunter,
                        defend_center,
                        lane_target,
                        prev_pt,
                        pioneer_2_cost_penalty,
                    )
                    if s > best_score:
                        best_score = s
                        best_dir = first_dir

                if depth >= BFS_MAX_DEPTH:
                    continue

                for d in DIRS:
                    nx, ny = pt.x + d.x, pt.y + d.y
                    if not _in_bounds(nx, ny, width, height):
                        continue
                    npt = Point(nx, ny)
                    if npt in visited:
                        continue

                    if threat_at(npt) >= sp.biomass:
                        continue

                    enemy_here = enemy_biomass_at.get(npt, 0)
                    if enemy_here > 0 and sp.biomass <= enemy_here:
                        continue

                    visited.add(npt)
                    ndir = first_dir if first_dir is not None else d
                    step_cost = _approx_step_cost(npt)
                    q.append((npt, depth + 1, ndir, path_cost + step_cost))

            return best_dir

        # ---------- 🚨 Emergency merge ----------
        defend_center: Optional[Point] = spawner_pts[0] if spawner_pts else None
        actionable_sorted = sorted(actionable_spores, key=lambda s: s.biomass, reverse=True)

        if actionable_sorted and not out_of_time():
            my_spore_pts: List[Tuple[Point, Spore]] = [(_pos_to_point(s.position), s) for s in my_team.spores]

            def _step_towards(src: Point, dst: Point) -> Optional[Position]:
                best: Optional[Position] = None
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
                    cost = 0 if (tile_owner(npt) == my_team_id and tile_biomass(npt) >= 1) else 1
                    if cost < best_cost:
                        best_cost = cost
                        best = d
                return best

            endangered: List[Spore] = []
            for s in actionable_sorted:
                if s.id in used_spores or s.id in builder_ids:
                    continue
                pt = _pos_to_point(s.position)
                if threat_at(pt) > 0 or adjacent_enemy_max(pt) > 0 or s.biomass <= 2:
                    endangered.append(s)
                if len(endangered) >= 3:
                    break

            for s in endangered:
                if out_of_time() or s.id in used_spores or s.id in builder_ids:
                    continue

                src = _pos_to_point(s.position)
                best_buddy: Optional[Point] = None
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
                if threat_at(npt) >= s.biomass:
                    continue

                move_cost = 0 if (tile_owner(npt) == my_team_id and tile_biomass(npt) >= 1) else 1
                if s.biomass == 2 and move_cost == 1:
                    continue

                add_action_for_spore(s.id, SporeMoveAction(sporeId=s.id, direction=step))
# ---------- 🎯 MAIN MOVEMENT LOGIC ----------
        bfs_used = 0
        for sp in actionable_sorted:
            if out_of_time():
                break
            if sp.id in used_spores or sp.id in builder_ids:
                continue

            is_defender = sp.id in defender_ids
            is_hunter = sp.id in hunter_ids

            sp_pt = _pos_to_point(sp.position)

            # lane / 方向锁定（仅普通扩张单位）
            lane_target: Optional[Point] = None
            if not is_defender and not is_hunter and (self._map_type in ("medium", "large") or is_stagnant):
                lane = self.lane_planner.assign_lane(sp.id, sp_pt, my_center, self._tick_count)
                lane_target = lane_targets.get(lane)

            # 上一格（用于防抖）
            hist = self._pos_hist_by_id.get(sp.id)
            prev_pt: Optional[Point] = None
            if hist is not None and len(hist) >= 2:
                prev_pt = hist[-2]

            # Pioneer：允许 2 biomass 在大地图“牺牲一次行动”去拉开距离（但要有安全余量）
            is_pioneer = False
            pioneer_2_cost_penalty = penalty_2biomass
            if (
                sp.biomass == 2
                and lane_target is not None
                and not survival_mode
                and not is_defender
                and not is_hunter
                and (len(my_team.spawners) >= 1 or len(actionable_spores) >= 3)
                and _manhattan(sp_pt, lane_target) >= 5
            ):
                is_pioneer = True
                pioneer_2_cost_penalty = max(20, penalty_2biomass // 3)

            if is_defender and defend_center is not None:
                if _manhattan(sp_pt, defend_center) <= 2:
                    cur_pt = sp_pt
                    if threat_at(cur_pt) == 0 and adjacent_enemy_max(cur_pt) == 0:
                        continue

            best_dir: Optional[Position] = None

            # 尝试 BFS
            if bfs_used < BFS_SPORE_LIMIT and time_left() > 0.020:
                best_dir = limited_bfs_first_step(
                    sp,
                    is_defender=is_defender,
                    is_hunter=is_hunter,
                    defend_center=defend_center,
                    lane_target=lane_target,
                    prev_pt=prev_pt,
                    pioneer_2_cost_penalty=pioneer_2_cost_penalty,
                )
                bfs_used += 1

            # 如果 BFS 没找到方向，使用贪心搜索
            if best_dir is None:
                pt = sp_pt
                best_score = -10**18

                # 非 pioneer 的 2 biomass 尽量走 0-cost；pioneer 允许走 1-cost
                prefer_zero_cost = (sp.biomass == 2 and not is_pioneer)
                passes = (0, 1) if prefer_zero_cost else (1,)

                for pass_id in passes:
                    for d in DIRS:
                        nx, ny = pt.x + d.x, pt.y + d.y
                        if not _in_bounds(nx, ny, width, height):
                            continue
                        npt = Point(nx, ny)

                        if threat_at(npt) >= sp.biomass:
                            continue

                        enemy_here = enemy_biomass_at.get(npt, 0)
                        if enemy_here > 0 and sp.biomass <= enemy_here:
                            continue

                        move_cost = 0 if (tile_owner(npt) == my_team_id and tile_biomass(npt) >= 1) else 1
                        if pass_id == 0 and move_cost != 0:
                            continue

                        score = 0
                        
                        # 角色评分
                        if is_hunter:
                            if enemy_here > 0:
                                score += 450 + (sp.biomass - enemy_here) * 5
                            score += tile_value(npt)
                        elif is_defender and defend_center is not None:
                            score -= _manhattan(npt, defend_center) * 35
                            score += (30 if tile_owner(npt) == my_team_id else 0)
                            score += tile_value(npt) // 2
                        else:
                            # 🌟 普通扩张模式 - 改进评分
                            score += tile_value(npt) * 4  # 提高地块价值权重
                            
                            if tile_owner(npt) != my_team_id:
                                score += 120  # 提高未控制地块吸引力
                            
                            # 🌍 扩张加分
                            expansion_bonus = self.expansion_enhancer.calculate_expansion_bonus(
                                pt=npt,
                                my_center=my_center,
                                my_territory_count=my_tile_count,
                                total_tiles=width * height,
                                is_stagnant=is_stagnant
                            )
                            score += expansion_bonus

                            # 🧭 Lane 方向奖励：持续往象限目标推进
                            if lane_target is not None:
                                base = 18 if not is_stagnant else 26
                                d0 = _manhattan(pt, lane_target)
                                d1 = _manhattan(npt, lane_target)
                                score += (d0 - d1) * base
                                outward = _manhattan(npt, my_center) - _manhattan(pt, my_center)
                                score += outward * (12 if not is_stagnant else 18)

                            if self._map_type in ("medium", "large") and tile_owner(npt) == my_team_id and _manhattan(npt, my_center) <= 3:
                                score -= 90

                        if move_cost == 0:
                            score += 15

                        if enemy_here > 0 and sp.biomass > enemy_here:
                            score += 450 + (sp.biomass - enemy_here) * 5

                        # 威胁惩罚（降低）
                        score -= threat_at(npt) * 30  # 从 45 降低
                        score -= adjacent_enemy_max(npt) * 20  # 从 25 降低

                        # 🎯 动态 2-biomass 惩罚（pioneer 会更宽松）
                        if sp.biomass == 2 and move_cost == 1:
                            score -= pioneer_2_cost_penalty

                        # 🔁 回头路惩罚（防抖）
                        if prev_pt is not None and npt == prev_pt:
                            if threat_at(pt) == 0 and adjacent_enemy_max(pt) == 0:
                                score -= 220

                        if my_biomass_at.get(npt, 0) > 0:
                            score -= 20

                        if score > best_score:
                            best_score = score
                            best_dir = d

                    if best_dir is not None:
                        break

            # 执行移动
            if best_dir is not None:
                add_action_for_spore(sp.id, SporeMoveAction(sporeId=sp.id, direction=best_dir))
            else:
                # ---------- 兜底逻辑 ----------
                if is_defender and defend_center is not None:
                    add_action_for_spore(
                        sp.id,
                        SporeMoveToAction(
                            sporeId=sp.id,
                            position=Position(x=defend_center.x, y=defend_center.y),
                        ),
                    )
                else:
                    # 优先：lane 目标（大地图避免“兜圈”）
                    sp_pt = _pos_to_point(sp.position)
                    if lane_target is not None and threat_at(lane_target) < sp.biomass:
                        add_action_for_spore(
                            sp.id,
                            SporeMoveToAction(
                                sporeId=sp.id,
                                position=Position(x=lane_target.x, y=lane_target.y),
                            ),
                        )
                        continue

                    # 次优：寻找最近的高价值地块
                    pool = self._top_tiles_cache[:25]
                    best_t: Optional[Point] = None
                    best_s = 10**18

                    for t in pool:
                        if threat_at(t) >= sp.biomass:
                            continue
                        enemy_here = enemy_biomass_at.get(t, 0)
                        if enemy_here > 0 and sp.biomass <= enemy_here:
                            continue
                        d = _manhattan(sp_pt, t)
                        score = d * 100 - tile_value(t)
                        if score < best_s:
                            best_s = score
                            best_t = t

                    if best_t is not None:
                        add_action_for_spore(
                            sp.id,
                            SporeMoveToAction(
                                sporeId=sp.id,
                                position=Position(x=best_t.x, y=best_t.y),
                            ),
                        )

        return actions