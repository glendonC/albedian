/**
 * Pure adaptive soundtrack director state machine (`SoundtrackDirector`).
 */

import {
  hasFreshPieceForMoment,
  isPieceAllowedBySetting,
  pieceIdFitsMoment,
  selectNextPieceId,
} from './pieceSelector.js';
import {
  computeDeadlines,
  drawSilenceGapEndSec,
  fadeEndSec,
  shortenGapForSettledMoment,
  updatePendingMoment,
  type PendingMoment,
} from './silenceScheduler.js';
import {
  CROSSING_AHEAD,
  CROSSING_MOMENTS,
  DEFAULT_SOUNDTRACK_MOMENT,
  MAX_DIRECTOR_TICK_STEPS,
  SOUNDTRACK_SETTING,
  SOUNDTRACK_TIMINGS,
  type DirectedPiece,
  type DirectorCommand,
  type DirectorOptions,
  type DirectorPhase,
  type DirectorState,
  type DirectorTimings,
  type SoundtrackMoment,
} from './types.js';

const isActivePiecePhase = (phase: DirectorPhase): boolean => phase === 'starting' || phase === 'playing';
const isValidPlaybackSpan = (startSec: number, endSec: number): boolean => endSec >= startSec;
/** A beat that falls within this of the earliest schedulable time still counts as reached. */
const BEAT_SNAP_SEC = 1e-9;

export class SoundtrackDirector {
  private phase: DirectorPhase = 'stopped';
  private moment: SoundtrackMoment;
  private setting: string;
  private piece: string | null = null;
  private last: string | null = null;
  private untilSec: number | null = null;
  private pending: PendingMoment | null = null;
  /** The playing piece's beat grid: its start and its beat; null when it has none. */
  private beat: { readonly t0: number; readonly beatSec: number } | null = null;
  /** A crossing moment waiting for its piece to be prepared (music-crossing-1). */
  private crossing: { readonly moment: SoundtrackMoment; readonly piece: string } | null = null;
  private readonly random: () => number;
  private readonly timings: DirectorTimings;

  constructor(
    private readonly pieces: readonly DirectedPiece[],
    options: DirectorOptions = {}
  ) {
    this.moment = options.moment ?? DEFAULT_SOUNDTRACK_MOMENT;
    this.setting = options.setting ?? SOUNDTRACK_SETTING;
    this.random = options.random ?? Math.random;
    this.timings = options.timings ?? SOUNDTRACK_TIMINGS;
  }

  get state(): DirectorState {
    return {
      phase: this.phase,
      moment: this.moment,
      setting: this.setting,
      piece: this.piece,
      last: this.last,
      untilSec: this.untilSec,
    };
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
    this.beat = null;
    this.crossing = null;
    this.pending = null;
  }

  /** The app's moment. While Audio is off it is taken at once; otherwise once it has held `settleSec`. */
  setMoment(moment: SoundtrackMoment, nowSec: number): DirectorCommand[] {
    if (this.phase === 'stopped') {
      this.moment = moment;
      this.pending = null;
      this.crossing = null;
      return [];
    }
    const out = this.takeMoment(moment, nowSec);
    return [...out, ...this.warmAhead(moment)];
  }

  /**
   * The pieces the crossing one press away from `moment` would pick, to be fetched now (music-crossing-2); none when
   * the playing piece already fits that crossing, or the music is pinned or off.
   */
  private warmAhead(moment: SoundtrackMoment): DirectorCommand[] {
    const ahead = CROSSING_AHEAD.get(moment);
    if (!ahead || this.setting !== SOUNDTRACK_SETTING || this.phase === 'stopped') return [];
    if (this.piece !== null && pieceIdFitsMoment(this.pieces, this.piece, ahead)) return [];
    return this.pieces
      .filter((piece) => piece.moments.includes(ahead))
      .map((piece) => ({ kind: 'warm' as const, piece: piece.id }));
  }

  private takeMoment(moment: SoundtrackMoment, nowSec: number): DirectorCommand[] {
    const due = this.tick(nowSec);
    // A new moment ends a crossing still waiting for its piece, whichever moment it is
    this.crossing = null;
    if (this.setting === SOUNDTRACK_SETTING && this.phase === 'playing' && CROSSING_MOMENTS.has(moment) && moment !== this.moment) {
      // The playing piece fits it: taken at once, nothing to cross
      if (pieceIdFitsMoment(this.pieces, this.piece!, moment)) {
        this.moment = moment;
        this.pending = null;
        return due;
      }
      // On the playing piece's beat, with the incoming piece fetched first (music-crossing-1); with no beat known,
      // the settle and the fade as for any moment
      const piece = this.beat ? selectNextPieceId(this.pieces, this.setting, moment, this.piece, this.random) : null;
      if (piece !== null) {
        this.pending = null;
        this.crossing = { moment, piece };
        return [...due, { kind: 'prepare', piece }];
      }
    }
    this.pending = updatePendingMoment(this.moment, this.pending, moment, nowSec);
    return [...due, ...this.tick(nowSec)];
  }

  /**
   * The music layer has `piece` ready (music-crossing-1). If it is the piece a crossing waits for, the moment is taken:
   * while a piece plays, both turn on its first beat at or after `nowSec + leadSec` (never past its end), the outgoing
   * fading across one beat and the incoming entering at full; in a silence the incoming enters at once. Any other
   * report is stale and changes nothing.
   */
  prepared(piece: string, nowSec: number, leadSec: number): DirectorCommand[] {
    const due = this.tick(nowSec);
    const crossing = this.crossing;
    if (!crossing || crossing.piece !== piece || this.setting !== SOUNDTRACK_SETTING || this.phase === 'stopped') return due;
    this.crossing = null;
    this.moment = crossing.moment;
    this.pending = null;
    const earliest = nowSec + leadSec;
    if (this.phase === 'playing' && this.beat) {
      const { t0, beatSec } = this.beat;
      const next = t0 + Math.max(0, Math.ceil((earliest - t0 - BEAT_SNAP_SEC) / beatSec)) * beatSec;
      const atSec = Math.min(next, this.untilSec ?? next);
      const outgoing = this.piece!;
      this.phase = 'starting';
      this.piece = piece;
      this.untilSec = null;
      this.beat = null;
      return [...due, { kind: 'fade', piece: outgoing, atSec, fadeSec: beatSec }, { kind: 'play', piece, atSec }];
    }
    if (this.phase === 'gap' || this.phase === 'silent') {
      this.phase = 'starting';
      this.piece = piece;
      this.untilSec = null;
      return [...due, { kind: 'play', piece, atSec: earliest }];
    }
    // Starting or fading: the moment is taken, as a settled one
    return [...due, ...this.commit(nowSec)];
  }

  /**
   * The restored compare arrived (compare-music-1, DECIDED-BY-LEAD 12:41 on 24 Nov): it counts as a reveal that has held.
   * In a silence the director takes the reveal at once, and the gap shortens to its entry for a reveal piece that did
   * not just play, as for a settled moment (`commit`); a piece that plays, fades or starts, the music off or pinned,
   * and Audio off are left as they are. A moment change still settling goes on settling.
   */
  arrive(nowSec: number): DirectorCommand[] {
    const due = this.tick(nowSec);
    if (this.phase !== 'gap' || this.setting !== SOUNDTRACK_SETTING) return due;
    this.moment = 'reveal';
    return [...due, ...this.commit(nowSec)];
  }

  /** The Music setting: 'soundtrack', 'off' or a piece id to pin. Answered at once. */
  setSetting(setting: string, nowSec: number): DirectorCommand[] {
    const due = this.tick(nowSec);
    if (setting === this.setting) return due;
    this.setting = setting;
    this.crossing = null;
    if (this.phase === 'stopped' || this.phase === 'fading') return due;
    if (isActivePiecePhase(this.phase)) {
      return isPieceAllowedBySetting(this.pieces, this.piece!, this.setting, this.moment)
        ? due
        : [...due, ...this.fade(nowSec)];
    }
    // In a gap or silent: answer now
    return [...due, ...this.play(nowSec)];
  }

  /**
   * The music layer scheduled `piece` from `startSec` to `endSec`, its beat `beatSec` long (null for none). A stale
   * report is ignored.
   */
  started(piece: string, startSec: number, endSec: number, beatSec: number | null = null): void {
    if (this.phase !== 'starting' || this.piece !== piece || !isValidPlaybackSpan(startSec, endSec)) return;
    this.phase = 'playing';
    this.last = piece;
    this.untilSec = endSec;
    this.beat = beatSec !== null && Number.isFinite(beatSec) && beatSec > 0 ? { t0: startSec, beatSec } : null;
  }

  /** When `tick` next has something to do; null when nothing waits. */
  nextWakeSec(): number | null {
    if (this.phase === 'stopped') return null;
    const { nextAt } = computeDeadlines(this.pending, this.untilSec, this.timings);
    return Number.isFinite(nextAt) ? nextAt : null;
  }

  /** Moves the director to `nowSec`, in time order: settled moments, ends of pieces, fades and gaps. */
  tick(nowSec: number): DirectorCommand[] {
    const out: DirectorCommand[] = [];
    for (let guard = 0; guard < MAX_DIRECTOR_TICK_STEPS && this.phase !== 'stopped'; guard++) {
      const { settleAt, untilAt, nextAt } = computeDeadlines(this.pending, this.untilSec, this.timings);
      if (nextAt > nowSec) break;
      if (settleAt <= untilAt) {
        this.moment = this.pending!.moment;
        this.pending = null;
        out.push(...this.commit(settleAt));
      } else if (this.phase === 'playing') {
        // The piece ended on its own: silence, then the next piece
        this.phase = 'gap';
        this.piece = null;
        this.untilSec = drawSilenceGapEndSec(untilAt, this.timings, this.random);
      } else {
        // A fade or a gap ended
        out.push(...this.play(untilAt));
      }
    }
    return out;
  }

  /** The moment changed: a piece that no longer fits fades; a silence shortens for a piece that fits. */
  private commit(atSec: number): DirectorCommand[] {
    if (this.setting !== SOUNDTRACK_SETTING) return [];
    if (isActivePiecePhase(this.phase)) {
      return pieceIdFitsMoment(this.pieces, this.piece!, this.moment) ? [] : this.fade(atSec);
    }
    if (this.phase === 'gap' && hasFreshPieceForMoment(this.pieces, this.last, this.moment)) {
      this.untilSec = shortenGapForSettledMoment(this.untilSec!, atSec, this.timings);
    }
    // A fade picks when it ends; silence has nothing to play
    return [];
  }

  private fade(atSec: number): DirectorCommand[] {
    const piece = this.piece!;
    this.phase = 'fading';
    this.untilSec = fadeEndSec(atSec, this.timings);
    return [{ kind: 'fade', piece, atSec, fadeSec: this.timings.fadeSec }];
  }

  private play(atSec: number): DirectorCommand[] {
    const piece = selectNextPieceId(this.pieces, this.setting, this.moment, this.last, this.random);
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
}
