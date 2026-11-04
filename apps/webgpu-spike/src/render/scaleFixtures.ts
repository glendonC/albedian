import { Object3D, Sphere, Vector3, type BufferGeometry, type WebGPURenderer } from 'three/webgpu';
import { GaussianSplat } from 'three/addons/objects/GaussianSplat.js';
import { SPZLoader } from 'three/addons/loaders/SPZLoader.js';
import { GpuArrays, SplatCollection, SplatRenderer, loadSplats } from '@sparkjsdev/radix';
import { applyRadixR187ComputeShim } from './radixCompat';

/**
 * Splat-count scaling fixtures.
 *
 * These `.spz` files sit unreferenced in apps/web/public/splats; their provenance is not
 * recorded in the repo and they are not astronomical. They are used only to load each WebGPU
 * splat path with 0.35M to 1M splats, far beyond the 2,811 Local Bubble splats, so sort and
 * raster costs at production scale can be compared. Spherical harmonics are disabled on both
 * paths so they render the same SH0 data.
 */
export const SCALE_FIXTURES = {
  valley: { url: '/splats/valley.spz', splats: 500_000 },
  snow: { url: '/splats/snow-street.spz', splats: 981_908 },
} as const;

export type ScaleFixtureName = keyof typeof SCALE_FIXTURES;

export const SCALE_FIXTURE_LABEL =
  'Scaling fixture: unreferenced repo .spz scene (provenance not recorded, not astronomical), SH disabled';

export interface ScaleLayer {
  object: Object3D;
  count: number;
  bounds: Sphere;
  dispose(): void;
}

const geometryCache = new Map<string, Promise<BufferGeometry>>();

function loadGeometry(url: string): Promise<BufferGeometry> {
  let pending = geometryCache.get(url);
  if (!pending) {
    pending = new SPZLoader().loadAsync(url).then((geometry: BufferGeometry) => {
      for (const name of ['sphericalHarmonics1', 'sphericalHarmonics2', 'sphericalHarmonics3']) {
        if (geometry.getAttribute(name)) geometry.deleteAttribute(name);
      }
      geometry.computeBoundingSphere();
      return geometry;
    });
    geometryCache.set(url, pending);
  }
  return pending;
}

export async function scaleFixtureBounds(name: ScaleFixtureName): Promise<Sphere> {
  const geometry = await loadGeometry(SCALE_FIXTURES[name].url);
  return geometry.boundingSphere?.clone() ?? new Sphere(new Vector3(), 10);
}

export async function createThreeScaleLayer(name: ScaleFixtureName): Promise<ScaleLayer> {
  const geometry = await loadGeometry(SCALE_FIXTURES[name].url);
  const mesh = new GaussianSplat(geometry);
  mesh.name = `scale-three-${name}`;
  return {
    object: mesh,
    count: geometry.getAttribute('position').count,
    bounds: geometry.boundingSphere?.clone() ?? new Sphere(new Vector3(), 10),
    dispose() {
      mesh.geometry.dispose();
      mesh.material.dispose();
    },
  };
}

export async function createRadixScaleLayer(renderer: WebGPURenderer, name: ScaleFixtureName): Promise<ScaleLayer> {
  applyRadixR187ComputeShim(renderer);
  const data = await loadSplats(SCALE_FIXTURES[name].url, { sh: false });
  const gpu = GpuArrays.fromData(renderer, data);
  const anchor = new Object3D();
  const splatRenderer = new SplatRenderer({
    renderer,
    collection: new SplatCollection([SplatCollection.arrays(gpu, { matrixWorld: anchor.matrixWorld })]),
  });
  splatRenderer.name = `scale-radix-${name}`;
  splatRenderer.frustumCulled = false;
  const group = new Object3D();
  group.add(anchor);
  group.add(splatRenderer);
  return {
    object: group,
    count: data.count,
    bounds: await scaleFixtureBounds(name),
    dispose() {
      splatRenderer.dispose();
      gpu.dispose();
    },
  };
}
