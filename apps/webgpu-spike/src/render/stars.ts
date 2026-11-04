import {
  AdditiveBlending,
  BufferAttribute,
  InstancedBufferAttribute,
  InstancedBufferGeometry,
  Mesh,
  NodeMaterial,
  type Node,
} from 'three/webgpu';
import {
  Discard,
  Fn,
  If,
  cameraProjectionMatrix,
  cameraViewport,
  clamp,
  cos,
  dot,
  exp,
  float,
  instancedBufferAttribute,
  length,
  max,
  modelViewMatrix,
  positionGeometry,
  pow,
  select,
  sin,
  sqrt,
  uniform,
  varyingProperty,
  vec3,
  vec4,
} from 'three/tsl';
import { BlackbodyLut, fluxFromMagnitude } from '../color/blackbody';
import { SRGB_CHANNEL_WAVELENGTHS_MICRON } from '../color/extinction';
import type { StarFixture } from '../data/starFixture';
import { AIRY_FIRST_ZERO, DEFAULT_PSF, PSF_ENERGY, type PsfParams } from './psfMath';

export interface StarLayer {
  mesh: Mesh;
  /** Linear exposure applied to every star's flux. */
  exposure: { value: number };
  /** drawingBufferHeight / 1080: PSF parameters are in 1080-line reference pixels. */
  psfScale: { value: number };
  dispose(): void;
}

type FloatNode = Node<'float'>;

/** Bessel J1 for v >= 0 (Numerical Recipes rational approximations, both branches). */
const besselJ1 = Fn(([vInput]: [FloatNode]) => {
  const v = max(vInput, float(1e-6)).toVar('j1v');
  const y = v.mul(v).toVar('j1y');
  const small = v
    .mul(
      float(72362614232.0).add(
        y.mul(
          float(-7895059235.0).add(
            y.mul(float(242396853.1).add(y.mul(float(-2972611.439).add(y.mul(float(15704.4826).add(y.mul(-30.16036606)))))))
          )
        )
      )
    )
    .div(
      float(144725228442.0).add(
        y.mul(
          float(2300535178.0).add(
            y.mul(float(18583304.74).add(y.mul(float(99447.43394).add(y.mul(float(376.9991397).add(y))))))
          )
        )
      )
    );
  const z = float(8.0).div(v).toVar('j1z');
  const zz = z.mul(z).toVar('j1zz');
  const xx = v.sub(2.356194491).toVar('j1xx');
  const p1 = float(1.0).add(
    zz.mul(float(0.183105e-2).add(zz.mul(float(-0.3516396496e-4).add(zz.mul(float(0.2457520174e-5).add(zz.mul(-0.240337019e-6)))))))
  );
  const p2 = float(0.04687499995).add(
    zz.mul(
      float(-0.2002690873e-3).add(
        zz.mul(float(0.8449199096e-5).add(zz.mul(float(-0.88228987e-6).add(zz.mul(0.105787412e-6)))))
      )
    )
  );
  const large = sqrt(float(0.636619772).div(v)).mul(cos(xx).mul(p1).sub(z.mul(sin(xx)).mul(p2)));
  return select(v.lessThan(8.0), small, large);
});

/** Normalized Airy intensity (unit integral over the plane) at radius r for wavenumber k. */
const airy = Fn(([r, k]: [FloatNode, FloatNode]) => {
  const v = k.mul(r).toVar('airyV');
  const shape = select(v.lessThan(1e-3), float(1.0), besselJ1(v).mul(2.0).div(v)).toVar('airyShape');
  return k.mul(k).div(4.0 * Math.PI).mul(shape).mul(shape);
});

/**
 * Analytic-PSF HDR star sprites in TSL.
 *
 * WebGPU point primitives are fixed at 1 px, so `gl_PointSize` starfields (as in
 * SparkPortalRenderer) cannot be ported as-is: each star is an instanced screen-aligned quad
 * sized by the radius at which its own PSF falls below a per-pixel threshold. The fragment
 * evaluates psfMath.ts's energy-normalized PSF: a chromatic Airy core (ring radius scales with
 * wavelength per channel), a Moffat glare halo and four diffraction spikes. Flux is
 * 10^(-0.4 m); color is blackbody chromaticity at unit luminance. Output is additive linear HDR,
 * so accumulation is order independent and needs no sort.
 */
export function createStarLayer(
  fixture: StarFixture,
  lut = new BlackbodyLut(),
  psf: PsfParams = DEFAULT_PSF
): StarLayer {
  const n = fixture.count;
  const radiance = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    lut.sampleInto(fixture.temperaturesK[i]!, radiance, i * 3);
    const flux = fluxFromMagnitude(fixture.magnitudes[i]!);
    radiance[i * 3] = radiance[i * 3]! * flux;
    radiance[i * 3 + 1] = radiance[i * 3 + 1]! * flux;
    radiance[i * 3 + 2] = radiance[i * 3 + 2]! * flux;
  }

  const geometry = new InstancedBufferGeometry();
  geometry.setAttribute(
    'position',
    new BufferAttribute(new Float32Array([-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0]), 3)
  );
  geometry.setIndex([0, 1, 2, 0, 2, 3]);
  geometry.instanceCount = n;

  const centerAttribute = new InstancedBufferAttribute(fixture.positions.slice(), 3);
  const radianceAttribute = new InstancedBufferAttribute(radiance, 3);

  const exposure = uniform(150.0);
  const psfScale = uniform(1.0);
  // Per-pixel linear radiance below which a sprite stops (sets the quad size per star).
  const threshold = 0.0015;
  const beta = 2.5;
  const redScale = SRGB_CHANNEL_WAVELENGTHS_MICRON.r / SRGB_CHANNEL_WAVELENGTHS_MICRON.g;
  const kGreenRef = AIRY_FIRST_ZERO / psf.firstZeroPx;
  // Airy dark-ring radii (zeros of J1) in reference pixels for the widest (red) channel, the
  // relative intensity of the bright ring just inside each one, and the encircled energy there.
  const ringZeros = [3.8317, 7.0156, 10.1735, 13.3237].map((v) => (v / kGreenRef) * redScale);
  const ringPeaks = [1, 0.0175, 0.0042, 0.0016];
  const ringEnergy = [0.838, 0.91, 0.938, 0.952];
  const corePeakCoeff = (PSF_ENERGY.core * kGreenRef * kGreenRef) / (4 * Math.PI);
  const haloPeakCoeff = (PSF_ENERGY.halo * (beta - 1)) / (Math.PI * psf.haloAlphaPx ** 2);
  const spikeCoeff =
    PSF_ENERGY.spike / (2 * Math.sqrt(2 * Math.PI) * psf.spikeWidthPx * Math.PI * psf.spikeLengthPx);

  const vUvRef = varyingProperty('vec2', 'vStarUvRef');
  const vRadiance = varyingProperty('vec3', 'vStarRadiance');
  const vRadius = varyingProperty('float', 'vStarRadius');
  const vCoreGain = varyingProperty('float', 'vStarCoreGain');

  const vertexNode = Fn(() => {
    const center = instancedBufferAttribute<'vec3'>(centerAttribute, 'vec3');
    const rad = instancedBufferAttribute<'vec3'>(radianceAttribute, 'vec3');
    const viewPos = modelViewMatrix.mul(vec4(center, 1.0)).toVar('starViewPos');
    const clip = cameraProjectionMatrix.mul(viewPos).toVar('starClip');

    // Visible radius in reference pixels from this star's own halo and spike fall-off.
    const fe = dot(rad, vec3(0.2126729, 0.7151522, 0.072175)).mul(exposure).toVar('starFe');
    const haloPeak = fe.mul(haloPeakCoeff);
    const rHalo = select(
      haloPeak.greaterThan(threshold),
      pow(haloPeak.div(threshold), 1 / (2 * beta)).mul(psf.haloAlphaPx),
      float(0)
    );
    const spikeA = fe.mul(spikeCoeff);
    const rSpike = select(spikeA.greaterThan(threshold), sqrt(spikeA.div(threshold)).mul(psf.spikeLengthPx), float(0));
    // Keep only the Airy rings that reach the display threshold; scale the truncated core by
    // 1 / encircled energy so the star's integrated flux is unchanged (flux-conserving cut).
    const corePeak = fe.mul(corePeakCoeff).toVar('starCorePeak');
    const coreR = float(ringZeros[0]! * 1.15).toVar('starCoreR');
    const coreGain = float(1 / ringEnergy[0]!).toVar('starCoreGain');
    for (let ring = 1; ring < 4; ring++) {
      If(corePeak.mul(ringPeaks[ring]!).greaterThan(threshold), () => {
        coreR.assign(ringZeros[ring]!);
        coreGain.assign(1 / ringEnergy[ring]!);
      });
    }
    const radiusRef = clamp(max(coreR, max(rHalo, rSpike)), 0.5, 220.0).toVar('starRadiusRef');

    const offsetRef = positionGeometry.xy.mul(radiusRef).toVar('starOffsetRef');
    vUvRef.assign(offsetRef);
    vRadius.assign(radiusRef);
    vCoreGain.assign(coreGain);
    vRadiance.assign(rad);
    const offsetNdc = offsetRef.mul(psfScale).mul(2.0).div(cameraViewport.zw);
    return clip.add(vec4(offsetNdc.mul(clip.w), 0.0, 0.0));
  })();

  const kGreen = AIRY_FIRST_ZERO / psf.firstZeroPx;
  const kRed = kGreen * (SRGB_CHANNEL_WAVELENGTHS_MICRON.g / SRGB_CHANNEL_WAVELENGTHS_MICRON.r);
  const kBlue = kGreen * (SRGB_CHANNEL_WAVELENGTHS_MICRON.g / SRGB_CHANNEL_WAVELENGTHS_MICRON.b);
  const spikeW2 = 2 * psf.spikeWidthPx * psf.spikeWidthPx;
  const spikeL2 = psf.spikeLengthPx * psf.spikeLengthPx;

  const colorNode = Fn(() => {
    const r = length(vUvRef).toVar('starR');
    If(r.greaterThan(vRadius), () => {
      Discard();
    });
    const core = vec3(airy(r, float(kRed)), airy(r, float(kGreen)), airy(r, float(kBlue)));
    const halo = pow(r.mul(r).div(psf.haloAlphaPx * psf.haloAlphaPx).add(1.0), -beta).mul(
      (beta - 1) / (Math.PI * psf.haloAlphaPx * psf.haloAlphaPx)
    );
    // Spider vanes at 45 degrees to the sensor axes.
    const sx = vUvRef.x.add(vUvRef.y).mul(Math.SQRT1_2);
    const sy = vUvRef.y.sub(vUvRef.x).mul(Math.SQRT1_2);
    const lineA = exp(sy.mul(sy).negate().div(spikeW2)).div(sx.mul(sx).div(spikeL2).add(1.0));
    const lineB = exp(sx.mul(sx).negate().div(spikeW2)).div(sy.mul(sy).div(spikeL2).add(1.0));
    const spikes = lineA.add(lineB).mul(1 / (2 * Math.sqrt(2 * Math.PI) * psf.spikeWidthPx * Math.PI * psf.spikeLengthPx));
    const psfValue = core
      .mul(vCoreGain.mul(PSF_ENERGY.core))
      .add(vec3(halo.mul(PSF_ENERGY.halo).add(spikes.mul(PSF_ENERGY.spike))));
    return vec4(vRadiance.mul(psfValue).mul(exposure), 1.0);
  })();

  const material = new NodeMaterial();
  material.name = 'spike_analyticPsfStars';
  material.vertexNode = vertexNode;
  material.colorNode = colorNode;
  material.transparent = true;
  material.depthWrite = false;
  material.depthTest = true;
  material.blending = AdditiveBlending;
  material.fog = false;

  const mesh = new Mesh(geometry, material);
  mesh.name = 'synthetic-star-fixture';
  // Background first, so splats alpha-blend over (and dim) the stars behind them.
  mesh.renderOrder = -10;
  // Instanced quad geometry has a tiny bounding sphere; culling happens per vertex instead.
  mesh.frustumCulled = false;

  return {
    mesh,
    exposure,
    psfScale,
    dispose() {
      geometry.dispose();
      material.dispose();
    },
  };
}

/** For the HUD and docs. */
export function describePsf(psf: PsfParams = DEFAULT_PSF): string {
  return `Airy core ${PSF_ENERGY.core} (first ring ${psf.firstZeroPx} px @1080, chromatic), Moffat halo ${PSF_ENERGY.halo} (alpha ${psf.haloAlphaPx} px), spikes ${PSF_ENERGY.spike}`;
}
