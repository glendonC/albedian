// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession, ObservationSolution } from '@albedian/contracts';
import { poseSession, sample } from '../../../components/__tests__/poseSessions.js';
import { RevealPhoto } from '../../../reveal/RevealPhoto.js';

// The There screen's disclosure takes the star layer's count of stars kept on the sky sphere (D27 item 2). WebGL 2 is
// not in jsdom, so both drawn layers are stand-ins: the star layer reports the count each test gives it (the verdict's
// note a on stacks 1 to 3: no test fed a count through onNoDistanceCount into the status).
const told = vi.hoisted(() => ({ count: 0 }));
vi.mock('../FlightStarsLayer.js', async () => {
  const { createElement: h, useEffect } = await import('react');
  return {
    FlightStarsLayer: (props: { onNoDistanceCount?: (count: number) => void }) => {
      useEffect(() => {
        props.onNoDistanceCount?.(told.count);
      });
      return h('div', { 'data-testid': 'flight-stars' });
    },
  };
});
vi.mock('../../../reveal/RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../../reveal/RealSkyLayer.js')>();
  const { createElement: h, useEffect } = await import('react');
  return {
    ...original,
    RealSkyLayer: (props: { onDrawn?: (drawn: boolean) => void }) => {
      useEffect(() => {
        props.onDrawn?.(true);
      }, [props.onDrawn]);
      return h('div', { 'data-testid': 'real-sky' });
    },
  };
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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

function rigelThere(): ObservationSession {
  const session = poseSession({ ...sample('plate_calibrated') } as ObservationSolution);
  const rigel = session.observationSolution!.findings.find((f) => f.title === 'Rigel')!;
  return { ...session, selectedFindingId: rigel.id } as ObservationSession;
}

function statusLines(count: number): string[] {
  told.count = count;
  act(() => {
    root.render(
      createElement(RevealPhoto, {
        session: rigelThere(),
        onSelectFinding: () => {},
        onHome: () => {},
        viewportWidthPx: 1440,
        viewportHeightPx: 900,
        flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 },
      })
    );
  });
  return [...container.querySelectorAll('[data-testid="reveal-status"] .reveal-status__line')].map((p) => p.textContent ?? '');
}

describe('There: the count of stars with no measured distance, from the star layer to the disclosure', () => {
  it('says how many stars stay where they appear from Earth, in the record\'s words, when the layer counts any', () => {
    expect(statusLines(1)).toContain('1 star has no measured distance, so it stays where it appears from Earth.');
    expect(statusLines(1200)).toContain('1,200 stars have no measured distance, so they stay where they appear from Earth.');
  });

  it('says nothing of it when every star drawn has its distance', () => {
    expect(statusLines(0).some((line) => line.includes('no measured distance'))).toBe(false);
  });
});
