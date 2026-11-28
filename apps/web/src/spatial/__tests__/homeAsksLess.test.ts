// @vitest-environment node
import * as THREE from 'three';
import type { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SparkPortalRenderer } from '../SparkPortalRenderer.js';
import { buildCislunarRegimeGroup, createEarthUniforms, createMoonUniforms } from '../webgl/cislunarRegime.js';

// home-asks-less-one (DECIDED-BY-LEAD 20:30 on 27 Nov, EXPORT's measure of a phone's home: the sky view's panorama and
// the Moon's model were asked behind the home, which shows neither): the renderer asks for them the first time the sky
// view is set up, which the viewport does as the home leaves; the same files through the same loaders, once.

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const MOON_MODEL = '/models/nasa_lro_moon_3d.glb';
function recordingGltf() {
  const asked: Array<{ url: string; onLoad: (gltf: { scene: THREE.Object3D }) => void }> = [];
  return { asked, loader: { load: (url: string, onLoad: (gltf: { scene: THREE.Object3D }) => void) => void asked.push({ url, onLoad }) } as unknown as GLTFLoader };
}

describe("the cislunar scene's Moon model waits until it is asked for", () => {
  it('is not asked while the scene is built, and is asked once however often it is asked for', () => {
    const { asked, loader } = recordingGltf();
    const built = buildCislunarRegimeGroup(new THREE.Group(), createEarthUniforms(), createMoonUniforms(), loader);
    expect(asked).toEqual([]);
    built.loadMoonShape();
    built.loadMoonShape();
    expect(asked.map((a) => a.url)).toEqual([MOON_MODEL]);
  });

  it("gives the Moon the model's shape at the scene's size once it comes, as before", () => {
    const { asked, loader } = recordingGltf();
    const built = buildCislunarRegimeGroup(new THREE.Group(), createEarthUniforms(), createMoonUniforms(), loader);
    built.loadMoonShape();
    const model = new THREE.Mesh(new THREE.BoxGeometry(20, 20, 20));
    const scene = new THREE.Group();
    scene.add(model);
    asked[0]!.onLoad({ scene });
    built.moonMesh.geometry.computeBoundingBox();
    const size = built.moonMesh.geometry.boundingBox!.getSize(new THREE.Vector3());
    // The model's side of 20 scaled by the Moon's radius over 10 (1.98 / 10)
    expect(size.x).toBeCloseTo(20 * (1.98 / 10), 5);
    expect(built.moonMesh.geometry).not.toBe(model.geometry);
  });
});

describe('SparkPortalRenderer asks for the sky view\'s pictures when the sky view is first set up', () => {
  function sparkStandIn() {
    vi.stubGlobal('window', { requestAnimationFrame: () => 1, cancelAnimationFrame() {}, addEventListener() {}, removeEventListener() {} });
    const container = { clientWidth: 800, clientHeight: 450, appendChild() {} } as unknown as HTMLElement;
    const renderer = new SparkPortalRenderer(container);
    const urls: string[] = [];
    const internals = renderer as unknown as { textureLoader: THREE.TextureLoader; renderer: unknown; loadMoonShape: (() => void) | null };
    internals.textureLoader = { load: (url: string) => void urls.push(url) } as unknown as THREE.TextureLoader;
    const loadMoonShape = vi.fn();
    internals.loadMoonShape = loadMoonShape;
    return { renderer, internals, urls, loadMoonShape };
  }

  it('asks for the panorama and the Moon model at the first showSkyView, once, where there is a context to draw them', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    const { renderer, internals, urls, loadMoonShape } = sparkStandIn();
    // No context yet: nothing asked, and nothing kept from asking later
    renderer.showSkyView();
    expect(urls).toEqual([]);
    expect(loadMoonShape).not.toHaveBeenCalled();
    internals.renderer = { render() {}, getPixelRatio: () => 1 };
    renderer.showSkyView();
    renderer.showSkyView();
    expect(urls).toEqual(['/textures/eso_milkyway_panorama.jpg']);
    expect(loadMoonShape).toHaveBeenCalledTimes(1);
  });
});
