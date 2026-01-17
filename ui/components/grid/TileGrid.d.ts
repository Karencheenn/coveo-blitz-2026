import { FC, PropsWithChildren } from 'react';
import { ViewerGameState } from '../../ViewerInterface';
import { Constants } from '../PlayZone';
export declare const TileGrid: FC<PropsWithChildren<{
    children?: React.ReactNode;
    constants: Constants;
    teamGameState: ViewerGameState;
}>>;
