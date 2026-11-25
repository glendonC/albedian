import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ARRIVAL_BLOOM_FRAGMENT, ARRIVAL_BLOOM_VERTEX, FLIGHT_STAR_FRAGMENT, FLIGHT_STAR_VERTEX } from '../flightStarShader.js';
import { FLIGHT_CONTEXT_ATTRIBUTES, forgetFlightWarm, issueFinishPrograms, prepareFlight } from '../prepareFlight.js';
import { CRAFT_SKY_URL } from '../craftLight.js';
import type { createFlightFinish } from '../flightFinish.js';
import { forgetKeptSkyPictures } from '../../sky/skyPicture.js';
import { FakeOffscreenCanvas, installSkyFakes, named } from '../../sky/__tests__/skyPictureFakes.js';

// realsky-upload-one (LEAD ANSWER 19:21): the flight's programs link once at rest, so the press of Go there links none;
// the finish's as three writes them, taken from the finish itself on a recording context that waits for nothing.

beforeEach(() => {
  forgetKeptSkyPictures();
  forgetFlightWarm();
});
afterEach(() => {
  vi.unstubAllGlobals();
  forgetKeptSkyPictures();
  forgetFlightWarm();
});

/** A finish that compiles one program the way three does: link, then read its status and uniforms, then draw. */
const threeLike = vi.fn(((_canvas: HTMLCanvasElement, gl: WebGL2RenderingContext) => {
  const vs = gl.createShader(gl.VERTEX_SHADER)!;
  gl.shaderSource(vs, 'bloom vertex');
  gl.compileShader(vs);
  const fs = gl.createShader(gl.FRAGMENT_SHADER)!;
  gl.shaderSource(fs, 'bloom fragment');
  gl.compileShader(fs);
  const p = gl.createProgram()!;
  gl.attachShader(p, vs);
  gl.attachShader(p, fs);
  gl.linkProgram(p);
  const seen = {
    linked: gl.getProgramParameter(p, gl.LINK_STATUS),
    uniforms: gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS),
    log: gl.getProgramInfoLog(p),
    at: gl.getUniformLocation(p, 'tDiffuse'),
    attribute: gl.getAttribLocation(p, 'position'),
    compiled: gl.getShaderParameter(fs, gl.COMPILE_STATUS),
  };
  const finish = {
    seen,
    begin: () => gl.clear(gl.COLOR_BUFFER_BIT),
    end: () => {
      gl.useProgram(p);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    },
    setSize: () => undefined,
    dispose: vi.fn(),
  };
  return finish;
}) as unknown as typeof createFlightFinish);

describe('issueFinishPrograms', () => {
  it('links the finish programs on the real context, answers every read that would wait for a link itself, and draws nothing', () => {
    const fakes = installSkyFakes({ pictureUrl: '/x', parallelDoneAt: 1 });
    const gl = new FakeOffscreenCanvas(1, 1).getContext('webgl2') as unknown as WebGL2RenderingContext;
    fakes.calls.length = 0;
    const linked = issueFinishPrograms(gl, threeLike);
    expect(linked).toHaveLength(1);
    expect(named(fakes.calls, 'shaderSource').map((c) => c.args[1])).toEqual(['bloom vertex', 'bloom fragment']);
    expect(named(fakes.calls, 'linkProgram')).toHaveLength(1);
    for (const waits of ['getProgramParameter', 'getShaderParameter', 'getProgramInfoLog', 'getUniformLocation', 'getAttribLocation', 'useProgram', 'drawArrays']) expect(named(fakes.calls, waits)).toHaveLength(0);
    // What three was told: linked, nothing active to look up
    const finish = vi.mocked(threeLike).mock.results.at(-1)!.value as { seen: Record<string, unknown>; dispose: () => void };
    expect(finish.seen).toEqual({ linked: true, uniforms: 0, log: '', at: null, attribute: -1, compiled: true });
    // Not disposed, which would delete the programs before they are linked
    expect(finish.dispose).not.toHaveBeenCalled();
    // Three is given a canvas of its own to listen on, not the warm context's
    expect(vi.mocked(threeLike).mock.calls.at(-1)![0]).not.toBe(gl.canvas);
  });

  it('keeps what it linked when the finish throws part way', () => {
    installSkyFakes({ pictureUrl: '/x' });
    const gl = new FakeOffscreenCanvas(1, 1).getContext('webgl2') as unknown as WebGL2RenderingContext;
    const partial = ((_c: HTMLCanvasElement, g: WebGL2RenderingContext) => {
      g.linkProgram(g.createProgram()!);
      throw new Error('no half floats');
    }) as unknown as typeof createFlightFinish;
    expect(issueFinishPrograms(gl, partial)).toHaveLength(1);
  });
});

describe('prepareFlight', () => {
  it('links the stars, the arrival bloom and the finish once a page, in a context of the star layer kind', () => {
    const fakes = installSkyFakes({ pictureUrl: '/x', parallelDoneAt: 1 });
    prepareFlight(threeLike);
    prepareFlight(threeLike);
    expect(fakes.contexts).toHaveLength(1);
    expect((fakes.contexts[0]!.canvas as unknown as FakeOffscreenCanvas).attributes).toEqual(FLIGHT_CONTEXT_ATTRIBUTES);
    expect(named(fakes.calls, 'shaderSource').map((c) => c.args[1])).toEqual([FLIGHT_STAR_VERTEX, FLIGHT_STAR_FRAGMENT, ARRIVAL_BLOOM_VERTEX, ARRIVAL_BLOOM_FRAGMENT, 'bloom vertex', 'bloom fragment']);
    expect(named(fakes.calls, 'linkProgram')).toHaveLength(3);
  });

  it('reads no picture: the craft\'s sky waits for the flight at rest (craft-compile-one, the lead\'s 00:18 line)', async () => {
    const fakes = installSkyFakes({ pictureUrl: '/x', parallelDoneAt: 1 });
    prepareFlight(threeLike);
    await new Promise((r) => setTimeout(r, 0));
    expect(fakes.fetched.filter((u) => u === CRAFT_SKY_URL || u.startsWith('/textures/'))).toHaveLength(0);
  });

  it('matches the star layer context attributes', async () => {
    const source = await import('node:fs').then((fs) => fs.readFileSync(new URL('../FlightStarsLayer.tsx', import.meta.url), 'utf8'));
    expect(source).toContain("canvas.getContext('webgl2', { antialias: false, premultipliedAlpha: true, alpha: false })");
    expect(FLIGHT_CONTEXT_ATTRIBUTES).toEqual({ antialias: false, premultipliedAlpha: true, alpha: false });
  });
});
