/**
 * Credits for the third-party works the audio engine ships, worded as each source asks, and the
 * full notices behind them (AUDIO_ENGINE.md section 7).
 *
 * - Omnitone (npm `omnitone` 2.0.0), copyright Google, Apache-2.0: the ambisonic bed's renderer,
 *   loaded only after the user enables audio.
 * - The Google/SADIE binaural filters inside Omnitone, copyright 2017 University of York,
 *   Apache-2.0, as Omnitone's NOTICE file states.
 * - The St. Nicholas Chapel impulse response the reverb loads, measured by Martin Zerwas and
 *   Selin Kayku, CC BY 4.0, changed for Albedian as `assets/ATTRIBUTION.txt` records.
 *
 * `AUDIO_CREDIT_LINES` are shown beside the canvas while audio is on, with a link to
 * `AUDIO_NOTICES_URL`: the Apache License 2.0 text, Omnitone's NOTICE and the response's
 * attribution, served as a text file the way the reverb's response is. `SpatialObservatoryViewport.tsx`
 * renders the disclosure, the credit lines and the Notices link while audio is enabled, and the
 * notices file ships in the build (`dist/assets/THIRD_PARTY_NOTICES-*.txt`).
 *
 * Each soundtrack piece heard this visit adds its own lines (`pieceCreditLines`, music/soundtrack.ts):
 * VCSL's samples (CC0 1.0) for the organ and pulse and the ambient beats, and for Signal to Noise
 * Scott Buckley's recording (CC BY 4.0) with VCSL's glockenspiel. Once a piece has played its
 * lines stay for the rest of the visit. Their notices are the MUSIC section of the same file.
 */

import { SOUNDTRACK, pieceCreditLines, type PieceId } from './music/soundtrack.js';

export interface AudioCredit {
  /** The work credited, as the engine loads it. */
  readonly id: 'omnitone' | 'sadie-hrirs' | 'chapel-impulse-response';
  /** The line shown while audio is on. */
  readonly line: string;
  readonly licence: 'Apache-2.0' | 'CC-BY-4.0';
}

export const AUDIO_CREDITS: readonly AudioCredit[] = [
  {
    id: 'omnitone',
    line: 'Omnitone, Copyright Google Inc., Apache-2.0',
    licence: 'Apache-2.0',
  },
  {
    id: 'sadie-hrirs',
    line: 'Google/SADIE binaural filters, Copyright (c) 2017, University of York, Apache-2.0',
    licence: 'Apache-2.0',
  },
  {
    id: 'chapel-impulse-response',
    line: 'Reverb: St. Nicholas Chapel, Aachen Cathedral, measured by Martin Zerwas and Selin Kayku, doi:10.5281/zenodo.23312556, CC BY 4.0, modified',
    licence: 'CC-BY-4.0',
  },
];

/**
 * What the sound is with the Music setting off, for the viewport to show beside the Audio control
 * while audio is on. It states what reaches a listener and makes no claim about space itself, and it
 * calls nothing data that is not: the reverb is a recording of an earthly room, and the detent click
 * and the identity pitch carry no data (AUDIO_ENGINE.md 4.8 and 12.4 (d)). The listening page opens
 * with its own sentence, which names the kinds of value its clips play.
 */
export const AUDIO_DISCLOSURE_WITHOUT_MUSIC =
  'No sound reaches us from space. What you hear is made from data and stated design choices; none of it is a recording of space.';

/**
 * What the sound is for a Music setting: with music, it says plainly that composed music, and in the
 * recording piece a recorded track, plays alongside the star notes, from what each piece the setting
 * can play declares (`alongside` in music/soundtrack.ts). Where no music can play (`canPlay` false:
 * no samples are served, or a file the music needs is missing) it says what plays without music.
 */
export function audioDisclosure(setting: string, canPlay = true): string {
  const pieces = !canPlay || setting === 'off' ? [] : SOUNDTRACK.filter((piece) => setting === 'soundtrack' || piece.id === setting);
  if (pieces.length === 0) return AUDIO_DISCLOSURE_WITHOUT_MUSIC;
  const composed = pieces.some((piece) => piece.alongside.composedMusic);
  const recorded = pieces.filter((piece) => piece.alongside.recordedTrack).map((piece) => piece.title).join(' and ');
  const alongside =
    composed && recorded
      ? `Composed music plays alongside them, and in ${recorded} a recorded track.`
      : composed
        ? 'Composed music plays alongside them.'
        : recorded
          ? `A recorded track, ${recorded}, plays alongside them.`
          : '';
  return ['No sound reaches us from space.', 'The star notes are made from catalog data and stated design choices.', alongside, 'None of it is a recording of space.']
    .filter((sentence) => sentence !== '')
    .join(' ');
}

/** What the sound is with the soundtrack, the default Music setting. */
export const AUDIO_DISCLOSURE = audioDisclosure('soundtrack');

/** The credit lines, in order, for the credits beside the canvas while audio is on. */
export const AUDIO_CREDIT_LINES: readonly string[] = AUDIO_CREDITS.map((credit) => credit.line);

/**
 * The credit lines beside the canvas while audio is on: the engine's works, then the lines of each
 * piece heard, first heard first, each line once.
 */
export function audioCreditLines(heard: readonly string[]): readonly string[] {
  return [...AUDIO_CREDIT_LINES, ...new Set(heard.flatMap((piece) => pieceCreditLines(piece)))];
}

/** The soundtrack pieces heard this visit, in the order first heard, for the credits. */
export class HeardMusic {
  private heard: readonly PieceId[] = [];
  private readonly listeners = new Set<() => void>();

  readonly get = (): readonly PieceId[] => this.heard;

  readonly add = (piece: PieceId): void => {
    if (this.heard.includes(piece)) return;
    this.heard = [...this.heard, piece];
    for (const listener of [...this.listeners]) listener();
  };

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
}

/** The pieces this page's engine has played (`useAudioCreditLines` shows their credits). */
export const appHeardMusic = new HeardMusic();

/**
 * Whether music can play on this page: false where no samples are served (a production build without
 * VITE_ALBEDIAN_MUSIC_SOURCES) or a file the music needs could not be fetched. The disclosure and the
 * Music control follow it.
 */
export class MusicAvailability {
  private available: boolean;
  private readonly listeners = new Set<() => void>();

  constructor(initial: boolean) {
    this.available = initial;
  }

  readonly get = (): boolean => this.available;

  readonly set = (available: boolean): void => {
    if (available === this.available) return;
    this.available = available;
    for (const listener of [...this.listeners]) listener();
  };

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
}

/** This page's music availability; the app's engine keeps it (ObservatoryAudioEngine.ts). */
export const appMusicAvailability = new MusicAvailability(true);

/** The full notices (licence text, Omnitone's NOTICE, the response's attribution), for a Notices link. */
export const AUDIO_NOTICES_URL: string = new URL('./assets/THIRD_PARTY_NOTICES.txt', import.meta.url).href;
