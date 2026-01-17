/**
 * Team color utilities for Ecosystem Dominance game
 *
 * Based on the game theme:
 * - Team 1: Electric Blue Oyster Collective (Blue)
 * - Team 2: Golden Chanterelle Consortium (Yellow/Gold)
 * - Team 3: Strawberry Federation (Red)
 * - Team 4: Bamboo Grove Alliance (Green)
 */
export interface TeamColorScheme {
    primary: number;
    light: number;
    dark: number;
    territory: number;
    sprite_prefix: string;
}
/**
 * Neutral entity colors (invasive species)
 */
export declare const NEUTRAL_ENTITY_COLORS: TeamColorScheme;
/**
 * Neutral/ground colors
 */
export declare const NEUTRAL_COLORS: {
    ground: number;
    wall: number;
    empty: number;
};
/**
 * Get the color scheme for a team using the team index from the map
 * @param teamIndex Team index (1-5) from map file (5 = NEUTRAL)
 * @returns Team color scheme
 */
export declare const getTeamColorScheme: (teamIndex: number) => TeamColorScheme;
/**
 * Get the primary color for a team
 * @param teamIndex Team index (1-4) from map file, or 5 for NEUTRAL
 * @returns Primary team color
 */
export declare const getTeamColor: (teamIndex: number) => number;
/**
 * Apply alpha transparency to a color by blending with a background color
 * Since PixiJS colors don't support alpha in hex format, we simulate transparency
 * by blending the color with the background color
 *
 * @param color Color to make transparent
 * @param alpha Alpha value (0.0 to 1.0)
 * @param backgroundColor Background color to blend with
 * @returns Blended color simulating transparency
 */
export declare const applyAlpha: (color: number, alpha: number, backgroundColor?: number) => number;
/**
 * Interpolate between two colors
 * @param color1 First color
 * @param color2 Second color
 * @param factor Interpolation factor (0.0 to 1.0)
 * @returns Interpolated color
 */
export declare const interpolateColor: (color1: number, color2: number, factor: number) => number;
/**
 * Get territory color for a team (faded version of team color)
 * @param teamIndex Team index (1-5) from map file (5 = NEUTRAL)
 * @param intensity Biomass intensity (0.0 to 1.0) - affects opacity
 * @returns Territory color with alpha applied
 */
export declare const getTerritoryColor: (teamIndex: number, intensity?: number) => number;
/**
 * Get color for biomass trail based on intensity
 * Stronger biomass = more opaque team color
 * @param teamIndex Team index (1-4) from map file, or 5 for NEUTRAL
 * @param biomass Biomass value
 * @param maxBiomass Maximum biomass for normalization
 * @returns Color representing the biomass intensity
 */
export declare const getBiomassTrailColor: (teamIndex: number, biomass: number, maxBiomass?: number) => number;
