// @vitest-environment jsdom
import { act, createElement, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { phoneSlotCrop } from '../phoneSlot.js';
import { RevealPhoto } from '../RevealPhoto.js';
import { PHONE_STEER_LINE } from '../../spatial/flight/steering.js';

// The sky layer never reports itself drawn here: a flight's first moments, before its picture is on screen, which a slow
// phone stretches (the lead's line stamped 12:05 on 27 Nov: run the route with the processor slowed six times). The
// steering case asks it to report itself drawn while mounted and not drawn as it leaves, as the real layer does
const sky = vi.hoisted(() => ({ draws: false }));
vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  const { createElement: h, useEffect } = await import('react');
  const RealSkyLayer = ({ onDrawn }: { onDrawn?: (drawn: boolean) => void }) => {
    useEffect(() => {
      if (!sky.draws) return undefined;
      onDrawn?.(true);
      return () => onDrawn?.(false);
    }, []);
    return sky.draws ? h('div', { 'data-testid': 'real-sky-layer' }) : null;
  };
  return { ...original, RealSkyLayer };
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
  sky.draws = false;
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

// nomatch-steer-test-one (DECIDED-BY-LEAD 13:31 on 27 Nov, EXPORT's finding): a drag on the sky takes the phone's
// steering, and while steering the real sky layer leaves (only the flight's own dome is drawn), so the sky is no longer
// on screen. On main before 6356ee8c7 the no-match sentence came back over the readout for the whole steer, at any speed
// (232 frames in a take with the processor slowed six times); the cases above never steer, so a rule that held the screen
// out only while not steering passed them
const pointer = (el: Element, type: string, x: number, y: number) =>
  act(() => {
    el.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: x, clientY: y }));
  });
function Steered({ session, w, h, flight }: { session: ObservationSession; w: number; h: number; flight: typeof FLIGHT | null }) {
  const [steering, setSteering] = useState(false);
  return createElement(RevealPhoto, { session, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: w, viewportHeightPx: h, phone: true, restoration: { phase: 'idle' }, onShowPredictedSky: () => {}, flight, phoneSteering: steering, onPhoneSteer: () => setSteering(true) } as unknown as Parameters<typeof RevealPhoto>[0]);
}

describe('nomatch-steer-test-one: the no-match screen out of the frame for the whole steer', () => {
  for (const [w, h] of [[390, 844], [402, 714], [375, 667], [874, 292]] as const) {
    it(`holds it out at ${w} by ${h} from the drag that takes the steering to its release`, () => {
      sky.draws = true;
      const session = noMatch(w, h);
      const out = () => {
        expect(q('[data-testid="flight-readout"]')).not.toBeNull();
        expect(q('[data-testid="reveal-phone-scroll"]')).toBeNull();
        expect(q('[data-testid="reveal-status"].is-unmatched')).toBeNull();
      };
      act(() => root.render(createElement(Steered, { session, w, h, flight: null })));
      expect(q('[data-testid="reveal-phone-scroll"]')).not.toBeNull();
      // In flight, the real sky drawn
      act(() => root.render(createElement(Steered, { session, w, h, flight: FLIGHT })));
      expect(q('[data-testid="real-sky-layer"]')).not.toBeNull();
      out();
      // A drag on the sky: past four pixels it takes the steering and the real sky layer leaves
      const flightSky = q('[data-testid="flight-sky"]')!;
      expect(q('[data-testid="steer-line"]')).toBeNull();
      pointer(flightSky, 'pointerdown', 120, 300);
      pointer(flightSky, 'pointermove', 150, 304);
      expect(q('[data-testid="steer-line"]')?.textContent).toBe(PHONE_STEER_LINE);
      expect(q('[data-testid="real-sky-layer"]')).toBeNull();
      out();
      // The rest of the steer and its release
      for (const x of [180, 210, 160, 90]) {
        pointer(q('[data-testid="flight-sky"]')!, 'pointermove', x, 310);
        out();
      }
      pointer(q('[data-testid="flight-sky"]')!, 'pointerup', 90, 310);
      out();
    });
  }
});
