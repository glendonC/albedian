import { createHash } from 'node:crypto';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';
import { APP_AUDIO_CREDIT_LINES, AUDIO_CREDITS, AUDIO_CREDIT_LINES, AUDIO_DISCLOSURE, AUDIO_NOTICES_URL, audioCreditLines } from '../audioCredits';
import { DEFAULT_IMPULSE_RESPONSE_URL } from '../engine/ConvolutionReverb';
import { RECORDING } from '../music/directions';
import { APP_MUSIC_DIRECTION, MUSIC_DIRECTIONS, musicCreditLines } from '../music/musicDirection';
import { observatoryAudio } from '../ObservatoryAudioEngine';

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

  it("quotes Omnitone's NOTICE exactly as published at its v2.0.0 tag", () => {
    const notice = section('NOTICE');
    // src/resources/NOTICE at GoogleChrome/omnitone v2.0.0 (747 bytes)
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

  it('uses timeless provenance wording with no session dates or pre-wiring lane drift in notices or AUDIO_ENGINE.md', () => {
    const doc = read('docs/architecture/AUDIO_ENGINE.md');
    const attribution = read('apps/web/src/audio/assets/ATTRIBUTION.txt');
    const creditsSource = read('apps/web/src/audio/audioCredits.ts');
    for (const [name, text] of [
      ['THIRD_PARTY_NOTICES.txt', notices],
      ['ATTRIBUTION.txt', attribution],
      ['audioCredits.ts', creditsSource],
      ['AUDIO_ENGINE.md', doc],
    ] as const) {
      expect(text, `${name} session dates`).not.toMatch(/2026-11-\d\d|November 2026/);
      expect(text, `${name} lane drift`).not.toMatch(/render lane|not in the app yet|Before 2026-|Until 2026-/);
      expect(text, `${name} em dash`).not.toMatch(/\u2014/);
    }
    expect(notices.replace(/\s+/g, ' ')).toContain('shown beside the canvas while audio is on');
    expect(doc).toContain('SpatialObservatoryViewport.tsx');
    expect(doc).toContain('AUDIO_NOTICES_URL');
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

describe('the credits and notices of the music that plays', () => {
  const sources = JSON.parse(read('apps/web/spike/music/sources.json')) as {
    vcslCommit: string;
    files: { path: string; sha256: string; licencePage: string; credit: string }[];
  };

  it('shows the engine credits alone without music, and adds the playing direction credits after them', () => {
    expect(audioCreditLines(null)).toEqual(AUDIO_CREDIT_LINES);
    for (const id of MUSIC_DIRECTIONS) {
      expect(audioCreditLines(id)).toEqual([...AUDIO_CREDIT_LINES, ...musicCreditLines(id)]);
      expect(musicCreditLines(id).length).toBeGreaterThan(0);
    }
  });

  it('shows the credits of the direction the app engine plays', () => {
    expect(observatoryAudio.getMusicDirection()).toBe(APP_MUSIC_DIRECTION);
    expect(APP_AUDIO_CREDIT_LINES).toEqual(audioCreditLines(APP_MUSIC_DIRECTION));
  });

  it('in development, plays and credits the direction the address names', async () => {
    vi.resetModules();
    vi.stubGlobal('window', { location: { search: '?music=recording' } });
    try {
      const credits = await import('../audioCredits');
      const engine = await import('../ObservatoryAudioEngine');
      expect(engine.observatoryAudio.getMusicDirection()).toBe('recording');
      expect(credits.APP_AUDIO_CREDIT_LINES).toEqual([...AUDIO_CREDIT_LINES, ...musicCreditLines('recording')]);
    } finally {
      vi.unstubAllGlobals();
      vi.resetModules();
    }
  });

  it('carries a notice for every source a direction credits, with its licence and where it came from', () => {
    const music = section('MUSIC');
    for (const id of MUSIC_DIRECTIONS) {
      for (const line of musicCreditLines(id)) if (line !== 'Music: Albedian') expect(music, `${id}: ${line}`).toContain(line);
    }
    expect(music).toContain(`github.com/sgossner/VCSL at commit ${sources.vcslCommit}`);
    expect(music).toContain('https://creativecommons.org/publicdomain/zero/1.0/');
    // CC BY 4.0 asks for the creator, the licence, a link and a note of the changes made
    const recording = sources.files.find((f) => f.path === RECORDING.file)!;
    expect(music).toContain(RECORDING.credit);
    expect(music).toContain(recording.sha256);
    expect(music).toContain(recording.licencePage);
    expect(music).toMatch(/excerpt[^.]*faded in and out/);
    expect(music).not.toMatch(/\u2014/);
  });
});
