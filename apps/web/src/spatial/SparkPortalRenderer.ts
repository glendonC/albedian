import * as THREE from 'three';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { SparkControls, SparkRenderer, SparkXr } from '@sparkjsdev/spark';
import type { RenderLayerSet, SkyView } from '@albedian/contracts';
import { cameraSkyPose } from './cameraSkyPose.js';
import type { CameraSkyPose, ShowcaseSceneKey } from './cosmosRenderer.js';
import { WEBGL_EXPOSURE } from './frameFinishing.js';
import { CatalogStarField, DEFAULT_LIMITING_MAGNITUDE, type CatalogStarFieldStats } from './catalogStars/index.js';
import { webglRenderLayersFor } from './webglRenderLayers.js';
import { ObserverSky, defaultObserverLook, localFrameQuaternionFor, lookOfSkyView, observerHorizonVisible } from './observerPose.js';
import { canvasCompassLabel, createObserverHorizon, disposeObserverHorizon } from './webgl/observerHorizon.js';
import { centreOnCamera } from './skyMounts.js';
import {
  createSkyDomeUniforms, createCinemaOpticsUniforms, createCinemaOpticsPass,
  createGlobalPhotorealisticSkyDomeMesh, loadLandingSceneTextures, loadSkyViewTextures,
  heardLimitOf, rendererUpload, setSkyPictureOf, type SkyPicture, type SkyDomeUniforms, type CinemaOpticsUniforms,
} from './webgl/skyDomeAndCinemaShader.js';
import {
  createEarthUniforms, createMoonUniforms, buildCislunarRegimeGroup,
  type EarthUniforms, type MoonUniforms,
} from './webgl/cislunarRegime.js';
import {
  clampLookPitch,
} from './webgl/skyTargets.js';

export type SpatialScaleRegime = 'earth_capture' | 'cislunar';

export const SCALE_REGIME_ORDER: SpatialScaleRegime[] = ['earth_capture', 'cislunar'];

const REGIME_CAMERA_ANCHORS: Record<SpatialScaleRegime, THREE.Vector3> = {
  earth_capture: new THREE.Vector3(0, 0, 0),
  cislunar: new THREE.Vector3(0, 42, -160),
};

export class SparkPortalRenderer {
  private renderer: THREE.WebGLRenderer | null = null;
  private sparkRenderer: SparkRenderer | null = null;
  private composer: EffectComposer | null = null;
  private bloomPass: UnrealBloomPass | null = null;
  private scene: THREE.Scene;
  private camera: THREE.PerspectiveCamera;
  private localFrame: THREE.Group;
  private sparkControls: SparkControls | null = null;
  private sparkXr: SparkXr | null = null;
  private textureLoader = new THREE.TextureLoader();
  private gltfLoader = new GLTFLoader();

  private regimeGroups: Record<SpatialScaleRegime, THREE.Group> = {
    earth_capture: new THREE.Group(),
    cislunar: new THREE.Group(),
  };

  private skyDomeUniforms: SkyDomeUniforms = createSkyDomeUniforms();
  private earthUniforms: EarthUniforms = createEarthUniforms();
  private moonUniforms: MoonUniforms = createMoonUniforms();
  private cinemaOpticsUniforms: CinemaOpticsUniforms = createCinemaOpticsUniforms();

  private cislunarMoonMesh: THREE.Mesh | null = null;
  private cislunarEarthMesh: THREE.Mesh | null = null;
  private catalogStars: CatalogStarField | null = null;
  private readonly catalogStarMounts: THREE.Group[] = [];

  private observer = new ObserverSky();
  private observerHorizon: THREE.Group | null = null;
  private activeRegime: SpatialScaleRegime = 'earth_capture';
  private flightState: {
    active: boolean; startPos: THREE.Vector3; endPos: THREE.Vector3; startQuat: THREE.Quaternion; endQuat: THREE.Quaternion;
    progress: number; targetRegime: SpatialScaleRegime;
  } | null = null;

  private lookTarget = { yaw: 0, pitch: 0, curYaw: 0, curPitch: 0, velYaw: 0, velPitch: 0 };
  private targetFov = 56;
  private curFov = 56;
  private showcaseMode = false;
  private onRegimeChange?: (regime: SpatialScaleRegime) => void;
  private rafId = 0;
  private drawing = true; // false while the home covers the canvas (setDrawing)
  private heldScene: { sceneKey: ShowcaseSceneKey; instant: boolean } | null = null; // asked for while not drawing
  private landingPicturesAsked = false; // once, by the first landing scene
  private tick: ((timestamp: number) => void) | null = null; // the frame loop's step, once started
  private timer = new THREE.Timer();
  private heldClockSec: number | null = null;

  constructor(container: HTMLElement, onRegimeChange?: (regime: SpatialScaleRegime) => void) {
    this.onRegimeChange = onRegimeChange;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x010205);

    this.camera = new THREE.PerspectiveCamera(56, 16 / 9, 0.05, 2800);
    this.camera.rotation.order = 'YXZ';
    this.localFrame = new THREE.Group();
    this.localFrame.position.copy(REGIME_CAMERA_ANCHORS.earth_capture);
    this.localFrame.add(this.camera);
    this.scene.add(this.localFrame);

    for (const r of SCALE_REGIME_ORDER) {
      this.regimeGroups[r].position.copy(REGIME_CAMERA_ANCHORS[r]);
      this.scene.add(this.regimeGroups[r]);
    }
    // The horizon needs no drawing context, so it is there even where WebGL is not
    this.localFrame.add((this.observerHorizon = createObserverHorizon(canvasCompassLabel)));

    try {
      this.renderer = new THREE.WebGLRenderer({
        antialias: false, powerPreference: 'high-performance', preserveDrawingBuffer: true,
      });
      this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
      this.renderer.toneMappingExposure = WEBGL_EXPOSURE;
      this.renderer.outputColorSpace = THREE.SRGBColorSpace;
      Object.assign(this.renderer.domElement.style, { width: '100%', height: '100%', display: 'block' });
      container.appendChild(this.renderer.domElement);

      this.sparkRenderer = new SparkRenderer({
        renderer: this.renderer, maxStdDev: Math.sqrt(8), premultipliedAlpha: true, sortRadial: true,
      });
      this.sparkRenderer.frustumCulled = false;
      this.localFrame.add(this.sparkRenderer);

      const width = container.clientWidth || 1600;
      const height = container.clientHeight || 1000;
      this.composer = new EffectComposer(this.renderer);
      this.composer.addPass(new RenderPass(this.scene, this.camera));
      this.bloomPass = new UnrealBloomPass(new THREE.Vector2(width, height), 0.64, 0.72, 0.60);
      this.composer.addPass(this.bloomPass);
      this.composer.addPass(createCinemaOpticsPass(this.cinemaOpticsUniforms));
      this.composer.addPass(new OutputPass());

      this.sparkControls = new SparkControls({ canvas: this.renderer.domElement });
      this.catalogStars = new CatalogStarField();
      // The sky view's panorama on every visit; the landing scenes' pictures (the bodies, and the Bruneton table that
      // sampleBrunetonAtmoLut reads) wait for a landing scene
      loadSkyViewTextures(this.textureLoader, this.skyDomeUniforms);
      this.localFrame.add(createGlobalPhotorealisticSkyDomeMesh(this.skyDomeUniforms));
      this.buildEarthCaptureWorld();
      this.buildCislunarWorld();
      this.startLoop();
    } catch {
      this.renderer = null;
    }
    this.syncRegimeVisibility();
  }

  private addCatalogStarMount(group: THREE.Group, radius: number): void {
    if (!this.catalogStars) return;
    const mount = this.catalogStars.createMount(radius);
    group.add(mount);
    this.catalogStarMounts.push(mount);
  }

  private buildEarthCaptureWorld(): void {
    this.addCatalogStarMount(this.regimeGroups.earth_capture, 58.0);
  }

  /** The recording's place and capture time (ISO 8601 UTC); without a time the sky view is for now. */
  public setObserverCoordinates(latDeg: number, lonDeg: number, capturedAtUtc: string | null = null): void {
    if (!this.observer.set(latDeg, lonDeg, capturedAtUtc)) return;
    this.applyObserverFrame(true);
    if (this.cislunarMoonMesh) this.cislunarMoonMesh.rotation.z = latDeg < 0 ? Math.PI * 0.85 : 0.0;
    this.cislunarEarthMesh?.rotation.set(...((latDeg < 0 ? [-0.88, -1.15, 0.18] : [-0.55, 1.68, -0.24]) as [number, number, number]));
  }

  /** A recording without a location: no place, time or horizon, and celestial north up again. */
  public clearObserverCoordinates(): void { if (this.observer.clear()) this.applyObserverFrame(false); }

  /** New observer frame; in the sky view the camera's parent turns to it, and a new place opens on its default look. */
  private applyObserverFrame(newPlace: boolean): void {
    const frame = this.observer.refresh(Date.now());
    if (this.activeRegime === 'earth_capture' && !this.flightState) {
      localFrameQuaternionFor(this.activeRegime, frame, this.localFrame.quaternion);
      this.localFrame.updateMatrixWorld(true);
      if (newPlace) this.lookAtDefault();
    }
    this.syncRegimeVisibility();
  }

  public lookAtSky(azDeg: number, altDeg: number, acrossDeg?: number): void { this.setLook((-azDeg * Math.PI) / 180, (altDeg * Math.PI) / 180); if (acrossDeg) this.targetFov = this.curFov = (360 / Math.PI) * Math.atan(Math.tan((acrossDeg * Math.PI) / 360) / this.camera.aspect); }
  /** go-there-predicted-2: turn the sky view back to a kept sky view (observerPose.ts lookOfSkyView). */
  public lookAtSkyView(view: SkyView): void { const l = lookOfSkyView(view, this.scene, this.regimeGroups.earth_capture, this.localFrame); this.setLook(l.yaw, clampLookPitch(l.pitch)); this.targetFov = this.curFov = view.fovVerticalDeg; }
  private setLook(yaw: number, pitch: number): void { Object.assign(this.lookTarget, { yaw, pitch, curYaw: yaw, curPitch: pitch }); }

  private lookAtDefault(): void {
    const look = this.observer.site ? defaultObserverLook(this.observer.site.latDeg) : null;
    if (look) this.setLook(look.yaw, look.pitch);
  }

  private buildCislunarWorld(): void {
    const group = this.regimeGroups.cislunar;
    this.addCatalogStarMount(group, 85.0);
    const { earthMesh, moonMesh } = buildCislunarRegimeGroup(group, this.earthUniforms, this.moonUniforms, this.gltfLoader);
    this.cislunarEarthMesh = earthMesh;
    this.cislunarMoonMesh = moonMesh;
  }

  private syncRegimeVisibility(): void {
    for (const r of SCALE_REGIME_ORDER) {
      this.regimeGroups[r].visible = r === this.activeRegime || this.flightState?.targetRegime === r;
    }
    const view = { regime: this.activeRegime, flying: Boolean(this.flightState), placed: this.observer.frame !== null };
    if (this.observerHorizon) this.observerHorizon.visible = observerHorizonVisible(view);
  }

  private startLoop(): void {
    if (typeof window === 'undefined' || !this.renderer) return;
    this.timer.connect(document);
    const tick = (timestamp: number) => {
      if (!this.renderer) return;
      this.timer.update(timestamp);
      const elapsed = this.heldClockSec ?? this.timer.getElapsed();

      this.skyDomeUniforms.uTime.value = elapsed;
      this.earthUniforms.uTime.value = elapsed;
      this.cinemaOpticsUniforms.uTime.value = elapsed;

      // Without a capture time the sky view is for now, so the sky keeps turning with the clock
      if (this.heldClockSec === null && this.observer.due(Date.now())) this.applyObserverFrame(false);

      const rotMult = this.heldClockSec !== null ? 0 : this.showcaseMode ? 3.2 : 1.0;
      if (this.cislunarMoonMesh) this.cislunarMoonMesh.rotation.y += 0.0005 * rotMult;
      if (this.cislunarEarthMesh) this.cislunarEarthMesh.rotation.y += 0.00018 * rotMult;

      if (this.showcaseMode && !this.flightState?.active) {
        this.lookTarget.yaw = Math.sin(elapsed * 0.42) * 0.095;
        this.lookTarget.pitch = Math.cos(elapsed * 0.29) * 0.042;
        this.targetFov = 53 + Math.sin(elapsed * 0.35) * 2.4;
      }

      const prevYaw = this.lookTarget.curYaw;
      const prevPitch = this.lookTarget.curPitch;
      this.lookTarget.curYaw += (this.lookTarget.yaw - this.lookTarget.curYaw) * 0.14;
      this.lookTarget.curPitch += (this.lookTarget.pitch - this.lookTarget.curPitch) * 0.14;
      this.lookTarget.velYaw = this.lookTarget.velYaw * 0.82 + (this.lookTarget.curYaw - prevYaw) * 18.0;
      this.lookTarget.velPitch = this.lookTarget.velPitch * 0.82 + (this.lookTarget.curPitch - prevPitch) * 18.0;
      this.curFov += (this.targetFov - this.curFov) * 0.14;

      if (this.flightState?.active) {
        this.flightState.progress = Math.min(1, this.flightState.progress + 0.048);
        const t = this.flightState.progress;
        const eased = t * t * (3 - 2 * t);
        this.localFrame.position.lerpVectors(this.flightState.startPos, this.flightState.endPos, eased);
        this.localFrame.quaternion.slerpQuaternions(this.flightState.startQuat, this.flightState.endQuat, eased);
        const warpArc = Math.sin(t * Math.PI);
        this.camera.fov = this.curFov + warpArc * 16;
        this.camera.updateProjectionMatrix();
        this.cinemaOpticsUniforms.uWarpFactor.value = warpArc;

        if (this.flightState.progress >= 1) {
          const target = this.flightState.targetRegime;
          this.flightState = null;
          this.cinemaOpticsUniforms.uWarpFactor.value = 0;
          this.jumpToRegime(target);
        }
      } else {
        this.cinemaOpticsUniforms.uWarpFactor.value *= 0.85;
        this.camera.rotation.y = this.lookTarget.curYaw;
        this.camera.rotation.x = this.lookTarget.curPitch;
        if (Math.abs(this.camera.fov - this.curFov) > 0.02) {
          this.camera.fov = this.curFov;
          this.camera.updateProjectionMatrix();
        }
      }

      for (const mount of this.catalogStarMounts) centreOnCamera(mount, this.camera);
      this.catalogStars?.update(this.camera, this.renderer.getPixelRatio());
      this.sparkXr?.updateControllers(this.camera);
      if (this.composer) {
        this.composer.render();
      } else {
        this.renderer.render(this.scene, this.camera);
      }
      this.scheduleNext();
    };
    this.tick = tick;
    this.scheduleNext();
  }

  /** Asks the browser for the next frame, unless frames are not being drawn now. */
  private scheduleNext(): void {
    if (this.drawing && this.tick) this.rafId = window.requestAnimationFrame(this.tick);
  }

  /** Stops drawing while no frame can be seen, and draws again with its clock reset, so the scene does not jump. */
  public setDrawing(drawing: boolean): void {
    if (drawing === this.drawing) return;
    this.drawing = drawing;
    if (typeof window === 'undefined') return;
    if (!drawing) {
      if (this.rafId) window.cancelAnimationFrame(this.rafId);
      this.rafId = 0;
      return;
    }
    this.timer.reset();
    this.scheduleNext();
    const held = this.heldScene;
    this.heldScene = null;
    if (held && this.showcaseMode) this.transitionToShowcaseScene(held.sceneKey, held.instant);
  }

  /** Asks for the landing scenes' pictures the first time a landing scene starts, where there is a context to draw them. */
  private askLandingPictures(): void {
    if (this.landingPicturesAsked || !this.renderer) return;
    this.landingPicturesAsked = true;
    loadLandingSceneTextures(this.textureLoader, this.earthUniforms, this.moonUniforms);
  }

  public dragLook(deltaPixelsX: number, deltaPixelsY: number): void {
    const sensitivity = 0.0032 * (this.curFov / 56);
    this.lookTarget.yaw -= deltaPixelsX * sensitivity;
    this.lookTarget.pitch = clampLookPitch(this.lookTarget.pitch - deltaPixelsY * sensitivity);
    if (this.activeRegime === 'cislunar' && this.cislunarMoonMesh) {
      this.cislunarMoonMesh.rotation.y += deltaPixelsX * 0.004;
      this.cislunarMoonMesh.rotation.x = Math.max(-0.6, Math.min(0.6, this.cislunarMoonMesh.rotation.x + deltaPixelsY * 0.003));
    }
  }

  public zoomByWheel(deltaY: number): void {
    const step = Math.max(-120, Math.min(120, deltaY)) * 0.045;
    this.targetFov = Math.max(18, Math.min(74, this.targetFov + step));
    if (this.activeRegime !== 'earth_capture') {
      const forward = new THREE.Vector3(0, 0, -1).applyQuaternion(this.camera.quaternion);
      this.localFrame.position.addScaledVector(forward, -step * 0.22);
    }
  }

  public getFovDeg(): number { return Math.round(this.targetFov); }
  public setLimitingMagnitude(mag: number): void { this.catalogStars?.setLimitingMagnitude(mag); }
  public getLimitingMagnitude(): number { return this.catalogStars?.getLimitingMagnitude() ?? DEFAULT_LIMITING_MAGNITUDE; }
  public setSkyPicture(picture: SkyPicture, limitMag: number, onShown?: (shown: SkyPicture) => void): void { setSkyPictureOf(this, rendererUpload(this.renderer), this.skyDomeUniforms, this.catalogStars, picture, limitMag, onShown); }
  public getCatalogStarField(): CatalogStarField | null { return this.catalogStars; }
  public getCatalogStarStats(): CatalogStarFieldStats | null { return this.catalogStars?.getStats() ?? null; }

  public getCameraSkyPose(): CameraSkyPose | null {
    const sky = this.catalogStarMounts.find((m) => m.parent?.visible !== false);
    if (!sky || !this.catalogStars) return null;
    return cameraSkyPose(this.camera, sky, heardLimitOf(this, this.catalogStars));
  }

  public nudgeMovement(dx: number, dy: number, dz: number): void {
    if (this.activeRegime === 'earth_capture') {
      this.lookTarget.yaw -= dx * 0.14;
      this.lookTarget.pitch = clampLookPitch(this.lookTarget.pitch - dz * 0.14 + dy * 0.14);
      return;
    }
    this.localFrame.position.addScaledVector(new THREE.Vector3(1, 0, 0).applyQuaternion(this.camera.quaternion), dx);
    this.localFrame.position.addScaledVector(new THREE.Vector3(0, 1, 0), dy);
    this.localFrame.position.addScaledVector(new THREE.Vector3(0, 0, 1).applyQuaternion(this.camera.quaternion), dz);
  }

  public nudgeLook(dYaw: number, dPitch: number): void {
    this.lookTarget.yaw += dYaw;
    this.lookTarget.pitch = clampLookPitch(this.lookTarget.pitch + dPitch);
  }

  public stepThroughPortal(direction: 1 | -1 = 1): void {
    const curIdx = SCALE_REGIME_ORDER.indexOf(this.activeRegime);
    const nextIdx = direction > 0 ? (curIdx + 1) % SCALE_REGIME_ORDER.length : Math.max(0, curIdx - 1);
    const targetRegime = SCALE_REGIME_ORDER[nextIdx]!;
    this.flightState = {
      active: true, startPos: this.localFrame.position.clone(),
      endPos: REGIME_CAMERA_ANCHORS[targetRegime].clone(),
      startQuat: this.localFrame.quaternion.clone(), endQuat: localFrameQuaternionFor(targetRegime, this.observer.frame),
      progress: 0, targetRegime,
    };
    this.syncRegimeVisibility();
  }

  public jumpToRegime(regime: SpatialScaleRegime): void {
    this.activeRegime = regime;
    this.flightState = null;
    this.localFrame.position.copy(REGIME_CAMERA_ANCHORS[regime]);
    localFrameQuaternionFor(regime, this.observer.frame, this.localFrame.quaternion);
    Object.assign(this.lookTarget, { yaw: 0, pitch: 0, curYaw: 0, curPitch: 0 });
    if (regime === 'earth_capture') this.lookAtDefault();
    this.camera.rotation.set(0, 0, 0);
    this.targetFov = 56;
    this.curFov = 56;
    this.camera.fov = 56;
    this.camera.updateProjectionMatrix();
    this.localFrame.updateMatrixWorld(true);
    this.camera.updateMatrixWorld(true);
    this.syncRegimeVisibility();
    this.onRegimeChange?.(regime);
  }

  public holdClock(seconds: number | null): void { this.heldClockSec = seconds; }
  public showSkyView(): void { if (this.activeRegime !== 'earth_capture' || this.flightState) this.jumpToRegime('earth_capture'); }

  public getRenderLayers(): RenderLayerSet | null {
    return webglRenderLayersFor({ regime: this.activeRegime, observerTime: this.observer.frame ? this.observer.clock.kind : null });
  }

  public setShowcaseMode(enabled: boolean): void {
    this.showcaseMode = enabled;
    if (!enabled) { this.lookTarget.yaw = 0; this.lookTarget.pitch = 0; this.targetFov = 56; this.heldScene = null; }
  }

  public transitionToShowcaseScene(sceneKey: ShowcaseSceneKey, instant = false): void {
    // While no frame is drawn no landing scene starts, so none of its pictures loads behind the home
    if (!this.drawing) { this.heldScene = { sceneKey, instant }; return; }
    this.askLandingPictures();
    this.jumpToRegime(sceneKey);
    if (!instant) {
      this.curFov = 61;
      this.cinemaOpticsUniforms.uWarpFactor.value = 0.48;
    }
  }

  public async enterWebXRSession(): Promise<boolean> {
    if (!this.renderer) return false;
    try {
      if (!this.sparkXr) this.sparkXr = new SparkXr({ renderer: this.renderer, mode: 'vr', enableHands: true });
      await this.sparkXr.toggleXr();
      return true;
    } catch { return false; }
  }

  public resize(width: number, height: number): void {
    if (!this.renderer || width <= 0 || height <= 0) return;
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
    this.composer?.setSize(width, height);
    this.bloomPass?.resolution.set(width, height);
  }

  public dispose(): void {
    if (typeof window !== 'undefined' && this.rafId) window.cancelAnimationFrame(this.rafId);
    this.timer.dispose();
    if (this.observerHorizon) disposeObserverHorizon(this.observerHorizon);
    this.catalogStars?.dispose();
    this.sparkRenderer?.dispose();
    this.composer?.dispose();
    if (this.renderer) {
      this.renderer.dispose();
      this.renderer.domElement.remove();
      this.renderer = null;
    }
  }
}
