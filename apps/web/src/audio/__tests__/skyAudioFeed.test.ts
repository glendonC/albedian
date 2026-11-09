import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import { CatalogStarNameIndexSchema } from '@albedian/contracts';
import { ICRS_TO_SCENE_QUATERNION } from '../../spatial/catalogStars/CatalogStarField';
import { raDecDegToVec } from '../../spatial/catalogStars/healpix';
import { ObservatoryAudioEngine } from '../ObservatoryAudioEngine';
import { SkyAudioFeed, type SkyAudioTarget } from '../SkyAudioFeed';
import { skySourceId, type SkyCameraPose } from '../skySources';
import type { AudioSourceInput, Quat, Vec3 } from '../voicePlanning';
import { organAndPulse } from '../music/directions';
import type { ScreenStar } from '../music/starNotes';

const repoFile = (rel: string) => fileURLToPath(new URL(`../../../../../${rel}`, import.meta.url));
const RELEASE = 'apps/web/public/catalog/stars/gaia-dr3';
const BASE_URL = '/catalog/stars/gaia-dr3/';
const names = CatalogStarNameIndexSchema.parse(JSON.parse(fs.readFileSync(repoFile(`${RELEASE}/names.json`), 'utf8')));
const idOf = (name: string) => {
  const e = names.names.find((n) => n.nameAscii === name)!;
  return skySourceId('gaia-dr3', e.layer, e.pix, e.index);
};

/** Serves the committed release from disk the way the dev server does, and logs every request. */
function releaseFetch(options: { fail?: (url: string) => boolean } = {}) {
  const requests: string[] = [];
  const fetchImpl = vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
    const url = String(input);
    requests.push(url);
    if (options.fail?.(url)) return new Response('unavailable in this test', { status: 503 });
    if (!url.startsWith(BASE_URL)) return new Response('not found', { status: 404 });
    const file = repoFile(`${RELEASE}/${url.slice(BASE_URL.length)}`);
    if (!fs.existsSync(file)) return new Response('not found', { status: 404 });
    return new Response(fs.readFileSync(file));
  });
  return { fetchImpl: fetchImpl as unknown as typeof fetch, requests };
}

/** Records what the feed asks of the engine. */
function recordingTarget() {
  const orientations: Quat[] = [];
  const sourceLists: AudioSourceInput[][] = [];
  const target: SkyAudioTarget = {
    setListenerOrientation: (q) => orientations.push(q),
    setSources: (sources) => sourceLists.push([...sources]),
  };
  return { target, orientations, sourceLists };
}

const SCENE_ICRS: Quat = [ICRS_TO_SCENE_QUATERNION.x, ICRS_TO_SCENE_QUATERNION.y, ICRS_TO_SCENE_QUATERNION.z, ICRS_TO_SCENE_QUATERNION.w];

function lookAtIcrs(forward: Vec3): Quat {
  const f = new THREE.Vector3(...forward).normalize();
  const right = new THREE.Vector3().crossVectors(f, new THREE.Vector3(0, 0, 1)).normalize();
  const up = new THREE.Vector3().crossVectors(right, f).normalize();
  const q = new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(right, up, f.clone().negate()));
  return [q.x, q.y, q.z, q.w];
}

function pose(raDeg: number, decDeg: number, fovDeg = 20, aspect = 16 / 9): SkyCameraPose {
  const camera = new THREE.Quaternion(...SCENE_ICRS).multiply(new THREE.Quaternion(...lookAtIcrs(raDecDegToVec(raDeg, decDeg))));
  return {
    cameraWorldQuaternion: [camera.x, camera.y, camera.z, camera.w],
    icrsWorldQuaternion: SCENE_ICRS,
    fovDeg,
    aspect,
    limitingMagnitude: 6.5,
  };
}

/** Follows `p` frame by frame, advancing the clock, until the feed has everything the view needs. */
async function settle(feed: SkyAudioFeed, p: SkyCameraPose, clock: { t: number }, frames = 200): Promise<void> {
  for (let i = 0; i < frames; i++) {
    clock.t += 50;
    feed.follow(p);
    await new Promise((resolve) => setTimeout(resolve, 0));
    if (feed.getStatus().settled) {
      clock.t += 200;
      feed.follow(p);
      return;
    }
  }
  throw new Error(`feed did not settle: ${JSON.stringify(feed.getStatus())}`);
}

const CASSIOPEIA = pose(15, 60);

afterEach(() => {
  vi.restoreAllMocks();
});

describe('SkyAudioFeed with the star voices off (the app singleton configuration)', () => {
  it('turns the listener with the camera but passes no sources and fetches nothing, so no voice is planned', async () => {
    const { fetchImpl, requests } = releaseFetch();
    const { target, orientations, sourceLists } = recordingTarget();
    const clock = { t: 0 };
    const feed = new SkyAudioFeed(target, { fetch: fetchImpl, baseUrl: BASE_URL, now: () => clock.t, voices: false });
    for (let i = 0; i < 20; i++) {
      clock.t += 120;
      feed.follow(CASSIOPEIA);
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    expect(orientations).toHaveLength(1);
    expect(sourceLists).toEqual([]);
    expect(requests).toEqual([]);
    expect(feed.getStatus()).toMatchObject({ catalog: 'idle', starsInView: 0, audibleSources: 0, silentSources: 0 });

    // Through the real engine: the stars in view (289 on screen there) plan no voice
    const engine = new ObservatoryAudioEngine();
    const appFeed = new SkyAudioFeed(engine, { fetch: fetchImpl, baseUrl: BASE_URL, now: () => clock.t, voices: false });
    appFeed.follow(CASSIOPEIA);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(engine.getSnapshot().voices).toEqual([]);
    expect(engine.getSnapshot().silent).toEqual([]);
  });

  it('is how the app singleton is built', async () => {
    const { skyAudio } = await import('../SkyAudioFeed');
    expect(skyAudio.voicesEnabled).toBe(false);
  });

  it('with a music target, hands the music the stars on screen with their tile values, and still plans no voice', async () => {
    const { fetchImpl } = releaseFetch();
    const { target, sourceLists } = recordingTarget();
    const musicStars: ScreenStar[][] = [];
    const clock = { t: 0 };
    const feed = new SkyAudioFeed(target, {
      fetch: fetchImpl,
      baseUrl: BASE_URL,
      now: () => clock.t,
      voices: false,
      music: { setMusicStars: (stars) => musicStars.push([...stars]) },
    });
    await settle(feed, CASSIOPEIA, clock);
    expect(sourceLists).toEqual([]);

    // Segin carries its Gaia DR3 values from the astro tile; Caph has none and is handed over without them
    const stars = musicStars.at(-1)!;
    const segin = stars.find((s) => s.id === idOf('Segin'))!;
    expect(segin).toMatchObject({ teffK: 14806 });
    expect(segin.luminositySolar).not.toBeNull();
    expect(Math.abs(segin.screenX)).toBeLessThanOrEqual(1);
    expect(Math.abs(segin.screenY)).toBeLessThanOrEqual(1);
    expect(stars.find((s) => s.id === idOf('Caph'))).toMatchObject({ teffK: null, luminositySolar: null });

    // Direction A over this sky: Segin and Achird play the data part, Caph plays nothing
    const complete = stars.filter((s) => s.teffK != null && s.luminositySolar != null && s.distancePc != null);
    const played = new Set(organAndPulse(complete).events.flatMap((e) => (e.type === 'sample' && e.star ? [e.star.starId] : [])));
    expect(played.has(idOf('Segin'))).toBe(true);
    expect(played.has(idOf('Achird'))).toBe(true);
    expect(played.has(idOf('Caph'))).toBe(false);
  });

  it('is built with the music as its music target only when a direction plays', async () => {
    const { skyAudio } = await import('../SkyAudioFeed');
    const { observatoryAudio } = await import('../ObservatoryAudioEngine');
    expect(skyAudio.musicEnabled).toBe(observatoryAudio.getMusicDirection() !== null);
  });
});

describe('SkyAudioFeed', () => {
  it('fetches nothing and asks nothing of the engine until a camera pose arrives', async () => {
    const { fetchImpl, requests } = releaseFetch();
    const { target, orientations, sourceLists } = recordingTarget();
    const feed = new SkyAudioFeed(target, { fetch: fetchImpl, baseUrl: BASE_URL });
    feed.follow(null);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(requests).toEqual([]);
    expect(orientations).toEqual([]);
    expect(sourceLists).toEqual([]);
    expect(feed.getStatus().catalog).toBe('idle');
  });

  it('turns the listener with the camera and sounds the stars it shows, loading only what the view needs', async () => {
    const { fetchImpl, requests } = releaseFetch();
    const { target, orientations, sourceLists } = recordingTarget();
    const clock = { t: 0 };
    const feed = new SkyAudioFeed(target, { fetch: fetchImpl, baseUrl: BASE_URL, now: () => clock.t });
    feed.follow(CASSIOPEIA);
    expect(orientations).toHaveLength(1);
    await settle(feed, CASSIOPEIA, clock);

    // The camera's turn relative to the sky sphere, which is the look direction in ICRS
    const q = orientations[0]!;
    const expected = lookAtIcrs(raDecDegToVec(15, 60));
    const dot = q.reduce((sum, value, i) => sum + value * expected[i]!, 0);
    expect(Math.abs(dot)).toBeCloseTo(1, 9);
    // The same pose again does not turn the listener again
    expect(orientations).toHaveLength(1);

    const sources = sourceLists.at(-1)!;
    const complete = sources.filter((s) => s.teffK != null && s.luminositySolar != null && s.distancePc != null);
    expect(complete.slice(0, 3).map((s) => s.id)).toEqual(['Segin', 'Fulu', 'Achird'].map(idOf));
    for (const name of ['Caph', 'Schedar', 'Ruchbah']) {
      expect(sources.find((s) => s.id === idOf(name)), name).toMatchObject({ teffK: null, luminositySolar: null });
    }

    // Manifest, then the naked-eye core tiles the view overlaps and their astro tiles; nothing else
    expect(requests[0]).toBe(`${BASE_URL}manifest.json`);
    const kinds = requests.slice(1).map((url) => /\.(core|detail|astro)\.bin$/.exec(url)?.[1] ?? url);
    expect(kinds.filter((k) => k === 'detail')).toEqual([]);
    expect(requests.slice(1).every((url) => /\/L0\/\d+\.(core|astro)\.bin$/.test(url))).toBe(true);
    const cores = requests.filter((url) => url.endsWith('.core.bin'));
    const astros = requests.filter((url) => url.endsWith('.astro.bin'));
    expect(astros.length).toBeGreaterThan(0);
    expect(astros.length).toBeLessThanOrEqual(cores.length);
    expect(cores.length).toBeLessThan(12);
    expect(feed.getStatus()).toMatchObject({ catalog: 'ready', error: null, tilesWithoutAstro: 0, astroFailures: 0, settled: true });

    // A turn of the camera turns the listener again
    feed.follow(pose(15.5, 60));
    expect(orientations).toHaveLength(2);
  });

  it("leaves a tile's stars out while its astro tile fails, and asks again later", async () => {
    let failing = true;
    const { fetchImpl, requests } = releaseFetch({ fail: (url) => failing && url.endsWith('/L0/0.astro.bin') });
    const { target, sourceLists } = recordingTarget();
    const clock = { t: 0 };
    const feed = new SkyAudioFeed(target, { fetch: fetchImpl, baseUrl: BASE_URL, now: () => clock.t });
    await settle(feed, CASSIOPEIA, clock);

    // Segin and Achird live in tile L0/0: left out, not called missing
    const ids = new Set((sourceLists.at(-1) ?? []).map((s) => s.id));
    expect(ids.has(idOf('Segin'))).toBe(false);
    expect(ids.has(idOf('Achird'))).toBe(false);
    const status = feed.getStatus();
    expect(status.astroFailures).toBe(1);
    expect(status.tilesWithoutAstro).toBeGreaterThanOrEqual(1);
    expect(status.error).toContain('503');

    failing = false;
    const asked = requests.filter((url) => url.endsWith('/L0/0.astro.bin')).length;
    clock.t += 16_000;
    await settle(feed, CASSIOPEIA, clock);
    expect(requests.filter((url) => url.endsWith('/L0/0.astro.bin')).length).toBe(asked + 1);
    expect(new Set(sourceLists.at(-1)!.map((s) => s.id)).has(idOf('Segin'))).toBe(true);
    expect(feed.getStatus()).toMatchObject({ error: null, tilesWithoutAstro: 0, astroFailures: 1 });
  });

  it('feeds the real engine without creating an AudioContext: Segin plans a voice, Caph is silent and says why', async () => {
    const { fetchImpl } = releaseFetch();
    const contextFactory = vi.fn(() => null);
    const engine = new ObservatoryAudioEngine({ contextFactory, impulseResponseUrl: null });
    const clock = { t: 0 };
    const feed = new SkyAudioFeed(engine, { fetch: fetchImpl, baseUrl: BASE_URL, now: () => clock.t });
    engine.setScaleRegime('cislunar');
    await settle(feed, CASSIOPEIA, clock);

    const snapshot = engine.getSnapshot();
    expect(contextFactory).not.toHaveBeenCalled();
    expect(snapshot.enabled).toBe(false);
    const segin = snapshot.voices.find((v) => v.id === idOf('Segin'));
    expect(segin).toBeDefined();
    expect(segin!.star?.teffK).toBe(14806);
    expect(segin!.levelDb).toBeGreaterThan(-23.1);
    expect(segin!.levelDb).toBeLessThan(-22.95);
    // At 140 pc in the stellar window: neither clamped near nor far, whatever the regime
    expect(segin!.perceptualDistance).toBeGreaterThan(0.6);
    expect(segin!.perceptualDistance).toBeLessThan(0.7);
    expect(snapshot.voices.length).toBe(8);
    expect(snapshot.silent).toContainEqual({ status: 'silent', id: idOf('Caph'), reason: 'missing_input', missing: ['luminositySolar', 'teffK'] });
  });

  it("carries the star layer's evidence class once declared, and drops it when no layer declares one", async () => {
    const { fetchImpl } = releaseFetch();
    const { target, sourceLists } = recordingTarget();
    const clock = { t: 0 };
    const feed = new SkyAudioFeed(target, { fetch: fetchImpl, baseUrl: BASE_URL, now: () => clock.t });
    await settle(feed, CASSIOPEIA, clock);
    expect(sourceLists.at(-1)!.every((s) => !('evidenceClass' in s))).toBe(true);

    feed.setEvidenceClass('simulated');
    clock.t += 200;
    feed.follow(CASSIOPEIA);
    expect(sourceLists.at(-1)!.every((s) => s.evidenceClass === 'simulated')).toBe(true);

    feed.setEvidenceClass(null);
    clock.t += 200;
    feed.follow(CASSIOPEIA);
    expect(sourceLists.at(-1)!.every((s) => !('evidenceClass' in s))).toBe(true);
  });

  it('leaves the sky silent when the catalog cannot be opened', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const { fetchImpl } = releaseFetch({ fail: (url) => url.endsWith('manifest.json') });
    const { target, sourceLists, orientations } = recordingTarget();
    const clock = { t: 0 };
    const feed = new SkyAudioFeed(target, { fetch: fetchImpl, baseUrl: BASE_URL, now: () => clock.t });
    await settle(feed, CASSIOPEIA, clock);
    expect(feed.getStatus()).toMatchObject({ catalog: 'unavailable', settled: true });
    expect(feed.getStatus().error).toContain('503');
    expect(sourceLists).toEqual([]);
    // The listener still turns with the camera: orientation is view state, not catalog data
    expect(orientations).toHaveLength(1);
  });

  it('stops after dispose', async () => {
    const { fetchImpl, requests } = releaseFetch();
    const { target, orientations } = recordingTarget();
    const feed = new SkyAudioFeed(target, { fetch: fetchImpl, baseUrl: BASE_URL });
    feed.dispose();
    feed.follow(CASSIOPEIA);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(requests).toEqual([]);
    expect(orientations).toEqual([]);
  });
});
