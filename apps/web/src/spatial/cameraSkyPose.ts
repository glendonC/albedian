import { Quaternion, type Object3D, type PerspectiveCamera } from 'three';
import type { CameraSkyPose } from './cosmosRenderer.js';

const cameraWorld = new Quaternion();
const skyWorld = new Quaternion();

/**
 * The camera's pose against a catalog star sphere (CosmosRenderer.getCameraSkyPose), for listeners
 * that follow the view. Both rotations are world rotations, read through every parent: on the
 * WebGPU host a rig at the landing's viewpoint carries the camera, whose own rotation is only the
 * look offset. `starMount` is the star sphere drawn now; its world rotation takes ICRS axes into
 * the world. Its position (centred on the camera each frame) does not enter.
 */
export function cameraSkyPose(
  camera: Pick<PerspectiveCamera, 'getWorldQuaternion' | 'fov' | 'aspect'>,
  starMount: Pick<Object3D, 'getWorldQuaternion'>,
  limitingMagnitude: number
): CameraSkyPose {
  const c = camera.getWorldQuaternion(cameraWorld);
  const s = starMount.getWorldQuaternion(skyWorld);
  return {
    cameraWorldQuaternion: [c.x, c.y, c.z, c.w],
    icrsWorldQuaternion: [s.x, s.y, s.z, s.w],
    fovDeg: camera.fov,
    aspect: camera.aspect,
    limitingMagnitude,
  };
}
