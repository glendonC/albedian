import { Euler, Matrix3, Matrix4, type Node, type Texture } from 'three/webgpu';
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

/** Orientation of the WebGL sky dome mesh (`skyDome.rotation`); not registered to ICRS. */
export const SKY_DOME_ROTATION = new Euler(0.18, 0, 0.32);

/**
 * The all-sky dome of the landing scenes as a background node, ported from `skyMat` in
 * SparkPortalRenderer.ts (optical band, restoration on, which is what the landing draws).
 *
 * The WebGL dome was a back-side `SphereGeometry` at radius 900 around the camera, so its look
 * depends only on the view direction. Here the same sphere UVs are computed per pixel from the
 * world direction: `SphereGeometry` puts x = -cos(phi) sin(theta), y = cos(theta),
 * z = sin(phi) sin(theta) at uv = (phi / 2 pi, 1 - theta / pi). The u derivative is wrapped at
 * both meridians where atan and the +0.48 shift jump, so the panorama keeps full mip detail
 * across them.
 *
 * Content: the ESO Milky Way panorama and the NASA star map, black-level clipped, contrast
 * curved and tinted for display, plus a boost of the panorama's red excess (its H II regions);
 * no new structure is synthesized. Neither image is registered to the catalog stars.
 * Not ported, since the landing never shows them: the halpha and jwst_ir band remaps, the
 * spectral monocle and the unrestored sensor veil.
 */
export function createSkyBackgroundNode(milkyWay: Texture, starMap: Texture, rotation = SKY_DOME_ROTATION): Node<'vec4'> {
  // World direction to dome-local: the inverse (transpose) of the dome rotation
  const toDome = uniform(new Matrix3().setFromMatrix4(new Matrix4().makeRotationFromEuler(rotation)).transpose());
  const milkyWayNode = texture(milkyWay);
  const starMapNode = texture(starMap);

  return Fn(() => {
    const d = toDome.mul(positionWorldDirection).normalize().toVar('skyDir');
    const u = fract(atan(d.z, d.x.negate()).mul(1 / (2 * Math.PI)).add(0.48));
    const v = asin(clamp(d.y, -1.0, 1.0)).mul(1 / Math.PI).add(0.5);
    const skyUv = vec2(u, v).toVar('skyUv');
    const rawDx = dFdx(skyUv);
    const rawDy = dFdy(skyUv);
    const gradX = vec2(fract(rawDx.x.add(0.5)).sub(0.5), rawDx.y);
    const gradY = vec2(fract(rawDy.x.add(0.5)).sub(0.5), rawDy.y);

    const mwCol = milkyWayNode.sample(skyUv).grad(gradX, gradY).rgb;
    const starCol = starMapNode.sample(skyUv).grad(gradX, gradY).rgb;
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
