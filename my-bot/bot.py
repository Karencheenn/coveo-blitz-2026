import random
from game_message import *
from typing import Optional, Tuple, List, Set, Dict
from collections import deque


class Bot:
    def __init__(self):
        print("Initializing Pure Water Flow Bot")
        self.explored_tiles = set()

    def get_next_move(self, game_message: TeamGameState) -> list[Action]:
        """Pure aggressive water flow - expand in all directions"""
        actions = []
        my_team: TeamInfo = game_message.world.teamInfos[game_message.yourTeamId]
        world = game_message.world
        tick = game_message.tick

        # Update exploration
        for spore in my_team.spores:
            self.explored_tiles.add((spore.position.x, spore.position.y))

        # Emergency: need first spawner
        if len(my_team.spawners) == 0 and my_team.spores:
            best_spore = max(my_team.spores, key=lambda s: s.biomass)
            if best_spore.biomass >= my_team.nextSpawnerCost:
                actions.append(SporeCreateSpawnerAction(sporeId=best_spore.id))
                return actions

        used_spores = set()
        used_spawners = set()
        planned_moves = {}  # Track where spores are going
        split_targets = set()  # Track split destinations

        # Phase 1: Identify clustered areas and disperse
        clustered_positions = self._find_clusters(my_team, world, game_message)

        # Phase 2: Move spores OUT of clusters first (before splitting)
        for cluster_pos in clustered_positions:
            spores_in_cluster = [s for s in my_team.spores
                                 if abs(s.position.x - cluster_pos[0]) <= 1
                                 and abs(s.position.y - cluster_pos[1]) <= 1
                                 and s.biomass >= 2]

            # Move the smaller spores away from cluster
            for spore in sorted(spores_in_cluster, key=lambda s: s.biomass)[:3]:
                if spore.id in used_spores:
                    continue

                escape_target = self._find_escape_from_cluster(
                    spore, cluster_pos, world, game_message, planned_moves)

                if escape_target:
                    print(
                        f"Dispersing {spore.id[:8]} from cluster at {cluster_pos}")
                    actions.append(SporeMoveToAction(
                        sporeId=spore.id, position=escape_target))
                    used_spores.add(spore.id)
                    planned_moves[(escape_target.x,
                                   escape_target.y)] = spore.id

        # Phase 3: Split large spores (but stagger the splits across different areas)
        spores_by_biomass = sorted(
            [s for s in my_team.spores if s.id not in used_spores],
            key=lambda s: s.biomass,
            reverse=True
        )

        split_count = 0
        for spore in spores_by_biomass:
            if split_count >= 2 or spore.biomass <= 20:
                break

            # Check if this area is already getting a split
            nearby_splits = sum(1 for pos in split_targets
                                if abs(pos[0] - spore.position.x) <= 3
                                and abs(pos[1] - spore.position.y) <= 3)

            if nearby_splits > 0:
                continue  # Skip, this area already has a split happening

            split_dir = self._find_best_split_direction(
                spore, world, game_message, split_targets)
            split_size = max(5, spore.biomass // 3)

            print(
                f"Splitting {spore.id[:8]} ({spore.biomass}) -> {split_size} + {spore.biomass - split_size}")
            actions.append(SporeSplitAction(
                sporeId=spore.id,
                biomassForMovingSpore=split_size,
                direction=split_dir
            ))
            used_spores.add(spore.id)

            # Mark both positions as having a split
            split_targets.add((spore.position.x, spore.position.y))
            split_targets.add((spore.position.x + split_dir.x,
                              spore.position.y + split_dir.y))
            split_count += 1

        # Phase 4: Build spawners
        if tick < 300 and len(my_team.spawners) < 3 and my_team.nextSpawnerCost <= 7:
            for spore in sorted(my_team.spores, key=lambda s: s.biomass, reverse=True):
                if spore.id not in used_spores and spore.biomass >= my_team.nextSpawnerCost + 3:
                    print(f"Building spawner #{len(my_team.spawners) + 1}")
                    actions.append(SporeCreateSpawnerAction(sporeId=spore.id))
                    used_spores.add(spore.id)
                    break

        # Phase 5: Produce spores
        for spawner in my_team.spawners:
            if spawner.id not in used_spawners and my_team.nutrients >= 5:
                biomass = min(8, my_team.nutrients // len(my_team.spawners))
                if biomass >= 5:
                    actions.append(SpawnerProduceSporeAction(
                        spawnerId=spawner.id,
                        biomass=biomass
                    ))
                    used_spawners.add(spawner.id)

        # Phase 6: Water flow movement - assign each spore to unique targets
        spores_to_move = [s for s in my_team.spores
                          if s.id not in used_spores and s.biomass >= 2]

        # Sort by position to distribute across map
        spores_to_move.sort(key=lambda s: (s.position.x, s.position.y))

        for spore in spores_to_move:
            target = self._find_water_flow_target(
                spore, world, game_message, planned_moves)

            if target:
                actions.append(SporeMoveToAction(
                    sporeId=spore.id, position=target))
                used_spores.add(spore.id)
                planned_moves[(target.x, target.y)] = spore.id

        return actions

    def _find_clusters(self, my_team: TeamInfo, world: GameWorld,
                       game_message: TeamGameState) -> List[Tuple[int, int]]:
        """Identify positions with high biomass clustering"""
        my_team_id = game_message.yourTeamId
        clusters = []

        checked = set()

        for y in range(world.map.height):
            for x in range(world.map.width):
                if (x, y) in checked:
                    continue

                if world.ownershipGrid[y][x] != my_team_id:
                    continue

                # Check 3x3 area for total biomass
                total_biomass = 0
                for dy in range(-1, 2):
                    for dx in range(-1, 2):
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < world.map.width and 0 <= ny < world.map.height:
                            if world.ownershipGrid[ny][nx] == my_team_id:
                                total_biomass += world.biomassGrid[ny][nx]
                                checked.add((nx, ny))

                # If this 3x3 area has >40 biomass, it's a cluster
                if total_biomass > 40:
                    clusters.append((x, y))

        return clusters

    def _find_escape_from_cluster(self, spore: Spore, cluster_pos: Tuple[int, int],
                                  world: GameWorld, game_message: TeamGameState,
                                  planned_moves: Dict) -> Optional[Position]:
        """Find direction away from cluster"""
        my_team_id = game_message.yourTeamId

        # Calculate direction AWAY from cluster center
        dx_away = spore.position.x - cluster_pos[0]
        dy_away = spore.position.y - cluster_pos[1]

        # Normalize to cardinal directions
        if abs(dx_away) > abs(dy_away):
            dx_away = 1 if dx_away > 0 else -1
            dy_away = 0
        else:
            dy_away = 1 if dy_away > 0 else -1
            dx_away = 0

        # Try to move away
        for attempt in range(4):
            nx = spore.position.x + dx_away
            ny = spore.position.y + dy_away

            if (0 <= nx < world.map.width and 0 <= ny < world.map.height and
                    (nx, ny) not in planned_moves):

                # Check biomass isn't too high
                if world.ownershipGrid[ny][nx] == my_team_id:
                    if world.biomassGrid[ny][nx] < 5:
                        return Position(x=nx, y=ny)
                else:
                    return Position(x=nx, y=ny)

            # Rotate direction if blocked
            dx_away, dy_away = -dy_away, dx_away

        return None

    def _find_best_split_direction(self, spore: Spore, world: GameWorld,
                                   game_message: TeamGameState,
                                   split_targets: Set) -> Position:
        """Find direction with least biomass and no recent splits"""
        my_team_id = game_message.yourTeamId

        directions = [(0, -1), (0, 1), (-1, 0), (1, 0)]
        best_dir = (1, 0)
        best_score = -float('inf')

        for dx, dy in directions:
            nx, ny = spore.position.x + dx, spore.position.y + dy

            if not (0 <= nx < world.map.width and 0 <= ny < world.map.height):
                continue

            # Skip if this direction already has a split
            if (nx, ny) in split_targets:
                continue

            score = 0

            # Check biomass in direction (less is better)
            for dist in range(1, 5):
                cx = spore.position.x + dx * dist
                cy = spore.position.y + dy * dist

                if not (0 <= cx < world.map.width and 0 <= cy < world.map.height):
                    break

                if world.ownershipGrid[cy][cx] == my_team_id:
                    # Closer biomass = worse
                    score -= world.biomassGrid[cy][cx] * (6 - dist)
                else:
                    score += 10  # Unclaimed is good

            if score > best_score:
                best_score = score
                best_dir = (dx, dy)

        return Position(x=best_dir[0], y=best_dir[1])

    def _find_water_flow_target(self, spore: Spore, world: GameWorld,
                                game_message: TeamGameState,
                                planned_moves: Dict) -> Optional[Position]:
        """Find target using pure water flow logic - seek empty frontiers"""
        my_team_id = game_message.yourTeamId
        neutral_id = game_message.constants.neutralTeamId

        # Calculate our territory center and edges
        our_tiles = []
        min_x, max_x = world.map.width, 0
        min_y, max_y = world.map.height, 0

        for y in range(world.map.height):
            for x in range(world.map.width):
                if world.ownershipGrid[y][x] == my_team_id:
                    our_tiles.append((x, y))
                    min_x, max_x = min(min_x, x), max(max_x, x)
                    min_y, max_y = min(min_y, y), max(max_y, y)

        if our_tiles:
            center_x = sum(x for x, y in our_tiles) / len(our_tiles)
            center_y = sum(y for x, y in our_tiles) / len(our_tiles)
        else:
            center_x, center_y = spore.position.x, spore.position.y

        # Check if we're in a dense area
        nearby_biomass = 0
        for dy in range(-3, 4):
            for dx in range(-3, 4):
                nx, ny = spore.position.x + dx, spore.position.y + dy
                if 0 <= nx < world.map.width and 0 <= ny < world.map.height:
                    if world.ownershipGrid[ny][nx] == my_team_id:
                        nearby_biomass += world.biomassGrid[ny][nx]

        in_dense_area = nearby_biomass > 30

        best_target = None
        best_score = -float('inf')

        # Check all 4 directions
        for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
            nx, ny = spore.position.x + dx, spore.position.y + dy

            if not (0 <= nx < world.map.width and 0 <= ny < world.map.height):
                continue

            # Skip if another spore is already going here
            if (nx, ny) in planned_moves:
                continue

            owner = world.ownershipGrid[ny][nx]
            biomass = world.biomassGrid[ny][nx]

            # CRITICAL: Skip high biomass areas
            if owner == my_team_id and biomass > 1:
                continue

            # Calculate score
            score = 0

            # Base bonuses for expansion
            if owner == "":
                score += 400  # Empty tiles are best
            elif owner != my_team_id:
                score += 300

            # HUGE bonus for moving toward unexplored map edges
            # Check if this direction leads toward empty parts of map
            edge_bonus = 0

            # Moving toward edges of map
            if dx < 0:  # Going left
                edge_bonus += (nx / world.map.width) * \
                    100  # Bonus for going toward x=0
            elif dx > 0:  # Going right
                edge_bonus += ((world.map.width - nx) / world.map.width) * 100

            if dy < 0:  # Going up
                edge_bonus += (ny / world.map.height) * 100
            elif dy > 0:  # Going down
                edge_bonus += ((world.map.height - ny) /
                               world.map.height) * 100

            score += edge_bonus

            # CRITICAL: Look ahead in this direction for empty space
            empty_path_ahead = 0
            blocked_by_biomass = False

            for look_dist in range(1, 12):  # Look far ahead
                lx, ly = spore.position.x + dx * look_dist, spore.position.y + dy * look_dist

                if not (0 <= lx < world.map.width and 0 <= ly < world.map.height):
                    break

                look_owner = world.ownershipGrid[ly][lx]
                look_biomass = world.biomassGrid[ly][lx]

                # Empty tiles ahead? Great!
                if look_owner == "":
                    empty_path_ahead += 1
                elif look_owner != my_team_id:
                    # Neutral with low biomass is ok
                    if look_owner == neutral_id and look_biomass < 5:
                        empty_path_ahead += 0.5
                    else:
                        empty_path_ahead += 0.3
                else:
                    # Our territory - check biomass
                    if look_biomass > 2:
                        blocked_by_biomass = True
                        break

            # MASSIVE bonus for directions with lots of empty space ahead
            score += empty_path_ahead * 50

            if blocked_by_biomass:
                score -= 500  # Heavily penalize blocked paths

            # If we're in a dense area, STRONGLY prefer paths leading away
            if in_dense_area:
                # Check if this path leads to less dense areas
                density_ahead = 0
                for look_dist in range(1, 6):
                    lx, ly = spore.position.x + dx * look_dist, spore.position.y + dy * look_dist
                    if not (0 <= lx < world.map.width and 0 <= ly < world.map.height):
                        break

                    # Check density around this look-ahead position
                    for ldy in range(-2, 3):
                        for ldx in range(-2, 3):
                            cx, cy = lx + ldx, ly + ldy
                            if 0 <= cx < world.map.width and 0 <= cy < world.map.height:
                                if world.ownershipGrid[cy][cx] == my_team_id:
                                    density_ahead += world.biomassGrid[cy][cx]

                # Strong bonus for low-density paths
                if density_ahead < 20:
                    score += 300

            # Distance from center bonus (move away from blob)
            dist_from_center = abs(nx - center_x) + abs(ny - center_y)
            score += dist_from_center * 20

            # Unexplored bonus
            if (nx, ny) not in self.explored_tiles:
                score += 200

            # Penalty for nearby biomass
            nearby_biomass_target = 0
            for cy in range(ny - 2, ny + 3):
                for cx in range(nx - 2, nx + 3):
                    if cx == nx and cy == ny:
                        continue
                    if 0 <= cx < world.map.width and 0 <= cy < world.map.height:
                        if world.ownershipGrid[cy][cx] == my_team_id:
                            nearby_biomass_target += world.biomassGrid[cy][cx]

            score -= nearby_biomass_target * 8

            # Isolation bonus
            if nearby_biomass_target < 3:
                score += 150

            if score > best_score:
                best_score = score
                best_target = Position(x=nx, y=ny)

        if best_target:
            print(
                f"Spore {spore.id[:8]} -> ({best_target.x},{best_target.y}) score:{best_score:.0f}")

        return best_target
