import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { createSkyDomeUniforms } from '../skyDomeAndCinemaShader.js';
import { heardLimitOf, recordPanorama, setSkyPictureOf } from '../skyPictureSwitch.js';

// predsky-starless-one (DECIDED-BY-LEAD 24 Nov 18:04 and 18:14): the predicted sky's dome takes the starless glow, put on
// only once the panorama's registration is bound, kept once loaded; the catalog reaches 10.5 there and 6.5 elsewhere;
// the music hears the limit the field had before the predicted sky raised it.

const GLOW = '/textures/eso_milkyway_diffuse_6k.webp';

/** Stand-ins for a texture loader (the panorama) and a sky picture upload (the glow): each waits until the test answers it. */
function loader() {
  const asked: Array<{ url: string; ok: (t: THREE.Texture) => void; fail: (e: unknown) => void }> = [];
  const l = { load: (url: string, ok: (t: THREE.Texture) => void, _p: unknown, fail: (e: unknown) => void) => void asked.push({ url, ok, fail }) };
  const up = (url: string) => new Promise<THREE.Texture>((ok, fail) => void asked.push({ url, ok, fail }));
  return { asked, loader: l as unknown as THREE.TextureLoader, up };
}

function field(limit = 6.5) {
  const f = { limit, getLimitingMagnitude: () => f.limit, setLimitingMagnitude: (m: number) => void (f.limit = m) };
  return f;
}

const settle = () => new Promise((r) => setTimeout(r, 0));

describe('the sky dome\'s picture', () => {
  it('puts the glow on only once the panorama is bound, as its own settings, and the photograph back on leaving', async () => {
    const { asked, up } = loader();
    const uniforms = createSkyDomeUniforms();
    const photograph = new THREE.Texture();
    let bind!: (t: THREE.Texture | null) => void;
    recordPanorama(uniforms, new Promise((r) => (bind = r)));
    const renderer = {};
    setSkyPictureOf(renderer, up, uniforms, field(), 'starless', 10.5);
    expect(asked.map((a) => a.url)).toEqual([GLOW]);
    const glow = new THREE.Texture();
    asked[0]!.ok(glow);
    await settle();
    // Not before the registration is bound: the dome has no picture yet
    expect(uniforms.uMilkyWayTex.value).toBeNull();
    uniforms.uMilkyWayTex.value = photograph;
    bind(photograph);
    await settle();
    expect(uniforms.uMilkyWayTex.value).toBe(glow);
    setSkyPictureOf(renderer, up, uniforms, field(), 'photograph', 6.5);
    await settle();
    expect(uniforms.uMilkyWayTex.value).toBe(photograph);
    // Kept: asked again, the glow goes back on with no second load
    setSkyPictureOf(renderer, up, uniforms, field(), 'starless', 10.5);
    await settle();
    expect(asked).toHaveLength(1);
    expect(uniforms.uMilkyWayTex.value).toBe(glow);
  });

  it('leaves the dome as it is when the glow does not come, and asks again next time', async () => {
    const { asked, up } = loader();
    const uniforms = createSkyDomeUniforms();
    const photograph = new THREE.Texture();
    uniforms.uMilkyWayTex.value = photograph;
    recordPanorama(uniforms, Promise.resolve(photograph));
    const renderer = {};
    setSkyPictureOf(renderer, up, uniforms, field(), 'starless', 10.5);
    asked[0]!.fail(new Error('404'));
    await settle();
    expect(uniforms.uMilkyWayTex.value).toBe(photograph);
    setSkyPictureOf(renderer, up, uniforms, field(), 'photograph', 6.5);
    setSkyPictureOf(renderer, up, uniforms, field(), 'starless', 10.5);
    expect(asked).toHaveLength(2);
  });

  it('never puts the glow on a dome whose panorama failed to place', async () => {
    const { asked, up } = loader();
    const uniforms = createSkyDomeUniforms();
    recordPanorama(uniforms, Promise.resolve(null));
    setSkyPictureOf({}, up, uniforms, field(), 'starless', 10.5);
    asked[0]!.ok(new THREE.Texture());
    await settle();
    expect(uniforms.uMilkyWayTex.value).toBeNull();
  });
});

describe('the catalog\'s limit, the planets\' fill and what the page is told, all with the glow', () => {
  it('raises the limit to 10.5, fills the planets and says starless only once the glow is on; all back on leaving', async () => {
    const { asked, up } = loader();
    const uniforms = createSkyDomeUniforms();
    const photograph = new THREE.Texture();
    uniforms.uMilkyWayTex.value = photograph;
    recordPanorama(uniforms, Promise.resolve(photograph));
    const renderer = {};
    const f = field(6.5);
    const told: string[] = [];
    setSkyPictureOf(renderer, up, uniforms, f, 'starless', 10.5, (shown) => told.push(shown));
    await settle();
    // Asked for, not yet on: the photograph at its own limit, nothing told
    expect([f.limit, uniforms.uFillPlanets.value, told]).toEqual([6.5, 0, []]);
    asked[0]!.ok(new THREE.Texture());
    await settle();
    expect([f.limit, uniforms.uFillPlanets.value, told]).toEqual([10.5, 1, ['starless']]);
    expect(heardLimitOf(renderer, f)).toBe(6.5);
    setSkyPictureOf(renderer, up, uniforms, f, 'photograph', 6.5, (shown) => told.push(shown));
    expect([f.limit, uniforms.uFillPlanets.value]).toEqual([6.5, 0]);
    await settle();
    expect(told.at(-1)).toBe('photograph');
    expect(uniforms.uMilkyWayTex.value).toBe(photograph);
    expect(heardLimitOf(renderer, f)).toBe(6.5);
  });

  it('keeps the photograph at its own limit when the glow fails, and says the photograph shows: no star drawn twice', async () => {
    const { asked, up } = loader();
    const uniforms = createSkyDomeUniforms();
    const photograph = new THREE.Texture();
    uniforms.uMilkyWayTex.value = photograph;
    recordPanorama(uniforms, Promise.resolve(photograph));
    const f = field(6.5);
    const told: string[] = [];
    setSkyPictureOf({}, up, uniforms, f, 'starless', 10.5, (shown) => told.push(shown));
    asked[0]!.fail(new Error('404'));
    await settle();
    expect([f.limit, uniforms.uFillPlanets.value, uniforms.uMilkyWayTex.value, told]).toEqual([6.5, 0, photograph, ['photograph']]);
  });

  it('forwards whatever limit the field was given otherwise', () => {
    expect(heardLimitOf({}, field(8.25))).toBe(8.25);
  });
});

describe('the WebGL renderer\'s pose for the music, in the predicted sky', () => {
  it('reports the limit from before the glow raised the field', async () => {
    const { SparkPortalRenderer } = await import('../../SparkPortalRenderer.js');
    const { asked, up } = loader();
    const uniforms = createSkyDomeUniforms();
    const photograph = new THREE.Texture();
    recordPanorama(uniforms, Promise.resolve(photograph));
    const f = field(6.5);
    const renderer = { camera: new THREE.PerspectiveCamera(56, 16 / 9), catalogStarMounts: [new THREE.Group()], catalogStars: f };
    setSkyPictureOf(renderer, up, uniforms, f, 'starless', 10.5);
    asked[0]!.ok(new THREE.Texture());
    await settle();
    const pose = SparkPortalRenderer.prototype.getCameraSkyPose.call(renderer as never);
    expect(f.limit).toBe(10.5);
    expect(pose!.limitingMagnitude).toBe(6.5);
  });
});

describe('the dome\'s planet fill', () => {
  it('fills the very windows the flight fills, only while the glow shows', async () => {
    const { createGlobalPhotorealisticSkyDomeMesh } = await import('../skyDomeAndCinemaShader.js');
    const { PLANET_WINDOWS, GLOW_SIZE_PX } = await import('../../sky/flightMilkyWay.js');
    const mesh = createGlobalPhotorealisticSkyDomeMesh(createSkyDomeUniforms());
    const fragment = (mesh.material as THREE.ShaderMaterial).fragmentShader;
    for (const w of PLANET_WINDOWS) expect(fragment).toContain(`vec3(${(w.xPx / GLOW_SIZE_PX.width).toFixed(6)}, ${(1 - w.yPx / GLOW_SIZE_PX.height).toFixed(6)}, ${w.radiusPx.toFixed(1)})`);
    expect(fragment).toContain('if (uFillPlanets > 0.5) mwCol = fillPlanets(mwUv, mwCol, mwGradX, mwGradY);');
    expect(fragment).toContain('* 2.0 * radiusPx / GLOW_SIZE');
    expect(createSkyDomeUniforms().uFillPlanets.value).toBe(0);
  });
});

describe('the glow and the panorama the dome really loads', () => {
  it('waits on loadSkyViewTextures\' own panorama, placed through its registration', async () => {
    const fs = await import('node:fs');
    const path = await import('node:path');
    const { fileURLToPath } = await import('node:url');
    const { loadSkyViewTextures } = await import('../skyDomeAndCinemaShader.js');
    const here = path.dirname(fileURLToPath(import.meta.url));
    const registration = fs.readFileSync(path.resolve(here, '../../../../public/textures/eso0932a_registration.json'), 'utf8');
    const realFetch = globalThis.fetch;
    globalThis.fetch = (async () => new Response(registration, { status: 200 })) as typeof fetch;
    try {
      const { asked, loader: l, up } = loader();
      const uniforms = createSkyDomeUniforms();
      loadSkyViewTextures(l, uniforms);
      expect(asked.map((a) => a.url)).toEqual(['/textures/eso_milkyway_panorama.jpg']);
      const renderer = {};
      setSkyPictureOf(renderer, up, uniforms, field(), 'starless', 10.5);
      const glow = new THREE.Texture();
      asked[1]!.ok(glow);
      await settle();
      expect(uniforms.uMilkyWayTex.value).toBeNull();
      const photograph = new THREE.Texture();
      asked[0]!.ok(photograph);
      for (let i = 0; i < 5; i++) await settle();
      expect(uniforms.uMilkyWayTex.value).toBe(glow);
    } finally {
      globalThis.fetch = realFetch;
    }
  });

  it('leaves the photograph on when the person has left the predicted sky before the glow came', async () => {
    const { asked, up } = loader();
    const uniforms = createSkyDomeUniforms();
    const photograph = new THREE.Texture();
    uniforms.uMilkyWayTex.value = photograph;
    recordPanorama(uniforms, Promise.resolve(photograph));
    const renderer = {};
    setSkyPictureOf(renderer, up, uniforms, field(), 'starless', 10.5);
    setSkyPictureOf(renderer, up, uniforms, field(), 'photograph', 6.5);
    asked[0]!.ok(new THREE.Texture());
    for (let i = 0; i < 3; i++) await settle();
    expect(uniforms.uMilkyWayTex.value).toBe(photograph);
  });
});
