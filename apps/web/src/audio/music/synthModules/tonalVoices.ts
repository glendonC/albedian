/**
 * Tonal synthesized voices for the music directions (AUDIO_ENGINE.md section 13): a warm
 * 32-harmonic detuned-saw pad and a sub bass with gentle second and third harmonics.
 */

import { midiToHz } from '../harmony.js';
import { dbToGain, type MonoNote, type PadNote } from './types.js';

const PAD_HARMONIC_COUNT = 32;
const PAD_DETUNE_CENTS = [-8, 0, 8] as const;
const PAD_STEREO_SPREAD = 0.6;
const PAD_FILTER_Q = 0.6;
const PAD_STOP_MARGIN_SEC = 0.02;

const SUB_HARMONIC_REAL = [0, 0, 0, 0] as const;
const SUB_HARMONIC_IMAG = [0, 1, 0.12, 0.04] as const;
const SUB_ATTACK_SEC = 0.02;
const SUB_RELEASE_SEC = 0.25;
const SUB_STOP_OFFSET_SEC = 0.27;

let padWave: { ctx: BaseAudioContext; wave: PeriodicWave } | null = null;

/** A saw of 32 harmonics (amplitude 1/n): warm, and with no sharp edge for the low-pass to round off. */
function padSaw(ctx: BaseAudioContext): PeriodicWave {
  if (padWave?.ctx === ctx) return padWave.wave;
  const real = new Float32Array(PAD_HARMONIC_COUNT + 1);
  const imag = new Float32Array(PAD_HARMONIC_COUNT + 1);
  for (let n = 1; n <= PAD_HARMONIC_COUNT; n++) imag[n] = 1 / n;
  padWave = { ctx, wave: ctx.createPeriodicWave(real, imag) };
  return padWave.wave;
}

/** Schedules a click-free linear attack-sustain-release gain trapezoid from 0 to `peak` and back to 0. */
function scheduleLinearGateEnvelope(
  gain: AudioParam,
  t0: number,
  end: number,
  peak: number,
  attackSec: number,
  releaseSec: number
): void {
  gain.setValueAtTime(0, t0);
  gain.linearRampToValueAtTime(peak, t0 + attackSec);
  gain.setValueAtTime(peak, end);
  gain.linearRampToValueAtTime(0, end + releaseSec);
}

/** Three 32-harmonic saws per note, detuned by -8, 0 and +8 cents and spread across the stereo field. */
export function playPad(ctx: BaseAudioContext, destination: AudioNode, note: PadNote): void {
  const t0 = note.timeSec;
  const end = t0 + note.durationSec;
  const filter = ctx.createBiquadFilter();
  filter.type = 'lowpass';
  filter.Q.value = PAD_FILTER_Q;
  filter.frequency.setValueAtTime(note.cutoffFromHz, t0);
  filter.frequency.exponentialRampToValueAtTime(note.cutoffToHz, end);
  const env = ctx.createGain();
  // A saw's RMS is 1/sqrt(3) of its peak; 3 saws per note, summed in power
  const peak = dbToGain(note.gainDb) / Math.sqrt(PAD_DETUNE_CENTS.length * Math.max(1, note.midis.length));
  scheduleLinearGateEnvelope(env.gain, t0, end, peak, note.attackSec, note.releaseSec);
  filter.connect(env);
  env.connect(destination);
  note.midis.forEach((midi, i) => {
    PAD_DETUNE_CENTS.forEach((detune, j) => {
      const osc = ctx.createOscillator();
      osc.setPeriodicWave(padSaw(ctx));
      osc.frequency.value = midiToHz(midi);
      osc.detune.value = detune;
      const pan = ctx.createStereoPanner();
      pan.pan.value = (j - 1) * PAD_STEREO_SPREAD * (i % 2 === 0 ? 1 : -1);
      osc.connect(pan);
      pan.connect(filter);
      osc.start(t0);
      osc.stop(end + note.releaseSec + PAD_STOP_MARGIN_SEC);
    });
  });
}

/** A sine with a little second and third harmonic, so it carries on small speakers. */
export function playSub(ctx: BaseAudioContext, destination: AudioNode, note: MonoNote): void {
  const t0 = note.timeSec;
  const end = t0 + note.durationSec;
  const osc = ctx.createOscillator();
  osc.setPeriodicWave(
    ctx.createPeriodicWave(new Float32Array(SUB_HARMONIC_REAL), new Float32Array(SUB_HARMONIC_IMAG), {
      disableNormalization: true,
    })
  );
  osc.frequency.value = midiToHz(note.midi);
  const env = ctx.createGain();
  const peak = dbToGain(note.gainDb);
  scheduleLinearGateEnvelope(env.gain, t0, end, peak, SUB_ATTACK_SEC, SUB_RELEASE_SEC);
  osc.connect(env);
  env.connect(destination);
  osc.start(t0);
  osc.stop(end + SUB_STOP_OFFSET_SEC);
}
