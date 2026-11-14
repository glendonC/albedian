import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import { CatalogStarNameIndexSchema } from '@albedian/contracts';
import { ICRS_TO_SCENE_QUATERNION } from '../../spatial/catalogStars/CatalogStarField';
import { CatalogStarTileLoader } from '../../spatial/catalogStars/CatalogStarTileLoader';
import { raDecDegToVec } from '../../spatial/catalogStars/healpix';
import { observatoryAudio } from '../ObservatoryAudioEngine';
import * as SkyAudioFeedModule from '../SkyAudioFeed';
import {
  SkyAudioFeed,
  skyAudio,
  type SkyAudioFeedOptions,
  type SkyAudioFeedStatus,
  type SkyAudioTarget,
  type SkyCatalogStatus,
  type SkyMusicTarget,
} from '../SkyAudioFeed';
import { skySourceId, skyViewFromPose, type SkyCameraPose } from '../skySources';
import type { ScreenStar } from '../music/starNotes';
import type { AudioSourceInput, Quat, Vec3 } from '../voicePlanning';

const repoFile = (rel: string) => fileURLToPath(new URL(`../../../../../${rel}`, import.meta.url));
const RELEASE = 'apps/web/public/catalog/stars/gaia-dr3';
const BASE_URL = '/catalog/stars/gaia-dr3/';
const RAW_MANIFEST = JSON.parse(fs.readFileSync(repoFile(`${RELEASE}/manifest.json`), 'utf8')) as Record<string, unknown>;
const names = CatalogStarNameIndexSchema.parse(JSON.parse(fs.readFileSync(repoFile(`${RELEASE}/names.json`), 'utf8')));

const idOf = (name: string) => {
  const entry = names.names.find((n) => n.nameAscii === name)!;
  return skySourceId('gaia-dr3', entry.layer, entry.pix, entry.index);
};

function releaseFetch(
  options: {
    fail?: (url: string) => boolean;
    override?: (url: string) => Promise<Response> | Response | null;
  } = {}
) {
  const requests: string[] = [];
  const fetchImpl = vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
    const url = String(input);
    requests.push(url);
    const custom = await options.override?.(url);
    if (custom) return custom;
    if (options.fail?.(url)) return new Response('unavailable in this test', { status: 503 });
    if (!url.startsWith(BASE_URL)) return new Response('not found', { status: 404 });
    const file = repoFile(`${RELEASE}/${url.slice(BASE_URL.length)}`);
    if (!fs.existsSync(file)) return new Response('not found', { status: 404 });
    return new Response(fs.readFileSync(file));
  });
  return { fetchImpl: fetchImpl as unknown as typeof fetch, requests };
}

function recordingTarget() {
  const orientations: Quat[] = [];
  const sourceLists: AudioSourceInput[][] = [];
  const target: SkyAudioTarget = {
    setListenerOrientation: (q) => orientations.push(q),
    setSources: (sources) => sourceLists.push([...sources]),
  };
  return { target, orientations, sourceLists };
}

function recordingMusic(wants: () => boolean = () => true) {
  const starFrames: ScreenStar[][] = [];
  const music: SkyMusicTarget = {
    setMusicStars: (stars) => starFrames.push([...stars]),
    wantsMusicStars: wants,
  };
  return { music, starFrames };
}

const SCENE_ICRS: Quat = [
  ICRS_TO_SCENE_QUATERNION.x,
  ICRS_TO_SCENE_QUATERNION.y,
  ICRS_TO_SCENE_QUATERNION.z,
  ICRS_TO_SCENE_QUATERNION.w,
];

function lookAtIcrs(forward: Vec3, rollRad = 0): Quat {
  const f = new THREE.Vector3(...forward).normalize();
  const right = new THREE.Vector3().crossVectors(f, new THREE.Vector3(0, 0, 1)).normalize();
  const up = new THREE.Vector3().crossVectors(right, f).normalize();
  const q = new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(right, up, f.clone().negate()));
  if (rollRad !== 0) {
    q.multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), rollRad));
  }
  return [q.x, q.y, q.z, q.w];
}

function pose(
  raDeg: number,
  decDeg: number,
  overrides: { fovDeg?: number; aspect?: number; limitingMagnitude?: number; rollRad?: number } = {}
): SkyCameraPose {
  const { fovDeg = 20, aspect = 16 / 9, limitingMagnitude = 6.5, rollRad = 0 } = overrides;
  const camera = new THREE.Quaternion(...SCENE_ICRS).multiply(
    new THREE.Quaternion(...lookAtIcrs(raDecDegToVec(raDeg, decDeg), rollRad))
  );
  return {
    cameraWorldQuaternion: [camera.x, camera.y, camera.z, camera.w],
    icrsWorldQuaternion: SCENE_ICRS,
    fovDeg,
    aspect,
    limitingMagnitude,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

async function flushMicrotasks(ticks = 6): Promise<void> {
  for (let i = 0; i < ticks; i++) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

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

describe('SkyAudioFeed module contract and singleton configuration', () => {
  it('exposes the exact runtime export keys and preserves type contracts', () => {
    expect(Object.keys(SkyAudioFeedModule).sort()).toEqual(['SkyAudioFeed', 'skyAudio']);

    const statusCheck: SkyAudioFeedStatus = skyAudio.getStatus();
    const catalogCheck: SkyCatalogStatus = statusCheck.catalog;
    const optionsCheck: SkyAudioFeedOptions = { voices: false };
    expect(['idle', 'loading', 'ready', 'unavailable']).toContain(catalogCheck);
    expect(optionsCheck.voices).toBe(false);
  });

  it('configures skyAudio with voices disabled and music delegated to observatoryAudio', () => {
    expect(skyAudio).toBeInstanceOf(SkyAudioFeed);
    expect(skyAudio.voicesEnabled).toBe(false);
    expect(skyAudio.musicEnabled).toBe(observatoryAudio.wantsMusicStars());

    const { target } = recordingTarget();
    const defaultFeed = new SkyAudioFeed(target);
    expect(defaultFeed.voicesEnabled).toBe(true);
    expect(defaultFeed.musicEnabled).toBe(false);
    expect(defaultFeed.getStatus()).toEqual({
      catalog: 'idle',
      error: null,
      starsInView: 0,
      completeInView: 0,
      audibleSources: 0,
      silentSources: 0,
      tilesWithoutAstro: 0,
      astroFailures: 0,
      settled: false,
    });
  });
});

describe('SkyAudioFeed lazy opening and invalid pose guards', () => {
  it('ignores null and degenerate camera poses without turning the listener or opening the catalog', async () => {
    const { fetchImpl, requests } = releaseFetch();
    const { target, orientations, sourceLists } = recordingTarget();
    const feed = new SkyAudioFeed(target, { fetch: fetchImpl, baseUrl: BASE_URL });

    const invalidPoses: (SkyCameraPose | null)[] = [
      null,
      { ...CASSIOPEIA, fovDeg: 0 },
      { ...CASSIOPEIA, fovDeg: -10 },
      { ...CASSIOPEIA, fovDeg: 180 },
      { ...CASSIOPEIA, fovDeg: Number.NaN },
      { ...CASSIOPEIA, aspect: 0 },
      { ...CASSIOPEIA, aspect: -1 },
      { ...CASSIOPEIA, limitingMagnitude: Number.NaN },
      { ...CASSIOPEIA, cameraWorldQuaternion: [0, 0, 0, 0] },
      { ...CASSIOPEIA, icrsWorldQuaternion: [0, 0, 0, 0] },
    ];

    for (const invalid of invalidPoses) {
      feed.follow(invalid);
    }
    await flushMicrotasks();

    expect(orientations).toEqual([]);
    expect(sourceLists).toEqual([]);
    expect(requests).toEqual([]);
    expect(feed.getStatus().catalog).toBe('idle');
    expect(feed.getStatus().settled).toBe(false);
  });

  it('opens the catalog only once while manifest.json is in flight and stays idle when both voices and music are off', async () => {
    const manifestGate = deferred<Response>();
    const { fetchImpl, requests } = releaseFetch({
      override: (url) => (url.endsWith('manifest.json') ? manifestGate.promise : null),
    });
    const { target, orientations } = recordingTarget();
    let wantsMusic = false;
    const { music } = recordingMusic(() => wantsMusic);
    const clock = { t: 0 };
    const feed = new SkyAudioFeed(target, {
      fetch: fetchImpl,
      baseUrl: BASE_URL,
      now: () => clock.t,
      voices: false,
      music,
    });

    feed.follow(CASSIOPEIA);
    expect(orientations).toHaveLength(1);
    expect(requests).toEqual([]);
    expect(feed.getStatus().catalog).toBe('idle');

    wantsMusic = true;
    feed.follow(CASSIOPEIA);
    feed.follow(pose(16, 60));
    feed.follow(pose(17, 60));

    expect(feed.getStatus().catalog).toBe('loading');
    expect(feed.getStatus().settled).toBe(false);
    expect(requests).toEqual([`${BASE_URL}manifest.json`]);
    expect(orientations).toHaveLength(3);

    manifestGate.resolve(new Response(JSON.stringify(RAW_MANIFEST)));
    await flushMicrotasks();
    expect(feed.getStatus().catalog).toBe('ready');
    feed.dispose();
  });
});

describe('SkyAudioFeed listener orientation thresholds', () => {
  it('treats antipodal and sub-epsilon quaternions as unchanged and emits rotations above epsilon every frame', () => {
    const { fetchImpl } = releaseFetch();
    const { target, orientations } = recordingTarget();
    const clock = { t: 0 };
    const feed = new SkyAudioFeed(target, {
      fetch: fetchImpl,
      baseUrl: BASE_URL,
      now: () => clock.t,
      voices: false,
    });

    feed.follow(CASSIOPEIA);
    expect(orientations).toHaveLength(1);

    // Negating a unit quaternion represents the exact same 3D rotation (dot = -1, 1 - |dot| = 0)
    const [cx, cy, cz, cw] = CASSIOPEIA.cameraWorldQuaternion;
    feed.follow({ ...CASSIOPEIA, cameraWorldQuaternion: [-cx, -cy, -cz, -cw] });
    expect(orientations).toHaveLength(1);

    // A tiny perturbation with 1 - |dot| < 1e-12 (half-angle ~ 5e-7 rad -> 1 - cos ~ 1.25e-13) stays suppressed
    const subEpsilon = pose(15, 60, { rollRad: 1e-6 });
    feed.follow(subEpsilon);
    expect(orientations).toHaveLength(1);

    // A rotation with 1 - |dot| > 1e-12 (rollRad = 1e-4 -> half-angle = 5e-5 -> 1 - cos ~ 1.25e-9) emits immediately,
    // even at the exact same timestamp (no 100 ms throttle on listener orientation)
    const aboveEpsilon = pose(15, 60, { rollRad: 1e-4 });
    feed.follow(aboveEpsilon);
    expect(orientations).toHaveLength(2);
  });
});

describe('SkyAudioFeed tile planning cadence', () => {
  it('throttles setView by 0.01 rad angular/radius thresholds, limitingMagnitude changes, and the 120 ms interval', async () => {
    const setViewSpy = vi.spyOn(CatalogStarTileLoader.prototype, 'setView');
    const { fetchImpl } = releaseFetch();
    const { target } = recordingTarget();
    const clock = { t: 1_000 };
    const feed = new SkyAudioFeed(target, { fetch: fetchImpl, baseUrl: BASE_URL, now: () => clock.t });

    await settle(feed, CASSIOPEIA, clock);
    expect(setViewSpy).toHaveBeenCalledTimes(1);

    const expectedInitialView = skyViewFromPose(CASSIOPEIA)!;
    expect(setViewSpy.mock.calls[0]![0]).toEqual({
      centerIcrs: [...expectedInitialView.centerIcrs],
      radiusRad: expectedInitialView.halfDiagonalRad + 0.08,
      limitingMagnitude: 6.5,
    });

    // Sub-threshold center move: Dec +60 to +60.25 deg is 0.00436 rad (< 0.01 rad), so even after 500 ms no replan occurs
    clock.t += 500;
    feed.follow(pose(15, 60.25));
    expect(setViewSpy).toHaveBeenCalledTimes(1);

    // Pure roll changes orientationIcrs without moving centerIcrs or radiusRad, so no tile replan occurs
    clock.t += 500;
    feed.follow(pose(15, 60, { rollRad: 0.4 }));
    expect(setViewSpy).toHaveBeenCalledTimes(1);

    // Above-threshold center move (Dec +60 to +61 deg is 0.01745 rad > 0.01 rad):
    // First establish planned.at by triggering a replan, then check 119 ms suppression vs 120 ms execution
    const movedA = pose(15, 61);
    clock.t += 500;
    const firstReplanAt = clock.t;
    feed.follow(movedA);
    expect(setViewSpy).toHaveBeenCalledTimes(2);

    const movedB = pose(15, 62.5);
    clock.t = firstReplanAt + 119;
    feed.follow(movedB);
    expect(setViewSpy).toHaveBeenCalledTimes(2);

    clock.t = firstReplanAt + 120;
    feed.follow(movedB);
    expect(setViewSpy).toHaveBeenCalledTimes(3);

    // Sub-threshold FOV change (radius delta <= 0.01 rad) does not replan; larger FOV change (> 0.01 rad) replans after 120 ms
    clock.t += 200;
    feed.follow(pose(15, 62.5, { fovDeg: 20.2 }));
    expect(setViewSpy).toHaveBeenCalledTimes(3);

    clock.t += 200;
    feed.follow(pose(15, 62.5, { fovDeg: 24 }));
    expect(setViewSpy).toHaveBeenCalledTimes(4);

    // Limiting magnitude change replans at >= 120 ms even when center and FOV are identical
    const magBaseAt = clock.t;
    clock.t = magBaseAt + 119;
    feed.follow(pose(15, 62.5, { fovDeg: 24, limitingMagnitude: 6.0 }));
    expect(setViewSpy).toHaveBeenCalledTimes(4);

    clock.t = magBaseAt + 120;
    feed.follow(pose(15, 62.5, { fovDeg: 24, limitingMagnitude: 6.0 }));
    expect(setViewSpy).toHaveBeenCalledTimes(5);
  });
});

describe('SkyAudioFeed source computation cadence and target routing', () => {
  it('enforces the 100 ms source refresh window, skips stationary clean frames, and routes music updates vs deduplicated voice sources', async () => {
    const { fetchImpl } = releaseFetch();
    const { target, sourceLists } = recordingTarget();
    let wantsMusic = true;
    const { music, starFrames } = recordingMusic(() => wantsMusic);
    const clock = { t: 0 };
    const feed = new SkyAudioFeed(target, {
      fetch: fetchImpl,
      baseUrl: BASE_URL,
      now: () => clock.t,
      voices: true,
      maxAudible: 3,
      maxSilent: 2,
      music,
    });

    await settle(feed, CASSIOPEIA, clock);
    const settledStatus = feed.getStatus();
    expect(settledStatus.audibleSources).toBe(3);
    expect(settledStatus.silentSources).toBe(2);
    expect(settledStatus.starsInView).toBeGreaterThan(settledStatus.completeInView);
    expect(settledStatus.completeInView).toBeGreaterThan(3);

    const sourcesAfterSettle = sourceLists.length;
    const musicAfterSettle = starFrames.length;
    expect(sourcesAfterSettle).toBeGreaterThan(0);
    expect(musicAfterSettle).toBeGreaterThan(0);

    // Stationary view when clean (!dirty && sameView): advancing past 100 ms recomputes neither music nor voice sources
    clock.t += 500;
    feed.follow(CASSIOPEIA);
    expect(sourceLists).toHaveLength(sourcesAfterSettle);
    expect(starFrames).toHaveLength(musicAfterSettle);

    // Establish an exact compute timestamp via a tiny roll, then verify 99 ms suppression vs 100 ms execution:
    // the 3 loudest + 2 brightest silent stars stay identical, so music receives new screen positions while setSources is deduplicated
    clock.t += 200;
    const baseComputeAt = clock.t;
    feed.follow(pose(15, 60, { rollRad: 0.003 }));
    const musicAfterBaseRoll = starFrames.length;
    expect(musicAfterBaseRoll).toBe(musicAfterSettle + 1);
    expect(sourceLists).toHaveLength(sourcesAfterSettle);

    const rolled = pose(15, 60, { rollRad: 0.008 });
    clock.t = baseComputeAt + 99;
    feed.follow(rolled);
    expect(starFrames).toHaveLength(musicAfterBaseRoll);

    clock.t = baseComputeAt + 100;
    feed.follow(rolled);
    expect(starFrames).toHaveLength(musicAfterBaseRoll + 1);
    expect(sourceLists).toHaveLength(sourcesAfterSettle);

    const beforeSegin = starFrames[musicAfterBaseRoll - 1]!.find((s) => s.id === idOf('Segin'))!;
    const afterSegin = starFrames[musicAfterBaseRoll]!.find((s) => s.id === idOf('Segin'))!;
    expect(afterSegin.screenX).not.toBe(beforeSegin.screenX);

    // Turning wantsMusicStars() off stops music delivery on subsequent camera rolls
    wantsMusic = false;
    clock.t += 150;
    feed.follow(pose(15, 60, { rollRad: 0.02 }));
    expect(starFrames).toHaveLength(musicAfterBaseRoll + 1);
  });

  it('with voices disabled and music toggled off after opening, updates listener orientation without computing sources', async () => {
    const { fetchImpl } = releaseFetch();
    const { target, orientations, sourceLists } = recordingTarget();
    let wantsMusic = true;
    const { music, starFrames } = recordingMusic(() => wantsMusic);
    const clock = { t: 0 };
    const feed = new SkyAudioFeed(target, {
      fetch: fetchImpl,
      baseUrl: BASE_URL,
      now: () => clock.t,
      voices: false,
      music,
    });

    await settle(feed, CASSIOPEIA, clock);
    const framesWhileMusicOn = starFrames.length;
    expect(framesWhileMusicOn).toBeGreaterThan(0);
    expect(sourceLists).toEqual([]);

    wantsMusic = false;
    const orientationsBefore = orientations.length;
    clock.t += 200;
    feed.follow(pose(25, 55));
    expect(orientations).toHaveLength(orientationsBefore + 1);
    expect(starFrames).toHaveLength(framesWhileMusicOn);
    expect(sourceLists).toEqual([]);
  });
});

describe('SkyAudioFeed evidence-class gating', () => {
  it('normalizes empty strings to null, avoids dirtying on unchanged values, and re-emits sources within cadence when changed', async () => {
    const { fetchImpl } = releaseFetch();
    const { target, sourceLists } = recordingTarget();
    const clock = { t: 0 };
    const feed = new SkyAudioFeed(target, { fetch: fetchImpl, baseUrl: BASE_URL, now: () => clock.t });

    await settle(feed, CASSIOPEIA, clock);
    const settledAt = clock.t;
    const baseCount = sourceLists.length;
    expect(feed.getStatus().settled).toBe(true);

    // Empty string normalizes to null, matching initial state: feed stays settled and clean
    feed.setEvidenceClass('');
    expect(feed.getStatus().settled).toBe(true);
    clock.t = settledAt + 200;
    feed.follow(CASSIOPEIA);
    expect(sourceLists).toHaveLength(baseCount);

    // Declaring a non-empty class marks dirty immediately, respects the 100 ms refresh window, then re-emits
    feed.setEvidenceClass('catalog_matched');
    expect(feed.getStatus().settled).toBe(false);

    // Trigger a baseline compute, then test 99 ms suppression on a subsequent class change
    clock.t += 200;
    const firstClassAt = clock.t;
    feed.follow(CASSIOPEIA);
    expect(sourceLists).toHaveLength(baseCount + 1);
    expect(sourceLists.at(-1)!.every((s) => s.evidenceClass === 'catalog_matched')).toBe(true);
    expect(feed.getStatus().settled).toBe(true);

    // Calling with identical class does not mark dirty or re-emit
    feed.setEvidenceClass('catalog_matched');
    expect(feed.getStatus().settled).toBe(true);
    clock.t += 200;
    feed.follow(CASSIOPEIA);
    expect(sourceLists).toHaveLength(baseCount + 1);

    // Switching to another class updates emitted sources on the next refresh
    feed.setEvidenceClass('simulated');
    clock.t = firstClassAt + 250;
    feed.follow(CASSIOPEIA);
    const simAt = clock.t;
    expect(sourceLists).toHaveLength(baseCount + 2);
    expect(sourceLists.at(-1)!.every((s) => s.evidenceClass === 'simulated')).toBe(true);

    // Clearing via empty string normalizes to null, is throttled at +99 ms, and applies at +100 ms
    feed.setEvidenceClass('');
    expect(feed.getStatus().settled).toBe(false);
    clock.t = simAt + 99;
    feed.follow(CASSIOPEIA);
    expect(sourceLists).toHaveLength(baseCount + 2);

    clock.t = simAt + 100;
    feed.follow(CASSIOPEIA);
    expect(sourceLists).toHaveLength(baseCount + 3);
    expect(sourceLists.at(-1)!.every((s) => !('evidenceClass' in s))).toBe(true);
    expect(feed.getStatus().settled).toBe(true);
  });
});

describe('SkyAudioFeed astro tile loading, retry backoff, format v1, and eviction', () => {
  it('enforces the exact 15,000 ms retry backoff and retains error until all failed astro tiles succeed', async () => {
    const failedAstroUrls = new Map<string, number>();
    let allowFirst = false;
    let allowAll = false;
    const clock = { t: 1_000 };

    const { fetchImpl, requests } = releaseFetch({
      fail: (url) => {
        if (!url.endsWith('.astro.bin')) return false;
        if (allowAll) return false;
        if (!failedAstroUrls.has(url)) failedAstroUrls.set(url, clock.t);
        const ordered = [...failedAstroUrls.keys()];
        if (allowFirst && url === ordered[0]) return false;
        return ordered.indexOf(url) < 2;
      },
    });
    const { target } = recordingTarget();
    const wideCassiopeia = pose(15, 60, { fovDeg: 60 });
    const feed = new SkyAudioFeed(target, { fetch: fetchImpl, baseUrl: BASE_URL, now: () => clock.t });

    await settle(feed, wideCassiopeia, clock);
    const twoFailed = [...failedAstroUrls.keys()].slice(0, 2);
    expect(twoFailed).toHaveLength(2);
    expect(feed.getStatus().astroFailures).toBe(2);
    expect(feed.getStatus().tilesWithoutAstro).toBe(2);
    expect(feed.getStatus().error).toContain('503');

    const firstFailedAt = failedAstroUrls.get(twoFailed[0]!)!;
    allowFirst = true;
    const countBeforeRetry = requests.filter((u) => u === twoFailed[0]).length;

    // 1 ms before the 15,000 ms retry window expires: astroRetryDue stays false and no retry occurs
    clock.t = firstFailedAt + 14_999;
    feed.follow(wideCassiopeia);
    await flushMicrotasks();
    expect(requests.filter((u) => u === twoFailed[0]).length).toBe(countBeforeRetry);

    // At 15,000 ms on a stationary view: astroRetryDue marks dirty; first tile recovers while second fails again,
    // so error stays non-null!
    clock.t = firstFailedAt + 15_000;
    await settle(feed, wideCassiopeia, clock);
    expect(requests.filter((u) => u === twoFailed[0]).length).toBe(countBeforeRetry + 1);
    expect(feed.getStatus().tilesWithoutAstro).toBe(1);
    expect(feed.getStatus().astroFailures).toBe(3);
    expect(feed.getStatus().error).toContain('503');

    // Once the remaining failed astro tile also succeeds after another 15,000 ms, error clears to null
    allowAll = true;
    clock.t += 15_500;
    await settle(feed, wideCassiopeia, clock);
    expect(feed.getStatus()).toMatchObject({
      catalog: 'ready',
      error: null,
      tilesWithoutAstro: 0,
      astroFailures: 3,
      settled: true,
    });
  });

  it('reports silent error and requests zero astro tiles when manifest formatVersion is 1', async () => {
    const { encoding, ...restManifest } = RAW_MANIFEST as { encoding: Record<string, unknown> } & Record<string, unknown>;
    const { astro: _unusedAstro, ...encodingV1 } = encoding;
    const manifestV1 = { ...restManifest, formatVersion: 1, encoding: encodingV1 };

    const { fetchImpl, requests } = releaseFetch({
      override: (url) => (url.endsWith('manifest.json') ? new Response(JSON.stringify(manifestV1)) : null),
    });
    const { target, sourceLists } = recordingTarget();
    const clock = { t: 0 };
    const feed = new SkyAudioFeed(target, { fetch: fetchImpl, baseUrl: BASE_URL, now: () => clock.t });

    await settle(feed, CASSIOPEIA, clock);
    expect(requests.filter((u) => u.endsWith('.astro.bin'))).toEqual([]);
    expect(sourceLists).toEqual([]);
    expect(feed.getStatus().catalog).toBe('ready');
    expect(feed.getStatus().settled).toBe(true);
    expect(feed.getStatus().error).toContain('has no astro tiles');
  });

  it('purges cached astro state when the core tile loader evicts tiles under maxResidentStars pressure', async () => {
    const loadAstroSpy = vi.spyOn(CatalogStarTileLoader.prototype, 'loadAstro');
    const { fetchImpl, requests } = releaseFetch();
    const { target } = recordingTarget();
    const clock = { t: 0 };
    const feed = new SkyAudioFeed(target, {
      fetch: fetchImpl,
      baseUrl: BASE_URL,
      now: () => clock.t,
      maxResidentStars: 1,
    });

    await settle(feed, CASSIOPEIA, clock);
    const countL0ZeroAstro = () => loadAstroSpy.mock.calls.filter(([layer, pix]) => layer === 0 && pix === 0).length;
    expect(countL0ZeroAstro()).toBe(1);
    expect(requests.filter((u) => u.endsWith('/L0/0.core.bin')).length).toBe(1);

    // Slew to antipodal sky so L0/0 leaves wanted tiles and is evicted under maxResidentStars: 1
    clock.t += 500;
    await settle(feed, pose(195, -60), clock);

    // Slew back to Cassiopeia: because onTileEvicted deleted L0/0 from the feed's astro map, loadAstro(0, 0) runs again
    clock.t += 500;
    await settle(feed, CASSIOPEIA, clock);
    expect(requests.filter((u) => u.endsWith('/L0/0.core.bin')).length).toBe(2);
    expect(countL0ZeroAstro()).toBe(2);
  });
});

describe('SkyAudioFeed error formatting and dispose race safety', () => {
  it('formats non-Error rejections cleanly when catalog open fails', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const fetchImpl = vi.fn(async () => Promise.reject('network offline string')) as unknown as typeof fetch;
    const { target } = recordingTarget();
    const clock = { t: 0 };
    const feed = new SkyAudioFeed(target, { fetch: fetchImpl, baseUrl: BASE_URL, now: () => clock.t });

    feed.follow(CASSIOPEIA);
    await flushMicrotasks();

    expect(feed.getStatus()).toMatchObject({
      catalog: 'unavailable',
      error: 'network offline string',
      settled: true,
    });
    expect(warnSpy).toHaveBeenCalledTimes(1);
  });

  it('disposes an in-flight loader that resolves after dispose() and suppresses post-dispose rejections', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const loaderDisposeSpy = vi.spyOn(CatalogStarTileLoader.prototype, 'dispose');

    // Case 1: manifest resolves after feed.dispose()
    const resolveGate = deferred<Response>();
    const { fetchImpl: resolvingFetch } = releaseFetch({
      override: (url) => (url.endsWith('manifest.json') ? resolveGate.promise : null),
    });
    const { target: target1, orientations: orientations1 } = recordingTarget();
    const feed1 = new SkyAudioFeed(target1, { fetch: resolvingFetch, baseUrl: BASE_URL });

    feed1.follow(CASSIOPEIA);
    expect(feed1.getStatus().catalog).toBe('loading');
    feed1.dispose();
    resolveGate.resolve(new Response(JSON.stringify(RAW_MANIFEST)));
    await flushMicrotasks();

    expect(loaderDisposeSpy).toHaveBeenCalledTimes(1);
    expect(feed1.getStatus().catalog).toBe('loading');
    feed1.follow(pose(30, 40));
    expect(orientations1).toHaveLength(1);

    // Case 2: manifest rejects after feed.dispose()
    const rejectGate = deferred<Response>();
    const { fetchImpl: rejectingFetch } = releaseFetch({
      override: (url) => (url.endsWith('manifest.json') ? rejectGate.promise : null),
    });
    const { target: target2 } = recordingTarget();
    const feed2 = new SkyAudioFeed(target2, { fetch: rejectingFetch, baseUrl: BASE_URL });

    feed2.follow(CASSIOPEIA);
    feed2.dispose();
    rejectGate.reject(new Error('late manifest failure'));
    await flushMicrotasks();

    expect(warnSpy).not.toHaveBeenCalled();
    expect(feed2.getStatus().catalog).toBe('loading');
    expect(feed2.getStatus().error).toBeNull();

    // Case 3: astro tile rejects after feed.dispose()
    const astroGate = deferred<Response>();
    const { fetchImpl: astroFetch, requests: astroRequests } = releaseFetch({
      override: (url) => (url.endsWith('/L0/0.astro.bin') ? astroGate.promise : null),
    });
    const { target: target3 } = recordingTarget();
    const clock = { t: 0 };
    const feed3 = new SkyAudioFeed(target3, { fetch: astroFetch, baseUrl: BASE_URL, now: () => clock.t });

    feed3.follow(CASSIOPEIA);
    await flushMicrotasks();
    clock.t += 150;
    feed3.follow(CASSIOPEIA);
    await flushMicrotasks();
    clock.t += 150;
    feed3.follow(CASSIOPEIA);
    expect(astroRequests.some((u) => u.endsWith('/L0/0.astro.bin'))).toBe(true);

    feed3.dispose();
    astroGate.reject(new Error('late astro failure'));
    await flushMicrotasks();

    expect(feed3.getStatus().astroFailures).toBe(0);
    expect(feed3.getStatus().error).toBeNull();
  });
});
