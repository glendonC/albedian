import crypto from 'node:crypto';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const repoFile = (rel: string) => fileURLToPath(new URL(`../../../../../../${rel}`, import.meta.url));
const TEXTURES = 'apps/web/public/textures';
const sources = JSON.parse(fs.readFileSync(repoFile(`${TEXTURES}/sky_texture_sources.json`), 'utf8')) as Record<
  string,
  { sha256?: string }
>;

describe('sky dome textures (sky_texture_sources.json)', () => {
  it('records what the dome draws and nothing else: the ESO panorama and its registration', () => {
    expect(Object.keys(sources).sort()).toEqual(['eso0932a_registration.json', 'eso_milkyway_panorama.jpg']);
  });

  it('matches each recorded checksum to the file on disk', () => {
    const pinned = Object.entries(sources).filter(([, entry]) => entry.sha256 !== undefined);
    expect(pinned.length).toBeGreaterThan(0);
    for (const [file, entry] of pinned) {
      const digest = crypto.createHash('sha256').update(fs.readFileSync(repoFile(`${TEXTURES}/${file}`))).digest('hex');
      expect(digest, file).toBe(entry.sha256);
    }
  });

  it('ships no plotted star map: the stars are drawn from the Gaia DR3 and Hipparcos 2007 tiles', () => {
    expect(fs.readdirSync(repoFile(TEXTURES)).filter((name) => /starmap/i.test(name))).toEqual([]);
  });
});
