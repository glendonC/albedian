// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';

// steer-hitch-one: the star layer has the steering dome made once the flight is at rest, beside the craft's sky, never
// during the move, so the first steer compiles nothing.

const prepared = vi.hoisted(() => ({ count: 0, args: [] as number[] }));
vi.mock('../../sky/FlightDomeLayer.js', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../sky/FlightDomeLayer.js')>();
  return { ...real, prepareFlightDome: (...args: number[]) => { prepared.count += 1; prepared.args = args; } };
});

const { FlightStarsLayer } = await import('../FlightStarsLayer.js');
type OpenFlightTiles = import('../FlightStarsLayer.js').OpenFlightTiles;

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
const inPhase = (s: ObservationSession, transitionPhase: string, transitionProgress: number) =>
  ({ ...s, spatialView: { ...s.spatialView, transitionPhase, transitionProgress } }) as ObservationSession;

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  prepared.count = 0;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 404 })));
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe('the steering dome made at the flight\'s rest (steer-hitch-one)', () => {
  const openTiles: OpenFlightTiles = async () => ({ setView: () => {}, dispose: () => {} });
  it('is not made during the move, and is made once the flight is at rest', async () => {
    const s = there();
    await act(async () => root.render(createElement(FlightStarsLayer, { session: inPhase(s, 'phase_b_sky_to_space', 0.5), viewportWidthPx: 390, viewportHeightPx: 844, phone: true, openTiles, craft: null })));
    expect(prepared.count).toBe(0);
    await act(async () => root.render(createElement(FlightStarsLayer, { session: inPhase(s, 'at_space', 1), viewportWidthPx: 390, viewportHeightPx: 844, phone: true, openTiles, craft: null })));
    expect(prepared.count).toBe(1);
    // at the window's size and the page's pixel ratio (jsdom: 1), as the dome layer will draw
    expect(prepared.args).toEqual([390, 844, 1]);
  });
});
