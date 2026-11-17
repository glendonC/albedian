import { NodeMaterial } from 'three/webgpu';
import {
  Fn,
  asin,
  clamp,
  dFdx,
  dFdy,
  dot,
  float,
  fract,
  max,
  min,
  mix,
  modelViewMatrix,
  normalize,
  positionLocal,
  pow,
  smoothstep,
  sqrt,
  texture,
  vec2,
  vec3,
  vec4,
  vertexStage,
} from 'three/tsl';
import type { BrunetonLut } from '../../brunetonAtmosphere.js';
import {
  EARTH_HEIGHT_OFFSET_M,
  EARTH_HEIGHT_TEX_HEIGHT,
  EARTH_HEIGHT_TEX_WIDTH,
  EARTH_MIN_COS_LAT,
  EARTH_NORTH_STEP_M,
  ETOPO_HIGH_BYTE_SCALE,
  ETOPO_LOW_BYTE_SCALE,
  ICE_SHELF_MIN_RG_HIGH,
  ICE_SHELF_MIN_RG_LOW,
} from './earthReliefMath.js';
import type { EarthTextures, FloatNode, Vec3Node, Vec4Node } from './materialTypes.js';
import {
  sphereTangentFrameNodes,
  viewDirection,
  viewNormal,
  wrapSeamDerivativeNode,
} from './tslSphereFrame.js';

export const CITY_LIGHT_GAIN = 1.25 / 3;

export function decodeEtopoHeightNode(sample: Vec4Node): FloatNode {
  return sample.r
    .mul(ETOPO_HIGH_BYTE_SCALE)
    .add(sample.g.mul(ETOPO_LOW_BYTE_SCALE))
    .sub(EARTH_HEIGHT_OFFSET_M);
}

export function buildEarthNodeMaterial(
  textures: EarthTextures,
  lut: BrunetonLut,
  sunDirView: { value: unknown },
  longitudeAngleNode: (p: Vec3Node) => FloatNode,
): NodeMaterial {
  const day = texture(textures.day);
  const night = texture(textures.night);
  const water = texture(textures.water);
  const height = texture(textures.height);
  const clouds = texture(textures.clouds);
  const sunDir = sunDirView as unknown as Vec3Node;
  const objectDir = vertexStage(normalize(positionLocal));

  const material = new NodeMaterial();
  material.name = 'cislunarEarth';
  material.fragmentNode = Fn(() => {
    const p = normalize(objectDir).toVar('earthP');
    const eqUv = vec2(
      longitudeAngleNode(p).mul(1 / (2 * Math.PI)).add(0.5),
      asin(clamp(p.y, -1.0, 1.0)).mul(1 / Math.PI).add(0.5),
    ).toVar('earthUv');

    const gradX = wrapSeamDerivativeNode(dFdx(eqUv)).toVar('earthGradX');
    const gradY = wrapSeamDerivativeNode(dFdy(eqUv)).toVar('earthGradY');

    const daySample = day.sample(eqUv).grad(gradX, gradY).rgb.toVar('earthDaySample');
    const dayCol = pow(daySample, vec3(1.32));
    const nightLights = night.sample(eqUv).grad(gradX, gradY).rgb;
    const waterMask = water.sample(eqUv).grad(gradX, gradY).r.toVar('earthWaterMask');
    const iceShelf = smoothstep(ICE_SHELF_MIN_RG_LOW, ICE_SHELF_MIN_RG_HIGH, min(daySample.r, daySample.g));
    const openWater = waterMask.mul(float(1).sub(iceShelf)).toVar('earthOpenWater');
    const cloud = clouds.sample(eqUv).grad(gradX, gradY).r.toVar('earthClouds');
    const cloudShadow = clouds.sample(eqUv.sub(vec2(0.0018, 0.0012))).grad(gradX, gradY).r;

    const du = float(1 / EARTH_HEIGHT_TEX_WIDTH);
    const dv = float(1 / EARTH_HEIGHT_TEX_HEIGHT);
    const hWestM = decodeEtopoHeightNode(height.sample(vec2(fract(eqUv.x.sub(du)), eqUv.y)));
    const hEastM = decodeEtopoHeightNode(height.sample(vec2(fract(eqUv.x.add(du)), eqUv.y)));
    const hSouthM = decodeEtopoHeightNode(height.sample(vec2(eqUv.x, clamp(eqUv.y.sub(dv), 0.0, 1.0))));
    const hNorthM = decodeEtopoHeightNode(height.sample(vec2(eqUv.x, clamp(eqUv.y.add(dv), 0.0, 1.0))));
    const land = float(1).sub(waterMask);
    const cosLat = max(EARTH_MIN_COS_LAT, sqrt(max(0.0, float(1).sub(p.y.mul(p.y)))));
    const slopeE = hEastM.sub(hWestM).div(cosLat.mul(2 * EARTH_NORTH_STEP_M)).mul(land);
    const slopeN = hNorthM.sub(hSouthM).div(2 * EARTH_NORTH_STEP_M).mul(land);
    const { east: eastLocal, north: northLocal } = sphereTangentFrameNodes(p);
    const localNormal = normalize(p.sub(eastLocal.mul(slopeE)).sub(northLocal.mul(slopeN)));

    const sphereN = viewNormal().toVar('earthSphereN');
    const N = normalize(modelViewMatrix.mul(vec4(localNormal, 0)).xyz).toVar('earthN');
    const L = normalize(sunDir);
    const V = viewDirection().toVar('earthV');
    const sunDot = dot(sphereN, L).toVar('earthSunDot');
    const surfaceSunDot = dot(N, L).toVar('earthSurfaceSunDot');
    const dayFactor = smoothstep(-0.08, 0.22, sunDot);

    const H = normalize(L.add(V));
    const spec = pow(max(0.0, dot(N, H)), 72.0)
      .mul(openWater)
      .mul(max(0.0, sunDot))
      .mul(float(1).sub(cloud.mul(0.85)));

    const oceanTint = mix(dayCol, vec3(0.025, 0.095, 0.24), openWater.mul(0.55)).toVar('earthOcean');
    const surfaceAlbedo = oceanTint.mul(float(1).sub(cloudShadow.mul(0.48)));
    const cloudLayer = vec3(0.92, 0.95, 1.0).mul(cloud).mul(0.85);
    const litSurface = surfaceAlbedo.mul(max(0.03, surfaceSunDot.mul(1.05)));
    const litClouds = cloudLayer.mul(max(0.03, sunDot.mul(1.05)));
    const litEarth = mix(litSurface, litClouds, clamp(cloud.mul(0.84), 0.0, 1.0))
      .add(vec3(0.42, 0.68, 0.98).mul(spec).mul(0.65));

    const nightEarth = nightLights.mul(CITY_LIGHT_GAIN).mul(float(1).sub(cloud.mul(0.72)));

    const viewDot = dot(sphereN, V).toVar('earthViewDot');
    const fresnel = pow(float(1).sub(max(0.0, viewDot)), 3.8);
    const lutSample = lut.sample(viewDot, sunDot).toVar('earthLut');
    const atmoTint = mix(vec3(0.06, 0.24, 0.64), lutSample.rgb.mul(1.25), 0.68)
      .mul(fresnel)
      .mul(dayFactor.mul(0.94));

    const radiance = mix(nightEarth, litEarth, dayFactor).add(atmoTint);
    return vec4(radiance, 1.0);
  })();
  return material;
}
