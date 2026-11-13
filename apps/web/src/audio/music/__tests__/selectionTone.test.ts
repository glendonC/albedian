// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { RECORDING, ambientBeats, barPitchSet, organAndPulse, recordingArrangement, type Arrangement, type RecordingAnalysis } from '../directions';
import { pitchClassOf } from '../harmony';
import { BEAT_SNAP_SEC, SELECTION_UNDER_DATA_DB, selectionNote, type MusicSelection } from '../selectionTone';
import type { ScreenStar } from '../starNotes';

/** Stars with all three values, spread across the screen and the temperature scale (as directions.test.ts). */
const anyStars = fc.array(
  fc.record({
    id: fc.uuid(),
    teffK: fc.double({ min: 2500, max: 40000, noNaN: true }),
    luminositySolar: fc.double({ min: 0.05, max: 5e4, noNaN: true }),
    distancePc: fc.double({ min: 2, max: 2000, noNaN: true }),
    screenX: fc.double({ min: -1, max: 1, noNaN: true }),
    screenY: fc.double({ min: -1, max: 1, noNaN: true }),
  }),
  { minLength: 0, maxLength: 24 }
) as fc.Arbitrary<ScreenStar[]>;

/** A made-up analysis of the shape main.ts measures, half its bars confident. */
const ANALYSIS: RecordingAnalysis = {
  file: RECORDING.file,
  bpm: 66.3,
  beatSec: 60 / 66.3,
  tempoStrength: 0.8,
  beatsPerBar: 4,
  key: { name: 'A minor', tonic: 9, mode: 'minor', correlation: 0.8 },
  fromSec: 203.1,
  bars: Array.from({ length: RECORDING.bars }, (_, i) => ({ bar: i + 1, chord: ['Am', 'F', 'C', 'G', 'E'][i % 5]!, confident: i % 2 === 0, score: 0.8, margin: 0.1 })),
  statement: 'made-up analysis for this test',
};

const PIECES: Record<Arrangement['id'], (stars: ScreenStar[]) => Arrangement> = {
  'organ-pulse': organAndPulse,
  'ambient-beats': ambientBeats,
  recording: (stars) => recordingArrangement(stars, ANALYSIS),
};

const selectionArb: fc.Arbitrary<MusicSelection> = fc.record({
  id: fc.constant('f1'),
  evidenceClass: fc.constantFrom('observed', 'catalog_matched', 'predicted', 'simulated'),
  temperatureK: fc.option(fc.double({ min: 2000, max: 50000, noNaN: true }), { nil: null }),
  panX: fc.option(fc.double({ min: -1, max: 1, noNaN: true }), { nil: null }),
});

const LEAD = 0.25;

/** The chord of the bar a note sounds in, read by the bar's number: the last bar's once its end has passed. */
function chordOfBar(arrangement: Arrangement, bar: number) {
  return arrangement.chords.find((c) => c.bar === Math.min(bar, arrangement.bars))!;
}

describe('the note a selected finding plays in the music', () => {
  it('lands on a beat of the playing pass, in that bar\'s set, quiet under the data part, coloured only by a catalog temperature', () => {
    fc.assert(
      fc.property(
        fc.constantFrom<Arrangement['id']>('organ-pulse', 'ambient-beats', 'recording'),
        anyStars,
        selectionArb,
        fc.double({ min: 0, max: 100, noNaN: true }),
        fc.double({ min: -2, max: 90, noNaN: true }),
        (id, stars, selection, t0, sinceStart) => {
          const arrangement = PIECES[id](stars);
          const now = t0 + sinceStart;
          const note = selectionNote(selection, { arrangement, t0 }, now, LEAD)!;
          expect(note).not.toBeNull();
          const beatSec = 60 / arrangement.bpm;
          const lastBeat = arrangement.bars * arrangement.beatsPerBar;
          const earliest = now + LEAD;
          // The one margin, the code's: never earlier than the earliest time by more than it
          expect(note.timeSec).toBeGreaterThanOrEqual(earliest - BEAT_SNAP_SEC);
          let bar = arrangement.bars;
          if (earliest - BEAT_SNAP_SEC <= t0 + lastBeat * beatSec) {
            // On the grid exactly, the beat that ends the last bar included, and the first beat late enough:
            // the pass's first beat when it has not begun
            const k = Math.round((note.timeSec - t0) / beatSec);
            expect(note.timeSec).toBe(t0 + k * beatSec);
            expect(k).toBeGreaterThanOrEqual(0);
            expect(k).toBeLessThanOrEqual(lastBeat);
            if (earliest - BEAT_SNAP_SEC <= t0) expect(k).toBe(0);
            else expect(t0 + (k - 1) * beatSec).toBeLessThan(earliest - BEAT_SNAP_SEC);
            bar = Math.floor(k / arrangement.beatsPerBar) + 1;
          } else {
            // After the beat that ends the last bar: at once
            expect(note.timeSec).toBe(earliest);
          }
          const set = barPitchSet(chordOfBar(arrangement, bar).chord);
          expect(set.pitchClasses).toContain(pitchClassOf(note.midi));
          expect(note.midi).toBeGreaterThanOrEqual(arrangement.dataMapping.lowMidi);
          expect(note.midi).toBeLessThanOrEqual(arrangement.dataMapping.highMidi);
          const data = arrangement.events.filter((e) => e.type === 'sample' && e.kind === 'data');
          if (data.length > 0) expect(note.gainDb).toBeLessThanOrEqual(Math.max(...data.map((e) => e.gainDb)) - SELECTION_UNDER_DATA_DB);
          expect(Math.abs(note.pan)).toBeLessThanOrEqual(arrangement.dataMapping.panWidth);
          const catalogTemperature = selection.evidenceClass === 'catalog_matched' && selection.temperatureK !== null;
          if (catalogTemperature) {
            expect(note.brightnessHz).not.toBeNull();
          } else {
            // The plain instrument on the set's root: nothing is coloured without a catalog value
            expect(note.brightnessHz).toBeNull();
            if (set.pitchClasses.includes(set.root)) expect(pitchClassOf(note.midi)).toBe(set.root);
          }
        }
      ),
      {
        seed: SEED,
        numRuns: 200,
        examples: [
          ['recording', [], { id: 'f1', evidenceClass: 'catalog_matched', temperatureK: 9940, panX: 0.3 }, 0, 500],
          ['organ-pulse', [], { id: 'f1', evidenceClass: 'observed', temperatureK: null, panX: null }, 0, -1.250000001],
          // CI's counterexample on 0a69e413 (seed 1268335036): inside the last beat, once played at once, off the grid
          ['organ-pulse', [], { id: 'f1', evidenceClass: 'observed', temperatureK: null, panX: null }, 0, 46.750001000000005],
          // The same, half a beat into the last beat of each piece, the beat that ends the last bar itself, and half a beat after it
          ['organ-pulse', [], { id: 'f1', evidenceClass: 'observed', temperatureK: null, panX: null }, 0, 47.25],
          ['ambient-beats', [], { id: 'f1', evidenceClass: 'observed', temperatureK: null, panX: null }, 0, 49.4375],
          ['recording', [], { id: 'f1', evidenceClass: 'observed', temperatureK: null, panX: null }, 0, 56 * (60 / 66.3) - 0.25 - (60 / 66.3) / 2],
          ['organ-pulse', [], { id: 'f1', evidenceClass: 'observed', temperatureK: null, panX: null }, 0, 47.75],
          ['organ-pulse', [], { id: 'f1', evidenceClass: 'observed', temperatureK: null, panX: null }, 0, 48.25],
          // The margin itself: a beat half of it before the earliest time is taken, one and a half of it is not
          ['organ-pulse', [], { id: 'f1', evidenceClass: 'observed', temperatureK: null, panX: null }, 0, 9.75 + BEAT_SNAP_SEC / 2],
          ['organ-pulse', [], { id: 'f1', evidenceClass: 'observed', temperatureK: null, panX: null }, 0, 9.75 + BEAT_SNAP_SEC * 1.5],
          // At 96 a minute a beat is 0.625 s, so a margin of a billionth of a beat (main's) is 0.625 billionths of a
          // second: a beat 0.8 billionths of a second before the earliest time is within the one margin and not within that
          ['ambient-beats', [], { id: 'f1', evidenceClass: 'observed', temperatureK: null, panX: null }, 0, 9.75 + BEAT_SNAP_SEC * 0.8],
          // A downbeat whose offset from the pass's start rounds a few units of the last place below its bar's start
          // (found by search), where that bar's root differs from the bar before: it is read as its own bar
          ['organ-pulse', [], { id: 'f1', evidenceClass: 'observed', temperatureK: null, panX: null }, 0.2, 7.5],
          ['ambient-beats', [], { id: 'f1', evidenceClass: 'observed', temperatureK: null, panX: null }, 0.7, 7],
          ['recording', [], { id: 'f1', evidenceClass: 'observed', temperatureK: null, panX: null }, 0.9, 6.9],
        ],
      }
    );
  });

  it('takes a beat at most a billionth of a second before the earliest time, in seconds at every tempo', () => {
    // The margin's size written out here, not read from the code
    const plain: MusicSelection = { id: 'f', evidenceClass: 'observed', temperatureK: null, panX: null };
    const at = (arrangement: Arrangement, sinceStart: number) => selectionNote(plain, { arrangement, t0: 0 }, sinceStart, LEAD)!.timeSec;
    // At 60 a minute the beat at 10 s is taken when the earliest time is 0.75 billionths of a second after it, and
    // passed over for the beat at 11 s when it is 1.5 billionths after it
    expect(at(organAndPulse([]), 9.75 + 0.75e-9)).toBe(10);
    expect(at(organAndPulse([]), 9.75 + 1.5e-9)).toBe(11);
    // At 96 a minute (a beat of 0.625 s) the same in seconds: taken 0.8 billionths after, not 1.5 billionths after
    expect(at(ambientBeats([]), 9.75 + 0.8e-9)).toBe(10);
    expect(at(ambientBeats([]), 9.75 + 1.5e-9)).toBe(10.625);
  });

  it('rises in pitch and brightness with the catalog temperature, as the stars\' own notes do', () => {
    const arrangement = organAndPulse([]);
    const at = (temperatureK: number) => selectionNote({ id: 'f', evidenceClass: 'catalog_matched', temperatureK, panX: null }, { arrangement, t0: 0 }, 0.1, LEAD)!;
    const notes = [3000, 4500, 6000, 9000, 15000, 30000].map(at);
    for (let i = 1; i < notes.length; i++) {
      expect(notes[i]!.midi).toBeGreaterThanOrEqual(notes[i - 1]!.midi);
      expect(notes[i]!.brightnessHz!).toBeGreaterThan(notes[i - 1]!.brightnessHz!);
    }
    // Across the scale the pitch does rise: a cool star plays lower than a hot one
    expect(notes.at(-1)!.midi).toBeGreaterThan(notes[0]!.midi);
    // An observed finding with the same number plays plain: only catalog values colour the note
    expect(selectionNote({ id: 'f', evidenceClass: 'observed', temperatureK: 30000, panX: null }, { arrangement, t0: 0 }, 0.1, LEAD)!.brightnessHz).toBeNull();
  });

  it('pans by the finding\'s place on the photo, within the data part\'s width, and centres without one', () => {
    const arrangement = ambientBeats([]);
    const at = (panX: number | null) => selectionNote({ id: 'f', evidenceClass: 'observed', temperatureK: null, panX }, { arrangement, t0: 0 }, 0, LEAD)!.pan;
    expect(at(0.4)).toBeCloseTo(0.4 * arrangement.dataMapping.panWidth, 9);
    expect(at(-1)).toBeCloseTo(-arrangement.dataMapping.panWidth, 9);
    expect(at(null)).toBe(0);
  });

  it("plays the piece's data instrument, from its data notes or, with none, the piece's own", () => {
    const plain: MusicSelection = { id: 'f', evidenceClass: 'observed', temperatureK: null, panX: null };
    expect(selectionNote(plain, { arrangement: organAndPulse([]), t0: 0 }, 0, LEAD)!.instrument).toBe('chimes');
    expect(selectionNote(plain, { arrangement: ambientBeats([]), t0: 0 }, 0, LEAD)!.instrument).toBe('vibes');
    expect(selectionNote(plain, { arrangement: recordingArrangement([], ANALYSIS), t0: 0 }, 0, LEAD)!.instrument).toBe('glock');
  });
});
