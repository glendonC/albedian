/**
 * Synthesized kick percussion and filtered white-noise riser for the music directions
 * (AUDIO_ENGINE.md section 13).
 */

import { whiteNoise } from '../../dsp.js';
import { dbToGain } from './types.js';

const KICK_SECONDS = 0.5;
const KICK_ATTACK_TAU_SEC = 0.0012;
const KICK_DECAY_TAU_SEC = 0.085;
const KICK_TAIL_FADE_SEC = 0.02;
const KICK_START_HZ = 150;
const KICK_END_HZ = 48;
const KICK_PITCH_DROP_SEC = 0.11;
const KICK_STOP_MARGIN_SEC = 0.01;

const RISER_NOISE_SECONDS = 4;
const RISER_LEFT_NOISE_SEED = 0x5eed1;
const RISER_RIGHT_NOISE_SEED = 0x5eed2;
const RISER_NOISE_RMS = 0.25;
const RISER_BANDPASS_Q = 1.6;
const RISER_START_HZ = 400;
const RISER_END_HZ = 7000;
const RISER_MIDPOINT_OFFSET_DB = 24;
const RISER_FADE_OUT_SEC = 0.06;
const RISER_STOP_OFFSET_SEC = 0.08;

let kickCurve: { rate: number; curve: Float32Array } | null = null;

/**
 * The kick's envelope, (1 - e^(-t / 1.2 ms)) e^(-t / 85 ms), scaled to a peak of 1 and faded to 0 over
 * its last 20 ms: smooth at every point, so the kick has no corner a click detector or an ear could
 * catch (a linear rise that turns into a decay at once leaves one).
 */
function kickEnvelope(sampleRate: number): Float32Array {
  if (kickCurve?.rate === sampleRate) return kickCurve.curve;
  const n = Math.round(KICK_SECONDS * sampleRate);
  const curve = new Float32Array(n);
  let peak = 0;
  for (let i = 0; i < n; i++) {
    const t = i / sampleRate;
    curve[i] = (1 - Math.exp(-t / KICK_ATTACK_TAU_SEC)) * Math.exp(-t / KICK_DECAY_TAU_SEC);
    peak = Math.max(peak, curve[i]!);
  }
  const fade = Math.round(KICK_TAIL_FADE_SEC * sampleRate);
  for (let i = 0; i < n; i++) curve[i] = (curve[i]! / peak) * Math.min(1, (n - 1 - i) / fade);
  kickCurve = { rate: sampleRate, curve };
  return curve;
}

/** A sine whose pitch falls from 150 to 48 Hz over 110 ms, under a smooth 0.5 s envelope. */
export function playKick(ctx: BaseAudioContext, destination: AudioNode, timeSec: number, gainDb: number): void {
  const t0 = timeSec;
  const osc = ctx.createOscillator();
  osc.frequency.setValueAtTime(KICK_START_HZ, t0);
  osc.frequency.exponentialRampToValueAtTime(KICK_END_HZ, t0 + KICK_PITCH_DROP_SEC);
  const env = ctx.createGain();
  env.gain.value = 0;
  env.gain.setValueCurveAtTime(kickEnvelope(ctx.sampleRate), t0, KICK_SECONDS);
  const level = ctx.createGain();
  level.gain.value = dbToGain(gainDb);
  osc.connect(env);
  env.connect(level);
  level.connect(destination);
  osc.start(t0);
  osc.stop(t0 + KICK_SECONDS + KICK_STOP_MARGIN_SEC);
}

let noiseCache: { ctx: BaseAudioContext; buffer: AudioBuffer } | null = null;

/** Four seconds of seeded white noise, shared by the risers. */
function noiseBuffer(ctx: BaseAudioContext): AudioBuffer {
  if (noiseCache?.ctx === ctx) return noiseCache.buffer;
  const length = Math.round(RISER_NOISE_SECONDS * ctx.sampleRate);
  const buffer = ctx.createBuffer(2, length, ctx.sampleRate);
  buffer.copyToChannel(whiteNoise(length, RISER_LEFT_NOISE_SEED, RISER_NOISE_RMS), 0);
  buffer.copyToChannel(whiteNoise(length, RISER_RIGHT_NOISE_SEED, RISER_NOISE_RMS), 1);
  noiseCache = { ctx, buffer };
  return buffer;
}

/** Noise through a band-pass that climbs from 400 Hz to 7 kHz while it swells, then fades in 60 ms. */
export function playRiser(
  ctx: BaseAudioContext,
  destination: AudioNode,
  timeSec: number,
  durationSec: number,
  gainDb: number
): void {
  const t0 = timeSec;
  const end = t0 + durationSec;
  const source = ctx.createBufferSource();
  source.buffer = noiseBuffer(ctx);
  source.loop = true;
  const band = ctx.createBiquadFilter();
  band.type = 'bandpass';
  band.Q.value = RISER_BANDPASS_Q;
  band.frequency.setValueAtTime(RISER_START_HZ, t0);
  band.frequency.exponentialRampToValueAtTime(RISER_END_HZ, end);
  const env = ctx.createGain();
  env.gain.setValueAtTime(0, t0);
  env.gain.linearRampToValueAtTime(dbToGain(gainDb - RISER_MIDPOINT_OFFSET_DB), t0 + durationSec * 0.5);
  env.gain.linearRampToValueAtTime(dbToGain(gainDb), end);
  env.gain.linearRampToValueAtTime(0, end + RISER_FADE_OUT_SEC);
  source.connect(band);
  band.connect(env);
  env.connect(destination);
  source.start(t0);
  source.stop(end + RISER_STOP_OFFSET_SEC);
}
