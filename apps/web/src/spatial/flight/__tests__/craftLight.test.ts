import { describe, expect, it } from 'vitest';
import { catalogStarTint } from '../../catalogStars/catalogStarTint.js';
import {
  CRAFT_SKY_SIZE,
  skyIntegralOfPixels,
  ACES_INPUT_SCALE,
  MIDDLE_GREY,
  SKY_INTEGRATED_MAG,
  SUN_ABSOLUTE_V_MAG,
  keyLight,
  magnitudeAt,
  meteredExposure,
  skyFillRatio,
  sunMagnitudeAt,
  type LightCandidate,
} from '../craftLight.js';

/**
 * craft-light-one, ruled by the lead at 15:34 on 24 Nov (rule A): a craft's key light is the brightest real source where
 * the camera is, from its true direction with its catalog tint; its only fill is the sky's own light, the catalog's
 * summed starlight; the exposure is metered on the side the camera sees at the photographic middle grey 0.18.
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

describe('the fill: only the sky’s own light', () => {
  it('is the catalog’s summed starlight over the key’s', () => {
    expect(SKY_INTEGRATED_MAG).toBe(-6.39);
    expect(skyFillRatio(-12.43)).toBeCloseTo(10 ** (-0.4 * (-6.39 + 12.43)), 12);
    expect(1 / skyFillRatio(-12.43)).toBeCloseTo(261, 0);
    expect(skyFillRatio(-9.27)).toBeCloseTo(0.0705, 3);
  });
});

describe('the exposure: metered on the side the camera sees', () => {
  // A white Lambert surface's radiance is its irradiance over pi; three's ACES multiplies by exposure / 0.6
  const acesInput = (exposure: number, irradiance: number) => (exposure * irradiance) / Math.PI / ACES_INPUT_SCALE;

  it('puts the seen side at middle grey when the star is behind the craft', () => {
    const fill = skyFillRatio(-9.27);
    const e = meteredExposure({ keyOnSeenSide: 0, fillRatio: fill });
    expect(MIDDLE_GREY).toBe(0.18);
    expect(acesInput(e, fill / 4)).toBeCloseTo(0.18, 12);
  });

  // three r187's ACES curve reaches code 254 of 255 for white at this input
  const CODE_254_INPUT = 16;

  it('lets the meter win beside Hadar, where the star-lit rim goes past white (ruled 16:08: the cap is dropped)', () => {
    const fill = skyFillRatio(-12.43);
    const e = meteredExposure({ keyOnSeenSide: 0, fillRatio: fill });
    expect(acesInput(e, fill / 4)).toBeCloseTo(0.18, 12);
    expect(acesInput(e, 1)).toBeGreaterThan(CODE_254_INPUT);
  });

  it('meters at middle grey even when the star only grazes the side the camera sees: no cap holds the exposure down', () => {
    const fill = skyFillRatio(-12.43);
    const e = meteredExposure({ keyOnSeenSide: 0.002, fillRatio: fill });
    expect(acesInput(e, 0.002 + fill / 4)).toBeCloseTo(MIDDLE_GREY, 12);
    expect(acesInput(e, 1)).toBeGreaterThan(CODE_254_INPUT);
  });

  it('meters a seen side the star half lights at middle grey', () => {
    const fill = skyFillRatio(-12.43);
    const e = meteredExposure({ keyOnSeenSide: 0.5, fillRatio: fill });
    expect(acesInput(e, 0.5 + fill / 4)).toBeCloseTo(MIDDLE_GREY, 12);
  });

  it('meters a well lit seen side beside a fainter star at middle grey', () => {
    const fill = skyFillRatio(-9.27);
    const e = meteredExposure({ keyOnSeenSide: 0.01, fillRatio: fill });
    expect(acesInput(e, 0.01 + fill / 4)).toBeCloseTo(0.18, 12);
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
