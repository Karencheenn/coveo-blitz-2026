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
    一个“能跑 + 稳”的 baseline bot（继续改进版）

    目标（先不追求最优）：
    - 尽快造出至少 1 个 Spawner（避免被 Elimination）
    - 用 SpawnerProduceSporeAction 维持可行动 Spores（2+ biomass）
    - 用“局部 BFS”在小范围内找更好的扩张方向（注意：BFS 深度/节点数都很小，避免 websocket 提前关闭）
    - Combat 只打稳赢的（deterministic combat）
    - 避免 biomass==2 的 spore 走到 empty tile 直接变 static
    """

    def __init__(self):
        print("Initializing improved bot (limited BFS)")

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

        # -------- 建一些“快速索引” --------
        # 记录每个 tile 上最大 enemy spore biomass（用于粗略 fight 判断）
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

        # -------- 小目标池（用于 MoveTo fallback）--------
        # 只取 top_k 个高 nutrientGrid 且不是我方 ownership 的 tile
        targets: List[Point] = []
        for y in range(height):
            for x in range(width):
                pt = Point(x, y)
                if tile_owner(pt) != my_team_id:
                    targets.append(pt)

        targets.sort(key=lambda p: tile_value(p), reverse=True)
        top_k = 25
        top_targets = targets[:top_k] if targets else []

        # 如果全图都属于我方（很少见），就退化为全图 top nutrient tiles
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
                # 如果目标 tile 上有更强/相等 enemy，别直接往里撞
                if enemy_here > 0 and sp.biomass <= enemy_here:
                    continue

                d = _manhattan(sp_pt, t)
                # 越近越好；nutrient 越高越好
                score = d * 100 - tile_value(t)
                if score < best_score:
                    best_score = score
                    best = t
            return best

        # -------- 一单位一动作：强制约束 --------
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

        # -------- Partition spores --------
        actionable_spores: List[Spore] = [s for s in my_team.spores if s.biomass >= 2]
        big_spores: List[Spore] = [s for s in my_team.spores if s.biomass >= 5]

        # =========================================================
        # Step 1) 先确保至少有一个 Spawner（生存 / 经济）
        # =========================================================
        if len(my_team.spawners) == 0:
            best_sp: Optional[Spore] = None
            best_score = -10**18

            for sp in actionable_spores:
                # 这里留一点余量：别把所有 biomass 都拿去造 spawner
                if sp.biomass < next_spawner_cost + 2:
                    continue

                pt = _pos_to_point(sp.position)
                danger = adjacent_enemy_max(pt)

                # 简单选址：高 nutrient + 我方 ownership + 周围无敌人
                score = tile_value(pt) * 3
                if tile_owner(pt) == my_team_id:
                    score += 50
                score -= danger * 200

                if score > best_score:
                    best_score = score
                    best_sp = sp

            # 非常保守：周围有敌人就先别造
            if best_sp is not None:
                pt = _pos_to_point(best_sp.position)
                if adjacent_enemy_max(pt) == 0:
                    add_action_for_spore(best_sp.id, SporeCreateSpawnerAction(sporeId=best_sp.id))

        # =========================================================
        # Step 2) 用 SpawnerProduceSporeAction 生产（保持 army/扩张）
        # =========================================================
        for spawner in my_team.spawners:
            if spawner.id in used_spawners:
                continue

            # 简单规则：前期优先 biomass=3（更耐用、更容易保持 actionable）
            desired = 3 if len(my_team.spores) < 4 else 2

            # 如果 nutrients 多一点，持续出 3
            if nutrients >= 12 and len(my_team.spores) < 10:
                desired = 3

            if nutrients >= desired:
                add_action_for_spawner(
                    spawner.id,
                    SpawnerProduceSporeAction(spawnerId=spawner.id, biomass=desired),
                )
                nutrients -= desired

        # =========================================================
        # Step 3) 第二个 Spawner（仍然非常保守）
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
                if adjacent_enemy_max(pt) > 0:
                    continue

                score = tile_value(pt) * 2
                if tile_owner(pt) == my_team_id:
                    score += 30
                score += _manhattan(pt, s0_pt) * 5  # 简单分散一下
                if score > best_score:
                    best_score = score
                    best_sp = sp

            if best_sp is not None:
                add_action_for_spore(best_sp.id, SporeCreateSpawnerAction(sporeId=best_sp.id))

        # =========================================================
        # Step 4) Split（可选，小军队时才做一次）
        # =========================================================
        # 中文注释：Split 很容易导致“单位变多但都很弱”，所以这里只做一次、并且只往高 nutrient 的空地 split。
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
                    # 只考虑 empty tile（tile_biomass==0）来快速扩张
                    if tile_biomass(npt) != 0:
                        continue

                    enemy_here = enemy_biomass_at.get(npt, 0)
                    if enemy_here >= sp.biomass:
                        continue

                    score = tile_value(npt)
                    if tile_owner(npt) != my_team_id:
                        score += 30
                    # 周围有敌人就扣分
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
        # Step 5) Limited BFS：小范围内找“更好的第一步”
        # =========================================================
        # 重点：BFS 要小！避免超时/导致 websocket 关闭
        # - max_depth 很小（建议 5~7）
        # - max_nodes 限制总访问节点数（建议 150~300）
        # - 只对前 N 个 spores 做 BFS（避免每 tick 计算爆炸）
        BFS_MAX_DEPTH = 6
        BFS_MAX_NODES = 220
        BFS_SPORE_LIMIT = 10

        def _approx_step_cost(from_pt: Point, to_pt: Point) -> int:
            """
            简化移动 cost：
            - 如果走到我方 trail（ownership==my 且 biomassGrid>=1）=> cost 0
            - 否则 => cost 1（相当于走到 empty/new ground 或敌方地）
            """
            if tile_owner(to_pt) == my_team_id and tile_biomass(to_pt) >= 1:
                return 0
            return 1

        def _score_tile_for_spore(sp: Spore, pt: Point, dist: int, path_cost: int) -> int:
            """
            给 BFS 扫到的 tile 打分：越高越想去。
            这不是最优，只是“更像样”的 heuristic。
            """
            tv = tile_value(pt)
            owner = tile_owner(pt)
            tb = tile_biomass(pt)
            enemy_here = enemy_biomass_at.get(pt, 0)

            score = 0

            # 经济：nutrient 高的 tile 更有价值
            score += tv * 2

            # 扩张：不是我方的更想抢
            if owner != my_team_id:
                score += 70

            # 战斗：只奖励稳赢的 fight；不稳赢直接极低（避免 BFS 把你带进死亡格）
            if enemy_here > 0:
                if sp.biomass <= enemy_here:
                    return -10**9
                score += 450 + (sp.biomass - enemy_here) * 5

            # 安全：周围敌人越多越危险
            score -= adjacent_enemy_max(pt) * 25

            # 路径成本/距离：越远越扣（鼓励近处快速收益）
            score -= dist * 25
            score -= path_cost * 35

            # biomass==2 的 spore：尽量别踩 empty tile（会变 static）
            # BFS 中用 tb==0 作为“可能会花 1 biomass”的强信号
            if sp.biomass == 2 and tb == 0 and _approx_step_cost(_pos_to_point(sp.position), pt) == 1:
                score -= 250

            # 不鼓励堆叠到我方已有 spore 的 tile（轻微扣分）
            if my_biomass_at.get(pt, 0) > 0:
                score -= 20

            return score

        def limited_bfs_first_step(sp: Spore) -> Optional[Position]:
            """
            在 spore 周围做一个很小的 BFS，选一个“目标 tile 分数最高”的点，
            并返回到达该点的第一步 direction。

            注意：我们不是在 BFS 中找“最短路”，而是扫一圈后找“综合得分最佳”的 tile。
            """
            start = _pos_to_point(sp.position)

            # BFS 队列元素：(point, depth, first_dir, path_cost)
            q: Deque[Tuple[Point, int, Optional[Position], int]] = deque()
            q.append((start, 0, None, 0))

            visited: Set[Point] = set([start])

            best_dir: Optional[Position] = None
            best_score: int = -10**18

            nodes = 0
            while q:
                pt, depth, first_dir, path_cost = q.popleft()
                nodes += 1
                if nodes > BFS_MAX_NODES:
                    break

                # 给当前 tile 打分（start 本身也可打分，但 first_dir=None 不会动）
                if depth > 0 and first_dir is not None:
                    s = _score_tile_for_spore(sp, pt, depth, path_cost)
                    if s > best_score:
                        best_score = s
                        best_dir = first_dir

                # 到达最大深度就不扩展
                if depth >= BFS_MAX_DEPTH:
                    continue

                # 扩展四邻
                for d in DIRS:
                    nx, ny = pt.x + d.x, pt.y + d.y
                    if not _in_bounds(nx, ny, width, height):
                        continue
                    npt = Point(nx, ny)
                    if npt in visited:
                        continue

                    # 这里不把 tile 当“墙”，因为地图一般可走；
                    # 但如果这个 tile 上有强敌（>=我方 biomass），就不要把它加入搜索（避免路径穿过死亡点）
                    enemy_here = enemy_biomass_at.get(npt, 0)
                    if enemy_here > 0 and sp.biomass <= enemy_here:
                        continue

                    visited.add(npt)

                    # first_dir：从 start 走出去的第一步方向
                    ndir = first_dir if first_dir is not None else d

                    # path_cost：粗略累计移动成本（不是严格真实成本，但足够当 heuristic）
                    step_cost = _approx_step_cost(pt, npt)
                    q.append((npt, depth + 1, ndir, path_cost + step_cost))

            return best_dir

        # =========================================================
        # Step 6) Move / Fight：先尝试 BFS 给的“更聪明第一步”，否则 fallback
        # =========================================================
        # 中文注释：为了控制计算量，我们只对前 BFS_SPORE_LIMIT 个 actionable spores 做 BFS。
        # 其余 spores 继续用“局部一格评分 + MoveTo fallback”。
        actionable_sorted = sorted(actionable_spores, key=lambda s: s.biomass, reverse=True)

        bfs_used = 0
        for sp in actionable_sorted:
            if sp.id in used_spores:
                continue

            # 6.1 先尝试 limited BFS
            best_dir: Optional[Position] = None
            if bfs_used < BFS_SPORE_LIMIT:
                best_dir = limited_bfs_first_step(sp)
                bfs_used += 1

            # 6.2 如果 BFS 没给出方向，就用“一格评分”找 best neighbor
            if best_dir is None:
                pt = _pos_to_point(sp.position)
                best_score = -10**18
                for d in DIRS:
                    nx, ny = pt.x + d.x, pt.y + d.y
                    if not _in_bounds(nx, ny, width, height):
                        continue
                    npt = Point(nx, ny)

                    enemy_here = enemy_biomass_at.get(npt, 0)
                    # 只打稳赢
                    if enemy_here > 0 and sp.biomass <= enemy_here:
                        continue

                    # 简单 move cost
                    move_cost = 0 if (tile_owner(npt) == my_team_id and tile_biomass(npt) >= 1) else 1

                    score = 0
                    score += tile_value(npt) * 2
                    if tile_owner(npt) != my_team_id:
                        score += 60
                    if move_cost == 0:
                        score += 15

                    # 赢战斗加分
                    if enemy_here > 0 and sp.biomass > enemy_here:
                        score += 450 + (sp.biomass - enemy_here) * 5

                    # 避免 2-biomass 变 static
                    if sp.biomass == 2 and tile_biomass(npt) == 0 and move_cost == 1:
                        score -= 300

                    # 周围敌人扣分
                    score -= adjacent_enemy_max(npt) * 25

                    if my_biomass_at.get(npt, 0) > 0:
                        score -= 20

                    if score > best_score:
                        best_score = score
                        best_dir = d

            # 6.3 如果有方向，就 SporeMove；否则 MoveTo 一个高价值目标
            if best_dir is not None:
                add_action_for_spore(sp.id, SporeMoveAction(sporeId=sp.id, direction=best_dir))
            else:
                t = pick_target_for_spore(sp)
                if t is not None:
                    add_action_for_spore(
                        sp.id,
                        SporeMoveToAction(sporeId=sp.id, position=Position(x=t.x, y=t.y)),
                    )

        return actions
