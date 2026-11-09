import * as THREE from 'three';
import { icrsToScene } from '../catalogStars/CatalogStarField.js';
import { raDecDegToVec } from '../catalogStars/healpix.js';

/** Radius of the sphere sky targets sit on around the sky view's camera, in scene units. */
const SKY_TARGET_RADIUS = 26;

/**
 * Where a sky target sits in the scene: on a sphere around the camera, in its catalog direction
 * (ICRS axes to scene axes as the catalog stars use). The name never moves it.
 */
export function resolveSkyTargetPosition(
  _id: string,
  _title: string,
  raDeg: number,
  decDeg: number
): THREE.Vector3 {
  return icrsToScene(raDecDegToVec(raDeg, decDeg)).multiplyScalar(SKY_TARGET_RADIUS);
}

/**
 * The sky view's look pitch limit: just short of the celestial poles, so the camera (Euler order
 * YXZ) can face the Magellanic Clouds and the south celestial pole without flipping over the top.
 */
export const MAX_LOOK_PITCH_RAD = Math.PI / 2 - 0.01;

export function clampLookPitch(pitch: number): number {
  return Math.max(-MAX_LOOK_PITCH_RAD, Math.min(MAX_LOOK_PITCH_RAD, pitch));
}

/** Yaw and pitch that face a scene direction (yaw about +Y, then pitch, as the sky view's camera). */
export function lookAnglesForSceneDirection(direction: THREE.Vector3): { yaw: number; pitch: number } {
  const d = direction.clone().normalize();
  return { yaw: Math.atan2(-d.x, -d.z), pitch: clampLookPitch(Math.asin(Math.max(-1, Math.min(1, d.y)))) };
}

export function projectCatalogDirectionToScreen(
  camera: THREE.PerspectiveCamera,
  sceneDirection: THREE.Vector3,
  viewportWidth: number,
  viewportHeight: number
): { x: number; y: number; visible: boolean } {
  camera.updateMatrixWorld();
  const camPos = camera.getWorldPosition(new THREE.Vector3());
  const worldPos = camPos.addScaledVector(sceneDirection, 100);
  const camSpace = worldPos.clone().applyMatrix4(camera.matrixWorldInverse);
  if (camSpace.z >= 0) return { x: -999, y: -999, visible: false };
  const ndc = worldPos.project(camera);
  const x = (ndc.x * 0.5 + 0.5) * viewportWidth;
  const y = (-ndc.y * 0.5 + 0.5) * viewportHeight;
  return { x, y, visible: x >= 0 && x <= viewportWidth && y >= 0 && y <= viewportHeight };
}

export function projectSkyWorldPointToScreen(
  camera: THREE.PerspectiveCamera,
  worldPos: THREE.Vector3,
  viewportWidth: number,
  viewportHeight: number
): { x: number; y: number; visible: boolean } {
  camera.updateMatrixWorld();
  const camSpace = worldPos.clone().applyMatrix4(camera.matrixWorldInverse);
  if (camSpace.z >= -0.2) {
    return { x: -999, y: -999, visible: false };
  }
  const ndc = worldPos.clone().project(camera);
  const x = (ndc.x * 0.5 + 0.5) * viewportWidth;
  const y = (-ndc.y * 0.5 + 0.5) * viewportHeight;
  const visible =
    x >= 32 &&
    x <= viewportWidth - 32 &&
    y >= 56 &&
    y <= viewportHeight - 64;
  return { x, y, visible };
}

export function computeCameraTelemetrySnapshot(params: {
  camera: THREE.PerspectiveCamera;
  activeRegime: string;
  deepSkyAnchor: THREE.Vector3;
  cislunarMoonMesh: THREE.Mesh | null;
  betelgeuseMesh: THREE.Mesh | null;
  planetaryParentBodyMesh: THREE.Mesh | null;
  curYaw: number;
  curPitch: number;
  curFov: number;
  velYaw: number;
  velPitch: number;
  warpFactor: number;
  viewportWidth: number;
  viewportHeight: number;
}) {
  const rawAz = ((-params.curYaw * 180) / Math.PI + 210) % 360;
  const yawDeg = (rawAz + 360) % 360;
  const pitchDeg = Math.round(((params.curPitch * 180) / Math.PI + 24) * 10) / 10;
  const fovDeg = Math.round(params.curFov);

  let subjectWorld = new THREE.Vector3(0, 0, -10);
  if (params.activeRegime === 'cislunar' && params.cislunarMoonMesh) {
    subjectWorld = params.cislunarMoonMesh.getWorldPosition(new THREE.Vector3());
  } else if (params.activeRegime === 'solar_neighborhood' && params.betelgeuseMesh) {
    subjectWorld = params.betelgeuseMesh.getWorldPosition(new THREE.Vector3());
  } else if (params.activeRegime === 'deep_sky') {
    subjectWorld = new THREE.Vector3(0, 0.3, -13.5).add(params.deepSkyAnchor);
  } else if (params.activeRegime === 'planetary_surface' && params.planetaryParentBodyMesh) {
    subjectWorld = params.planetaryParentBodyMesh.getWorldPosition(new THREE.Vector3());
  }

  params.camera.updateMatrixWorld();
  const camSpace = subjectWorld.clone().applyMatrix4(params.camera.matrixWorldInverse);
  let subjectScreen = { x: params.viewportWidth * 0.5, y: params.viewportHeight * 0.5, visible: false };
  if (params.activeRegime !== 'earth_capture' && camSpace.z < -0.5) {
    const ndc = subjectWorld.clone().project(params.camera);
    subjectScreen = {
      x: (ndc.x * 0.5 + 0.5) * params.viewportWidth,
      y: (-ndc.y * 0.5 + 0.5) * params.viewportHeight,
      visible: true,
    };
  }

  return {
    yawDeg,
    pitchDeg,
    fovDeg,
    velYaw: params.velYaw,
    velPitch: params.velPitch,
    warpFactor: params.warpFactor,
    subjectScreen,
  };
}
