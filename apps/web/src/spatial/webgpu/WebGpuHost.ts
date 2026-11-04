/// <reference types="@webgpu/types" />
import {
  AgXToneMapping,
  NoColorSpace,
  RGBAFormat,
  RenderTarget,
  SRGBColorSpace,
  UnsignedByteType,
  Vector2,
  WebGPURenderer,
} from 'three/webgpu';

/**
 * Owns three's `WebGPURenderer` for the cosmos: backend choice, the frame loop, canvas and
 * drawing-buffer sizing, GPU idle waits and readback captures.
 *
 * Measured in the spike (docs/architecture/RENDERER_WEBGPU_SPIKE.md, section 8): r187 advances
 * its node frame only inside its own requestAnimationFrame loop, so frame-scoped nodes (the
 * scene pass, bloom) re-render outside `setAnimationLoop` only when the frame is advanced by
 * hand; WebGPU has no `preserveDrawingBuffer`, so screenshots read a render target back.
 */

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

interface BackendInternals {
  isWebGPUBackend?: boolean;
  device?: GPUDevice;
  gl?: WebGL2RenderingContext;
}

interface RendererInternals {
  info: { autoReset: boolean; reset(): void; frame: number };
  _nodes?: { nodeFrame: { update(): void; frameId: number } };
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

export class WebGpuHost {
  readonly renderer: WebGPURenderer;
  readonly canvas: HTMLCanvasElement;
  private readonly maxPixelRatio: number;
  private cssSize = { width: 1, height: 1 };
  private fixedSize: { width: number; height: number } | null = null;
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
    const host = new WebGpuHost(renderer, options.maxPixelRatio ?? 2);
    host.resize(container.clientWidth || 1600, container.clientHeight || 1000);
    return host;
  }

  get backend(): WebGpuHostBackend {
    return (this.renderer.backend as unknown as BackendInternals).isWebGPUBackend ? 'webgpu' : 'webgl2';
  }

  /** Drawing-buffer size in device pixels. */
  get drawingBufferSize(): { width: number; height: number } {
    const size = this.renderer.getDrawingBufferSize(new Vector2());
    return { width: size.x, height: size.y };
  }

  /** CSS size of the canvas, the space pointer input and overlays use. */
  get cssViewport(): { width: number; height: number } {
    return this.fixedSize ?? { ...this.cssSize };
  }

  get aspect(): number {
    const { width, height } = this.drawingBufferSize;
    return width / Math.max(1, height);
  }

  onResize(listener: () => void): () => void {
    this.resizeListeners.push(listener);
    return () => {
      this.resizeListeners = this.resizeListeners.filter((l) => l !== listener);
    };
  }

  /** Canvas CSS size; the drawing buffer follows at min(devicePixelRatio, maxPixelRatio). */
  resize(width: number, height: number): void {
    if (width <= 0 || height <= 0) return;
    this.cssSize = { width, height };
    if (this.fixedSize) return;
    const dpr = typeof window === 'undefined' ? 1 : window.devicePixelRatio || 1;
    this.renderer.setPixelRatio(Math.min(dpr, this.maxPixelRatio));
    this.renderer.setSize(width, height, false);
    this.notifyResize();
  }

  /**
   * Pins the drawing buffer to exact device pixels (pixel ratio 1), for benchmarks and stills at
   * a stated resolution; null returns to the canvas size.
   */
  setFixedDrawingBuffer(size: { width: number; height: number } | null): void {
    this.fixedSize = size;
    if (size) {
      this.renderer.setPixelRatio(1);
      this.renderer.setSize(size.width, size.height, false);
      this.notifyResize();
    } else {
      this.resize(this.cssSize.width, this.cssSize.height);
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
    const r = this.renderer as unknown as RendererInternals;
    if (!r._nodes) throw new Error('WebGpuHost: renderer node manager is not initialized');
    if (r.info.autoReset) r.info.reset();
    r._nodes.nodeFrame.update();
    r.info.frame = r._nodes.nodeFrame.frameId;
  }

  /**
   * Resolves when the GPU has finished all submitted work. WebGPU: queue.onSubmittedWorkDone().
   * WebGL2: Chrome's gl.finish() does not block, so a fence is polled across tasks.
   */
  async waitForGpuIdle(): Promise<void> {
    const backend = this.renderer.backend as unknown as BackendInternals;
    if (backend.isWebGPUBackend && backend.device) {
      await backend.device.queue.onSubmittedWorkDone();
      return;
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
   * Renders one frame into an RGBA8 target the size of the drawing buffer and reads it back.
   * `render` draws the frame (for example `pipeline.render()`); the node frame is advanced
   * first, so the capture is a complete new frame even outside the animation loop.
   */
  async capture(render: () => void): Promise<HostCapture> {
    const { width, height } = this.drawingBufferSize;
    const target = new RenderTarget(width, height, {
      type: UnsignedByteType,
      format: RGBAFormat,
      colorSpace: NoColorSpace,
      depthBuffer: false,
    });
    const previous = this.renderer.getRenderTarget();
    this.renderer.setRenderTarget(target);
    try {
      this.advanceNodeFrame();
      render();
    } finally {
      this.renderer.setRenderTarget(previous);
    }
    const raw = (await this.renderer.readRenderTargetPixelsAsync(target, 0, 0, width, height)) as Uint8Array;
    target.dispose();
    const pixels = new Uint8ClampedArray(width * height * 4);
    const row = width * 4;
    // WebGL reads rows bottom up; WebGPU texture copies are top down
    const bottomUp = this.backend === 'webgl2';
    for (let y = 0; y < height; y++) {
      const src = (bottomUp ? height - 1 - y : y) * row;
      pixels.set(raw.subarray(src, src + row), y * row);
    }
    return { width, height, pixels, backend: this.backend };
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
