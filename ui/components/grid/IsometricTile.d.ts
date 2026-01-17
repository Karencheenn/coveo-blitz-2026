import { ColorSource } from "pixi.js";
import { FC } from "react";
interface Position {
    x: number;
    y: number;
}
interface IsometricTileProps {
    color: ColorSource;
    lineWidth: number;
    fillColor?: ColorSource;
    fillAlpha?: number;
    position: Position;
    tileWidth: number;
    tileHeight: number;
    thickness?: number;
}
export declare const IsometricTile: FC<IsometricTileProps>;
export {};
