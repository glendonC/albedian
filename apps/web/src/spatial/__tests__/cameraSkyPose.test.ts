import * as THREE from 'three';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { conjugateQuat, directionInView, listenerOrientationIcrs, skyViewFromPose } from '../../audio/skySources.js';
import { rotateByQuat, type Vec3 } from '../../audio/voicePlanning.js';
import { ICRS_TO_SCENE_QUATERNION } from '../catalogStars/CatalogStarField.js';
import type { CameraSkyPose } from '../cosmosRenderer.js';
import { ICRS_TO_GALACTIC } from '../sky/skyRegistration.js';
import { centreOnCamera } from '../skyMounts.js';
import { SparkPortalRenderer } from '../SparkPortalRenderer.js';
import { CislunarWebGpuRenderer } from '../webgpu/CislunarWebGpuRenderer.js';
import { EARTH_MOON_SEPARATION_DEG, landingPose } from '../webgpu/cislunar/landingScene.js';

/**
 * The camera's pose against the catalog sky, as both renderers report it to the audio listener
 * (CosmosRenderer.getCameraSkyPose). Each pose here comes from the renderer's own method, called on
 * the scene graph that method reads, and is read back through the audio feed's own functions
 * (audio/skySources.ts). The claim under test: a voice is heard from the direction its star is
 * drawn, wherever the camera's parents carry it. On the WebGPU host a rig at the landing's
 * viewpoint carries the camera, so only world rotations give the drawn view.
 */

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const DEG = Math.PI / 180;
const LANDING = landingPose();
/** The host's star mount radius and landing field of view (CislunarWebGpuRenderer.ts). */
const MOUNT_RADIUS = 85;
const LANDING_FOV_DEG = 26;
/** The look offsets dragLook and nudgeLook allow: any yaw, pitch clamped to 1.15 rad. */
const PITCH_LIMIT = 1.15;
const GALACTIC_CENTRE: Vec3 = [ICRS_TO_GALACTIC[0]!, ICRS_TO_GALACTIC[1]!, ICRS_TO_GALACTIC[2]!];

interface Arrangement {
  readonly scene: THREE.Scene;
  readonly camera: THREE.PerspectiveCamera;
  readonly starMount: THREE.Group;
}

/**
 * The WebGPU host's graph (constructor, placeRig and applyCamera): the camera on a rig at the
 * landing's viewpoint, the star mount in an unturned world group, centred on the camera.
 * `worldTurn` turns that world group, which the host does not do today, to show that the pose
 * follows any parent's rotation.
 */
function webgpuArrangement(yaw = 0, pitch = 0, worldTurn: THREE.Euler = new THREE.Euler()): Arrangement {
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(LANDING_FOV_DEG, 16 / 9, 0.05, 2800);
  camera.rotation.order = 'YXZ';
  const rig = new THREE.Group();
  rig.add(camera);
  scene.add(rig);
  rig.position.copy(LANDING.camera.position);
  rig.quaternion.copy(LANDING.camera.quaternion);
  const world = new THREE.Group();
  world.rotation.copy(worldTurn);
  scene.add(world);
  const starMount = new THREE.Group();
  starMount.quaternion.copy(ICRS_TO_SCENE_QUATERNION);
  starMount.scale.setScalar(MOUNT_RADIUS);
  world.add(starMount);
  camera.rotation.y = yaw;
  camera.rotation.x = pitch;
  camera.updateProjectionMatrix();
  rig.updateMatrixWorld(true);
  centreOnCamera(starMount, camera);
  scene.updateMatrixWorld(true);
  return { scene, camera, starMount };
}

/**
 * SparkPortalRenderer's graph: the camera in the local frame at a regime's anchor, each regime's
 * star mount in that regime's group at its own anchor, centred on the camera.
 */
function webglArrangement(yaw = 0, pitch = 0, regimeTurn: THREE.Euler = new THREE.Euler()) {
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(56, 16 / 9, 0.05, 2800);
  camera.rotation.order = 'YXZ';
  const localFrame = new THREE.Group();
  localFrame.position.set(0, 0, 600);
  localFrame.add(camera);
  scene.add(localFrame);
  const regimes = [new THREE.Group(), new THREE.Group()];
  regimes[0]!.position.set(0, 0, 0);
  regimes[1]!.position.set(0, 0, 600);
  regimes[1]!.rotation.copy(regimeTurn);
  const mounts = regimes.map((group, i) => {
    const mount = new THREE.Group();
    mount.quaternion.copy(ICRS_TO_SCENE_QUATERNION);
    mount.scale.setScalar(i === 0 ? 58 : 85);
    group.add(mount);
    scene.add(group);
    return mount;
  });
  // The second regime is shown, the first hidden, as syncRegimeVisibility leaves them
  regimes[0]!.visible = false;
  camera.rotation.y = yaw;
  camera.rotation.x = pitch;
  camera.updateProjectionMatrix();
  scene.updateMatrixWorld(true);
  for (const mount of mounts) centreOnCamera(mount, camera);
  scene.updateMatrixWorld(true);
  return { scene, camera, starMount: mounts[1]!, mounts };
}

/** The WebGPU host's own method, on the fields it reads. */
function webgpuPose(arrangement: Arrangement, limitingMagnitude = 6.5): CameraSkyPose {
  const host = {
    camera: arrangement.camera,
    starMount: arrangement.starMount,
    stars: { getLimitingMagnitude: () => limitingMagnitude },
  };
  return CislunarWebGpuRenderer.prototype.getCameraSkyPose.call(host as unknown as CislunarWebGpuRenderer);
}

/** SparkPortalRenderer's own method, on the fields it reads. */
function webglPose(
  camera: THREE.PerspectiveCamera,
  mounts: THREE.Group[],
  limitingMagnitude: number | null = 6.5
): CameraSkyPose | null {
  const renderer = {
    camera,
    catalogStarMounts: mounts,
    catalogStars: limitingMagnitude === null ? null : { getLimitingMagnitude: () => limitingMagnitude },
  };
  return SparkPortalRenderer.prototype.getCameraSkyPose.call(renderer as unknown as SparkPortalRenderer);
}

/** Where the renderer draws catalog direction `d`: the mount's point for it, in camera axes. */
function drawnInCamera({ camera, starMount }: Arrangement, d: Vec3): THREE.Vector3 {
  const world = starMount.localToWorld(new THREE.Vector3(d[0], d[1], d[2]));
  return camera.worldToLocal(world).normalize();
}

/** Angle between two vectors, radians; atan2 keeps it exact near zero, where acos cannot go below 2^-26. */
function angle(a: THREE.Vector3, b: THREE.Vector3): number {
  return Math.atan2(a.clone().cross(b).length(), a.dot(b));
}

/** Where the audio feed hears catalog direction `d`, in listener (camera) axes. */
function heardInCamera(pose: CameraSkyPose, d: Vec3): THREE.Vector3 {
  const orientation = listenerOrientationIcrs(pose);
  expect(orientation).not.toBeNull();
  const [x, y, z] = rotateByQuat(d, conjugateQuat(orientation!));
  return new THREE.Vector3(x, y, z);
}

const unitDirection = fc
  .tuple(
    fc.double({ min: -1, max: 1, noNaN: true }),
    fc.double({ min: -1, max: 1, noNaN: true }),
    fc.double({ min: -1, max: 1, noNaN: true })
  )
  .filter(([x, y, z]) => Math.hypot(x, y, z) > 1e-3)
  .map(([x, y, z]): Vec3 => {
    const n = Math.hypot(x, y, z);
    return [x / n, y / n, z / n];
  });
const yawArb = fc.double({ min: -Math.PI, max: Math.PI, noNaN: true });
const pitchArb = fc.double({ min: -PITCH_LIMIT, max: PITCH_LIMIT, noNaN: true });
const turnArb = fc
  .tuple(
    fc.double({ min: -Math.PI, max: Math.PI, noNaN: true }),
    fc.double({ min: -Math.PI, max: Math.PI, noNaN: true }),
    fc.double({ min: -Math.PI, max: Math.PI, noNaN: true })
  )
  .map(([x, y, z]) => new THREE.Euler(x, y, z));
const NO_TURN = new THREE.Euler();
const CELESTIAL_POLE: Vec3 = [0, 0, 1];

describe('the WebGPU landing reports where it looks against the catalog sky', () => {
  it('hears the galactic centre exactly where the landing draws the Earth, at 2025-03-07 11:00 UTC', () => {
    const arrangement = webgpuArrangement();
    const pose = webgpuPose(arrangement);
    // The camera stands on the line from the galactic centre through the Earth (landingScene.ts)
    const earthInCamera = arrangement.camera.worldToLocal(LANDING.earth.position.clone()).normalize();
    expect(angle(heardInCamera(pose, GALACTIC_CENTRE), earthInCamera)).toBeLessThan(1e-9);

    // The listener faces where the camera looks: between the Earth and the Moon
    const view = skyViewFromPose(pose);
    expect(view).not.toBeNull();
    const centre = new THREE.Vector3(...view!.centerIcrs);
    const separation = angle(centre, new THREE.Vector3(...GALACTIC_CENTRE)) / DEG;
    expect(Math.abs(separation - EARTH_MOON_SEPARATION_DEG / 2)).toBeLessThan(1e-6);
    expect(directionInView(GALACTIC_CENTRE, view!)).toBe(true);
  });

  it('passes the vertical field of view, the aspect and the drawn limit through', () => {
    const arrangement = webgpuArrangement();
    arrangement.camera.fov = 31.5;
    arrangement.camera.aspect = 2.4;
    const pose = webgpuPose(arrangement, 7.25);
    expect(pose.fovDeg).toBe(31.5);
    expect(pose.aspect).toBe(2.4);
    expect(pose.limitingMagnitude).toBe(7.25);
  });

  it('PBT-POSE-1: hears every catalog direction where the host draws its star, under any look offset', () => {
    fc.assert(
      fc.property(yawArb, pitchArb, turnArb, unitDirection, (yaw, pitch, turn, d) => {
        const arrangement = webgpuArrangement(yaw, pitch, turn);
        const pose = webgpuPose(arrangement);
        expect(angle(heardInCamera(pose, d), drawnInCamera(arrangement, d))).toBeLessThan(1e-9);
      }),
      {
        seed: SEED,
        numRuns: 300,
        examples: [
          [0, 0, NO_TURN, GALACTIC_CENTRE],
          [Math.PI, PITCH_LIMIT, NO_TURN, CELESTIAL_POLE],
          [-Math.PI / 2, -PITCH_LIMIT, NO_TURN, GALACTIC_CENTRE],
        ],
      }
    );
  });

  it('PBT-POSE-2: plans voices for exactly the frustum the camera draws', () => {
    fc.assert(
      fc.property(
        yawArb,
        pitchArb,
        fc.double({ min: 6, max: 74, noNaN: true }),
        fc.double({ min: 0.5, max: 3, noNaN: true }),
        unitDirection,
        (yaw, pitch, fov, aspect, d) => {
          const arrangement = webgpuArrangement(yaw, pitch);
          arrangement.camera.fov = fov;
          arrangement.camera.aspect = aspect;
          arrangement.camera.updateProjectionMatrix();
          const view = skyViewFromPose(webgpuPose(arrangement));
          expect(view).not.toBeNull();
          const inCamera = drawnInCamera(arrangement, d);
          const ndc = inCamera.clone().applyMatrix4(arrangement.camera.projectionMatrix);
          // Leave out directions within rounding of the frustum's edge
          fc.pre(Math.abs(Math.abs(ndc.x) - 1) > 1e-6 && Math.abs(Math.abs(ndc.y) - 1) > 1e-6);
          const drawnOnScreen = inCamera.z < 0 && Math.abs(ndc.x) < 1 && Math.abs(ndc.y) < 1;
          expect(directionInView(d, view!)).toBe(drawnOnScreen);
        }
      ),
      {
        seed: SEED,
        numRuns: 300,
        examples: [[0, 0, LANDING_FOV_DEG, 16 / 9, GALACTIC_CENTRE]],
      }
    );
  });
});

describe('the WebGL renderer reports where it looks against the catalog sky', () => {
  it('reads the star mount of the regime it shows and forwards a non-default limiting magnitude', () => {
    const turn = new THREE.Euler(0.4, -0.7, 0.2);
    const { camera, mounts, starMount } = webglArrangement(0.3, -0.2, turn);
    const pose = webglPose(camera, mounts, 8.25);
    expect(pose).not.toBeNull();
    const shown = starMount.getWorldQuaternion(new THREE.Quaternion());
    expect(pose!.icrsWorldQuaternion).toEqual([shown.x, shown.y, shown.z, shown.w]);
    expect(pose!.fovDeg).toBe(56);
    expect(pose!.aspect).toBe(16 / 9);
    expect(pose!.limitingMagnitude).toBe(8.25);
  });

  it('reports no pose before a star sphere is mounted, when no regime is shown, or before the catalog exists', () => {
    const { camera, mounts } = webglArrangement();
    expect(webglPose(camera, [])).toBeNull();
    expect(webglPose(camera, mounts, null)).toBeNull();
    for (const mount of mounts) {
      if (mount.parent) mount.parent.visible = false;
    }
    expect(webglPose(camera, mounts)).toBeNull();
  });

  it('PBT-POSE-3: hears every catalog direction where the shown regime draws its star', () => {
    fc.assert(
      fc.property(yawArb, pitchArb, turnArb, unitDirection, (yaw, pitch, turn, d) => {
        const arrangement = webglArrangement(yaw, pitch, turn);
        const pose = webglPose(arrangement.camera, arrangement.mounts);
        expect(pose).not.toBeNull();
        expect(angle(heardInCamera(pose!, d), drawnInCamera(arrangement, d))).toBeLessThan(1e-9);
      }),
      {
        seed: SEED,
        numRuns: 300,
        examples: [
          [0, 0, NO_TURN, GALACTIC_CENTRE],
          [Math.PI, -PITCH_LIMIT, NO_TURN, CELESTIAL_POLE],
        ],
      }
    );
  });
});
