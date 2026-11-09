import fs from 'node:fs';
import path from 'node:path';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import {
  CONSTELLATION_BOUNDARIES_FORMAT,
  CONSTELLATION_BOUNDARY_EDGE_EQUINOX,
  CONSTELLATION_BOUNDARY_VERTEX_EQUINOX,
  CONSTELLATION_CODES,
  CONSTELLATION_EVIDENCE_KIND,
  CONSTELLATION_FORMAT_VERSION,
  CONSTELLATION_LINES_EQUINOX,
  CONSTELLATION_LINES_FORMAT,
  CONSTELLATION_RANK_MAX,
  CONSTELLATION_RANK_MIN,
  CONSTELLATION_RING_MIN_POINTS,
  ConstellationBoundariesSchema,
  ConstellationLinesSchema,
} from '../index.js';

/**
 * Constellation lines and boundaries against the samples the Python mirror wrote (services/contracts/constellations.py,
 * tests/fixtures/constellations_contract.json): seven real entries of each d3-celestial file at commit d2e20e104b86
 * (BSD-3-Clause, Copyright (c) 2015, Olaf Frohn), and one fault per rule. Both sides must accept and refuse exactly
 * the same documents.
 */
const PYTHON_FIXTURE = path.resolve(__dirname, '../../../../tests/fixtures/constellations_contract.json');

type Kind = 'lines' | 'bounds';
type Point = unknown[];
type Entry = Record<string, unknown> & { code: string; polylines?: Point[][]; ring?: Point[] };
type Doc = Record<string, unknown> & { constellations: Entry[] };

interface ConstellationFixture {
  formats: Record<Kind, string>;
  formatVersion: number;
  evidenceKind: string;
  codes: string[];
  rankRange: [number, number];
  equinoxes: Record<string, string>;
  ringMinPoints: number;
  valid: { kind: Kind; table: Doc }[];
  invalid: { rule: string; kind: Kind; table: Doc }[];
}

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

const fixture = JSON.parse(fs.readFileSync(PYTHON_FIXTURE, 'utf8')) as ConstellationFixture;
const SCHEMAS = { lines: ConstellationLinesSchema, bounds: ConstellationBoundariesSchema } as const;

const first = (t: Doc) => t.constellations[0]!;
const setLinePoint = (t: Doc, point: Point) => void (first(t).polylines![0]![0] = point);
// Move the ring's first point, and its closing copy with it, so only the point itself breaks a rule
const setRingPoint = (t: Doc, point: Point) => {
  const ring = first(t).ring!;
  ring[0] = point;
  ring[ring.length - 1] = structuredClone(point);
};

// Each fault breaks a rule of any valid document of at least two entries (the same faults as the Python test's)
const COMMON_FAULTS: Record<string, (t: Doc) => void> = {
  formatVersion: (t) => void (t.formatVersion = 2),
  'evidence kind': (t) => void (t.evidenceKind = 'observed'),
  'snake-case top-level key': (t) => {
    t.format_version = t.formatVersion;
    delete t.formatVersion;
  },
  'extra field': (t) => void (t.extra = 1),
  'blank-padded release': (t) => void (t.release = ' d3-celestial'),
  'no constellations': (t) => void (t.constellations = []),
  'repeated code': (t) => void (t.constellations[1]!.code = first(t).code),
  'unknown code': (t) => void (first(t).code = 'Ser'),
  'rank 0': (t) => void (first(t).rank = 0),
  'rank 4': (t) => void (first(t).rank = 4),
  'fractional rank': (t) => void (first(t).rank = 1.5),
  'rank as text': (t) => void (first(t).rank = '1'),
  'boolean rank': (t) => void (first(t).rank = true),
  'extra entry field': (t) => void (first(t).name = 'Crux'),
};
const LINE_FAULTS: Record<string, (t: Doc) => void> = {
  'lines format': (t) => void (t.format = CONSTELLATION_BOUNDARIES_FORMAT),
  'lines equinox B1875': (t) => void (t.equinox = 'B1875'),
  'no polylines': (t) => void (first(t).polylines = []),
  'one-point polyline': (t) => void (first(t).polylines![0] = [first(t).polylines![0]![0]!]),
  'point repeated at once in a polyline': (t) => {
    const line = first(t).polylines![0]!;
    line.splice(1, 0, structuredClone(line[0]!));
  },
  'line right ascension of 360': (t) => setLinePoint(t, [360.0, 0.0]),
  'line negative right ascension': (t) => setLinePoint(t, [-0.5, 0.0]),
  'line declination past the pole': (t) => setLinePoint(t, [10.0, 90.5]),
  'line declination below the south pole': (t) => setLinePoint(t, [10.0, -90.5]),
  'line point of three numbers': (t) => setLinePoint(t, [10.0, 0.0, 1.0]),
  'line point as text': (t) => setLinePoint(t, ['10.0', 0.0]),
  'a ring where polylines belong': (t) => {
    const entry = first(t);
    entry.ring = entry.polylines![0];
    delete entry.polylines;
  },
};
const BOUND_FAULTS: Record<string, (t: Doc) => void> = {
  'bounds format': (t) => void (t.format = CONSTELLATION_LINES_FORMAT),
  'vertices in B1875': (t) => void (t.vertexEquinox = 'B1875'),
  'edges in J2000': (t) => void (t.edgeEquinox = 'J2000'),
  'open ring': (t) => void first(t).ring!.pop(),
  'ring of three points': (t) => {
    const ring = first(t).ring!;
    first(t).ring = [...ring.slice(0, 2), structuredClone(ring[0]!)];
  },
  'point repeated at once in a ring': (t) => {
    const ring = first(t).ring!;
    ring.splice(1, 0, structuredClone(ring[0]!));
  },
  'ring right ascension of 360': (t) => setRingPoint(t, [360.0, 0.0]),
  'ring negative right ascension': (t) => setRingPoint(t, [-0.5, 0.0]),
  'ring declination past the pole': (t) => setRingPoint(t, [10.0, -90.5]),
  'ring declination above the north pole': (t) => setRingPoint(t, [10.0, 90.5]),
  'ring point as text': (t) => setRingPoint(t, ['10.0', 0.0]),
  'polylines where a ring belongs': (t) => {
    const entry = first(t);
    entry.polylines = [entry.ring!];
    delete entry.ring;
  },
};
const FAULTS: Record<Kind, Record<string, (t: Doc) => void>> = {
  lines: { ...COMMON_FAULTS, ...LINE_FAULTS },
  bounds: { ...COMMON_FAULTS, ...BOUND_FAULTS },
};

// =============================================================================================
// Generators of valid documents, by the same rules as the Python test's
// =============================================================================================

const point = fc.tuple(
  fc.double({ min: 0, max: 360, maxExcluded: true, noNaN: true, noDefaultInfinity: true }),
  fc.double({ min: -90, max: 90, noNaN: true, noDefaultInfinity: true })
);
// Distinct by value, as the rules compare them (0 and -0 are the same point)
const distinctPoints = (minLength: number, maxLength: number) =>
  fc.uniqueArray(point, { minLength, maxLength, comparator: (a, b) => a[0] === b[0] && a[1] === b[1] });

const rankArb = fc.integer({ min: CONSTELLATION_RANK_MIN, max: CONSTELLATION_RANK_MAX });
const entryArb = (kind: Kind, code: string): fc.Arbitrary<Entry> =>
  kind === 'lines'
    ? fc.record({ rank: rankArb, polylines: fc.array(distinctPoints(2, 5), { minLength: 1, maxLength: 3 }) }).map((v) => ({ code, ...v }))
    : fc
        .record({ rank: rankArb, corners: distinctPoints(CONSTELLATION_RING_MIN_POINTS - 1, 6) })
        .map((v) => ({ code, rank: v.rank, ring: [...v.corners, [...v.corners[0]!]] }));

const docOf = (kind: Kind, entries: Entry[]): Doc => {
  const head: Record<string, unknown> = {
    format: kind === 'lines' ? CONSTELLATION_LINES_FORMAT : CONSTELLATION_BOUNDARIES_FORMAT,
    formatVersion: CONSTELLATION_FORMAT_VERSION,
    evidenceKind: CONSTELLATION_EVIDENCE_KIND,
    release: 'd3-celestial d2e20e104b86429d90ac8227a5b021262b45d75a',
  };
  if (kind === 'lines') head.equinox = CONSTELLATION_LINES_EQUINOX;
  else Object.assign(head, { vertexEquinox: CONSTELLATION_BOUNDARY_VERTEX_EQUINOX, edgeEquinox: CONSTELLATION_BOUNDARY_EDGE_EQUINOX });
  return { ...head, constellations: entries };
};

const validDoc = (kind: Kind) =>
  fc
    .uniqueArray(fc.constantFrom(...CONSTELLATION_CODES), { minLength: 2, maxLength: 4 })
    .chain((codes) => fc.tuple(...codes.map((code) => entryArb(kind, code))).map((entries) => docOf(kind, entries)));

const validSample = (kind: Kind) => fixture.valid.find((v) => v.kind === kind)!.table;

describe('@albedian/contracts: constellation lines and boundaries', () => {
  it('matches the Pydantic mirror on formats, codes, ranks and equinoxes', () => {
    expect(fixture.formats).toEqual({ lines: CONSTELLATION_LINES_FORMAT, bounds: CONSTELLATION_BOUNDARIES_FORMAT });
    expect(fixture.formatVersion).toBe(CONSTELLATION_FORMAT_VERSION);
    expect(fixture.evidenceKind).toBe(CONSTELLATION_EVIDENCE_KIND);
    expect(fixture.codes).toEqual([...CONSTELLATION_CODES]);
    expect(fixture.rankRange).toEqual([CONSTELLATION_RANK_MIN, CONSTELLATION_RANK_MAX]);
    expect(fixture.equinoxes).toEqual({
      lines: CONSTELLATION_LINES_EQUINOX,
      boundaryVertices: CONSTELLATION_BOUNDARY_VERTEX_EQUINOX,
      boundaryEdges: CONSTELLATION_BOUNDARY_EDGE_EQUINOX,
    });
    expect(fixture.ringMinPoints).toBe(CONSTELLATION_RING_MIN_POINTS);
  });

  it('parses the real entries the Python side wrote, unchanged', () => {
    expect(fixture.valid.map((v) => v.kind)).toEqual(['lines', 'bounds']);
    for (const { kind, table } of fixture.valid) {
      const parsed = SCHEMAS[kind].safeParse(table);
      expect(parsed.success, `${kind}: ${JSON.stringify(parsed.error?.issues)}`).toBe(true);
      expect(parsed.data).toEqual(table);
    }
  });

  it('refuses every document the Python side refuses', () => {
    const expected = (['lines', 'bounds'] as const).flatMap((kind) => Object.keys(FAULTS[kind]).map((rule) => `${kind}: ${rule}`));
    expect(fixture.invalid.map((c) => `${c.kind}: ${c.rule}`)).toEqual(expected);
    for (const { rule, kind, table } of fixture.invalid) {
      expect(SCHEMAS[kind].safeParse(table).success, `${kind}: ${rule}`).toBe(false);
    }
  });

  it('refuses numbers that JSON cannot carry but a caller can pass', () => {
    for (const bad of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY]) {
      for (const axis of [0, 1]) {
        const lines = structuredClone(validSample('lines'));
        first(lines).polylines![0]![0]![axis] = bad;
        expect(ConstellationLinesSchema.safeParse(lines).success, `lines ${bad} at ${axis}`).toBe(false);
        const bounds = structuredClone(validSample('bounds'));
        const ring = first(bounds).ring!;
        ring[0]![axis] = bad;
        ring[ring.length - 1]![axis] = bad;
        expect(ConstellationBoundariesSchema.safeParse(bounds).success, `bounds ${bad} at ${axis}`).toBe(false);
      }
    }
  });

  it('PBT-CON-1: the schemas accept exactly the documents the rules allow', () => {
    const caseArb = fc.constantFrom<Kind>('lines', 'bounds').chain((kind) =>
      validDoc(kind).chain((t) =>
        fc.option(fc.constantFrom(...Object.keys(FAULTS[kind])), { nil: undefined, freq: 2 }).map((fault) => {
          const copy = structuredClone(t);
          if (fault !== undefined) FAULTS[kind][fault]!(copy);
          return [kind, copy, fault] as const;
        })
      )
    );
    let accepted = 0;
    let rejected = 0;
    fc.assert(
      fc.property(caseArb, ([kind, t, fault]) => {
        const parsed = SCHEMAS[kind].safeParse(t);
        expect(parsed.success, `${kind} ${fault ?? 'no fault'}: ${JSON.stringify(parsed.error?.issues)}`).toBe(fault === undefined);
        if (parsed.success) {
          accepted++;
          expect(parsed.data).toEqual(t);
        } else {
          rejected++;
        }
      }),
      {
        numRuns: 300,
        seed: SEED,
        // The real entries: both poles, both Serpens parts
        examples: [[['lines', validSample('lines'), undefined]], [['bounds', validSample('bounds'), undefined]]],
      }
    );
    expect(accepted).toBeGreaterThan(30);
    expect(rejected).toBeGreaterThan(30);
  }, 30_000);
});
