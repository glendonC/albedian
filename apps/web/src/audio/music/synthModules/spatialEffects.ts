/**
 * Spatial and bus-shaping effects for the music directions (AUDIO_ENGINE.md section 13): a
 * two-band synthetic hall impulse response, a low-pass ping-pong stereo delay, and ramp-only bus
 * ducking under kicks.
 */

import { energyNormalize, mulberry32 } from '../../dsp.js';
import { dbToGain, type PingPongDelayOptions } from './types.js';

const DEFAULT_HALL_SECONDS = 3.6;
const DEFAULT_HALL_SEED = 0x4a11;
const HALL_PREDELAY_SEC = 0.018;
const HALL_FADE_IN_SEC = 0.004;
const HALL_CROSSOVER_HZ = 2000;
const HALL_LOW_RT60_SEC = 3.4;
const HALL_HIGH_RT60_SEC = 1.6;

const MAX_DELAY_SEC = 2;
const DELAY_STEREO_PAN = 0.8;

const DUCK_LEAD_IN_SEC = 0.005;
const DUCK_ATTACK_SEC = 0.03;
const DUCK_MIN_RECOVERY_SEC = 0.06;
const DUCK_NEXT_KICK_GUARD_SEC = 0.04;

/**
 * A synthetic hall: two bands of seeded noise, below and above 2 kHz, decaying to -60 dB in 3.4 s
 * and 1.6 s, after 18 ms of pre-delay and a 4 ms fade-in, normalized to unit energy per channel.
 * A design choice for the beats direction; the organ direction uses the recorded chapel instead.
 */
export function syntheticHallImpulse(
  ctx: BaseAudioContext,
  seconds = DEFAULT_HALL_SECONDS,
  seed = DEFAULT_HALL_SEED
): AudioBuffer {
  const sr = ctx.sampleRate;
  const length = Math.round(seconds * sr);
  const predelay = Math.round(HALL_PREDELAY_SEC * sr);
  const fade = Math.round(HALL_FADE_IN_SEC * sr);
  const channels: Float32Array[] = [];
  for (let c = 0; c < 2; c++) {
    const rand = mulberry32(seed + c);
    const out = new Float32Array(length);
    let low = 0;
    const a = Math.exp((-2 * Math.PI * HALL_CROSSOVER_HZ) / sr);
    for (let i = predelay; i < length; i++) {
      const t = (i - predelay) / sr;
      const white = 2 * rand() - 1;
      low = a * low + (1 - a) * white;
      const high = white - low;
      const value = low * 10 ** ((-3 * t) / HALL_LOW_RT60_SEC) + high * 10 ** ((-3 * t) / HALL_HIGH_RT60_SEC);
      out[i] = value * Math.min(1, (i - predelay) / fade);
    }
    channels.push(out);
  }
  const normalized = energyNormalize(channels);
  const buffer = ctx.createBuffer(2, length, sr);
  normalized.forEach((data, c) => buffer.copyToChannel(data, c));
  return buffer;
}

/** A ping-pong delay: left, then right, then left again, each pass darker through a low-pass. */
export function pingPongDelay(
  ctx: BaseAudioContext,
  options: PingPongDelayOptions
): { input: AudioNode; output: AudioNode } {
  const input = ctx.createGain();
  const output = ctx.createGain();
  output.gain.value = dbToGain(options.wetDb);
  const left = ctx.createDelay(MAX_DELAY_SEC);
  const right = ctx.createDelay(MAX_DELAY_SEC);
  left.delayTime.value = options.delaySec;
  right.delayTime.value = options.delaySec;
  const damp = ctx.createBiquadFilter();
  damp.type = 'lowpass';
  damp.frequency.value = options.lowpassHz;
  const feedback = ctx.createGain();
  feedback.gain.value = options.feedback;
  const panL = ctx.createStereoPanner();
  panL.pan.value = -DELAY_STEREO_PAN;
  const panR = ctx.createStereoPanner();
  panR.pan.value = DELAY_STEREO_PAN;
  input.connect(left);
  left.connect(damp);
  damp.connect(right);
  right.connect(feedback);
  feedback.connect(left);
  left.connect(panL);
  right.connect(panR);
  panL.connect(output);
  panR.connect(output);
  return { input, output };
}

/** Dips a bus's gain by `depthDb` over 30 ms at each kick and brings it back, with ramps only (no jumps). */
export function duckUnderKicks(
  param: AudioParam,
  kickTimes: readonly number[],
  depthDb: number,
  releaseSec: number
): void {
  const low = dbToGain(depthDb);
  param.setValueAtTime(1, 0);
  const times = [...kickTimes].sort((a, b) => a - b);
  times.forEach((t, i) => {
    const next = times[i + 1] ?? Number.POSITIVE_INFINITY;
    const back = Math.min(t + releaseSec, next - DUCK_NEXT_KICK_GUARD_SEC);
    param.linearRampToValueAtTime(1, Math.max(0, t - DUCK_LEAD_IN_SEC));
    param.linearRampToValueAtTime(low, t + DUCK_ATTACK_SEC);
    param.linearRampToValueAtTime(1, Math.max(t + DUCK_MIN_RECOVERY_SEC, back));
  });
}
