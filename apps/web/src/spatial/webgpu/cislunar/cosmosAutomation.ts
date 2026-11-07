import type { RenderLayerSet } from '@albedian/contracts';
import type { FrameTimeBenchOptions, FrameTimeBenchRun } from '../frameTimeBench.js';
import { captureToPngBlob, type HostCapture, type WebGpuHostBackend } from '../WebGpuHost.js';
import type { CislunarWebGpuRenderer } from '../CislunarWebGpuRenderer.js';
import { CISLUNAR_RENDER_LAYERS } from './cislunarLayers.js';

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

export function createCosmosAutomation(renderer: CislunarWebGpuRenderer): CosmosAutomation {
  return {
    get backend() {
      return renderer.backend;
    },
    ready: renderer.ready,
    whenSettled: (timeoutMs?: number) => renderer.whenSettled(timeoutMs),
    capture: () => renderer.capture(),
    capturePngDataUrl: async () => blobToDataUrl(await captureToPngBlob(await renderer.capture())),
    describePost: () => renderer.describePost(),
    runBench: async (options) => {
      const bench = await import('../frameTimeBench.js');
      return bench.runFrameTimeBench(renderer, { ...bench.DEFAULT_FRAME_TIME_BENCH, ...options });
    },
    layers: CISLUNAR_RENDER_LAYERS,
    renderer,
  };
}
