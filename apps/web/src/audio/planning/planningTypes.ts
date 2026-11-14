/**
 * Domain types and interfaces for pure spatial audio voice planning.
 *
 * Nothing here touches Web Audio.
 */

import type { DistanceWindow } from '../mappings.js';

export type Vec3 = readonly [number, number, number];
/** Quaternion in three.js order (x, y, z, w), rotating listener-local axes into world axes. */
export type Quat = readonly [number, number, number, number];

export type AudioSourceKind = 'star' | 'pulsar';

export interface AudioSourceInput {
  readonly id: string;
  readonly kind: AudioSourceKind;
  /**
   * Unit vector from the listener toward the object, in the world frame the listener
   * orientation is expressed in (three.js convention: +x right, +y up, -z forward).
   */
  readonly directionWorld?: Vec3 | null;
  readonly distancePc?: number | null;
  /** Bolometric luminosity for stars; for pulsars, the power the caller chose (e.g. ATNF EDOT). */
  readonly luminositySolar?: number | null;
  /** Required for stars. */
  readonly teffK?: number | null;
  /** Required for pulsars: ATNF `F0`, Hz. */
  readonly spinFrequencyHz?: number | null;
  /** Optional muffling layer. */
  readonly extinctionAvMag?: number | null;
  /** Optional beating layer, e.g. ScientificEvidence.angularResidualArcsec. */
  readonly residualArcsec?: number | null;
  /** The user's selected target always gets a voice when it is audible. */
  readonly isTarget?: boolean;
  /** Passed through unchanged so an inspector can disclose what kind of evidence is heard. */
  readonly evidenceClass?: string;
}

export interface VoicePlan {
  readonly id: string;
  readonly kind: AudioSourceKind;
  readonly isTarget: boolean;
  readonly evidenceClass: string | null;
  readonly levelDb: number;
  readonly perceptualDistance: number;
  readonly drrDb: number;
  readonly directGain: number;
  readonly reverbGain: number;
  readonly distanceLowpassHz: number;
  /** `null`: no A_V supplied, so no muffling layer. */
  readonly dustLowpassHz: number | null;
  readonly directionListener: Vec3;
  readonly star: { readonly teffK: number; readonly planckScaleHz: number } | null;
  readonly pulsar: { readonly pulseRateHz: number } | null;
  /** `null`: no residual supplied, so no beating layer (not a clean interval either). */
  readonly uncertainty: { readonly beatHz: number } | null;
}

export type PlanOutcome =
  | { readonly status: 'audible'; readonly plan: VoicePlan }
  | {
      readonly status: 'silent';
      readonly id: string;
      readonly reason: 'missing_input' | 'below_audibility_floor';
      readonly missing: readonly string[];
    };

export interface PlanContext {
  readonly distanceWindow: DistanceWindow;
  readonly listenerOrientation: Quat;
}
