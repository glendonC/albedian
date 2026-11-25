// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { INSTRUMENTS, RECORDING, type Arrangement, type InstrumentDef, type RecordingAnalysis } from '../directions';
import {
  SOUNDTRACK,
  SOUNDTRACK_MOMENTS,
  SOUNDTRACK_TIMINGS,
  SoundtrackDirector,
  pieceCreditLines,
  type DirectedPiece,
  type DirectorCommand,
  type SoundtrackMoment,
} from '../soundtrack';
import type { ScreenStar } from '../starNotes';

/**
 * Two made-up complete stars, enough for each piece to play its data part. The credits depend on
 * which instruments a piece uses, not on star values; musicLayer.test.ts plays real tile rows.
 */
const STARS: ScreenStar[] = [
  { id: 'test-a', teffK: 5_000, luminositySolar: 1, distancePc: 10, screenX: -0.4, screenY: 0.2 },
  { id: 'test-b', teffK: 12_000, luminositySolar: 500, distancePc: 100, screenX: 0.5, screenY: -0.3 },
];

/** A made-up analysis of the shape main.ts measures, enough to build the recording piece. */
const ANALYSIS: RecordingAnalysis = {
  file: RECORDING.file,
  bpm: 75,
  beatSec: 0.8,
  tempoStrength: 0.5,
  beatsPerBar: 4,
  key: { name: 'D major', tonic: 2, mode: 'major', correlation: 0.8 },
  fromSec: 203,
  bars: Array.from({ length: RECORDING.bars }, (_, i) => ({ bar: i + 1, chord: i % 2 ? 'D' : null, confident: i % 2 === 1, score: 0.8, margin: 0.1 })),
  statement: 'test analysis',
};

const SOURCES = { recordingAnalysis: async () => ANALYSIS };

const T = SOUNDTRACK_TIMINGS;

describe('the soundtrack registry', () => {
  it("holds the three pieces with the moments the lead mapped them to", () => {
    expect(SOUNDTRACK.map((piece) => [piece.id, piece.moments])).toEqual([
      ['organ-pulse', ['arrive', 'back_to_moment']],
      ['ambient-beats', ['reveal', 'look_around', 'star_card']],
      ['recording', ['travel']],
    ]);
    expect(SOUNDTRACK.map((piece) => piece.title)).toEqual(['Organ and pulse', 'Ambient beats', 'Signal to Noise']);
  });

  it('has a piece for every moment, and no piece named like a setting', () => {
    for (const moment of SOUNDTRACK_MOMENTS) expect(SOUNDTRACK.some((piece) => piece.moments.includes(moment)), moment).toBe(true);
    for (const piece of SOUNDTRACK) expect(['soundtrack', 'off']).not.toContain(piece.id);
    expect(new Set(SOUNDTRACK.map((piece) => piece.id)).size).toBe(SOUNDTRACK.length);
  });

  it('times the gaps, the settling, the fades and the entry as agreed', () => {
    expect(T).toEqual({ gapMinSec: 20, gapMaxSec: 60, settleSec: 1.5, fadeSec: 3, entrySec: 2 });
  });

  it('credits every instrument source and recording each piece plays, and nothing it does not', async () => {
    for (const piece of SOUNDTRACK) {
      const arrangement: Arrangement = await piece.arrange(STARS, SOURCES);
      expect(arrangement.id).toBe(piece.id);
      const used = new Set<string>();
      for (const event of arrangement.events) if (event.type === 'sample') used.add((INSTRUMENTS[event.instrument] as InstrumentDef).credit);
      if (arrangement.recording) used.add(arrangement.recording.credit);
      const lines = pieceCreditLines(piece.id);
      for (const credit of used) expect(lines, `${piece.id} credits ${credit}`).toContain(credit);
      for (const line of lines) {
        if (line === 'Music: Albedian') continue;
        expect(used, `${piece.id} plays what ${line} credits`).toContain(line);
      }
    }
  });

  it('declares the composed music and the recorded track each piece plays, as its arrangement plays them', async () => {
    for (const piece of SOUNDTRACK) {
      const arrangement = await piece.arrange(STARS, SOURCES);
      // Synthesized events, and samples marked composed, are composed music; the data notes are not
      const composedMusic = arrangement.events.some((event) => event.type !== 'sample' || event.kind === 'composed');
      expect(piece.alongside, piece.id).toEqual({ composedMusic, recordedTrack: arrangement.recording !== undefined });
    }
    expect(SOUNDTRACK.find((piece) => piece.id === 'recording')!.alongside).toEqual({ composedMusic: false, recordedTrack: true });
  });

  it('credits the composed pieces as Albedian music and the recording as its own, with no em dash', () => {
    expect(pieceCreditLines('organ-pulse')[0]).toBe('Music: Albedian');
    expect(pieceCreditLines('ambient-beats')[0]).toBe('Music: Albedian');
    expect(pieceCreditLines('recording')).not.toContain('Music: Albedian');
    expect(pieceCreditLines('recording')[0]).toBe(RECORDING.credit);
    expect(pieceCreditLines('organ-pulse').join(' ')).toContain('Simon Dalzell of Ivy Audio');
    expect(pieceCreditLines('not-a-piece')).toEqual([]);
    for (const piece of SOUNDTRACK) for (const line of pieceCreditLines(piece.id)) expect(line).not.toMatch(/\u2014/);
  });
});

/** Plays the director's commands as the music layer would: a play starts its piece at once. */
class Stage {
  readonly plays: { piece: string; atSec: number; setting: string; last: string | null }[] = [];
  readonly fades: { piece: string; atSec: number; fadeSec: number; moment: SoundtrackMoment; setting: string; settingChanged: boolean }[] = [];
  /** Each prepare the director asked for, and when its piece is ready (after the stage's fetch). */
  readonly prepares: { piece: string; askedSec: number; readyAt: number }[] = [];
  /** How long a prepare's fetch takes; the layer schedules this far ahead of its clock. */
  fetchSec = 0.2;
  leadSec = 0.25;
  /** Natural ends: the end time each piece that finished on its own had been given. */
  readonly ends: number[] = [];
  /** When a moment took effect or the setting changed: only these may move a gap off its draw. */
  readonly changes: number[] = [];
  now = 0;
  sounding: string | null = null;
  private lastStarted: string | null = null;
  private settingChanged = false;

  constructor(
    readonly director: SoundtrackDirector,
    private readonly lengthSec: (piece: string) => number
  ) {}

  run(commands: readonly DirectorCommand[]): void {
    for (const command of commands) {
      if (command.kind === 'fade') {
        this.fades.push({ piece: command.piece, atSec: command.atSec, fadeSec: command.fadeSec, moment: this.director.state.moment, setting: this.director.state.setting, settingChanged: this.settingChanged });
        this.sounding = null;
        continue;
      }
      if (command.kind === 'prepare') {
        this.prepares.push({ piece: command.piece, askedSec: this.now, readyAt: this.now + this.fetchSec });
        continue;
      }
      // A play never overlaps a piece that still sounds
      expect(this.sounding, `play ${command.piece} at ${command.atSec} over ${this.sounding}`).toBeNull();
      this.plays.push({ piece: command.piece, atSec: command.atSec, setting: this.director.state.setting, last: this.lastStarted });
      this.sounding = command.piece;
      this.lastStarted = command.piece;
      this.director.started(command.piece, command.atSec, command.atSec + this.lengthSec(command.piece), BEAT[command.piece] ?? null);
    }
    this.settingChanged = false;
  }

  private watchEnd(call: () => readonly DirectorCommand[]): void {
    const before = this.director.state;
    const commands = call();
    if (this.director.state.moment !== before.moment) this.changes.push(this.now);
    if (before.phase === 'playing' && this.director.state.phase !== 'playing' && this.director.state.phase !== 'fading' && before.untilSec !== null && this.now >= before.untilSec) {
      this.ends.push(before.untilSec);
      this.sounding = null;
    }
    this.run(commands);
  }

  start(): void {
    this.run(this.director.start(this.now));
  }
  moment(moment: SoundtrackMoment): void {
    this.watchEnd(() => this.director.setMoment(moment, this.now));
  }
  /** The restored compare arrived: like a moment taking effect, it may move a gap off its draw. */
  arrive(): void {
    this.changes.push(this.now);
    this.watchEnd(() => this.director.arrive(this.now));
  }
  setting(setting: string): void {
    this.settingChanged = true;
    this.changes.push(this.now);
    this.watchEnd(() => this.director.setSetting(setting, this.now));
  }
  private readied = 0;
  /** Moves the clock to `nowSec`, waking the director at every time it asked for on the way, and telling it each
   * prepared piece when its fetch is done, in time order. */
  advanceTo(nowSec: number): void {
    for (let guard = 0; guard < 1000; guard++) {
      const wake = this.director.nextWakeSec() ?? Infinity;
      const ready = this.prepares[this.readied]?.readyAt ?? Infinity;
      if (Math.min(wake, ready) > nowSec) break;
      if (ready <= wake) {
        const prepared = this.prepares[this.readied++]!;
        this.now = Math.max(this.now, ready);
        this.watchEnd(() => this.director.prepared(prepared.piece, this.now, this.leadSec));
        continue;
      }
      this.now = Math.max(this.now, wake);
      this.watchEnd(() => this.director.tick(this.now));
    }
    this.now = Math.max(this.now, nowSec);
    this.watchEnd(() => this.director.tick(this.now));
  }
  advance(seconds: number): void {
    this.advanceTo(this.now + seconds);
  }
}

const LENGTH: Record<string, number> = { 'organ-pulse': 53, 'ambient-beats': 53.5, recording: 40 };
/** Each piece's beat: Organ and pulse at 60 a minute, Ambient beats at 96, the recording's measured 66.7. */
const BEAT: Record<string, number> = { 'organ-pulse': 1, 'ambient-beats': 0.625, recording: 0.9 };
const lengthOf = (piece: string) => LENGTH[piece] ?? 30;

function stage(options: { moment?: SoundtrackMoment; setting?: string; random?: () => number; pieces?: readonly DirectedPiece[] } = {}) {
  const director = new SoundtrackDirector(options.pieces ?? SOUNDTRACK, {
    moment: options.moment ?? 'arrive',
    setting: options.setting ?? 'soundtrack',
    random: options.random ?? (() => 0),
  });
  return new Stage(director, lengthOf);
}

describe('the soundtrack director, through the moments of a visit', () => {
  it('plays the moment piece on Audio on, fades it when a moment it does not fit has held, and plays that moment piece after the fade', () => {
    const s = stage({ moment: 'arrive' });
    s.start();
    expect(s.plays.map((p) => p.piece)).toEqual(['organ-pulse']);

    // Launch: the visit moves on to looking around, which the organ does not fit
    s.advance(10);
    s.moment('look_around');
    s.advanceTo(10 + T.settleSec - 0.1);
    expect(s.fades).toEqual([]);
    s.advanceTo(10 + T.settleSec);
    expect(s.fades.map((f) => [f.piece, f.atSec])).toEqual([['organ-pulse', 10 + T.settleSec]]);
    s.advanceTo(10 + T.settleSec + T.fadeSec - 0.1);
    expect(s.plays).toHaveLength(1);
    s.advanceTo(10 + T.settleSec + T.fadeSec);
    expect(s.plays.at(-1)).toMatchObject({ piece: 'ambient-beats', atSec: 10 + T.settleSec + T.fadeSec });

    // The star card: ambient beats fits it too, so it keeps playing
    s.advance(5);
    s.moment('star_card');
    s.advance(5);
    expect(s.fades).toHaveLength(1);
    expect(s.sounding).toBe('ambient-beats');

    // Travel: the recording
    s.moment('travel');
    s.advance(T.settleSec + T.fadeSec);
    expect(s.fades.at(-1)!.piece).toBe('ambient-beats');
    expect(s.plays.at(-1)!.piece).toBe('recording');

    // Back to Moment: the organ again
    s.advance(4);
    s.moment('back_to_moment');
    s.advance(T.settleSec + T.fadeSec);
    expect(s.plays.map((p) => p.piece)).toEqual(['organ-pulse', 'ambient-beats', 'recording', 'organ-pulse']);
  });

  it('does not react to a moment that changes back before it has held', () => {
    // The star card and looking around settle (the crossing moments answer on the beat instead: music-crossing-1)
    const s = stage({ moment: 'back_to_moment' });
    s.start();
    s.advance(5);
    s.moment('star_card');
    s.advance(1);
    s.moment('back_to_moment');
    s.advance(10);
    expect(s.fades).toEqual([]);
    expect(s.plays.map((p) => p.piece)).toEqual(['organ-pulse']);
  });

  it('leaves a silence gap after a piece ends, drawn between 20 and 60 s', () => {
    for (const [draw, gap] of [[0, 20], [0.5, 40], [0.999, 59.96]] as const) {
      const s = stage({ moment: 'look_around', random: () => draw });
      s.start();
      s.advance(53.5);
      expect(s.ends).toEqual([53.5]);
      expect(s.director.state).toMatchObject({ phase: 'gap', piece: null });
      s.advanceTo(53.5 + gap - 0.05);
      expect(s.plays).toHaveLength(1);
      s.advanceTo(53.5 + gap + 0.05);
      expect(s.plays).toHaveLength(2);
      expect(s.plays[1]!.atSec).toBeCloseTo(53.5 + gap, 6);
    }
  });

  it('never plays the piece that just played again: after the gap it plays another, preferring one that fits', () => {
    // Look around fits only ambient beats; the next piece is one of the others
    for (const draw of [0, 0.5, 0.99]) {
      const s = stage({ moment: 'look_around', random: () => draw });
      s.start();
      s.advance(53.5 + 60.1);
      expect(s.plays.map((p) => p.piece)[0]).toBe('ambient-beats');
      expect(['organ-pulse', 'recording']).toContain(s.plays[1]!.piece);
    }
    // A moment with two pieces that fit: the one not just played
    const pieces: DirectedPiece[] = [...SOUNDTRACK, { id: 'test-piece', moments: ['look_around'] }];
    for (const draw of [0, 0.5, 0.99]) {
      const s = stage({ moment: 'look_around', random: () => draw, pieces });
      s.start();
      const first = s.plays[0]!.piece;
      expect(['ambient-beats', 'test-piece']).toContain(first);
      s.advance(lengthOf(first) + 60.1);
      expect(s.plays[1]!.piece).toBe(first === 'ambient-beats' ? 'test-piece' : 'ambient-beats');
    }
  });

  it('picks a piece added to the registry for the moments it declares', () => {
    const pieces: DirectedPiece[] = [...SOUNDTRACK, { id: 'test-piece', moments: ['reveal'] }];
    // The added piece fits the reveal beside ambient beats; a draw near 1 picks the last fitting piece in registry order
    const s = stage({ moment: 'reveal', random: () => 0.99, pieces });
    s.start();
    expect(s.plays[0]!.piece).toBe('test-piece');
    s.advance(lengthOf('test-piece') + 60.1);
    expect(s.plays[1]!.piece).toBe('ambient-beats');
  });

  it('starts the new moment piece 2 s into a gap, unless it is the piece that just played', () => {
    const s = stage({ moment: 'look_around' });
    s.start();
    s.advance(53.5 + 5);
    expect(s.director.state.phase).toBe('gap');
    s.moment('travel');
    s.advance(T.settleSec);
    s.advance(T.entrySec - 0.05);
    expect(s.plays).toHaveLength(1);
    s.advance(0.05);
    expect(s.plays.at(-1)).toMatchObject({ piece: 'recording' });
    expect(s.plays.at(-1)!.atSec).toBeCloseTo(53.5 + 5 + T.settleSec + T.entrySec, 6);

    // In a gap after the recording, looking around has a fitting piece that did not just play: it enters early
    const t = stage({ moment: 'look_around', random: () => 0 });
    t.start();
    t.moment('travel');
    t.advance(T.settleSec + T.fadeSec);
    expect(t.sounding).toBe('recording');
    t.advance(40);
    expect(t.director.state.phase).toBe('gap');
    const gapEnd = t.director.state.untilSec!;
    t.moment('look_around');
    t.advance(T.settleSec + T.entrySec + 1);
    expect(t.plays.at(-1)!.piece).toBe('ambient-beats');
    expect(t.plays.at(-1)!.atSec).toBeLessThan(gapEnd);

    // A moment whose only fitting piece is the one that just played: the gap runs its course
    const u = stage({ moment: 'travel', random: () => 0, pieces: [{ id: 'a', moments: ['travel', 'star_card'] }, { id: 'b', moments: ['look_around'] }] });
    u.start();
    u.advance(30 + 1);
    expect(u.director.state.phase).toBe('gap');
    const until = u.director.state.untilSec!;
    u.moment('star_card');
    u.advance(T.settleSec + T.entrySec + 1);
    expect(u.plays).toHaveLength(1);
    expect(u.director.state).toMatchObject({ phase: 'gap', untilSec: until });
  });

  it('plays only the pinned piece, again after each gap, whatever the moment', () => {
    const s = stage({ moment: 'arrive', setting: 'recording', random: () => 0 });
    s.start();
    s.moment('look_around');
    s.advance(10);
    s.moment('back_to_moment');
    s.advance(40 + 20 + 40 + 20 + 1);
    expect(s.plays.map((p) => p.piece)).toEqual(['recording', 'recording', 'recording']);
    expect(s.fades).toEqual([]);
  });

  it('turns off with a fade and plays nothing until the setting changes, then answers at once', () => {
    const s = stage({ moment: 'look_around' });
    s.start();
    s.advance(10);
    s.setting('off');
    expect(s.fades.map((f) => [f.piece, f.atSec])).toEqual([['ambient-beats', 10]]);
    s.advance(600);
    expect(s.plays).toHaveLength(1);
    expect(s.director.state.phase).toBe('silent');
    s.setting('soundtrack');
    // Ambient beats is the last piece played, so the soundtrack starts with another
    expect(s.plays).toHaveLength(2);
    expect(s.plays[1]!.atSec).toBe(610);
    expect(s.plays[1]!.piece).not.toBe('ambient-beats');
  });

  it('keeps a piece that the new setting still allows, and fades one it does not', () => {
    const s = stage({ moment: 'look_around' });
    s.start();
    s.setting('ambient-beats');
    expect(s.fades).toEqual([]);
    s.setting('soundtrack');
    expect(s.fades).toEqual([]);
    s.setting('organ-pulse');
    expect(s.fades.map((f) => f.piece)).toEqual(['ambient-beats']);
    s.advance(T.fadeSec);
    expect(s.plays.map((p) => p.piece)).toEqual(['ambient-beats', 'organ-pulse']);
  });

  it('starts nothing before Audio on and nothing after Audio off, and keeps the moment meanwhile', () => {
    const director = new SoundtrackDirector(SOUNDTRACK, { moment: 'arrive', random: () => 0 });
    expect(director.setMoment('travel', 0)).toEqual([]);
    expect(director.state).toMatchObject({ phase: 'stopped', moment: 'travel' });
    expect(director.nextWakeSec()).toBeNull();
    expect(director.start(5)).toEqual([{ kind: 'play', piece: 'recording', atSec: 5 }]);
    director.started('recording', 5, 45);
    director.stop();
    expect(director.state).toMatchObject({ phase: 'stopped', piece: null });
    expect(director.tick(100)).toEqual([]);
    expect(director.nextWakeSec()).toBeNull();
  });

  it('plays the one piece again when it is the only piece there is', () => {
    const s = stage({ moment: 'travel', pieces: [{ id: 'only', moments: ['travel'] }] });
    s.start();
    s.advance(30 + 60.1);
    expect(s.plays.map((p) => p.piece)).toEqual(['only', 'only']);
  });
});

type Step =
  | { readonly kind: 'moment'; readonly moment: SoundtrackMoment; readonly dt: number }
  | { readonly kind: 'setting'; readonly setting: string; readonly dt: number }
  | { readonly kind: 'arrive'; readonly dt: number }
  | { readonly kind: 'wait'; readonly dt: number };

const SETTINGS = ['soundtrack', 'off', ...SOUNDTRACK.map((piece) => piece.id)];

const stepArb: fc.Arbitrary<Step> = fc.oneof(
  { weight: 4, arbitrary: fc.record({ kind: fc.constant('moment' as const), moment: fc.constantFrom(...SOUNDTRACK_MOMENTS), dt: fc.double({ min: 0, max: 8, noNaN: true }) }) },
  { weight: 1, arbitrary: fc.record({ kind: fc.constant('setting' as const), setting: fc.constantFrom(...SETTINGS), dt: fc.double({ min: 0, max: 8, noNaN: true }) }) },
  { weight: 3, arbitrary: fc.record({ kind: fc.constant('wait' as const), dt: fc.double({ min: 0, max: 120, noNaN: true }) }) },
  { weight: 1, arbitrary: fc.record({ kind: fc.constant('arrive' as const), dt: fc.double({ min: 0, max: 60, noNaN: true }) }) }
);

/** Random draws for the director, cycled. */
const drawsArb = fc.array(fc.double({ min: 0, max: 1, maxExcluded: true, noNaN: true }), { minLength: 1, maxLength: 16 });

function cycle(draws: readonly number[]): () => number {
  let i = 0;
  return () => draws[i++ % draws.length]!;
}

/** A visit with a long stay in look around, the case that exercises the no-repeat rule. */
const LONG_STAY: [Step[], number[]] = [[{ kind: 'moment', moment: 'look_around', dt: 0 }, { kind: 'wait', dt: 120 }, { kind: 'wait', dt: 120 }, { kind: 'wait', dt: 120 }], [0.1]];
/** Moments that change in a gap, and a setting turned off and on. */
const BUSY: [Step[], number[]] = [
  [
    { kind: 'wait', dt: 60 },
    { kind: 'moment', moment: 'travel', dt: 0 },
    { kind: 'wait', dt: 2 },
    { kind: 'setting', setting: 'off', dt: 1 },
    { kind: 'setting', setting: 'soundtrack', dt: 3 },
    { kind: 'moment', moment: 'star_card', dt: 0.5 },
    { kind: 'wait', dt: 90 },
  ],
  [0.9, 0.2],
];

// compare-music-1 (DECIDED-BY-LEAD 12:41 on 24 Nov, MOTION motion-sound-gaps-24nov item four): the restored compare's
// arrival counts as a reveal for the music, so a silence shortens to its 2 s entry, as it does for a settled moment; no
// new sound, and a piece that plays is left as it is.
describe("the restored compare's arrival", () => {
  it('counts in a silence as a reveal that has held: the gap shortens to its 2 s entry and a reveal piece plays', () => {
    const s = stage({ moment: 'back_to_moment', random: () => 0.999 });
    s.start();
    s.advance(53 + 5);
    expect(s.director.state).toMatchObject({ phase: 'gap', moment: 'back_to_moment' });
    const drawn = s.director.state.untilSec!;
    expect(drawn - 53).toBeGreaterThan(50);
    s.arrive();
    expect(s.director.state).toMatchObject({ phase: 'gap', moment: 'reveal', untilSec: 58 + T.entrySec });
    s.advance(T.entrySec - 0.05);
    expect(s.plays).toHaveLength(1);
    s.advance(0.05);
    expect(s.plays.at(-1)).toMatchObject({ piece: 'ambient-beats' });
    expect(s.plays.at(-1)!.atSec).toBeCloseTo(58 + T.entrySec, 6);
  });

  it('never lengthens a silence that ends sooner than its entry', () => {
    const s = stage({ moment: 'back_to_moment', random: () => 0 });
    s.start();
    s.advance(53 + 19);
    const until = s.director.state.untilSec!;
    expect(until - s.now).toBeLessThan(T.entrySec);
    s.arrive();
    expect(s.director.state.untilSec).toBe(until);
  });

  it('leaves a piece that plays, fades or starts as it is, and the moment with it', () => {
    const s = stage({ moment: 'back_to_moment' });
    s.start();
    s.advance(10);
    s.arrive();
    expect(s.director.state).toMatchObject({ phase: 'playing', piece: 'organ-pulse', moment: 'back_to_moment' });
    expect(s.fades).toHaveLength(0);
    s.moment('star_card');
    s.advance(T.settleSec + 0.5);
    expect(s.director.state.phase).toBe('fading');
    s.arrive();
    expect(s.director.state).toMatchObject({ phase: 'fading', moment: 'star_card' });
    expect(s.fades).toHaveLength(1);
  });

  it('lets the gap run its course when the only reveal piece is the one that just played', () => {
    const s = stage({ moment: 'look_around', random: () => 0, pieces: [{ id: 'a', moments: ['reveal', 'look_around'] }, { id: 'b', moments: ['travel'] }] });
    s.start();
    s.advance(30 + 1);
    const until = s.director.state.untilSec!;
    s.arrive();
    expect(s.director.state).toMatchObject({ phase: 'gap', untilSec: until });
    s.advance(T.entrySec + 1);
    expect(s.plays).toHaveLength(1);
  });

  it('does nothing with the music off, pinned or before Audio on', () => {
    for (const setting of ['off', 'organ-pulse']) {
      const s = stage({ moment: 'back_to_moment', setting, random: () => 0.999 });
      s.start();
      s.advance(60);
      const before = s.director.state;
      s.arrive();
      expect(s.director.state).toEqual(before);
    }
    const quiet = stage({ moment: 'back_to_moment' });
    expect(quiet.director.arrive(5)).toEqual([]);
    expect(quiet.director.state).toMatchObject({ phase: 'stopped', moment: 'back_to_moment' });
  });

  it('keeps a moment change still settling, which takes effect when it has held', () => {
    const s = stage({ moment: 'back_to_moment', random: () => 0.999 });
    s.start();
    s.advance(53 + 5);
    s.moment('star_card');
    s.advance(0.5);
    s.arrive();
    expect(s.director.state.moment).toBe('reveal');
    s.advance(T.settleSec);
    expect(s.director.state.moment).toBe('star_card');
  });
});

// music-crossing-1 (DECIDED-BY-LEAD 12:55 on 24 Nov, UIUX-sound-READY.md, MOTION-music-crossing-PROPOSAL.md): the reveal,
// Go there (travel) and Back to Moment cross on the playing piece's next beat: the incoming piece is fetched at once
// so no gap is heard, enters at full on that beat, and the outgoing fades across one beat. The star card and looking
// around keep the settle, so quick presses do not flap.
describe('the crossing on the beat', () => {
  it('answers Back to Moment on the playing piece\'s first beat after the incoming piece is ready, with no settle', () => {
    const s = stage({ moment: 'look_around', random: () => 0 });
    s.start();
    s.advance(10.1);
    expect(s.plays).toEqual([expect.objectContaining({ piece: 'ambient-beats', atSec: 0 })]);
    s.moment('back_to_moment');
    // Asked for at once, no 1.5 s wait; the music keeps playing meanwhile
    expect(s.prepares).toEqual([{ piece: 'organ-pulse', askedSec: 10.1, readyAt: 10.1 + s.fetchSec }]);
    expect(s.fades).toEqual([]);
    expect(s.director.state).toMatchObject({ phase: 'playing', piece: 'ambient-beats', moment: 'look_around' });
    s.advance(0.2);
    // Ready at 10.3, schedulable from 10.55: Ambient beats' next beat (0.625 s from its start at 0) is 10.625
    expect(s.fades).toEqual([expect.objectContaining({ piece: 'ambient-beats', atSec: 10.625, fadeSec: 0.625, moment: 'back_to_moment' })]);
    expect(s.plays.at(-1)).toMatchObject({ piece: 'organ-pulse', atSec: 10.625 });
    expect(s.director.state).toMatchObject({ phase: 'playing', piece: 'organ-pulse', moment: 'back_to_moment', untilSec: 10.625 + 53 });
  });

  it('answers Go there and the reveal the same way, on the beat of the piece that plays', () => {
    const s = stage({ moment: 'back_to_moment', random: () => 0 });
    s.start();
    s.advance(20.3);
    s.moment('travel');
    s.advance(s.fetchSec);
    // Organ and pulse beats each second: ready at 20.5, schedulable from 20.75, so 21
    expect(s.fades.at(-1)).toMatchObject({ piece: 'organ-pulse', atSec: 21, fadeSec: 1 });
    expect(s.plays.at(-1)).toMatchObject({ piece: 'recording', atSec: 21 });
    s.advance(5);
    s.moment('reveal');
    s.advance(s.fetchSec);
    // The recording's beat is 0.9 s from 21: asked at 25.5, ready at 25.7, schedulable from 25.95, so 21 + 6 * 0.9
    expect(s.fades.at(-1)).toMatchObject({ piece: 'recording', fadeSec: 0.9 });
    expect(s.fades.at(-1)!.atSec).toBeCloseTo(26.4, 9);
    expect(s.plays.at(-1)).toMatchObject({ piece: 'ambient-beats' });
    expect(s.plays.at(-1)!.atSec).toBeCloseTo(26.4, 9);
  });

  it('waits for a slow fetch: the crossing is the first beat after the piece is ready, never a gap', () => {
    const s = stage({ moment: 'look_around', random: () => 0 });
    s.fetchSec = 1.4;
    s.start();
    s.advance(10.1);
    s.moment('back_to_moment');
    s.advance(1.0);
    expect(s.fades).toEqual([]);
    expect(s.sounding).toBe('ambient-beats');
    s.advance(0.4);
    // Ready at 11.5, schedulable from 11.75: the beat at 11.875
    expect(s.fades.at(-1)).toMatchObject({ atSec: 11.875 });
    expect(s.plays.at(-1)).toMatchObject({ piece: 'organ-pulse', atSec: 11.875 });
  });

  it('keeps the settle and the 3 s fade for the star card and looking around', () => {
    const s = stage({ moment: 'back_to_moment', random: () => 0 });
    s.start();
    s.advance(10);
    s.moment('star_card');
    expect(s.prepares).toEqual([]);
    s.advance(T.settleSec);
    expect(s.fades.at(-1)).toMatchObject({ piece: 'organ-pulse', atSec: 10 + T.settleSec, fadeSec: T.fadeSec });
    expect(s.prepares).toEqual([]);
  });

  it('takes a crossing moment the playing piece fits at once, crossing nothing', () => {
    const s = stage({ moment: 'look_around', random: () => 0 });
    s.start();
    s.advance(10);
    s.moment('reveal');
    expect(s.director.state).toMatchObject({ phase: 'playing', piece: 'ambient-beats', moment: 'reveal' });
    s.advance(5);
    expect([s.prepares, s.fades]).toEqual([[], []]);
  });

  it('crosses nothing for a moment taken back before its piece is ready, and only the last of two', () => {
    const s = stage({ moment: 'back_to_moment', random: () => 0 });
    s.start();
    s.advance(10);
    s.moment('travel');
    s.advance(0.1);
    s.moment('back_to_moment');
    s.advance(2);
    expect(s.prepares).toHaveLength(1);
    expect([s.fades, s.plays.length]).toEqual([[], 1]);
    expect(s.director.state).toMatchObject({ piece: 'organ-pulse', moment: 'back_to_moment' });
    s.moment('travel');
    s.advance(0.1);
    s.moment('reveal');
    s.advance(1);
    // The recording's prepare came back first and crossed nothing; the reveal's piece crossed
    expect(s.prepares.map((prepare) => prepare.piece)).toEqual(['recording', 'recording', 'ambient-beats']);
    expect(s.plays.map((play) => play.piece)).toEqual(['organ-pulse', 'ambient-beats']);
    expect(s.fades).toHaveLength(1);
  });

  it('plays the prepared piece at once if the playing piece ended before its beat came', () => {
    const s = stage({ moment: 'back_to_moment', random: () => 0 });
    s.start();
    s.advance(52.9);
    s.moment('travel');
    s.advance(s.fetchSec);
    // Organ and pulse ended at 53: a silence, so the recording enters when it can, with nothing to fade
    expect(s.fades).toEqual([]);
    expect(s.plays.at(-1)).toMatchObject({ piece: 'recording' });
    expect(s.plays.at(-1)!.atSec).toBeCloseTo(52.9 + s.fetchSec + s.leadSec, 9);
  });

  it("crosses at the playing piece's own end when it ends before its next beat", () => {
    const s = stage({ moment: 'look_around', random: () => 0 });
    s.start();
    s.advance(52.9);
    s.moment('back_to_moment');
    s.advance(s.fetchSec);
    // Ambient beats ends at 53.5, off its 0.625 s grid: its next beat after 53.35 would be 53.75, past its end
    expect(s.fades.at(-1)).toMatchObject({ piece: 'ambient-beats', atSec: 53.5 });
    expect(s.plays.at(-1)).toMatchObject({ piece: 'organ-pulse', atSec: 53.5 });
  });

  it('keeps today\'s rule in a silence, while a piece starts or fades, with the music pinned, and with no beat known', () => {
    // A silence: the settle, then the entry
    const s = stage({ moment: 'look_around', random: () => 0 });
    s.start();
    s.advance(53.5 + 5);
    s.moment('travel');
    expect(s.prepares).toEqual([]);
    s.advance(T.settleSec + T.entrySec + 0.01);
    expect(s.plays.at(-1)).toMatchObject({ piece: 'recording' });
    expect(s.plays.at(-1)!.atSec).toBeCloseTo(53.5 + 5 + T.settleSec + T.entrySec, 9);
    // Pinned: the pin plays whatever the moment
    const pinned = stage({ moment: 'look_around', setting: 'ambient-beats' });
    pinned.start();
    pinned.advance(10);
    pinned.moment('travel');
    pinned.advance(5);
    expect([pinned.prepares, pinned.fades]).toEqual([[], []]);
    // A piece with no beat: the settle and the fade
    const unbeaten = stage({ moment: 'look_around', random: () => 0, pieces: [{ id: 'a', moments: ['look_around'] }, { id: 'b', moments: ['travel'] }] });
    unbeaten.start();
    unbeaten.advance(5);
    unbeaten.moment('travel');
    expect(unbeaten.prepares).toEqual([]);
    unbeaten.advance(T.settleSec);
    expect(unbeaten.fades.at(-1)).toMatchObject({ piece: 'a', fadeSec: T.fadeSec });
  });
});

describe('the soundtrack director, over any visit', () => {
  it('keeps its rules whatever the moments, settings and waits', () => {
    fc.assert(
      fc.property(fc.array(stepArb, { maxLength: 40 }), drawsArb, (steps, draws) => {
        const s = stage({ moment: 'arrive', random: cycle(draws) });
        s.start();
        for (const step of steps) {
          s.advance(step.dt);
          if (step.kind === 'moment') s.moment(step.moment);
          if (step.kind === 'setting') s.setting(step.setting);
          if (step.kind === 'arrive') s.arrive();
        }
        s.advance(200);

        for (const play of s.plays) {
          // Off plays nothing; a pin plays only its piece
          expect(play.setting).not.toBe('off');
          if (play.setting !== 'soundtrack') expect(play.piece).toBe(play.setting);
          // Unpinned, never the piece that just played (three pieces, so another always exists)
          else expect(play.piece).not.toBe(play.last);
        }
        for (const fade of s.fades) {
          // A fade answers a setting change, or a moment the piece does not fit
          if (fade.settingChanged) continue;
          expect(fade.setting).toBe('soundtrack');
          expect(SOUNDTRACK.find((piece) => piece.id === fade.piece)!.moments).not.toContain(fade.moment);
          // Across one beat of the piece that fades on a crossing, or the settle's 3 s
          expect([T.fadeSec, BEAT[fade.piece]]).toContain(fade.fadeSec);
        }
        for (const end of s.ends) {
          const next = s.plays.find((play) => play.atSec >= end);
          if (!next) continue;
          // With no moment or setting change in it, the silence lasts its draw; a change may only shorten it, or turn the music off
          if (s.changes.some((time) => time >= end && time <= next.atSec)) continue;
          const gap = next.atSec - end;
          expect(gap).toBeGreaterThanOrEqual(T.gapMinSec - 1e-9);
          expect(gap).toBeLessThanOrEqual(T.gapMaxSec + 1e-9);
        }
      }),
      { seed: SEED, examples: [LONG_STAY, BUSY] }
    );
  });
});
