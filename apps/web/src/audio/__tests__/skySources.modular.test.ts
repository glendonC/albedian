import { describe, expect, it } from 'vitest';
import type { CatalogStarAstroTile, CatalogStarCoreTile } from '../../spatial/catalogStars/tileCodec';
import { tileBoundingRadius, tileCenter } from '../../spatial/catalogStars/healpix';
import { rotateByQuat, type Quat, type Vec3 } from '../voicePlanning';
import * as skySourcesModule from '../skySources';
import {
  DEFAULT_INCUMBENT_MARGIN_RAD,
  DEFAULT_MAX_AUDIBLE_SOURCES,
  DEFAULT_MAX_SILENT_SOURCES,
  angleBetweenUnit,
  conjugateQuat,
  directionInView,
  listenerOrientationIcrs,
  multiplyQuat,
  skySourceId,
  skySourcesInView,
  skyViewFromPose,
  widenView,
  type SkyCameraPose,
  type SkySourceOptions,
  type SkySourceResult,
  type SkyTileData,
  type SkyView,
} from '../skySources';

const IDENTITY_QUAT: Quat = [0, 0, 0, 1];

function axisAngleQuat(axis: Vec3, angleRad: number): Quat {
  const norm = Math.hypot(axis[0], axis[1], axis[2]);
  const half = angleRad / 2;
  const s = Math.sin(half) / norm;
  return [axis[0] * s, axis[1] * s, axis[2] * s, Math.cos(half)];
}

function lookTowardIcrs(target: Vec3): Quat {
  // Rotate camera -z [0, 0, -1] onto unit vector `target`.
  const norm = Math.hypot(target[0], target[1], target[2]);
  const tx = target[0] / norm;
  const ty = target[1] / norm;
  const tz = target[2] / norm;
  if (tz < -0.999999) return IDENTITY_QUAT;
  if (tz > 0.999999) return [0, 1, 0, 0];
  // Cross([0, 0, -1], [tx, ty, tz]) = [ty, -tx, 0]; dot = -tz.
  const axis: Vec3 = [ty, -tx, 0];
  const angle = Math.atan2(Math.hypot(ty, -tx), -tz);
  return axisAngleQuat(axis, angle);
}

interface StarRowSpec {
  readonly direction: Vec3;
  readonly magnitude: number;
  readonly distancePc: number;
  readonly teffK: number;
  readonly luminositySolar: number;
}

function buildTile(
  spec: { layer?: number; order?: number; pix: number; astroCountOverride?: number; withAstro?: boolean },
  rows: readonly StarRowSpec[]
): SkyTileData {
  const layer = spec.layer ?? 0;
  const order = spec.order ?? 0;
  const count = rows.length;
  const fillNan = (len: number) => new Float32Array(len).fill(Number.NaN);
  const core: CatalogStarCoreTile = {
    layer,
    order,
    pix: spec.pix,
    count,
    positionBits: 32,
    directions: Float32Array.from(rows.flatMap((r) => [r.direction[0], r.direction[1], r.direction[2]])),
    magnitude: Float32Array.from(rows.map((r) => r.magnitude)),
    colorIndex: fillNan(count),
    distancePc: Float32Array.from(rows.map((r) => r.distancePc)),
    flags: new Uint8Array(count),
    byteLength: 0,
  };
  if (spec.withAstro === false) {
    return { core, astro: null };
  }
  const astroCount = spec.astroCountOverride ?? count;
  const astro: CatalogStarAstroTile = {
    layer,
    order,
    pix: spec.pix,
    count: astroCount,
    teffK: Float32Array.from({ length: astroCount }, (_, i) => rows[i]?.teffK ?? Number.NaN),
    teffLowerK: fillNan(astroCount),
    teffUpperK: fillNan(astroCount),
    luminositySolar: Float32Array.from({ length: astroCount }, (_, i) => rows[i]?.luminositySolar ?? Number.NaN),
    luminosityLowerSolar: fillNan(astroCount),
    luminosityUpperSolar: fillNan(astroCount),
    a0Mag: fillNan(astroCount),
    a0LowerMag: fillNan(astroCount),
    a0UpperMag: fillNan(astroCount),
    agMag: fillNan(astroCount),
    ruwe: fillNan(astroCount),
    parallaxErrorMas: fillNan(astroCount),
    byteLength: 0,
  };
  return { core, astro };
}

describe('skySources public module surface', () => {
  it('exposes the exact 12 runtime bindings with zero internal helper leakage', () => {
    expect(Object.keys(skySourcesModule).sort()).toEqual([
      'DEFAULT_INCUMBENT_MARGIN_RAD',
      'DEFAULT_MAX_AUDIBLE_SOURCES',
      'DEFAULT_MAX_SILENT_SOURCES',
      'angleBetweenUnit',
      'conjugateQuat',
      'directionInView',
      'listenerOrientationIcrs',
      'multiplyQuat',
      'skySourceId',
      'skySourcesInView',
      'skyViewFromPose',
      'widenView',
    ]);
    expect(DEFAULT_MAX_AUDIBLE_SOURCES).toBe(24);
    expect(DEFAULT_MAX_SILENT_SOURCES).toBe(8);
    expect(DEFAULT_INCUMBENT_MARGIN_RAD).toBe(0.035);
  });

  it('preserves exported TypeScript interfaces across pose, view, tile, options and result', () => {
    const pose: SkyCameraPose = {
      cameraWorldQuaternion: IDENTITY_QUAT,
      icrsWorldQuaternion: IDENTITY_QUAT,
      fovDeg: 60,
      aspect: 16 / 9,
      limitingMagnitude: 6.5,
    };
    const view: SkyView | null = skyViewFromPose(pose);
    expect(view).not.toBeNull();
    const tile: SkyTileData = buildTile({ pix: 0, withAstro: false }, []);
    const options: SkySourceOptions = { releaseId: 'gaia-dr3' };
    const result: SkySourceResult = skySourcesInView([tile], view!, options);
    expect(Array.isArray(result.sources)).toBe(true);
  });
});

describe('ICRS quaternion algebra and angular separation', () => {
  it('computes Hamilton product in three.js (x, y, z, w) order with non-commutative basis rules', () => {
    const i: Quat = [1, 0, 0, 0];
    const j: Quat = [0, 1, 0, 0];
    const k: Quat = [0, 0, 1, 0];

    expect(multiplyQuat(IDENTITY_QUAT, [0.2, -0.4, 0.6, 0.8])).toEqual([0.2, -0.4, 0.6, 0.8]);
    expect(multiplyQuat([0.2, -0.4, 0.6, 0.8], IDENTITY_QUAT)).toEqual([0.2, -0.4, 0.6, 0.8]);

    // i * j = k, j * i = -k
    expect(multiplyQuat(i, j)).toEqual([0, 0, 1, 0]);
    expect(multiplyQuat(j, i)).toEqual([0, 0, -1, 0]);
    // j * k = i, k * j = -i
    expect(multiplyQuat(j, k)).toEqual([1, 0, 0, 0]);
    expect(multiplyQuat(k, j)).toEqual([-1, 0, 0, 0]);
    // k * i = j, i * k = -j
    expect(multiplyQuat(k, i)).toEqual([0, 1, 0, 0]);
    expect(multiplyQuat(i, k)).toEqual([0, -1, 0, 0]);
    // i^2 = j^2 = k^2 = -1
    expect(multiplyQuat(i, i)).toEqual([0, 0, 0, -1]);
    expect(multiplyQuat(j, j)).toEqual([0, 0, 0, -1]);
    expect(multiplyQuat(k, k)).toEqual([0, 0, 0, -1]);
  });

  it('conjugates vector components while preserving scalar w and inverting unit rotations', () => {
    const q: Quat = [0.5, -0.5, 0.5, 0.5];
    const conj = conjugateQuat(q);
    expect(conj).toEqual([-0.5, 0.5, -0.5, 0.5]);

    const rightProd = multiplyQuat(q, conj);
    const leftProd = multiplyQuat(conj, q);
    for (const prod of [rightProd, leftProd]) {
      expect(prod[0]).toBeCloseTo(0, 12);
      expect(prod[1]).toBeCloseTo(0, 12);
      expect(prod[2]).toBeCloseTo(0, 12);
      expect(prod[3]).toBeCloseTo(1, 12);
    }
  });

  it('computes normalized (icrs^-1 * camera) in listenerOrientationIcrs and rejects degenerate quaternions', () => {
    const rotX90 = axisAngleQuat([1, 0, 0], Math.PI / 2);
    const rotY90 = axisAngleQuat([0, 1, 0], Math.PI / 2);

    // Non-commuting world rotations distinguish conjugate(icrs) * camera from camera * conjugate(icrs).
    const expected = multiplyQuat(conjugateQuat(rotX90), rotY90);
    const reversedOrder = multiplyQuat(rotY90, conjugateQuat(rotX90));
    expect(expected[2]).not.toBeCloseTo(reversedOrder[2], 3);

    // Scale both input quaternions by non-unit factors to verify normalization.
    const orientation = listenerOrientationIcrs({
      cameraWorldQuaternion: [rotY90[0] * 3, rotY90[1] * 3, rotY90[2] * 3, rotY90[3] * 3],
      icrsWorldQuaternion: [rotX90[0] * 5, rotX90[1] * 5, rotX90[2] * 5, rotX90[3] * 5],
    });
    expect(orientation).not.toBeNull();
    expect(Math.hypot(...orientation!)).toBeCloseTo(1, 12);
    for (let idx = 0; idx < 4; idx++) {
      expect(orientation![idx]).toBeCloseTo(expected[idx]!, 12);
    }

    // Degenerate and non-finite inputs return null.
    expect(listenerOrientationIcrs({ cameraWorldQuaternion: [0, 0, 0, 0], icrsWorldQuaternion: IDENTITY_QUAT })).toBeNull();
    expect(listenerOrientationIcrs({ cameraWorldQuaternion: IDENTITY_QUAT, icrsWorldQuaternion: [0, 0, 0, 0] })).toBeNull();
    expect(listenerOrientationIcrs({ cameraWorldQuaternion: [Number.NaN, 0, 0, 1], icrsWorldQuaternion: IDENTITY_QUAT })).toBeNull();
    expect(
      listenerOrientationIcrs({ cameraWorldQuaternion: IDENTITY_QUAT, icrsWorldQuaternion: [0, Number.POSITIVE_INFINITY, 0, 1] })
    ).toBeNull();
  });

  it('measures angleBetweenUnit accurately at 0, pi/2, pi and sub-microradian separations', () => {
    expect(angleBetweenUnit([1, 0, 0], [1, 0, 0])).toBe(0);
    expect(angleBetweenUnit([1, 0, 0], [0, 1, 0])).toBeCloseTo(Math.PI / 2, 12);
    expect(angleBetweenUnit([0, 1, 0], [0, 0, 1])).toBeCloseTo(Math.PI / 2, 12);
    expect(angleBetweenUnit([0, 0, 1], [0, 0, -1])).toBeCloseTo(Math.PI, 12);

    // At 1e-10 rad, 1 - cos(delta) underflows to 0 in float64 so naive acos(dot) returns 0, whereas atan2(||cross||, dot) stays exact.
    const tiny = 1e-10;
    const nearCollinear: Vec3 = [Math.cos(tiny), Math.sin(tiny), 0];
    expect(angleBetweenUnit([1, 0, 0], nearCollinear)).toBeCloseTo(tiny, 20);

    const nearAntipodal: Vec3 = [-Math.cos(tiny), Math.sin(tiny), 0];
    expect(Math.PI - angleBetweenUnit([1, 0, 0], nearAntipodal)).toBeCloseTo(tiny, 15);
  });
});

describe('frustum view construction, widening and screen containment', () => {
  const basePose: SkyCameraPose = {
    cameraWorldQuaternion: IDENTITY_QUAT,
    icrsWorldQuaternion: IDENTITY_QUAT,
    fovDeg: 90,
    aspect: 2,
    limitingMagnitude: 6.5,
  };

  it('derives exact frustum tangents, half-diagonal and center direction in skyViewFromPose', () => {
    const view = skyViewFromPose(basePose);
    expect(view).not.toBeNull();
    expect(view!.centerIcrs[0]).toBeCloseTo(0, 12);
    expect(view!.centerIcrs[1]).toBeCloseTo(0, 12);
    expect(view!.centerIcrs[2]).toBeCloseTo(-1, 12);
    expect(view!.tanHalfFovY).toBeCloseTo(1, 12);
    expect(view!.tanHalfFovX).toBeCloseTo(2, 12);
    expect(view!.halfDiagonalRad).toBeCloseTo(Math.atan(Math.sqrt(5)), 12);
    expect(view!.limitingMagnitude).toBe(6.5);
  });

  it('rejects out-of-range fovDeg, non-positive aspect and non-finite limitingMagnitude while accepting negative magnitudes', () => {
    expect(skyViewFromPose({ ...basePose, fovDeg: 0 })).toBeNull();
    expect(skyViewFromPose({ ...basePose, fovDeg: -10 })).toBeNull();
    expect(skyViewFromPose({ ...basePose, fovDeg: 180 })).toBeNull();
    expect(skyViewFromPose({ ...basePose, fovDeg: 185 })).toBeNull();
    expect(skyViewFromPose({ ...basePose, fovDeg: Number.NaN })).toBeNull();
    expect(skyViewFromPose({ ...basePose, aspect: 0 })).toBeNull();
    expect(skyViewFromPose({ ...basePose, aspect: -1.5 })).toBeNull();
    expect(skyViewFromPose({ ...basePose, aspect: Number.POSITIVE_INFINITY })).toBeNull();
    expect(skyViewFromPose({ ...basePose, limitingMagnitude: Number.NaN })).toBeNull();
    expect(skyViewFromPose({ ...basePose, limitingMagnitude: Number.POSITIVE_INFINITY })).toBeNull();
    expect(skyViewFromPose({ ...basePose, limitingMagnitude: Number.NEGATIVE_INFINITY })).toBeNull();

    const brightOnly = skyViewFromPose({ ...basePose, limitingMagnitude: -1.0 });
    expect(brightOnly).not.toBeNull();
    expect(brightOnly!.limitingMagnitude).toBe(-1.0);
  });

  it('returns the original view for non-positive marginRad and caps widened angles below pi/2 and pi', () => {
    const view = skyViewFromPose(basePose)!;
    expect(widenView(view, 0)).toBe(view);
    expect(widenView(view, -0.05)).toBe(view);
    expect(widenView(view, Number.NaN)).toBe(view);

    const margin = 0.05;
    const widened = widenView(view, margin);
    expect(widened.orientationIcrs).toBe(view.orientationIcrs);
    expect(widened.centerIcrs).toBe(view.centerIcrs);
    expect(widened.limitingMagnitude).toBe(view.limitingMagnitude);
    expect(widened.tanHalfFovY).toBeCloseTo(Math.tan(Math.PI / 4 + margin), 12);
    expect(widened.tanHalfFovX).toBeCloseTo(Math.tan(Math.atan(2) + margin), 12);
    expect(widened.halfDiagonalRad).toBeCloseTo(view.halfDiagonalRad + margin, 12);

    const hugeMargin = widenView(view, 4.0);
    const maxHalfAngle = Math.PI / 2 - 1e-6;
    expect(hugeMargin.tanHalfFovX).toBeCloseTo(Math.tan(maxHalfAngle), 6);
    expect(hugeMargin.tanHalfFovY).toBeCloseTo(Math.tan(maxHalfAngle), 6);
    expect(hugeMargin.tanHalfFovX).toBeGreaterThan(0);
    expect(hugeMargin.halfDiagonalRad).toBe(Math.PI);
  });

  it('includes directions on the exact frustum border and rejects behind-camera or perpendicular directions', () => {
    const view = skyViewFromPose(basePose)!;
    const tx = view.tanHalfFovX;
    const ty = view.tanHalfFovY;
    // Center of screen.
    expect(directionInView([0, 0, -1], view)).toBe(true);
    // Exact frustum boundary (|x| === tanHalfFovX * depth, |y| === tanHalfFovY * depth at z = -1).
    expect(directionInView([tx, ty, -1], view)).toBe(true);
    expect(directionInView([-tx, -ty, -1], view)).toBe(true);
    expect(directionInView([tx, 0, -1], view)).toBe(true);
    expect(directionInView([0, -ty, -1], view)).toBe(true);

    // Just outside horizontal or vertical boundary.
    expect(directionInView([tx + 1e-9, 0, -1], view)).toBe(false);
    expect(directionInView([0, ty + 1e-9, -1], view)).toBe(false);

    // Behind camera (z > 0) or at depth === 0 (z === 0).
    expect(directionInView([0, 0, 1], view)).toBe(false);
    expect(directionInView([1, 0, 0], view)).toBe(false);
    expect(directionInView([0, 1, 0], view)).toBe(false);
    expect(directionInView([0, 0, 0], view)).toBe(false);
    expect(directionInView([Number.NaN, 0, -1], view)).toBe(false);
  });
});

describe('skySourceId, HEALPix cone culling, incumbent hysteresis and source ranking', () => {
  it('formats stable source identifiers as releaseId:L<layer>/<pix>#<index>', () => {
    expect(skySourceId('gaia-dr3', 0, 4, 31)).toBe('gaia-dr3:L0/4#31');
    expect(skySourceId('custom', 2, 191, 0)).toBe('custom:L2/191#0');
  });

  it('culls out-of-cone tiles before astro checks and sorts missing or mismatched-count astro tiles', () => {
    // Tile 4 center and antipodal direction.
    const center4 = tileCenter(0, 4);
    const antipode4: Vec3 = [-center4[0], -center4[1], -center4[2]];
    // Narrow 8 deg view looking at center4; find a tile whose bounding cone does not overlap.
    const view = skyViewFromPose({
      cameraWorldQuaternion: lookTowardIcrs(center4),
      icrsWorldQuaternion: IDENTITY_QUAT,
      fovDeg: 8,
      aspect: 1,
      limitingMagnitude: 6.5,
    })!;

    let farPix = -1;
    for (let pix = 0; pix < 12; pix++) {
      const c = tileCenter(0, pix);
      const r = tileBoundingRadius(0, pix);
      if (angleBetweenUnit(center4, c) - r > view.halfDiagonalRad + DEFAULT_INCUMBENT_MARGIN_RAD + 0.1) {
        farPix = pix;
        break;
      }
    }
    expect(farPix).toBeGreaterThanOrEqual(0);
    expect(angleBetweenUnit(center4, antipode4)).toBeCloseTo(Math.PI, 12);

    const missingInViewA = buildTile({ layer: 1, order: 0, pix: 4, withAstro: false }, []);
    const missingInViewB = buildTile(
      { layer: 0, order: 0, pix: 4, astroCountOverride: 0 },
      [{ direction: center4, magnitude: 2, distancePc: 10, teffK: 5800, luminositySolar: 1 }]
    );
    const missingFarAway = buildTile({ layer: 0, order: 0, pix: farPix, withAstro: false }, []);

    const result = skySourcesInView([missingInViewA, missingFarAway, missingInViewB], view, { releaseId: 'rel' });
    expect(result.sources).toEqual([]);
    expect(result.starsInView).toBe(0);
    expect(result.completeInView).toBe(0);
    // Out-of-cone farPix is skipped before astro check; in-cone missing/mismatched tiles are sorted lexicographically.
    expect(result.tilesWithoutAstro).toEqual(['0/4', '1/4']);
  });

  it('enforces strict magnitude < limitingMagnitude and excludes off-screen incumbents from starsInView/completeInView', () => {
    const center4 = tileCenter(0, 4);
    const orientation = lookTowardIcrs(center4);
    const view = skyViewFromPose({
      cameraWorldQuaternion: orientation,
      icrsWorldQuaternion: IDENTITY_QUAT,
      fovDeg: 40,
      aspect: 1,
      limitingMagnitude: 6.5,
    })!;

    const localDir = (u: number, v: number): Vec3 => {
      const x = u * view.tanHalfFovX;
      const y = v * view.tanHalfFovY;
      const z = -1;
      const n = Math.hypot(x, y, z);
      return rotateByQuat([x / n, y / n, z / n], view.orientationIcrs);
    };

    // Index 0: on-screen complete star (mag 4.0 < 6.5).
    // Index 1: on-screen star right at limitingMagnitude (mag 6.5 === 6.5) -> must be excluded even if incumbent.
    // Index 2: complete star just outside screen edge (u = 1.03) within default incumbent margin.
    // Index 3: incomplete star just outside screen edge (u = -1.03) within default incumbent margin.
    const tile = buildTile({ pix: 4 }, [
      { direction: localDir(0, 0), magnitude: 4.0, distancePc: 20, teffK: 6000, luminositySolar: 5 },
      { direction: localDir(0.1, 0), magnitude: 6.5, distancePc: 20, teffK: 6000, luminositySolar: 5 },
      { direction: localDir(1.03, 0), magnitude: 3.0, distancePc: 10, teffK: 7000, luminositySolar: 10 },
      { direction: localDir(-1.03, 0), magnitude: 2.0, distancePc: 10, teffK: Number.NaN, luminositySolar: 10 },
    ]);

    const id0 = skySourceId('rel', 0, 4, 0);
    const id1 = skySourceId('rel', 0, 4, 1);
    const id2 = skySourceId('rel', 0, 4, 2);
    const id3 = skySourceId('rel', 0, 4, 3);

    const withoutIncumbents = skySourcesInView([tile], view, { releaseId: 'rel' });
    expect(withoutIncumbents.sources.map((s) => s.id)).toEqual([id0]);
    expect(withoutIncumbents.starsInView).toBe(1);
    expect(withoutIncumbents.completeInView).toBe(1);

    const withIncumbents = skySourcesInView([tile], view, {
      releaseId: 'rel',
      incumbents: new Set([id1, id2, id3]),
    });
    // id2 (louder complete incumbent) ranks ahead of id0; id3 (incomplete incumbent) follows; id1 at mag 6.5 stays excluded.
    expect(withIncumbents.sources.map((s) => s.id)).toEqual([id2, id0, id3]);
    // starsInView and completeInView count only on-screen stars, not off-screen incumbents.
    expect(withIncumbents.starsInView).toBe(1);
    expect(withIncumbents.completeInView).toBe(1);

    // Zero incumbentMarginRad disables edge hysteresis.
    const zeroMargin = skySourcesInView([tile], view, {
      releaseId: 'rel',
      incumbents: new Set([id2, id3]),
      incumbentMarginRad: 0,
    });
    expect(zeroMargin.sources.map((s) => s.id)).toEqual([id0]);
  });

  it('breaks complete levelDb ties by magnitude then lexicographic id, and clamps/floors maxAudible and maxSilent', () => {
    const center0 = tileCenter(0, 0);
    const view = skyViewFromPose({
      cameraWorldQuaternion: lookTowardIcrs(center0),
      icrsWorldQuaternion: IDENTITY_QUAT,
      fovDeg: 50,
      aspect: 1,
      limitingMagnitude: 8.0,
    })!;

    // Tile layer 1 and layer 0 at pix 0 both hold stars with identical luminositySolar=10, distancePc=20 (same levelDb).
    const tileLayer1 = buildTile({ layer: 1, order: 0, pix: 0 }, [
      // Tie in levelDb AND magnitude=3.0 with tileLayer0 index 0 -> id 'rel:L0/0#0' < 'rel:L1/0#0'.
      { direction: center0, magnitude: 3.0, distancePc: 20, teffK: 5500, luminositySolar: 10 },
      // Same levelDb, fainter magnitude=4.5 -> ranks after magnitude=3.0 stars.
      { direction: center0, magnitude: 4.5, distancePc: 20, teffK: 6500, luminositySolar: 10 },
      // Incomplete star tied at magnitude=2.5 with tileLayer0 index 1 -> id 'rel:L0/0#1' < 'rel:L1/0#2'.
      { direction: center0, magnitude: 2.5, distancePc: Number.POSITIVE_INFINITY, teffK: 5000, luminositySolar: 10 },
    ]);
    const tileLayer0 = buildTile({ layer: 0, order: 0, pix: 0 }, [
      { direction: center0, magnitude: 3.0, distancePc: 20, teffK: 6000, luminositySolar: 10 },
      { direction: center0, magnitude: 2.5, distancePc: 20, teffK: 5000, luminositySolar: Number.NaN },
      // Incomplete because distancePc <= 0 makes receivedLevelDb null even though all three numbers are finite.
      { direction: center0, magnitude: 5.0, distancePc: 0, teffK: 5000, luminositySolar: 10 },
    ]);

    const forward = skySourcesInView([tileLayer1, tileLayer0], view, {
      releaseId: 'rel',
      evidenceClass: undefined,
    });
    const backward = skySourcesInView([tileLayer0, tileLayer1], view, {
      releaseId: 'rel',
      evidenceClass: 'predicted',
    });

    const expectedOrder = [
      'rel:L0/0#0',
      'rel:L1/0#0',
      'rel:L1/0#1',
      'rel:L0/0#1',
      'rel:L1/0#2',
      'rel:L0/0#2',
    ];
    expect(forward.sources.map((s) => s.id)).toEqual(expectedOrder);
    expect(backward.sources.map((s) => s.id)).toEqual(expectedOrder);
    expect(forward.starsInView).toBe(6);
    expect(forward.completeInView).toBe(3);

    for (const s of forward.sources) {
      expect('evidenceClass' in s).toBe(false);
    }
    for (const s of backward.sources) {
      expect(s.evidenceClass).toBe('predicted');
    }
    // Non-finite distancePc maps to null, while finite 0 stays 0 on the source input.
    expect(forward.sources.find((s) => s.id === 'rel:L1/0#2')!.distancePc).toBeNull();
    expect(forward.sources.find((s) => s.id === 'rel:L0/0#2')!.distancePc).toBe(0);

    // Fractional bounds are floored and negative bounds clamp to 0.
    const fractional = skySourcesInView([tileLayer1, tileLayer0], view, {
      releaseId: 'rel',
      maxAudible: 2.9,
      maxSilent: 1.7,
    });
    expect(fractional.sources.map((s) => s.id)).toEqual(['rel:L0/0#0', 'rel:L1/0#0', 'rel:L0/0#1']);

    const negativeBounds = skySourcesInView([tileLayer1, tileLayer0], view, {
      releaseId: 'rel',
      maxAudible: -4,
      maxSilent: -2,
    });
    expect(negativeBounds.sources).toEqual([]);
    expect(negativeBounds.starsInView).toBe(6);
    expect(negativeBounds.completeInView).toBe(3);
  });
});
