import { FederatedPointerEvent } from 'pixi.js';
import { CameraState } from '../utils/camera';
import { IsometricConstants } from '../geometry/coordinates';
interface UseCameraOptions {
    constants: IsometricConstants;
    initialCamera?: Partial<CameraState>;
    zoomSpeed?: number;
    panSensitivity?: number;
}
interface CameraControls {
    camera: CameraState;
    setCamera: (camera: CameraState) => void;
    handlePointerDown: (event: FederatedPointerEvent) => void;
    handlePointerMove: (event: FederatedPointerEvent) => void;
    handlePointerUp: (event: FederatedPointerEvent) => void;
    handleWheel: (event: React.WheelEvent) => void;
    zoomIn: () => void;
    zoomOut: () => void;
    resetCameraView: () => void;
}
export declare function useCamera({ constants, initialCamera, zoomSpeed, panSensitivity }: UseCameraOptions): CameraControls;
export {};
