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


# If Bot instance is recreated often, this global cache still helps.
_GLOBAL_TOP_TILES_CACHE: Dict[Tuple[int, int], List[Point]] = {}


class Bot:
    """
    Improved bot v3.2 (anti-timeout)
    - Hard tick time budget gate (avoid websocket disconnects due to >100ms tick)
    - Cached top nutrient tiles
    - Locked spawner sites + builder targets
    - Relative safety for spawner site (builder can survive/win)
    - BFS throttled by time remaining + reduced parameters
    """

    def __init__(self):
        print("Initializing improved bot v3.2 (anti-timeout)")

        # local cache pointers
        self._cached_map_key: Optional[Tuple[int, int]] = None
        self._top_tiles_cache: List[Point] = []  # points sorted by nutrient desc
        self._top_tiles_k: int = 400

        # lock targets
        self._planned_sites: Dict[str, Point] = {}          # {"first": pt, "second": pt}
        self._builder_target_by_id: Dict[str, Point] = {}   # spore_id -> site

    def get_next_move(self, game_message: TeamGameState) -> list[Action]:
        actions: List[Action] = []

        # =========================
        # Tick time budget gate
        # =========================
        # Engine budget is ~100ms/tick; keep margin.
        TICK_BUDGET_SEC = 0.085
        tick_start = time.perf_counter()

        def time_left() -> float:
            return TICK_BUDGET_SEC - (time.perf_counter() - tick_start)

        def out_of_time() -> bool:
            return time_left() <= 0.0

        # Debug invalid actions from previous tick.
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
        # 0) Cache: top nutrient tiles (sort once)
        # ---------------------------------------------------------
        def _ensure_top_tiles_cache() -> None:
            key = (width, height)

            # Global cache
            if key in _GLOBAL_TOP_TILES_CACHE and _GLOBAL_TOP_TILES_CACHE[key]:
                self._cached_map_key = key
                self._top_tiles_cache = _GLOBAL_TOP_TILES_CACHE[key]
                return

            # Local cache
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
        # 1) Quick indexing: enemy / mine
        # ---------------------------------------------------------
        enemy_biomass_at: Dict[Point, int] = {}
        my_biomass_at: Dict[Point, int] = {}

        # This loop is typically not the bottleneck; keep it simple.
        for sp in world.spores:
            pt = _pos_to_point(sp.position)
            if sp.teamId == my_team_id:
                my_biomass_at[pt] = max(my_biomass_at.get(pt, 0), sp.biomass)
            else:
                enemy_biomass_at[pt] = max(enemy_biomass_at.get(pt, 0), sp.biomass)

        enemy_list: List[Tuple[Point, int]] = list(enemy_biomass_at.items())
        enemy_list.sort(key=lambda t: t[1], reverse=True)

        def adjacent_enemy_max(pt: Point) -> int:
            m = 0
            for d in DIRS:
                nx, ny = pt.x + d.x, pt.y + d.y
                if not _in_bounds(nx, ny, width, height):
                    continue
                m = max(m, enemy_biomass_at.get(Point(nx, ny), 0))
            return m

        # ---------------------------------------------------------
        # 2) Threat map: enemy cell + 4-neighbors (max biomass)
        # ---------------------------------------------------------
        threat_map: Dict[Point, int] = {}
        for ept, eb in enemy_biomass_at.items():
            # enemy's own tile
            prev = threat_map.get(ept, 0)
            if eb > prev:
                threat_map[ept] = eb
            # neighbors
            for d in DIRS:
                nx, ny = ept.x + d.x, ept.y + d.y
                if not _in_bounds(nx, ny, width, height):
                    continue
                npt = Point(nx, ny)
                prev2 = threat_map.get(npt, 0)
                if eb > prev2:
                    threat_map[npt] = eb

        def threat_at(pt: Point) -> int:
            return threat_map.get(pt, 0)

        # ---------------------------------------------------------
        # 3) One unit one action
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
        # 4) Partition
        # ---------------------------------------------------------
        actionable_spores: List[Spore] = [s for s in my_team.spores if s.biomass >= 2]
        big_spores: List[Spore] = [s for s in my_team.spores if s.biomass >= 5]
        spawner_pts: List[Point] = [_pos_to_point(s.position) for s in my_team.spawners]

        # ---------------------------------------------------------
        # 5) Spawner placement (locked + relative safe)
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

            # Adjacent enemy must be beatable; adjacent threat must be tolerable
            for d in DIRS:
                nx, ny = pt.x + d.x, pt.y + d.y
                if not _in_bounds(nx, ny, width, height):
                    continue
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
                # avoid starting in losing-threat zone
                if threat_at(_pos_to_point(sp.position)) >= sp.biomass:
                    continue
                d = _manhattan(_pos_to_point(sp.position), site)
                score = d * 100 - sp.biomass * 5
                if score < best_score:
                    best_score = score
                    best = sp
            return best

        builder_ids: Set[str] = set()

        # --- First spawner ---
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

        # --- Second spawner ---
        if not out_of_time() and len(my_team.spawners) == 1 and nutrients >= 45 and actionable_spores:
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
            # Safety return: never exceed time budget
            return actions

        # ---------------------------------------------------------
        # 6) Defender assignment (avoid builders)
        # ---------------------------------------------------------
        defender_ids: Set[str] = set()
        spawner_pts = [_pos_to_point(s.position) for s in my_team.spawners]

        if spawner_pts and actionable_spores:
            need_defense = False
            for spt in spawner_pts:
                if threat_at(spt) > 0 or adjacent_enemy_max(spt) > 0:
                    need_defense = True
                    break
                for d in DIRS:
                    nx, ny = spt.x + d.x, spt.y + d.y
                    if _in_bounds(nx, ny, width, height) and threat_at(Point(nx, ny)) > 0:
                        need_defense = True
                        break
                if need_defense:
                    break

            if need_defense:
                remaining = [s for s in actionable_spores if s.id not in builder_ids and s.id not in used_spores]
                remaining.sort(key=lambda s: s.biomass, reverse=True)

                def pick_nearest_defender(target_pt: Point, exclude: Set[str]) -> Optional[Spore]:
                    best_s: Optional[Spore] = None
                    best_d = 10**9
                    for sp in remaining:
                        if sp.id in exclude:
                            continue
                        d = _manhattan(_pos_to_point(sp.position), target_pt)
                        if d < best_d:
                            best_d = d
                            best_s = sp
                    return best_s

                assigned: Set[str] = set()
                for spt in spawner_pts:
                    if out_of_time():
                        break
                    sp1 = pick_nearest_defender(spt, assigned)
                    if sp1 is not None:
                        assigned.add(sp1.id)
                        defender_ids.add(sp1.id)
                    if len(actionable_spores) >= 6:
                        sp2 = pick_nearest_defender(spt, assigned)
                        if sp2 is not None:
                            assigned.add(sp2.id)
                            defender_ids.add(sp2.id)

        if out_of_time():
            return actions

        # ---------------------------------------------------------
        # 7) SpawnerProduceSpore (time-safe)
        # ---------------------------------------------------------
        reserve_nutrients = 10 if len(my_team.spawners) == 1 else 0

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

            desired = 3 if len(my_team.spores) < 5 else 2

            # If threatened, produce strictly bigger than threat to avoid "free food"
            if local_threat > 0:
                desired = max(desired, local_threat + 1)

            if nutrients >= 14 and len(my_team.spores) < 10 and local_threat == 0:
                desired = max(desired, 3)

            if nutrients - desired < reserve_nutrients:
                continue

            if nutrients >= desired:
                add_action_for_spawner(
                    spawner.id,
                    SpawnerProduceSporeAction(spawnerId=spawner.id, biomass=desired),
                )
                nutrients -= desired

        if out_of_time():
            return actions

        # ---------------------------------------------------------
        # 8) Split (guarded by time)
        # ---------------------------------------------------------
        if len(my_team.spores) < 6 and time_left() > 0.010:
            for sp in sorted(big_spores, key=lambda s: s.biomass, reverse=True):
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

                if best_dir is not None and best_score >= 45:
                    moving_biomass = 3
                    if 1 <= moving_biomass < sp.biomass:
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
        # 9) Limited BFS (throttled by time remaining)
        # ---------------------------------------------------------
        BFS_MAX_DEPTH = 5
        BFS_MAX_NODES = 160
        BFS_SPORE_LIMIT = 6

        def _approx_step_cost(to_pt: Point) -> int:
            # Move cost approximation: on our trail => 0 else 1
            if tile_owner(to_pt) == my_team_id and tile_biomass(to_pt) >= 1:
                return 0
            return 1

        def _score_tile_for_spore(sp: Spore, pt: Point, dist: int, path_cost: int, is_defender: bool, defend_center: Optional[Point]) -> int:
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

            if not is_defender:
                score += tv * 2
                if owner != my_team_id:
                    score += 70
            else:
                score += (30 if owner == my_team_id else 0)
                score += tv // 2
                if defend_center is not None:
                    score -= _manhattan(pt, defend_center) * 35

            score -= dist * 25
            score -= path_cost * 35

            if sp.biomass == 2 and tb == 0 and _approx_step_cost(pt) == 1:
                score -= 250

            if my_biomass_at.get(pt, 0) > 0:
                score -= 20

            return score

        def limited_bfs_first_step(sp: Spore, is_defender: bool, defend_center: Optional[Point]) -> Optional[Position]:
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
                    s = _score_tile_for_spore(sp, pt, depth, path_cost, is_defender, defend_center)
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
        # 10) Move / Fight (time-safe)
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

            if is_defender and defend_center is not None:
                if _manhattan(_pos_to_point(sp.position), defend_center) <= 2:
                    cur_pt = _pos_to_point(sp.position)
                    if threat_at(cur_pt) == 0 and adjacent_enemy_max(cur_pt) == 0:
                        continue

            best_dir: Optional[Position] = None

            # Only do BFS if we have enough time left
            if bfs_used < BFS_SPORE_LIMIT and time_left() > 0.020:
                best_dir = limited_bfs_first_step(sp, is_defender=is_defender, defend_center=defend_center)
                bfs_used += 1

            # fallback: 1-step scoring (cheap)
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
                    if is_defender and defend_center is not None:
                        score -= _manhattan(npt, defend_center) * 35
                        score += (30 if tile_owner(npt) == my_team_id else 0)
                        score += tile_value(npt) // 2
                    else:
                        score += tile_value(npt) * 2
                        if tile_owner(npt) != my_team_id:
                            score += 60

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
                # ultimate fallback: cheap MoveTo to a cached high-nutrient tile
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

        # Optional: quick timing log to verify we're below budget
        # elapsed_ms = (time.perf_counter() - tick_start) * 1000
        # print(f"[tick] actions={len(actions)} elapsed_ms={elapsed_ms:.1f}")

        return actions
