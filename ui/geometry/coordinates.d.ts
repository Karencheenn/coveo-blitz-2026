export interface GridPosition {
    x: number;
    y: number;
}
export interface PixelPosition {
    x: number;
    y: number;
}
export interface IsometricConstants {
    tileWidth: number;
    tileHeight: number;
    gridWidth: number;
    gridHeight: number;
    screenWidth: number;
    screenHeight: number;
}
/**
 * Convert grid coordinates to isometric pixel coordinates
 * Grid (0,0) maps to top center of diamond
 */
export declare function gridToIsometricPixel(gridPos: GridPosition, constants: IsometricConstants): PixelPosition;
/**
 * Convert isometric pixel coordinates back to grid coordinates
 * Useful for mouse interaction and hit detection
 */
export declare function isometricPixelToGrid(pixelPos: PixelPosition, constants: IsometricConstants): GridPosition;
/**
 * Calculate isometric constants based on screen size and grid dimensions
 */
export declare function calculateIsometricConstants(screenWidth: number, screenHeight: number, gridWidth: number, gridHeight: number, options?: {
    screenUsage?: number;
    aspectRatio?: number;
    verticalPadding?: number;
}): IsometricConstants;
