import * as THREE from 'three';
import type { AliveSignals } from './craftAlive.js';

/**
 * craft-alive-two, part two of the alive craft (LEAD ANSWER 25 Nov 07:12 and 07:16, on MOTION's craft-alive-proposal,
 * "The suit"): the suit's three poses from ASSETS' rig (suit-rig-one version two, craft-ao-one), blended by the same
 * signals that lean and bank it. Hover as NASA filmed a free flight (knees a little bent, forearms forward), cruise
 * (forearms down along the body, legs straight), brake (knees up, forearms raised forward); the hands stay on the pack's
 * hand controllers in every pose and the upper arms keep NASA's stance. Each pose is one animation of the suit's file,
 * by its name, holding its bones still. The EMU's poses bend its elbows, hips and knees; the Z-2's file carries the three
 * names too, but its hard segments cannot bend without opening a ring, so its poses move nothing (ASSETS' record) and
 * only the lean and bank move it. While not steered the suit stands in the stance its file was modelled in, as its NASA
 * picture shows it. The poses are the film's language for flight, under the flight's Simulated tag, not a record.
 */
export const SUIT_POSES = ['hover', 'cruise', 'brake'] as const;
export type SuitPose = (typeof SUIT_POSES)[number];

/** How much of each pose the suit shows; the three sum to 1. */
export type PoseWeights = Readonly<Record<SuitPose, number>>;

const share = (v: number) => Math.max(0, Math.min(1, v));

/**
 * The poses' weights from the signals: the brake as much as the brake signal, cruise as much as the speed's share of
 * what the brake leaves, hover the rest. Steered at rest it hovers; under Reduce Motion the signals stay at rest, so it
 * holds the hover pose and nothing moves. The bank turns the whole figure (craftAlive.ts) and poses nothing.
 */
export function poseWeights(alive: AliveSignals): PoseWeights {
  const brake = share(alive.brake);
  const cruise = share(alive.speed) * (1 - brake);
  return { hover: 1 - brake - cruise, cruise, brake };
}

/** A model's poses: one action each on one mixer, every one of them playing at the weight it is given. */
export interface CraftPoses {
  readonly mixer: THREE.AnimationMixer;
  readonly actions: Readonly<Record<SuitPose, THREE.AnimationAction>>;
}

/** The model's three poses when its file carries all three by name with something to move; otherwise none. */
export function posesOf(model: THREE.Object3D): CraftPoses | null {
  const clips = SUIT_POSES.map((name) => model.animations.find((c) => c.name === name));
  if (clips.some((c) => !c) || clips.every((c) => c!.tracks.length === 0)) return null;
  const mixer = new THREE.AnimationMixer(model);
  const actions = Object.fromEntries(
    SUIT_POSES.map((name, i) => {
      const action = mixer.clipAction(clips[i]!);
      action.play();
      action.setEffectiveWeight(0);
      return [name, action];
    })
  ) as Record<SuitPose, THREE.AnimationAction>;
  return { mixer, actions };
}

/**
 * The poses set to their weights and put on the bones at once (no time passes: each pose is a still). With no weights
 * the bones go back to the stance the file was modelled in: three's mixer fills what the weights leave with it.
 */
export function setPoses(poses: CraftPoses, weights: PoseWeights | null): void {
  for (const name of SUIT_POSES) poses.actions[name].setEffectiveWeight(weights ? weights[name] : 0);
  poses.mixer.update(0);
}

/** The scene of a loaded glTF with its animations kept on it (three's loader returns them beside the scene). */
export function sceneWithClips(gltf: { scene: THREE.Group; animations: THREE.AnimationClip[] }): THREE.Group {
  gltf.scene.animations = gltf.animations;
  return gltf.scene;
}

/** What the layer says it drew: the weights, or `rest` for the modelled stance, or `none` for a model without poses. */
export function posesReport(poses: CraftPoses | null, weights: PoseWeights | null): string {
  if (!poses) return 'none';
  if (!weights) return 'rest';
  return SUIT_POSES.map((name) => `${name} ${weights[name].toFixed(2)}`).join(' ');
}
