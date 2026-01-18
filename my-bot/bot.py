import random
from typing import Dict, List, Optional, Set, Tuple

from game_message import *


class Bot:
    def __init__(self):
        print("=== Initializing optimized Ecosystem Dominance bot ===")
        self.explored_tiles: Set[Tuple[int, int]] = set()
        # (x, y, nutrient_value)
        self.expansion_targets: List[Tuple[int, int, int]] = []
        self.pending_actions: List[Action] = []  # Track actions from this tick
        # Positions where spores will be spawned
        self.spawner_occupancy: Set[Tuple[int, int]] = set()
        # spore_id -> (x, y) where it will move
        self.spore_destinations: dict = {}

        # Cache for expansion targets per spore
        # spore_id -> (target_x, target_y, tick)
        self.expansion_cache: dict = {}
        self.cache_duration = 5  # How many ticks to keep cached target
        self.spore_actions_taken: Set[str] = set()
        self.spore_index_map: Dict[str, int] = {}

        # 🔥 鞭子策略变量
        self.whip_direction = "right"  # 当前鞭子挥动方向
        self.whip_ticks = 0  # 当前方向已持续的tick数
        self.whip_duration = 35  # 每个方向持续的tick数
        self.max_spores = 30  # 最大孢子数
        self.min_nutrients = 5  # 最小养分阈值

    def get_next_move(self, game_message: TeamGameState) -> list[Action]:
        """Advanced strategy combining expansion, economy, and combat."""
        try:
            print(f"\n=== TICK {game_message.tick} ===")

            # Reset tracking for this tick
            self.pending_actions = []
            self.spawner_occupancy = set()
            self.spore_destinations = {}
            self.spore_actions_taken = set()

            actions = []
            my_team: TeamInfo = game_message.world.teamInfos[game_message.yourTeamId]
            world = game_message.world
            self.spore_index_map = {
                spore.id: idx for idx, spore in enumerate(my_team.spores, start=1)
            }

            # 🔥 更新鞭子方向
            self.whip_ticks += 1
            if self.whip_ticks >= self.whip_duration:
                self.whip_ticks = 0
                directions = ["right", "down", "left", "up"]
                current_idx = directions.index(self.whip_direction)
                self.whip_direction = directions[(current_idx + 1) % 4]
                print(f"🔥 鞭子转向: {self.whip_direction}")

            print(
                f"Nutrients: {my_team.nutrients}, Spawners: {len(my_team.spawners)}, Spores: {len(my_team.spores)}")
            print(f"Next spawner cost: {my_team.nextSpawnerCost}")
            print(f"Team alive: {my_team.isAlive}")
            print(
                f"🔥 鞭子方向: {self.whip_direction} ({self.whip_ticks}/{self.whip_duration})")
            if game_message.tick == 1 and len(my_team.spawners) == 0:
                print(
                    "❗ Warning: Tick 1 reached without a spawner. This indicates the opening turn failed to create one.")

            # Check for errors from last tick
            if game_message.lastTickErrors:
                print(
                    f"❌ ERRORS from last tick: {game_message.lastTickErrors}")

            # Initialize expansion targets on first tick
            if game_message.tick == 0:
                self._analyze_map(world.map)
                print(
                    f"Analyzed map: found {len(self.expansion_targets)} valuable tiles")

            # Phase 1: Manage spawners - produce spores
            if not my_team.spawners:
                forced_action = self._force_spawner_creation(
                    my_team, world.map)
                if forced_action:
                    actions.append(forced_action)
                    self.spore_actions_taken.add(forced_action.sporeId)
                    print("🔧 Forced spawner creation due to absence.")
            spawner_actions = self._manage_spawners(
                my_team, world, game_message)
            actions.extend(spawner_actions)
            print(f"Spawner actions: {len(spawner_actions)}")

            # Phase 2: Manage spores - expansion, combat, and spawner creation
            spore_actions = self._manage_spores(my_team, world, game_message)
            actions.extend(spore_actions)
            print(f"Spore actions: {len(spore_actions)}")

            print(f"Total actions this tick: {len(actions)}")

            # Verify all actions are valid
            if len(actions) == 0:
                print("⚠️ WARNING: No actions generated this tick!")

            return actions

        except Exception as e:
            print(f"💥 CRITICAL ERROR in get_next_move: {e}")
            import traceback
            traceback.print_exc()
            return []  # Return empty list instead of crashing

    def _analyze_map(self, game_map: GameMap):
        """Analyze map to find high-value tiles for expansion."""
        self.expansion_targets = []
        print(f"Map dimensions: {game_map.width} x {game_map.height}")
        print(
            f"NutrientGrid dimensions: {len(game_map.nutrientGrid)} rows x {len(game_map.nutrientGrid[0]) if game_map.nutrientGrid else 0} cols")

        # Correct indexing: nutrientGrid[row][col] = nutrientGrid[y][x]
        for y in range(len(game_map.nutrientGrid)):
            for x in range(len(game_map.nutrientGrid[y])):
                nutrient_value = game_map.nutrientGrid[y][x]
                if nutrient_value > 0:
                    self.expansion_targets.append((x, y, nutrient_value))
                    if len(self.expansion_targets) <= 20:  # Debug first 20 tiles
                        print(f"  Tile ({x},{y}): {nutrient_value} nutrients")

        # Sort by nutrient value (descending)
        self.expansion_targets.sort(key=lambda t: t[2], reverse=True)
        print(f"\nTop 10 highest-value tiles:")
        for i, (x, y, val) in enumerate(self.expansion_targets[:10]):
            print(f"  {i+1}. Position ({x},{y}): {val} nutrients")
        print(f"Total tiles with nutrients: {len(self.expansion_targets)}")

    def _get_nutrient_value(self, x: int, y: int, game_map: GameMap) -> int:
        """Safely get nutrient value at position. Grid is indexed as [y][x]."""
        try:
            if 0 <= y < len(game_map.nutrientGrid) and 0 <= x < len(game_map.nutrientGrid[y]):
                value = game_map.nutrientGrid[y][x]
                return value
        except (IndexError, TypeError) as e:
            print(f"      ERROR accessing nutrient at ({x},{y}): {e}")
        return 0

    def _spore_label(self, spore: Spore) -> str:
        idx = self.spore_index_map.get(spore.id)
        return f"Spore #{idx}" if idx is not None else "Spore"

    def _force_spawner_creation(self, my_team: TeamInfo, game_map: GameMap) -> Optional[Action]:
        """Ensure we have at least one spawner by converting the best candidate."""
        cost = max(2, my_team.nextSpawnerCost or 0)
        best_spore: Optional[Spore] = None
        best_value = -1
        for spore in my_team.spores:
            if spore.biomass < cost:
                continue
            value = self._get_nutrient_value(
                spore.position.x, spore.position.y, game_map)
            if value > best_value:
                best_value = value
                best_spore = spore
        if best_spore:
            label = self._spore_label(best_spore)
            print(
                f"    Forcing spawner creation with {label} at ({best_spore.position.x},{best_spore.position.y}), nutrient={best_value}")
            return SporeCreateSpawnerAction(sporeId=best_spore.id)
        print("    ⚠️ No eligible spore available for forced spawner creation.")
        return None

    def _manage_spawners(self, my_team: TeamInfo, world: GameWorld, game_message: TeamGameState) -> List[Action]:
        """Manage spawner production - ONE ACTION PER SPAWNER."""
        actions = []

        try:
            available_nutrients = my_team.nutrients

            for spawner in my_team.spawners:
                print(
                    f"  Spawner {spawner.id} at ({spawner.position.x}, {spawner.position.y})")

                # Check if there's already a spore at spawner location (from game state)
                spore_at_spawner = None
                for spore in my_team.spores:
                    if spore.position.x == spawner.position.x and spore.position.y == spawner.position.y:
                        spore_at_spawner = spore
                        break

                if spore_at_spawner:
                    label = self._spore_label(spore_at_spawner)
                    print(
                        f"    {label} already at spawner (biomass: {spore_at_spawner.biomass})")
                    continue

                # Check if we're about to spawn a spore here in this tick (from pending actions)
                spawner_pos = (spawner.position.x, spawner.position.y)
                if spawner_pos in self.spawner_occupancy:
                    print(f"    Already producing at this spawner this tick")
                    continue

                desired_biomass = 15  # Fixed biomass size

                if available_nutrients >= desired_biomass:
                    print(
                        f"    ✓ Producing spore with biomass {desired_biomass}")
                    action = SpawnerProduceSporeAction(
                        spawnerId=spawner.id,
                        biomass=desired_biomass
                    )
                    actions.append(action)
                    # Mark as occupied for this tick
                    self.spawner_occupancy.add(spawner_pos)
                    available_nutrients -= desired_biomass  # Track locally
            else:
                print(
                    f"    ✗ Not enough nutrients (have {available_nutrients}, need {desired_biomass})")

        except Exception as e:
            print(f"💥 ERROR in _manage_spawners: {e}")
            import traceback
            traceback.print_exc()

        return actions

    def _manage_spores(self, my_team: TeamInfo, world: GameWorld, game_message: TeamGameState) -> List[Action]:
        """Manage all spores - 鞭子式移动策略。"""
        actions = []

        try:
            enemy_positions = self._get_enemy_positions(world, my_team.teamId)
            neutral_positions = self._get_neutral_positions(world)

            print(f"  Managing {len(my_team.spores)} spores")
            print(
                f"  Detected {len(enemy_positions)} enemy spores, {len(neutral_positions)} neutral spores")

            # 🔥 按鞭子方向排序孢子
            sorted_spores = self._sort_spores_for_whip(my_team.spores)

            print("  Total spores: ", len(sorted_spores))
            for idx, spore in enumerate(sorted_spores, start=1):
                try:
                    label = f"Spore #{idx}"
                    is_leader = (idx <= 5)  # 前5个是领头的

                    if idx <= 5:
                        print(
                            f"  {label} at ({spore.position.x}, {spore.position.y}), biomass: {spore.biomass}")
                    if spore.id in self.spore_actions_taken:
                        continue

                    # Skip spores with insufficient biomass
                    if spore.biomass < 2:
                        print(
                            f"    ✗ {label} insufficient biomass ({spore.biomass} < 2)")
                        continue

                    # Decision 1: Create spawner?
                    if self._should_create_spawner(spore, my_team, world, game_message):
                        print(f"    ✓ {label} creating spawner")
                        actions.append(
                            SporeCreateSpawnerAction(sporeId=spore.id))
                        self.spore_actions_taken.add(spore.id)
                        continue

                    # Decision 2: Combat with enemies?
                    enemy_action = self._handle_combat(
                        spore, enemy_positions, world)
                    if enemy_action:
                        print(f"    ✓ {label} engaging enemy")
                        actions.append(enemy_action)
                        self.spore_actions_taken.add(spore.id)
                        continue

                    # Decision 3: Attack weak neutrals if nearby?
                    neutral_action = self._handle_neutrals(
                        spore, neutral_positions, world)
                    if neutral_action:
                        print(f"    ✓ {label} attacking neutral spore")
                        actions.append(neutral_action)
                        self.spore_actions_taken.add(spore.id)
                        continue

                    # Decision 4: 🔥 鞭子式扩张
                    whip_move = self._get_whip_move(
                        spore, world, my_team, is_leader)
                    if whip_move:
                        role = "领头" if is_leader else "跟随"
                        new_x = spore.position.x + whip_move.x
                        new_y = spore.position.y + whip_move.y
                        print(f"    🔥 {label} ({role}) -> ({new_x}, {new_y})")
                        actions.append(SporeMoveAction(
                            sporeId=spore.id, direction=whip_move))
                        self.spore_actions_taken.add(spore.id)
                        self.spore_destinations[spore.id] = (new_x, new_y)
                    else:
                        print(f"    ✗ {label} has no valid expansion action")

                except Exception as e:
                    print(f"    💥 ERROR processing {label}: {e}")
                    continue

        except Exception as e:
            print(f"💥 ERROR in _manage_spores: {e}")
            import traceback
            traceback.print_exc()

        return actions

    def _should_create_spawner(self, spore: Spore, my_team: TeamInfo,
                               world: GameWorld, game_message: TeamGameState) -> bool:
        """Determine if a spore should create a spawner."""
        # Need enough biomass
        if spore.biomass < my_team.nextSpawnerCost:
            print(
                f"      Not enough biomass for spawner (have {spore.biomass}, need {my_team.nextSpawnerCost})")
            return False

        # Don't create too many spawners
        if len(my_team.spawners) >= 10:
            print(
                f"      Already have {len(my_team.spawners)} spawners (max 10)")
            return False

        # Check if we have enough nutrients accumulated (economic readiness)
        if my_team.nutrients < 100:
            print(
                f"      Not enough nutrients yet ({my_team.nutrients} < 100) to justify new spawner")
            return False

        # Check if far enough from other spawners (5 tiles minimum)
        if not self._is_good_spawner_location(spore.position, my_team.spawners):
            print(f"      Too close to existing spawner")
            return False

        # Get nutrient value at current position
        nutrient_value = self._get_nutrient_value(
            spore.position.x, spore.position.y, world.map)

        print(
            f"      ✓ Good spawner location at ({spore.position.x},{spore.position.y}): nutrient={nutrient_value}, cost={my_team.nextSpawnerCost}, team_nutrients={my_team.nutrients}")
        return True

    def _is_good_spawner_location(self, position: Position, spawners: List[Spawner]) -> bool:
        """Ensure new spawner is not too close to existing ones."""
        min_distance = 5
        for existing in spawners:
            dist = abs(existing.position.x - position.x) + \
                abs(existing.position.y - position.y)
            if dist < min_distance:
                return False
        return True

    def _handle_neutrals(self, spore: Spore, neutral_positions: List[Tuple[Position, int]],
                         world: GameWorld) -> Optional[Action]:
        """Attack neutral spores if we can win easily."""
        for neutral_pos, neutral_biomass in neutral_positions:
            distance = abs(spore.position.x - neutral_pos.x) + \
                abs(spore.position.y - neutral_pos.y)

            # Only attack if we're much stronger and it's close
            if distance <= 1 and spore.biomass > neutral_biomass + 50:
                print(
                    f"      ✓ Attacking neutral at distance {distance} (our {spore.biomass} vs their {neutral_biomass})")
                self.spore_destinations[spore.id] = (
                    neutral_pos.x, neutral_pos.y)
                return SporeMoveToAction(sporeId=spore.id, position=neutral_pos)

        return None
        """Check if position is far enough from other spawners."""
        min_distance = 8

        for spawner in spawners:
            distance = abs(position.x - spawner.position.x) + \
                abs(position.y - spawner.position.y)
            if distance < min_distance:
                print(
                    f"        Distance to spawner at ({spawner.position.x}, {spawner.position.y}): {distance} < {min_distance}")
                return False
        return True

    def _get_enemy_positions(self, world: GameWorld, my_team_id: str) -> List[Tuple[Position, int, str]]:
        """Get all enemy spore positions (excluding neutral spores)."""
        enemies = []
        neutral_team_id = world.teamInfos.get(list(world.teamInfos.keys())[
                                              0]).teamId if world.teamInfos else None

        # Find the actual neutral team ID from constants if available
        try:
            if hasattr(world, 'constants'):
                neutral_team_id = world.constants.neutralTeamId
        except:
            pass

        for spore in world.spores:
            # Skip our own spores
            if spore.teamId == my_team_id or spore.teamId == "":
                continue

            # Skip neutral spores - they don't move or attack
            if neutral_team_id and spore.teamId == neutral_team_id:
                continue

            # This is an actual enemy player spore
            enemies.append((spore.position, spore.biomass, spore.teamId))

        return enemies

    def _get_neutral_positions(self, world: GameWorld) -> List[Tuple[Position, int]]:
        """Get all neutral spore positions."""
        neutrals = []
        neutral_team_id = None

        # Find the actual neutral team ID
        try:
            if hasattr(world, 'constants'):
                neutral_team_id = world.constants.neutralTeamId
        except:
            pass

        if not neutral_team_id:
            return neutrals

        for spore in world.spores:
            if spore.teamId == neutral_team_id:
                neutrals.append((spore.position, spore.biomass))

        return neutrals

    def _handle_combat(self, spore: Spore, enemy_positions: List[Tuple[Position, int, str]],
                       world: GameWorld) -> Optional[Action]:
        """Engage enemy players if advantageous (not neutrals)."""
        for enemy_pos, enemy_biomass, enemy_team in enemy_positions:
            distance = abs(spore.position.x - enemy_pos.x) + \
                abs(spore.position.y - enemy_pos.y)

            # Attack if close and we're stronger
            if distance <= 4 and spore.biomass > enemy_biomass + 3:
                print(
                    f"      ✓ Attacking enemy at distance {distance} (our {spore.biomass} vs their {enemy_biomass})")
                self.spore_destinations[spore.id] = (enemy_pos.x, enemy_pos.y)
                return SporeMoveToAction(sporeId=spore.id, position=enemy_pos)

        return None

    def _expand_territory(self, spore: Spore, world: GameWorld,
                          my_team: TeamInfo, game_message: TeamGameState) -> Optional[Action]:
        """Expand to unclaimed or high-value territory - prefer SporeMoveToAction."""
        # Find best nearby target (with caching)
        best_target = self._find_best_expansion_target(
            spore, world, my_team.teamId, game_message.tick)

        if best_target:
            target_pos = Position(x=best_target[0], y=best_target[1])

            # Check if this target is already being targeted by another spore this tick
            if (best_target[0], best_target[1]) in self.spore_destinations.values():
                print(
                    f"      Target ({best_target[0]}, {best_target[1]}) already targeted by another spore")
                # Clear cache and try random move
                if spore.id in self.expansion_cache:
                    del self.expansion_cache[spore.id]
                return self._random_valid_move(spore, world)

            # Always use SporeMoveToAction - it finds optimal path automatically
            print(f"      Moving to target ({target_pos.x}, {target_pos.y})")
            self.spore_destinations[spore.id] = (target_pos.x, target_pos.y)
            return SporeMoveToAction(sporeId=spore.id, position=target_pos)

        # Fallback: move in a valid cardinal direction
        return self._random_valid_move(spore, world)

    def _get_next_step_towards(self, current: Position, target: Position, world: GameWorld) -> Optional[Tuple[int, int]]:
        """Calculate next step towards target (cardinal directions only)."""
        dx = target.x - current.x
        dy = target.y - current.y

        # Prioritize moving in the direction with larger distance
        if abs(dx) > abs(dy):
            # Move horizontally
            if dx > 0:
                return (current.x + 1, current.y)
            else:
                return (current.x - 1, current.y)
        elif dy != 0:
            # Move vertically
            if dy > 0:
                return (current.x, current.y + 1)
            else:
                return (current.x, current.y - 1)

        return None

    def _random_valid_move(self, spore: Spore, world: GameWorld) -> Optional[Action]:
        """Generate a random valid move in cardinal directions, avoiding conflicts."""
        directions = [
            Position(x=0, y=-1),  # up
            Position(x=0, y=1),   # down
            Position(x=-1, y=0),  # left
            Position(x=1, y=0),   # right
        ]

        valid_directions = []
        for direction in directions:
            new_x = spore.position.x + direction.x
            new_y = spore.position.y + direction.y

            # Check bounds
            if 0 <= new_x < world.map.width and 0 <= new_y < world.map.height:
                # Check if another spore is already moving here this tick
                if (new_x, new_y) not in self.spore_destinations.values():
                    valid_directions.append(direction)

        if valid_directions:
            chosen = random.choice(valid_directions)
            new_x = spore.position.x + chosen.x
            new_y = spore.position.y + chosen.y
            self.spore_destinations[spore.id] = (new_x, new_y)
            return SporeMoveAction(sporeId=spore.id, direction=chosen)

        return None

    def _find_best_expansion_target(self, spore: Spore, world: GameWorld,
                                    my_team_id: str, current_tick: int) -> Optional[Tuple[int, int]]:
        """Find best nearby uncontrolled tile - with caching."""

        # Check cache first
        if spore.id in self.expansion_cache:
            cached_target, cached_tick = self.expansion_cache[spore.id]
            if current_tick - cached_tick < self.cache_duration:
                # Verify cached target is still valid
                target_x, target_y = cached_target
                owner = world.ownershipGrid[target_y][target_x]
                biomass = world.biomassGrid[target_y][target_x]

                # If we've claimed it, clear cache
                if owner == my_team_id and biomass > 0:
                    print(
                        f"      Cached target ({target_x},{target_y}) now controlled, finding new target")
                    del self.expansion_cache[spore.id]
                else:
                    print(
                        f"      Using cached target: ({target_x},{target_y})")
                    return cached_target

        search_radius = 50
        print(
            f"      Searching for expansion target from ({spore.position.x}, {spore.position.y})")

        # Build list of candidates
        candidates = []
        checked_count = 0

        for dx in range(-search_radius, search_radius + 1):
            for dy in range(-search_radius, search_radius + 1):
                target_x = spore.position.x + dx
                target_y = spore.position.y + dy

                # Check bounds
                if not (0 <= target_x < world.map.width and 0 <= target_y < world.map.height):
                    continue

                # Calculate Manhattan distance
                distance = abs(dx) + abs(dy)
                if distance == 0 or distance > search_radius:
                    continue

                checked_count += 1

                # Get tile info
                owner = world.ownershipGrid[target_y][target_x]
                biomass_on_tile = world.biomassGrid[target_y][target_x]

                # Skip tiles we already control
                if owner == my_team_id and biomass_on_tile > 0:
                    continue

                # Skip tiles already targeted this tick
                if (target_x, target_y) in self.spore_destinations.values():
                    continue

                # Get nutrient value
                nutrient_value = self._get_nutrient_value(
                    target_x, target_y, world.map)

                # Only consider tiles with some value
                if nutrient_value <= 0:
                    continue

                # Calculate score
                score = nutrient_value * 10.0 / (distance + 1)

                # Bonus for uncontrolled tiles
                if owner != my_team_id or biomass_on_tile == 0:
                    score *= 2000.0

                candidates.append((target_x, target_y, score,
                                  nutrient_value, distance, owner))

        print(
            f"      Checked {checked_count} tiles, found {len(candidates)} candidates")

        # Pick best candidate
        if candidates:
            candidates.sort(key=lambda x: x[2], reverse=True)

            # Show top 3 candidates
            print(f"      Top 3 candidates:")
            for i, (x, y, score, nut, dist, owner) in enumerate(candidates[:3]):
                print(
                    f"        {i+1}. ({x},{y}): nutrient={nut}, dist={dist}, score={score:.1f}, owner={owner}")

            best = candidates[0]
            best_target = (best[0], best[1])

            # Cache the result
            self.expansion_cache[spore.id] = (best_target, current_tick)

            return best_target

        print(f"      No valid expansion target found")
        return None

    def _sort_spores_for_whip(self, spores: List[Spore]) -> List[Spore]:
        """按照鞭子方向排序孢子，形成链条。"""
        if not spores:
            return []

        # 根据当前鞭子方向排序
        if self.whip_direction == "right":
            # 最右边的先动
            return sorted(spores, key=lambda s: s.position.x, reverse=True)
        elif self.whip_direction == "down":
            # 最下方的先动
            return sorted(spores, key=lambda s: s.position.y, reverse=True)
        elif self.whip_direction == "left":
            # 最左边的先动
            return sorted(spores, key=lambda s: s.position.x)
        else:  # up
            # 最上方的先动
            return sorted(spores, key=lambda s: s.position.y)

    def _get_whip_move(self, spore: Spore, world: GameWorld, my_team: TeamInfo, is_leader: bool) -> Optional[Position]:
        """获取鞭子式移动方向。"""
        # 主要方向
        primary_dir = self._get_whip_primary_direction()

        # 尝试主方向
        nx = spore.position.x + primary_dir.x
        ny = spore.position.y + primary_dir.y

        if self._is_valid_move(nx, ny, spore, world, my_team.teamId):
            return primary_dir

        # 如果主方向不行，尝试侧向扩散
        side_dirs = self._get_whip_side_directions()
        random.shuffle(side_dirs)

        for side_dir in side_dirs:
            nx = spore.position.x + side_dir.x
            ny = spore.position.y + side_dir.y

            if self._is_valid_move(nx, ny, spore, world, my_team.teamId):
                return side_dir

        # 最后尝试任意方向
        all_dirs = [
            Position(x=0, y=-1), Position(x=0, y=1),
            Position(x=-1, y=0), Position(x=1, y=0)
        ]
        random.shuffle(all_dirs)

        for direction in all_dirs:
            nx = spore.position.x + direction.x
            ny = spore.position.y + direction.y

            if self._is_valid_move(nx, ny, spore, world, my_team.teamId):
                return direction

        return None

    def _get_whip_primary_direction(self) -> Position:
        """获取鞭子主方向。"""
        if self.whip_direction == "right":
            return Position(x=1, y=0)
        elif self.whip_direction == "down":
            return Position(x=0, y=1)
        elif self.whip_direction == "left":
            return Position(x=-1, y=0)
        else:  # up
            return Position(x=0, y=-1)

    def _get_whip_side_directions(self) -> List[Position]:
        """获取鞭子侧向方向。"""
        if self.whip_direction in ["right", "left"]:
            # 水平移动时，侧向是上下
            return [Position(x=0, y=-1), Position(x=0, y=1)]
        else:
            # 垂直移动时，侧向是左右
            return [Position(x=-1, y=0), Position(x=1, y=0)]

    def _is_valid_move(self, nx: int, ny: int, spore: Spore, world: GameWorld, my_team_id: str) -> bool:
        """检查移动是否有效。"""
        # 检查边界
        if not (0 <= nx < world.map.width and 0 <= ny < world.map.height):
            return False

        # 检查是否已被占用
        if (nx, ny) in self.spore_destinations.values():
            return False

        owner = world.ownershipGrid[ny][nx]
        biomass = world.biomassGrid[ny][nx]

        # 可以移动到空地或自己的领地
        if owner == "" or owner == my_team_id:
            return True

        # 可以攻击较弱的敌人
        if spore.biomass > biomass + 3:
            return True

        return False
