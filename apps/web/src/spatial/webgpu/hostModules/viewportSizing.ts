import { Vector2, type WebGPURenderer } from 'three/webgpu';
import type { ViewportDimensions } from './hostTypes.js';

export interface ViewportState {
  cssSize: ViewportDimensions;
  fixedSize: ViewportDimensions | null;
}

export function createViewportState(): ViewportState {
  return {
    cssSize: { width: 1, height: 1 },
    fixedSize: null,
  };
}

export function resolveDevicePixelRatio(maxPixelRatio: number): number {
  const dpr = typeof window === 'undefined' ? 1 : window.devicePixelRatio || 1;
  return Math.min(dpr, maxPixelRatio);
}

export function readDrawingBufferSize(renderer: WebGPURenderer): ViewportDimensions {
  const size = renderer.getDrawingBufferSize(new Vector2());
  return { width: size.x, height: size.y };
}

export function readCssViewport(state: ViewportState): ViewportDimensions {
  return state.fixedSize ? { ...state.fixedSize } : { ...state.cssSize };
}

export function computeAspectRatio(dimensions: ViewportDimensions): number {
  return dimensions.width / Math.max(1, dimensions.height);
}

export function applyResponsiveResize(
  state: ViewportState,
  renderer: WebGPURenderer,
  width: number,
  height: number,
  maxPixelRatio: number
): boolean {
  if (width <= 0 || height <= 0) return false;
  state.cssSize = { width, height };
  if (state.fixedSize) return false;
  renderer.setPixelRatio(resolveDevicePixelRatio(maxPixelRatio));
  renderer.setSize(width, height, false);
  return true;
}

export function applyFixedDrawingBuffer(
  state: ViewportState,
  renderer: WebGPURenderer,
  size: ViewportDimensions | null,
  maxPixelRatio: number
): boolean {
  state.fixedSize = size;
  if (size) {
    renderer.setPixelRatio(1);
    renderer.setSize(size.width, size.height, false);
    return true;
  }
  return applyResponsiveResize(state, renderer, state.cssSize.width, state.cssSize.height, maxPixelRatio);
}
