import { NodeMaterial } from 'three/webgpu';
import {
  Fn,
  cameraPosition,
  dot,
  max,
  modelWorldMatrix,
  normalize,
  positionLocal,
  positionWorld,
  smoothstep,
  texture,
  uv,
  vec4,
} from 'three/tsl';
import type { MoonTextures, Vec3Node } from './materialTypes.js';
import { sphereTangentFrameNodes } from './tslSphereFrame.js';

/**
 * How far past the sphere's terminator relief may still catch the Sun: the sine of 2.7 deg, the
 * dip of the horizon seen from a 2 km peak, acos(1737.4 / 1739.4).
 */
export const MOON_HORIZON_DIP = Math.sin((2.7 * Math.PI) / 180);
export const MOON_MIN_VIEW_COS = 0.001;

/**
 * Single-scattering Lommel-Seeliger reflectance `2 * mu0 / (mu0 + mu)` normalized to `1` at normal
 * incidence and view (`mu0 = 1, mu = 1`), with `mu` floored at `MOON_MIN_VIEW_COS`.
 */
export function moonLommelSeeligerReflectance(sunCos: number, viewCos: number): number {
  const mu0 = Math.max(0, sunCos);
  if (mu0 === 0) return 0;
  const mu = Math.max(MOON_MIN_VIEW_COS, viewCos);
  return (2 * mu0) / (mu0 + mu);
}

/** Hermite terminator fade across `[-MOON_HORIZON_DIP, 0]` for unmodelled relief shadows. */
export function moonTerminatorFade(sphereSunDot: number): number {
  const t = Math.max(0, Math.min(1, (sphereSunDot + MOON_HORIZON_DIP) / MOON_HORIZON_DIP));
  return t * t * (3 - 2 * t);
}

export function buildMoonNodeMaterial(
  textures: MoonTextures,
  sunDirWorld: { value: unknown },
  albedoScale: number,
): NodeMaterial {
  const colourMap = texture(textures.colour);
  const normalMap = texture(textures.normal);
  const sunDir = sunDirWorld as unknown as Vec3Node;
  const material = new NodeMaterial();
  material.name = 'cislunarMoon';
  material.fragmentNode = Fn(() => {
    const coord = uv().toVar('moonUv');
    const albedo = colourMap.sample(coord).rgb.mul(albedoScale);
    const up = normalize(positionLocal).toVar('moonUp');
    const { east: rawEast, north } = sphereTangentFrameNodes(up);
    const east = rawEast.toVar('moonEast');
    const tangentNormal = normalMap.sample(coord).xyz.mul(2).sub(1);
    const localNormal = east
      .mul(tangentNormal.x)
      .add(north.mul(tangentNormal.y))
      .add(up.mul(tangentNormal.z));
    const N = normalize(modelWorldMatrix.mul(vec4(localNormal, 0)).xyz).toVar('moonN');
    const sphereNormal = normalize(modelWorldMatrix.mul(vec4(up, 0)).xyz);
    const L = normalize(sunDir).toVar('moonL');
    const V = normalize(cameraPosition.sub(positionWorld));
    const mu0 = max(0.0, dot(N, L)).toVar('moonMu0');
    const mu = max(MOON_MIN_VIEW_COS, dot(N, V));
    const reflectance = mu0.mul(2.0).div(mu0.add(mu));
    const sunlit = smoothstep(-MOON_HORIZON_DIP, 0.0, dot(sphereNormal, L));
    return vec4(albedo.mul(reflectance).mul(sunlit), 1.0);
  })();
  return material;
}
