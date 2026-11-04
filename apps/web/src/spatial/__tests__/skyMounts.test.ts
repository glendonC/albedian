import * as THREE from 'three';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { centreOnCamera } from '../skyMounts.js';

/**
 * The catalog stars are at parsecs, so on screen they sit at infinity: a sphere of stars drawn
 * around a fixed point slides against the sky dome (also at infinity) whenever the camera moves
 * off that point (SKY_PANORAMA_REGISTRATION.md, finding 5). Centring each star mount on the
 * camera every frame keeps every star at its catalog direction from wherever the camera is.
 */
const finite = fc.double({ min: -500, max: 500, noNaN: true });
const angle = fc.double({ min: -Math.PI, max: Math.PI, noNaN: true });

describe('star mounts follow the camera', () => {
  it('PBT-MOUNT-1: puts the mount at the camera wherever the camera and the mount parent are', () => {
    fc.assert(
      fc.property(
        fc.tuple(finite, finite, finite),
        fc.tuple(finite, finite, finite),
        fc.tuple(angle, angle, angle),
        fc.tuple(finite, finite, finite),
        ([px, py, pz], [rx, ry, rz], [ax, ay, az], [cx, cy, cz]) => {
          const scene = new THREE.Scene();
          const regime = new THREE.Group();
          regime.position.set(px, py, pz);
          regime.rotation.set(ax, ay, az);
          const mount = new THREE.Group();
          mount.quaternion.set(0, -0.5, 0.5, 0.7071067811865476).normalize();
          mount.scale.setScalar(85);
          regime.add(mount);
          const rig = new THREE.Group();
          rig.position.set(rx, ry, rz);
          const camera = new THREE.PerspectiveCamera();
          camera.position.set(cx * 0.01, cy * 0.01, cz * 0.01);
          rig.add(camera);
          scene.add(regime, rig);
          scene.updateMatrixWorld(true);

          const quaternionBefore = mount.quaternion.clone();
          centreOnCamera(mount, camera);

          const mountWorld = mount.getWorldPosition(new THREE.Vector3());
          const cameraWorld = camera.getWorldPosition(new THREE.Vector3());
          expect(mountWorld.distanceTo(cameraWorld)).toBeLessThan(1e-9 * (1 + cameraWorld.length()));
          // Only the position moves: the ICRS orientation and the radius stay as they were
          expect(mount.quaternion.equals(quaternionBefore)).toBe(true);
          expect(mount.scale.x).toBe(85);
        }
      ),
      { numRuns: 200 }
    );
  });

  it('keeps a star at its catalog direction from a camera moved off the old anchor', () => {
    const regime = new THREE.Group();
    const mount = new THREE.Group();
    mount.scale.setScalar(85);
    regime.add(mount);
    const star = new THREE.Object3D();
    star.position.set(0, 0, -1); // unit direction in the mount
    mount.add(star);
    const camera = new THREE.PerspectiveCamera();
    camera.position.set(1, 0, 0); // one unit off the anchor
    const scene = new THREE.Scene();
    scene.add(regime, camera);
    scene.updateMatrixWorld(true);

    const seen = () =>
      star.getWorldPosition(new THREE.Vector3()).sub(camera.getWorldPosition(new THREE.Vector3())).normalize();
    // Without centring, the star is seen 1/85 rad (0.67 deg) off its direction
    expect(seen().angleTo(new THREE.Vector3(0, 0, -1))).toBeGreaterThan(0.01);
    centreOnCamera(mount, camera);
    scene.updateMatrixWorld(true);
    expect(seen().angleTo(new THREE.Vector3(0, 0, -1))).toBeLessThan(1e-12);
  });
});
