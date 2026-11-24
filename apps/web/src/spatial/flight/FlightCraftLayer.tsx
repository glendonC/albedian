import React, { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js';
import dracoWrapperUrl from 'three/examples/jsm/libs/draco/gltf/draco_wasm_wrapper.js?url';
import dracoWasmUrl from 'three/examples/jsm/libs/draco/gltf/draco_decoder.wasm?url';
import type { Vec3, WcsCameraRows } from '@albedian/camera-rig';
import type { FlightCamera } from './flightFrame.js';
import { craftModelUrl, type Craft } from './crafts.js';
import { craftFrameRadiusM, craftSceneOf, type CraftFraming } from './craftScene.js';

/**
 * pilot-1: a NASA model drawn in the photo's own view during the flight through the stars, a few metres ahead of the
 * camera at the framing point and turned toward the star flown to, lit from the Sun's real direction. Its own canvas
 * sits over the star layer. No words and no movement of its own: it moves only because the flight's camera does.
 * The light's strength is the app's choice so the craft can be seen (the design lane words what is said about it).
 *
 * craft-cache-one (DECIDED-BY-LEAD 15:59): one renderer and one model loader for the whole flight, each parsed model kept
 * by its address so a switch back draws at once, the oldest beyond MODEL_CACHE_SIZE and every one on leaving disposed
 * (geometry, materials, textures). The picture of a craft is unchanged.
 */

type CraftState = 'loading' | 'drawn' | 'failed';

/** How many parsed models one flight keeps: the craft shown and the two before it (each up to about 18 MB on a phone). */
export const MODEL_CACHE_SIZE = 3;

/** A model loader for one flight: one Draco decoder (its workers and wasm kept warm) and one glTF loader. */
export interface ModelLoader {
  load(url: string): Promise<THREE.Group>;
  dispose(): void;
}

/** Loads NASA models (Draco compressed as NASA publishes them), the decoder from three's own files as Vite serves them. */
export function createModelLoader(): ModelLoader {
  const manager = new THREE.LoadingManager();
  manager.setURLModifier((asked) => (asked.endsWith('draco_wasm_wrapper.js') ? dracoWrapperUrl : asked.endsWith('draco_decoder.wasm') ? dracoWasmUrl : asked));
  const draco = new DRACOLoader(manager);
  draco.setDecoderPath('/draco/');
  const loader = new GLTFLoader(manager);
  loader.setDRACOLoader(draco);
  return { load: (url) => loader.loadAsync(url).then((gltf) => gltf.scene), dispose: () => draco.dispose() };
}

/** The renderer's part the layer uses: three's WebGLRenderer, or a test's stand-in. */
export interface CraftRenderer {
  setPixelRatio(ratio: number): void;
  setSize(width: number, height: number, updateStyle?: boolean): void;
  setClearColor(color: number, alpha: number): void;
  render(scene: THREE.Scene, camera: THREE.Camera): void;
  dispose(): void;
}

const makeWebGlRenderer = (canvas: HTMLCanvasElement): CraftRenderer => new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, premultipliedAlpha: true });

/** Frees what a model holds on the GPU: its geometry, its materials and their textures. */
export function disposeModel(model: THREE.Object3D): void {
  model.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    mesh.geometry?.dispose();
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      if (!material) continue;
      for (const value of Object.values(material)) if (value instanceof THREE.Texture) value.dispose();
      material.dispose();
    }
  });
}

interface CachedModel {
  readonly model: THREE.Group;
  readonly centre: [number, number, number];
  readonly radius: number;
  readonly size: Vec3;
}

interface CraftGpu {
  renderer: CraftRenderer;
  scene: THREE.Scene;
  camera: THREE.Camera;
  holder: THREE.Group;
  sun: THREE.DirectionalLight;
  /** The model drawn now and the craft it is drawn as (kept until the next model is ready), none while the first loads. */
  shown: { cached: CachedModel; craft: Craft; frameRadius: number } | null;
}

function drawCraft(gpu: CraftGpu, camera: FlightCamera, steer?: { positionPc: Vec3; rows: WcsCameraRows; craftRows?: WcsCameraRows }, framing?: CraftFraming): boolean {
  const shown = gpu.shown;
  if (!shown) return false;
  const scene = craftSceneOf(camera, shown.craft, shown.cached.centre, shown.cached.radius, steer, framing, shown.frameRadius);
  if (!scene) return false;
  gpu.holder.matrix.fromArray(scene.modelMatrix);
  gpu.holder.matrixWorldNeedsUpdate = true;
  gpu.camera.projectionMatrix.fromArray(scene.clipMatrix);
  gpu.camera.projectionMatrixInverse.copy(gpu.camera.projectionMatrix).invert();
  // The light shines from the Sun's side onto the craft
  const c = new THREE.Vector3(scene.modelMatrix[12], scene.modelMatrix[13], scene.modelMatrix[14]);
  gpu.sun.position.copy(c).addScaledVector(new THREE.Vector3(...scene.toSun), 10 * shown.cached.radius + 1);
  gpu.sun.target.position.copy(c);
  gpu.sun.target.updateMatrixWorld();
  gpu.renderer.render(gpu.scene, gpu.camera);
  return true;
}

export const FlightCraftLayer: React.FC<{
  camera: FlightCamera;
  craft: Craft;
  /** Loads a model by address; the flight's own loader (NASA's files) by default. */
  load?: (url: string) => Promise<THREE.Group>;
  /** Makes the layer's one renderer; three's WebGLRenderer by default. */
  makeRenderer?: (canvas: HTMLCanvasElement) => CraftRenderer;
  /** While the person steers: the camera they turn and move. */
  steer?: { positionPc: Vec3; rows: WcsCameraRows; craftRows?: WcsCameraRows } | null;
  /** Where the screen frames the craft; the laptop's (D26 item 2) by default. */
  framing?: CraftFraming;
  /** At phone width the hangar's far level is drawn. */
  phone?: boolean;
}> = ({ camera, craft, load, makeRenderer = makeWebGlRenderer, steer = null, framing, phone = false }) => {
  const canvas = useRef<HTMLCanvasElement>(null);
  const gpu = useRef<CraftGpu | null>(null);
  const cameraRef = useRef(camera);
  cameraRef.current = camera;
  const steerRef = useRef(steer);
  steerRef.current = steer;
  const framingRef = useRef(framing);
  framingRef.current = framing;
  const [state, setState] = useState<CraftState>('loading');
  const dpr = typeof window === 'undefined' ? 1 : Math.min(2, window.devicePixelRatio || 1);
  // The flight's one loader and its parsed models, by address, oldest first
  const loader = useRef<ModelLoader | null>(null);
  const models = useRef(new Map<string, Promise<CachedModel>>());
  const loadRef = useRef(load);
  loadRef.current = load;

  // One renderer, scene, camera and light for the whole flight; on leaving, every model and the loader go too
  useEffect(() => {
    const el = canvas.current;
    if (!el) return undefined;
    let renderer: CraftRenderer;
    try {
      renderer = makeRenderer(el);
    } catch {
      setState('failed');
      return undefined;
    }
    renderer.setPixelRatio(dpr);
    renderer.setSize(cameraRef.current.viewportWidthPx, cameraRef.current.viewportHeightPx, false);
    renderer.setClearColor(0x000000, 0);
    const scene = new THREE.Scene();
    const cam = new THREE.Camera();
    cam.matrixAutoUpdate = false;
    const holder = new THREE.Group();
    holder.matrixAutoUpdate = false;
    scene.add(holder);
    const sun = new THREE.DirectionalLight(0xffffff, 3);
    scene.add(sun, sun.target);
    gpu.current = { renderer, scene, camera: cam, holder, sun, shown: null };
    const cache = models.current;
    return () => {
      gpu.current = null;
      for (const pending of cache.values()) void pending.then(({ model }) => disposeModel(model), () => {});
      cache.clear();
      loader.current?.dispose();
      loader.current = null;
      renderer.dispose();
    };
  }, [dpr, makeRenderer]);

  // A craft asked for: its model from the flight's cache, else loaded once and kept
  useEffect(() => {
    let current = true;
    const g = gpu.current;
    if (!g) return undefined;
    const url = craftModelUrl(craft, phone);
    const cache = models.current;
    let pending = cache.get(url);
    if (pending) {
      // Most recently used last
      cache.delete(url);
    } else {
      const loadOne = loadRef.current ?? ((u: string) => (loader.current ??= createModelLoader()).load(u));
      pending = loadOne(url).then((model) => {
        model.traverse((o) => {
          o.frustumCulled = false;
        });
        const box = new THREE.Box3().setFromObject(model);
        const sphere = box.getBoundingSphere(new THREE.Sphere());
        const size = box.getSize(new THREE.Vector3());
        return { model, centre: [sphere.center.x, sphere.center.y, sphere.center.z] as [number, number, number], radius: sphere.radius, size: [size.x, size.y, size.z] as Vec3 };
      });
      // Beyond the cache's size the oldest goes, its GPU memory freed
      while (cache.size >= MODEL_CACHE_SIZE) {
        const [oldestUrl, oldest] = cache.entries().next().value as [string, Promise<CachedModel>];
        cache.delete(oldestUrl);
        void oldest.then(({ model }) => {
          if (g.holder.children.includes(model)) g.holder.remove(model);
          disposeModel(model);
        }, () => {});
      }
    }
    cache.set(url, pending);
    setState('loading');
    pending.then(
      (cached) => {
        if (!current || gpu.current !== g) return;
        g.holder.clear();
        g.holder.add(cached.model);
        g.shown = { cached, craft, frameRadius: craftFrameRadiusM(craft, cached.size, cached.radius) };
        setState(drawCraft(g, cameraRef.current, steerRef.current ?? undefined, framingRef.current) ? 'drawn' : 'failed');
      },
      () => {
        cache.delete(url);
        if (current) setState('failed');
      }
    );
    return () => {
      current = false;
    };
  }, [craft, phone, dpr, makeRenderer]);

  // Every frame of the flight draws the craft again from where the camera stands
  useEffect(() => {
    const g = gpu.current;
    if (!g || !g.shown) return;
    g.renderer.setSize(camera.viewportWidthPx, camera.viewportHeightPx, false);
    drawCraft(g, camera, steer ?? undefined, framing);
  }, [camera, steer, framing?.xPx, framing?.yPx, framing?.diameterPx]);

  return (
    <div className="flight-craft" data-testid="flight-craft" data-craft={craft.id} data-state={state} data-framing={framing ? `${framing.xPx.toFixed(1)},${framing.yPx.toFixed(1)}` : undefined} aria-hidden="true" style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
      <canvas ref={canvas} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', display: 'block' }} />
    </div>
  );
};
