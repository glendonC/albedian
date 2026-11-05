import {
  ClampToEdgeWrapping,
  Group,
  LinearFilter,
  Mesh,
  NoColorSpace,
  PerspectiveCamera,
  Quaternion,
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
import type { RenderLayerSet } from '@albedian/contracts';
import { cameraSkyPose } from '../cameraSkyPose.js';
import type { CameraSkyPose, CosmosRenderer, ShowcaseSceneKey } from '../cosmosRenderer.js';
import type { SpatialScaleRegime } from '../SparkPortalRenderer.js';
import { loadPanoramaRegistration } from '../sky/skyRegistration.js';
import { centreOnCamera } from '../skyMounts.js';
import { BRUNETON_ATMO_LUT_URL, createBrunetonLut } from './brunetonAtmosphere.js';
import { createCinemaOpticsUniforms } from './cinemaOptics.js';
import { createAtmosphereShellMaterial, createEarthMaterial, createMoonMaterial } from './cislunar/bodyMaterials.js';
import { CISLUNAR_RENDER_LAYERS } from './cislunar/cislunarLayers.js';
import { EARTH_RADIUS_KM, KM_PER_UNIT, MOON_RADIUS_KM, landingPose } from './cislunar/landingScene.js';
import { createSkyBackgroundNode } from './cislunar/skyBackground.js';
import { buildCosmosPostChain, DEFAULT_COSMOS_POST, type CosmosPostChain, type CosmosPostOptions } from './postChain.js';
import { CatalogStarSpriteField, DEFAULT_CATALOG_STAR_EXPOSURE } from './stars/CatalogStarSpriteField.js';
import type { FrameTimeBenchOptions, FrameTimeBenchRun } from './frameTimeBench.js';
import { WebGpuHost, captureToPngBlob, type HostCapture, type WebGpuHostBackend } from './WebGpuHost.js';

/**
 * The landing at one real moment, at true scale (cislunar/landingScene.ts): the Earth at the
 * origin and the Moon where JPL Horizons puts them at 2025-03-07 11:00 UTC, turned by their IAU
 * rotation models and lit by that moment's Sun, with the camera rig beyond the Moon on the line
 * from the galactic centre through the Earth. One scene unit is 1000 km. The moment is held: the
 * bodies do not spin, and nothing the viewer does turns them.
 */
const LANDING = landingPose();
const ATMOSPHERE_RADIUS_FACTOR = 1.022;
const CATALOG_STAR_MOUNT_RADIUS = 85.0;
/** Vertical field of view of the landing: the Moon fills about 11.6 deg of it, the Earth 1.85. */
const LANDING_FOV_DEG = 26;
/** The narrowest wheel zoom, wide enough to keep the Moon's relief at the map's resolution. */
const MIN_FOV_DEG = 6;
/**
 * Exposure of the landing and the display gain of the sky panorama. With the Moon at its true
 * albedo, the camera exposes for the sunlit bodies; the Milky Way is then shown far brighter than
 * one exposure could record it, and dimmed from its usual grade so the bodies stay brightest.
 */
const LANDING_EXPOSURE = 3.0;
const LANDING_SKY_GAIN = 0.3;
/** Sharpens the 8k colour and the normal map toward the limb, where the sphere is seen edge-on. */
const MOON_MAP_ANISOTROPY = 8;
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
  /** The catalog stars belong to the sky, so they take its display gain under the landing's exposure */
  private readonly stars = new CatalogStarSpriteField({ exposure: DEFAULT_CATALOG_STAR_EXPOSURE * LANDING_SKY_GAIN });
  /** The catalog star sphere, centred on the camera every frame so the stars sit at infinity. */
  private readonly starMount: Group;
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
  private targetFov = LANDING_FOV_DEG;
  private curFov = LANDING_FOV_DEG;
  /** The Sun's direction in view space, for the Earth and its atmosphere; set every frame. */
  private readonly earthSunView = uniform(new Vector3(0, 0, 1));
  private readonly cameraWorldQuaternion = new Quaternion();
  private readonly warned = new Set<string>();
  private readonly onRegimeChange: ((regime: SpatialScaleRegime) => void) | undefined;

  constructor(container: HTMLElement, options: CislunarWebGpuRendererOptions = {}) {
    this.camera.rotation.order = 'YXZ';
    this.rig.add(this.camera);
    this.scene.add(this.rig);
    this.placeRig();
    this.scene.add(this.world);

    // ESO eso0932a; provenance in textures/PROVENANCE.json
    const milkyWay = this.loadTexture('/textures/eso_milkyway_panorama.jpg', SRGBColorSpace, true);
    // The dome is drawn only with the panorama's fitted registration, never misaligned
    this.pending.push(
      loadPanoramaRegistration().then(
        (registration) => {
          this.scene.backgroundNode = createSkyBackgroundNode(milkyWay, registration, LANDING_SKY_GAIN);
        },
        (error: unknown) => console.warn('[webgpu host] sky panorama registration failed; no sky dome', error)
      )
    );

    this.starMount = this.stars.createMount(CATALOG_STAR_MOUNT_RADIUS);
    this.world.add(this.starMount);

    const lutReady = uniform(0);
    const lutTexture = this.loadTexture(BRUNETON_ATMO_LUT_URL, NoColorSpace, false, () => (lutReady.value = 1));
    lutTexture.minFilter = LinearFilter;
    lutTexture.magFilter = LinearFilter;
    lutTexture.generateMipmaps = false;
    const lut = createBrunetonLut(lutTexture, lutReady);

    const earthSun = this.earthSunView;
    this.earth = new Mesh(
      new SphereGeometry(EARTH_RADIUS_KM / KM_PER_UNIT, 160, 160),
      createEarthMaterial(
        {
          // NASA Blue Marble (Nov 2004) and Black Marble 2016; provenance in textures/PROVENANCE.json
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
    this.earth.position.copy(LANDING.earth.position);
    this.earth.quaternion.copy(LANDING.earth.quaternion);
    this.world.add(this.earth);

    const atmosphere = new Mesh(
      new SphereGeometry((EARTH_RADIUS_KM / KM_PER_UNIT) * ATMOSPHERE_RADIUS_FACTOR, 160, 160),
      createAtmosphereShellMaterial(lut, earthSun)
    );
    atmosphere.name = 'cislunarAtmosphereShell';
    atmosphere.position.copy(LANDING.earth.position);
    this.world.add(atmosphere);

    // NASA SVS 4720 colour and LOLA normals, baked by scripts/bake_moon_maps.py
    const moonColour = this.loadTexture('/textures/moon_svs4720_color_8k.jpg', SRGBColorSpace, true);
    const moonNormal = this.loadTexture('/textures/moon_svs4720_lola_normal_16ppd.webp', NoColorSpace, true);
    moonColour.anisotropy = MOON_MAP_ANISOTROPY;
    moonNormal.anisotropy = MOON_MAP_ANISOTROPY;
    this.moon = new Mesh(
      new SphereGeometry(MOON_RADIUS_KM / KM_PER_UNIT, 256, 128),
      createMoonMaterial({ colour: moonColour, normal: moonNormal }, uniform(LANDING.moon.sunDirection.clone()))
    );
    this.moon.name = 'cislunarMoon';
    this.moon.position.copy(LANDING.moon.position);
    this.moon.quaternion.copy(LANDING.moon.quaternion);
    this.world.add(this.moon);

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

  /**
   * The WebGL renderer turns its composed globe toward the observer's hemisphere. This landing
   * shows the Earth and the Moon as they were at its moment, so where the viewer is turns nothing.
   */
  setObserverCoordinates(_latDeg: number, _lonDeg: number): void {}

  setShowcaseMode(enabled: boolean): void {
    this.showcaseMode = enabled;
    if (!enabled) {
      this.look.yaw = 0;
      this.look.pitch = 0;
      this.targetFov = LANDING_FOV_DEG;
    }
  }

  transitionToShowcaseScene(sceneKey: ShowcaseSceneKey, instant = false): void {
    if (sceneKey !== 'cislunar') {
      this.warnOnce(`scene:${sceneKey}`, `showcase scene ${sceneKey} is not ported to the WebGPU host`);
      return;
    }
    this.placeRig();
    Object.assign(this.look, { yaw: 0, pitch: 0, curYaw: 0, curPitch: 0 });
    this.camera.rotation.set(0, 0, 0);
    this.targetFov = LANDING_FOV_DEG;
    this.curFov = LANDING_FOV_DEG;
    if (!instant) {
      this.curFov = LANDING_FOV_DEG + 5;
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
  }

  zoomByWheel(deltaY: number): void {
    const step = Math.max(-120, Math.min(120, deltaY)) * 0.045;
    this.targetFov = Math.max(MIN_FOV_DEG, Math.min(74, this.targetFov + step));
    const forward = new Vector3(0, 0, -1).applyQuaternion(this.camera.getWorldQuaternion(this.cameraWorldQuaternion));
    this.rig.position.addScaledVector(forward, -step * 0.22);
  }

  nudgeMovement(dx: number, dy: number, dz: number): void {
    const turn = this.camera.getWorldQuaternion(this.cameraWorldQuaternion);
    this.rig.position.addScaledVector(new Vector3(1, 0, 0).applyQuaternion(turn), dx);
    this.rig.position.addScaledVector(new Vector3(0, 1, 0).applyQuaternion(this.rig.quaternion), dy);
    this.rig.position.addScaledVector(new Vector3(0, 0, 1).applyQuaternion(turn), dz);
  }

  /** Puts the camera rig at the landing's viewpoint, looking between the Earth and the Moon. */
  private placeRig(): void {
    this.rig.position.copy(LANDING.camera.position);
    this.rig.quaternion.copy(LANDING.camera.quaternion);
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

  /** The camera's pose against the catalog star sphere, for the audio listener; the rig's turn included. */
  getCameraSkyPose(): CameraSkyPose {
    return cameraSkyPose(this.camera, this.starMount, this.stars.getLimitingMagnitude());
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
    host.renderer.toneMappingExposure = LANDING_EXPOSURE;
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

    // Showcase drift of the landing camera, as in SparkPortalRenderer's loop, scaled from its 56 deg view
    if (this.showcaseMode) {
      const scale = LANDING_FOV_DEG / 56;
      this.look.yaw = Math.sin(t * 0.42) * 0.095 * scale;
      this.look.pitch = Math.cos(t * 0.29) * 0.042 * scale;
      this.targetFov = (53 + Math.sin(t * 0.35) * 2.4) * scale;
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
    // The rig carries the landing's viewpoint; the camera's own turn is the look offset
    this.rig.updateMatrixWorld(true);
    centreOnCamera(this.starMount, this.camera);
    // The Earth's materials light in view space; the Sun stays put in the world as the view turns
    this.earthSunView.value.copy(LANDING.earth.sunDirection).transformDirection(this.camera.matrixWorldInverse);
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
