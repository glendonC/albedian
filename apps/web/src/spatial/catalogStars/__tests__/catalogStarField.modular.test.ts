import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import fc from 'fast-check';
import * as THREE from 'three';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  CATALOG_STAR_TILE_ENCODING_V1 as ENC,
  CATALOG_STAR_TILE_HEADER_VERSION,
  CatalogStarTileManifestSchema,
  type CatalogStarTileManifest,
} from '@albedian/contracts';
import * as catalogStarFieldModule from '../CatalogStarField.js';
import {
  CatalogStarField,
  DEFAULT_LIMITING_MAGNITUDE,
  ICRS_TO_SCENE_QUATERNION,
  PINNED_STAR_CATALOG_RELEASE,
  STAR_CATALOG_BASE_URL,
  icrsToScene,
} from '../CatalogStarField.js';
import { CatalogStarTileLoader } from '../CatalogStarTileLoader.js';
import { B_MINUS_V_TO_BP_RP, catalogStarTint } from '../catalogStarTint.js';
import { raDecDegToVec, tileBoundingRadius, tileCenter, type Vec3 } from '../healpix.js';
import * as catalogStarsBarrel from '../index.js';
import { TILE_KIND_CORE } from '../tileCodec.js';

const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

const repoFile = (rel: string) => fileURLToPath(new URL(`../../../../../../${rel}`, import.meta.url));
const readBuffer = (rel: string): ArrayBuffer => {
  const bytes = fs.readFileSync(repoFile(rel));
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
};

interface SyntheticStarSpec {
  u?: number;
  v?: number;
  magnitude: number;
  colorIndex: number | null;
  distancePc?: number | null;
  flags?: number;
}

/** Encodes a valid v1 core tile binary buffer matching `decodeCoreTile` in `tileCodec.ts`. */
function encodeCoreTileBuffer(
  header: { layer: number; order: number; pix: number; positionBits?: 16 | 32 },
  stars: SyntheticStarSpec[]
): ArrayBuffer {
  const positionBits = header.positionBits ?? (header.order >= 3 ? 16 : 32);
  const posBytes = positionBits / 8;
  const n = stars.length;
  const byteLength = ENC.headerBytes + n * (2 * posBytes + 7);
  const buffer = new ArrayBuffer(byteLength);
  const headerBytes = new Uint8Array(buffer, 0, 4);
  for (let i = 0; i < 4; i++) headerBytes[i] = ENC.magic.charCodeAt(i);
  const view = new DataView(buffer);
  view.setUint16(4, CATALOG_STAR_TILE_HEADER_VERSION, true);
  view.setUint8(6, TILE_KIND_CORE);
  view.setUint8(7, positionBits);
  view.setUint8(8, header.layer);
  view.setUint8(9, header.order);
  view.setUint32(12, header.pix, true);
  view.setUint32(16, n, true);

  const scale = 2 ** positionBits;
  let offset = ENC.headerBytes;
  const posU = positionBits === 32 ? new Uint32Array(buffer, offset, n) : new Uint16Array(buffer, offset, n);
  offset += n * posBytes;
  const posV = positionBits === 32 ? new Uint32Array(buffer, offset, n) : new Uint16Array(buffer, offset, n);
  offset += n * posBytes;
  const magCodes = new Uint16Array(buffer, offset, n);
  offset += 2 * n;
  const colorCodes = new Int16Array(buffer, offset, n);
  offset += 2 * n;
  const distCodes = new Uint16Array(buffer, offset, n);
  offset += 2 * n;
  const flagCodes = new Uint8Array(buffer, offset, n);

  stars.forEach((star, i) => {
    const u = Math.max(0, Math.min(scale - 1, Math.floor((star.u ?? 0.5) * scale)));
    const v = Math.max(0, Math.min(scale - 1, Math.floor((star.v ?? 0.5) * scale)));
    posU[i] = u;
    posV[i] = v;
    magCodes[i] = Math.round((star.magnitude + ENC.magnitudeOffset) * ENC.magnitudeScale);
    colorCodes[i] =
      star.colorIndex === null || !Number.isFinite(star.colorIndex)
        ? ENC.colorMissing
        : Math.round(star.colorIndex * ENC.colorScale);
    distCodes[i] =
      star.distancePc === undefined || star.distancePc === null || !Number.isFinite(star.distancePc)
        ? ENC.distanceMissing
        : Math.round((Math.log10(star.distancePc) - ENC.distanceLog10Min) / ENC.distanceLog10Step) + 1;
    flagCodes[i] = star.flags ?? 0;
  });

  return buffer;
}

function buildTestManifest(options: {
  magnitudeLimit?: number;
  layer0Counts?: Record<number, number>;
  layer2Pix0Count?: number;
} = {}): CatalogStarTileManifest {
  const zero = (order: number) => new Array<number>(12 * 4 ** order).fill(0);
  const stats = { min: 0, median: 0, max: 0 };
  const l0 = zero(0);
  for (const [pix, count] of Object.entries(options.layer0Counts ?? { 0: 5 })) {
    l0[Number(pix)] = count;
  }
  const l1 = zero(1);
  const l2 = zero(3);
  l2[0] = options.layer2Pix0Count ?? 2;
  const mkLayer = (n: number, magMin: number | null, magMax: number, order: number, counts: number[]) => ({
    layer: n,
    magMin,
    magMax,
    order,
    positionBits: order >= 3 ? 16 : 32,
    stars: counts.reduce((a, b) => a + b, 0),
    nonEmptyTiles: counts.filter((c) => c > 0).length,
    coreBytes: 0,
    detailBytes: 0,
    coreTileBytes: stats,
    detailTileBytes: stats,
    maxPositionErrorArcsec: 0,
    tileStarCounts: counts,
  });
  const totalStars = l0.reduce((a, b) => a + b, 0) + l2.reduce((a, b) => a + b, 0);
  return CatalogStarTileManifestSchema.parse({
    format: 'albedian.catalog-star-tiles',
    formatVersion: 1,
    releaseId: 'modular-fixture',
    title: 'Modular test fixture',
    evidenceKind: 'catalog-derived',
    disclosure: 'Catalog test fixture.',
    frame: 'ICRS',
    epochJulianYear: 2016,
    magnitudeLimit: options.magnitudeLimit ?? 12,
    magnitudeBands: { G: 'Gaia G', Hp: 'Hipparcos Hp' },
    counts: { stars: totalStars, gaiaSources: totalStars, hipparcosStars: 0, namedStars: 1 },
    tiling: {
      scheme: 'healpix',
      ordering: 'nested',
      positionEncoding: 'healpix-face-subpixel',
      tilePath: 'L{layer}/{pix}.{kind}.bin',
    },
    layers: [
      mkLayer(0, null, 6.5, 0, l0),
      mkLayer(1, 6.5, 9.5, 1, l1),
      mkLayer(2, 9.5, options.magnitudeLimit ?? 12, 3, l2),
    ],
    encoding: {
      byteOrder: 'little-endian',
      magic: 'ALBS',
      headerBytes: 32,
      magnitude: { offset: 2, scale: 1000 },
      color: { scale: 1000, missing: -32768 },
      distance: { log10Min: -1, log10Step: 0.0001, missing: 0 },
      distanceBounds: { missing: 0, log10FractionMin: -5, lowerLog10Span: 5, upperLog10Span: 7 },
      flags: {
        distanceKindMask: 3,
        distanceKinds: { '0': 'none', '1': 'bailer-jones-2021-photogeometric' },
        magnitudeBandHp: 4,
        colorBMinusV: 8,
        catalogHipparcos: 16,
        named: 32,
      },
    },
    sources: [
      {
        id: 'gaia-dr3',
        title: 'Gaia DR3',
        provider: 'ESA',
        citation: 'Gaia Collaboration 2023',
        license: 'CC BY-NC 3.0 IGO',
      },
    ],
    selection: {},
    files: { names: 'names.json', brightStarMerge: 'bright-star-merge.json', checksums: 'SHA256SUMS' },
    bake: { script: 'x', command: 'x', verify: 'x' },
  });
}

const TEST_NAMES_DOCUMENT = {
  format: 'albedian.catalog-star-tiles.names',
  releaseId: 'modular-fixture',
  evidenceKind: 'catalog-derived',
  source: 'iau-csn',
  sourceUpdated: '2022-04-04',
  sourceSha256: 'f9bd5c237841e096a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718',
  names: [
    {
      name: 'Achird',
      nameAscii: 'Achird',
      designation: 'HR 219',
      bayer: 'eta Cas',
      constellation: 'Cas',
      component: 'A',
      hip: 3821,
      catalog: 'gaia-dr3',
      catalogId: '33264522507136',
      layer: 2,
      pix: 0,
      index: 1,
      raDeg: 44.97466211975327,
      decDeg: 0.4734652233431856,
      mag: 9.887423,
      magBand: 'G',
    },
  ],
};

function mockFetch(files: Record<string, ArrayBuffer | object>) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const body = files[url];
    if (body === undefined) return new Response(null, { status: 404 });
    return body instanceof ArrayBuffer
      ? new Response(body.slice(0), { status: 200 })
      : new Response(JSON.stringify(body), { status: 200 });
  }) as unknown as typeof fetch;
}

/** Orient a PerspectiveCamera so its local -Z forward vector points along `sceneDir` without gimbal nudges. */
function aimCameraAlongSceneDirection(camera: THREE.PerspectiveCamera, sceneDir: THREE.Vector3): void {
  camera.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, -1), sceneDir.clone().normalize());
  camera.updateMatrixWorld(true);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('CatalogStarField module and barrel export surface', () => {
  it('exports the exact runtime symbol set and matches index.ts barrel re-exports', () => {
    const expectedKeys = [
      'CatalogStarField',
      'DEFAULT_LIMITING_MAGNITUDE',
      'ICRS_TO_SCENE_QUATERNION',
      'PINNED_STAR_CATALOG_RELEASE',
      'STAR_CATALOG_BASE_URL',
      'icrsToScene',
    ];
    expect(Object.keys(catalogStarFieldModule).sort()).toEqual(expectedKeys);
    for (const key of expectedKeys) {
      expect((catalogStarsBarrel as Record<string, unknown>)[key], key).toBe(
        (catalogStarFieldModule as Record<string, unknown>)[key]
      );
    }
  });

  it('pins catalog release, base URL and default naked-eye limiting magnitude constants', () => {
    expect(PINNED_STAR_CATALOG_RELEASE).toBe('gaia-dr3');
    expect(STAR_CATALOG_BASE_URL).toBe('/catalog/stars/gaia-dr3/');
    expect(DEFAULT_LIMITING_MAGNITUDE).toBe(6.5);
  });
});

describe('ICRS to scene coordinate frame conversions', () => {
  it('maps ICRS cardinal axes onto +Y celestial north, -Z RA 0h, -X RA 6h and +X RA 18h', () => {
    expect(ICRS_TO_SCENE_QUATERNION.length()).toBeCloseTo(1, 12);
    const mat = new THREE.Matrix4().makeRotationFromQuaternion(ICRS_TO_SCENE_QUATERNION);
    expect(mat.determinant()).toBeCloseTo(1, 12);

    const check = (icrs: Vec3, expected: [number, number, number]) => {
      const got = icrsToScene(icrs);
      expect(got.x).toBeCloseTo(expected[0], 12);
      expect(got.y).toBeCloseTo(expected[1], 12);
      expect(got.z).toBeCloseTo(expected[2], 12);
    };
    check([1, 0, 0], [0, 0, -1]); // RA 0h, Dec 0
    check([0, 1, 0], [-1, 0, 0]); // RA 6h, Dec 0 (east sits to the left when facing RA 0h)
    check([0, -1, 0], [1, 0, 0]); // RA 18h, Dec 0
    check([0, 0, 1], [0, 1, 0]); // North celestial pole
    check([0, 0, -1], [0, -1, 0]); // South celestial pole
  });

  it('reuses a caller-supplied target Vector3 and allocates a fresh one when omitted', () => {
    const target = new THREE.Vector3(9, 9, 9);
    const returned = icrsToScene([0, 0, 1], target);
    expect(returned).toBe(target);
    expect([target.x, target.y, target.z]).toEqual([0, 1, 0]);

    const fresh = icrsToScene([1, 0, 0]);
    expect(fresh).not.toBe(target);
    expect(fresh.z).toBeCloseTo(-1, 12);
  });

  it('PBT: icrsToScene is a right-handed isometry that round-trips via ICRS_TO_SCENE_QUATERNION.conjugate()', () => {
    const vecArb = fc.tuple(
      fc.double({ min: -10, max: 10, noNaN: true }),
      fc.double({ min: -10, max: 10, noNaN: true }),
      fc.double({ min: -10, max: 10, noNaN: true })
    );
    const inverseQuat = ICRS_TO_SCENE_QUATERNION.clone().conjugate();
    fc.assert(
      fc.property(vecArb, vecArb, (a, b) => {
        const sa = icrsToScene(a);
        const sb = icrsToScene(b);
        expect(sa.length()).toBeCloseTo(Math.hypot(...a), 10);
        expect(sa.dot(sb)).toBeCloseTo(a[0] * b[0] + a[1] * b[1] + a[2] * b[2], 9);

        const crossIcrs: Vec3 = [
          a[1] * b[2] - a[2] * b[1],
          a[2] * b[0] - a[0] * b[2],
          a[0] * b[1] - a[1] * b[0],
        ];
        const crossScene = sa.clone().cross(sb);
        expect(crossScene.distanceTo(icrsToScene(crossIcrs))).toBeLessThan(1e-9);

        const roundTrip = sa.clone().applyQuaternion(inverseQuat);
        expect(roundTrip.x).toBeCloseTo(a[0], 10);
        expect(roundTrip.y).toBeCloseTo(a[1], 10);
        expect(roundTrip.z).toBeCloseTo(a[2], 10);
      }),
      { seed: SEED, numRuns: 200 }
    );
  });

  it('computes sceneDirection from catalog RA/Dec degrees and writes into optional target', () => {
    const field = new CatalogStarField({ fetch: mockFetch({}) });
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const out = new THREE.Vector3();
    const got = field.sceneDirection(90, 0, out);
    expect(got).toBe(out);
    expect(out.distanceTo(new THREE.Vector3(-1, 0, 0))).toBeLessThan(1e-12);
    expect(field.sceneDirection(0, 90).distanceTo(new THREE.Vector3(0, 1, 0))).toBeLessThan(1e-12);
    field.dispose();
    return field.ready.then(() => {
      expect(warn).toHaveBeenCalled();
    });
  });
});

describe('CatalogStarField Three.js material, tile geometry and attribute packing', () => {
  const tile0Stars: SyntheticStarSpec[] = [
    { u: 0.25, v: 0.25, magnitude: 0.4, colorIndex: 0.82, flags: 0 }, // Gaia BP-RP = 0.82, bright spike star
    { u: 0.5, v: 0.5, magnitude: 2.1, colorIndex: 0.82, flags: ENC.flags.colorBMinusV }, // Hipparcos B-V = 0.82 -> BP-RP = 1.0414
    { u: 0.75, v: 0.25, magnitude: 4.8, colorIndex: null, flags: 0 }, // Missing colour -> neutral linear-sRGB
    { u: 0.25, v: 0.75, magnitude: 5.5, colorIndex: -2.0, flags: 0 }, // Clamped to blue LUT floor
    { u: 0.75, v: 0.75, magnitude: 6.1, colorIndex: 4.5, flags: 0 }, // Clamped to red LUT ceiling
  ];

  function linearTintFor(colorIndex: number, isBMinusV: boolean): [number, number, number] {
    const [r, g, b] = catalogStarTint(colorIndex, isBMinusV);
    const c = new THREE.Color().setRGB(r, g, b, THREE.SRGBColorSpace);
    return [c.r, c.g, c.b];
  }

  it('builds ShaderMaterial, shared BufferGeometry, chord boundingSphere and Points across pre- and post-load mounts', async () => {
    const core0 = encodeCoreTileBuffer({ layer: 0, order: 0, pix: 0 }, tile0Stars);
    const fetchImpl = mockFetch({
      '/stars/manifest.json': buildTestManifest({ layer0Counts: { 0: tile0Stars.length } }),
      '/stars/L0/0.core.bin': core0,
    });
    const field = new CatalogStarField({ baseUrl: '/stars', limitingMagnitude: 6.5, fetch: fetchImpl });
    const mountBefore = field.createMount(85);
    expect(mountBefore.name).toBe('catalogStarField');
    expect(mountBefore.quaternion.angleTo(ICRS_TO_SCENE_QUATERNION)).toBeLessThan(1e-12);
    expect([mountBefore.scale.x, mountBefore.scale.y, mountBefore.scale.z]).toEqual([85, 85, 85]);

    await field.ready;
    expect(field.getStatus()).toBe('ready');

    const camera = new THREE.PerspectiveCamera(55, 16 / 9, 0.1, 2000);
    const scene = new THREE.Scene();
    scene.add(mountBefore, camera);
    aimCameraAlongSceneDirection(camera, icrsToScene(tileCenter(0, 0)));
    scene.updateMatrixWorld(true);

    field.update(camera, 2, 1000);
    await vi.waitFor(() => expect(mountBefore.children).toHaveLength(1));

    // A second mount created after tiles are resident receives Points sharing the same geometry and material
    const mountAfter = field.createMount(58);
    expect(mountAfter.scale.x).toBe(58);
    expect(mountAfter.children).toHaveLength(1);

    const pointsA = mountBefore.children[0] as THREE.Points;
    const pointsB = mountAfter.children[0] as THREE.Points;
    expect(pointsA).toBeInstanceOf(THREE.Points);
    expect(pointsA.name).toBe('catalogStarTile');
    expect(pointsB.name).toBe('catalogStarTile');
    expect(pointsB.geometry).toBe(pointsA.geometry);
    expect(pointsB.material).toBe(pointsA.material);

    const mat = pointsA.material as THREE.ShaderMaterial;
    expect(mat).toBeInstanceOf(THREE.ShaderMaterial);
    expect(mat.transparent).toBe(true);
    expect(mat.depthWrite).toBe(false);
    expect(mat.blending).toBe(THREE.AdditiveBlending);
    expect(mat.uniforms.uLimitingMagnitude?.value).toBe(6.5);
    expect(mat.uniforms.uPixelRatio?.value).toBe(2);
    expect(mat.vertexShader).toContain('attribute float aMagnitude;');
    expect(mat.vertexShader).toContain('attribute vec3 aTint;');
    expect(mat.vertexShader).toContain('uLimitingMagnitude');
    expect(mat.vertexShader).toContain('uPixelRatio');
    expect(mat.fragmentShader).toContain('gl_PointCoord');
    expect(mat.fragmentShader).toContain('gl_FragColor = vec4(col, 1.0);');

    const geo = pointsA.geometry;
    expect(geo.boundingSphere).not.toBeNull();
    const expectedCenter = tileCenter(0, 0);
    expect(geo.boundingSphere!.center.distanceTo(new THREE.Vector3(...expectedCenter))).toBeLessThan(1e-12);
    const expectedChord = 2 * Math.sin(Math.min(tileBoundingRadius(0, 0), Math.PI) / 2);
    expect(geo.boundingSphere!.radius).toBeCloseTo(expectedChord, 12);

    const posAttr = geo.getAttribute('position');
    const magAttr = geo.getAttribute('aMagnitude');
    const tintAttr = geo.getAttribute('aTint');
    expect(posAttr.itemSize).toBe(3);
    expect(posAttr.count).toBe(tile0Stars.length);
    expect(magAttr.itemSize).toBe(1);
    expect(magAttr.count).toBe(tile0Stars.length);
    expect(tintAttr.itemSize).toBe(3);
    expect(tintAttr.count).toBe(tile0Stars.length);

    for (let i = 0; i < tile0Stars.length; i++) {
      const p = new THREE.Vector3().fromBufferAttribute(posAttr, i);
      expect(p.length()).toBeCloseTo(1, 5);
      expect(magAttr.getX(i)).toBeCloseTo(tile0Stars[i]!.magnitude, 3);
    }

    // Star 0: Gaia BP-RP = 0.82 in linear-sRGB (distinct from raw sRGB and distinct from B-V = 0.82)
    const tintGaia = [tintAttr.getX(0), tintAttr.getY(0), tintAttr.getZ(0)];
    const wantGaia = linearTintFor(0.82, false);
    const srgbGaia = catalogStarTint(0.82, false);
    expect(tintGaia[0]).toBeCloseTo(wantGaia[0], 2);
    expect(tintGaia[1]).toBeCloseTo(wantGaia[1], 2);
    expect(tintGaia[2]).toBeCloseTo(wantGaia[2], 2);
    expect(Math.abs(tintGaia[2]! - srgbGaia[2])).toBeGreaterThan(0.05);

    // Star 1: Hipparcos B-V = 0.82 scaled by B_MINUS_V_TO_BP_RP (1.27) -> warmer/less blue than BP-RP = 0.82
    const tintHip = [tintAttr.getX(1), tintAttr.getY(1), tintAttr.getZ(1)];
    const wantHip = linearTintFor(0.82 * B_MINUS_V_TO_BP_RP, false);
    expect(tintHip[0]).toBeCloseTo(wantHip[0], 2);
    expect(tintHip[1]).toBeCloseTo(wantHip[1], 2);
    expect(tintHip[2]).toBeCloseTo(wantHip[2], 2);
    expect(tintHip[2]!).toBeLessThan(tintGaia[2]! - 0.05);

    // Star 2: Missing colour (NaN) -> exact neutral linear-sRGB, never NaN
    const tintNeutral = [tintAttr.getX(2), tintAttr.getY(2), tintAttr.getZ(2)];
    const wantNeutral = linearTintFor(Number.NaN, false);
    expect(tintNeutral[0]).toBeCloseTo(wantNeutral[0], 6);
    expect(tintNeutral[1]).toBeCloseTo(wantNeutral[1], 6);
    expect(tintNeutral[2]).toBeCloseTo(wantNeutral[2], 6);

    // Stars 3 & 4: Out-of-range blue and red clamp to LUT endpoints cleanly
    const wantBlue = linearTintFor(-0.6, false);
    const wantRed = linearTintFor(3.0, false);
    expect([tintAttr.getX(3), tintAttr.getY(3), tintAttr.getZ(3)]).toEqual([
      expect.closeTo(wantBlue[0], 5),
      expect.closeTo(wantBlue[1], 5),
      expect.closeTo(wantBlue[2], 5),
    ]);
    expect([tintAttr.getX(4), tintAttr.getY(4), tintAttr.getZ(4)]).toEqual([
      expect.closeTo(wantRed[0], 5),
      expect.closeTo(wantRed[1], 5),
      expect.closeTo(wantRed[2], 5),
    ]);

    field.dispose();
  });
});

describe('CatalogStarField camera view cone sync, floating-origin invariance and throttling', () => {
  it('extracts camera ICRS view cone through visible mount world rotation, ignores floating-origin translation, and throttles updates', async () => {
    const setViewSpy = vi.spyOn(CatalogStarTileLoader.prototype, 'setView');
    const fetchImpl = mockFetch({
      '/stars/manifest.json': buildTestManifest({ layer0Counts: { 0: 1 }, layer2Pix0Count: 2 }),
      '/stars/L0/0.core.bin': encodeCoreTileBuffer({ layer: 0, order: 0, pix: 0 }, [{ magnitude: 3.2, colorIndex: 0.4 }]),
      '/stars/L2/0.core.bin': readBuffer('tests/fixtures/catalog_star_tiles/core.bin'),
    });

    const field = new CatalogStarField({ baseUrl: '/stars', fetch: fetchImpl });
    await field.ready;

    // Two regimes: regime 0 hidden, regime 1 visible at floating-origin offset [420, -180, 750] with a parent rotation
    const scene = new THREE.Scene();
    const hiddenRegime = new THREE.Group();
    hiddenRegime.visible = false;
    hiddenRegime.rotation.set(1.1, -0.8, 0.4);
    const hiddenMount = field.createMount(58);
    hiddenRegime.add(hiddenMount);

    const activeRegime = new THREE.Group();
    activeRegime.position.set(420, -180, 750);
    activeRegime.rotation.set(0.35, -0.6, 0.2);
    const activeMount = field.createMount(85);
    activeMount.position.set(12, 34, -56);
    activeRegime.add(activeMount);

    const cameraFrame = new THREE.Group();
    cameraFrame.position.set(432, -146, 694);
    cameraFrame.rotation.set(-0.25, 0.5, 0.1);
    const camera = new THREE.PerspectiveCamera(50, 16 / 9, 0.05, 2800);
    cameraFrame.add(camera);
    scene.add(hiddenRegime, activeRegime, cameraFrame);
    scene.updateMatrixWorld(true);

    // Centre activeMount on camera in world space (floating-origin sky sphere), then aim at `wantIcrs`
    const cameraWorldPos = camera.getWorldPosition(new THREE.Vector3());
    activeMount.position.copy(activeRegime.worldToLocal(cameraWorldPos.clone()));
    scene.updateMatrixWorld(true);

    const wantIcrs = tileCenter(3, 0);
    const worldStarPoint = activeMount.localToWorld(new THREE.Vector3(...wantIcrs));
    const worldForward = worldStarPoint.sub(cameraWorldPos).normalize();
    const parentWorldQuat = cameraFrame.getWorldQuaternion(new THREE.Quaternion());
    const desiredWorldQuat = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, -1), worldForward);
    camera.quaternion.copy(parentWorldQuat.clone().invert().multiply(desiredWorldQuat));
    scene.updateMatrixWorld(true);

    field.update(camera, 1.5, 1000);
    expect(setViewSpy).toHaveBeenCalledTimes(1);
    const firstView = setViewSpy.mock.calls[0]![0];
    expect(firstView.centerIcrs[0]).toBeCloseTo(wantIcrs[0], 6);
    expect(firstView.centerIcrs[1]).toBeCloseTo(wantIcrs[1], 6);
    expect(firstView.centerIcrs[2]).toBeCloseTo(wantIcrs[2], 6);

    const wantHalfDiag = Math.atan(Math.tan(THREE.MathUtils.degToRad(50) / 2) * Math.hypot(1, 16 / 9));
    expect(firstView.radiusRad).toBeCloseTo(wantHalfDiag + 0.08, 10);
    expect(firstView.limitingMagnitude).toBe(6.5);

    // Calling again with identical view after 500 ms does not re-dispatch
    field.update(camera, 1.5, 1500);
    expect(setViewSpy).toHaveBeenCalledTimes(1);

    // Sub-epsilon rotation (0.004 rad < 0.01 rad) does not re-dispatch
    camera.rotateY(0.004);
    camera.updateMatrixWorld(true);
    field.update(camera, 1.5, 2000);
    expect(setViewSpy).toHaveBeenCalledTimes(1);

    // Significant rotation (0.2 rad > 0.01 rad) within < 120 ms of lastView.at (1000 + 80 = 1080) is throttled
    camera.rotateY(0.2);
    camera.updateMatrixWorld(true);
    field.update(camera, 1.5, 1080);
    expect(setViewSpy).toHaveBeenCalledTimes(1);

    // Once >= 120 ms has elapsed since 1000 (at 1125 ms), the pending view change dispatches
    field.update(camera, 1.5, 1125);
    expect(setViewSpy).toHaveBeenCalledTimes(2);

    // setLimitingMagnitude clamps to [-2, manifest.magnitudeLimit] and resets throttle immediately
    field.setLimitingMagnitude(18);
    expect(field.getLimitingMagnitude()).toBe(12);
    field.update(camera, 1.5, 1125);
    expect(setViewSpy).toHaveBeenCalledTimes(3);
    expect(setViewSpy.mock.calls[2]![0].limitingMagnitude).toBe(12);

    field.setLimitingMagnitude(-9);
    expect(field.getLimitingMagnitude()).toBe(-2);

    field.dispose();
  });

  it('falls back to ICRS_TO_SCENE_QUATERNION inverse when update runs before any mount is created', async () => {
    const setViewSpy = vi.spyOn(CatalogStarTileLoader.prototype, 'setView');
    const field = new CatalogStarField({
      baseUrl: '/stars',
      fetch: mockFetch({ '/stars/manifest.json': buildTestManifest({ layer0Counts: {} }) }),
    });
    await field.ready;

    const camera = new THREE.PerspectiveCamera(60, 1, 0.1, 1000);
    const icrsDir: Vec3 = [0, 0, 1];
    aimCameraAlongSceneDirection(camera, icrsToScene(icrsDir));
    field.update(camera, 1, 500);
    expect(setViewSpy).toHaveBeenCalledTimes(1);
    const [cx, cy, cz] = setViewSpy.mock.calls[0]![0].centerIcrs;
    expect(cx).toBeCloseTo(0, 6);
    expect(cy).toBeCloseTo(0, 6);
    expect(cz).toBeCloseTo(1, 6);

    field.dispose();
  });
});

describe('CatalogStarField lifecycle, stats, inspection, eviction and disposal', () => {
  it('reports loading then ready stats, clamps initial limitingMagnitude to manifest ceiling, and resolves names and star records', async () => {
    let resolveManifest!: (res: Response) => void;
    const manifestGate = new Promise<Response>((resolve) => {
      resolveManifest = resolve;
    });
    const baseFiles: Record<string, ArrayBuffer | object> = {
      '/stars/names.json': TEST_NAMES_DOCUMENT,
      '/stars/L2/0.core.bin': readBuffer('tests/fixtures/catalog_star_tiles/core.bin'),
      '/stars/L2/0.detail.bin': readBuffer('tests/fixtures/catalog_star_tiles/detail.bin'),
    };
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/stars/manifest.json') return manifestGate;
      const body = baseFiles[url];
      if (body === undefined) return new Response(null, { status: 404 });
      return body instanceof ArrayBuffer
        ? new Response(body.slice(0), { status: 200 })
        : new Response(JSON.stringify(body), { status: 200 });
    }) as unknown as typeof fetch;

    const field = new CatalogStarField({ baseUrl: '/stars', limitingMagnitude: 14, fetch: fetchImpl });
    expect(field.getStatus()).toBe('loading');
    expect(field.getManifest()).toBeNull();
    expect(field.getLimitingMagnitude()).toBe(14);
    expect(field.getStats()).toEqual({ status: 'loading', limitingMagnitude: 14 });

    // Updating while loading updates pixelRatio safely without throwing
    const mount = field.createMount(80);
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 1000);
    aimCameraAlongSceneDirection(camera, icrsToScene(tileCenter(3, 0)));
    field.update(camera, 2.5, 100);

    resolveManifest(new Response(JSON.stringify(buildTestManifest({ magnitudeLimit: 11.5, layer0Counts: {} })), { status: 200 }));
    await field.ready;

    expect(field.getStatus()).toBe('ready');
    expect(field.getManifest()?.releaseId).toBe('modular-fixture');
    expect(field.getLimitingMagnitude()).toBe(11.5);

    field.update(camera, 2.5, 300);
    await vi.waitFor(() => expect(field.getStats().tilesLoaded).toBe(1));
    expect(field.getStats()).toMatchObject({
      releaseId: 'modular-fixture',
      status: 'ready',
      limitingMagnitude: 11.5,
      tilesLoaded: 1,
      starsResident: 2,
    });
    expect((mount.children[0] as THREE.Points).material).toBeInstanceOf(THREE.ShaderMaterial);
    expect(((mount.children[0] as THREE.Points).material as THREE.ShaderMaterial).uniforms.uLimitingMagnitude?.value).toBe(
      11.5
    );

    const names = await field.getNamedStars();
    expect(names).toHaveLength(1);
    expect(names[0]?.nameAscii).toBe('Achird');

    const record = await field.getStarRecord(2, 0, 1);
    expect(record).toMatchObject({
      evidenceKind: 'catalog-derived',
      catalog: 'gaia-dr3',
      catalogId: '33264522507136',
      designation: 'Gaia DR3 33264522507136',
    });
    expect(record?.names.map((n) => n.nameAscii)).toEqual(['Achird']);

    field.dispose();
  });

  it('transitions to unavailable without fallback stars when manifest loading fails', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const field = new CatalogStarField({ baseUrl: '/missing', fetch: mockFetch({}) });
    const mount = field.createMount(70);
    await field.ready;

    expect(field.getStatus()).toBe('unavailable');
    expect(field.getManifest()).toBeNull();
    expect(field.getStats()).toEqual({ status: 'unavailable', limitingMagnitude: DEFAULT_LIMITING_MAGNITUDE });
    expect(mount.children).toHaveLength(0);
    expect(await field.getNamedStars()).toEqual([]);
    expect(await field.getStarRecord(0, 0, 0)).toBeNull();
    expect(warn).toHaveBeenCalledTimes(1);

    field.dispose();
  });

  it('disposes evicted tile BufferGeometry and detaches Points from all mounts while keeping active tiles and material', async () => {
    const realOpen = CatalogStarTileLoader.open.bind(CatalogStarTileLoader);
    vi.spyOn(CatalogStarTileLoader, 'open').mockImplementation((baseUrl, opts) =>
      realOpen(baseUrl, { ...opts, maxResidentStars: 2 })
    );

    const core0 = encodeCoreTileBuffer({ layer: 0, order: 0, pix: 0 }, [
      { u: 0.4, v: 0.4, magnitude: 2.0, colorIndex: 0.2 },
      { u: 0.6, v: 0.6, magnitude: 3.0, colorIndex: 0.5 },
    ]);
    const core10 = encodeCoreTileBuffer({ layer: 0, order: 0, pix: 10 }, [
      { u: 0.4, v: 0.4, magnitude: 2.5, colorIndex: 0.3 },
      { u: 0.6, v: 0.6, magnitude: 3.5, colorIndex: 0.7 },
    ]);
    const fetchImpl = mockFetch({
      '/stars/manifest.json': buildTestManifest({ layer0Counts: { 0: 2, 10: 2 }, layer2Pix0Count: 0 }),
      '/stars/L0/0.core.bin': core0,
      '/stars/L0/10.core.bin': core10,
    });

    const field = new CatalogStarField({ baseUrl: '/stars', fetch: fetchImpl });
    const mountA = field.createMount(85);
    const mountB = field.createMount(58);
    await field.ready;

    const camera = new THREE.PerspectiveCamera(20, 1, 0.1, 1000);
    aimCameraAlongSceneDirection(camera, icrsToScene(tileCenter(0, 0)));
    field.update(camera, 1, 1000);
    await vi.waitFor(() => expect(mountA.children).toHaveLength(1));
    expect(mountB.children).toHaveLength(1);

    const tile0Points = mountA.children[0] as THREE.Points;
    const tile0Geo = tile0Points.geometry;
    const sharedMat = tile0Points.material as THREE.ShaderMaterial;
    const tile0GeoDispose = vi.spyOn(tile0Geo, 'dispose');
    const matDispose = vi.spyOn(sharedMat, 'dispose');

    // Swing camera to antipodal tile 0/10 so resident stars (4) exceed maxResidentStars (2) and tile 0/0 is evicted
    aimCameraAlongSceneDirection(camera, icrsToScene(tileCenter(0, 10)));
    field.update(camera, 1, 2000);
    await vi.waitFor(() => expect(tile0GeoDispose).toHaveBeenCalledTimes(1));

    expect(matDispose).not.toHaveBeenCalled();
    expect(mountA.children).toHaveLength(1);
    expect(mountB.children).toHaveLength(1);
    const tile10Geo = (mountA.children[0] as THREE.Points).geometry;
    expect(tile10Geo).not.toBe(tile0Geo);
    const tile10GeoDispose = vi.spyOn(tile10Geo, 'dispose');

    // Full dispose cleans up remaining tile geometries, detaches all Points, and disposes ShaderMaterial
    field.dispose();
    expect(mountA.children).toHaveLength(0);
    expect(mountB.children).toHaveLength(0);
    expect(tile10GeoDispose).toHaveBeenCalledTimes(1);
    expect(matDispose).toHaveBeenCalledTimes(1);
  });

  it('handles pre-ready setLimitingMagnitude clamping, pre-ready dispose, and tile fetch failures cleanly', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

    // 1. Pre-ready setLimitingMagnitude clamps to default ceiling 12, and pre-ready dispose ignores late manifest
    let resolveLateManifest!: (res: Response) => void;
    const lateGate = new Promise<Response>((resolve) => {
      resolveLateManifest = resolve;
    });
    const earlyDisposed = new CatalogStarField({
      baseUrl: '/late',
      fetch: (async () => lateGate) as unknown as typeof fetch,
    });
    earlyDisposed.setLimitingMagnitude(19);
    expect(earlyDisposed.getLimitingMagnitude()).toBe(12);
    earlyDisposed.dispose();
    resolveLateManifest(new Response(JSON.stringify(buildTestManifest()), { status: 200 }));
    await earlyDisposed.ready;
    expect(earlyDisposed.getStatus()).toBe('loading');
    expect(earlyDisposed.getManifest()).toBeNull();

    // 2. Tile 500 error logs warning and increments failures while keeping mount empty
    const brokenTileFetch = mockFetch({
      '/stars/manifest.json': buildTestManifest({ layer0Counts: { 0: 2 }, layer2Pix0Count: 0 }),
    });
    const field = new CatalogStarField({ baseUrl: '/stars', fetch: brokenTileFetch });
    const mount = field.createMount(80);
    await field.ready;

    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 1000);
    aimCameraAlongSceneDirection(camera, icrsToScene(tileCenter(0, 0)));
    field.update(camera, 1, 1000);
    await vi.waitFor(() => expect(field.getStats().failures).toBe(1));
    expect(mount.children).toHaveLength(0);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('tile 0/0 failed'), expect.any(Error));

    field.dispose();
  });
});
