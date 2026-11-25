import { vi } from 'vitest';

// realsky-upload-one: a recording WebGL 2 context, bitmaps that remember their rows, an OffscreenCanvas and animation
// frames run one at a time, so the tests can say what goes up on which frame and that nothing waits for the GPU.

export interface GlCall {
  readonly name: string;
  readonly args: readonly unknown[];
  readonly frame: number;
}

export interface FakeBitmap {
  readonly kind: 'whole' | 'slice' | 'probe';
  // As a real ImageBitmap: 0 by 0 once closed
  width: number;
  height: number;
  readonly y: number;
  readonly flipped: boolean;
  closed: boolean;
  close(): void;
}

export interface SkyFakes {
  readonly calls: GlCall[];
  readonly contexts: FakeGl[];
  readonly bitmaps: FakeBitmap[];
  readonly fetched: string[];
  /** Runs the animation frames asked for so far, one by one, letting the work each starts settle. */
  frame(count?: number): Promise<void>;
  frames(): number;
  pendingFrames(): number;
  lose(context?: FakeGl): void;
}

export type FakeGl = WebGL2RenderingContext & { readonly canvas: FakeOffscreenCanvas; lost: boolean };

const settle = async () => {
  for (let i = 0; i < 12; i++) await Promise.resolve();
  await new Promise((r) => setTimeout(r, 0));
};

export class FakeOffscreenCanvas {
  width: number;
  height: number;
  listeners: Record<string, (() => void)[]> = {};
  context: FakeGl | null = null;
  transferred = 0;
  static fakes: SkyFakesInternal;
  constructor(width: number, height: number) {
    this.width = width;
    this.height = height;
  }
  addEventListener(type: string, fn: () => void) {
    (this.listeners[type] ??= []).push(fn);
  }
  attributes: unknown = undefined;
  getContext(type: string, attributes?: unknown) {
    const fakes = FakeOffscreenCanvas.fakes;
    if (type === 'webgl2') this.attributes = attributes;
    if (type === '2d') {
      let drawn: FakeBitmap | null = null;
      return {
        drawImage: (b: FakeBitmap) => (drawn = b),
        // A one pixel wide red over blue picture: the top pixel is blue when its rows were flipped
        getImageData: () => ({ data: drawn && drawn.flipped && fakes.flipHonoured ? [0, 0, 255, 255] : [255, 0, 0, 255] }),
      };
    }
    if (type !== 'webgl2' || !fakes.webgl2) return null;
    this.context ??= makeGl(fakes, this);
    return this.context;
  }
  transferToImageBitmap(): { frameOf: FakeOffscreenCanvas; size: number[] } {
    this.transferred += 1;
    return { frameOf: this, size: [this.width, this.height] };
  }
}

class FakeImageData {
  constructor(
    readonly data: unknown,
    readonly width: number,
    readonly height: number,
  ) {}
}

interface SkyFakesInternal extends SkyFakes {
  /** KHR_parallel_shader_compile offered, every link done from this frame on. */
  parallelDoneAt: number | null;
  parallelStagger: boolean;
  frameNo: number;
  flipHonoured: boolean;
  webgl2: boolean;
}

function makeGl(fakes: SkyFakesInternal, canvas: FakeOffscreenCanvas | HTMLCanvasElement): FakeGl {
  let lost = false;
  let n = 0;
  const programsMade: unknown[] = [];
  const target = {
    get canvas() {
      return canvas;
    },
    get lost() {
      return lost;
    },
    set lost(v: boolean) {
      lost = v;
    },
  } as Record<string, unknown>;
  const gl = new Proxy(target, {
    get(t, prop: string | symbol) {
      if (typeof prop === 'symbol') return undefined;
      if (prop in t) return t[prop];
      // Constants by their own names, so a test reads the calls in GL's words
      if (/^[A-Z0-9_]+$/.test(prop)) return prop;
      return (...args: unknown[]) => {
        fakes.calls.push({ name: prop, args, frame: fakes.frameNo });
        if (prop === 'isContextLost') return lost;
        if (prop === 'getExtension') {
          if (args[0] === 'KHR_parallel_shader_compile') return fakes.parallelDoneAt === null ? null : { COMPLETION_STATUS_KHR: 'COMPLETION_STATUS_KHR' };
          if (args[0] === 'WEBGL_lose_context') return { loseContext: () => (fakes.calls.push({ name: 'loseContext', args: [], frame: fakes.frameNo }), (lost = true)) };
          return null;
        }
        if (prop === 'getProgramParameter' && args[1] === 'COMPLETION_STATUS_KHR') return fakes.parallelDoneAt !== null && fakes.frameNo >= fakes.parallelDoneAt + (fakes.parallelStagger ? programsMade.indexOf(args[0]) : 0);
        if (prop === 'createProgram') {
          const made = { id: `program-${++n}` };
          programsMade.push(made);
          return made;
        }
        if (prop === 'getShaderParameter' || prop === 'getProgramParameter') return true;
        if (prop === 'getAttribLocation') return 0;
        if (prop === 'getError') return 0;
        if (prop.startsWith('create')) return { id: `${prop.slice(6).toLowerCase()}-${++n}` };
        if (prop === 'getUniformLocation') return { name: args[1] };
        return undefined;
      };
    },
  }) as unknown as FakeGl;
  fakes.contexts.push(gl);
  return gl;
}

/** A WebGL 2 context for an element canvas (the layer's own road), recording into the same calls. */
export function elementGl(fakes: SkyFakes, canvas: HTMLCanvasElement): FakeGl {
  return makeGl(fakes as SkyFakesInternal, canvas);
}

/**
 * Stubs fetch (the picture `pictureUrl` answers with `pictureRows` rows, anything else with `otherBody`),
 * createImageBitmap, OffscreenCanvas and requestAnimationFrame.
 */
export function installSkyFakes({ pictureUrl, pictureRows = 450, pictureWidth = 900, otherBody = '{}', flipHonoured = true, webgl2 = true, failFetch = false, parallelDoneAt = null, parallelStagger = false }: { pictureUrl: string; pictureRows?: number; pictureWidth?: number; otherBody?: string; flipHonoured?: boolean; webgl2?: boolean; failFetch?: boolean; parallelDoneAt?: number | null; parallelStagger?: boolean }): SkyFakes & { setFailFetch(v: boolean): void } {
  let queue: (() => void)[] = [];
  let frameCount = 0;
  let fail = failFetch;
  const fakes: SkyFakesInternal = {
    calls: [],
    contexts: [],
    bitmaps: [],
    fetched: [],
    parallelDoneAt,
    parallelStagger,
    frameNo: 0,
    flipHonoured,
    webgl2,
    async frame(count = 1) {
      for (let i = 0; i < count; i++) {
        await settle();
        const run = queue;
        queue = [];
        frameCount += 1;
        fakes.frameNo = frameCount;
        for (const fn of run) fn();
        await settle();
      }
    },
    frames: () => frameCount,
    pendingFrames: () => queue.length,
    lose(context) {
      const c = context ?? fakes.contexts[fakes.contexts.length - 1]!;
      c.lost = true;
      for (const fn of (c.canvas as FakeOffscreenCanvas).listeners?.webglcontextlost ?? []) fn();
    },
  };
  FakeOffscreenCanvas.fakes = fakes;
  const bitmap = (b: Omit<FakeBitmap, 'closed' | 'close'>): FakeBitmap => {
    const made: FakeBitmap = { ...b, closed: false, close() { made.closed = true; made.width = 0; made.height = 0; } };
    fakes.bitmaps.push(made);
    return made;
  };
  vi.stubGlobal('OffscreenCanvas', FakeOffscreenCanvas);
  vi.stubGlobal('ImageData', FakeImageData);
  vi.stubGlobal('requestAnimationFrame', (fn: () => void) => {
    queue.push(fn);
    return queue.length;
  });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    fakes.fetched.push(url);
    if (url === pictureUrl) {
      if (fail) return new Response('missing', { status: 404 });
      return new Response('jpeg', { status: 200 });
    }
    return new Response(otherBody, { status: 200 });
  }));
  vi.stubGlobal('createImageBitmap', vi.fn(async (source: unknown, ...rest: unknown[]) => {
    const options = (rest.length === 1 ? rest[0] : rest[4]) as ImageBitmapOptions | undefined;
    if ((source as FakeBitmap).kind === 'whole') {
      const [, y, , h] = rest as number[];
      return bitmap({ kind: 'slice', width: (source as FakeBitmap).width, height: h!, y: y!, flipped: (source as FakeBitmap).flipped });
    }
    if (source instanceof FakeImageData) return bitmap({ kind: 'probe', width: 1, height: 2, y: 0, flipped: options?.imageOrientation === 'flipY' });
    // The fetched file's bytes
    return bitmap({ kind: 'whole', width: pictureWidth, height: pictureRows, y: 0, flipped: options?.imageOrientation === 'flipY' });
  }));
  return Object.assign(fakes, { setFailFetch: (v: boolean) => (fail = v) });
}

export const named = (calls: readonly GlCall[], name: string) => calls.filter((c) => c.name === name);
