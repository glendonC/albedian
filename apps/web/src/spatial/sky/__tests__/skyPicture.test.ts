import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SKY_PICTURE_SLICE_ROWS, forgetKeptSkyPictures, keptSkyPicture, prepareSkyPicture, skyProgram } from '../skyPicture.js';
import { installSkyFakes, named, type FakeBitmap } from './skyPictureFakes.js';

// realsky-upload-one (LEAD ANSWER 18:27): a sky picture is decoded once off the main thread and uploaded once, a slice a
// frame, into one context kept for the page; nothing on a frame waits for the GPU.

const URL_ = '/textures/sky.jpg';

beforeEach(() => forgetKeptSkyPictures());
afterEach(() => {
  vi.unstubAllGlobals();
  forgetKeptSkyPictures();
});

/** Asks for the picture and runs frames until it is kept. */
async function keep(fakes: ReturnType<typeof installSkyFakes>, url = URL_) {
  const asked = keptSkyPicture(url);
  let picture: Awaited<typeof asked> | undefined;
  void asked.then((p) => (picture = p), () => undefined);
  for (let i = 0; i < 40 && picture === undefined; i++) await fakes.frame();
  return picture;
}

describe('a sky picture is decoded off the main thread', () => {
  it('is fetched once and decoded by createImageBitmap with its rows flipped, then cut into slices covering every row', async () => {
    const fakes = installSkyFakes({ pictureUrl: URL_, pictureRows: 450 });
    await keep(fakes);
    expect(fakes.fetched.filter((u) => u === URL_)).toHaveLength(1);
    // The fetched file's bytes, as a Blob
    const decode = vi.mocked(createImageBitmap).mock.calls.find(([src]) => typeof (src as Blob).arrayBuffer === 'function');
    expect(decode?.[1]).toEqual({ imageOrientation: 'flipY', premultiplyAlpha: 'none', colorSpaceConversion: 'default' });
    const whole = fakes.bitmaps.find((b) => b.kind === 'whole')!;
    const slices = fakes.bitmaps.filter((b) => b.kind === 'slice');
    // Each slice cropped from the whole: its first row and how many rows
    const crops = vi.mocked(createImageBitmap).mock.calls.filter((c) => c.length === 5).map((c) => [c[2], c[4]]);
    expect(crops).toEqual([
      [0, SKY_PICTURE_SLICE_ROWS],
      [SKY_PICTURE_SLICE_ROWS, SKY_PICTURE_SLICE_ROWS],
      [2 * SKY_PICTURE_SLICE_ROWS, 450 - 2 * SKY_PICTURE_SLICE_ROWS],
    ]);
    expect(slices.every((s) => s.flipped)).toBe(true);
    // The whole decoded picture is let go once cut, and every slice once it is up
    expect(whole.closed).toBe(true);
    expect(slices.every((s) => s.closed)).toBe(true);
  });
});

describe('a sky picture goes up a slice a frame', () => {
  it('allocates every mip level once, uploads one slice per animation frame at its own row unflipped, and makes the mipmaps on a later frame', async () => {
    const fakes = installSkyFakes({ pictureUrl: URL_, pictureRows: 450, pictureWidth: 900 });
    const picture = await keep(fakes);
    expect(picture).not.toBeNull();
    const storage = named(fakes.calls, 'texStorage2D');
    expect(storage).toHaveLength(1);
    expect(storage[0]!.args).toEqual(['TEXTURE_2D', Math.floor(Math.log2(900)) + 1, 'RGBA8', 900, 450]);
    const subs = named(fakes.calls, 'texSubImage2D');
    expect(subs.map((c) => c.args.slice(0, 6))).toEqual([0, 200, 400].map((y) => ['TEXTURE_2D', 0, 0, y, 'RGBA', 'UNSIGNED_BYTE']));
    expect(subs.map((c) => (c.args[6] as FakeBitmap).y)).toEqual([0, 200, 400]);
    // One a frame, each on a frame of its own
    expect(new Set(subs.map((c) => c.frame)).size).toBe(3);
    expect(subs.every((c) => c.frame > 0)).toBe(true);
    const mip = named(fakes.calls, 'generateMipmap');
    expect(mip).toHaveLength(1);
    expect(mip[0]!.frame).toBeGreaterThan(Math.max(...subs.map((c) => c.frame)));
    // WebGL ignores the flip flag for bitmaps: the rows were flipped as they were decoded
    expect(named(fakes.calls, 'pixelStorei').every((c) => c.args[0] !== 'UNPACK_FLIP_Y_WEBGL' || c.args[1] === false)).toBe(true);
    expect(named(fakes.calls, 'texImage2D')).toHaveLength(0);
    expect(named(fakes.calls, 'getError')).toHaveLength(0);
    expect(named(fakes.calls, 'texParameteri').map((c) => c.args.slice(1))).toEqual([
      ['TEXTURE_WRAP_S', 'REPEAT'],
      ['TEXTURE_WRAP_T', 'CLAMP_TO_EDGE'],
      ['TEXTURE_MIN_FILTER', 'LINEAR_MIPMAP_LINEAR'],
      ['TEXTURE_MAG_FILTER', 'LINEAR'],
    ]);
  });
});

describe('a sky picture is kept for the page', () => {
  it('uploads once however many times it is asked for, and a second picture goes into the same context', async () => {
    const fakes = installSkyFakes({ pictureUrl: URL_ });
    const a = await keep(fakes);
    const b = await keep(fakes);
    expect(b).toBe(a);
    expect(fakes.fetched.filter((u) => u === URL_)).toHaveLength(1);
    expect(named(fakes.calls, 'texStorage2D')).toHaveLength(1);
    expect(fakes.contexts).toHaveLength(1);
  });

  it('starts uploading when prepared, before anything draws', async () => {
    const fakes = installSkyFakes({ pictureUrl: URL_ });
    prepareSkyPicture(URL_);
    await fakes.frame(6);
    expect(named(fakes.calls, 'generateMipmap')).toHaveLength(1);
    const kept = await keptSkyPicture(URL_);
    expect(kept).not.toBeNull();
    expect(named(fakes.calls, 'texStorage2D')).toHaveLength(1);
  });

  it('asks again after a picture that could not be had, rather than keeping the failure', async () => {
    const fakes = installSkyFakes({ pictureUrl: URL_, failFetch: true });
    await expect(keptSkyPicture(URL_)).rejects.toThrow();
    fakes.setFailFetch(false);
    expect(await keep(fakes)).not.toBeNull();
    expect(fakes.fetched.filter((u) => u === URL_)).toHaveLength(2);
  });

  it('makes a new context and uploads again once the kept one is lost', async () => {
    const fakes = installSkyFakes({ pictureUrl: URL_ });
    const first = await keep(fakes);
    fakes.lose();
    const target = { width: 10, height: 10, getContext: () => ({ transferFromImageBitmap: vi.fn() }) } as unknown as HTMLCanvasElement;
    expect(first!.paint(target, () => true)).toBe(false);
    const second = await keep(fakes);
    expect(second).not.toBe(first);
    expect(fakes.contexts).toHaveLength(2);
    expect(named(fakes.calls, 'texStorage2D')).toHaveLength(2);
  });
});

describe('a kept picture paints into the layer canvas without a copy', () => {
  it('sizes the kept canvas to the target, binds the texture, draws and hands the frame over', async () => {
    const fakes = installSkyFakes({ pictureUrl: URL_ });
    const picture = (await keep(fakes))!;
    const transfer = vi.fn();
    const target = { width: 780, height: 1688, getContext: vi.fn(() => ({ transferFromImageBitmap: transfer })) } as unknown as HTMLCanvasElement;
    let seen: unknown;
    expect(picture.paint(target, (gl) => ((seen = [gl.canvas.width, gl.canvas.height]), true))).toBe(true);
    expect(seen).toEqual([780, 1688]);
    expect(target.getContext).toHaveBeenCalledWith('bitmaprenderer');
    expect(transfer).toHaveBeenCalledWith({ frameOf: fakes.contexts[0]!.canvas, size: [780, 1688] });
    expect(named(fakes.calls, 'getError')).toHaveLength(0);
  });

  it('hands nothing over when the draw refuses', async () => {
    const fakes = installSkyFakes({ pictureUrl: URL_ });
    const picture = (await keep(fakes))!;
    const transfer = vi.fn();
    const target = { width: 4, height: 4, getContext: () => ({ transferFromImageBitmap: transfer }) } as unknown as HTMLCanvasElement;
    expect(picture.paint(target, () => false)).toBe(false);
    expect(transfer).not.toHaveBeenCalled();
  });
});

describe('where a browser cannot keep sky pictures', () => {
  it('says so with null: no WebGL 2 offscreen, or bitmaps whose rows it does not flip', async () => {
    installSkyFakes({ pictureUrl: URL_, webgl2: false });
    expect(await keptSkyPicture(URL_)).toBeNull();
    vi.unstubAllGlobals();
    forgetKeptSkyPictures();
    const fakes = installSkyFakes({ pictureUrl: URL_, flipHonoured: false });
    expect(await keptSkyPicture(URL_)).toBeNull();
    expect(fakes.fetched).toHaveLength(0);
  });

  it('says so with null where there is no createImageBitmap or no OffscreenCanvas', async () => {
    installSkyFakes({ pictureUrl: URL_ });
    vi.stubGlobal('createImageBitmap', undefined);
    expect(await keptSkyPicture(URL_)).toBeNull();
    vi.unstubAllGlobals();
    forgetKeptSkyPictures();
    installSkyFakes({ pictureUrl: URL_ });
    vi.stubGlobal('OffscreenCanvas', undefined);
    expect(await keptSkyPicture(URL_)).toBeNull();
  });
});

describe('skyProgram', () => {
  it('compiles a program once per context and shaders', async () => {
    const fakes = installSkyFakes({ pictureUrl: URL_ });
    await keep(fakes);
    const gl = fakes.contexts[0]!;
    const a = skyProgram(gl, 'v', 'f');
    expect(skyProgram(gl, 'v', 'f')).toBe(a);
    expect(skyProgram(gl, 'v', 'g')).not.toBe(a);
    expect(named(fakes.calls, 'linkProgram')).toHaveLength(2);
  });
});
