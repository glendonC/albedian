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

export {
  SOUNDTRACK_MOMENTS,
  SOUNDTRACK_TIMINGS,
  type DirectedPiece,
  type DirectorCommand,
  type DirectorOptions,
  type DirectorPhase,
  type DirectorState,
  type DirectorTimings,
  type SoundtrackMoment,
} from './soundtrackModules/types.js';

export {
  SOUNDTRACK,
  pieceCreditLines,
  soundtrackPiece,
  type PieceId,
  type PieceSources,
  type SoundtrackPiece,
} from './soundtrackModules/soundtrackCatalog.js';

export { SoundtrackDirector } from './soundtrackModules/soundtrackDirector.js';
