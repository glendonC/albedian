// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import * as THREE from 'three';
import type { ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { FlightCraftLayer, type CraftRenderer } from '../FlightCraftLayer.js';
import { FILL_AO_ATTRIBUTE, FILL_AO_DEFINE } from '../craftFillOcclusion.js';
import { CRAFTS } from '../crafts.js';
import { flightCameraOf } from '../flightFrame.js';

// craft-ao-fill-one: a model carrying the sky fill's occlusion (_AO, read by three as _ao) is drawn with it on the fill
// alone, and the layer says how many of the model's meshes take it, so a take and the auditor read it off the page.

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

const makeRenderer = (): CraftRenderer => ({ setPixelRatio: () => {}, setSize: () => {}, setClearColor: () => {}, clear: () => {}, dispose: () => {}, render: () => {} });

function box(ao: boolean, material: THREE.Material): THREE.Mesh {
  const g = new THREE.BoxGeometry(2, 1, 1);
  if (ao) g.setAttribute(FILL_AO_ATTRIBUTE, new THREE.Float32BufferAttribute(new Array(g.getAttribute('position').count).fill(0.4), 1));
  return new THREE.Mesh(g, material);
}

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

describe('the craft drawn with the sky fill\'s occlusion it carries', () => {
  const camera = flightCameraOf(there(), 1440, 900)!;

  it('marks every mesh that carries it before the model is drawn, and says so on the layer', async () => {
    const a = new THREE.MeshStandardMaterial();
    const b = new THREE.MeshStandardMaterial();
    const load = async () => {
      const g = new THREE.Group();
      g.add(box(true, a), box(true, b));
      return g;
    };
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['astronaut-emu']!, load, makeRenderer })));
    await act(async () => await new Promise((r) => setTimeout(r, 0)));
    const layer = container.querySelector('[data-testid="flight-craft"]')!;
    expect(layer.getAttribute('data-state')).toBe('drawn');
    expect(layer.getAttribute('data-fill-ao')).toBe('2/2');
    expect(a.defines?.[FILL_AO_DEFINE]).toBe('');
    expect(b.defines?.[FILL_AO_DEFINE]).toBe('');
  });

  it('draws a model without it as before, saying none of its meshes take it', async () => {
    const m = new THREE.MeshStandardMaterial();
    const load = async () => {
      const g = new THREE.Group();
      g.add(box(false, m));
      return g;
    };
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['voyager-1']!, load, makeRenderer })));
    await act(async () => await new Promise((r) => setTimeout(r, 0)));
    const layer = container.querySelector('[data-testid="flight-craft"]')!;
    expect(layer.getAttribute('data-state')).toBe('drawn');
    expect(layer.getAttribute('data-fill-ao')).toBe('0/1');
    expect(m.defines?.[FILL_AO_DEFINE]).toBeUndefined();
  });
});
