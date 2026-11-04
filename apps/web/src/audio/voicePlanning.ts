/**
 * Pure voice planning: turns data about objects into the parameters a voice needs, decides
 * which objects get one of the bounded HRTF voices, and gives each voice a pitch.
 *
 * A voice is silent (never planned) when any input its core sound depends on is missing.
 * Optional layers (dust muffling, uncertainty beating) are present only when their own input
 * is present. Nothing here touches Web Audio.
 */

import {
  directReverbGains,
  directToReverberantDb,
  distanceLowpassHz,
  dustLowpassHz,
  isFiniteNumber,
  perceptualDistance,
  planckFrequencyScaleHz,
  pulsarPulseRateHz,
  receivedLevelDb,
  uncertaintyBeatHz,
  type DistanceWindow,
} from './mappings.js';

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

export function planVoice(input: AudioSourceInput, context: PlanContext): PlanOutcome {
  const id = typeof input.id === 'string' ? input.id : '';
  const missing: string[] = [];
  if (id.length === 0) missing.push('id');

  const directionListener = worldToListenerDirection(input.directionWorld ?? null, context.listenerOrientation);
  if (directionListener === null) missing.push('directionWorld');

  const perceptual = perceptualDistance(input.distancePc, context.distanceWindow);
  if (perceptual === null) missing.push('distancePc');

  if (!isFiniteNumber(input.luminositySolar) || input.luminositySolar <= 0) missing.push('luminositySolar');

  let star: VoicePlan['star'] = null;
  let pulsar: VoicePlan['pulsar'] = null;
  if (input.kind === 'star') {
    const planckScaleHz = planckFrequencyScaleHz(input.teffK);
    if (planckScaleHz === null || !isFiniteNumber(input.teffK)) missing.push('teffK');
    else star = { teffK: input.teffK, planckScaleHz };
  } else if (input.kind === 'pulsar') {
    const pulseRateHz = pulsarPulseRateHz(input.spinFrequencyHz);
    if (pulseRateHz === null) missing.push('spinFrequencyHz');
    else pulsar = { pulseRateHz };
  } else {
    missing.push('kind');
  }

  if (missing.length > 0 || directionListener === null || perceptual === null) {
    return { status: 'silent', id, reason: 'missing_input', missing };
  }

  const levelDb = receivedLevelDb(input.luminositySolar, input.distancePc);
  if (levelDb === null || levelDb === -Infinity) {
    return { status: 'silent', id, reason: 'below_audibility_floor', missing: [] };
  }

  const drrDb = directToReverberantDb(perceptual) ?? 0;
  const { direct, reverb } = directReverbGains(drrDb);
  const beatHz = uncertaintyBeatHz(input.residualArcsec);

  return {
    status: 'audible',
    plan: {
      id,
      kind: input.kind,
      isTarget: input.isTarget === true,
      evidenceClass: typeof input.evidenceClass === 'string' ? input.evidenceClass : null,
      levelDb,
      perceptualDistance: perceptual,
      drrDb,
      directGain: direct,
      reverbGain: reverb,
      distanceLowpassHz: distanceLowpassHz(perceptual) ?? 20000,
      dustLowpassHz: dustLowpassHz(input.extinctionAvMag),
      directionListener,
      star,
      pulsar,
      uncertainty: beatHz === null ? null : { beatHz },
    },
  };
}

// ---------------------------------------------------------------------------
// Pool selection
// ---------------------------------------------------------------------------

export const DEFAULT_VOICE_CAPACITY = 8;
export const DEFAULT_SELECTION_HYSTERESIS_DB = 3;

/**
 * Chooses which planned voices sound. Targets come first, then the loudest (brightest at the
 * listener), with incumbents favoured by `hysteresisDb` so voices do not thrash when two
 * objects are nearly equal. Duplicate ids keep their loudest plan. Deterministic for any
 * input order.
 */
export function selectVoices(
  plans: readonly VoicePlan[],
  incumbents: ReadonlySet<string>,
  capacity: number = DEFAULT_VOICE_CAPACITY,
  hysteresisDb: number = DEFAULT_SELECTION_HYSTERESIS_DB
): VoicePlan[] {
  const limit = Math.max(0, Math.floor(capacity));
  const byId = new Map<string, VoicePlan>();
  for (const plan of plans) {
    const existing = byId.get(plan.id);
    if (!existing || rankBefore(plan, existing)) byId.set(plan.id, plan);
  }
  const score = (plan: VoicePlan) => plan.levelDb + (incumbents.has(plan.id) ? hysteresisDb : 0);
  return [...byId.values()]
    .sort((a, b) => {
      if (a.isTarget !== b.isTarget) return a.isTarget ? -1 : 1;
      const diff = score(b) - score(a);
      if (diff !== 0) return diff;
      return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
    })
    .slice(0, limit);
}

function rankBefore(a: VoicePlan, b: VoicePlan): boolean {
  if (a.isTarget !== b.isTarget) return a.isTarget;
  return a.levelDb > b.levelDb;
}

// ---------------------------------------------------------------------------
// Identity pitch (carries no data)
// ---------------------------------------------------------------------------

/**
 * Pitches available to voices: just ratios of a pentatonic set on A2, 110 to 440 Hz. Pitch
 * encodes no physical quantity. It exists only so simultaneous voices stay separable, and it
 * is kept stable per object id so a listener can follow one object over time.
 */
export const IDENTITY_PITCHES_HZ: readonly number[] = [1, 9 / 8, 5 / 4, 3 / 2, 5 / 3, 2, 9 / 4, 5 / 2, 3, 10 / 3, 4].map(
  (ratio) => 110 * ratio
);

/** 32-bit FNV-1a hash, used only to spread ids over the pitch set. */
export function fnv1a32(text: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

/**
 * Gives each id a pitch from `pitches`. An id keeps its previous pitch while it stays active;
 * a new id takes its hashed preference or the next free pitch, so no two active voices share
 * a pitch while there are enough pitches.
 */
export function assignIdentityPitches(
  ids: readonly string[],
  previous: ReadonlyMap<string, number>,
  pitches: readonly number[] = IDENTITY_PITCHES_HZ
): Map<string, number> {
  const result = new Map<string, number>();
  if (pitches.length === 0) return result;
  const used = new Set<number>();
  const pending: string[] = [];
  for (const id of ids) {
    if (result.has(id)) continue;
    const prevIndex = pitches.indexOf(previous.get(id) ?? Number.NaN);
    if (prevIndex >= 0 && !used.has(prevIndex)) {
      used.add(prevIndex);
      result.set(id, pitches[prevIndex] ?? pitches[0]!);
    } else {
      pending.push(id);
    }
  }
  for (const id of pending) {
    if (result.has(id)) continue;
    const start = fnv1a32(id) % pitches.length;
    let index = start;
    for (let step = 0; step < pitches.length; step++) {
      const candidate = (start + step) % pitches.length;
      if (!used.has(candidate)) {
        index = candidate;
        break;
      }
    }
    used.add(index);
    result.set(id, pitches[index] ?? pitches[0]!);
  }
  return result;
}

// ---------------------------------------------------------------------------
// Listener geometry
// ---------------------------------------------------------------------------

export const IDENTITY_QUAT: Quat = [0, 0, 0, 1];

export function normalizeQuat(q: Quat | null | undefined): Quat | null {
  if (!q || q.length !== 4 || !q.every(isFiniteNumber)) return null;
  const n = Math.hypot(q[0], q[1], q[2], q[3]);
  if (!(n > 1e-12)) return null;
  return [q[0] / n, q[1] / n, q[2] / n, q[3] / n];
}

/** Rotates `v` by the unit quaternion `q` (v' = q v q*). */
export function rotateByQuat(v: Vec3, q: Quat): Vec3 {
  const [x, y, z] = v;
  const [qx, qy, qz, qw] = q;
  // t = 2 (q.xyz x v); v' = v + w t + q.xyz x t
  const tx = 2 * (qy * z - qz * y);
  const ty = 2 * (qz * x - qx * z);
  const tz = 2 * (qx * y - qy * x);
  return [
    x + qw * tx + (qy * tz - qz * ty),
    y + qw * ty + (qz * tx - qx * tz),
    z + qw * tz + (qx * ty - qy * tx),
  ];
}

/**
 * Direction of an object in listener-local axes (Web Audio's default listener faces -z with
 * +y up, the same as a three.js camera). `null` when the direction is missing or degenerate;
 * an invalid orientation falls back to identity because orientation is view state, not data.
 */
export function worldToListenerDirection(
  directionWorld: Vec3 | null | undefined,
  listenerOrientation: Quat | null | undefined
): Vec3 | null {
  if (!directionWorld || directionWorld.length !== 3 || !directionWorld.every(isFiniteNumber)) return null;
  const length = Math.hypot(directionWorld[0], directionWorld[1], directionWorld[2]);
  if (!(length > 1e-12)) return null;
  const unit: Vec3 = [directionWorld[0] / length, directionWorld[1] / length, directionWorld[2] / length];
  const q = normalizeQuat(listenerOrientation) ?? IDENTITY_QUAT;
  const local = rotateByQuat(unit, [-q[0], -q[1], -q[2], q[3]]);
  const n = Math.hypot(local[0], local[1], local[2]);
  return [local[0] / n, local[1] / n, local[2] / n];
}

/** Unit direction from azimuth (degrees, positive to the right) and elevation (degrees, up). */
export function directionFromAzimuthElevation(azimuthDeg: number, elevationDeg: number): Vec3 {
  const az = (azimuthDeg * Math.PI) / 180;
  const el = (elevationDeg * Math.PI) / 180;
  return [Math.sin(az) * Math.cos(el), Math.sin(el), -Math.cos(az) * Math.cos(el)];
}

/** Quaternion for a yaw (degrees, positive turns the listener to the left about +y). */
export function yawQuat(yawDeg: number): Quat {
  const half = (yawDeg * Math.PI) / 360;
  return [0, Math.sin(half), 0, Math.cos(half)];
}
