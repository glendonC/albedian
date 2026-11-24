// @vitest-environment jsdom
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';
import { WEBGL_EXPOSURE } from '../../frameFinishing.js';
import { raDecToUnitVector } from '@albedian/camera-rig';
import type { CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';
import { arrivalStarOfTile } from '../arrival.js';

// flight-stars-finish-one (16:08): the flight's stars through the predicted sky's own bloom and tone mapping. Three's
// renderer is stood in for (no WebGL here); its composer, bloom pass and output pass are three's own, built and run.

const log: string[] = [];
const made: Stub[] = [];
let refuse = false;

class Stub {
  autoClear = true;
  toneMapping: THREE.ToneMapping = THREE.NoToneMapping;
  toneMappingExposure = 1;
  outputColorSpace: THREE.ColorSpace = THREE.SRGBColorSpace;
  params: unknown;
  private target: THREE.WebGLRenderTarget | null = null;
  private w = 1;
  private h = 1;
  private ratio = 1;
  constructor(params: unknown) {
    this.params = params;
    made.push(this);
  }
  setPixelRatio(r: number) { this.ratio = r; }
  getPixelRatio() { return this.ratio; }
  setSize(w: number, h: number) { this.w = w; this.h = h; }
  getSize(v: THREE.Vector2) { return v.set(this.w, this.h); }
  setRenderTarget(t: THREE.WebGLRenderTarget | null) { this.target = t; log.push(t ? 'target' : 'screen'); }
  getRenderTarget() { return this.target; }
  setClearColor() {}
  getClearColor(c: THREE.Color) { return c.set(0, 0, 0); }
  getClearAlpha() { return 1; }
  clear() { log.push('clear'); }
  resetState() { log.push('reset'); }
  render(scene: THREE.Object3D) {
    const material = (scene as THREE.Mesh).material as THREE.ShaderMaterial | undefined;
    log.push(`draw:${material?.name || material?.type || 'scene'}`);
  }
  dispose() { log.push('dispose'); }
}

vi.mock('three', async (importOriginal) => {
  const three = await importOriginal<typeof import('three')>();
  return { ...three, WebGLRenderer: function (params: unknown) {
    if (refuse) throw new Error('no context');
    return new Stub(params);
  } };
});

// A WebGL 2 context class, so the finish takes its stand-in as a real context
class FakeGl2 {}
vi.stubGlobal('WebGL2RenderingContext', FakeGl2);
const webgl2 = () => new FakeGl2() as unknown as WebGL2RenderingContext;

const { FLIGHT_BLOOM, createFlightFinish, pickedRowOf } = await import('../flightFinish.js');
const here = dirname(fileURLToPath(import.meta.url));

describe('the flight finish is the predicted sky\'s', () => {
  it('blooms with the very numbers the predicted sky blooms with', () => {
    const portal = readFileSync(resolve(here, '../../SparkPortalRenderer.ts'), 'utf8');
    const m = /new UnrealBloomPass\(new THREE\.Vector2\(width, height\), ([\d.]+), ([\d.]+), ([\d.]+)\)/.exec(portal);
    expect(m).not.toBeNull();
    expect([FLIGHT_BLOOM.strength, FLIGHT_BLOOM.radius, FLIGHT_BLOOM.threshold]).toEqual([Number(m![1]), Number(m![2]), Number(m![3])]);
  });

  it('tone maps with ACES at the WebGL exposure, on the stars\' own context, half-float buffers, bloom then output', () => {
    made.length = 0;
    const canvas = document.createElement('canvas');
    const gl = webgl2();
    const finish = createFlightFinish(canvas, gl, 1440, 900, 2)!;
    expect(finish).not.toBeNull();
    const r = made[0]!;
    expect(r.params).toMatchObject({ canvas, context: gl });
    expect(r.toneMapping).toBe(THREE.ACESFilmicToneMapping);
    expect(r.toneMappingExposure).toBe(WEBGL_EXPOSURE);
    expect(r.autoClear).toBe(false);
    // begin binds the composer's buffer (half-float) and clears it
    log.length = 0;
    finish.begin();
    expect(log).toEqual(['target', 'clear']);
    const buffer = r.getRenderTarget()!;
    expect(buffer.texture.type).toBe(THREE.HalfFloatType);
    expect([buffer.width, buffer.height]).toEqual([2880, 1800]);
    // end: three's state reset (the stars drew on the context directly), bloom then output to the screen, reset again
    log.length = 0;
    finish.end();
    expect(log[0]).toBe('reset');
    expect(log.at(-1)).toBe('reset');
    expect(log.filter((e) => e.startsWith('draw')).length).toBeGreaterThan(5);
    expect(log.lastIndexOf('screen')).toBeGreaterThan(log.indexOf('draw:ShaderMaterial'));
  });

  it('builds the bloom pass with those numbers and ends with three\'s output pass', async () => {
    made.length = 0;
    const addPass = vi.spyOn((await import('three/examples/jsm/postprocessing/EffectComposer.js')).EffectComposer.prototype, 'addPass');
    createFlightFinish(document.createElement('canvas'), webgl2(), 390, 844, 2);
    const passes = addPass.mock.calls.map(([p]) => p);
    expect(passes).toHaveLength(2);
    const bloom = passes[0] as UnrealBloomPass;
    expect(bloom).toBeInstanceOf(UnrealBloomPass);
    expect([bloom.strength, bloom.radius, bloom.threshold]).toEqual([0.64, 0.72, 0.6]);
    expect(passes[1]).toBeInstanceOf(OutputPass);
    addPass.mockRestore();
  });

  it('is none where three cannot take the context', () => {
    refuse = true;
    try {
      expect(createFlightFinish(document.createElement('canvas'), webgl2(), 10, 10, 1)).toBeNull();
    } finally {
      refuse = false;
    }
  });
});

describe('the finish only on a real WebGL 2 context', () => {
  it('leaves a stand-in context unfinished, never asking three for it', () => {
    made.length = 0;
    expect(createFlightFinish(document.createElement('canvas'), {} as WebGL2RenderingContext, 10, 10, 1)).toBeNull();
    expect(made).toHaveLength(0);
  });
});

describe('the star flown to, found in its tile row as arrival-star-1 places it', () => {
  /** Three stars written by hand (typed test data, not read from any catalog release). */
  const tile: CatalogStarCoreTile = {
    layer: 0, order: 0, pix: 1, count: 3, positionBits: 32,
    directions: new Float32Array([...raDecToUnitVector(80, 5), ...raDecToUnitVector(83, 4), ...raDecToUnitVector(83, 4)]),
    magnitude: new Float32Array([3, 2, 7]), colorIndex: new Float32Array([0.5, 0.5, 0.5]),
    distancePc: new Float32Array([200, 120, Number.NaN]), flags: new Uint8Array([1, 1, 0]), byteLength: 0,
  };

  it('is the very row arrival-star-1 chose, the one with no distance in the same direction never taken', () => {
    const at = raDecToUnitVector(83, 4);
    const star = arrivalStarOfTile(tile, [at[0] * 120, at[1] * 120, at[2] * 120])!;
    expect(star).not.toBeNull();
    expect(pickedRowOf(tile, star.positionPc)).toBe(1);
  });

  it('is none for a place no row holds, or no place', () => {
    const at = raDecToUnitVector(83, 4);
    expect(pickedRowOf(tile, [at[0] * 121, at[1] * 121, at[2] * 121])).toBeNull();
    expect(pickedRowOf(tile, [0, 0, 0])).toBeNull();
  });
});
