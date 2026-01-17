import random
from game_message import *
from typing import Optional, Tuple, List, Set
from collections import deque
import math


class Bot:
    def __init__(self):
        print("Initializing Advanced Ecosystem Dominance Bot")
        self.explored_tiles = set()
        self.enemy_spawner_locations = {}
        self.failed_moves = {}  # Track stuck spores
        self.expansion_vectors = {}  # Track which direction each spore should expand

    def get_next_move(self, game_message: TeamGameState) -> list[Action]:
        """Main bot logic - coordinates all strategies"""
        actions = []
        my_team: TeamInfo = game_message.world.teamInfos[game_message.yourTeamId]

        # CRITICAL: Survival check first!
        if self._is_about_to_die(my_team):
            print(
                f"EMERGENCY! Survival threatened at tick {game_message.tick}")
            return self._emergency_defense(game_message, my_team)

        # Clean up old tracking data
        self._cleanup_tracking(my_team)

        # Update exploration tracking
        for spore in my_team.spores:
            self.explored_tiles.add((spore.position.x, spore.position.y))

        # Track enemy positions
        self._track_enemies(game_message)

        # Determine game phase based on tick
        tick = game_message.tick

        if tick < 900:
            # Focus on economy and military strength
            actions = self._economy_military_phase(game_message, my_team)
        else:
            # Tick >= 900: Maximum expansion mode!
            print(f"FINAL EXPANSION MODE! Tick {tick}/1000")
            actions = self._final_expansion_phase(game_message, my_team)

        return actions

    def _is_about_to_die(self, my_team: TeamInfo) -> bool:
        """Check if we're about to be eliminated"""
        if len(my_team.spawners) > 0:
            return False  # Safe if we have spawners

        # Check if we have any actionable spores (biomass >= 2)
        max_spore_biomass = 0
        for spore in my_team.spores:
            max_spore_biomass = max(max_spore_biomass, spore.biomass)

        return max_spore_biomass < 2

    def _emergency_defense(self, game_message: TeamGameState, my_team: TeamInfo) -> list[Action]:
        """Last-ditch survival strategy"""
        actions = []

        # If we have ANY spores with 1 biomass, try to merge them
        if len(my_team.spores) > 1:
            # Find all spores and try to merge them
            target_pos = my_team.spores[0].position
            for spore in my_team.spores[1:]:
                actions.append(SporeMoveToAction(
                    sporeId=spore.id,
                    position=target_pos
                ))

        # If we have nutrients, try to create a spawner with any spore
        if my_team.spores and my_team.nutrients > 0:
            # Find spore with highest biomass
            best_spore = max(my_team.spores, key=lambda s: s.biomass)
            if best_spore.biomass >= my_team.nextSpawnerCost:
                actions.append(SporeCreateSpawnerAction(sporeId=best_spore.id))

        return actions

    def _economy_military_phase(self, game_message: TeamGameState, my_team: TeamInfo) -> list[Action]:
        """Tick < 900: Build economy (nutrients) and military (biomass)"""
        actions = []
        world = game_message.world
        tick = game_message.tick

        # Priority 1: Ensure we have spawners
        if len(my_team.spawners) == 0 and len(my_team.spores) > 0:
            best_spore = self._find_best_spawner_candidate(
                my_team.spores, world, game_message)
            if best_spore:
                actions.append(SporeCreateSpawnerAction(sporeId=best_spore.id))
                return actions

        # Priority 2: Split overly large spores to enable more expansion
        used_spores = set()
        for spore in my_team.spores:
            # If spore has >50 biomass before tick 800, it's too concentrated
            if spore.biomass > 50 and tick < 800:
                print(
                    f"Splitting large spore {spore.id[:8]} with {spore.biomass} biomass")
                # Split off a reasonable combat unit, keep rest moving
                split_amount = min(20, spore.biomass // 2)
                actions.append(SporeSplitAction(
                    sporeId=spore.id,
                    biomassForMovingSpore=split_amount,
                    direction=self._find_best_split_direction(
                        spore, world, game_message)
                ))
                used_spores.add(spore.id)

        # Priority 3: Attack weak neutral colonies blocking expansion
        neutral_attacks = self._attack_neutral_colonies(
            my_team, world, game_message, used_spores)
        actions.extend(neutral_attacks)

        # Priority 4: Build spawner network (while costs are reasonable)
        if my_team.nextSpawnerCost <= 31 and len(my_team.spawners) < 8:
            candidate = self._find_spawner_creation_candidate(
                my_team.spores, my_team.nextSpawnerCost, world, game_message)
            if candidate and candidate.id not in used_spores:
                print(
                    f"Creating spawner #{len(my_team.spawners)+1}, cost: {my_team.nextSpawnerCost}")
                actions.append(SporeCreateSpawnerAction(sporeId=candidate.id))
                used_spores.add(candidate.id)

        # Priority 5: Maintain spore production
        used_spawners = set()
        target_spores = 12 if tick < 400 else 8

        if len(my_team.spores) < target_spores:
            for spawner in my_team.spawners:
                if my_team.nutrients >= 5 and spawner.id not in used_spawners:
                    # Variable size based on phase
                    if tick < 300:
                        biomass = 5  # Small scouts
                    elif tick < 600:
                        biomass = 10  # Medium fighters for neutrals
                    else:
                        biomass = 15  # Larger units

                    if my_team.nutrients >= biomass:
                        actions.append(SpawnerProduceSporeAction(
                            spawnerId=spawner.id,
                            biomass=biomass
                        ))
                        used_spawners.add(spawner.id)
                        break

        # Priority 6: Invest excess nutrients into combat spores
        for spawner in my_team.spawners:
            if spawner.id not in used_spawners and my_team.nutrients >= 30:
                biomass = min(my_team.nutrients // 3, 25)  # Smaller investment
                actions.append(SpawnerProduceSporeAction(
                    spawnerId=spawner.id,
                    biomass=biomass
                ))
                used_spawners.add(spawner.id)
                break

        # Priority 7: Handle enemy combat threats
        combat_actions = self._handle_combat(
            my_team, world, game_message, used_spores)
        actions.extend(combat_actions)

        # Priority 8: Intelligent expansion
        expansion_actions = self._intelligent_expansion(
            my_team, world, game_message, used_spores)
        actions.extend(expansion_actions)

        return actions

    def _final_expansion_phase(self, game_message: TeamGameState, my_team: TeamInfo) -> list[Action]:
        """Tick >= 900: Maximum tile grab mode"""
        actions = []
        world = game_message.world
        used_spores = set()

        # Strategy: Split large spores into many small ones and scatter them

        # Priority 1: Split all large spores (biomass > 3)
        for spore in my_team.spores:
            if spore.biomass > 3 and spore.id not in used_spores:
                # Split into 2-biomass pieces
                # Keep 2 biomass moving, leave rest behind
                actions.append(SporeSplitAction(
                    sporeId=spore.id,
                    biomassForMovingSpore=2,
                    direction=self._find_best_split_direction(
                        spore, world, game_message)
                ))
                used_spores.add(spore.id)

        # Priority 2: Spend ALL nutrients on small spores
        for spawner in my_team.spawners:
            while my_team.nutrients >= 2:
                actions.append(SpawnerProduceSporeAction(
                    spawnerId=spawner.id,
                    biomass=2  # Minimum actionable size
                ))
                my_team.nutrients -= 2  # Track spending

        # Priority 3: Move ALL spores to unclaimed tiles
        for spore in my_team.spores:
            if spore.id not in used_spores and spore.biomass >= 2:
                # Find nearest unclaimed tile
                target = self._find_nearest_unclaimed_tile(
                    spore, world, game_message)
                if target:
                    actions.append(SporeMoveToAction(
                        sporeId=spore.id,
                        position=target
                    ))
                    used_spores.add(spore.id)

        print(
            f"Final expansion: {len(actions)} actions, targeting {len([a for a in actions if isinstance(a, SporeMoveToAction)])} new tiles")
        return actions

    def _find_best_split_direction(self, spore: Spore, world: GameWorld,
                                   game_message: TeamGameState) -> Position:
        """Find best direction to split spore toward unclaimed territory"""
        directions = [
            Position(x=0, y=-1),   # Up
            Position(x=0, y=1),    # Down
            Position(x=-1, y=0),   # Left
            Position(x=1, y=0),    # Right
        ]

        best_dir = directions[0]
        best_score = -1

        for direction in directions:
            nx = spore.position.x + direction.x
            ny = spore.position.y + direction.y

            if not self._is_valid_position(nx, ny, world):
                continue

            # Score based on whether it's unclaimed
            if world.ownershipGrid[ny][nx] != game_message.yourTeamId:
                score = world.map.nutrientGrid[ny][nx]
                if score > best_score:
                    best_score = score
                    best_dir = direction

        return best_dir

    def _find_nearest_unclaimed_tile(self, spore: Spore, world: GameWorld,
                                     game_message: TeamGameState) -> Optional[Position]:
        """Find the nearest tile we don't control"""
        # BFS to find nearest unclaimed tile
        queue = deque([(spore.position.x, spore.position.y, 0)])
        visited = {(spore.position.x, spore.position.y)}

        while queue:
            x, y, dist = queue.popleft()

            # Check if this tile is unclaimed
            if world.ownershipGrid[y][x] != game_message.yourTeamId:
                return Position(x=x, y=y)

            # Don't search too far
            if dist > 20:
                break

            # Add neighbors
            for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
                nx, ny = x + dx, y + dy
                if self._is_valid_position(nx, ny, world) and (nx, ny) not in visited:
                    visited.add((nx, ny))
                    queue.append((nx, ny, dist + 1))

        # Fallback: random unexplored direction
        return self._find_best_expansion_target(spore, world, game_message)

    def _cleanup_tracking(self, my_team: TeamInfo):
        """Remove tracking data for spores that no longer exist"""
        current_spore_ids = {s.id for s in my_team.spores}
        self.failed_moves = {
            k: v for k, v in self.failed_moves.items() if k in current_spore_ids}
        self.expansion_vectors = {
            k: v for k, v in self.expansion_vectors.items() if k in current_spore_ids}

    def _intelligent_expansion(self, my_team: TeamInfo, world: GameWorld,
                               game_message: TeamGameState, used_spores: Set[str]) -> list[Action]:
        """Smart expansion that explores in multiple directions and avoids getting stuck"""
        actions = []

        # Assign expansion vectors to new spores
        for spore in my_team.spores:
            if spore.id not in self.expansion_vectors:
                self.expansion_vectors[spore.id] = self._assign_expansion_vector(
                    spore, my_team, world, game_message)

        for spore in my_team.spores:
            if spore.id in used_spores or spore.biomass < 2:
                continue

            # Check if spore is stuck
            if self._is_spore_stuck(spore, world, game_message):
                target = self._find_unstuck_target(spore, world, game_message)
                if target:
                    actions.append(SporeMoveToAction(
                        sporeId=spore.id, position=target))
                    used_spores.add(spore.id)
                    continue

            # Determine best expansion target based on spore's role
            if spore.biomass <= 8:  # Scout spores
                target = self._find_directional_expansion_target(
                    spore, world, game_message)
            else:  # Combat/patrol spores
                target = self._find_strategic_position(
                    spore, world, game_message, my_team)

            if target:
                actions.append(SporeMoveToAction(
                    sporeId=spore.id, position=target))
                used_spores.add(spore.id)

        return actions

    def _assign_expansion_vector(self, spore: Spore, my_team: TeamInfo,
                                 world: GameWorld, game_message: TeamGameState) -> Tuple[int, int]:
        """Assign a primary expansion direction to a spore"""
        directions = [
            (0, -1), (1, -1), (1, 0), (1, 1),
            (0, 1), (-1, 1), (-1, 0), (-1, -1),
        ]

        direction_scores = []
        for dx, dy in directions:
            score = self._score_direction(
                spore.position, dx, dy, world, game_message)
            direction_scores.append((score, (dx, dy)))

        direction_scores.sort(reverse=True)

        # Add randomization to avoid clustering
        if len(direction_scores) > 3 and random.random() < 0.3:
            return direction_scores[random.randint(0, 2)][1]

        return direction_scores[0][1]

    def _score_direction(self, pos: Position, dx: int, dy: int,
                         world: GameWorld, game_message: TeamGameState) -> float:
        """Score a direction based on unexplored high-value tiles"""
        score = 0
        search_distance = 20

        for dist in range(1, search_distance + 1):
            nx = pos.x + dx * dist
            ny = pos.y + dy * dist

            if not self._is_valid_position(nx, ny, world):
                break

            if (nx, ny) not in self.explored_tiles:
                nutrient_value = world.map.nutrientGrid[ny][nx]
                score += nutrient_value / (dist * 0.5 + 1)

            if world.ownershipGrid[ny][nx] != game_message.yourTeamId:
                nutrient_value = world.map.nutrientGrid[ny][nx]
                score += nutrient_value / (dist + 1)

        return score

    def _find_directional_expansion_target(self, spore: Spore, world: GameWorld,
                                           game_message: TeamGameState) -> Optional[Position]:
        """Find expansion target in spore's assigned direction"""
        # First check if spore is in a cage and needs to escape
        escape_route = self._find_escape_route(spore, world, game_message)
        if escape_route:
            print(f"Spore {spore.id[:8]} escaping cage via {escape_route}")
            return escape_route

        # Check if spore is looping in same area
        if self._is_spore_looping(spore):
            print(
                f"Spore {spore.id[:8]} detected looping, forcing outward expansion")
            return self._find_outward_target(spore, world, game_message)

        if spore.id not in self.expansion_vectors:
            return self._find_best_expansion_target(spore, world, game_message)

        dx, dy = self.expansion_vectors[spore.id]

        best_target = None
        best_score = -float('inf')
        search_radius = 12

        for dist in range(1, search_radius + 1):
            for offset in range(-2, 3):
                angle_offset = offset * 0.3
                current_angle = math.atan2(dy, dx)
                new_angle = current_angle + angle_offset

                nx = int(spore.position.x + math.cos(new_angle) * dist)
                ny = int(spore.position.y + math.sin(new_angle) * dist)

                if not self._is_valid_position(nx, ny, world):
                    continue

                # CRITICAL: Skip tiles we already own
                if world.ownershipGrid[ny][nx] == game_message.yourTeamId:
                    continue

                nutrient_value = world.map.nutrientGrid[ny][nx]
                distance = abs(nx - spore.position.x) + \
                    abs(ny - spore.position.y)

                unexplored_bonus = 50 if (
                    nx, ny) not in self.explored_tiles else 0

                enemy_penalty = 0
                if world.ownershipGrid[ny][nx] != game_message.constants.neutralTeamId:
                    enemy_penalty = world.biomassGrid[ny][nx] * 3

                # Bonus for frontier tiles (away from our controlled area)
                frontier_bonus = 0
                nearby_our_tiles = 0
                for edy in range(-2, 3):
                    for edx in range(-2, 3):
                        enx, eny = nx + edx, ny + edy
                        if self._is_valid_position(enx, eny, world):
                            if world.ownershipGrid[eny][enx] == game_message.yourTeamId:
                                nearby_our_tiles += 1

                if nearby_our_tiles < 8:  # Frontier tile
                    frontier_bonus = 30

                score = (nutrient_value + unexplored_bonus +
                         frontier_bonus) / (distance + 1) - enemy_penalty

                if score > best_score:
                    best_score = score
                    best_target = Position(x=nx, y=ny)

        return best_target if best_target else self._find_best_expansion_target(spore, world, game_message)

    def _is_spore_stuck(self, spore: Spore, world: GameWorld, game_message: TeamGameState) -> bool:
        """Check if a spore appears to be stuck"""
        spore_key = spore.id
        current_pos = (spore.position.x, spore.position.y)

        if spore_key not in self.failed_moves:
            self.failed_moves[spore_key] = []

        self.failed_moves[spore_key].append(current_pos)

        if len(self.failed_moves[spore_key]) > 5:
            self.failed_moves[spore_key].pop(0)

        if len(self.failed_moves[spore_key]) >= 4:
            unique_positions = set(self.failed_moves[spore_key])
            if len(unique_positions) <= 2:
                return True

        return False

    def _find_unstuck_target(self, spore: Spore, world: GameWorld,
                             game_message: TeamGameState) -> Optional[Position]:
        """Find a target to help unstuck a spore"""
        # First try to find escape route if in cage
        escape_route = self._find_escape_route(spore, world, game_message)
        if escape_route:
            print(
                f"Spore {spore.id[:8]} found escape route from stuck position")
            return escape_route

        # Otherwise find furthest unexplored tile
        best_target = None
        best_distance = 0

        for dy in range(-15, 16):
            for dx in range(-15, 16):
                nx, ny = spore.position.x + dx, spore.position.y + dy

                if not self._is_valid_position(nx, ny, world):
                    continue

                if world.ownershipGrid[ny][nx] == game_message.yourTeamId:
                    continue

                distance = abs(dx) + abs(dy)
                if (nx, ny) not in self.explored_tiles and distance > best_distance:
                    best_distance = distance
                    best_target = Position(x=nx, y=ny)

        return best_target

    def _find_strategic_position(self, spore: Spore, world: GameWorld,
                                 game_message: TeamGameState, my_team: TeamInfo) -> Optional[Position]:
        """Find strategic position for combat/patrol spores"""
        threats = self._identify_threats(game_message)

        if threats:
            closest_threat = min(threats, key=lambda t:
                                 abs(t.position.x - spore.position.x) + abs(t.position.y - spore.position.y))
            return closest_threat.position

        return self._find_best_expansion_target(spore, world, game_message)

    def _find_best_expansion_target(self, spore: Spore, world: GameWorld,
                                    game_message: TeamGameState) -> Optional[Position]:
        """Find the highest-value tile to expand to"""
        best_target = None
        best_score = -float('inf')
        search_radius = 15

        # Count how many tiles we already control nearby
        our_tiles_nearby = 0
        for dy in range(-3, 4):
            for dx in range(-3, 4):
                nx, ny = spore.position.x + dx, spore.position.y + dy
                if self._is_valid_position(nx, ny, world):
                    if world.ownershipGrid[ny][nx] == game_message.yourTeamId:
                        our_tiles_nearby += 1

        # If we're surrounded by our own tiles, heavily prefer expansion away
        surrounded_penalty = our_tiles_nearby / 49.0  # 7x7 grid

        for dy in range(-search_radius, search_radius + 1):
            for dx in range(-search_radius, search_radius + 1):
                nx, ny = spore.position.x + dx, spore.position.y + dy

                if not self._is_valid_position(nx, ny, world):
                    continue

                # CRITICAL: Skip if already controlled by us
                if world.ownershipGrid[ny][nx] == game_message.yourTeamId:
                    continue

                distance = abs(dx) + abs(dy)
                if distance == 0:
                    continue

                nutrient_value = world.map.nutrientGrid[ny][nx]
                unexplored_bonus = 30 if (
                    nx, ny) not in self.explored_tiles else 0

                # Enemy presence penalty
                enemy_penalty = 0
                if world.ownershipGrid[ny][nx] != game_message.constants.neutralTeamId:
                    enemy_biomass = world.biomassGrid[ny][nx]
                    enemy_penalty = enemy_biomass * 2

                # Bonus for tiles that are AWAY from our controlled territory
                expansion_bonus = 0
                nearby_our_tiles = 0
                for edy in range(-2, 3):
                    for edx in range(-2, 3):
                        enx, eny = nx + edx, ny + edy
                        if self._is_valid_position(enx, eny, world):
                            if world.ownershipGrid[eny][enx] == game_message.yourTeamId:
                                nearby_our_tiles += 1

                # If target has few of our tiles nearby, it's on the frontier - bonus!
                if nearby_our_tiles < 8:  # Less than 1/3 of 5x5 grid
                    expansion_bonus = 40

                # If we're surrounded, HEAVILY prefer targets that expand outward
                if surrounded_penalty > 0.5:  # >50% surrounded
                    expansion_bonus += 100

                score = (nutrient_value + unexplored_bonus +
                         expansion_bonus) / (distance + 1) - enemy_penalty

                if score > best_score:
                    best_score = score
                    best_target = Position(x=nx, y=ny)

        return best_target

    def _is_valid_position(self, x: int, y: int, world: GameWorld) -> bool:
        """Check if position is within map bounds"""
        return 0 <= x < world.map.width and 0 <= y < world.map.height

    def _identify_threats(self, game_message: TeamGameState) -> List[Spore]:
        """Identify enemy spores that pose a threat"""
        threats = []
        world = game_message.world

        for spore in world.spores:
            if (spore.teamId != game_message.yourTeamId and
                    spore.teamId != game_message.constants.neutralTeamId):
                if self._is_threat_to_us(spore, world, game_message):
                    threats.append(spore)

        return threats

    def _is_threat_to_us(self, enemy_spore: Spore, world: GameWorld,
                         game_message: TeamGameState) -> bool:
        """Check if enemy spore is a threat"""
        my_team = game_message.world.teamInfos[game_message.yourTeamId]
        for spawner in my_team.spawners:
            distance = abs(spawner.position.x - enemy_spore.position.x) + \
                abs(spawner.position.y - enemy_spore.position.y)
            if distance <= 5:
                return True

        return self._is_near_our_territory(enemy_spore.position, world, game_message.yourTeamId)

    def _is_near_our_territory(self, pos: Position, world: GameWorld, our_team_id: str) -> bool:
        """Check if position is near tiles we control"""
        for dy in range(-3, 4):
            for dx in range(-3, 4):
                nx, ny = pos.x + dx, pos.y + dy
                if self._is_valid_position(nx, ny, world):
                    if world.ownershipGrid[ny][nx] == our_team_id:
                        return True
        return False

    def _handle_combat(self, my_team: TeamInfo, world: GameWorld,
                       game_message: TeamGameState, used_spores: set) -> list[Action]:
        """Handle combat situations"""
        actions = []
        threats = self._identify_threats(game_message)

        for threat in threats:
            defender = self._find_best_defender(
                threat, my_team.spores, used_spores)
            if defender and defender.biomass > threat.biomass + 1:
                actions.append(SporeMoveToAction(
                    sporeId=defender.id,
                    position=threat.position
                ))
                used_spores.add(defender.id)

        return actions

    def _find_best_defender(self, threat: Spore, our_spores: List[Spore],
                            used_spores: set) -> Optional[Spore]:
        """Find best spore to defend against threat"""
        candidates = []

        for spore in our_spores:
            if spore.id in used_spores or spore.biomass < 2:
                continue

            if spore.biomass <= threat.biomass:
                continue

            distance = abs(spore.position.x - threat.position.x) + \
                abs(spore.position.y - threat.position.y)

            overkill = spore.biomass - threat.biomass
            score = 100 / (distance + 1) - overkill * 0.5

            candidates.append((score, spore))

        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][1]

        return None

    def _find_best_spawner_candidate(self, spores: list[Spore], world: GameWorld,
                                     game_message: TeamGameState) -> Optional[Spore]:
        """Find the best spore to convert into a spawner"""
        candidates = []

        for spore in spores:
            if spore.biomass >= 1:
                score = self._evaluate_spawner_location(
                    spore.position, world, game_message)
                candidates.append((score, spore))

        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][1]

        return None

    def _find_spawner_creation_candidate(self, spores: list[Spore], cost: int,
                                         world: GameWorld, game_message: TeamGameState) -> Optional[Spore]:
        """Find spore that can create spawner in good location"""
        candidates = []

        for spore in spores:
            if spore.biomass > cost + 1:  # Need buffer
                score = self._evaluate_spawner_location(
                    spore.position, world, game_message)

                # Prefer spores far from existing spawners
                my_team = game_message.world.teamInfos[game_message.yourTeamId]
                min_spawner_dist = float('inf')
                for spawner in my_team.spawners:
                    dist = abs(spawner.position.x - spore.position.x) + \
                        abs(spawner.position.y - spore.position.y)
                    min_spawner_dist = min(min_spawner_dist, dist)

                score += min_spawner_dist * 3
                candidates.append((score, spore))

        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][1]

        return None

    def _evaluate_spawner_location(self, pos: Position, world: GameWorld,
                                   game_message: TeamGameState) -> float:
        """Score a potential spawner location"""
        score = 0
        search_radius = 10

        for dy in range(-search_radius, search_radius + 1):
            for dx in range(-search_radius, search_radius + 1):
                nx, ny = pos.x + dx, pos.y + dy
                if self._is_valid_position(nx, ny, world):
                    distance = abs(dx) + abs(dy)
                    if distance > 0:
                        nutrient_value = world.map.nutrientGrid[ny][nx]
                        score += nutrient_value / distance

        return score

    def _count_controlled_tiles(self, team_id: str, world: GameWorld) -> int:
        """Count tiles controlled by a team"""
        count = 0
        for y in range(world.map.height):
            for x in range(world.map.width):
                if world.ownershipGrid[y][x] == team_id:
                    count += 1
        return count

    def _track_enemies(self, game_message: TeamGameState):
        """Track enemy spawner locations"""
        for spawner in game_message.world.spawners:
            if spawner.teamId != game_message.yourTeamId:
                key = (spawner.position.x, spawner.position.y)
                self.enemy_spawner_locations[key] = spawner

    def _find_escape_route(self, spore: Spore, world: GameWorld,
                           game_message: TeamGameState) -> Optional[Position]:
        """Find the easiest way out if spore is trapped in a cage"""
        # Check if spore is surrounded by enemy/neutral territory
        surrounded_score = 0
        free_directions = []

        # Check 4 cardinal directions
        directions = [
            (0, -1, "North"),
            (0, 1, "South"),
            (-1, 0, "West"),
            (1, 0, "East")
        ]

        for dx, dy, name in directions:
            # Check up to 5 tiles in this direction
            blocked = 0
            for dist in range(1, 6):
                nx = spore.position.x + dx * dist
                ny = spore.position.y + dy * dist

                if not self._is_valid_position(nx, ny, world):
                    blocked += 5  # Map edge is a strong block
                    break

                owner = world.ownershipGrid[ny][nx]

                # If it's ours or empty, it's not blocking
                if owner == game_message.yourTeamId:
                    continue

                # Check if there's a strong enemy presence
                biomass = world.biomassGrid[ny][nx]
                if owner != game_message.constants.neutralTeamId and biomass > 0:
                    # Enemy territory
                    blocked += biomass
                elif biomass > 0:
                    # Neutral territory
                    blocked += biomass * 0.5

            free_directions.append((blocked, dx, dy, name))

        # Sort by least blocked direction
        free_directions.sort()

        # If the best direction has significant blockage, we're in a cage
        best_blockage, best_dx, best_dy, direction_name = free_directions[0]

        # Check if we're actually caged (all directions somewhat blocked)
        avg_blockage = sum(
            b for b, _, _, _ in free_directions) / len(free_directions)

        if avg_blockage > 3 or best_blockage > 5:
            # We're caged! Find the easiest escape route
            print(
                f"Spore {spore.id[:8]} is caged! Avg blockage: {avg_blockage:.1f}, escaping {direction_name}")

            # Use BFS to find the nearest truly free territory
            return self._bfs_find_free_territory(spore, world, game_message)

        return None

    def _bfs_find_free_territory(self, spore: Spore, world: GameWorld,
                                 game_message: TeamGameState) -> Optional[Position]:
        """Use BFS to find path to free, high-value territory"""
        queue = deque([(spore.position.x, spore.position.y, 0, [])])
        visited = {(spore.position.x, spore.position.y)}

        best_target = None
        best_score = -float('inf')

        while queue:
            x, y, dist, path = queue.popleft()

            # Don't search too far
            if dist > 25:
                continue

            # Score this position as potential target
            if dist > 0:  # Not starting position
                owner = world.ownershipGrid[y][x]
                biomass = world.biomassGrid[y][x]
                nutrient = world.map.nutrientGrid[y][x]

                # Good escape target: unclaimed or weakly held, high nutrients
                is_free = (owner == game_message.yourTeamId or
                           owner == game_message.constants.neutralTeamId or
                           biomass == 0)

                # Check if this area is "open" (has many unclaimed neighbors)
                openness = self._calculate_openness(x, y, world, game_message)

                if is_free and openness > 0.3:  # At least 30% open
                    # Score: prioritize closer, more open, higher nutrient areas
                    score = (nutrient * openness * 10) / (dist + 1)

                    # Bonus for truly unclaimed territory
                    if owner != game_message.yourTeamId and owner != game_message.constants.neutralTeamId:
                        pass  # Neutral, no bonus
                    elif biomass == 0:
                        score += 50  # Empty is best

                    if score > best_score:
                        best_score = score
                        # Return the first step on the path
                        if len(path) > 0:
                            best_target = Position(x=path[0][0], y=path[0][1])
                        else:
                            best_target = Position(x=x, y=y)

            # Explore neighbors
            for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
                nx, ny = x + dx, y + dy

                if not self._is_valid_position(nx, ny, world):
                    continue

                if (nx, ny) in visited:
                    continue

                visited.add((nx, ny))

                # Build path
                new_path = path + [(nx, ny)] if dist == 0 else path

                # Add to queue even if occupied (we need to pathfind through)
                queue.append((nx, ny, dist + 1, new_path))

        return best_target

    def _calculate_openness(self, x: int, y: int, world: GameWorld,
                            game_message: TeamGameState) -> float:
        """Calculate how 'open' an area is (% of nearby tiles unclaimed)"""
        total = 0
        unclaimed = 0

        for dy in range(-3, 4):
            for dx in range(-3, 4):
                nx, ny = x + dx, y + dy
                if self._is_valid_position(nx, ny, world):
                    total += 1
                    owner = world.ownershipGrid[ny][nx]
                    biomass = world.biomassGrid[ny][nx]

                    # Count as unclaimed if: empty, ours, or weakly held neutral
                    if (owner == game_message.yourTeamId or
                        biomass == 0 or
                            (owner == game_message.constants.neutralTeamId and biomass < 5)):
                        unclaimed += 1

        return unclaimed / total if total > 0 else 0

    def _attack_neutral_colonies(self, my_team: TeamInfo, world: GameWorld,
                                 game_message: TeamGameState, used_spores: Set[str]) -> list[Action]:
        """Attack weak neutral spores and spawners to clear expansion paths"""
        actions = []

        # Find all neutral spores and spawners
        neutral_targets = []

        # Add neutral spores
        for spore in world.spores:
            if spore.teamId == game_message.constants.neutralTeamId:
                neutral_targets.append(
                    ('spore', spore.position, spore.biomass))

        # Add neutral spawners (treated as 0 biomass - easy targets)
        for spawner in world.spawners:
            if spawner.teamId == game_message.constants.neutralTeamId:
                neutral_targets.append(('spawner', spawner.position, 0))

        if not neutral_targets:
            return actions

        # Find our spores that can attack neutrals
        attackers = []
        for spore in my_team.spores:
            if spore.id not in used_spores and spore.biomass >= 2:
                attackers.append(spore)

        # Match attackers to targets
        for target_type, target_pos, target_biomass in neutral_targets:
            best_attacker = None
            best_score = -1

            for attacker in attackers:
                if attacker.id in used_spores:
                    continue

                # Can we win?
                if attacker.biomass <= target_biomass:
                    continue

                # Calculate distance
                distance = abs(attacker.position.x - target_pos.x) + \
                    abs(attacker.position.y - target_pos.y)

                # Prefer closer attackers with appropriate strength
                overkill = attacker.biomass - target_biomass

                # Bonus for attacking spawners (high priority)
                spawner_bonus = 100 if target_type == 'spawner' else 0

                # Score: prefer close, minimal overkill, spawners
                score = spawner_bonus + 50 / (distance + 1) - overkill * 0.3

                if score > best_score:
                    best_score = score
                    best_attacker = attacker

            if best_attacker:
                print(
                    f"Attacking neutral {target_type} at ({target_pos.x}, {target_pos.y}) with {best_attacker.biomass} biomass")
                actions.append(SporeMoveToAction(
                    sporeId=best_attacker.id,
                    position=target_pos
                ))
                used_spores.add(best_attacker.id)

        return actions

    def _is_spore_looping(self, spore: Spore) -> bool:
        """Check if spore is moving in circles"""
        if spore.id not in self.failed_moves:
            return False

        history = self.failed_moves[spore.id]
        if len(history) < 6:
            return False

        # Check if revisiting same positions frequently
        unique_positions = set(history[-6:])
        if len(unique_positions) <= 3:
            return True

        return False

    def _find_outward_target(self, spore: Spore, world: GameWorld,
                             game_message: TeamGameState) -> Optional[Position]:
        """Find target that pushes spore away from controlled territory"""
        best_target = None
        best_score = -float('inf')

        # Find center of our territory
        our_tiles = []
        for y in range(world.map.height):
            for x in range(world.map.width):
                if world.ownershipGrid[y][x] == game_message.yourTeamId:
                    our_tiles.append((x, y))

        if not our_tiles:
            return None

        center_x = sum(x for x, y in our_tiles) / len(our_tiles)
        center_y = sum(y for x, y in our_tiles) / len(our_tiles)

        # Find unclaimed tiles far from center
        for dy in range(-20, 21):
            for dx in range(-20, 21):
                nx, ny = spore.position.x + dx, spore.position.y + dy

                if not self._is_valid_position(nx, ny, world):
                    continue

                if world.ownershipGrid[ny][nx] == game_message.yourTeamId:
                    continue

                # Distance from our territory center
                dist_from_center = abs(nx - center_x) + abs(ny - center_y)

                # Distance from spore
                dist_from_spore = abs(dx) + abs(dy)

                if dist_from_spore == 0:
                    continue

                # Heavily favor tiles FAR from our territory center
                score = dist_from_center / (dist_from_spore + 1)

                if score > best_score:
                    best_score = score
                    best_target = Position(x=nx, y=ny)

        return best_target
