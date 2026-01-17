import { _ReactPixi } from '@pixi/react';
import { FC } from 'react';
import { Filter, Texture } from 'pixi.js';
interface OutlinedSpriteProps {
    width: number;
    height: number;
    texture: Texture;
    position: _ReactPixi.PointLike;
    anchor: _ReactPixi.PointLike;
    angle: number;
    color: number;
    filter?: Filter;
    highlightRatio?: number;
}
export declare const OutlinedSprite: FC<OutlinedSpriteProps>;
export {};
