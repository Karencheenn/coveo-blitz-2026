import { FC } from 'react';
import { Spore as SporeType } from '../../GameInterface';
import { Constants } from '../PlayZone';
import { ViewerGameState } from '../../ViewerInterface';
interface SporeProps {
    spore: SporeType;
    constants: Constants;
    gameState: ViewerGameState;
    nutrientValue?: number;
}
export declare const SporeSprite: FC<SporeProps>;
export {};
