import {
  NoColorSpace,
  RGBAFormat,
  RenderTarget,
  UnsignedByteType,
  type WebGPURenderer,
} from 'three/webgpu';
import type { HostCapture, ViewportDimensions, WebGpuHostBackend } from './hostTypes.js';

/**
 * Renders one frame into a temporary RGBA8 render target matching `size`, restores the previous
 * render target in `finally`, reads the pixels back, and normalizes row order to top-down.
 */
export async function captureRenderTargetRgba8(
  renderer: WebGPURenderer,
  size: ViewportDimensions,
  backend: WebGpuHostBackend,
  advanceFrame: () => void,
  render: () => void
): Promise<HostCapture> {
  const { width, height } = size;
  const target = new RenderTarget(width, height, {
    type: UnsignedByteType,
    format: RGBAFormat,
    colorSpace: NoColorSpace,
    depthBuffer: false,
  });
  const previous = renderer.getRenderTarget();
  renderer.setRenderTarget(target);
  try {
    advanceFrame();
    render();
  } finally {
    renderer.setRenderTarget(previous);
  }
  const raw = (await renderer.readRenderTargetPixelsAsync(target, 0, 0, width, height)) as Uint8Array;
  target.dispose();
  const pixels = new Uint8ClampedArray(width * height * 4);
  const row = width * 4;
  // WebGL reads rows bottom up; WebGPU texture copies are top down
  const bottomUp = backend === 'webgl2';
  for (let y = 0; y < height; y++) {
    const src = (bottomUp ? height - 1 - y : y) * row;
    pixels.set(raw.subarray(src, src + row), y * row);
  }
  return { width, height, pixels, backend };
}

/** Encodes a capture as a PNG blob (for downloads and automated snapshots). */
export async function captureToPngBlob(capture: HostCapture): Promise<Blob> {
  const canvas = document.createElement('canvas');
  canvas.width = capture.width;
  canvas.height = capture.height;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('captureToPngBlob: no 2D context');
  ctx.putImageData(new ImageData(capture.pixels, capture.width, capture.height), 0, 0);
  return new Promise((resolve, reject) =>
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('PNG encoding failed'))), 'image/png')
  );
}
