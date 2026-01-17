import { Spawner, Constants, Spore } from './GameInterface.ts';
/**
 * Team info from the game (from TeamInfo schema)
 */
export interface TeamInfo {
    teamId: string;
    name: string;
    teamIndex: number;
    nutrients: number;
    spores: Array<Spore>;
    spawners: Array<Spawner>;
    nextSpawnerCost: number;
}
/**
 * Team result statistics interface
 */
export interface TeamResult {
    rank: number;
    score: number;
    teamId: number;
    teamName: string;
    stats: {
        spawnersDestroyed: number;
        spawnersLost: number;
        territoryControlled: number;
        totalNutrients: number;
        sporesCreated: number;
        spawnersCreated: number;
        combatWins: number;
        combatLosses: number;
        totalMovement: number;
        perTick: any[];
        eliminatedAtTurn: number | null;
        actionsTaken: number;
        turnsAlive: number;
        totalBiomass: number;
    };
}
/**
 * Viewer-specific game state (returned by serializeForViewer)
 */
export interface ViewerGameState {
    tick: number;
    spores: Array<Spore>;
    spawners: Array<Spawner>;
    biomassGrid: Array<Array<number>>;
    ownershipGrid: Array<Array<string>>;
    nutrientGrid: Array<Array<number>>;
    teamInfos: {
        [teamId: string]: TeamInfo;
    };
    stats: {
        [teamId: string]: {
            territoryControlled: number;
            nutrients: number;
            sporesCount: number;
            coloniesCount: number;
        };
    } | Array<Array<{
        teamId: string;
        teamName: string;
        stats: any;
    }>>;
    constants: Constants;
    currentTickNumber: number;
    mapName?: string;
    gameComplete?: boolean;
    endGameReason?: string;
    winner?: TeamResult | null;
    finalResults?: TeamResult[];
}
export type ActualGameState = ViewerGameState;
