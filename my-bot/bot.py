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


class Bot:
    """
    Improved bot v4.0 - Economic & Combat Enhanced
    
    Major improvements:
    - Aggressive early-game economy (dynamic spawner production)
    - Active combat system (hunt weak enemies & neutrals)
    - Smarter split strategy (more frequent, adaptive sizing)
    - Intelligent spawner timing (control rate based)
    - Layered defense (reduce over-defending)
    """

    def __init__(self):
        print("Initializing improved bot v4.0 (Economic & Combat Enhanced)")

        # Cache
        self._cached_map_key: Optional[Tuple[int, int]] = None
        self._top_tiles_cache: List[Point] = []
        self._top_tiles_k: int = 400

        # Spawner planning
        self._planned_sites: Dict[str, Point] = {}
        self._builder_target_by_id: Dict[str, Point] = {}
        
        # Timing tracking
        self._first_spawner_tick: Optional[int] = None
        self._tick_count: int = 0

    def get_next_move(self, game_message: TeamGameState) -> list[Action]:
        actions: List[Action] = []
        self._tick_count = game_message.tick

        # =========================
        # Tick time budget
        # =========================
        TICK_BUDGET_SEC = 0.085
        tick_start = time.perf_counter()

        def time_left() -> float:
            return TICK_BUDGET_SEC - (time.perf_counter() - tick_start)

        def out_of_time() -> bool:
            return time_left() <= 0.0

        if game_message.lastTickErrors:
            print("lastTickErrors:", game_message.lastTickErrors)

        world = game_message.world
        width, height = world.map.width, world.map.height
        my_team_id = game_message.yourTeamId

        my_team: TeamInfo = world.teamInfos[my_team_id]
        nutrients: int = my_team.nutrients
        next_spawner_cost: int = my_team.nextSpawnerCost

        nutrient_grid = world.map.nutrientGrid
        ownership_grid = world.ownershipGrid
        biomass_grid = world.biomassGrid

        # ---------------------------------------------------------
        # Cache top tiles
        # ---------------------------------------------------------
        def _ensure_top_tiles_cache() -> None:
            key = (width, height)

            if key in _GLOBAL_TOP_TILES_CACHE and _GLOBAL_TOP_TILES_CACHE[key]:
                self._cached_map_key = key
                self._top_tiles_cache = _GLOBAL_TOP_TILES_CACHE[key]
                return

            if self._cached_map_key == key and self._top_tiles_cache:
                _GLOBAL_TOP_TILES_CACHE[key] = self._top_tiles_cache
                return

            pts: List[Point] = [Point(x, y) for y in range(height) for x in range(width)]
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

        # ---------------------------------------------------------
        # Index units
        # ---------------------------------------------------------
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

        # ---------------------------------------------------------
        # Threat map
        # ---------------------------------------------------------
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

        # ---------------------------------------------------------
        # One action per unit
        # ---------------------------------------------------------
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

        # ---------------------------------------------------------
        # Partition units
        # ---------------------------------------------------------
        actionable_spores: List[Spore] = [s for s in my_team.spores if s.biomass >= 2]
        big_spores: List[Spore] = [s for s in my_team.spores if s.biomass >= 6]
        spawner_pts: List[Point] = [_pos_to_point(s.position) for s in my_team.spawners]

        # Track first spawner timing
        if len(my_team.spawners) > 0 and self._first_spawner_tick is None:
            self._first_spawner_tick = self._tick_count

        # ---------------------------------------------------------
        # Helper: Calculate map control rate
        # ---------------------------------------------------------
        def get_control_rate() -> float:
            total_tiles = width * height
            my_tiles = sum(1 for row in ownership_grid for owner in row if owner == my_team_id)
            return my_tiles / total_tiles if total_tiles > 0 else 0.0

        # ---------------------------------------------------------
        # Spawner placement (enhanced timing)
        # ---------------------------------------------------------
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

        def is_safe_site_for_builder(pt: Point, builder_biomass: int) -> bool:
            if builder_biomass <= 0:
                return False
            if threat_at(pt) >= builder_biomass - SITE_SAFETY_MARGIN:
                return False
            for d in DIRS:
                nx, ny = pt.x + d.x, pt.y + d.y
                if _in_bounds(nx, ny, width, height):
                    npt = Point(nx, ny)
                    eb = enemy_biomass_at.get(npt, 0)
                    if eb > 0 and builder_biomass <= eb:
                        return False
                    if threat_at(npt) >= builder_biomass - SITE_SAFETY_MARGIN:
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

        def pick_best_site(is_second: bool, builder_biomass_hint: int) -> Optional[Point]:
            pool = self._top_tiles_cache[:SITE_POOL_K]
            best: Optional[Point] = None
            best_score = -10**18
            for pt in pool:
                if pt in spawner_pts:
                    continue
                if not is_safe_site_for_builder(pt, builder_biomass_hint):
                    continue
                s = score_site(pt, is_second=is_second)
                if s > best_score:
                    best_score = s
                    best = pt
            return best

        def pick_builder_spore(site: Point, min_biomass_needed: int, avoid_ids: Set[str]) -> Optional[Spore]:
            best: Optional[Spore] = None
            best_score = 10**18
            for sp in actionable_spores:
                if sp.id in avoid_ids or sp.id in used_spores:
                    continue
                if sp.biomass < min_biomass_needed:
                    continue
                if threat_at(_pos_to_point(sp.position)) >= sp.biomass:
                    continue
                d = _manhattan(_pos_to_point(sp.position), site)
                score = d * 100 - sp.biomass * 5
                if score < best_score:
                    best_score = score
                    best = sp
            return best

        builder_ids: Set[str] = set()

        # First spawner
        if not out_of_time() and len(my_team.spawners) == 0 and actionable_spores:
            min_needed = next_spawner_cost + 2
            hint_biomass = max(min_needed, 6)

            locked = self._planned_sites.get("first")
            if locked is None or not site_still_valid(locked, hint_biomass):
                locked = pick_best_site(is_second=False, builder_biomass_hint=hint_biomass)
                if locked is not None:
                    self._planned_sites["first"] = locked

            if locked is not None:
                builder = pick_builder_spore(site=locked, min_biomass_needed=min_needed, avoid_ids=set())
                if builder is not None:
                    builder_ids.add(builder.id)
                    self._builder_target_by_id[builder.id] = locked

                    bpt = _pos_to_point(builder.position)
                    if bpt == locked and is_safe_site_for_builder(bpt, builder.biomass):
                        add_action_for_spore(builder.id, SporeCreateSpawnerAction(sporeId=builder.id))
                    else:
                        add_action_for_spore(
                            builder.id,
                            SporeMoveToAction(sporeId=builder.id, position=Position(x=locked.x, y=locked.y)),
                        )

        # Second spawner (improved timing: control rate OR nutrient threshold)
        def should_build_second_spawner() -> bool:
            if len(my_team.spawners) != 1:
                return False
            
            control_rate = get_control_rate()
            spawner_age = self._tick_count - self._first_spawner_tick if self._first_spawner_tick else 0
            
            # Build if we control 30%+ of map OR have 30+ nutrients after 20 ticks
            return (control_rate > 0.30 and nutrients >= 25) or \
                   (nutrients >= 30 and spawner_age > 20)

        if not out_of_time() and should_build_second_spawner() and actionable_spores:
            min_needed2 = next_spawner_cost + 4
            hint_biomass2 = max(min_needed2, 8)

            locked2 = self._planned_sites.get("second")
            if locked2 is None or not site_still_valid(locked2, hint_biomass2):
                locked2 = pick_best_site(is_second=True, builder_biomass_hint=hint_biomass2)
                if locked2 is not None:
                    self._planned_sites["second"] = locked2

            if locked2 is not None:
                builder2 = pick_builder_spore(site=locked2, min_biomass_needed=min_needed2, avoid_ids=builder_ids)
                if builder2 is not None:
                    builder_ids.add(builder2.id)
                    self._builder_target_by_id[builder2.id] = locked2

                    bpt2 = _pos_to_point(builder2.position)
                    if bpt2 == locked2 and is_safe_site_for_builder(bpt2, builder2.biomass):
                        add_action_for_spore(builder2.id, SporeCreateSpawnerAction(sporeId=builder2.id))
                    else:
                        add_action_for_spore(
                            builder2.id,
                            SporeMoveToAction(sporeId=builder2.id, position=Position(x=locked2.x, y=locked2.y)),
                        )

        if out_of_time():
            return actions

        # ---------------------------------------------------------
        # Soft target identification (NEW: Active combat)
        # ---------------------------------------------------------
        def find_soft_targets() -> List[Tuple[Point, int, str, int]]:
            """Find attackable enemies and neutrals. Returns (target_pt, profit, attacker_id, dist)"""
            targets = []
            
            # Enemy targets
            for enemy_pt, enemy_bio in enemy_list:
                attackers = [s for s in actionable_spores 
                           if s.id not in builder_ids 
                           and s.id not in used_spores
                           and s.biomass > enemy_bio + 1]
                if attackers:
                    nearest = min(attackers, key=lambda s: _manhattan(_pos_to_point(s.position), enemy_pt))
                    dist = _manhattan(_pos_to_point(nearest.position), enemy_pt)
                    if dist <= 10:
                        profit = tile_value(enemy_pt) + enemy_bio
                        targets.append((enemy_pt, profit, nearest.id, dist))
            
            # Neutral targets
            for neutral_pt, neutral_bio in neutral_spores:
                attackers = [s for s in actionable_spores 
                           if s.id not in builder_ids 
                           and s.id not in used_spores
                           and s.biomass > neutral_bio]
                if attackers:
                    nearest = min(attackers, key=lambda s: _manhattan(_pos_to_point(s.position), neutral_pt))
                    dist = _manhattan(_pos_to_point(nearest.position), neutral_pt)
                    if dist <= 8:
                        profit = tile_value(neutral_pt) + neutral_bio // 2
                        targets.append((neutral_pt, profit, nearest.id, dist))
            
            targets.sort(key=lambda t: t[1] / (t[3] + 1), reverse=True)
            return targets[:5]

        hunter_ids: Set[str] = set()
        soft_targets = find_soft_targets()
        
        for target_pt, profit, attacker_id, dist in soft_targets:
            if out_of_time():
                break
            if attacker_id not in used_spores and profit > 20:
                hunter_ids.add(attacker_id)
                add_action_for_spore(
                    attacker_id,
                    SporeMoveToAction(sporeId=attacker_id, position=Position(x=target_pt.x, y=target_pt.y))
                )

        if out_of_time():
            return actions

        # ---------------------------------------------------------
        # Layered defense (reduced over-defending)
        # ---------------------------------------------------------
        defender_ids: Set[str] = set()

        if spawner_pts and actionable_spores:
            for spt in spawner_pts:
                if out_of_time():
                    break
                
                direct_threat = threat_at(spt)
                adjacent_threat = adjacent_enemy_max(spt)
                
                if direct_threat > 0:
                    remaining = [s for s in actionable_spores 
                               if s.id not in builder_ids 
                               and s.id not in hunter_ids
                               and s.id not in used_spores
                               and s.biomass >= direct_threat + 2]
                    if remaining:
                        defender = min(remaining, key=lambda s: _manhattan(_pos_to_point(s.position), spt))
                        defender_ids.add(defender.id)
                
                elif adjacent_threat > 0:
                    remaining = [s for s in actionable_spores 
                               if s.id not in builder_ids 
                               and s.id not in hunter_ids
                               and s.id not in defender_ids
                               and s.id not in used_spores
                               and s.biomass >= 4]
                    if remaining and len(defender_ids) < len(spawner_pts):
                        defender = min(remaining, key=lambda s: _manhattan(_pos_to_point(s.position), spt))
                        defender_ids.add(defender.id)

        if out_of_time():
            return actions

        # ---------------------------------------------------------
        # SpawnerProduceSpore (ENHANCED: Dynamic production)
        # ---------------------------------------------------------
        def calculate_spawner_production(local_threat: int, spore_count: int) -> int:
            base = 4
            
            if self._tick_count < 200 and spore_count < 15:
                base = max(5, nutrients // 10)
            
            if local_threat > 0:
                base = max(base, local_threat + 3)
            
            if 200 <= self._tick_count < 500 and nutrients > 50:
                base = max(base, 6)
            
            return min(base, nutrients - 3)

        reserve_nutrients = 3 if len(my_team.spawners) >= 2 else 5

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

            desired = calculate_spawner_production(local_threat, len(my_team.spores))

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

        # ---------------------------------------------------------
        # Split (ENHANCED: More aggressive)
        # ---------------------------------------------------------
        if len(my_team.spores) < 18 and time_left() > 0.010:
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

        # ---------------------------------------------------------
        # BFS (Adaptive parameters)
        # ---------------------------------------------------------
        def get_bfs_params() -> Dict[str, int]:
            if self._tick_count < 100:
                return {"depth": 7, "nodes": 250, "spore_limit": 10}
            elif time_left() > 0.040:
                return {"depth": 6, "nodes": 200, "spore_limit": 8}
            else:
                return {"depth": 4, "nodes": 120, "spore_limit": 4}

        bfs_params = get_bfs_params()
        BFS_MAX_DEPTH = bfs_params["depth"]
        BFS_MAX_NODES = bfs_params["nodes"]
        BFS_SPORE_LIMIT = bfs_params["spore_limit"]

        def _approx_step_cost(to_pt: Point) -> int:
            if tile_owner(to_pt) == my_team_id and tile_biomass(to_pt) >= 1:
                return 0
            elif tile_biomass(to_pt) > 0:
                return 2
            else:
                return 1

        def _score_tile_for_spore(sp: Spore, pt: Point, dist: int, path_cost: int, 
                                  is_defender: bool, is_hunter: bool, defend_center: Optional[Point]) -> int:
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

            if sp.biomass == 2 and tb == 0 and _approx_step_cost(pt) == 1:
                score -= 250

            if my_biomass_at.get(pt, 0) > 0:
                score -= 20

            return score

        def limited_bfs_first_step(sp: Spore, is_defender: bool, is_hunter: bool, 
                                   defend_center: Optional[Point]) -> Optional[Position]:
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
                    s = _score_tile_for_spore(sp, pt, depth, path_cost, is_defender, is_hunter, defend_center)
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

        # ---------------------------------------------------------
        # Move / Fight
        # ---------------------------------------------------------
        defend_center: Optional[Point] = spawner_pts[0] if spawner_pts else None
        actionable_sorted = sorted(actionable_spores, key=lambda s: s.biomass, reverse=True)

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
                best_dir = limited_bfs_first_step(sp, is_defender=is_defender, is_hunter=is_hunter, defend_center=defend_center)
                bfs_used += 1

            if best_dir is None:
                pt = _pos_to_point(sp.position)
                best_score = -10**18

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

                    if sp.biomass == 2 and tile_biomass(npt) == 0 and move_cost == 1:
                        score -= 300

                    if my_biomass_at.get(npt, 0) > 0:
                        score -= 20

                    if score > best_score:
                        best_score = score
                        best_dir = d

            if best_dir is not None:
                add_action_for_spore(sp.id, SporeMoveAction(sporeId=sp.id, direction=best_dir))
            else:
                if is_defender and defend_center is not None:
                    add_action_for_spore(
                        sp.id,
                        SporeMoveToAction(sporeId=sp.id, position=Position(x=defend_center.x, y=defend_center.y)),
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
                            SporeMoveToAction(sporeId=sp.id, position=Position(x=best_t.x, y=best_t.y)),
                        )

        return actions