import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import * as THREE from 'three';
import { CatalogStarNameIndexSchema, CatalogStarTileManifestSchema } from '@albedian/contracts';
import { ICRS_TO_SCENE_QUATERNION } from '../../spatial/catalogStars/CatalogStarField';
import { raDecDegToVec, tileCenter } from '../../spatial/catalogStars/healpix';
import {
  decodeAstroTile,
  decodeCoreTile,
  type CatalogStarAstroTile,
  type CatalogStarCoreTile,
} from '../../spatial/catalogStars/tileCodec';
import { centreOnCamera } from '../../spatial/skyMounts';
import { STAR_DISTANCE_WINDOW, receivedLevelDb } from '../mappings';
import { planVoice, rotateByQuat, worldToListenerDirection, type AudioSourceInput, type Quat, type Vec3 } from '../voicePlanning';
import {
  directionInView,
  listenerOrientationIcrs,
  skySourceId,
  skySourcesInView,
  skyViewFromPose,
  type SkyCameraPose,
  type SkyTileData,
  type SkyView,
} from '../skySources';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

const repoFile = (rel: string) => fileURLToPath(new URL(`../../../../../${rel}`, import.meta.url));
const readBuffer = (rel: string): ArrayBuffer => {
  const bytes = fs.readFileSync(repoFile(rel));
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
};

const RELEASE = 'apps/web/public/catalog/stars/gaia-dr3';
const manifest = CatalogStarTileManifestSchema.parse(JSON.parse(fs.readFileSync(repoFile(`${RELEASE}/manifest.json`), 'utf8')));
const names = CatalogStarNameIndexSchema.parse(JSON.parse(fs.readFileSync(repoFile(`${RELEASE}/names.json`), 'utf8')));

function readTile(layer: number, pix: number): SkyTileData {
  const order = manifest.layers[layer]!.order;
  return {
    core: decodeCoreTile(readBuffer(`${RELEASE}/L${layer}/${pix}.core.bin`), { layer, order, pix }),
    astro: decodeAstroTile(readBuffer(`${RELEASE}/L${layer}/${pix}.astro.bin`), { layer, order, pix }),
  };
}

/** Every naked-eye tile of the release (layer 0, G and Hp to 6.5): the whole sky the default view draws. */
const L0: SkyTileData[] = manifest.layers[0]!.tileStarCounts.flatMap((count, pix) => (count > 0 ? [readTile(0, pix)] : []));

function entry(name: string) {
  const found = names.names.find((n) => n.nameAscii === name);
  if (!found) throw new Error(`no IAU name ${name} in the release`);
  return found;
}
const idOf = (name: string) => {
  const e = entry(name);
  return skySourceId(manifest.releaseId, e.layer, e.pix, e.index);
};

const SCENE_ICRS: Quat = [
  ICRS_TO_SCENE_QUATERNION.x,
  ICRS_TO_SCENE_QUATERNION.y,
  ICRS_TO_SCENE_QUATERNION.z,
  ICRS_TO_SCENE_QUATERNION.w,
];

/** A camera at the given ICRS direction with celestial north up (camera -z forward, +y up). */
function lookAtIcrs(forward: Vec3): Quat {
  const f = new THREE.Vector3(...forward).normalize();
  const hint = Math.abs(f.z) > 0.999 ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 0, 1);
  const right = new THREE.Vector3().crossVectors(f, hint).normalize();
  const up = new THREE.Vector3().crossVectors(right, f).normalize();
  const q = new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(right, up, f.clone().negate()));
  return [q.x, q.y, q.z, q.w];
}

/** A pose as a renderer reports it: the sky sphere turned into world axes, the camera turned in it. */
function poseFor(orientationIcrs: Quat, fovDeg: number, aspect: number, limitingMagnitude = 6.5, icrsWorld = SCENE_ICRS): SkyCameraPose {
  const camera = new THREE.Quaternion(...icrsWorld).multiply(new THREE.Quaternion(...orientationIcrs));
  return {
    cameraWorldQuaternion: [camera.x, camera.y, camera.z, camera.w],
    icrsWorldQuaternion: icrsWorld,
    fovDeg,
    aspect,
    limitingMagnitude,
  };
}

const isComplete = (s: AudioSourceInput) => s.distancePc != null && s.teffK != null && s.luminositySolar != null;

function sourceRow(tiles: readonly SkyTileData[], id: string) {
  const match = /^[^:]+:L(\d+)\/(\d+)#(\d+)$/.exec(id);
  if (!match) throw new Error(`unexpected source id ${id}`);
  const [layer, pix, index] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const tile = tiles.find((t) => t.core.layer === layer && t.core.pix === pix)!;
  return { tile, index };
}

/** On screen by three's own projection: the reference the view test is checked against. */
function projectsOnScreen(direction: Vec3, orientationIcrs: Quat, fovDeg: number, aspect: number): { onScreen: boolean; edge: number } {
  const camera = new THREE.PerspectiveCamera(fovDeg, aspect, 0.05, 2800);
  camera.quaternion.set(...orientationIcrs);
  camera.updateMatrixWorld(true);
  camera.updateProjectionMatrix();
  const point = new THREE.Vector3(...direction).multiplyScalar(10);
  const local = camera.worldToLocal(point.clone());
  const ndc = point.project(camera);
  const inFront = local.z < 0;
  const onScreen = inFront && Math.abs(ndc.x) <= 1 && Math.abs(ndc.y) <= 1;
  // How close the star sits to the screen's border, in NDC; ties at the border are not tested
  const edge = inFront ? Math.min(Math.abs(Math.abs(ndc.x) - 1), Math.abs(Math.abs(ndc.y) - 1)) : Number.POSITIVE_INFINITY;
  return { onScreen, edge };
}

const unitQuat = fc
  .tuple(
    fc.double({ min: -1, max: 1, noNaN: true }),
    fc.double({ min: -1, max: 1, noNaN: true }),
    fc.double({ min: -1, max: 1, noNaN: true }),
    fc.double({ min: -1, max: 1, noNaN: true })
  )
  .filter((q) => Math.hypot(...q) > 1e-3)
  .map((q): Quat => {
    const n = Math.hypot(...q);
    return [q[0] / n, q[1] / n, q[2] / n, q[3] / n];
  });

const unitVec = fc
  .tuple(
    fc.double({ min: -1, max: 1, noNaN: true }),
    fc.double({ min: -1, max: 1, noNaN: true }),
    fc.double({ min: -1, max: 1, noNaN: true })
  )
  .filter((v) => Math.hypot(...v) > 1e-3)
  .map((v): Vec3 => {
    const n = Math.hypot(...v);
    return [v[0] / n, v[1] / n, v[2] / n];
  });

const fovDeg = fc.double({ min: 18, max: 74, noNaN: true });
const aspect = fc.double({ min: 0.5, max: 3, noNaN: true });

/** A direction placed by screen position: (u, v) in units of the half field of view, in front or behind. */
function screenDirection(view: SkyView, u: number, v: number, behind: boolean): Vec3 {
  const x = u * view.tanHalfFovX;
  const y = v * view.tanHalfFovY;
  const z = behind ? 1 : -1;
  const n = Math.hypot(x, y, z);
  return rotateByQuat([x / n, y / n, z / n], view.orientationIcrs);
}

describe('the catalog stars in view, from real Gaia DR3 and Hipparcos rows', () => {
  it('voices Segin, Fulu and Achird from their catalog values and keeps Caph, Schedar and Ruchbah silent', () => {
    const orientation = lookAtIcrs(raDecDegToVec(15, 60));
    const view = skyViewFromPose(poseFor(orientation, 20, 16 / 9))!;
    const result = skySourcesInView(L0, view, { releaseId: manifest.releaseId });
    const context = { distanceWindow: STAR_DISTANCE_WINDOW, listenerOrientation: view.orientationIcrs };

    // The loudest complete stars, by received level from FLAME luminosity and Bailer-Jones distance
    const complete = result.sources.filter(isComplete);
    expect(complete.slice(0, 3).map((s) => s.id)).toEqual(['Segin', 'Fulu', 'Achird'].map(idOf));

    const byName = (name: string) => result.sources.find((s) => s.id === idOf(name))!;
    const segin = byName('Segin');
    const achird = byName('Achird');
    // Archive values as the tiles carry them: GSP-Phot Teff in whole kelvin, FLAME L, Bailer-Jones d
    expect(segin.teffK).toBe(14806);
    expect(segin.luminositySolar).toBeGreaterThan(3050);
    expect(segin.luminositySolar).toBeLessThan(3075);
    expect(segin.distancePc).toBeGreaterThan(139.5);
    expect(segin.distancePc).toBeLessThan(141.5);
    expect(achird.teffK).toBe(5726);
    expect(Math.abs(achird.luminositySolar! / 1.2662435 - 1)).toBeLessThan(0.00116);
    expect(achird.distancePc).toBeGreaterThan(5.9);
    expect(achird.distancePc).toBeLessThan(5.95);

    // Both look alike at G 3.3, but Segin's bolometric flux is about 6.3 dB greater, and it is hotter
    const seginPlan = planVoice(segin, context);
    const achirdPlan = planVoice(achird, context);
    expect(seginPlan.status).toBe('audible');
    expect(achirdPlan.status).toBe('audible');
    if (seginPlan.status !== 'audible' || achirdPlan.status !== 'audible') return;
    expect(seginPlan.plan.levelDb - achirdPlan.plan.levelDb).toBeGreaterThan(6.2);
    expect(seginPlan.plan.levelDb - achirdPlan.plan.levelDb).toBeLessThan(6.45);
    expect(seginPlan.plan.star!.planckScaleHz / achirdPlan.plan.star!.planckScaleHz).toBeCloseTo(14806 / 5726, 9);

    // Hipparcos entries carry no Gaia DR3 astrophysics: silent, with the missing inputs named
    for (const name of ['Caph', 'Schedar', 'Ruchbah']) {
      const star = byName(name);
      expect(star, name).toBeDefined();
      expect(star.teffK).toBeNull();
      expect(star.luminositySolar).toBeNull();
      expect(star.distancePc).toBeGreaterThan(0);
      expect(planVoice(star, context)).toMatchObject({
        status: 'silent',
        reason: 'missing_input',
        missing: ['luminositySolar', 'teffK'],
      });
    }

    // Every value is the tile's own, and the direction is the tile's ICRS direction
    for (const source of result.sources) {
      const { tile, index } = sourceRow(L0, source.id);
      const asIs = (value: number | undefined) => (value !== undefined && Number.isFinite(value) ? value : null);
      expect(source.teffK).toBe(asIs(tile.astro!.teffK[index]));
      expect(source.luminositySolar).toBe(asIs(tile.astro!.luminositySolar[index]));
      expect(source.distancePc).toBe(asIs(tile.core.distancePc[index]));
      expect(source.directionWorld).toEqual([
        tile.core.directions[3 * index],
        tile.core.directions[3 * index + 1],
        tile.core.directions[3 * index + 2],
      ]);
      expect(source.extinctionAvMag).toBeUndefined();
    }

    // The counts agree with three's projection of every drawn star
    let onScreen = 0;
    let completeOnScreen = 0;
    for (const { core, astro } of L0) {
      for (let i = 0; i < core.count; i++) {
        if (!(core.magnitude[i]! < 6.5)) continue;
        const d: Vec3 = [core.directions[3 * i]!, core.directions[3 * i + 1]!, core.directions[3 * i + 2]!];
        if (!projectsOnScreen(d, orientation, 20, 16 / 9).onScreen) continue;
        onScreen += 1;
        const values = [core.distancePc[i], astro!.teffK[i], astro!.luminositySolar[i]];
        if (values.every((value) => Number.isFinite(value))) completeOnScreen += 1;
      }
    }
    expect(result.starsInView).toBe(onScreen);
    expect(result.completeInView).toBe(completeOnScreen);
    expect(result.starsInView).toBeGreaterThan(200);
    expect(result.tilesWithoutAstro).toEqual([]);
  });

  it('keeps Sirius and Vega silent: Hipparcos entries with distances but no Gaia DR3 temperature or luminosity', () => {
    for (const [name, minPc, maxPc] of [
      ['Sirius', 2.6, 2.68],
      ['Vega', 7.6, 7.76],
    ] as const) {
      const e = entry(name);
      expect(e.catalog).toBe('hipparcos-2007');
      const view = skyViewFromPose(poseFor(lookAtIcrs(raDecDegToVec(e.raDeg, e.decDeg)), 30, 16 / 9))!;
      const { sources } = skySourcesInView(L0, view, { releaseId: manifest.releaseId });
      const star = sources.find((s) => s.id === idOf(name));
      expect(star, name).toBeDefined();
      expect(star!.teffK).toBeNull();
      expect(star!.luminositySolar).toBeNull();
      expect(star!.distancePc).toBeGreaterThan(minPc);
      expect(star!.distancePc).toBeLessThan(maxPc);
      expect(
        planVoice(star!, { distanceWindow: STAR_DISTANCE_WINDOW, listenerOrientation: view.orientationIcrs })
      ).toMatchObject({ status: 'silent', reason: 'missing_input', missing: ['luminositySolar', 'teffK'] });
    }
  });
});

describe('Property: the stars sound where the camera sees them', () => {
  it('turns the camera pose into the listener orientation in ICRS: the camera relative to the sky sphere', () => {
    fc.assert(
      fc.property(unitQuat, unitQuat, (icrsWorld, orientation) => {
        const q = listenerOrientationIcrs(poseFor(orientation, 56, 16 / 9, 6.5, icrsWorld))!;
        // A quaternion and its negative are the same rotation
        const dot = q[0] * orientation[0] + q[1] * orientation[1] + q[2] * orientation[2] + q[3] * orientation[3];
        expect(Math.abs(dot)).toBeCloseTo(1, 9);
      }),
      { seed: SEED }
    );
  });

  it('places a drawn star, in a scene graph like the renderers', () => {
    // A rotated parent, an ICRS star sphere centred on the camera, a camera turned inside a moved rig:
    // the listener-local direction equals the star's direction in camera space, wherever it is drawn
    fc.assert(
      fc.property(
        unitQuat,
        fc.double({ min: -Math.PI, max: Math.PI, noNaN: true }),
        fc.double({ min: -1.15, max: 1.15, noNaN: true }),
        fc.tuple(fc.double({ min: -200, max: 200, noNaN: true }), fc.double({ min: -200, max: 200, noNaN: true }), fc.double({ min: -200, max: 200, noNaN: true })),
        unitVec,
        (parentQuat, yaw, pitch, rigPosition, starIcrs) => {
          const scene = new THREE.Scene();
          const parent = new THREE.Group();
          parent.quaternion.set(...parentQuat);
          scene.add(parent);
          const mount = new THREE.Group();
          mount.quaternion.copy(ICRS_TO_SCENE_QUATERNION);
          mount.scale.setScalar(85);
          parent.add(mount);
          const rig = new THREE.Group();
          rig.position.set(...rigPosition);
          scene.add(rig);
          const camera = new THREE.PerspectiveCamera(56, 16 / 9, 0.05, 2800);
          camera.rotation.order = 'YXZ';
          camera.rotation.set(pitch, yaw, 0);
          rig.add(camera);
          scene.updateMatrixWorld(true);
          centreOnCamera(mount, camera);
          scene.updateMatrixWorld(true);

          // What getCameraSkyPose() reads in both renderers
          const cameraWorld = camera.getWorldQuaternion(new THREE.Quaternion());
          const icrsWorld = mount.getWorldQuaternion(new THREE.Quaternion());
          const orientation = listenerOrientationIcrs({
            cameraWorldQuaternion: [cameraWorld.x, cameraWorld.y, cameraWorld.z, cameraWorld.w],
            icrsWorldQuaternion: [icrsWorld.x, icrsWorld.y, icrsWorld.z, icrsWorld.w],
          })!;

          const drawnAt = mount.localToWorld(new THREE.Vector3(...starIcrs));
          const seen = camera.worldToLocal(drawnAt).normalize();
          const heard = worldToListenerDirection(starIcrs, orientation)!;
          expect(heard[0]).toBeCloseTo(seen.x, 6);
          expect(heard[1]).toBeCloseTo(seen.y, 6);
          expect(heard[2]).toBeCloseTo(seen.z, 6);
        }
      ),
      { seed: SEED }
    );
  });

  it('counts a star on screen exactly where three projects it on screen', () => {
    fc.assert(
      fc.property(
        unitQuat,
        fovDeg,
        aspect,
        fc.double({ min: -1.6, max: 1.6, noNaN: true }),
        fc.double({ min: -1.6, max: 1.6, noNaN: true }),
        fc.boolean(),
        (orientation, fov, asp, u, v, behind) => {
          const view = skyViewFromPose(poseFor(orientation, fov, asp))!;
          const direction = screenDirection(view, u, v, behind);
          const reference = projectsOnScreen(direction, orientation, fov, asp);
          fc.pre(reference.edge > 1e-6);
          expect(directionInView(direction, view)).toBe(reference.onScreen);
        }
      ),
      {
        seed: SEED,
        examples: [
          [[0, 0, 0, 1], 56, 16 / 9, 0, 0, false],
          [[0, 0, 0, 1], 56, 16 / 9, 0, 0, true],
          [[0, 0, 0, 1], 56, 16 / 9, 0.99, -0.99, false],
          [[0, 0, 0, 1], 56, 16 / 9, 1.01, 0, false],
        ],
      }
    );
  });
});

interface SyntheticStar {
  readonly u: number;
  readonly v: number;
  readonly behind: boolean;
  readonly magnitude: number;
  readonly distancePc: number;
  readonly teffK: number;
  readonly luminositySolar: number;
}

/** An order 0 tile with made-up stars, for the properties; the real rows are tested above. */
function syntheticTile(pix: number, view: SkyView, stars: readonly SyntheticStar[], withAstro = true): SkyTileData {
  const n = stars.length;
  const nan = () => new Float32Array(n).fill(Number.NaN);
  const core: CatalogStarCoreTile = {
    layer: 0,
    order: 0,
    pix,
    count: n,
    positionBits: 32,
    directions: Float32Array.from(stars.flatMap((s) => screenDirection(view, s.u, s.v, s.behind))),
    magnitude: Float32Array.from(stars.map((s) => s.magnitude)),
    colorIndex: nan(),
    distancePc: Float32Array.from(stars.map((s) => s.distancePc)),
    flags: new Uint8Array(n),
    byteLength: 0,
  };
  const astro: CatalogStarAstroTile = {
    layer: 0,
    order: 0,
    pix,
    count: n,
    teffK: Float32Array.from(stars.map((s) => s.teffK)),
    teffLowerK: nan(),
    teffUpperK: nan(),
    luminositySolar: Float32Array.from(stars.map((s) => s.luminositySolar)),
    luminosityLowerSolar: nan(),
    luminosityUpperSolar: nan(),
    a0Mag: nan(),
    a0LowerMag: nan(),
    a0UpperMag: nan(),
    agMag: nan(),
    ruwe: nan(),
    parallaxErrorMas: nan(),
    byteLength: 0,
  };
  return { core, astro: withAstro ? astro : null };
}

const maybe = (arb: fc.Arbitrary<number>) => fc.oneof({ weight: 3, arbitrary: arb }, { weight: 1, arbitrary: fc.constant(Number.NaN) });
const syntheticStar: fc.Arbitrary<SyntheticStar> = fc.record({
  u: fc.double({ min: -1.3, max: 1.3, noNaN: true }),
  v: fc.double({ min: -1.3, max: 1.3, noNaN: true }),
  behind: fc.boolean(),
  magnitude: fc.double({ min: -1.5, max: 9, noNaN: true }),
  distancePc: maybe(fc.double({ min: 1, max: 5000, noNaN: true })),
  teffK: maybe(fc.double({ min: 2000, max: 40000, noNaN: true })),
  luminositySolar: maybe(fc.double({ min: 1e-3, max: 1e5, noNaN: true })),
});

/** A view centred on an order 0 tile, so the made-up stars around it pass the tile cull. */
const tileView = fc.record({ pix: fc.integer({ min: 0, max: 11 }), fov: fovDeg, asp: aspect, limit: fc.double({ min: 2, max: 8, noNaN: true }) }).map(
  ({ pix, fov, asp, limit }) => ({ pix, view: skyViewFromPose(poseFor(lookAtIcrs(tileCenter(0, pix)), fov, asp, limit))! })
);

describe('Property: catalog values are carried as they are', () => {
  it('passes each value through and leaves a value Gaia DR3 lacks missing, never filled', () => {
    fc.assert(
      fc.property(tileView, fc.array(syntheticStar, { maxLength: 40 }), ({ pix, view }, stars) => {
        const tile = syntheticTile(pix, view, stars);
        const { sources } = skySourcesInView([tile], view, { releaseId: 'test', maxAudible: 100, maxSilent: 100 });
        for (const source of sources) {
          const { index } = sourceRow([tile], source.id);
          const asIs = (value: number | undefined) => (value !== undefined && Number.isFinite(value) ? value : null);
          expect(source.kind).toBe('star');
          expect(source.distancePc).toBe(asIs(tile.core.distancePc[index]));
          expect(source.teffK).toBe(asIs(tile.astro!.teffK[index]));
          expect(source.luminositySolar).toBe(asIs(tile.astro!.luminositySolar[index]));
          expect(source.extinctionAvMag).toBeUndefined();
          expect(source.residualArcsec).toBeUndefined();
          expect(source.isTarget).toBeUndefined();
        }
      }),
      { seed: SEED }
    );
  });

  it('gives no source to a star at or fainter than the drawn limit, and one to each brighter star on screen', () => {
    fc.assert(
      fc.property(tileView, fc.array(syntheticStar, { maxLength: 40 }), ({ pix, view }, stars) => {
        const tile = syntheticTile(pix, view, stars);
        const { sources, starsInView } = skySourcesInView([tile], view, { releaseId: 'test', maxAudible: 100, maxSilent: 100 });
        const passed = new Set(sources.map((s) => s.id));
        let expected = 0;
        for (let i = 0; i < tile.core.count; i++) {
          const id = skySourceId('test', 0, pix, i);
          const direction: Vec3 = [tile.core.directions[3 * i]!, tile.core.directions[3 * i + 1]!, tile.core.directions[3 * i + 2]!];
          const visible = tile.core.magnitude[i]! < view.limitingMagnitude && directionInView(direction, view);
          if (visible) expected += 1;
          expect(passed.has(id)).toBe(visible);
        }
        expect(starsInView).toBe(expected);
      }),
      { seed: SEED }
    );
  });
});

describe('Property: a bounded, ranked source list', () => {
  it('passes at most the loudest complete stars and the brightest incomplete ones, whatever the tile order', () => {
    fc.assert(
      fc.property(
        tileView,
        fc.array(fc.array(syntheticStar, { maxLength: 30 }), { minLength: 1, maxLength: 3 }),
        fc.integer({ min: 0, max: 10 }),
        fc.integer({ min: 0, max: 10 }),
        ({ pix, view }, groups, maxAudible, maxSilent) => {
          // Every group sits around the view, under distinct tile ids
          const tiles = groups.map((stars, k) => ({ ...syntheticTile(pix, view, stars), core: { ...syntheticTile(pix, view, stars).core, layer: k } }));
          const result = skySourcesInView(tiles, view, { releaseId: 'test', maxAudible, maxSilent });
          const reversed = skySourcesInView([...tiles].reverse(), view, { releaseId: 'test', maxAudible, maxSilent });
          expect(reversed.sources).toEqual(result.sources);

          const complete = result.sources.filter(isComplete);
          const incomplete = result.sources.filter((s) => !isComplete(s));
          expect(complete.length).toBeLessThanOrEqual(maxAudible);
          expect(incomplete.length).toBeLessThanOrEqual(maxSilent);
          expect(result.sources.slice(0, complete.length)).toEqual(complete);

          const level = (s: AudioSourceInput) => receivedLevelDb(s.luminositySolar, s.distancePc)!;
          for (let i = 1; i < complete.length; i++) expect(level(complete[i - 1]!)).toBeGreaterThanOrEqual(level(complete[i]!));

          const magnitudeOf = (s: AudioSourceInput) => {
            const { tile, index } = sourceRow(tiles, s.id);
            return tile.core.magnitude[index]!;
          };
          for (let i = 1; i < incomplete.length; i++) expect(magnitudeOf(incomplete[i - 1]!)).toBeLessThanOrEqual(magnitudeOf(incomplete[i]!));

          // Nothing louder than the quietest complete star passed was left out
          const all = skySourcesInView(tiles, view, { releaseId: 'test', maxAudible: 1000, maxSilent: 1000 });
          const allComplete = all.sources.filter(isComplete);
          expect(complete.length).toBe(Math.min(maxAudible, allComplete.length));
          expect(complete.map((s) => s.id)).toEqual(allComplete.slice(0, complete.length).map((s) => s.id));
        }
      ),
      { seed: SEED }
    );
  });
});

describe('what the source list leaves out and carries', () => {
  const view = skyViewFromPose(poseFor(lookAtIcrs(tileCenter(0, 4)), 56, 16 / 9))!;
  const star = (u: number, v: number, overrides: Partial<SyntheticStar> = {}): SyntheticStar => ({
    u,
    v,
    behind: false,
    magnitude: 3,
    distancePc: 50,
    teffK: 6000,
    luminositySolar: 20,
    ...overrides,
  });

  it('leaves out the stars of a tile whose astro columns have not arrived, rather than calling them missing', () => {
    const pending = syntheticTile(4, view, [star(0, 0), star(0.5, 0.5, { distancePc: Number.NaN })], false);
    const result = skySourcesInView([pending], view, { releaseId: 'test' });
    expect(result.sources).toEqual([]);
    expect(result.starsInView).toBe(0);
    expect(result.tilesWithoutAstro).toEqual(['0/4']);
  });

  it('keeps a voiced star through jitter at the screen edge, and only a voiced one', () => {
    const tile = syntheticTile(4, view, [star(1.02, 0)]);
    const id = skySourceId('test', 0, 4, 0);
    expect(skySourcesInView([tile], view, { releaseId: 'test' }).sources).toEqual([]);
    const kept = skySourcesInView([tile], view, { releaseId: 'test', incumbents: new Set([id]) });
    expect(kept.sources.map((s) => s.id)).toEqual([id]);
    expect(kept.starsInView).toBe(0);
    const far = syntheticTile(4, view, [star(1.6, 0)]);
    expect(skySourcesInView([far], view, { releaseId: 'test', incumbents: new Set([id]) }).sources).toEqual([]);
  });

  it("carries the star layer's evidence class when one is declared, and none otherwise", () => {
    const tile = syntheticTile(4, view, [star(0, 0)]);
    expect(skySourcesInView([tile], view, { releaseId: 'test', evidenceClass: 'simulated' }).sources[0]!.evidenceClass).toBe('simulated');
    expect('evidenceClass' in skySourcesInView([tile], view, { releaseId: 'test', evidenceClass: null }).sources[0]!).toBe(false);
  });

  it('refuses a pose that describes no view', () => {
    const good = poseFor(lookAtIcrs([1, 0, 0]), 56, 16 / 9);
    expect(skyViewFromPose(good)).not.toBeNull();
    expect(skyViewFromPose({ ...good, cameraWorldQuaternion: [0, 0, 0, 0] })).toBeNull();
    expect(skyViewFromPose({ ...good, icrsWorldQuaternion: [Number.NaN, 0, 0, 1] })).toBeNull();
    expect(skyViewFromPose({ ...good, fovDeg: 0 })).toBeNull();
    expect(skyViewFromPose({ ...good, fovDeg: 180 })).toBeNull();
    expect(skyViewFromPose({ ...good, aspect: 0 })).toBeNull();
    expect(skyViewFromPose({ ...good, limitingMagnitude: Number.NaN })).toBeNull();
  });
});
