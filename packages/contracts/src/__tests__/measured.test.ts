import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { MeasuredDetectionsSchema } from '../measured';

/** wait-detections-one: the measured route's payload read as tests/integration/test_measured_contract.py reads it. */
const ROOT = path.resolve(__dirname, '../../../..');
const fixture = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests/fixtures/measured_detections_contract.json'), 'utf8')) as {
  cases: Array<{ what: string; value: unknown; accepted: boolean }>;
};

describe('@albedian/contracts: the measured detections a running solve publishes', () => {
  it('answers every shared case as Python does', () => {
    expect(fixture.cases.length).toBe(6);
    for (const c of fixture.cases) expect(MeasuredDetectionsSchema.safeParse(c.value).success, c.what).toBe(c.accepted);
  });
});
