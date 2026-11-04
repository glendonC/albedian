import { Vector3, type Object3D } from 'three';

const cameraWorld = new Vector3();

/**
 * Centres a sky mount (a sphere of catalog stars) on the camera, so each star sits at its catalog
 * direction from wherever the camera is, as the sky dome does at infinity: the stars are parsecs
 * away, and a sphere of finite radius around a fixed point slides against the dome as soon as
 * the camera leaves that point. Only the position changes, in the mount parent's frame; the
 * mount's ICRS orientation and radius stay. Call once per frame after the camera has moved.
 */
export function centreOnCamera(mount: Object3D, camera: Object3D): void {
  camera.getWorldPosition(cameraWorld);
  if (mount.parent) {
    mount.parent.updateWorldMatrix(true, false);
    mount.parent.worldToLocal(cameraWorld);
  }
  mount.position.copy(cameraWorld);
}
