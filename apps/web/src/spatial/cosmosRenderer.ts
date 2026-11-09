import type { RenderLayerSet } from '@albedian/contracts';

/**
 * What the observatory viewport asks of a cosmos renderer. `SparkPortalRenderer` (WebGL2 and
 * Spark) is the default; the WebGPU host (`webgpu/CislunarWebGpuRenderer.ts`, `?renderer=webgpu`)
 * implements the same calls for the scenes ported to it so far.
 */

export type ShowcaseSceneKey = 'cislunar' | 'black_hole' | 'supergiant' | 'saturn_horizon';

export type CosmosRendererKind = 'webgl-spark' | 'webgpu';

/**
 * Where the camera looks against the catalog sky, for listeners that follow the view (the audio
 * engine). Quaternions are three.js order (x, y, z, w).
 */
export interface CameraSkyPose {
  /** Rotates camera axes (looking along -z, +y up) into world axes. */
  readonly cameraWorldQuaternion: readonly [number, number, number, number];
  /** Rotates ICRS axes into world axes: the catalog star sphere drawn now. */
  readonly icrsWorldQuaternion: readonly [number, number, number, number];
  /** Vertical field of view, degrees. */
  readonly fovDeg: number;
  /** Width over height. */
  readonly aspect: number;
  /** Faintest catalog magnitude the star field draws now. */
  readonly limitingMagnitude: number;
}

export interface CosmosRenderer {
  /**
   * The recording's place and, when it has one, its capture time (ISO 8601 UTC); without a capture
   * time the sky is for the device's time now.
   */
  setObserverCoordinates(latDeg: number, lonDeg: number, capturedAtUtc?: string | null): void;
  /** Forgets the place and time, for a recording without a location; absent where nothing is held. */
  clearObserverCoordinates?(): void;
  setShowcaseMode(enabled: boolean): void;
  /**
   * Shows the sky view when the landing ends, unless it is already showing (so the viewer keeps
   * their turn); absent where a renderer draws only the landing scene.
   */
  showSkyView?(): void;
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
  /** The camera's pose against the catalog sky; absent, or null before a star sphere is mounted. */
  getCameraSkyPose?(): CameraSkyPose | null;
}

/** Landing scenes each renderer can draw, in carousel order. */
export const WEBGPU_PORTED_SHOWCASE_SCENES: readonly ShowcaseSceneKey[] = ['cislunar'];
