import * as THREE from 'three';
import { PackedSplats, SplatMesh } from '@sparkjsdev/spark';
import { PLANETARY_PALETTE_PRESETS, applyPlanetaryPaletteToUniforms } from './planetaryPalettes.js';
import type { PlanetarySkyUniforms, PlanetarySplatSample, PlanetaryTerrainPalette } from './planetaryTypes.js';

export const PLANETARY_TERRAIN_SPLAT_COUNT = 1800;
export const PLANETARY_TERRAIN_SPLAT_OPACITY = 0.016;

export function samplePlanetaryTerrainSplat(index: number): PlanetarySplatSample {
  const seed1 = Math.sin(index * 12.9898) * 43758.5453;
  const seed2 = Math.sin(index * 78.233) * 43758.5453;
  const u = seed1 - Math.floor(seed1) - 0.5;
  const v = seed2 - Math.floor(seed2);
  const z = -12.0 - v * 26.0;
  const x = u * (24.0 + v * 34.0);
  const y = -0.8 + Math.sin(x * 0.25 + z * 0.2) * 0.8 + (index % 7) * 0.35;

  const s = 0.012 + (index % 4) * 0.003;
  const horizontalScale = s * 1.5;
  return {
    x,
    y,
    z,
    sx: horizontalScale,
    sy: s * 0.8,
    sz: horizontalScale,
  };
}

export function populatePlanetaryTerrainSplats(
  splats: Pick<PackedSplats, 'pushSplat'>,
  palette: PlanetaryTerrainPalette,
): void {
  const center = new THREE.Vector3();
  const scales = new THREE.Vector3();
  const quat = new THREE.Quaternion();
  const [r, g, b] = PLANETARY_PALETTE_PRESETS[palette].splatRgb;
  const color = new THREE.Color().setRGB(r, g, b);

  for (let i = 0; i < PLANETARY_TERRAIN_SPLAT_COUNT; i++) {
    const sample = samplePlanetaryTerrainSplat(i);
    center.set(sample.x, sample.y, sample.z);
    scales.set(sample.sx, sample.sy, sample.sz);
    splats.pushSplat(center, scales, quat, PLANETARY_TERRAIN_SPLAT_OPACITY, color);
  }
}

export function createPlanetaryTerrainSplatMesh(
  palette: PlanetaryTerrainPalette,
  planetarySkyUniforms: PlanetarySkyUniforms,
): SplatMesh {
  applyPlanetaryPaletteToUniforms(palette, planetarySkyUniforms);

  return new SplatMesh({
    constructSplats: (splats: PackedSplats) => {
      populatePlanetaryTerrainSplats(splats, palette);
    },
  });
}
