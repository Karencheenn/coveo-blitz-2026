/**
 * Configuration for tile visual properties
 */
export interface TileThicknessConfig {
    /** Minimum thickness for tiles with 0 nutrients */
    minThickness: number;
    /** Maximum thickness for tiles with maximum nutrients */
    maxThickness: number;
    /** Maximum nutrient value expected in the game (used for linear interpolation) */
    maxNutrientValue: number;
}
/**
 * Tile thickness configuration - easily adjustable for visual tuning
 */
export declare const TILE_THICKNESS_CONFIG: TileThicknessConfig;
/**
 * Calculate tile thickness based on nutrient value using linear interpolation
 * @param nutrientValue - The nutrient value for the tile
 * @param customMaxNutrientValue - Optional custom max nutrient value (defaults to config value)
 */
export declare function calculateTileThickness(nutrientValue: number, customMaxNutrientValue?: number): number;
