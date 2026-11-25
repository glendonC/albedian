// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSolution } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { RevealPhoto } from '../RevealPhoto.js';

// realsky-upload-one (LEAD ANSWER 18:41): once the photo's reveal shows a verified solve, the Real sky layer is asked to
// prepare its panorama, so it goes up while the photo is at rest, before Real sky or Go there asks for it.

const sky = vi.hoisted(() => ({ prepared: 0, flight: 0 }));
vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, prepareRealSky: () => (sky.prepared += 1) };
});
vi.mock('../../spatial/flight/prepareFlight.js', () => ({ prepareFlight: () => (sky.flight += 1) }));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const noop = () => undefined;
const calibrated = (over: Partial<ObservationSolution> = {}) => poseSession({ ...sample('plate_calibrated'), ...over } as ObservationSolution);

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  sky.prepared = 0;
  sky.flight = 0;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const show = (session: ReturnType<typeof calibrated>) =>
  act(() => root.render(createElement(RevealPhoto, { session, onSelectFinding: noop, onHome: noop, viewportWidthPx: 1440, viewportHeightPx: 900 })));

describe('the reveal prepares the Real sky panorama', () => {
  it('asks once a verified solve shows, with neither Real sky nor There on, and not again as the reveal draws on', () => {
    const session = calibrated();
    show(session);
    expect(container.querySelector('[data-testid="real-sky"]')).toBeNull();
    expect(sky.prepared).toBe(1);
    // The flight's programs too, at the same rest
    expect(sky.flight).toBe(1);
    show({ ...session, selectedFindingId: session.observationSolution!.findings[0]!.id });
    expect(sky.prepared).toBe(1);
  });

  it('does not ask for a photo with no verified solve: stars predicted from the horizon only, or too few found to solve', () => {
    show(poseSession(sample('horizon_predicted_only')));
    show(poseSession(sample('plate_too_few_sources')));
    expect(sky.prepared).toBe(0);
    expect(sky.flight).toBe(0);
  });
});
