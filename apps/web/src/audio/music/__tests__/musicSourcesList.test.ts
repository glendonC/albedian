import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { INSTRUMENTS, RECORDING } from '../directions.js';

// music-sources-fetch-one (LEAD ANSWER 15:49 on 24 Nov): a clean clone fetches the music's samples with
// scripts/fetch_music_sources.py from the tracked list data/music_sources.json. The list names exactly the files the
// app plays: every zone of every instrument and the recording, no more (nothing is fetched that the app does not use)
// and no less (no sample is missing from a clean clone). tests/integration/test_fetch_music_sources.py checks each
// entry against the catalogue, apps/web/spike/music/sources.json.

const here = path.dirname(fileURLToPath(import.meta.url));
const listing = JSON.parse(fs.readFileSync(path.resolve(here, '../../../../../../data/music_sources.json'), 'utf8')) as {
  downloads: Array<{ path: string }>;
};
const PREFIX = '.cache/music/sources/';

describe("the tracked list of the music's samples", () => {
  it('names each file the app plays once, and nothing else', () => {
    const played = new Set<string>([RECORDING.file]);
    for (const instrument of Object.values(INSTRUMENTS)) for (const zone of instrument.zones) played.add(zone.file);
    const listed = listing.downloads.map((entry) => entry.path);
    expect(listed.every((entry) => entry.startsWith(PREFIX))).toBe(true);
    expect(new Set(listed).size).toBe(listed.length);
    expect([...listed.map((entry) => entry.slice(PREFIX.length))].sort()).toEqual([...played].sort());
  });
});
