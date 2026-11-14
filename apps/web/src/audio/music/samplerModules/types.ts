/**
 * Domain types for recorded instrument sample zones, instruments, scheduled notes, and bank
 * diagnostics (AUDIO_ENGINE.md section 13).
 */

/** One recorded note: its file under the sources folder and the pitch it sounds at. */
export interface SampleZone {
  readonly file: string;
  /** Sounding pitch, MIDI; null for an unpitched sample (a clave, a cymbal). */
  readonly rootMidi: number | null;
  /** Velocity layer, 0 the softest. */
  readonly layer: number;
  readonly roundRobin: number;
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

/** Every loaded file in a SampleBank with its measured tuning and decoded duration. */
export interface SampleTuningReport {
  file: string;
  tuningCents: number | null;
  seconds: number;
}
