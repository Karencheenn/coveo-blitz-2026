import { FC } from 'react';
export interface TeamStats {
    territoryControlled: number;
    sporesCount: number;
    spawnersCount: number;
    totalBiomass?: number;
    spawnersDestroyed?: number;
    spawnersLost?: number;
    totalNutrients?: number;
    sporesCreated?: number;
    spawnersCreated?: number;
    combatWins?: number;
    combatLosses?: number;
    totalMovement?: number;
    perTick?: any[];
    eliminatedAtTurn?: number | null;
    actionsTaken?: number;
    turnsAlive?: number;
}
export interface TeamInfo {
    teamId?: string;
    name: string;
    teamIndex: number;
    nutrients: number;
    spores?: any[];
    spawners?: any[];
    nextSpawnerCost: number;
    stats: TeamStats;
    territoryControlled?: number;
    sporesCount?: number;
    spawnersCount?: number;
    totalBiomass?: number;
    spawnersDestroyed?: number;
    spawnersLost?: number;
    totalNutrients?: number;
    sporesCreated?: number;
    spawnersCreated?: number;
    combatWins?: number;
    combatLosses?: number;
    totalMovement?: number;
    perTick?: any[];
    eliminatedAtTurn?: number | null;
    actionsTaken?: number;
    turnsAlive?: number;
    isAlive?: boolean;
    nextNutrientGeneration: number;
}
export interface NeutralTeamInfo {
    stats: {
        territoryControlled: number;
    };
}
export interface TickState {
    mapName: string;
    teamInfos: {
        NEUTRAL: NeutralTeamInfo;
        [teamKey: string]: TeamInfo | NeutralTeamInfo;
    };
    currentTickNumber: number;
    stats?: Array<{
        teamId: string;
        teamName: string;
        stats: any;
    }>;
}
export declare const GameInformation: FC<{
    tickState: TickState;
    isCaster: boolean;
}>;
