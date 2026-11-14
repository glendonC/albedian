/**
 * Pure piece-matching, setting-allowance, and four-tier non-repeating selection rules
 * for `SoundtrackDirector`.
 */

import { SOUNDTRACK_SETTING, type DirectedPiece, type SoundtrackMoment } from './types.js';

export function pieceFitsMoment(piece: DirectedPiece, moment: SoundtrackMoment): boolean {
  return piece.moments.includes(moment);
}

/** The pieces other than the one just played. */
export function freshPieces(pieces: readonly DirectedPiece[], lastPieceId: string | null): readonly DirectedPiece[] {
  return pieces.filter((piece) => piece.id !== lastPieceId);
}

export function hasFreshPieceForMoment(
  pieces: readonly DirectedPiece[],
  lastPieceId: string | null,
  moment: SoundtrackMoment
): boolean {
  return freshPieces(pieces, lastPieceId).some((piece) => pieceFitsMoment(piece, moment));
}

export function pieceIdFitsMoment(pieces: readonly DirectedPiece[], id: string, moment: SoundtrackMoment): boolean {
  const piece = pieces.find((candidate) => candidate.id === id);
  return piece ? pieceFitsMoment(piece, moment) : false;
}

export function isPieceAllowedBySetting(
  pieces: readonly DirectedPiece[],
  id: string,
  setting: string,
  moment: SoundtrackMoment
): boolean {
  return setting === SOUNDTRACK_SETTING ? pieceIdFitsMoment(pieces, id, moment) : setting === id;
}

function drawUniformItem<T>(pool: readonly T[], random: () => number): T | null {
  if (pool.length === 0) return null;
  const index = Math.min(pool.length - 1, Math.floor(random() * pool.length));
  return pool[index] ?? null;
}

/**
 * The next piece. Pinned: the pin, again and again; off (or any setting that names no piece): none.
 * The soundtrack: one that fits the moment and is not the piece just played; when only that piece
 * fits, another piece; the one piece again only when the soundtrack has no other.
 */
export function selectNextPieceId(
  pieces: readonly DirectedPiece[],
  setting: string,
  moment: SoundtrackMoment,
  lastPieceId: string | null,
  random: () => number
): string | null {
  if (setting !== SOUNDTRACK_SETTING) {
    return pieces.some((piece) => piece.id === setting) ? setting : null;
  }
  const others = freshPieces(pieces, lastPieceId);
  const fitting = others.filter((piece) => pieceFitsMoment(piece, moment));
  const pool = fitting.length > 0 ? fitting : others.length > 0 ? others : pieces;
  return drawUniformItem(pool, random)?.id ?? null;
}
