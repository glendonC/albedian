import * as THREE from 'three';
import type { PlanetaryPalettePreset, PlanetarySkyUniforms, PlanetaryTerrainPalette } from './planetaryTypes.js';

export const PLANETARY_PALETTE_PRESETS: Readonly<Record<PlanetaryTerrainPalette, PlanetaryPalettePreset>> = {
  lunar_regolith: {
    paletteId: 0.0,
    atmoZenith: [0.001, 0.002, 0.006],
    atmoHorizon: [0.05, 0.11, 0.22],
    sunColor: [0.95, 0.98, 1.05],
    sunAngularRadius: 0.085,
    hasRings: 0.0,
    splatRgb: [0.65, 0.78, 0.96],
  },
  exoplanet_terminator: {
    paletteId: 1.0,
    atmoZenith: [0.008, 0.018, 0.052],
    atmoHorizon: [0.48, 0.18, 0.10],
    sunColor: [1.18, 0.58, 0.36],
    sunAngularRadius: 0.105,
    hasRings: 0.0,
    splatRgb: [0.92, 0.56, 0.42],
  },
  titan_methane: {
    paletteId: 2.0,
    atmoZenith: [0.05, 0.03, 0.012],
    atmoHorizon: [0.84, 0.48, 0.16],
    sunColor: [1.12, 0.80, 0.42],
    sunAngularRadius: 0.11,
    hasRings: 1.0,
    splatRgb: [0.95, 0.62, 0.24],
  },
};

export function createPlanetarySkyUniforms(): PlanetarySkyUniforms {
  return {
    uTime: { value: 0.0 },
    uAtmoZenith: { value: new THREE.Vector3(0.01, 0.03, 0.08) },
    uAtmoHorizon: { value: new THREE.Vector3(0.78, 0.28, 0.12) },
    uSunColor: { value: new THREE.Vector3(1.25, 0.52, 0.22) },
    uSunAngularRadius: { value: 0.095 },
    uHasRings: { value: 0.0 },
    uPaletteId: { value: 1.0 },
    uBrunetonLutTex: { value: null },
  };
}

export function applyPlanetaryPaletteToUniforms(
  palette: PlanetaryTerrainPalette,
  planetarySkyUniforms: PlanetarySkyUniforms,
): PlanetaryPalettePreset {
  const preset = PLANETARY_PALETTE_PRESETS[palette];
  planetarySkyUniforms.uAtmoZenith.value.set(...preset.atmoZenith);
  planetarySkyUniforms.uAtmoHorizon.value.set(...preset.atmoHorizon);
  planetarySkyUniforms.uSunColor.value.set(...preset.sunColor);
  planetarySkyUniforms.uSunAngularRadius.value = preset.sunAngularRadius;
  planetarySkyUniforms.uHasRings.value = preset.hasRings;
  planetarySkyUniforms.uPaletteId.value = preset.paletteId;
  return preset;
}
