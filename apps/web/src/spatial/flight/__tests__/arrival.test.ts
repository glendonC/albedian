import { describe, expect, it } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { CATALOG_STAR_TILE_ENCODING_V1 as ENC } from '@albedian/contracts';
import { raDecToUnitVector, type Vec3 } from '@albedian/camera-rig';
import { dispatchAlbedianAction, flightEndOrigin } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import type { CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';
import { B_MINUS_V_TO_BP_RP, catalogStarTint } from '../../catalogStars/catalogStarTint.js';
import { flightCameraOf, flightViewCone, placeFinding, screenOfPlacement } from '../flightFrame.js';
import { starViewOfFlight } from '../starView.js';
import hangar from '../../../../public/models/hangar/hangar.json';
import { HANGAR_CRAFTS } from '../crafts.js';
import { craftFrameRadiusM, craftSceneOf, laptopCraftFraming, phoneCraftFraming } from '../craftScene.js';
import {
  ARRIVAL_BLOOM_CAP_SHARE,
  ARRIVAL_GLARE_AT_FLOOR,
  ARRIVAL_CLEAR_PX,
  ARRIVAL_CRAFT_SHARE,
  ARRIVAL_MARK_HALF_PX,
  ARRIVAL_REST_SHARE,
  arrivalCraftFraming,
  arrivalBloomDiameterPx,
  arrivalBloomOf,
  arrivalGlow,
  arrivalGlowYielded,
  BLOOM_YIELD_RAMP_PX,
  phoneWordsTopPx,
  arrivalProgress,
  arrivalShiftOf,
  arrivalStarOfTile,
  arrivalViewCone,
  arrivedFlightCamera,
  cappedBloomDiameterPx,
  discAngularRadiusRad,
  discTintOf,
  magnitudeAt,
  STAR_SPRITE_PX,
  starCurvePx,
  SUN_ANGULAR_RADIUS_AT_AU_RAD,
  SUN_TEFF_K,
  temperatureOfBMinusV,
} from '../arrival.js';
import { AU_PC, LIGHT_YEAR_PC, SUN_ON_EARTH_MAG, keyToFillOf, nearStarShare } from '../craftLight.js';

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

// near-star-light-one (DECIDED-BY-LEAD 25 Nov 07:39 and 08:00, on LOOK's pictures): the star flown to grows with its
// true brightness within what the screen holds, its white-hot centre held at arrival-star-1's size while only the faint
// skirt grows, and its disc drawn at the size a model gives where that passes the star's own sprite
describe('near-star-light-one: the star flown to as the flight nears it', () => {
  // Rigel (Hp 0.193, 3.78 mas, as above) and Hipparcos B-V -0.03; typed test data, not read from any catalog release
  const rigelPc = 1000 / 3.78;
  const rigelAt = (d: number) => magnitudeAt(0.193, rigelPc, d);
  const floorOf = (mag: number, earthPc: number) => earthPc * Math.pow(10, (SUN_ON_EARTH_MAG - mag) / 5);

  it('keeps arrival-star-1\'s size a light year from the star and beyond, and without a distance', () => {
    for (const d of [LIGHT_YEAR_PC, 2 * LIGHT_YEAR_PC, 40]) {
      expect(arrivalBloomDiameterPx(rigelAt(d), 1440, 900, d)).toBeCloseTo(cappedBloomDiameterPx(rigelAt(d), 1440, 900), 9);
    }
    expect(arrivalBloomDiameterPx(rigelAt(LIGHT_YEAR_PC), 1440, 900, LIGHT_YEAR_PC)).toBeCloseTo(480, 9);
    for (const m of [6, 0.6, -12.43, -14.49]) {
      expect(arrivalBloomDiameterPx(m, 1440, 900)).toBe(cappedBloomDiameterPx(m, 1440, 900));
      expect(cappedBloomDiameterPx(m, 390, 844)).toBe(Math.min(starCurvePx(m), 844 / 3));
    }
  });

  it('grows nearer on a log scale from its size at a light year to the screen\'s longer side where it shines as the Sun on Earth', () => {
    const floor = floorOf(0.193, rigelPc);
    expect(floor / AU_PC).toBeGreaterThan(150);
    expect(floor / AU_PC).toBeLessThan(300);
    expect(arrivalBloomDiameterPx(rigelAt(floor), 1440, 900, floor)).toBeCloseTo(1440, 6);
    expect(arrivalBloomDiameterPx(rigelAt(floor), 390, 844, floor)).toBeCloseTo(844, 6);
    // Held at the screen nearer than the floor
    expect(arrivalBloomDiameterPx(rigelAt(floor / 2), 1440, 900, floor / 2)).toBeCloseTo(1440, 6);
    // Halfway in magnitude from a light year to the floor: halfway on the log scale
    const half = (rigelAt(LIGHT_YEAR_PC) + SUN_ON_EARTH_MAG) / 2;
    const dHalf = rigelPc * Math.pow(10, (half - 0.193) / 5);
    expect(arrivalBloomDiameterPx(half, 1440, 900, dHalf)).toBeCloseTo(Math.sqrt(480 * 1440), 6);
    // Never falling as the flight nears
    let last = 0;
    for (let k = 0; k <= 60; k++) {
      const d = 2 * LIGHT_YEAR_PC * Math.pow(floor / 2 / (2 * LIGHT_YEAR_PC), k / 60);
      const D = arrivalBloomDiameterPx(rigelAt(d), 1440, 900, d);
      expect(D).toBeGreaterThanOrEqual(last - 1e-9);
      last = D;
    }
  });

  it('grows on the one curve the craft\'s shadows deepen on (B, craft-fill-eight-one)', () => {
    const floor = floorOf(0.193, rigelPc);
    for (const d of [0.5 * LIGHT_YEAR_PC, 0.1 * LIGHT_YEAR_PC, 4000 * AU_PC, 500 * AU_PC, floor]) {
      const t = Math.log(arrivalBloomDiameterPx(rigelAt(d), 1440, 900, d) / 480) / Math.log(1440 / 480);
      expect(t).toBeCloseTo(nearStarShare(rigelAt(d), d), 9);
      expect(t).toBeCloseTo(Math.log2(keyToFillOf({ magnitude: rigelAt(d), distancePc: d }) / 8), 9);
    }
  });

  it('grows a fainter star from its own size at a light year, under the cap there', () => {
    // A star of magnitude 6 at 100 pc: about 61 px across at a light year
    const at = (d: number) => magnitudeAt(6, 100, d);
    const from = starCurvePx(at(LIGHT_YEAR_PC));
    expect(from).toBeGreaterThan(50);
    expect(from).toBeLessThan(480);
    expect(arrivalBloomDiameterPx(at(LIGHT_YEAR_PC), 1440, 900, LIGHT_YEAR_PC)).toBeCloseTo(from, 9);
    const floor = floorOf(6, 100);
    expect(arrivalBloomDiameterPx(at(floor), 1440, 900, floor)).toBeCloseTo(1440, 6);
    // Halfway in magnitude: halfway on the log scale from its own size at a light year, not from the cap
    const half = (at(LIGHT_YEAR_PC) + SUN_ON_EARTH_MAG) / 2;
    const dHalf = 100 * Math.pow(10, (half - 6) / 5);
    expect(arrivalBloomDiameterPx(half, 1440, 900, dHalf)).toBeCloseTo(Math.sqrt(from * 1440), 6);
  });

  it('holds the white-hot centre at arrival-star-1\'s size: only the faint skirt grows', () => {
    // Today's bloom 480 px across, A's 1440: the centre's term at every pixel radius the same in both
    const share = 480 / 1440;
    const core = (glow: number, q: number) => glow - 0.25 * Math.pow(1 - q, 3);
    for (const r of [0, 10, 40, 77, 120, 200, 239]) {
      expect(core(arrivalGlow(r / 720, share), r / 720)).toBeCloseTo(core(arrivalGlow(r / 240), r / 240), 9);
    }
    // The skirt reaches where today's bloom has ended
    expect(arrivalGlow(400 / 240)).toBe(0);
    expect(arrivalGlow(400 / 720, share)).toBeGreaterThan(0.01);
    // Brightest at the point, as before, falling to nothing at the edge
    expect(arrivalGlow(0, share)).toBeCloseTo(1.15, 9);
    for (let q = 0; q <= 1; q += 0.01) expect(arrivalGlow(q, 1)).toBe(arrivalGlow(q));
    let last = Number.POSITIVE_INFINITY;
    for (let q = 0; q <= 1.0001; q += 0.01) {
      const g = arrivalGlow(q, share);
      expect(g).toBeLessThanOrEqual(last);
      last = g;
    }
    expect(arrivalGlow(1, share)).toBe(0);
  });

  it('lays a wide glare under the held centre, from none a light year out to its fill at the floor, never past the peak', () => {
    expect(ARRIVAL_GLARE_AT_FLOOR).toBe(0.35);
    const share = 480 / 1440;
    const g = ARRIVAL_GLARE_AT_FLOOR;
    // No glare: as before; the peak stays arrival-star-1's 1.15 with any glare
    for (let q = 0; q <= 1; q += 0.05) expect(arrivalGlow(q, share, 0)).toBe(arrivalGlow(q, share));
    expect(arrivalGlow(0, share, g)).toBeCloseTo(1.15, 9);
    expect(arrivalGlow(0, 1, g)).toBeCloseTo(1.15, 9);
    // Nothing at the bloom's edge, falling all the way
    expect(arrivalGlow(0.9999, share, g)).toBeLessThan(1e-3);
    expect(arrivalGlow(1, share, g)).toBe(0);
    let last = Number.POSITIVE_INFINITY;
    for (let q = 0; q <= 1.0001; q += 0.005) {
      const v = arrivalGlow(q, share, g);
      expect(v).toBeLessThanOrEqual(last + 1e-12);
      last = v;
    }
    // In pixels at the laptop: the white-hot centre (0.9 and over) stays near arrival-star-1's, the glare (a quarter and
    // over) reaches out past twice as far again
    const reach = (level: number, radiusPx: number, s: number, glare: number) => {
      let r = 0;
      while (r < radiusPx && arrivalGlow(r / radiusPx, s, glare) >= level) r += 0.25;
      return r;
    };
    const whiteToday = reach(0.9, 240, 1, 0);
    const glareToday = reach(0.25, 240, 1, 0);
    expect(reach(0.9, 720, share, g) / whiteToday).toBeLessThan(1.6);
    expect(reach(0.25, 720, share, g) / glareToday).toBeGreaterThan(2.5);
    // Outside the centre the glare never nears white: under 0.6 past three times the centre's reach, under half past five
    for (let r = 3 * whiteToday; r < 720; r += 5) expect(arrivalGlow(r / 720, share, g)).toBeLessThan(0.6);
    for (let r = 5 * whiteToday; r < 720; r += 5) expect(arrivalGlow(r / 720, share, g)).toBeLessThan(0.5);
  });

  it('yields under the phone\'s words: below the readout\'s top it is arrival-star-1\'s bloom, the words winning', () => {
    // The phone's readout stands from the sky band's foot; the laptop's words stand in their own panes
    expect(phoneWordsTopPx(true, phoneCraftFraming(390, 400))).toBeCloseTo(400, 9);
    expect(phoneWordsTopPx(true, phoneCraftFraming(390, 612))).toBeCloseTo(612, 9);
    expect(phoneWordsTopPx(false, phoneCraftFraming(390, 400))).toBe(-1);
    expect(phoneWordsTopPx(true, undefined)).toBe(-1);
    // A framing that does not say its band's height: from its framing point at 62 percent of the band
    const { frameHeightPx: _height, ...noHeight } = phoneCraftFraming(390, 400);
    expect(phoneWordsTopPx(true, noHeight)).toBeCloseTo(400, 9);
    // A fade long enough to read as the glare thinning towards the words, never as an edge across the sky
    expect(BLOOM_YIELD_RAMP_PX).toBe(120);
    const share = 480 / 1440;
    const g = ARRIVAL_GLARE_AT_FLOOR;
    for (const q of [0, 0.05, 0.2, 0.3, 0.5, 0.9]) {
      // Wholly under the words: the bloom as arrival-star-1 drew it, at its capped size
      expect(arrivalGlowYielded(q, share, g, 1)).toBeCloseTo(arrivalGlow(q / share), 12);
      // Clear of them: the grown bloom
      expect(arrivalGlowYielded(q, share, g, 0)).toBeCloseTo(arrivalGlow(q, share, g), 12);
      expect(arrivalGlowYielded(q, share, g, 0.5)).toBeCloseTo((arrivalGlow(q / share) + arrivalGlow(q, share, g)) / 2, 12);
    }
    // Past arrival-star-1's edge nothing is drawn under the words
    expect(arrivalGlowYielded(0.5, share, g, 1)).toBe(0);
  });

  it('sets the glare by the one near-star share: none a light year out, its fill at the floor', () => {
    const session = there();
    const camera = flightCameraOf(session, 1440, 900)!;
    const picked = session.observationSolution!.findings.find((f) => f.id === session.selectedFindingId)!;
    const placement = placeFinding(picked);
    const pos = placement.kind === 'at_distance' ? placement.positionPc : ([0, 0, 0] as Vec3);
    const earthPc = Math.hypot(pos[0], pos[1], pos[2]);
    const view = starViewOfFlight(camera);
    const from = (d: number) => ({ ...view, observerPc: pos.map((x) => x * (1 - d / earthPc)) as unknown as Vec3 });
    const star = { positionPc: pos, earthPc, magnitude: 0.193, tint: [0.8, 0.85, 1] as [number, number, number] };
    for (const d of [LIGHT_YEAR_PC, 0.1 * LIGHT_YEAR_PC, 4000 * AU_PC, floorOf(0.193, earthPc)]) {
      const bloom = arrivalBloomOf(star, from(d))!;
      expect(bloom.glare).toBeCloseTo(ARRIVAL_GLARE_AT_FLOOR * nearStarShare(magnitudeAt(0.193, earthPc, d), d), 9);
    }
    expect(arrivalBloomOf(star, from(LIGHT_YEAR_PC))!.glare).toBeCloseTo(0, 9);
    expect(arrivalBloomOf(star, from(floorOf(0.193, earthPc)))!.glare).toBeCloseTo(ARRIVAL_GLARE_AT_FLOOR, 9);
  });

  it('takes a star\'s temperature from its B-V by Ballesteros\'s black-body fit, within a sane range', () => {
    // The Sun's B-V of 0.65 gives about 5778 K (its effective temperature is 5772 K)
    expect(temperatureOfBMinusV(0.65)).toBeCloseTo(5778, -1);
    expect(temperatureOfBMinusV(0.65)).toBeCloseTo(4600 * (1 / (0.92 * 0.65 + 1.7) + 1 / (0.92 * 0.65 + 0.62)), 9);
    expect(temperatureOfBMinusV(-0.03)).toBeGreaterThan(temperatureOfBMinusV(0.65));
    expect(temperatureOfBMinusV(1.85)).toBeLessThan(3500);
    // Held inside -0.4 to 2.0, so a stray colour never divides by nothing
    expect(temperatureOfBMinusV(-1)).toBe(temperatureOfBMinusV(-0.4));
    expect(temperatureOfBMinusV(3)).toBe(temperatureOfBMinusV(2));
    expect(temperatureOfBMinusV(Number.NaN)).toBeNaN();
  });

  it('models the disc from magnitude, colour and distance, set by the Sun\'s own', () => {
    expect(SUN_TEFF_K).toBe(5772);
    expect(SUN_ANGULAR_RADIUS_AT_AU_RAD).toBeCloseTo(695700 / 149597870.7, 15);
    // The Sun at one AU: its own disc
    expect(discAngularRadiusRad(SUN_ON_EARTH_MAG, SUN_TEFF_K)).toBeCloseTo(SUN_ANGULAR_RADIUS_AT_AU_RAD, 15);
    // Ten times farther (five magnitudes fainter): a tenth of the size
    expect(discAngularRadiusRad(SUN_ON_EARTH_MAG + 5, SUN_TEFF_K)).toBeCloseTo(SUN_ANGULAR_RADIUS_AT_AU_RAD / 10, 15);
    // As bright and hotter: a smaller disc; cooler: a larger
    expect(discAngularRadiusRad(-20, 10000)).toBeLessThan(discAngularRadiusRad(-20, 5772));
    expect(discAngularRadiusRad(-20, 3300)).toBeGreaterThan(discAngularRadiusRad(-20, 5772));
    expect(discAngularRadiusRad(Number.NaN, 5772)).toBe(0);
    expect(discAngularRadiusRad(-20, Number.NaN)).toBe(0);
    // The radius it gives, in Suns, the same from any distance: Rigel near the published 79 (Moravveji et al. 2012) and
    // Betelgeuse (V 0.42, B-V 1.85, 168 pc, Joyce et al. 2020) near the published 764, within a quarter [inference]
    const suns = (v: number, bv: number, earthPc: number, d: number) => (discAngularRadiusRad(magnitudeAt(v, earthPc, d), temperatureOfBMinusV(bv)) * d) / (SUN_ANGULAR_RADIUS_AT_AU_RAD * AU_PC);
    for (const d of [LIGHT_YEAR_PC, 1000 * AU_PC]) {
      expect(suns(0.193, -0.03, rigelPc, d)).toBeGreaterThan(79 * 0.75);
      expect(suns(0.193, -0.03, rigelPc, d)).toBeLessThan(79 * 1.25);
      expect(suns(0.42, 1.85, 168, d)).toBeGreaterThan(764 * 0.75);
      expect(suns(0.42, 1.85, 168, d)).toBeLessThan(764 * 1.25);
    }
    expect(suns(0.193, -0.03, rigelPc, LIGHT_YEAR_PC)).toBeCloseTo(suns(0.193, -0.03, rigelPc, 1000 * AU_PC), 9);
  });

  it('carries the row\'s colour as B-V: Hipparcos as it is, Gaia\'s BP-RP by the renderer\'s own slope, none as none', () => {
    const at = (colorIndex: number | undefined, bMinusV: boolean) =>
      arrivalStarOfTile(tileOf([{ dir: raDecToUnitVector(10, 20), mag: 5, distancePc: 145.4, kind: 1, colorIndex, bMinusV }]), raDecToUnitVector(10, 20).map((x) => x * 145.4) as Vec3)!;
    expect(at(1.85, true).bMinusV).toBeCloseTo(Math.fround(1.85), 9);
    expect(at(1.788, false).bMinusV).toBeCloseTo(Math.fround(1.788) / B_MINUS_V_TO_BP_RP, 9);
    expect(at(undefined, false).bMinusV).toBeNaN();
  });

  it('draws the disc only where it passes the star\'s own sprite, centred on the star, white-hot as the sprite\'s core', () => {
    const session = there();
    const camera = flightCameraOf(session, 1440, 900)!;
    const picked = session.observationSolution!.findings.find((f) => f.id === session.selectedFindingId)!;
    const placement = placeFinding(picked);
    const pos = placement.kind === 'at_distance' ? placement.positionPc : ([0, 0, 0] as Vec3);
    const earthPc = Math.hypot(pos[0], pos[1], pos[2]);
    const view = starViewOfFlight(camera);
    const pxPerRad = Math.hypot(view.storedToScreen[0], view.storedToScreen[3]) / view.scaleRadPerPx;
    // The camera on the line from Earth through the star, `d` from it
    const from = (d: number) => ({ ...view, observerPc: pos.map((x) => x * (1 - d / earthPc)) as unknown as Vec3 });
    const red = { positionPc: pos, earthPc, magnitude: 0.42, tint: [1, 0.62, 0.43] as [number, number, number], bMinusV: 1.85 };
    const blue = { ...red, magnitude: 0.193, tint: [0.8, 0.85, 1] as [number, number, number], bMinusV: -0.03 };
    const raw = (star: typeof red, d: number) => 2 * discAngularRadiusRad(magnitudeAt(star.magnitude, earthPc, d), temperatureOfBMinusV(star.bMinusV)) * pxPerRad;
    const redFloor = floorOf(red.magnitude, earthPc);
    expect(STAR_SPRITE_PX).toBe(22);
    expect(raw(red, redFloor)).toBeGreaterThan(STAR_SPRITE_PX);
    const near = arrivalBloomOf(red, from(redFloor))!;
    expect(near.discDiameterPx).toBeGreaterThan(STAR_SPRITE_PX);
    // Measured through the camera's own projection: within 3 percent of the scale at the frame's centre (the star sits
    // off the photo's axis, where a gnomonic view stretches a little)
    expect(Math.abs(near.discDiameterPx / raw(red, redFloor) - 1)).toBeLessThan(0.03);
    expect(near.discDiameterPx).toBeLessThan(near.diameterPx);
    expect(near.coreDiameterPx).toBeCloseTo(cappedBloomDiameterPx(SUN_ON_EARTH_MAG, 1440, 900), 6);
    expect(near.diameterPx).toBeCloseTo(1440, 3);
    // A light year out the same star's disc is far under the sprite: none
    expect(raw(red, LIGHT_YEAR_PC)).toBeLessThan(1);
    expect(arrivalBloomOf(red, from(LIGHT_YEAR_PC))!.discDiameterPx).toBe(0);
    // A hot star at its floor: its disc stays inside the sprite: none
    const blueFloor = floorOf(blue.magnitude, earthPc);
    expect(raw(blue, blueFloor)).toBeLessThan(STAR_SPRITE_PX);
    expect(arrivalBloomOf(blue, from(blueFloor))!.discDiameterPx).toBe(0);
    // No colour, no temperature: none
    expect(arrivalBloomOf({ ...red, bMinusV: Number.NaN }, from(redFloor))!.discDiameterPx).toBe(0);
    expect(arrivalBloomOf({ positionPc: pos, earthPc, magnitude: 0.42, tint: red.tint }, from(redFloor))!.discDiameterPx).toBe(0);
    // White-hot as the star pass's core: the tint taken 0.6 of the way to white
    expect(discTintOf([1, 0.62, 0.43])).toEqual([1, 0.62 + 0.38 * 0.6, 0.43 + 0.57 * 0.6].map((c) => expect.closeTo(c, 12)));
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

  it('keeps one full shift for the whole flight, wherever the camera stands on its way (arrival-shift-steady-one)', () => {
    // Go there's animation writes the camera's place to a thousandth of a parsec each frame
    // (useCameraFlightAnimation.ts), which near the star leaves it a hair off the line to it: the full shift, and so
    // Real sky's area, must not follow that, or both sky canvases are drawn again every frame of Phase B
    for (const [w, h, phone] of [[1440, 900, false], [390, 844, true]] as const) {
      const s = there();
      const picked = s.observationSolution!.findings.find((f) => f.id === s.selectedFindingId)!;
      const placement = placeFinding(picked);
      if (placement.kind !== 'at_distance') throw new Error('the picked star has a distance');
      const end = flightEndOrigin([0, 0, 0], placement.positionPc);
      const atRest = arrivalShiftOf({ ...inPhase(s, 'at_space', 1), spatialView: { ...s.spatialView, transitionPhase: 'at_space', transitionProgress: 1, observerOriginParsec: end } } as ObservationSession, w, h, phone)!;
      for (const p of [0.05, 0.3, 0.6, 0.9, 0.97, 0.995]) {
        const at = end.map((c) => Number((c * p).toFixed(3))) as [number, number, number];
        const flying = { ...s, spatialView: { ...s.spatialView, transitionPhase: 'phase_b_sky_to_space', transitionProgress: p, observerOriginParsec: at } } as ObservationSession;
        const shift = arrivalShiftOf(flying, w, h, phone)!;
        expect(shift.fullPx[0]).toBe(atRest.fullPx[0]);
        expect(shift.fullPx[1]).toBe(atRest.fullPx[1]);
      }
    }
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
    expect(arrivalCraftFraming(base, 1440, 900, false, 1)).toEqual({ xPx: expect.closeTo(0.361 * 1440, 9), yPx: expect.closeTo(0.711 * 900, 9), diameterPx: base.diameterPx, frameHeightPx: 900 });
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

