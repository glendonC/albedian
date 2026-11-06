/**
 * Pitch, chords and scales for the music (AUDIO_ENGINE.md section 13). Pure.
 *
 * Equal temperament with A4 = 440 Hz; MIDI note numbers with C4 = 60. A chord or a scale is a
 * pitch set: the pitch classes (0 = C to 11 = B) a data note may land on while it sounds.
 */

export type PitchClass = number;

/** The pitch classes a note may take while a chord (or a stated scale) sounds. */
export interface PitchSet {
  readonly name: string;
  readonly root: PitchClass;
  readonly pitchClasses: readonly PitchClass[];
  /** The bass under it, which may lie outside the set (a pedal, a slash chord's bass). */
  readonly bass: PitchClass;
}

const SHARP_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'] as const;
const LETTERS: Readonly<Record<string, number>> = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };

export function midiToHz(midi: number): number {
  return 440 * 2 ** ((midi - 69) / 12);
}

export function hzToMidi(hz: number): number {
  return 69 + 12 * Math.log2(hz / 440);
}

export function pitchClassOf(midi: number): PitchClass {
  return ((Math.round(midi) % 12) + 12) % 12;
}

/** 'C4' for 60; sharps for black keys. */
export function noteName(midi: number): string {
  const m = Math.round(midi);
  return `${SHARP_NAMES[pitchClassOf(m)]}${Math.floor(m / 12) - 1}`;
}

function parsePitchClass(text: string): { pc: PitchClass; rest: string } {
  const letter = LETTERS[text.charAt(0)];
  if (letter === undefined) throw new Error(`not a note name: '${text}'`);
  let pc = letter;
  let i = 1;
  while (text.charAt(i) === '#' || text.charAt(i) === 'b') {
    pc += text.charAt(i) === '#' ? 1 : -1;
    i += 1;
  }
  return { pc: ((pc % 12) + 12) % 12, rest: text.slice(i) };
}

/** 'D4' to 62, 'Bb3' to 58, 'Cb4' to 59 (the octave number belongs to the letter). */
export function parseNote(name: string): number {
  const match = /^([A-G])([#b]*)(-?\d+)$/.exec(name);
  if (!match) throw new Error(`not a note: '${name}'`);
  const letter = LETTERS[match[1]!]!;
  const accidentals = [...match[2]!].reduce((sum, c) => sum + (c === '#' ? 1 : -1), 0);
  return (Number(match[3]) + 1) * 12 + letter + accidentals;
}

/** Intervals above the root, in semitones, for each chord quality the scores use. */
const QUALITIES: Readonly<Record<string, readonly number[]>> = {
  '': [0, 4, 7],
  m: [0, 3, 7],
  '6': [0, 4, 7, 9],
  m6: [0, 3, 7, 9],
  '7': [0, 4, 7, 10],
  maj7: [0, 4, 7, 11],
  m7: [0, 3, 7, 10],
  '9': [0, 4, 7, 10, 14],
  maj9: [0, 4, 7, 11, 14],
  m9: [0, 3, 7, 10, 14],
  add9: [0, 4, 7, 14],
  madd9: [0, 3, 7, 14],
  sus2: [0, 2, 7],
  sus4: [0, 5, 7],
  '5': [0, 7],
};

/** A chord symbol such as 'Dm', 'Bb/D', 'Fm9', 'Dbmaj7' or 'Asus4'. */
export function parseChord(symbol: string): PitchSet {
  const [head, bassText, extra] = symbol.split('/');
  if (!head || extra !== undefined) throw new Error(`not a chord: '${symbol}'`);
  const { pc: root, rest } = parsePitchClass(head);
  const intervals = QUALITIES[rest];
  if (!intervals) throw new Error(`unknown chord quality in '${symbol}'`);
  const pitchClasses = [...new Set(intervals.map((i) => (root + i) % 12))];
  const bass = bassText === undefined ? root : parsePitchClass(bassText).pc;
  if (bassText !== undefined && parsePitchClass(bassText).rest !== '') throw new Error(`not a bass note in '${symbol}'`);
  return { name: symbol, root, pitchClasses, bass };
}

/** A named pitch set that is not a chord, such as a key's pentatonic notes. */
export function pitchSet(name: string, root: PitchClass, pitchClasses: readonly PitchClass[]): PitchSet {
  return { name, root, pitchClasses: [...new Set(pitchClasses.map((pc) => ((pc % 12) + 12) % 12))], bass: root };
}

export type ScaleMode = 'major' | 'minor' | 'majorPentatonic' | 'minorPentatonic';

const MODES: Readonly<Record<ScaleMode, readonly number[]>> = {
  major: [0, 2, 4, 5, 7, 9, 11],
  minor: [0, 2, 3, 5, 7, 8, 10],
  majorPentatonic: [0, 2, 4, 7, 9],
  minorPentatonic: [0, 3, 5, 7, 10],
};

export function scalePitchClasses(tonic: PitchClass, mode: ScaleMode): PitchClass[] {
  return MODES[mode].map((i) => (tonic + i) % 12);
}

/** Every MIDI note from `lowMidi` to `highMidi` whose pitch class is in the set, ascending. */
export function chordTonesInRange(set: PitchSet, lowMidi: number, highMidi: number): number[] {
  const tones: number[] = [];
  for (let m = Math.ceil(lowMidi); m <= highMidi; m++) if (set.pitchClasses.includes(pitchClassOf(m))) tones.push(m);
  return tones;
}
