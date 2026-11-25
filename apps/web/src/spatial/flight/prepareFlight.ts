import { createFlightFinish } from './flightFinish.js';
import { ARRIVAL_BLOOM_FRAGMENT, ARRIVAL_BLOOM_VERTEX, FLIGHT_STAR_FRAGMENT, FLIGHT_STAR_VERTEX } from './flightStarShader.js';
import { warmPrograms } from '../sky/skyPicture.js';

/**
 * realsky-upload-one (LEAD ANSWER 19:21): the flight's programs linked once while the reveal is at rest, so the press of
 * Go there and the first steer link none (flight-stars-finish-one's report: the bloom's shaders linked at the star
 * layer's first frame, about 40 ms of the press): the stars' points, the arrival's bloom, and the finish's bloom and
 * tone mapping as three writes them for this browser (the sky's own, the dome's among them, in RealSkyLayer's prepare). They are linked in a throwaway context with
 * nothing read back (skyPicture.ts warmPrograms), so the warming itself makes no frame wait.
 */

/** The star layer's context attributes (FlightStarsLayer.tsx createGpu), so the warm context is the same kind. */
export const FLIGHT_CONTEXT_ATTRIBUTES: WebGLContextAttributes = { antialias: false, premultipliedAlpha: true, alpha: false };

const LINK_QUERIES = new Set(['getProgramInfoLog', 'getShaderInfoLog']);
const NOTHING_DRAWN = new Set(['useProgram', 'drawArrays', 'drawElements', 'drawArraysInstanced', 'drawElementsInstanced', 'drawRangeElements', 'readPixels', 'finish']);

/**
 * The finish made once on `gl` through a recording stand-in: three compiles and links each of its programs on the real
 * context as it would for the flight, while every read that would wait for a link is answered here and nothing is
 * drawn. Returns the programs linked. Three listens on `canvas`, a stand-in of its own, so letting the context go later
 * tells it nothing.
 */
export function issueFinishPrograms(gl: WebGL2RenderingContext, make: typeof createFlightFinish = createFlightFinish): WebGLProgram[] {
  const linked: WebGLProgram[] = [];
  const recording = new Proxy(gl, {
    get(target, prop) {
      const value: unknown = Reflect.get(target, prop, target);
      if (typeof value !== 'function' || typeof prop !== 'string') return value;
      if (prop === 'linkProgram') {
        return (program: WebGLProgram) => {
          linked.push(program);
          target.linkProgram(program);
        };
      }
      if (prop === 'getProgramParameter') return (_p: WebGLProgram, pname: number) => (pname === target.ACTIVE_UNIFORMS || pname === target.ACTIVE_ATTRIBUTES || pname === target.ACTIVE_UNIFORM_BLOCKS ? 0 : true);
      if (prop === 'getShaderParameter') return () => true;
      if (LINK_QUERIES.has(prop)) return () => '';
      if (prop === 'getUniformLocation') return () => null;
      if (prop === 'getAttribLocation') return () => -1;
      if (NOTHING_DRAWN.has(prop)) return () => undefined;
      return (value as (...args: unknown[]) => unknown).bind(target);
    },
  });
  const canvas = new OffscreenCanvas(1, 1) as unknown as HTMLCanvasElement;
  try {
    const finish = make(canvas, recording, 64, 64, 1);
    finish?.begin();
    finish?.end();
    // Not disposed: that would delete the programs before their links are done; the context is let go whole instead
  } catch {
    // A browser three cannot finish on links what it reached; the flight then links the rest itself, as before
  }
  return linked;
}

let warmed = false;

/** Links the flight's programs once a page, at rest; nothing where the browser has no OffscreenCanvas. */
export function prepareFlight(make: typeof createFlightFinish = createFlightFinish): void {
  if (warmed) return;
  warmed = warmPrograms(
    [
      [FLIGHT_STAR_VERTEX, FLIGHT_STAR_FRAGMENT],
      [ARRIVAL_BLOOM_VERTEX, ARRIVAL_BLOOM_FRAGMENT],
    ],
    (gl) => issueFinishPrograms(gl, make),
    FLIGHT_CONTEXT_ATTRIBUTES,
  );
}

/** Tests only: warm again. */
export function forgetFlightWarm(): void {
  warmed = false;
}
