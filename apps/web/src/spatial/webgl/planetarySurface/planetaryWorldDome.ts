import * as THREE from 'three';
import type { SplatMesh } from '@sparkjsdev/spark';
import type { EarthUniforms, MoonUniforms } from '../cislunarRegime.js';
import {
  PLANETARY_SURFACE_VERTEX_SHADER,
  buildPlanetarySurfaceFragmentShader,
} from './planetarySurfaceShader.js';
import { createPlanetaryTerrainSplatMesh } from './planetaryTerrainSplats.js';
import type { PlanetarySkyUniforms, PlanetaryTerrainPalette } from './planetaryTypes.js';

export const PLANETARY_WORLD_DOME_RADIUS = 85;
export const PLANETARY_WORLD_DOME_WIDTH_SEGMENTS = 128;
export const PLANETARY_WORLD_DOME_HEIGHT_SEGMENTS = 96;
export const INITIAL_PLANETARY_TERRAIN_PALETTE: PlanetaryTerrainPalette = 'exoplanet_terminator';

export function createPlanetaryWorldDomeMesh(
  planetarySkyUniforms: PlanetarySkyUniforms,
  earthUniforms: EarthUniforms,
): THREE.Mesh<THREE.SphereGeometry, THREE.ShaderMaterial> {
  const worldGeo = new THREE.SphereGeometry(
    PLANETARY_WORLD_DOME_RADIUS,
    PLANETARY_WORLD_DOME_WIDTH_SEGMENTS,
    PLANETARY_WORLD_DOME_HEIGHT_SEGMENTS,
  );
  const worldMat = new THREE.ShaderMaterial({
    side: THREE.BackSide,
    transparent: true,
    depthWrite: false,
    uniforms: {
      uTime: planetarySkyUniforms.uTime,
      uAtmoZenith: planetarySkyUniforms.uAtmoZenith,
      uAtmoHorizon: planetarySkyUniforms.uAtmoHorizon,
      uSunColor: planetarySkyUniforms.uSunColor,
      uPaletteId: planetarySkyUniforms.uPaletteId,
      uBrunetonLutTex: planetarySkyUniforms.uBrunetonLutTex,
      uEarthDayTex: earthUniforms.uDayTex,
      uEarthNightTex: earthUniforms.uNightTex,
      uEarthCloudsTex: earthUniforms.uCloudsTex,
      uEarthSpecTex: earthUniforms.uSpecTex,
    },
    vertexShader: PLANETARY_SURFACE_VERTEX_SHADER,
    fragmentShader: buildPlanetarySurfaceFragmentShader(),
  });
  return new THREE.Mesh(worldGeo, worldMat);
}

/**
 * Builds the modelled planetary_surface WebGL regime group (sky/terrain dome + Gaussian terrain splats).
 */
export function buildPlanetarySurfaceRegimeGroup(
  group: THREE.Group,
  planetarySkyUniforms: PlanetarySkyUniforms,
  earthUniforms: EarthUniforms,
  _moonUniforms?: MoonUniforms,
): { terrainSplatMesh: SplatMesh } {
  const worldDome = createPlanetaryWorldDomeMesh(planetarySkyUniforms, earthUniforms);
  group.add(worldDome);

  const terrainSplatMesh = createPlanetaryTerrainSplatMesh(
    INITIAL_PLANETARY_TERRAIN_PALETTE,
    planetarySkyUniforms,
  );
  group.add(terrainSplatMesh);

  return { terrainSplatMesh };
}
