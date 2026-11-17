import {
  cross,
  fract,
  normalViewGeometry,
  normalize,
  positionView,
  vec2,
  vec3,
  vertexStage,
} from 'three/tsl';
import type { Vec2Node, Vec3Node } from './materialTypes.js';

export const POLE_AXIS_EPSILON = 1e-7;

/** View-space unit normal as `normalize(normalMatrix * normal)`; not flipped on back faces. */
export function viewNormal(): Vec3Node {
  return normalize(normalViewGeometry);
}

/** View-space direction from the surface to the camera, as `normalize(-mvPosition.xyz)`. */
export function viewDirection(): Vec3Node {
  return normalize(vertexStage(normalize(positionView.negate())));
}

/**
 * Builds the `SphereGeometry` local tangent basis `(east, north)` for a unit radial vector `up`:
 * `+y` is north, `+x` is longitude `0`, `-z` is longitude `90 E`. Adding `POLE_AXIS_EPSILON` on `x`
 * keeps `east` defined at the poles where `cross(+y, up)` vanishes.
 */
export function sphereTangentFrameNodes(unitUp: Vec3Node): { east: Vec3Node; north: Vec3Node } {
  const east = normalize(cross(vec3(0, 1, 0), unitUp).add(vec3(POLE_AXIS_EPSILON, 0, 0)));
  const north = cross(unitUp, east);
  return { east, north };
}

/** Wraps an equirectangular `u` screen derivative across the `-180 / +180` longitude seam. */
export function wrapSeamDerivativeNode(rawGrad: Vec2Node): Vec2Node {
  return vec2(fract(rawGrad.x.add(0.5)).sub(0.5), rawGrad.y);
}
