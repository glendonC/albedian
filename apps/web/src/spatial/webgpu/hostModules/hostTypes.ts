/// <reference types="@webgpu/types" />

export type WebGpuHostBackend = 'webgpu' | 'webgl2';

export interface WebGpuHostOptions {
  /** Run the same TSL code on three's WebGL2 backend. Also used when WebGPU is unavailable. */
  forceWebGL?: boolean;
  /** Upper bound on the device pixel ratio of the drawing buffer. */
  maxPixelRatio?: number;
}

export interface HostCapture {
  width: number;
  height: number;
  /** RGBA8 display values, top row first. */
  pixels: Uint8ClampedArray<ArrayBuffer>;
  backend: WebGpuHostBackend;
}

export interface ViewportDimensions {
  width: number;
  height: number;
}

export interface BackendInternals {
  isWebGPUBackend?: boolean;
  device?: GPUDevice;
  gl?: WebGL2RenderingContext;
}

export interface RendererInternals {
  info: { autoReset: boolean; reset(): void; frame: number };
  _nodes?: { nodeFrame: { update(): void; frameId: number } };
}

export const DEFAULT_MAX_PIXEL_RATIO = 2;
export const FALLBACK_WIDTH_PX = 1600;
export const FALLBACK_HEIGHT_PX = 1000;
