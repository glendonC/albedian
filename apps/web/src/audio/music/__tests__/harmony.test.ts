import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  chordTonesInRange,
  hzToMidi,
  midiToHz,
  noteName,
  parseChord,
  parseNote,
  pitchClassOf,
  scalePitchClasses,
} from '../harmony';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const params = (examples: unknown[] = []) => ({ ...(SEED !== undefined ? { seed: SEED } : {}), numRuns: 300, examples } as never);

const sorted = (pcs: readonly number[]) => [...pcs].sort((a, b) => a - b);

describe('pitch', () => {
  it('tunes A4 to 440 Hz in equal temperament', () => {
    expect(midiToHz(69)).toBe(440);
    expect(midiToHz(81)).toBeCloseTo(880, 9);
    expect(midiToHz(60)).toBeCloseTo(261.6255653005986, 9);
  });

  it('reads note names with sharps and flats, octave 4 holding middle C', () => {
    expect(parseNote('C4')).toBe(60);
    expect(parseNote('A4')).toBe(69);
    expect(parseNote('Bb3')).toBe(58);
    expect(parseNote('A#3')).toBe(58);
    expect(parseNote('F#5')).toBe(78);
    expect(parseNote('D#1')).toBe(27);
    expect(parseNote('Cb4')).toBe(59);
    expect(noteName(60)).toBe('C4');
    expect(noteName(58)).toBe('A#3');
    expect(() => parseNote('H2')).toThrow();
    expect(() => parseNote('C')).toThrow();
  });

  it('round-trips MIDI through Hz and names', () => {
    fc.assert(
      fc.property(fc.integer({ min: 0, max: 127 }), (m) => {
        expect(hzToMidi(midiToHz(m))).toBeCloseTo(m, 9);
        expect(parseNote(noteName(m))).toBe(m);
        expect(pitchClassOf(m)).toBe(m % 12);
      }),
      params([[0], [127], [60]])
    );
  });
});

describe('chords', () => {
  it('spells the chords the directions use', () => {
    expect(sorted(parseChord('Dm').pitchClasses)).toEqual([2, 5, 9]);
    expect(parseChord('Dm').root).toBe(2);
    expect(sorted(parseChord('D').pitchClasses)).toEqual([2, 6, 9]);
    expect(sorted(parseChord('Bb/D').pitchClasses)).toEqual([2, 5, 10]);
    expect(parseChord('Bb/D').bass).toBe(2);
    expect(sorted(parseChord('C/D').pitchClasses)).toEqual([0, 4, 7]);
    expect(parseChord('C/D').bass).toBe(2);
    expect(sorted(parseChord('Fm9').pitchClasses)).toEqual([0, 3, 5, 7, 8]);
    expect(sorted(parseChord('Dbmaj7').pitchClasses)).toEqual([0, 1, 5, 8]);
    expect(sorted(parseChord('Dbmaj9').pitchClasses)).toEqual([0, 1, 3, 5, 8]);
    expect(sorted(parseChord('Asus4').pitchClasses)).toEqual([2, 4, 9]);
    expect(sorted(parseChord('Dsus2').pitchClasses)).toEqual([2, 4, 9]);
    expect(sorted(parseChord('Eb6').pitchClasses)).toEqual([0, 3, 7, 10]);
    expect(sorted(parseChord('Cm7').pitchClasses)).toEqual([0, 3, 7, 10]);
    expect(sorted(parseChord('Gm7').pitchClasses)).toEqual([2, 5, 7, 10]);
    expect(sorted(parseChord('Ebmaj7').pitchClasses)).toEqual([2, 3, 7, 10]);
    expect(sorted(parseChord('Abadd9').pitchClasses)).toEqual([0, 3, 8, 10]);
    expect(sorted(parseChord('E7').pitchClasses)).toEqual([2, 4, 8, 11]);
    expect(sorted(parseChord('Am').pitchClasses)).toEqual([0, 4, 9]);
    expect(parseChord('Dm').bass).toBe(2);
    expect(() => parseChord('Dx')).toThrow();
    expect(() => parseChord('')).toThrow();
  });

  it('lists every chord tone in a register, ascending, and nothing else', () => {
    expect(chordTonesInRange(parseChord('Dm'), 62, 74)).toEqual([62, 65, 69, 74]);
    const names = ['Dm', 'Bb/D', 'C/D', 'Gm/D', 'D', 'Fm9', 'Dbmaj7', 'Eb6', 'Cm7', 'Asus4', 'Abadd9', 'Dbmaj9'];
    fc.assert(
      fc.property(
        fc.constantFrom(...names),
        fc.integer({ min: 21, max: 100 }),
        fc.integer({ min: 0, max: 36 }),
        (name, low, span) => {
          const chord = parseChord(name);
          const tones = chordTonesInRange(chord, low, low + span);
          for (let i = 1; i < tones.length; i++) expect(tones[i]!).toBeGreaterThan(tones[i - 1]!);
          for (let m = low; m <= low + span; m++) {
            expect(tones.includes(m)).toBe(chord.pitchClasses.includes(pitchClassOf(m)));
          }
        }
      ),
      params([['Dm', 62, 12], ['Fm9', 21, 0]])
    );
  });
});

describe('scales', () => {
  it('gives the pentatonic notes of a key', () => {
    expect(sorted(scalePitchClasses(9, 'minorPentatonic'))).toEqual([0, 2, 4, 7, 9]);
    expect(sorted(scalePitchClasses(0, 'majorPentatonic'))).toEqual([0, 2, 4, 7, 9]);
    expect(sorted(scalePitchClasses(2, 'minor'))).toEqual([0, 2, 4, 5, 7, 9, 10]);
    expect(sorted(scalePitchClasses(5, 'major'))).toEqual([0, 2, 4, 5, 7, 9, 10]);
  });
});
