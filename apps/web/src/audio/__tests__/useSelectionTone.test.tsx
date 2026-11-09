// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CelestialFinding, ObservationSession } from '@albedian/contracts';
import { MusicSelectionStore, selectionOf, useSelectionTone } from '../useSelectionTone';

declare global {
  // React's flag for tests that drive updates through act()
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

/**
 * A catalog-matched Sirius whose catalog reference has the shape astrophysicsOf reads (values made up
 * for the test). No producer sets catalogRef.astrophysics today, so in the app every selection plays
 * plain until one does.
 */
const MATCHED = {
  id: 'f-sirius',
  evidenceClass: 'catalog_matched',
  sourceDetection: { xPx: 300, yPx: 200 },
  catalogRef: { id: 'cat-1', astrophysics: { temperatureK: { median: 9940, lower: 9800, upper: 10100 } } },
} as unknown as CelestialFinding;
const OBSERVED = { id: 'f-spot', evidenceClass: 'observed', sourceDetection: { xPx: 900, yPx: 10 } } as unknown as CelestialFinding;
const NO_DETECTION = { id: 'f-star', evidenceClass: 'catalog_matched', catalogRef: { id: 'cat-2' } } as unknown as CelestialFinding;

const session = (width: number | null) => ({ sourceAsset: width === null ? undefined : { width, height: 600 } }) as unknown as ObservationSession;

describe('what the music hears of a selected finding', () => {
  it('takes the catalog temperature, the evidence class and the pan from the photo, and nothing it lacks', () => {
    expect(selectionOf(MATCHED, session(1000))).toEqual({ id: 'f-sirius', evidenceClass: 'catalog_matched', temperatureK: 9940, panX: -0.4 });
    expect(selectionOf(OBSERVED, session(1000))).toEqual({ id: 'f-spot', evidenceClass: 'observed', temperatureK: null, panX: 0.8 });
    // No detection on the photo, or no photo size: centre; no astrophysics block: no temperature
    expect(selectionOf(NO_DETECTION, session(1000))).toEqual({ id: 'f-star', evidenceClass: 'catalog_matched', temperatureK: null, panX: null });
    expect(selectionOf(MATCHED, session(null))!.panX).toBeNull();
    expect(selectionOf(null, session(1000))).toBeNull();
  });

  it('keeps the selection for the engine and tells its listeners only when the finding changes', () => {
    const store = new MusicSelectionStore();
    const heard = vi.fn();
    store.subscribe(heard);
    store.set(selectionOf(MATCHED, session(1000)));
    store.set(selectionOf(MATCHED, session(1000)));
    store.set(selectionOf(OBSERVED, session(1000)));
    store.set(null);
    expect(heard).toHaveBeenCalledTimes(3);
    expect(store.get()).toBeNull();
  });
});

describe('the viewport hook', () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('puts each newly selected finding in the store, and none when nothing is selected', () => {
    const store = new MusicSelectionStore();
    function Viewport({ finding }: { finding: CelestialFinding | null }) {
      useSelectionTone(finding, session(1000), store);
      return null;
    }
    act(() => root.render(createElement(Viewport, { finding: null })));
    expect(store.get()).toBeNull();
    act(() => root.render(createElement(Viewport, { finding: MATCHED })));
    expect(store.get()).toMatchObject({ id: 'f-sirius', temperatureK: 9940 });
    act(() => root.render(createElement(Viewport, { finding: OBSERVED })));
    expect(store.get()).toMatchObject({ id: 'f-spot', temperatureK: null });
    act(() => root.render(createElement(Viewport, { finding: null })));
    expect(store.get()).toBeNull();
  });
});
