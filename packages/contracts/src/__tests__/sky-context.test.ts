import fs from 'node:fs';
import path from 'node:path';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import type { ZodTypeAny } from 'zod';
import * as contracts from '../index.js';
import {
  SKY_CONTEXT_BODIES_MAX,
  SKY_CONTEXT_BP_RP_MAX,
  SKY_CONTEXT_BP_RP_MIN,
  SKY_CONTEXT_CONVENTION,
  SKY_CONTEXT_EDGES_MAX,
  SKY_CONTEXT_EPHEMERIS_FROM,
  SKY_CONTEXT_EPHEMERIS_UNTIL,
  SKY_CONTEXT_FORMAT,
  SKY_CONTEXT_FORMAT_VERSION,
  SKY_CONTEXT_MAG_MAX,
  SKY_CONTEXT_MAG_MIN,
  SKY_CONTEXT_NAME_MAX_UTF16,
  SKY_CONTEXT_NOTES_MAX,
  SKY_CONTEXT_PROPER_MOTION_MAX,
  SKY_CONTEXT_PROVIDERS,
  SKY_CONTEXT_RADIAL_VELOCITY_MAX,
  SKY_CONTEXT_RULES,
  SKY_CONTEXT_STARS_MAX,
  SKY_CONTEXT_SUN_BAND_EDGES,
  SKY_CONTEXT_SUN_BANDS,
  SKY_CONTEXT_TEXT_MAX_UTF16,
  SkyContextReplySchema,
  SkyContextRequestSchema,
  sunBandOf,
} from '../sky-context.js';

/**
 * The sky-context contract, the TypeScript half: tests/fixtures/sky_context_contract.json is written by
 * tests/integration/test_sky_context_contract.py, and every verdict in it holds here too.
 */
type Kind = 'request' | 'reply';
type Json = Record<string, any>;
interface Change {
  path: (string | number)[];
  value?: unknown;
  drop?: boolean;
}
interface Sample {
  name: string;
  kind: Kind;
  body?: unknown;
  base?: string;
  changes?: Change[];
  message?: string;
  verdict?: 'accepted' | 'refused';
}
interface Fixture {
  format: string;
  formatVersion: number;
  ephemerisSpan: { from: string; until: string };
  limits: Record<string, number>;
  convention: Record<string, string>;
  providers: Record<string, string>;
  sunBands: string[];
  sunBandEdges: Record<string, number>;
  rules: Record<string, string>;
  bases: Record<string, Json>;
  valid: Sample[];
  invalid: Sample[];
  edges: Sample[];
}

const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const ROOT = path.resolve(__dirname, '../../../..');
const fixture = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests/fixtures/sky_context_contract.json'), 'utf8')) as Fixture;
const SCHEMAS: Record<Kind, ZodTypeAny> = { request: SkyContextRequestSchema, reply: SkyContextReplySchema };
const accepts = (kind: Kind, body: unknown): boolean => SCHEMAS[kind].safeParse(body).success;

/** The document with each change made in turn, as the Python side's `_apply` makes it. */
function applied(document: unknown, changes: Change[]): unknown {
  let out = structuredClone(document);
  for (const change of changes) {
    if (change.path.length === 0) {
      out = structuredClone(change.value);
      continue;
    }
    let target = out as Json;
    for (const key of change.path.slice(0, -1)) target = target[key as string];
    const last = change.path.at(-1)!;
    if (change.drop) {
      if (Array.isArray(target)) target.splice(Number(last), 1);
      else delete target[last as string];
    } else if (Array.isArray(target) && last === target.length) target.push(structuredClone(change.value));
    else (target as Json)[last as string] = structuredClone(change.value);
  }
  return out;
}
const resolved = (sample: Sample): unknown =>
  sample.changes ? applied(fixture.bases[sample.base!], sample.changes) : sample.body;

const BASE_REQUEST = fixture.bases.request!;
const BASE_REPLY = fixture.bases.reply!;

describe('the sky-context contract', () => {
  it('holds the decisions the Python side writes', () => {
    expect([SKY_CONTEXT_FORMAT, SKY_CONTEXT_FORMAT_VERSION]).toEqual([fixture.format, fixture.formatVersion]);
    expect([SKY_CONTEXT_FORMAT, SKY_CONTEXT_FORMAT_VERSION]).toEqual(['albedian.sky-context', 1]);
    expect({ from: SKY_CONTEXT_EPHEMERIS_FROM, until: SKY_CONTEXT_EPHEMERIS_UNTIL }).toEqual(fixture.ephemerisSpan);
    expect({
      magMin: SKY_CONTEXT_MAG_MIN,
      magMax: SKY_CONTEXT_MAG_MAX,
      nameMaxUtf16: SKY_CONTEXT_NAME_MAX_UTF16,
      textMaxUtf16: SKY_CONTEXT_TEXT_MAX_UTF16,
      starsMax: SKY_CONTEXT_STARS_MAX,
      bodiesMax: SKY_CONTEXT_BODIES_MAX,
      edgesMax: SKY_CONTEXT_EDGES_MAX,
      notesMax: SKY_CONTEXT_NOTES_MAX,
      properMotionMax: SKY_CONTEXT_PROPER_MOTION_MAX,
      radialVelocityMax: SKY_CONTEXT_RADIAL_VELOCITY_MAX,
      bpRpMin: SKY_CONTEXT_BP_RP_MIN,
      bpRpMax: SKY_CONTEXT_BP_RP_MAX,
    }).toEqual(fixture.limits);
    expect(SKY_CONTEXT_CONVENTION).toEqual(fixture.convention);
    expect(SKY_CONTEXT_PROVIDERS).toEqual(fixture.providers);
    expect(SKY_CONTEXT_RULES).toEqual(fixture.rules);
    expect(SKY_CONTEXT_SUN_BANDS).toEqual(fixture.sunBands);
    expect(SKY_CONTEXT_SUN_BAND_EDGES).toEqual(fixture.sunBandEdges);
  });

  it.each(fixture.valid.map((sample) => [sample.name, sample] as const))('accepts %s unchanged', (_, sample) => {
    const read = SCHEMAS[sample.kind].safeParse(sample.body);
    expect(read.success && read.data).toEqual(sample.body);
    expect(accepts(sample.kind === 'request' ? 'reply' : 'request', sample.body)).toBe(false);
  });

  it.each(fixture.invalid.map((sample) => [sample.name, sample] as const))('refuses %s', (_, sample) => {
    const read = SCHEMAS[sample.kind].safeParse(resolved(sample));
    expect(read.success).toBe(false);
    const messages = read.success ? [] : read.error.issues.map((issue) => issue.message);
    // Zod still runs an object's own checks when a field inside it failed a refinement, so a rule's sentence may sit
    // beside another; the Python side holds each sample to its one sentence
    if (sample.message !== undefined) expect(messages).toContain(sample.message);
  });

  it.each(fixture.edges.map((sample) => [sample.name, sample] as const))('holds the bound at %s', (_, sample) => {
    expect(accepts(sample.kind, resolved(sample))).toBe(sample.verdict === 'accepted');
  });

  it('takes the reply the route made where the Moon\'s phase sits on a sector\'s edge, its phase name null, unchanged', () => {
    const edge = fixture.bases['reply-edge']!;
    expect(edge.predictedSkyBrightness.lunarPhaseName).toBeNull();
    const read = SkyContextReplySchema.safeParse(edge);
    expect(read.success && read.data).toEqual(edge);
    for (const [path, value] of [[['predictedSkyBrightness', 'airmass'], null], [['observerLocation', 'latDeg'], null], [['filters', 'magLimit'], null]] as const) {
      expect(accepts('reply', applied(BASE_REPLY, [{ path: [...path], value }])), path.join('.')).toBe(false);
    }
  });

  it('judges generated Suns by the band rule as written here (PBT)', () => {
    const edges = fixture.sunBandEdges;
    const expected = (alt: number): string => {
      if (alt > edges.dayAboveDeg!) return 'day';
      if (alt >= edges.civilTwilightFromDeg!) return 'civil_twilight';
      if (alt >= edges.nauticalTwilightFromDeg!) return 'nautical_twilight';
      return alt >= edges.astronomicalTwilightFromDeg! ? 'astronomical_twilight' : 'night';
    };
    const near = Object.values(edges).flatMap((edge) => [edge, edge - 1e-6, edge + 1e-6]);
    const alt = fc.oneof(fc.constantFrom(...near, -90, 90, 0, -47.7), fc.double({ min: -90, max: 90, noNaN: true }));
    const band = fc.constantFrom(...fixture.sunBands, 'dusk');
    fc.assert(
      fc.property(alt, band, (a, b) => {
        // -0 is a wire number like any other; JSON has no negative zero to tell apart
        const reply = applied(BASE_REPLY, [{ path: ['sun', 'altDeg'], value: a === 0 ? 0 : a }, { path: ['sun', 'band'], value: b }]);
        expect(accepts('reply', reply)).toBe(b === expected(a));
        expect(sunBandOf(a)).toBe(expected(a));
      }),
      { seed: SEED, numRuns: 200 }
    );
  });

  it('keeps the Sun whatever the filters say, and takes a reply made before the Sun was added', () => {
    const above = fixture.bases['reply-above']!;
    expect(above.filters.aboveHorizonOnly).toBe(true);
    expect(above.sun).toEqual(BASE_REPLY.sun);
    expect(BASE_REPLY.sun.altDeg).toBeLessThan(0);
    expect(accepts('reply', above)).toBe(true);
    const { sun: _sun, ...older } = BASE_REPLY;
    expect(accepts('reply', older)).toBe(true);
    expect(accepts('reply', { ...BASE_REPLY, sun: null })).toBe(false);
  });

  it('holds each list to its stated number', () => {
    const empty = { ...BASE_REPLY, stars: [], ephemerides: [], constellationEdges: [] };
    const star = BASE_REPLY.stars.find((s: Json) => s.id === 'star-vega');
    const many = Array.from({ length: SKY_CONTEXT_STARS_MAX + 1 }, (_, i) => ({ ...star, id: `star-n${i}` }));
    expect(accepts('reply', { ...empty, stars: many.slice(0, -1) })).toBe(true);
    expect(accepts('reply', { ...empty, stars: many })).toBe(false);
    const body = BASE_REPLY.ephemerides.find((b: Json) => b.name === 'Mars');
    const bodies = Array.from({ length: SKY_CONTEXT_BODIES_MAX + 1 }, (_, i) => {
      const name = `Mars${'s'.repeat(i)}`;
      const stem = name.toLowerCase();
      return { ...body, name, id: `finding-predicted-${stem}`, evidence: { ...body.evidence, id: `ev-ephem-${stem}` } };
    });
    expect(accepts('reply', { ...empty, ephemerides: bodies.slice(0, -1) })).toBe(true);
    expect(accepts('reply', { ...empty, ephemerides: bodies })).toBe(false);
    const two = BASE_REPLY.stars.filter((s: Json) => s.id === 'star-vega' || s.id === 'star-sheliak');
    const edge = { constellation: 'Lyra', fromStarId: 'star-vega', toStarId: 'star-sheliak' };
    const edges = (n: number) => Array.from({ length: n }, () => edge);
    expect(accepts('reply', { ...empty, stars: two, constellationEdges: edges(SKY_CONTEXT_EDGES_MAX) })).toBe(true);
    expect(accepts('reply', { ...empty, stars: two, constellationEdges: edges(SKY_CONTEXT_EDGES_MAX + 1) })).toBe(false);
  });

  it('judges generated requests by the contract rules (PBT)', () => {
    const inSpan = [SKY_CONTEXT_EPHEMERIS_FROM, '2031-03-20T12:00:00Z', '2031-03-20T12:00:00.250Z', '2050-12-31T23:59:59.999Z'];
    const outOfSpan = [SKY_CONTEXT_EPHEMERIS_UNTIL, '1799-12-31T23:59:59.999Z', '2031-02-30T00:00:00Z', '2031-03-20T12:00:00', '0000-01-01T00:00:00Z'];
    const number = (value: unknown, low: number, high: number) => typeof value === 'number' && low <= value && value <= high;
    const expected = (c: Json): boolean => {
      const keys = Object.keys(c);
      const allowed = ['observedAtUTC', 'latDeg', 'lonDeg', 'magLimit', 'aboveHorizonOnly'];
      if (!['observedAtUTC', 'latDeg', 'lonDeg'].every((key) => keys.includes(key))) return false;
      if (!keys.every((key) => allowed.includes(key))) return false;
      return (
        inSpan.includes(c.observedAtUTC) &&
        number(c.latDeg, -90, 90) &&
        number(c.lonDeg, -180, 180) &&
        (!('magLimit' in c) || number(c.magLimit, -30, 30)) &&
        (!('aboveHorizonOnly' in c) || typeof c.aboveHorizonOnly === 'boolean')
      );
    };
    const requestArb = fc.record(
      {
        observedAtUTC: fc.constantFrom<unknown>(...inSpan, ...outOfSpan, null),
        latDeg: fc.constantFrom<unknown>(0, 20, 90, 90.000001, -90, -90.5, true, '20', null),
        lonDeg: fc.constantFrom<unknown>(0, -140, 180, -180, 180.000001, -181, false, '0'),
        magLimit: fc.constantFrom<unknown>(2, 2.5, -30, 30, 30.000001, -31, true, null, '2'),
        aboveHorizonOnly: fc.constantFrom<unknown>(true, false, 1, 0, 'true', null),
        extraKey: fc.constant(1),
      },
      { requiredKeys: ['observedAtUTC', 'latDeg', 'lonDeg'] }
    );
    fc.assert(
      fc.property(requestArb, (candidate) => {
        expect(accepts('request', candidate)).toBe(expected(candidate as Json));
      }),
      {
        seed: SEED,
        numRuns: 150,
        examples: [
          [BASE_REQUEST as any],
          [{ ...BASE_REQUEST, magLimit: 30, aboveHorizonOnly: false } as any],
          [{ ...BASE_REQUEST, observedAtUTC: SKY_CONTEXT_EPHEMERIS_UNTIL } as any],
        ],
      }
    );
  });

  it('judges generated filtered replies by the reply-level rules (PBT)', () => {
    const expected = (reply: Json): boolean => {
      const items: Json[] = [...reply.stars, ...reply.ephemerides];
      if (items.some((item) => item.aboveHorizon !== item.altDeg >= 0)) return false;
      const ids: string[] = reply.stars.map((s: Json) => s.id);
      const bodies: string[] = reply.ephemerides.map((b: Json) => b.id);
      if (new Set(ids).size !== ids.length || new Set(bodies).size !== bodies.length) return false;
      if (reply.constellationEdges.some((e: Json) => !ids.includes(e.fromStarId) || !ids.includes(e.toStarId))) return false;
      if (reply.filters.aboveHorizonOnly && items.some((item) => item.altDeg < 0)) return false;
      return !('magLimit' in reply.filters) || reply.stars.every((s: Json) => s.mag <= reply.filters.magLimit);
    };
    const stars = BASE_REPLY.stars.length;
    const bodies = BASE_REPLY.ephemerides.length;
    const edges = BASE_REPLY.constellationEdges.length;
    const arb = fc.record({
      stars: fc.array(fc.nat({ max: stars - 1 }), { maxLength: stars + 1 }),
      bodies: fc.array(fc.nat({ max: bodies - 1 }), { maxLength: bodies + 1 }),
      edges: fc.array(fc.nat({ max: edges - 1 }), { maxLength: edges }),
      flips: fc.uniqueArray(fc.nat({ max: 8 }), { maxLength: 2 }),
      aboveOnly: fc.boolean(),
      magLimit: fc.constantFrom<number | null>(null, -1, 0.03, 1.58, 2, 3.52, 30),
    });
    fc.assert(
      fc.property(arb, (pick) => {
        const reply = structuredClone(BASE_REPLY);
        reply.stars = pick.stars.map((i) => structuredClone(BASE_REPLY.stars[i]));
        reply.ephemerides = pick.bodies.map((i) => structuredClone(BASE_REPLY.ephemerides[i]));
        reply.constellationEdges = pick.edges.map((i) => structuredClone(BASE_REPLY.constellationEdges[i]));
        const items: Json[] = [...reply.stars, ...reply.ephemerides];
        for (const flip of pick.flips) if (flip < items.length) items[flip]!.aboveHorizon = !items[flip]!.aboveHorizon;
        reply.filters = { aboveHorizonOnly: pick.aboveOnly, ...(pick.magLimit === null ? {} : { magLimit: pick.magLimit }) };
        expect(accepts('reply', reply)).toBe(expected(reply));
      }),
      { seed: SEED, numRuns: 200 }
    );
  });

  it('is exported from @albedian/contracts, checked by verify:contracts, and registered in wire_faults', () => {
    const barrel = contracts as Record<string, unknown>;
    expect(barrel.SkyContextRequestSchema).toBe(SkyContextRequestSchema);
    expect(barrel.SkyContextReplySchema).toBe(SkyContextReplySchema);
    expect(barrel.SKY_CONTEXT_RULES).toBe(SKY_CONTEXT_RULES);

    const verify = fs.readFileSync(path.join(ROOT, 'scripts/verify-contracts.mjs'), 'utf8');
    for (const name of ['SkyContextRequestSchema', 'SkyContextReplySchema']) expect(verify, name).toContain(`'${name}'`);

    const wireIndex = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests/fixtures/wire_faults/index.json'), 'utf8')) as {
      pairs: Record<string, { faults: string; base: string; schemas: string[] }>;
    };
    expect(wireIndex.pairs.sky_context).toEqual({
      faults: 'tests/fixtures/wire_faults/sky_context.json',
      base: 'tests/fixtures/sky_context_contract.json',
      schemas: ['SkyContextReply', 'SkyContextRequest'],
    });
  });
});
