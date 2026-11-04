import {
  AgXToneMapping,
  Color,
  PerspectiveCamera,
  SRGBColorSpace,
  Scene,
  Vector3,
  WebGPURenderer,
} from 'three/webgpu';
import type { GaussianSplat } from 'three/addons/objects/GaussianSplat.js';
import { BlackbodyLut } from './color/blackbody';
import { srgbExtinctionRatios } from './color/extinction';
import { parseLocalBubbleSplats, type SplatArrays } from './data/localBubble';
import { makeSyntheticStarFixture, SYNTHETIC_STAR_FIXTURE_LABEL } from './data/starFixture';
import { voxelizeSplats } from './data/voxelize';
import { createStarLayer, describePsf, type StarLayer } from './render/stars';
import { createThreeSplatLayer, type ThreeSplatLayer } from './render/splatsThree';
import { createRadixSplatLayer, type RadixSplatLayer } from './render/splatsRadix';
import { DEFAULT_DUST_OPTIONS, uploadDustVolume, type DustVolumeGpu } from './render/dustVolume';
import {
  buildPostChain,
  DEFAULT_POST,
  type AaMode,
  type DustStage,
  type PostChain,
  type PostOptions,
} from './render/post';
import {
  SCALE_FIXTURE_LABEL,
  createRadixScaleLayer,
  createThreeScaleLayer,
  type ScaleFixtureName,
  type ScaleLayer,
} from './render/scaleFixtures';
import {
  postBenchRun,
  runBenchSuite,
  waitForGpuIdle,
  DEFAULT_BENCH_OPTIONS,
  type BenchConfig,
  type BenchHost,
  type BenchRun,
  type SplatPath,
  type VolumeMode,
} from './bench/bench';
import { diffImages, readCanvas, type ImageDiff } from './bench/stability';

const params = new URLSearchParams(window.location.search);
const SPLAT_INTENSITY = Number(params.get('intensity') ?? 2.2);
const STAR_COUNT = Number(params.get('stars') ?? 100_000);
const VOXEL_SIZE = Number(params.get('voxel') ?? 0.1);

declare global {
  interface Window {
    __spike?: SpikeApp;
    __benchRun?: BenchRun;
    __benchFile?: string | null;
    __benchError?: string;
    __spikeReady?: boolean;
  }
}

async function adapterLimits(): Promise<Record<string, number> | undefined> {
  if (!navigator.gpu) return undefined;
  const adapter = await navigator.gpu.requestAdapter({ powerPreference: 'high-performance' });
  if (!adapter) return undefined;
  const names = [
    'maxStorageBuffersPerShaderStage',
    'maxStorageBufferBindingSize',
    'maxBufferSize',
    'maxComputeWorkgroupStorageSize',
    'maxTextureDimension2D',
    'maxTextureDimension3D',
    'maxTextureArrayLayers',
    'maxColorAttachmentBytesPerSample',
  ] as const;
  const limits: Record<string, number> = {};
  for (const name of names) limits[name] = adapter.limits[name];
  return limits;
}

function parseResolution(value: string | null): [number, number] | null {
  if (!value || value === 'window') return null;
  const m = /^(\d+)x(\d+)$/.exec(value);
  return m ? [Number(m[1]), Number(m[2])] : null;
}

function scaleTarget(path: SplatPath): { renderer: 'three' | 'radix'; fixture: ScaleFixtureName } | null {
  const m = /^(three|radix)-(valley|snow)$/.exec(path);
  return m ? { renderer: m[1] as 'three' | 'radix', fixture: m[2] as ScaleFixtureName } : null;
}

interface SortPopReport {
  method: string;
  resolution: string;
  poses: number;
  threeStaleVsFresh: ImageDiff;
  radixRepeat: ImageDiff;
  volumeRepeat: ImageDiff;
}

class SpikeApp implements BenchHost {
  readonly renderer: WebGPURenderer;
  readonly scene = new Scene();
  readonly camera = new PerspectiveCamera(56, 16 / 9, 0.05, 2800);
  readonly stars: StarLayer;
  readonly splatArrays: SplatArrays;
  private threeSplats: ThreeSplatLayer | null = null;
  private radixSplats: RadixSplatLayer | null = null;
  private scaleLayer: { path: SplatPath; layer: ScaleLayer } | null = null;
  private dustGpu: DustVolumeGpu | null = null;
  dustBuild: { voxelizeMs: number; uploadMs: number; dims: number[]; voxels: number; nonEmptyBrickFraction: number; maxSigma: number } | null = null;
  private volumeMode: VolumeMode = 'off';
  private extinctionScale = 1;
  private post: PostChain;
  private postOptions: PostOptions = { ...DEFAULT_POST };
  private splatPath: SplatPath = 'three';
  private fixedSize: [number, number] | null = null;
  private orbitCenter = new Vector3(0, 1.5, 0);
  private benchRadius = 22;
  private orbitAngle = 0;
  private orbitElevation = 0.22;
  private orbitRadius = 22;
  autoOrbit = true;
  readonly errors: string[] = [];

  private constructor(renderer: WebGPURenderer, splatArrays: SplatArrays, stars: StarLayer) {
    this.renderer = renderer;
    this.splatArrays = splatArrays;
    this.stars = stars;
    this.scene.background = new Color(0x000000);
    this.scene.add(stars.mesh);
    this.post = buildPostChain(renderer, this.scene, this.camera, this.postOptions);
  }

  static async create(stage: HTMLElement): Promise<SpikeApp> {
    const forceWebGL = params.get('backend') === 'webgl' || !navigator.gpu;
    const requiredLimits = forceWebGL ? undefined : await adapterLimits();
    const renderer = new WebGPURenderer({
      antialias: false,
      forceWebGL,
      trackTimestamp: true,
      powerPreference: 'high-performance',
      ...(requiredLimits ? { requiredLimits } : {}),
    });
    await renderer.init();
    renderer.toneMapping = AgXToneMapping;
    renderer.toneMappingExposure = 1.0;
    renderer.outputColorSpace = SRGBColorSpace;
    stage.appendChild(renderer.domElement);

    const raw = (await fetch('/models/lb_splats.json').then((r) => r.json())) as unknown;
    const splatArrays = parseLocalBubbleSplats(raw);
    const fixture = makeSyntheticStarFixture({ count: STAR_COUNT });
    const stars = createStarLayer(fixture, new BlackbodyLut());
    return new SpikeApp(renderer, splatArrays, stars);
  }

  get backendName(): 'webgpu' | 'webgl2' {
    return (this.renderer.backend as unknown as { isWebGPUBackend?: boolean }).isWebGPUBackend
      ? 'webgpu'
      : 'webgl2';
  }

  get threeSplatMesh(): GaussianSplat | null {
    return this.threeSplats?.mesh ?? null;
  }

  async setSplatPath(path: SplatPath): Promise<void> {
    this.splatPath = path;
    if (path === 'three' && !this.threeSplats) {
      this.threeSplats = createThreeSplatLayer(this.splatArrays, { intensity: SPLAT_INTENSITY });
      this.scene.add(this.threeSplats.mesh);
    }
    if (path === 'radix' && !this.radixSplats) {
      try {
        this.radixSplats = createRadixSplatLayer(this.renderer, this.splatArrays, {
          intensity: SPLAT_INTENSITY,
        });
        this.scene.add(this.radixSplats.anchor);
        this.scene.add(this.radixSplats.renderer);
      } catch (error) {
        this.errors.push(`radix: ${String(error)}`);
      }
    }
    if (this.threeSplats) this.threeSplats.mesh.visible = path === 'three';
    if (this.radixSplats) this.radixSplats.renderer.visible = path === 'radix';

    const target = scaleTarget(path);
    if (this.scaleLayer && this.scaleLayer.path !== path) {
      this.scene.remove(this.scaleLayer.layer.object);
      this.scaleLayer.layer.dispose();
      this.scaleLayer = null;
    }
    if (target && !this.scaleLayer) {
      const layer =
        target.renderer === 'three'
          ? await createThreeScaleLayer(target.fixture)
          : await createRadixScaleLayer(this.renderer, target.fixture);
      this.scene.add(layer.object);
      this.scaleLayer = { path, layer };
    }
    if (this.scaleLayer) {
      // Orbit inside the dense part of the capture rather than its outlier-inflated bounds.
      this.orbitCenter.copy(this.scaleLayer.layer.bounds.center);
      this.benchRadius = Math.max(2, this.scaleLayer.layer.bounds.radius * 0.15);
    } else {
      this.orbitCenter.set(0, 1.5, 0);
      this.benchRadius = 22;
    }
    this.orbitRadius = this.benchRadius;
  }

  private ensureDust(): DustVolumeGpu {
    if (!this.dustGpu) {
      const t0 = performance.now();
      const volume = voxelizeSplats(this.splatArrays, { voxelSize: VOXEL_SIZE });
      const t1 = performance.now();
      this.dustGpu = uploadDustVolume(volume);
      const t2 = performance.now();
      this.dustBuild = {
        voxelizeMs: Math.round(t1 - t0),
        uploadMs: Math.round(t2 - t1),
        dims: volume.dims,
        voxels: volume.stats.voxels,
        nonEmptyBrickFraction: Math.round(volume.stats.nonEmptyBrickFraction * 1000) / 1000,
        maxSigma: Math.round(volume.maxSigma * 1e4) / 1e4,
      };
    }
    return this.dustGpu;
  }

  setVolume(mode: VolumeMode, extinctionScale = this.extinctionScale, emission = this.dustEmission): void {
    if (mode === this.volumeMode && extinctionScale === this.extinctionScale && emission === this.dustEmission) return;
    this.volumeMode = mode;
    this.extinctionScale = extinctionScale;
    this.dustEmission = emission;
    this.rebuildPost();
  }

  /** Dust emission multiplier (SPLAT_INTENSITY by default; 0 = absorption only). */
  private dustEmission = SPLAT_INTENSITY;

  setStarsVisible(visible: boolean): void {
    this.stars.mesh.visible = visible;
  }

  setPost(options: Partial<PostOptions>): void {
    const next = { ...this.postOptions, ...options };
    const changed = (Object.keys(next) as (keyof PostOptions)[]).some((k) => next[k] !== this.postOptions[k]);
    if (!changed) return;
    this.postOptions = next;
    this.rebuildPost();
  }

  private rebuildPost(): void {
    let dust: DustStage | null = null;
    if (this.volumeMode !== 'off') {
      dust = {
        gpu: this.ensureDust(),
        options: {
          ...DEFAULT_DUST_OPTIONS,
          intensity: this.dustEmission,
          extinctionScale: this.extinctionScale,
          emptySpaceSkipping: this.volumeMode !== 'noskip',
          resolutionScale: this.volumeMode === 'skip-half' ? 0.5 : 1,
        },
      };
    }
    this.post.dispose();
    this.post = buildPostChain(this.renderer, this.scene, this.camera, this.postOptions, dust);
  }

  setFixedSize(size: [number, number] | null): void {
    this.fixedSize = size;
    this.resize();
  }

  resize(): void {
    let width: number;
    let height: number;
    if (this.fixedSize) {
      [width, height] = this.fixedSize;
      this.renderer.setPixelRatio(1);
    } else {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.renderer.setPixelRatio(dpr);
      width = window.innerWidth;
      height = window.innerHeight;
    }
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    const bufferHeight = this.fixedSize ? height : Math.round(height * this.renderer.getPixelRatio());
    this.stars.psfScale.value = bufferHeight / 1080;
  }

  private placeCamera(angle: number, elevation: number, radius: number): void {
    const c = Math.cos(elevation);
    this.camera.position.set(
      this.orbitCenter.x + Math.cos(angle) * radius * c,
      this.orbitCenter.y + Math.sin(elevation) * radius,
      this.orbitCenter.z + Math.sin(angle) * radius * c
    );
    this.camera.lookAt(this.orbitCenter);
    this.camera.updateMatrixWorld();
  }

  /**
   * three r187 advances its node frame (and so re-renders frame-scoped nodes such as the
   * scene PassNode) only inside its own requestAnimationFrame loop. Benchmark frames are
   * rendered outside rAF, so do the same bookkeeping as Animation.update() per frame;
   * otherwise every call after the first only redraws the final full-screen quad.
   */
  private advanceNodeFrame(): void {
    const r = this.renderer as unknown as {
      info: { autoReset: boolean; reset(): void; frame: number };
      _nodes: { nodeFrame: { update(): void; frameId: number } };
    };
    if (r.info.autoReset) r.info.reset();
    r._nodes.nodeFrame.update();
    r.info.frame = r._nodes.nodeFrame.frameId;
  }

  private benchPose(t: number): void {
    const angle = t * Math.PI * 2;
    this.placeCamera(angle, 0.22 + 0.1 * Math.sin(angle * 2), this.benchRadius);
  }

  /** Fixed benchmark orbit: one revolution over t in [0, 1). */
  renderFrame(t: number): void {
    this.benchPose(t);
    this.advanceNodeFrame();
    this.post.pipeline.render();
  }

  /** Draw calls and triangles of the last rendered frame (sanity check that work happened). */
  lastFrameInfo(): { drawCalls: number; triangles: number; frame: number } {
    const info = this.renderer.info as unknown as {
      render: { drawCalls: number; triangles: number };
      frame: number;
    };
    return { drawCalls: info.render.drawCalls, triangles: info.render.triangles, frame: info.frame };
  }

  renderInteractive(dtSec: number): void {
    if (this.autoOrbit) this.orbitAngle += dtSec * 0.08;
    this.placeCamera(this.orbitAngle, this.orbitElevation, this.orbitRadius);
    this.post.pipeline.render();
  }

  drag(dx: number, dy: number): void {
    this.orbitAngle += dx * 0.005;
    this.orbitElevation = Math.max(-1.3, Math.min(1.3, this.orbitElevation + dy * 0.005));
  }

  zoom(delta: number): void {
    this.orbitRadius = Math.max(1, Math.min(400, this.orbitRadius * Math.exp(delta * 0.001)));
  }

  async applyConfig(config: BenchConfig): Promise<void> {
    await this.setSplatPath(config.splats);
    this.setStarsVisible(config.stars);
    this.setPost({ aa: config.aa as AaMode, bloom: config.bloom });
    this.setVolume(config.volume ?? 'off', 1);
    this.setFixedSize([config.width, config.height]);
    await waitForGpuIdle(this.renderer);
  }

  describePost(): string {
    return this.post.describe();
  }

  counts(): { splats: number; stars: number } {
    const splats = this.scaleLayer
      ? this.scaleLayer.layer.count
      : this.splatPath === 'none'
        ? 0
        : this.splatArrays.count;
    return { splats, stars: this.stars.mesh.visible ? STAR_COUNT : 0 };
  }

  get currentSplatPath(): SplatPath {
    return this.splatPath;
  }

  get currentVolumeMode(): VolumeMode {
    return this.volumeMode;
  }

  get splatLabel(): string {
    if (this.scaleLayer) return `${this.scaleLayer.layer.count.toLocaleString()} splats. ${SCALE_FIXTURE_LABEL}`;
    if (this.splatPath === 'none') return 'none';
    return `${this.splatArrays.count} Local Bubble splats`;
  }

  /**
   * Reddening check on the GPU path: star pixels rendered without dust and through the dust
   * volume (absorption only, extinction x`scale`, bloom and AA off) at the same pose. Reports how
   * many star pixels were dimmed and how their display-space red/blue ratio changed. A ratio
   * above 1 means the integrated CCM89 extinction reddened them.
   */
  async measureReddening(scale = 16, width = 1280, height = 720, t = 0.35): Promise<Record<string, number>> {
    const scratch = document.createElement('canvas');
    const canvas = this.renderer.domElement;
    const prev = { ...this.postOptions };
    await this.setSplatPath('none');
    this.setStarsVisible(true);
    this.setPost({ aa: 'none', bloom: false });
    this.setFixedSize([width, height]);
    const grab = async () => {
      for (let i = 0; i < 3; i++) {
        this.benchPose(t);
        this.advanceNodeFrame();
        this.post.pipeline.render();
        await waitForGpuIdle(this.renderer);
      }
      this.benchPose(t);
      this.advanceNodeFrame();
      this.post.pipeline.render();
      const px = readCanvas(canvas, scratch);
      await waitForGpuIdle(this.renderer);
      return px;
    };
    this.setVolume('off');
    const clear = await grab();
    this.setVolume('skip', scale, 0);
    const dusty = await grab();
    this.setVolume('off', 1, SPLAT_INTENSITY);
    this.setPost(prev);

    const ratios: number[] = [];
    const dims: number[] = [];
    for (let i = 0; i < clear.length; i += 4) {
      const ra = clear[i]!;
      const ga = clear[i + 1]!;
      const ba = clear[i + 2]!;
      if (Math.max(ra, ga, ba) < 40 || ba < 8 || ra < 8) continue;
      const rb = dusty[i]!;
      const gb = dusty[i + 1]!;
      const bb = dusty[i + 2]!;
      const la = 0.2126 * ra + 0.7152 * ga + 0.0722 * ba;
      const lb = 0.2126 * rb + 0.7152 * gb + 0.0722 * bb;
      // Skip pixels the dust did not dim, and ones too dark for a stable channel ratio.
      if (lb > 0.85 * la || lb < 6 || bb < 4 || rb < 4) continue;
      ratios.push(rb / bb / (ra / ba));
      dims.push(lb / la);
    }
    const median = (xs: number[]) => {
      const s = [...xs].sort((a, b) => a - b);
      return s.length ? s[Math.floor(s.length / 2)]! : Number.NaN;
    };
    return {
      extinctionScale: scale,
      dimmedStarPixels: ratios.length,
      medianDisplayLumaRatio: Math.round(median(dims) * 1000) / 1000,
      medianRedOverBlueChange: Math.round(median(ratios) * 1000) / 1000,
      fractionRedder: ratios.length ? Math.round((ratios.filter((r) => r > 1.02).length / ratios.length) * 1000) / 1000 : 0,
    };
  }

  /**
   * Sorting-pop measurement. For each pose, three's GaussianSplat is rendered with the order it
   * computed 1.7 degrees earlier on the orbit (just under its 1.8 degree re-sort threshold, so
   * this is the staleness it actually shows between sorts) and again with a fresh sort; the
   * difference is what a viewer sees when the re-sort lands. radix (sorted every frame) and the
   * volume (no sort) are rendered twice at each pose as controls.
   */
  async measureSortPops(poses = 24, width = 1280, height = 720): Promise<SortPopReport> {
    const scratch = document.createElement('canvas');
    const canvas = this.renderer.domElement;
    const prevAa = this.postOptions.aa;
    this.setStarsVisible(true);
    this.setPost({ aa: 'none' });
    this.setFixedSize([width, height]);
    const staleDeg = 1.7;
    const accumulate = (into: ImageDiff[], d: ImageDiff) => into.push(d);
    const reduce = (list: ImageDiff[]): ImageDiff => ({
      max: Math.max(0, ...list.map((d) => d.max)),
      mean: list.reduce((s, d) => s + d.mean, 0) / Math.max(1, list.length),
      over2: list.reduce((s, d) => s + d.over2, 0) / Math.max(1, list.length),
      over8: list.reduce((s, d) => s + d.over8, 0) / Math.max(1, list.length),
    });
    const render = async (t: number) => {
      this.benchPose(t);
      this.advanceNodeFrame();
      this.post.pipeline.render();
      const pixels = readCanvas(canvas, scratch);
      await waitForGpuIdle(this.renderer);
      return pixels;
    };

    // three: stale vs fresh order.
    this.setVolume('off');
    await this.setSplatPath('three');
    const mesh = this.threeSplatMesh!;
    const forceSortAt = (t: number) => {
      this.benchPose(t);
      (mesh as unknown as { _sortInitialized: boolean })._sortInitialized = false;
      mesh.updateSort(this.renderer, this.camera);
    };
    mesh.autoSort = false;
    const threeDiffs: ImageDiff[] = [];
    for (let i = 0; i < poses; i++) {
      const t = i / poses;
      forceSortAt(t - staleDeg / 360);
      const stale = await render(t);
      forceSortAt(t);
      const fresh = await render(t);
      accumulate(threeDiffs, diffImages(stale, fresh));
    }
    mesh.autoSort = true;

    // radix: two renders at the same pose.
    await this.setSplatPath('radix');
    const radixDiffs: ImageDiff[] = [];
    for (let i = 0; i < poses; i++) {
      const t = i / poses;
      await render(t);
      const a = await render(t);
      const b = await render(t);
      accumulate(radixDiffs, diffImages(a, b));
    }

    // Volume: two renders at the same pose.
    await this.setSplatPath('none');
    this.setVolume('skip', 1);
    const volumeDiffs: ImageDiff[] = [];
    for (let i = 0; i < poses; i++) {
      const t = i / poses;
      const a = await render(t);
      const b = await render(t);
      accumulate(volumeDiffs, diffImages(a, b));
    }
    this.setVolume('off');
    this.setPost({ aa: prevAa });

    return {
      method: `stale order = sort computed ${staleDeg} deg earlier on the orbit; AA off; 8-bit display values after AgX`,
      resolution: `${width}x${height}`,
      poses,
      threeStaleVsFresh: reduce(threeDiffs),
      radixRepeat: reduce(radixDiffs),
      volumeRepeat: reduce(volumeDiffs),
    };
  }
}

function buildSuite(resolutions: Array<[number, number]>, includeScale: boolean): BenchConfig[] {
  const suite: BenchConfig[] = [];
  for (const [width, height] of resolutions) {
    const add = (id: string, c: Omit<BenchConfig, 'id' | 'width' | 'height'>) =>
      suite.push({ id: `${width}x${height}/${id}`, width, height, ...c });
    add('post-only', { splats: 'none', stars: false, aa: 'fxaa', bloom: true });
    add('stars-100k', { splats: 'none', stars: true, aa: 'fxaa', bloom: true });
    add('splats-three', { splats: 'three', stars: false, aa: 'fxaa', bloom: true });
    add('splats-radix', { splats: 'radix', stars: false, aa: 'fxaa', bloom: true });
    add('volume-skip', { splats: 'none', stars: false, aa: 'fxaa', bloom: true, volume: 'skip' });
    add('volume-noskip', { splats: 'none', stars: false, aa: 'fxaa', bloom: true, volume: 'noskip' });
    add('volume-skip-half', { splats: 'none', stars: false, aa: 'fxaa', bloom: true, volume: 'skip-half' });
    add('full-three/fxaa', { splats: 'three', stars: true, aa: 'fxaa', bloom: true });
    add('full-radix/fxaa', { splats: 'radix', stars: true, aa: 'fxaa', bloom: true });
    add('full-volume-skip/fxaa', { splats: 'none', stars: true, aa: 'fxaa', bloom: true, volume: 'skip' });
    add('full-volume-skip-half/fxaa', { splats: 'none', stars: true, aa: 'fxaa', bloom: true, volume: 'skip-half' });
    add('full-three/no-post-aa-or-bloom', { splats: 'three', stars: true, aa: 'none', bloom: false });
    add('full-three/smaa', { splats: 'three', stars: true, aa: 'smaa', bloom: true });
    add('full-three/traa', { splats: 'three', stars: true, aa: 'traa', bloom: true });
    add('full-three/msaa4', { splats: 'three', stars: true, aa: 'msaa4', bloom: true });
    if (includeScale) {
      add('scale-three-valley-500k', { splats: 'three-valley', stars: false, aa: 'fxaa', bloom: true });
      add('scale-radix-valley-500k', { splats: 'radix-valley', stars: false, aa: 'fxaa', bloom: true });
      add('scale-three-snow-982k', { splats: 'three-snow', stars: false, aa: 'fxaa', bloom: true });
      add('scale-radix-snow-982k', { splats: 'radix-snow', stars: false, aa: 'fxaa', bloom: true });
    }
  }
  return suite;
}

async function main(): Promise<void> {
  const stage = document.getElementById('stage')!;
  const hud = document.getElementById('hud-text')!;
  const app = await SpikeApp.create(stage);
  window.__spike = app;

  const splatSelect = document.getElementById('splat-path') as HTMLSelectElement;
  const dustSelect = document.getElementById('dust') as HTMLSelectElement;
  const extinctionSelect = document.getElementById('extinction') as HTMLSelectElement;
  const aaSelect = document.getElementById('aa') as HTMLSelectElement;
  const bloomBox = document.getElementById('bloom') as HTMLInputElement;
  const starsBox = document.getElementById('stars') as HTMLInputElement;
  const orbitBox = document.getElementById('orbit') as HTMLInputElement;
  const resSelect = document.getElementById('res') as HTMLSelectElement;
  const benchButton = document.getElementById('bench') as HTMLButtonElement;

  const initialPath = (params.get('splats') as SplatPath | null) ?? 'three';
  splatSelect.value = initialPath;
  await app.setSplatPath(initialPath);
  app.setFixedSize(parseResolution(params.get('res')));
  resSelect.value = params.get('res') ?? 'window';
  if (params.get('aa')) {
    aaSelect.value = params.get('aa')!;
    app.setPost({ aa: aaSelect.value as AaMode });
  }
  const initialVolume = (params.get('volume') as VolumeMode | null) ?? 'off';
  const initialExtinction = Number(params.get('ext') ?? 1);
  dustSelect.value = initialVolume;
  extinctionSelect.value = String(initialExtinction);
  app.setVolume(initialVolume, initialExtinction);
  if (params.get('orbit') === '0') {
    orbitBox.checked = false;
    app.autoOrbit = false;
  }

  splatSelect.onchange = () => void app.setSplatPath(splatSelect.value as SplatPath);
  dustSelect.onchange = () => app.setVolume(dustSelect.value as VolumeMode);
  extinctionSelect.onchange = () => app.setVolume(app.currentVolumeMode, Number(extinctionSelect.value));
  aaSelect.onchange = () => app.setPost({ aa: aaSelect.value as AaMode });
  bloomBox.onchange = () => app.setPost({ bloom: bloomBox.checked });
  starsBox.onchange = () => app.setStarsVisible(starsBox.checked);
  orbitBox.onchange = () => (app.autoOrbit = orbitBox.checked);
  resSelect.onchange = () => app.setFixedSize(parseResolution(resSelect.value));
  window.addEventListener('resize', () => app.resize());

  let dragging = false;
  let lastX = 0;
  let lastY = 0;
  stage.addEventListener('pointerdown', (e) => {
    dragging = true;
    lastX = e.clientX;
    lastY = e.clientY;
  });
  window.addEventListener('pointerup', () => (dragging = false));
  window.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    app.drag(e.clientX - lastX, e.clientY - lastY);
    lastX = e.clientX;
    lastY = e.clientY;
  });
  stage.addEventListener('wheel', (e) => app.zoom(e.deltaY), { passive: true });

  // Interactive loop with live GPU timing (resolved asynchronously, one frame late).
  let last = performance.now();
  const frameDeltas: number[] = [];
  let gpuRender = Number.NaN;
  let gpuCompute = Number.NaN;
  let resolving = false;
  let benchRunning = false;
  let benchProgress = 'starting';

  const loop = () => {
    if (benchRunning) return;
    const now = performance.now();
    const dt = (now - last) / 1000;
    frameDeltas.push(now - last);
    if (frameDeltas.length > 120) frameDeltas.shift();
    last = now;
    app.renderInteractive(Math.min(dt, 0.1));
    if (!resolving) {
      resolving = true;
      Promise.all([
        app.renderer.resolveTimestampsAsync('render'),
        app.renderer.resolveTimestampsAsync('compute'),
      ])
        .then(([r, c]) => {
          gpuRender = r ?? Number.NaN;
          gpuCompute = c ?? Number.NaN;
        })
        .catch(() => {})
        .finally(() => (resolving = false));
    }
  };
  app.renderer.setAnimationLoop(loop);

  const size = () => {
    const canvas = app.renderer.domElement;
    return `${canvas.width}x${canvas.height}`;
  };
  const ratios = srgbExtinctionRatios().map((v) => v.toFixed(3)).join('/');
  setInterval(() => {
    const sorted = [...frameDeltas].sort((a, b) => a - b);
    const median = sorted.length ? sorted[Math.floor(sorted.length / 2)]! : Number.NaN;
    hud.textContent = [
      `backend      ${app.backendName}  (three r187, WebGPURenderer)`,
      `drawing buf  ${size()}`,
      `splats       ${app.currentSplatPath}: ${app.splatLabel}`,
      `dust volume  ${app.currentVolumeMode}${app.dustBuild ? `  ${app.dustBuild.dims.join('x')} voxels, CCM89 A/Av r/g/b ${ratios}` : ''}`,
      `stars        ${app.stars.mesh.visible ? STAR_COUNT.toLocaleString() : 0}  synthetic fixture; PSF: ${describePsf()}`,
      `post         ${app.describePost()}`,
      `rAF interval ${median.toFixed(2)} ms median (vsync-capped)`,
      `GPU render   ${gpuRender.toFixed(2)} ms   compute ${gpuCompute.toFixed(2)} ms (timestamp queries)`,
      `visibility   ${document.visibilityState}`,
      ...(app.errors.length ? [`errors       ${app.errors.join('; ')}`] : []),
      ...(benchRunning ? [`benchmark    ${benchProgress}`] : []),
    ].join('\n');
  }, 250);

  const runSuite = async () => {
    if (benchRunning) return;
    benchRunning = true;
    app.renderer.setAnimationLoop(null);
    const restorePath = app.currentSplatPath;
    const restoreVolume = app.currentVolumeMode;
    try {
      const resolutions = (params.get('benchres') ?? '1920x1080,2560x1440,3840x2160')
        .split(',')
        .map((r) => parseResolution(r))
        .filter((r): r is [number, number] => r !== null);
      const quick = params.get('quick') === '1';
      const includeScale = params.get('scale') !== '0';
      const run = await runBenchSuite(app, buildSuite(resolutions, includeScale), {
        ...DEFAULT_BENCH_OPTIONS,
        ...(quick ? { warmupFrames: 20, frames: 60, batches: 3, batchSize: 20 } : {}),
        onProgress: (m) => (benchProgress = m),
      });
      benchProgress = 'sorting-pop measurement';
      const sortPops = params.get('pops') === '0' ? null : await app.measureSortPops();
      benchProgress = 'reddening check';
      const reddening = await app.measureReddening();
      run.environment.notes.push(`Star layer: ${SYNTHETIC_STAR_FIXTURE_LABEL}. PSF: ${describePsf()}.`);
      run.environment.notes.push(
        `Splat intensity ${SPLAT_INTENSITY}; ${app.splatArrays.count} Local Bubble splats after the app loader's stride and cavity rules.`
      );
      if (includeScale) run.environment.notes.push(`${SCALE_FIXTURE_LABEL}.`);
      run.extras = {
        dustVolumeBuild: app.dustBuild,
        voxelSize: VOXEL_SIZE,
        ccm89RatiosRgb: srgbExtinctionRatios(),
        sortPops,
        reddening,
      };
      window.__benchRun = run;
      window.__benchFile = await postBenchRun(run);
    } catch (error) {
      window.__benchError = String(error);
    } finally {
      benchRunning = false;
      await app.setSplatPath(restorePath);
      app.setVolume(restoreVolume);
      app.setFixedSize(parseResolution(resSelect.value));
      last = performance.now();
      app.renderer.setAnimationLoop(loop);
    }
  };
  benchButton.onclick = () => void runSuite();

  window.__spikeReady = true;
  if (params.get('bench') === 'suite') {
    // Let pipelines compile on a few interactive frames first.
    setTimeout(() => void runSuite(), 1500);
  }
}

main().catch((error) => {
  const hud = document.getElementById('hud-text');
  if (hud) hud.textContent = `Failed to start: ${String(error)}`;
  window.__benchError = String(error);
  console.error(error);
});
