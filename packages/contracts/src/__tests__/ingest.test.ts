import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  CAPTURE_TIME_MARKS,
  CaptureTimeHintSchema,
  composeDisplayOrientations,
  type DisplayOrientation,
  EXIF_ORIENTATIONS,
  INGEST_PATTERNS,
  IngestReceiptSchema,
  invertDisplayOrientation,
  JOB_STAGES,
  JOB_STATES,
  METADATA_CONTAINERS,
  OrientationHintSchema,
  orientedSize,
  orientStoredPoint,
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

type SchemaName = 'SourceAsset' | 'IngestReceipt' | 'CaptureTimeHint' | 'OrientationHint' | 'SolveJob';

interface IngestFixture {
  patterns: Record<string, string>;
  enums: Record<string, string[]>;
  valid: Record<SchemaName, unknown[]>;
  invalid: { schema: SchemaName; sample: unknown; reason: string }[];
  orientations: { exif: Record<string, DisplayOrientation> };
}

const fixture = JSON.parse(fs.readFileSync(PYTHON_FIXTURE, 'utf8')) as IngestFixture;
const SCHEMAS = {
  SourceAsset: SourceAssetSchema,
  IngestReceipt: IngestReceiptSchema,
  CaptureTimeHint: CaptureTimeHintSchema,
  OrientationHint: OrientationHintSchema,
  SolveJob: SolveJobSchema,
};

const FORMS: DisplayOrientation[] = [false, true].flatMap((mirrored) =>
  [0, 1, 2, 3].map((quarterTurnsClockwise) => ({ quarterTurnsClockwise, mirrored }))
);
type Grid = number[][];
const mirror = (grid: Grid): Grid => grid.map((row) => [...row].reverse());
// A quarter turn clockwise: each new row is an old column, read from the bottom up
const turnClockwise = (grid: Grid): Grid => grid[0]!.map((_, column) => grid.map((row) => row[column]!).reverse());
function show(form: DisplayOrientation, grid: Grid): Grid {
  let out = form.mirrored ? mirror(grid) : grid;
  for (let turn = 0; turn < form.quarterTurnsClockwise; turn += 1) out = turnClockwise(out);
  return out;
}

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
      localDateTime: '2031-05-02T05:42:18',
      utcOffsetMinutes: null,
      utc: null,
      marks: 'recorded',
      source: { container: 'exif', tags: ['DateTimeOriginal'] },
    };
    expect(CaptureTimeHintSchema.safeParse(zoneless).success).toBe(true);
    expect(CaptureTimeHintSchema.safeParse({ ...zoneless, utc: '2031-05-02T05:42:18Z' }).success).toBe(false);
  });

  it('shares the EXIF orientation table with the Pydantic mirror, eight values to the eight forms', () => {
    const values = [1, 2, 3, 4, 5, 6, 7, 8] as const;
    for (const value of values) {
      expect(EXIF_ORIENTATIONS[value]).toEqual(fixture.orientations.exif[String(value)]);
    }
    const forms = values.map((value) => JSON.stringify(EXIF_ORIENTATIONS[value])).sort();
    expect(forms).toEqual(FORMS.map((form) => JSON.stringify(form)).sort());
    // EXIF 6 turns a landscape-stored portrait upright: the left column becomes the top row
    expect(show(EXIF_ORIENTATIONS[6], [[1, 2, 3], [4, 5, 6]])).toEqual([[4, 1], [5, 2], [6, 3]]);
  });

  it('PBT: orientations compose and invert as the pixels they move', () => {
    const grid = fc
      .tuple(fc.integer({ min: 2, max: 4 }), fc.integer({ min: 2, max: 5 }))
      .chain(([height, width]) =>
        fc.array(fc.array(fc.integer({ min: 0, max: 255 }), { minLength: width, maxLength: width }), {
          minLength: height,
          maxLength: height,
        })
      );
    fc.assert(
      fc.property(fc.constantFrom(...FORMS), fc.constantFrom(...FORMS), grid, (first, then, pixels) => {
        expect(show(composeDisplayOrientations(first, then), pixels)).toEqual(show(then, show(first, pixels)));
        const undo = invertDisplayOrientation(first);
        expect(show(undo, show(first, pixels))).toEqual(pixels);
        expect(show(first, show(undo, pixels))).toEqual(pixels);
        expect(composeDisplayOrientations(first, undo)).toEqual({ quarterTurnsClockwise: 0, mirrored: false });
      }),
      { seed: SEED, numRuns: 200, examples: [[FORMS[1]!, FORMS[4]!, [[1, 2, 3], [4, 5, 6]]]] }
    );
  });

  it('PBT: orientStoredPoint moves each pixel centre to where the orientation moves that pixel (experience-9h)', () => {
    const size = fc.tuple(fc.integer({ min: 1, max: 6 }), fc.integer({ min: 1, max: 7 }));
    fc.assert(
      fc.property(fc.constantFrom(...FORMS), size, (form, [height, width]) => {
        // Every pixel holds its own index, so a pixel that lands in the wrong place is caught
        const pixels = Array.from({ length: height }, (_, row) => Array.from({ length: width }, (_, col) => row * width + col));
        const shown = show(form, pixels);
        const shownSize = orientedSize(width, height, form);
        expect([shown[0]!.length, shown.length]).toEqual([shownSize.widthPx, shownSize.heightPx]);
        for (let row = 0; row < height; row += 1) {
          for (let col = 0; col < width; col += 1) {
            const p = orientStoredPoint(col + 0.5, row + 0.5, width, height, form);
            expect(shown[Math.floor(p.yPx)]![Math.floor(p.xPx)]).toBe(row * width + col);
          }
        }
      }),
      { seed: SEED, numRuns: 200, examples: [[EXIF_ORIENTATIONS[6], [2, 3]]] }
    );
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

  it('gives a frame rate only to a clip, both fields or neither, and a stored clip the rate it was probed at', () => {
    // A photo receipt with every hint and a clip receipt with none, as the Python side wrote them
    const receipts = fixture.valid.IngestReceipt as { sourceAsset: Record<string, unknown> }[];
    const cases = [receipts[0]!, receipts[2]!];
    expect(cases.map((receipt) => receipt.sourceAsset.mediaKind)).toEqual(['image', 'video']);
    for (const receipt of cases) {
      const { fpsNumerator: _numerator, fpsDenominator: _denominator, ...unrated } = receipt.sourceAsset;
      const kind = unrated.mediaKind;
      for (const hasNumerator of [false, true]) {
        for (const hasDenominator of [false, true]) {
          const asset = {
            ...unrated,
            ...(hasNumerator ? { fpsNumerator: 30000 } : {}),
            ...(hasDenominator ? { fpsDenominator: 1001 } : {}),
          };
          const assetOk = hasNumerator === hasDenominator && (kind === 'video' || !hasNumerator);
          const label = `${String(kind)} with numerator ${hasNumerator} and denominator ${hasDenominator}`;
          expect(SourceAssetSchema.safeParse(asset).success, label).toBe(assetOk);
          // The backend probes every clip it keeps, so a receipt's clip carries its rate
          const receiptOk = assetOk && (kind === 'image' || hasNumerator);
          expect(IngestReceiptSchema.safeParse({ ...receipt, sourceAsset: asset }).success, `receipt: ${label}`).toBe(receiptOk);
        }
      }
    }
  });

  it('requires a source for every capture time, place and camera a SourceAsset carries', () => {
    const asset = fixture.valid.SourceAsset[0] as Record<string, unknown>;
    expect(SourceAssetSchema.safeParse(asset).success).toBe(true);
    const { metadataSources: _sources, ...unsourced } = asset;
    expect(SourceAssetSchema.safeParse(unsourced).success).toBe(false);
    expect(SourceAssetSchema.safeParse({ ...asset, sha256: 'a8f5c2d9104e8b771234567890abcdef' }).success).toBe(false);
  });
});
