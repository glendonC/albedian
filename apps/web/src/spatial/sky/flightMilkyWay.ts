import type { Vec3 } from '../catalogStars/healpix.js';
import {
  applyRows,
  icrsToPanoramaRows,
  plateCarreeUv,
  sampleOffsetTable,
  type Mat3Rows,
  type PanoramaRegistration,
} from './skyRegistration.js';

/**
 * The Milky Way in flight (milkyway-flight-one, ruled by the lead at 16:18 on 24 Nov; reports/LOOK/milkyway-flight-one.md):
 * the baked starless glow (eso_milkyway_diffuse_6k.webp, made from ESO's photograph by scripts/bake_starless_panorama.py,
 * its every catalog star to magnitude 7 removed and filled from its surroundings) at full level, so the only stars on
 * screen are the catalog's, placed and lit from where the camera is. At a star it is drawn through the photo's solve as
 * Real sky draws ESO's photograph (RealSkyLayer); while steering it is a dome turned with the camera (FLIGHT_DOME_*).
 * The bake puts Venus and Jupiter back as photographed in 2009 and 2008; seen from another star they would read as two
 * stars, so the flight's copy fills their windows from the glow around them. Nothing is added.
 */

export const FLIGHT_MILKY_WAY_URL = '/textures/eso_milkyway_diffuse_6k.webp';

/** The disclosure's line, as ruled (15:34 and 16:18 on 24 Nov). */
export const FLIGHT_MILKY_WAY_LINE = "Milky Way: ESO/S. Brunier's photograph as seen from Earth, with its own stars removed.";

export const GLOW_SIZE_PX = { width: 6000, height: 3000 } as const;

/** The Credits page's line for what the flight does to ESO's photograph (eso0932a's terms ask that changes be indicated). */
export function milkyWayInFlightChanges(): string {
  return 'In the flight, the copy made from it with its own stars removed is drawn in its place at its own level, ungraded, with Venus and Jupiter filled from the copy around them; while you steer it is drawn on a sky turned with the camera.';
}

export interface PlanetWindow {
  readonly name: string;
  readonly xPx: number;
  readonly yPx: number;
  readonly radiusPx: number;
}

/**
 * Where the bake put the planets back, in the glow's pixels (row 0 at the top), as its own planet_windows computes them
 * [tested: .lanetools/mw/planet_windows.py in the LOOK lane, on the shipped panorama and registration].
 */
export const PLANET_WINDOWS: readonly PlanetWindow[] = [
  { name: 'Venus', xPx: 1851.19, yPx: 2472.21, radiusPx: 12 },
  { name: 'Jupiter', xPx: 2776.02, yPx: 1775.73, radiusPx: 10 },
];

const wrapHalf = (d: number) => d - Math.round(d);

/** The planet window a texture coordinate falls in (u to the right, wrapping; v = 1 at the top row), or none. */
export function planetWindowAt(u: number, v: number): PlanetWindow | null {
  for (const w of PLANET_WINDOWS) {
    const dx = wrapHalf(u - w.xPx / GLOW_SIZE_PX.width) * GLOW_SIZE_PX.width;
    const dy = (1 - v) * GLOW_SIZE_PX.height - w.yPx;
    if (Math.hypot(dx, dy) <= w.radiusPx) return w;
  }
  return null;
}

const MIN_POLE_COS_LAT = 1e-3;

/** The glow's texture coordinate for an ICRS direction: the same mapping Real sky uses (realSkyGrid's panoramaUvOfIcrs). */
export function glowUvOfIcrs(icrs: Vec3, registration: PanoramaRegistration): [number, number] {
  const p = applyRows(icrsToPanoramaRows(registration), icrs);
  const [u, v] = plateCarreeUv(p);
  const [east, north] = sampleOffsetTable(registration.offsetTable, u, v);
  const cosLat = Math.max(Math.hypot(p[0], p[1]), MIN_POLE_COS_LAT);
  return [u - east / (registration.imageWidth * cosLat), v + (north - registration.yOffsetPx) / registration.imageHeight];
}

/** The flight camera's fields the dome needs (a StarView has them). */
export interface DomeCamera {
  readonly rows: { readonly x: Vec3; readonly y: Vec3; readonly z: Vec3 };
  readonly crpixX: number;
  readonly crpixY: number;
  readonly scaleRadPerPx: number;
  readonly k1: number;
  readonly k2: number;
  /** Stored photo pixel to window CSS pixel: [a, b, c, d, e, f] with x = a u + b w + c, y = d u + e w + f. */
  readonly storedToScreen: readonly [number, number, number, number, number, number];
}

/** The lens iterations the dome runs to undo the solve's radial distortion, on the CPU and in the shader. */
export const DOME_LENS_ITERATIONS = 3;

/** The window's inverse affine (CSS pixel to stored photo pixel), as two rows. */
export function screenToStoredRows(m: DomeCamera['storedToScreen']): [Vec3, Vec3] {
  const det = m[0] * m[4] - m[1] * m[3];
  return [
    [m[4] / det, -m[1] / det, (m[1] * m[5] - m[4] * m[2]) / det],
    [-m[3] / det, m[0] / det, (m[3] * m[2] - m[0] * m[5]) / det],
  ];
}

/**
 * The ICRS direction a window pixel looks along: the flight's projection (flightStarShader.ts) undone: the window's
 * affine, the tangent plane, the lens by DOME_LENS_ITERATIONS fixed-point steps, then the camera's rows.
 */
export function domeDirectionAt(camera: DomeCamera, xPx: number, yPx: number): Vec3 {
  const [rx, ry] = screenToStoredRows(camera.storedToScreen);
  const u = rx[0] * xPx + rx[1] * yPx + rx[2];
  const w = ry[0] * xPx + ry[1] * yPx + ry[2];
  const xd = (u - camera.crpixX) * camera.scaleRadPerPx;
  const yd = (camera.crpixY - w) * camera.scaleRadPerPx;
  let xr = xd;
  let yr = yd;
  for (let i = 0; i < DOME_LENS_ITERATIONS; i++) {
    const r2 = xr * xr + yr * yr;
    const k = 1 + camera.k1 * r2 + camera.k2 * r2 * r2;
    xr = xd / k;
    yr = yd / k;
  }
  const { x, y, z } = camera.rows;
  const d: Vec3 = [x[0] * xr + y[0] * yr + z[0], x[1] * xr + y[1] * yr + z[1], x[2] * xr + y[2] * yr + z[2]];
  const n = Math.hypot(d[0], d[1], d[2]);
  return [d[0] / n, d[1] / n, d[2] / n];
}

const f6 = (n: number) => n.toFixed(6);
const PLANETS_GLSL = PLANET_WINDOWS.map(
  (w) => `vec3(${f6(w.xPx / GLOW_SIZE_PX.width)}, ${f6(1 - w.yPx / GLOW_SIZE_PX.height)}, ${w.radiusPx.toFixed(1)})`
).join(', ');

/** GLSL: the glow at a texture coordinate, with the planet windows filled from a ring of eight samples outside them. */
const FILL_PLANETS_GLSL = `
const vec2 GLOW_SIZE = vec2(${GLOW_SIZE_PX.width.toFixed(1)}, ${GLOW_SIZE_PX.height.toFixed(1)});
const vec3 PLANETS[${PLANET_WINDOWS.length}] = vec3[${PLANET_WINDOWS.length}](${PLANETS_GLSL});
vec3 fillPlanets(sampler2D glow, vec2 uv, vec3 base) {
  for (int i = 0; i < ${PLANET_WINDOWS.length}; i++) {
    vec2 c = PLANETS[i].xy;
    float radiusPx = PLANETS[i].z;
    vec2 dPx = vec2((fract(uv.x - c.x + 0.5) - 0.5) * GLOW_SIZE.x, (uv.y - c.y) * GLOW_SIZE.y);
    if (length(dPx) <= radiusPx) {
      float ringPx = 2.0 * radiusPx;
      vec3 sum = vec3(0.0);
      for (int k = 0; k < 8; k++) {
        float a = float(k) * 0.7853981633974483;
        sum += textureLod(glow, c + vec2(cos(a), sin(a)) * ringPx / GLOW_SIZE, 0.0).rgb;
      }
      return sum / 8.0;
    }
  }
  return base;
}`;

/** Fragment shader for Real sky's grid in flight (RealSkyLayer's vertex shader and vUv): the glow, planets filled. */
export const FLIGHT_GLOW_FRAGMENT = `#version 300 es
precision highp float;
in vec2 vUv;
uniform sampler2D uPanorama;
out vec4 outColor;
${FILL_PLANETS_GLSL}
void main() {
  outColor = vec4(fillPlanets(uPanorama, vUv, texture(uPanorama, vUv).rgb), 1.0);
}`;

/** The dome while steering: one triangle over the whole window. */
export const FLIGHT_DOME_VERTEX = `#version 300 es
void main() {
  vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`;

/** Each pixel's ray through the flight camera (domeDirectionAt), into the registration, then the glow (glowUvOfIcrs). */
export const FLIGHT_DOME_FRAGMENT = `#version 300 es
precision highp float;
uniform sampler2D uGlow;
uniform sampler2D uOffsets;
uniform vec2 uOffsetDecode;
uniform vec3 uGeometry;
uniform mat3 uIcrsToPanorama;
uniform vec3 uRowX;
uniform vec3 uRowY;
uniform vec3 uRowZ;
uniform vec3 uPix;
uniform vec2 uLens;
uniform vec3 uInvAffX;
uniform vec3 uInvAffY;
uniform vec2 uView;
uniform float uPixelRatio;
out vec4 outColor;
${FILL_PLANETS_GLSL}
vec2 plateCarree(vec3 d) {
  return vec2(fract(0.5 - atan(d.y, d.x) * 0.15915494309189535), 0.5 + asin(clamp(d.z, -1.0, 1.0)) * 0.3183098861837907);
}
void main() {
  vec2 s = vec2(gl_FragCoord.x, uView.y * uPixelRatio - gl_FragCoord.y) / uPixelRatio;
  float u = dot(uInvAffX, vec3(s, 1.0));
  float w = dot(uInvAffY, vec3(s, 1.0));
  vec2 d = vec2((u - uPix.x) * uPix.z, (uPix.y - w) * uPix.z);
  vec2 r = d;
  for (int i = 0; i < ${DOME_LENS_ITERATIONS}; i++) {
    float r2 = dot(r, r);
    r = d / (1.0 + uLens.x * r2 + uLens.y * r2 * r2);
  }
  vec3 dir = normalize(uRowX * r.x + uRowY * r.y + uRowZ);
  vec3 p = normalize(uIcrsToPanorama * dir);
  vec2 frameUv = plateCarree(p);
  vec2 offset = (texture(uOffsets, frameUv).rg * 255.0 - uOffsetDecode.y) / uOffsetDecode.x;
  float cosLat = max(length(p.xy), ${MIN_POLE_COS_LAT.toExponential()});
  vec2 uv = frameUv + vec2(-offset.x / (uGeometry.x * cosLat), (offset.y - uGeometry.z) / uGeometry.y);
  vec2 gx = dFdx(frameUv);
  vec2 gy = dFdy(frameUv);
  gx.x = fract(gx.x + 0.5) - 0.5;
  gy.x = fract(gy.x + 0.5) - 0.5;
  outColor = vec4(fillPlanets(uGlow, uv, textureGrad(uGlow, uv, gx, gy).rgb), 1.0);
}`;

const UNIFORMS = ['uGlow', 'uOffsets', 'uOffsetDecode', 'uGeometry', 'uIcrsToPanorama', 'uRowX', 'uRowY', 'uRowZ', 'uPix', 'uLens', 'uInvAffX', 'uInvAffY', 'uView', 'uPixelRatio'] as const;

export interface FlightDomeGpu {
  readonly program: WebGLProgram;
  readonly vao: WebGLVertexArrayObject;
  readonly glow: WebGLTexture;
  readonly offsets: WebGLTexture;
  readonly icrsToPanorama: Mat3Rows;
  readonly registration: PanoramaRegistration;
  readonly at: Readonly<Record<(typeof UNIFORMS)[number], WebGLUniformLocation | null>>;
}

function compile(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader | null {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  return gl.getShaderParameter(shader, gl.COMPILE_STATUS) ? shader : null;
}

/** The dome's program and textures in a flight context; none where the browser refuses a step. */
export function createFlightDome(gl: WebGL2RenderingContext, glowImage: TexImageSource, registration: PanoramaRegistration): FlightDomeGpu | null {
  const vs = compile(gl, gl.VERTEX_SHADER, FLIGHT_DOME_VERTEX);
  const fs = compile(gl, gl.FRAGMENT_SHADER, FLIGHT_DOME_FRAGMENT);
  const program = gl.createProgram();
  const vao = gl.createVertexArray();
  const glow = gl.createTexture();
  const offsets = gl.createTexture();
  if (!vs || !fs || !program || !vao || !glow || !offsets) return null;
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return null;
  gl.bindTexture(gl.TEXTURE_2D, glow);
  // Row 0 of the image is the top, at v = 1, as Real sky uploads it
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, glowImage);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
  gl.generateMipmap(gl.TEXTURE_2D);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  const t = registration.offsetTable;
  gl.bindTexture(gl.TEXTURE_2D, offsets);
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RG8, t.width, t.height, 0, gl.RG, gl.UNSIGNED_BYTE, t.codes);
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 4);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  const at = Object.fromEntries(UNIFORMS.map((name) => [name, gl.getUniformLocation(program, name)])) as FlightDomeGpu['at'];
  return { program, vao, glow, offsets, icrsToPanorama: icrsToPanoramaRows(registration), registration, at };
}

/** Draws the dome over the whole window for this frame's camera, opaque, before the stars. */
export function drawFlightDome(gl: WebGL2RenderingContext, dome: FlightDomeGpu, camera: DomeCamera, viewWidthPx: number, viewHeightPx: number, pixelRatio: number): void {
  const { at } = dome;
  gl.useProgram(dome.program);
  gl.bindVertexArray(dome.vao);
  gl.disable(gl.BLEND);
  gl.activeTexture(gl.TEXTURE0);
  gl.bindTexture(gl.TEXTURE_2D, dome.glow);
  gl.activeTexture(gl.TEXTURE1);
  gl.bindTexture(gl.TEXTURE_2D, dome.offsets);
  gl.uniform1i(at.uGlow, 0);
  gl.uniform1i(at.uOffsets, 1);
  const t = dome.registration.offsetTable;
  gl.uniform2f(at.uOffsetDecode, t.scale, t.bias);
  gl.uniform3f(at.uGeometry, dome.registration.imageWidth, dome.registration.imageHeight, dome.registration.yOffsetPx);
  // WebGL 2 takes the rows with transpose set
  gl.uniformMatrix3fv(at.uIcrsToPanorama, true, dome.icrsToPanorama as unknown as Float32List);
  gl.uniform3fv(at.uRowX, camera.rows.x as unknown as Float32List);
  gl.uniform3fv(at.uRowY, camera.rows.y as unknown as Float32List);
  gl.uniform3fv(at.uRowZ, camera.rows.z as unknown as Float32List);
  gl.uniform3f(at.uPix, camera.crpixX, camera.crpixY, camera.scaleRadPerPx);
  gl.uniform2f(at.uLens, camera.k1, camera.k2);
  const [ix, iy] = screenToStoredRows(camera.storedToScreen);
  gl.uniform3fv(at.uInvAffX, ix as unknown as Float32List);
  gl.uniform3fv(at.uInvAffY, iy as unknown as Float32List);
  gl.uniform2f(at.uView, viewWidthPx, viewHeightPx);
  gl.uniform1f(at.uPixelRatio, pixelRatio);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
  gl.bindVertexArray(null);
  gl.activeTexture(gl.TEXTURE0);
}

/** Frees the dome's program, vertex array and textures. */
export function disposeFlightDome(gl: WebGL2RenderingContext, dome: FlightDomeGpu): void {
  gl.deleteProgram(dome.program);
  gl.deleteVertexArray(dome.vao);
  gl.deleteTexture(dome.glow);
  gl.deleteTexture(dome.offsets);
}

/** Whether the flight's Milky Way line stands: at a star once the starless glow has drawn, while steering once the dome has. */
export function flightMilkyWayLineShown({ steering, glowDrawn, domeDrawn }: { steering: boolean; glowDrawn: boolean; domeDrawn: boolean }): boolean {
  return steering ? domeDrawn : glowDrawn;
}
