import React, { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js';
import dracoWrapperUrl from 'three/examples/jsm/libs/draco/gltf/draco_wasm_wrapper.js?url';
import dracoWasmUrl from 'three/examples/jsm/libs/draco/gltf/draco_decoder.wasm?url';
import type { Vec3, WcsCameraRows } from '@albedian/camera-rig';
import type { FlightCamera } from './flightFrame.js';
import { craftModelUrl, type Craft } from './crafts.js';
import { craftFrameRadiusM, craftSceneOf, laptopCraftFraming, type CraftFraming } from './craftScene.js';
import { addEdgeLight, createEdgeLightUniforms, edgeLightColor, type EdgeLightUniforms } from './craftEdgeLight.js';
import { markFillOcclusion } from './craftFillOcclusion.js';
import { poseWeights, posesOf, posesReport, sceneWithClips, setPoses, type CraftPoses } from './craftPoses.js';
import { aliveFraming, aliveRotation, chaseAxes, diameterPerHeight, rotatedAboutCentre, screenBoxOf, type AliveSignals } from './craftAlive.js';
import { brightestSkyDirection, CRAFT_SKY_SIZE, CRAFT_SKY_URL, craftLighting, environmentRotationRows, keyLight, keyToFillOf, skyIntegralOfPixels, type CraftSkyLight, type LightCandidate, type SkyRim } from './craftLight.js';
import { loadPanoramaRegistration } from '../sky/skyRegistration.js';
import { arrivedFlightCamera } from './arrival.js';
import { skyPictureSourceOf } from '../sky/skyPicture.js';

/**
 * pilot-1: a NASA model drawn in the photo's own view during the flight through the stars, a few metres ahead of the
 * camera at the framing point and turned toward the star flown to. Its own canvas sits over the star layer. No words
 * and no movement of its own: it moves only because the flight's camera does.
 *
 * craft-light-one (ruled 15:34 and 16:08 on 24 Nov): lit by the brightest real source where the camera is (beside the
 * star flown to, that star, in its catalog tint; near Earth, the Sun), its only fill the sky's own light (the starless
 * glow scaled to the catalog's summed starlight), tone mapped with ACES and exposed for the side the camera sees at
 * middle grey (craftLight.ts). Nothing lights it that is not there.
 * craft-light-two (DECIDED-BY-LEAD on LOOK's pictures): the sky fills at four to one whatever the star, the Milky Way's
 * brightest sky is a second light at half the key, and the meter counts every light (craftLighting).
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

const NO_CANDIDATES: readonly LightCandidate[] = [];

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
  // craft-alive-two: the scene with its file's animations kept on it, where the suit's three poses are
  return { load: (url) => loader.loadAsync(url).then(sceneWithClips), dispose: () => draco.dispose() };
}

/** The renderer's part the layer uses: three's WebGLRenderer, or a test's stand-in. */
export interface CraftRenderer {
  setPixelRatio(ratio: number): void;
  setSize(width: number, height: number, updateStyle?: boolean): void;
  setClearColor(color: number, alpha: number): void;
  render(scene: THREE.Scene, camera: THREE.Camera): void;
  clear(): void;
  dispose(): void;
  /** Set on three's renderer (ACES and the metered exposure); a stand-in may leave them be. */
  toneMapping?: THREE.ToneMapping;
  toneMappingExposure?: number;
  /** craft-compile-one: three's own asynchronous compile, and one texture's upload; a stand-in may have neither. */
  compileAsync?(scene: THREE.Object3D, camera: THREE.Camera, targetScene?: THREE.Scene | null): Promise<unknown>;
  initTexture?(texture: THREE.Texture): void;
}

const makeWebGlRenderer = (canvas: HTMLCanvasElement): CraftRenderer => new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, premultipliedAlpha: true });

/** The textures a model's materials hold, each once. */
function texturesOf(model: THREE.Object3D): THREE.Texture[] {
  const found = new Set<THREE.Texture>();
  model.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      if (!material) continue;
      for (const value of Object.values(material)) if (value instanceof THREE.Texture) found.add(value);
    }
  });
  return [...found];
}

const nextFrame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

/**
 * craft-compile-one (DECIDED-BY-LEAD 22:31: a craft's first load never stops the page): what a new model needs on the
 * GPU is made before it is shown, so no frame waits for it: its programs compiled by three's own asynchronous compile
 * against the flight's scene (its lights and environment, so the programs are the ones its draws use; the browser's
 * parallel compile where it has one), then its textures uploaded one a frame. A renderer without these (a test's
 * stand-in) shows it at once, as before; a compile that fails leaves the first draw to compile, as before.
 */
function readyOnGpu(g: CraftGpu, model: THREE.Object3D): Promise<void> {
  const r = g.renderer;
  if (!r.compileAsync && !r.initTexture) return Promise.resolve();
  let ready = g.ready.get(model);
  if (!ready) {
    ready = (async () => {
      if (r.compileAsync) await r.compileAsync(model, g.camera, g.scene).catch(() => undefined);
      if (r.initTexture) {
        for (const texture of texturesOf(model)) {
          await nextFrame();
          r.initTexture(texture);
        }
      }
      await nextFrame();
    })();
    g.ready.set(model, ready);
  }
  return ready;
}

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
  /** craft-ao-fill-one: how many of the model's meshes are drawn with the sky fill's occlusion, of how many, as "n/m". */
  readonly fillAo: string;
  /** craft-alive-two: the suit's three poses when its file carries them (craftPoses.ts), else none. */
  readonly poses: CraftPoses | null;
}

interface CraftGpu {
  /** craft-alive-shoulder-one: where the craft drawn last stands on screen, CSS pixels. */
  lastBox?: { x0: number; y0: number; x1: number; y1: number } | null;
  /** craft-alive-two: the poses the craft drawn last was drawn in (posesReport). */
  lastPoses?: string;
  renderer: CraftRenderer;
  scene: THREE.Scene;
  camera: THREE.Camera;
  holder: THREE.Group;
  key: THREE.DirectionalLight;
  /** craft-light-two: the second light, from the Milky Way's brightest sky. */
  galaxy: THREE.DirectionalLight;
  /** chase-light-one: the edge light every model of this renderer shares (craftEdgeLight.ts). */
  edge: EdgeLightUniforms;
  /** The sky the craft takes its fill from, loading once a flight: its light summed over the sphere and where it is brightest, or none. */
  sky: Promise<CraftSkyLight | null>;
  /** That light once a craft is drawn with it; none until then (no fill, no second light). */
  skyLight: CraftSkyLight | null;
  /** The model drawn now and the craft it is drawn as, none while the incoming one loads. */
  shown: Shown | null;
  /** The craft framing out after a switch, drawn into its own canvas until its frame-out ends. */
  leaving: Shown | null;
  /** craft-compile-one: each model made ready on this renderer once, before it is first shown. */
  ready: WeakMap<THREE.Object3D, Promise<void>>;
}

interface Shown {
  readonly cached: CachedModel;
  readonly craft: Craft;
  /** The model's address, which keeps it in the cache while it is on screen. */
  readonly url: string;
  readonly frameRadius: number;
}

/** phone-star-open-one: `shiftPx`, the steering frame's shift on the window beyond the camera's own (heldStar.ts). */
type Steer = { positionPc: Vec3; rows: WcsCameraRows; craftRows?: WcsCameraRows; alive?: AliveSignals; shiftPx?: readonly [number, number] };

interface CraftSkySource {
  readonly copy: HTMLCanvasElement;
  readonly integral: number;
  readonly rotation: THREE.Matrix4;
  /** craft-light-two: where the sky is brightest, the second light's source. */
  readonly galaxy: SkyRim | null;
}

/** The decoded sky, once a page: each flight builds its own environment from it but loads and sums it once. */
let craftSkySource: Promise<CraftSkySource> | null = null;

/**
 * The glow at the craft sky's size, decoded and shrunk off the main thread (createImageBitmap's resize), so building
 * the craft's sky costs Go there no 6000 px decode (AUDIT-D, 18:18); a browser without the resize options decodes it
 * off the main thread at full size and the canvas below shrinks it.
 */
export async function smallCraftSky(): Promise<ImageBitmap | HTMLImageElement> {
  // On a phone the glow's half-side copy, the file the glow itself is kept from there, so the page fetches one of them
  const response = await fetch(skyPictureSourceOf(CRAFT_SKY_URL));
  if (!response.ok) throw new Error('craft sky');
  const blob = await response.blob();
  try {
    return await createImageBitmap(blob, { resizeWidth: CRAFT_SKY_SIZE.width, resizeHeight: CRAFT_SKY_SIZE.height, resizeQuality: 'high' });
  } catch {
    const url = URL.createObjectURL(blob);
    try {
      const img = new Image();
      img.src = url;
      await img.decode();
      return img;
    } finally {
      URL.revokeObjectURL(url);
    }
  }
}

async function decodeCraftSky(): Promise<CraftSkySource> {
  const [source, registration] = await Promise.all([smallCraftSky(), loadPanoramaRegistration()]);
  // The environment and the sum, both from one small copy of the same pixels (a fill needs no detail)
  const copy = document.createElement('canvas');
  copy.width = CRAFT_SKY_SIZE.width;
  copy.height = CRAFT_SKY_SIZE.height;
  const ctx = copy.getContext('2d', { willReadFrequently: true });
  if (!ctx) throw new Error('craft sky');
  ctx.drawImage(source, 0, 0, copy.width, copy.height);
  if ('close' in source) source.close();
  const pixels = ctx.getImageData(0, 0, copy.width, copy.height).data;
  const integral = skyIntegralOfPixels(pixels, copy.width, copy.height);
  const galaxy = brightestSkyDirection(pixels, copy.width, copy.height, registration);
  const r = environmentRotationRows(registration);
  const rotation = new THREE.Matrix4().set(r[0], r[1], r[2], 0, r[3], r[4], r[5], 0, r[6], r[7], r[8], 0, 0, 0, 0, 1);
  return { copy, integral, rotation, galaxy };
}

/** The sky the craft takes its fill from: the starless glow, turned to where the registration puts it, as a PMREM. */
/** The decoded and summed sky, once a page; a failure is asked again next time. */
function craftSkySourceOnce(): Promise<CraftSkySource> {
  craftSkySource ??= decodeCraftSky().catch((error: unknown) => {
    craftSkySource = null;
    throw error;
  });
  return craftSkySource;
}

/**
 * craft-compile-one: the craft's sky decoded, copied and summed while the reveal is at rest (its copy into a canvas and
 * its sum were 19 to 25 ms of the first craft's pick), so the first craft of a flight only builds its environment.
 */
export function prepareCraftSky(): void {
  craftSkySourceOnce().catch(() => undefined);
}

async function loadCraftSky(renderer: THREE.WebGLRenderer, scene: THREE.Scene, alive: () => boolean): Promise<CraftSkyLight | null> {
  const { copy, integral, rotation, galaxy } = await craftSkySourceOnce();
  // craft-compile-one: the environment is built on a frame of its own, not in the task that mounts the layer at the pick
  await nextFrame();
  if (!alive()) return null;
  const tex = new THREE.CanvasTexture(copy);
  tex.mapping = THREE.EquirectangularReflectionMapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromEquirectangular(tex).texture;
  pmrem.dispose();
  tex.dispose();
  scene.environmentRotation.setFromRotationMatrix(rotation);
  return { integral, galaxy };
}

/**
 * One frame: the outgoing craft, if any, rendered and copied into its own canvas; then the craft shown (or a clear canvas
 * while it loads). True when the craft shown was drawn.
 */
function drawFrame(gpu: CraftGpu, camera: FlightCamera, candidates: readonly LightCandidate[], steer: Steer | undefined, framing: CraftFraming | undefined, glCanvas: HTMLCanvasElement | null, outCanvas: HTMLCanvasElement | null): boolean {
  if (gpu.leaving && outCanvas && glCanvas) {
    if (drawCraft(gpu, gpu.leaving, camera, candidates, steer, framing)) {
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
  return drawCraft(gpu, gpu.shown, camera, candidates, steer, framing);
}

function drawCraft(gpu: CraftGpu, shown: Shown, flightCamera: FlightCamera, candidates: readonly LightCandidate[], steer?: Steer, framing?: CraftFraming): boolean {
  // phone-star-open-one: drawn through the steering frame's shift, as the stars are
  const camera = steer?.shiftPx ? arrivedFlightCamera(flightCamera, steer.shiftPx) : flightCamera;
  // craft-alive-one: while steered, the craft seen from behind along its line of flight, framed to a third of the
  // frame's height and falling back with speed, then leaning and banking about its own centre (craftAlive.ts)
  const alive = steer?.alive && steer.craftRows ? steer.alive : null;
  // craft-alive-shoulder-one (DECIDED-BY-LEAD 25 Nov 06:40): over the shoulder, sized by its height to a third of the
  // frame at rest and set at the chase's share of the width, the star ahead in the open side
  const axes = alive ? chaseAxes(shown.craft.flightForward, shown.craft.flightUp) : null;
  const seen = axes ? { ...shown.craft, modelForward: axes.forward, modelUp: axes.up } : shown.craft;
  const frameNow = alive && axes ? aliveFraming(framing ?? laptopCraftFraming(camera), alive, { viewportWidthPx: camera.viewportWidthPx, diameterPerHeight: diameterPerHeight(shown.frameRadius, shown.cached.size, axes.up) }) : framing;
  const scene = craftSceneOf(camera, seen, shown.cached.centre, shown.cached.radius, steer, frameNow, shown.frameRadius);
  if (!scene) return false;
  if (gpu.holder.children.length !== 1 || gpu.holder.children[0] !== shown.cached.model) {
    gpu.holder.clear();
    gpu.holder.add(shown.cached.model);
  }
  const modelMatrix = alive && steer?.craftRows ? rotatedAboutCentre(scene.modelMatrix, shown.cached.centre, aliveRotation(shown.craft.kind, steer.craftRows, alive)) : scene.modelMatrix;
  gpu.holder.matrix.fromArray(modelMatrix);
  gpu.lastBox = screenBoxOf(modelMatrix, scene.clipMatrix, shown.cached.centre, shown.cached.size, camera.viewportWidthPx, camera.viewportHeightPx);
  gpu.holder.matrixWorldNeedsUpdate = true;
  gpu.camera.projectionMatrix.fromArray(scene.clipMatrix);
  gpu.camera.projectionMatrixInverse.copy(gpu.camera.projectionMatrix).invert();
  // The key: the brightest real source where the camera stands, from its true direction, in its catalog tint
  const at = steer ? steer.positionPc : camera.observerPc;
  const key = keyLight(at, at, candidates);
  const toKey: Vec3 = key.direction.every(Number.isFinite) ? [key.direction[0], key.direction[1], key.direction[2]] : scene.toSun;
  const c = new THREE.Vector3(scene.modelMatrix[12], scene.modelMatrix[13], scene.modelMatrix[14]);
  gpu.key.color.setRGB(key.tint[0], key.tint[1], key.tint[2], THREE.LinearSRGBColorSpace);
  gpu.key.position.copy(c).addScaledVector(new THREE.Vector3(...toKey), 10 * shown.cached.radius + 1);
  gpu.key.target.position.copy(c);
  gpu.key.target.updateMatrixWorld();
  // craft-light-two: the sky's fill, the Milky Way's brightest sky as a second light, the meter counting both
  // chase-light-one: the edge light while the key is behind the craft
  // craft-fill-eight-one: the fill at the key's ratio, eight to one far from any star, sixteen where it shines as the Sun on Earth
  const look = craftLighting({ craftFromCameraM: [c.x, c.y, c.z], keyDirection: toKey, sky: gpu.skyLight, keyToFill: keyToFillOf(key) });
  const edge = edgeLightColor([c.x, c.y, c.z], toKey, key.tint);
  gpu.edge.uEdgeColor.value.setRGB(edge[0], edge[1], edge[2], THREE.LinearSRGBColorSpace);
  gpu.scene.environmentIntensity = look.environmentIntensity;
  gpu.renderer.toneMappingExposure = look.exposure;
  gpu.galaxy.intensity = look.galaxy ? look.galaxy.intensity : 0;
  if (look.galaxy) {
    const g = look.galaxy;
    gpu.galaxy.color.setRGB(g.tint[0], g.tint[1], g.tint[2], THREE.LinearSRGBColorSpace);
    gpu.galaxy.position.copy(c).addScaledVector(new THREE.Vector3(g.direction[0], g.direction[1], g.direction[2]), 10 * shown.cached.radius + 1);
    gpu.galaxy.target.position.copy(c);
    gpu.galaxy.target.updateMatrixWorld();
  }
  // craft-alive-two: the suit's poses blended by the same signals, on its bones before this render; while not steered
  // the stance its file was modelled in
  const weights = alive ? poseWeights(alive) : null;
  if (shown.cached.poses) setPoses(shown.cached.poses, weights);
  gpu.lastPoses = posesReport(shown.cached.poses, weights);
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
  steer?: Steer | null;
  /** Where the screen frames the craft; the laptop's (D26 item 2) by default. */
  framing?: CraftFraming;
  /** At phone width the hangar's far level is drawn. */
  phone?: boolean;
  /**
   * steer-frame-one: filled with this layer's draw for one steering frame, which the star layer's one frame loop calls
   * each frame with the camera of that frame (no React render per frame).
   */
  drawRef?: React.MutableRefObject<((steer: Steer) => void) | null>;
  /** Stars the key may come from besides the Sun: the star flown to (craftLight.ts lightCandidateOfFinding). */
  keyCandidates?: readonly LightCandidate[];
}> = ({ camera, craft, load, makeRenderer = makeWebGlRenderer, steer = null, framing, phone = false, drawRef, keyCandidates = NO_CANDIDATES }) => {
  const canvas = useRef<HTMLCanvasElement>(null);
  const gpu = useRef<CraftGpu | null>(null);
  const cameraRef = useRef(camera);
  cameraRef.current = camera;
  const steerRef = useRef(steer);
  steerRef.current = steer;
  const framingRef = useRef(framing);
  framingRef.current = framing;
  const candidatesRef = useRef(keyCandidates);
  candidatesRef.current = keyCandidates;
  const [state, setState] = useState<CraftState>('loading');
  const dpr = typeof window === 'undefined' ? 1 : Math.min(2, window.devicePixelRatio || 1);
  // craft-lod-one: the framed craft's span in device pixels picks its level, once a mount, never switched in flight;
  // craft-frame-share-one: the laptop's framed size as the window frames it (laptopFrameShare)
  const [spanDevicePx] = useState(() => (framing?.diameterPx ?? laptopCraftFraming(camera).diameterPx) * dpr);
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
  // craft-alive-shoulder-one: the layer says where the craft it drew last stands (x0,y0,x1,y1 in CSS pixels), and
  // craft-alive-held-keys: which pose it drew, the chase's (seen from behind, alive) or the rest's
  const root = useRef<HTMLDivElement>(null);
  const tellDrawn = (g: CraftGpu, s: Steer | undefined) => {
    const b = g.lastBox;
    if (b) root.current?.setAttribute('data-box', [b.x0, b.y0, b.x1, b.y1].map((v) => v.toFixed(1)).join(','));
    root.current?.setAttribute('data-pose', s?.alive && s.craftRows ? 'chase' : 'rest');
    if (g.shown) root.current?.setAttribute('data-fill-ao', g.shown.cached.fillAo);
    if (g.lastPoses) root.current?.setAttribute('data-poses', g.lastPoses);
  };
  const draw = (g: CraftGpu): boolean => {
    const s = steerRef.current ?? undefined;
    const drawn = drawFrame(g, cameraRef.current, candidatesRef.current, s, framingRef.current, canvas.current, outCanvas.current);
    if (drawn) tellDrawn(g, s);
    return drawn;
  };

  // One renderer, scene, camera, light and sky for the whole flight; on leaving, every model and the loader go too
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
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    const scene = new THREE.Scene();
    const cam = new THREE.Camera();
    cam.matrixAutoUpdate = false;
    const holder = new THREE.Group();
    holder.matrixAutoUpdate = false;
    scene.add(holder);
    const key = new THREE.DirectionalLight(0xffffff, 1);
    scene.add(key, key.target);
    const galaxy = new THREE.DirectionalLight(0xffffff, 0);
    scene.add(galaxy, galaxy.target);
    // A sky that fails (or a renderer that is not three's) leaves the craft lit by its key alone
    let alive = true;
    const sky = renderer instanceof THREE.WebGLRenderer ? loadCraftSky(renderer, scene, () => alive).catch(() => null) : Promise.resolve(null);
    gpu.current = { renderer, scene, camera: cam, holder, key, galaxy, edge: createEdgeLightUniforms(), sky, skyLight: null, shown: null, leaving: null, ready: new WeakMap() };
    const cache = models.current;
    return () => {
      alive = false;
      scene.environment?.dispose();
      gpu.current = null;
      for (const pending of cache.values()) void pending.then(({ model }) => disposeModel(model), () => {});
      cache.clear();
      loader.current?.dispose();
      loader.current = null;
      renderer.dispose();
    };
  }, [dpr, makeRenderer]);

  /**
   * Beyond the cache's size the models used longest ago go, their GPU memory freed; never one on screen, shown or framing
   * out (craft-cache-pin-one). At most two are on screen, so an older one can always go: the cache keeps its size, and
   * the model just asked for, the newest, is never reached.
   */
  const trimCache = (g: CraftGpu) => {
    const cache = models.current;
    const inUse = new Set([g.shown?.url, g.leaving?.url]);
    for (const [oldUrl, oldest] of [...cache]) {
      if (cache.size <= MODEL_CACHE_SIZE) break;
      if (inUse.has(oldUrl)) continue;
      cache.delete(oldUrl);
      void oldest.then(({ model }) => disposeModel(model), () => {});
    }
  };

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
    const url = craftModelUrl(craft, phone, spanDevicePx);
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
        // craft-ao-fill-one: the sky fill's occlusion where the model carries it, and chase-light-one: the edge light in
        // each standard material, both before the model's programs are made
        const occlusion = markFillOcclusion(model);
        addEdgeLight(model, g.edge);
        const box = new THREE.Box3().setFromObject(model);
        const sphere = box.getBoundingSphere(new THREE.Sphere());
        const size = box.getSize(new THREE.Vector3());
        return { model, centre: [sphere.center.x, sphere.center.y, sphere.center.z] as [number, number, number], radius: sphere.radius, size: [size.x, size.y, size.z] as Vec3, fillAo: `${occlusion.occluded}/${occlusion.meshes}`, poses: posesOf(model) };
      });
    }
    cache.set(url, pending);
    trimCache(g);
    setState('loading');
    // The craft is drawn once both its model and the flight's sky are in
    // craft-compile-one: shown only once its programs and textures are made, so the frame-in is painted from its start
    Promise.all([pending, g.sky])
      .then(([cached, skyLight]) => readyOnGpu(g, cached.model).then(() => [cached, skyLight] as const))
      .then(
      ([cached, skyLight]) => {
        if (!current || gpu.current !== g) return;
        g.skyLight = skyLight;
        g.shown = { cached, craft, url, frameRadius: craftFrameRadiusM(craft, cached.size, cached.radius) };
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
  }, [camera, steer, framing?.xPx, framing?.yPx, framing?.diameterPx, keyCandidates]);

  // The star layer's frame loop draws a steering frame through this
  useEffect(() => {
    if (!drawRef) return undefined;
    drawRef.current = (s) => {
      const g = gpu.current;
      if (g && (g.shown || g.leaving) && drawFrame(g, cameraRef.current, candidatesRef.current, s, framingRef.current, canvas.current, outCanvas.current)) tellDrawn(g, s);
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
    <div ref={root} className="flight-craft" data-testid="flight-craft" data-craft={craft?.id ?? 'none'} data-state={state} data-framing={framing ? `${framing.xPx.toFixed(1)},${framing.yPx.toFixed(1)}` : undefined} aria-hidden="true" style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
      <div ref={inFrame} className="flight-craft__in" style={{ position: 'absolute', inset: 0 }}>
        <canvas ref={canvas} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', display: 'block' }} />
      </div>
      {leavingKey !== null && (
        <canvas key={leavingKey} ref={outCanvas} className="flight-craft__out" data-testid="flight-craft-out" onAnimationEnd={leaveDone} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', display: 'block' }} />
      )}
    </div>
  );
};
