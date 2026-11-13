/**
 * The note a selected finding plays inside the soundtrack (AUDIO_ENGINE.md 13.7): music first, so it
 * sits in the playing piece's key, on its beat grid, quietly under its data part.
 *
 * - When: on the pass's grid exactly, the first beat no earlier than now plus the scheduling lead (a beat
 *   at most BEAT_SNAP_SEC before that counts), the beat that ends the last bar included; once that beat
 *   is past (a silence between pieces) at once, still in that pass's key.
 * - Pitch: a tone of that bar's pitch set in the data part's register. With a catalog temperature on a
 *   catalog-matched finding, the tone the star-note mapping gives that temperature, as the stars' own
 *   notes do; otherwise the set's root.
 * - Timbre: the piece's data instrument. Only a catalog-matched finding with a catalog temperature is
 *   coloured by it: a low-pass whose cutoff rises with the temperature on the same log scale (a stated
 *   design choice, cooler stars darker). Anything else plays the plain instrument: no colour is made up.
 * - Level: SELECTION_UNDER_DATA_DB under the pass's loudest data note.
 */

import { barPitchSet, type Arrangement, type InstrumentId } from './directions';
import { chordTonesInRange } from './harmony';
import { temperaturePosition } from './starNotes';

export interface MusicSelection {
  /** The finding's id; a new id is a new selection. */
  readonly id: string;
  readonly evidenceClass: string;
  /** The catalog temperature in kelvin (Gaia DR3 GSP-Phot, from the finding's catalog reference); null when it has none. */
  readonly temperatureK: number | null;
  /** -1 (left) to 1 (right), from the finding's source detection on the photo; null when it has none. */
  readonly panX: number | null;
}

export interface SelectionNote {
  readonly timeSec: number;
  readonly midi: number;
  readonly gainDb: number;
  readonly pan: number;
  readonly instrument: InstrumentId;
  readonly layer: number;
  /** The low-pass cutoff that colours the note by temperature, Hz; null for the plain instrument. */
  readonly brightnessHz: number | null;
}

/**
 * A beat at most this many seconds before the earliest time still counts as late enough: it takes in the
 * rounding of the clock's sums, and a note a billionth of a second early is still a lead ahead of now.
 * The one margin on the note's time and on the bar it reads as.
 */
export const BEAT_SNAP_SEC = 1e-9;
/** How far under the pass's loudest data note the selection plays ([proposed], a listening choice). */
export const SELECTION_UNDER_DATA_DB = 6;
/** The level when the pass has no data note to sit under (no star on screen) ([proposed], a listening choice). */
const QUIET_DB = -20;
/** The cutoff at the bottom and the top of the temperature scale (a stated design choice). */
const DARKEST_HZ = 1_500;
const BRIGHTEST_HZ = 12_000;

/** The data part's instrument of each piece, for a pass with no data note to read it from. */
const DATA_INSTRUMENT: Record<Arrangement['id'], InstrumentId> = { 'organ-pulse': 'chimes', 'ambient-beats': 'vibes', recording: 'glock' };

/** Whether a selection carries a catalog temperature the note may be coloured by. */
export function coloured(selection: MusicSelection): selection is MusicSelection & { temperatureK: number } {
  return selection.evidenceClass === 'catalog_matched' && typeof selection.temperatureK === 'number' && Number.isFinite(selection.temperatureK) && selection.temperatureK > 0;
}

/** The selection's note in a pass that started at `t0`, played no earlier than `nowSec + leadSec`; null when the register holds no tone of the set. */
export function selectionNote(selection: MusicSelection, pass: { readonly arrangement: Arrangement; readonly t0: number }, nowSec: number, leadSec: number): SelectionNote | null {
  const { arrangement, t0 } = pass;
  const beatSec = 60 / arrangement.bpm;
  const earliest = nowSec + leadSec;
  // Counted in whole beats, so the beat that ends the last bar is met exactly
  const beat = Math.max(0, Math.ceil((earliest - BEAT_SNAP_SEC - t0) / beatSec));
  const timeSec = beat <= arrangement.bars * arrangement.beatsPerBar ? t0 + beat * beatSec : earliest;
  // The bar it sounds in. A downbeat's offset can round a few units of the last place below its bar's start, so the
  // same margin takes it as that bar. The chords stop at the last bar, so the beat that ends it and a note after it
  // read as the last bar with no bound of their own.
  const offset = timeSec - t0;
  const bar = [...arrangement.chords].reverse().find((chord) => chord.startSec <= offset + BEAT_SNAP_SEC) ?? arrangement.chords[0];
  if (!bar) return null;
  const set = barPitchSet(bar.chord);
  const mapping = arrangement.dataMapping;
  const tones = chordTonesInRange(set, mapping.lowMidi, mapping.highMidi);
  if (tones.length === 0) return null;
  const isColoured = coloured(selection);
  const u = isColoured ? temperaturePosition(selection.temperatureK, mapping) : 0;
  const midi = isColoured ? tones[Math.min(tones.length - 1, Math.floor(u * tones.length))]! : (tones.find((t) => ((t % 12) + 12) % 12 === set.root) ?? tones[0]!);
  const data = arrangement.events.filter((e) => e.type === 'sample' && e.kind === 'data');
  const loudest = data.length > 0 ? Math.max(...data.map((e) => e.gainDb)) : QUIET_DB + SELECTION_UNDER_DATA_DB;
  const first = data[0];
  return {
    timeSec,
    midi,
    gainDb: loudest - SELECTION_UNDER_DATA_DB,
    pan: Math.max(-1, Math.min(1, selection.panX ?? 0)) * mapping.panWidth,
    instrument: first && first.type === 'sample' ? first.instrument : DATA_INSTRUMENT[arrangement.id],
    layer: 0,
    brightnessHz: isColoured ? DARKEST_HZ * (BRIGHTEST_HZ / DARKEST_HZ) ** u : null,
  };
}

/** The page's current selection for the music; the viewport's hook sets it, the engine follows it. */
export class MusicSelectionStore {
  private selection: MusicSelection | null = null;
  private readonly listeners = new Set<() => void>();

  readonly get = (): MusicSelection | null => this.selection;

  /** A different finding (or none) is a change; the same finding again is not. */
  readonly set = (selection: MusicSelection | null): void => {
    if ((selection?.id ?? null) === (this.selection?.id ?? null)) return;
    this.selection = selection;
    for (const listener of [...this.listeners]) listener();
  };

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
}

export const appMusicSelection = new MusicSelectionStore();
