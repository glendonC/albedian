/**
 * The three music directions over the real landing (AUDIO_ENGINE.md section 13), as data: tempo,
 * key, bars, chords, sections, and every event with what plays it and whether it is composed or
 * data. The catalog stars on screen play the data notes (starNotes.ts); everything else is composed
 * here, from no existing score. Qualities only were taken from the references (an organ, an ostinato,
 * a ticking pulse and swells; polished ambient beats), never their notes.
 *
 * Pure: main.ts renders an arrangement with Web Audio.
 */

import { parseChord, pitchClassOf, pitchSet, scalePitchClasses, type PitchSet } from './harmony';
import { DEFAULT_STAR_NOTE_MAPPING, starSweep, type ScreenStar, type StarNote, type StarNoteMapping } from './starNotes';
import { vcslRootMidi, type SampleZone } from './sampler';

export type DirectionId = 'organ-pulse' | 'ambient-beats' | 'recording';
export type Kind = 'composed' | 'data' | 'recording';

/** An instrument made of recorded samples, its files as sources.json names them. */
export interface InstrumentDef {
  readonly id: string;
  readonly label: string;
  readonly credit: string;
  readonly zones: readonly SampleZone[];
  readonly attackSec: number;
  readonly releaseSec: number;
  readonly gainDb: number;
}

const VCSL = 'vcsl/';
const ORGAN = `${VCSL}Aerophones/Edge-blown Aerophones/Pipe Organ/`;
const STRUCK = `${VCSL}Idiophones/Struck Idiophones/`;
const ORGAN_CREDIT = 'Pipe organ sampled by Simon Dalzell of Ivy Audio, Versilian Community Sample Library (VCSL), CC0 1.0';
const VCSL_CREDIT = 'Versilian Community Sample Library (VCSL), Versilian Studios LLC, CC0 1.0';
const MINOR_THIRDS = ['C', 'D#', 'F#', 'A'];

const pitched = (names: readonly string[], file: (name: string) => string, layer = 0, roundRobin = 0): SampleZone[] =>
  names.map((name) => ({ file: file(name), rootMidi: vcslRootMidi(name), layer, roundRobin }));
const octaves = (letters: readonly string[], from: number, to: number) =>
  letters.flatMap((l) => Array.from({ length: to - from + 1 }, (_, i) => `${l}${from + i}`));

export const INSTRUMENTS = {
  organQuiet: {
    id: 'organQuiet',
    label: 'Pipe organ, quiet manual',
    credit: ORGAN_CREDIT,
    zones: pitched([...octaves(MINOR_THIRDS, 1, 5), 'C6'], (n) => `${ORGAN}Quiet/NT5_Man3Quiet_${n}_rr1.wav`),
    attackSec: 0.02,
    releaseSec: 0.7,
    gainDb: 0,
  },
  organLoud: {
    id: 'organLoud',
    label: 'Pipe organ, loud manual (open stops)',
    credit: ORGAN_CREDIT,
    zones: pitched([...octaves(MINOR_THIRDS, 1, 5), 'C6'], (n) => `${ORGAN}Loud/Rode_Man3Open_${n}.wav`),
    attackSec: 0.02,
    releaseSec: 0.7,
    gainDb: 0,
  },
  pedalQuiet: {
    id: 'pedalQuiet',
    label: 'Pipe organ, quiet pedal',
    credit: ORGAN_CREDIT,
    zones: pitched([...octaves(['C', 'D#', 'F#'], 1, 3), 'A1', 'A2'], (n) => `${ORGAN}Quiet Pedal/NT5_PedalQuiet_${n}_rr1.wav`),
    attackSec: 0.03,
    releaseSec: 0.9,
    gainDb: 0,
  },
  pedalLoud: {
    id: 'pedalLoud',
    label: 'Pipe organ, loud pedal',
    credit: ORGAN_CREDIT,
    zones: pitched([...octaves(['C', 'D#', 'F#'], 1, 3), 'A1', 'A2'], (n) => `${ORGAN}Loud Pedal/Rode_Pedal_${n}.wav`),
    attackSec: 0.03,
    releaseSec: 0.9,
    gainDb: 0,
  },
  blower: {
    id: 'blower',
    label: "The organ's blower (its wind, recorded)",
    credit: ORGAN_CREDIT,
    zones: [{ file: `${ORGAN}Rode_Blower.wav`, rootMidi: null, layer: 0, roundRobin: 0 }],
    attackSec: 2.5,
    releaseSec: 3,
    gainDb: 0,
  },
  chimes: {
    id: 'chimes',
    label: 'Hand chimes',
    credit: VCSL_CREDIT,
    zones: pitched(
      ['C3', 'D3', 'E3', 'F#3', 'G#3', 'A#3', 'C4', 'D4', 'E4', 'F#4', 'G#4', 'A4', 'C5', 'D5', 'E5', 'F#5', 'G#5', 'A#5', 'C6'],
      (n) => `${STRUCK}Hand Chimes/sus_${n}_r01_main.wav`
    ),
    attackSec: 0.004,
    releaseSec: 1.8,
    gainDb: 0,
  },
  claves: {
    id: 'claves',
    label: 'Claves',
    credit: VCSL_CREDIT,
    zones: [1, 2, 3].flatMap((v) => [
      { file: `${STRUCK}Claves/Claves1_Hit_v${v}_rr1_Mid.wav`, rootMidi: null, layer: v - 1, roundRobin: 0 },
      { file: `${STRUCK}Claves/Claves2_Hit_v${v}_rr1_Mid.wav`, rootMidi: null, layer: v - 1, roundRobin: 1 },
    ]),
    attackSec: 0.001,
    releaseSec: 0.05,
    gainDb: 0,
  },
  cymbalSwell: {
    id: 'cymbalSwell',
    label: 'Suspended cymbal, crescendo roll',
    credit: VCSL_CREDIT,
    zones: [{ file: `${STRUCK}Suspended Cymbal 1/susCymb1_cresc_7.5s.wav`, rootMidi: null, layer: 0, roundRobin: 0 }],
    attackSec: 0.05,
    releaseSec: 1.5,
    gainDb: 0,
  },
  vibes: {
    id: 'vibes',
    label: 'Vibraphone, soft mallets',
    credit: VCSL_CREDIT,
    zones: [
      ...pitched(['F2', 'A2', 'B3', 'D4', 'F4', 'G3', 'A4', 'C5', 'E5'], (n) => `${STRUCK}Vibraphone/Soft Mallets/Vibes_soft_${n}_v1_rr1_Main.wav`, 0),
      ...pitched(['C3', 'E3'], (n) => `${STRUCK}Vibraphone/Soft Mallets/Vibes_soft_${n}_v1_rr2_Main.wav`, 0),
      ...pitched(['F2', 'A2', 'C3', 'E3', 'G3', 'B3', 'D4', 'F4', 'A4', 'C5', 'E5'], (n) => `${STRUCK}Vibraphone/Soft Mallets/Vibes_soft_${n}_v2_rr1_Main.wav`, 1),
    ],
    attackSec: 0.015,
    releaseSec: 1.2,
    gainDb: 0,
  },
  glock: {
    id: 'glock',
    label: 'Glockenspiel',
    credit: VCSL_CREDIT,
    zones: [
      ...pitched(['G4', 'G5', 'G6', 'C6'], (n) => `${STRUCK}Glockenspiel/glock_soft_${n}_01.wav`, 0),
      ...pitched(['C5'], (n) => `${STRUCK}Glockenspiel/glock_soft_${n}_02.wav`, 0),
      ...pitched(['C7'], (n) => `${STRUCK}Glockenspiel/glock_soft_${n}_03.wav`, 0),
      ...pitched(['G4', 'C5', 'G5', 'C6', 'G6', 'C7'], (n) => `${STRUCK}Glockenspiel/glock_medium_${n}_01.wav`, 1),
    ],
    attackSec: 0.002,
    releaseSec: 1.5,
    gainDb: 0,
  },
  hats: {
    id: 'hats',
    label: 'Hi-hat cymbal',
    credit: VCSL_CREDIT,
    zones: [1, 2, 3, 4].flatMap((v) =>
      [1, 2].map((rr) => ({ file: `${STRUCK}Hi-Hat Cymbal/HiHat_HitC_v${v}_rr${rr}_Mid.wav`, rootMidi: null, layer: v - 1, roundRobin: rr - 1 }))
    ),
    attackSec: 0.001,
    releaseSec: 0.06,
    gainDb: 0,
  },
  clap: {
    id: 'clap',
    label: 'Hand claps',
    credit: VCSL_CREDIT,
    zones: [1, 2, 3, 4, 5, 6].map((rr) => ({ file: `${STRUCK}Claps/Clap_rr${rr}.wav`, rootMidi: null, layer: 0, roundRobin: rr - 1 })),
    attackSec: 0.001,
    releaseSec: 0.1,
    gainDb: 0,
  },
  shaker: {
    id: 'shaker',
    label: 'Small shaker',
    credit: VCSL_CREDIT,
    zones: [1, 2].flatMap((rr) => [
      { file: `${STRUCK}Shaker, Small/Mid_ShakerHighFaster_Down_rr${rr}.wav`, rootMidi: null, layer: 0, roundRobin: 2 * (rr - 1) },
      { file: `${STRUCK}Shaker, Small/Mid_ShakerHighFaster_Up_rr${rr}.wav`, rootMidi: null, layer: 0, roundRobin: 2 * (rr - 1) + 1 },
    ]),
    attackSec: 0.001,
    releaseSec: 0.04,
    gainDb: 0,
  },
} as const satisfies Record<string, InstrumentDef>;

export type InstrumentId = keyof typeof INSTRUMENTS;
export type BusId = 'main' | 'data' | 'drums' | 'pads' | 'recording';

export type MusicEvent =
  | { readonly type: 'sample'; readonly instrument: InstrumentId; readonly bus: BusId; readonly kind: Kind; readonly part: string; readonly timeSec: number; readonly midi: number; readonly durationSec: number; readonly gainDb: number; readonly pan: number; readonly layer: number; readonly star?: StarNote }
  | { readonly type: 'pad'; readonly bus: BusId; readonly part: string; readonly timeSec: number; readonly durationSec: number; readonly midis: readonly number[]; readonly gainDb: number; readonly attackSec: number; readonly releaseSec: number; readonly cutoffFromHz: number; readonly cutoffToHz: number }
  | { readonly type: 'sub'; readonly bus: BusId; readonly part: string; readonly timeSec: number; readonly durationSec: number; readonly midi: number; readonly gainDb: number }
  | { readonly type: 'kick'; readonly bus: BusId; readonly part: string; readonly timeSec: number; readonly gainDb: number }
  | { readonly type: 'riser'; readonly bus: BusId; readonly part: string; readonly timeSec: number; readonly durationSec: number; readonly gainDb: number };

export interface BusDef {
  readonly id: BusId;
  readonly gainDb: number;
  /** Send to the room, dB; null for none. */
  readonly reverbSendDb: number | null;
  readonly delaySendDb: number | null;
  readonly duckUnderKick?: { readonly depthDb: number; readonly releaseSec: number };
}

export interface BarChord {
  readonly bar: number;
  readonly startSec: number;
  readonly chord: string;
  /** Where the bar's pitch set comes from. */
  readonly source: 'composed' | 'measured' | 'pentatonic fallback';
}

export interface PartFact {
  readonly name: string;
  readonly kind: Kind;
  readonly plays: string;
  readonly source: string;
}

export interface Arrangement {
  readonly id: DirectionId;
  readonly title: string;
  readonly summary: string;
  readonly bpm: number;
  readonly beatsPerBar: number;
  readonly bars: number;
  readonly durationSec: number;
  readonly key: string;
  readonly chords: readonly BarChord[];
  readonly events: readonly MusicEvent[];
  readonly buses: readonly BusDef[];
  readonly room: 'chapel' | 'synthetic-hall';
  readonly roomReturnDb: number;
  readonly delay: { readonly delaySec: number; readonly feedback: number; readonly wetDb: number; readonly lowpassHz: number } | null;
  readonly parts: readonly PartFact[];
  readonly dataMapping: StarNoteMapping;
  readonly recording?: { readonly file: string; readonly fromSec: number; readonly durationSec: number; readonly fadeInSec: number; readonly fadeOutSec: number; readonly gainDb: number; readonly credit: string };
}

const DATA_PART = 'The stars';

/** The data notes of one bar: the loudest `count` complete stars in sweep order, one per step. */
function sweepBar(
  stars: readonly ScreenStar[],
  chord: PitchSet,
  mapping: StarNoteMapping,
  startSec: number,
  stepSec: number,
  count: number,
  instrument: InstrumentId,
  options: { gainDb: number; durationSec: number; layer: number; offsetStars?: number; octave?: number }
): MusicEvent[] {
  const pool = options.offsetStars ? starSweepSkipping(stars, chord, mapping, count, options.offsetStars) : starSweep(stars, chord, mapping, count);
  return pool.map((note, i) => ({
    type: 'sample',
    instrument,
    bus: 'data',
    kind: 'data',
    part: DATA_PART,
    timeSec: startSec + i * stepSec,
    midi: note.midi + 12 * (options.octave ?? 0),
    durationSec: options.durationSec,
    gainDb: options.gainDb + note.gainDb,
    pan: note.pan,
    layer: options.layer,
    star: note,
  }));
}

/** The next `count` stars after the loudest `skip`, in sweep order. */
function starSweepSkipping(stars: readonly ScreenStar[], chord: PitchSet, mapping: StarNoteMapping, count: number, skip: number): StarNote[] {
  const loudest = new Set(starSweep(stars, chord, mapping, skip).map((n) => n.starId));
  return starSweep(stars.filter((s) => !loudest.has(s.id)), chord, mapping, count);
}

/**
 * A. Organ and pulse. D minor, 60 beats a minute, so the ticking pulse marks real seconds; 12 bars of
 * 4 s and a 5 s tail. A pipe organ over a pedal D, the stars' ostinato on hand chimes from bar 3,
 * a suspended-cymbal crescendo and the loud manual swelling into a D major arrival at 40 s.
 */
export function organAndPulse(stars: readonly ScreenStar[]): Arrangement {
  const bpm = 60;
  const bar = 4;
  const plan: { chord: string; voicing: number[]; pedal: number }[] = [
    { chord: 'Dm', voicing: [50, 57, 62, 65, 69], pedal: 38 },
    { chord: 'Dm', voicing: [50, 57, 62, 65, 69], pedal: 38 },
    { chord: 'Bb/D', voicing: [50, 58, 62, 65, 70], pedal: 38 },
    { chord: 'C/D', voicing: [52, 55, 60, 64, 67], pedal: 38 },
    { chord: 'Dm', voicing: [50, 57, 62, 65, 69], pedal: 38 },
    { chord: 'Bb/D', voicing: [50, 58, 62, 65, 70], pedal: 38 },
    { chord: 'Gm/D', voicing: [50, 55, 62, 67, 70], pedal: 38 },
    { chord: 'Asus4', voicing: [52, 57, 62, 64, 69], pedal: 45 },
    { chord: 'Bb', voicing: [53, 58, 62, 65, 70], pedal: 46 },
    { chord: 'C', voicing: [52, 55, 60, 64, 67, 72], pedal: 36 },
    { chord: 'D', voicing: [50, 57, 62, 66, 69, 74], pedal: 38 },
    { chord: 'D', voicing: [50, 57, 62, 66, 69, 74], pedal: 38 },
  ];
  const mapping: StarNoteMapping = { ...DEFAULT_STAR_NOTE_MAPPING, lowMidi: 69, highMidi: 98 };
  const events: MusicEvent[] = [];
  const organGain = [-11, -10, -9, -9, -9, -9, -8, -8, -7, -5, -2, -5];
  const loudGain = [null, null, null, null, null, null, null, null, -20, -13, -5, -9] as const;

  // Organ chords: a voice held over bars where it does not change, so it is not struck again
  const holdVoice = (instrument: InstrumentId, pick: (i: number) => number | null, gains: readonly (number | null)[], part: string, pan: number) => {
    let i = 0;
    while (i < plan.length) {
      const midi = pick(i);
      const gain = gains[i];
      if (midi === null || gain === null || gain === undefined) {
        i++;
        continue;
      }
      let j = i + 1;
      while (j < plan.length && pick(j) === midi && gains[j] !== null && j - i < 2) j++;
      events.push({ type: 'sample', instrument, bus: 'main', kind: 'composed', part, timeSec: i * bar, midi, durationSec: (j - i) * bar - 0.05, gainDb: gain, pan, layer: 0 });
      i = j;
    }
  };
  for (let v = 0; v < 6; v++) {
    const pan = (v - 2.5) * 0.12;
    holdVoice('organQuiet', (i) => plan[i]!.voicing[v] ?? null, organGain, 'Organ', pan);
    holdVoice('organLoud', (i) => plan[i]!.voicing[v] ?? null, loudGain, 'Organ, loud manual', pan);
  }
  holdVoice('pedalQuiet', (i) => plan[i]!.pedal, organGain.map((g) => g - 2), 'Organ pedal', 0);
  holdVoice('pedalLoud', (i) => plan[i]!.pedal, [null, null, null, null, null, null, null, null, -16, -10, -4, -8], 'Organ pedal, loud', 0);

  // The organ's own wind, two overlapping takes for the whole clip
  events.push({ type: 'sample', instrument: 'blower', bus: 'main', kind: 'composed', part: "The organ's wind", timeSec: 0, midi: 48, durationSec: 28, gainDb: -29, pan: 0, layer: 0 });
  events.push({ type: 'sample', instrument: 'blower', bus: 'main', kind: 'composed', part: "The organ's wind", timeSec: 26, midi: 48, durationSec: 24, gainDb: -29, pan: 0, layer: 0 });

  // The pulse: one tick a second from bar 2 to the last beat of bar 12, the first of each bar a little firmer
  for (let beat = 4; beat < 48; beat++) {
    events.push({ type: 'sample', instrument: 'claves', bus: 'main', kind: 'composed', part: 'Ticking pulse', timeSec: beat, midi: 60, durationSec: 0.3, gainDb: beat % 4 === 0 ? -17 : -21, pan: 0.18, layer: beat % 4 === 0 ? 1 : 0 });
  }

  // The swell into the arrival at 40 s: the cymbal's 7.5 s crescendo ends on the downbeat
  events.push({ type: 'sample', instrument: 'cymbalSwell', bus: 'main', kind: 'composed', part: 'Cymbal swell', timeSec: 40 - 7.5, midi: 60, durationSec: 8.2, gainDb: -14, pan: -0.1, layer: 0 });

  // The stars: eight chime notes a bar from bar 3, on the eighth notes; at the arrival the next eight stars join on the sixteenth off-beats
  for (let b = 2; b < 12; b++) {
    const chord = parseChord(plan[b]!.chord);
    events.push(...sweepBar(stars, chord, mapping, b * bar, 0.5, 8, 'chimes', { gainDb: b < 4 ? -13 : b < 10 ? -10 : -8, durationSec: 1.6, layer: 0 }));
    if (b === 10 || b === 11) {
      events.push(...sweepBar(stars, chord, mapping, b * bar + 0.25, 0.5, 8, 'chimes', { gainDb: -14, durationSec: 1.2, layer: 0, offsetStars: 8 }));
    }
  }

  return {
    id: 'organ-pulse',
    title: 'A. Organ and pulse',
    summary: 'A pipe organ in a stone chapel over a pedal D, a clock-like tick every second, and the stars as a chime ostinato that swells into a D major arrival.',
    bpm,
    beatsPerBar: 4,
    bars: 12,
    durationSec: 53,
    key: 'D minor',
    chords: plan.map((p, i) => ({ bar: i + 1, startSec: i * bar, chord: p.chord, source: 'composed' })),
    events,
    buses: [
      { id: 'main', gainDb: 0, reverbSendDb: -6, delaySendDb: null },
      { id: 'data', gainDb: 3, reverbSendDb: -7, delaySendDb: null },
    ],
    room: 'chapel',
    roomReturnDb: -4,
    delay: null,
    parts: [
      { name: 'Organ', kind: 'composed', plays: 'the chords, quiet manual and pedal, then the loud manual in the swell', source: ORGAN_CREDIT },
      { name: "The organ's wind", kind: 'composed', plays: "the organ's blower, recorded, under the whole clip", source: ORGAN_CREDIT },
      { name: 'Ticking pulse', kind: 'composed', plays: 'claves, one tick a second (60 beats a minute)', source: VCSL_CREDIT },
      { name: 'Cymbal swell', kind: 'composed', plays: 'a suspended-cymbal crescendo into the arrival', source: VCSL_CREDIT },
      { name: DATA_PART, kind: 'data', plays: 'hand chimes: each note is a catalog star on screen (how, below)', source: VCSL_CREDIT },
      { name: 'Room', kind: 'composed', plays: 'the St. Nicholas Chapel, Aachen, a measured impulse response', source: 'Zerwas and Kayku, Zenodo 23312556, CC BY 4.0 (in the app already)' },
    ],
    dataMapping: mapping,
  };
}

/**
 * B. Ambient beats. F minor, 96 beats a minute, 20 bars of 2.5 s and a 3.5 s tail. A detuned-saw pad
 * and a sub bass on Fm9, Dbmaj7, Abmaj7, Eb6; kick, clap, hi-hats and shaker; the stars as a
 * vibraphone arpeggio through a dotted-eighth delay; a breakdown and a riser into an arrival at 40 s.
 */
export function ambientBeats(stars: readonly ScreenStar[]): Arrangement {
  const bpm = 96;
  const beat = 60 / bpm;
  const bar = 4 * beat;
  const loop: { chord: string; voicing: number[]; root: number }[] = [
    { chord: 'Fm9', voicing: [53, 56, 60, 63, 67], root: 41 },
    { chord: 'Dbmaj7', voicing: [49, 56, 60, 65], root: 37 },
    { chord: 'Abmaj7', voicing: [51, 56, 60, 67], root: 44 },
    { chord: 'Eb6', voicing: [51, 55, 58, 60], root: 39 },
  ];
  const bars = 20;
  const plan = Array.from({ length: bars }, (_, i) => loop[i % 4]!);
  const mapping: StarNoteMapping = { ...DEFAULT_STAR_NOTE_MAPPING, lowMidi: 65, highMidi: 88 };
  const events: MusicEvent[] = [];
  const kickTimes: number[] = [];
  const swing = 0.04 * beat;

  for (let b = 0; b < bars; b++) {
    const t = b * bar;
    const p = plan[b]!;
    const section = b < 4 ? 'intro' : b < 12 ? 'groove' : b < 16 ? 'break' : 'arrival';
    const cutoff = section === 'intro' ? [700, 900] : section === 'groove' ? [900 + (b - 4) * 180, 900 + (b - 3) * 180] : section === 'break' ? [1400 + (b - 12) * 500, 1400 + (b - 11) * 500] : [3800, 4200];
    events.push({ type: 'pad', bus: 'pads', part: 'Pad', timeSec: t, durationSec: bar - 0.05, midis: p.voicing, gainDb: section === 'arrival' ? -11 : -13, attackSec: b === 0 ? 1.6 : 0.25, releaseSec: b === bars - 1 ? 3 : 0.4, cutoffFromHz: cutoff[0]!, cutoffToHz: cutoff[1]! });
    if (b >= 2) events.push({ type: 'sub', bus: 'main', part: 'Sub bass', timeSec: t, durationSec: bar * (section === 'break' ? 0.5 : 0.92), midi: p.root, gainDb: -14 });

    const drums = section === 'groove' || section === 'arrival';
    if (drums && b < bars - 1) {
      for (const k of [0, 2]) {
        kickTimes.push(t + k * beat);
        events.push({ type: 'kick', bus: 'drums', part: 'Kick', timeSec: t + k * beat, gainDb: -7 });
      }
      if (b % 2 === 1) {
        kickTimes.push(t + 3.5 * beat);
        events.push({ type: 'kick', bus: 'drums', part: 'Kick', timeSec: t + 3.5 * beat, gainDb: -13 });
      }
      if (b >= 6) for (const c of [1, 3]) events.push({ type: 'sample', instrument: 'clap', bus: 'drums', kind: 'composed', part: 'Clap', timeSec: t + c * beat, midi: 60, durationSec: 0.4, gainDb: -14, pan: 0.05, layer: 0 });
    }
    if (section !== 'intro' && b < bars - 1) {
      for (let e = 0; e < 8; e++) {
        const off = e % 2 === 1;
        events.push({ type: 'sample', instrument: 'hats', bus: 'drums', kind: 'composed', part: 'Hi-hats', timeSec: t + e * (beat / 2) + (off ? swing : 0), midi: 60, durationSec: 0.15, gainDb: off ? -22 : -27, pan: 0.25, layer: off ? 2 : 0 });
      }
    }
    if ((section === 'groove' && b >= 8) || section === 'arrival') {
      for (let s = 0; s < 16; s++) {
        events.push({ type: 'sample', instrument: 'shaker', bus: 'drums', kind: 'composed', part: 'Shaker', timeSec: t + s * (beat / 4), midi: 60, durationSec: 0.1, gainDb: s % 2 === 0 ? -30 : -27, pan: -0.3, layer: 0 });
      }
    }

    // The stars: a vibraphone arpeggio, eight notes a bar on the eighths; sixteen on the sixteenths in the
    // last bar of the break; in the arrival, the next eight stars answer on the off-sixteenths
    const chord = parseChord(p.chord);
    if (b === 15) {
      events.push(...sweepBar(stars, chord, mapping, t, beat / 4, 16, 'vibes', { gainDb: -11, durationSec: 0.6, layer: 0 }));
    } else if (b < bars - 1) {
      events.push(...sweepBar(stars, chord, mapping, t, beat / 2, 8, 'vibes', { gainDb: b < 2 ? -12 : -9, durationSec: 0.9, layer: section === 'arrival' ? 1 : 0 }));
      if (section === 'arrival') events.push(...sweepBar(stars, chord, mapping, t + beat / 4, beat / 2, 8, 'vibes', { gainDb: -15, durationSec: 0.6, layer: 0, offsetStars: 8 }));
    } else {
      events.push(...sweepBar(stars, chord, mapping, t, beat, 4, 'vibes', { gainDb: -10, durationSec: 2.4, layer: 0 }));
    }
  }
  events.push({ type: 'riser', bus: 'main', part: 'Riser', timeSec: 14 * bar, durationSec: 2 * bar, gainDb: -20 });

  return {
    id: 'ambient-beats',
    title: 'B. Ambient beats',
    summary: 'A warm pad and sub bass in F minor at 96 beats a minute, crisp drums, and the stars as a vibraphone arpeggio with a delay, building through a breakdown into an arrival.',
    bpm,
    beatsPerBar: 4,
    bars,
    durationSec: bars * bar + 3.5,
    key: 'F minor',
    chords: plan.map((p, i) => ({ bar: i + 1, startSec: i * bar, chord: p.chord, source: 'composed' })),
    events,
    buses: [
      { id: 'main', gainDb: 0, reverbSendDb: -16, delaySendDb: null },
      { id: 'pads', gainDb: 0, reverbSendDb: -8, delaySendDb: null, duckUnderKick: { depthDb: -5, releaseSec: 0.28 } },
      { id: 'drums', gainDb: 0, reverbSendDb: -20, delaySendDb: null },
      { id: 'data', gainDb: 0, reverbSendDb: -9, delaySendDb: -6 },
    ],
    room: 'synthetic-hall',
    roomReturnDb: -6,
    delay: { delaySec: 0.75 * beat, feedback: 0.38, wetDb: -6, lowpassHz: 3200 },
    parts: [
      { name: 'Pad', kind: 'composed', plays: 'the chords: three detuned saws a note through a low-pass that opens over the clip', source: 'synthesized in Web Audio' },
      { name: 'Sub bass', kind: 'composed', plays: 'the chord roots', source: 'synthesized in Web Audio' },
      { name: 'Kick', kind: 'composed', plays: 'a falling sine on beats 1 and 3', source: 'synthesized in Web Audio' },
      { name: 'Clap, hi-hats, shaker', kind: 'composed', plays: 'the backbeat and the eighth and sixteenth notes', source: VCSL_CREDIT },
      { name: 'Riser', kind: 'composed', plays: 'filtered noise rising into the arrival', source: 'synthesized in Web Audio' },
      { name: DATA_PART, kind: 'data', plays: 'vibraphone: each note is a catalog star on screen (how, below)', source: VCSL_CREDIT },
      { name: 'Room', kind: 'composed', plays: 'a synthetic hall (seeded noise decaying in 3.4 s below 2 kHz, 1.6 s above) and a dotted-eighth delay', source: 'synthesized in Web Audio' },
    ],
    dataMapping: mapping,
  };
}

/** The recording direction C plays under the stars (sources.json has its licence and SHA-256). */
export const RECORDING = {
  file: 'scottbuckley/sb_signaltonoise_nomelody.mp3',
  title: "Signal to Noise, the 'No Piano Melody' mix",
  credit: "'Signal to Noise' by Scott Buckley - released under CC-BY 4.0. www.scottbuckley.com.au",
  /** The excerpt starts at the measured downbeat nearest this: the build into the piece's climax. */
  approxFromSec: 203,
  bars: 14,
} as const;

/** What main.ts measured of the recording with analysis.ts, for the arrangement and the page. */
export interface RecordingAnalysis {
  readonly file: string;
  readonly bpm: number;
  readonly beatSec: number;
  readonly tempoStrength: number;
  readonly beatsPerBar: number;
  readonly key: { readonly name: string; readonly tonic: number; readonly mode: 'major' | 'minor'; readonly correlation: number };
  /** Start of the excerpt in the recording, at a measured downbeat. */
  readonly fromSec: number;
  readonly bars: readonly { readonly bar: number; readonly chord: string | null; readonly confident: boolean; readonly score: number; readonly margin: number }[];
  readonly statement: string;
}

/**
 * C. A professionally produced recording. The 'No Piano Melody' mix of Scott Buckley's Signal to
 * Noise (CC BY 4.0), 14 bars from the build into its climax, with the stars on a glockenspiel where
 * the piano melody was: on the recording's measured beat, inside each bar's measured chord, or on the
 * key's pentatonic notes where a bar's chord could not be measured with confidence.
 */
export function recordingArrangement(stars: readonly ScreenStar[], analysis: RecordingAnalysis): Arrangement {
  const beat = analysis.beatSec;
  const bar = beat * analysis.beatsPerBar;
  const mapping: StarNoteMapping = { ...DEFAULT_STAR_NOTE_MAPPING, lowMidi: 81, highMidi: 105 };
  const events: MusicEvent[] = [];
  const chords: BarChord[] = [];
  for (let b = 0; b < analysis.bars.length; b++) {
    const t = b * bar;
    const { set, source } = recordingBarSet(analysis.bars[b]!, analysis.key.tonic, analysis.key.mode, analysis.key.name);
    chords.push({ bar: b + 1, startSec: t, chord: set.name, source });
    // Quarter notes for two bars as the stars come in, then the full sweep on the eighths
    if (b < 2) events.push(...sweepBar(stars, set, mapping, t, beat, 4, 'glock', { gainDb: b === 0 ? -16 : -13, durationSec: 1.4, layer: 0 }));
    else events.push(...sweepBar(stars, set, mapping, t, beat / 2, 8, 'glock', { gainDb: -11, durationSec: 1.1, layer: b >= analysis.bars.length - 4 ? 1 : 0 }));
  }
  const length = analysis.bars.length * bar;
  return {
    id: 'recording',
    title: 'C. A recording, with the stars in its key',
    summary: `${RECORDING.title} by Scott Buckley, with the stars on a glockenspiel where its piano melody was, on its beat and inside its chords.`,
    bpm: analysis.bpm,
    beatsPerBar: analysis.beatsPerBar,
    bars: analysis.bars.length,
    durationSec: length + 2.5,
    key: analysis.key.name,
    chords,
    events,
    buses: [
      { id: 'recording', gainDb: 0, reverbSendDb: null, delaySendDb: null },
      { id: 'data', gainDb: 0, reverbSendDb: -9, delaySendDb: null },
    ],
    room: 'synthetic-hall',
    roomReturnDb: -8,
    delay: null,
    parts: [
      { name: 'Recording', kind: 'recording', plays: `${RECORDING.title}, an excerpt of ${analysis.bars.length} bars from ${analysis.fromSec.toFixed(1)} s, faded in and out`, source: RECORDING.credit },
      { name: DATA_PART, kind: 'data', plays: 'glockenspiel: each note is a catalog star on screen (how, below)', source: VCSL_CREDIT },
      { name: 'Room', kind: 'composed', plays: "a synthetic hall on the stars' notes only", source: 'synthesized in Web Audio' },
    ],
    dataMapping: mapping,
    recording: { file: RECORDING.file, fromSec: analysis.fromSec, durationSec: length + 1, fadeInSec: 1.5, fadeOutSec: 4, gainDb: 0, credit: RECORDING.credit },
  };
}

/** The pitch set a data note may use in a bar of the recording: its measured chord, or the key's pentatonic notes. */
export function recordingBarSet(measured: { chord: string | null; confident: boolean }, keyTonic: number, keyMode: 'major' | 'minor', keyName: string): { set: PitchSet; source: BarChord['source'] } {
  if (measured.confident && measured.chord) return { set: parseChord(measured.chord), source: 'measured' };
  const mode = keyMode === 'minor' ? 'minorPentatonic' : 'majorPentatonic';
  return { set: pitchSet(`${keyName} pentatonic`, keyTonic, scalePitchClasses(keyTonic, mode)), source: 'pentatonic fallback' };
}

/** True when every data note of an arrangement is a tone of its bar's pitch set. */
export function dataNotesInChords(arrangement: Arrangement): { notes: number; outside: number } {
  let notes = 0;
  let outside = 0;
  for (const e of arrangement.events) {
    if (e.type !== 'sample' || e.kind !== 'data' || !e.star) continue;
    notes++;
    if (!barPitchSet(e.star.chord).pitchClasses.includes(pitchClassOf(e.midi))) outside++;
  }
  return { notes, outside };
}

/**
 * A bar's pitch set from its name: a chord symbol, or a key's pentatonic set as the recording's
 * unmeasured bars name it (`A minor pentatonic`). A pentatonic name of another form has no notes.
 */
export function barPitchSet(name: string): PitchSet {
  if (!name.endsWith('pentatonic')) return parseChord(name);
  const match = /^([A-G][#b]?) (major|minor) pentatonic$/.exec(name);
  if (!match) return pitchSet(name, 0, []);
  const tonic = parseChord(match[1]!).root;
  return pitchSet(name, tonic, scalePitchClasses(tonic, match[2] === 'minor' ? 'minorPentatonic' : 'majorPentatonic'));
}
