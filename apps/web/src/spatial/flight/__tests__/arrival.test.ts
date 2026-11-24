import { describe, expect, it } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { CATALOG_STAR_TILE_ENCODING_V1 as ENC } from '@albedian/contracts';
import { raDecToUnitVector, type Vec3 } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import type { CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';
import { catalogStarTint } from '../../catalogStars/catalogStarTint.js';
import { flightCameraOf, flightViewCone, placeFinding, screenOfPlacement } from '../flightFrame.js';
import { starViewOfFlight } from '../starView.js';
import hangar from '../../../../public/models/hangar/hangar.json';
import { HANGAR_CRAFTS } from '../crafts.js';
import { craftFrameRadiusM, craftSceneOf, laptopCraftFraming, phoneCraftFraming } from '../craftScene.js';
import {
  ARRIVAL_BLOOM_CAP_SHARE,
  ARRIVAL_CLEAR_PX,
  ARRIVAL_CRAFT_SHARE,
  ARRIVAL_MARK_HALF_PX,
  ARRIVAL_REST_SHARE,
  arrivalCraftFraming,
  arrivalBloomDiameterPx,
  arrivalBloomOf,
  arrivalGlow,
  arrivalProgress,
  arrivalShiftOf,
  arrivalStarOfTile,
  arrivalViewCone,
  arrivedFlightCamera,
  magnitudeAt,
  starCurvePx,
} from '../arrival.js';

/**
 * arrival-star-1 (UIUX-arrivalstar-READY.md, DECIDED-BY-LEAD 12:57, 13:09 and 15:34): the star flown to arrives as a point
 * with a bloom by the renderer's own size curve without its clamp, capped at a third of the screen's longer side, in
 * its catalog tint; and the flight's second half brings it to rest where Hadar rests on rp-b-01.
 */

const LY_PC = 0.30660139378;

/** A core tile written by hand (typed test data, not read from any catalog release). */
function tileOf(stars: Array<{ dir: Vec3; mag: number; distancePc: number; kind: number; colorIndex?: number; bMinusV?: boolean }>): CatalogStarCoreTile {
  const n = stars.length;
  const directions = new Float32Array(3 * n);
  const magnitude = new Float32Array(n);
  const colorIndex = new Float32Array(n).fill(Number.NaN);
  const distancePc = new Float32Array(n);
  const flags = new Uint8Array(n);
  stars.forEach((s, i) => {
    directions.set(s.dir, 3 * i);
    magnitude[i] = s.mag;
    distancePc[i] = s.distancePc;
    colorIndex[i] = s.colorIndex ?? Number.NaN;
    flags[i] = s.kind | (s.bMinusV ? ENC.flags.colorBMinusV : 0);
  });
  return { layer: 0, order: 0, pix: 4, count: n, positionBits: 32, directions, magnitude, colorIndex, distancePc, flags, byteLength: 0 };
}

function there(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const session = {
    ...base,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 },
    spatialView: { ...base.spatialView, mode: 'source_2d' },
  } as ObservationSession;
  const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
  return dispatchAlbedianAction(session, { type: 'space.enter', requestId: 'go', expectedViewRevision: session.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated' }).session;
}

const inPhase = (s: ObservationSession, transitionPhase: string, transitionProgress: number): ObservationSession =>
  ({ ...s, spatialView: { ...s.spatialView, transitionPhase, transitionProgress } }) as ObservationSession;

describe('the arrival bloom: size, cap and tint (rules 1, 2 and 4)', () => {
  it('keeps the renderer\'s own curve, 1.9 rel^0.3 px with rel = 10^(-0.4 (m - 6)), only without the 22 px clamp', () => {
    for (const m of [6, 1, -2.86, -9.27, -12.43]) expect(starCurvePx(m)).toBeCloseTo(1.9 * Math.pow(Math.pow(10, -0.4 * (m - 6)), 0.3), 9);
    // The record's table: Hadar from a light year 309.2 px, Mu Muscae 129.1 px; both stars arrived as 48.4 px before
    expect(starCurvePx(-12.43)).toBeCloseTo(309.2, 0);
    expect(starCurvePx(-9.27)).toBeCloseTo(129.1, 0);
    expect(starCurvePx(-2.86)).toBeCloseTo(22, 0);
  });

  it('moves a star\'s magnitude by the inverse square law to the camera, as the flight shader does', () => {
    expect(magnitudeAt(0.6, 100, 100)).toBeCloseTo(0.6, 12);
    expect(magnitudeAt(0.6, 100, 10)).toBeCloseTo(0.6 - 5, 12);
    expect(magnitudeAt(1, 120.19, LY_PC)).toBeCloseTo(1 + 5 * Math.log10(LY_PC / 120.19), 12);
  });

  it('caps the bloom at a third of the screen\'s longer side: Rigel and Deneb from a light year are held there', () => {
    expect(ARRIVAL_BLOOM_CAP_SHARE).toBeCloseTo(1 / 3, 12);
    // Rigel (Hp 0.193, 3.78 mas) and Deneb (Hp 1.297, 2.31 mas), from UIUX-arrivalstar-READY.md condition 2
    const rigel = magnitudeAt(0.193, 1000 / 3.78, LY_PC);
    const deneb = magnitudeAt(1.297, 1000 / 2.31, LY_PC);
    expect(rigel).toBeCloseTo(-14.49, 1);
    expect(deneb).toBeCloseTo(-14.45, 1);
    expect(starCurvePx(rigel)).toBeGreaterThan(540);
    for (const m of [rigel, deneb]) {
      expect(arrivalBloomDiameterPx(m, 1440, 900)).toBeCloseTo(480, 9);
      expect(arrivalBloomDiameterPx(m, 390, 844)).toBeCloseTo(844 / 3, 9);
    }
    // Under the cap the curve stands: Hadar 309 px at 1440, held at 281 px at 390; Mu Muscae 129 px at both
    expect(arrivalBloomDiameterPx(-12.43, 1440, 900)).toBeCloseTo(starCurvePx(-12.43), 9);
    expect(arrivalBloomDiameterPx(-12.43, 390, 844)).toBeCloseTo(844 / 3, 9);
    expect(arrivalBloomDiameterPx(-9.27, 390, 844)).toBeCloseTo(starCurvePx(-9.27), 9);
  });

  it('finds the picked star\'s own row (placed at its catalog distance) and takes its tint from it', () => {
    const ref: Vec3 = raDecToUnitVector(211.67, -60.37).map((x) => x * 120.19) as Vec3;
    const tile = tileOf([
      { dir: raDecToUnitVector(211.0, -60.0), mag: 2.1, distancePc: 120.19, kind: 3, colorIndex: 0.5, bMinusV: true },
      { dir: raDecToUnitVector(211.67, -60.37), mag: 0.6, distancePc: 120.19, kind: 3, colorIndex: -0.231, bMinusV: true },
    ]);
    const star = arrivalStarOfTile(tile, ref)!;
    expect(star.magnitude).toBeCloseTo(0.6, 5);
    expect(star.earthPc).toBeCloseTo(120.19, 3);
    // Hipparcos B-V placed on BP-RP by 1.27, through the renderer's own tint
    expect(star.tint).toEqual(catalogStarTint(Math.fround(-0.231), true));
    star.positionPc.forEach((x, i) => expect(x).toBeCloseTo(ref[i]!, 3));
    // A Gaia row's BP-RP as it is; a row with no colour index stays neutral
    const gaia = arrivalStarOfTile(tileOf([{ dir: raDecToUnitVector(10, 20), mag: 5, distancePc: 145.4, kind: 1, colorIndex: 1.788 }]), raDecToUnitVector(10, 20).map((x) => x * 145.4) as Vec3)!;
    expect(gaia.tint).toEqual(catalogStarTint(Math.fround(1.788), false));
    expect(gaia.tint).not.toEqual(catalogStarTint(Math.fround(1.788), true));
    const none = arrivalStarOfTile(tileOf([{ dir: raDecToUnitVector(10, 20), mag: 5, distancePc: 145.4, kind: 1 }]), raDecToUnitVector(10, 20).map((x) => x * 145.4) as Vec3)!;
    expect(none.tint).toEqual(catalogStarTint(Number.NaN, false));
  });

  it('finds no row when none sits at the place flown to, or the row has no usable distance', () => {
    const ref: Vec3 = raDecToUnitVector(10, 20).map((x) => x * 100) as Vec3;
    expect(arrivalStarOfTile(tileOf([{ dir: raDecToUnitVector(10.2, 20), mag: 1, distancePc: 100, kind: 1 }]), ref)).toBeNull();
    expect(arrivalStarOfTile(tileOf([{ dir: raDecToUnitVector(10, 20), mag: 1, distancePc: 110, kind: 1 }]), ref)).toBeNull();
    expect(arrivalStarOfTile(tileOf([{ dir: raDecToUnitVector(10, 20), mag: 1, distancePc: 100, kind: 0 }]), ref)).toBeNull();
  });

  it('draws the bloom where the camera sees the star, its size from the camera\'s distance to it', () => {
    const session = there();
    const camera = flightCameraOf(session, 1440, 900)!;
    const picked = session.observationSolution!.findings.find((f) => f.id === session.selectedFindingId)!;
    const placement = placeFinding(picked);
    expect(placement.kind).toBe('at_distance');
    const pos = placement.kind === 'at_distance' ? placement.positionPc : ([0, 0, 0] as Vec3);
    const earthPc = Math.hypot(pos[0], pos[1], pos[2]);
    const star = { positionPc: pos, earthPc, magnitude: 0.6, tint: [0.6, 0.7, 1] as [number, number, number] };
    const bloom = arrivalBloomOf(star, starViewOfFlight(camera))!;
    const at = screenOfPlacement(camera, placement)!;
    expect(bloom.xPx).toBeCloseTo(at.xPx, 6);
    expect(bloom.yPx).toBeCloseTo(at.yPx, 6);
    const o = camera.observerPc;
    const d = Math.hypot(pos[0] - o[0], pos[1] - o[1], pos[2] - o[2]);
    expect(bloom.diameterPx).toBeCloseTo(arrivalBloomDiameterPx(magnitudeAt(0.6, earthPc, d), 1440, 900), 9);
    expect(bloom.tint).toEqual([0.6, 0.7, 1]);
    // From Earth it is the size the rule gives at Earth's distance: a point
    const fromEarth = arrivalBloomOf(star, { ...starViewOfFlight(camera), observerPc: [0, 0, 0] })!;
    expect(fromEarth.diameterPx).toBeCloseTo(starCurvePx(0.6), 9);
    expect(fromEarth.diameterPx).toBeLessThan(bloom.diameterPx);
    // Behind the camera: none
    expect(arrivalBloomOf(star, { ...starViewOfFlight(camera), observerPc: [pos[0] * 2, pos[1] * 2, pos[2] * 2] })).toBeNull();
  });

  it('glows brightest at the point and falls off smoothly to nothing at the bloom\'s edge: no edge, no disc', () => {
    expect(arrivalGlow(0)).toBeCloseTo(1.15, 9);
    expect(arrivalGlow(0.999)).toBeLessThan(1e-3);
    expect(arrivalGlow(1)).toBe(0);
    expect(arrivalGlow(1.5)).toBe(0);
    let last = Number.POSITIVE_INFINITY;
    for (let q = 0; q <= 1.0001; q += 0.01) {
      const g = arrivalGlow(q);
      expect(g).toBeLessThanOrEqual(last);
      last = g;
    }
    // Half way out it is under a tenth of its peak, as the mock (bloom_mock.py) shows a lens glow
    expect(arrivalGlow(0.5)).toBeLessThan(0.1 * arrivalGlow(0));
  });
});

describe('the arrival\'s resting place (look gap two, DECIDED-BY-LEAD 15:34)', () => {
  it('rests every star where Hadar rests on rp-b-01: the same shares of the frame at laptop and phone size', () => {
    expect(ARRIVAL_REST_SHARE.laptop).toEqual([0.536, 0.476]);
    expect(ARRIVAL_REST_SHARE.phone).toEqual([0.577, 0.432]);
  });

  it('rides the flight\'s own progress: none in Phase A, Phase B\'s eased progress, all of it at rest', () => {
    const s = there();
    expect(arrivalProgress(inPhase(s, 'phase_a_photo_to_sky', 0.8))).toBe(0);
    expect(arrivalProgress(inPhase(s, 'phase_b_sky_to_space', 0.4))).toBe(0.4);
    expect(arrivalProgress(inPhase(s, 'at_space', 1))).toBe(1);
    expect(arrivalProgress(inPhase(s, 'idle', 1))).toBe(0);
  });

  it('keeps the photo\'s place through Phase A and brings the star to its resting place by the flight\'s end', () => {
    for (const [w, h, phone] of [[1440, 900, false], [390, 844, true]] as const) {
      const s = there();
      const picked = s.observationSolution!.findings.find((f) => f.id === s.selectedFindingId)!;
      const placement = placeFinding(picked);
      const photoPlace = screenOfPlacement(flightCameraOf(s, w, h)!, placement)!;
      const share = phone ? ARRIVAL_REST_SHARE.phone : ARRIVAL_REST_SHARE.laptop;
      const landed = (session: ObservationSession) => {
        const shift = arrivalShiftOf(session, w, h, phone)!;
        return screenOfPlacement(arrivedFlightCamera(flightCameraOf(session, w, h)!, shift.nowPx), placement)!;
      };
      const a = landed(inPhase(s, 'phase_a_photo_to_sky', 0.9));
      expect([a.xPx, a.yPx]).toEqual([expect.closeTo(photoPlace.xPx, 6), expect.closeTo(photoPlace.yPx, 6)]);
      const rest = landed(inPhase(s, 'at_space', 1));
      expect([rest.xPx, rest.yPx]).toEqual([expect.closeTo(share[0] * w, 6), expect.closeTo(share[1] * h, 6)]);
      const half = landed(inPhase(s, 'phase_b_sky_to_space', 0.5));
      expect(half.xPx).toBeCloseTo((photoPlace.xPx + share[0] * w) / 2, 6);
      expect(half.yPx).toBeCloseTo((photoPlace.yPx + share[1] * h) / 2, 6);
      // Back to Moment runs Phase B backwards: the same place for the same progress
      const shift = arrivalShiftOf(inPhase(s, 'at_space', 1), w, h, phone)!;
      expect(shift.fullPx[0]).toBeCloseTo(share[0] * w - photoPlace.xPx, 6);
      expect(shift.fullPx[1]).toBeCloseTo(share[1] * h - photoPlace.yPx, 6);
    }
  });

  it('moves every star of the view together: the frame turns, nothing is placed by hand', () => {
    const s = inPhase(there(), 'at_space', 1);
    const camera = flightCameraOf(s, 1440, 900)!;
    const shift = arrivalShiftOf(s, 1440, 900, false)!;
    const moved = arrivedFlightCamera(camera, shift.nowPx);
    for (const f of s.observationSolution!.findings.filter((x) => x.evidenceClass === 'catalog_matched').slice(0, 5)) {
      const before = screenOfPlacement(camera, placeFinding(f));
      const after = screenOfPlacement(moved, placeFinding(f));
      if (!before || !after) continue;
      expect(after.xPx - before.xPx).toBeCloseTo(shift.nowPx[0], 6);
      expect(after.yPx - before.yPx).toBeCloseTo(shift.nowPx[1], 6);
    }
    // The inverse stays the inverse
    const m = moved.storedToScreen;
    const inv = moved.screenToStored;
    const x = 123;
    const y = 456;
    const u = inv[0] * x + inv[1] * y + inv[2];
    const v = inv[3] * x + inv[4] * y + inv[5];
    expect(m[0] * u + m[1] * v + m[2]).toBeCloseTo(x, 6);
    expect(m[3] * u + m[4] * v + m[5]).toBeCloseTo(y, 6);
  });

  it('has none without a picked star placed at its distance', () => {
    const s = there();
    expect(arrivalShiftOf({ ...s, selectedFindingId: null } as ObservationSession, 1440, 900, false)).toBeNull();
  });

  it('loads the sky the whole move shows: the cone holds the window both from the photo\'s place and at rest', () => {
    const s = inPhase(there(), 'at_space', 1);
    const camera = flightCameraOf(s, 1440, 900)!;
    const shift = arrivalShiftOf(s, 1440, 900, false)!;
    const cone = arrivalViewCone(camera, shift.fullPx);
    const before = flightViewCone(camera);
    const after = flightViewCone(arrivedFlightCamera(camera, shift.fullPx));
    const sep = (a: Vec3, b: Vec3) => Math.acos(Math.min(1, a[0] * b[0] + a[1] * b[1] + a[2] * b[2]));
    expect(cone.centerIcrs).toEqual(before.centerIcrs);
    expect(cone.radiusRad).toBeGreaterThanOrEqual(before.radiusRad);
    expect(cone.radiusRad).toBeGreaterThanOrEqual(sep(before.centerIcrs, after.centerIcrs) + after.radiusRad - 1e-12);
    expect(cone.limitingMagnitude).toBe(before.limitingMagnitude);
    // No move: the photo's own cone
    expect(arrivalViewCone(camera, [0, 0])).toEqual(before);
  });
});

describe('the craft at the arrival (the chase camera addition, DECIDED-BY-LEAD 15:50)', () => {
  it('frames the craft below and left of the star, moving there on the star\'s own progress, its size kept', () => {
    expect(ARRIVAL_CRAFT_SHARE.laptop).toEqual([0.361, 0.711]);
    expect(ARRIVAL_CRAFT_SHARE.phone).toEqual([0.308, 0.787]);
    const s = inPhase(there(), 'at_space', 1);
    const base = laptopCraftFraming(flightCameraOf(s, 1440, 900)!);
    expect(arrivalCraftFraming(base, 1440, 900, false, 0)).toEqual(base);
    expect(arrivalCraftFraming(base, 1440, 900, false, 1)).toEqual({ xPx: expect.closeTo(0.361 * 1440, 9), yPx: expect.closeTo(0.711 * 900, 9), diameterPx: base.diameterPx });
    const half = arrivalCraftFraming(base, 1440, 900, false, 0.5);
    expect(half.xPx).toBeCloseTo((base.xPx + 0.361 * 1440) / 2, 9);
    // The phone (16:40): the ruled 0.308 across and 0.787 down the sky band (104 px to the readout's top), moved left
    // and up only as far as keeps the 184 px box 8 px clear of the star's mark (22 px about it) and of the readout's top
    expect([ARRIVAL_CLEAR_PX, ARRIVAL_MARK_HALF_PX]).toEqual([8, 22]);
    const at = (readoutTopPx: number) => arrivalCraftFraming(phoneCraftFraming(390, readoutTopPx), 390, 844, true, 1);
    const markLeft = 0.577 * 390 - 22;
    // A craft's readout at 448 px (the suit, Webb): 103, 348; Voyager's taller one at 414 px: 103, 314
    expect([at(448).xPx, at(448).yPx]).toEqual([expect.closeTo(markLeft - 8 - 92, 9), expect.closeTo(448 - 100, 9)]);
    expect([at(448).xPx, at(448).yPx].map(Math.round)).toEqual([103, 348]);
    expect([at(414).xPx, at(414).yPx].map(Math.round)).toEqual([103, 314]);
    // A readout low enough leaves the ruled share down the band; the ruled share across holds where the mark allows
    expect(at(900).yPx).toBeCloseTo(104 + 0.787 * (900 - 104), 9);
    expect(arrivalCraftFraming(phoneCraftFraming(600, 900), 600, 844, true, 1).xPx).toBeCloseTo(0.308 * 600, 9);
    expect(at(448).diameterPx).toBe(phoneCraftFraming(390, 448).diameterPx);
  });

  it('never covers the star flown to at rest, at either size, with any craft or suit of the hangar, the widest and tallest among them', () => {
    const models = (hangar as unknown as { models: Record<string, { boundsM: { min: number[]; max: number[] } }>; entries: Array<{ id: string; model: string }> });
    const modelOf = Object.fromEntries(models.entries.map((e) => [e.id, e.model]));
    let widest = '';
    let tallest = '';
    let widestPx = 0;
    let tallestPx = 0;
    for (const [w, h, phone] of [[1440, 900, false], [390, 844, true]] as const) {
      const s = inPhase(there(), 'at_space', 1);
      const shift = arrivalShiftOf(s, w, h, phone)!;
      const camera = arrivedFlightCamera(flightCameraOf(s, w, h)!, shift.nowPx);
      // On the phone a craft's readout tops out near 448 px (414 for Voyager's), as measured live
      const base = phone ? phoneCraftFraming(w, 414) : laptopCraftFraming(camera);
      const framing = arrivalCraftFraming(base, w, h, phone, 1);
      const share = phone ? ARRIVAL_REST_SHARE.phone : ARRIVAL_REST_SHARE.laptop;
      const star = { x: share[0] * w, y: share[1] * h };
      for (const craft of HANGAR_CRAFTS) {
        const b = models.models[modelOf[craft.id]!]!.boundsM;
        const size: Vec3 = [b.max[0]! - b.min[0]!, b.max[1]! - b.min[1]!, b.max[2]! - b.min[2]!];
        const centre: Vec3 = [(b.max[0]! + b.min[0]!) / 2, (b.max[1]! + b.min[1]!) / 2, (b.max[2]! + b.min[2]!) / 2];
        const radius = Math.hypot(size[0], size[1], size[2]) / 2;
        const scene = craftSceneOf(camera, craft, centre, radius, undefined, framing, craftFrameRadiusM(craft, size, radius))!;
        let x0 = Infinity;
        let x1 = -Infinity;
        let y0 = Infinity;
        let y1 = -Infinity;
        for (const cx of [b.min[0]!, b.max[0]!]) for (const cy of [b.min[1]!, b.max[1]!]) for (const cz of [b.min[2]!, b.max[2]!]) {
          const m = scene.modelMatrix;
          const wp = [0, 1, 2].map((r) => m[r]! * cx + m[4 + r]! * cy + m[8 + r]! * cz + m[12 + r]!);
          const c = [0, 1, 2, 3].map((r) => scene.clipMatrix[r]! * wp[0]! + scene.clipMatrix[4 + r]! * wp[1]! + scene.clipMatrix[8 + r]! * wp[2]! + scene.clipMatrix[12 + r]!);
          const sx = ((c[0]! / c[3]! + 1) / 2) * w;
          const sy = ((1 - c[1]! / c[3]!) / 2) * h;
          x0 = Math.min(x0, sx); x1 = Math.max(x1, sx); y0 = Math.min(y0, sy); y1 = Math.max(y1, sy);
        }
        // The star's point stands outside the craft's whole box on screen (its corners, a box wider than the craft); a
        // craft framed by its body (the Van Allen Probes, 18:35) by the framed body, its thin wires checked live
        const body = craft.frameByBodyRadiusM !== null ? { x0: framing.xPx - framing.diameterPx / 2, x1: framing.xPx + framing.diameterPx / 2, y0: framing.yPx - framing.diameterPx / 2, y1: framing.yPx + framing.diameterPx / 2 } : null;
        const box = body ?? { x0, x1, y0, y1 };
        // The laptop: the star's point; the phone (16:40): its whole mark, 22 px about it
        const m = phone ? ARRIVAL_MARK_HALF_PX : 0;
        const clear = star.x - m > box.x1 || star.x + m < box.x0 || star.y - m > box.y1 || star.y + m < box.y0;
        expect({ craft: craft.id, phone, clear }).toEqual({ craft: craft.id, phone, clear: true });
        if (!phone && x1 - x0 > widestPx) { widestPx = x1 - x0; widest = craft.id; }
        if (!phone && y1 - y0 > tallestPx) { tallestPx = y1 - y0; tallest = craft.id; }
      }
    }
    // The widest and the tallest on the laptop are among those checked
    expect(widest).not.toBe('');
    expect(tallest).not.toBe('');
  });
});

