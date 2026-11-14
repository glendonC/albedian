import { describe, expect, it, vi, afterEach } from 'vitest';
import fc from 'fast-check';
import {
  BufferAttribute,
  Group,
  InstancedBufferAttribute,
  InstancedBufferGeometry,
  MathUtils,
  Mesh,
  NodeMaterial,
  PerspectiveCamera,
  Quaternion,
  Vector3,
} from 'three/webgpu';
import { setCurrentStack, stack } from 'three/tsl';
import {
  CATALOG_STAR_TILE_ENCODING_V1,
  CATALOG_STAR_TILE_HEADER_VERSION,
  CatalogStarTileManifestSchema,
  type CatalogStarTileManifest,
} from '@albedian/contracts';
import {
  DEFAULT_LIMITING_MAGNITUDE,
  ICRS_TO_SCENE_QUATERNION,
  STAR_CATALOG_BASE_URL,
  icrsToScene,
} from '../../catalogStars/CatalogStarField.js';
import { CatalogStarTileLoader } from '../../catalogStars/CatalogStarTileLoader.js';
import { tileBoundingRadius, tileCenter } from '../../catalogStars/healpix.js';
import { TILE_KIND_CORE, decodeCoreTile, type CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';
import { fluxFromMagnitude, linearSrgbLuminance } from '../stars/blackbody.js';
import { PSF_STAR_ATTRIBUTES } from '../stars/psfStarMaterial.js';
import { StarColourTable } from '../stars/starColor.js';
import * as spriteFieldModule from '../stars/CatalogStarSpriteField.js';
import {
  CatalogStarSpriteField,
  DEFAULT_CATALOG_STAR_EXPOSURE,
  tileRadiance,
} from '../stars/CatalogStarSpriteField.js';

const ENC = CATALOG_STAR_TILE_ENCODING_V1;

interface SyntheticStarSpec {
  u?: number;
  v?: number;
  magnitude: number;
  colorIndex: number;
  flags?: number;
}

function encodeTestCoreTile(
  spec: { layer: number; order: number; pix: number; positionBits?: 16 | 32 },
  stars: SyntheticStarSpec[]
): ArrayBuffer {
  const positionBits = spec.positionBits ?? (spec.order >= 3 ? 16 : 32);
  const posBytes = positionBits / 8;
  const n = stars.length;
  const totalBytes = ENC.headerBytes + n * (2 * posBytes + 7);
  const buffer = new ArrayBuffer(totalBytes);
  const headerBytes = new Uint8Array(buffer, 0, 4);
  for (let i = 0; i < 4; i++) headerBytes[i] = ENC.magic.charCodeAt(i);
  const headerView = new DataView(buffer);
  headerView.setUint16(4, CATALOG_STAR_TILE_HEADER_VERSION, true);
  headerView.setUint8(6, TILE_KIND_CORE);
  headerView.setUint8(7, positionBits);
  headerView.setUint8(8, spec.layer);
  headerView.setUint8(9, spec.order);
  headerView.setUint32(12, spec.pix, true);
  headerView.setUint32(16, n, true);

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
  const distanceCodes = new Uint16Array(buffer, offset, n);
  offset += 2 * n;
  const flags = new Uint8Array(buffer, offset, n);

  stars.forEach((star, i) => {
    const uFrac = star.u ?? 0.5;
    const vFrac = star.v ?? 0.5;
    posU[i] = Math.min(scale - 1, Math.max(0, Math.floor(uFrac * scale)));
    posV[i] = Math.min(scale - 1, Math.max(0, Math.floor(vFrac * scale)));
    magCodes[i] = Math.round((star.magnitude + ENC.magnitudeOffset) * ENC.magnitudeScale);
    colorCodes[i] = Number.isFinite(star.colorIndex)
      ? Math.round(star.colorIndex * ENC.colorScale)
      : ENC.colorMissing;
    distanceCodes[i] = ENC.distanceMissing;
    flags[i] = star.flags ?? 0;
  });

  return buffer;
}

function makeManifest(options: {
  magnitudeLimit?: number;
  layer0Counts?: Record<number, number>;
  layer2Counts?: Record<number, number>;
} = {}): CatalogStarTileManifest {
  const zero = (order: number) => new Array<number>(12 * 4 ** order).fill(0);
  const stats = { min: 0, median: 0, max: 0 };
  const buildLayer = (
    layerIndex: number,
    magMin: number | null,
    magMax: number,
    order: number,
    populated: Record<number, number>
  ) => {
    const counts = zero(order);
    for (const [pix, starCount] of Object.entries(populated)) {
      counts[Number(pix)] = starCount;
    }
    return {
      layer: layerIndex,
      magMin,
      magMax,
      order,
      positionBits: (order >= 3 ? 16 : 32) as 16 | 32,
      stars: counts.reduce((a, b) => a + b, 0),
      nonEmptyTiles: counts.filter((c) => c > 0).length,
      coreBytes: 0,
      detailBytes: 0,
      coreTileBytes: stats,
      detailTileBytes: stats,
      maxPositionErrorArcsec: 0,
      tileStarCounts: counts,
    };
  };

  const l0 = options.layer0Counts ?? { 4: 2 };
  const l2 = options.layer2Counts ?? { 0: 3 };
  const totalStars =
    Object.values(l0).reduce((a, b) => a + b, 0) + Object.values(l2).reduce((a, b) => a + b, 0);

  return CatalogStarTileManifestSchema.parse({
    format: 'albedian.catalog-star-tiles',
    formatVersion: 1,
    releaseId: 'sprite-field-test',
    title: 'Sprite field test catalog',
    evidenceKind: 'catalog-derived',
    disclosure: 'Synthetic catalog fixture for characterization tests.',
    frame: 'ICRS',
    epochJulianYear: 2016,
    magnitudeLimit: options.magnitudeLimit ?? 12,
    magnitudeBands: { G: 'Gaia G', Hp: 'Hipparcos Hp' },
    counts: { stars: totalStars, gaiaSources: totalStars, hipparcosStars: 0, namedStars: 0 },
    tiling: {
      scheme: 'healpix',
      ordering: 'nested',
      positionEncoding: 'healpix-face-subpixel',
      tilePath: 'L{layer}/{pix}.{kind}.bin',
    },
    layers: [
      buildLayer(0, null, 6.5, 0, l0),
      buildLayer(1, 6.5, 9.5, 1, {}),
      buildLayer(2, 9.5, options.magnitudeLimit ?? 12, 3, l2),
    ],
    encoding: {
      byteOrder: 'little-endian',
      magic: ENC.magic,
      headerBytes: ENC.headerBytes,
      magnitude: { offset: ENC.magnitudeOffset, scale: ENC.magnitudeScale },
      color: { scale: ENC.colorScale, missing: ENC.colorMissing },
      distance: {
        log10Min: ENC.distanceLog10Min,
        log10Step: ENC.distanceLog10Step,
        missing: ENC.distanceMissing,
      },
      distanceBounds: {
        missing: ENC.boundMissing,
        log10FractionMin: ENC.boundLog10FractionMin,
        lowerLog10Span: ENC.boundLowerLog10Span,
        upperLog10Span: ENC.boundUpperLog10Span,
      },
      flags: {
        distanceKindMask: ENC.flags.distanceKindMask,
        distanceKinds: { '0': 'none', '1': 'bailer-jones-2021-photogeometric' },
        magnitudeBandHp: ENC.flags.magnitudeBandHp,
        colorBMinusV: ENC.flags.colorBMinusV,
        catalogHipparcos: ENC.flags.catalogHipparcos,
        named: ENC.flags.named,
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
    bake: { script: 'bake', command: 'bake', verify: 'verify' },
  });
}

function buildStaticFetch(files: Record<string, ArrayBuffer | object | number>): typeof fetch {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const entry = files[url];
    if (entry === undefined) return new Response(null, { status: 404 });
    if (typeof entry === 'number') return new Response(null, { status: entry });
    if (entry instanceof ArrayBuffer) return new Response(entry.slice(0), { status: 200 });
    return new Response(JSON.stringify(entry), { status: 200 });
  }) as unknown as typeof fetch;
}

function makeCameraAimedAtSceneDir(
  sceneDir: Vector3,
  fovDeg = 56,
  aspect = 16 / 9
): PerspectiveCamera {
  const camera = new PerspectiveCamera(fovDeg, aspect, 0.05, 2800);
  camera.position.set(0, 0, 0);
  camera.lookAt(sceneDir);
  camera.updateMatrixWorld(true);
  camera.updateProjectionMatrix();
  return camera;
}

interface ExtractedPsfUniforms {
  limitingMagnitude: { value: number };
  exposure: { value: number };
  psfScale: { value: number };
}

function extractMaterialUniforms(material: NodeMaterial): ExtractedPsfUniforms {
  const vertexCall = material.vertexNode as unknown as {
    shaderNode: { jsFunc: (inputs: unknown[]) => { traverse: (cb: (node: unknown) => void) => void } };
  };
  const uniforms: { value: number }[] = [];
  const seen = new Set<unknown>();
  const collect = (node: unknown) => {
    const candidate = node as { isUniformNode?: boolean; value?: unknown };
    if (candidate.isUniformNode && typeof candidate.value === 'number' && !seen.has(candidate)) {
      seen.add(candidate);
      uniforms.push(candidate as { value: number });
    }
  };
  const activeStack = stack();
  setCurrentStack(activeStack);
  try {
    const ast = vertexCall.shaderNode.jsFunc([]);
    activeStack.traverse(collect);
    ast.traverse(collect);
  } finally {
    setCurrentStack(null);
  }
  if (uniforms.length !== 3) {
    throw new Error(`Expected 3 scalar uniforms in PSF vertex node, found ${uniforms.length}`);
  }
  return {
    limitingMagnitude: uniforms[0]!,
    exposure: uniforms[1]!,
    psfScale: uniforms[2]!,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('CatalogStarSpriteField module export surface', () => {
  it('exposes exact runtime export keys and calibrated exposure constant', () => {
    expect(Object.keys(spriteFieldModule).sort()).toEqual([
      'CatalogStarSpriteField',
      'DEFAULT_CATALOG_STAR_EXPOSURE',
      'tileRadiance',
    ]);
    expect(DEFAULT_CATALOG_STAR_EXPOSURE).toBe(650);
    expect(typeof CatalogStarSpriteField).toBe('function');
    expect(typeof tileRadiance).toBe('function');
  });
});

describe('tileRadiance', () => {
  it('returns an empty Float32Array for an empty tile', () => {
    const emptyTile: CatalogStarCoreTile = {
      layer: 0,
      order: 0,
      pix: 0,
      count: 0,
      positionBits: 32,
      directions: new Float32Array(0),
      magnitude: new Float32Array(0),
      colorIndex: new Float32Array(0),
      distancePc: new Float32Array(0),
      flags: new Uint8Array(0),
      byteLength: 32,
    };
    const out = tileRadiance(emptyTile, new StarColourTable());
    expect(out).toBeInstanceOf(Float32Array);
    expect(out.length).toBe(0);
  });

  it('distinguishes Gaia BP-RP (flags = 0) from Hipparcos B-V (colorBMinusV flag) at identical numeric colour index', () => {
    const colours = new StarColourTable();
    const hipFlag = ENC.flags.colorBMinusV | ENC.flags.catalogHipparcos;
    const tile: CatalogStarCoreTile = {
      layer: 0,
      order: 0,
      pix: 4,
      count: 2,
      positionBits: 32,
      directions: new Float32Array([1, 0, 0, 0, 1, 0]),
      magnitude: new Float32Array([1.5, 1.5]),
      colorIndex: new Float32Array([1.25, 1.25]),
      distancePc: new Float32Array([10, 10]),
      flags: new Uint8Array([0, hipFlag]),
      byteLength: 0,
    };

    const radiance = tileRadiance(tile, colours);
    const flux = fluxFromMagnitude(1.5);
    const expectedGaia = new Float32Array(3);
    const expectedHip = new Float32Array(3);
    colours.sampleInto(1.25, false, expectedGaia, 0);
    colours.sampleInto(1.25, true, expectedHip, 0);

    for (let c = 0; c < 3; c++) {
      expect(radiance[c]).toBeCloseTo(expectedGaia[c]! * flux, 6);
      expect(radiance[3 + c]).toBeCloseTo(expectedHip[c]! * flux, 6);
    }
    // B-V = 1.25 corresponds to BP-RP ~ 1.50 (cooler/redder than BP-RP = 1.25)
    expect(radiance[3]! / radiance[5]!).toBeGreaterThan(radiance[0]! / radiance[2]! + 0.25);
  });

  it('falls back to neutral white for NaN or missing colour indices and handles Infinity or faint magnitudes without NaN', () => {
    const colours = new StarColourTable();
    const tile: CatalogStarCoreTile = {
      layer: 2,
      order: 3,
      pix: 0,
      count: 4,
      positionBits: 16,
      directions: new Float32Array(12),
      // Intentionally shorter arrays for index 3 to exercise undefined fallback paths
      magnitude: new Float32Array([2.0, Number.POSITIVE_INFINITY, 12.0]),
      colorIndex: new Float32Array([Number.NaN, Number.NaN, 0.5]),
      distancePc: new Float32Array(4),
      flags: new Uint8Array([0, ENC.flags.colorBMinusV, 0]),
      byteLength: 0,
    };

    const radiance = tileRadiance(tile, colours);
    const fluxMag2 = fluxFromMagnitude(2.0);
    expect(radiance[0]).toBeCloseTo(fluxMag2, 6);
    expect(radiance[1]).toBeCloseTo(fluxMag2, 6);
    expect(radiance[2]).toBeCloseTo(fluxMag2, 6);

    // Infinity magnitude yields exact zero radiance with no NaN
    expect([...radiance.slice(3, 6)]).toEqual([0, 0, 0]);

    // Faint magnitude 12 scales unit-luminance colour by 10^(-4.8)
    const fluxMag12 = fluxFromMagnitude(12.0);
    expect(linearSrgbLuminance(radiance[6]!, radiance[7]!, radiance[8]!)).toBeCloseTo(fluxMag12, 8);
    expect(radiance[6]).toBeGreaterThan(0);

    // Out-of-bounds index 3 defaults colourIndex -> NaN (white) and magnitude -> +Infinity (0 flux)
    expect([...radiance.slice(9, 12)]).toEqual([0, 0, 0]);

    // Isolated undefined colorIndex on a finite-magnitude star falls back to neutral white (not BP-RP = 0)
    const missingColorTile: CatalogStarCoreTile = {
      layer: 0,
      order: 0,
      pix: 0,
      count: 1,
      positionBits: 32,
      directions: new Float32Array([1, 0, 0]),
      magnitude: new Float32Array([1.0]),
      colorIndex: new Float32Array(0),
      distancePc: new Float32Array(1),
      flags: new Uint8Array([0]),
      byteLength: 0,
    };
    const missingColorRad = tileRadiance(missingColorTile, colours);
    const fluxMag1 = fluxFromMagnitude(1.0);
    expect(missingColorRad[0]).toBeCloseTo(fluxMag1, 6);
    expect(missingColorRad[1]).toBeCloseTo(fluxMag1, 6);
    expect(missingColorRad[2]).toBeCloseTo(fluxMag1, 6);

    // Isolated undefined flags on a finite-magnitude coloured star falls back to Gaia BP-RP (flags = 0), not Hipparcos B-V
    const missingFlagsTile: CatalogStarCoreTile = {
      layer: 0,
      order: 0,
      pix: 0,
      count: 1,
      positionBits: 32,
      directions: new Float32Array([1, 0, 0]),
      magnitude: new Float32Array([1.0]),
      colorIndex: new Float32Array([1.25]),
      distancePc: new Float32Array(1),
      flags: new Uint8Array(0),
      byteLength: 0,
    };
    const missingFlagsRad = tileRadiance(missingFlagsTile, colours);
    const explicitGaiaSample = new Float32Array(3);
    const explicitHipSample = new Float32Array(3);
    colours.sampleInto(1.25, false, explicitGaiaSample, 0);
    colours.sampleInto(1.25, true, explicitHipSample, 0);
    for (let c = 0; c < 3; c++) {
      expect(missingFlagsRad[c]).toBeCloseTo(explicitGaiaSample[c]! * fluxMag1, 6);
    }
    expect(Math.abs(missingFlagsRad[0]! - explicitHipSample[0]! * fluxMag1)).toBeGreaterThan(1e-3);
  });

  it('PBT: preserves linear-sRGB luminance = 10^(-0.4 * mag) and exact 5-magnitude factor-of-100 ratio', () => {
    const colours = new StarColourTable();
    const seedEnv = Number(process.env.ALBEDIAN_FC_SEED);
    fc.assert(
      fc.property(
        fc.double({ min: -2, max: 9, noNaN: true }),
        fc.double({ min: -1.2, max: 4.3, noNaN: true }),
        fc.boolean(),
        (mag, colourIdx, isHip) => {
          const flags = isHip ? ENC.flags.colorBMinusV : 0;
          const tile: CatalogStarCoreTile = {
            layer: 0,
            order: 0,
            pix: 0,
            count: 2,
            positionBits: 32,
            directions: new Float32Array([1, 0, 0, 0, 1, 0]),
            magnitude: new Float32Array([mag, mag + 5]),
            colorIndex: new Float32Array([colourIdx, colourIdx]),
            distancePc: new Float32Array([Number.NaN, Number.NaN]),
            flags: new Uint8Array([flags, flags]),
            byteLength: 0,
          };
          const rad = tileRadiance(tile, colours);
          const lumBright = linearSrgbLuminance(rad[0]!, rad[1]!, rad[2]!);
          const wantBright = fluxFromMagnitude(mag);
          expect(Math.abs(lumBright - wantBright) / wantBright).toBeLessThan(2e-2);
          for (let c = 0; c < 3; c++) {
            if (rad[3 + c]! > 0) {
              expect(rad[c]! / rad[3 + c]!).toBeCloseTo(100, 3);
            }
          }
        }
      ),
      {
        numRuns: 120,
        ...(Number.isFinite(seedEnv) && seedEnv !== 0 ? { seed: seedEnv } : {}),
        examples: [
          [0.0, 0.82, false],
          [1.5, 1.25, true],
        ],
      }
    );
  });
});

describe('CatalogStarSpriteField instanced tile geometry and multi-mount sharing', () => {
  it('builds shared quad buffers, instanced PSF attributes, widened HEALPix boundingSphere, renderOrder -10, and attaches across pre/post mounts', async () => {
    const manifest = makeManifest({
      layer0Counts: { 4: 2 },
      layer2Counts: { 0: 3 },
    });
    const tileL0P4 = encodeTestCoreTile({ layer: 0, order: 0, pix: 4 }, [
      { u: 0.25, v: 0.5, magnitude: 0.5, colorIndex: 0.4, flags: ENC.flags.colorBMinusV },
      { u: 0.75, v: 0.5, magnitude: 4.0, colorIndex: Number.NaN, flags: 0 },
    ]);
    const tileL2P0 = encodeTestCoreTile({ layer: 2, order: 3, pix: 0 }, [
      { u: 0.2, v: 0.3, magnitude: 10.2, colorIndex: 0.85, flags: 0 },
      { u: 0.5, v: 0.5, magnitude: 11.1, colorIndex: 1.4, flags: 0 },
      { u: 0.8, v: 0.7, magnitude: 11.8, colorIndex: -0.1, flags: 0 },
    ]);

    const fetchImpl = buildStaticFetch({
      '/stars/manifest.json': manifest,
      '/stars/L0/4.core.bin': tileL0P4,
      '/stars/L2/0.core.bin': tileL2P0,
    });

    const field = new CatalogStarSpriteField({
      baseUrl: '/stars',
      limitingMagnitude: 6.5,
      exposure: 800,
      fetch: fetchImpl,
    });
    const mountBefore = field.createMount(2400);
    expect(mountBefore.name).toBe('catalogStarSprites');
    expect(mountBefore.quaternion.angleTo(ICRS_TO_SCENE_QUATERNION)).toBeLessThan(1e-12);
    expect([mountBefore.scale.x, mountBefore.scale.y, mountBefore.scale.z]).toEqual([2400, 2400, 2400]);

    await field.ready;
    expect(field.getStatus()).toBe('ready');

    // Aim at HEALPix order 0 pix 4 center ([1, 0, 0] in ICRS) at mag 6.5 -> loads L0/4 only
    const camToL0P4 = makeCameraAimedAtSceneDir(icrsToScene(tileCenter(0, 4)));
    field.update(camToL0P4, 1080, 1000);
    await vi.waitFor(() => expect(field.residentStars()).toBe(2));
    expect(mountBefore.children).toHaveLength(1);

    const meshBefore0 = mountBefore.children[0] as Mesh;
    expect(meshBefore0).toBeInstanceOf(Mesh);
    expect(meshBefore0.name).toBe('catalogStarTile');
    expect(meshBefore0.renderOrder).toBe(-10);

    const geo0 = meshBefore0.geometry as InstancedBufferGeometry;
    expect(geo0).toBeInstanceOf(InstancedBufferGeometry);
    expect(geo0.instanceCount).toBe(2);

    const posAttr0 = geo0.getAttribute('position') as BufferAttribute;
    expect(posAttr0).toBeInstanceOf(BufferAttribute);
    expect(posAttr0).not.toBeInstanceOf(InstancedBufferAttribute);
    expect(posAttr0.itemSize).toBe(3);
    expect([...(posAttr0.array as Float32Array)]).toEqual([-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0]);

    const indexAttr0 = geo0.getIndex()!;
    expect(indexAttr0.itemSize).toBe(1);
    expect([...(indexAttr0.array as Uint16Array)]).toEqual([0, 1, 2, 0, 2, 3]);

    const dirAttr0 = geo0.getAttribute(PSF_STAR_ATTRIBUTES.direction);
    const radAttr0 = geo0.getAttribute(PSF_STAR_ATTRIBUTES.radiance);
    const magAttr0 = geo0.getAttribute(PSF_STAR_ATTRIBUTES.magnitude);
    expect(dirAttr0).toBeInstanceOf(InstancedBufferAttribute);
    expect(dirAttr0.itemSize).toBe(3);
    expect(radAttr0).toBeInstanceOf(InstancedBufferAttribute);
    expect(radAttr0.itemSize).toBe(3);
    expect(magAttr0).toBeInstanceOf(InstancedBufferAttribute);
    expect(magAttr0.itemSize).toBe(1);
    expect((magAttr0.array as Float32Array)[0]).toBeCloseTo(0.5, 3);
    expect((magAttr0.array as Float32Array)[1]).toBeCloseTo(4.0, 3);

    const decodedL0P4 = decodeCoreTile(tileL0P4, { layer: 0, order: 0, pix: 4 });
    const expectedRad0 = tileRadiance(decodedL0P4, new StarColourTable());
    const dirArr0 = dirAttr0.array as Float32Array;
    const radArr0 = radAttr0.array as Float32Array;
    expect(dirArr0).toHaveLength(6);
    expect(radArr0).toHaveLength(6);
    for (let s = 0; s < 2; s++) {
      const dx = dirArr0[s * 3]!;
      const dy = dirArr0[s * 3 + 1]!;
      const dz = dirArr0[s * 3 + 2]!;
      expect(Math.hypot(dx, dy, dz)).toBeCloseTo(1, 6);
    }
    for (let k = 0; k < 6; k++) {
      expect(dirArr0[k]).toBeCloseTo(decodedL0P4.directions[k]!, 6);
      expect(radArr0[k]).toBeCloseTo(expectedRad0[k]!, 6);
      expect(radArr0[k]).toBeGreaterThan(0);
    }
    expect(Math.abs(dirArr0[0]! - radArr0[0]!)).toBeGreaterThan(0.1);

    // Check widened HEALPix boundingSphere: chord of min(PI, tileBoundingRadius + SPRITE_MARGIN_RAD)
    const spriteMarginRad = MathUtils.degToRad((220 / 1080) * 56);
    const expectedCenter0 = tileCenter(0, 4);
    const expectedAngular0 = Math.min(Math.PI, tileBoundingRadius(0, 4) + spriteMarginRad);
    const expectedChord0 = 2 * Math.sin(expectedAngular0 / 2);
    const unwidenedChord0 = 2 * Math.sin(tileBoundingRadius(0, 4) / 2);
    expect(geo0.boundingSphere).not.toBeNull();
    expect(geo0.boundingSphere!.center.distanceTo(new Vector3(...expectedCenter0))).toBeLessThan(1e-12);
    expect(geo0.boundingSphere!.radius).toBeCloseTo(expectedChord0, 10);
    expect(geo0.boundingSphere!.radius).toBeGreaterThan(unwidenedChord0 + 0.1);

    // Create a second mount AFTER tile L0/4 is already resident
    const mountAfter = field.createMount(750);
    expect([mountAfter.scale.x, mountAfter.scale.y, mountAfter.scale.z]).toEqual([750, 750, 750]);
    expect(mountAfter.children).toHaveLength(1);
    const meshAfter0 = mountAfter.children[0] as Mesh;
    expect(meshAfter0).not.toBe(meshBefore0);
    expect(meshAfter0.name).toBe('catalogStarTile');
    expect(meshAfter0.renderOrder).toBe(-10);
    expect(meshAfter0.geometry).toBe(geo0);
    expect(meshAfter0.material).toBe(meshBefore0.material);

    // Stream a second tile (L2/0) by deepening limiting magnitude and aiming at order 3 pix 0
    field.setLimitingMagnitude(12);
    const camToL2P0 = makeCameraAimedAtSceneDir(icrsToScene(tileCenter(3, 0)));
    field.update(camToL2P0, 1080, 1010);
    await vi.waitFor(() => expect(field.residentStars()).toBe(5));

    expect(mountBefore.children).toHaveLength(2);
    expect(mountAfter.children).toHaveLength(2);
    const meshBefore1 = mountBefore.children[1] as Mesh;
    const meshAfter1 = mountAfter.children[1] as Mesh;
    const geo1 = meshBefore1.geometry as InstancedBufferGeometry;
    expect(meshAfter1.geometry).toBe(geo1);
    expect(meshBefore1.renderOrder).toBe(-10);

    // Both tiles share the exact same quad vertex and index BufferAttribute instances
    expect(geo1.getAttribute('position')).toBe(posAttr0);
    expect(geo1.getIndex()).toBe(indexAttr0);

    const expectedCenter1 = tileCenter(3, 0);
    const expectedAngular1 = Math.min(Math.PI, tileBoundingRadius(3, 0) + spriteMarginRad);
    expect(geo1.boundingSphere!.center.distanceTo(new Vector3(...expectedCenter1))).toBeLessThan(1e-12);
    expect(geo1.boundingSphere!.radius).toBeCloseTo(2 * Math.sin(expectedAngular1 / 2), 10);

    field.dispose();
  });
});

describe('CatalogStarSpriteField update, mount selection, throttling and uniform controls', () => {
  it('updates psfScale, exposure and clamped limitingMagnitude uniforms before and after manifest load', async () => {
    const manifest = makeManifest({ magnitudeLimit: 10.5, layer0Counts: { 4: 1 }, layer2Counts: {} });
    const tile = encodeTestCoreTile({ layer: 0, order: 0, pix: 4 }, [
      { magnitude: 3.2, colorIndex: 0.65, flags: 0 },
    ]);
    let resolveManifest!: (res: Response) => void;
    const fetchImpl = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/manifest.json')) {
        return new Promise<Response>((resolve) => {
          resolveManifest = resolve;
        });
      }
      if (url.endsWith('/L0/4.core.bin')) {
        return Promise.resolve(new Response(tile.slice(0), { status: 200 }));
      }
      return Promise.resolve(new Response(null, { status: 404 }));
    }) as unknown as typeof fetch;

    const field = new CatalogStarSpriteField({
      limitingMagnitude: 14,
      exposure: 720,
      fetch: fetchImpl,
    });
    // Default baseUrl is STAR_CATALOG_BASE_URL
    expect(vi.mocked(fetchImpl)).toHaveBeenCalledWith(`${STAR_CATALOG_BASE_URL}manifest.json`);
    expect(field.getStatus()).toBe('loading');
    expect(field.getLimitingMagnitude()).toBe(14);

    // Before manifest load, setLimitingMagnitude clamps to [-2, 12]
    field.setLimitingMagnitude(-5);
    expect(field.getLimitingMagnitude()).toBe(-2);
    field.setLimitingMagnitude(15);
    expect(field.getLimitingMagnitude()).toBe(12);

    const mount = field.createMount(1000);
    const cam = makeCameraAimedAtSceneDir(icrsToScene(tileCenter(0, 4)));
    // Calling update while loader is still null still scales psfScale = drawingBufferHeight / 1080
    field.update(cam, 2160, 500);

    resolveManifest(new Response(JSON.stringify(manifest), { status: 200 }));
    await field.ready;

    // Manifest ceiling (10.5) clamps limitingMagnitude down from 12 to 10.5
    expect(field.getStatus()).toBe('ready');
    expect(field.getManifest()?.magnitudeLimit).toBe(10.5);
    expect(field.getLimitingMagnitude()).toBe(10.5);

    field.update(cam, 2160, 600);
    await vi.waitFor(() => expect(mount.children).toHaveLength(1));

    const material = (mount.children[0] as Mesh).material as NodeMaterial;
    const uniforms = extractMaterialUniforms(material);
    expect(uniforms.psfScale.value).toBeCloseTo(2.0, 6);
    expect(uniforms.exposure.value).toBe(720);
    expect(uniforms.limitingMagnitude.value).toBe(10.5);

    // Subsequent psfScale updates apply even when camera has not moved (throttled view sync)
    field.update(cam, 540, 610);
    expect(uniforms.psfScale.value).toBeCloseTo(0.5, 6);

    field.setExposure(1100);
    expect(uniforms.exposure.value).toBe(1100);

    // Post-ready setLimitingMagnitude clamps to [-2, manifest.magnitudeLimit (10.5)]
    field.setLimitingMagnitude(11.8);
    expect(field.getLimitingMagnitude()).toBe(10.5);
    field.setLimitingMagnitude(-3.5);
    expect(field.getLimitingMagnitude()).toBe(-2);
    expect(uniforms.limitingMagnitude.value).toBe(-2);

    field.dispose();
  });

  it('selects visible-parent mount over hidden-parent mount, falls back to mounts[0] or ICRS_TO_SCENE_QUATERNION, and inverts mount world rotation', async () => {
    const manifest = makeManifest({
      layer0Counts: {},
      layer2Counts: { 0: 1, 200: 1, 500: 1 },
    });
    const fetchImpl = buildStaticFetch({
      '/stars/manifest.json': manifest,
      '/stars/L2/0.core.bin': encodeTestCoreTile({ layer: 2, order: 3, pix: 0 }, [
        { magnitude: 10.5, colorIndex: 0.5 },
      ]),
      '/stars/L2/200.core.bin': encodeTestCoreTile({ layer: 2, order: 3, pix: 200 }, [
        { magnitude: 10.5, colorIndex: 0.5 },
      ]),
      '/stars/L2/500.core.bin': encodeTestCoreTile({ layer: 2, order: 3, pix: 500 }, [
        { magnitude: 10.5, colorIndex: 0.5 },
      ]),
    });

    const setViewSpy = vi.spyOn(CatalogStarTileLoader.prototype, 'setView');
    const field = new CatalogStarSpriteField({
      baseUrl: '/stars',
      limitingMagnitude: 12,
      fetch: fetchImpl,
    });
    await field.ready;

    // 1. Unmounted: uses ICRS_TO_SCENE_QUATERNION.invert()
    const icrsTarget0 = new Vector3(...tileCenter(3, 0));
    const sceneDirUnmounted = icrsToScene(tileCenter(3, 0));
    const cam0 = makeCameraAimedAtSceneDir(sceneDirUnmounted, 20, 1);
    field.update(cam0, 1080, 1000);
    expect(setViewSpy).toHaveBeenCalledTimes(1);
    const view0 = setViewSpy.mock.calls[0]![0];
    expect(new Vector3(...view0.centerIcrs).angleTo(icrsTarget0)).toBeLessThan(1e-6);

    // 2. Hidden first mount vs rotated+translated visible second mount
    const hiddenParent = new Group();
    hiddenParent.visible = false;
    hiddenParent.quaternion.setFromAxisAngle(new Vector3(0, 1, 0), 1.1);
    const mount0 = field.createMount(1500);
    hiddenParent.add(mount0);
    hiddenParent.updateMatrixWorld(true);

    const visibleParent = new Group();
    visibleParent.visible = true;
    visibleParent.position.set(420, -180, 910);
    visibleParent.quaternion.setFromAxisAngle(new Vector3(1, 1, 0).normalize(), -0.95);
    const mount1 = field.createMount(2400);
    visibleParent.add(mount1);
    visibleParent.updateMatrixWorld(true);

    // Aim camera along the world direction where mount1 places HEALPix tile (3, 200)
    const icrsTarget200 = new Vector3(...tileCenter(3, 200));
    const mount1WorldQuat = mount1.getWorldQuaternion(new Quaternion());
    const worldDirForMount1 = icrsTarget200.clone().applyQuaternion(mount1WorldQuat);
    const cam1 = makeCameraAimedAtSceneDir(worldDirForMount1, 20, 1);
    field.update(cam1, 1080, 1200);
    expect(setViewSpy).toHaveBeenCalledTimes(2);
    const view1 = setViewSpy.mock.calls[1]![0];
    expect(new Vector3(...view1.centerIcrs).angleTo(icrsTarget200)).toBeLessThan(1e-6);

    // 3. When visibleParent is hidden, an unparented mount (parent === null) is treated as visible over hidden mounts[0]
    visibleParent.visible = false;
    const mount2 = field.createMount(900);
    expect(mount2.parent).toBeNull();
    mount2.quaternion.multiply(new Quaternion().setFromAxisAngle(new Vector3(0, 0, 1), 0.72));
    mount2.updateMatrixWorld(true);
    const icrsTargetUnparented = new Vector3(...tileCenter(3, 500));
    const mount2WorldQuat = mount2.getWorldQuaternion(new Quaternion());
    const worldDirForMount2 = icrsTargetUnparented.clone().applyQuaternion(mount2WorldQuat);
    const camUnparented = makeCameraAimedAtSceneDir(worldDirForMount2, 20, 1);
    field.update(camUnparented, 1080, 1400);
    expect(setViewSpy).toHaveBeenCalledTimes(3);
    const viewUnparented = setViewSpy.mock.calls[2]![0];
    expect(new Vector3(...viewUnparented.centerIcrs).angleTo(icrsTargetUnparented)).toBeLessThan(1e-6);

    // 4. When all mount parents are hidden, falls back to mounts[0]
    const hiddenParent2 = new Group();
    hiddenParent2.visible = false;
    hiddenParent2.add(mount2);
    hiddenParent2.updateMatrixWorld(true);
    const icrsTarget500 = new Vector3(...tileCenter(3, 0));
    const mount0WorldQuat = mount0.getWorldQuaternion(new Quaternion());
    const worldDirForMount0 = icrsTarget500.clone().applyQuaternion(mount0WorldQuat);
    const cam2 = makeCameraAimedAtSceneDir(worldDirForMount0, 20, 1);
    field.update(cam2, 1080, 1600);
    expect(setViewSpy).toHaveBeenCalledTimes(4);
    const view2 = setViewSpy.mock.calls[3]![0];
    expect(new Vector3(...view2.centerIcrs).angleTo(icrsTarget500)).toBeLessThan(1e-6);

    field.dispose();
  });

  it('enforces 120 ms and 0.01 rad throttling, invalidates throttle on setLimitingMagnitude, and bypasses on force = true', async () => {
    const manifest = makeManifest({ layer0Counts: { 4: 1 }, layer2Counts: {} });
    const fetchImpl = buildStaticFetch({
      '/stars/manifest.json': manifest,
      '/stars/L0/4.core.bin': encodeTestCoreTile({ layer: 0, order: 0, pix: 4 }, [
        { magnitude: 2.5, colorIndex: 0.2 },
      ]),
    });

    const setViewSpy = vi.spyOn(CatalogStarTileLoader.prototype, 'setView');
    const field = new CatalogStarSpriteField({
      baseUrl: '/stars',
      limitingMagnitude: DEFAULT_LIMITING_MAGNITUDE,
      fetch: fetchImpl,
    });
    await field.ready;

    const camA = makeCameraAimedAtSceneDir(new Vector3(0, 0, -1), 56, 16 / 9);
    const expectedHalfDiag56 = Math.atan(
      Math.tan(MathUtils.degToRad(56) / 2) * Math.hypot(1, 16 / 9)
    );
    field.update(camA, 1080, 1000);
    expect(setViewSpy).toHaveBeenCalledTimes(1);
    expect(setViewSpy.mock.calls[0]![0].radiusRad).toBeCloseTo(expectedHalfDiag56 + 0.08, 10);
    expect(setViewSpy.mock.calls[0]![0].limitingMagnitude).toBe(DEFAULT_LIMITING_MAGNITUDE);

    // Large rotation at t = 1119 (119 ms < 120 ms elapsed) with force = false is throttled
    const camRotated = makeCameraAimedAtSceneDir(new Vector3(1, 0, 0), 56, 16 / 9);
    field.update(camRotated, 1080, 1119);
    expect(setViewSpy).toHaveBeenCalledTimes(1);

    // Reaching t = 1120 (exact 120 ms boundary) lets the rotated view through
    field.update(camRotated, 1080, 1120);
    expect(setViewSpy).toHaveBeenCalledTimes(2);

    // Sub-epsilon rotation (0.008 rad <= 0.01 rad) even after 500 ms does not re-plan
    const tinyTurnDir = new Vector3(1, 0, 0).applyAxisAngle(new Vector3(0, 1, 0), 0.008);
    const camTinyTurn = makeCameraAimedAtSceneDir(tinyTurnDir, 56, 16 / 9);
    field.update(camTinyTurn, 1080, 1700);
    expect(setViewSpy).toHaveBeenCalledTimes(2);

    // Supra-epsilon rotation (0.015 rad > 0.01 rad, < 0.05 rad) triggers re-plan and updates lastDir
    const smallTurnDir = new Vector3(1, 0, 0).applyAxisAngle(new Vector3(0, 1, 0), 0.015);
    const camSmallTurn = makeCameraAimedAtSceneDir(smallTurnDir, 56, 16 / 9);
    field.update(camSmallTurn, 1080, 1850);
    expect(setViewSpy).toHaveBeenCalledTimes(3);
    field.update(camSmallTurn, 1080, 2000);
    expect(setViewSpy).toHaveBeenCalledTimes(3);

    // FOV change widening view-cone radius by > 0.01 rad after >= 120 ms triggers re-plan
    const camWiderFov = makeCameraAimedAtSceneDir(smallTurnDir, 64, 16 / 9);
    field.update(camWiderFov, 1080, 2150);
    expect(setViewSpy).toHaveBeenCalledTimes(4);

    // Zooming IN to a narrower FOV (radius - lastRadius < -0.01 rad) without rotating triggers re-plan via Math.abs
    const camNarrowerFov = makeCameraAimedAtSceneDir(smallTurnDir, 48, 16 / 9);
    field.update(camNarrowerFov, 1080, 2300);
    expect(setViewSpy).toHaveBeenCalledTimes(5);

    // Sub-epsilon FOV tweak (< 0.01 rad radius delta) at narrower FOV is suppressed because lastRadius was saved
    const camSubEpsFov = makeCameraAimedAtSceneDir(smallTurnDir, 48.2, 16 / 9);
    field.update(camSubEpsFov, 1080, 2450);
    expect(setViewSpy).toHaveBeenCalledTimes(5);

    // force = true bypasses BOTH the 120 ms interval (only 5 ms later) AND zero camera delta
    field.update(camNarrowerFov, 1080, 2455, true);
    expect(setViewSpy).toHaveBeenCalledTimes(6);

    // setLimitingMagnitude resets lastView.at so an update 5 ms later with force = false re-plans immediately
    field.setLimitingMagnitude(11.0);
    field.update(camNarrowerFov, 1080, 2460, false);
    expect(setViewSpy).toHaveBeenCalledTimes(7);
    expect(setViewSpy.mock.calls[6]![0].limitingMagnitude).toBe(11.0);

    // Calling setLimitingMagnitude with the SAME magnitude 11.0 resets lastAtMs, yet unchanged state returns null
    field.setLimitingMagnitude(11.0);
    field.update(camNarrowerFov, 1080, 2465, false);
    expect(setViewSpy).toHaveBeenCalledTimes(7);

    field.dispose();
  });
});

describe('CatalogStarSpriteField lifecycle, settlement, eviction and disposal', () => {
  it('tracks loading -> ready settlement across in-flight, loaded and failed core tiles', async () => {
    const manifest = makeManifest({
      layer0Counts: { 4: 2 },
      layer2Counts: { 0: 1 },
    });
    const tileOk = encodeTestCoreTile({ layer: 0, order: 0, pix: 4 }, [
      { magnitude: 1.0, colorIndex: 0.1 },
      { magnitude: 3.0, colorIndex: 0.9 },
    ]);

    let resolveTileL0!: (res: Response) => void;
    const fetchImpl = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/manifest.json')) {
        return Promise.resolve(new Response(JSON.stringify(manifest), { status: 200 }));
      }
      if (url.endsWith('/L0/4.core.bin')) {
        return new Promise<Response>((resolve) => {
          resolveTileL0 = resolve;
        });
      }
      if (url.endsWith('/L2/0.core.bin')) {
        return Promise.resolve(new Response(null, { status: 500 }));
      }
      return Promise.resolve(new Response(null, { status: 404 }));
    }) as unknown as typeof fetch;

    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const field = new CatalogStarSpriteField({
      baseUrl: '/stars',
      limitingMagnitude: 6.5,
      fetch: fetchImpl,
    });

    // While loading manifest: not settled, no manifest, stats show loading
    expect(field.getStatus()).toBe('loading');
    expect(field.getManifest()).toBeNull();
    expect(field.isSettled()).toBe(false);
    expect(field.residentStars()).toBe(0);
    expect(field.getStats()).toEqual({ status: 'loading', limitingMagnitude: 6.5 });

    await field.ready;
    expect(field.getStatus()).toBe('ready');
    // Zero wanted tiles initially -> settled
    expect(field.isSettled()).toBe(true);

    // Request L0/4: while core tile is in flight, isSettled() must be false
    const camL0 = makeCameraAimedAtSceneDir(icrsToScene(tileCenter(0, 4)), 20, 1);
    field.update(camL0, 1080, 1000);
    expect(field.isSettled()).toBe(false);
    expect(field.getStats().tilesInFlight).toBe(1);

    resolveTileL0(new Response(tileOk.slice(0), { status: 200 }));
    await vi.waitFor(() => expect(field.residentStars()).toBe(2));
    expect(field.isSettled()).toBe(true);
    expect(field.getStats()).toMatchObject({
      status: 'ready',
      limitingMagnitude: 6.5,
      tilesLoaded: 1,
      tilesInFlight: 0,
      tilesWanted: 1,
      starsResident: 2,
    });

    // Pan to L2/0 at mag 12 where tile fetch fails with HTTP 500: tilesInFlight drops to 0,
    // but tilesLoaded (1) >= tilesWanted (1) would pass ONLY if L0/4 were not counted!
    // Aim at L2/0 with narrow cone where only L2/0 is wanted (1 wanted), and use a fresh field
    // with 0 loaded tiles to pin tilesLoaded (0) < tilesWanted (1) -> isSettled() === false!
    field.dispose();

    const failManifest = makeManifest({ layer0Counts: {}, layer2Counts: { 0: 2 } });
    const failFetch = buildStaticFetch({
      '/stars/manifest.json': failManifest,
      '/stars/L2/0.core.bin': 500,
    });
    const failingField = new CatalogStarSpriteField({
      baseUrl: '/stars',
      limitingMagnitude: 12,
      fetch: failFetch,
    });
    await failingField.ready;
    const camL2 = makeCameraAimedAtSceneDir(icrsToScene(tileCenter(3, 0)), 20, 1);
    failingField.update(camL2, 1080, 2000);
    await vi.waitFor(() => expect(failingField.getStats().failures).toBe(1));
    expect(failingField.getStats().tilesInFlight).toBe(0);
    expect(failingField.getStats().tilesLoaded).toBe(0);
    expect(failingField.getStats().tilesWanted).toBe(1);
    expect(failingField.isSettled()).toBe(false);
    expect(warnSpy).toHaveBeenCalledWith(
      '[catalog stars] tile 2/0 failed',
      expect.any(Error)
    );
    failingField.dispose();
  });

  it('transitions loading -> unavailable without fallback stars when manifest fails', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const fetchImpl = buildStaticFetch({ '/stars/manifest.json': 503 });
    const field = new CatalogStarSpriteField({
      baseUrl: '/stars',
      limitingMagnitude: 8.0,
      fetch: fetchImpl,
    });

    await field.ready;
    expect(field.getStatus()).toBe('unavailable');
    expect(field.getManifest()).toBeNull();
    expect(field.isSettled()).toBe(true);
    expect(field.residentStars()).toBe(0);
    expect(field.getStats()).toEqual({ status: 'unavailable', limitingMagnitude: 8.0 });
    expect(warnSpy).toHaveBeenCalledWith(
      '[catalog stars] catalog unavailable',
      expect.any(Error)
    );

    const cam = makeCameraAimedAtSceneDir(new Vector3(0, 0, -1));
    expect(() => field.update(cam, 1440, 1000)).not.toThrow();
    field.dispose();
  });

  it('detaches evicted tile meshes and disposes their InstancedBufferGeometry, then cleans up completely on dispose()', async () => {
    const manifest = makeManifest({
      layer0Counts: {},
      layer2Counts: { 0: 2, 400: 3 },
    });
    const tile0 = encodeTestCoreTile({ layer: 2, order: 3, pix: 0 }, [
      { magnitude: 10.1, colorIndex: 0.3 },
      { magnitude: 10.8, colorIndex: 0.7 },
    ]);
    const tile400 = encodeTestCoreTile({ layer: 2, order: 3, pix: 400 }, [
      { magnitude: 10.2, colorIndex: 0.4 },
      { magnitude: 10.9, colorIndex: 0.8 },
      { magnitude: 11.4, colorIndex: 1.1 },
    ]);
    const fetchImpl = buildStaticFetch({
      '/stars/manifest.json': manifest,
      '/stars/L2/0.core.bin': tile0,
      '/stars/L2/400.core.bin': tile400,
    });

    const realOpen = CatalogStarTileLoader.open.bind(CatalogStarTileLoader);
    let capturedOnTileLoaded: ((tile: CatalogStarCoreTile) => void) | undefined;
    let capturedOnTileEvicted: ((key: { layer: number; pix: number }) => void) | undefined;
    vi.spyOn(CatalogStarTileLoader, 'open').mockImplementation((baseUrl, opts = {}) => {
      capturedOnTileLoaded = opts.onTileLoaded;
      capturedOnTileEvicted = opts.onTileEvicted;
      return realOpen(baseUrl, { ...opts, maxResidentStars: 3 });
    });

    const field = new CatalogStarSpriteField({
      baseUrl: '/stars',
      fetch: fetchImpl,
    });
    expect(field.getLimitingMagnitude()).toBe(DEFAULT_LIMITING_MAGNITUDE);
    field.setLimitingMagnitude(12);
    const mountA = field.createMount(1000);
    const mountB = field.createMount(2000);
    await field.ready;

    // Load tile 2/0 (2 stars <= cap 3)
    const cam0 = makeCameraAimedAtSceneDir(icrsToScene(tileCenter(3, 0)), 15, 1);
    field.update(cam0, 1080, 1000);
    await vi.waitFor(() => expect(field.residentStars()).toBe(2));
    expect(mountA.children).toHaveLength(1);
    expect(mountB.children).toHaveLength(1);

    const tile0MeshA = mountA.children[0] as Mesh;
    const tile0MeshB = mountB.children[0] as Mesh;
    const defaultUniforms = extractMaterialUniforms(tile0MeshA.material as NodeMaterial);
    expect(defaultUniforms.exposure.value).toBe(DEFAULT_CATALOG_STAR_EXPOSURE);

    // Duplicate onTileLoaded for already-resident key 2/0 is a no-op
    capturedOnTileLoaded!({
      layer: 2,
      order: 3,
      pix: 0,
      count: 2,
      positionBits: 16,
      directions: new Float32Array(6),
      magnitude: new Float32Array(2),
      colorIndex: new Float32Array(2),
      distancePc: new Float32Array(2),
      flags: new Uint8Array(2),
      byteLength: 0,
    });
    expect(field.residentStars()).toBe(2);
    expect(mountA.children).toHaveLength(1);

    // Evicting an unknown tile key is a safe no-op
    expect(() => capturedOnTileEvicted!({ layer: 9, pix: 999 })).not.toThrow();
    const tile0Geo = tile0MeshA.geometry as InstancedBufferGeometry;
    const tile0GeoDispose = vi.spyOn(tile0Geo, 'dispose');
    const matDispose = vi.spyOn(tile0MeshA.material as NodeMaterial, 'dispose');

    // Pan to tile 2/400 (3 stars; 2 + 3 = 5 > cap 3 -> evicts 2/0!)
    const cam400 = makeCameraAimedAtSceneDir(icrsToScene(tileCenter(3, 400)), 15, 1);
    field.update(cam400, 1080, 1200);
    await vi.waitFor(() => expect(field.residentStars()).toBe(3));

    expect(tile0GeoDispose).toHaveBeenCalledTimes(1);
    expect(tile0MeshA.parent).toBeNull();
    expect(tile0MeshB.parent).toBeNull();
    expect(mountA.children).toHaveLength(1);
    expect(mountB.children).toHaveLength(1);

    const tile400MeshA = mountA.children[0] as Mesh;
    const tile400Geo = tile400MeshA.geometry as InstancedBufferGeometry;
    const tile400GeoDispose = vi.spyOn(tile400Geo, 'dispose');
    const loaderDisposeSpy = vi.spyOn(CatalogStarTileLoader.prototype, 'dispose');

    // Full dispose() disposes the tile loader, detaches remaining meshes, and disposes remaining geometry and PSF material
    field.dispose();
    expect(loaderDisposeSpy).toHaveBeenCalledTimes(1);
    expect(tile400GeoDispose).toHaveBeenCalledTimes(1);
    expect(matDispose).toHaveBeenCalledTimes(1);
    expect(mountA.children).toHaveLength(0);
    expect(mountB.children).toHaveLength(0);
    expect(field.residentStars()).toBe(0);

    // Late onTileLoaded after dispose() is ignored
    expect(capturedOnTileLoaded).toBeDefined();
    capturedOnTileLoaded!({
      layer: 2,
      order: 3,
      pix: 12,
      count: 4,
      positionBits: 16,
      directions: new Float32Array(12),
      magnitude: new Float32Array(4),
      colorIndex: new Float32Array(4),
      distancePc: new Float32Array(4),
      flags: new Uint8Array(4),
      byteLength: 0,
    });
    expect(field.residentStars()).toBe(0);
    expect(mountA.children).toHaveLength(0);
  });

  it('ignores manifest resolution if dispose() is called while CatalogStarTileLoader.open is still pending', async () => {
    const manifest = makeManifest();
    let resolveManifest!: (res: Response) => void;
    const fetchImpl = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          resolveManifest = resolve;
        })
    ) as unknown as typeof fetch;

    const field = new CatalogStarSpriteField({ baseUrl: '/stars', fetch: fetchImpl });
    field.dispose();
    resolveManifest(new Response(JSON.stringify(manifest), { status: 200 }));
    await field.ready;

    expect(field.getStatus()).toBe('loading');
    expect(field.getManifest()).toBeNull();
  });
});
