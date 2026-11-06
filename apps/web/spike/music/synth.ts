/**
 * The synthesized parts of the music directions (AUDIO_ENGINE.md section 13): a detuned-saw pad, a
 * sub bass, a kick, a noise riser, a synthetic hall response, a ping-pong delay and the pad's
 * ducking under the kick. All are composed sound design, not data. Every note opens and closes with
 * a ramp, so none starts or stops with a click.
 */

import { energyNormalize, mulberry32, whiteNoise } from '../../src/audio/dsp';
import { midiToHz } from '../../src/audio/music/harmony';

const dbToGain = (db: number) => 10 ** (db / 20);

export interface PadNote {
  readonly timeSec: number;
  readonly durationSec: number;
  readonly midis: readonly number[];
  readonly gainDb: number;
  readonly attackSec: number;
  readonly releaseSec: number;
  /** Low-pass cutoff at the start and at the end of the note, Hz (an exponential glide). */
  readonly cutoffFromHz: number;
  readonly cutoffToHz: number;
}

let padWave: { ctx: BaseAudioContext; wave: PeriodicWave } | null = null;

/** A saw of 32 harmonics (amplitude 1/n): warm, and with no sharp edge for the low-pass to round off. */
function padSaw(ctx: BaseAudioContext): PeriodicWave {
  if (padWave?.ctx === ctx) return padWave.wave;
  const real = new Float32Array(33);
  const imag = new Float32Array(33);
  for (let n = 1; n <= 32; n++) imag[n] = 1 / n;
  padWave = { ctx, wave: ctx.createPeriodicWave(real, imag) };
  return padWave.wave;
}

/** Three 32-harmonic saws per note, detuned by -8, 0 and +8 cents and spread across the stereo field. */
export function playPad(ctx: BaseAudioContext, destination: AudioNode, note: PadNote): void {
  const t0 = note.timeSec;
  const end = t0 + note.durationSec;
  const filter = ctx.createBiquadFilter();
  filter.type = 'lowpass';
  filter.Q.value = 0.6;
  filter.frequency.setValueAtTime(note.cutoffFromHz, t0);
  filter.frequency.exponentialRampToValueAtTime(note.cutoffToHz, end);
  const env = ctx.createGain();
  // A saw's RMS is 1/sqrt(3) of its peak; 3 saws per note, summed in power
  const peak = dbToGain(note.gainDb) / Math.sqrt(3 * Math.max(1, note.midis.length));
  env.gain.setValueAtTime(0, t0);
  env.gain.linearRampToValueAtTime(peak, t0 + note.attackSec);
  env.gain.setValueAtTime(peak, end);
  env.gain.linearRampToValueAtTime(0, end + note.releaseSec);
  filter.connect(env);
  env.connect(destination);
  note.midis.forEach((midi, i) => {
    [-8, 0, 8].forEach((detune, j) => {
      const osc = ctx.createOscillator();
      osc.setPeriodicWave(padSaw(ctx));
      osc.frequency.value = midiToHz(midi);
      osc.detune.value = detune;
      const pan = ctx.createStereoPanner();
      pan.pan.value = (j - 1) * 0.6 * (i % 2 === 0 ? 1 : -1);
      osc.connect(pan);
      pan.connect(filter);
      osc.start(t0);
      osc.stop(end + note.releaseSec + 0.02);
    });
  });
}

export interface MonoNote {
  readonly timeSec: number;
  readonly durationSec: number;
  readonly midi: number;
  readonly gainDb: number;
}

/** A sine with a little second and third harmonic, so it carries on small speakers. */
export function playSub(ctx: BaseAudioContext, destination: AudioNode, note: MonoNote): void {
  const t0 = note.timeSec;
  const end = t0 + note.durationSec;
  const osc = ctx.createOscillator();
  osc.setPeriodicWave(ctx.createPeriodicWave(new Float32Array([0, 0, 0, 0]), new Float32Array([0, 1, 0.12, 0.04]), { disableNormalization: true }));
  osc.frequency.value = midiToHz(note.midi);
  const env = ctx.createGain();
  const peak = dbToGain(note.gainDb);
  env.gain.setValueAtTime(0, t0);
  env.gain.linearRampToValueAtTime(peak, t0 + 0.02);
  env.gain.setValueAtTime(peak, end);
  env.gain.linearRampToValueAtTime(0, end + 0.25);
  osc.connect(env);
  env.connect(destination);
  osc.start(t0);
  osc.stop(end + 0.27);
}

const KICK_SECONDS = 0.5;
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
    curve[i] = (1 - Math.exp(-t / 0.0012)) * Math.exp(-t / 0.085);
    peak = Math.max(peak, curve[i]!);
  }
  const fade = Math.round(0.02 * sampleRate);
  for (let i = 0; i < n; i++) curve[i] = (curve[i]! / peak) * Math.min(1, (n - 1 - i) / fade);
  kickCurve = { rate: sampleRate, curve };
  return curve;
}

/** A sine whose pitch falls from 150 to 48 Hz over 110 ms, under a smooth 0.5 s envelope. */
export function playKick(ctx: BaseAudioContext, destination: AudioNode, timeSec: number, gainDb: number): void {
  const t0 = timeSec;
  const osc = ctx.createOscillator();
  osc.frequency.setValueAtTime(150, t0);
  osc.frequency.exponentialRampToValueAtTime(48, t0 + 0.11);
  const env = ctx.createGain();
  env.gain.value = 0;
  env.gain.setValueCurveAtTime(kickEnvelope(ctx.sampleRate), t0, KICK_SECONDS);
  const level = ctx.createGain();
  level.gain.value = dbToGain(gainDb);
  osc.connect(env);
  env.connect(level);
  level.connect(destination);
  osc.start(t0);
  osc.stop(t0 + KICK_SECONDS + 0.01);
}

let noiseCache: { ctx: BaseAudioContext; buffer: AudioBuffer } | null = null;

/** Four seconds of seeded white noise, shared by the risers. */
function noiseBuffer(ctx: BaseAudioContext): AudioBuffer {
  if (noiseCache?.ctx === ctx) return noiseCache.buffer;
  const length = Math.round(4 * ctx.sampleRate);
  const buffer = ctx.createBuffer(2, length, ctx.sampleRate);
  buffer.copyToChannel(whiteNoise(length, 0x5eed1, 0.25), 0);
  buffer.copyToChannel(whiteNoise(length, 0x5eed2, 0.25), 1);
  noiseCache = { ctx, buffer };
  return buffer;
}

/** Noise through a band-pass that climbs from 400 Hz to 7 kHz while it swells, then fades in 60 ms. */
export function playRiser(ctx: BaseAudioContext, destination: AudioNode, timeSec: number, durationSec: number, gainDb: number): void {
  const t0 = timeSec;
  const end = t0 + durationSec;
  const source = ctx.createBufferSource();
  source.buffer = noiseBuffer(ctx);
  source.loop = true;
  const band = ctx.createBiquadFilter();
  band.type = 'bandpass';
  band.Q.value = 1.6;
  band.frequency.setValueAtTime(400, t0);
  band.frequency.exponentialRampToValueAtTime(7000, end);
  const env = ctx.createGain();
  env.gain.setValueAtTime(0, t0);
  env.gain.linearRampToValueAtTime(dbToGain(gainDb - 24), t0 + durationSec * 0.5);
  env.gain.linearRampToValueAtTime(dbToGain(gainDb), end);
  env.gain.linearRampToValueAtTime(0, end + 0.06);
  source.connect(band);
  band.connect(env);
  env.connect(destination);
  source.start(t0);
  source.stop(end + 0.08);
}

/**
 * A synthetic hall: two bands of seeded noise, below and above 2 kHz, decaying to -60 dB in 3.4 s
 * and 1.6 s, after 18 ms of pre-delay and a 4 ms fade-in, normalized to unit energy per channel.
 * A design choice for the beats direction; the organ direction uses the recorded chapel instead.
 */
export function syntheticHallImpulse(ctx: BaseAudioContext, seconds = 3.6, seed = 0x4a11): AudioBuffer {
  const sr = ctx.sampleRate;
  const length = Math.round(seconds * sr);
  const predelay = Math.round(0.018 * sr);
  const fade = Math.round(0.004 * sr);
  const channels: Float32Array[] = [];
  for (let c = 0; c < 2; c++) {
    const rand = mulberry32(seed + c);
    const out = new Float32Array(length);
    let low = 0;
    const a = Math.exp((-2 * Math.PI * 2000) / sr);
    for (let i = predelay; i < length; i++) {
      const t = (i - predelay) / sr;
      const white = 2 * rand() - 1;
      low = a * low + (1 - a) * white;
      const high = white - low;
      const value = low * 10 ** ((-3 * t) / 3.4) + high * 10 ** ((-3 * t) / 1.6);
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
  options: { delaySec: number; feedback: number; wetDb: number; lowpassHz: number }
): { input: AudioNode; output: AudioNode } {
  const input = ctx.createGain();
  const output = ctx.createGain();
  output.gain.value = dbToGain(options.wetDb);
  const left = ctx.createDelay(2);
  const right = ctx.createDelay(2);
  left.delayTime.value = options.delaySec;
  right.delayTime.value = options.delaySec;
  const damp = ctx.createBiquadFilter();
  damp.type = 'lowpass';
  damp.frequency.value = options.lowpassHz;
  const feedback = ctx.createGain();
  feedback.gain.value = options.feedback;
  const panL = ctx.createStereoPanner();
  panL.pan.value = -0.8;
  const panR = ctx.createStereoPanner();
  panR.pan.value = 0.8;
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
export function duckUnderKicks(param: AudioParam, kickTimes: readonly number[], depthDb: number, releaseSec: number): void {
  const low = dbToGain(depthDb);
  param.setValueAtTime(1, 0);
  const times = [...kickTimes].sort((a, b) => a - b);
  times.forEach((t, i) => {
    const next = times[i + 1] ?? Number.POSITIVE_INFINITY;
    const back = Math.min(t + releaseSec, next - 0.04);
    param.linearRampToValueAtTime(1, Math.max(0, t - 0.005));
    param.linearRampToValueAtTime(low, t + 0.03);
    param.linearRampToValueAtTime(1, Math.max(t + 0.06, back));
  });
}
