import { FC, PropsWithChildren, ReactNode } from 'react';
import type { ViewerGameState } from './ViewerTypes';
interface BlitzViewerProps {
    game?: string | ViewerGameState[];
    isCaster?: boolean;
    children?: ReactNode;
}
export declare const BlitzViewer: FC<BlitzViewerProps> & {
    DownloadGameLogs: typeof DownloadGameLogs;
    DownloadBotLogs: typeof DownloadBotLogs;
    MapName: typeof MapName;
    Scores: typeof Scores;
};
declare const DownloadGameLogs: FC<PropsWithChildren>;
declare const DownloadBotLogs: FC<PropsWithChildren>;
declare const MapName: FC<PropsWithChildren>;
declare const Scores: FC<PropsWithChildren>;
export {};
