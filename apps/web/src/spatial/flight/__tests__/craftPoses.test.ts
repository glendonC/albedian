import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import * as THREE from 'three';
import { ALIVE_AT_REST } from '../craftAlive.js';
import { poseWeights, posesOf, sceneWithClips, setPoses, SUIT_POSES } from '../craftPoses.js';

// craft-alive-two (LEAD ANSWER 25 Nov 07:12 and 07:16; MOTION's craft-alive-proposal, "The suit"): the EMU's three poses
// from ASSETS' rig, hover as NASA filmed a free flight, cruise and brake, blended by the signals that lean and bank it.

const seed = Number(process.env.ALBEDIAN_FC_SEED ?? 1);

describe('the poses\' weights from the alive signals', () => {
  it('hovers at rest, cruises at full speed and brakes on a full brake', () => {
    expect(poseWeights(ALIVE_AT_REST)).toEqual({ hover: 1, cruise: 0, brake: 0 });
    expect(poseWeights({ ...ALIVE_AT_REST, speed: 1 })).toEqual({ hover: 0, cruise: 1, brake: 0 });
    expect(poseWeights({ ...ALIVE_AT_REST, speed: 1, brake: 1 })).toEqual({ hover: 0, cruise: 0, brake: 1 });
  });

  it('cruises as much as the speed\'s share of what the brake leaves, and hovers the rest', () => {
    const w = poseWeights({ ...ALIVE_AT_REST, speed: 0.5, brake: 0.4 });
    expect(w.brake).toBeCloseTo(0.4, 12);
    expect(w.cruise).toBeCloseTo(0.5 * 0.6, 12);
    expect(w.hover).toBeCloseTo(1 - 0.4 - 0.3, 12);
  });

  it('never leaves the three outside 0 to 1, and they always sum to 1, whatever the signals (the bank does not pose)', () => {
    fc.assert(
      fc.property(fc.double({ min: -2, max: 3, noNaN: true }), fc.double({ min: -2, max: 3, noNaN: true }), fc.double({ min: -1, max: 1, noNaN: true }), (speed, brake, bank) => {
        const w = poseWeights({ bank, speed, brake, roll: 0 });
        for (const k of SUIT_POSES) {
          expect(w[k]).toBeGreaterThanOrEqual(0);
          expect(w[k]).toBeLessThanOrEqual(1);
        }
        expect(w.hover + w.cruise + w.brake).toBeCloseTo(1, 12);
        expect(poseWeights({ bank: 0, speed, brake, roll: 0 })).toEqual(w);
      }),
      { seed, numRuns: 200 }
    );
  });
});

/** A one-bone figure with three poses that turn the bone about x by 10, 40 and -30 degrees. */
function figure(names: readonly string[] = SUIT_POSES, empty = false) {
  const root = new THREE.Group();
  const bone = new THREE.Bone();
  bone.name = 'knee';
  root.add(bone);
  const deg = { hover: 10, cruise: 40, brake: -30 } as Record<string, number>;
  root.animations = names.map((name) => {
    const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), ((deg[name] ?? 0) * Math.PI) / 180);
    return new THREE.AnimationClip(name, 0, empty ? [] : [new THREE.QuaternionKeyframeTrack('knee.quaternion', [0], [q.x, q.y, q.z, q.w])]);
  });
  return { root, bone };
}

const angleX = (bone: THREE.Bone) => (2 * Math.atan2(bone.quaternion.x, bone.quaternion.w) * 180) / Math.PI;

describe('a model\'s poses', () => {
  it('are found by their names when the file carries all three, and none otherwise', () => {
    expect(posesOf(figure().root)).not.toBeNull();
    expect(posesOf(figure(['hover', 'cruise']).root)).toBeNull();
    expect(posesOf(figure(SUIT_POSES, true).root)).toBeNull();
    expect(posesOf(new THREE.Group())).toBeNull();
  });

  it('put each pose whole on the bones at its weight', () => {
    const { root, bone } = figure();
    const poses = posesOf(root)!;
    setPoses(poses, { hover: 1, cruise: 0, brake: 0 });
    expect(angleX(bone)).toBeCloseTo(10, 4);
    setPoses(poses, { hover: 0, cruise: 1, brake: 0 });
    expect(angleX(bone)).toBeCloseTo(40, 4);
    setPoses(poses, { hover: 0, cruise: 0, brake: 1 });
    expect(angleX(bone)).toBeCloseTo(-30, 4);
  });

  it('blend between the poses by their weights, as the mixer blends quaternions', () => {
    const { root, bone } = figure();
    const poses = posesOf(root)!;
    setPoses(poses, { hover: 0.5, cruise: 0.5, brake: 0 });
    expect(angleX(bone)).toBeCloseTo(25, 1);
  });

  it('give the stance the file was modelled in back with no weights', () => {
    const { root, bone } = figure();
    const poses = posesOf(root)!;
    setPoses(poses, { hover: 0, cruise: 1, brake: 0 });
    setPoses(poses, null);
    expect(angleX(bone)).toBeCloseTo(0, 6);
  });

  it('come with the scene from the glTF loader, which keeps them beside it', () => {
    const scene = new THREE.Group();
    const clips = figure().root.animations;
    expect(sceneWithClips({ scene, animations: clips })).toBe(scene);
    expect(scene.animations).toBe(clips);
  });
});
