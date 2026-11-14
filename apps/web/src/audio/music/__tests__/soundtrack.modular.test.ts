import { afterEach, describe, expect, it, vi } from 'vitest';
import { HeardMusic, audioCreditLines } from '../../audioCredits';
import { INSTRUMENTS, RECORDING, type InstrumentDef, type RecordingAnalysis } from '../directions';
import { MusicLayer } from '../MusicLayer';
import * as soundtrackModule from '../soundtrack';
import {
  SOUNDTRACK,
  SOUNDTRACK_MOMENTS,
  SOUNDTRACK_TIMINGS,
  SoundtrackDirector,
  pieceCreditLines,
  soundtrackPiece,
  type DirectedPiece,
  type DirectorCommand,
  type DirectorOptions,
  type DirectorPhase,
  type DirectorState,
  type DirectorTimings,
  type PieceId,
  type PieceSources,
  type SoundtrackMoment,
  type SoundtrackPiece,
} from '../soundtrack';
import type { ScreenStar } from '../starNotes';

const SAMPLE_STARS: readonly ScreenStar[] = [
  { id: 'star-cool', teffK: 4_200, luminositySolar: 0.8, distancePc: 12, screenX: -0.5, screenY: 0.1 },
  { id: 'star-hot', teffK: 14_000, luminositySolar: 600, distancePc: 95, screenX: 0.4, screenY: -0.2 },
];

const SAMPLE_ANALYSIS: RecordingAnalysis = {
  file: RECORDING.file,
  bpm: 66.66,
  beatSec: 0.9,
  tempoStrength: 0.72,
  beatsPerBar: 4,
  key: { name: 'G major', tonic: 7, mode: 'major', correlation: 0.67 },
  fromSec: 203.12,
  bars: Array.from({ length: RECORDING.bars }, (_, index) => ({
    bar: index + 1,
    chord: index === 11 ? null : 'Em',
    confident: index !== 11,
    score: 0.76,
    margin: index === 11 ? 0.039 : 0.09,
  })),
  statement: 'characterization analysis',
};

afterEach(() => {
  vi.useRealTimers();
});

describe('soundtrack module public surface', () => {
  it('exposes the exact sorted runtime export keys', () => {
    expect(Object.keys(soundtrackModule).sort()).toEqual([
      'SOUNDTRACK',
      'SOUNDTRACK_MOMENTS',
      'SOUNDTRACK_TIMINGS',
      'SoundtrackDirector',
      'pieceCreditLines',
      'soundtrackPiece',
    ]);
  });

  it('preserves every exported type and interface contract', () => {
    const moment: SoundtrackMoment = 'arrive';
    const pieceId: PieceId = 'organ-pulse';
    const directed: DirectedPiece = { id: pieceId, moments: [moment] };
    const timings: DirectorTimings = { ...SOUNDTRACK_TIMINGS };
    const options: DirectorOptions = { moment, setting: 'soundtrack', random: () => 0, timings };
    const director = new SoundtrackDirector([directed], options);
    const state: DirectorState = director.state;
    const phase: DirectorPhase = state.phase;
    const commands: DirectorCommand[] = director.start(0);
    const piece: SoundtrackPiece | undefined = soundtrackPiece(pieceId);
    const sources: PieceSources = { recordingAnalysis: async () => SAMPLE_ANALYSIS };

    expect(phase).toBe('stopped');
    expect(commands).toEqual([{ kind: 'play', piece: 'organ-pulse', atSec: 0 }]);
    expect(piece?.id).toBe('organ-pulse');
    expect(typeof sources.recordingAnalysis).toBe('function');
  });
});

describe('SOUNDTRACK catalog, lookup, and credit lines', () => {
  it('lists the six visit moments and the three soundtrack pieces in canonical order', () => {
    expect(SOUNDTRACK_MOMENTS).toEqual([
      'arrive',
      'reveal',
      'look_around',
      'star_card',
      'travel',
      'back_to_moment',
    ]);
    expect(SOUNDTRACK.map((piece) => ({
      id: piece.id,
      title: piece.title,
      moments: piece.moments,
      alongside: piece.alongside,
    }))).toEqual([
      {
        id: 'organ-pulse',
        title: 'Organ and pulse',
        moments: ['arrive', 'back_to_moment'],
        alongside: { composedMusic: true, recordedTrack: false },
      },
      {
        id: 'ambient-beats',
        title: 'Ambient beats',
        moments: ['reveal', 'look_around', 'star_card'],
        alongside: { composedMusic: true, recordedTrack: false },
      },
      {
        id: 'recording',
        title: 'Signal to Noise',
        moments: ['travel'],
        alongside: { composedMusic: false, recordedTrack: true },
      },
    ]);
  });

  it('looks up pieces by exact id through soundtrackPiece and returns undefined otherwise', () => {
    for (const expected of SOUNDTRACK) {
      expect(soundtrackPiece(expected.id)).toBe(expected);
    }
    for (const missing of ['', 'soundtrack', 'off', 'ORGAN-PULSE', 'organ', 'not-a-piece']) {
      expect(soundtrackPiece(missing)).toBeUndefined();
    }
  });

  it('deduplicates credit lines in first-seen instrument order with zero banned dash characters', () => {
    const organCredits = [
      'Music: Albedian',
      (INSTRUMENTS.organQuiet as InstrumentDef).credit,
      (INSTRUMENTS.chimes as InstrumentDef).credit,
    ];
    const ambientCredits = [
      'Music: Albedian',
      (INSTRUMENTS.vibes as InstrumentDef).credit,
    ];
    const recordingCredits = [
      RECORDING.credit,
      (INSTRUMENTS.glock as InstrumentDef).credit,
    ];

    expect(pieceCreditLines('organ-pulse')).toEqual(organCredits);
    expect(new Set(pieceCreditLines('organ-pulse')).size).toBe(pieceCreditLines('organ-pulse').length);

    expect(pieceCreditLines('ambient-beats')).toEqual(ambientCredits);
    expect(new Set(pieceCreditLines('ambient-beats')).size).toBe(pieceCreditLines('ambient-beats').length);

    expect(pieceCreditLines('recording')).toEqual(recordingCredits);
    expect(new Set(pieceCreditLines('recording')).size).toBe(pieceCreditLines('recording').length);

    expect(pieceCreditLines('off')).toEqual([]);
    expect(pieceCreditLines('soundtrack')).toEqual([]);
    expect(pieceCreditLines('unknown')).toEqual([]);

    for (const piece of SOUNDTRACK) {
      expect(piece.title).not.toMatch(/[\u2013\u2014]/);
      for (const line of piece.credits) {
        expect(line).not.toMatch(/[\u2013\u2014]/);
      }
    }
  });

  it('arranges each piece with stars and with empty stars, reading recordingAnalysis only for recording', async () => {
    const recordingAnalysis = vi.fn(async () => SAMPLE_ANALYSIS);
    const sources: PieceSources = { recordingAnalysis };

    const organComposedOnly = await soundtrackPiece('organ-pulse')!.arrange([], sources);
    const organWithStars = await soundtrackPiece('organ-pulse')!.arrange(SAMPLE_STARS, sources);
    expect(organComposedOnly.events.filter((e) => e.type === 'sample' && e.kind === 'data')).toHaveLength(0);
    expect(organWithStars.events.filter((e) => e.type === 'sample' && e.kind === 'data').length).toBeGreaterThan(0);

    const ambientComposedOnly = await soundtrackPiece('ambient-beats')!.arrange([], sources);
    const ambientWithStars = await soundtrackPiece('ambient-beats')!.arrange(SAMPLE_STARS, sources);
    expect(ambientComposedOnly.events.filter((e) => e.type === 'sample' && e.kind === 'data')).toHaveLength(0);
    expect(ambientWithStars.events.filter((e) => e.type === 'sample' && e.kind === 'data').length).toBeGreaterThan(0);

    expect(recordingAnalysis).not.toHaveBeenCalled();

    const recordingComposedOnly = await soundtrackPiece('recording')!.arrange([], sources);
    const recordingWithStars = await soundtrackPiece('recording')!.arrange(SAMPLE_STARS, sources);
    expect(recordingAnalysis).toHaveBeenCalledTimes(2);
    expect(recordingComposedOnly.recording?.file).toBe(RECORDING.file);
    expect(recordingComposedOnly.events.filter((e) => e.type === 'sample' && e.kind === 'data')).toHaveLength(0);
    expect(recordingWithStars.events.filter((e) => e.type === 'sample' && e.kind === 'data').length).toBeGreaterThan(0);
  });
});

describe('SoundtrackDirector piece selection, fallback tiers, and settings', () => {
  it('selects the canonical piece for each of the six visit moments on fresh start', () => {
    const expectedByMoment: Record<SoundtrackMoment, PieceId> = {
      arrive: 'organ-pulse',
      back_to_moment: 'organ-pulse',
      reveal: 'ambient-beats',
      look_around: 'ambient-beats',
      star_card: 'ambient-beats',
      travel: 'recording',
    };

    for (const moment of SOUNDTRACK_MOMENTS) {
      const director = new SoundtrackDirector(SOUNDTRACK, { moment, random: () => 0 });
      expect(director.start(10)).toEqual([{ kind: 'play', piece: expectedByMoment[moment], atSec: 10 }]);
      expect(director.state).toEqual({
        phase: 'starting',
        moment,
        setting: 'soundtrack',
        piece: expectedByMoment[moment],
        last: null,
        untilSec: null,
      });
    }
  });

  it('applies the four-tier choice hierarchy and clamps boundary random draws', () => {
    // Tier 1: multiple fresh fitting pieces; boundary draw of 1.0 clamps to last element
    const multiFit: DirectedPiece[] = [
      { id: 'fit-first', moments: ['arrive'] },
      { id: 'fit-second', moments: ['arrive'] },
      { id: 'other', moments: ['travel'] },
    ];
    const dHigh = new SoundtrackDirector(multiFit, { moment: 'arrive', random: () => 1 });
    expect(dHigh.start(0)).toEqual([{ kind: 'play', piece: 'fit-second', atSec: 0 }]);
    dHigh.started('fit-second', 0, 20);
    expect(dHigh.tick(100)).toEqual([{ kind: 'play', piece: 'fit-first', atSec: 80 }]);

    // Tier 2: only the piece just played fits (or no piece fits), so choose among fresh non-fitting pieces
    const dFallback = new SoundtrackDirector(SOUNDTRACK, { moment: 'travel', random: () => 0.99 });
    expect(dFallback.start(0)).toEqual([{ kind: 'play', piece: 'recording', atSec: 0 }]);
    dFallback.started('recording', 0, 40);
    // Draws: first draw sets gap (~59.6s), second draw picks last of ['organ-pulse', 'ambient-beats']
    const nextCommands = dFallback.tick(120);
    expect(nextCommands).toHaveLength(1);
    expect(nextCommands[0]!.kind).toBe('play');
    expect(nextCommands[0]!.piece).toBe('ambient-beats');

    // Tier 3: single-piece registry repeats the lone piece after each gap
    const solo = new SoundtrackDirector([{ id: 'solo', moments: ['look_around'] }], {
      moment: 'arrive',
      random: () => 0,
    });
    expect(solo.start(0)).toEqual([{ kind: 'play', piece: 'solo', atSec: 0 }]);
    solo.started('solo', 0, 15);
    expect(solo.tick(35)).toEqual([{ kind: 'play', piece: 'solo', atSec: 35 }]);

    // Tier 4: empty piece list stays silent with no wake scheduled
    const empty = new SoundtrackDirector([], { moment: 'arrive' });
    expect(empty.start(5)).toEqual([]);
    expect(empty.state).toEqual({
      phase: 'silent',
      moment: 'arrive',
      setting: 'soundtrack',
      piece: null,
      last: null,
      untilSec: null,
    });
    expect(empty.nextWakeSec()).toBeNull();
  });

  it('handles off, unknown, soundtrack, and pinned PieceId settings across all phases', () => {
    // Starting with 'off' or an unknown setting enters 'silent' without playing
    const offDirector = new SoundtrackDirector(SOUNDTRACK, { setting: 'off', moment: 'arrive' });
    expect(offDirector.start(2)).toEqual([]);
    expect(offDirector.state.phase).toBe('silent');
    expect(offDirector.nextWakeSec()).toBeNull();

    const unknownDirector = new SoundtrackDirector(SOUNDTRACK, { setting: 'unknown-piece', moment: 'arrive' });
    expect(unknownDirector.start(2)).toEqual([]);
    expect(unknownDirector.state.phase).toBe('silent');

    // Pinned piece ignores moment changes for fades and gap shortening, yet updates state.moment once settled
    const pinned = new SoundtrackDirector(SOUNDTRACK, { setting: 'recording', moment: 'arrive', random: () => 0.5 });
    expect(pinned.start(0)).toEqual([{ kind: 'play', piece: 'recording', atSec: 0 }]);
    pinned.started('recording', 0, 30);
    expect(pinned.setMoment('look_around', 1)).toEqual([]);
    expect(pinned.tick(2.5)).toEqual([]);
    expect(pinned.state.moment).toBe('look_around');
    expect(pinned.state.phase).toBe('playing');

    // Natural end enters a 40s gap (30..70); settling 'travel' at 35s must NOT shorten pinned gap
    expect(pinned.tick(30)).toEqual([]);
    expect(pinned.state).toMatchObject({ phase: 'gap', untilSec: 70 });
    expect(pinned.setMoment('travel', 33.5)).toEqual([]);
    expect(pinned.tick(35)).toEqual([]);
    expect(pinned.state).toMatchObject({ moment: 'travel', phase: 'gap', untilSec: 70 });

    // Switching setting to 'off' mid-gap cancels the gap wake immediately
    expect(pinned.setSetting('off', 40)).toEqual([]);
    expect(pinned.state).toMatchObject({ phase: 'silent', piece: null, untilSec: null });
    expect(pinned.nextWakeSec()).toBeNull();

    // Switching setting to a pinned piece while silent plays immediately; unpinning to 'soundtrack' fades if moment mismatches
    expect(pinned.setMoment('look_around', 41)).toEqual([]);
    expect(pinned.tick(42.5)).toEqual([]);
    expect(pinned.setSetting('recording', 43)).toEqual([{ kind: 'play', piece: 'recording', atSec: 43 }]);
    pinned.started('recording', 43, 83);
    expect(pinned.setSetting('soundtrack', 45)).toEqual([
      { kind: 'fade', piece: 'recording', atSec: 45, fadeSec: SOUNDTRACK_TIMINGS.fadeSec },
    ]);

    // Changing setting while already 'fading' does not emit a second fade and takes effect when the fade ends
    expect(pinned.setSetting('off', 46)).toEqual([]);
    expect(pinned.state).toMatchObject({ phase: 'fading', setting: 'off', untilSec: 45 + SOUNDTRACK_TIMINGS.fadeSec });
    expect(pinned.tick(45 + SOUNDTRACK_TIMINGS.fadeSec)).toEqual([]);
    expect(pinned.state).toMatchObject({ phase: 'silent', piece: null, untilSec: null });
  });

  it('fades a starting piece on disallowed setting or settled moment without marking it as last played', () => {
    const director = new SoundtrackDirector(SOUNDTRACK, { moment: 'arrive', random: () => 0 });
    expect(director.start(10)).toEqual([{ kind: 'play', piece: 'organ-pulse', atSec: 10 }]);
    expect(director.state.last).toBeNull();

    // Before started() is reported, user pins 'ambient-beats'
    expect(director.setSetting('ambient-beats', 11)).toEqual([
      { kind: 'fade', piece: 'organ-pulse', atSec: 11, fadeSec: SOUNDTRACK_TIMINGS.fadeSec },
    ]);
    expect(director.state.last).toBeNull();
    expect(director.tick(14)).toEqual([{ kind: 'play', piece: 'ambient-beats', atSec: 14 }]);

    // Now unpin back to 'soundtrack' while 'ambient-beats' is still 'starting' in 'arrive': fades immediately, last stays null
    expect(director.setSetting('soundtrack', 14.5)).toEqual([
      { kind: 'fade', piece: 'ambient-beats', atSec: 14.5, fadeSec: SOUNDTRACK_TIMINGS.fadeSec },
    ]);
    expect(director.state.last).toBeNull();
    // Because 'organ-pulse' never started, it is still fresh for 'arrive'
    expect(director.tick(17.5)).toEqual([{ kind: 'play', piece: 'organ-pulse', atSec: 17.5 }]);
  });
});

describe('SoundtrackDirector timings, debounce, gap bounds, and lifecycle guards', () => {
  it('honors custom DirectorTimings and never lengthens a gap that ends sooner than entrySec', () => {
    const customTimings: DirectorTimings = {
      gapMinSec: 10,
      gapMaxSec: 14,
      settleSec: 0.5,
      fadeSec: 1.25,
      entrySec: 4,
    };
    const director = new SoundtrackDirector(SOUNDTRACK, {
      moment: 'look_around',
      random: () => 0.5,
      timings: customTimings,
    });

    expect(director.start(0)).toEqual([{ kind: 'play', piece: 'ambient-beats', atSec: 0 }]);
    director.started('ambient-beats', 0, 20);
    expect(director.nextWakeSec()).toBe(20);

    // Piece ends at 20s -> gap is 20 + 10 + 0.5 * 4 = 32s
    expect(director.tick(20)).toEqual([]);
    expect(director.state).toMatchObject({ phase: 'gap', untilSec: 32 });

    // Moment changes at 29.5s and settles at 30.0s; 30 + entrySec (34s) > untilSec (32s), so untilSec stays 32s!
    expect(director.setMoment('travel', 29.5)).toEqual([]);
    expect(director.nextWakeSec()).toBe(30);
    expect(director.tick(30)).toEqual([]);
    expect(director.state).toMatchObject({ phase: 'gap', moment: 'travel', untilSec: 32 });
    expect(director.tick(32)).toEqual([{ kind: 'play', piece: 'recording', atSec: 32 }]);
  });

  it('keeps pending sinceSec when the same pending moment is reported again, and resets it on a new moment', () => {
    const director = new SoundtrackDirector(SOUNDTRACK, { moment: 'arrive', random: () => 0 });
    director.start(0);
    director.started('organ-pulse', 0, 53);

    // Report 'travel' at t=1.0, then report 'travel' again at t=2.0: must still settle at 1.0 + 1.5 = 2.5
    expect(director.setMoment('travel', 1.0)).toEqual([]);
    expect(director.nextWakeSec()).toBe(2.5);
    expect(director.setMoment('travel', 2.0)).toEqual([]);
    expect(director.nextWakeSec()).toBe(2.5);
    expect(director.tick(2.5)).toEqual([
      { kind: 'fade', piece: 'organ-pulse', atSec: 2.5, fadeSec: SOUNDTRACK_TIMINGS.fadeSec },
    ]);

    // During fade (until 5.5), test changing pending moment to 'look_around' at 3.0 then 'reveal' at 4.2
    expect(director.setMoment('look_around', 3.0)).toEqual([]);
    expect(director.nextWakeSec()).toBe(4.5);
    expect(director.setMoment('reveal', 4.2)).toEqual([]);
    expect(director.nextWakeSec()).toBeCloseTo(5.5, 9); // fade ends at 5.5, before reveal settles at 5.7
    expect(director.tick(5.5)).toEqual([{ kind: 'play', piece: 'recording', atSec: 5.5 }]);
    expect(director.nextWakeSec()).toBeCloseTo(5.7, 9);
    expect(director.tick(5.7)).toEqual([
      { kind: 'fade', piece: 'recording', atSec: 5.7, fadeSec: SOUNDTRACK_TIMINGS.fadeSec },
    ]);
  });

  it('ignores duplicate start(), stale or invalid started() reports, and preserves moment, setting, and last across stop()', () => {
    const director = new SoundtrackDirector(SOUNDTRACK, { moment: 'arrive', random: () => 0 });
    expect(director.start(0)).toEqual([{ kind: 'play', piece: 'organ-pulse', atSec: 0 }]);
    // Duplicate start while running is a no-op
    expect(director.start(5)).toEqual([]);

    // Invalid started() reports are ignored
    director.started('recording', 0, 40); // wrong piece id
    expect(director.state.phase).toBe('starting');
    director.started('organ-pulse', 10, 9); // endSec < startSec
    expect(director.state.phase).toBe('starting');
    director.started('organ-pulse', 10, Number.NaN); // NaN endSec
    expect(director.state.phase).toBe('starting');

    // Zero-duration piece (endSec === startSec) is valid
    director.started('organ-pulse', 10, 10);
    expect(director.state).toEqual({
      phase: 'playing',
      moment: 'arrive',
      setting: 'soundtrack',
      piece: 'organ-pulse',
      last: 'organ-pulse',
      untilSec: 10,
    });
    // Duplicate started() while already 'playing' is ignored
    director.started('organ-pulse', 10, 999);
    expect(director.state.untilSec).toBe(10);

    // Queue a pending moment, then stop(): pending, piece, and untilSec clear; moment, setting, and last remain
    director.setMoment('look_around', 9);
    director.stop();
    expect(director.state).toEqual({
      phase: 'stopped',
      moment: 'arrive',
      setting: 'soundtrack',
      piece: null,
      last: 'organ-pulse',
      untilSec: null,
    });
    expect(director.nextWakeSec()).toBeNull();

    // Restarting in 'arrive' remembers last === 'organ-pulse', so it picks a fresh piece instead of repeating
    const restarted = director.start(20);
    expect(restarted).toHaveLength(1);
    expect(restarted[0]!.piece).not.toBe('organ-pulse');
  });
});

describe('MusicLayer integration: inter-piece silence timers, cancellation, and heard-piece credits', () => {
  class MinimalParam {
    value = 0;
    setValueAtTime(v: number) { this.value = v; return this; }
    linearRampToValueAtTime(v: number) { this.value = v; return this; }
    exponentialRampToValueAtTime(v: number) { this.value = v; return this; }
    setTargetAtTime(v: number) { this.value = v; return this; }
    setValueCurveAtTime(values: ArrayLike<number>) { this.value = values[values.length - 1] ?? 0; return this; }
    cancelScheduledValues() { return this; }
  }

  class MinimalNode {
    [key: string]: unknown;
    disconnected = false;
    constructor() {
      return new Proxy(this, {
        get(target, prop, receiver) {
          if (typeof prop === 'string' && !(prop in target)) target[prop] = new MinimalParam();
          return Reflect.get(target, prop, receiver);
        },
      });
    }
    connect(dest: unknown) { return dest; }
    disconnect() { this.disconnected = true; }
    start() {}
    stop() {}
    setPeriodicWave() {}
  }

  class MinimalContext {
    currentTime = 0;
    state: AudioContextState = 'running';
    readonly sampleRate = 48_000;
    addEventListener() {}
    removeEventListener() {}
    createGain() { return new MinimalNode(); }
    createDynamicsCompressor() { return new MinimalNode(); }
    createConvolver() { return new MinimalNode(); }
    createDelay() { return new MinimalNode(); }
    createBiquadFilter() { return new MinimalNode(); }
    createStereoPanner() { return new MinimalNode(); }
    createBufferSource() { return new MinimalNode(); }
    createOscillator() { return new MinimalNode(); }
    createPeriodicWave() { return {}; }
    createBuffer(_ch: number, length: number, sampleRate: number) {
      const channels = [new Float32Array(length), new Float32Array(length)];
      return {
        duration: length / sampleRate,
        length,
        sampleRate,
        numberOfChannels: 2,
        getChannelData: (c: number) => channels[c]!,
        copyToChannel: (data: Float32Array, c: number) => channels[c]!.set(data),
      };
    }
    async decodeAudioData() {
      return this.createBuffer(2, 48_000, 48_000);
    }
  }

  it('notifies onPieceStarted into HeardMusic credits, schedules inter-piece silence, and cancels timers on stop()', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const heard = new HeardMusic();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('recording-analysis.json')) {
        return new Response(JSON.stringify(SAMPLE_ANALYSIS));
      }
      return new Response(new Uint8Array(16));
    }) as unknown as typeof fetch;

    const layer = new MusicLayer({
      moment: 'arrive',
      sourcesBase: 'https://music.example.test/',
      fetch: fetchMock,
      random: () => 0,
      onPieceStarted: (piece) => heard.add(piece),
    });
    layer.setStars(SAMPLE_STARS);

    const ctx = new MinimalContext();
    await layer.start(ctx as unknown as BaseAudioContext, new MinimalNode() as unknown as AudioNode);

    expect(heard.get()).toEqual(['organ-pulse']);
    expect(audioCreditLines(heard.get())).toEqual(expect.arrayContaining([...pieceCreditLines('organ-pulse')]));
    expect(layer.getStatus()).toMatchObject({ state: 'playing', piece: 'organ-pulse', passes: 1 });

    // Advance past organ-pulse natural end (0.25 lead + 53s = 53.25s) into the 20s silence gap
    ctx.currentTime = 53.5;
    await vi.advanceTimersByTimeAsync(54_000);
    expect(layer.getStatus()).toMatchObject({ state: 'gap', piece: null, passes: 1 });

    // Advance past the 20s silence gap (73.25s): next piece starts and adds its credits
    ctx.currentTime = 73.5;
    await vi.advanceTimersByTimeAsync(21_000);
    expect(layer.getStatus().passes).toBe(2);
    expect(heard.get()).toEqual(['organ-pulse', 'ambient-beats']);
    expect(audioCreditLines(heard.get())).toEqual(
      expect.arrayContaining([...pieceCreditLines('organ-pulse'), ...pieceCreditLines('ambient-beats')])
    );

    // Stop cancels all scheduled director timers so advancing clock + timers plays nothing further
    layer.setMoment('travel');
    layer.stop();
    expect(layer.getStatus()).toMatchObject({ state: 'off', piece: null });
    ctx.currentTime = 200;
    await vi.advanceTimersByTimeAsync(120_000);
    expect(layer.getStatus()).toMatchObject({ state: 'off', passes: 2 });
    expect(heard.get()).toEqual(['organ-pulse', 'ambient-beats']);
  });
});
