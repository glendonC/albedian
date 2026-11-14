/// <reference types="@webgpu/types" />
import { AgXToneMapping, SRGBColorSpace, WebGPURenderer } from 'three/webgpu';
import { captureRenderTargetRgba8, captureToPngBlob } from './hostModules/frameCapture.js';
import { advanceRendererNodeFrame, detectHostBackend, waitForBackendGpuIdle } from './hostModules/gpuSync.js';
import {
  DEFAULT_MAX_PIXEL_RATIO,
  FALLBACK_HEIGHT_PX,
  FALLBACK_WIDTH_PX,
  type HostCapture,
  type WebGpuHostBackend,
  type WebGpuHostOptions,
} from './hostModules/hostTypes.js';
import {
  applyFixedDrawingBuffer,
  applyResponsiveResize,
  computeAspectRatio,
  createViewportState,
  readCssViewport,
  readDrawingBufferSize,
  type ViewportState,
} from './hostModules/viewportSizing.js';

export { captureToPngBlob };
export type { HostCapture, WebGpuHostBackend, WebGpuHostOptions };

/**
 * Owns three's `WebGPURenderer` for the cosmos: backend choice, the frame loop, canvas and
 * drawing-buffer sizing, GPU idle waits and readback captures.
 *
 * Measured in the spike (docs/architecture/RENDERER_WEBGPU_SPIKE.md, section 8): r187 advances
 * its node frame only inside its own requestAnimationFrame loop, so frame-scoped nodes (the
 * scene pass, bloom) re-render outside `setAnimationLoop` only when the frame is advanced by
 * hand; WebGPU has no `preserveDrawingBuffer`, so screenshots read a render target back.
 */
export class WebGpuHost {
  readonly renderer: WebGPURenderer;
  readonly canvas: HTMLCanvasElement;
  private readonly maxPixelRatio: number;
  private readonly viewport: ViewportState = createViewportState();
  private resizeListeners: Array<() => void> = [];

  private constructor(renderer: WebGPURenderer, maxPixelRatio: number) {
    this.renderer = renderer;
    this.canvas = renderer.domElement;
    this.maxPixelRatio = maxPixelRatio;
  }

  static async create(container: HTMLElement, options: WebGpuHostOptions = {}): Promise<WebGpuHost> {
    // Without forceWebGL, three falls back to WebGL2 by itself when WebGPU cannot start
    const renderer = new WebGPURenderer({
      antialias: false,
      forceWebGL: options.forceWebGL === true,
      powerPreference: 'high-performance',
    });
    await renderer.init();
    renderer.toneMapping = AgXToneMapping;
    renderer.toneMappingExposure = 1.0;
    renderer.outputColorSpace = SRGBColorSpace;
    const canvas = renderer.domElement;
    canvas.style.width = '100%';
    canvas.style.height = '100%';
    canvas.style.display = 'block';
    container.appendChild(canvas);
    const host = new WebGpuHost(renderer, options.maxPixelRatio ?? DEFAULT_MAX_PIXEL_RATIO);
    host.resize(container.clientWidth || FALLBACK_WIDTH_PX, container.clientHeight || FALLBACK_HEIGHT_PX);
    return host;
  }

  get backend(): WebGpuHostBackend {
    return detectHostBackend(this.renderer);
  }

  /** Drawing-buffer size in device pixels. */
  get drawingBufferSize(): { width: number; height: number } {
    return readDrawingBufferSize(this.renderer);
  }

  /** CSS size of the canvas, the space pointer input and overlays use. */
  get cssViewport(): { width: number; height: number } {
    return readCssViewport(this.viewport);
  }

  get aspect(): number {
    return computeAspectRatio(this.drawingBufferSize);
  }

  onResize(listener: () => void): () => void {
    this.resizeListeners.push(listener);
    return () => {
      this.resizeListeners = this.resizeListeners.filter((l) => l !== listener);
    };
  }

  /** Canvas CSS size; the drawing buffer follows at min(devicePixelRatio, maxPixelRatio). */
  resize(width: number, height: number): void {
    if (applyResponsiveResize(this.viewport, this.renderer, width, height, this.maxPixelRatio)) {
      this.notifyResize();
    }
  }

  /**
   * Pins the drawing buffer to exact device pixels (pixel ratio 1), for benchmarks and stills at
   * a stated resolution; null returns to the canvas size.
   */
  setFixedDrawingBuffer(size: { width: number; height: number } | null): void {
    if (applyFixedDrawingBuffer(this.viewport, this.renderer, size, this.maxPixelRatio)) {
      this.notifyResize();
    }
  }

  /** Runs `frame` from three's animation loop, which advances the node frame before each call. */
  start(frame: (timeMs: number) => void): void {
    void this.renderer.setAnimationLoop(frame);
  }

  stop(): void {
    void this.renderer.setAnimationLoop(null);
  }

  /**
   * Does what three's animation loop does before each frame: resets per-frame info and
   * advances the node frame. Call once per frame rendered outside `start()` (captures,
   * benchmarks, video export); without it every later frame redraws only the final quad.
   */
  advanceNodeFrame(): void {
    advanceRendererNodeFrame(this.renderer);
  }

  /**
   * Resolves when the GPU has finished all submitted work. WebGPU: queue.onSubmittedWorkDone().
   * WebGL2: Chrome's gl.finish() does not block, so a fence is polled across tasks.
   */
  async waitForGpuIdle(): Promise<void> {
    await waitForBackendGpuIdle(this.renderer);
  }

  /**
   * Renders one frame into an RGBA8 target the size of the drawing buffer and reads it back.
   * `render` draws the frame (for example `pipeline.render()`); the node frame is advanced
   * first, so the capture is a complete new frame even outside the animation loop.
   */
  async capture(render: () => void): Promise<HostCapture> {
    return captureRenderTargetRgba8(
      this.renderer,
      this.drawingBufferSize,
      this.backend,
      () => this.advanceNodeFrame(),
      render
    );
  }

  dispose(): void {
    this.stop();
    this.resizeListeners = [];
    this.renderer.dispose();
    this.canvas.remove();
  }

  private notifyResize(): void {
    for (const listener of this.resizeListeners) listener();
  }
}
