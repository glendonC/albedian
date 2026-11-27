import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { SampleAssetSchema, SampleNightSchema, sampleAssetMatches } from '../samples';

/**
 * sample-asset-zero: a sample night's photo as the app reads it, the SourceAsset its own route serves (a public sample,
 * an image, shown from that route, under the id its kept solve names), read here as services/contracts/samples.py
 * reads it, through the shared fixture tests/integration/test_sample_asset_contract.py writes.
 */
const ROOT = path.resolve(__dirname, '../../../..');
const fixture = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests/fixtures/sample_asset_contract.json'), 'utf8')) as {
  night: unknown;
  cases: Array<{ what: string; value: Record<string, unknown>; accepted: boolean; matchesTheNight: boolean }>;
};
const night = SampleNightSchema.parse(fixture.night);
const base = fixture.cases.find((c) => c.what === "the night's photo")!.value;

describe('@albedian/contracts: a sample night\'s photo as the app reads it', () => {
  it('answers every case of the shared fixture as Python does', () => {
    expect(fixture.cases.length).toBeGreaterThanOrEqual(13);
    for (const c of fixture.cases) {
      const parsed = SampleAssetSchema.safeParse(c.value);
      expect(parsed.success, c.what).toBe(c.accepted);
      expect(parsed.success && sampleAssetMatches(night, parsed.data), c.what).toBe(c.matchesTheNight);
    }
  });

  it.each([
    ['an upload\'s privacy', { privacyClass: 'private' }, "a sample's photo is a public sample"],
    ['a clip', { mediaKind: 'video' }, 'a sample night is a photo'],
    ['an upload\'s route', { uri: `/api/ingest/${night.photo.sha256}/original` }, "a sample's photo is shown from its own route"],
    ['its route with a newline after it', { uri: `/api/samples/${night.id}/original\n` }, "a sample's photo is shown from its own route"],
    ['another hash\'s id', { id: 'src_0000000000000000' }, "a sample's asset id is the one its solve names"],
  ])('refuses %s in the words Python refuses it with', (_label, change, message) => {
    const parsed = SampleAssetSchema.safeParse({ ...base, ...change });
    expect(parsed.success).toBe(false);
    expect(parsed.error?.issues.map((issue) => issue.message)).toContain(message);
  });

  it('keeps every SourceAsset rule: a still with a frame rate is refused', () => {
    expect(SampleAssetSchema.safeParse({ ...base, fpsNumerator: 30, fpsDenominator: 1 }).success).toBe(false);
  });

  it('holds an asset with no byte count or type not to be the night\'s photo', () => {
    const { byteSize: _bytes, ...noSize } = base;
    const { mimeType: _type, ...noType } = base;
    expect(sampleAssetMatches(night, SampleAssetSchema.parse(noSize))).toBe(false);
    expect(sampleAssetMatches(night, SampleAssetSchema.parse(noType))).toBe(false);
  });
});
