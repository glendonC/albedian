import {
  ClampToEdgeWrapping,
  Group,
  LinearFilter,
  Mesh,
  NoColorSpace,
  PerspectiveCamera,
  RepeatWrapping,
  SRGBColorSpace,
  Scene,
  SphereGeometry,
  TextureLoader,
  Timer,
  Vector3,
  type ColorSpace,
  type Texture,
} from 'three/webgpu';
import { uniform } from 'three/tsl';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import type { RenderLayerSet } from '@albedian/contracts';
import type { CosmosRenderer, ShowcaseSceneKey } from '../cosmosRenderer.js';
import type { SpatialScaleRegime } from '../SparkPortalRenderer.js';
import { loadPanoramaRegistration } from '../sky/skyRegistration.js';
import { BRUNETON_ATMO_LUT_URL, createBrunetonLut } from './brunetonAtmosphere.js';
import { createCinemaOpticsUniforms } from './cinemaOptics.js';
import { createAtmosphereShellMaterial, createEarthMaterial, createMoonMaterial } from './cislunar/bodyMaterials.js';
import { CISLUNAR_RENDER_LAYERS } from './cislunar/cislunarLayers.js';
import { createSkyBackgroundNode } from './cislunar/skyBackground.js';
import { buildCosmosPostChain, DEFAULT_COSMOS_POST, type CosmosPostChain, type CosmosPostOptions } from './postChain.js';
import { CatalogStarSpriteField } from './stars/CatalogStarSpriteField.js';
import type { FrameTimeBenchOptions, FrameTimeBenchRun } from './frameTimeBench.js';
import { WebGpuHost, captureToPngBlob, type HostCapture, type WebGpuHostBackend } from './WebGpuHost.js';

/**
 * Scene layout of the cislunar landing regime, as built by SparkPortalRenderer.buildCislunarWorld.
 * The WebGL renderer places the regime at (0, 42, -160) with the camera rig at the same anchor;
 * only relative positions matter, so this host puts both at the origin.
 */
const EARTH = { radius: 13.5, center: new Vector3(-11.8, -16.8, -28.0), rotation: new Vector3(0.12, -1.52, -0.18) };
const ATMOSPHERE_RADIUS_FACTOR = 1.022;
const MOON = { radius: 1.98, center: new Vector3(2.45, 1.25, -8.2), yaw: -0.85 };
const CATALOG_STAR_MOUNT_RADIUS = 85.0;
const EARTH_SUN_DIR_VIEW = new Vector3(0.68, 0.28, -0.58).normalize();
const MOON_SUN_DIR_VIEW = new Vector3(0.58, 0.22, 0.78).normalize();

/**
 * The WebGL loop advanced these once per frame (0.00018 and 0.0005 rad, x3.2 on the landing);
 * here they are per second at the 60 Hz the values were tuned at, so the spin does not depend
 * on the display's refresh rate.
 */
const EARTH_SPIN_RAD_PER_S = 0.00018 * 60;
const MOON_SPIN_RAD_PER_S = 0.0005 * 60;
const SHOWCASE_SPIN_MULTIPLIER = 3.2;
/** Per-frame easing of 0.14 at 60 Hz, applied as 1 - 0.86^(60 dt). */
const LOOK_EASING_PER_60HZ_FRAME = 0.14;
const WARP_DECAY_PER_60HZ_FRAME = 0.85;

export interface CislunarWebGpuRendererOptions {
  forceWebGL?: boolean;
  /** Called on every scene jump with the regime shown, as SparkPortalRenderer does. */
  onRegimeChange?: (regime: SpatialScaleRegime) => void;
  /**
   * Publishes `window.__albedianCosmos` for automated captures and benchmarks, once the host runs.
   * A renderer disposed before then never publishes, so a disposed one cannot replace or delete
   * the live one's hook (React StrictMode builds the viewport's renderer twice in development).
   */
  exposeForAutomation?: boolean;
}

export interface CosmosAutomation {
  readonly backend: WebGpuHostBackend | null;
  ready: Promise<void>;
  /** Resolves when every texture, the Moon mesh and the catalog tiles for the view are loaded. */
  whenSettled(timeoutMs?: number): Promise<boolean>;
  capture(): Promise<HostCapture>;
  capturePngDataUrl(): Promise<string>;
  describePost(): string;
  /** Frame times with the spike harness's method (frameTimeBench.ts). */
  runBench(options?: Partial<FrameTimeBenchOptions>): Promise<FrameTimeBenchRun>;
  layers: RenderLayerSet;
  renderer: CislunarWebGpuRenderer;
}

declare global {
  interface Window {
    __albedianCosmos?: CosmosAutomation;
  }
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

/**
 * The cislunar landing scene on three's WebGPURenderer with TSL (migration step 5 of
 * docs/architecture/RENDERER_WEBGPU_SPIKE.md): the sky dome as a background node, Gaia DR3 and
 * Hipparcos 2007 tiles as analytic-PSF sprites, the Earth, its atmosphere shell and the Moon
 * as node materials, and the HDR post chain with AgX. Construction is synchronous; WebGPU
 * starts asynchronously, and calls made before it is up only set state.
 */
export class CislunarWebGpuRenderer implements CosmosRenderer {
  readonly ready: Promise<void>;
  private host: WebGpuHost | null = null;
  private post: CosmosPostChain | null = null;
  private postOptions: CosmosPostOptions = { ...DEFAULT_COSMOS_POST };
  private disposed = false;
  private readonly scene = new Scene();
  private readonly camera = new PerspectiveCamera(56, 16 / 9, 0.05, 2800);
  private readonly rig = new Group();
  private readonly world = new Group();
  private readonly stars = new CatalogStarSpriteField();
  private readonly optics = createCinemaOpticsUniforms();
  private readonly textureLoader = new TextureLoader();
  private readonly pending: Promise<unknown>[] = [];
  private readonly earth: Mesh;
  private readonly moon: Mesh;
  private readonly timer = new Timer();
  private cssSize = { width: 1600, height: 1000 };
  private heldClockSec: number | null = null;
  private elapsedSec = 0;
  private showcaseMode = false;
  private readonly look = { yaw: 0, pitch: 0, curYaw: 0, curPitch: 0 };
  private targetFov = 56;
  private curFov = 56;
  private readonly warned = new Set<string>();
  private readonly onRegimeChange: ((regime: SpatialScaleRegime) => void) | undefined;

  constructor(container: HTMLElement, options: CislunarWebGpuRendererOptions = {}) {
    this.camera.rotation.order = 'YXZ';
    this.rig.add(this.camera);
    this.scene.add(this.rig);
    this.scene.add(this.world);

    // ESO eso0932a; provenance in sky_texture_sources.json
    const milkyWay = this.loadTexture('/textures/eso_milkyway_panorama.jpg', SRGBColorSpace, true);
    // The dome is drawn only with the panorama's fitted registration, never misaligned
    this.pending.push(
      loadPanoramaRegistration().then(
        (registration) => {
          this.scene.backgroundNode = createSkyBackgroundNode(milkyWay, registration);
        },
        (error: unknown) => console.warn('[webgpu host] sky panorama registration failed; no sky dome', error)
      )
    );

    this.world.add(this.stars.createMount(CATALOG_STAR_MOUNT_RADIUS));

    const lutReady = uniform(0);
    const lutTexture = this.loadTexture(BRUNETON_ATMO_LUT_URL, NoColorSpace, false, () => (lutReady.value = 1));
    lutTexture.minFilter = LinearFilter;
    lutTexture.magFilter = LinearFilter;
    lutTexture.generateMipmaps = false;
    const lut = createBrunetonLut(lutTexture, lutReady);

    const earthSun = uniform(EARTH_SUN_DIR_VIEW.clone());
    this.earth = new Mesh(
      new SphereGeometry(EARTH.radius, 160, 160),
      createEarthMaterial(
        {
          // NASA Blue Marble (Nov 2004) and Black Marble 2016; provenance in earth_texture_sources.json
          day: this.loadTexture('/textures/earth_day_bluemarble_2004-11_8k.jpg', SRGBColorSpace, true),
          night: this.loadTexture('/textures/earth_night_blackmarble_2016_8k.jpg', SRGBColorSpace, true),
          specular: this.loadTexture('/textures/earth_specular.jpg', NoColorSpace, true),
          clouds: this.loadTexture('/textures/earth_clouds.png', SRGBColorSpace, true),
        },
        lut,
        earthSun
      )
    );
    this.earth.name = 'cislunarEarth';
    this.earth.position.copy(EARTH.center);
    this.earth.rotation.set(EARTH.rotation.x, EARTH.rotation.y, EARTH.rotation.z);
    this.world.add(this.earth);

    const atmosphere = new Mesh(
      new SphereGeometry(EARTH.radius * ATMOSPHERE_RADIUS_FACTOR, 160, 160),
      createAtmosphereShellMaterial(lut, earthSun)
    );
    atmosphere.name = 'cislunarAtmosphereShell';
    atmosphere.position.copy(EARTH.center);
    this.world.add(atmosphere);

    this.moon = new Mesh(
      new SphereGeometry(MOON.radius, 160, 160),
      createMoonMaterial(
        {
          albedo: this.loadTexture('/textures/lro_moon_2k.jpg', SRGBColorSpace, true),
          relief: this.loadTexture('/textures/lro_moon_disp.jpg', NoColorSpace, true),
        },
        uniform(MOON_SUN_DIR_VIEW.clone())
      )
    );
    this.moon.name = 'cislunarMoon';
    this.moon.position.copy(MOON.center);
    this.moon.rotation.y = MOON.yaw;
    this.world.add(this.moon);
    this.pending.push(this.loadMoonMesh());

    this.onRegimeChange = options.onRegimeChange;
    this.ready = this.start(container, options);
    // Logged here; callers that await `ready` still see the rejection
    this.ready.catch((error: unknown) => console.error('[webgpu host] failed to start', error));
  }

  get backend(): WebGpuHostBackend | null {
    return this.host?.backend ?? null;
  }

  getRenderLayers(): RenderLayerSet {
    return CISLUNAR_RENDER_LAYERS;
  }

  describePost(): string {
    return this.post?.describe() ?? 'not started';
  }

  /** Rebuilds the post chain with some options changed (look development and benchmarks). */
  setPostOptions(options: Partial<CosmosPostOptions>): void {
    this.postOptions = { ...this.postOptions, ...options };
    if (!this.host) return;
    this.post?.dispose();
    this.post = buildCosmosPostChain(this.host.renderer, this.scene, this.camera, this.postOptions, this.optics);
  }

  setObserverCoordinates(latDeg: number, _lonDeg: number): void {
    // As SparkPortalRenderer: turn the globe toward the observer's hemisphere, flip the Moon in the south
    const isSouthern = latDeg < 0;
    this.moon.rotation.z = isSouthern ? Math.PI * 0.85 : 0.0;
    if (isSouthern) this.earth.rotation.set(-0.88, -1.15, 0.18);
    else this.earth.rotation.set(-0.55, 1.68, -0.24);
  }

  setShowcaseMode(enabled: boolean): void {
    this.showcaseMode = enabled;
    if (!enabled) {
      this.look.yaw = 0;
      this.look.pitch = 0;
      this.targetFov = 56;
    }
  }

  transitionToShowcaseScene(sceneKey: ShowcaseSceneKey, instant = false): void {
    if (sceneKey !== 'cislunar') {
      this.warnOnce(`scene:${sceneKey}`, `showcase scene ${sceneKey} is not ported to the WebGPU host`);
      return;
    }
    this.rig.position.set(0, 0, 0);
    this.rig.rotation.set(0, 0, 0);
    Object.assign(this.look, { yaw: 0, pitch: 0, curYaw: 0, curPitch: 0 });
    this.camera.rotation.set(0, 0, 0);
    this.targetFov = 56;
    this.curFov = 56;
    if (!instant) {
      this.curFov = 61;
      this.optics.warpFactor.value = 0.48;
    }
    // The WebGL renderer reports every regime jump; the audio engine follows it
    this.onRegimeChange?.('cislunar');
  }

  holdClock(seconds: number | null): void {
    this.heldClockSec = seconds;
  }

  dragLook(deltaPixelsX: number, deltaPixelsY: number): void {
    const sensitivity = 0.0032 * (this.curFov / 56);
    this.look.yaw -= deltaPixelsX * sensitivity;
    this.look.pitch = Math.max(-1.15, Math.min(1.15, this.look.pitch - deltaPixelsY * sensitivity));
    this.moon.rotation.y += deltaPixelsX * 0.004;
    this.moon.rotation.x = Math.max(-0.6, Math.min(0.6, this.moon.rotation.x + deltaPixelsY * 0.003));
  }

  zoomByWheel(deltaY: number): void {
    const step = Math.max(-120, Math.min(120, deltaY)) * 0.045;
    this.targetFov = Math.max(18, Math.min(74, this.targetFov + step));
    const forward = new Vector3(0, 0, -1).applyQuaternion(this.camera.quaternion);
    this.rig.position.addScaledVector(forward, -step * 0.22);
  }

  nudgeMovement(dx: number, dy: number, dz: number): void {
    const forward = new Vector3(0, 0, 1).applyQuaternion(this.camera.quaternion);
    const right = new Vector3(1, 0, 0).applyQuaternion(this.camera.quaternion);
    this.rig.position.addScaledVector(right, dx);
    this.rig.position.addScaledVector(new Vector3(0, 1, 0), dy);
    this.rig.position.addScaledVector(forward, dz);
  }

  nudgeLook(dYaw: number, dPitch: number): void {
    this.look.yaw += dYaw;
    this.look.pitch = Math.max(-1.15, Math.min(1.15, this.look.pitch + dPitch));
  }

  resize(width: number, height: number): void {
    if (width <= 0 || height <= 0) return;
    this.cssSize = { width, height };
    this.host?.resize(width, height);
  }

  /**
   * Advances the scene by `dtSec` and renders. `clockSec` sets the scene clock outright (as a
   * held clock does) for benchmarks and captures that must be repeatable.
   */
  renderFrame(dtSec: number, clockSec: number | null = this.heldClockSec): void {
    if (!this.host || !this.post) return;
    this.step(dtSec, clockSec);
    this.post.pipeline.render();
  }

  /**
   * One frame outside the animation loop (benchmarks, video export): advances three's node
   * frame first, which the loop otherwise does, then steps and renders.
   */
  renderOffscreenFrame(dtSec: number, clockSec: number | null): void {
    if (!this.host || !this.post) return;
    this.host.advanceNodeFrame();
    this.renderFrame(dtSec, clockSec);
  }

  /** Reads back one complete frame at the drawing-buffer size, at the current (or held) clock. */
  async capture(): Promise<HostCapture> {
    await this.ready;
    if (!this.host || !this.post) throw new Error('WebGPU host is not running');
    const post = this.post;
    return this.host.capture(() => {
      this.step(0, this.heldClockSec);
      post.pipeline.render();
    });
  }

  /** Stops the animation loop (benchmarks drive frames themselves); `resumeLoop` restarts it. */
  pauseLoop(): void {
    this.host?.stop();
  }

  resumeLoop(): void {
    this.timer.reset();
    this.host?.start((timeMs) => this.onAnimationFrame(timeMs));
  }

  get webGpuHost(): WebGpuHost | null {
    return this.host;
  }

  get catalogStars(): CatalogStarSpriteField {
    return this.stars;
  }

  /** Updates the camera and the tile view for the current pose without rendering. */
  syncView(force = false): void {
    if (!this.host) return;
    this.applyCamera();
    this.stars.update(this.camera, this.host.drawingBufferSize.height, performance.now(), force);
  }

  async whenSettled(timeoutMs = 30_000): Promise<boolean> {
    await this.ready;
    const deadline = performance.now() + timeoutMs;
    await Promise.race([
      Promise.allSettled(this.pending),
      new Promise((resolve) => setTimeout(resolve, timeoutMs)),
    ]);
    await this.stars.ready;
    for (;;) {
      this.syncView(true);
      await new Promise((resolve) => setTimeout(resolve, 100));
      if (this.stars.isSettled()) return true;
      if (performance.now() > deadline) return false;
    }
  }

  dispose(): void {
    this.disposed = true;
    this.timer.dispose();
    this.stars.dispose();
    this.post?.dispose();
    this.host?.dispose();
    this.host = null;
    if (typeof window !== 'undefined' && window.__albedianCosmos?.renderer === this) {
      delete window.__albedianCosmos;
    }
  }

  private async start(container: HTMLElement, options: CislunarWebGpuRendererOptions): Promise<void> {
    const host = await WebGpuHost.create(container, { forceWebGL: options.forceWebGL === true });
    if (this.disposed) {
      host.dispose();
      return;
    }
    this.host = host;
    host.onResize(() => {
      this.camera.aspect = host.aspect;
      this.camera.updateProjectionMatrix();
    });
    host.resize(this.cssSize.width, this.cssSize.height);
    this.post = buildCosmosPostChain(host.renderer, this.scene, this.camera, this.postOptions, this.optics);
    if (typeof document !== 'undefined') this.timer.connect(document);
    this.resumeLoop();
    if (options.exposeForAutomation && typeof window !== 'undefined') {
      window.__albedianCosmos = this.automation();
    }
    console.info(`[webgpu host] cislunar scene on ${host.backend}; post: ${this.post.describe()}`);
  }

  private onAnimationFrame(timeMs: number): void {
    this.timer.update(timeMs);
    this.renderFrame(Math.min(0.1, this.timer.getDelta()));
  }

  private step(dtSec: number, clockSec: number | null): void {
    const held = clockSec !== null;
    if (!held) this.elapsedSec += dtSec;
    const t = clockSec ?? this.elapsedSec;

    // A held clock holds the spin too, as in the WebGL renderer, so the pose depends on t alone
    const spin = held ? 0 : (this.showcaseMode ? SHOWCASE_SPIN_MULTIPLIER : 1) * dtSec;
    this.earth.rotation.y += EARTH_SPIN_RAD_PER_S * spin;
    this.moon.rotation.y += MOON_SPIN_RAD_PER_S * spin;

    // Showcase drift of the landing camera, as in SparkPortalRenderer's loop
    if (this.showcaseMode) {
      this.look.yaw = Math.sin(t * 0.42) * 0.095;
      this.look.pitch = Math.cos(t * 0.29) * 0.042;
      this.targetFov = 53 + Math.sin(t * 0.35) * 2.4;
    }
    // A held clock has no frame history, so the eased values sit on their targets
    const k = held ? 1 : 1 - Math.pow(1 - LOOK_EASING_PER_60HZ_FRAME, 60 * dtSec);
    this.look.curYaw += (this.look.yaw - this.look.curYaw) * k;
    this.look.curPitch += (this.look.pitch - this.look.curPitch) * k;
    this.curFov += (this.targetFov - this.curFov) * k;
    this.optics.warpFactor.value = held ? 0 : this.optics.warpFactor.value * Math.pow(WARP_DECAY_PER_60HZ_FRAME, 60 * dtSec);

    this.applyCamera();
    if (this.host) this.stars.update(this.camera, this.host.drawingBufferSize.height);
  }

  private applyCamera(): void {
    this.camera.rotation.y = this.look.curYaw;
    this.camera.rotation.x = this.look.curPitch;
    if (this.host) this.camera.aspect = this.host.aspect;
    this.camera.fov = this.curFov;
    this.camera.updateProjectionMatrix();
    this.camera.updateMatrixWorld();
  }

  /**
   * Starts loading a texture and returns it at once; materials bind it now and three uploads the
   * image when it arrives. `wrapU` repeats across the u seam of equirectangular maps.
   */
  private loadTexture(url: string, colorSpace: ColorSpace, wrapU: boolean, onLoad?: () => void): Texture {
    let resolveLoad!: () => void;
    let rejectLoad!: (error: unknown) => void;
    this.pending.push(
      new Promise<void>((resolve, reject) => {
        resolveLoad = resolve;
        rejectLoad = reject;
      })
    );
    const tex = this.textureLoader.load(
      url,
      () => {
        onLoad?.();
        resolveLoad();
      },
      undefined,
      (error) => {
        console.warn(`[webgpu host] texture ${url} failed`, error);
        rejectLoad(error);
      }
    );
    tex.colorSpace = colorSpace;
    tex.wrapS = wrapU ? RepeatWrapping : ClampToEdgeWrapping;
    tex.wrapT = ClampToEdgeWrapping;
    return tex;
  }

  private loadMoonMesh(): Promise<void> {
    // NASA LRO Moon mesh at radius 10, rescaled to the scene Moon as the WebGL path does
    return new GLTFLoader().loadAsync('/models/nasa_lro_moon_3d.glb').then(
      (gltf) => {
        gltf.scene.traverse((child) => {
          const mesh = child as Mesh;
          if (!mesh.isMesh) return;
          this.moon.geometry.dispose();
          this.moon.geometry = mesh.geometry.clone();
          this.moon.geometry.scale(MOON.radius / 10, MOON.radius / 10, MOON.radius / 10);
        });
      },
      (error: unknown) => console.warn('[webgpu host] Moon mesh failed; keeping the sphere', error)
    );
  }

  private warnOnce(key: string, message: string): void {
    if (this.warned.has(key)) return;
    this.warned.add(key);
    console.warn(`[webgpu host] ${message}`);
  }

  private automation(): CosmosAutomation {
    const renderer = this;
    return {
      get backend() {
        return renderer.backend;
      },
      ready: this.ready,
      whenSettled: (timeoutMs?: number) => this.whenSettled(timeoutMs),
      capture: () => this.capture(),
      capturePngDataUrl: async () => blobToDataUrl(await captureToPngBlob(await this.capture())),
      describePost: () => this.describePost(),
      runBench: async (options) => {
        const bench = await import('./frameTimeBench.js');
        return bench.runFrameTimeBench(this, { ...bench.DEFAULT_FRAME_TIME_BENCH, ...options });
      },
      layers: CISLUNAR_RENDER_LAYERS,
      renderer,
    };
  }
}
