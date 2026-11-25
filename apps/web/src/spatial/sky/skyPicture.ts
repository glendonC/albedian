/**
 * realsky-upload-one (LEAD ANSWER 18:27): a sky picture, such as ESO's Milky Way panorama or its baked starless glow, is
 * fetched and decoded off the main thread (`createImageBitmap`), then uploaded once into one WebGL 2 context kept for
 * the page, in row slices of the decoded picture, one slice a frame, its mipmaps made after the last. Nothing waits for
 * the GPU on a frame (no `getError`). Each draw goes from that context to the layer's own canvas without a copy.
 *
 * Before this, an image element was decoded and uploaded whole on the main thread at each mount of Real sky or There,
 * then `getError` waited for the GPU: 113 to 139 ms of upload and 96 to 105 ms of waiting each time on the production
 * build at phone size. The texture made here is byte for byte the one that road made, whole, minified through its mips
 * and magnified (`.lanetools/rs_probe.mjs`, 24 Nov).
 *
 * Rows are flipped as the picture is decoded, so row 0 of the image is the top of the texture (v = 1), as
 * UNPACK_FLIP_Y_WEBGL did for the image element; WebGL ignores that flag for bitmaps. Where a browser cannot keep sky
 * pictures this way (no `createImageBitmap`, no WebGL 2 in an OffscreenCanvas, or bitmaps whose rows it does not flip),
 * `keptSkyPicture` says so with null and the layer draws as before in its own canvas.
 */

/** Rows of the picture per slice: one slice goes up a frame (250 rows took 1.3 to 9.5 ms each on the probe). */
export const SKY_PICTURE_SLICE_ROWS = 200;

/** One slice of a decoded picture: rows `y` to `y + height` of the picture, top row first after the flip. */
export interface SkyPictureSlice {
  readonly y: number;
  readonly height: number;
  readonly bitmap: ImageBitmap;
}

export interface DecodedSkyPicture {
  readonly width: number;
  readonly height: number;
  readonly slices: readonly SkyPictureSlice[];
}

/** Fetches the picture and decodes it off the main thread, rows flipped, cut into slices of `rows` rows. */
export async function decodeSkyPicture(url: string, rows: number = SKY_PICTURE_SLICE_ROWS): Promise<DecodedSkyPicture> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`sky picture: ${response.status}`);
  const blob = await response.blob();
  const whole = await createImageBitmap(blob, { imageOrientation: 'flipY', premultiplyAlpha: 'none', colorSpaceConversion: 'default' });
  // Read before the whole is let go: a closed bitmap says it is 0 by 0
  const { width, height } = whole;
  const slices: SkyPictureSlice[] = [];
  try {
    for (let y = 0; y < height; y += rows) {
      const tall = Math.min(rows, height - y);
      slices.push({ y, height: tall, bitmap: await createImageBitmap(whole, 0, y, width, tall) });
    }
  } catch (error) {
    for (const slice of slices) slice.bitmap.close();
    throw error;
  } finally {
    whole.close();
  }
  return { width, height, slices };
}

/** A sky picture kept in the page's context: `paint` draws with it into a canvas of the layer's own. */
export interface KeptSkyPicture {
  /**
   * Draws at the target's size through the kept context, the picture's texture bound on unit 0, and hands the frame to
   * the target (its `bitmaprenderer`) without a copy. False when the context or the target refuses.
   */
  paint(target: HTMLCanvasElement, draw: (gl: WebGL2RenderingContext, texture: WebGLTexture) => boolean): boolean;
}

interface KeptContext {
  readonly canvas: OffscreenCanvas;
  readonly gl: WebGL2RenderingContext;
  readonly pictures: Map<string, Promise<KeptSkyPicture>>;
  /** The pictures already up, for a draw that must happen in the same frame as the change that asks for it. */
  readonly ready: Map<string, KeptSkyPicture>;
}

let kept: KeptContext | null | undefined;
let rowsFlip: Promise<boolean> | undefined;

/** Whether this browser's bitmaps come out with their rows flipped when asked: a picture one pixel wide, red over blue. */
function bitmapsFlipRows(): Promise<boolean> {
  rowsFlip ??= (async () => {
    try {
      const pixels = new ImageData(new Uint8ClampedArray([255, 0, 0, 255, 0, 0, 255, 255]), 1, 2);
      const bitmap = await createImageBitmap(pixels, { imageOrientation: 'flipY', premultiplyAlpha: 'none' });
      const c2d = new OffscreenCanvas(1, 2).getContext('2d');
      if (!c2d) return false;
      c2d.drawImage(bitmap, 0, 0);
      bitmap.close();
      const [r, , b] = c2d.getImageData(0, 0, 1, 1).data;
      return b! > 200 && r! < 50;
    } catch {
      return false;
    }
  })();
  return rowsFlip;
}

function keptContext(): KeptContext | null {
  // A context lost before its loss event has come is as gone as one whose event has
  if (kept && kept.gl.isContextLost()) kept = undefined;
  if (kept !== undefined) return kept;
  kept = null;
  if (typeof OffscreenCanvas !== 'function' || typeof createImageBitmap !== 'function') return kept;
  const canvas = new OffscreenCanvas(1, 1);
  // The same attributes as the layer's own canvas had, so its frames are the same
  const gl = canvas.getContext('webgl2', { antialias: false, premultipliedAlpha: false }) as WebGL2RenderingContext | null;
  if (!gl) return kept;
  const context: KeptContext = { canvas, gl, pictures: new Map(), ready: new Map() };
  // A lost context takes its textures with it: the next ask makes a new one and uploads again
  canvas.addEventListener('webglcontextlost', () => {
    if (kept === context) kept = undefined;
  });
  kept = context;
  return kept;
}

const nextFrame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

/**
 * Uploads a decoded picture into a new texture of `gl`, a slice a frame, its mipmaps made on the frame after the last,
 * letting each slice go once it is up; rejects if the context is lost on the way. For a picture that must live in a
 * context of its own; `keptSkyPicture` keeps one in the page's context.
 */
export async function uploadSkyPicture(gl: WebGL2RenderingContext, picture: DecodedSkyPicture): Promise<WebGLTexture> {
  const texture = gl.createTexture();
  if (!texture) {
    for (const slice of picture.slices) slice.bitmap.close();
    throw new Error('sky picture: no texture');
  }
  const levels = Math.floor(Math.log2(Math.max(picture.width, picture.height))) + 1;
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.texStorage2D(gl.TEXTURE_2D, levels, gl.RGBA8, picture.width, picture.height);
  let at = 0;
  try {
    for (; at < picture.slices.length; at++) {
      await nextFrame();
      if (gl.isContextLost()) throw new Error('sky picture: context lost');
      const slice = picture.slices[at]!;
      gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
      gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, slice.y, gl.RGBA, gl.UNSIGNED_BYTE, slice.bitmap);
      slice.bitmap.close();
    }
  } finally {
    for (; at < picture.slices.length; at++) picture.slices[at]!.bitmap.close();
  }
  await nextFrame();
  if (gl.isContextLost()) throw new Error('sky picture: context lost');
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.generateMipmap(gl.TEXTURE_2D);
  // Around the sky the picture wraps; over the poles it does not
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  return texture;
}

async function upload(context: KeptContext, url: string): Promise<KeptSkyPicture> {
  const { gl } = context;
  const texture = await uploadSkyPicture(gl, await decodeSkyPicture(url));
  return {
    paint(target, draw) {
      if (gl.isContextLost()) return false;
      const out = target.getContext('bitmaprenderer');
      if (!out) return false;
      // A frame loop paints every frame (the steering dome): the kept canvas is sized only when the target's size changes
      if (context.canvas.width !== target.width) context.canvas.width = target.width;
      if (context.canvas.height !== target.height) context.canvas.height = target.height;
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, texture);
      if (!draw(gl, texture)) return false;
      out.transferFromImageBitmap(context.canvas.transferToImageBitmap());
      return true;
    },
  };
}

/**
 * The picture at `url` kept in the page's context, uploaded the first time it is asked for and then kept; null where
 * this browser cannot keep sky pictures (the caller draws as before). Rejects when the picture cannot be had; the next
 * ask tries again.
 */
export async function keptSkyPicture(url: string): Promise<KeptSkyPicture | null> {
  if (typeof OffscreenCanvas !== 'function' || typeof createImageBitmap !== 'function' || !(await bitmapsFlipRows())) return null;
  const context = keptContext();
  if (!context) return null;
  let picture = context.pictures.get(url);
  if (!picture) {
    picture = upload(context, url);
    context.pictures.set(url, picture);
    picture.then(
      (up) => context.ready.set(url, up),
      () => {
        if (context.pictures.get(url) === picture) context.pictures.delete(url);
      },
    );
  }
  return picture;
}

/**
 * The picture at `url` if it is already up in the page's kept context, now, with no wait: a layer whose canvas a change
 * has just cleared (a new size) draws again in the same frame from it (DECIDED-BY-LEAD 19:45: no painted frame without
 * a sky). Null while it is not up, where pictures are not kept, or once that context is lost.
 */
export function keptSkyPictureNow(url: string): KeptSkyPicture | null {
  if (!kept || kept.gl.isContextLost()) return null;
  return kept.ready.get(url) ?? null;
}

/** Starts the picture's upload ahead of any motion, while the screen is at rest; nothing if it cannot be kept. */
export function prepareSkyPicture(url: string): void {
  keptSkyPicture(url).catch(() => undefined);
}

const programs = new WeakMap<WebGL2RenderingContext, Map<string, WebGLProgram | null>>();

/** The program for these shaders in this context, compiled once; null when the browser refuses it. */
export function skyProgram(gl: WebGL2RenderingContext, vertex: string, fragment: string): WebGLProgram | null {
  let byContext = programs.get(gl);
  if (!byContext) {
    byContext = new Map();
    programs.set(gl, byContext);
  }
  const key = `${vertex}\n//\n${fragment}`;
  if (byContext.has(key)) return byContext.get(key)!;
  const compile = (type: number, source: string) => {
    const shader = gl.createShader(type);
    if (!shader) return null;
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    return gl.getShaderParameter(shader, gl.COMPILE_STATUS) ? shader : null;
  };
  const vs = compile(gl.VERTEX_SHADER, vertex);
  const fs = compile(gl.FRAGMENT_SHADER, fragment);
  const program = vs && fs ? gl.createProgram() : null;
  if (program && vs && fs) {
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.linkProgram(program);
  }
  const linked = program && gl.getProgramParameter(program, gl.LINK_STATUS) ? program : null;
  byContext.set(key, linked);
  return linked;
}

/** A program's two shaders' sources. */
export type ProgramSources = readonly [vertex: string, fragment: string];

/** How long a warm context is kept where the browser cannot say its links are done. */
export const WARM_GIVE_UP_MS = 4000;

/** Compiles and links one program, reading nothing back, so the call returns at once. */
export function issueProgram(gl: WebGL2RenderingContext, vertex: string, fragment: string): WebGLProgram | null {
  const vs = gl.createShader(gl.VERTEX_SHADER);
  const fs = gl.createShader(gl.FRAGMENT_SHADER);
  const program = gl.createProgram();
  if (!vs || !fs || !program) return null;
  gl.shaderSource(vs, vertex);
  gl.compileShader(vs);
  gl.shaderSource(fs, fragment);
  gl.compileShader(fs);
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  return program;
}

/**
 * realsky-upload-one (LEAD ANSWER 19:21): links programs once while the screen is at rest, in a throwaway context of
 * their own, reading nothing back, so no frame waits for them; the context is let go once every link is done (polled a
 * frame at a time where the browser can say, KHR_parallel_shader_compile) or after WARM_GIVE_UP_MS. A later context
 * that links the same sources finds them linked: in Chrome a cold link took 86 to 141 ms and the same source in a new
 * context 4.1 to 4.5 ms, and six cold links issued this way kept every frame at 17.8 ms or less
 * (`.lanetools/rs_probe2.mjs`, `rs_probe3.mjs`, 24 Nov). `more` issues further programs on the same context.
 */
export function warmPrograms(
  pairs: readonly ProgramSources[],
  more?: (gl: WebGL2RenderingContext) => readonly WebGLProgram[],
  // The attributes of the context that will link them for real, so nothing about the context differs
  attributes: WebGLContextAttributes = { antialias: false, premultipliedAlpha: false },
): boolean {
  if (typeof OffscreenCanvas !== 'function') return false;
  const gl = new OffscreenCanvas(1, 1).getContext('webgl2', attributes) as WebGL2RenderingContext | null;
  if (!gl) return false;
  const programs = pairs.map(([vertex, fragment]) => issueProgram(gl, vertex, fragment)).filter((p): p is WebGLProgram => p !== null);
  programs.push(...(more?.(gl) ?? []));
  const parallel = (gl.getExtension('KHR_parallel_shader_compile') ?? null) as { COMPLETION_STATUS_KHR: number } | null;
  const started = performance.now();
  const check = () => {
    const done = parallel !== null && programs.every((p) => gl.getProgramParameter(p, parallel.COMPLETION_STATUS_KHR) === true);
    if (done || gl.isContextLost() || performance.now() - started > WARM_GIVE_UP_MS) {
      gl.getExtension('WEBGL_lose_context')?.loseContext();
      return;
    }
    requestAnimationFrame(check);
  };
  requestAnimationFrame(check);
  return true;
}

/** Tests only: forget the kept context and the flip check. */
export function forgetKeptSkyPictures(): void {
  kept = undefined;
  rowsFlip = undefined;
}
