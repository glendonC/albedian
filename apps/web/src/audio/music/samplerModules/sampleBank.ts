/**
 * Sample bank loader, multi-channel mono mixdown for tuning calibration, and per-instrument
 * round-robin counters (AUDIO_ENGINE.md section 13).
 */

import { midiToHz } from '../harmony.js';
import { measureTuningCents } from './tuningAnalysis.js';
import type { SampleTuningReport, SampleZone } from './types.js';

function encodeSampleUrl(baseUrl: string, file: string): string {
  return baseUrl + file.split('/').map(encodeURIComponent).join('/');
}

function formatErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function mixDown(buffer: AudioBuffer): Float32Array {
  const out = new Float32Array(buffer.length);
  for (let c = 0; c < buffer.numberOfChannels; c++) {
    const data = buffer.getChannelData(c);
    for (let i = 0; i < out.length; i++) out[i] = out[i]! + data[i]! / buffer.numberOfChannels;
  }
  return out;
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
        const url = encodeSampleUrl(this.baseUrl, z.file);
        // Called as a plain function: a browser's fetch refuses to run as another object's method
        const fetchImpl = this.fetchImpl;
        const response = await fetchImpl(url);
        if (!response.ok) throw new Error(`sample ${z.file} unavailable (${response.status})`);
        const buffer = await this.ctx.decodeAudioData(await response.arrayBuffer()).catch((error: unknown) => {
          throw new Error(`sample ${z.file} could not be decoded: ${formatErrorMessage(error)}`);
        });
        this.buffers.set(z.file, buffer);
        if (z.rootMidi !== null) {
          this.tuning.set(z.file, measureTuningCents(mixDown(buffer), buffer.sampleRate, midiToHz(z.rootMidi)));
        }
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
  report(): SampleTuningReport[] {
    return [...this.buffers.entries()].map(([file, b]) => ({
      file,
      tuningCents: this.tuning.get(file) ?? null,
      seconds: b.duration,
    }));
  }

  nextCount(instrumentId: string): number {
    const n = this.counters.get(instrumentId) ?? 0;
    this.counters.set(instrumentId, n + 1);
    return n;
  }
}
