import { IsometricConstants } from '../geometry/coordinates';
export interface CameraState {
    zoom: number;
    panX: number;
    panY: number;
    minZoom: number;
    maxZoom: number;
}
export interface PixelPosition {
    x: number;
    y: number;
}
/**
 * Create initial camera state with sensible defaults
 */
export declare function createCameraState(options?: Partial<CameraState>): CameraState;
/**
 * Convert screen coordinates to world coordinates considering camera transform
 */
export declare function screenToWorldCoordinates(screenPos: PixelPosition, camera: CameraState): PixelPosition;
/**
 * Apply constraints to camera state to prevent excessive panning/zooming
 */
export declare function constrainCamera(camera: CameraState, constants: IsometricConstants): CameraState;
/**
 * Version of constrainCamera with minimal constraints - useful for debugging
 * Only constrains zoom, allows nearly unlimited panning
 */
export declare function constrainCameraLoose(camera: CameraState, constants: IsometricConstants): CameraState;
/**
 * Calculate visible grid bounds for performance optimization
 */
export declare function getVisibleGridBounds(camera: CameraState, constants: IsometricConstants): {
    minX: number;
    maxX: number;
    minY: number;
    maxY: number;
};
/**
 * Zoom the camera while keeping a specific point fixed on screen
 * @param camera Current camera state
 * @param newZoom Target zoom level
 * @param screenPos Screen position to zoom towards (defaults to screen center)
 * @param constants Isometric constants for screen dimensions
 */
export declare function zoomToPoint(camera: CameraState, newZoom: number, screenPos: PixelPosition | null, constants: IsometricConstants): CameraState;
/**
 * Zoom to screen center by a relative amount
 * @param camera Current camera state
 * @param zoomDelta Amount to change zoom (positive = zoom in, negative = zoom out)
 * @param constants Isometric constants for screen dimensions
 */
export declare function zoomToCenter(camera: CameraState, zoomDelta: number, constants: IsometricConstants): CameraState;
/**
 * Handle mouse wheel zoom towards cursor position
 * @param camera Current camera state
 * @param wheelEvent Mouse wheel event
 * @param zoomSpeed Zoom sensitivity
 * @param constants Isometric constants
 */
export declare function handleWheelZoom(camera: CameraState, wheelEvent: WheelEvent, zoomSpeed: number, constants: IsometricConstants): CameraState;
/**
 * Reset camera to initial state (zoom 1.0, pan 0,0)
 * @param camera Current camera state (to preserve min/max zoom settings)
 */
export declare function resetCamera(camera: CameraState): CameraState;
/**
 * Calculate camera position to center the isometric map on screen
 * @param constants Isometric constants with screen and grid dimensions
 */
export declare function calculateCenteredCameraPosition(constants: IsometricConstants): {
    panX: number;
    panY: number;
};
