import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  CAPTURE_OFFSET_SOURCES,
  CAPTURE_TIME_MARKS,
  GPS_TIME_STAMP_CHECKS,
  GPS_TIME_STAMP_LEFTOVER_LIMIT_SEC,
  PLACE_TIME_ZONE_CHECKS,
  CaptureTimeHintSchema,
  composeDisplayOrientations,
  type DisplayOrientation,
  EXIF_ORIENTATIONS,
  INGEST_PATTERNS,
  IngestReceiptSchema,
  MetadataHintsSchema,
  NAMED_OFFSET_TAG_MESSAGE,
  OFFSET_NOT_APPLIED_CAUSES,
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

type SchemaName = 'SourceAsset' | 'IngestReceipt' | 'CaptureTimeHint' | 'OrientationHint' | 'MetadataHints' | 'SolveJob';

interface IngestFixture {
  patterns: Record<string, string>;
  enums: Record<string, string[]>;
  valid: Record<SchemaName, unknown[]>;
  invalid: { schema: SchemaName; sample: unknown; reason: string }[];
  orientations: { exif: Record<string, DisplayOrientation> };
  messages: Record<string, string>;
}

const fixture = JSON.parse(fs.readFileSync(PYTHON_FIXTURE, 'utf8')) as IngestFixture;
const SCHEMAS = {
  SourceAsset: SourceAssetSchema,
  IngestReceipt: IngestReceiptSchema,
  CaptureTimeHint: CaptureTimeHintSchema,
  OrientationHint: OrientationHintSchema,
  MetadataHints: MetadataHintsSchema,
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
      captureOffsetSources: [...CAPTURE_OFFSET_SOURCES],
      placeTimeZoneChecks: [...PLACE_TIME_ZONE_CHECKS],
      gpsTimeStampChecks: [...GPS_TIME_STAMP_CHECKS],
      offsetNotAppliedCauses: [...OFFSET_NOT_APPLIED_CAUSES],
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
          // The recorded source names its offset tag (a clip's creationdate): with no offset and no instant it says
          // why (capture-time-4), and no hint here does
          const namesItsOffsetWithoutOne = marks === 'recorded' && !hasOffset && !hasUtc;
          const hintOk = (hasLocal || hasUtc) && hasOffset === (hasLocal && hasUtc) && !namesItsOffsetWithoutOne;
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
          [new Date(Date.UTC(2031, 4, 2, 21, 31, 40)), 0, true, false, false, 'recorded', false],
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

  it("states a capture time's uncertainty in whole seconds, from one to one past the leftover bound (capture-time-1 v3)", () => {
    type Hint = { source: { basis?: { offsetSource?: string; uncertaintySec?: unknown } } };
    const zoned = (fixture.valid.CaptureTimeHint as Hint[]).find(
      (hint) => hint.source.basis?.offsetSource === 'place_time_zone_agrees_with_gps_time_stamp'
    )!;
    const edges: [unknown, boolean][] = [
      [1, true],
      [GPS_TIME_STAMP_LEFTOVER_LIMIT_SEC + 1, true],
      [0, false],
      [GPS_TIME_STAMP_LEFTOVER_LIMIT_SEC + 2, false],
      [12.5, false],
      [1.5, false],
      [-1, false],
      [true, false],
      ['13', false],
    ];
    for (const [value, ok] of edges) {
      const hint = structuredClone(zoned);
      hint.source.basis!.uncertaintySec = value;
      expect(CaptureTimeHintSchema.safeParse(hint).success, String(value)).toBe(ok);
    }
  });

  it("names why a file's offset was not applied, by the wire rules (capture-time-2)", () => {
    const notApplied = {
      localDateTime: '9999-12-31T21:00:00',
      utcOffsetMinutes: null,
      utc: null,
      marks: 'recorded',
      source: { container: 'exif', tags: ['DateTimeOriginal', 'OffsetTimeOriginal'] },
      offsetNotApplied: 'instant_outside_the_calendar',
    };
    const clip = { ...notApplied, localDateTime: '0001-01-01T02:59:59', source: { container: 'quicktime', tags: ['com.apple.quicktime.creationdate'] } };
    const { offsetNotApplied: _cause, ...withoutCause } = notApplied;
    const cases: [unknown, boolean, string][] = [
      [notApplied, true, 'an EXIF time whose offset tag gives an instant past the end'],
      [clip, true, "a clip's creation date before the start"],
      [{ ...notApplied, localDateTime: '0001-01-01T13:59:59.999' }, true, 'the last local time an offset carries before year 1'],
      [{ ...notApplied, localDateTime: '0001-01-01T14:00:00' }, false, 'no offset carries it before year 1'],
      [{ ...notApplied, localDateTime: '9999-12-31T10:00:00' }, true, 'the first local time an offset carries past year 9999'],
      [{ ...notApplied, localDateTime: '9999-12-31T09:59:59.999' }, false, 'no offset carries it past year 9999'],
      [{ ...notApplied, offsetNotApplied: 'instant_past_the_calendar' }, false, 'a cause not in the closed list'],
      [{ ...notApplied, offsetNotApplied: null }, false, 'null'],
      [{ ...withoutCause, offset_not_applied: 'instant_outside_the_calendar' }, false, 'a Python field name'],
      [{ ...withoutCause, offsetnotapplied: 'instant_outside_the_calendar' }, false, 'an unknown key'],
      [{ ...notApplied, utcOffsetMinutes: -180, utc: '9999-12-31T23:59:59Z' }, false, 'a cause with an offset'],
      // Decided by the "no offset" rule alone (audit J), and the neighbours without the offset or without the cause
      [{ ...notApplied, utcOffsetMinutes: 180, utc: '9999-12-31T18:00:00Z' }, false, 'a cause beside an applied offset, past the end'],
      [{ ...clip, utcOffsetMinutes: -180, utc: '0001-01-01T05:59:59Z' }, false, 'a cause beside an applied offset, before the start'],
      [{ ...withoutCause, utcOffsetMinutes: 180, utc: '9999-12-31T18:00:00Z' }, true, 'the applied offset with no cause'],
      [
        { ...withoutCause, localDateTime: '0001-01-01T02:59:59', source: clip.source, utcOffsetMinutes: -180, utc: '0001-01-01T05:59:59Z' },
        true,
        'the applied offset with no cause, before the start',
      ],
      [{ ...notApplied, source: { container: 'exif', tags: ['DateTimeOriginal'] } }, false, 'the offset tag is named'],
      [{ ...clip, source: { container: 'quicktime', tags: ['creation_time'] } }, false, 'the creation date is named'],
      [{ ...notApplied, source: { container: 'heif', tags: ['DateTimeOriginal', 'OffsetTimeOriginal'] } }, false, 'heif'],
      [{ ...notApplied, marks: 'file_written' }, false, 'a written time'],
      [{ ...notApplied, localDateTime: null, utc: '9999-12-31T23:59:59Z' }, false, 'no local time'],
      [
        { ...notApplied, source: { ...notApplied.source, basis: { placeTimeZone: 'unchecked', gpsTimeStamp: 'no_usable_time_stamp' } } },
        false,
        'a basis',
      ],
      [{ ...withoutCause, utcOffsetMinutes: -180, utc: '9999-12-31T23:00:00Z' }, false, 'utc not the local time minus the offset'],
      [
        { ...withoutCause, localDateTime: '0001-01-01T01:00:00', utcOffsetMinutes: 180, utc: '0001-01-01T00:00:00Z' },
        false,
        'utc not the local time minus the offset, before year 1',
      ],
    ];
    expect(OFFSET_NOT_APPLIED_CAUSES).toEqual(['instant_outside_the_calendar']);
    for (const [record, ok, reason] of cases) {
      expect(CaptureTimeHintSchema.safeParse(record).success, reason).toBe(ok);
    }
  });

  // capture-time-4: one table of days for a local time and a UTC instant, each inside end and one step out
  const CALENDAR_DAYS: [string, boolean][] = [
    ['0001-01-01', true], ['0000-12-31', false], ['9999-12-31', true],
    ['2031-01-31', true], ['2031-02-28', true], ['2031-02-29', false], ['2032-02-29', true], ['2032-02-30', false],
    ['2000-02-29', true], ['1900-02-29', false], ['2031-04-30', true], ['2031-04-31', false], ['2031-06-31', false],
    ['2031-12-31', true], ['2031-11-31', false],
  ];

  it('takes a capture time only on a real calendar day, local or UTC (capture-time-4)', () => {
    for (const [day, real] of CALENDAR_DAYS) {
      const local = { localDateTime: `${day}T12:00:00`, utcOffsetMinutes: null, utc: null, marks: 'recorded',
        source: { container: 'exif', tags: ['DateTimeOriginal'] } };
      const written = { localDateTime: null, utcOffsetMinutes: null, utc: `${day}T12:00:00Z`, marks: 'file_written',
        source: { container: 'quicktime', tags: ['creation_time'] } };
      expect(CaptureTimeHintSchema.safeParse(local).success, `local ${day}`).toBe(real);
      expect(CaptureTimeHintSchema.safeParse({ ...local, localDateTime: `${day}T23:59:59.999` }).success, `local ${day} late`).toBe(real);
      expect(CaptureTimeHintSchema.safeParse(written).success, `utc ${day}`).toBe(real);
    }
  });

  it("takes an asset's capture instant only on a real calendar day (capture-time-4 v3)", () => {
    const photo = fixture.valid.SourceAsset[0] as Record<string, unknown>;
    for (const [day, real] of CALENDAR_DAYS) {
      const parsed = SourceAssetSchema.safeParse({ ...photo, capturedAtUTC: `${day}T12:00:00Z` });
      expect(parsed.success, day).toBe(real);
      if (day.startsWith('0000')) {
        // The one day Zod's own datetime pattern takes and no calendar holds: refused by the wire module's check
        expect(parsed.error?.issues.map((issue) => issue.message), day).toContain('not a real calendar day');
      }
    }
  });

  it('tells a named offset tag with no offset, no instant and no cause the one sentence both sides write', () => {
    expect(NAMED_OFFSET_TAG_MESSAGE).toBe('a capture time that names its offset tag gives the offset, or says why it does not');
    expect(fixture.messages).toEqual({ namedOffsetTag: NAMED_OFFSET_TAG_MESSAGE });
    const parsed = CaptureTimeHintSchema.safeParse({
      localDateTime: '9999-12-31T21:00:00', utcOffsetMinutes: null, utc: null, marks: 'recorded',
      source: { container: 'exif', tags: ['DateTimeOriginal', 'OffsetTimeOriginal'] },
    });
    expect(parsed.success).toBe(false);
    expect(parsed.error?.issues.map((issue) => issue.message)).toEqual([NAMED_OFFSET_TAG_MESSAGE]);
  });

  it('takes a solve job\'s three instants only on a real calendar day', () => {
    const job = {
      ...(fixture.valid.SolveJob[2] as Record<string, unknown>),
      createdAtUTC: '2032-02-29T19:20:00Z', startedAtUTC: '2032-02-29T19:20:01.250Z', finishedAtUTC: '2032-02-29T19:20:41Z',
    };
    expect(SolveJobSchema.safeParse(job).success).toBe(true);
    for (const field of ['createdAtUTC', 'startedAtUTC', 'finishedAtUTC'] as const) {
      const moved = { ...job, [field]: (job[field] as string).replace('2032-02-29', '2032-02-30') };
      const parsed = SolveJobSchema.safeParse(moved);
      expect(parsed.success, field).toBe(false);
      expect(parsed.error?.issues.map((issue) => issue.message), field).toContain('not a real calendar day');
    }
  });

  it('asks a named offset tag with no offset and no instant to say why (capture-time-4)', () => {
    const local = { localDateTime: '9999-12-31T21:00:00', utcOffsetMinutes: null, utc: null, marks: 'recorded' };
    const cases: [unknown, boolean, string][] = [
      [{ ...local, source: { container: 'exif', tags: ['DateTimeOriginal', 'OffsetTimeOriginal'] } }, false, 'EXIF tag, no cause'],
      [{ ...local, source: { container: 'exif', tags: ['DateTimeOriginal', 'OffsetTimeOriginal'] }, offsetNotApplied: 'instant_outside_the_calendar' }, true, 'with its cause'],
      [{ ...local, source: { container: 'exif', tags: ['DateTimeOriginal'] } }, true, 'no offset tag named'],
      [{ ...local, source: { container: 'quicktime', tags: ['com.apple.quicktime.creationdate'] } }, false, 'creation date, no cause'],
      [{ ...local, source: { container: 'quicktime', tags: ['com.apple.quicktime.creationdate'] }, offsetNotApplied: 'instant_outside_the_calendar' }, true, 'with its cause'],
      [{ ...local, source: { container: 'heif', tags: ['DateTimeOriginal', 'OffsetTimeOriginal'] } }, true, 'a container the rule does not name'],
      [{ ...local, marks: 'file_written', source: { container: 'quicktime', tags: ['com.apple.quicktime.creationdate'] } }, false, 'file-written, names the tag'],
      [{ ...local, marks: 'file_written', source: { container: 'quicktime', tags: ['creation_time'] } }, true, 'file-written, names no offset tag'],
    ];
    for (const [record, ok, why] of cases) {
      expect(CaptureTimeHintSchema.safeParse(record).success, why).toBe(ok);
    }
  });

  it('retires the label for two offsets that both fit: a place that offers two derives nothing first', () => {
    expect(PLACE_TIME_ZONE_CHECKS).toContain('more_than_one_offset');
    expect([...PLACE_TIME_ZONE_CHECKS, ...GPS_TIME_STAMP_CHECKS]).not.toContain('more_than_one_offset_fits');
    const zoneless = {
      localDateTime: '2031-05-02T05:42:18',
      utcOffsetMinutes: null,
      utc: null,
      marks: 'recorded',
      source: { container: 'exif', tags: ['DateTimeOriginal', 'GPSDateStamp', 'GPSTimeStamp'] },
    };
    const withBasis = (placeTimeZone: string, gpsTimeStamp: string) => ({
      ...zoneless,
      source: { ...zoneless.source, basis: { placeTimeZone, gpsTimeStamp } },
    });
    expect(CaptureTimeHintSchema.safeParse(withBasis('more_than_one_offset', 'unchecked')).success).toBe(true);
    expect(CaptureTimeHintSchema.safeParse(withBasis('more_than_one_offset_fits', 'more_than_one_offset_fits')).success).toBe(false);
  });
});

/**
 * source-asset-parity-one (.lead/kiro/verdicts/main-source-asset-parity.md): the ingest family read one way in both
 * languages, from the cases tests/integration/test_ingest_contract.py writes: a field under its Python name is never
 * read, and a whole number written with a decimal point is that whole number, in every integer field.
 */
describe('@albedian/contracts: the ingest family read as Python reads it', () => {
  const PARITY = path.resolve(__dirname, '../../../../tests/fixtures/ingest_parity_contract.json');
  const parity = JSON.parse(fs.readFileSync(PARITY, 'utf8')) as {
    cases: Array<{ what: string; schema: 'SourceAsset' | 'CaptureTimeHint' | 'SolveJob' | 'OrientationHint'; value: unknown; accepted: boolean; path: Array<string | number>; read: unknown }>;
  };
  const schemas = { SourceAsset: SourceAssetSchema, CaptureTimeHint: CaptureTimeHintSchema, SolveJob: SolveJobSchema, OrientationHint: OrientationHintSchema };
  const at = (value: unknown, steps: Array<string | number>): unknown =>
    steps.reduce<unknown>((node, step) => (node !== null && typeof node === 'object' ? (node as Record<string | number, unknown>)[step] : undefined), value);

  it('accepts and refuses every case as Python does, and reads the same value', () => {
    expect(parity.cases.length).toBeGreaterThanOrEqual(13);
    for (const c of parity.cases) {
      const parsed = schemas[c.schema].safeParse(c.value);
      expect(parsed.success, c.what).toBe(c.accepted);
      if (parsed.success) expect(at(parsed.data, c.path) ?? null, c.what).toBe(c.read);
    }
  });
});
