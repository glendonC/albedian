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
  readonly fades: { piece: string; atSec: number; moment: SoundtrackMoment; setting: string; settingChanged: boolean }[] = [];
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
        this.fades.push({ piece: command.piece, atSec: command.atSec, moment: this.director.state.moment, setting: this.director.state.setting, settingChanged: this.settingChanged });
        this.sounding = null;
        continue;
      }
      // A play never overlaps a piece that still sounds
      expect(this.sounding, `play ${command.piece} at ${command.atSec} over ${this.sounding}`).toBeNull();
      this.plays.push({ piece: command.piece, atSec: command.atSec, setting: this.director.state.setting, last: this.lastStarted });
      this.sounding = command.piece;
      this.lastStarted = command.piece;
      this.director.started(command.piece, command.atSec, command.atSec + this.lengthSec(command.piece));
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
  setting(setting: string): void {
    this.settingChanged = true;
    this.changes.push(this.now);
    this.watchEnd(() => this.director.setSetting(setting, this.now));
  }
  /** Moves the clock to `nowSec`, waking the director at every time it asked for on the way. */
  advanceTo(nowSec: number): void {
    for (let guard = 0; guard < 1000; guard++) {
      const wake = this.director.nextWakeSec();
      if (wake === null || wake > nowSec) break;
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
    const s = stage({ moment: 'look_around' });
    s.start();
    s.advance(5);
    s.moment('travel');
    s.advance(1);
    s.moment('look_around');
    s.advance(10);
    expect(s.fades).toEqual([]);
    expect(s.plays.map((p) => p.piece)).toEqual(['ambient-beats']);
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
  | { readonly kind: 'wait'; readonly dt: number };

const SETTINGS = ['soundtrack', 'off', ...SOUNDTRACK.map((piece) => piece.id)];

const stepArb: fc.Arbitrary<Step> = fc.oneof(
  { weight: 4, arbitrary: fc.record({ kind: fc.constant('moment' as const), moment: fc.constantFrom(...SOUNDTRACK_MOMENTS), dt: fc.double({ min: 0, max: 8, noNaN: true }) }) },
  { weight: 1, arbitrary: fc.record({ kind: fc.constant('setting' as const), setting: fc.constantFrom(...SETTINGS), dt: fc.double({ min: 0, max: 8, noNaN: true }) }) },
  { weight: 3, arbitrary: fc.record({ kind: fc.constant('wait' as const), dt: fc.double({ min: 0, max: 120, noNaN: true }) }) }
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
