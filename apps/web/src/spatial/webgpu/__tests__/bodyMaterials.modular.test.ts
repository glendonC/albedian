// @vitest-environment node
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { AdditiveBlending, BackSide, FrontSide, NodeMaterial, Texture, Vector3 } from 'three/webgpu';
import { atan, uniform, vec2, vec3, vec4 } from 'three/tsl';
import fc from 'fast-check';
import { describe, expect, it, vi } from 'vitest';
import { createBrunetonLut } from '../brunetonAtmosphere.js';
import * as bodyMaterialsModule from '../cislunar/bodyMaterials.js';
import {
  EARTH_HEIGHT_OFFSET_M,
  EARTH_RADIUS_M,
  MOON_GEOMETRIC_ALBEDO,
  SVS_COLOUR_MEAN_LINEAR_LUMINANCE,
  createAtmosphereShellMaterial,
  createEarthMaterial,
  createMoonMaterial,
  decodeEtopoHeightMetres,
  earthTangentNormal,
  openWaterFraction,
} from '../cislunar/bodyMaterials.js';
import {
  ATMO_LIMB_ALPHA_MAX,
  buildAtmosphereShellNodeMaterial,
} from '../cislunar/materials/atmosphereShellMaterial.js';
import {
  CITY_LIGHT_GAIN,
  buildEarthNodeMaterial,
  decodeEtopoHeightNode,
} from '../cislunar/materials/earthMaterial.js';
import {
  EARTH_HEIGHT_TEX_HEIGHT,
  EARTH_HEIGHT_TEX_WIDTH,
  EARTH_MIN_COS_LAT,
  EARTH_NORTH_STEP_M,
  ETOPO_HIGH_BYTE_SCALE,
  ETOPO_LOW_BYTE_SCALE,
  ICE_SHELF_MIN_RG_HIGH,
  ICE_SHELF_MIN_RG_LOW,
} from '../cislunar/materials/earthReliefMath.js';
import {
  MOON_HORIZON_DIP,
  MOON_MIN_VIEW_COS,
  buildMoonNodeMaterial,
  moonLommelSeeligerReflectance,
  moonTerminatorFade,
} from '../cislunar/materials/moonMaterial.js';
import {
  POLE_AXIS_EPSILON,
  sphereTangentFrameNodes,
  viewDirection,
  viewNormal,
  wrapSeamDerivativeNode,
} from '../cislunar/materials/tslSphereFrame.js';

interface TslGraphSummary {
  varNames: Set<string>;
  numbers: number[];
  textures: Set<Texture>;
}

function walkTslRoot(root: unknown): TslGraphSummary {
  const seen = new Set<object>();
  const varNames = new Set<string>();
  const numbers: number[] = [];
  const textures = new Set<Texture>();

  const walk = (item: unknown): void => {
    if (!item || typeof item !== 'object') return;
    if (seen.has(item)) return;
    seen.add(item);

    if (item instanceof Texture) {
      textures.add(item);
      return;
    }

    const rec = item as Record<string, unknown>;
    if (rec.isVarNode === true && typeof rec.name === 'string' && rec.name.length > 0) {
      varNames.add(rec.name);
    }
    if (rec.isConstNode === true) {
      if (typeof rec.value === 'number') {
        numbers.push(rec.value);
      } else if (rec.value && typeof rec.value === 'object') {
        for (const key of ['x', 'y', 'z', 'w']) {
          const comp = (rec.value as Record<string, unknown>)[key];
          if (typeof comp === 'number') numbers.push(comp);
        }
      }
    }

    for (const val of Object.values(rec)) {
      if (Array.isArray(val)) {
        for (const elem of val) walk(elem);
      } else if (val && typeof val === 'object') {
        walk(val);
      }
    }
  };

  walk(root);
  return { varNames, numbers, textures };
}

function evaluateTslFragmentSummary(material: NodeMaterial): TslGraphSummary {
  const frag = material.fragmentNode as unknown as {
    shaderNode?: { jsFunc?: (input: Record<string, never>) => unknown };
    node?: { shaderNode?: { jsFunc?: (input: Record<string, never>) => unknown } };
  };
  const fn = frag.shaderNode?.jsFunc ?? frag.node?.shaderNode?.jsFunc;
  if (!fn) {
    throw new Error('Expected NodeMaterial.fragmentNode to carry shaderNode.jsFunc');
  }
  return walkTslRoot(fn({}));
}

function hasCloseNumber(numbers: readonly number[], target: number, tol = 1e-9): boolean {
  return numbers.some((n) => Math.abs(n - target) <= tol);
}

describe('bodyMaterials public export surface and source invariants', () => {
  it('exports the exact 10 runtime keys from bodyMaterials.ts', () => {
    expect(Object.keys(bodyMaterialsModule).sort()).toEqual([
      'EARTH_HEIGHT_OFFSET_M',
      'EARTH_RADIUS_M',
      'MOON_GEOMETRIC_ALBEDO',
      'SVS_COLOUR_MEAN_LINEAR_LUMINANCE',
      'createAtmosphereShellMaterial',
      'createEarthMaterial',
      'createMoonMaterial',
      'decodeEtopoHeightMetres',
      'earthTangentNormal',
      'openWaterFraction',
    ]);
  });

  it('pins physical and photometric constants and preserves raw source-inspection invariants', () => {
    expect(EARTH_RADIUS_M).toBe(6_371_000);
    expect(EARTH_HEIGHT_OFFSET_M).toBe(11_000);
    expect(MOON_GEOMETRIC_ALBEDO).toBe(0.12);
    expect(SVS_COLOUR_MEAN_LINEAR_LUMINANCE).toBe(0.483);

    const rawText = fs.readFileSync(
      fileURLToPath(new URL('../cislunar/bodyMaterials.ts', import.meta.url)),
      'utf8',
    );
    expect(rawText).toContain('export const SVS_COLOUR_MEAN_LINEAR_LUMINANCE = 0.483;');
    expect(rawText).toContain('export const MOON_GEOMETRIC_ALBEDO = 0.12;');
    const earthSlice = rawText.slice(rawText.indexOf('export function createEarthMaterial'));
    const earthBody = earthSlice.slice(0, earthSlice.indexOf('\nexport function'));
    expect(earthBody).toMatch(/atan\(p\.z\.negate\(\), p\.x\)/);
    expect(earthBody).not.toMatch(/globeTilt|0\.866/);
  });
});

describe('earthReliefMath constants, ETOPO decoding, ice-shelf suppression, and tangent normals', () => {
  it('exposes shared grid and ice-shelf constants matching ETOPO 2022 and Blue Marble', () => {
    expect(EARTH_HEIGHT_TEX_WIDTH).toBe(4096);
    expect(EARTH_HEIGHT_TEX_HEIGHT).toBe(2048);
    expect(EARTH_NORTH_STEP_M).toBeCloseTo((Math.PI / 2048) * 6_371_000, 9);
    expect(ETOPO_HIGH_BYTE_SCALE).toBe(65_280);
    expect(ETOPO_LOW_BYTE_SCALE).toBe(255);
    expect(ICE_SHELF_MIN_RG_LOW).toBe(0.15);
    expect(ICE_SHELF_MIN_RG_HIGH).toBe(0.35);
    expect(EARTH_MIN_COS_LAT).toBe(0.05);
  });

  it('decodes ETOPO 2022 normalized (r, g) channels across boundary and interior elevations', () => {
    expect(decodeEtopoHeightMetres(0, 0)).toBe(-11_000);
    expect(decodeEtopoHeightMetres(1, 1)).toBe(65_280 + 255 - 11_000);
    expect(decodeEtopoHeightMetres(42 / 255, 248 / 255)).toBeCloseTo(0, 9);
    expect(decodeEtopoHeightMetres(77 / 255, 137 / 255)).toBeCloseTo(8_849, 9);
  });

  it('evaluates openWaterFraction at exact smoothstep thresholds 0.15, 0.25, and 0.35 with optional blue argument', () => {
    expect(openWaterFraction(0.8, 0.15, 0.9)).toBeCloseTo(0.8, 12);
    expect(openWaterFraction(0.8, 0.9, 0.15, 0)).toBeCloseTo(0.8, 12);
    expect(openWaterFraction(0.8, 0.25, 0.4)).toBeCloseTo(0.4, 12);
    expect(openWaterFraction(0.8, 0.35, 0.35, 0)).toBeCloseTo(0, 12);
    expect(openWaterFraction(0.8, 0.6, 0.7)).toBe(0);
  });

  it('clamps waterMask and pins the exact 0.05 polar cosLat floor in earthTangentNormal', () => {
    const seaNormal = earthTangentNormal(0, 4000, 0, 4000, 0, 1.5);
    expect(seaNormal[0]).toBeCloseTo(0, 12);
    expect(seaNormal[1]).toBeCloseTo(0, 12);
    expect(seaNormal[2]).toBeCloseTo(1, 12);

    const clampedLand = earthTangentNormal(0, 1500, 0, 800, 0.3, -0.5);
    const exactLand = earthTangentNormal(0, 1500, 0, 800, 0.3, 0);
    expect(clampedLand[0]).toBeCloseTo(exactLand[0], 12);
    expect(clampedLand[1]).toBeCloseTo(exactLand[1], 12);
    expect(clampedLand[2]).toBeCloseTo(exactLand[2], 12);

    const poleNormal = earthTangentNormal(0, 1000, 0, 0, 1.0, 0);
    const sinAtCos01 = Math.sqrt(1 - 0.10 * 0.10);
    const cos01Normal = earthTangentNormal(0, 1000, 0, 0, sinAtCos01, 0);
    // Pole uses cosLat = 0.05 whereas sinAtCos01 uses unclamped cosLat = 0.10 -> slope ratio is 2.0
    const poleSlope = poleNormal[0] / poleNormal[2];
    const cos01Slope = cos01Normal[0] / cos01Normal[2];
    expect(poleSlope / cos01Slope).toBeCloseTo(2.0, 9);
    const nearPole = earthTangentNormal(0, 1000, 0, 0, 0.9995, 0);
    expect(nearPole[0]).toBeCloseTo(poleNormal[0], 12);
  });

  it('PBT: earthTangentNormal always produces a unit vector with positive up component and monotonic slope signs', () => {
    const seedEnv = Number(process.env.ALBEDIAN_FC_SEED);
    const seed = Number.isInteger(seedEnv) && seedEnv > 0 ? seedEnv : 20261117;
    fc.assert(
      fc.property(
        fc.double({ min: -11_000, max: 9_000, noNaN: true }),
        fc.double({ min: -11_000, max: 9_000, noNaN: true }),
        fc.double({ min: -11_000, max: 9_000, noNaN: true }),
        fc.double({ min: -11_000, max: 9_000, noNaN: true }),
        fc.double({ min: -1.0, max: 1.0, noNaN: true }),
        fc.double({ min: -0.2, max: 1.2, noNaN: true }),
        (hW, hE, hS, hN, sinLat, waterMask) => {
          const [nx, ny, nz] = earthTangentNormal(hW, hE, hS, hN, sinLat, waterMask);
          expect(Math.hypot(nx, ny, nz)).toBeCloseTo(1, 9);
          expect(nz).toBeGreaterThan(0);
          if (waterMask >= 1) {
            expect(nx).toBeCloseTo(0, 12);
            expect(ny).toBeCloseTo(0, 12);
            expect(nz).toBeCloseTo(1, 12);
          } else {
            if (hE > hW) expect(nx).toBeLessThanOrEqual(0);
            if (hE < hW) expect(nx).toBeGreaterThanOrEqual(0);
            if (hN > hS) expect(ny).toBeLessThanOrEqual(0);
            if (hN < hS) expect(ny).toBeGreaterThanOrEqual(0);
          }
        },
      ),
      {
        seed,
        numRuns: 60,
        examples: [
          [0, 1000, 0, 0, 1.0, 0],
          [-5000, 2000, -3000, 1000, -1.0, 1.0],
          [100, 100, 100, 100, 0.0, -0.1],
        ],
      },
    );
  });
});

describe('moonMaterial Lommel-Seeliger reflectance and terminator horizon dip', () => {
  it('evaluates Lommel-Seeliger reflectance and 2.7 deg horizon-dip terminator fade at exact bounds', () => {
    expect(MOON_HORIZON_DIP).toBeCloseTo(Math.sin((2.7 * Math.PI) / 180), 12);
    expect(MOON_MIN_VIEW_COS).toBe(0.001);

    // Normal incidence & view (mu0 = 1, mu = 1) -> 2 * 1 / (1 + 1) = 1
    expect(moonLommelSeeligerReflectance(1, 1)).toBeCloseTo(1, 12);
    // Unlit facet (mu0 <= 0) -> 0
    expect(moonLommelSeeligerReflectance(0, 0.5)).toBe(0);
    expect(moonLommelSeeligerReflectance(-0.4, 0.5)).toBe(0);
    // Grazing view (mu <= 0) clamps mu to MOON_MIN_VIEW_COS (0.001)
    expect(moonLommelSeeligerReflectance(0.5, -0.2)).toBeCloseTo((2 * 0.5) / (0.5 + 0.001), 12);

    // Terminator smoothstep over [-MOON_HORIZON_DIP, 0]
    expect(moonTerminatorFade(-MOON_HORIZON_DIP - 0.01)).toBe(0);
    expect(moonTerminatorFade(-MOON_HORIZON_DIP)).toBeCloseTo(0, 12);
    expect(moonTerminatorFade(-MOON_HORIZON_DIP * 0.75)).toBeCloseTo(0.15625, 12);
    expect(moonTerminatorFade(-MOON_HORIZON_DIP / 2)).toBeCloseTo(0.5, 12);
    expect(moonTerminatorFade(0)).toBeCloseTo(1, 12);
    expect(moonTerminatorFade(0.4)).toBe(1);
  });

  it('PBT: moonLommelSeeligerReflectance and moonTerminatorFade are monotonic and bounded', () => {
    const seedEnv = Number(process.env.ALBEDIAN_FC_SEED);
    const seed = Number.isInteger(seedEnv) && seedEnv > 0 ? seedEnv : 20261117;
    fc.assert(
      fc.property(
        fc.double({ min: -1, max: 1, noNaN: true }),
        fc.double({ min: 0.001, max: 1, noNaN: true }),
        fc.double({ min: 0.01, max: 0.5, noNaN: true }),
        (sunDot, viewCos, step) => {
          const r0 = moonLommelSeeligerReflectance(sunDot, viewCos);
          const r1 = moonLommelSeeligerReflectance(Math.min(1, sunDot + step), viewCos);
          expect(r0).toBeGreaterThanOrEqual(0);
          expect(r0).toBeLessThan(2);
          expect(r1).toBeGreaterThanOrEqual(r0 - 1e-12);

          const f0 = moonTerminatorFade(sunDot);
          const f1 = moonTerminatorFade(sunDot + step);
          expect(f0).toBeGreaterThanOrEqual(0);
          expect(f0).toBeLessThanOrEqual(1);
          expect(f1).toBeGreaterThanOrEqual(f0 - 1e-12);
        },
      ),
      {
        seed,
        numRuns: 60,
        examples: [
          [-MOON_HORIZON_DIP, 0.5, 0.02],
          [0, 0.001, 0.1],
          [0.5, 0.5, 0.25],
        ],
      },
    );
  });
});

describe('tslSphereFrame and extracted TSL material builders', () => {
  it('constructs shared TSL tangent frame, seam derivative wrapper, and ETOPO height node', () => {
    expect(POLE_AXIS_EPSILON).toBe(1e-7);
    expect(CITY_LIGHT_GAIN).toBeCloseTo(1.25 / 3, 12);
    expect(ATMO_LIMB_ALPHA_MAX).toBe(0.84);

    const frame = sphereTangentFrameNodes(vec3(0, 0, 1));
    const eastSummary = walkTslRoot(frame.east);
    expect(hasCloseNumber(eastSummary.numbers, 1e-7, 1e-15)).toBe(true);

    const wrapped = walkTslRoot(wrapSeamDerivativeNode(vec2(0.8, 0.1)));
    expect(hasCloseNumber(wrapped.numbers, 0.5)).toBe(true);

    const heightNode = walkTslRoot(decodeEtopoHeightNode(vec4(0.2, 0.3, 0, 1)));
    expect(hasCloseNumber(heightNode.numbers, 65_280)).toBe(true);
    expect(hasCloseNumber(heightNode.numbers, 255)).toBe(true);
    expect(hasCloseNumber(heightNode.numbers, 11_000)).toBe(true);

    expect(viewNormal()).toBeTruthy();
    expect(viewDirection()).toBeTruthy();
  });

  it('constructs cislunarEarth, cislunarAtmosphereShell, and cislunarMoon NodeMaterials and pins their TSL AST constants and textures', () => {
    const lutTex = new Texture();
    const baseLut = createBrunetonLut(lutTex, uniform(0));
    const sampleSpy = vi.spyOn(baseLut, 'sample');
    const sunView = uniform(new Vector3(0.6, 0.3, 0.7));
    const sunWorld = uniform(new Vector3(-0.8, 0.1, 0.5));

    const earthTextures = {
      day: new Texture(),
      night: new Texture(),
      water: new Texture(),
      height: new Texture(),
      clouds: new Texture(),
    };
    const earthMat = createEarthMaterial(earthTextures, baseLut, sunView);
    expect(earthMat).toBeInstanceOf(NodeMaterial);
    expect(earthMat.name).toBe('cislunarEarth');
    expect(earthMat.side).toBe(FrontSide);
    expect(earthMat.transparent).toBe(false);

    const directEarth = buildEarthNodeMaterial(earthTextures, baseLut, sunView, (p) => atan(p.z.negate(), p.x));
    expect(directEarth.name).toBe('cislunarEarth');

    const earthAst = evaluateTslFragmentSummary(earthMat);
    expect(sampleSpy).toHaveBeenCalledTimes(1);
    for (const tex of Object.values(earthTextures)) {
      expect(earthAst.textures.has(tex)).toBe(true);
    }
    for (const varName of ['earthP', 'earthUv', 'earthOpenWater', 'earthN', 'earthSunDot', 'earthLut']) {
      expect(earthAst.varNames.has(varName)).toBe(true);
    }
    expect(hasCloseNumber(earthAst.numbers, 65280)).toBe(true);
    expect(hasCloseNumber(earthAst.numbers, 255)).toBe(true);
    expect(hasCloseNumber(earthAst.numbers, 11_000)).toBe(true);
    expect(hasCloseNumber(earthAst.numbers, 72.0)).toBe(true);
    expect(hasCloseNumber(earthAst.numbers, 1.25 / 3)).toBe(true);

    const shellMat = createAtmosphereShellMaterial(baseLut, sunView);
    expect(shellMat).toBeInstanceOf(NodeMaterial);
    expect(shellMat.name).toBe('cislunarAtmosphereShell');
    expect(shellMat.side).toBe(BackSide);
    expect(shellMat.transparent).toBe(true);
    expect(shellMat.depthWrite).toBe(false);
    expect(shellMat.blending).toBe(AdditiveBlending);
    expect(buildAtmosphereShellNodeMaterial(baseLut, sunView).name).toBe('cislunarAtmosphereShell');

    const shellAst = evaluateTslFragmentSummary(shellMat);
    expect(sampleSpy).toHaveBeenCalledTimes(2);
    for (const varName of ['atmoN', 'atmoViewDot', 'atmoLimb', 'atmoSunAlign']) {
      expect(shellAst.varNames.has(varName)).toBe(true);
    }
    expect(hasCloseNumber(shellAst.numbers, 0.84)).toBe(true);
    expect(hasCloseNumber(shellAst.numbers, 0.86)).toBe(true);

    const moonTextures = {
      colour: new Texture(),
      normal: new Texture(),
    };
    const moonMat = createMoonMaterial(moonTextures, sunWorld);
    expect(moonMat).toBeInstanceOf(NodeMaterial);
    expect(moonMat.name).toBe('cislunarMoon');
    expect(moonMat.side).toBe(FrontSide);
    expect(moonMat.transparent).toBe(false);
    expect(buildMoonNodeMaterial(moonTextures, sunWorld, 0.12 / 0.483).name).toBe('cislunarMoon');

    const moonAst = evaluateTslFragmentSummary(moonMat);
    expect(moonAst.textures.has(moonTextures.colour)).toBe(true);
    expect(moonAst.textures.has(moonTextures.normal)).toBe(true);
    for (const varName of ['moonUv', 'moonUp', 'moonEast', 'moonN', 'moonL', 'moonMu0']) {
      expect(moonAst.varNames.has(varName)).toBe(true);
    }
    expect(hasCloseNumber(moonAst.numbers, 0.12 / 0.483)).toBe(true);
    expect(hasCloseNumber(moonAst.numbers, -Math.sin((2.7 * Math.PI) / 180))).toBe(true);
    expect(hasCloseNumber(moonAst.numbers, 0.001)).toBe(true);
  });
});
