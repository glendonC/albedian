import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import fc from 'fast-check';
import * as THREE from 'three';
import { describe, expect, it } from 'vitest';
import { icrsToScene } from '../catalogStars/CatalogStarField.js';
import { raDecDegToVec } from '../catalogStars/healpix.js';
import { createOriginCameraFrustumIndicator } from '../referenceFrameContinuity.js';
import {
  MAX_LOOK_PITCH_RAD,
  clampLookPitch,
  lookAnglesForSceneDirection,
  resolveSkyTargetPosition,
} from '../webgl/skyTargets.js';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

/**
 * Catalog positions (ICRS, J2000): Hipparcos for the stars, the LMC and SMC optical centres as
 * SIMBAD lists them, Sgr A* (Reid and Brunthaler 2004). The southern ones are the ones the old
 * hand-set scene vectors and pitch clamps got wrong. The last two rows are test directions, not
 * catalog positions: one in Carina near the nebula (no cited position), and one for the Moon, which
 * has no fixed position; both had special cases in the old code.
 */
const TARGETS: Array<{ id: string; title: string; raDeg: number; decDeg: number }> = [
  { id: 'hip_60718_acrux', title: 'Acrux', raDeg: 186.6496, decDeg: -63.0991 },
  { id: 'hip_71683_alpha_centauri', title: 'Alpha Centauri A', raDeg: 219.9021, decDeg: -60.8340 },
  { id: 'hip_30438_canopus', title: 'Canopus', raDeg: 95.9880, decDeg: -52.6957 },
  { id: 'hip_32349_sirius', title: 'Sirius', raDeg: 101.2872, decDeg: -16.7161 },
  { id: 'sgr_a_star', title: 'Sagittarius A*', raDeg: 266.4168, decDeg: -29.0078 },
  { id: 'lmc', title: 'Large Magellanic Cloud', raDeg: 80.894, decDeg: -69.756 },
  { id: 'smc', title: 'Small Magellanic Cloud', raDeg: 13.187, decDeg: -72.829 },
  { id: 'hip_91262_vega', title: 'Vega', raDeg: 279.2347, decDeg: 38.7837 },
  // Test directions, not catalog positions
  { id: 'ngc_3372_carina', title: 'Carina Nebula', raDeg: 161.2650, decDeg: -59.8667 },
  { id: 'moon', title: 'Moon', raDeg: 150.0, decDeg: -25.0 },
];

/**
 * How far apart two directions are: the chord between them as unit vectors, equal to the angle in
 * radians at these sizes. angleTo's acos cannot resolve under about 1.5e-8 rad, so it is not used.
 */
const angleBetween = (a: THREE.Vector3, b: THREE.Vector3) => a.clone().normalize().distanceTo(b.clone().normalize());
const catalogDirection = (raDeg: number, decDeg: number) => icrsToScene(raDecDegToVec(raDeg, decDeg));
/** The direction the sky view's camera faces for a yaw and pitch (camera Euler order YXZ). */
const facing = (yaw: number, pitch: number) =>
  new THREE.Vector3(0, 0, -1).applyEuler(new THREE.Euler(pitch, yaw, 0, 'YXZ'));

describe('southern sky pose in the WebGL sky view', () => {
  it('puts every named target at its catalog direction, not a hand-set scene vector', () => {
    for (const t of TARGETS) {
      const p = resolveSkyTargetPosition(t.id, t.title, t.raDeg, t.decDeg);
      expect(angleBetween(p, catalogDirection(t.raDeg, t.decDeg)), t.title).toBeLessThan(1e-9);
    }
  });

  it('puts any target at its RA and Dec whatever its name says', () => {
    const names = TARGETS.map((t) => [t.id, t.title] as const);
    fc.assert(
      fc.property(
        fc.constantFrom(...names, ['', ''] as const, ['hip_1', 'Some star'] as const),
        fc.double({ min: 0, max: 360, maxExcluded: true, noNaN: true }),
        fc.double({ min: -90, max: 90, noNaN: true }),
        ([id, title], raDeg, decDeg) => {
          const p = resolveSkyTargetPosition(id, title, raDeg, decDeg);
          return angleBetween(p, catalogDirection(raDeg, decDeg)) < 1e-9;
        }
      ),
      {
        seed: SEED,
        examples: [
          [['hip_60718_acrux', 'Acrux'], 186.6496, -63.0991],
          [['lmc', 'Large Magellanic Cloud'], 80.894, -69.756],
          [['', ''], 0, -90],
        ],
      }
    );
  });

  it('lets the camera centre the Magellanic Clouds and Crux, stopping just short of the pole', () => {
    expect(MAX_LOOK_PITCH_RAD).toBeGreaterThan(Math.PI / 2 - 0.02);
    expect(MAX_LOOK_PITCH_RAD).toBeLessThan(Math.PI / 2);
    for (const t of TARGETS) {
      const { yaw, pitch } = lookAnglesForSceneDirection(catalogDirection(t.raDeg, t.decDeg));
      expect(angleBetween(facing(yaw, pitch), catalogDirection(t.raDeg, t.decDeg)), t.title).toBeLessThan(1e-9);
    }
    expect(clampLookPitch(-Math.PI / 2)).toBe(-MAX_LOOK_PITCH_RAD);
    expect(clampLookPitch(2)).toBe(MAX_LOOK_PITCH_RAD);
  });

  it('faces any direction within the pitch limit exactly', () => {
    const limitDeg = (MAX_LOOK_PITCH_RAD * 180) / Math.PI;
    fc.assert(
      fc.property(
        fc.double({ min: 0, max: 360, maxExcluded: true, noNaN: true }),
        fc.double({ min: -limitDeg, max: limitDeg, noNaN: true }),
        (raDeg, decDeg) => {
          const dir = catalogDirection(raDeg, decDeg);
          const { yaw, pitch } = lookAnglesForSceneDirection(dir);
          return angleBetween(facing(yaw, pitch), dir) < 1e-9;
        }
      ),
      { seed: SEED, examples: [[80.894, -69.756], [13.187, -72.829], [0, -limitDeg]] }
    );
  });

  it('routes every look clamp in SparkPortalRenderer through clampLookPitch', () => {
    const src = fs.readFileSync(fileURLToPath(new URL('../SparkPortalRenderer.ts', import.meta.url).href), 'utf8');
    expect(src).not.toMatch(/Math\.max\(-1\.15/);
    expect(src).not.toMatch(/Math\.max\(-0\.85/);
    expect(src.match(/clampLookPitch\(/g)?.length ?? 0).toBeGreaterThanOrEqual(3);
  });

  it('draws no decorative observer horizon ring', () => {
    const frustum = createOriginCameraFrustumIndicator();
    expect(frustum.getObjectByName('ObserverHorizonRing')).toBeUndefined();
    const src = fs.readFileSync(fileURLToPath(new URL('../referenceFrameContinuity.ts', import.meta.url).href), 'utf8');
    expect(src).not.toContain('horizonRing');
  });
});
