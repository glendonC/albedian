// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import type { ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { FlightCraftLayer } from '../FlightCraftLayer.js';
import { FlightStarsLayer, type OpenFlightTiles } from '../FlightStarsLayer.js';
import { CRAFTS } from '../crafts.js';
import { flightCameraOf } from '../flightFrame.js';

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

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe('FlightCraftLayer', () => {
  it('says it failed, with no words, where the browser cannot draw it', async () => {
    const load = vi.fn(async () => new THREE.Group());
    const camera = flightCameraOf(there(), 1440, 900)!;
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['voyager-1']!, load })));
    const layer = container.querySelector('[data-testid="flight-craft"]')!;
    expect(layer.getAttribute('data-state')).toBe('failed');
    expect(layer.getAttribute('data-craft')).toBe('voyager-1');
    expect(layer.textContent).toBe('');
    expect(layer.getAttribute('aria-hidden')).toBe('true');
    expect(load).not.toHaveBeenCalled();
  });
});

describe('FlightStarsLayer with a craft', () => {
  const openTiles: OpenFlightTiles = async () => ({ setView: () => {}, dispose: () => {} });

  it('draws the craft only when one is asked for', async () => {
    await act(async () => root.render(createElement(FlightStarsLayer, { session: there(), viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, craft: null })));
    expect(container.querySelector('[data-testid="flight-craft"]')).toBeNull();
    await act(async () => root.render(createElement(FlightStarsLayer, { session: there(), viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, craft: CRAFTS['astronaut-emu']! })));
    expect(container.querySelector('[data-testid="flight-craft"]')!.getAttribute('data-craft')).toBe('astronaut-emu');
  });
});
