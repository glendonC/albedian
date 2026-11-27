import { describe, expect, it } from 'vitest';
import { catalogStarTint } from '../../catalogStars/catalogStarTint.js';
import {
  CRAFT_SKY_SIZE,
  skyIntegralOfPixels,
  ACES_INPUT_SCALE,
  GREY_CARD_ALBEDO,
  MIDDLE_GREY,
  CRAFT_GALAXY_TO_KEY,
  CRAFT_KEY_TO_FILL,
  CRAFT_KEY_TO_FILL_AT_FLOOR,
  CRAFT_WHITE_CAP_INPUT,
  AU_PC,
  LIGHT_YEAR_PC,
  SUN_ON_EARTH_MAG,
  keyToFillOf,
  nearStarShare,
  craftLighting,
  seenSideKeyShare,
  SUN_ABSOLUTE_V_MAG,
  keyLight,
  magnitudeAt,
  meteredExposure,
  sunMagnitudeAt,
  type LightCandidate,
} from '../craftLight.js';

/**
 * craft-light-one, ruled by the lead at 15:34 on 24 Nov (rule A): a craft's key light is the brightest real source where
 * the camera is, from its true direction with its catalog tint; its only fill is the sky's own light, the catalog's
 * summed starlight; the exposure is metered on the side the camera sees at the photographic middle grey 0.18, since
 * craft-light-two incident: a grey card there, not a white surface, lands at middle grey.
 */
const LY_PC = 0.30660139;
const unit = (raDeg: number, decDeg: number): [number, number, number] => {
  const r = (raDeg * Math.PI) / 180;
  const d = (decDeg * Math.PI) / 180;
  return [Math.cos(d) * Math.cos(r), Math.cos(d) * Math.sin(r), Math.sin(d)];
};
// Hadar, HIP 68702, from the app's bright-star supplement (hipparcos-bright-3d/stars.json): Hp 0.5366, B-V -0.231, 8.32 mas
const hadarDir = unit(210.9560192034163, -60.372978868942);
const hadarPc = 1000 / 8.32;
const hadar: LightCandidate = { positionPc: [hadarDir[0] * hadarPc, hadarDir[1] * hadarPc, hadarDir[2] * hadarPc], earthMag: 0.5366, earthDistancePc: hadarPc, colorIndex: -0.231, isBMinusV: true };
// A colourless candidate far off the line, for the neutral tint
const plain: LightCandidate = { positionPc: [0, 0, 50], earthMag: 3, earthDistancePc: 50, colorIndex: Number.NaN, isBMinusV: false };
const besideHadar: [number, number, number] = [hadarDir[0] * (hadarPc - LY_PC), hadarDir[1] * (hadarPc - LY_PC), hadarDir[2] * (hadarPc - LY_PC)];
const srgbToLinear = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);

describe('the key: the brightest real source where the camera is', () => {
  it('moves a catalog magnitude by the inverse square law, as the flight does', () => {
    expect(magnitudeAt(hadar, besideHadar)).toBeCloseTo(0.5366 + 5 * Math.log10(LY_PC / hadarPc), 9);
    expect(magnitudeAt(hadar, [0, 0, 0])).toBeCloseTo(0.5366, 9);
  });

  it('puts the Sun at magnitude -26.76 from Earth, from its absolute V magnitude 4.81 (Willmer 2018)', () => {
    expect(SUN_ABSOLUTE_V_MAG).toBe(4.81);
    expect(sunMagnitudeAt([4.84813681e-6, 0, 0])).toBeCloseTo(-26.76, 2);
  });

  it('beside Hadar is Hadar, from Hadar, in its catalog tint made linear', () => {
    const craft = besideHadar;
    const key = keyLight(besideHadar, craft, [plain, hadar]);
    expect(key.source).toBe('star');
    expect(key.magnitude).toBeLessThan(-12);
    const toHadar = [hadar.positionPc[0] - craft[0], hadar.positionPc[1] - craft[1], hadar.positionPc[2] - craft[2]];
    const len = Math.hypot(...toHadar);
    key.direction.forEach((v, i) => expect(v).toBeCloseTo(toHadar[i]! / len, 9));
    const t = catalogStarTint(-0.231, true);
    key.tint.forEach((v, i) => expect(v).toBeCloseTo(srgbToLinear(t[i]!), 9));
  });

  it('near Earth is the Sun, from the Sun', () => {
    const near: [number, number, number] = [0.001, 0, 0];
    const key = keyLight(near, near, [hadar, plain]);
    expect(key.source).toBe('sun');
    key.direction.forEach((v, i) => expect(v).toBeCloseTo([-1, 0, 0][i]!, 9));
    expect(key.tint).toEqual([1, 1, 1]);
  });

  it('gives a colourless star the neutral catalog tint', () => {
    const at: [number, number, number] = [0, 0, 49.9];
    const key = keyLight(at, at, [plain]);
    expect(key.source).toBe('star');
    const t = catalogStarTint(Number.NaN, false);
    key.tint.forEach((v, i) => expect(v).toBeCloseTo(srgbToLinear(t[i]!), 9));
  });
});

// craft-light-two (DECIDED-BY-LEAD on LOOK's pictures): the sky fills at a ruled ratio (since craft-fill-eight-one eight
// to one far from any star, with the brightest lit white placed at the cap), the Milky
// Way's brightest sky is a second light at half the key wherever it lies, and the meter counts every light
describe('the ruled look', () => {
  const sky = { integral: 2.5, galaxy: { direction: [0, 0, 1] as const, tint: [1, 0.86, 0.85] as const } };
  const acesInput = (exposure: number, irradiance: number) => (exposure * irradiance) / Math.PI / ACES_INPUT_SCALE;

  it('holds the sky at eight to one against the key far from any star, and at the ratio it is given', () => {
    expect(CRAFT_KEY_TO_FILL).toBe(8);
    const look = craftLighting({ craftFromCameraM: [0, 0, 10], keyDirection: [1, 0, 0], sky });
    // A surface facing the sky takes a quarter of its whole light: the whole sky at 4 / 8 of the key, spread over the sky's own sum
    expect(look.environmentIntensity).toBeCloseTo(0.5 / 2.5, 12);
    expect(craftLighting({ craftFromCameraM: [0, 0, 10], keyDirection: [1, 0, 0], sky, keyToFill: 16 }).environmentIntensity).toBeCloseTo(0.25 / 2.5, 12);
    expect(craftLighting({ craftFromCameraM: [0, 0, 10], keyDirection: [1, 0, 0], sky: null }).environmentIntensity).toBe(0);
  });

  it('lights the craft from the Milky Way\'s brightest sky at half the key, in its colour', () => {
    expect(CRAFT_GALAXY_TO_KEY).toBe(0.5);
    const look = craftLighting({ craftFromCameraM: [0, 0, 10], keyDirection: [1, 0, 0], sky });
    expect(look.galaxy).toEqual({ direction: [0, 0, 1], tint: [1, 0.86, 0.85], intensity: 0.5 });
    expect(craftLighting({ craftFromCameraM: [0, 0, 10], keyDirection: [1, 0, 0], sky: { integral: 2.5, galaxy: null } }).galaxy).toBeNull();
  });

  it('places the brightest lit white at the cap, counting every light; the seen side at middle grey when it is the brightest', () => {
    expect(CRAFT_WHITE_CAP_INPUT).toBe(1);
    // The camera looks along +z at the craft; the second light comes from the camera's side, the key from beside the craft
    const at: [number, number, number] = [0, 0, 10];
    const fromCameraSide = { integral: 2.5, galaxy: { direction: [0, 0, -1] as const, tint: [1, 1, 1] as const } };
    const fill = 4 / CRAFT_KEY_TO_FILL;
    const look = craftLighting({ craftFromCameraM: at, keyDirection: [1, 0, 0], sky: fromCameraSide });
    expect(seenSideKeyShare(at, [1, 0, 0])).toBe(0);
    // The brightest surface faces between the two lights
    expect(acesInput(look.exposure, Math.hypot(1, 0.5) + fill / 4)).toBeCloseTo(CRAFT_WHITE_CAP_INPUT, 12);
    // Counting the second light lowers the exposure against a cap that left it out
    expect(look.exposure).toBeLessThan((CRAFT_WHITE_CAP_INPUT * ACES_INPUT_SCALE * Math.PI) / (1 + fill / 4));
    // Every light from the camera's side: the seen side is the brightest, and a grey card there lands at middle grey
    const front = craftLighting({ craftFromCameraM: at, keyDirection: [0, 0, -1], sky: fromCameraSide });
    expect(GREY_CARD_ALBEDO * acesInput(front.exposure, 1.5 + fill / 4)).toBeCloseTo(MIDDLE_GREY, 12);
  });
});

describe('the exposure: metered on the side the camera sees', () => {
  // A white Lambert surface's radiance is its irradiance over pi; three's ACES multiplies by exposure / 0.6
  const acesInput = (exposure: number, irradiance: number) => (exposure * irradiance) / Math.PI / ACES_INPUT_SCALE;

  it('puts the seen side at middle grey when the star is behind the craft', () => {
    const fill = 0.0705;
    const e = meteredExposure({ keyOnSeenSide: 0, fillRatio: fill });
    expect(MIDDLE_GREY).toBe(0.18);
    expect(GREY_CARD_ALBEDO).toBe(0.18);
    // Incident: a grey card lands at middle grey, so a white surface lands at one
    expect(GREY_CARD_ALBEDO * acesInput(e, fill / 4)).toBeCloseTo(0.18, 12);
    expect(acesInput(e, fill / 4)).toBeCloseTo(1, 12);
  });

  // three r187's ACES curve reaches code 254 of 255 for white at this input
  const CODE_254_INPUT = 16;

  it('lets the meter win beside Hadar, where the star-lit rim goes past white (ruled 16:08: the cap is dropped)', () => {
    const fill = 1 / 261;
    const e = meteredExposure({ keyOnSeenSide: 0, fillRatio: fill });
    expect(GREY_CARD_ALBEDO * acesInput(e, fill / 4)).toBeCloseTo(0.18, 12);
    expect(acesInput(e, 1)).toBeGreaterThan(CODE_254_INPUT);
  });

  it('meters at middle grey even when the star only grazes the side the camera sees: no cap holds the exposure down', () => {
    const fill = 1 / 261;
    const e = meteredExposure({ keyOnSeenSide: 0.002, fillRatio: fill });
    expect(GREY_CARD_ALBEDO * acesInput(e, 0.002 + fill / 4)).toBeCloseTo(MIDDLE_GREY, 12);
    expect(acesInput(e, 1)).toBeGreaterThan(CODE_254_INPUT);
  });

  it('meters a seen side the star half lights at middle grey', () => {
    const fill = 1 / 261;
    const e = meteredExposure({ keyOnSeenSide: 0.5, fillRatio: fill });
    expect(GREY_CARD_ALBEDO * acesInput(e, 0.5 + fill / 4)).toBeCloseTo(MIDDLE_GREY, 12);
  });

  it('meters a well lit seen side beside a fainter star at middle grey', () => {
    const fill = 0.0705;
    const e = meteredExposure({ keyOnSeenSide: 0.01, fillRatio: fill });
    expect(GREY_CARD_ALBEDO * acesInput(e, 0.01 + fill / 4)).toBeCloseTo(0.18, 12);
  });
});

describe('the key from the star you flew to, and the sky turned to where it is', async () => {
  const fs = await import('node:fs');
  const path = await import('node:path');
  const { fileURLToPath } = await import('node:url');
  const { createSyntheticCalibratedSession } = await import('@albedian/observation/testing');
  const { placeFinding } = await import('../flightFrame.js');
  const { parsePanoramaRegistration, plateCarreeUv, applyRows, icrsToPanoramaRows } = await import('../../sky/skyRegistration.js');
  const { lightCandidateOfFinding, environmentSamplingRows, environmentRotationRows, seenSideKeyShare, environmentIntensity } = await import('../craftLight.js');
  const here = path.dirname(fileURLToPath(import.meta.url));
  const registration = parsePanoramaRegistration(JSON.parse(fs.readFileSync(path.resolve(here, '../../../../public/textures/eso0932a_registration.json'), 'utf8')));

  it('takes the picked star’s place, catalog magnitude and colour from its finding', () => {
    const session = createSyntheticCalibratedSession();
    const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc && f.catalogRef?.apparentMagnitude !== undefined)!;
    const c = lightCandidateOfFinding(star)!;
    const placed = placeFinding(star);
    expect(placed.kind).toBe('at_distance');
    if (placed.kind !== 'at_distance') return;
    expect(c.positionPc).toEqual(placed.positionPc);
    expect(c.earthDistancePc).toBe(placed.distancePc);
    expect(c.earthMag).toBe(star.catalogRef!.apparentMagnitude);
    expect(Number.isNaN(c.colorIndex) || c.colorIndex === star.catalogRef!.colorIndexBpRp).toBe(true);
    expect(c.isBMinusV).toBe(false);
    expect(lightCandidateOfFinding({ ...star, catalogRef: undefined, inferredDistancePc: undefined })).toBeNull();
  });

  it('samples the sky where the glow’s own plate carree puts each ICRS direction, in three’s equirect convention', () => {
    const rows = environmentSamplingRows(registration);
    const m = icrsToPanoramaRows(registration);
    for (const d of [[1, 0, 0], [0, 1, 0], [0, 0, 1], [0.3, -0.5, 0.81], [-0.6, 0.2, -0.77]] as const) {
      const n = Math.hypot(...d);
      const v: [number, number, number] = [d[0] / n, d[1] / n, d[2] / n];
      const q = applyRows(rows, v);
      // three r187's equirectUv: (atan(z, x) / 2 pi + 0.5, asin(y) / pi + 0.5)
      const three = [Math.atan2(q[2], q[0]) / (2 * Math.PI) + 0.5, Math.asin(q[1]) / Math.PI + 0.5];
      const [u, w] = plateCarreeUv(applyRows(m, v));
      expect(Math.abs(((three[0]! - u + 1.5) % 1) - 0.5)).toBeLessThan(1e-9);
      expect(Math.abs(three[1]! - w)).toBeLessThan(1e-9);
    }
  });

  it('gives three the rotation whose transpose is that sampling (r187 samples transpose(R(E)) times the normal)', () => {
    const s = environmentSamplingRows(registration);
    const r = environmentRotationRows(registration);
    for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) expect(r[3 * i + j]).toBeCloseTo(s[3 * j + i]!, 12);
  });

  it('shares the key on the seen side only where the star is on the camera’s side of the craft', () => {
    expect(seenSideKeyShare([0, 0, 10], [0, 0, 1])).toBe(0);
    expect(seenSideKeyShare([0, 0, 10], [0, 0, -1])).toBeCloseTo(1, 12);
    expect(seenSideKeyShare([0, 0, 10], [1, 0, 0])).toBeCloseTo(0, 12);
  });

  it('scales the sky so its whole light over the key’s is the fill ratio', () => {
    expect(environmentIntensity(0.00384, 0.1629)).toBeCloseTo(0.00384 / 0.1629, 12);
  });
});

describe('the sky\'s light summed over the sphere (the craft\'s fill, from its small copy)', () => {
  function uniform(code: number, width = 72, height = 36): Uint8ClampedArray {
    const px = new Uint8ClampedArray(4 * width * height);
    for (let i = 0; i < px.length; i += 4) px.set([code, code, code, 255], i);
    return px;
  }
  it('sums a uniform white sky to the sphere\'s whole solid angle, 4 pi', () => {
    expect(skyIntegralOfPixels(uniform(255), 72, 36)).toBeCloseTo(4 * Math.PI, 2);
  });
  it('sums in linear light: a mid-grey code is about a fifth of white, not half', () => {
    const lin = ((128 / 255 + 0.055) / 1.055) ** 2.4;
    expect(skyIntegralOfPixels(uniform(128), 72, 36) / skyIntegralOfPixels(uniform(255), 72, 36)).toBeCloseTo(lin, 9);
  });
  it('weights each row by the solid angle it covers: a band at the equator outweighs the same band at a pole', () => {
    const w = 72;
    const h = 36;
    const band = (row: number) => {
      const px = uniform(0, w, h);
      for (let x = 0; x < w; x++) px.set([255, 255, 255, 255], 4 * (row * w + x));
      return skyIntegralOfPixels(px, w, h);
    };
    expect(band(18) / band(0)).toBeGreaterThan(20);
  });
  it('is made at 720 by 360', () => {
    expect(CRAFT_SKY_SIZE).toEqual({ width: 720, height: 360 });
  });
});

// chase-light-one (lead's word on LOOK's chase findings): (b) the exposure falls when the star is behind the craft so
// the near side sits under white and the rim reads, and the brightest lit surface never clips
describe('the chase light', async () => {
  const { CRAFT_BACKLIT_STOPS, brightestIrradiance, keyBehindCraft } = await import('../craftLight.js');
  const acesInput = (exposure: number, irradiance: number) => (exposure * irradiance) / Math.PI / ACES_INPUT_SCALE;
  const sky = { integral: 2.5, galaxy: { direction: [0, 1, 0] as const, tint: [1, 1, 1] as const } };

  it('knows how far the key is behind the craft: straight behind 1, beside or behind the camera 0', () => {
    expect(keyBehindCraft([0, 0, 10], [0, 0, 1])).toBe(1);
    expect(keyBehindCraft([0, 0, 10], [Math.sin(Math.PI / 3), 0, Math.cos(Math.PI / 3)])).toBeCloseTo(0.5, 12);
    expect(keyBehindCraft([0, 0, 10], [1, 0, 0])).toBe(0);
    expect(keyBehindCraft([0, 0, 10], [0, 0, -1])).toBe(0);
  });

  it('lets the near side fall one and a half stops under middle grey when the star is straight behind the craft', () => {
    expect(CRAFT_BACKLIT_STOPS).toBe(1.5);
    const at: [number, number, number] = [0, 0, 10];
    // The second light from the camera's side lights the seen side, the key straight behind lights none of it
    const front = { integral: 2.5, galaxy: { direction: [0, 0, -1] as const, tint: [1, 1, 1] as const } };
    const behind = craftLighting({ craftFromCameraM: at, keyDirection: [0, 0, 1], sky: front });
    // The meter's exposure for that seen side, one and a half stops down (the cap, on the brightest surface, not binding)
    expect(behind.exposure).toBeCloseTo(meteredExposure({ keyOnSeenSide: 0.5, fillRatio: 4 / CRAFT_KEY_TO_FILL }) * 2 ** -1.5, 12);
  });

  it('never lets the brightest lit surface pass the cap, whatever the lights\' direction', () => {
    for (const keyDirection of [[0, 0, -1], [1, 0, 0], [0.6, 0.8, 0], [0, 0, 1]] as const) {
      const look = craftLighting({ craftFromCameraM: [0, 0, 10], keyDirection: [...keyDirection], sky });
      const most = brightestIrradiance([...keyDirection], sky.galaxy && { ...sky.galaxy, intensity: 0.5 }, 4 / CRAFT_KEY_TO_FILL);
      expect(acesInput(look.exposure, most)).toBeLessThanOrEqual(CRAFT_WHITE_CAP_INPUT + 1e-9);
    }
    // The key and the second light together, plus the fill's quarter
    expect(brightestIrradiance([1, 0, 0], { direction: [1, 0, 0], intensity: 0.5 }, 1)).toBeCloseTo(1.75, 12);
    expect(brightestIrradiance([1, 0, 0], { direction: [-1, 0, 0], intensity: 0.5 }, 1)).toBeCloseTo(0.75, 12);
  });
});

// craft-fill-eight-one (DECIDED-BY-LEAD on LOOK's near-star pictures): the craft's shadows deepen as the key's light
// grows: eight to one a light year from the key and beyond, sixteen to one where it shines as our Sun does on Earth
describe('the fill by the key\'s light', () => {
  const key = (magnitude: number, distancePc: number) => ({ magnitude, distancePc });

  it('is eight to one a light year from the key and beyond, for a star and for the Sun', () => {
    expect(keyToFillOf(key(-12, LIGHT_YEAR_PC))).toBe(8);
    expect(keyToFillOf(key(-3, 1))).toBe(8);
    expect(keyToFillOf(key(SUN_ABSOLUTE_V_MAG, 10))).toBe(8);
  });

  it('is sixteen to one where the key shines as our Sun does on Earth, and nearer', () => {
    expect(CRAFT_KEY_TO_FILL_AT_FLOOR).toBe(16);
    expect(SUN_ON_EARTH_MAG).toBeCloseTo(-26.76, 2);
    expect(LIGHT_YEAR_PC).toBeCloseTo(LY_PC, 8);
    expect(keyToFillOf(key(SUN_ON_EARTH_MAG, 0.001))).toBeCloseTo(16, 12);
    expect(keyToFillOf(key(SUN_ON_EARTH_MAG - 2, 0.0005))).toBe(16);
    expect(keyToFillOf({ magnitude: sunMagnitudeAt([AU_PC, 0, 0]), distancePc: AU_PC })).toBeCloseTo(16, 9);
  });

  it('rises between on a log scale of the key\'s light, never falling as the key nears', () => {
    // Five magnitudes brighter than at a light year, ten short of the floor there: halfway, eight times the square root of two
    expect(keyToFillOf(key(SUN_ON_EARTH_MAG + 5, LIGHT_YEAR_PC / 10))).toBeCloseTo(8 * Math.SQRT2, 9);
    // A star of absolute magnitude -7 coming in from a light year towards its floor (about 0.0036 ly)
    const at = [1, 0.3, 0.1, 0.03, 0.01].map((ly) => ly * LIGHT_YEAR_PC);
    const ratios = at.map((d) => keyToFillOf(key(-7 + 5 * Math.log10(d / 10), d)));
    ratios.slice(1).forEach((r, i) => expect(r).toBeGreaterThan(ratios[i]!));
    expect(ratios[0]).toBe(8);
    expect(ratios.at(-1)!).toBeLessThan(16);
  });

  it('is sixteen when the key is already brighter at a light year than the Sun on Earth', () => {
    expect(keyToFillOf(key(SUN_ON_EARTH_MAG - 10, LIGHT_YEAR_PC / 10))).toBe(16);
  });

  it('stays eight to one without a magnitude or a distance', () => {
    expect(keyToFillOf(key(Number.NaN, 0.1))).toBe(8);
    expect(keyToFillOf(key(-20, 0))).toBe(8);
    expect(keyToFillOf(key(-20, Number.POSITIVE_INFINITY))).toBe(8);
    expect(keyToFillOf(key(-20, Number.NaN))).toBe(8);
  });

  it('goes by the one near-star share, 0 at a light year and beyond to 1 at the floor, that the star flown to grows by', () => {
    for (const [m, d] of [[-12, LIGHT_YEAR_PC], [SUN_ON_EARTH_MAG + 5, LIGHT_YEAR_PC / 10], [-7 + 5 * Math.log10(0.01 * LIGHT_YEAR_PC / 10), 0.01 * LIGHT_YEAR_PC], [SUN_ON_EARTH_MAG, 0.001], [Number.NaN, 0.1]] as const) {
      expect(keyToFillOf(key(m, d))).toBeCloseTo(8 * 2 ** nearStarShare(m, d), 12);
    }
    expect(nearStarShare(-12, LIGHT_YEAR_PC)).toBe(0);
    expect(nearStarShare(-12, 2 * LIGHT_YEAR_PC)).toBe(0);
    expect(nearStarShare(SUN_ON_EARTH_MAG + 5, LIGHT_YEAR_PC / 10)).toBeCloseTo(0.5, 12);
    expect(nearStarShare(SUN_ON_EARTH_MAG, 0.001)).toBe(1);
    expect(nearStarShare(SUN_ON_EARTH_MAG - 1, 0.001)).toBe(1);
    expect(nearStarShare(Number.NaN, 0.1)).toBe(0);
    expect(nearStarShare(-20, 0)).toBe(0);
  });

  it('takes the key\'s distance from where the camera is: the star\'s, or the Sun\'s', () => {
    const beside = keyLight(besideHadar, besideHadar, [plain, hadar]);
    expect(beside.distancePc).toBeCloseTo(LY_PC, 9);
    expect(keyToFillOf(beside)).toBeCloseTo(8, 6);
    const near: [number, number, number] = [0.001, 0, 0];
    expect(keyLight(near, near, [hadar, plain]).distancePc).toBeCloseTo(0.001, 12);
  });
});
