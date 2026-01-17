import random
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from game_message import *


class Bot:
    """Turn-based strategy bot for Ecosystem Dominance."""

    # --- Tunable strategy constants ---
    SEARCH_RADIUS = 20
    CACHE_DURATION = 5
    COLONIZER_BIOMASS = 3
    MIN_SPORE_BIOMASS = 2
    MAX_SPAWNERS = 10
    SPAWNER_SPACING = 5
    MIN_SPAWNER_NUTRIENTS = 100

    def __init__(self):
        print("=== Initializing reorganized Ecosystem Dominance bot ===")
        # Persistent across the match
        self.expansion_targets: List[Tuple[int, int, int]] = []
        self.expansion_cache: Dict[str, Tuple[Tuple[int, int], int]] = {}
        self.neutral_team_id: Optional[str] = "NEUTRAL"

        # Per-tick scratch state (set every tick)
        self.spawner_occupancy: Set[Tuple[int, int]] = set()
        self.assigned_targets: Set[Tuple[int, int]] = set()
        self.spore_actions_taken: Set[str] = set()
        self.spore_destinations: Dict[str, Tuple[int, int]] = {}
        self.spore_index_map: Dict[str, int] = {}

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #
    def get_next_move(self, game_message: TeamGameState) -> list[Action]:
        """Main entry point invoked each tick."""
        try:
            my_team, world = self._begin_tick(game_message)

            if game_message.tick == 0:
                self._analyze_map(world.map)

            actions: List[Action] = []

            forced = self._ensure_first_spawner(my_team, world)
            if forced:
                actions.append(forced)

            actions.extend(self._manage_spawners(my_team))
            actions.extend(self._manage_spores(my_team, world, game_message))

            if not actions:
                print("⚠️ WARNING: no actions generated this tick.")
            else:
                print(f"Generated {len(actions)} actions this tick.")

            return actions

        except Exception as exc:  # Fail-safe
            print(f"💥 CRITICAL ERROR in get_next_move: {exc}")
            import traceback

            traceback.print_exc()
            return []

    # ------------------------------------------------------------------ #
    # Tick orchestration helpers                                         #
    # ------------------------------------------------------------------ #
    def _begin_tick(self, game_message: TeamGameState) -> Tuple[TeamInfo, GameWorld]:
        """Reset per-tick state and capture frequently used references."""
        self.spawner_occupancy.clear()
        self.assigned_targets.clear()
        self.spore_actions_taken.clear()
        self.spore_destinations = {}

        if hasattr(game_message, "constants"):
            self.neutral_team_id = game_message.constants.neutralTeamId

        my_team = game_message.world.teamInfos[game_message.yourTeamId]
        self.spore_index_map = {spore.id: idx for idx, spore in enumerate(my_team.spores, start=1)}

        print(
            f"\n=== TICK {game_message.tick} === | Nutrients={my_team.nutrients} | "
            f"Spawners={len(my_team.spawners)} | Spores={len(my_team.spores)} | "
            f"NextSpawnerCost={my_team.nextSpawnerCost}"
        )
        if game_message.lastTickErrors:
            print(f"Last tick errors: {game_message.lastTickErrors}")

        return my_team, game_message.world

    def _analyze_map(self, game_map: GameMap) -> None:
        """Pre-compute high-nutrient tiles for quick heuristics."""
        self.expansion_targets.clear()
        for y, row in enumerate(game_map.nutrientGrid):
            for x, nutrient in enumerate(row):
                if nutrient > 0:
                    self.expansion_targets.append((x, y, nutrient))
        self.expansion_targets.sort(key=lambda t: t[2], reverse=True)
        top = ", ".join(f"({x},{y})={val}" for x, y, val in self.expansion_targets[:10])
        print(f"Map analysis complete. Top nutrient tiles: {top}")

    # ------------------------------------------------------------------ #
    # Spawner management                                                 #
    # ------------------------------------------------------------------ #
    def _ensure_first_spawner(self, my_team: TeamInfo, world: GameWorld) -> Optional[Action]:
        if my_team.spawners:
            return None
        candidate = self._pick_spawner_candidate(my_team.spores, my_team.nextSpawnerCost, world.map)
        if candidate:
            print(f"🔧 Forcing first spawner with {self._spore_label(candidate, 1)}")
            self.spore_actions_taken.add(candidate.id)
            return SporeCreateSpawnerAction(sporeId=candidate.id)
        print("⚠️ Unable to locate spore for first spawner.")
        return None

    def _pick_spawner_candidate(
        self,
        spores: List[Spore],
        next_cost: int,
        game_map: GameMap,
    ) -> Optional[Spore]:
        best_spore = None
        best_value = -1
        cost = max(2, next_cost or 0)
        for spore in spores:
            if spore.biomass < cost:
                continue
            score = self._get_nutrient_value(spore.position.x, spore.position.y, game_map)
            if score > best_value:
                best_value = score
                best_spore = spore
        return best_spore

    def _manage_spawners(self, my_team: TeamInfo) -> List[Action]:
        actions: List[Action] = []
        available_nutrients = my_team.nutrients

        for spawner in my_team.spawners:
            position = (spawner.position.x, spawner.position.y)
            if position in self.spawner_occupancy:
                print(f"  Spawner {spawner.id} already scheduled to act.")
                continue

            if available_nutrients < self.COLONIZER_BIOMASS:
                print(
                    f"  Spawner {spawner.id} skipped (nutrients {available_nutrients} < "
                    f"{self.COLONIZER_BIOMASS})."
                )
                continue

            actions.append(
                SpawnerProduceSporeAction(spawnerId=spawner.id, biomass=self.COLONIZER_BIOMASS)
            )
            self.spawner_occupancy.add(position)
            available_nutrients -= self.COLONIZER_BIOMASS
            print(f"  Spawner {spawner.id} producing biomass {self.COLONIZER_BIOMASS}.")

        return actions

    # ------------------------------------------------------------------ #
    # Spore management                                                   #
    # ------------------------------------------------------------------ #
    def _manage_spores(
        self,
        my_team: TeamInfo,
        world: GameWorld,
        game_message: TeamGameState,
    ) -> List[Action]:
        actions: List[Action] = []
        enemy_positions = self._get_enemy_positions(world, my_team.teamId, self.neutral_team_id)
        neutral_positions = self._get_neutral_positions(world)

        spores_sorted = sorted(my_team.spores, key=lambda sp: sp.id)
        for idx, spore in enumerate(spores_sorted, start=1):
            label = self._spore_label(spore, idx)
            try:
                if spore.id in self.spore_actions_taken:
                    continue
                if spore.biomass < self.MIN_SPORE_BIOMASS:
                    print(f"    {label} idle (biomass {spore.biomass} too low).")
                    continue

                if self._should_create_spawner(spore, my_team, world):
                    action = SporeCreateSpawnerAction(sporeId=spore.id)
                    actions.append(action)
                    self.spore_actions_taken.add(spore.id)
                    print(f"    {label} converts into a spawner.")
                    continue

                enemy_action = self._handle_combat(spore, enemy_positions)
                if enemy_action:
                    actions.append(enemy_action)
                    self.spore_actions_taken.add(spore.id)
                    print(f"    {label} engaging enemy.")
                    continue

                neutral_action = self._handle_neutrals(spore, neutral_positions)
                if neutral_action:
                    actions.append(neutral_action)
                    self.spore_actions_taken.add(spore.id)
                    print(f"    {label} attacking neutral spore.")
                    continue

                expand_action = self._expand_territory(spore, world, my_team.teamId, game_message.tick)
                if expand_action:
                    actions.append(expand_action)
                    self.spore_actions_taken.add(spore.id)
                    if isinstance(expand_action, SporeMoveAction):
                        direction = expand_action.direction
                        print(f"    {label} moving via SporeMove ({direction.x},{direction.y}).")
                    elif isinstance(expand_action, SporeMoveToAction):
                        pos = expand_action.position
                        print(f"    {label} moving toward ({pos.x},{pos.y}).")
                    else:
                        print(f"    {label} taking action {type(expand_action).__name__}.")
                else:
                    print(f"    {label} has no viable expansion action.")
            except Exception as exc:
                print(f"    💥 ERROR processing {label}: {exc}")

        return actions

    # ------------------------------------------------------------------ #
    # Spore helper decisions                                             #
    # ------------------------------------------------------------------ #
    def _should_create_spawner(self, spore: Spore, my_team: TeamInfo, world: GameWorld) -> bool:
        if spore.biomass < my_team.nextSpawnerCost:
            return False
        if len(my_team.spawners) >= self.MAX_SPAWNERS:
            return False
        if my_team.nutrients < self.MIN_SPAWNER_NUTRIENTS:
            return False
        if not self._is_good_spawner_location(spore.position, my_team.spawners):
            return False
        return True

    def _is_good_spawner_location(self, position: Position, spawners: List[Spawner]) -> bool:
        for existing in spawners:
            dist = abs(existing.position.x - position.x) + abs(existing.position.y - position.y)
            if dist < self.SPAWNER_SPACING:
                return False
        return True

    def _handle_combat(
        self,
        spore: Spore,
        enemies: List[Tuple[Position, int, str]],
    ) -> Optional[Action]:
        best_enemy = None
        best_score = None
        for enemy_pos, enemy_biomass, _ in enemies:
            distance = abs(spore.position.x - enemy_pos.x) + abs(spore.position.y - enemy_pos.y)
            if distance > 4 or spore.biomass <= enemy_biomass + 3:
                continue
            score = spore.biomass - enemy_biomass - distance
            if best_score is None or score > best_score:
                best_score = score
                best_enemy = enemy_pos
        if best_enemy:
            self._reserve_tile(spore.id, best_enemy.x, best_enemy.y)
            return SporeMoveToAction(sporeId=spore.id, position=best_enemy)
        return None

    def _handle_neutrals(
        self,
        spore: Spore,
        neutrals: List[Tuple[Position, int]],
    ) -> Optional[Action]:
        for neutral_pos, neutral_biomass in neutrals:
            distance = abs(spore.position.x - neutral_pos.x) + abs(spore.position.y - neutral_pos.y)
            if distance <= 5 and spore.biomass > neutral_biomass + 5:
                self._reserve_tile(spore.id, neutral_pos.x, neutral_pos.y)
                return SporeMoveToAction(sporeId=spore.id, position=neutral_pos)
        return None

    def _expand_territory(
        self,
        spore: Spore,
        world: GameWorld,
        my_team_id: str,
        current_tick: int,
    ) -> Optional[Action]:
        target = self._resolve_cached_target(spore, world, my_team_id, current_tick)
        if not target:
            target = self._bfs_best_target(spore, world, my_team_id)
            if target:
                self.expansion_cache[spore.id] = (target, current_tick)

        if not target:
            return self._random_valid_move(spore, world)

        self._reserve_tile(spore.id, target[0], target[1])
        return SporeMoveToAction(sporeId=spore.id, position=Position(x=target[0], y=target[1]))

    def _resolve_cached_target(
        self,
        spore: Spore,
        world: GameWorld,
        my_team_id: str,
        current_tick: int,
    ) -> Optional[Tuple[int, int]]:
        cached = self.expansion_cache.get(spore.id)
        if not cached:
            return None
        (target_x, target_y), cached_tick = cached
        if current_tick - cached_tick >= self.CACHE_DURATION:
            del self.expansion_cache[spore.id]
            return None
        owner = world.ownershipGrid[target_y][target_x]
        if owner == my_team_id:
            del self.expansion_cache[spore.id]
            return None
        if (target_x, target_y) in self.assigned_targets:
            return None
        print(f"      Using cached target ({target_x},{target_y}) for spore {spore.id}")
        return (target_x, target_y)

    def _bfs_best_target(
        self,
        spore: Spore,
        world: GameWorld,
        my_team_id: str,
    ) -> Optional[Tuple[int, int]]:
        queue = deque([(spore.position.x, spore.position.y, 0)])
        visited = {(spore.position.x, spore.position.y)}
        best_score = float("-inf")
        best_tile: Optional[Tuple[int, int]] = None
        directions = [(0, -1), (0, 1), (-1, 0), (1, 0)]  # up, down, left, right
        checked = 0

        while queue:
            x, y, distance = queue.popleft()
            if distance >= self.SEARCH_RADIUS:
                continue
            for dx, dy in directions:
                nx, ny = x + dx, y + dy
                ndistance = distance + 1
                if not (0 <= nx < world.map.width and 0 <= ny < world.map.height):
                    continue
                if ndistance > self.SEARCH_RADIUS or (nx, ny) in visited:
                    continue
                visited.add((nx, ny))
                queue.append((nx, ny, ndistance))

                owner = world.ownershipGrid[ny][nx]
                if owner == my_team_id:
                    continue
                if (nx, ny) in self.assigned_targets:
                    continue

                checked += 1
                nutrient = self._get_nutrient_value(nx, ny, world.map)
                score = self._score_tile(nutrient, owner, ndistance)
                if score > best_score:
                    best_score = score
                    best_tile = (nx, ny)

        print(f"      BFS evaluated {checked} tiles.")
        if best_tile:
            print(f"      Best tile {best_tile} with score {best_score:.2f}")
        return best_tile

    # ------------------------------------------------------------------ #
    # Utility helpers                                                    #
    # ------------------------------------------------------------------ #
    def _score_tile(
        self,
        nutrient: int,
        owner: Optional[str],
        distance: int,
    ) -> float:
        base = (nutrient + 1) * 10.0 / (distance + 1)
        if owner is None: # Unowned
            base *= 3.0
        elif owner == self.neutral_team_id:
            base *= 0.5
        else:
            base *= 0.2 # Enemy owned

        base += nutrient * 0.5
        return base

    def _reserve_tile(self, spore_id: str, x: int, y: int) -> None:
        self.spore_destinations[spore_id] = (x, y)
        self.assigned_targets.add((x, y))

    def _random_valid_move(self, spore: Spore, world: GameWorld) -> Optional[Action]:
        directions = [
            Position(x=0, y=-1),
            Position(x=0, y=1),
            Position(x=-1, y=0),
            Position(x=1, y=0),
        ]
        random.shuffle(directions)
        for direction in directions:
            nx = spore.position.x + direction.x
            ny = spore.position.y + direction.y
            if 0 <= nx < world.map.width and 0 <= ny < world.map.height:
                if (nx, ny) not in self.assigned_targets:
                    self._reserve_tile(spore.id, nx, ny)
                    return SporeMoveAction(sporeId=spore.id, direction=direction)
        return None

    def _get_enemy_positions(
        self,
        world: GameWorld,
        my_team_id: str,
        neutral_team_id: Optional[str] = None,
    ) -> List[Tuple[Position, int, str]]:
        enemies = []
        neutral_id = neutral_team_id if neutral_team_id is not None else self.neutral_team_id
        for spore in world.spores:
            if spore.teamId == my_team_id or not spore.teamId:
                continue
            if neutral_id and spore.teamId == neutral_id:
                continue
            enemies.append((spore.position, spore.biomass, spore.teamId))
        return enemies

    def _get_neutral_positions(self, world: GameWorld) -> List[Tuple[Position, int]]:
        neutrals: List[Tuple[Position, int]] = []
        if not self.neutral_team_id:
            return neutrals
        for spore in world.spores:
            if spore.teamId == self.neutral_team_id:
                neutrals.append((spore.position, spore.biomass))
        return neutrals

    def _spore_label(self, spore: Spore, idx: int) -> str:
        return f"Spore #{idx} (id={spore.id})"

    def _get_nutrient_value(self, x: int, y: int, game_map: GameMap) -> int:
        try:
            return game_map.nutrientGrid[y][x]
        except (IndexError, TypeError):
            return 0
