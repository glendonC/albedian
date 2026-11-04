import type { RenderLayerSet } from '@albedian/contracts';

/**
 * What the observatory viewport asks of a cosmos renderer. `SparkPortalRenderer` (WebGL2 and
 * Spark) is the default; the WebGPU host (`webgpu/CislunarWebGpuRenderer.ts`, `?renderer=webgpu`)
 * implements the same calls for the scenes ported to it so far.
 */

export type ShowcaseSceneKey = 'cislunar' | 'black_hole' | 'supergiant' | 'orion_nebula' | 'saturn_horizon';

export type CosmosRendererKind = 'webgl-spark' | 'webgpu';

export interface CosmosRenderer {
  setObserverCoordinates(latDeg: number, lonDeg: number): void;
  setShowcaseMode(enabled: boolean): void;
  transitionToShowcaseScene(sceneKey: ShowcaseSceneKey, instant?: boolean): void;
  /** Holds the scene clock at `seconds` for matched snapshots; null runs it again. */
  holdClock(seconds: number | null): void;
  nudgeMovement(dx: number, dy: number, dz: number): void;
  nudgeLook(dYaw: number, dPitch: number): void;
  dragLook(deltaPixelsX: number, deltaPixelsY: number): void;
  zoomByWheel(deltaY: number): void;
  resize(width: number, height: number): void;
  dispose(): void;
  /**
   * Layers drawn now with their evidence class and disclosure; absent, or null for the current
   * regime, where not declared yet.
   */
  getRenderLayers?(): RenderLayerSet | null;
}

/** Landing scenes each renderer can draw, in carousel order. */
export const WEBGPU_PORTED_SHOWCASE_SCENES: readonly ShowcaseSceneKey[] = ['cislunar'];
