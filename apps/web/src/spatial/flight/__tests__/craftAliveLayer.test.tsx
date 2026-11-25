// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import * as THREE from 'three';
import type { ObservationSession } from '@albedian/contracts';
import type { Vec3, WcsCameraRows } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { FlightCraftLayer, type CraftRenderer } from '../FlightCraftLayer.js';
import { ALIVE_AT_REST, type AliveSignals } from '../craftAlive.js';
import { CRAFTS } from '../crafts.js';
import { flightCameraOf } from '../flightFrame.js';
import { steerStartOf } from '../starView.js';

// craft-alive-one (DECIDED-BY-LEAD 25 Nov 03:22): the craft layer draws the alive pose a steering frame hands it: from
// behind, leaning and banking about its own centre, smaller as the camera falls back, and as before with no signals.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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

/** A renderer stand-in that records the craft's model matrix at each render. */
function recordingRenderer() {
  const drawn: number[][] = [];
  const makeRenderer = (): CraftRenderer => ({
    setPixelRatio: () => {},
    setSize: () => {},
    setClearColor: () => {},
    clear: () => {},
    dispose: () => {},
    render: (scene: THREE.Scene) => {
      const holder = scene.children.find((c) => (c as THREE.Group).isGroup && c.children.length === 1)!;
      drawn.push([...holder.matrix.elements]);
    },
  });
  return { drawn, makeRenderer };
}

const model = async () => {
  const g = new THREE.Group();
  g.add(new THREE.Mesh(new THREE.BoxGeometry(1, 2, 0.5), new THREE.MeshStandardMaterial()));
  return g;
};

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

describe('the craft layer draws the alive pose a steering frame hands it', () => {
  const camera = flightCameraOf(there(), 1440, 900)!;
  const rows: WcsCameraRows = steerStartOf(camera).rows;
  const at: Vec3 = camera.observerPc;

  async function framesFor(signals: (AliveSignals | undefined)[]) {
    const { drawn, makeRenderer } = recordingRenderer();
    const drawRef: { current: ((s: { positionPc: Vec3; rows: WcsCameraRows; craftRows?: WcsCameraRows; alive?: AliveSignals }) => void) | null } = { current: null };
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['astronaut-emu']!, load: model, makeRenderer, drawRef })));
    await act(async () => await new Promise((r) => setTimeout(r, 0)));
    return signals.map((alive) => {
      act(() => drawRef.current!({ positionPc: at, rows, craftRows: rows, ...(alive ? { alive } : {}) }));
      return drawn.at(-1)!;
    });
  }
  const turnOf = (m: number[]) => [m[0]!, m[1]!, m[2]!, m[4]!, m[5]!, m[6]!, m[8]!, m[9]!, m[10]!];
  const scaleOf = (m: number[]) => Math.hypot(m[0]!, m[1]!, m[2]!);
  const near = (a: number[], b: number[]) => a.every((v, i) => Math.abs(v - b[i]!) < 1e-9);

  it('turns the suit from behind while steered, and draws it as before with no signals', async () => {
    const [plain, rest] = await framesFor([undefined, ALIVE_AT_REST]);
    expect(near(turnOf(plain!), turnOf(rest!))).toBe(false);
    // Its line of flight (the hangar's +Z) points away from the camera in the chase, toward it as NASA pictured it
    const away = (m: number[]) => m[10]!;
    expect(Math.sign(away(rest!))).toBe(-Math.sign(away(plain!)));
  });

  it('banks and leans it about its own centre, so its place on screen holds', async () => {
    const [rest, banked, fast] = await framesFor([ALIVE_AT_REST, { ...ALIVE_AT_REST, bank: 1 }, { ...ALIVE_AT_REST, speed: 0.4 }]);
    expect(near(turnOf(rest!), turnOf(banked!))).toBe(false);
    expect(near(turnOf(rest!), turnOf(fast!))).toBe(false);
    // The model's centre (its box's centre is its own origin here) is drawn where it was
    for (const m of [banked!]) [12, 13, 14].forEach((k) => expect(m[k]!).toBeCloseTo(rest![k]!, 6));
  });

  it('draws it smaller as the camera falls back with speed', async () => {
    const [rest, cruise] = await framesFor([ALIVE_AT_REST, { ...ALIVE_AT_REST, speed: 1 }]);
    // The same model at the same distance is framed by its size: the drawn scale shrinks with the framed diameter
    const restDepth = Math.hypot(rest![12]!, rest![13]!, rest![14]!);
    const cruiseDepth = Math.hypot(cruise![12]!, cruise![13]!, cruise![14]!);
    expect(scaleOf(cruise!) / cruiseDepth).toBeLessThan((scaleOf(rest!) / restDepth) * 0.6);
  });
});
