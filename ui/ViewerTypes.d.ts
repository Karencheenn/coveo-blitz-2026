import type { ViewerGameState as BaseViewerGameState } from './ViewerInterface.ts';
import type { Position } from './GameInterface.ts';
/**
 * Colony type enum for different nutrient patterns (viewer-only information)
 */
export declare enum ColonyTileId {
    STEADY_PRODUCER = 1,
    HIGH_VALUE_OSCILLATOR = 2,
    STANDARD_RHYTHM = 3,
    DOUBLE_BEAT = 4,
    CRESCENDO = 5,
    TEST = 100
}
/**
 * Colony type information available only to viewers
 */
export interface ColonyTypeInfo {
    position: Position;
    tileId: ColonyTileId;
}
/**
 * Extended viewer game state with additional information not available to players
 */
export interface ViewerGameState extends BaseViewerGameState {
    colonyTypes?: ColonyTypeInfo[];
}
