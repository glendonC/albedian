import { z } from 'zod';
import { DEEP_SKY_CONSTELLATIONS } from './deep-sky.js';
import { ASSET_PROVENANCE_LIMITS, ASSET_PROVENANCE_PATTERNS } from './provenance.js';

/**
 * Constellation lines and boundaries: d3-celestial (github.com/ofrohn/d3-celestial, BSD-3-Clause, Copyright (c)
 * 2015, Olaf Frohn), its data/constellations.lines.json and data/constellations.bounds.json, as
 * scripts/bake_constellations_d3celestial.py writes them to apps/web/public/catalog/constellations/. Neither is
 * measured by this app, so the evidence kind is 'catalog-derived'. The lines are the IAU constellation figures with
 * the d3-celestial author's edits, not an IAU product; the boundaries are Davenhall and Leggett (1989), the Delporte
 * boundaries the IAU adopted.
 *
 * Both documents hold one entry per constellation, by the 88 IAU abbreviations with Serpens split into Caput ('Se1')
 * and Cauda ('Se2') as OpenNGC writes it (./deep-sky.ts), and d3-celestial's display rank (1 for the most prominent
 * figures, 3 for the faintest). A point is [right ascension, declination] in degrees, right ascension in [0, 360).
 *
 * - Lines {format, formatVersion, evidenceKind, release, equinox, constellations}: each entry's polylines join star
 *   positions of equinox J2000; a polyline has at least two points and never repeats a point at once.
 * - Boundaries {format, formatVersion, evidenceKind, release, vertexEquinox, edgeEquinox, constellations}: each
 *   entry has one closed ring (its last point is its first) of at least four points, no point repeated at once. The
 *   vertices are J2000 positions of corners defined at equinox B1875, and each edge runs along a constant right
 *   ascension or declination of B1875, not straight between the J2000 vertices: a straight segment can sit
 *   arcminutes off the true boundary. A layer that draws or tests an edge must densify it along B1875 right
 *   ascension or declination (precess both vertices to B1875, step the coordinate that changes, precess each step
 *   back to J2000). Each edge goes the short way in right ascension (none spans 180 degrees; the widest spans
 *   97.5), and ring orientation is mixed: with right ascension increasing to the right, 56 rings run clockwise
 *   and 31 counterclockwise, and the two round a pole (Octans, Ursa Minor) run all the way round, so a
 *   point-in-ring test must not assume either winding.
 *
 * Rules beyond each value's own range: codes are unique within a document. Completeness (all 89 entries) is a
 * property of the baked files, checked by tests/integration/test_constellations_d3celestial.py, not of the contract.
 *
 * Mirrored in Python by services/contracts/constellations.py, which the bake validates against; the samples in
 * tests/fixtures/constellations_contract.json are written by the Python side and parsed here.
 */

export const CONSTELLATION_LINES_FORMAT = 'albedian.constellation-lines';
export const CONSTELLATION_BOUNDARIES_FORMAT = 'albedian.constellation-boundaries';
export const CONSTELLATION_FORMAT_VERSION = 1;
export const CONSTELLATION_EVIDENCE_KIND = 'catalog-derived';
export const CONSTELLATION_CODES = DEEP_SKY_CONSTELLATIONS;
export const CONSTELLATION_RANK_MIN = 1;
export const CONSTELLATION_RANK_MAX = 3;
export const CONSTELLATION_LINES_EQUINOX = 'J2000';
export const CONSTELLATION_BOUNDARY_VERTEX_EQUINOX = 'J2000';
export const CONSTELLATION_BOUNDARY_EDGE_EQUINOX = 'B1875';
export const CONSTELLATION_RING_MIN_POINTS = 4;

/** A string matching `pattern` with at most `max` Unicode code points (an astral character counts once). */
const bounded = (pattern: string, max: number) =>
  z
    .string()
    .regex(new RegExp(pattern))
    .refine((value) => [...value].length <= max, { message: `At most ${max} characters.` });

const shortText = bounded(ASSET_PROVENANCE_PATTERNS.text, ASSET_PROVENANCE_LIMITS.shortTextMaxLength);
const rank = z.number().int().min(CONSTELLATION_RANK_MIN).max(CONSTELLATION_RANK_MAX);

/** [raDeg, decDeg]: right ascension in [0, 360), declination in [-90, 90], both finite. */
export const SkyPointSchema = z.tuple([z.number().finite().min(0).lt(360), z.number().finite().min(-90).max(90)]);
export type SkyPoint = z.infer<typeof SkyPointSchema>;

const samePoint = (a: SkyPoint, b: SkyPoint) => a[0] === b[0] && a[1] === b[1];

const noPointRepeatedAtOnce = (points: SkyPoint[], where: string, path: (string | number)[], ctx: z.RefinementCtx) => {
  for (let i = 1; i < points.length; i++) {
    if (samePoint(points[i]!, points[i - 1]!)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: [...path, i], message: `${where}: point ${i} repeats the point before it.` });
    }
  }
};

const uniqueCodes = (entries: { code: string }[], ctx: z.RefinementCtx) => {
  const seen = new Set<string>();
  entries.forEach((entry, i) => {
    if (seen.has(entry.code)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['constellations', i, 'code'], message: `Constellation ${entry.code} appears more than once.` });
    }
    seen.add(entry.code);
  });
};

/** One constellation's lines. */
export const ConstellationFigureSchema = z
  .object({
    code: z.enum(CONSTELLATION_CODES),
    rank,
    polylines: z.array(z.array(SkyPointSchema).min(2)).min(1),
  })
  .strict()
  .superRefine((entry, ctx) => {
    entry.polylines.forEach((polyline, i) => noPointRepeatedAtOnce(polyline, `${entry.code} polyline ${i}`, ['polylines', i], ctx));
  });
export type ConstellationFigure = z.infer<typeof ConstellationFigureSchema>;

/** One constellation's boundary ring. */
export const ConstellationBoundarySchema = z
  .object({
    code: z.enum(CONSTELLATION_CODES),
    rank,
    ring: z.array(SkyPointSchema).min(CONSTELLATION_RING_MIN_POINTS),
  })
  .strict()
  .superRefine((entry, ctx) => {
    const [start, end] = [entry.ring[0], entry.ring[entry.ring.length - 1]];
    if (start === undefined || end === undefined || !samePoint(start, end)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['ring'], message: `${entry.code}: a boundary ring ends at its first point.` });
    }
    noPointRepeatedAtOnce(entry.ring, `${entry.code} ring`, ['ring'], ctx);
  });
export type ConstellationBoundary = z.infer<typeof ConstellationBoundarySchema>;

/** apps/web/public/catalog/constellations/lines.json. */
export const ConstellationLinesSchema = z
  .object({
    format: z.literal(CONSTELLATION_LINES_FORMAT),
    formatVersion: z.literal(CONSTELLATION_FORMAT_VERSION),
    evidenceKind: z.literal(CONSTELLATION_EVIDENCE_KIND),
    release: shortText,
    equinox: z.literal(CONSTELLATION_LINES_EQUINOX),
    constellations: z.array(ConstellationFigureSchema).min(1),
  })
  .strict()
  .superRefine((doc, ctx) => uniqueCodes(doc.constellations, ctx));
export type ConstellationLines = z.infer<typeof ConstellationLinesSchema>;

/** apps/web/public/catalog/constellations/bounds.json. */
export const ConstellationBoundariesSchema = z
  .object({
    format: z.literal(CONSTELLATION_BOUNDARIES_FORMAT),
    formatVersion: z.literal(CONSTELLATION_FORMAT_VERSION),
    evidenceKind: z.literal(CONSTELLATION_EVIDENCE_KIND),
    release: shortText,
    vertexEquinox: z.literal(CONSTELLATION_BOUNDARY_VERTEX_EQUINOX),
    edgeEquinox: z.literal(CONSTELLATION_BOUNDARY_EDGE_EQUINOX),
    constellations: z.array(ConstellationBoundarySchema).min(1),
  })
  .strict()
  .superRefine((doc, ctx) => uniqueCodes(doc.constellations, ctx));
export type ConstellationBoundaries = z.infer<typeof ConstellationBoundariesSchema>;
