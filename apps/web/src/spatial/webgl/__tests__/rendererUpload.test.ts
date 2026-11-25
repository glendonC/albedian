import { afterEach, describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import { SKY_PICTURE_SLICE_ROWS } from '../../sky/skyPicture.js';
import { elementGl, installSkyFakes, named, type FakeGl } from '../../sky/__tests__/skyPictureFakes.js';
import { rendererUpload } from '../skyPictureSwitch.js';

// predsky-starless-one on SPATIAL's loader (lead 21:58): the predicted sky's glow is decoded by the loader's
// decodeSkyPicture and goes up into the portal's own context a slice a frame, its mipmaps on a later frame, every bind
// through three's record of what is bound.

const GLOW = '/textures/eso_milkyway_diffuse_6k.webp';

afterEach(() => vi.unstubAllGlobals());

function portal(rows = 450, width = 900) {
  const fakes = installSkyFakes({ pictureUrl: GLOW, pictureRows: rows, pictureWidth: width });
  const gl = elementGl(fakes, {} as HTMLCanvasElement);
  const bound: unknown[][] = [];
  const renderer = { getContext: () => gl, state: { bindTexture: (...args: unknown[]) => void bound.push(args) } } as unknown as THREE.WebGLRenderer;
  return { fakes, gl, bound, upload: rendererUpload(renderer)! };
}

/** Runs frames until the upload settles. */
async function settled(fakes: ReturnType<typeof installSkyFakes>, asked: Promise<THREE.Texture>) {
  let out: { texture?: THREE.Texture; error?: unknown } | undefined;
  void asked.then((texture) => (out = { texture }), (error) => (out = { error }));
  for (let i = 0; i < 40 && out === undefined; i++) await fakes.frame();
  return out!;
}

describe('the glow goes up into the portal\'s context', () => {
  it('a slice a frame at its own row, sRGB with every mip level, the mipmaps a frame after the last, every bind through three', async () => {
    const { fakes, bound, upload } = portal(450, 900);
    const out = await settled(fakes, upload(GLOW));
    expect(fakes.fetched).toEqual([GLOW]);
    expect(named(fakes.calls, 'createTexture')).toHaveLength(1);
    const storage = named(fakes.calls, 'texStorage2D');
    expect(storage.map((c) => c.args)).toEqual([['TEXTURE_2D', 10, 'SRGB8_ALPHA8', 900, 450]]);
    const slices = named(fakes.calls, 'texSubImage2D');
    expect(slices.map((c) => [c.args[3], c.frame])).toEqual([
      [0, slices[0]!.frame],
      [SKY_PICTURE_SLICE_ROWS, slices[0]!.frame + 1],
      [2 * SKY_PICTURE_SLICE_ROWS, slices[0]!.frame + 2],
    ]);
    expect(slices.every((c) => c.args[4] === 'RGBA' && c.args[5] === 'UNSIGNED_BYTE')).toBe(true);
    const mips = named(fakes.calls, 'generateMipmap');
    expect(mips).toHaveLength(1);
    expect(mips[0]!.frame).toBeGreaterThan(slices.at(-1)!.frame);
    const params = named(fakes.calls, 'texParameteri').map((c) => [c.args[1], c.args[2]]);
    expect(params).toEqual([
      ['TEXTURE_WRAP_S', 'REPEAT'],
      ['TEXTURE_WRAP_T', 'CLAMP_TO_EDGE'],
      ['TEXTURE_MIN_FILTER', 'LINEAR_MIPMAP_LINEAR'],
      ['TEXTURE_MAG_FILTER', 'LINEAR'],
    ]);
    // Bound through three's own state before the storage, each slice and the mipmaps; never behind its back
    expect(bound).toHaveLength(1 + 3 + 1);
    expect(bound.every(([target, texture]) => target === 'TEXTURE_2D' && texture === bound[0]![1])).toBe(true);
    expect(bound[0]![1]).toMatchObject({ id: expect.stringMatching(/^texture-/) });
    expect(named(fakes.calls, 'bindTexture')).toHaveLength(0);
    expect(out.texture).toBeInstanceOf(THREE.ExternalTexture);
    expect((out.texture as THREE.ExternalTexture).sourceTexture).toBe(bound[0]![1]);
    expect(fakes.bitmaps.filter((b) => b.kind === 'slice').every((b) => b.closed)).toBe(true);
  });

  it('gives up when the context is lost on the way, every slice still let go', async () => {
    const { fakes, gl, upload } = portal(450, 900);
    const asked = upload(GLOW);
    let out: { error?: unknown } | undefined;
    void asked.then(() => (out = {}), (error) => (out = { error }));
    for (let i = 0; i < 40 && named(fakes.calls, 'texSubImage2D').length < 1; i++) await fakes.frame();
    (gl as FakeGl).lost = true;
    for (let i = 0; i < 10 && out === undefined; i++) await fakes.frame();
    expect(String(out?.error)).toContain('context lost');
    expect(named(fakes.calls, 'texSubImage2D')).toHaveLength(1);
    expect(named(fakes.calls, 'generateMipmap')).toHaveLength(0);
    expect(fakes.bitmaps.filter((b) => b.kind === 'slice').every((b) => b.closed)).toBe(true);
  });

  it('fails when the picture cannot be had, and there is nothing to upload into without a renderer', async () => {
    const fakes = installSkyFakes({ pictureUrl: GLOW, failFetch: true });
    const gl = elementGl(fakes, {} as HTMLCanvasElement);
    const upload = rendererUpload({ getContext: () => gl, state: { bindTexture: () => undefined } } as unknown as THREE.WebGLRenderer)!;
    const out = await settled(fakes, upload(GLOW));
    expect(out.error).toBeDefined();
    expect(named(fakes.calls, 'texStorage2D')).toHaveLength(0);
    expect(rendererUpload(null)).toBeNull();
  });
});

describe('the WebGL renderer\'s predicted sky', () => {
  it('fetches the glow and puts it up into the renderer\'s own context', async () => {
    const { SparkPortalRenderer } = await import('../../SparkPortalRenderer.js');
    const { createSkyDomeUniforms } = await import('../skyDomeAndCinemaShader.js');
    const { recordPanorama } = await import('../skyPictureSwitch.js');
    const fakes = installSkyFakes({ pictureUrl: GLOW, pictureRows: 300, pictureWidth: 600 });
    const gl = elementGl(fakes, {} as HTMLCanvasElement);
    const renderer = { getContext: () => gl, state: { bindTexture: () => undefined } };
    const uniforms = createSkyDomeUniforms();
    const photograph = new THREE.Texture();
    uniforms.uMilkyWayTex.value = photograph;
    recordPanorama(uniforms, Promise.resolve(photograph));
    const portal = { renderer, skyDomeUniforms: uniforms, catalogStars: null };
    SparkPortalRenderer.prototype.setSkyPicture.call(portal as never, 'starless', 10.5);
    for (let i = 0; i < 40 && !(uniforms.uMilkyWayTex.value instanceof THREE.ExternalTexture); i++) await fakes.frame();
    expect(fakes.fetched).toEqual([GLOW]);
    expect(named(fakes.calls, 'texSubImage2D')).toHaveLength(2);
    expect(uniforms.uMilkyWayTex.value).toBeInstanceOf(THREE.ExternalTexture);
  });
});
