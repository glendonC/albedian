import {
  Group,
  PerspectiveCamera,
  Quaternion,
  Scene,
  TextureLoader,
  Timer,
  Vector3,
} from 'three/webgpu';
import { uniform } from 'three/tsl';
import type { RenderLayerSet } from '@albedian/contracts';
import { cameraSkyPose } from '../cameraSkyPose.js';
import type { CameraSkyPose, CosmosRenderer, ShowcaseSceneKey } from '../cosmosRenderer.js';
import type { SpatialScaleRegime } from '../SparkPortalRenderer.js';
import { centreOnCamera } from '../skyMounts.js';
import { createCinemaOpticsUniforms } from './cinemaOptics.js';
import { CISLUNAR_RENDER_LAYERS } from './cislunar/cislunarLayers.js';
import {
  CATALOG_STAR_MOUNT_RADIUS,
  LANDING_EXPOSURE,
  LANDING_FOV_DEG,
  LANDING_LIMITING_MAGNITUDE,
  LANDING_SKY_GAIN,
  MIN_FOV_DEG,
  populateCislunarWorld,
} from './cislunar/cislunarSceneNodes.js';
import { createCosmosAutomation, type CosmosAutomation } from './cislunar/cosmosAutomation.js';
import { landingCameraPositionAt, landingPose } from './cislunar/landingScene.js';
import { buildCosmosPostChain, DEFAULT_COSMOS_POST, type CosmosPostChain, type CosmosPostOptions } from './postChain.js';
import { CatalogStarSpriteField, DEFAULT_CATALOG_STAR_EXPOSURE } from './stars/CatalogStarSpriteField.js';
import { WebGpuHost, type HostCapture, type WebGpuHostBackend } from './WebGpuHost.js';

export { LANDING_LIMITING_MAGNITUDE };
export type { CosmosAutomation };

/**
 * The landing at one real moment, at true scale (cislunar/landingScene.ts): the Earth at the
 * origin and the Moon where JPL Horizons puts them at 2025-03-07 11:00 UTC, turned by their IAU
 * rotation models and lit by that moment's Sun. One scene unit is 1000 km.
 */
const LANDING = landingPose();
const LOOK_EASING_PER_60HZ_FRAME = 0.14;
const WARP_DECAY_PER_60HZ_FRAME = 0.85;

export interface CislunarWebGpuRendererOptions {
  forceWebGL?: boolean;
  onRegimeChange?: (regime: SpatialScaleRegime) => void;
  exposeForAutomation?: boolean;
}

/**
 * The cislunar landing scene on three's WebGPURenderer with TSL: the sky dome as a background
 * node, Gaia DR3 and Hipparcos 2007 tiles as analytic-PSF sprites, the Earth, its atmosphere
 * shell and the Moon as node materials, and the HDR post chain with AgX.
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
  private readonly stars = new CatalogStarSpriteField({
    exposure: DEFAULT_CATALOG_STAR_EXPOSURE * LANDING_SKY_GAIN,
    limitingMagnitude: LANDING_LIMITING_MAGNITUDE,
  });
  /** The catalog star sphere, centred on the camera every frame so the stars sit at infinity. */
  private readonly starMount: Group;
  private readonly optics = createCinemaOpticsUniforms();
  private readonly textureLoader = new TextureLoader();
  private readonly pending: Promise<unknown>[] = [];
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

    this.starMount = this.stars.createMount(CATALOG_STAR_MOUNT_RADIUS);
    this.world.add(this.starMount);

    populateCislunarWorld(this.world, this.scene, this.textureLoader, this.pending, this.earthSunView, LANDING);

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

  /** Puts the camera rig on the landing's path, looking between the Earth and the Moon. */
  private placeRig(): void {
    this.rig.position.copy(landingCameraPositionAt(this.heldClockSec ?? this.elapsedSec, LANDING));
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
      window.__albedianCosmos = createCosmosAutomation(this);
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

    // Lateral translation past the Moon's far side (1,000 km over 30 s, 33.3 km/s), giving real parallax
    if (this.showcaseMode) {
      this.rig.position.copy(landingCameraPositionAt(t, LANDING));
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

  private warnOnce(key: string, message: string): void {
    if (this.warned.has(key)) return;
    this.warned.add(key);
    console.warn(`[webgpu host] ${message}`);
  }
}
