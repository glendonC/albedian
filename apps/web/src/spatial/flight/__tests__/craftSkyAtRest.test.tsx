// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession, ObservationSolution } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { FlightStarsLayer, type OpenFlightTiles } from '../FlightStarsLayer.js';
import { CRAFT_SKY_URL } from '../craftLight.js';
import { RevealPhoto } from '../../../reveal/RevealPhoto.js';
import { poseSession, sample } from '../../../components/__tests__/poseSessions.js';

// craft-compile-one version two (the lead's 00:18 line): the craft's sky is decoded and summed once the flight is at
// rest, never on the reveal and never during the move; the reveal at rest reads no texture.

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
let fetched: string[];
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  fetched = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string) => (fetched.push(String(url)), new Response('{}', { status: 404 }))));
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

const textures = () => fetched.filter((u) => u.startsWith('/textures/'));

describe('the craft sky prepared at the flight\'s rest', () => {
  const openTiles: OpenFlightTiles = async () => ({ setView: () => {}, dispose: () => {} });
  it('is not read during the move, and is read once the flight is at rest', async () => {
    const s = there();
    await act(async () => root.render(createElement(FlightStarsLayer, { session: inPhase(s, 'phase_b_sky_to_space', 0.5), viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, craft: null })));
    expect(fetched.filter((u) => u === CRAFT_SKY_URL)).toHaveLength(0);
    await act(async () => root.render(createElement(FlightStarsLayer, { session: inPhase(s, 'at_space', 1), viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, craft: null })));
    await act(async () => await new Promise((r) => setTimeout(r, 0)));
    expect(fetched.filter((u) => u === CRAFT_SKY_URL)).toHaveLength(1);
  });
});

describe('the reveal at rest', () => {
  it('reads no texture, on a verified solve or a pose estimate', async () => {
    for (const solution of [sample('plate_calibrated'), sample('pose_moon_level_head')] as ObservationSolution[]) {
      await act(async () => root.render(createElement(RevealPhoto, { session: poseSession(solution), onSelectFinding: () => undefined, onHome: () => undefined, viewportWidthPx: 1440, viewportHeightPx: 900 })));
      await act(async () => await new Promise((r) => setTimeout(r, 0)));
    }
    expect(textures()).toEqual([]);
  });
});
