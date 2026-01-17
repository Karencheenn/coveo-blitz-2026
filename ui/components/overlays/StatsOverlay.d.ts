import { FC } from 'react';
import type { ViewerGameState } from '../../ViewerTypes';
import { BlitzType } from '@coveo/blitz-ui/src/types';
interface GameStatsProps {
    tick: ViewerGameState | null;
    useBlitzState: <P extends keyof BlitzType>(property: P) => BlitzType[P];
    currentStats: unknown;
}
export declare const GameStats: FC<GameStatsProps>;
export {};
