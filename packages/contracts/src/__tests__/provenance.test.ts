import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  ASSET_PROVENANCE_FORMAT,
  ASSET_PROVENANCE_FORMAT_VERSION,
  ASSET_PROVENANCE_LIMITS,
  ASSET_PROVENANCE_PATTERNS,
  ASSET_PROVENANCE_RECORD_FILENAME,
  AssetProvenanceRecordSchema,
  SPDX_LICENSE_IDS,
} from '../index.js';

// Written by tests/integration/test_provenance_contract.py from the Pydantic model
const PYTHON_FIXTURE = path.resolve(__dirname, '../../../../tests/fixtures/provenance_contract.json');

type Json = Record<string, unknown>;

interface ProvenanceFixture {
  format: string;
  formatVersion: number;
  recordFilename: string;
  spdxLicenseIds: string[];
  patterns: Record<string, string>;
  limits: Record<string, number>;
  valid: Json[];
  invalid: { rule: string; record: Json }[];
}

const fixture = JSON.parse(fs.readFileSync(PYTHON_FIXTURE, 'utf8')) as ProvenanceFixture;

// A stand-in checksum for a generated entry, as the Python samples make them
const digest = (label: string) =>
  createHash('sha256').update(`albedian provenance contract sample: ${label}`).digest('hex');

describe('@albedian/contracts: asset provenance records', () => {
  it('matches the Pydantic model on format, licence ids, patterns and limits', () => {
    expect(fixture.format).toBe(ASSET_PROVENANCE_FORMAT);
    expect(fixture.formatVersion).toBe(ASSET_PROVENANCE_FORMAT_VERSION);
    expect(fixture.recordFilename).toBe(ASSET_PROVENANCE_RECORD_FILENAME);
    expect(fixture.spdxLicenseIds).toEqual([...SPDX_LICENSE_IDS]);
    expect(fixture.patterns).toEqual(ASSET_PROVENANCE_PATTERNS);
    expect(fixture.limits).toEqual(ASSET_PROVENANCE_LIMITS);
  });

  it('parses every record the Python side wrote, unchanged', () => {
    expect(fixture.valid.length).toBeGreaterThan(0);
    for (const record of fixture.valid) {
      const parsed = AssetProvenanceRecordSchema.safeParse(record);
      expect(parsed.success, JSON.stringify(parsed.error?.issues)).toBe(true);
      expect(parsed.data).toEqual(record);
    }
  });

  it('rejects every record the Python side rejects', () => {
    expect(fixture.invalid.length).toBeGreaterThan(30);
    for (const { rule, record } of fixture.invalid) {
      expect(AssetProvenanceRecordSchema.safeParse(record).success, rule).toBe(false);
    }
  });

  it('PBT-PROV-1: the schema accepts exactly the records the contract rules allow', () => {
    const SOURCE_IDS = ['src-a', 'src-b', 'src-c'];
    const PATHS = ['sky.jpg', 'maps/moon_2k.jpg', 'L3/17.core.bin', 'SHA256SUMS', 'names.json'];
    const text = fc
      .stringOf(fc.constantFrom(...'abc XYZ.,'), { minLength: 1, maxLength: 40 })
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
    const licence = fc
      .oneof(fc.constantFrom(...SPDX_LICENSE_IDS), fc.stringMatching(/^LicenseRef-[A-Za-z0-9.-]{1,12}$/))
      .map((spdx) => ({ spdx, name: 'Some licence', url: 'https://example.org/terms' }));
    const producer = fc.constantFrom<Json>(
      { method: 'downloaded-as-is' },
      { method: 'script', script: 'scripts/bake_catalog_star_tiles.py' },
      { method: 'script', script: 'scripts/x.py', command: 'uv run python scripts/x.py' }
    );
    const source = (id: string) =>
      fc
        .record({
          title: text,
          url: fc.constantFrom('https://www.eso.org/public/images/', 'http://archive.example.org/x?y=1'),
          license: licence,
          credit: text,
          when: fc.constantFrom('version', 'retrieved', 'both'),
          version: text,
          retrieved: fc.constantFrom('2026-11-04', '2024-02-29', '1999-12-31'),
          citation: fc.option(text, { nil: undefined }),
          acknowledgement: fc.option(text, { nil: undefined }),
          note: fc.option(text, { nil: undefined }),
          doi: fc.option(fc.constant('10.5281/zenodo.8187943'), { nil: undefined }),
        })
        .map(({ when, version, retrieved, ...rest }) => {
          const out: Json = { id };
          for (const [key, value] of Object.entries(rest)) if (value !== undefined) out[key] = value;
          if (when !== 'retrieved') out.version = version;
          if (when !== 'version') out.retrieved = retrieved;
          return out;
        });
    const record = fc.integer({ min: 1, max: SOURCE_IDS.length }).chain((n) => {
      const ids = SOURCE_IDS.slice(0, n);
      return fc
        .record({
          sources: fc.tuple(...ids.map(source)),
          paths: fc.shuffledSubarray(PATHS, { minLength: 1 }),
          split: fc.nat(),
          refs: fc.array(fc.shuffledSubarray(ids, { minLength: 1 }), { minLength: PATHS.length, maxLength: PATHS.length }),
          producers: fc.array(producer, { minLength: PATHS.length, maxLength: PATHS.length }),
        })
        .map(({ sources, paths, split, refs, producers }) => {
          const entries = paths.map((p, i) => ({
            path: p,
            sha256: digest(p),
            sources: [...refs[i]!],
            producedBy: producers[i]!,
          }));
          // Every source must be referenced somewhere
          for (const id of ids) if (!entries.some((e) => e.sources.includes(id))) entries[0]!.sources.push(id);
          const cut = split % (paths.length + 1);
          return {
            format: ASSET_PROVENANCE_FORMAT,
            formatVersion: ASSET_PROVENANCE_FORMAT_VERSION,
            sources: [...sources],
            files: entries.slice(0, cut),
            fileSets: entries.slice(cut).map(({ path: checksums, ...rest }) => ({ checksums, ...rest })),
          } as Json;
        });
    });

    const entriesOf = (r: Json) => [...(r.files as Json[]), ...(r.fileSets as Json[])];
    const firstEntry = (r: Json) => entriesOf(r)[0]!;
    const firstSource = (r: Json) => (r.sources as Json[])[0]!;
    const setEntryPath = (r: Json, value: string) => {
      const entry = firstEntry(r);
      entry['checksums' in entry ? 'checksums' : 'path'] = value;
    };
    // Each fault breaks one rule whatever the record it is applied to
    const faults: Record<string, (r: Json) => void> = {
      format: (r) => void (r.format = 'albedian.asset-sources'),
      formatVersion: (r) => void (r.formatVersion = 2),
      'no entries': (r) => void ((r.files = []), (r.fileSets = [])),
      'extra field': (r) => void (r.extra = 1),
      'bad source id': (r) => void (firstSource(r).id = 'Bad Id'),
      'repeated source': (r) => void (r.sources as Json[]).push(structuredClone(firstSource(r))),
      'orphan source': (r) => void (r.sources as Json[]).push({ ...structuredClone(firstSource(r)), id: 'orphan' }),
      'undefined reference': (r) => void (firstEntry(r).sources as string[]).push('undefined-source'),
      'no date or version': (r) => void (delete firstSource(r).version, delete firstSource(r).retrieved),
      'impossible date': (r) => void (firstSource(r).retrieved = '2025-02-29'),
      'unlisted licence': (r) => void ((firstSource(r).license as Json).spdx = 'GPL-2.0-or-later'),
      'padded credit': (r) => void (firstSource(r).credit = ' ESO'),
      'blank-padded title': (r) => void (firstSource(r).title = '\ufeff' + String(firstSource(r).title)),
      'long credit': (r) => void (firstSource(r).credit = 'c'.repeat(ASSET_PROVENANCE_LIMITS.creditMaxLength + 1)),
      'bad url': (r) => void (firstSource(r).url = 'www.eso.org'),
      'null doi': (r) => void (firstSource(r).doi = null),
      'climbing path': (r) => setEntryPath(r, '../sky.jpg'),
      'record file path': (r) => setEntryPath(r, ASSET_PROVENANCE_RECORD_FILENAME),
      'bad checksum': (r) => void (firstEntry(r).sha256 = '0'.repeat(63)),
      'no references': (r) => void (firstEntry(r).sources = []),
      'unknown method': (r) => void (firstEntry(r).producedBy = { method: 'copied' }),
    };
    const caseArb = fc
      .tuple(record, fc.option(fc.constantFrom(...Object.keys(faults)), { nil: undefined, freq: 2 }))
      .map(([r, fault]) => {
        const copy = structuredClone(r);
        if (fault !== undefined) faults[fault]!(copy);
        return [copy, fault] as const;
      });

    let accepted = 0;
    let rejected = 0;
    fc.assert(
      fc.property(caseArb, ([r, fault]) => {
        const parsed = AssetProvenanceRecordSchema.safeParse(r);
        expect(parsed.success, `${fault ?? 'no fault'}: ${JSON.stringify(parsed.error?.issues)}`).toBe(fault === undefined);
        if (parsed.success) {
          accepted++;
          // Round trip: the parsed record is already in wire form and parses back to itself
          expect(AssetProvenanceRecordSchema.parse(parsed.data)).toEqual(parsed.data);
        } else {
          rejected++;
        }
      }),
      {
        numRuns: 300,
        // File sets only, and a LicenseRef licence with a nested path: rare draws the rules must accept
        examples: [[[fixture.valid[1]!, undefined]], [[fixture.valid[2]!, undefined]]],
      }
    );
    // Both outcomes must actually be exercised for the property to mean anything
    expect(accepted).toBeGreaterThan(30);
    expect(rejected).toBeGreaterThan(30);
  });
});
