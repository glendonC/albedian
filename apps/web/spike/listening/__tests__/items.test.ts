import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { CatalogStarNameIndexSchema, CatalogStarTileManifestSchema } from '@albedian/contracts';
import { decodeAstroTile, decodeCoreTile } from '../../../src/spatial/catalogStars/tileCodec';
import { LOUDNESS } from '../../../src/audio/mappings';
import { LISTENING_ITEMS, TEST_VALUE_LABEL, type StatedValue, type Voice } from '../items';

const repoFile = (rel: string) => fileURLToPath(new URL(`../../../../../${rel}`, import.meta.url));
const readBuffer = (rel: string): ArrayBuffer => {
  const bytes = fs.readFileSync(repoFile(rel));
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
};
const RELEASE = 'apps/web/public/catalog/stars/gaia-dr3';
const manifest = CatalogStarTileManifestSchema.parse(JSON.parse(fs.readFileSync(repoFile(`${RELEASE}/manifest.json`), 'utf8')));
const names = CatalogStarNameIndexSchema.parse(JSON.parse(fs.readFileSync(repoFile(`${RELEASE}/names.json`), 'utf8')));

const clips = LISTENING_ITEMS.flatMap((item) => item.clips);
const voices: Voice[] = clips.flatMap((clip) =>
  clip.steps.flatMap((step) => (Array.isArray(step.sounds) ? (step.sounds as readonly Voice[]) : []))
);

function statedValues(): StatedValue[] {
  const values: StatedValue[] = [];
  for (const clip of clips) {
    for (const step of clip.steps) if (step.skyBrightness) values.push(step.skyBrightness);
  }
  for (const voice of voices) {
    if (voice.kind === 'star') {
      if (voice.extinctionAvMag) values.push(voice.extinctionAvMag);
      if (voice.residualArcsec) values.push(voice.residualArcsec);
    } else {
      values.push(voice.spinFrequencyHz, voice.distancePc, voice.luminositySolar);
    }
  }
  return values;
}

describe('the listening session items', () => {
  it("asks the first listener's impression first, then each of section 10's thirteen items once, in order", () => {
    expect(LISTENING_ITEMS.map((item) => item.section10)).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]);
    expect(LISTENING_ITEMS[0]!.question).toContain('static');
    expect(new Set(LISTENING_ITEMS.map((item) => item.id)).size).toBe(LISTENING_ITEMS.length);
    expect(new Set(clips.map((clip) => clip.id)).size).toBe(clips.length);
    for (const item of LISTENING_ITEMS) {
      expect(item.question.length, item.id).toBeGreaterThan(20);
      expect(item.clips.length, item.id).toBeGreaterThan(0);
    }
  });

  it('starts every clip with what it plays, and keeps every step inside the clip', () => {
    for (const clip of clips) {
      expect(clip.steps[0]?.atSec, clip.id).toBe(0);
      expect(clip.steps[0]?.sounds, clip.id).toBeDefined();
      for (let i = 1; i < clip.steps.length; i++) expect(clip.steps[i]!.atSec).toBeGreaterThan(clip.steps[i - 1]!.atSec);
      expect(clip.steps.at(-1)!.atSec, clip.id).toBeLessThan(clip.durationSec);
    }
  });

  it('labels every value that is not a catalog star: published with its source, or a test value with its reference', () => {
    const values = statedValues();
    expect(values.length).toBeGreaterThan(0);
    for (const value of values) {
      expect(Number.isFinite(value.value)).toBe(true);
      expect(value.reference.length).toBeGreaterThan(10);
      if (value.kind === 'test') expect(value.label).toBe(TEST_VALUE_LABEL);
      else expect(value.label.length).toBeGreaterThan(0);
    }
    // The channels with no data in the app are exactly the ones on test values
    const testUnits = new Set(values.filter((value) => value.kind === 'test').map((value) => value.unit));
    expect([...testUnits].sort()).toEqual(['V mag/arcsec^2', 'arcsec', 'mag']);
  });

  it('plays only catalog stars that Gaia DR3 gives a temperature, a luminosity and a distance', () => {
    const stars = new Set(voices.flatMap((voice) => (voice.kind === 'star' ? [voice.star] : [])));
    expect(stars.size).toBeGreaterThan(10);
    for (const star of stars) {
      const entry = names.names.find((n) => n.nameAscii === star);
      expect(entry, star).toBeDefined();
      expect(entry!.catalog, star).toBe('gaia-dr3');
      const order = manifest.layers[entry!.layer]!.order;
      const tile = { layer: entry!.layer, order, pix: entry!.pix };
      const core = decodeCoreTile(readBuffer(`${RELEASE}/L${tile.layer}/${tile.pix}.core.bin`), tile);
      const astro = decodeAstroTile(readBuffer(`${RELEASE}/L${tile.layer}/${tile.pix}.astro.bin`), tile);
      const i = entry!.index;
      expect(Number.isFinite(core.distancePc[i]), `${star} distance`).toBe(true);
      expect(Number.isFinite(astro.teffK[i]), `${star} temperature`).toBe(true);
      expect(Number.isFinite(astro.luminositySolar[i]), `${star} luminosity`).toBe(true);
    }
  });

  it('holds levels only inside the range the engine plays', () => {
    for (const voice of voices) {
      if (voice.kind !== 'star' || voice.holdLevelDb === undefined) continue;
      expect(voice.holdLevelDb).toBeGreaterThan(LOUDNESS.floorDb);
      expect(voice.holdLevelDb).toBeLessThanOrEqual(LOUDNESS.ceilingDb);
    }
    for (const voice of voices) {
      if (voice.kind === 'star' && voice.movedToPc !== undefined) expect(voice.holdLevelDb, 'a moved star holds its level').toBeDefined();
    }
  });
});
