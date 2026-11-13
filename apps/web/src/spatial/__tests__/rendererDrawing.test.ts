// @vitest-environment node
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as THREE from 'three';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SparkPortalRenderer } from '../SparkPortalRenderer.js';
import { createEarthUniforms, createMoonUniforms } from '../webgl/cislunarRegime.js';
import { createNebulaUniforms } from '../webgl/deepSkyRegime.js';
import { createPlanetarySkyUniforms } from '../webgl/planetarySurfaceRegime.js';
import { createSkyDomeUniforms, loadLandingSceneTextures, loadSkyViewTextures } from '../webgl/skyDomeAndCinemaShader.js';
import { CislunarWebGpuRenderer } from '../webgpu/CislunarWebGpuRenderer.js';
import { WebGpuHost } from '../webgpu/WebGpuHost.js';

// The WebGPU renderer's post chain needs a real renderer; its start-up path is reached with this stand-in
vi.mock('../webgpu/postChain.js', () => ({
  buildCosmosPostChain: () => ({ describe: () => 'a stand-in post chain', dispose() {}, pipeline: { render() {} } }),
}));

const here = path.dirname(fileURLToPath(import.meta.url));

/**
 * Both renderers stop drawing frames while no frame can be seen, and start again when one can (web-home-idle-1). Under
 * Node there is no WebGL or WebGPU, so the frame loop is driven through a stand-in for the browser's frame requests.
 */

interface SparkLoop {
  rafId: number;
  drawing: boolean;
  tick: ((timestamp: number) => void) | null;
  timer: { reset(): unknown };
  scheduleNext(): void;
}

function sparkWithLoop() {
  const frames = { requested: [] as Array<(t: number) => void>, cancelled: [] as number[], next: 7 };
  vi.stubGlobal('window', {
    requestAnimationFrame: (callback: (t: number) => void) => {
      frames.requested.push(callback);
      return frames.next++;
    },
    cancelAnimationFrame: (id: number) => frames.cancelled.push(id),
    addEventListener() {},
    removeEventListener() {},
  });
  const container = { clientWidth: 800, clientHeight: 450, appendChild() {} } as unknown as HTMLElement;
  const renderer = new SparkPortalRenderer(container);
  const loop = renderer as unknown as SparkLoop;
  // The loop as startLoop leaves it: its step known and one frame asked for
  const tick = vi.fn();
  loop.tick = tick;
  loop.rafId = 3;
  const reset = vi.spyOn(loop.timer, 'reset');
  return { renderer, loop, frames, tick, reset };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('SparkPortalRenderer: drawing stops and starts', () => {
  it('cancels the frame it asked for and asks for none while it is not drawing', () => {
    const { renderer, loop, frames } = sparkWithLoop();
    renderer.setDrawing(false);
    expect(frames.cancelled).toEqual([3]);
    expect(loop.rafId).toBe(0);
    loop.scheduleNext();
    expect(frames.requested).toHaveLength(0);
  });

  it('asks for the next frame when it draws again, with its clock reset so the scene does not jump', () => {
    const { renderer, loop, frames, tick, reset } = sparkWithLoop();
    renderer.setDrawing(false);
    renderer.setDrawing(true);
    expect(reset).toHaveBeenCalledTimes(1);
    expect(frames.requested).toEqual([tick]);
    expect(loop.rafId).toBe(7);
    // A frame step asks for the next while drawing
    loop.scheduleNext();
    expect(frames.requested).toEqual([tick, tick]);
  });

  it('does nothing when told what it is already doing', () => {
    const { renderer, frames, reset } = sparkWithLoop();
    renderer.setDrawing(true);
    expect(frames.requested).toHaveLength(0);
    expect(frames.cancelled).toHaveLength(0);
    renderer.setDrawing(false);
    renderer.setDrawing(false);
    expect(frames.cancelled).toEqual([3]);
    expect(reset).not.toHaveBeenCalled();
  });

  it('asks for no frame before its loop has started, even when told to draw', () => {
    const { renderer, loop, frames } = sparkWithLoop();
    loop.tick = null;
    renderer.setDrawing(false);
    renderer.setDrawing(true);
    expect(frames.requested).toHaveLength(0);
  });
});

describe('SparkPortalRenderer: its own frame step', () => {
  it('asks for the next frame after drawing one, and for none once drawing has stopped', () => {
    const { renderer, loop, frames } = sparkWithLoop();
    vi.stubGlobal('document', { hidden: false, addEventListener() {}, removeEventListener() {} });
    // A stand-in drawing context, so the loop starts as it does in a browser
    const drawn: number[] = [];
    const internals = renderer as unknown as { renderer: unknown; composer: unknown; startLoop(): void };
    internals.renderer = { render: () => drawn.push(1), getPixelRatio: () => 1 };
    internals.composer = null;
    loop.tick = null;
    internals.startLoop();
    expect(frames.requested).toHaveLength(1);
    const step = frames.requested[0]!;
    expect(loop.tick).toBe(step);
    step(16);
    expect(drawn).toEqual([1]);
    expect(frames.requested).toEqual([step, step]);
    // A frame the browser had already queued runs once more, draws, and asks for no next one
    renderer.setDrawing(false);
    step(32);
    expect(drawn).toEqual([1, 1]);
    expect(frames.requested).toHaveLength(2);
  });
});

describe('SparkPortalRenderer: a loop that starts while not drawing', () => {
  it('asks for no first frame when told to stop before its loop started, and asks once it draws', () => {
    const { renderer, loop, frames } = sparkWithLoop();
    vi.stubGlobal('document', { hidden: false, addEventListener() {}, removeEventListener() {} });
    const internals = renderer as unknown as { renderer: unknown; composer: unknown; startLoop(): void };
    internals.renderer = { render() {}, getPixelRatio: () => 1 };
    internals.composer = null;
    loop.tick = null;
    renderer.setDrawing(false);
    internals.startLoop();
    expect(frames.requested).toHaveLength(0);
    renderer.setDrawing(true);
    expect(frames.requested).toHaveLength(1);
  });
});

describe('CislunarWebGpuRenderer: drawing stops and starts its host loop', () => {
  const fake = () => ({ drawing: true, pauseLoop: vi.fn(), resumeLoop: vi.fn() });
  const setDrawing = (target: ReturnType<typeof fake>, drawing: boolean) => CislunarWebGpuRenderer.prototype.setDrawing.call(target, drawing);

  it('pauses its loop while not drawing and resumes it (with its clock reset) when it draws again, once each', () => {
    const r = fake();
    setDrawing(r, false);
    setDrawing(r, false);
    expect(r.pauseLoop).toHaveBeenCalledTimes(1);
    setDrawing(r, true);
    setDrawing(r, true);
    expect(r.resumeLoop).toHaveBeenCalledTimes(1);
  });

  it('does nothing when told to draw while drawing', () => {
    const r = fake();
    setDrawing(r, true);
    expect(r.pauseLoop).not.toHaveBeenCalled();
    expect(r.resumeLoop).not.toHaveBeenCalled();
  });
});

// The landing scenes' pictures, which the sky view never draws, load when a landing scene first starts and not on every
// visit; a renderer that is not drawing starts no landing scene, so under the home none loads (the lead's 04:30 answer)
const LANDING_PICTURES = [
  '/textures/bruneton_atmo_lut.png',
  '/textures/earth_clouds_bluemarble2002_2k.jpg',
  '/textures/earth_day_bluemarble_2004-03_base_8k.jpg',
  '/textures/earth_height_etopo2022_4k.png',
  '/textures/earth_night_blackmarble_2016_8k.jpg',
  '/textures/earth_water_natural_earth_4k.png',
  '/textures/eso1804a.jpg',
  '/textures/lro_moon_2k.jpg',
  '/textures/lro_moon_disp.jpg',
];
const recordingLoader = () => {
  const urls: string[] = [];
  return { urls, loader: { load: (url: string) => void urls.push(url) } as unknown as THREE.TextureLoader };
};

describe('which pictures load when', () => {
  it('the sky view loads the panorama alone', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    const { urls, loader } = recordingLoader();
    loadSkyViewTextures(loader, createSkyDomeUniforms());
    expect(urls).toEqual(['/textures/eso_milkyway_panorama.jpg']);
  });

  it('a landing scene loads the Earth, the Moon, the atmosphere table and the Lupus 3 photograph', () => {
    const { urls, loader } = recordingLoader();
    loadLandingSceneTextures(loader, createEarthUniforms(), createMoonUniforms(), createNebulaUniforms(), createPlanetarySkyUniforms(), '/textures/eso1804a.jpg');
    expect([...urls].sort()).toEqual(LANDING_PICTURES);
  });
});

describe('SparkPortalRenderer: no landing scene starts while it is not drawing', () => {
  function sparkWithLoader() {
    const { renderer } = sparkWithLoop();
    const { urls, loader } = recordingLoader();
    (renderer as unknown as { textureLoader: THREE.TextureLoader }).textureLoader = loader;
    // A stand-in drawing context: pictures are asked for only where there is one to draw them
    (renderer as unknown as { renderer: unknown }).renderer = { render() {}, getPixelRatio: () => 1 };
    const regimes: string[] = [];
    (renderer as unknown as { onRegimeChange: (regime: string) => void }).onRegimeChange = (regime) => regimes.push(regime);
    return { renderer, urls, regimes };
  }

  it('holds a scene change while not drawing, and drops it when the showcase ends', () => {
    const { renderer, urls, regimes } = sparkWithLoader();
    renderer.setShowcaseMode(true);
    renderer.setDrawing(false);
    renderer.transitionToShowcaseScene('black_hole', true);
    expect(regimes).toEqual([]);
    expect(urls).toEqual([]);
    renderer.setShowcaseMode(false);
    renderer.setDrawing(true);
    expect(regimes).toEqual([]);
    expect(urls).toEqual([]);
  });

  it('starts a held scene when it draws again with the showcase still on', () => {
    const { renderer, urls, regimes } = sparkWithLoader();
    renderer.setShowcaseMode(true);
    renderer.setDrawing(false);
    renderer.transitionToShowcaseScene('black_hole', true);
    renderer.setDrawing(true);
    expect(regimes).toEqual(['deep_sky']);
    expect([...urls].sort()).toEqual(LANDING_PICTURES);
  });

  it('drops a held scene for good when the showcase ends, even if it starts again before drawing does', () => {
    const { renderer, urls, regimes } = sparkWithLoader();
    renderer.setShowcaseMode(true);
    renderer.setDrawing(false);
    renderer.transitionToShowcaseScene('black_hole', true);
    renderer.setShowcaseMode(false);
    renderer.setShowcaseMode(true);
    renderer.setDrawing(true);
    expect(regimes).toEqual([]);
    expect(urls).toEqual([]);
  });

  it('does not start a scene held while the showcase was off', () => {
    const { renderer, urls, regimes } = sparkWithLoader();
    renderer.setDrawing(false);
    renderer.transitionToShowcaseScene('black_hole', true);
    renderer.setDrawing(true);
    expect(regimes).toEqual([]);
    expect(urls).toEqual([]);
  });

  it('asks for no picture without a context to draw them, as under Node', () => {
    const { renderer, urls, regimes } = sparkWithLoader();
    (renderer as unknown as { renderer: unknown }).renderer = null;
    renderer.setShowcaseMode(true);
    renderer.transitionToShowcaseScene('black_hole', true);
    expect(regimes).toEqual(['deep_sky']);
    expect(urls).toEqual([]);
  });

  it('loads the landing pictures once, when the first landing scene starts', () => {
    const { renderer, urls, regimes } = sparkWithLoader();
    renderer.setShowcaseMode(true);
    renderer.transitionToShowcaseScene('black_hole', true);
    renderer.transitionToShowcaseScene('supergiant', true);
    expect(regimes).toEqual(['deep_sky', 'solar_neighborhood']);
    expect([...urls].sort()).toEqual(LANDING_PICTURES);
  });
});

// The two lines a browser run held, held under Node (web-home-idle-1's second version)
describe('SparkPortalRenderer: the landing pictures are asked for in one place only', () => {
  it('calls their load only inside askLandingPictures, and askLandingPictures only when a landing scene starts', () => {
    const source = fs.readFileSync(path.resolve(here, '../SparkPortalRenderer.ts'), 'utf8');
    const ask = source.indexOf('  private askLandingPictures(): void {');
    const askEnd = source.indexOf('\n  }\n', ask);
    expect(ask, 'askLandingPictures').toBeGreaterThan(-1);
    // Found by its own name, so imported under it; reached another way (a namespace import) it is held by
    // nebulaTextures.test.ts, which counts the black hole picture's loads
    expect(source, 'loadLandingSceneTextures imported under another name').not.toMatch(/\bloadLandingSceneTextures\s+as\b/);
    const loads = [...source.matchAll(/loadLandingSceneTextures\(/g)].map((m) => m.index!);
    expect(loads.filter((at) => at < ask || at > askEnd), 'a load outside askLandingPictures').toEqual([]);
    expect(loads, 'loadLandingSceneTextures( in SparkPortalRenderer.ts, once').toHaveLength(1);
    const asks = [...source.matchAll(/this\.askLandingPictures\(\)/g)].map((m) => m.index!);
    expect(asks, 'this.askLandingPictures() in SparkPortalRenderer.ts, once').toHaveLength(1);
    const transition = source.indexOf('  public transitionToShowcaseScene(');
    const transitionEnd = source.indexOf('\n  }\n', transition);
    expect(asks[0]! > transition && asks[0]! < transitionEnd, 'asked for when a landing scene starts').toBe(true);
    // The sky view's panorama is what loads at start
    expect(source).toContain('      loadSkyViewTextures(this.textureLoader, this.skyDomeUniforms);\n');
  });
});

describe('CislunarWebGpuRenderer: a host that becomes ready under the home waits to draw', () => {
  const loop = CislunarWebGpuRenderer.prototype as unknown as { resumeLoop(): void; pauseLoop(): void };
  async function startWith(drawing: boolean) {
    const host = { renderer: {}, aspect: 1, backend: 'webgl2', onResize() {}, resize() {}, dispose() {}, start: vi.fn(), stop: vi.fn() };
    vi.spyOn(WebGpuHost, 'create').mockResolvedValue(host as unknown as WebGpuHost);
    vi.spyOn(console, 'info').mockImplementation(() => {});
    const renderer = {
      disposed: false,
      drawing,
      host: null as unknown,
      post: null as unknown,
      camera: { aspect: 1, updateProjectionMatrix() {} },
      cssSize: { width: 800, height: 450 },
      scene: {},
      postOptions: {},
      optics: {},
      timer: { connect() {}, reset() {} },
      onAnimationFrame() {},
      // The renderer's own resume and pause, watched, so the stand-in host's start and stop are reached as in the app
      resumeLoop: vi.fn(function (this: unknown) { loop.resumeLoop.call(this); }),
      pauseLoop: vi.fn(function (this: unknown) { loop.pauseLoop.call(this); }),
    };
    const start = (CislunarWebGpuRenderer.prototype as unknown as { start(this: unknown, c: unknown, o: unknown): Promise<void> }).start;
    await start.call(renderer, {}, { forceWebGL: false });
    return { renderer, host };
  }

  it('does not start drawing when it becomes ready while the home covers the canvas, and starts once the home leaves', async () => {
    const { renderer, host } = await startWith(false);
    expect(renderer.host).not.toBeNull();
    expect(renderer.resumeLoop).not.toHaveBeenCalled();
    expect(host.start, 'the host loop started under the home').not.toHaveBeenCalled();
    CislunarWebGpuRenderer.prototype.setDrawing.call(renderer as unknown as CislunarWebGpuRenderer, true);
    expect(renderer.resumeLoop).toHaveBeenCalledTimes(1);
    expect(host.start).toHaveBeenCalledTimes(1);
    expect(host.start).toHaveBeenCalledWith(expect.any(Function));
  });

  it('starts drawing at once when it becomes ready with nothing covering it', async () => {
    const { renderer, host } = await startWith(true);
    expect(renderer.resumeLoop).toHaveBeenCalledTimes(1);
    expect(host.start).toHaveBeenCalledTimes(1);
  });
});
