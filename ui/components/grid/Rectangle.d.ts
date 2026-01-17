import { ColorSource } from "pixi.js";
import { FC } from "react";
import { Position } from '../../GameInterface.ts';
interface RectangleProps {
    color: ColorSource;
    lineWidth: number;
    fillColor?: ColorSource;
    fillAlpha?: number;
    position: Position;
    length: number;
    width: number;
}
export declare const Rectangle: FC<RectangleProps>;
export {};
