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

// phone-star-open-one: the craft is drawn through the same steering frame's shift as the stars, so it keeps its place on
// the window (its framing point) while the sky's frame moves under it, and it is turned as the moved frame sees it.

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

describe('the craft through the steering frame\'s shift', () => {
  const camera = flightCameraOf(there(), 390, 844)!;
  const rows: WcsCameraRows = steerStartOf(camera).rows;
  const at: Vec3 = camera.observerPc;

  it('stays at its framing point on the window and is seen through the moved frame', async () => {
    const { drawn, makeRenderer } = recordingRenderer();
    const drawRef: { current: ((s: { positionPc: Vec3; rows: WcsCameraRows; craftRows?: WcsCameraRows; alive?: AliveSignals; shiftPx?: readonly [number, number] }) => void) | null } = { current: null };
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['astronaut-emu']!, load: model, makeRenderer, drawRef })));
    await act(async () => await new Promise((r) => setTimeout(r, 0)));
    const layer = container.querySelector('[data-testid="flight-craft"]')!;
    const centre = () => {
      const [x0, y0, x1, y1] = layer.getAttribute('data-box')!.split(',').map(Number);
      return [(x0! + x1!) / 2, (y0! + y1!) / 2];
    };
    act(() => drawRef.current!({ positionPc: at, rows, craftRows: rows, alive: ALIVE_AT_REST }));
    const plain = drawn.at(-1)!;
    const plainAt = centre();
    act(() => drawRef.current!({ positionPc: at, rows, craftRows: rows, alive: ALIVE_AT_REST, shiftPx: [60, -90] }));
    const moved = drawn.at(-1)!;
    const movedAt = centre();
    // Its place on the window holds (the framing point is the window's), within the box's own rounding
    expect(Math.hypot(movedAt[0]! - plainAt[0]!, movedAt[1]! - plainAt[1]!)).toBeLessThan(2);
    // The frame moved under it, so the camera sees it along another ray: its place in the scene is not the same
    expect(Math.hypot(moved[12]! - plain[12]!, moved[13]! - plain[13]!, moved[14]! - plain[14]!)).toBeGreaterThan(1e-3);
  });
});
