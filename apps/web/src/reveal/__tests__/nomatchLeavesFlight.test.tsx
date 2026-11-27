// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { phoneSlotCrop } from '../phoneSlot.js';
import { RevealPhoto } from '../RevealPhoto.js';

// The sky layer never reports itself drawn here: a flight's first moments, before its picture is on screen, which a slow
// phone stretches (the lead's line stamped 12:05 on 27 Nov: run the route with the processor slowed six times)
vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// nomatch-leaves-flight-one (SPATIAL's still on main before batch 195: in a flight from a no-match photo's predicted sky
// the no-match sentence stood over the flight readout's numbers at the phone's size; DECIDED-BY-LEAD 11:43 and 12:05 on
// 27 Nov). Read from the code: at phone width the no-match screen stood whenever the sky was not yet drawn on screen, and
// a flight's sky is drawn only once its layer reports it; until batch 195 the predicted sky's flight ran on bare black for
// seconds, now about half a second, longer on a slow device. The no-match screen is out of the frame once the flight's
// readout is in it, whichever way round: leaving for the flight and coming Back to Moment.

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants: [], attempts: [] }), { status: 200 }));
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  delete document.documentElement.dataset.phoneScroll;
  vi.restoreAllMocks();
});

const q = (s: string) => container.querySelector(s);
/** The predicted-only sample at phone width, its star picked as Go there leaves it picked. */
function noMatch(w: number, h: number): ObservationSession {
  const session = poseSession(sample('horizon_predicted_only'));
  const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'predicted')!;
  return { ...session, selectedFindingId: star.id, cropTransform: { ...session.cropTransform, ...phoneSlotCrop(3024, 4032, w, h) } } as ObservationSession;
}
const FLIGHT = { name: 'Sirius', toStarLy: 0.4, fromEarthLy: 8.2 };
const show = (session: ObservationSession, w: number, h: number, extra: Record<string, unknown>) =>
  act(() =>
    root.render(
      createElement(RevealPhoto, { session, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: w, viewportHeightPx: h, phone: true, restoration: { phase: 'idle' }, onShowPredictedSky: () => {}, ...extra } as unknown as Parameters<typeof RevealPhoto>[0])
    )
  );

describe('nomatch-leaves-flight-one: the no-match screen out of the frame once the flight\'s readout is in it', () => {
  for (const [w, h] of [[390, 844], [402, 714], [375, 667], [874, 292]] as const) {
    it(`holds it out at ${w} by ${h}, leaving for the flight and coming Back to Moment`, () => {
      const session = noMatch(w, h);
      // At rest on the photo: the no-match screen
      show(session, w, h, { flight: null });
      expect(q('[data-testid="reveal-phone-scroll"]')).not.toBeNull();
      // The flight's first frames, its sky not yet drawn: the readout in, the no-match screen out
      show(session, w, h, { flight: FLIGHT });
      expect(q('[data-testid="flight-readout"]')).not.toBeNull();
      expect(q('[data-testid="reveal-phone-scroll"]')).toBeNull();
      expect(q('[data-testid="reveal-status"].is-unmatched')).toBeNull();
      // Back to Moment: the readout still in while the flight returns, the no-match screen still out
      show(session, w, h, { flight: FLIGHT, returning: true });
      expect(q('[data-testid="flight-readout"]')).not.toBeNull();
      expect(q('[data-testid="reveal-phone-scroll"]')).toBeNull();
      // Back on the photo, the readout gone: the no-match screen again
      show(session, w, h, { flight: null, returning: false });
      expect(q('[data-testid="flight-readout"]')).toBeNull();
      expect(q('[data-testid="reveal-phone-scroll"]')).not.toBeNull();
    });
  }
});
