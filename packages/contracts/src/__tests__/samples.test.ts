import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { SampleNightSchema, SampleRecordSchema } from '../samples';

/**
 * first-use-sample-zero: the sample night's tracked record read by its contract here as services/contracts/samples.py
 * reads it (tests/integration/test_samples_contract.py holds the photo to its data/downloads.json entry).
 */
const ROOT = path.resolve(__dirname, '../../../..');
const record = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/samples.json'), 'utf8')) as { samples: Array<Record<string, unknown>> };

describe('@albedian/contracts: the sample night', () => {
  it('the tracked record meets the contract: one sample, said to be one', () => {
    const parsed = SampleRecordSchema.parse(record);
    expect(parsed.samples.map((s) => [s.id, s.kind, s.title])).toEqual([['night-rp-b-12', 'sample', 'Venus, Moon, Orion']]);
    expect(parsed.samples[0]!.credit).toMatchObject({ author: 'YagnKambariya', licence: 'CC0' });
  });

  it.each([
    ['not a sample', { kind: 'upload' }],
    ['an id of another shape', { id: 'Night 12' }],
    ['a photo from the uploads', { photo: { ...(record.samples[0]!.photo as object), downloadPath: 'data/uploads/x.jpg' } }],
    ['a short hash', { photo: { ...(record.samples[0]!.photo as object), sha256: 'abc' } }],
    ['a solution kept where git tracks it', { solution: { ...(record.samples[0]!.solution as object), path: 'tests/fixtures/sample.json' } }],
    ['an unknown key', { extra: 1 }],
  ])('refuses %s', (_label, change) => {
    expect(SampleNightSchema.safeParse({ ...record.samples[0], ...change }).success).toBe(false);
  });
});
