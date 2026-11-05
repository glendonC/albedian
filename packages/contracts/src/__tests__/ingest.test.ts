import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  CaptureTimeHintSchema,
  INGEST_PATTERNS,
  IngestReceiptSchema,
  JOB_STAGES,
  JOB_STATES,
  METADATA_CONTAINERS,
  PROGRESS_UNITS,
  SolveJobSchema,
  SourceAssetSchema,
} from '../index.js';

/**
 * The ingest contracts (roadmap Track 1 M1) against the samples the Python mirror wrote
 * (`services/contracts/ingest.py`, `tests/fixtures/ingest_contract.json`): both sides must accept and
 * reject exactly the same records.
 */
const PYTHON_FIXTURE = path.resolve(__dirname, '../../../../tests/fixtures/ingest_contract.json');

interface IngestFixture {
  patterns: Record<string, string>;
  enums: Record<string, string[]>;
  valid: Record<'SourceAsset' | 'IngestReceipt' | 'SolveJob', unknown[]>;
  invalid: { schema: 'SourceAsset' | 'IngestReceipt' | 'SolveJob'; sample: unknown; reason: string }[];
}

const fixture = JSON.parse(fs.readFileSync(PYTHON_FIXTURE, 'utf8')) as IngestFixture;
const SCHEMAS = { SourceAsset: SourceAssetSchema, IngestReceipt: IngestReceiptSchema, SolveJob: SolveJobSchema };

describe('@albedian/contracts: ingest (uploads, metadata hints, solve jobs)', () => {
  it('shares its patterns and enums with the Pydantic mirror', () => {
    expect(fixture.patterns).toEqual(INGEST_PATTERNS);
    expect(fixture.enums).toEqual({
      metadataContainers: [...METADATA_CONTAINERS],
      jobStages: [...JOB_STAGES],
      progressUnits: [...PROGRESS_UNITS],
      jobStates: [...JOB_STATES],
    });
  });

  it('parses every record the Python side wrote, unchanged', () => {
    for (const [name, records] of Object.entries(fixture.valid) as [keyof typeof SCHEMAS, unknown[]][]) {
      expect(records.length).toBeGreaterThan(0);
      for (const record of records) {
        const parsed = SCHEMAS[name].safeParse(record);
        expect(parsed.success, `${name}: ${JSON.stringify(parsed.error?.issues)}`).toBe(true);
        expect(parsed.data).toEqual(record);
      }
    }
  });

  it('rejects every record the Python side rejects', () => {
    expect(fixture.invalid.length).toBeGreaterThanOrEqual(20);
    for (const { schema, sample, reason } of fixture.invalid) {
      expect(SCHEMAS[schema].safeParse(sample).success, `${schema} should reject: ${reason}`).toBe(false);
    }
  });

  it('keeps a capture time with no zone local, never a guessed UTC', () => {
    const zoneless = {
      localDateTime: '2031-05-02T21:14:09',
      utcOffsetMinutes: null,
      utc: null,
      source: { container: 'exif', tags: ['DateTimeOriginal'] },
    };
    expect(CaptureTimeHintSchema.safeParse(zoneless).success).toBe(true);
    expect(CaptureTimeHintSchema.safeParse({ ...zoneless, utc: '2031-05-02T21:14:09Z' }).success).toBe(false);
  });

  it('requires a source for every capture time, place and camera a SourceAsset carries', () => {
    const asset = fixture.valid.SourceAsset[0] as Record<string, unknown>;
    expect(SourceAssetSchema.safeParse(asset).success).toBe(true);
    const { metadataSources: _sources, ...unsourced } = asset;
    expect(SourceAssetSchema.safeParse(unsourced).success).toBe(false);
    expect(SourceAssetSchema.safeParse({ ...asset, sha256: 'a8f5c2d9104e8b771234567890abcdef' }).success).toBe(false);
  });
});
