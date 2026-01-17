import { FC } from 'react';
import type { Spawner } from '../../GameInterface';
import type { ViewerGameState } from '../../ViewerTypes';
import { Constants } from '../PlayZone';
interface ColonySpriteProps {
    colony: Spawner;
    constants: Constants;
    gameState: ViewerGameState;
    nutrientValue?: number;
}
export declare const ColonySprite: FC<ColonySpriteProps>;
export {};
