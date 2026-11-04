/// <reference types="@webgpu/types" />
import { roundStats, summarize, type Stats } from './benchStats.js';
import type { CislunarWebGpuRenderer } from './CislunarWebGpuRenderer.js';

/**
 * Frame-time harness for the WebGPU host, with the method of apps/webgpu-spike/src/bench/bench.ts
 * (docs/architecture/RENDERER_WEBGPU_SPIKE.md, section 4): frames are rendered from an async
 * loop, not requestAnimationFrame, advancing three's node frame by hand each frame.
 *
 * - Serialized: render() through queue idle per frame (CPU encode + GPU + queue latency).
 * - Pipelined (the headline): `batchSize` frames back to back, one idle wait, divided by
 *   `batchSize`, over `batches` batches.
 * - Camera: the landing's showcase drift over one 15 s cycle per `frames` frames (the clock is
 *   set per frame, so the pose depends on the frame index alone).
 */

export interface FrameTimeBenchOptions {
  resolutions: Array<[number, number]>;
  warmupFrames: number;
  frames: number;
  batches: number;
  batchSize: number;
}

export const DEFAULT_FRAME_TIME_BENCH: FrameTimeBenchOptions = {
  resolutions: [
    [1920, 1080],
    [3840, 2160],
  ],
  warmupFrames: 60,
  frames: 240,
  batches: 8,
  batchSize: 30,
};

/** Period of the showcase yaw drift, sin(0.42 t). */
const SHOWCASE_CYCLE_SEC = (2 * Math.PI) / 0.42;

export interface FrameTimeRecord {
  width: number;
  height: number;
  post: string;
  starsResident: number;
  serializedFrameMs: Stats;
  pipelinedFrameMs: Stats;
  cpuSubmitMs: Stats;
  lastFrame: { drawCalls: number; triangles: number; frame: number };
  visibility: string[];
}

export interface FrameTimeBenchRun {
  runId: string;
  startedAt: string;
  finishedAt: string;
  environment: {
    userAgent: string;
    backend: string;
    adapter: Record<string, string> | null;
    devicePixelRatio: number;
    hasFocus: boolean;
  };
  method: Record<string, string | number>;
  records: FrameTimeRecord[];
}

async function adapterInfo(): Promise<Record<string, string> | null> {
  if (typeof navigator === 'undefined' || !navigator.gpu) return null;
  const adapter = await navigator.gpu.requestAdapter({ powerPreference: 'high-performance' });
  if (!adapter) return null;
  const info = adapter.info;
  return { vendor: info.vendor, architecture: info.architecture, device: info.device, description: info.description };
}

export async function runFrameTimeBench(
  renderer: CislunarWebGpuRenderer,
  options: FrameTimeBenchOptions = DEFAULT_FRAME_TIME_BENCH,
  onProgress?: (message: string) => void
): Promise<FrameTimeBenchRun> {
  await renderer.ready;
  const host = renderer.webGpuHost;
  if (!host) throw new Error('runFrameTimeBench: the WebGPU host is not running');
  const startedAt = new Date().toISOString();
  const clockAt = (i: number) => ((i % options.frames) / options.frames) * SHOWCASE_CYCLE_SEC;
  const render = (i: number) => renderer.renderOffscreenFrame(0, clockAt(i));

  renderer.pauseLoop();
  const records: FrameTimeRecord[] = [];
  try {
    for (const [width, height] of options.resolutions) {
      onProgress?.(`${width}x${height}`);
      host.setFixedDrawingBuffer({ width, height });
      // Stream every catalog tile the path needs before timing, so no fetch or decode lands in a frame
      for (let i = 0; i < options.frames; i += options.frames / 8) {
        render(i);
        renderer.syncView(true);
        await host.waitForGpuIdle();
      }
      await renderer.whenSettled(30_000);

      const visibility = [document.visibilityState as string];
      for (let i = 0; i < options.warmupFrames; i++) {
        render(i);
        await host.waitForGpuIdle();
      }

      const serialized: number[] = [];
      const cpu: number[] = [];
      for (let i = 0; i < options.frames; i++) {
        const t0 = performance.now();
        render(i);
        const t1 = performance.now();
        await host.waitForGpuIdle();
        const t2 = performance.now();
        cpu.push(t1 - t0);
        serialized.push(t2 - t0);
      }
      const info = host.renderer.info as unknown as { render: { drawCalls: number; triangles: number }; frame: number };
      const lastFrame = { drawCalls: info.render.drawCalls, triangles: info.render.triangles, frame: info.frame };
      visibility.push(document.visibilityState);

      const pipelined: number[] = [];
      for (let b = 0; b < options.batches; b++) {
        const t0 = performance.now();
        for (let k = 0; k < options.batchSize; k++) render(b * options.batchSize + k);
        await host.waitForGpuIdle();
        pipelined.push((performance.now() - t0) / options.batchSize);
      }
      visibility.push(document.visibilityState);

      records.push({
        width,
        height,
        post: renderer.describePost(),
        starsResident: renderer.catalogStars.residentStars(),
        serializedFrameMs: roundStats(summarize(serialized)),
        pipelinedFrameMs: roundStats(summarize(pipelined)),
        cpuSubmitMs: roundStats(summarize(cpu)),
        lastFrame,
        visibility,
      });
    }
  } finally {
    host.setFixedDrawingBuffer(null);
    renderer.resumeLoop();
  }

  return {
    runId: `cislunar-${host.backend}-${Date.now()}`,
    startedAt,
    finishedAt: new Date().toISOString(),
    environment: {
      userAgent: navigator.userAgent,
      backend: host.backend,
      adapter: await adapterInfo(),
      devicePixelRatio: window.devicePixelRatio,
      hasFocus: document.hasFocus(),
    },
    method: {
      warmupFrames: options.warmupFrames,
      framesPerResolution: options.frames,
      pipelinedBatches: options.batches,
      pipelinedBatchSize: options.batchSize,
      cameraPath: `showcase drift, one ${SHOWCASE_CYCLE_SEC.toFixed(2)} s cycle per framesPerResolution frames`,
      serialized: 'performance.now() around render() plus a GPU idle wait (queue.onSubmittedWorkDone or a WebGL fence)',
      pipelined: 'batchSize frames rendered back to back, one idle wait, divided by batchSize',
      nodeFrame: 'advanced by hand before every frame (renderOffscreenFrame)',
    },
    records,
  };
}
