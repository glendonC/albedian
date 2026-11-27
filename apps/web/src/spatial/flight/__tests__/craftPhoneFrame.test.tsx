// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import * as THREE from 'three';
import type { ObservationSession } from '@albedian/contracts';
import type { Vec3, WcsCameraRows } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import hangar from '../../../../public/models/hangar/hangar.json';
import { FlightCraftLayer, type CraftRenderer } from '../FlightCraftLayer.js';
import { ALIVE_AT_REST, type AliveSignals } from '../craftAlive.js';
import { arrivalCraftFraming } from '../arrival.js';
import { CRAFTS } from '../crafts.js';
import { PHONE_BAND_CLEAR_PX, PHONE_SKY_TOP_PX, fitFramingToBand, phoneBandFit, phoneBandOf, phoneCraftFraming, type CraftFraming } from '../craftScene.js';
import { flightCameraOf } from '../flightFrame.js';
import { steerStartOf } from '../starView.js';

// craft-phone-frame-one (DECIDED-BY-LEAD 27 Nov 17:48, on FIELD's New Horizons cut by the phone's top edge at a star's
// floor): on the phone every craft in the hangar stands whole between the top bar's foot and the readout's top, each
// 8 px clear (the arrival rule's clearance), at rest, at the floor and while steered, measured from each craft's own
// bounds on screen: moved down or up only as far as it must, drawn smaller only where its box is taller than the band.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('phoneBandFit', () => {
  const band = { topPx: 112, bottomPx: 264 };
  const base: CraftFraming = { xPx: 120, yPx: 180, diameterPx: 184, frameHeightPx: 168 };

  it('leaves a craft whose box stands inside the band as it is', () => {
    expect(phoneBandFit(base, { y0: 112, y1: 264 }, band)).toEqual(base);
    expect(phoneBandFit(base, { y0: 130, y1: 230 }, band)).toEqual(base);
  });

  it('moves a craft over the top bar down only as far as it must, its size kept', () => {
    const fit = phoneBandFit(base, { y0: 92, y1: 200 }, band);
    expect(fit.yPx).toBeCloseTo(200, 9);
    expect(fit.diameterPx).toBe(184);
    expect(fit.xPx).toBe(120);
    expect(fit.frameHeightPx).toBe(168);
  });

  it('moves a craft over the readout up only as far as it must, its size kept', () => {
    const fit = phoneBandFit(base, { y0: 140, y1: 270 }, band);
    expect(fit.yPx).toBeCloseTo(174, 9);
    expect(fit.diameterPx).toBe(184);
  });

  it('draws a craft taller than the band smaller by the band over its height, its top at the band\'s top', () => {
    // 190 px tall in a band of 152: drawn at 152 / 190 of its size; about its framing point its top moves from 80 to
    // 180 - 100 * 0.8 = 100, then down to 112
    const fit = phoneBandFit(base, { y0: 80, y1: 270 }, band);
    expect(fit.diameterPx).toBeCloseTo((184 * 152) / 190, 9);
    expect(fit.yPx).toBeCloseTo(180 + (112 - (180 - 100 * (152 / 190))), 9);
  });

  it('reads the band from a phone\'s framing: from the top bar\'s foot to the readout\'s top, each 8 px in', () => {
    expect(phoneBandOf(phoneCraftFraming(390, 448))).toEqual({ topPx: 112, bottomPx: 440 });
    expect(phoneBandOf({ xPx: 1, yPx: 2, diameterPx: 3 })).toBeNull();
    expect(phoneBandOf({ xPx: 1, yPx: 2, diameterPx: 3, frameHeightPx: 0 })).toBeNull();
  });

  it('is the band\'s top and foot each 8 px clear of the top bar\'s foot and the readout\'s top', () => {
    expect(PHONE_BAND_CLEAR_PX).toBe(8);
    expect(PHONE_SKY_TOP_PX).toBe(104);
  });
});

describe('fitFramingToBand: the craft as drawn brought whole into the band', () => {
  const band = { topPx: 112, bottomPx: 440 };
  const base: CraftFraming = { xPx: 150, yPx: 300, diameterPx: 400, frameHeightPx: 344 };
  // A box that grows with the framed size as a craft near the camera does: faster than the size (a part close to the
  // camera), centred a little above the framing point
  const near = (f: CraftFraming) => {
    const s = f.diameterPx / 400;
    const tall = 300 * s + 900 * s * s * s;
    return { x0: f.xPx - 100, y0: f.yPx - 0.6 * tall, x1: f.xPx + 100, y1: f.yPx + 0.4 * tall };
  };
  const linear = (f: CraftFraming) => ({ x0: f.xPx - 50, y0: f.yPx - f.diameterPx / 2, x1: f.xPx + 50, y1: f.yPx + f.diameterPx / 2 });

  it('leaves a framing whose box stands inside the band as it is', () => {
    const f = { ...base, diameterPx: 200 };
    expect(fitFramingToBand(f, band, linear)!.framing).toEqual(f);
  });

  it('moves a box over the top bar down only as far as it must, its size kept', () => {
    const r = fitFramingToBand({ ...base, yPx: 180, diameterPx: 200 }, band, linear)!;
    expect(r.framing.diameterPx).toBe(200);
    expect(r.box.y0).toBeCloseTo(112, 6);
  });

  it('draws a box taller than the band at the largest size whose box fits, not smaller', () => {
    const r = fitFramingToBand(base, band, near)!;
    expect(r.box.y0).toBeGreaterThanOrEqual(band.topPx - 0.25);
    expect(r.box.y1).toBeLessThanOrEqual(band.bottomPx + 0.25);
    // Its box fills the band within a pixel, and a size one percent larger would not fit
    expect(r.box.y1 - r.box.y0).toBeGreaterThan(band.bottomPx - band.topPx - 1);
    const larger = near({ ...r.framing, diameterPx: r.framing.diameterPx * 1.01 });
    expect(larger.y1 - larger.y0).toBeGreaterThan(band.bottomPx - band.topPx);
  });

  it('is none when the craft cannot be placed at all', () => {
    expect(fitFramingToBand(base, band, () => null)).toBeNull();
  });
});

function there(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const session = {
    ...base,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 },
    spatialView: { ...base.spatialView, mode: 'source_2d' },
  } as ObservationSession;
  const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
  return dispatchAlbedianAction(session, { type: 'space.enter', requestId: 'go', expectedViewRevision: session.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated' }).session;
}

const makeRenderer = (): CraftRenderer => ({ setPixelRatio: () => {}, setSize: () => {}, setClearColor: () => {}, clear: () => {}, dispose: () => {}, render: () => {} });

/**
 * Each craft's model as the phone draws it: one box mesh for each part (mesh primitive) of its phone file, each part's
 * own bounds read from the file's glTF JSON (POSITION's min and max, which glTF requires) through its node's transform,
 * so the layer measures the parts of the real file without decoding its geometry.
 */
const IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
type GltfNode = { mesh?: number; skin?: number; children?: number[]; matrix?: number[]; translation?: number[]; rotation?: number[]; scale?: number[] };
const mul4 = (a: readonly number[], b: readonly number[]) => {
  const o = new Array<number>(16).fill(0);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) for (let k = 0; k < 4; k++) o[c * 4 + r]! += a[k * 4 + r]! * b[c * 4 + k]!;
  return o;
};
const trs = (n: GltfNode): number[] => {
  if (n.matrix) return n.matrix;
  const [x, y, z, w] = n.rotation ?? [0, 0, 0, 1];
  const [sx, sy, sz] = n.scale ?? [1, 1, 1];
  const [tx, ty, tz] = n.translation ?? [0, 0, 0];
  return [(1 - 2 * (y! * y! + z! * z!)) * sx!, 2 * (x! * y! + z! * w!) * sx!, 2 * (x! * z! - y! * w!) * sx!, 0, 2 * (x! * y! - z! * w!) * sy!, (1 - 2 * (x! * x! + z! * z!)) * sy!, 2 * (y! * z! + x! * w!) * sy!, 0, 2 * (x! * z! + y! * w!) * sz!, 2 * (y! * z! - x! * w!) * sz!, (1 - 2 * (x! * x! + y! * y!)) * sz!, 0, tx!, ty!, tz!, 1];
};
function partsOfFile(path: string): { min: number[]; max: number[] }[] {
  const buf = readFileSync(path);
  const json = JSON.parse(buf.subarray(20, 20 + buf.readUInt32LE(12)).toString('utf8')) as { nodes?: GltfNode[]; meshes: { primitives: { attributes: { POSITION: number } }[] }[]; accessors: { min: number[]; max: number[] }[] };
  const nodes = json.nodes ?? [];
  const parent = new Map<number, number>();
  nodes.forEach((n, i) => (n.children ?? []).forEach((c) => parent.set(c, i)));
  const world = (i: number): number[] => (parent.has(i) ? mul4(world(parent.get(i)!), trs(nodes[i]!)) : trs(nodes[i]!));
  const parts: { min: number[]; max: number[] }[] = [];
  nodes.forEach((n, i) => {
    if (n.mesh === undefined) return;
    // A skinned mesh's positions are in the model's frame at its bind pose (glTF ignores its node's transform)
    const m = n.skin !== undefined ? IDENTITY : world(i);
    for (const p of json.meshes[n.mesh]!.primitives) {
      const a = json.accessors[p.attributes.POSITION]!;
      const lo = [Infinity, Infinity, Infinity];
      const hi = [-Infinity, -Infinity, -Infinity];
      for (const sx of [0, 1]) for (const sy of [0, 1]) for (const sz of [0, 1]) {
        const v = [sx ? a.max[0]! : a.min[0]!, sy ? a.max[1]! : a.min[1]!, sz ? a.max[2]! : a.min[2]!];
        for (let k = 0; k < 3; k++) {
          const w = m[k]! * v[0]! + m[4 + k]! * v[1]! + m[8 + k]! * v[2]! + m[12 + k]!;
          lo[k] = Math.min(lo[k]!, w);
          hi[k] = Math.max(hi[k]!, w);
        }
      }
      parts.push({ min: lo, max: hi });
    }
  });
  return parts;
}
const PUBLIC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../public');
const PARTS: Readonly<Record<string, { min: number[]; max: number[] }[]>> = Object.fromEntries(Object.values(CRAFTS).map((c) => [c.id, partsOfFile(`${PUBLIC}${c.phoneModelUrl}`)]));
const boxModel = (id: string) => async () => {
  const g = new THREE.Group();
  for (const b of PARTS[id]!) {
    const geometry = new THREE.BoxGeometry(b.max[0]! - b.min[0]!, b.max[1]! - b.min[1]!, b.max[2]! - b.min[2]!);
    geometry.translate((b.max[0]! + b.min[0]!) / 2, (b.max[1]! + b.min[1]!) / 2, (b.max[2]! + b.min[2]!) / 2);
    g.add(new THREE.Mesh(geometry, new THREE.MeshStandardMaterial()));
  }
  return g;
};

/**
 * The phones, each with its readout's top as measured live on main at Betelgeuse with a craft chosen (craft-phone-frame-
 * one's take, rounded down): 390 by 844 with a craft's readout at 448 (UIUX-arrivalstar 16:32) and New Horizons' and
 * Voyager's taller one at 414; FIELD's 402 by 714 with Stardust's and the suit's at 317 and New Horizons' at 284; the
 * iPhone SE's 375 by 667 with New Horizons' at 237.
 */
const PHONES = [
  { w: 390, h: 844, readoutTop: 448 },
  { w: 390, h: 844, readoutTop: 414 },
  { w: 402, h: 714, readoutTop: 317 },
  { w: 402, h: 714, readoutTop: 284 },
  { w: 375, h: 667, readoutTop: 237 },
] as const;

const STEERED: readonly AliveSignals[] = [
  ALIVE_AT_REST,
  { bank: 0, speed: 1, brake: 0, roll: 0 },
  { bank: 0, speed: 0, brake: 1, roll: 0 },
  { bank: 1, speed: 1, brake: 0, roll: 0.35 },
  { bank: -1, speed: 0.5, brake: 0.5, roll: -0.35 },
];

describe('every craft in the hangar on the phone', () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const ids = Object.keys(CRAFTS);
  const boxOf = () => (container.querySelector('[data-testid="flight-craft"]')?.getAttribute('data-box') ?? 'NaN,NaN,NaN,NaN').split(',').map(Number) as [number, number, number, number];

  it('reads every craft of the hangar with the parts of its own phone file', () => {
    expect(ids.length).toBe((hangar as unknown as { entries: unknown[] }).entries.length);
    for (const id of ids) expect(PARTS[id]!.length, id).toBeGreaterThan(0);
  });

  for (const phone of PHONES) {
    const top = PHONE_SKY_TOP_PX + PHONE_BAND_CLEAR_PX;
    const foot = phone.readoutTop - PHONE_BAND_CLEAR_PX;
    const label = `${phone.w} by ${phone.h}, readout at ${phone.readoutTop}`;

    it(`stands whole between the top bar and the readout at rest, at the floor and while steered: ${label}`, async () => {
      const camera = flightCameraOf(there(), phone.w, phone.h)!;
      const rest = phoneCraftFraming(phone.w, phone.readoutTop);
      const floor = arrivalCraftFraming(rest, phone.w, phone.h, true, 1);
      const rows: WcsCameraRows = steerStartOf(camera).rows;
      const at: Vec3 = camera.observerPc;
      const out: string[] = [];
      const check = (id: string, state: string) => {
        const [, y0, , y1] = boxOf();
        if (!(y0 >= top - 0.5)) out.push(`${id} ${state}: top ${y0.toFixed(1)} above ${top}`);
        if (!(y1 <= foot + 0.5)) out.push(`${id} ${state}: foot ${y1.toFixed(1)} below ${foot}`);
      };
      for (const id of ids) {
        const craft = CRAFTS[id]!;
        const load = boxModel(id);
        for (const [state, framing] of [['rest', rest], ['floor', floor]] as const) {
          await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft, load, makeRenderer, framing, phone: true })));
          await act(async () => await new Promise((r) => setTimeout(r, 0)));
          check(id, state);
          act(() => root.unmount());
          root = createRoot(container);
        }
        const drawRef: { current: ((s: { positionPc: Vec3; rows: WcsCameraRows; craftRows?: WcsCameraRows; alive?: AliveSignals }) => void) | null } = { current: null };
        await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft, load, makeRenderer, framing: rest, phone: true, drawRef })));
        await act(async () => await new Promise((r) => setTimeout(r, 0)));
        for (const [i, alive] of STEERED.entries()) {
          act(() => drawRef.current!({ positionPc: at, rows, craftRows: rows, alive }));
          check(id, `steered ${i}`);
        }
        act(() => root.unmount());
        root = createRoot(container);
      }
      expect(out).toEqual([]);
    }, 60_000);
  }

  it('draws every craft at rest on the 390 by 844 phone with a craft\'s readout exactly as before: its parts already stand in the band', async () => {
    const camera = flightCameraOf(there(), 390, 844)!;
    const framing = phoneCraftFraming(390, 448);
    const moved: string[] = [];
    for (const id of ids) {
      const boxes: number[][] = [];
      for (const phone of [false, true]) {
        await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS[id]!, load: boxModel(id), makeRenderer, framing, phone })));
        await act(async () => await new Promise((r) => setTimeout(r, 0)));
        boxes.push(boxOf());
        act(() => root.unmount());
        root = createRoot(container);
      }
      if (boxes[0]!.some((v, i) => !(Math.abs(v - boxes[1]![i]!) < 0.05))) moved.push(`${id}: ${boxes[0]!.join(',')} to ${boxes[1]!.join(',')}`);
    }
    expect(moved).toEqual([]);
  });

  it('measures the craft as drawn: its box is every vertex of its parts through the drawn model and clip matrices, its pose included', async () => {
    // Each vertex of the drawn model projected here on its own (the box meshes' vertices are their parts' corners), so
    // the layer's box is checked against the drawing itself, not against the layer's own measure
    const drawn: { model: number[]; clip: number[]; group: THREE.Object3D }[] = [];
    const recording = (): CraftRenderer => ({
      ...makeRenderer(),
      render: (scene: THREE.Scene, cam: THREE.Camera) => {
        const holder = scene.children.find((c) => (c as THREE.Group).isGroup && c.children.length === 1)!;
        drawn.push({ model: [...holder.matrix.elements], clip: [...(cam as THREE.PerspectiveCamera).projectionMatrix.elements], group: holder.children[0]! });
      },
    });
    const projected = (d: (typeof drawn)[number], w: number, h: number) => {
      let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
      const m = new THREE.Matrix4().fromArray(d.clip).multiply(new THREE.Matrix4().fromArray(d.model));
      d.group.traverse((o) => {
        const g = (o as THREE.Mesh).geometry as THREE.BufferGeometry | undefined;
        if (!g) return;
        const p = g.getAttribute('position');
        for (let i = 0; i < p.count; i++) {
          const v = new THREE.Vector4(p.getX(i), p.getY(i), p.getZ(i), 1).applyMatrix4(m);
          const x = ((v.x / v.w + 1) / 2) * w;
          const y = ((1 - v.y / v.w) / 2) * h;
          [x0, y0, x1, y1] = [Math.min(x0, x), Math.min(y0, y), Math.max(x1, x), Math.max(y1, y)];
        }
      });
      return [x0, y0, x1, y1];
    };
    const camera = flightCameraOf(there(), 390, 844)!;
    const rows: WcsCameraRows = steerStartOf(camera).rows;
    const misses: string[] = [];
    const compare = (label: string) => {
      const want = projected(drawn.at(-1)!, 390, 844);
      const got = boxOf();
      if (got.some((v, i) => !(Math.abs(v - want[i]!) < 0.05))) misses.push(`${label}: ${got.map((v) => v.toFixed(2)).join(',')} against ${want.map((v) => v.toFixed(2)).join(',')}`);
    };
    // The shuttle's forty-two parts at the floor; the suit leaning at full speed and banking while steered
    for (const id of ['shuttle', 'astronaut-emu', 'dawn']) {
      const drawRef: { current: ((s: { positionPc: Vec3; rows: WcsCameraRows; craftRows?: WcsCameraRows; alive?: AliveSignals }) => void) | null } = { current: null };
      const framing = arrivalCraftFraming(phoneCraftFraming(390, 448), 390, 844, true, 1);
      await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS[id]!, load: boxModel(id), makeRenderer: recording, framing, phone: true, drawRef })));
      await act(async () => await new Promise((r) => setTimeout(r, 0)));
      compare(`${id} floor`);
      act(() => drawRef.current!({ positionPc: camera.observerPc, rows, craftRows: rows, alive: { bank: 1, speed: 1, brake: 0, roll: 0.35 } }));
      compare(`${id} steered`);
      act(() => root.unmount());
      root = createRoot(container);
    }
    expect(misses).toEqual([]);
  });

  it('leaves the laptop as it frames it: no band there, even for a framing that reaches above the top bar\'s foot', async () => {
    const camera = flightCameraOf(there(), 1440, 900)!;
    const framing: CraftFraming = { xPx: 720, yPx: 90, diameterPx: 320, frameHeightPx: 900 };
    const topOf = async (phone: boolean) => {
      await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['new-horizons']!, load: boxModel('new-horizons'), makeRenderer, framing, phone })));
      await act(async () => await new Promise((r) => setTimeout(r, 0)));
      const [, y0] = boxOf();
      act(() => root.unmount());
      root = createRoot(container);
      return y0;
    };
    expect(await topOf(false)).toBeLessThan(PHONE_SKY_TOP_PX);
    expect(await topOf(true)).toBeGreaterThanOrEqual(PHONE_SKY_TOP_PX + PHONE_BAND_CLEAR_PX - 0.5);
  });
});
