import { createHash } from 'node:crypto';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';
import {
  AUDIO_CREDITS,
  AUDIO_CREDIT_LINES,
  AUDIO_DISCLOSURE,
  AUDIO_DISCLOSURE_WITHOUT_MUSIC,
  AUDIO_NOTICES_URL,
  HeardMusic,
  MusicAvailability,
  appHeardMusic,
  appMusicAvailability,
  audioCreditLines,
  audioDisclosure,
} from '../audioCredits';
import { DEFAULT_IMPULSE_RESPONSE_URL } from '../engine/ConvolutionReverb';
import { RECORDING } from '../music/directions';
import { appMusicSetting } from '../music/musicSetting';
import { SOUNDTRACK, pieceCreditLines } from '../music/soundtrack';
import { appSoundtrackMoment } from '../music/soundtrackMoment';
import { APP_AUDIO_OPTIONS, observatoryAudio } from '../ObservatoryAudioEngine';

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
    expect(AUDIO_DISCLOSURE_WITHOUT_MUSIC).toBe(
      'No sound reaches us from space. What you hear is made from data and stated design choices; none of it is a recording of space.'
    );
    // The soundtrack is the default Music setting
    expect(AUDIO_DISCLOSURE).toBe(audioDisclosure('soundtrack'));
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

describe('what the sound is, with the music', () => {
  it('says plainly that composed music, and in the recording piece a recorded track, plays alongside the star notes', () => {
    expect(audioDisclosure('off')).toBe(AUDIO_DISCLOSURE_WITHOUT_MUSIC);
    expect(audioDisclosure('soundtrack')).toBe(
      'No sound reaches us from space. The star notes are made from catalog data and stated design choices. Composed music plays alongside them, and in Signal to Noise a recorded track. None of it is a recording of space.'
    );
    for (const pinned of ['organ-pulse', 'ambient-beats']) {
      expect(audioDisclosure(pinned)).toBe(
        'No sound reaches us from space. The star notes are made from catalog data and stated design choices. Composed music plays alongside them. None of it is a recording of space.'
      );
    }
    expect(audioDisclosure('recording')).toBe(
      'No sound reaches us from space. The star notes are made from catalog data and stated design choices. A recorded track, Signal to Noise, plays alongside them. None of it is a recording of space.'
    );
    for (const setting of ['off', 'soundtrack', ...SOUNDTRACK.map((piece) => piece.id)]) expect(audioDisclosure(setting)).not.toMatch(/\u2014/);
  });

  it('says no music plays wherever none can, whatever the setting, and why when the files cannot be reached', () => {
    for (const setting of ['off', 'soundtrack', ...SOUNDTRACK.map((piece) => piece.id)]) {
      // No sources base (a build that serves no music): the plain sentence
      expect(audioDisclosure(setting, 'not_served')).toBe(AUDIO_DISCLOSURE_WITHOUT_MUSIC);
      // A sources base whose files cannot be fetched: the plain reason after it
      expect(audioDisclosure(setting, 'not_reachable')).toBe('No sound reaches us from space. What you hear is made from data and stated design choices; none of it is a recording of space. The music files are not reachable, so no music plays.');
    }
    expect(audioDisclosure('soundtrack', 'available')).toBe(AUDIO_DISCLOSURE);
    expect(audioDisclosure('soundtrack', 'not_reachable')).not.toMatch(/[\u2013\u2014]/);
  });

  it('keeps whether music can play on this page and why not, and tells its listeners of a change', () => {
    const available = new MusicAvailability('available');
    const listener = vi.fn();
    available.subscribe(listener);
    available.set('available');
    available.set('not_reachable');
    available.set('not_reachable');
    available.set('not_served');
    expect(available.get()).toBe('not_served');
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("follows the app engine's music: available, not served (no sources base) or not reachable (a file could not be fetched)", () => {
    const status = observatoryAudio.getMusicStatus();
    expect(appMusicAvailability.get()).toBe(status.state === 'unavailable' ? status.why : 'available');
    APP_AUDIO_OPTIONS.onMusicStatus?.({ ...status, state: 'unavailable', why: 'not_served', reason: 'the music samples are not served here' });
    expect(appMusicAvailability.get()).toBe('not_served');
    APP_AUDIO_OPTIONS.onMusicStatus?.({ ...status, state: 'unavailable', why: 'not_reachable', reason: 'Failed to fetch' });
    expect(appMusicAvailability.get()).toBe('not_reachable');
    APP_AUDIO_OPTIONS.onMusicStatus?.({ ...status, state: 'loading', why: null, reason: null });
    expect(appMusicAvailability.get()).toBe('available');
  });
});

describe('the credits and notices of the music that plays', () => {
  const sources = JSON.parse(read('apps/web/spike/music/sources.json')) as {
    vcslCommit: string;
    files: { path: string; sha256: string; licencePage: string; credit: string }[];
  };

  it('shows the engine credits alone before any music is heard, then adds each piece heard, its lines once', () => {
    expect(audioCreditLines([])).toEqual(AUDIO_CREDIT_LINES);
    expect(audioCreditLines(['recording'])).toEqual([...AUDIO_CREDIT_LINES, ...pieceCreditLines('recording')]);
    // The composed pieces share their head line and VCSL's credit: each line shows once, first heard first
    const both = audioCreditLines(['ambient-beats', 'organ-pulse']);
    expect(new Set(both).size).toBe(both.length);
    expect(both).toEqual([...AUDIO_CREDIT_LINES, ...new Set([...pieceCreditLines('ambient-beats'), ...pieceCreditLines('organ-pulse')])]);
    for (const piece of SOUNDTRACK) {
      expect(pieceCreditLines(piece.id).length).toBeGreaterThan(0);
      for (const line of pieceCreditLines(piece.id)) expect(audioCreditLines([piece.id])).toContain(line);
    }
  });

  it('keeps the pieces heard this visit in the order first heard, and tells its listeners of each new one', () => {
    const heard = new HeardMusic();
    const listener = vi.fn();
    const unsubscribe = heard.subscribe(listener);
    expect(heard.get()).toEqual([]);
    heard.add('ambient-beats');
    heard.add('recording');
    heard.add('ambient-beats');
    expect(heard.get()).toEqual(['ambient-beats', 'recording']);
    expect(listener).toHaveBeenCalledTimes(2);
    unsubscribe();
    heard.add('organ-pulse');
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("records every piece the app engine starts for the credits, and plays the page's Music setting and moment", () => {
    const before = appHeardMusic.get();
    APP_AUDIO_OPTIONS.onMusicPieceStarted?.('recording');
    expect(appHeardMusic.get()).toContain('recording');
    expect(audioCreditLines(appHeardMusic.get())).toContain(RECORDING.credit);
    expect(appHeardMusic.get().slice(0, before.length)).toEqual(before);

    expect(observatoryAudio.getMusicSetting()).toBe(appMusicSetting.get());
    const was = appMusicSetting.get();
    try {
      appMusicSetting.set('off');
      expect(observatoryAudio.getMusicSetting()).toBe('off');
      appMusicSetting.set('ambient-beats');
      expect(observatoryAudio.getMusicSetting()).toBe('ambient-beats');
    } finally {
      appMusicSetting.set(was);
    }

    const moment = appSoundtrackMoment.get();
    expect(observatoryAudio.getMusicStatus().moment).toBe(moment);
    appSoundtrackMoment.set(moment === 'travel' ? 'reveal' : 'travel');
    expect(observatoryAudio.getMusicStatus().moment).toBe(appSoundtrackMoment.get());
    appSoundtrackMoment.set(moment);
  });

  it('in development, plays the music the address names for that load', async () => {
    vi.resetModules();
    vi.stubGlobal('window', { location: { search: '?music=recording' } });
    try {
      const setting = await import('../music/musicSetting');
      const engine = await import('../ObservatoryAudioEngine');
      expect(setting.appMusicSetting.get()).toBe('recording');
      expect(engine.observatoryAudio.getMusicSetting()).toBe('recording');
    } finally {
      vi.unstubAllGlobals();
      vi.resetModules();
    }
  });

  it('carries a notice for every source a piece credits, with its licence and where it came from', () => {
    const music = section('MUSIC');
    for (const piece of SOUNDTRACK) {
      for (const line of pieceCreditLines(piece.id)) if (line !== 'Music: Albedian') expect(music, `${piece.id}: ${line}`).toContain(line);
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
