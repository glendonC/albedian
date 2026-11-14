/**
 * Shared note and delay option shapes plus dB-to-linear amplitude conversion for the music
 * synthesizer modules (AUDIO_ENGINE.md section 13).
 */

export const dbToGain = (db: number): number => 10 ** (db / 20);

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

export interface MonoNote {
  readonly timeSec: number;
  readonly durationSec: number;
  readonly midi: number;
  readonly gainDb: number;
}

export interface PingPongDelayOptions {
  readonly delaySec: number;
  readonly feedback: number;
  readonly wetDb: number;
  readonly lowpassHz: number;
}
