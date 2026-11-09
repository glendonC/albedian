/**
 * Plays recorded instrument samples (VCSL, CC0 1.0) for the music directions (AUDIO_ENGINE.md
 * section 13): the nearest recorded note shifted to the wanted pitch, the nearest velocity layer,
 * round robins in turn, and every note opened and closed with a ramp so none starts or stops with a
 * click. Each pitched sample's tuning is measured once when it loads, and the playback rate corrects
 * it, so a sample recorded a few cents sharp still sounds at its nominal pitch.
 *
 * The zone choice, the playback rate and the tuning measure are pure; the bank and `playSample`
 * need a Web Audio context.
 */

import { fft } from '../dsp';
import { midiToHz, parseNote } from './harmony';

/** One recorded note: its file under the sources folder and the pitch it sounds at. */
export interface SampleZone {
  readonly file: string;
  /** Sounding pitch, MIDI; null for an unpitched sample (a clave, a cymbal). */
  readonly rootMidi: number | null;
  /** Velocity layer, 0 the softest. */
  readonly layer: number;
  readonly roundRobin: number;
}

/**
 * VCSL's note names put middle C at C3: NT5_Man3Quiet_C3 sounds at 261.6 Hz and sus_C4 at 522.7 Hz
 * (measured by a harmonic-sum pitch estimate), so a name's pitch is one octave above the
 * scientific reading.
 */
export function vcslRootMidi(name: string): number {
  return parseNote(name) + 12;
}

/** The zone for a note: the nearest root (a tie takes the lower root), the nearest layer, then round robin. */
export function chooseZone(
  zones: readonly SampleZone[],
  midi: number,
  layer: number,
  counter: number
): { zone: SampleZone; semitones: number } {
  if (zones.length === 0) throw new Error('an instrument needs at least one sample');
  let candidates = [...zones];
  if (zones.some((z) => z.rootMidi !== null)) {
    const pitched = zones.filter((z) => z.rootMidi !== null);
    const distance = (z: SampleZone) => Math.abs(z.rootMidi! - midi);
    const nearest = Math.min(...pitched.map(distance));
    const lowestNearest = Math.min(...pitched.filter((z) => distance(z) === nearest).map((z) => z.rootMidi!));
    candidates = pitched.filter((z) => z.rootMidi === lowestNearest);
  }
  const layerDistance = (z: SampleZone) => Math.abs(z.layer - layer);
  const nearestLayer = Math.min(...candidates.map(layerDistance));
  const chosenLayer = Math.min(...candidates.filter((z) => layerDistance(z) === nearestLayer).map((z) => z.layer));
  const pool = candidates.filter((z) => z.layer === chosenLayer).sort((a, b) => a.roundRobin - b.roundRobin);
  const zone = pool[((Math.floor(counter) % pool.length) + pool.length) % pool.length]!;
  return { zone, semitones: zone.rootMidi === null ? 0 : midi - zone.rootMidi };
}

/** Playback rate for a shift in semitones, correcting a tuning offset measured in cents. */
export function playbackRate(semitones: number, centsOffset: number): number {
  return 2 ** (semitones / 12 - centsOffset / 1200);
}

const TUNING_SEARCH_CENTS = 50;

/**
 * How many cents a recorded note lies from its nominal pitch: a Hann-windowed spectrum of up to 1 s
 * from 0.3 s after the onset, the peak near each of the first four harmonics of the nominal pitch
 * (within 50 cents), refined by parabolic interpolation on log magnitude, averaged with the peaks'
 * magnitudes as weights over the harmonics at least a quarter as strong as the strongest. Null when
 * the sample is silent or holds no such peak.
 */
export function measureTuningCents(samples: ArrayLike<number>, sampleRate: number, nominalHz: number): number | null {
  let peak = 0;
  for (let i = 0; i < samples.length; i++) peak = Math.max(peak, Math.abs(samples[i] ?? 0));
  if (!(peak > 1e-6)) return null;
  let onset = 0;
  while (onset < samples.length && Math.abs(samples[onset] ?? 0) < 0.1 * peak) onset++;
  let start = onset + Math.round(0.3 * sampleRate);
  let length = Math.min(Math.round(sampleRate), samples.length - start);
  if (length < 0.25 * sampleRate) {
    start = onset;
    length = Math.min(Math.round(sampleRate), samples.length - start);
  }
  if (length < 1024) return null;
  let n = 1;
  while (n < 2 * length) n <<= 1;
  const re = new Float64Array(n);
  const im = new Float64Array(n);
  for (let i = 0; i < length; i++) re[i] = (samples[start + i] ?? 0) * (0.5 - 0.5 * Math.cos((2 * Math.PI * i) / length));
  fft(re, im);
  const binHz = sampleRate / n;
  const mag = (k: number) => Math.hypot(re[k] ?? 0, im[k] ?? 0);
  const found: { cents: number; weight: number }[] = [];
  for (let h = 1; h <= 4; h++) {
    const target = h * nominalHz;
    const lo = Math.max(1, Math.floor((target * 2 ** (-TUNING_SEARCH_CENTS / 1200)) / binHz));
    const hi = Math.min(n / 2 - 2, Math.ceil((target * 2 ** (TUNING_SEARCH_CENTS / 1200)) / binHz));
    if (hi <= lo) continue;
    let best = lo;
    for (let k = lo; k <= hi; k++) if (mag(k) > mag(best)) best = k;
    if (best === lo || best === hi) continue;
    const a = Math.log(mag(best - 1) + 1e-30);
    const b = Math.log(mag(best) + 1e-30);
    const c = Math.log(mag(best + 1) + 1e-30);
    const offset = (0.5 * (a - c)) / (a - 2 * b + c);
    const hz = (best + (Number.isFinite(offset) ? offset : 0)) * binHz;
    found.push({ cents: 1200 * Math.log2(hz / target), weight: mag(best) });
  }
  const strongest = Math.max(0, ...found.map((f) => f.weight));
  if (!(strongest > 0)) return null;
  const used = found.filter((f) => f.weight >= 0.25 * strongest);
  const total = used.reduce((s, f) => s + f.weight, 0);
  return used.reduce((s, f) => s + f.cents * f.weight, 0) / total;
}

/** A loaded instrument: its zones, envelope and trim. */
export interface Instrument {
  readonly id: string;
  readonly zones: readonly SampleZone[];
  readonly attackSec: number;
  readonly releaseSec: number;
  /** Trim applied to every note, dB. */
  readonly gainDb: number;
}

/** Decoded samples by file, with each pitched sample's measured tuning. */
export class SampleBank {
  private readonly buffers = new Map<string, AudioBuffer>();
  private readonly tuning = new Map<string, number | null>();
  private readonly counters = new Map<string, number>();

  constructor(
    private readonly ctx: BaseAudioContext,
    /** URL prefix of the sources folder, ending in '/'. */
    private readonly baseUrl: string,
    private readonly fetchImpl: typeof fetch = fetch
  ) {}

  async load(zones: readonly SampleZone[]): Promise<void> {
    await Promise.all(
      zones.map(async (z) => {
        if (this.buffers.has(z.file)) return;
        const url = this.baseUrl + z.file.split('/').map(encodeURIComponent).join('/');
        // Called as a plain function: a browser's fetch refuses to run as another object's method
        const fetchImpl = this.fetchImpl;
        const response = await fetchImpl(url);
        if (!response.ok) throw new Error(`sample ${z.file} unavailable (${response.status})`);
        const buffer = await this.ctx.decodeAudioData(await response.arrayBuffer()).catch((error: unknown) => {
          throw new Error(`sample ${z.file} could not be decoded: ${error instanceof Error ? error.message : String(error)}`);
        });
        this.buffers.set(z.file, buffer);
        if (z.rootMidi !== null) this.tuning.set(z.file, measureTuningCents(mixDown(buffer), buffer.sampleRate, midiToHz(z.rootMidi)));
      })
    );
  }

  buffer(file: string): AudioBuffer {
    const b = this.buffers.get(file);
    if (!b) throw new Error(`sample ${file} was not loaded`);
    return b;
  }

  /** Measured offset in cents; null when not pitched or not measurable (then no correction). */
  tuningCents(file: string): number | null {
    return this.tuning.get(file) ?? null;
  }

  /** Every loaded file with its measured tuning, for the page's facts. */
  report(): { file: string; tuningCents: number | null; seconds: number }[] {
    return [...this.buffers.entries()].map(([file, b]) => ({ file, tuningCents: this.tuning.get(file) ?? null, seconds: b.duration }));
  }

  nextCount(instrumentId: string): number {
    const n = this.counters.get(instrumentId) ?? 0;
    this.counters.set(instrumentId, n + 1);
    return n;
  }
}

function mixDown(buffer: AudioBuffer): Float32Array {
  const out = new Float32Array(buffer.length);
  for (let c = 0; c < buffer.numberOfChannels; c++) {
    const data = buffer.getChannelData(c);
    for (let i = 0; i < out.length; i++) out[i] = out[i]! + data[i]! / buffer.numberOfChannels;
  }
  return out;
}

export interface SampleNote {
  readonly timeSec: number;
  /** Sounding pitch, MIDI (ignored for unpitched instruments). */
  readonly midi: number;
  /** How long the key is held; the release follows. */
  readonly durationSec: number;
  readonly gainDb: number;
  /** -1 left to 1 right. */
  readonly pan: number;
  readonly layer: number;
}

const dbToGain = (db: number) => 10 ** (db / 20);

/**
 * Schedules one note: a fresh source, a gain that ramps up from 0 over the attack and back to 0
 * over the release, and a stereo panner. The source never runs past the end of its sample.
 */
export function playSample(
  ctx: BaseAudioContext,
  bank: SampleBank,
  instrument: Instrument,
  destination: AudioNode,
  note: SampleNote
): { file: string; rate: number } {
  const { zone, semitones } = chooseZone(instrument.zones, note.midi, note.layer, bank.nextCount(instrument.id));
  const buffer = bank.buffer(zone.file);
  const rate = playbackRate(semitones, bank.tuningCents(zone.file) ?? 0);
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.playbackRate.value = rate;
  const env = ctx.createGain();
  const panner = ctx.createStereoPanner();
  panner.pan.value = Math.max(-1, Math.min(1, note.pan));
  const peak = dbToGain(note.gainDb + instrument.gainDb);
  const t0 = Math.max(0, note.timeSec);
  // The sample's own length at this rate bounds the note, release included
  const available = buffer.duration / rate;
  const release = Math.min(instrument.releaseSec, available / 2);
  const hold = Math.max(instrument.attackSec, Math.min(note.durationSec, available - release));
  env.gain.setValueAtTime(0, t0);
  env.gain.linearRampToValueAtTime(peak, t0 + instrument.attackSec);
  env.gain.setValueAtTime(peak, t0 + hold);
  env.gain.linearRampToValueAtTime(0, t0 + hold + release);
  source.connect(env);
  env.connect(panner);
  panner.connect(destination);
  source.start(t0);
  source.stop(t0 + hold + release + 0.01);
  return { file: zone.file, rate };
}
