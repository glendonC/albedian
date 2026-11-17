import * as THREE from 'three';

export type PlanetaryTerrainPalette = 'lunar_regolith' | 'exoplanet_terminator' | 'titan_methane';

export type RgbTriplet = readonly [number, number, number];

export interface PlanetarySkyUniforms {
  [uniform: string]: THREE.IUniform<unknown>;
  uTime: { value: number };
  uAtmoZenith: { value: THREE.Vector3 };
  uAtmoHorizon: { value: THREE.Vector3 };
  uSunColor: { value: THREE.Vector3 };
  uSunAngularRadius: { value: number };
  uHasRings: { value: number };
  uPaletteId: { value: number };
  uBrunetonLutTex: { value: THREE.Texture | null };
}

export interface PlanetaryPalettePreset {
  readonly paletteId: number;
  readonly atmoZenith: RgbTriplet;
  readonly atmoHorizon: RgbTriplet;
  readonly sunColor: RgbTriplet;
  readonly sunAngularRadius: number;
  readonly hasRings: number;
  readonly splatRgb: RgbTriplet;
}

export interface PlanetarySplatSample {
  readonly x: number;
  readonly y: number;
  readonly z: number;
  readonly sx: number;
  readonly sy: number;
  readonly sz: number;
}
