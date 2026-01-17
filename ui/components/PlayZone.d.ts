import { FC } from 'react';
import { IsometricConstants } from '../geometry/coordinates.ts';
export interface Constants {
    tileSize: number;
    width: number;
    height: number;
    isometric: IsometricConstants;
}
export declare const PlayZone: FC;
