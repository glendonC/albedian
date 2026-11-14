/**
 * Soundtrack piece catalog, arrangement bindings, and deduplicated third-party credit lines
 * (AUDIO_ENGINE.md 13.7).
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
} from '../directions.js';
import type { ScreenStar } from '../starNotes.js';
import type { DirectedPiece } from './types.js';

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

const COMPOSED_MUSIC_CREDIT = 'Music: Albedian';

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
    credits: creditLines(COMPOSED_MUSIC_CREDIT, [
      'organQuiet',
      'organLoud',
      'pedalQuiet',
      'pedalLoud',
      'blower',
      'chimes',
      'claves',
      'cymbalSwell',
    ]),
    alongside: { composedMusic: true, recordedTrack: false },
    arrange: async (stars) => organAndPulse(stars),
  },
  {
    id: 'ambient-beats',
    title: 'Ambient beats',
    moments: ['reveal', 'look_around', 'star_card'],
    credits: creditLines(COMPOSED_MUSIC_CREDIT, ['vibes', 'hats', 'clap', 'shaker']),
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
