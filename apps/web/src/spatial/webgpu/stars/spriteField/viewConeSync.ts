import { MathUtils, Quaternion, Vector3, type Group, type PerspectiveCamera } from 'three/webgpu';
import { ICRS_TO_SCENE_QUATERNION } from '../../../catalogStars/CatalogStarField.js';
import type { CatalogStarView } from '../../../catalogStars/CatalogStarTileLoader.js';

export const REFERENCE_DRAWING_HEIGHT_PX = 1080;
export const VIEW_CONE_PADDING_RAD = 0.08;
export const VIEW_CHANGE_EPSILON_RAD = 0.01;
export const VIEW_UPDATE_MIN_INTERVAL_MS = 120;

export interface ViewSyncState {
  readonly lastDir: Vector3;
  lastRadius: number;
  lastLimit: number;
  lastAtMs: number;
  readonly tmpDir: Vector3;
  readonly tmpQuat: Quaternion;
}

export function createViewSyncState(): ViewSyncState {
  return {
    lastDir: new Vector3(),
    lastRadius: -1,
    lastLimit: Number.NaN,
    lastAtMs: Number.NEGATIVE_INFINITY,
    tmpDir: new Vector3(),
    tmpQuat: new Quaternion(),
  };
}

export function invalidateViewSync(state: ViewSyncState): void {
  state.lastAtMs = Number.NEGATIVE_INFINITY;
}

export function selectActiveMount(mounts: readonly Group[]): Group | undefined {
  return mounts.find((m) => m.parent?.visible !== false) ?? mounts[0];
}

export function cameraHalfDiagonalRad(camera: PerspectiveCamera): number {
  return Math.atan(Math.tan(MathUtils.degToRad(camera.fov) / 2) * Math.hypot(1, camera.aspect));
}

export function pollCameraIcrsView(
  state: ViewSyncState,
  camera: PerspectiveCamera,
  mounts: readonly Group[],
  limitingMagnitude: number,
  nowMs: number,
  force = false
): CatalogStarView | null {
  const mount = selectActiveMount(mounts);
  camera.getWorldDirection(state.tmpDir);
  if (mount) {
    state.tmpDir.applyQuaternion(mount.getWorldQuaternion(state.tmpQuat).invert());
  } else {
    state.tmpDir.applyQuaternion(state.tmpQuat.copy(ICRS_TO_SCENE_QUATERNION).invert());
  }
  const radius = cameraHalfDiagonalRad(camera) + VIEW_CONE_PADDING_RAD;
  const moved = state.lastDir.angleTo(state.tmpDir);
  const changed =
    moved > VIEW_CHANGE_EPSILON_RAD ||
    Math.abs(radius - state.lastRadius) > VIEW_CHANGE_EPSILON_RAD ||
    limitingMagnitude !== state.lastLimit;
  if (!force && (!changed || nowMs - state.lastAtMs < VIEW_UPDATE_MIN_INTERVAL_MS)) return null;
  state.lastDir.copy(state.tmpDir);
  state.lastRadius = radius;
  state.lastLimit = limitingMagnitude;
  state.lastAtMs = nowMs;
  return {
    centerIcrs: [state.tmpDir.x, state.tmpDir.y, state.tmpDir.z],
    radiusRad: radius,
    limitingMagnitude,
  };
}
