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

export class SoundtrackDirector {
  private phase: DirectorPhase = 'stopped';
  private moment: SoundtrackMoment;
  private setting: string;
  private piece: string | null = null;
  private last: string | null = null;
  private untilSec: number | null = null;
  private pending: PendingMoment | null = null;
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
    this.pending = null;
  }

  /** The app's moment. While Audio is off it is taken at once; otherwise once it has held `settleSec`. */
  setMoment(moment: SoundtrackMoment, nowSec: number): DirectorCommand[] {
    if (this.phase === 'stopped') {
      this.moment = moment;
      this.pending = null;
      return [];
    }
    this.pending = updatePendingMoment(this.moment, this.pending, moment, nowSec);
    return this.tick(nowSec);
  }

  /** The Music setting: 'soundtrack', 'off' or a piece id to pin. Answered at once. */
  setSetting(setting: string, nowSec: number): DirectorCommand[] {
    const due = this.tick(nowSec);
    if (setting === this.setting) return due;
    this.setting = setting;
    if (this.phase === 'stopped' || this.phase === 'fading') return due;
    if (isActivePiecePhase(this.phase)) {
      return isPieceAllowedBySetting(this.pieces, this.piece!, this.setting, this.moment)
        ? due
        : [...due, ...this.fade(nowSec)];
    }
    // In a gap or silent: answer now
    return [...due, ...this.play(nowSec)];
  }

  /** The music layer scheduled `piece` from `startSec` to `endSec`. A stale report is ignored. */
  started(piece: string, startSec: number, endSec: number): void {
    if (this.phase !== 'starting' || this.piece !== piece || !isValidPlaybackSpan(startSec, endSec)) return;
    this.phase = 'playing';
    this.last = piece;
    this.untilSec = endSec;
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
