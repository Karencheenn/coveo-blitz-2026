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
# 🗺️ IMPROVED MAP ANALYSIS CLASS
# ============================================================================
class ImprovedMapAnalysis:
    """
    改进的地图分析系统
    - 智能识别地图类型（open_rush, blocked_small, medium, large）
    - 动态调整建造时机
    - 自适应 2-biomass 惩罚
    """
    
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
        """
        识别地图类型：
        - "open_rush": 开阔小图，适合快速建造
        - "blocked_small": 有障碍的小图（Cross等），需要先清理
        - "medium": 中型图
        - "large": 大型图
        """
        key = (width, height)
        if key in self._map_type_cache:
            return self._map_type_cache[key]
        
        area = width * height
        
        # 计算中心区域的 neutral 密度
        center_x, center_y = width // 2, height // 2
        center_neutrals = sum(
            1 for pt, _ in neutral_spores
            if abs(pt.x - center_x) <= 3 and abs(pt.y - center_y) <= 3
        )
        
        # 计算总 neutral 数量
        total_neutrals = len(neutral_spores)
        
        # 分类逻辑
        if area <= 225:  # 小图 (15x15 或更小)
            # Cross 类型检测：中心有密集 neutral
            if center_neutrals >= 5 or total_neutrals >= 8:
                map_type = "blocked_small"
                print(f"🗺️  Map Type: BLOCKED_SMALL (neutrals: {total_neutrals}, center: {center_neutrals})")
            else:
                map_type = "open_rush"
                print(f"🗺️  Map Type: OPEN_RUSH (area: {area})")
        elif area <= 900:  # 中型图 (30x30 或更小)
            map_type = "medium"
            print(f"🗺️  Map Type: MEDIUM (area: {area})")
        else:
            map_type = "large"
            print(f"🗺️  Map Type: LARGE (area: {area})")
        
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
        """
        返回: (should_build, min_biomass_needed, is_emergency)
        
        改进点：
        1. blocked_small 地图延迟建造
        2. 确保有足够单位存活
        3. Emergency 模式更宽松
        """
        
        # 🚨 Emergency 条件
        is_emergency = tick >= 20
        
        if map_type == "open_rush":
            # 真正的小图 rush
            if tick >= 2 and actionable_count >= 1 and max_biomass >= next_spawner_cost:
                return (True, next_spawner_cost + 1, False)
        
        elif map_type == "blocked_small":
            # Cross 等有障碍的小图 - 关键改进！
            
            # ⚠️ 核心逻辑：如果有很多 neutral，必须等待
            if neutral_count >= 5:
                # 策略1：等到有多个单位再建造
                if tick >= 15 and actionable_count >= 2 and max_biomass >= next_spawner_cost + 2:
                    return (True, max(next_spawner_cost + 2, 4), False)
                # 策略2：Emergency 模式但要求更高
                elif tick >= 25 and actionable_count >= 1 and max_biomass >= 3:
                    return (True, max(next_spawner_cost, 3), True)
                # 否则继续等待
                return (False, 0, False)
            else:
                # neutral 已清理大部分，可以建造
                if tick >= 5 and actionable_count >= 1 and max_biomass >= next_spawner_cost + 1:
                    return (True, next_spawner_cost + 1, False)
        
        elif map_type == "medium":
            # 中型图
            if total_spore_count <= 6:
                if actionable_count >= 2 or tick > 15:
                    return (True, max(next_spawner_cost + 1, 4), False)
            else:
                if actionable_count >= 3 or tick > 20:
                    return (True, max(next_spawner_cost + 1, 5), False)
        
        else:  # large
            if actionable_count >= 4 or tick > 25:
                return (True, max(next_spawner_cost + 2, 6), False)
        
        # Emergency fallback - 但要确保不会自杀
        if is_emergency and actionable_count >= 1:
            # 确保建造后还有 actionable spores
            if max_biomass >= next_spawner_cost + 2:
                return (True, max(3, next_spawner_cost), True)
        
        return (False, 0, False)
    
    def calculate_2biomass_penalty(
        self,
        map_type: str,
        tick: int,
        my_tile_count: int,
        total_tiles: int,
        spawner_count: int
    ) -> int:
        """
        动态调整 2-biomass 移动惩罚
        
        原则：
        - 早期需要扩张 -> 低惩罚
        - 控制率低 -> 低惩罚  
        - 后期 + 有 spawner -> 高惩罚（保护单位）
        """
        control_rate = my_tile_count / total_tiles if total_tiles > 0 else 0
        
        # 早期阶段：必须扩张
        if tick < 50:
            if map_type == "blocked_small":
                # Cross 等地图早期更需要移动清理 neutral
                return 150
            elif map_type == "open_rush":
                return 200
            return 300
        
        # 中期：根据控制率调整
        if tick < 200:
            if control_rate < 0.15:
                return 250  # 控制率太低，必须扩张
            elif control_rate < 0.30:
                return 400
            else:
                return 600
        
        # 后期：保护模式
        if tick >= 500:
            if spawner_count >= 2:
                return 900  # 有经济了，保护单位
            return 700
        
        # 默认中等惩罚
        return 500


# ============================================================================
# 🤖 MAIN BOT CLASS
# ============================================================================
class Bot:
    """Coveo Blitz Bot v5.0
    
    主要改进：
    ✅ 智能地图分类（解决 Cross 地图早死）
    ✅ 动态 2-biomass 策略
    ✅ 改进的 neutral 处理
    ✅ 更好的 emergency 逻辑
    """

    def __init__(self) -> None:
        print("🚀 Initializing Bot v5.0 with Improved Map Analysis")

        # 🗺️ Map analyzer
        self.map_analyzer = ImprovedMapAnalysis()
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
        spawner_pts: List[Point] = [_pos_to_point(s.position) for s in my_team.spawners]

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
            my_tiles = sum(
                1
                for row in ownership_grid
                for owner in row
                if owner == my_team_id
            )
            return my_tiles / total_tiles if total_tiles > 0 else 0.0

        my_tile_count = sum(
            1
            for row in ownership_grid
            for owner in row
            if owner == my_team_id
        )

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

            # Threat / margin logic
            if lenient or (self._total_spore_count or 3) <= 3:
                margin = 0
            else:
                margin = SITE_SAFETY_MARGIN

            if threat_at(pt) >= builder_biomass - margin:
                return False

            # 🌟 敌人密度判断，避免在敌堆中建造
            if not lenient:
                density = self.enemy_density_in_radius(pt, enemy_biomass_at, radius=5)
                if density >= 6:
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

                # 确保 builder 起点也不要在必死格子里
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
# ---------- 🏗️ FIRST SPAWNER LOGIC (IMPROVED) ----------
        if not out_of_time() and len(my_team.spawners) == 0 and actionable_spores:
            # 使用新的地图分析决策
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

                # 根据地图类型调整 hint_biomass
                if self._map_type == "blocked_small":
                    hint_biomass = max(min_needed, 4)  # blocked 地图需要更多 biomass
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
                if self._tick_count % 10 == 0:  # 每 10 tick 报告一次
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

            if (self._total_spore_count or 3) <= 3:
                return (control_rate > 0.15 and nutrients >= 10) or (nutrients >= 15 and spawner_age > 10)
            return (control_rate > 0.25 and nutrients >= 20) or (nutrients >= 25 and spawner_age > 15)

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

        # ---------- 🏭 Spawner production ----------
        def calculate_spawner_production(local_threat: int, spore_count: int, nutrients_now: int) -> int:
            base = 4

            if self._tick_count < 200 and spore_count < 15:
                base = max(6, nutrients_now // 8)

            if local_threat > 0:
                base = max(base, local_threat + 3)

            if 200 <= self._tick_count < 500 and nutrients_now > 50:
                base = max(base, 7)

            # 🌋 高资源爆兵阶段
            if self._tick_count >= 800 and nutrients_now >= 2000:
                base = max(base, 12)

            return min(base, nutrients_now - 2)

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

            desired = calculate_spawner_production(local_threat, len(my_team.spores), nutrients)

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

        # ---------- 📐 BFS parameters (dynamic based on time) ----------
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

        # 🎯 获取动态 2-biomass 惩罚
        penalty_2biomass = self.map_analyzer.calculate_2biomass_penalty(
            map_type=self._map_type,
            tick=self._tick_count,
            my_tile_count=my_tile_count,
            total_tiles=width * height,
            spawner_count=len(my_team.spawners)
        )

        def _score_tile_for_spore(
            sp: Spore,
            pt: Point,
            dist: int,
            path_cost: int,
            is_defender: bool,
            is_hunter: bool,
            defend_center: Optional[Point],
        ) -> int:
            tv = tile_value(pt)
            owner = tile_owner(pt)
            tb = tile_biomass(pt)

            enemy_here = enemy_biomass_at.get(pt, 0)
            thr = threat_at(pt)

            if thr >= sp.biomass:
                return -10**9

            score = 0

            if enemy_here > 0:
                if sp.biomass <= enemy_here:
                    return -10**9
                score += 450 + (sp.biomass - enemy_here) * 5

            score -= thr * 45
            score -= adjacent_enemy_max(pt) * 25

            if is_hunter:
                if enemy_here > 0:
                    score += 300
                score += tv
            elif is_defender and defend_center is not None:
                score += (30 if owner == my_team_id else 0)
                score += tv // 2
                score -= _manhattan(pt, defend_center) * 35
            else:
                score += tv * 3
                if owner != my_team_id:
                    score += 80

            score -= dist * 25
            score -= path_cost * 35

            if path_cost == 0 and dist > 0:
                score += (BFS_MAX_DEPTH - dist) * 5

            # 🎯 使用动态 2-biomass 惩罚
            if sp.biomass == 2 and _approx_step_cost(pt) == 1:
                score -= penalty_2biomass

            if my_biomass_at.get(pt, 0) > 0:
                score -= 20

            return score

        def limited_bfs_first_step(
            sp: Spore,
            is_defender: bool,
            is_hunter: bool,
            defend_center: Optional[Point],
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
                        pt,
                        depth,
                        path_cost,
                        is_defender,
                        is_hunter,
                        defend_center,
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

        # ---------- 🎯 Main movement logic ----------
        bfs_used = 0
        for sp in actionable_sorted:
            if out_of_time():
                break
            if sp.id in used_spores or sp.id in builder_ids:
                continue

            is_defender = sp.id in defender_ids
            is_hunter = sp.id in hunter_ids

            if is_defender and defend_center is not None:
                if _manhattan(_pos_to_point(sp.position), defend_center) <= 2:
                    cur_pt = _pos_to_point(sp.position)
                    if threat_at(cur_pt) == 0 and adjacent_enemy_max(cur_pt) == 0:
                        continue

            best_dir: Optional[Position] = None

            if bfs_used < BFS_SPORE_LIMIT and time_left() > 0.020:
                best_dir = limited_bfs_first_step(
                    sp,
                    is_defender=is_defender,
                    is_hunter=is_hunter,
                    defend_center=defend_center,
                )
                bfs_used += 1

            if best_dir is None:
                pt = _pos_to_point(sp.position)
                best_score = -10**18

                prefer_zero_cost = sp.biomass == 2
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
                        if is_hunter:
                            if enemy_here > 0:
                                score += 450 + (sp.biomass - enemy_here) * 5
                            score += tile_value(npt)
                        elif is_defender and defend_center is not None:
                            score -= _manhattan(npt, defend_center) * 35
                            score += (30 if tile_owner(npt) == my_team_id else 0)
                            score += tile_value(npt) // 2
                        else:
                            score += tile_value(npt) * 3
                            if tile_owner(npt) != my_team_id:
                                score += 70

                        if move_cost == 0:
                            score += 15

                        if enemy_here > 0 and sp.biomass > enemy_here:
                            score += 450 + (sp.biomass - enemy_here) * 5

                        score -= threat_at(npt) * 45
                        score -= adjacent_enemy_max(npt) * 25

                        # 🎯 使用动态惩罚
                        if sp.biomass == 2 and move_cost == 1:
                            score -= penalty_2biomass

                        if my_biomass_at.get(npt, 0) > 0:
                            score -= 20

                        if score > best_score:
                            best_score = score
                            best_dir = d

                    if best_dir is not None:
                        break

            if best_dir is not None:
                add_action_for_spore(sp.id, SporeMoveAction(sporeId=sp.id, direction=best_dir))
            else:
                if is_defender and defend_center is not None:
                    add_action_for_spore(
                        sp.id,
                        SporeMoveToAction(
                            sporeId=sp.id,
                            position=Position(x=defend_center.x, y=defend_center.y),
                        ),
                    )
                else:
                    pool = self._top_tiles_cache[:25]
                    sp_pt = _pos_to_point(sp.position)
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