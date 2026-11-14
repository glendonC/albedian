/**
 * Single-source voice parameter planning and regime distance-window selection.
 *
 * A voice is silent (never planned) when any input its core sound depends on is missing.
 * Optional layers (dust muffling, uncertainty beating) are present only when their own input
 * is present.
 */

import {
  DUST,
  REGIME_DISTANCE_WINDOWS,
  STAR_DISTANCE_WINDOW,
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
  type ScaleAudioRegime,
} from '../mappings.js';
import type {
  AudioSourceInput,
  AudioSourceKind,
  PlanContext,
  PlanOutcome,
  VoicePlan,
} from './planningTypes.js';
import { worldToListenerDirection } from './spatialOrientation.js';

const NEUTRAL_DRR_DB = 0;

/** The window a voice's distance cues sweep: stars take the stellar window in every regime, other kinds their regime's. */
export function distanceWindowFor(kind: AudioSourceKind, regime: ScaleAudioRegime): DistanceWindow {
  return kind === 'star' ? STAR_DISTANCE_WINDOW : REGIME_DISTANCE_WINDOWS[regime];
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

  const drrDb = directToReverberantDb(perceptual) ?? NEUTRAL_DRR_DB;
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
      distanceLowpassHz: distanceLowpassHz(perceptual) ?? DUST.openHz,
      dustLowpassHz: dustLowpassHz(input.extinctionAvMag),
      directionListener,
      star,
      pulsar,
      uncertainty: beatHz === null ? null : { beatHz },
    },
  };
}
