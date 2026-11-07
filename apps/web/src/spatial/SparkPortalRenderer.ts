import * as THREE from 'three';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { SparkControls, SparkRenderer, SparkXr, type SplatMesh } from '@sparkjsdev/spark';
import type { RenderLayerSet } from '@albedian/contracts';
import { cameraSkyPose } from './cameraSkyPose.js';
import type { CameraSkyPose } from './cosmosRenderer.js';
import { CatalogStarField, DEFAULT_LIMITING_MAGNITUDE, type CatalogStarFieldStats } from './catalogStars/index.js';
import { webglRenderLayersFor } from './webglRenderLayers.js';
import { createOriginCameraFrustumIndicator } from './referenceFrameContinuity.js';
import { centreOnCamera } from './skyMounts.js';
import {
  createSkyDomeUniforms, createCinemaOpticsUniforms, createCinemaOpticsPass,
  createGlobalPhotorealisticSkyDomeMesh, loadAstronomicalSceneTextures,
  type SkyDomeUniforms, type CinemaOpticsUniforms,
} from './webgl/skyDomeAndCinemaShader.js';
import {
  createEarthUniforms, createMoonUniforms, buildCislunarRegimeGroup,
  type EarthUniforms, type MoonUniforms,
} from './webgl/cislunarRegime.js';
import {
  createLocalBubbleUniforms, buildSolarNeighborhoodRegimeGroup, applyTargetAppearanceToUniforms,
  type LocalBubbleUniforms,
} from './webgl/solarNeighborhoodRegime.js';
import { createNebulaUniforms, buildDeepSkyRegimeGroup, type NebulaUniforms } from './webgl/deepSkyRegime.js';
import {
  createPlanetarySkyUniforms, buildPlanetarySurfaceRegimeGroup, createPlanetaryTerrainSplatMesh,
  type PlanetarySkyUniforms, type PlanetaryTerrainPalette,
} from './webgl/planetarySurfaceRegime.js';
import {
  resolveSkyTargetPosition, projectCatalogDirectionToScreen,
  projectSkyWorldPointToScreen, computeCameraTelemetrySnapshot,
} from './webgl/skyTargets.js';

export type SpatialScaleRegime =
  | 'earth_capture'
  | 'cislunar'
  | 'solar_neighborhood'
  | 'deep_sky'
  | 'planetary_surface';

export const SCALE_REGIME_ORDER: SpatialScaleRegime[] = [
  'earth_capture', 'cislunar', 'solar_neighborhood', 'deep_sky', 'planetary_surface',
];

const REGIME_CAMERA_ANCHORS: Record<SpatialScaleRegime, THREE.Vector3> = {
  earth_capture: new THREE.Vector3(0, 0, 0),
  cislunar: new THREE.Vector3(0, 42, -160),
  solar_neighborhood: new THREE.Vector3(0, 95, -380),
  deep_sky: new THREE.Vector3(0, 150, -620),
  planetary_surface: new THREE.Vector3(0, 210, -880),
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
    solar_neighborhood: new THREE.Group(),
    deep_sky: new THREE.Group(),
    planetary_surface: new THREE.Group(),
  };

  private skyDomeUniforms: SkyDomeUniforms = createSkyDomeUniforms();
  private earthUniforms: EarthUniforms = createEarthUniforms();
  private moonUniforms: MoonUniforms = createMoonUniforms();
  private nebulaUniforms: NebulaUniforms = createNebulaUniforms();
  private localBubbleUniforms: LocalBubbleUniforms = createLocalBubbleUniforms();
  private planetarySkyUniforms: PlanetarySkyUniforms = createPlanetarySkyUniforms();
  private cinemaOpticsUniforms: CinemaOpticsUniforms = createCinemaOpticsUniforms();

  private skyMoonMesh: THREE.Mesh | null = null;
  private cislunarMoonMesh: THREE.Mesh | null = null;
  private cislunarEarthMesh: THREE.Mesh | null = null;
  private betelgeuseMesh: THREE.Mesh | null = null;
  private localBubbleCavityGroup: THREE.Group | null = null;
  private deepSkyBlackHoleMeshes: THREE.Object3D[] = [];
  private captureHorizonSplatMesh: SplatMesh | null = null;
  private planetaryTerrainSplatMesh: SplatMesh | null = null;
  private planetaryParentBodyMesh: THREE.Mesh | null = null;
  private originFrustumGroup: THREE.Group | null = null;
  private catalogStars: CatalogStarField | null = null;
  private readonly catalogStarMounts: THREE.Group[] = [];

  private observerLatDeg: number | null = null;
  private observerLonDeg: number | null = null;
  private activeRegime: SpatialScaleRegime = 'earth_capture';
  private flightState: {
    active: boolean; startPos: THREE.Vector3; endPos: THREE.Vector3;
    progress: number; targetRegime: SpatialScaleRegime;
  } | null = null;

  private lookTarget = { yaw: 0, pitch: 0, curYaw: 0, curPitch: 0, velYaw: 0, velPitch: 0 };
  private targetFov = 56;
  private curFov = 56;
  private showcaseMode = false;
  private onRegimeChange?: (regime: SpatialScaleRegime) => void;
  private rafId = 0;
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

    try {
      this.renderer = new THREE.WebGLRenderer({
        antialias: false, powerPreference: 'high-performance', preserveDrawingBuffer: true,
      });
      this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
      this.renderer.toneMappingExposure = 1.12;
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
      // Binds sky, body, and Bruneton LUT (sampleBrunetonAtmoLut) textures
      loadAstronomicalSceneTextures(
        this.textureLoader, this.skyDomeUniforms, this.earthUniforms,
        this.moonUniforms, this.nebulaUniforms, this.planetarySkyUniforms,
        '/textures/eso1804a.jpg'
      );
      this.localFrame.add(createGlobalPhotorealisticSkyDomeMesh(this.skyDomeUniforms));
      this.buildEarthCaptureWorld();
      this.buildCislunarWorld();
      this.buildSolarNeighborhoodWorld();
      this.buildDeepSkyWorld();
      this.buildPlanetarySurfaceWorld();
      this.originFrustumGroup = createOriginCameraFrustumIndicator();
      this.localFrame.add(this.originFrustumGroup);
      this.syncRegimeVisibility();
      this.startLoop();
    } catch {
      this.renderer = null;
    }
  }

  private addCatalogStarMount(group: THREE.Group, radius: number): void {
    if (!this.catalogStars) return;
    const mount = this.catalogStars.createMount(radius);
    group.add(mount);
    this.catalogStarMounts.push(mount);
  }

  private rebuildCaptureHorizonSplatMesh(latDeg: number): void {
    const group = this.regimeGroups.earth_capture;
    if (this.captureHorizonSplatMesh) {
      group.remove(this.captureHorizonSplatMesh);
      this.captureHorizonSplatMesh.dispose();
      this.captureHorizonSplatMesh = null;
    }
    const horizonDome = group.getObjectByName('captureHorizonLandscapeDome') as THREE.Mesh | undefined;
    const mat = horizonDome?.material as THREE.ShaderMaterial | undefined;
    if (mat?.uniforms?.uIsSouthern) mat.uniforms.uIsSouthern.value = latDeg < 0 ? 1.0 : 0.0;
  }

  private buildEarthCaptureWorld(): void {
    this.addCatalogStarMount(this.regimeGroups.earth_capture, 58.0);
  }

  public setObserverCoordinates(latDeg: number, lonDeg: number): void {
    if (
      this.observerLatDeg !== null && this.observerLonDeg !== null &&
      Math.abs(latDeg - this.observerLatDeg) < 0.01 && Math.abs(lonDeg - this.observerLonDeg) < 0.01
    ) return;
    this.observerLatDeg = latDeg;
    this.observerLonDeg = lonDeg;
    this.rebuildCaptureHorizonSplatMesh(latDeg);

    const isSouthern = latDeg < 0;
    if (this.skyMoonMesh) this.skyMoonMesh.rotation.z = isSouthern ? Math.PI : 0.0;
    if (this.cislunarMoonMesh) this.cislunarMoonMesh.rotation.z = isSouthern ? Math.PI * 0.85 : 0.0;
    if (this.cislunarEarthMesh) {
      if (isSouthern) this.cislunarEarthMesh.rotation.set(-0.88, -1.15, 0.18);
      else this.cislunarEarthMesh.rotation.set(-0.55, 1.68, -0.24);
    }
  }

  private buildCislunarWorld(): void {
    const group = this.regimeGroups.cislunar;
    this.addCatalogStarMount(group, 85.0);
    const { earthMesh, moonMesh } = buildCislunarRegimeGroup(group, this.earthUniforms, this.moonUniforms, this.gltfLoader);
    this.cislunarEarthMesh = earthMesh;
    this.cislunarMoonMesh = moonMesh;
  }

  private buildSolarNeighborhoodWorld(): void {
    const group = this.regimeGroups.solar_neighborhood;
    this.addCatalogStarMount(group, 85.0);
    const { betelgeuseMesh, cavityGroup } = buildSolarNeighborhoodRegimeGroup(group, this.localBubbleUniforms);
    this.betelgeuseMesh = betelgeuseMesh;
    this.localBubbleCavityGroup = cavityGroup;
  }

  private showDeepSkyBlackHole(visible: boolean): void {
    for (const m of this.deepSkyBlackHoleMeshes) m.visible = visible;
  }

  private buildDeepSkyWorld(): void {
    const group = this.regimeGroups.deep_sky;
    this.addCatalogStarMount(group, 95.0);
    this.deepSkyBlackHoleMeshes = buildDeepSkyRegimeGroup(group, this.nebulaUniforms).blackHoleMeshes;
    this.showDeepSkyBlackHole(this.nebulaUniforms.uIsBlackHole.value > 0.5);
  }

  private buildPlanetarySurfaceWorld(): void {
    const group = this.regimeGroups.planetary_surface;
    this.addCatalogStarMount(group, 95.0);
    const { parentBodyMesh, terrainSplatMesh } = buildPlanetarySurfaceRegimeGroup(
      group, this.planetarySkyUniforms, this.earthUniforms, this.moonUniforms
    );
    this.planetaryParentBodyMesh = parentBodyMesh;
    this.planetaryTerrainSplatMesh = terrainSplatMesh;
  }

  private rebuildPlanetaryTerrainSplats(palette: PlanetaryTerrainPalette): void {
    const group = this.regimeGroups.planetary_surface;
    if (this.planetaryTerrainSplatMesh) {
      group.remove(this.planetaryTerrainSplatMesh);
      this.planetaryTerrainSplatMesh.dispose();
      this.planetaryTerrainSplatMesh = null;
    }
    const terrainSplatMesh = createPlanetaryTerrainSplatMesh(palette, this.planetarySkyUniforms);
    group.add(terrainSplatMesh);
    this.planetaryTerrainSplatMesh = terrainSplatMesh;
  }

  private syncRegimeVisibility(): void {
    for (const r of SCALE_REGIME_ORDER) {
      this.regimeGroups[r].visible = r === this.activeRegime || this.flightState?.targetRegime === r;
    }
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
      this.nebulaUniforms.uTime.value = elapsed;
      this.localBubbleUniforms.uTime.value = elapsed;
      this.planetarySkyUniforms.uTime.value = elapsed;
      this.cinemaOpticsUniforms.uTime.value = elapsed;

      const rotMult = this.heldClockSec !== null ? 0 : this.showcaseMode ? 3.2 : 1.0;
      if (this.cislunarMoonMesh) this.cislunarMoonMesh.rotation.y += 0.0005 * rotMult;
      if (this.cislunarEarthMesh) this.cislunarEarthMesh.rotation.y += 0.00018 * rotMult;
      if (this.betelgeuseMesh) this.betelgeuseMesh.rotation.y += 0.0006 * rotMult;
      if (this.localBubbleCavityGroup) this.localBubbleCavityGroup.rotation.y += 0.00045 * rotMult;
      if (this.planetaryParentBodyMesh) this.planetaryParentBodyMesh.rotation.y += 0.0005 * rotMult;

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
        this.localFrame.position.lerpVectors(this.flightState.startPos, this.flightState.endPos, t * t * (3 - 2 * t));
        const warpArc = Math.sin(t * Math.PI);
        this.camera.fov = this.curFov + warpArc * 16;
        this.camera.updateProjectionMatrix();
        this.cinemaOpticsUniforms.uWarpFactor.value = warpArc;
        this.localBubbleUniforms.uWarpFactor.value = warpArc;

        if (this.flightState.progress >= 1) {
          const target = this.flightState.targetRegime;
          this.flightState = null;
          this.cinemaOpticsUniforms.uWarpFactor.value = 0;
          this.localBubbleUniforms.uWarpFactor.value = 0;
          this.jumpToRegime(target);
        }
      } else {
        this.cinemaOpticsUniforms.uWarpFactor.value *= 0.85;
        this.localBubbleUniforms.uWarpFactor.value *= 0.85;
        this.camera.rotation.y = this.lookTarget.curYaw;
        this.camera.rotation.x = this.lookTarget.curPitch;
        if (Math.abs(this.camera.fov - this.curFov) > 0.02) {
          this.camera.fov = this.curFov;
          this.camera.updateProjectionMatrix();
        }
      }

      if (this.originFrustumGroup) {
        this.originFrustumGroup.visible = false;
      }

      for (const mount of this.catalogStarMounts) centreOnCamera(mount, this.camera);
      this.catalogStars?.update(this.camera, this.renderer.getPixelRatio());
      this.sparkXr?.updateControllers(this.camera);
      if (this.composer) {
        this.composer.render();
      } else {
        this.renderer.render(this.scene, this.camera);
      }
      this.rafId = window.requestAnimationFrame(tick);
    };
    this.rafId = window.requestAnimationFrame(tick);
  }

  public getCameraTelemetry(viewportWidth: number, viewportHeight: number) {
    return computeCameraTelemetrySnapshot({
      camera: this.camera, activeRegime: this.activeRegime, deepSkyAnchor: REGIME_CAMERA_ANCHORS.deep_sky,
      cislunarMoonMesh: this.cislunarMoonMesh, betelgeuseMesh: this.betelgeuseMesh,
      planetaryParentBodyMesh: this.planetaryParentBodyMesh,
      curYaw: this.lookTarget.curYaw, curPitch: this.lookTarget.curPitch, curFov: this.curFov,
      velYaw: this.lookTarget.velYaw, velPitch: this.lookTarget.velPitch,
      warpFactor: this.cinemaOpticsUniforms.uWarpFactor.value, viewportWidth, viewportHeight,
    });
  }

  public dragLook(deltaPixelsX: number, deltaPixelsY: number): void {
    const sensitivity = 0.0032 * (this.curFov / 56);
    this.lookTarget.yaw -= deltaPixelsX * sensitivity;
    this.lookTarget.pitch = Math.max(-1.15, Math.min(1.15, this.lookTarget.pitch - deltaPixelsY * sensitivity));
    if (this.activeRegime === 'cislunar' && this.cislunarMoonMesh) {
      this.cislunarMoonMesh.rotation.y += deltaPixelsX * 0.004;
      this.cislunarMoonMesh.rotation.x = Math.max(-0.6, Math.min(0.6, this.cislunarMoonMesh.rotation.x + deltaPixelsY * 0.003));
    } else if (this.activeRegime === 'solar_neighborhood') {
      if (this.betelgeuseMesh) this.betelgeuseMesh.rotation.y += deltaPixelsX * 0.004;
      if (this.localBubbleCavityGroup) {
        this.localBubbleCavityGroup.rotation.y += deltaPixelsX * 0.0035;
        this.localBubbleCavityGroup.rotation.x = Math.max(-0.6, Math.min(0.6, this.localBubbleCavityGroup.rotation.x + deltaPixelsY * 0.0025));
      }
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
  public getCatalogStarField(): CatalogStarField | null { return this.catalogStars; }
  public getCatalogStarStats(): CatalogStarFieldStats | null { return this.catalogStars?.getStats() ?? null; }

  public getCameraSkyPose(): CameraSkyPose | null {
    const sky = this.catalogStarMounts.find((m) => m.parent?.visible !== false);
    if (!sky || !this.catalogStars) return null;
    return cameraSkyPose(this.camera, sky, this.catalogStars.getLimitingMagnitude());
  }

  public projectCatalogPositionToScreen(raDeg: number, decDeg: number, viewportWidth: number, viewportHeight: number) {
    if (!this.catalogStars) return { x: -999, y: -999, visible: false };
    return projectCatalogDirectionToScreen(this.camera, this.catalogStars.sceneDirection(raDeg, decDeg), viewportWidth, viewportHeight);
  }

  public nudgeMovement(dx: number, dy: number, dz: number): void {
    if (this.activeRegime === 'earth_capture') {
      this.lookTarget.yaw -= dx * 0.14;
      this.lookTarget.pitch = Math.max(-1.15, Math.min(1.15, this.lookTarget.pitch - dz * 0.14 + dy * 0.14));
      return;
    }
    this.localFrame.position.addScaledVector(new THREE.Vector3(1, 0, 0).applyQuaternion(this.camera.quaternion), dx);
    this.localFrame.position.addScaledVector(new THREE.Vector3(0, 1, 0), dy);
    this.localFrame.position.addScaledVector(new THREE.Vector3(0, 0, 1).applyQuaternion(this.camera.quaternion), dz);
  }

  public nudgeLook(dYaw: number, dPitch: number): void {
    this.lookTarget.yaw += dYaw;
    this.lookTarget.pitch = Math.max(-1.15, Math.min(1.15, this.lookTarget.pitch + dPitch));
  }

  public getSkyTargetPosition(id: string, title: string, raDeg: number, decDeg: number): THREE.Vector3 {
    return resolveSkyTargetPosition(id, title, raDeg, decDeg);
  }

  public projectSkyTargetToScreen(id: string, title: string, raDeg: number, decDeg: number, viewportWidth: number, viewportHeight: number) {
    if (viewportWidth > 0 && viewportHeight > 0 && Math.abs(this.camera.aspect - viewportWidth / viewportHeight) > 0.001) {
      this.resize(viewportWidth, viewportHeight);
    }
    return projectSkyWorldPointToScreen(this.camera, this.getSkyTargetPosition(id, title, raDeg, decDeg), viewportWidth, viewportHeight);
  }

  public focusSkyTarget(id: string, title: string, raDeg: number, decDeg: number): void {
    const dir = this.getSkyTargetPosition(id, title, raDeg, decDeg).clone().normalize();
    this.lookTarget.yaw = Math.atan2(-dir.x, -dir.z);
    this.lookTarget.pitch = Math.asin(Math.max(-0.85, Math.min(0.85, dir.y)));
  }

  public setTargetAppearance(title: string, bpRp?: number, objectType?: string): void {
    const isBh = applyTargetAppearanceToUniforms(title, bpRp, objectType, this.localBubbleUniforms);
    this.nebulaUniforms.uIsBlackHole.value = isBh ? 1.0 : 0.0;
    this.showDeepSkyBlackHole(isBh);
  }

  public stepThroughPortal(direction: 1 | -1 = 1): void {
    const curIdx = SCALE_REGIME_ORDER.indexOf(this.activeRegime);
    const nextIdx = direction > 0 ? (curIdx + 1) % SCALE_REGIME_ORDER.length : Math.max(0, curIdx - 1);
    const targetRegime = SCALE_REGIME_ORDER[nextIdx]!;
    this.flightState = {
      active: true, startPos: this.localFrame.position.clone(),
      endPos: REGIME_CAMERA_ANCHORS[targetRegime].clone(), progress: 0, targetRegime,
    };
    this.syncRegimeVisibility();
  }

  public jumpToRegime(regime: SpatialScaleRegime): void {
    this.activeRegime = regime;
    this.flightState = null;
    this.localFrame.position.copy(REGIME_CAMERA_ANCHORS[regime]);
    this.localFrame.rotation.set(0, 0, 0);
    Object.assign(this.lookTarget, { yaw: 0, pitch: 0, curYaw: 0, curPitch: 0 });
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

  public setSkyRestorationActive(active: boolean): void { this.skyDomeUniforms.uRestoration.value = active ? 1.0 : 0.0; }
  public holdClock(seconds: number | null): void { this.heldClockSec = seconds; }

  public getRenderLayers(): RenderLayerSet | null {
    return webglRenderLayersFor({ regime: this.activeRegime, blackHole: this.nebulaUniforms.uIsBlackHole.value > 0.5 });
  }

  public setShowcaseMode(enabled: boolean): void {
    this.showcaseMode = enabled;
    if (!enabled) { this.lookTarget.yaw = 0; this.lookTarget.pitch = 0; this.targetFov = 56; }
  }

  public transitionToShowcaseScene(sceneKey: 'cislunar' | 'black_hole' | 'supergiant' | 'saturn_horizon', instant = false): void {
    let targetRegime: SpatialScaleRegime = 'cislunar';
    if (sceneKey === 'black_hole') {
      targetRegime = 'deep_sky';
      this.setTargetAppearance('Sagittarius A* (Galactic Center)', 1.85, 'deep_sky');
    } else if (sceneKey === 'supergiant') {
      targetRegime = 'solar_neighborhood';
      this.setTargetAppearance('Betelgeuse (α Orionis)', 1.85, 'star');
    } else if (sceneKey === 'saturn_horizon') {
      targetRegime = 'planetary_surface';
      this.setTargetAppearance('Saturn', 0.9, 'planet');
      this.rebuildPlanetaryTerrainSplats('titan_methane');
    }
    this.jumpToRegime(targetRegime);
    if (!instant) {
      this.curFov = 61;
      this.cinemaOpticsUniforms.uWarpFactor.value = 0.48;
      this.localBubbleUniforms.uWarpFactor.value = 0.48;
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
    this.captureHorizonSplatMesh?.dispose();
    this.planetaryTerrainSplatMesh?.dispose();
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
