/**
 * Game constants. These will never change during the game.
 */
export interface Constants {
    /**
     * Team id used for neutral tiles.
     */
    neutralTeamId: string;
    /**
     * Maximum number of ticks before the game ends.
     */
    maxTicks: number;
}
/**
 * A two-dimensional point on the map.
 */
export interface Position {
    /**
     * X coordinate of the position. 0 is the column on the left.
     */
    x: number;
    /**
     * Y coordinate of the position. 0 is the top row.
     */
    y: number;
}
/**
 * A spore entity that can move and engage in combat.
 */
export interface Spore {
    /**
     * Unique identifier of the spore.
     */
    id: string;
    /**
     * Team that controls this spore.
     */
    teamId: string;
    /**
     * Current position of the spore.
     */
    position: Position;
    /**
     * Current biomass level determining combat strength.
     */
    biomass: number;
}
/**
 * A spawner that can produce spores.
 */
export interface Spawner {
    /**
     * Unique identifier of the spawner.
     */
    id: string;
    /**
     * Team that controls this spawner.
     */
    teamId: string;
    /**
     * Position of the spawner.
     */
    position: Position;
}
/**
 * Information for a team.
 */
export interface TeamInfo {
    /**
     * Team identifier.
     */
    teamId: string;
    /**
     * True if the team is still alive in the game.
     */
    isAlive: boolean;
    /**
     * Current nutrients available to the team.
     */
    nutrients: number;
    /**
     * List of all spores controlled by the team.
     */
    spores: Array<Spore>;
    /**
     * List of all spawners controlled by the team.
     */
    spawners: Array<Spawner>;
    /**
     * Cost in biomass to create the next spawner.
     */
    nextSpawnerCost: number;
}
/**
 * The game map.
 */
export interface GameMap {
    /**
     * Width of the map.
     */
    width: number;
    /**
     * Height of the map.
     */
    height: number;
    /**
     * The nutrients level per tile.
     */
    nutrientGrid: Array<Array<number>>;
}
/**
 * The game world.
 */
export interface GameWorld {
    /**
     * The loaded game map. This map will never change during the game.
     */
    map: GameMap;
    /**
     * The biomass level per tile. Use this in combination with ownershipGrid.
     */
    biomassGrid: Array<Array<number>>;
    /**
     * The owner id per tile. Will be the team id if owned by a player. Use this in combination with biomassGrid.
     */
    ownershipGrid: Array<Array<string>>;
    /**
     * The spores in the map.
     */
    spores: Array<Spore>;
    /**
     * The spawners in the map.
     */
    spawners: Array<Spawner>;
    /**
     * Current info of each team by team id.
     */
    teamInfos: {
        [key: string]: TeamInfo;
    };
}
/**
 * State of the game for a specific team.
 */
export interface TeamGameState {
    /**
     * Current tick number.
     */
    tick: number;
    /**
     * Your team id.
     */
    yourTeamId: string;
    /**
     * Errors that happened during the last tick.
     */
    lastTickErrors: Array<string>;
    /**
     * Game constants.
     */
    constants: Constants;
    /**
     * List of all the teams currently playing.
     */
    teamIds: Array<string>;
    /**
     * The game map, and objects
     */
    world: GameWorld;
}
export declare enum ActionType {
    SPORE_MOVE = "SPORE_MOVE",
    SPORE_MOVE_TO = "SPORE_MOVE_TO",
    SPORE_CREATE_SPAWNER = "SPORE_CREATE_SPAWNER",
    SPAWNER_PRODUCE_SPORE = "SPAWNER_PRODUCE_SPORE",
    SPORE_SPLIT = "SPORE_SPLIT"
}
export type Action = ActionSporeMove | ActionSporeMoveTo | ActionSporeCreateSpawner | ActionSpawnerProduceSpore | ActionSporeSplit;
interface ActionBase {
    type: ActionType;
}
/**
 * Move the spore one tile in the specified direction. Leaves biomass trail on empty tiles.
 */
export interface ActionSporeMove extends ActionBase {
    type: ActionType.SPORE_MOVE;
    /**
     * ID of the spore to move.
     */
    sporeId: string;
    /**
     * Direction vector to move. Should be one of: {x:0,y:-1} (up), {x:0,y:1} (down), {x:-1,y:0} (left), {x:1,y:0} (right).
     */
    direction: Position;
}
/**
 * Move the spore towards the specified position using pathfinding. Moves one tile closer each turn.
 */
export interface ActionSporeMoveTo extends ActionBase {
    type: ActionType.SPORE_MOVE_TO;
    /**
     * ID of the spore to move.
     */
    sporeId: string;
    /**
     * Target position to move towards.
     */
    position: Position;
}
/**
 * Create a spawner using part of the spore's biomass at its current position. Cost follows exponential sequence: 0, 1, 3, 7, 15, 31...
 */
export interface ActionSporeCreateSpawner extends ActionBase {
    type: ActionType.SPORE_CREATE_SPAWNER;
    /**
     * ID of the spore to create a spawner.
     */
    sporeId: string;
}
/**
 * Create a new spore at the spawner location with specified biomass. Costs nutrients equal to biomass.
 */
export interface ActionSpawnerProduceSpore extends ActionBase {
    type: ActionType.SPAWNER_PRODUCE_SPORE;
    /**
     * ID of the spawner that will produce the spore.
     */
    spawnerId: string;
    /**
     * Amount of biomass for the new spore. Must be positive and team must have enough nutrients.
     */
    biomass: number;
}
/**
 * Split a spore into two spores, distributing biomass between them. Original spore moves with specified biomass, new spore created at original position with remaining biomass.
 */
export interface ActionSporeSplit extends ActionBase {
    type: ActionType.SPORE_SPLIT;
    /**
     * ID of the spore to split.
     */
    sporeId: string;
    /**
     * Amount of biomass for the moving spore (must be at least 1 and less than current biomass).
     */
    biomassForMovingSpore: number;
    /**
     * Direction for the original spore to move. Should be one of: {x:0,y:-1} (up), {x:0,y:1} (down), {x:-1,y:0} (left), {x:1,y:0} (right).
     */
    direction: Position;
}
export {};
