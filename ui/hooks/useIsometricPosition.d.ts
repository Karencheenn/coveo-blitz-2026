import { GridPosition, PixelPosition } from '../geometry/coordinates';
import { Constants } from '../components/PlayZone';
export declare function useIsometricPosition(gridPosition: GridPosition, constants: Constants): PixelPosition;
export declare function useIsometricPositions(gridPositions: GridPosition[], constants: Constants): PixelPosition[];
/**
 * Enhanced isometric positioning that accounts for tile thickness based on nutrient values
 */
export declare function useIsometricPositionWithThickness(gridPosition: GridPosition, constants: Constants, nutrientValue?: number): PixelPosition;
