// @vitest-environment node
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import type { PackedSplats } from '@sparkjsdev/spark';
import * as THREE from 'three';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { createEarthUniforms, createMoonUniforms } from '../cislunarRegime.js';
import * as planetaryRegimeModule from '../planetarySurfaceRegime.js';
import {
  buildPlanetarySurfaceRegimeGroup,
  createPlanetarySkyUniforms,
  createPlanetaryTerrainSplatMesh,
  type PlanetaryTerrainPalette,
} from '../planetarySurfaceRegime.js';
import { PLANETARY_CELESTIAL_SKY_GLSL } from '../planetarySurface/planetaryCelestialSkyGlsl.js';
import { PLANETARY_NOISE_AND_HEIGHT_GLSL } from '../planetarySurface/planetaryNoiseAndHeightGlsl.js';
import {
  PLANETARY_PALETTE_PRESETS,
  applyPlanetaryPaletteToUniforms,
} from '../planetarySurface/planetaryPalettes.js';
import {
  PLANETARY_SURFACE_VERTEX_SHADER,
  buildPlanetarySurfaceFragmentShader,
} from '../planetarySurface/planetarySurfaceShader.js';
import {
  PLANETARY_TERRAIN_SPLAT_COUNT,
  PLANETARY_TERRAIN_SPLAT_OPACITY,
  populatePlanetaryTerrainSplats,
  samplePlanetaryTerrainSplat,
} from '../planetarySurface/planetaryTerrainSplats.js';
import {
  INITIAL_PLANETARY_TERRAIN_PALETTE,
  PLANETARY_WORLD_DOME_HEIGHT_SEGMENTS,
  PLANETARY_WORLD_DOME_RADIUS,
  PLANETARY_WORLD_DOME_WIDTH_SEGMENTS,
  createPlanetaryWorldDomeMesh,
} from '../planetarySurface/planetaryWorldDome.js';

function expectedSplatGeometry(index: number) {
  const seed1 = Math.sin(index * 12.9898) * 43758.5453;
  const seed2 = Math.sin(index * 78.233) * 43758.5453;
  const u = seed1 - Math.floor(seed1) - 0.5;
  const v = seed2 - Math.floor(seed2);
  const z = -12.0 - v * 26.0;
  const x = u * (24.0 + v * 34.0);
  const y = -0.8 + Math.sin(x * 0.25 + z * 0.2) * 0.8 + (index % 7) * 0.35;
  const s = 0.012 + (index % 4) * 0.003;
  return { x, y, z, sx: s * 1.5, sy: s * 0.8, sz: s * 1.5 };
}

const PALETTE_TABLE: ReadonlyArray<{
  palette: PlanetaryTerrainPalette;
  zenith: [number, number, number];
  horizon: [number, number, number];
  sunColor: [number, number, number];
  sunRadius: number;
  hasRings: number;
  paletteId: number;
  splatColor: [number, number, number];
}> = [
  {
    palette: 'lunar_regolith',
    zenith: [0.001, 0.002, 0.006],
    horizon: [0.05, 0.11, 0.22],
    sunColor: [0.95, 0.98, 1.05],
    sunRadius: 0.085,
    hasRings: 0.0,
    paletteId: 0.0,
    splatColor: [0.65, 0.78, 0.96],
  },
  {
    palette: 'exoplanet_terminator',
    zenith: [0.008, 0.018, 0.052],
    horizon: [0.48, 0.18, 0.10],
    sunColor: [1.18, 0.58, 0.36],
    sunRadius: 0.105,
    hasRings: 0.0,
    paletteId: 1.0,
    splatColor: [0.92, 0.56, 0.42],
  },
  {
    palette: 'titan_methane',
    zenith: [0.05, 0.03, 0.012],
    horizon: [0.84, 0.48, 0.16],
    sunColor: [1.12, 0.80, 0.42],
    sunRadius: 0.11,
    hasRings: 1.0,
    paletteId: 2.0,
    splatColor: [0.95, 0.62, 0.24],
  },
];

describe('planetarySurfaceRegime public surface and sky uniforms', () => {
  it('exports the exact three runtime functions from planetarySurfaceRegime.ts', () => {
    expect(Object.keys(planetaryRegimeModule).sort()).toEqual([
      'buildPlanetarySurfaceRegimeGroup',
      'createPlanetarySkyUniforms',
      'createPlanetaryTerrainSplatMesh',
    ]);
  });

  it('creates independent PlanetarySkyUniforms instances with expected initial values', () => {
    const a = createPlanetarySkyUniforms();
    const b = createPlanetarySkyUniforms();

    expect(a.uTime.value).toBe(0.0);
    expect(a.uAtmoZenith.value.toArray()).toEqual([0.01, 0.03, 0.08]);
    expect(a.uAtmoHorizon.value.toArray()).toEqual([0.78, 0.28, 0.12]);
    expect(a.uSunColor.value.toArray()).toEqual([1.25, 0.52, 0.22]);
    expect(a.uSunAngularRadius.value).toBe(0.095);
    expect(a.uHasRings.value).toBe(0.0);
    expect(a.uPaletteId.value).toBe(1.0);
    expect(a.uBrunetonLutTex.value).toBeNull();

    a.uAtmoZenith.value.set(9, 9, 9);
    a.uAtmoHorizon.value.set(8, 8, 8);
    a.uSunColor.value.set(7, 7, 7);
    expect(b.uAtmoZenith.value.toArray()).toEqual([0.01, 0.03, 0.08]);
    expect(b.uAtmoHorizon.value.toArray()).toEqual([0.78, 0.28, 0.12]);
    expect(b.uSunColor.value.toArray()).toEqual([1.25, 0.52, 0.22]);
  });
});

describe('planetaryPalettes single-source-of-truth presets', () => {
  it('defines all three palettes with exact uniform values and splat RGB triplets', () => {
    expect(Object.keys(PLANETARY_PALETTE_PRESETS).sort()).toEqual([
      'exoplanet_terminator',
      'lunar_regolith',
      'titan_methane',
    ]);
    for (const row of PALETTE_TABLE) {
      const preset = PLANETARY_PALETTE_PRESETS[row.palette];
      expect(preset).toEqual({
        paletteId: row.paletteId,
        atmoZenith: row.zenith,
        atmoHorizon: row.horizon,
        sunColor: row.sunColor,
        sunAngularRadius: row.sunRadius,
        hasRings: row.hasRings,
        splatRgb: row.splatColor,
      });

      const uniforms = createPlanetarySkyUniforms();
      const returned = applyPlanetaryPaletteToUniforms(row.palette, uniforms);
      expect(returned).toBe(preset);
      expect(uniforms.uAtmoZenith.value.toArray()).toEqual(row.zenith);
      expect(uniforms.uAtmoHorizon.value.toArray()).toEqual(row.horizon);
      expect(uniforms.uSunColor.value.toArray()).toEqual(row.sunColor);
      expect(uniforms.uSunAngularRadius.value).toBe(row.sunRadius);
      expect(uniforms.uHasRings.value).toBe(row.hasRings);
      expect(uniforms.uPaletteId.value).toBe(row.paletteId);
    }
  });
});

describe('planetaryTerrainSplats deterministic geometry and packing', () => {
  it('computes exact deterministic splat sample bounds and modulo cycles', () => {
    expect(PLANETARY_TERRAIN_SPLAT_COUNT).toBe(1800);
    expect(PLANETARY_TERRAIN_SPLAT_OPACITY).toBe(0.016);

    for (const index of [0, 1, 3, 4, 6, 7, 42, 1799]) {
      const sample = samplePlanetaryTerrainSplat(index);
      const expected = expectedSplatGeometry(index);
      expect(sample.x).toBeCloseTo(expected.x, 12);
      expect(sample.y).toBeCloseTo(expected.y, 12);
      expect(sample.z).toBeCloseTo(expected.z, 12);
      expect(sample.sx).toBeCloseTo(expected.sx, 12);
      expect(sample.sy).toBeCloseTo(expected.sy, 12);
      expect(sample.sz).toBeCloseTo(expected.sz, 12);
    }
  });

  it('pushes 1800 splats with identity rotation, opacity 0.016 and palette RGB', () => {
    for (const row of PALETTE_TABLE) {
      const recorded: Array<{
        center: [number, number, number];
        scales: [number, number, number];
        quat: [number, number, number, number];
        opacity: number;
        rgb: [number, number, number];
      }> = [];
      populatePlanetaryTerrainSplats(
        {
          pushSplat(center, scales, quat, opacity, color) {
            recorded.push({
              center: [center.x, center.y, center.z],
              scales: [scales.x, scales.y, scales.z],
              quat: [quat.x, quat.y, quat.z, quat.w],
              opacity,
              rgb: [color.r, color.g, color.b],
            });
          },
        },
        row.palette,
      );
      expect(recorded).toHaveLength(1800);
      for (const idx of [0, 1, 7, 511, 1799]) {
        const entry = recorded[idx]!;
        const expected = expectedSplatGeometry(idx);
        expect(entry.center[0]).toBeCloseTo(expected.x, 12);
        expect(entry.center[1]).toBeCloseTo(expected.y, 12);
        expect(entry.center[2]).toBeCloseTo(expected.z, 12);
        expect(entry.scales[0]).toBeCloseTo(expected.sx, 12);
        expect(entry.scales[1]).toBeCloseTo(expected.sy, 12);
        expect(entry.scales[2]).toBeCloseTo(expected.sz, 12);
        expect(entry.quat).toEqual([0, 0, 0, 1]);
        expect(entry.opacity).toBe(0.016);
        expect(entry.rgb[0]).toBeCloseTo(row.splatColor[0], 12);
        expect(entry.rgb[1]).toBeCloseTo(row.splatColor[1], 12);
        expect(entry.rgb[2]).toBeCloseTo(row.splatColor[2], 12);
      }
    }
  });

  for (const row of PALETTE_TABLE) {
    it(`configures uniforms and packs 1800 splats into SplatMesh for ${row.palette}`, () => {
      const sky = createPlanetarySkyUniforms();
      const zenithRef = sky.uAtmoZenith.value;
      const horizonRef = sky.uAtmoHorizon.value;
      const sunRef = sky.uSunColor.value;

      const mesh = createPlanetaryTerrainSplatMesh(row.palette, sky);
      try {
        expect(sky.uAtmoZenith.value).toBe(zenithRef);
        expect(sky.uAtmoHorizon.value).toBe(horizonRef);
        expect(sky.uSunColor.value).toBe(sunRef);
        expect(sky.uAtmoZenith.value.toArray()).toEqual(row.zenith);
        expect(sky.uAtmoHorizon.value.toArray()).toEqual(row.horizon);
        expect(sky.uSunColor.value.toArray()).toEqual(row.sunColor);
        expect(sky.uSunAngularRadius.value).toBe(row.sunRadius);
        expect(sky.uHasRings.value).toBe(row.hasRings);
        expect(sky.uPaletteId.value).toBe(row.paletteId);

        const packed = (mesh as unknown as { packedSplats: PackedSplats }).packedSplats;
        expect(packed.numSplats).toBe(1800);

        for (const sampleIndex of [0, 1, 6, 7, 1799]) {
          const splat = packed.getSplat(sampleIndex);
          const expected = expectedSplatGeometry(sampleIndex);
          expect(Math.abs(splat.center.x - expected.x)).toBeLessThan(0.05);
          expect(Math.abs(splat.center.y - expected.y)).toBeLessThan(0.05);
          expect(Math.abs(splat.center.z - expected.z)).toBeLessThan(0.05);
          expect(Math.abs(splat.scales.x - expected.sx)).toBeLessThan(0.002);
          expect(Math.abs(splat.scales.y - expected.sy)).toBeLessThan(0.002);
          expect(Math.abs(splat.scales.z - expected.sz)).toBeLessThan(0.002);
          expect(Math.abs(splat.opacity - 0.016)).toBeLessThan(0.008);
          expect(Math.abs(splat.color.r - row.splatColor[0])).toBeLessThan(0.02);
          expect(Math.abs(splat.color.g - row.splatColor[1])).toBeLessThan(0.02);
          expect(Math.abs(splat.color.b - row.splatColor[2])).toBeLessThan(0.02);
        }
      } finally {
        mesh.dispose();
      }
    });
  }

  it('PBT: samplePlanetaryTerrainSplat stays within geometric and scale bounds for every index', () => {
    const seedEnv = Number(process.env.ALBEDIAN_FC_SEED);
    const seed = Number.isInteger(seedEnv) && seedEnv > 0 ? seedEnv : 20261117;
    fc.assert(
      fc.property(fc.integer({ min: 0, max: 1799 }), (index) => {
        const sample = samplePlanetaryTerrainSplat(index);
        expect(sample.z).toBeLessThanOrEqual(-12.0);
        expect(sample.z).toBeGreaterThanOrEqual(-38.0);
        expect(Math.abs(sample.x)).toBeLessThanOrEqual(29.0);
        expect(sample.y).toBeGreaterThanOrEqual(-1.600001);
        expect(sample.y).toBeLessThanOrEqual(2.100001);
        expect(sample.sx).toBeGreaterThanOrEqual(0.018 - 1e-12);
        expect(sample.sx).toBeLessThanOrEqual(0.0315 + 1e-12);
        expect(sample.sy).toBeGreaterThanOrEqual(0.0096 - 1e-12);
        expect(sample.sy).toBeLessThanOrEqual(0.0168 + 1e-12);
        expect(sample.sz).toBe(sample.sx);
      }),
      {
        seed,
        numRuns: 60,
        examples: [[0], [1], [3], [6], [7], [1799]],
      },
    );
  });
});

describe('planetarySurfaceShader and planetaryWorldDome modules', () => {
  it('assembles GLSL chunks without dead uMoonAlbedoTex or dead out vec2 grad', () => {
    expect(PLANETARY_SURFACE_VERTEX_SHADER).toContain('varying vec3 vWorldDir;');
    expect(PLANETARY_SURFACE_VERTEX_SHADER).toContain('vWorldDir = normalize(position);');

    expect(PLANETARY_NOISE_AND_HEIGHT_GLSL).toContain('float hash2(vec2 p)');
    expect(PLANETARY_NOISE_AND_HEIGHT_GLSL).toContain('vec3 noised(vec2 x)');
    expect(PLANETARY_NOISE_AND_HEIGHT_GLSL).toContain('float terrainHeight(vec2 p)');
    expect(PLANETARY_NOISE_AND_HEIGHT_GLSL).not.toContain('out vec2 grad');
    expect(PLANETARY_NOISE_AND_HEIGHT_GLSL).toContain('for (int i = 0; i < 8; i++)');
    expect(PLANETARY_NOISE_AND_HEIGHT_GLSL).toContain('if (uPaletteId < 0.5)');

    expect(PLANETARY_CELESTIAL_SKY_GLSL).toContain('vec3 evaluateCelestialSky(vec3 rd, bool isReflection)');
    expect(PLANETARY_CELESTIAL_SKY_GLSL).toContain('sampleBrunetonAtmoLut');
    expect(PLANETARY_CELESTIAL_SKY_GLSL).toContain('if (uPaletteId < 0.5)');
    expect(PLANETARY_CELESTIAL_SKY_GLSL).toContain('if (uPaletteId < 1.5)');
    expect(PLANETARY_CELESTIAL_SKY_GLSL).toContain('if (uPaletteId < 2.5)');
    expect(PLANETARY_CELESTIAL_SKY_GLSL.match(/if \(isReflection\)/g)).toHaveLength(2);

    const frag = buildPlanetarySurfaceFragmentShader();
    expect(frag).toContain(PLANETARY_NOISE_AND_HEIGHT_GLSL.trim());
    expect(frag).toContain(PLANETARY_CELESTIAL_SKY_GLSL.trim());
    expect(frag).toContain('for (int i = 0; i < 96; i++)');
    expect(frag).toContain('if (rd.y > 0.22)');
    expect(frag).toContain('if (!hit && !hitWater)');
    expect(frag).toContain('if (hit)');
    expect(frag).toContain('if (hitWater)');
    expect(frag).not.toContain('uMoonAlbedoTex');
    expect(frag).not.toContain('hitGrad');
    expect(frag).not.toContain('wGrad');
  });

  it('builds the backside sphere dome and exoplanet_terminator terrain splats with exact 10 live uniforms', () => {
    expect(PLANETARY_WORLD_DOME_RADIUS).toBe(85);
    expect(PLANETARY_WORLD_DOME_WIDTH_SEGMENTS).toBe(128);
    expect(PLANETARY_WORLD_DOME_HEIGHT_SEGMENTS).toBe(96);
    expect(INITIAL_PLANETARY_TERRAIN_PALETTE).toBe('exoplanet_terminator');

    const group = new THREE.Group();
    const sky = createPlanetarySkyUniforms();
    const earth = createEarthUniforms();
    const moon = createMoonUniforms();

    const standaloneDome = createPlanetaryWorldDomeMesh(sky, earth);
    expect(Object.keys(standaloneDome.material.uniforms).sort()).toEqual([
      'uAtmoHorizon',
      'uAtmoZenith',
      'uBrunetonLutTex',
      'uEarthCloudsTex',
      'uEarthDayTex',
      'uEarthNightTex',
      'uEarthSpecTex',
      'uPaletteId',
      'uSunColor',
      'uTime',
    ]);

    const { terrainSplatMesh } = buildPlanetarySurfaceRegimeGroup(group, sky, earth, moon);
    try {
      expect(group.children).toHaveLength(2);
      const worldDome = group.children[0] as THREE.Mesh<THREE.SphereGeometry, THREE.ShaderMaterial>;
      expect(worldDome).toBeInstanceOf(THREE.Mesh);
      expect(group.children[1]).toBe(terrainSplatMesh);

      expect(worldDome.geometry.parameters.radius).toBe(85);
      expect(worldDome.geometry.parameters.widthSegments).toBe(128);
      expect(worldDome.geometry.parameters.heightSegments).toBe(96);

      const mat = worldDome.material;
      expect(mat.side).toBe(THREE.BackSide);
      expect(mat.transparent).toBe(true);
      expect(mat.depthWrite).toBe(false);
      expect('uMoonAlbedoTex' in mat.uniforms).toBe(false);

      expect(mat.uniforms.uTime).toBe(sky.uTime);
      expect(mat.uniforms.uAtmoZenith).toBe(sky.uAtmoZenith);
      expect(mat.uniforms.uAtmoHorizon).toBe(sky.uAtmoHorizon);
      expect(mat.uniforms.uSunColor).toBe(sky.uSunColor);
      expect(mat.uniforms.uPaletteId).toBe(sky.uPaletteId);
      expect(mat.uniforms.uBrunetonLutTex).toBe(sky.uBrunetonLutTex);
      expect(mat.uniforms.uEarthDayTex).toBe(earth.uDayTex);
      expect(mat.uniforms.uEarthNightTex).toBe(earth.uNightTex);
      expect(mat.uniforms.uEarthCloudsTex).toBe(earth.uCloudsTex);
      expect(mat.uniforms.uEarthSpecTex).toBe(earth.uSpecTex);

      expect(sky.uPaletteId.value).toBe(1.0);
      expect(sky.uHasRings.value).toBe(0.0);
      expect(sky.uSunAngularRadius.value).toBe(0.105);
    } finally {
      terrainSplatMesh.dispose();
    }
  });

  it('preserves source-inspection guard invariants across facade and all planetarySurface submodules', () => {
    const files = [
      '../planetarySurfaceRegime.ts',
      '../planetarySurface/planetaryTypes.ts',
      '../planetarySurface/planetaryPalettes.ts',
      '../planetarySurface/planetaryTerrainSplats.ts',
      '../planetarySurface/planetaryNoiseAndHeightGlsl.ts',
      '../planetarySurface/planetaryCelestialSkyGlsl.ts',
      '../planetarySurface/planetarySurfaceShader.ts',
      '../planetarySurface/planetaryWorldDome.ts',
    ];
    for (const rel of files) {
      const text = fs.readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');
      expect(text, rel).not.toMatch(/parentBodyMesh|telemetry/);
      expect(text, rel).not.toMatch(/createDecorativeParticles|DecorativeParticles|uCoronaInner|uCoronaOuter/);
      expect(text, rel).not.toMatch(
        /derivePlanetaryWorldDescriptor|PlanetaryWorldDescriptor|descendToPlanetarySurface|gargantua_tidal_ocean|supergiant_silicate|Miller's Planet|Miller Analog|Kerr Metric|Time Dilation 1\.7/,
      );
      expect(text, rel).not.toMatch(
        /\b(?:DNGR|MRI|GPGPU|MHD|einsteinAmplification)\b|von Tunzelmann|Kip Thorne|ISCO \(2\.65|Magneto-rotational|Blackbody thermal|Einstein ring starlight|Magnetohydrodynamic/,
      );
    }
  });

  it('PBT: arbitrary palette transitions keep shared Vector3 instances and deterministic paletteId', () => {
    const seedEnv = Number(process.env.ALBEDIAN_FC_SEED);
    const seed = Number.isInteger(seedEnv) && seedEnv > 0 ? seedEnv : 20261117;
    const paletteArb = fc.constantFrom<PlanetaryTerrainPalette>(
      'lunar_regolith',
      'exoplanet_terminator',
      'titan_methane',
    );
    fc.assert(
      fc.property(fc.array(paletteArb, { minLength: 1, maxLength: 6 }), (sequence) => {
        const sky = createPlanetarySkyUniforms();
        const z0 = sky.uAtmoZenith.value;
        const h0 = sky.uAtmoHorizon.value;
        const s0 = sky.uSunColor.value;
        for (const p of sequence) {
          const mesh = createPlanetaryTerrainSplatMesh(p, sky);
          mesh.dispose();
        }
        const last = sequence[sequence.length - 1]!;
        const expectedId = last === 'lunar_regolith' ? 0.0 : last === 'exoplanet_terminator' ? 1.0 : 2.0;
        expect(sky.uPaletteId.value).toBe(expectedId);
        expect(sky.uAtmoZenith.value).toBe(z0);
        expect(sky.uAtmoHorizon.value).toBe(h0);
        expect(sky.uSunColor.value).toBe(s0);
      }),
      {
        seed,
        numRuns: 25,
        examples: [
          [['lunar_regolith']],
          [['titan_methane', 'exoplanet_terminator', 'lunar_regolith']],
        ],
      },
    );
  });
});
