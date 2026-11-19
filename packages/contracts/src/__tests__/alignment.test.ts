import fs from 'node:fs';
import path from 'node:path';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import * as contracts from '../index.js';
import {
  PERSON_ALIGNMENT_CONVENTION,
  PERSON_ALIGNMENT_FORMAT,
  PERSON_ALIGNMENT_FORMAT_VERSION,
  PERSON_ALIGNMENT_FOV_MAX_DEG,
  PERSON_ALIGNMENT_SOURCE,
  PERSON_ALIGNMENT_UNCERTAINTY_DEG,
  PersonAlignmentSchema,
} from '../alignment.js';
import { SKY_CONTEXT_CONVENTION } from '../sky-context.js';

/**
 * A person's alignment of the predicted sky (D24, "Solve from here"), the TypeScript half of its contract:
 * tests/fixtures/person_alignment_contract.json is written by tests/integration/test_person_alignment_contract.py,
 * and every verdict in it holds here too.
 */
interface Sample {
  name: string;
  alignment: unknown;
}
const fixture = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, '../../../../tests/fixtures/person_alignment_contract.json'), 'utf8'),
) as {
  format: string;
  formatVersion: number;
  source: string;
  uncertaintyDeg: number;
  fovMaxDeg: number;
  convention: Record<string, string>;
  valid: Sample[];
  invalid: Sample[];
};

describe('a person\'s alignment of the predicted sky', () => {
  it('states the same decisions as the Python mirror', () => {
    expect(PERSON_ALIGNMENT_FORMAT).toBe(fixture.format);
    expect(PERSON_ALIGNMENT_FORMAT_VERSION).toBe(fixture.formatVersion);
    expect(PERSON_ALIGNMENT_SOURCE).toBe(fixture.source);
    expect(PERSON_ALIGNMENT_UNCERTAINTY_DEG).toBe(fixture.uncertaintyDeg);
    expect(PERSON_ALIGNMENT_FOV_MAX_DEG).toBe(fixture.fovMaxDeg);
    expect(PERSON_ALIGNMENT_CONVENTION).toEqual(fixture.convention);
    expect(PERSON_ALIGNMENT_CONVENTION).toEqual({ ...SKY_CONTEXT_CONVENTION, roll: 'upright_frame_positive_clockwise' });
  });

  it('is exported from the package', () => {
    expect(contracts.PersonAlignmentSchema).toBe(PersonAlignmentSchema);
  });

  it.each(fixture.valid.map((s) => [s.name, s] as const))('accepts %s unchanged', (_name, s) => {
    const parsed = PersonAlignmentSchema.safeParse(structuredClone(s.alignment));
    expect(parsed.success).toBe(true);
    if (parsed.success) expect(parsed.data).toEqual(s.alignment);
  });

  it.each(fixture.invalid.map((s) => [s.name, s] as const))('refuses %s', (_name, s) => {
    expect(PersonAlignmentSchema.safeParse(structuredClone(s.alignment)).success).toBe(false);
  });

  it('takes every alignment inside its bounds and nothing past them', () => {
    const base = (fixture.valid[0] as Sample).alignment as Record<string, unknown>;
    fc.assert(
      fc.property(
        fc.double({ min: -90, max: 90, noNaN: true }),
        fc.double({ min: 0, max: 360, maxExcluded: true, noNaN: true }),
        fc.double({ min: -180, max: 180, noNaN: true }),
        fc.double({ min: 1e-6, max: PERSON_ALIGNMENT_FOV_MAX_DEG, noNaN: true }),
        (centerAltDeg, centerAzDeg, rollDeg, fovDiagonalDeg) => {
          const value = { ...base, centerAltDeg, centerAzDeg, rollDeg, fovDiagonalDeg };
          expect(PersonAlignmentSchema.safeParse(value).success).toBe(true);
          expect(PersonAlignmentSchema.safeParse({ ...value, centerAltDeg: centerAltDeg + 90.001 * Math.sign(centerAltDeg || 1) }).success).toBe(false);
        },
      ),
      { seed: Number(process.env.ALBEDIAN_FC_SEED ?? 19), numRuns: 200 },
    );
  });
});
