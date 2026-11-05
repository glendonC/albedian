import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  CAPTURE_TIME_MARKS,
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

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

/**
 * The ingest contracts (roadmap Track 1 M1) against the samples the Python mirror wrote
 * (`services/contracts/ingest.py`, `tests/fixtures/ingest_contract.json`): both sides must accept and
 * reject exactly the same records.
 */
const PYTHON_FIXTURE = path.resolve(__dirname, '../../../../tests/fixtures/ingest_contract.json');

type SchemaName = 'SourceAsset' | 'IngestReceipt' | 'CaptureTimeHint' | 'SolveJob';

interface IngestFixture {
  patterns: Record<string, string>;
  enums: Record<string, string[]>;
  valid: Record<SchemaName, unknown[]>;
  invalid: { schema: SchemaName; sample: unknown; reason: string }[];
}

const fixture = JSON.parse(fs.readFileSync(PYTHON_FIXTURE, 'utf8')) as IngestFixture;
const SCHEMAS = {
  SourceAsset: SourceAssetSchema,
  IngestReceipt: IngestReceiptSchema,
  CaptureTimeHint: CaptureTimeHintSchema,
  SolveJob: SolveJobSchema,
};

describe('@albedian/contracts: ingest (uploads, metadata hints, solve jobs)', () => {
  it('shares its patterns and enums with the Pydantic mirror', () => {
    expect(fixture.patterns).toEqual(INGEST_PATTERNS);
    expect(fixture.enums).toEqual({
      metadataContainers: [...METADATA_CONTAINERS],
      captureTimeMarks: [...CAPTURE_TIME_MARKS],
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
      marks: 'recorded',
      source: { container: 'exif', tags: ['DateTimeOriginal'] },
    };
    expect(CaptureTimeHintSchema.safeParse(zoneless).success).toBe(true);
    expect(CaptureTimeHintSchema.safeParse({ ...zoneless, utc: '2031-05-02T21:14:09Z' }).success).toBe(false);
  });

  it('holds a time known only in UTC, and never makes a file time the asset capture time', () => {
    const fileTime = {
      localDateTime: null,
      utcOffsetMinutes: null,
      utc: '2031-05-02T19:31:40Z',
      marks: 'file_written',
      source: { container: 'quicktime', tags: ['creation_time'] },
    };
    expect(CaptureTimeHintSchema.safeParse(fileTime).success).toBe(true);
    const clip = fixture.valid.SourceAsset[1] as Record<string, unknown>;
    const receipt = { sourceAsset: clip, hints: { captureTime: fileTime }, alreadyStored: false };
    expect(IngestReceiptSchema.safeParse(receipt).success).toBe(true);
    const taken = { ...clip, capturedAtUTC: fileTime.utc, metadataSources: { capturedAtUTC: fileTime.source } };
    expect(IngestReceiptSchema.safeParse({ ...receipt, sourceAsset: taken }).success).toBe(false);
  });

  it('PBT: a capture time is local, UTC or both, and only a recorded one reaches the asset', () => {
    const clip = fixture.valid.SourceAsset[1] as Record<string, unknown>;
    const sources = {
      recorded: { container: 'quicktime', tags: ['com.apple.quicktime.creationdate'] },
      file_written: { container: 'quicktime', tags: ['creation_time'] },
    } as const;
    fc.assert(
      fc.property(
        fc.date({ min: new Date(Date.UTC(1990, 0, 1)), max: new Date(Date.UTC(2099, 11, 31)), noInvalidDate: true }),
        fc.integer({ min: -840, max: 840 }),
        fc.boolean(),
        fc.boolean(),
        fc.boolean(),
        fc.constantFrom(...CAPTURE_TIME_MARKS),
        fc.boolean(),
        (wall, offset, hasLocal, hasOffset, hasUtc, marks, assetTakesIt) => {
          // The wall clock is read as UTC fields, so local minus the offset is the instant
          const localMs = Math.floor(wall.getTime() / 1000) * 1000;
          const local = new Date(localMs).toISOString().slice(0, 19);
          const utc = `${new Date(localMs - offset * 60_000).toISOString().slice(0, 19)}Z`;
          const hint = {
            localDateTime: hasLocal ? local : null,
            utcOffsetMinutes: hasOffset ? offset : null,
            utc: hasUtc ? utc : null,
            marks,
            source: sources[marks],
          };
          const hintOk = (hasLocal || hasUtc) && hasOffset === (hasLocal && hasUtc);
          expect(CaptureTimeHintSchema.safeParse(hint).success).toBe(hintOk);
          if (!hintOk) return;
          const takes = assetTakesIt && hasUtc;
          const asset = takes ? { ...clip, capturedAtUTC: utc, metadataSources: { capturedAtUTC: hint.source } } : clip;
          const receipt = { sourceAsset: asset, hints: { captureTime: hint }, alreadyStored: false };
          expect(IngestReceiptSchema.safeParse(receipt).success).toBe(takes === (marks === 'recorded' && hasUtc));
        }
      ),
      {
        seed: SEED,
        numRuns: 300,
        examples: [
          [new Date(Date.UTC(2031, 4, 2, 19, 31, 40)), 0, false, false, true, 'file_written', true],
          [new Date(Date.UTC(2031, 4, 2, 21, 31, 40)), 120, true, true, true, 'recorded', true],
          [new Date(Date.UTC(2031, 4, 2, 21, 31, 40)), 0, false, false, false, 'recorded', false],
        ],
      }
    );
  });

  it('requires a source for every capture time, place and camera a SourceAsset carries', () => {
    const asset = fixture.valid.SourceAsset[0] as Record<string, unknown>;
    expect(SourceAssetSchema.safeParse(asset).success).toBe(true);
    const { metadataSources: _sources, ...unsourced } = asset;
    expect(SourceAssetSchema.safeParse(unsourced).success).toBe(false);
    expect(SourceAssetSchema.safeParse({ ...asset, sha256: 'a8f5c2d9104e8b771234567890abcdef' }).success).toBe(false);
  });
});
