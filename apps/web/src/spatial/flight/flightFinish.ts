import * as THREE from 'three';
import { usableDistancePc, type Vec3 } from '@albedian/camera-rig';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';
import { WEBGL_EXPOSURE } from '../frameFinishing.js';
import { distanceKindOf, type CatalogStarCoreTile } from '../catalogStars/tileCodec.js';

/**
 * flight-stars-finish-one (DECIDED-BY-LEAD 24 Nov 16:08): the flight's stars go through the predicted sky's own bloom
 * and tone mapping, so one sky looks like one sky. The stars are drawn in linear light into a half-float buffer, then
 * bloomed and tone mapped onto the canvas by the predicted sky's composer steps (SparkPortalRenderer.ts: its bloom's
 * numbers, then OutputPass with ACES at WEBGL_EXPOSURE); the cinema optics are not part of it. Whatever is drawn after
 * `end` (the picked star and its own bloom, arrival-star-1's) is drawn in display light, as before.
 */

/** The predicted sky's bloom (SparkPortalRenderer.ts, UnrealBloomPass): strength, radius, threshold. */
export const FLIGHT_BLOOM = { strength: 0.64, radius: 0.72, threshold: 0.6 } as const;

/** The finish one flight's star canvas goes through, on that canvas's own WebGL 2 context. */
export interface FlightFinish {
  /** Binds the half-float buffer the stars are drawn into next, cleared to black. */
  begin(): void;
  /** Blooms and tone maps that buffer onto the canvas, and leaves the context at its defaults for the draws after it. */
  end(): void;
  setSize(widthPx: number, heightPx: number, pixelRatio: number): void;
  dispose(): void;
}

/**
 * The row of a tile that holds the star flown to, as arrival-star-1 places it (arrival.ts: the row's direction at its
 * usable distance), so that row alone is left out of the bloomed pass and drawn after it; none if the tile lacks it.
 */
export function pickedRowOf(tile: CatalogStarCoreTile, positionPc: Vec3): number | null {
  const scale = Math.hypot(positionPc[0], positionPc[1], positionPc[2]);
  if (!(scale > 0)) return null;
  for (let i = 0; i < tile.count; i++) {
    const d = usableDistancePc({ medianPc: tile.distancePc[i] ?? Number.NaN, kind: distanceKindOf(tile.flags[i] ?? 0) });
    if (d === null) continue;
    const x = tile.directions[3 * i] ?? 0;
    const y = tile.directions[3 * i + 1] ?? 0;
    const z = tile.directions[3 * i + 2] ?? 0;
    const len = Math.hypot(x, y, z) || 1;
    if (Math.hypot((x / len) * d - positionPc[0], (y / len) * d - positionPc[1], (z / len) * d - positionPc[2]) <= 1e-9 * scale) return i;
  }
  return null;
}

/** Makes the finish on the context the stars draw with; null where three cannot take that context. */
export function createFlightFinish(canvas: HTMLCanvasElement, gl: WebGL2RenderingContext, widthPx: number, heightPx: number, pixelRatio: number): FlightFinish | null {
  // Only a real WebGL 2 context: a stand-in (a test's) leaves the stars unfinished, as before
  if (typeof WebGL2RenderingContext === 'undefined' || !(gl instanceof WebGL2RenderingContext)) return null;
  let renderer: THREE.WebGLRenderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, context: gl, antialias: false, alpha: false });
  } catch {
    return null;
  }
  renderer.autoClear = false;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = WEBGL_EXPOSURE;
  renderer.setPixelRatio(pixelRatio);
  renderer.setSize(widthPx, heightPx, false);
  // The composer's own buffers are half-float, so a star's core above 1 reaches the bloom and the tone curve unclipped
  const composer = new EffectComposer(renderer);
  composer.setPixelRatio(pixelRatio);
  composer.setSize(widthPx, heightPx);
  const bloom = new UnrealBloomPass(new THREE.Vector2(widthPx, heightPx), FLIGHT_BLOOM.strength, FLIGHT_BLOOM.radius, FLIGHT_BLOOM.threshold);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());
  return {
    begin() {
      renderer.setRenderTarget(composer.readBuffer);
      renderer.setClearColor(0x000000, 1);
      renderer.clear(true, false, false);
    },
    end() {
      // The stars were drawn on the context directly: three's record of its state is stale until reset
      renderer.resetState();
      composer.render();
      renderer.resetState();
    },
    setSize(w, h, ratio) {
      renderer.setPixelRatio(ratio);
      renderer.setSize(w, h, false);
      composer.setPixelRatio(ratio);
      composer.setSize(w, h);
    },
    dispose() {
      bloom.dispose();
      composer.dispose();
      renderer.dispose();
    },
  };
}
