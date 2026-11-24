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
 *
 * craft-switch-motion-1 (MOTION-READY-web.md section eight, ruled 12:41 and 12:43): switching craft, the outgoing craft
 * frames out from the press (look-mo-frame-out) while still drawn from the camera every frame, copied into its own
 * canvas after its render; the incoming one frames in (look-mo-frame-in) from the first frame its model is drawn; the
 * two overlap and neither waits. Under Reduce Motion the tokens make both the 270 ms dissolve.
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
  clear(): void;
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
  /** The model drawn now and the craft it is drawn as, none while the incoming one loads. */
  shown: Shown | null;
  /** The craft framing out after a switch, drawn into its own canvas until its frame-out ends. */
  leaving: Shown | null;
}

interface Shown {
  readonly cached: CachedModel;
  readonly craft: Craft;
  readonly frameRadius: number;
}

type Steer = { positionPc: Vec3; rows: WcsCameraRows; craftRows?: WcsCameraRows };

/**
 * One frame: the outgoing craft, if any, rendered and copied into its own canvas; then the craft shown (or a clear canvas
 * while it loads). True when the craft shown was drawn.
 */
function drawFrame(gpu: CraftGpu, camera: FlightCamera, steer: Steer | undefined, framing: CraftFraming | undefined, glCanvas: HTMLCanvasElement | null, outCanvas: HTMLCanvasElement | null): boolean {
  if (gpu.leaving && outCanvas && glCanvas) {
    if (drawCraft(gpu, gpu.leaving, camera, steer, framing)) {
      if (outCanvas.width !== glCanvas.width) outCanvas.width = glCanvas.width;
      if (outCanvas.height !== glCanvas.height) outCanvas.height = glCanvas.height;
      const ctx = outCanvas.getContext('2d');
      ctx?.clearRect(0, 0, outCanvas.width, outCanvas.height);
      ctx?.drawImage(glCanvas, 0, 0);
    }
  }
  if (!gpu.shown) {
    gpu.holder.clear();
    gpu.renderer.clear();
    return false;
  }
  return drawCraft(gpu, gpu.shown, camera, steer, framing);
}

function drawCraft(gpu: CraftGpu, shown: Shown, camera: FlightCamera, steer?: Steer, framing?: CraftFraming): boolean {
  const scene = craftSceneOf(camera, shown.craft, shown.cached.centre, shown.cached.radius, steer, framing, shown.frameRadius);
  if (!scene) return false;
  if (gpu.holder.children.length !== 1 || gpu.holder.children[0] !== shown.cached.model) {
    gpu.holder.clear();
    gpu.holder.add(shown.cached.model);
  }
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
  /** The craft asked for; none (Your view) after one was shown, so it can frame out. */
  craft: Craft | null;
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
  /**
   * steer-frame-one: filled with this layer's draw for one steering frame, which the star layer's one frame loop calls
   * each frame with the camera of that frame (no React render per frame).
   */
  drawRef?: React.MutableRefObject<((steer: Steer) => void) | null>;
}> = ({ camera, craft, load, makeRenderer = makeWebGlRenderer, steer = null, framing, phone = false, drawRef }) => {
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
  // The outgoing craft's canvas, one per switch, and the incoming one's frame-in, restarted at each first drawn frame
  const outCanvas = useRef<HTMLCanvasElement>(null);
  const inFrame = useRef<HTMLDivElement>(null);
  const [leavingKey, setLeavingKey] = useState<number | null>(null);
  const switches = useRef(0);
  const [entering, setEntering] = useState(0);
  const draw = (g: CraftGpu) => drawFrame(g, cameraRef.current, steerRef.current ?? undefined, framingRef.current, canvas.current, outCanvas.current);

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
    gpu.current = { renderer, scene, camera: cam, holder, sun, shown: null, leaving: null };
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

  // A craft asked for: its model from the flight's cache, else loaded once and kept; the one shown before frames out
  useEffect(() => {
    let current = true;
    const g = gpu.current;
    if (!g) return undefined;
    if (g.shown && g.shown.craft.id !== craft?.id) {
      g.leaving = g.shown;
      g.shown = null;
      switches.current += 1;
      setLeavingKey(switches.current);
    }
    if (!craft) {
      setState('drawn');
      return undefined;
    }
    if (g.shown?.craft.id === craft.id) return undefined;
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
        g.shown = { cached, craft, frameRadius: craftFrameRadiusM(craft, cached.size, cached.radius) };
        const drawn = draw(g);
        if (drawn) setEntering((n) => n + 1);
        setState(drawn ? 'drawn' : 'failed');
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

  // Every frame of the flight draws the craft again from where the camera stands, and the outgoing one while it goes
  useEffect(() => {
    const g = gpu.current;
    if (!g || (!g.shown && !g.leaving)) return;
    g.renderer.setSize(camera.viewportWidthPx, camera.viewportHeightPx, false);
    draw(g);
  }, [camera, steer, framing?.xPx, framing?.yPx, framing?.diameterPx]);

  // The star layer's frame loop draws a steering frame through this
  useEffect(() => {
    if (!drawRef) return undefined;
    drawRef.current = (s) => {
      const g = gpu.current;
      if (g && (g.shown || g.leaving)) drawFrame(g, cameraRef.current, s, framingRef.current, canvas.current, outCanvas.current);
    };
    return () => {
      drawRef.current = null;
    };
  });

  // The outgoing craft's canvas is drawn on its first frame
  useEffect(() => {
    const g = gpu.current;
    if (g && leavingKey !== null) draw(g);
  }, [leavingKey]);

  // The incoming craft frames in from its first drawn frame: the animation started again on that frame
  useEffect(() => {
    const el = inFrame.current;
    if (!el || entering === 0) return;
    el.classList.remove('is-entering');
    void el.offsetWidth;
    el.classList.add('is-entering');
  }, [entering]);

  const leaveDone = () => {
    const g = gpu.current;
    if (g) g.leaving = null;
    setLeavingKey(null);
  };

  return (
    <div className="flight-craft" data-testid="flight-craft" data-craft={craft?.id ?? 'none'} data-state={state} data-framing={framing ? `${framing.xPx.toFixed(1)},${framing.yPx.toFixed(1)}` : undefined} aria-hidden="true" style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
      <div ref={inFrame} className="flight-craft__in" style={{ position: 'absolute', inset: 0 }}>
        <canvas ref={canvas} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', display: 'block' }} />
      </div>
      {leavingKey !== null && (
        <canvas key={leavingKey} ref={outCanvas} className="flight-craft__out" data-testid="flight-craft-out" onAnimationEnd={leaveDone} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', display: 'block' }} />
      )}
    </div>
  );
};
