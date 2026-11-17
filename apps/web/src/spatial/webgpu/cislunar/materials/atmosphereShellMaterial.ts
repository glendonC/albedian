import { AdditiveBlending, BackSide, NodeMaterial } from 'three/webgpu';
import { Fn, clamp, dot, mix, normalize, smoothstep, vec3, vec4 } from 'three/tsl';
import { gaussian, type BrunetonLut } from '../../brunetonAtmosphere.js';
import type { Vec3Node } from './materialTypes.js';
import { viewDirection, viewNormal } from './tslSphereFrame.js';

export const ATMO_LIMB_ALPHA_MAX = 0.84;

/** Additive back-side limb glow around the Earth, sampled from the shared atmosphere LUT. */
export function buildAtmosphereShellNodeMaterial(
  lut: BrunetonLut,
  sunDirView: { value: unknown },
): NodeMaterial {
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
    const sunAlign = dot(N, normalize(sunDir)).toVar('atmoSunAlign');
    const sunOrient = smoothstep(-0.2, 0.65, sunAlign);
    const sunriseArc = gaussian(sunAlign.sub(0.32).mul(Math.sqrt(6.5))).mul(0.38);
    const lutLimb = lut.sample(clamp(viewDot.add(0.16), -0.35, 1.0), sunAlign);
    const baseRayleigh = mix(vec3(0.14, 0.5, 0.96), vec3(0.52, 0.78, 1.0), sunriseArc);
    const rayleighCol = mix(baseRayleigh, lutLimb.rgb.mul(1.35), 0.62)
      .mul(limbIntensity)
      .mul(sunOrient)
      .mul(0.98);
    return vec4(rayleighCol, clamp(limbIntensity.mul(0.86), 0.0, ATMO_LIMB_ALPHA_MAX));
  })();
  return material;
}
