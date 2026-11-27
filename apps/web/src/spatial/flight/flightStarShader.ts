import type { StarView } from './starView.js';
import { BLOOM_YIELD_RAMP_PX } from './arrival.js';

/**
 * The flight layer's shaders (WebGL 2). The vertex shader is `projectPlacementThroughWcs` (packages/camera-rig
 * flight3d.ts) in float32 on positions the CPU rebased in float64 on the camera's reference point (the floating
 * origin; flight3d.test.ts checks this very arithmetic within 0.05 px plus the floating origin's own float32 bound,
 * floatingOriginBoundRad), followed by the photo's frame on the window.
 * A star's magnitude is its catalog magnitude seen from Earth moved by the inverse square law to the camera's
 * distance. The look per magnitude (size, opacity, gain, spikes below magnitude 1, the core and halo) is the app's
 * catalog star look, copied from catalogStars/field/starPointMaterial.ts so the flight's stars read as the sky view's.
 */

export const FLIGHT_STAR_VERTEX = `#version 300 es
precision highp float;
in vec3 aPos;
in float aPlaced;
in float aEarthPc;
in float aMag;
in vec3 aTint;
uniform vec3 uEye;
uniform vec3 uRowX;
uniform vec3 uRowY;
uniform vec3 uRowZ;
uniform vec3 uPix;
uniform vec3 uLens;
uniform vec3 uAffX;
uniform vec3 uAffY;
uniform vec2 uView;
uniform float uLimit;
uniform float uPixelRatio;
uniform float uGain;
out vec3 vTint;
out float vAlpha;
out float vGain;
out float vSpike;
void main() {
  vec3 v = aPlaced > 0.5 ? aPos - uEye : aPos;
  float len = length(v);
  float z = dot(uRowZ, v);
  bool hidden = !(len > 0.0) || z <= 1e-6 * len;
  float safeZ = hidden ? 1.0 : z;
  float xr = dot(uRowX, v) / safeZ;
  float yr = dot(uRowY, v) / safeZ;
  float r2 = xr * xr + yr * yr;
  hidden = hidden || (uLens.z > 0.0 && sqrt(r2) >= uLens.z);
  float k = 1.0 + uLens.x * r2 + uLens.y * r2 * r2;
  float u = uPix.x + xr * k / uPix.z;
  float w = uPix.y - yr * k / uPix.z;
  vec2 s = vec2(dot(uAffX, vec3(u, w, 1.0)), dot(uAffY, vec3(u, w, 1.0)));
  // log10(x) = log2(x) * log10(2)
  float m = aPlaced > 0.5 ? aMag + 5.0 * log2(len / aEarthPc) * 0.30102999566 : aMag;
  float fade = clamp((uLimit - m) / 0.3, 0.0, 1.0);
  float rel = pow(10.0, -0.4 * (m - 6.0));
  vAlpha = clamp(sqrt(rel), 0.05, 1.0) * fade * uGain * min(1.0, pow(1.9 * pow(rel, 0.3) / 1.5, 2.0));
  vGain = clamp(pow(rel, 0.18), 1.0, 3.5);
  vTint = aTint;
  vSpike = m < 1.0 ? 1.0 : 0.0;
  float size = clamp(1.9 * pow(rel, 0.3), 1.5, 22.0) * mix(1.0, 2.2, vSpike);
  gl_PointSize = (hidden || fade <= 0.0) ? 0.0 : size * uPixelRatio;
  gl_Position = hidden ? vec4(2.0, 2.0, 2.0, 1.0) : vec4(s.x / uView.x * 2.0 - 1.0, 1.0 - s.y / uView.y * 2.0, 0.0, 1.0);
}`;

export const FLIGHT_STAR_FRAGMENT = `#version 300 es
precision highp float;
in vec3 vTint;
in float vAlpha;
in float vGain;
in float vSpike;
// flight-stars-finish-one: 1 when the stars are finished, so the catalog tint is taken to linear light as the predicted
// sky's points take it (tilePointsMesh.ts); 0 in display light, as before
uniform float uLinear;
out vec4 outColor;
void main() {
  vec2 uv = gl_PointCoord * 2.0 - 1.0;
  float d = length(uv);
  if (d > 1.0) discard;
  vec3 tint = uLinear > 0.5 ? mix(vTint / 12.92, pow((vTint + 0.055) / 1.055, vec3(2.4)), step(0.04045, vTint)) : vTint;
  float r = d * mix(1.0, 2.2, vSpike);
  float core = exp(-r * r * 3.2);
  float halo = exp(-r * 3.0) * 0.3;
  float spikes = 0.0;
  if (vSpike > 0.5) {
    float sx = exp(-abs(uv.y) * 42.0) * exp(-abs(uv.x) * 3.8);
    float sy = exp(-abs(uv.x) * 42.0) * exp(-abs(uv.y) * 3.8);
    spikes = (sx + sy) * 0.55;
  }
  float intensity = (core + halo + spikes) * vAlpha * vGain;
  vec3 col = mix(tint, vec3(1.0), clamp(core * 0.6, 0.0, 1.0)) * intensity;
  outColor = vec4(col, 1.0);
}`;

/** The uniforms the vertex shader takes for a camera, all from float64 values cast once. */
export interface FlightStarUniforms {
  uEye: [number, number, number];
  uRowX: [number, number, number];
  uRowY: [number, number, number];
  uRowZ: [number, number, number];
  uPix: [number, number, number];
  uLens: [number, number, number];
  uAffX: [number, number, number];
  uAffY: [number, number, number];
  uView: [number, number];
}

export function flightStarUniforms(view: StarView): FlightStarUniforms {
  const m = view.storedToScreen;
  // The camera's offset from the reference, in float64 before the cast (the floating origin)
  const eye: [number, number, number] = [
    view.observerPc[0] - view.referencePc[0],
    view.observerPc[1] - view.referencePc[1],
    view.observerPc[2] - view.referencePc[2],
  ];
  return {
    uEye: eye,
    uRowX: [...view.rows.x],
    uRowY: [...view.rows.y],
    uRowZ: [...view.rows.z],
    uPix: [view.crpixX, view.crpixY, view.scaleRadPerPx],
    uLens: [view.k1, view.k2, Number.isFinite(view.foldRad) ? view.foldRad : -1],
    uAffX: [m[0], m[1], m[2]],
    uAffY: [m[3], m[4], m[5]],
    uView: [view.viewportWidthPx, view.viewportHeightPx],
  };
}

/**
 * arrival-star-1: the bloom of the star flown to, one quad centred on the star (a point sprite cannot reach the cap's
 * 480 px on every GPU). Its glow is `arrivalGlow` (arrival.ts), in the star's catalog tint, added as light like the
 * stars; the star's own dot stays the star pass's. near-star-light-one: its white-hot centre spans uCore of it over a
 * wide glare of fill uGlare, and the modelled disc (uDisc of its radius, 0 for none) is added in uDiscTint with a
 * pixel's soft edge. From BLOOM_YIELD_RAMP_PX above uYieldY (the phone's words; negative for none) down, the glow gives way to
 * arrival-star-1's at its capped size (`arrivalGlowYielded`).
 */
export const ARRIVAL_BLOOM_VERTEX = `#version 300 es
precision highp float;
in vec2 aCorner;
uniform vec2 uCenter;
uniform float uRadius;
uniform vec2 uView;
out vec2 vCorner;
void main() {
  vCorner = aCorner;
  vec2 p = uCenter + aCorner * uRadius;
  gl_Position = vec4(p.x / uView.x * 2.0 - 1.0, 1.0 - p.y / uView.y * 2.0, 0.0, 1.0);
}`;

export const ARRIVAL_BLOOM_FRAGMENT = `#version 300 es
precision highp float;
in vec2 vCorner;
uniform vec3 uTint;
uniform float uCore;
uniform float uDisc;
uniform float uRadius;
uniform vec3 uDiscTint;
uniform float uGlare;
uniform vec2 uCenter;
uniform float uYieldY;
out vec4 outColor;
void main() {
  float q = length(vCorner);
  if (q >= 1.0) discard;
  float g = q / (0.32 * uCore);
  // The wide glare falls to 1/e at half the radius and to nothing at the edge (exp(-4) = 0.0183156389)
  float wide = (exp(-q * q * 4.0) - 0.0183156389) / (1.0 - 0.0183156389);
  float grown = (0.9 - uGlare) * exp(-g * g) + uGlare * wide + 0.25 * pow(1.0 - q, 3.0);
  // Under the phone's words: arrival-star-1's bloom at its capped size, the held centre's
  float qc = q / uCore;
  float gc = qc / 0.32;
  float capped = qc < 1.0 ? 0.9 * exp(-gc * gc) + 0.25 * pow(max(1.0 - qc, 0.0), 3.0) : 0.0;
  float yPx = uCenter.y + vCorner.y * uRadius;
  float under = uYieldY >= 0.0 ? clamp((yPx - uYieldY) / ${BLOOM_YIELD_RAMP_PX.toFixed(1)} + 1.0, 0.0, 1.0) : 0.0;
  float glow = mix(grown, capped, under);
  // The disc's edge over one CSS pixel; none where it has no disc
  float disc = uDisc > 0.0 ? clamp((uDisc - q) * uRadius + 0.5, 0.0, 1.0) : 0.0;
  outColor = vec4(uTint * glow + uDiscTint * disc, 1.0);
}`;
