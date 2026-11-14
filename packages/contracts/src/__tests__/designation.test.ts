/**
 * The designation functions beside the catalog reference answer as the Python twin does (catalog-2b), on the cases
 * tests/integration/test_designations.py writes into tests/fixtures/designation_cases.json.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  BAYER_DESIGNATION_PATTERN,
  FLAMSTEED_NUMBER_MAX,
  FLAMSTEED_NUMBER_MIN,
  catalogTitle,
  compactDesignation,
  flamsteedDesignation,
  flamsteedLongDesignation,
  type CatalogTitleInput,
} from '../index.js';
import { CatalogReferenceSchema, ObservationSolutionSchema } from '../astronomy.js';
import { DESIGNATION_CONSTELLATIONS, DESIGNATION_INDICES, DESIGNATION_LETTERS } from '../designation-tables.js';

const CASES = path.resolve(__dirname, '../../../../tests/fixtures/designation_cases.json');

interface Cases {
  pattern: string;
  compact: [string, string | null][];
  flamsteed: [number, string, string | null][];
  titles: { reference: CatalogTitleInput; title: string; subtitleParts: string[] }[];
  flamsteedProbes?: [unknown, unknown, string | null, string | null][];
  compactProbes?: [unknown, string | null][];
  titleProbes?: [string, [unknown, unknown, unknown, unknown, unknown], string, string[]][];
  namedStarDesignations: string[];
  storedOldShape: { before: Record<string, unknown>; after: Record<string, unknown> };
}

const PROBE_TOKENS: Record<string, unknown> = {
  __NAN__: Number.NaN,
  __INF__: Number.POSITIVE_INFINITY,
  __NEG_INF__: Number.NEGATIVE_INFINITY,
  __NEG_ZERO__: -0,
  __NEL_U0085__: '\u0085',
  __US_U001F__: '\u001f',
};

const decodeProbe = (token: unknown): unknown =>
  typeof token === 'string' && Object.prototype.hasOwnProperty.call(PROBE_TOKENS, token) ? PROBE_TOKENS[token] : token;

const buildProbeRef = (parts: [unknown, unknown, unknown, unknown, unknown]): CatalogTitleInput => {
  const keys = ['catalogSourceId', 'commonName', 'constellation', 'bayerDesignation', 'flamsteedNumber'] as const;
  const out: Record<string, unknown> = {};
  keys.forEach((key, idx) => {
    if (parts[idx] !== '__UNDEFINED__') {
      out[key] = decodeProbe(parts[idx]);
    }
  });
  return out as unknown as CatalogTitleInput;
};

const cases = JSON.parse(fs.readFileSync(CASES, 'utf8')) as Cases;

describe('@albedian/contracts: designations', () => {
  it('builds the same strict pattern as the Python side, from the same tables', () => {
    expect(BAYER_DESIGNATION_PATTERN).toBe(cases.pattern);
    expect(DESIGNATION_LETTERS).toHaveLength(24);
    expect(DESIGNATION_CONSTELLATIONS).toHaveLength(88);
    expect(DESIGNATION_INDICES.map(([digit]) => digit)).toEqual(['1', '2', '3', '4', '5', '6', '7', '8', '9']);
    expect(FLAMSTEED_NUMBER_MAX).toBe(140);
  });

  it('gives each compact form the Python side gives, every shipped long form included', () => {
    expect(cases.compact.length).toBeGreaterThan(70);
    for (const [text, compact] of cases.compact) {
      expect(compactDesignation(text), text).toBe(compact);
    }
  });

  it('writes each Flamsteed designation as the Python side does', () => {
    for (const [number, constellation, expected] of cases.flamsteed) {
      expect(flamsteedDesignation(number, constellation), `${number} ${constellation}`).toBe(expected);
    }
    expect(flamsteedDesignation(27.5, 'Cas')).toBeNull();
  });

  it('takes every designation the solver writes for a named star, and refuses the old shape in plain words', () => {
    expect(cases.namedStarDesignations).toHaveLength(244);
    const pattern = new RegExp(BAYER_DESIGNATION_PATTERN);
    for (const text of cases.namedStarDesignations) expect(pattern.test(text), text).toBe(true);
    const old = {
      id: 'gaia-dr3-5044368071870093312', provider: 'Gaia DR3', releaseVersion: 'Gaia DR3',
      catalogSourceId: 'Gaia DR3 5044368071870093312', commonName: 'Acamar', bayerDesignation: 'HR 897',
      constellation: 'Eri', objectType: 'star', coordinateEpoch: 'J2016.0', raDeg: 44.565, decDeg: -40.305,
      apparentMagnitude: 3.228, citation: 'Gaia DR3',
    };
    const refused = CatalogReferenceSchema.safeParse(old);
    expect(refused.success).toBe(false);
    expect(refused.error?.issues.map((issue) => [issue.path.join('.'), issue.code])).toEqual([['bayerDesignation', 'invalid_string']]);
    expect(CatalogReferenceSchema.safeParse({ ...old, bayerDesignation: 'Theta1 Eridani' }).success).toBe(true);
  });

  it('writes a Flamsteed title with the genitive in full, as the Python side does', () => {
    expect(flamsteedLongDesignation(119, 'Tau')).toBe('119 Tauri');
    expect(flamsteedLongDesignation(62, 'Sagittarius')).toBe('62 Sagittarii');
    expect(flamsteedLongDesignation(27, 'Cas')).toBe('27 Cassiopeiae');
    expect(flamsteedLongDesignation(27, 'Nowhere')).toBeNull();
    expect(flamsteedLongDesignation(FLAMSTEED_NUMBER_MAX + 1, 'Cas')).toBeNull();
    expect(flamsteedLongDesignation(0, 'Cas')).toBeNull();
    expect(flamsteedLongDesignation(1.5, 'Cas')).toBeNull();
  });

  it('orders the title and the subtitle as the Python side does', () => {
    expect(FLAMSTEED_NUMBER_MIN).toBe(1);
    for (const { reference, title, subtitleParts } of cases.titles) {
      expect(catalogTitle(reference), JSON.stringify(reference)).toEqual({ title, subtitleParts });
    }
  });

  it('agrees with Python on whole floats, non-integers, non-numbers, empty strings and whitespace probes', () => {
    const flamsteedProbes = cases.flamsteedProbes ?? [];
    expect(flamsteedProbes.length).toBeGreaterThanOrEqual(40);
    for (const [numTok, conTok, expShort, expLong] of flamsteedProbes) {
      const num = numTok === '__UNDEFINED__' ? undefined : decodeProbe(numTok);
      const con = conTok === '__UNDEFINED__' ? undefined : decodeProbe(conTok);
      expect(flamsteedDesignation(num as number, con as string), `short ${String(numTok)} ${String(conTok)}`).toBe(expShort);
      expect(flamsteedLongDesignation(num as number, con as string), `long ${String(numTok)} ${String(conTok)}`).toBe(expLong);
    }
    for (const [abbr, name, genitive] of DESIGNATION_CONSTELLATIONS) {
      expect(flamsteedDesignation(1.0, abbr)).toBe(`1 ${abbr}`);
      expect(flamsteedLongDesignation(140.0, name)).toBe(`140 ${genitive}`);
    }
    const compactProbes = cases.compactProbes ?? [];
    expect(compactProbes.length).toBeGreaterThanOrEqual(10);
    for (const [textTok, expCompact] of compactProbes) {
      const val = textTok === '__UNDEFINED__' ? undefined : decodeProbe(textTok);
      expect(compactDesignation(val as string), `compact ${String(textTok)}`).toBe(expCompact);
    }
    const titleProbes = cases.titleProbes ?? [];
    expect(titleProbes.length).toBeGreaterThanOrEqual(20);
    for (const [tag, rawParts, expTitle, expSub] of titleProbes) {
      expect(catalogTitle(buildProbeRef(rawParts)), tag).toEqual({ title: expTitle, subtitleParts: expSub });
    }
  });

  it('refuses a stored solution of the old shape and takes it as the server reads it now, every finding kept', () => {
    const { before, after } = cases.storedOldShape;
    const refused = ObservationSolutionSchema.safeParse(before);
    expect(refused.success).toBe(false);
    expect(new Set(refused.error?.issues.map((issue) => `${issue.path.slice(2).join('.')} ${issue.code}`))).toEqual(
      new Set(['catalogRef.bayerDesignation invalid_string']),
    );
    const read = ObservationSolutionSchema.parse(after);
    const titles = (solution: Record<string, unknown>) => (solution.findings as { title: string }[]).map((f) => f.title);
    expect(read.findings).toHaveLength((before.findings as unknown[]).length);
    expect(titles(read as unknown as Record<string, unknown>)).toEqual(titles(before));
  });
});
