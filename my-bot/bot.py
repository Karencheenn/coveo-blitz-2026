from __future__ import annotations

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
    Position(x=0, y=1),  # DOWN
    Position(x=-1, y=0),  # LEFT
    Position(x=1, y=0),  # RIGHT
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


def _neighbors4(pt: Point) -> List[Point]:
    """4-neighborhood points (no bounds checking)."""
    return [
        Point(pt.x, pt.y - 1),
        Point(pt.x, pt.y + 1),
        Point(pt.x - 1, pt.y),
        Point(pt.x + 1, pt.y),
    ]


class Bot:
    """
    改进方向（仍然保持“能跑 + 稳”，不追求最优）：
    1) Threat Map（威胁地图）：考虑 enemy 下一步可达位置，减少“走进去被秒”
    2) Defender 角色：靠近 spawners 的 spores 会防守 / 回防（尤其在被威胁时）
    3) 更智能的 SpawnerProduceSpore：根据威胁与资源动态产 2/3/4 biomass
    4) Limited BFS：仍然保持“小范围 + 节点上限”，避免 websocket 提前关闭
    """

    def __init__(self):
        print("Initializing improved bot v2 (threat map + defenders + limited BFS)")

    def get_next_move(self, game_message: TeamGameState) -> list[Action]:
        actions: List[Action] = []

        # Debug invalid actions from previous tick.
        if game_message.lastTickErrors:
            print("lastTickErrors:", game_message.lastTickErrors)

        world = game_message.world
        width, height = world.map.width, world.map.height
        my_team_id = game_message.yourTeamId

        my_team: TeamInfo = world.teamInfos[my_team_id]
        nutrients: int = my_team.nutrients
        next_spawner_cost: int = my_team.nextSpawnerCost

        # =========================================================
        # 0) 快速索引：enemy_biomass_at / my_biomass_at
        # =========================================================
        enemy_biomass_at: Dict[Point, int] = {}
        my_biomass_at: Dict[Point, int] = {}

        for sp in world.spores:
            pt = _pos_to_point(sp.position)
            if sp.teamId == my_team_id:
                my_biomass_at[pt] = max(my_biomass_at.get(pt, 0), sp.biomass)
            else:
                enemy_biomass_at[pt] = max(enemy_biomass_at.get(pt, 0), sp.biomass)

        def tile_value(pt: Point) -> int:
            return world.map.nutrientGrid[pt.y][pt.x]

        def tile_owner(pt: Point) -> int:
            return world.ownershipGrid[pt.y][pt.x]

        def tile_biomass(pt: Point) -> int:
            return world.biomassGrid[pt.y][pt.x]

        def adjacent_enemy_max(pt: Point) -> int:
            m = 0
            for nb in _neighbors4(pt):
                if _in_bounds(nb.x, nb.y, width, height):
                    m = max(m, enemy_biomass_at.get(nb, 0))
            return m

        # =========================================================
        # 1) Threat Map（威胁地图）
        #    思路：enemy spore 现在在某 tile，下一 tick 可能到 4-neighbors
        #    -> 把这些格子标记为 threatened，并记录最大 enemy biomass
        # =========================================================
        threat_map: Dict[Point, int] = {}
        for ept, eb in enemy_biomass_at.items():
            # enemy 站立格本身也算威胁（它不动就可以 fight）
            threat_map[ept] = max(threat_map.get(ept, 0), eb)
            # enemy 下一步可达格
            for nb in _neighbors4(ept):
                if _in_bounds(nb.x, nb.y, width, height):
                    threat_map[nb] = max(threat_map.get(nb, 0), eb)

        def threat_at(pt: Point) -> int:
            return threat_map.get(pt, 0)

        # =========================================================
        # 2) 小目标池（MoveTo fallback 用）
        # =========================================================
        targets: List[Point] = []
        for y in range(height):
            for x in range(width):
                pt = Point(x, y)
                if tile_owner(pt) != my_team_id:
                    targets.append(pt)

        targets.sort(key=lambda p: tile_value(p), reverse=True)
        top_k = 25
        top_targets = targets[:top_k] if targets else []

        if not top_targets:
            all_tiles = [Point(x, y) for y in range(height) for x in range(width)]
            all_tiles.sort(key=lambda p: tile_value(p), reverse=True)
            top_targets = all_tiles[:top_k]

        def pick_target_for_spore(sp: Spore) -> Optional[Point]:
            """在 top_targets 里找一个距离近、且不明显送死的目标。"""
            sp_pt = _pos_to_point(sp.position)
            best: Optional[Point] = None
            best_score = 10**9

            for t in top_targets:
                enemy_here = enemy_biomass_at.get(t, 0)
                # 目标点上有更强/相等 enemy：别撞
                if enemy_here > 0 and sp.biomass <= enemy_here:
                    continue
                # 目标点 threat 太高：也别硬送（粗略回避）
                if threat_at(t) >= sp.biomass:
                    continue

                d = _manhattan(sp_pt, t)
                score = d * 100 - tile_value(t)
                if score < best_score:
                    best_score = score
                    best = t
            return best

        # =========================================================
        # 3) 一单位一动作：强制约束
        # =========================================================
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

        # =========================================================
        # 4) Partition spores
        # =========================================================
        actionable_spores: List[Spore] = [s for s in my_team.spores if s.biomass >= 2]
        big_spores: List[Spore] = [s for s in my_team.spores if s.biomass >= 5]

        # =========================================================
        # Step 1) 先确保至少有一个 Spawner（生存 / 经济）
        # =========================================================
        if len(my_team.spawners) == 0:
            best_sp: Optional[Spore] = None
            best_score = -10**18

            for sp in actionable_spores:
                # 留一点余量：别把所有 biomass 都拿去造 spawner
                if sp.biomass < next_spawner_cost + 2:
                    continue

                pt = _pos_to_point(sp.position)

                # 非常关键：threat_map 比 adjacent_enemy 更“预判”
                danger_adj = adjacent_enemy_max(pt)
                danger_threat = threat_at(pt)

                score = tile_value(pt) * 3
                if tile_owner(pt) == my_team_id:
                    score += 50
                score -= danger_adj * 200
                score -= danger_threat * 120

                if score > best_score:
                    best_score = score
                    best_sp = sp

            # 非常保守：周围威胁太大就先不建（先跑/先防守）
            if best_sp is not None:
                pt = _pos_to_point(best_sp.position)
                if adjacent_enemy_max(pt) == 0 and threat_at(pt) == 0:
                    add_action_for_spore(best_sp.id, SporeCreateSpawnerAction(sporeId=best_sp.id))

        # =========================================================
        # Step 2) Defender 角色分配（轻量版）
        # - 如果 spawner 周围有威胁：挑最近的 1~2 个 actionable spores 作为 Defender
        # - Defender 目标：回到 spawner 附近（radius=2）巡逻/挡人
        # =========================================================
        defender_ids: Set[str] = set()
        if my_team.spawners and actionable_spores:
            # 先判断“是否需要防守”：任意 spawner 附近有 threat
            spawner_pts = [_pos_to_point(s.position) for s in my_team.spawners]

            need_defense = False
            for spt in spawner_pts:
                # 站点 threat/邻近 threat
                if threat_at(spt) > 0 or adjacent_enemy_max(spt) > 0:
                    need_defense = True
                    break
                for nb in _neighbors4(spt):
                    if _in_bounds(nb.x, nb.y, width, height) and threat_at(nb) > 0:
                        need_defense = True
                        break
                if need_defense:
                    break

            if need_defense:
                # 给每个 spawner 分配 1 个 defender（如果 spores 足够，再尝试第 2 个）
                remaining = [s for s in actionable_spores]
                # 按 biomass 降序，优先用更强的当 defender（更能挡）
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
                    sp1 = pick_nearest_defender(spt, assigned)
                    if sp1 is not None:
                        assigned.add(sp1.id)
                        defender_ids.add(sp1.id)
                    # 如果 spores 很多，给同一个 spawner 再配一个
                    if len(actionable_spores) >= 6:
                        sp2 = pick_nearest_defender(spt, assigned)
                        if sp2 is not None:
                            assigned.add(sp2.id)
                            defender_ids.add(sp2.id)

        # =========================================================
        # Step 3) SpawnerProduceSpore：更智能的生产（但依旧便宜）
        # =========================================================
        # 中文注释：
        # - 如果 spawner 附近 threat 高：更倾向出 4（更能打/更耐打）
        # - 如果 spores 少：出 3
        # - 如果 nutrients 紧：出 2
        # - 如果我们打算马上建第二个 spawner：稍微留一点 nutrients（别全花光）
        reserve_nutrients = 0
        if len(my_team.spawners) >= 1 and len(my_team.spawners) < 2:
            # 很粗的“保留策略”：准备冲第二 spawner 的资金
            reserve_nutrients = 10

        for spawner in my_team.spawners:
            if spawner.id in used_spawners:
                continue

            spt = _pos_to_point(spawner.position)
            local_threat = max(threat_at(spt), adjacent_enemy_max(spt))
            # 也考虑 spawner 周围一圈的 threat
            for nb in _neighbors4(spt):
                if _in_bounds(nb.x, nb.y, width, height):
                    local_threat = max(local_threat, threat_at(nb))

            # 基础选择
            desired = 3 if len(my_team.spores) < 5 else 2

            # 有威胁时更偏向大一点
            if local_threat >= 3:
                desired = 4
            elif local_threat > 0:
                desired = max(desired, 3)

            # nutrients 充裕时出 3（保持行动力和扩张能力）
            if nutrients >= 14 and len(my_team.spores) < 10 and local_threat == 0:
                desired = 3

            # 保留一点 nutrients
            if nutrients - desired < reserve_nutrients:
                continue

            if nutrients >= desired:
                add_action_for_spawner(
                    spawner.id,
                    SpawnerProduceSporeAction(spawnerId=spawner.id, biomass=desired),
                )
                nutrients -= desired

        # =========================================================
        # Step 4) 第二个 Spawner（仍然非常保守）
        # =========================================================
        if len(my_team.spawners) >= 1 and len(my_team.spawners) < 2 and nutrients >= 45:
            best_sp: Optional[Spore] = None
            best_score = -10**18
            s0_pt = _pos_to_point(my_team.spawners[0].position)

            for sp in actionable_spores:
                if sp.id in used_spores:
                    continue
                if sp.biomass < next_spawner_cost + 4:
                    continue

                pt = _pos_to_point(sp.position)
                # threat/邻敌都不允许（保守）
                if threat_at(pt) > 0 or adjacent_enemy_max(pt) > 0:
                    continue

                score = tile_value(pt) * 2
                if tile_owner(pt) == my_team_id:
                    score += 30
                score += _manhattan(pt, s0_pt) * 5  # 分散一下
                if score > best_score:
                    best_score = score
                    best_sp = sp

            if best_sp is not None:
                add_action_for_spore(best_sp.id, SporeCreateSpawnerAction(sporeId=best_sp.id))

        # =========================================================
        # Step 5) Split（小军队时才做一次）
        # =========================================================
        if len(my_team.spores) < 6:
            for sp in sorted(big_spores, key=lambda s: s.biomass, reverse=True):
                if sp.id in used_spores:
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
                    # threat 太高也不 split 过去
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

        # =========================================================
        # Step 6) Limited BFS（小范围找“更好的第一步”）
        # =========================================================
        BFS_MAX_DEPTH = 6
        BFS_MAX_NODES = 220
        BFS_SPORE_LIMIT = 10

        def _approx_step_cost(from_pt: Point, to_pt: Point) -> int:
            """
            简化移动 cost：
            - 走到我方 trail（ownership==my 且 biomassGrid>=1）=> cost 0
            - 否则 => cost 1
            """
            if tile_owner(to_pt) == my_team_id and tile_biomass(to_pt) >= 1:
                return 0
            return 1

        def _score_tile_for_spore(sp: Spore, pt: Point, dist: int, path_cost: int, is_defender: bool) -> int:
            """
            BFS 扫到 tile 的打分：
            - Explorer：更看重 nutrient 与扩张
            - Defender：更看重离 spawner 近、以及安全（低 threat）
            """
            tv = tile_value(pt)
            owner = tile_owner(pt)
            tb = tile_biomass(pt)

            enemy_here = enemy_biomass_at.get(pt, 0)
            thr = threat_at(pt)

            # 不去必死点：threat >= 我方 biomass（粗略回避）
            if thr >= sp.biomass:
                return -10**9

            # 目标基础分
            score = 0

            # --- 战斗：只奖励稳赢的 fight；不稳赢直接否决 ---
            if enemy_here > 0:
                if sp.biomass <= enemy_here:
                    return -10**9
                score += 450 + (sp.biomass - enemy_here) * 5

            # --- 安全：threat 越高越危险 ---
            score -= thr * 45
            score -= adjacent_enemy_max(pt) * 25

            # --- Explorer 的经济/扩张 ---
            if not is_defender:
                score += tv * 2
                if owner != my_team_id:
                    score += 70
            else:
                # Defender：更看重“我方领地”与“靠近 spawner”
                score += (30 if owner == my_team_id else 0)
                # Defender 也不完全忽略 nutrient，但权重低
                score += tv // 2

            # --- 路径成本/距离惩罚：鼓励近处收益 ---
            score -= dist * 25
            score -= path_cost * 35

            # biomass==2 的 spore：尽量别踩 empty tile（会变 static）
            if sp.biomass == 2 and tb == 0 and _approx_step_cost(_pos_to_point(sp.position), pt) == 1:
                score -= 250

            # 不鼓励堆叠
            if my_biomass_at.get(pt, 0) > 0:
                score -= 20

            return score

        def limited_bfs_first_step(sp: Spore, is_defender: bool, defend_center: Optional[Point]) -> Optional[Position]:
            """
            小 BFS 返回“第一步 direction”。
            - Explorer：找高分 tile
            - Defender：如果 defend_center 存在，就更偏向靠近它（通过打分体现）
            """
            start = _pos_to_point(sp.position)

            q: Deque[Tuple[Point, int, Optional[Position], int]] = deque()
            q.append((start, 0, None, 0))

            visited: Set[Point] = {start}
            best_dir: Optional[Position] = None
            best_score: int = -10**18

            nodes = 0
            while q:
                pt, depth, first_dir, path_cost = q.popleft()
                nodes += 1
                if nodes > BFS_MAX_NODES:
                    break

                if depth > 0 and first_dir is not None:
                    s = _score_tile_for_spore(sp, pt, depth, path_cost, is_defender)

                    # Defender：额外鼓励靠近 defend_center（轻量，不做大搜索）
                    if is_defender and defend_center is not None:
                        s -= _manhattan(pt, defend_center) * 35

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

                    # 不把“必死 threat”加入 BFS（避免路径穿过死亡点）
                    if threat_at(npt) >= sp.biomass:
                        continue

                    enemy_here = enemy_biomass_at.get(npt, 0)
                    if enemy_here > 0 and sp.biomass <= enemy_here:
                        continue

                    visited.add(npt)
                    ndir = first_dir if first_dir is not None else d
                    step_cost = _approx_step_cost(pt, npt)
                    q.append((npt, depth + 1, ndir, path_cost + step_cost))

            return best_dir

        # =========================================================
        # Step 7) Move / Fight：Defender 先回防，Explorer 再扩张
        # =========================================================
        # Defender 的“防守中心”：简单取第一个 spawner（也可以后续优化为就近 spawner）
        defend_center: Optional[Point] = None
        if my_team.spawners:
            defend_center = _pos_to_point(my_team.spawners[0].position)

        actionable_sorted = sorted(actionable_spores, key=lambda s: s.biomass, reverse=True)

        bfs_used = 0
        for sp in actionable_sorted:
            if sp.id in used_spores:
                continue

            is_defender = sp.id in defender_ids

            # Defender：如果已经在 spawner 半径 2 内，尽量“站住/微调”，别跑太远
            if is_defender and defend_center is not None:
                if _manhattan(_pos_to_point(sp.position), defend_center) <= 2:
                    # 如果当前位置 threat 不高，可以选择不动（减少无意义移动）
                    if threat_at(_pos_to_point(sp.position)) == 0:
                        continue

            best_dir: Optional[Position] = None

            # 7.1 limited BFS（限制数量）
            if bfs_used < BFS_SPORE_LIMIT:
                best_dir = limited_bfs_first_step(sp, is_defender=is_defender, defend_center=defend_center)
                bfs_used += 1

            # 7.2 fallback：一格评分
            if best_dir is None:
                pt = _pos_to_point(sp.position)
                best_score = -10**18

                for d in DIRS:
                    nx, ny = pt.x + d.x, pt.y + d.y
                    if not _in_bounds(nx, ny, width, height):
                        continue
                    npt = Point(nx, ny)

                    # threat 回避：threat >= biomass 不去
                    if threat_at(npt) >= sp.biomass:
                        continue

                    enemy_here = enemy_biomass_at.get(npt, 0)
                    if enemy_here > 0 and sp.biomass <= enemy_here:
                        continue

                    move_cost = 0 if (tile_owner(npt) == my_team_id and tile_biomass(npt) >= 1) else 1

                    score = 0
                    # Defender 更偏向靠近 defend_center
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

                    # 赢战斗加分
                    if enemy_here > 0 and sp.biomass > enemy_here:
                        score += 450 + (sp.biomass - enemy_here) * 5

                    # 安全扣分
                    score -= threat_at(npt) * 45
                    score -= adjacent_enemy_max(npt) * 25

                    # 避免 2-biomass 变 static
                    if sp.biomass == 2 and tile_biomass(npt) == 0 and move_cost == 1:
                        score -= 300

                    if my_biomass_at.get(npt, 0) > 0:
                        score -= 20

                    if score > best_score:
                        best_score = score
                        best_dir = d

            # 7.3 如果仍没有方向：Explorer 用 MoveTo 去高价值目标；Defender 用 MoveTo 回 spawner
            if best_dir is not None:
                add_action_for_spore(sp.id, SporeMoveAction(sporeId=sp.id, direction=best_dir))
            else:
                if is_defender and defend_center is not None:
                    add_action_for_spore(
                        sp.id,
                        SporeMoveToAction(sporeId=sp.id, position=Position(x=defend_center.x, y=defend_center.y)),
                    )
                else:
                    t = pick_target_for_spore(sp)
                    if t is not None:
                        add_action_for_spore(
                            sp.id,
                            SporeMoveToAction(sporeId=sp.id, position=Position(x=t.x, y=t.y)),
                        )

        return actions
