import { periodicWaveCoefficients, schroederPhases } from '../dsp.js';
import { clamp, isFiniteNumber, planckHarmonicAmplitudes } from '../mappings.js';

export const DEFAULT_DETENT_PITCH_HZ = 920;

const DETENT_GAIN = 0.11;
const DETENT_GAIN_FLOOR = 0.0001;
const DETENT_PITCH_END_FACTOR = 0.35;
const DETENT_PITCH_RAMP_SEC = 0.024;
const DETENT_GAIN_RAMP_SEC = 0.026;
const DETENT_STOP_SEC = 0.028;

const LOCK_TONE_BASE_HZ = 220;
const LOCK_TONE_UPPER_HZ = LOCK_TONE_BASE_HZ * 1.5;
const LOCK_TONE_HARMONICS = 32;
const LOCK_TONE_PAN_LIMIT = 0.9;
const LOCK_TONE_GAIN_FLOOR = 0.0001;
const LOCK_TONE_PEAK_GAIN = 0.09;
const LOCK_TONE_ATTACK_SEC = 0.03;
const LOCK_TONE_RELEASE_SEC = 0.65;
const LOCK_TONE_STOP_SEC = 0.68;

/** Short UI detent click (interface feedback only, carries no astronomical data). */
export function playDetentCue(
  ctx: BaseAudioContext,
  destination: AudioNode,
  pitchHz = DEFAULT_DETENT_PITCH_HZ
): void {
  if (!isFiniteNumber(pitchHz) || pitchHz <= 0) return;
  const now = ctx.currentTime;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = 'sine';
  osc.frequency.setValueAtTime(pitchHz, now);
  osc.frequency.exponentialRampToValueAtTime(pitchHz * DETENT_PITCH_END_FACTOR, now + DETENT_PITCH_RAMP_SEC);
  gain.gain.setValueAtTime(DETENT_GAIN, now);
  gain.gain.exponentialRampToValueAtTime(DETENT_GAIN_FLOOR, now + DETENT_GAIN_RAMP_SEC);
  osc.connect(gain);
  gain.connect(destination);
  osc.start(now);
  osc.stop(now + DETENT_STOP_SEC);
}

/**
 * One-shot lock tone: a clean just fifth whose timbre follows `teffK`, stereo-panned by `panX`.
 * Silent when either input is missing or invalid.
 */
export function playLockToneCue(
  ctx: BaseAudioContext,
  destination: AudioNode,
  teffK: number | null,
  panX: number | null
): void {
  if (!isFiniteNumber(panX)) return;
  const lowerAmplitudes = planckHarmonicAmplitudes(teffK, LOCK_TONE_BASE_HZ, LOCK_TONE_HARMONICS);
  const upperAmplitudes = planckHarmonicAmplitudes(teffK, LOCK_TONE_UPPER_HZ, LOCK_TONE_HARMONICS);
  if (lowerAmplitudes === null || upperAmplitudes === null) return;

  const tones = [
    { hz: LOCK_TONE_BASE_HZ, amplitudes: lowerAmplitudes },
    { hz: LOCK_TONE_UPPER_HZ, amplitudes: upperAmplitudes },
  ] as const;

  const now = ctx.currentTime;
  const panner = ctx.createStereoPanner();
  panner.pan.setValueAtTime(clamp(panX, -LOCK_TONE_PAN_LIMIT, LOCK_TONE_PAN_LIMIT), now);
  const env = ctx.createGain();
  env.gain.setValueAtTime(LOCK_TONE_GAIN_FLOOR, now);
  env.gain.linearRampToValueAtTime(LOCK_TONE_PEAK_GAIN, now + LOCK_TONE_ATTACK_SEC);
  env.gain.exponentialRampToValueAtTime(LOCK_TONE_GAIN_FLOOR, now + LOCK_TONE_RELEASE_SEC);
  env.connect(panner);
  panner.connect(destination);
  for (const tone of tones) {
    const { real, imag } = periodicWaveCoefficients(
      tone.amplitudes,
      schroederPhases(tone.amplitudes.map((a) => a * a))
    );
    const osc = ctx.createOscillator();
    osc.setPeriodicWave(ctx.createPeriodicWave(real, imag, { disableNormalization: true }));
    osc.frequency.setValueAtTime(tone.hz, now);
    osc.connect(env);
    osc.start(now);
    osc.stop(now + LOCK_TONE_STOP_SEC);
  }
}
