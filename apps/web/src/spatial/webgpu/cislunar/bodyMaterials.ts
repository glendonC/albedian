import { AdditiveBlending, BackSide, NodeMaterial, type Node, type Texture } from 'three/webgpu';
import {
  Fn,
  clamp,
  dFdx,
  dFdy,
  dot,
  float,
  fract,
  max,
  mix,
  normalViewGeometry,
  normalize,
  positionLocal,
  positionView,
  pow,
  smoothstep,
  texture,
  uv,
  vec2,
  vec3,
  vec4,
  vertexStage,
  asin,
  atan,
} from 'three/tsl';
import { gaussian, type BrunetonLut } from '../brunetonAtmosphere.js';

type Vec3Node = Node<'vec3'>;

/**
 * Earth, its atmosphere shell and the Moon of the cislunar landing scene, ported from the GLSL
 * `ShaderMaterial`s in SparkPortalRenderer.ts. Lighting vectors stay in view space, as there:
 * the composition lights each body from a fixed direction relative to the camera. Materials
 * write linear radiance; AgX in the post chain is the only tone mapping.
 */

export interface EarthTextures {
  /** NASA Blue Marble Next Generation, November 2004 (sRGB) */
  day: Texture;
  /** NASA Black Marble 2016 city lights (sRGB) */
  night: Texture;
  /** Ocean mask (linear) */
  specular: Texture;
  /** Cloud cover (sRGB, red channel used) */
  clouds: Texture;
}

/** View-space unit normal as `normalize(normalMatrix * normal)`; not flipped on back faces. */
const viewNormal = () => normalize(normalViewGeometry);
/** View-space direction from the surface to the camera, as `normalize(-mvPosition.xyz)`. */
const viewDirection = () => normalize(vertexStage(normalize(positionView.negate())));

export function createEarthMaterial(textures: EarthTextures, lut: BrunetonLut, sunDirView: { value: unknown }): NodeMaterial {
  const day = texture(textures.day);
  const night = texture(textures.night);
  const specular = texture(textures.specular);
  const clouds = texture(textures.clouds);
  const sunDir = sunDirView as unknown as Vec3Node;
  const objectDir = vertexStage(normalize(positionLocal));

  const material = new NodeMaterial();
  material.name = 'cislunarEarth';
  material.fragmentNode = Fn(() => {
    // Sampling rotated so the Mediterranean, Sahara and Atlantic face the camera and the
    // equirectangular polar pinch sits on the far limb (the GLSL mat3 globeTilt, by rows)
    const raw = normalize(objectDir);
    const p = normalize(
      vec3(
        dot(vec3(0.866, 0.0, 0.5), raw),
        dot(vec3(0.25, 0.866, -0.433), raw),
        dot(vec3(-0.433, 0.5, 0.75), raw)
      )
    ).toVar('earthP');
    const eqUv = vec2(
      atan(p.z, p.x.negate()).mul(1 / (2 * Math.PI)).add(0.5),
      asin(clamp(p.y, -1.0, 1.0)).mul(1 / Math.PI).add(0.5)
    ).toVar('earthUv');
    // atan wraps at the texture seam; wrapping the u derivative keeps full mip detail there
    const rawDx = dFdx(eqUv);
    const rawDy = dFdy(eqUv);
    const gradX = vec2(fract(rawDx.x.add(0.5)).sub(0.5), rawDx.y).toVar('earthGradX');
    const gradY = vec2(fract(rawDy.x.add(0.5)).sub(0.5), rawDy.y).toVar('earthGradY');

    const dayCol = pow(day.sample(eqUv).grad(gradX, gradY).rgb, vec3(1.32));
    const nightLights = night.sample(eqUv).grad(gradX, gradY).rgb;
    const specMask = specular.sample(eqUv).grad(gradX, gradY).r.toVar('earthSpecMask');
    const cloud = clouds.sample(eqUv).grad(gradX, gradY).r.toVar('earthClouds');
    const cloudShadow = clouds.sample(eqUv.sub(vec2(0.0018, 0.0012))).grad(gradX, gradY).r;

    const N = viewNormal().toVar('earthN');
    const L = normalize(sunDir);
    const V = viewDirection().toVar('earthV');
    const sunDot = dot(N, L).toVar('earthSunDot');
    const dayFactor = smoothstep(-0.08, 0.22, sunDot);
    // Narrow warm band along the terminator
    const twilightFactor = gaussian(sunDot.add(0.02).mul(9.0)).toVar('earthTwilight');

    const H = normalize(L.add(V));
    const spec = pow(max(0.0, dot(N, H)), 72.0).mul(specMask).mul(max(0.0, sunDot)).mul(float(1).sub(cloud.mul(0.85)));

    const oceanTint = mix(dayCol, vec3(0.025, 0.095, 0.24), specMask.mul(0.55)).toVar('earthOcean');
    const surfaceAlbedo = oceanTint.mul(float(1).sub(cloudShadow.mul(0.48)));
    const cloudLayer = vec3(0.92, 0.95, 1.0).mul(cloud).mul(twilightFactor.mul(0.25).add(0.85));
    const litEarth = mix(surfaceAlbedo, cloudLayer, clamp(cloud.mul(0.84), 0.0, 1.0))
      .mul(max(0.03, sunDot.mul(1.05)))
      .add(vec3(0.42, 0.68, 0.98).mul(spec).mul(0.65));

    const twilightGlow = vec3(0.92, 0.36, 0.1).mul(twilightFactor).mul(cloud.mul(0.32).add(0.2));
    // NASA Black Marble 2016 city light (dim base layer removed at bake time), dimmed under cloud
    const cityLightGain = 1.25;
    const cityGlow = nightLights.mul(cityLightGain).mul(float(1).sub(cloud.mul(0.72)));
    const darkContinents = oceanTint
      .mul(vec3(0.015, 0.034, 0.075))
      .add(vec3(0.005, 0.014, 0.032).mul(float(1).sub(specMask.mul(0.5))));
    const nightEarth = darkContinents.add(cityGlow).add(vec3(0.012, 0.026, 0.055).mul(cloud));

    const viewDot = dot(N, V).toVar('earthViewDot');
    const fresnel = pow(float(1).sub(max(0.0, viewDot)), 3.8);
    const lutSample = lut.sample(viewDot, sunDot).toVar('earthLut');
    const atmoTint = mix(vec3(0.06, 0.24, 0.64), lutSample.rgb.mul(1.25), 0.68)
      .mul(fresnel)
      .mul(dayFactor.mul(0.52).add(0.42));
    const lutTwilight = lutSample.rgb.mul(twilightFactor).mul(cloud.mul(0.36).add(0.28));

    // The GLSL compressed this with x / (1 + 0.56 x); AgX now does all the tone mapping
    const radiance = mix(nightEarth, litEarth, dayFactor).add(mix(twilightGlow, lutTwilight, 0.65)).add(atmoTint);
    return vec4(radiance, 1.0);
  })();
  return material;
}

/** Additive back-side limb glow around the Earth, from the same LUT. */
export function createAtmosphereShellMaterial(lut: BrunetonLut, sunDirView: { value: unknown }): NodeMaterial {
  const sunDir = sunDirView as unknown as Vec3Node;
  const material = new NodeMaterial();
  material.name = 'cislunarAtmosphereShell';
  material.side = BackSide;
  material.transparent = true;
  material.depthWrite = false;
  material.blending = AdditiveBlending;
  material.fragmentNode = Fn(() => {
    const N = viewNormal().toVar('atmoN');
    const viewDot = dot(N, viewDirection()).toVar('atmoViewDot');
    const limbIntensity = gaussian(viewDot.add(0.14).mul(5.2)).toVar('atmoLimb');
    const sunAlign = dot(N.negate(), normalize(sunDir)).toVar('atmoSunAlign');
    const sunOrient = smoothstep(-0.2, 0.65, sunAlign).mul(0.78).add(0.22);
    const sunriseArc = gaussian(sunAlign.sub(0.32).mul(Math.sqrt(6.5))).mul(0.38);
    const lutLimb = lut.sample(clamp(viewDot.add(0.16), -0.35, 1.0), sunAlign);
    const baseRayleigh = mix(vec3(0.14, 0.5, 0.96), vec3(0.52, 0.78, 1.0), sunriseArc);
    const rayleighCol = mix(baseRayleigh, lutLimb.rgb.mul(1.35), 0.62).mul(limbIntensity).mul(sunOrient).mul(0.98);
    // Additive blending scales rgb by alpha, as THREE.AdditiveBlending did in WebGL
    return vec4(rayleighCol, clamp(limbIntensity.mul(0.86), 0.0, 0.84));
  })();
  return material;
}

export interface MoonTextures {
  /** LRO colour albedo (sRGB) */
  albedo: Texture;
  /** LOLA-derived relief (linear, red channel used) */
  relief: Texture;
}

/**
 * Lommel-Seeliger regolith shading with an opposition surge and a faint earthshine on the
 * night side, bump-lit from the relief map.
 *
 * The GLSL also added four octaves of hashed value noise as "micro-crater" relief on top of the
 * LOLA data. That invented terrain the map does not have, so the port leaves it out.
 */
export function createMoonMaterial(textures: MoonTextures, sunDirView: { value: unknown }): NodeMaterial {
  const albedoMap = texture(textures.albedo);
  const relief = texture(textures.relief);
  const sunDir = sunDirView as unknown as Vec3Node;
  const material = new NodeMaterial();
  material.name = 'cislunarMoon';
  material.fragmentNode = Fn(() => {
    const coord = uv().toVar('moonUv');
    const albedo = albedoMap.sample(coord).rgb.toVar('moonAlbedo');
    // UV step of the GLSL, kept for the same bump strength: it assumed a 2048x1024 map, so on the
    // 1024x512 relief texture it spans half a texel
    const texel = vec2(1 / 2048, 1 / 1024);
    const hL = relief.sample(coord.sub(vec2(texel.x, 0.0))).r;
    const hR = relief.sample(coord.add(vec2(texel.x, 0.0))).r;
    const hD = relief.sample(coord.sub(vec2(0.0, texel.y))).r;
    const hU = relief.sample(coord.add(vec2(0.0, texel.y))).r;
    const slopeX = hL.sub(hR).toVar('moonSlopeX');
    const bumpOffset = vec3(slopeX.mul(3.8), hD.sub(hU).mul(3.8), 0.0);

    const geometricN = viewNormal().toVar('moonGeomN');
    const N = normalize(geometricN.add(bumpOffset.mul(0.62)));
    const L = normalize(sunDir).toVar('moonL');
    const V = viewDirection().toVar('moonV');
    const rawNdotL = dot(geometricN, L);
    const NdotL = max(0.0, dot(N, L)).toVar('moonNdotL');
    const NdotV = max(0.05, dot(N, V));

    const lommelSeeliger = NdotL.div(NdotL.add(NdotV.mul(0.62)).add(0.001));
    const oppositionSurge = pow(max(0.0, dot(V, L)), 6.0).mul(0.32).add(1.0);
    const terminatorMask = smoothstep(-0.08, 0.12, rawNdotL.add(slopeX.mul(0.45))).toVar('moonTerminator');
    const regolithReflectance = lommelSeeliger.mul(terminatorMask).mul(oppositionSurge).mul(1.68);

    const rimFresnel = pow(float(1).sub(max(0.0, dot(geometricN, V))), 2.4);
    const earthshine = pow(albedo, vec3(1.1))
      .mul(vec3(0.024, 0.046, 0.088))
      .mul(float(1).sub(terminatorMask.mul(0.85)))
      .mul(rimFresnel.mul(0.6).add(0.7));
    const sunlitMoon = pow(albedo, vec3(1.22)).mul(vec3(1.06, 1.01, 0.93)).mul(regolithReflectance);
    return vec4(sunlitMoon.add(earthshine), 1.0);
  })();
  return material;
}
