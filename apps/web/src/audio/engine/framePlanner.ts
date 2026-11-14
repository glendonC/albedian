import {
  REGIME_DISTANCE_WINDOWS,
  clamp,
  isFiniteNumber,
  skyglowBedLevelDb,
  type ScaleAudioRegime,
} from '../mappings.js';
import {
  IDENTITY_QUAT,
  assignIdentityPitches,
  distanceWindowFor,
  normalizeQuat,
  planVoice,
  selectVoices,
  type AudioSourceInput,
  type PlanOutcome,
  type Quat,
  type VoicePlan,
} from '../voicePlanning.js';

export interface PlannedAudioFrame {
  readonly outcomes: readonly PlanOutcome[];
  readonly selected: readonly VoicePlan[];
  readonly pitches: Map<string, number>;
}

export const EMPTY_PLANNED_FRAME: PlannedAudioFrame = {
  outcomes: [],
  selected: [],
  pitches: new Map(),
};

export interface FramePlanInputs {
  readonly sources: readonly AudioSourceInput[];
  readonly regime: ScaleAudioRegime;
  readonly orientation: Quat;
  readonly capacity: number;
  readonly previousSelected: readonly VoicePlan[];
  readonly previousPitches: ReadonlyMap<string, number>;
}

/** Plans audible voices, applies pool selection with incumbent hysteresis, and assigns stable identity pitches. */
export function planAudioFrame(inputs: FramePlanInputs): PlannedAudioFrame {
  const outcomes = inputs.sources.map((source) =>
    planVoice(source, {
      distanceWindow: distanceWindowFor(source.kind, inputs.regime),
      listenerOrientation: inputs.orientation,
    })
  );
  const audible = outcomes.flatMap((outcome) => (outcome.status === 'audible' ? [outcome.plan] : []));
  const incumbents = new Set(inputs.previousSelected.map((plan) => plan.id));
  const selected = selectVoices(audible, incumbents, inputs.capacity);
  const pitches = assignIdentityPitches(
    selected.map((plan) => plan.id),
    inputs.previousPitches
  );
  return { outcomes, selected, pitches };
}

export interface BedLevelEvaluation {
  readonly levelDb: number | null;
  readonly silentBecause: string | null;
}

/** Computes the skyglow bed level in dBFS and the explicit reason whenever the bed is silent. */
export function evaluateBedLevel(
  regime: ScaleAudioRegime,
  skyBrightnessMagArcsec2: number | null,
  unveil: number
): BedLevelEvaluation {
  if (regime !== 'earth_capture') {
    return { levelDb: -Infinity, silentBecause: 'skyglow is heard only in the earth_capture regime' };
  }
  const levelDb = skyglowBedLevelDb(skyBrightnessMagArcsec2, unveil);
  if (levelDb === null) return { levelDb: null, silentBecause: 'skyBrightnessMagArcsec2 missing' };
  if (levelDb === -Infinity) {
    return {
      levelDb,
      silentBecause: unveil >= 1 ? 'sky fully unveiled' : 'no audible artificial skyglow at this brightness',
    };
  }
  return { levelDb, silentBecause: null };
}

export function isKnownRegime(regime: ScaleAudioRegime): boolean {
  return Object.hasOwn(REGIME_DISTANCE_WINDOWS, regime);
}

export function sanitizeSkyBrightness(magArcsec2: number | null): number | null {
  return isFiniteNumber(magArcsec2) ? magArcsec2 : null;
}

export function sanitizeUnveil(fraction: number): number {
  return isFiniteNumber(fraction) ? clamp(fraction, 0, 1) : 0;
}

export function sanitizeOrientation(orientation: Quat): Quat {
  return normalizeQuat(orientation) ?? IDENTITY_QUAT;
}
