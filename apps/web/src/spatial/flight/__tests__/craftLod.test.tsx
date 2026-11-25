// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import type { ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { FlightCraftLayer } from '../FlightCraftLayer.js';
import { CRAFTS, CRAFT_FINE_SPAN_PX, HANGAR_CRAFTS, craftModelUrl } from '../crafts.js';
import { flightCameraOf } from '../flightFrame.js';

// craft-lod-one (DECIDED-BY-LEAD 18:27): the finest level when the framed craft spans 600 device pixels or more (the
// laptop and the film at scale two), the middle level at scale one, the phone's level as now, chosen once per mount
// and never switched in flight.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function there(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const session = {
    ...base,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 },
    spatialView: { ...base.spatialView, mode: 'source_2d' },
  } as ObservationSession;
  const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
  return dispatchAlbedianAction(session, { type: 'space.enter', requestId: 'go', expectedViewRevision: session.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated' } as never).session;
}

describe('craftModelUrl: the level by the framed craft\'s span in device pixels', () => {
  const emu = CRAFTS['astronaut-emu']!;
  it('takes the hangar\'s finest level at 600 device pixels or more, the middle level below, the far level on a phone', () => {
    expect(CRAFT_FINE_SPAN_PX).toBe(600);
    expect(emu.fineModelUrl).toMatch(/emu_lod0\.glb$/);
    expect(craftModelUrl(emu, false, 640)).toBe(emu.fineModelUrl);
    expect(craftModelUrl(emu, false, 600)).toBe(emu.fineModelUrl);
    expect(craftModelUrl(emu, false, 599)).toBe(emu.modelUrl);
    expect(craftModelUrl(emu, false, 320)).toBe(emu.modelUrl);
    expect(craftModelUrl(emu, true, 1280)).toBe(emu.phoneModelUrl);
    expect(craftModelUrl(emu, false)).toBe(emu.modelUrl);
  });

  it('gives every hangar craft its finest level where the hangar lists one', () => {
    for (const craft of HANGAR_CRAFTS) {
      expect(craft.fineModelUrl === undefined || /_lod0\.glb$/.test(craft.fineModelUrl)).toBe(true);
      expect(craftModelUrl(craft, false, 640)).toBe(craft.fineModelUrl ?? craft.modelUrl);
    }
    expect(HANGAR_CRAFTS.filter((c) => c.fineModelUrl).length).toBe(HANGAR_CRAFTS.length);
  });
});

describe('FlightCraftLayer: the level chosen once a mount', () => {
  let container: HTMLDivElement;
  let root: Root;
  const ratio = Object.getOwnPropertyDescriptor(window, 'devicePixelRatio');
  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    if (ratio) Object.defineProperty(window, 'devicePixelRatio', ratio);
    vi.restoreAllMocks();
  });
  const setRatio = (r: number) => Object.defineProperty(window, 'devicePixelRatio', { value: r, configurable: true });
  const makeRenderer = () => ({ setPixelRatio: vi.fn(), setSize: vi.fn(), setClearColor: vi.fn(), dispose: vi.fn(), clear: vi.fn(), render: vi.fn() });

  it('asks for the finest level at scale two on the laptop (320 px framed is 640 device pixels), the middle level at scale one', async () => {
    const camera = flightCameraOf(there(), 1440, 900)!;
    const emu = CRAFTS['astronaut-emu']!;
    for (const [r, want] of [[2, emu.fineModelUrl], [1, emu.modelUrl]] as const) {
      setRatio(r);
      const load = vi.fn(async (_url: string) => new THREE.Group());
      await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: emu, load, makeRenderer })));
      expect(load.mock.calls.map(([u]) => u)).toEqual([want]);
      act(() => root.unmount());
      root = createRoot(container);
    }
  });

  it('keeps the level it chose for the whole mount, through a switch and a change of scale', async () => {
    setRatio(2);
    const camera = flightCameraOf(there(), 1440, 900)!;
    const load = vi.fn(async (_url: string) => new THREE.Group());
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['astronaut-emu']!, load, makeRenderer })));
    setRatio(1);
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS.jwst!, load, makeRenderer })));
    expect(load.mock.calls.map(([u]) => u)).toEqual([CRAFTS['astronaut-emu']!.fineModelUrl, CRAFTS.jwst!.fineModelUrl]);
  });

  it('draws the phone\'s level on a phone at any scale', async () => {
    setRatio(3);
    const camera = flightCameraOf(there(), 390, 844)!;
    const load = vi.fn(async (_url: string) => new THREE.Group());
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS.jwst!, load, makeRenderer, phone: true })));
    expect(load.mock.calls.map(([u]) => u)).toEqual([CRAFTS.jwst!.phoneModelUrl]);
  });
});
