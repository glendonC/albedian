import {
  ClampToEdgeWrapping,
  DataTexture,
  LinearFilter,
  Matrix3,
  NoColorSpace,
  RGFormat,
  RepeatWrapping,
  UnsignedByteType,
  type Node,
  type Texture,
} from 'three/webgpu';
import {
  Fn,
  asin,
  atan,
  clamp,
  dFdx,
  dFdy,
  fract,
  max,
  positionWorldDirection,
  pow,
  texture,
  uniform,
  vec2,
  vec3,
  vec4,
} from 'three/tsl';
import { sceneToIcrsRows, sceneToPanoramaRows, type PanoramaRegistration } from '../../sky/skyRegistration.js';

/** Plate carree coordinates of a unit vector: longitude 0 at u = 0.5, increasing to the left; north at v = 1. */
function plateCarree(d: Node<'vec3'>): Node<'vec2'> {
  return vec2(fract(atan(d.y, d.x).mul(-1 / (2 * Math.PI)).add(0.5)), asin(clamp(d.z, -1.0, 1.0)).mul(1 / Math.PI).add(0.5));
}

/** Screen-space gradients of plate carree coordinates, wrapped where u jumps across the seam. */
function seamlessGrad(uv: Node<'vec2'>): [Node<'vec2'>, Node<'vec2'>] {
  const dx = dFdx(uv);
  const dy = dFdy(uv);
  return [vec2(fract(dx.x.add(0.5)).sub(0.5), dx.y), vec2(fract(dy.x.add(0.5)).sub(0.5), dy.y)];
}

/** The panorama's offset table as a filterable RG8 texture (u repeats, v clamps). */
export function createPanoramaOffsetTexture(registration: PanoramaRegistration): DataTexture {
  const { width, height, codes } = registration.offsetTable;
  const tex = new DataTexture(codes, width, height, RGFormat, UnsignedByteType);
  tex.colorSpace = NoColorSpace;
  tex.wrapS = RepeatWrapping;
  tex.wrapT = ClampToEdgeWrapping;
  tex.minFilter = LinearFilter;
  tex.magFilter = LinearFilter;
  tex.generateMipmaps = false;
  tex.unpackAlignment = 1;
  tex.needsUpdate = true;
  return tex;
}

/**
 * The all-sky dome of the landing scenes as a background node, ported from `skyMat` in
 * SparkPortalRenderer.ts (optical band, restoration on, which is what the landing draws), and
 * sampled by view direction so it lines up with the catalog stars (see sky/skyRegistration.ts):
 * the ESO panorama through its fitted rotation and offset table, the NASA SVS star map through
 * right ascension and declination. Screen-space u derivatives are wrapped at the seam so the
 * panorama keeps full mip detail across it.
 *
 * Content: the ESO Milky Way panorama and the NASA star map, black-level clipped, contrast
 * curved and tinted for display, plus a boost of the panorama's red excess (its H II regions);
 * no new structure is synthesized.
 * Not ported, since the landing never shows them: the halpha and jwst_ir band remaps, the
 * spectral monocle and the unrestored sensor veil.
 */
export function createSkyBackgroundNode(
  milkyWay: Texture,
  starMap: Texture,
  registration: PanoramaRegistration
): Node<'vec4'> {
  const sceneToPanorama = uniform(new Matrix3().set(...sceneToPanoramaRows(registration)));
  const sceneToIcrs = uniform(new Matrix3().set(...sceneToIcrsRows()));
  const { imageWidth, imageHeight, yOffsetPx, offsetTable } = registration;
  const milkyWayNode = texture(milkyWay);
  const starMapNode = texture(starMap);
  const offsetNode = texture(createPanoramaOffsetTexture(registration));

  return Fn(() => {
    const dir = positionWorldDirection.toVar('skyDir');

    const p = sceneToPanorama.mul(dir).normalize().toVar('skyPanoramaDir');
    const frameUv = plateCarree(p).toVar('skyFrameUv');
    const offset = offsetNode.sample(frameUv).rg.mul(255).sub(offsetTable.bias).div(offsetTable.scale);
    const cosLat = max(p.xy.length(), 1e-3);
    const mwUv = frameUv.add(
      vec2(offset.x.negate().div(cosLat.mul(imageWidth)), offset.y.sub(yOffsetPx).div(imageHeight))
    );
    const [mwGradX, mwGradY] = seamlessGrad(frameUv);

    const starUv = plateCarree(sceneToIcrs.mul(dir).normalize()).toVar('skyStarUv');
    const [starGradX, starGradY] = seamlessGrad(starUv);

    const mwCol = milkyWayNode.sample(mwUv).grad(mwGradX, mwGradY).rgb;
    const starCol = starMapNode.sample(starUv).grad(starGradX, starGradY).rgb;
    const cleanMw = max(vec3(0.0), mwCol.sub(0.042)).toVar('skyCleanMw');
    const cleanStars = max(vec3(0.0), starCol.sub(0.068));

    const galacticBulge = pow(cleanMw, vec3(1.52)).mul(vec3(1.22, 1.04, 0.9)).mul(1.32);
    // Called hAlphaKnots in the GLSL: a colour boost of the panorama's red excess, not a measurement
    const redExcessBoost = vec3(1.25, 0.28, 0.48).mul(pow(max(0.0, cleanMw.r.sub(cleanMw.g.mul(0.85))), 1.6)).mul(1.85);
    const deepSpaceBase = vec3(0.002, 0.003, 0.008);
    const sky = deepSpaceBase.add(galacticBulge).add(redExcessBoost).add(pow(cleanStars, vec3(1.95)).mul(0.72));
    return vec4(sky, 1.0);
  })() as unknown as Node<'vec4'>;
}
