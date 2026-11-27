// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { CATALOG_STAR_TILE_ENCODING_V1 as ENC } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { catalogStarTint, B_MINUS_V_TO_BP_RP } from '../../catalogStars/catalogStarTint.js';
import type { CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';
import { keyLight, withDrawnStarColour, type LightCandidate, type Vec3 } from '../craftLight.js';
import { flightCameraOf } from '../flightFrame.js';
import { CRAFTS } from '../crafts.js';
import { FlightStarsLayer, type OpenFlightTiles } from '../FlightStarsLayer.js';

// craft-star-light-one (DECIDED-BY-LEAD 27 Nov 12:39, LOOK-craft-star-light-NOTE.md; the owner's first item, the craft
// that feels alive): the craft's key takes the star's own catalog colour from the row the flight draws the star from.
// A Hipparcos 2007 finding carries no Gaia BP-RP, so the key fell to the neutral tint beside blue Rigel and orange
// Betelgeuse alike; the star layer's own row carries the B-V the star is drawn in.

const seen = vi.hoisted(() => ({ candidates: [] as unknown[] }));
vi.mock('../FlightCraftLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../FlightCraftLayer.js')>();
  return {
    ...original,
    FlightCraftLayer: (props: { keyCandidates: readonly unknown[] }) => {
      seen.candidates.push(props.keyCandidates);
      return null;
    },
  };
});

const srgbToLinear = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const linearTint = (colorIndex: number, isBMinusV: boolean) => catalogStarTint(colorIndex, isBMinusV).map(srgbToLinear);

const AT: Vec3 = [30, -120, 40];
const hipparcos: LightCandidate = { positionPc: AT, earthMag: 0.5, earthDistancePc: Math.hypot(...AT), colorIndex: Number.NaN, isBMinusV: false };
/** Turned by `rad` about an axis across the line of sight and moved by `share` of its distance. */
const off = (p: Vec3, rad: number, share = 0): Vec3 => {
  const d = Math.hypot(...p);
  const u: Vec3 = [p[0] / d, p[1] / d, p[2] / d];
  const a = [-u[1], u[0], 0];
  const an = Math.hypot(a[0]!, a[1]!, a[2]!);
  const k = d * (1 + share);
  return [k * (Math.cos(rad) * u[0] + (Math.sin(rad) * a[0]!) / an), k * (Math.cos(rad) * u[1] + (Math.sin(rad) * a[1]!) / an), k * (Math.cos(rad) * u[2] + (Math.sin(rad) * a[2]!) / an)];
};

describe('the key takes the colour of the row the flight draws the star from', () => {
  it('gives a Hipparcos finding the row\'s B-V, so beside Betelgeuse the key is orange and beside Rigel blue-white', () => {
    const red = withDrawnStarColour([hipparcos], { positionPc: off(AT, 1e-4, 0.01), bMinusV: 1.85 });
    expect(red).toEqual([{ ...hipparcos, colorIndex: 1.85, isBMinusV: true }]);
    const near = off(AT, 0, -0.9);
    const orange = keyLight(near, near, red).tint;
    orange.forEach((v, k) => expect(v).toBeCloseTo(linearTint(1.85, true)[k]!, 12));
    expect(orange[0]).toBeGreaterThan(orange[1]!);
    expect(orange[1]).toBeGreaterThan(orange[2]!);
    const blue = keyLight(near, near, withDrawnStarColour([hipparcos], { positionPc: AT, bMinusV: -0.03 })).tint;
    expect(blue[2]).toBeGreaterThan(blue[0]!);
    // Before: the neutral tint for both
    const neutral = keyLight(near, near, [hipparcos]).tint;
    neutral.forEach((v, k) => expect(v).toBeCloseTo(linearTint(Number.NaN, false)[k]!, 12));
    expect(orange).not.toEqual(neutral);
  });

  it('keeps a finding\'s own Gaia BP-RP, and leaves the list as it is with no row or a row with no colour', () => {
    const gaia: LightCandidate = { ...hipparcos, colorIndex: 2.1 };
    const list = [gaia];
    expect(withDrawnStarColour(list, { positionPc: AT, bMinusV: 1.85 })).toBe(list);
    const plain = [hipparcos];
    expect(withDrawnStarColour(plain, null)).toBe(plain);
    expect(withDrawnStarColour(plain, { positionPc: AT })).toBe(plain);
    expect(withDrawnStarColour(plain, { positionPc: AT, bMinusV: Number.NaN })).toBe(plain);
    expect(withDrawnStarColour([], { positionPc: AT, bMinusV: 1 })).toEqual([]);
  });

  it('colours only the star at the row\'s place: within about a minute of arc and 3 percent of its distance', () => {
    const plain = [hipparcos];
    expect(withDrawnStarColour(plain, { positionPc: off(AT, 2.9e-4, 0.029), bMinusV: 1 })[0]!.colorIndex).toBe(1);
    expect(withDrawnStarColour(plain, { positionPc: off(AT, 3.1e-4), bMinusV: 1 })).toBe(plain);
    expect(withDrawnStarColour(plain, { positionPc: off(AT, 0, 0.031), bMinusV: 1 })).toBe(plain);
    expect(withDrawnStarColour(plain, { positionPc: off(AT, 0, -0.031), bMinusV: 1 })).toBe(plain);
  });

  it('draws a Gaia row\'s key in the tint the star is drawn in (the row\'s B-V is its BP-RP over the tint\'s slope)', () => {
    const bpRp = 1.3;
    const viaRow = withDrawnStarColour([hipparcos], { positionPc: AT, bMinusV: bpRp / B_MINUS_V_TO_BP_RP })[0]!;
    const drawn = catalogStarTint(bpRp, false);
    catalogStarTint(viaRow.colorIndex, viaRow.isBMinusV).forEach((v, k) => expect(v).toBeCloseTo(drawn[k]!, 12));
  });
});

// The layer: the star layer hands the craft its key candidates with the colour of the row it found at the place
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function there(bpRp: number | undefined): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const session = { ...base, sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 }, spatialView: { ...base.spatialView, mode: 'source_2d' } } as ObservationSession;
  const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
  // The picked finding's catalog row as a Hipparcos row (no BP-RP) or as a Gaia row with its own
  const findings = session.observationSolution!.findings.map((f) => {
    if (f.id !== star.id) return f;
    const { colorIndexBpRp: _drop, ...ref } = f.catalogRef!;
    return { ...f, catalogRef: bpRp === undefined ? ref : { ...ref, colorIndexBpRp: bpRp } };
  });
  const withRow = { ...session, observationSolution: { ...session.observationSolution!, findings } } as ObservationSession;
  return dispatchAlbedianAction(withRow, { type: 'space.enter', requestId: 'go', expectedViewRevision: withRow.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated' } as never).session;
}

function quietGl(): unknown {
  let n = 0;
  return new Proxy({}, {
    get: (_t, key) => {
      if (key === 'getShaderParameter' || key === 'getProgramParameter') return () => true;
      if (key === 'isContextLost') return () => false;
      if (key === 'getUniformLocation') return () => ({ id: ++n });
      if (key === 'getAttribLocation') return () => 0;
      if (typeof key === 'string' && /^create/.test(key)) return () => ({ id: ++n });
      if (typeof key === 'string' && /^[A-Z_0-9]+$/.test(key)) return 1;
      return () => {};
    },
  });
}

/** The star flown to as its own Hipparcos row (B-V `bMinusV`), at the place flown to. Typed test data. */
function rowAtThePlace(session: ObservationSession, bMinusV: number): CatalogStarCoreTile {
  const ref = flightCameraOf(session, 1440, 900)!.referencePc;
  const d = Math.hypot(ref[0], ref[1], ref[2]);
  return {
    layer: 0, order: 0, pix: 1, count: 1, positionBits: 32, directions: new Float32Array([ref[0] / d, ref[1] / d, ref[2] / d]),
    magnitude: new Float32Array([0.5]), colorIndex: new Float32Array([bMinusV]), distancePc: new Float32Array([d]), flags: new Uint8Array([1 | ENC.flags.colorBMinusV]), byteLength: 0,
  };
}

let container: HTMLDivElement;
let root: Root;
let frames: FrameRequestCallback[];
let now = 10_000;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  frames = [];
  seen.candidates = [];
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
    frames.push(cb);
    return frames.length;
  });
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
  vi.spyOn(performance, 'now').mockImplementation(() => now);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(quietGl() as never);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

async function handed(session: ObservationSession, tile: CatalogStarCoreTile): Promise<LightCandidate[]> {
  const openTiles: OpenFlightTiles = async (onTile) => {
    onTile(tile);
    return { setView: () => {}, dispose: () => {} };
  };
  await act(async () => root.render(createElement(FlightStarsLayer, { session, viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, craft: CRAFTS['astronaut-emu']! })));
  for (let i = 0; i < 4; i++) {
    now += 1000 / 60;
    const due = frames.splice(0);
    await act(async () => {
      for (const cb of due) cb(now);
    });
  }
  await act(async () => root.render(createElement(FlightStarsLayer, { session, viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, craft: CRAFTS['astronaut-emu']! })));
  return seen.candidates.at(-1) as LightCandidate[];
}

describe('the star layer hands the craft the key in the drawn row\'s colour', () => {
  it('a Hipparcos finding\'s key takes the B-V of the row drawn at the place flown to', async () => {
    const session = there(undefined);
    const candidates = await handed(session, rowAtThePlace(session, 1.85));
    expect(candidates).toHaveLength(1);
    expect(candidates[0]!.isBMinusV).toBe(true);
    expect(candidates[0]!.colorIndex).toBeCloseTo(1.85, 6);
  });

  it('a Gaia finding\'s key keeps its own BP-RP', async () => {
    const session = there(2.1);
    const candidates = await handed(session, rowAtThePlace(session, 1.85));
    expect(candidates).toHaveLength(1);
    expect(candidates[0]!).toMatchObject({ colorIndex: 2.1, isBMinusV: false });
  });
});
