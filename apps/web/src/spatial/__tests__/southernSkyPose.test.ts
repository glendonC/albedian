import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import fc from 'fast-check';
import * as THREE from 'three';
import { describe, expect, it } from 'vitest';
import { icrsToScene } from '../catalogStars/CatalogStarField.js';
import { raDecDegToVec } from '../catalogStars/healpix.js';
import * as skyTargets from '../webgl/skyTargets.js';
import { MAX_LOOK_PITCH_RAD, clampLookPitch } from '../webgl/skyTargets.js';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

const read = (rel: string) => fs.readFileSync(fileURLToPath(new URL(rel, import.meta.url).href), 'utf8');

/**
 * Catalog positions (ICRS, J2000): Hipparcos for the stars, the LMC and SMC optical centres as
 * SIMBAD lists them, Sgr A* (Reid and Brunthaler 2004). The southern ones are the ones the old
 * hand-set scene vectors and pitch clamps got wrong.
 */
const TARGETS: Array<{ title: string; raDeg: number; decDeg: number }> = [
  { title: 'Acrux', raDeg: 186.6496, decDeg: -63.0991 },
  { title: 'Alpha Centauri A', raDeg: 219.9021, decDeg: -60.834 },
  { title: 'Canopus', raDeg: 95.988, decDeg: -52.6957 },
  { title: 'Sirius', raDeg: 101.2872, decDeg: -16.7161 },
  { title: 'Sagittarius A*', raDeg: 266.4168, decDeg: -29.0078 },
  { title: 'Large Magellanic Cloud', raDeg: 80.894, decDeg: -69.756 },
  { title: 'Small Magellanic Cloud', raDeg: 13.187, decDeg: -72.829 },
  { title: 'Vega', raDeg: 279.2347, decDeg: 38.7837 },
];

const catalogDirection = (raDeg: number, decDeg: number) => icrsToScene(raDecDegToVec(raDeg, decDeg));
/** The direction the sky view's camera faces for a yaw and pitch (camera Euler order YXZ). */
const facing = (yaw: number, pitch: number) =>
  new THREE.Vector3(0, 0, -1).applyEuler(new THREE.Euler(pitch, yaw, 0, 'YXZ'));

describe('the sky view camera reaches the southern sky', () => {
  it('stops just short of the poles, so the camera never flips over the top', () => {
    expect(MAX_LOOK_PITCH_RAD).toBeGreaterThan(Math.PI / 2 - 0.02);
    expect(MAX_LOOK_PITCH_RAD).toBeLessThan(Math.PI / 2);
    expect(clampLookPitch(-Math.PI / 2)).toBe(-MAX_LOOK_PITCH_RAD);
    expect(clampLookPitch(2)).toBe(MAX_LOOK_PITCH_RAD);
    expect(clampLookPitch(0.3)).toBe(0.3);
  });

  it('can centre the Magellanic Clouds and Crux before a place is known (celestial north up)', () => {
    for (const t of TARGETS) {
      const d = catalogDirection(t.raDeg, t.decDeg);
      // With celestial north up, pitch is the declination and yaw the right ascension
      const pitch = Math.asin(d.y);
      const yaw = Math.atan2(-d.x, -d.z);
      expect(clampLookPitch(pitch), t.title).toBe(pitch);
      // Chord between unit vectors: angleTo's acos cannot resolve under about 1.5e-8 rad
      expect(facing(yaw, pitch).distanceTo(d), t.title).toBeLessThan(1e-9);
    }
  });

  it('faces any direction within the pitch limit exactly', () => {
    const limitDeg = (MAX_LOOK_PITCH_RAD * 180) / Math.PI;
    fc.assert(
      fc.property(
        fc.double({ min: 0, max: 360, maxExcluded: true, noNaN: true }),
        fc.double({ min: -limitDeg, max: limitDeg, noNaN: true }),
        (raDeg, decDeg) => {
          const d = catalogDirection(raDeg, decDeg);
          const pitch = clampLookPitch(Math.asin(d.y));
          return facing(Math.atan2(-d.x, -d.z), pitch).distanceTo(d) < 1e-9;
        }
      ),
      { seed: SEED, examples: [[80.894, -69.756], [13.187, -72.829], [0, -limitDeg]] }
    );
  });

  it('routes every look clamp in SparkPortalRenderer through clampLookPitch', () => {
    const src = read('../SparkPortalRenderer.ts');
    expect(src).not.toMatch(/Math\.max\(-1\.15/);
    expect(src).not.toMatch(/Math\.max\(-0\.85/);
    expect(src.match(/clampLookPitch\(/g)?.length ?? 0).toBeGreaterThanOrEqual(3);
  });
});

describe('dead sky target and frustum code stays cut', () => {
  it('keeps only the pitch limit in skyTargets.ts', () => {
    expect(Object.keys(skyTargets).sort()).toEqual(['MAX_LOOK_PITCH_RAD', 'clampLookPitch']);
  });

  it('draws no hidden frustum indicator, telemetry with made-up offsets, or uncalled target projections', () => {
    const src = read('../SparkPortalRenderer.ts');
    expect(src).not.toMatch(
      /referenceFrameContinuity|originFrustumGroup|getCameraTelemetry|getSkyTargetPosition|projectSkyTargetToScreen|projectCatalogPositionToScreen/
    );
    expect(fs.existsSync(fileURLToPath(new URL('../referenceFrameContinuity.ts', import.meta.url).href))).toBe(false);
  });

  it('builds no invisible telemetry anchor in the planetary scene', () => {
    expect(read('../webgl/planetarySurfaceRegime.ts')).not.toMatch(/parentBodyMesh|telemetry/);
    expect(read('../SparkPortalRenderer.ts')).not.toMatch(/planetaryParentBodyMesh/);
  });
});
