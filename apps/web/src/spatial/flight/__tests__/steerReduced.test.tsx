// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import type { OpenFlightTiles } from '../FlightStarsLayer.js';

// MOTION-READY-web.md section seven, DECIDED-BY-LEAD 22 Nov 21:50: the flight's steering hands are told of Reduce
// Motion, so a released drag stops at once there (steering.test.ts holds the rule itself)

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const made = vi.hoisted(() => [] as Array<(() => boolean) | undefined>);
vi.mock('../steering.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../steering.js')>();
  class TellingHands extends original.SteerHands {
    constructor(reduced?: () => boolean) {
      super(reduced);
      made.push(reduced);
    }
  }
  return { ...original, SteerHands: TellingHands };
});
const { FlightStarsLayer } = await import('../FlightStarsLayer.js');

function there(reducedMotion: boolean): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const session = {
    ...base,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 },
    spatialView: { ...base.spatialView, mode: 'source_2d' },
  } as ObservationSession;
  const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
  const out = dispatchAlbedianAction(session, { type: 'space.enter', requestId: 'go', expectedViewRevision: session.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated' });
  return { ...out.session, reducedMotion } as ObservationSession;
}

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  made.length = 0;
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe('the flight tells its steering hands of Reduce Motion', () => {
  for (const reduced of [true, false]) {
    it(`hands steering a way to read it (${reduced ? 'on' : 'off'})`, async () => {
      vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
      await act(async () => root.render(createElement(FlightStarsLayer, { session: there(reduced), viewportWidthPx: 1440, viewportHeightPx: 900, openTiles: vi.fn<OpenFlightTiles>(), steering: true })));
      expect(container.querySelector('[data-testid="flight-sky"]')?.getAttribute('data-steering')).toBe('true');
      // Every steering hands the flight makes (the ref's and the one steering starts with) can read it
      expect(made.length).toBeGreaterThan(1);
      expect(made.map((hands) => hands?.())).toEqual(made.map(() => reduced));
    });
  }
});
