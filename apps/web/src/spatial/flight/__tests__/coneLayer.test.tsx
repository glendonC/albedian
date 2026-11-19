// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { ConeLayer } from '../ConeLayer.js';
import { CONE_ORBIT_START, coneGeometryOf, coneStarView, pinchWheelDelta } from '../coneView.js';

// D30 item 7: on a touch screen two fingers pinch the cone view closer, one finger turns it. WebGL 2 is not in jsdom,
// so the photo's plane fails to draw; the layer's own surface still takes the hand.
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
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

/** A pointer event as jsdom can make one: a mouse event carrying the pointer's id. */
function finger(el: Element, type: string, id: number, x: number, y: number) {
  const e = new MouseEvent(type, { clientX: x, clientY: y, bubbles: true });
  Object.defineProperty(e, 'pointerId', { value: id });
  act(() => void el.dispatchEvent(e));
}

describe('ConeLayer: the hand on a touch screen (D30 item 7)', () => {
  it('pinches closer with two fingers spreading, without turning, and turns with one', () => {
    const session = there();
    const view = coneStarView(session, CONE_ORBIT_START, 390, 844, true)!;
    const geometry = coneGeometryOf(session)!;
    const onDrag = vi.fn();
    const onWheel = vi.fn();
    act(() => root.render(createElement(ConeLayer, { session, view, geometry, keepOut: [], onDrag, onWheel })));
    const el = container.querySelector('[data-testid="flight-cone"]')!;
    finger(el, 'pointerdown', 1, 100, 400);
    finger(el, 'pointerdown', 2, 200, 400);
    finger(el, 'pointermove', 2, 300, 400);
    expect(onWheel).toHaveBeenCalledTimes(1);
    expect(onWheel.mock.calls[0]![0]).toBeCloseTo(pinchWheelDelta(100, 200), 9);
    expect(onWheel.mock.calls[0]![0]).toBeLessThan(0);
    expect(onDrag).not.toHaveBeenCalled();
    finger(el, 'pointerup', 2, 300, 400);
    finger(el, 'pointerup', 1, 100, 400);
    finger(el, 'pointerdown', 3, 100, 400);
    finger(el, 'pointermove', 3, 120, 410);
    expect(onDrag).toHaveBeenCalledWith(20, 10);
    expect(onWheel).toHaveBeenCalledTimes(1);
  });
});
