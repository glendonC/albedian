import { createHash } from 'node:crypto';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { AUDIO_CREDITS, AUDIO_CREDIT_LINES, AUDIO_DISCLOSURE, AUDIO_NOTICES_URL } from '../audioCredits';
import { DEFAULT_IMPULSE_RESPONSE_URL } from '../engine/ConvolutionReverb';

const repoFile = (rel: string) => fileURLToPath(new URL(`../../../../../${rel}`, import.meta.url));
const read = (rel: string) => fs.readFileSync(repoFile(rel), 'utf8');

/** The notices file the credits link to, as the app serves it. */
const notices = fs.readFileSync(fileURLToPath(AUDIO_NOTICES_URL), 'utf8');

/** The text between `----- BEGIN <name> -----` and `----- END <name> -----`, exactly. */
function section(name: string): string {
  const begin = `----- BEGIN ${name} -----\n`;
  const end = `----- END ${name} -----\n`;
  const start = notices.indexOf(begin);
  const stop = notices.indexOf(end);
  expect(start, `${name} section`).toBeGreaterThanOrEqual(0);
  expect(stop, `${name} section end`).toBeGreaterThan(start);
  expect(notices.indexOf(begin, start + 1), `one ${name} section`).toBe(-1);
  return notices.slice(start + begin.length, stop);
}

describe('audio credits and notices', () => {
  it('credits each third-party work the engine loads: Omnitone, its SADIE filters and the chapel response', () => {
    expect(AUDIO_CREDITS.map((credit) => credit.id)).toEqual(['omnitone', 'sadie-hrirs', 'chapel-impulse-response']);
    expect(AUDIO_CREDIT_LINES).toEqual(AUDIO_CREDITS.map((credit) => credit.line));
    // What the engine loads: the reverb's default response and the bed's Omnitone module
    expect(DEFAULT_IMPULSE_RESPONSE_URL).toContain('aachen-nikolauskapelle-s1-r12-stereo.wav');
    expect(read('apps/web/src/audio/engine/SkyglowBed.ts')).toContain("import('omnitone')");
    expect(read('apps/web/package.json')).toContain('"omnitone": "^2.0.0"');
  });

  it('words each line as its source does', () => {
    const [omnitone, sadie, chapel] = AUDIO_CREDITS;
    expect(omnitone).toMatchObject({ licence: 'Apache-2.0' });
    expect(omnitone!.line).toContain('Omnitone');
    expect(omnitone!.line).toContain('Google');
    // Omnitone's NOTICE: "Google/SADIE binaural filters", "Copyright (c) 2017, University of York."
    expect(sadie).toMatchObject({ licence: 'Apache-2.0' });
    expect(sadie!.line).toContain('Google/SADIE binaural filters');
    expect(sadie!.line).toContain('Copyright (c) 2017, University of York');
    // CC BY 4.0 asks for the creators, the licence and a note that the material was changed
    expect(chapel).toMatchObject({ licence: 'CC-BY-4.0' });
    for (const part of ['Martin Zerwas', 'Selin Kayku', 'St. Nicholas Chapel', 'doi:10.5281/zenodo.23312556', 'CC BY 4.0', 'modified']) {
      expect(chapel!.line).toContain(part);
    }
    for (const line of AUDIO_CREDIT_LINES) expect(line).not.toMatch(/\u2014/);
  });

  it('ships the Apache License 2.0 exactly as the omnitone package carries it', () => {
    expect(section('LICENSE')).toBe(read('apps/web/node_modules/omnitone/LICENSE'));
    expect(section('LICENSE')).toContain('Version 2.0, January 2004');
  });

  it("quotes Omnitone's NOTICE exactly as retrieved at its v2.0.0 tag", () => {
    const notice = section('NOTICE');
    // src/resources/NOTICE at GoogleChrome/omnitone v2.0.0, retrieved 2026-11-04 (747 bytes)
    expect(createHash('sha256').update(notice, 'utf8').digest('hex')).toBe(
      '2a35afd9c4c6d2fc9271a25ccc3c1442e0cf61c3284265722f158b78f33808a8'
    );
    expect(notice).toContain('Copyright (c) 2017, University of York.');
    expect(notices).toContain('2a35afd9c4c6d2fc9271a25ccc3c1442e0cf61c3284265722f158b78f33808a8');
  });

  it("carries the chapel response's attribution exactly as recorded beside the file", () => {
    expect(section('ATTRIBUTION')).toBe(read('apps/web/src/audio/assets/ATTRIBUTION.txt'));
    expect(notices).toContain('https://creativecommons.org/licenses/by/4.0/');
  });

  it('names Google as the holder of the Omnitone code, as its source headers do, and has no em dash', () => {
    expect(notices).toContain('Copyright 2016 Google Inc. All Rights Reserved.');
    expect(notices).toContain('Copyright 2017 Google Inc. All Rights Reserved.');
    expect(notices).not.toMatch(/\u2014/);
  });

  it('says what the sound is beside the audio, and the listening page opens with its own version', () => {
    // Not every sound is data: the reverb is a recorded room, the click and the identity pitch carry none
    expect(AUDIO_DISCLOSURE).toBe(
      'No sound reaches us from space. What you hear is made from data and stated design choices; none of it is a recording of space.'
    );
    // The page names its value kinds, since some clips play labelled test values. The sentence must be
    // the page's own paragraph: commented-out markup or a sentence left in a comment does not count.
    const page =
      'No sound reaches us from space. Each clip is made from the values it names (catalog, published or labelled test values) with stated design choices; none is a recording of space.';
    const lines = read('apps/web/spike/listening/render.mjs')
      .replace(/<!--[\s\S]*?-->/g, '')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .split('\n')
      .map((line) => line.trim());
    expect(lines).toContain(`<p class="lede">${page}</p>`);
  });
});
