import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { raDecToUnitVector, worldToPixelWCS } from '@albedian/camera-rig';
import { ICRS_TO_SCENE_QUATERNION } from '../../catalogStars/field/icrsFrame.js';
import { skyViewOfPose, wcsOfSkyView } from '../skyViewWcs.js';

// go-there-predicted-2 (DECIDED-BY-LEAD 25 Nov 02:45, A): the flight leaves from the star's ring as the predicted sky
// drew it: the TAN WCS made from the sky view draws every star where the portal's perspective camera did.

const W = 390;
const H = 844;

/** The portal as it stands: a star mount turned ICRS to scene under a local frame, the camera turned under it. */
function portal(yaw: number, pitch: number, roll: number, fovDeg: number, frame: THREE.Quaternion) {
  const scene = new THREE.Scene();
  const local = new THREE.Group();
  local.quaternion.copy(frame);
  scene.add(local);
  const camera = new THREE.PerspectiveCamera(fovDeg, W / H, 0.05, 2800);
  camera.rotation.order = 'YXZ';
  camera.rotation.set(pitch, yaw, roll);
  local.add(camera);
  const mount = new THREE.Group();
  mount.quaternion.copy(ICRS_TO_SCENE_QUATERNION);
  scene.add(mount);
  scene.updateMatrixWorld(true);
  const q = (o: THREE.Object3D) => o.getWorldQuaternion(new THREE.Quaternion());
  const pose = { cameraWorldQuaternion: q(camera).toArray() as [number, number, number, number], icrsWorldQuaternion: q(mount).toArray() as [number, number, number, number], fovDeg };
  /** Where the portal's camera draws an ICRS direction, in CSS pixels. */
  const drawn = (raDeg: number, decDeg: number) => {
    const p = new THREE.Vector3(...raDecToUnitVector(raDeg, decDeg)).multiplyScalar(100).applyMatrix4(mount.matrixWorld).project(camera);
    return { x: ((p.x + 1) / 2) * W, y: ((1 - p.y) / 2) * H, front: p.z < 1 };
  };
  return { pose, drawn, camera };
}

describe('wcsOfSkyView', () => {
  it('draws every star in the window where the predicted sky\'s camera drew it, to a thousandth of a pixel', () => {
    const frame = new THREE.Quaternion().setFromEuler(new THREE.Euler(0.4, -1.1, 0.25));
    for (const [yaw, pitch, roll, fov] of [[0.3, 0.45, 0, 46], [-2.2, 0.9, 0.2, 30], [1.4, -0.3, -0.35, 70]] as const) {
      const { pose, drawn, camera } = portal(yaw, pitch, roll, fov, frame);
      const view = skyViewOfPose(pose)!;
      const wcs = wcsOfSkyView(view, W, H);
      // Stars spread over the window: each one's ICRS direction from a point on the screen through the portal's camera
      let checked = 0;
      for (let k = 0; k < 40; k++) {
        const ndc = new THREE.Vector3(((k * 37) % 19) / 9.5 - 1, ((k * 23) % 17) / 8.5 - 1, 0.5).unproject(camera);
        const dir = ndc.sub(camera.getWorldPosition(new THREE.Vector3())).normalize();
        const icrs = dir.applyQuaternion(pose.icrsWorldQuaternion ? new THREE.Quaternion(...pose.icrsWorldQuaternion).invert() : new THREE.Quaternion());
        const ra = (((Math.atan2(icrs.y, icrs.x) * 180) / Math.PI) % 360 + 360) % 360;
        const dec = (Math.asin(icrs.z) * 180) / Math.PI;
        const want = drawn(ra, dec);
        if (!want.front) continue;
        const got = worldToPixelWCS(ra, dec, wcs);
        expect(got.uPx).toBeCloseTo(want.x, 3);
        expect(got.vPx).toBeCloseTo(want.y, 3);
        checked += 1;
      }
      expect(checked).toBeGreaterThan(30);
    }
  });

  it('keeps the vertical field, and the window\'s centre is the look', () => {
    const view = { cameraQuaternionIcrs: [0, 0, 0, 1] as [number, number, number, number], fovVerticalDeg: 50 };
    const wcs = wcsOfSkyView(view, W, H);
    expect(wcs.fovVerticalDeg).toBe(50);
    expect([wcs.crpixX, wcs.crpixY]).toEqual([W / 2, H / 2]);
    // The identity camera looks along ICRS -z: toward the south celestial pole
    expect(wcs.crvalDecDeg).toBeCloseTo(-90, 9);
    // The tangent plane's half height is tan of half the field
    expect(((wcs.scaleDegPerPx * Math.PI) / 180) * (H / 2)).toBeCloseTo(Math.tan((25 * Math.PI) / 180), 12);
  });
});

describe('skyViewOfPose', () => {
  it('refuses a degenerate pose or a field the contract does not take', () => {
    expect(skyViewOfPose({ cameraWorldQuaternion: [0, 0, 0, 0], icrsWorldQuaternion: [0, 0, 0, 1], fovDeg: 50 })).toBeNull();
    expect(skyViewOfPose({ cameraWorldQuaternion: [0, 0, 0, 1], icrsWorldQuaternion: [0, 0, 0, 1], fovDeg: 0.5 })).toBeNull();
    expect(skyViewOfPose({ cameraWorldQuaternion: [0, 0, 0, 1], icrsWorldQuaternion: [0, 0, 0, 1], fovDeg: 50 })!.cameraQuaternionIcrs).toEqual([0, 0, 0, 1]);
  });
});
