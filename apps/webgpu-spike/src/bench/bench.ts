import type { WebGPURenderer } from 'three/webgpu';
import type { AaMode } from '../render/post';
import { roundStats, summarize, type Stats } from './stats';

/** Local Bubble through either path, or a splat-count scaling fixture through either path. */
export type SplatPath =
  | 'none'
  | 'three'
  | 'radix'
  | 'three-valley'
  | 'radix-valley'
  | 'three-snow'
  | 'radix-snow';

/**
 * Dust as a ray-marched density volume: off, with empty-space skipping, without it, or with
 * skipping at half resolution (composited at full resolution).
 */
export type VolumeMode = 'off' | 'skip' | 'noskip' | 'skip-half';

export interface BenchConfig {
  id: string;
  splats: SplatPath;
  stars: boolean;
  aa: AaMode;
  bloom: boolean;
  width: number;
  height: number;
  volume?: VolumeMode;
}

export interface BenchHost {
  renderer: WebGPURenderer;
  applyConfig(config: BenchConfig): Promise<void>;
  /** Renders one frame with the camera at `t` in [0, 1) along the fixed benchmark orbit. */
  renderFrame(t: number): void;
  describePost(): string;
  counts(): { splats: number; stars: number };
  /** Draw calls and triangles of the last rendered frame, to prove the scene was redrawn. */
  lastFrameInfo(): { drawCalls: number; triangles: number; frame: number };
}

export interface BenchRecord {
  config: BenchConfig;
  post: string;
  splatCount: number;
  starCount: number;
  /** Per frame: render() call through queue idle (CPU encode + GPU + queue latency, no overlap). */
  serializedFrameMs: Stats;
  /** Per frame, averaged over batches rendered back to back (CPU and GPU overlap). */
  pipelinedFrameMs: Stats;
  /** JS time inside render() (encode and submit). */
  cpuSubmitMs: Stats;
  /** Sum of three's render-pass GPU timestamps per frame, or null when unavailable. */
  gpuRenderMs: Stats | null;
  /** Sum of three's compute-pass GPU timestamps per frame (splat sort, radix projection). */
  gpuComputeMs: Stats | null;
  /** From renderer.info after the last serialized frame. */
  lastFrame: { drawCalls: number; triangles: number; frame: number };
  visibility: string[];
}

export interface BenchFailure {
  config: BenchConfig;
  error: string;
}

export interface BenchEnvironment {
  userAgent: string;
  backend: 'webgpu' | 'webgl2';
  adapter: Record<string, string> | null;
  devicePixelRatio: number;
  screen: { width: number; height: number };
  rafProbeMs: Stats;
  timestampQueries: boolean;
  notes: string[];
}

export interface BenchRun {
  runId: string;
  startedAt: string;
  finishedAt: string;
  environment: BenchEnvironment;
  method: Record<string, string | number>;
  records: BenchRecord[];
  failures: BenchFailure[];
  /** Extra measurements attached by the host (voxelization cost, sort-pop stability). */
  extras?: Record<string, unknown>;
}

interface BackendInternals {
  isWebGPUBackend?: boolean;
  isWebGLBackend?: boolean;
  device?: GPUDevice;
  gl?: WebGL2RenderingContext;
  trackTimestamp?: boolean;
}

function internals(renderer: WebGPURenderer): BackendInternals {
  return renderer.backend as unknown as BackendInternals;
}

const yieldChannel = typeof MessageChannel !== 'undefined' ? new MessageChannel() : null;
const yieldQueue: Array<() => void> = [];
if (yieldChannel) yieldChannel.port1.onmessage = () => yieldQueue.shift()?.();

/** Yields to the event loop without setTimeout's 4 ms nesting clamp. */
function yieldTask(): Promise<void> {
  if (!yieldChannel) return new Promise((resolve) => setTimeout(resolve, 0));
  return new Promise((resolve) => {
    yieldQueue.push(resolve);
    yieldChannel.port2.postMessage(0);
  });
}

/**
 * Resolves when the GPU has finished all submitted work. WebGPU: queue.onSubmittedWorkDone().
 * WebGL2: gl.finish() does not block in Chrome, so a fence is inserted and polled across tasks
 * (WebGL only updates sync status between tasks).
 */
export async function waitForGpuIdle(renderer: WebGPURenderer): Promise<void> {
  const backend = internals(renderer);
  if (backend.isWebGPUBackend && backend.device) {
    return backend.device.queue.onSubmittedWorkDone();
  }
  const gl = backend.gl;
  if (!gl) return;
  const sync = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0);
  if (!sync) return;
  gl.flush();
  for (;;) {
    const status = gl.clientWaitSync(sync, 0, 0);
    if (status === gl.ALREADY_SIGNALED || status === gl.CONDITION_SATISFIED || status === gl.WAIT_FAILED) break;
    await yieldTask();
  }
  gl.deleteSync(sync);
}

/**
 * Samples requestAnimationFrame intervals. A hidden or background tab may pause rAF entirely,
 * so the probe gives up after `timeoutMs` and reports however many intervals it saw (n = 0
 * means rAF was paused). The benchmark loops themselves do not depend on rAF.
 */
export function rafProbe(frames = 90, timeoutMs = 3000): Promise<Stats> {
  return new Promise((resolve) => {
    const deltas: number[] = [];
    let last = -1;
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      resolve(roundStats(summarize(deltas)));
    };
    const step = (t: number) => {
      if (settled) return;
      if (last >= 0) deltas.push(t - last);
      last = t;
      if (deltas.length >= frames) finish();
      else requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
    setTimeout(finish, timeoutMs);
  });
}

async function adapterInfo(): Promise<Record<string, string> | null> {
  if (typeof navigator === 'undefined' || !navigator.gpu) return null;
  const adapter = await navigator.gpu.requestAdapter({ powerPreference: 'high-performance' });
  if (!adapter) return null;
  const info = adapter.info;
  return {
    vendor: info.vendor,
    architecture: info.architecture,
    device: info.device,
    description: info.description,
  };
}

export interface BenchOptions {
  warmupFrames: number;
  frames: number;
  batches: number;
  batchSize: number;
  onProgress?: (message: string) => void;
}

export const DEFAULT_BENCH_OPTIONS: BenchOptions = {
  warmupFrames: 60,
  frames: 240,
  batches: 8,
  batchSize: 30,
};

async function resolveTimestamps(renderer: WebGPURenderer, enabled: boolean): Promise<[number, number]> {
  if (!enabled) return [Number.NaN, Number.NaN];
  const render = await renderer.resolveTimestampsAsync('render');
  const compute = await renderer.resolveTimestampsAsync('compute');
  return [render ?? Number.NaN, compute ?? Number.NaN];
}

async function measureConfig(
  host: BenchHost,
  config: BenchConfig,
  options: BenchOptions,
  timestamps: boolean
): Promise<BenchRecord> {
  const renderer = host.renderer;
  await host.applyConfig(config);
  const visibility = [document.visibilityState];

  for (let i = 0; i < options.warmupFrames; i++) {
    host.renderFrame((i % options.frames) / options.frames);
    await waitForGpuIdle(renderer);
  }
  await resolveTimestamps(renderer, timestamps);

  const serialized: number[] = [];
  const cpu: number[] = [];
  const gpuRender: number[] = [];
  const gpuCompute: number[] = [];
  for (let i = 0; i < options.frames; i++) {
    const t0 = performance.now();
    host.renderFrame(i / options.frames);
    const t1 = performance.now();
    await waitForGpuIdle(renderer);
    const t2 = performance.now();
    cpu.push(t1 - t0);
    serialized.push(t2 - t0);
    const [r, c] = await resolveTimestamps(renderer, timestamps);
    gpuRender.push(r);
    gpuCompute.push(c);
  }
  const lastFrame = host.lastFrameInfo();
  visibility.push(document.visibilityState);

  const pipelined: number[] = [];
  for (let b = 0; b < options.batches; b++) {
    const t0 = performance.now();
    for (let k = 0; k < options.batchSize; k++) {
      host.renderFrame(((b * options.batchSize + k) % options.frames) / options.frames);
    }
    await waitForGpuIdle(renderer);
    pipelined.push((performance.now() - t0) / options.batchSize);
    await resolveTimestamps(renderer, timestamps);
  }
  visibility.push(document.visibilityState);

  const counts = host.counts();
  return {
    config,
    post: host.describePost(),
    splatCount: counts.splats,
    starCount: counts.stars,
    serializedFrameMs: roundStats(summarize(serialized)),
    pipelinedFrameMs: roundStats(summarize(pipelined)),
    cpuSubmitMs: roundStats(summarize(cpu)),
    gpuRenderMs: timestamps ? roundStats(summarize(gpuRender)) : null,
    gpuComputeMs: timestamps ? roundStats(summarize(gpuCompute)) : null,
    lastFrame,
    visibility,
  };
}

export async function runBenchSuite(
  host: BenchHost,
  configs: readonly BenchConfig[],
  options: BenchOptions = DEFAULT_BENCH_OPTIONS
): Promise<BenchRun> {
  const startedAt = new Date().toISOString();
  const renderer = host.renderer;
  const backend = internals(renderer);
  const isWebGPU = backend.isWebGPUBackend === true;
  const timestamps = backend.trackTimestamp === true;
  const notes: string[] = [];
  if (!isWebGPU) notes.push('WebGL2 backend: GPU idle measured with a fenceSync polled across tasks.');
  if (!timestamps) notes.push('GPU timestamp queries unavailable; gpuRenderMs and gpuComputeMs are null.');

  const environment: BenchEnvironment = {
    userAgent: navigator.userAgent,
    backend: isWebGPU ? 'webgpu' : 'webgl2',
    adapter: await adapterInfo(),
    devicePixelRatio: window.devicePixelRatio,
    screen: { width: window.screen.width, height: window.screen.height },
    rafProbeMs: await rafProbe(),
    timestampQueries: timestamps,
    notes,
  };
  if (environment.rafProbeMs.n === 0) {
    notes.push('requestAnimationFrame was paused (hidden pane or background tab) during the probe.');
  }
  notes.push(`document.visibilityState=${document.visibilityState}, hasFocus=${document.hasFocus()} at start.`);

  const records: BenchRecord[] = [];
  const failures: BenchFailure[] = [];
  for (const [index, config] of configs.entries()) {
    options.onProgress?.(`(${index + 1}/${configs.length}) ${config.id}`);
    try {
      records.push(await measureConfig(host, config, options, timestamps));
    } catch (error) {
      failures.push({ config, error: String(error) });
    }
  }

  return {
    runId: `bench-${environment.backend}-${Date.now()}`,
    startedAt,
    finishedAt: new Date().toISOString(),
    environment,
    method: {
      warmupFrames: options.warmupFrames,
      framesPerConfig: options.frames,
      pipelinedBatches: options.batches,
      pipelinedBatchSize: options.batchSize,
      cameraPath: 'one full orbit (radius 22, gentle elevation wave) per framesPerConfig frames',
      serialized: 'performance.now() around render() plus await queue.onSubmittedWorkDone()',
      pipelined: 'batchSize frames rendered back to back, one queue idle wait, divided by batchSize',
      gpu: 'three.js TimestampQuery sums per frame (render passes, compute passes)',
    },
    records,
    failures,
  };
}

export async function postBenchRun(run: BenchRun): Promise<string | null> {
  try {
    const res = await fetch('/__bench', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(run),
    });
    const body = (await res.json()) as { ok: boolean; file?: string };
    return body.ok ? body.file ?? null : null;
  } catch {
    return null;
  }
}
