import crypto from 'node:crypto';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { AssetProvenanceRecordSchema } from '@albedian/contracts';
import { describe, expect, it } from 'vitest';

const repoFile = (rel: string) => fileURLToPath(new URL(`../../../../../../${rel}`, import.meta.url));
const TEXTURES = 'apps/web/public/textures';
const record = AssetProvenanceRecordSchema.parse(
  JSON.parse(fs.readFileSync(repoFile(`${TEXTURES}/PROVENANCE.json`), 'utf8')),
);
const fileEntry = (path: string) => record.files.find((file) => file.path === path);
const source = (id: string) => record.sources.find((candidate) => candidate.id === id);

describe('sky dome textures (textures/PROVENANCE.json)', () => {
  it('records the ESO panorama as downloaded as is, with its credit and licence', () => {
    const panorama = fileEntry('eso_milkyway_panorama.jpg');
    expect(panorama?.producedBy).toEqual({ method: 'downloaded-as-is' });
    expect(panorama?.sources).toEqual(['eso0932a']);
    expect(source('eso0932a')).toMatchObject({ credit: 'ESO/S. Brunier', license: { spdx: 'CC-BY-4.0' } });
  });

  it('records the registration as fitted by its script from the panorama and the catalog stars', () => {
    const registration = fileEntry('eso0932a_registration.json');
    expect(registration?.producedBy).toMatchObject({ method: 'script', script: 'scripts/register_sky_panorama.py' });
    expect(registration?.sources).toEqual(['eso0932a', 'gaia-dr3', 'hipparcos-2007']);
  });

  it('matches each recorded checksum to the file on disk', () => {
    expect(record.files.length).toBeGreaterThan(0);
    for (const file of record.files) {
      const digest = crypto.createHash('sha256').update(fs.readFileSync(repoFile(`${TEXTURES}/${file.path}`))).digest('hex');
      expect(digest, file.path).toBe(file.sha256);
    }
  });

  it('keeps no older source manifest beside the record', () => {
    expect(fs.readdirSync(repoFile(TEXTURES)).filter((name) => name.endsWith('_sources.json'))).toEqual([]);
  });

  it('ships no plotted star map: the stars are drawn from the Gaia DR3 and Hipparcos 2007 tiles', () => {
    expect(fs.readdirSync(repoFile(TEXTURES)).filter((name) => /starmap/i.test(name))).toEqual([]);
  });
});
