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
 * `AUDIO_CREDIT_LINES` are for the credits beside the canvas while audio is on, with a link to
 * `AUDIO_NOTICES_URL`: the Apache License 2.0 text, Omnitone's NOTICE and the response's
 * attribution, served as a text file the way the reverb's response is. The display belongs to
 * the viewport; the notices file ships once the app imports this module.
 */

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
 * What the sound is, for the viewport to show beside the Audio control while audio is on. It states
 * what reaches a listener and makes no claim about space itself, and it calls nothing data that is
 * not: the reverb is a recording of an earthly room, and the detent click and the identity pitch
 * carry no data (AUDIO_ENGINE.md 4.8 and 12.4 (d)). The listening page opens with its own sentence,
 * which names the kinds of value its clips play.
 */
export const AUDIO_DISCLOSURE =
  'No sound reaches us from space. What you hear is made from data and stated design choices; none of it is a recording of space.';

/** The credit lines, in order, for the credits beside the canvas while audio is on. */
export const AUDIO_CREDIT_LINES: readonly string[] = AUDIO_CREDITS.map((credit) => credit.line);

/** The full notices (licence text, Omnitone's NOTICE, the response's attribution), for a Notices link. */
export const AUDIO_NOTICES_URL: string = new URL('./assets/THIRD_PARTY_NOTICES.txt', import.meta.url).href;
