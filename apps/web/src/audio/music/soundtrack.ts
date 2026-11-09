/**
 * The soundtrack (AUDIO_ENGINE.md 13.7): the pieces the app plays, the moments of a visit each one
 * fits, and the director that picks what plays when. It follows the pattern of Minecraft's
 * soundtrack: a piece plays through, a silence follows, then another piece.
 *
 * A new piece drops in as one entry in `SOUNDTRACK`: its id, its title, the moments it fits, its
 * credit lines and how it is arranged from the stars on screen.
 *
 * The director is pure: the music layer passes in the clock (the AudioContext's, in seconds) and
 * carries out the director's commands, so tests can drive it through any visit.
 */

import {
  INSTRUMENTS,
  RECORDING,
  ambientBeats,
  organAndPulse,
  recordingArrangement,
  type Arrangement,
  type DirectionId,
  type InstrumentDef,
  type InstrumentId,
  type RecordingAnalysis,
} from './directions';
import type { ScreenStar } from './starNotes';

/** The moments of a visit the soundtrack follows (soundtrackMoment.ts reads them from the app). */
export type SoundtrackMoment = 'arrive' | 'reveal' | 'look_around' | 'star_card' | 'travel' | 'back_to_moment';

export const SOUNDTRACK_MOMENTS: readonly SoundtrackMoment[] = ['arrive', 'reveal', 'look_around', 'star_card', 'travel', 'back_to_moment'];

/** What the director needs to know of a piece. */
export interface DirectedPiece {
  readonly id: string;
  /** The moments the piece fits. */
  readonly moments: readonly SoundtrackMoment[];
}

/** What a piece may need beyond the stars to be arranged, loaded by the music layer. */
export interface PieceSources {
  /** The measured beats and chords of the recording (analysis.ts, served beside the samples). */
  recordingAnalysis(): Promise<RecordingAnalysis>;
}

export type PieceId = DirectionId;

export interface SoundtrackPiece extends DirectedPiece {
  readonly id: PieceId;
  /** The name the Music setting shows. */
  readonly title: string;
  /** The credit lines shown once the piece has been heard: what it is, then each source it plays. */
  readonly credits: readonly string[];
  /** What plays alongside its star notes, for the disclosure beside the Audio control. */
  readonly alongside: { readonly composedMusic: boolean; readonly recordedTrack: boolean };
  /** The piece arranged over these stars; with no stars, its composed parts alone. */
  arrange(stars: readonly ScreenStar[], sources: PieceSources): Promise<Arrangement>;
}

const creditLines = (head: string, instruments: readonly InstrumentId[]): readonly string[] => [
  ...new Set([head, ...instruments.map((id) => (INSTRUMENTS[id] as InstrumentDef).credit)]),
];

/**
 * Every piece, with the moments the lead mapped it to: the organ and pulse for arriving and coming
 * back to the moment, the ambient beats for the reveal, looking around and a star's card, and the
 * recording for travel.
 */
export const SOUNDTRACK: readonly SoundtrackPiece[] = [
  {
    id: 'organ-pulse',
    title: 'Organ and pulse',
    moments: ['arrive', 'back_to_moment'],
    credits: creditLines('Music: Albedian', ['organQuiet', 'organLoud', 'pedalQuiet', 'pedalLoud', 'blower', 'chimes', 'claves', 'cymbalSwell']),
    alongside: { composedMusic: true, recordedTrack: false },
    arrange: async (stars) => organAndPulse(stars),
  },
  {
    id: 'ambient-beats',
    title: 'Ambient beats',
    moments: ['reveal', 'look_around', 'star_card'],
    credits: creditLines('Music: Albedian', ['vibes', 'hats', 'clap', 'shaker']),
    alongside: { composedMusic: true, recordedTrack: false },
    arrange: async (stars) => ambientBeats(stars),
  },
  {
    id: 'recording',
    title: 'Signal to Noise',
    moments: ['travel'],
    credits: creditLines(RECORDING.credit, ['glock']),
    alongside: { composedMusic: false, recordedTrack: true },
    // It plays inside the recording's measured beats and chords
    arrange: async (stars, sources) => recordingArrangement(stars, await sources.recordingAnalysis()),
  },
];

export function soundtrackPiece(id: string): SoundtrackPiece | undefined {
  return SOUNDTRACK.find((piece) => piece.id === id);
}

/** A piece's credit lines; none for an id that is not in the soundtrack. */
export function pieceCreditLines(id: string): readonly string[] {
  return soundtrackPiece(id)?.credits ?? [];
}

export interface DirectorTimings {
  /** The silence after a piece ends is drawn uniformly between these. */
  readonly gapMinSec: number;
  readonly gapMaxSec: number;
  /** A new moment must hold this long before the music answers it, so quick clicks do not flap. */
  readonly settleSec: number;
  /** A piece that no longer fits fades out over this long. */
  readonly fadeSec: number;
  /** In a silence, the new moment's piece starts this long after the moment has settled. */
  readonly entrySec: number;
}

/** Design choices, agreed with the lead: a visit lasts minutes, so the gaps are under a minute. */
export const SOUNDTRACK_TIMINGS: DirectorTimings = { gapMinSec: 20, gapMaxSec: 60, settleSec: 1.5, fadeSec: 3, entrySec: 2 };

export type DirectorCommand =
  /** Start a pass of `piece` at `atSec` (or as soon after as its files are ready). */
  | { readonly kind: 'play'; readonly piece: string; readonly atSec: number }
  /** Fade the piece that sounds (or is about to) out from `atSec` over `fadeSec`. */
  | { readonly kind: 'fade'; readonly piece: string; readonly atSec: number; readonly fadeSec: number };

/**
 * stopped: Audio is off. silent: nothing to play (the setting is off). starting: a play was sent and
 * the piece is being prepared. playing, fading, gap: as named.
 */
export type DirectorPhase = 'stopped' | 'silent' | 'starting' | 'playing' | 'fading' | 'gap';

export interface DirectorState {
  readonly phase: DirectorPhase;
  readonly moment: SoundtrackMoment;
  /** 'soundtrack', 'off', or the id of the pinned piece. */
  readonly setting: string;
  /** The piece starting, playing or fading; null otherwise. */
  readonly piece: string | null;
  /** The last piece that started playing. */
  readonly last: string | null;
  /** When the piece, the fade or the gap ends; null when none runs. */
  readonly untilSec: number | null;
}

export interface DirectorOptions {
  readonly moment?: SoundtrackMoment;
  readonly setting?: string;
  /** Uniform draws in [0, 1) for the gaps and the choice between pieces. */
  readonly random?: () => number;
  readonly timings?: DirectorTimings;
}

export class SoundtrackDirector {
  private phase: DirectorPhase = 'stopped';
  private moment: SoundtrackMoment;
  private setting: string;
  private piece: string | null = null;
  private last: string | null = null;
  private untilSec: number | null = null;
  private pending: { readonly moment: SoundtrackMoment; readonly sinceSec: number } | null = null;
  private readonly random: () => number;
  private readonly timings: DirectorTimings;

  constructor(
    private readonly pieces: readonly DirectedPiece[],
    options: DirectorOptions = {}
  ) {
    this.moment = options.moment ?? 'arrive';
    this.setting = options.setting ?? 'soundtrack';
    this.random = options.random ?? Math.random;
    this.timings = options.timings ?? SOUNDTRACK_TIMINGS;
  }

  get state(): DirectorState {
    return { phase: this.phase, moment: this.moment, setting: this.setting, piece: this.piece, last: this.last, untilSec: this.untilSec };
  }

  /** Audio on: plays the piece for the moment (or the pin) now. */
  start(nowSec: number): DirectorCommand[] {
    if (this.phase !== 'stopped') return [];
    this.phase = 'silent';
    this.pending = null;
    return this.play(nowSec);
  }

  /** Audio off: nothing plays until `start`. The moment, the setting and the last piece are kept. */
  stop(): void {
    this.phase = 'stopped';
    this.piece = null;
    this.untilSec = null;
    this.pending = null;
  }

  /** The app's moment. While Audio is off it is taken at once; otherwise once it has held `settleSec`. */
  setMoment(moment: SoundtrackMoment, nowSec: number): DirectorCommand[] {
    if (this.phase === 'stopped') {
      this.moment = moment;
      this.pending = null;
      return [];
    }
    if (moment === this.moment) this.pending = null;
    else if (this.pending?.moment !== moment) this.pending = { moment, sinceSec: nowSec };
    return this.tick(nowSec);
  }

  /** The Music setting: 'soundtrack', 'off' or a piece id to pin. Answered at once. */
  setSetting(setting: string, nowSec: number): DirectorCommand[] {
    const due = this.tick(nowSec);
    if (setting === this.setting) return due;
    this.setting = setting;
    if (this.phase === 'stopped' || this.phase === 'fading') return due;
    if (this.phase === 'starting' || this.phase === 'playing') {
      return this.allows(this.piece!) ? due : [...due, ...this.fade(nowSec)];
    }
    // In a gap or silent: answer now
    return [...due, ...this.play(nowSec)];
  }

  /** The music layer scheduled `piece` from `startSec` to `endSec`. A stale report is ignored. */
  started(piece: string, startSec: number, endSec: number): void {
    if (this.phase !== 'starting' || this.piece !== piece || !(endSec >= startSec)) return;
    this.phase = 'playing';
    this.last = piece;
    this.untilSec = endSec;
  }

  /** When `tick` next has something to do; null when nothing waits. */
  nextWakeSec(): number | null {
    if (this.phase === 'stopped') return null;
    const settle = this.pending ? this.pending.sinceSec + this.timings.settleSec : Infinity;
    const until = this.untilSec ?? Infinity;
    const next = Math.min(settle, until);
    return Number.isFinite(next) ? next : null;
  }

  /** Moves the director to `nowSec`, in time order: settled moments, ends of pieces, fades and gaps. */
  tick(nowSec: number): DirectorCommand[] {
    const out: DirectorCommand[] = [];
    for (let guard = 0; guard < 16 && this.phase !== 'stopped'; guard++) {
      const settleAt = this.pending ? this.pending.sinceSec + this.timings.settleSec : Infinity;
      const untilAt = this.untilSec ?? Infinity;
      if (Math.min(settleAt, untilAt) > nowSec) break;
      if (settleAt <= untilAt) {
        this.moment = this.pending!.moment;
        this.pending = null;
        out.push(...this.commit(settleAt));
      } else if (this.phase === 'playing') {
        // The piece ended on its own: silence, then the next piece
        this.phase = 'gap';
        this.piece = null;
        this.untilSec = untilAt + this.timings.gapMinSec + this.random() * (this.timings.gapMaxSec - this.timings.gapMinSec);
      } else {
        // A fade or a gap ended
        out.push(...this.play(untilAt));
      }
    }
    return out;
  }

  /** The moment changed: a piece that no longer fits fades; a silence shortens for a piece that fits. */
  private commit(atSec: number): DirectorCommand[] {
    if (this.setting !== 'soundtrack') return [];
    if (this.phase === 'starting' || this.phase === 'playing') return this.fits(this.piece!) ? [] : this.fade(atSec);
    if (this.phase === 'gap' && this.fresh().some((piece) => piece.moments.includes(this.moment))) {
      this.untilSec = Math.min(this.untilSec!, atSec + this.timings.entrySec);
    }
    // A fade picks when it ends; silence has nothing to play
    return [];
  }

  private fade(atSec: number): DirectorCommand[] {
    const piece = this.piece!;
    this.phase = 'fading';
    this.untilSec = atSec + this.timings.fadeSec;
    return [{ kind: 'fade', piece, atSec, fadeSec: this.timings.fadeSec }];
  }

  private play(atSec: number): DirectorCommand[] {
    const piece = this.choose();
    if (piece === null) {
      this.phase = 'silent';
      this.piece = null;
      this.untilSec = null;
      return [];
    }
    this.phase = 'starting';
    this.piece = piece;
    this.untilSec = null;
    return [{ kind: 'play', piece, atSec }];
  }

  /**
   * The next piece. Pinned: the pin, again and again; off (or any setting that names no piece): none.
   * The soundtrack: one that fits the moment and is not the piece just played; when only that piece
   * fits, another piece; the one piece again only when the soundtrack has no other.
   */
  private choose(): string | null {
    if (this.setting !== 'soundtrack') return this.pieces.some((piece) => piece.id === this.setting) ? this.setting : null;
    const others = this.fresh();
    const fitting = others.filter((piece) => piece.moments.includes(this.moment));
    const pool = fitting.length > 0 ? fitting : others.length > 0 ? others : this.pieces;
    if (pool.length === 0) return null;
    return pool[Math.min(pool.length - 1, Math.floor(this.random() * pool.length))]!.id;
  }

  /** The pieces other than the one just played. */
  private fresh(): readonly DirectedPiece[] {
    return this.pieces.filter((piece) => piece.id !== this.last);
  }

  private fits(id: string): boolean {
    return this.pieces.find((piece) => piece.id === id)?.moments.includes(this.moment) ?? false;
  }

  private allows(id: string): boolean {
    return this.setting === 'soundtrack' ? this.fits(id) : this.setting === id;
  }
}
