// @vitest-environment node
import {
  AgXToneMapping,
  NoColorSpace,
  RGBAFormat,
  RenderTarget,
  SRGBColorSpace,
  UnsignedByteType,
  type Vector2,
} from 'three/webgpu';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as webGpuHostModule from '../WebGpuHost.js';
import {
  WebGpuHost,
  captureToPngBlob,
  type HostCapture,
  type WebGpuHostBackend,
  type WebGpuHostOptions,
} from '../WebGpuHost.js';

interface FakeGlSync {
  readonly kind: 'WebGLSync';
}

interface FakeWebGl2 {
  readonly SYNC_GPU_COMMANDS_COMPLETE: number;
  readonly ALREADY_SIGNALED: number;
  readonly TIMEOUT_EXPIRED: number;
  readonly CONDITION_SATISFIED: number;
  readonly WAIT_FAILED: number;
  fenceSync: ReturnType<typeof vi.fn>;
  flush: ReturnType<typeof vi.fn>;
  clientWaitSync: ReturnType<typeof vi.fn>;
  deleteSync: ReturnType<typeof vi.fn>;
}

interface FakeBackend {
  isWebGPUBackend?: boolean;
  device?: {
    queue: {
      onSubmittedWorkDone: ReturnType<typeof vi.fn>;
    };
  };
  gl?: FakeWebGl2;
}

interface FakeRenderer {
  domElement: {
    style: { width: string; height: string; display: string };
    remove: ReturnType<typeof vi.fn>;
  };
  backend: FakeBackend;
  toneMapping: number;
  toneMappingExposure: number;
  outputColorSpace: string;
  pixelRatio: number;
  cssWidth: number;
  cssHeight: number;
  drawingBufferOverride: { width: number; height: number } | null;
  animationLoop: ((timeMs: number) => void) | null;
  activeRenderTarget: RenderTarget | null;
  info: {
    autoReset: boolean;
    reset: ReturnType<typeof vi.fn>;
    frame: number;
  };
  _nodes?: {
    nodeFrame: {
      update: ReturnType<typeof vi.fn>;
      frameId: number;
    };
  };
  init: ReturnType<typeof vi.fn>;
  setPixelRatio: ReturnType<typeof vi.fn>;
  setSize: ReturnType<typeof vi.fn>;
  getDrawingBufferSize: ReturnType<typeof vi.fn>;
  setAnimationLoop: ReturnType<typeof vi.fn>;
  getRenderTarget: ReturnType<typeof vi.fn>;
  setRenderTarget: ReturnType<typeof vi.fn>;
  readRenderTargetPixelsAsync: ReturnType<typeof vi.fn>;
  dispose: ReturnType<typeof vi.fn>;
}

const mockState = vi.hoisted(() => ({
  nextRenderer: null as FakeRenderer | null,
  lastConstructorOptions: null as Record<string, unknown> | null,
}));

vi.mock('three/webgpu', async (importOriginal) => {
  const actual = await importOriginal<typeof import('three/webgpu')>();
  class MockWebGPURenderer {
    constructor(options: Record<string, unknown> = {}) {
      mockState.lastConstructorOptions = options;
      if (!mockState.nextRenderer) {
        throw new Error('mockState.nextRenderer was not initialized before WebGpuHost.create');
      }
      return mockState.nextRenderer as unknown as MockWebGPURenderer;
    }
  }
  return {
    ...actual,
    WebGPURenderer: MockWebGPURenderer,
  };
});

function createFakeRenderer(backendOverride: FakeBackend = { isWebGPUBackend: true }): FakeRenderer {
  let frameCounter = 10;
  const renderer: FakeRenderer = {
    domElement: {
      style: { width: '', height: '', display: '' },
      remove: vi.fn(),
    },
    backend: backendOverride,
    toneMapping: 0,
    toneMappingExposure: 0,
    outputColorSpace: '',
    pixelRatio: 1,
    cssWidth: 1,
    cssHeight: 1,
    drawingBufferOverride: null,
    animationLoop: null,
    activeRenderTarget: null,
    info: {
      autoReset: true,
      reset: vi.fn(),
      frame: 0,
    },
    _nodes: {
      nodeFrame: {
        update: vi.fn(() => {
          frameCounter += 1;
          renderer._nodes!.nodeFrame.frameId = frameCounter;
        }),
        frameId: frameCounter,
      },
    },
    init: vi.fn(async () => {}),
    setPixelRatio: vi.fn((ratio: number) => {
      renderer.pixelRatio = ratio;
    }),
    setSize: vi.fn((width: number, height: number, _updateStyle?: boolean) => {
      renderer.cssWidth = width;
      renderer.cssHeight = height;
    }),
    getDrawingBufferSize: vi.fn((target: Vector2) => {
      if (renderer.drawingBufferOverride) {
        return target.set(renderer.drawingBufferOverride.width, renderer.drawingBufferOverride.height);
      }
      return target.set(
        Math.round(renderer.cssWidth * renderer.pixelRatio),
        Math.round(renderer.cssHeight * renderer.pixelRatio)
      );
    }),
    setAnimationLoop: vi.fn((loop: ((timeMs: number) => void) | null) => {
      renderer.animationLoop = loop;
    }),
    getRenderTarget: vi.fn(() => renderer.activeRenderTarget),
    setRenderTarget: vi.fn((target: RenderTarget | null) => {
      renderer.activeRenderTarget = target;
    }),
    readRenderTargetPixelsAsync: vi.fn(
      async (_target: RenderTarget, _x: number, _y: number, width: number, height: number) =>
        new Uint8Array(width * height * 4)
    ),
    dispose: vi.fn(),
  };
  return renderer;
}

function createFakeContainer(clientWidth = 1200, clientHeight = 800) {
  const appended: unknown[] = [];
  const container = {
    clientWidth,
    clientHeight,
    appendChild: vi.fn((child: unknown) => {
      appended.push(child);
      return child;
    }),
  } as unknown as HTMLElement;
  return { container, appended };
}

function createFakeWebGl2(statuses: number[]): { gl: FakeWebGl2; sync: FakeGlSync; order: string[] } {
  const sync: FakeGlSync = { kind: 'WebGLSync' };
  const order: string[] = [];
  let pollIndex = 0;
  const gl: FakeWebGl2 = {
    SYNC_GPU_COMMANDS_COMPLETE: 0x9117,
    ALREADY_SIGNALED: 0x911a,
    TIMEOUT_EXPIRED: 0x911b,
    CONDITION_SATISFIED: 0x911c,
    WAIT_FAILED: 0x911d,
    fenceSync: vi.fn((condition: number, flags: number) => {
      order.push(`fenceSync:${condition}:${flags}`);
      return sync;
    }),
    flush: vi.fn(() => {
      order.push('flush');
    }),
    clientWaitSync: vi.fn((_sync: FakeGlSync, flags: number, timeout: number) => {
      const status = statuses[Math.min(pollIndex, statuses.length - 1)]!;
      pollIndex += 1;
      order.push(`clientWaitSync:${flags}:${timeout}->${status}`);
      return status;
    }),
    deleteSync: vi.fn((_sync: FakeGlSync) => {
      order.push('deleteSync');
    }),
  };
  return { gl, sync, order };
}

async function buildHost(
  options: WebGpuHostOptions = {},
  backend: FakeBackend = { isWebGPUBackend: true },
  containerDims: { width: number; height: number } = { width: 1200, height: 800 }
) {
  const renderer = createFakeRenderer(backend);
  mockState.nextRenderer = renderer;
  const { container, appended } = createFakeContainer(containerDims.width, containerDims.height);
  const host = await WebGpuHost.create(container, options);
  return { host, renderer, container, appended };
}

afterEach(() => {
  mockState.nextRenderer = null;
  mockState.lastConstructorOptions = null;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('WebGpuHost module export surface', () => {
  it('exports the exact runtime bindings WebGpuHost and captureToPngBlob', () => {
    expect(Object.keys(webGpuHostModule).sort()).toEqual(['WebGpuHost', 'captureToPngBlob']);
    expect(typeof WebGpuHost).toBe('function');
    expect(typeof captureToPngBlob).toBe('function');

    // Compile-time check for public type exports
    const backendName: WebGpuHostBackend = 'webgpu';
    const hostOptions: WebGpuHostOptions = { forceWebGL: false, maxPixelRatio: 2 };
    expect(backendName).toBe('webgpu');
    expect(hostOptions.maxPixelRatio).toBe(2);
  });
});

describe('WebGpuHost.create, backend detection, viewport getters and onResize', () => {
  it('initializes WebGPURenderer with AgX sRGB output, styles and appends canvas, and falls back to 1600x1000', async () => {
    const { host, renderer, appended } = await buildHost({}, { isWebGPUBackend: true }, { width: 0, height: 0 });

    expect(mockState.lastConstructorOptions).toEqual({
      antialias: false,
      forceWebGL: false,
      powerPreference: 'high-performance',
    });
    expect(renderer.init).toHaveBeenCalledTimes(1);
    expect(renderer.toneMapping).toBe(AgXToneMapping);
    expect(renderer.toneMappingExposure).toBe(1.0);
    expect(renderer.outputColorSpace).toBe(SRGBColorSpace);
    expect(host.renderer).toBe(renderer);
    expect(host.canvas).toBe(renderer.domElement);
    expect(renderer.domElement.style).toEqual({
      width: '100%',
      height: '100%',
      display: 'block',
    });
    expect(appended).toEqual([renderer.domElement]);
    expect(host.cssViewport).toEqual({ width: 1600, height: 1000 });
    expect(renderer.setSize).toHaveBeenCalledWith(1600, 1000, false);
  });

  it('forwards forceWebGL: true to WebGPURenderer and detects webgpu vs webgl2 backends', async () => {
    const webgl = await buildHost({ forceWebGL: true }, { isWebGPUBackend: false });
    expect(mockState.lastConstructorOptions).toEqual({
      antialias: false,
      forceWebGL: true,
      powerPreference: 'high-performance',
    });
    expect(webgl.host.backend).toBe('webgl2');

    const implicitWebgl = await buildHost({}, {});
    expect(implicitWebgl.host.backend).toBe('webgl2');

    const webgpu = await buildHost({}, { isWebGPUBackend: true });
    expect(webgpu.host.backend).toBe('webgpu');
  });

  it('computes drawingBufferSize and aspect with a height floor of 1 to avoid division by zero', async () => {
    vi.stubGlobal('window', { devicePixelRatio: 2 });
    const { host, renderer } = await buildHost({}, { isWebGPUBackend: true }, { width: 960, height: 540 });

    expect(host.drawingBufferSize).toEqual({ width: 1920, height: 1080 });
    expect(host.aspect).toBeCloseTo(16 / 9, 9);

    renderer.drawingBufferOverride = { width: 640, height: 0 };
    expect(host.drawingBufferSize).toEqual({ width: 640, height: 0 });
    expect(host.aspect).toBe(640);
  });

  it('returns a defensive copy from cssViewport when not pinned to a fixed drawing buffer', async () => {
    const { host } = await buildHost({}, { isWebGPUBackend: true }, { width: 1280, height: 720 });
    const viewport = host.cssViewport;
    expect(viewport).toEqual({ width: 1280, height: 720 });

    viewport.width = 42;
    viewport.height = 24;
    expect(host.cssViewport).toEqual({ width: 1280, height: 720 });
  });

  it('subscribes and unsubscribes onResize listeners independently', async () => {
    const { host } = await buildHost();
    const events: string[] = [];
    const unsubscribeA = host.onResize(() => events.push('A'));
    const unsubscribeB = host.onResize(() => events.push('B'));

    host.resize(800, 600);
    expect(events).toEqual(['A', 'B']);

    unsubscribeA();
    unsubscribeA();
    host.resize(1024, 768);
    expect(events).toEqual(['A', 'B', 'B']);

    unsubscribeB();
    host.resize(640, 480);
    expect(events).toEqual(['A', 'B', 'B']);
  });
});

describe('WebGpuHost resize and setFixedDrawingBuffer', () => {
  it('ignores non-positive dimensions without mutating cssViewport, renderer size or listeners', async () => {
    const { host, renderer } = await buildHost({}, { isWebGPUBackend: true }, { width: 1000, height: 700 });
    const onResize = vi.fn();
    host.onResize(onResize);
    renderer.setPixelRatio.mockClear();
    renderer.setSize.mockClear();

    for (const [w, h] of [
      [0, 500],
      [-10, 500],
      [500, 0],
      [500, -1],
      [0, 0],
    ] as const) {
      host.resize(w, h);
    }

    expect(host.cssViewport).toEqual({ width: 1000, height: 700 });
    expect(renderer.setPixelRatio).not.toHaveBeenCalled();
    expect(renderer.setSize).not.toHaveBeenCalled();
    expect(onResize).not.toHaveBeenCalled();
  });

  it('caps devicePixelRatio by maxPixelRatio and falls back to 1 when window or devicePixelRatio is absent', async () => {
    const headless = await buildHost({ maxPixelRatio: 2 }, { isWebGPUBackend: true }, { width: 800, height: 500 });
    expect(headless.renderer.setPixelRatio).toHaveBeenLastCalledWith(1);

    vi.stubGlobal('window', { devicePixelRatio: 0 });
    headless.host.resize(900, 600);
    expect(headless.renderer.setPixelRatio).toHaveBeenLastCalledWith(1);

    vi.stubGlobal('window', { devicePixelRatio: 1.5 });
    headless.host.resize(900, 600);
    expect(headless.renderer.setPixelRatio).toHaveBeenLastCalledWith(1.5);

    vi.stubGlobal('window', { devicePixelRatio: 3.25 });
    headless.host.resize(900, 600);
    expect(headless.renderer.setPixelRatio).toHaveBeenLastCalledWith(2);

    const customCap = await buildHost({ maxPixelRatio: 1.25 }, { isWebGPUBackend: true }, { width: 800, height: 500 });
    expect(customCap.renderer.setPixelRatio).toHaveBeenLastCalledWith(1.25);
    expect(customCap.renderer.setSize).toHaveBeenLastCalledWith(800, 500, false);
  });

  it('pins pixelRatio to 1 during setFixedDrawingBuffer, suppresses responsive resize, and restores latest cssSize on null', async () => {
    vi.stubGlobal('window', { devicePixelRatio: 3 });
    const { host, renderer } = await buildHost({ maxPixelRatio: 2 }, { isWebGPUBackend: true }, { width: 1200, height: 800 });
    const notifyCount = vi.fn();
    host.onResize(notifyCount);

    renderer.setPixelRatio.mockClear();
    renderer.setSize.mockClear();

    host.setFixedDrawingBuffer({ width: 3840, height: 2160 });
    expect(renderer.setPixelRatio).toHaveBeenCalledTimes(1);
    expect(renderer.setPixelRatio).toHaveBeenLastCalledWith(1);
    expect(renderer.setSize).toHaveBeenCalledTimes(1);
    expect(renderer.setSize).toHaveBeenLastCalledWith(3840, 2160, false);
    expect(host.cssViewport).toEqual({ width: 3840, height: 2160 });
    expect(host.drawingBufferSize).toEqual({ width: 3840, height: 2160 });
    expect(notifyCount).toHaveBeenCalledTimes(1);

    // Responsive resize while fixed updates stored cssSize only; renderer and listeners stay untouched
    renderer.setPixelRatio.mockClear();
    renderer.setSize.mockClear();
    host.resize(1440, 900);
    expect(renderer.setPixelRatio).not.toHaveBeenCalled();
    expect(renderer.setSize).not.toHaveBeenCalled();
    expect(host.cssViewport).toEqual({ width: 3840, height: 2160 });
    expect(notifyCount).toHaveBeenCalledTimes(1);

    // Clearing fixed size restores the updated cssSize with capped DPR
    host.setFixedDrawingBuffer(null);
    expect(renderer.setPixelRatio).toHaveBeenCalledTimes(1);
    expect(renderer.setPixelRatio).toHaveBeenLastCalledWith(2);
    expect(renderer.setSize).toHaveBeenCalledTimes(1);
    expect(renderer.setSize).toHaveBeenLastCalledWith(1440, 900, false);
    expect(host.cssViewport).toEqual({ width: 1440, height: 900 });
    expect(host.drawingBufferSize).toEqual({ width: 2880, height: 1800 });
    expect(notifyCount).toHaveBeenCalledTimes(2);
  });
});

describe('WebGpuHost animation loop and advanceNodeFrame', () => {
  it('wires start and stop directly to renderer.setAnimationLoop', async () => {
    const { host, renderer } = await buildHost();
    const frameCallback = vi.fn();

    host.start(frameCallback);
    expect(renderer.setAnimationLoop).toHaveBeenLastCalledWith(frameCallback);
    expect(renderer.animationLoop).toBe(frameCallback);

    host.stop();
    expect(renderer.setAnimationLoop).toHaveBeenLastCalledWith(null);
    expect(renderer.animationLoop).toBeNull();
  });

  it('throws when renderer node manager is not initialized', async () => {
    const { host, renderer } = await buildHost();
    delete renderer._nodes;

    expect(() => host.advanceNodeFrame()).toThrow('WebGpuHost: renderer node manager is not initialized');
  });

  it('resets renderer.info when autoReset is true, skips reset when false, and syncs info.frame to nodeFrame.frameId', async () => {
    const { host, renderer } = await buildHost();
    const callOrder: string[] = [];
    renderer.info.reset.mockImplementation(() => {
      callOrder.push('info.reset');
      renderer.info.frame = 0;
    });
    renderer._nodes!.nodeFrame.update.mockImplementation(() => {
      callOrder.push('nodeFrame.update');
      renderer._nodes!.nodeFrame.frameId = 47;
    });

    renderer.info.autoReset = true;
    host.advanceNodeFrame();
    expect(callOrder).toEqual(['info.reset', 'nodeFrame.update']);
    expect(renderer.info.reset).toHaveBeenCalledTimes(1);
    expect(renderer.info.frame).toBe(47);

    callOrder.length = 0;
    renderer.info.autoReset = false;
    renderer._nodes!.nodeFrame.update.mockImplementation(() => {
      callOrder.push('nodeFrame.update');
      renderer._nodes!.nodeFrame.frameId = 48;
    });
    host.advanceNodeFrame();
    expect(callOrder).toEqual(['nodeFrame.update']);
    expect(renderer.info.reset).toHaveBeenCalledTimes(1);
    expect(renderer.info.frame).toBe(48);
  });
});

describe('WebGpuHost waitForGpuIdle', () => {
  it('awaits device.queue.onSubmittedWorkDone on the WebGPU backend', async () => {
    let resolveQueue: (() => void) | null = null;
    const onSubmittedWorkDone = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveQueue = resolve;
        })
    );
    const { gl } = createFakeWebGl2([0x911a]);
    const { host } = await buildHost({}, { isWebGPUBackend: true, device: { queue: { onSubmittedWorkDone } }, gl });

    let settled = false;
    const idlePromise = host.waitForGpuIdle().then(() => {
      settled = true;
    });
    await Promise.resolve();
    expect(onSubmittedWorkDone).toHaveBeenCalledTimes(1);
    expect(settled).toBe(false);
    expect(gl.fenceSync).not.toHaveBeenCalled();

    resolveQueue!();
    await idlePromise;
    expect(settled).toBe(true);
  });

  it('polls WebGL2 fenceSync across TIMEOUT_EXPIRED ticks, flushes before waiting, and deletes the sync object', async () => {
    const TIMEOUT_EXPIRED = 0x911b;
    const CONDITION_SATISFIED = 0x911c;
    const { gl, sync, order } = createFakeWebGl2([TIMEOUT_EXPIRED, TIMEOUT_EXPIRED, CONDITION_SATISFIED]);
    const { host } = await buildHost({}, { isWebGPUBackend: false, gl });

    await host.waitForGpuIdle();

    expect(gl.fenceSync).toHaveBeenCalledWith(gl.SYNC_GPU_COMMANDS_COMPLETE, 0);
    expect(gl.flush).toHaveBeenCalledTimes(1);
    expect(gl.clientWaitSync).toHaveBeenCalledTimes(3);
    expect(gl.deleteSync).toHaveBeenCalledTimes(1);
    expect(gl.deleteSync).toHaveBeenCalledWith(sync);
    expect(order).toEqual([
      `fenceSync:${gl.SYNC_GPU_COMMANDS_COMPLETE}:0`,
      'flush',
      `clientWaitSync:0:0->${TIMEOUT_EXPIRED}`,
      `clientWaitSync:0:0->${TIMEOUT_EXPIRED}`,
      `clientWaitSync:0:0->${CONDITION_SATISFIED}`,
      'deleteSync',
    ]);
  });

  it('completes WebGL2 fence wait on ALREADY_SIGNALED, WAIT_FAILED, null sync, or headless backend', async () => {
    for (const terminalStatus of [0x911a, 0x911d]) {
      const { gl, sync } = createFakeWebGl2([terminalStatus]);
      const { host } = await buildHost({}, { isWebGPUBackend: false, gl });
      await host.waitForGpuIdle();
      expect(gl.clientWaitSync).toHaveBeenCalledTimes(1);
      expect(gl.deleteSync).toHaveBeenCalledWith(sync);
    }

    const nullSyncGl = createFakeWebGl2([0x911a]).gl;
    nullSyncGl.fenceSync.mockReturnValueOnce(null);
    const nullSyncHost = await buildHost({}, { isWebGPUBackend: false, gl: nullSyncGl });
    await nullSyncHost.host.waitForGpuIdle();
    expect(nullSyncGl.flush).not.toHaveBeenCalled();
    expect(nullSyncGl.clientWaitSync).not.toHaveBeenCalled();
    expect(nullSyncGl.deleteSync).not.toHaveBeenCalled();

    const headlessWebGl = await buildHost({}, { isWebGPUBackend: false });
    await expect(headlessWebGl.host.waitForGpuIdle()).resolves.toBeUndefined();

    const deviceLessWebGpu = await buildHost({}, { isWebGPUBackend: true });
    await expect(deviceLessWebGpu.host.waitForGpuIdle()).resolves.toBeUndefined();
  });

  it('falls back to setTimeout yielding when MessageChannel is unavailable', async () => {
    vi.resetModules();
    vi.stubGlobal('MessageChannel', undefined);
    const freshModule = await import('../WebGpuHost.js');

    const TIMEOUT_EXPIRED = 0x911b;
    const CONDITION_SATISFIED = 0x911c;
    const { gl, sync } = createFakeWebGl2([TIMEOUT_EXPIRED, CONDITION_SATISFIED]);
    const renderer = createFakeRenderer({ isWebGPUBackend: false, gl });
    mockState.nextRenderer = renderer;
    const { container } = createFakeContainer(800, 600);
    const host = await freshModule.WebGpuHost.create(container);

    await host.waitForGpuIdle();
    expect(gl.clientWaitSync).toHaveBeenCalledTimes(2);
    expect(gl.deleteSync).toHaveBeenCalledWith(sync);
  });
});

describe('WebGpuHost capture, dispose and captureToPngBlob', () => {
  it('captures RGBA8 top-down on WebGPU, advancing node frame before render and disposing the temporary target', async () => {
    const { host, renderer } = await buildHost({}, { isWebGPUBackend: true }, { width: 2, height: 3 });
    const previousTarget = new RenderTarget(4, 4);
    renderer.activeRenderTarget = previousTarget;

    // 2x3 image: row 0 = [10..], row 1 = [20..], row 2 = [30..]
    const rawTopDown = new Uint8Array([
      10, 11, 12, 255, 13, 14, 15, 255,
      20, 21, 22, 255, 23, 24, 25, 255,
      30, 31, 32, 255, 33, 34, 35, 255,
    ]);
    let capturedTarget: RenderTarget | null = null;
    const disposeSpy = vi.spyOn(RenderTarget.prototype, 'dispose');

    renderer.readRenderTargetPixelsAsync.mockImplementation(
      async (target: RenderTarget, x: number, y: number, width: number, height: number) => {
        capturedTarget = target;
        expect(renderer.activeRenderTarget).toBe(previousTarget);
        expect([x, y, width, height]).toEqual([0, 0, 2, 3]);
        return rawTopDown;
      }
    );

    let frameDuringRender = -1;
    let targetDuringRender: RenderTarget | null = null;
    const result = await host.capture(() => {
      frameDuringRender = renderer.info.frame;
      targetDuringRender = renderer.activeRenderTarget;
    });

    expect(frameDuringRender).toBe(11);
    expect(targetDuringRender).not.toBeNull();
    expect(targetDuringRender).not.toBe(previousTarget);
    expect(capturedTarget).toBe(targetDuringRender);
    expect(capturedTarget!.width).toBe(2);
    expect(capturedTarget!.height).toBe(3);
    expect(capturedTarget!.depthBuffer).toBe(false);
    expect(capturedTarget!.texture.type).toBe(UnsignedByteType);
    expect(capturedTarget!.texture.format).toBe(RGBAFormat);
    expect(capturedTarget!.texture.colorSpace).toBe(NoColorSpace);
    expect(disposeSpy).toHaveBeenCalledTimes(1);
    expect(renderer.activeRenderTarget).toBe(previousTarget);

    expect(result.width).toBe(2);
    expect(result.height).toBe(3);
    expect(result.backend).toBe('webgpu');
    expect(result.pixels).toBeInstanceOf(Uint8ClampedArray);
    expect([...result.pixels]).toEqual([...rawTopDown]);
  });

  it('flips bottom-up rows to top-down when capturing on WebGL2', async () => {
    const { host, renderer } = await buildHost({}, { isWebGPUBackend: false }, { width: 2, height: 3 });

    // WebGL bottom-up readback: bottom row (y=0 in GL) is row2, middle is row1, top (y=2 in GL) is row0
    const rowBottom = [30, 31, 32, 255, 33, 34, 35, 255];
    const rowMiddle = [20, 21, 22, 255, 23, 24, 25, 255];
    const rowTop = [10, 11, 12, 255, 13, 14, 15, 255];
    renderer.readRenderTargetPixelsAsync.mockResolvedValueOnce(
      new Uint8Array([...rowBottom, ...rowMiddle, ...rowTop])
    );

    const result = await host.capture(() => {});
    expect(result.backend).toBe('webgl2');
    expect([...result.pixels]).toEqual([...rowTop, ...rowMiddle, ...rowBottom]);
  });

  it('restores the previous RenderTarget in finally even when render throws', async () => {
    const { host, renderer } = await buildHost({}, { isWebGPUBackend: true }, { width: 4, height: 4 });
    const previousTarget = new RenderTarget(8, 8);
    renderer.activeRenderTarget = previousTarget;

    await expect(
      host.capture(() => {
        expect(renderer.activeRenderTarget).not.toBe(previousTarget);
        throw new Error('pipeline failure');
      })
    ).rejects.toThrow('pipeline failure');

    expect(renderer.activeRenderTarget).toBe(previousTarget);
    expect(renderer.readRenderTargetPixelsAsync).not.toHaveBeenCalled();
  });

  it('disposes animation loop, resize listeners, renderer and canvas element', async () => {
    const { host, renderer } = await buildHost();
    const resizeListener = vi.fn();
    host.onResize(resizeListener);
    host.start(() => {});

    host.dispose();

    expect(renderer.setAnimationLoop).toHaveBeenLastCalledWith(null);
    expect(renderer.dispose).toHaveBeenCalledTimes(1);
    expect(renderer.domElement.remove).toHaveBeenCalledTimes(1);

    host.resize(640, 480);
    expect(resizeListener).not.toHaveBeenCalled();
  });

  it('encodes HostCapture to a PNG Blob via 2D canvas and reports missing context or failed encoding', async () => {
    class FakeImageData {
      readonly data: Uint8ClampedArray;
      readonly width: number;
      readonly height: number;
      constructor(data: Uint8ClampedArray, width: number, height: number) {
        this.data = data;
        this.width = width;
        this.height = height;
      }
    }
    vi.stubGlobal('ImageData', FakeImageData);

    const sampleCapture: HostCapture = {
      width: 2,
      height: 1,
      pixels: new Uint8ClampedArray([1, 2, 3, 255, 4, 5, 6, 255]),
      backend: 'webgpu',
    };

    const putImageData = vi.fn();
    const expectedBlob = new Blob(['png-bytes'], { type: 'image/png' });
    let createdCanvas: {
      width: number;
      height: number;
      getContext: ReturnType<typeof vi.fn>;
      toBlob: ReturnType<typeof vi.fn>;
    } | null = null;

    vi.stubGlobal('document', {
      createElement: vi.fn((tag: string) => {
        expect(tag).toBe('canvas');
        createdCanvas = {
          width: 0,
          height: 0,
          getContext: vi.fn((kind: string) => (kind === '2d' ? { putImageData } : null)),
          toBlob: vi.fn((cb: (blob: Blob | null) => void, mimeType?: string) => {
            expect(mimeType).toBe('image/png');
            cb(expectedBlob);
          }),
        };
        return createdCanvas;
      }),
    });

    const blob = await captureToPngBlob(sampleCapture);
    expect(blob).toBe(expectedBlob);
    expect(createdCanvas!.width).toBe(2);
    expect(createdCanvas!.height).toBe(1);
    expect(putImageData).toHaveBeenCalledTimes(1);
    const [imgData, x, y] = putImageData.mock.calls[0] as [FakeImageData, number, number];
    expect(imgData.width).toBe(2);
    expect(imgData.height).toBe(1);
    expect(imgData.data).toBe(sampleCapture.pixels);
    expect([x, y]).toEqual([0, 0]);

    // Null 2D context throws exact error message
    vi.stubGlobal('document', {
      createElement: () => ({
        width: 0,
        height: 0,
        getContext: () => null,
        toBlob: vi.fn(),
      }),
    });
    await expect(captureToPngBlob(sampleCapture)).rejects.toThrow('captureToPngBlob: no 2D context');

    // Null blob from canvas.toBlob rejects with exact error message
    vi.stubGlobal('document', {
      createElement: () => ({
        width: 0,
        height: 0,
        getContext: () => ({ putImageData: vi.fn() }),
        toBlob: (cb: (blob: Blob | null) => void) => cb(null),
      }),
    });
    await expect(captureToPngBlob(sampleCapture)).rejects.toThrow('PNG encoding failed');
  });
});
