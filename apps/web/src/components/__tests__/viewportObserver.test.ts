// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { webglRenderLayersFor } from '../../spatial/webglRenderLayers.js';

/**
 * The observatory viewport hands the renderer the recording's place and capture time for the sky
 * view's horizon (render-10), then shows the layers the renderer declares for them. The renderer
 * is a stand-in that records what it is told and declares its layers by the time it was given.
 */

const stub = vi.hoisted(() => ({
  calls: [] as Array<[number, number, string | null | undefined]>,
  clears: 0,
  // What the stand-in renderer holds: no place, or a place with or without a capture time
  placed: null as null | 'capture' | 'now',
}));

vi.mock('../../spatial/SparkPortalRenderer.js', () => ({
  SparkPortalRenderer: class {
    setObserverCoordinates(latDeg: number, lonDeg: number, capturedAtUtc?: string | null) {
      stub.calls.push([latDeg, lonDeg, capturedAtUtc]);
      stub.placed = capturedAtUtc ? 'capture' : 'now';
    }
    clearObserverCoordinates() {
      stub.clears += 1;
      stub.placed = null;
    }
    setShowcaseMode() {}
    transitionToShowcaseScene() {}
    holdClock() {}
    nudgeMovement() {}
    nudgeLook() {}
    dragLook() {}
    zoomByWheel() {}
    resize() {}
    dispose() {}
    getRenderLayers() {
      return webglRenderLayersFor({ regime: 'earth_capture', blackHole: false, observerTime: stub.placed });
    }
    getCameraSkyPose() {
      return null;
    }
  },
}));

vi.mock('../../audio/ObservatoryAudioEngine.js', () => ({
  observatoryAudio: { toggle: vi.fn(), setMasterVolume: vi.fn(), setScaleRegime: vi.fn(), playDetentClick: vi.fn() },
}));

vi.mock('../../audio/SkyAudioFeed.js', () => ({
  skyAudio: { follow: vi.fn(), setEvidenceClass: vi.fn() },
}));

// The volume bar draws on a 2D canvas, which jsdom lacks; nothing here depends on it
vi.mock('../CoalescingBar.js', () => ({
  CoalescingBar: () => null,
}));

const { SpatialObservatoryViewport } = await import('../SpatialObservatoryViewport.js');

declare global {
  // React's flag for tests that drive updates through act()
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

// Siding Spring Observatory and a February night: test values, not any recording's
const PLACE = { latDeg: -31.277, lonDeg: 149.0661 };
const CAPTURED_AT = '2027-02-06T17:00:00Z';

const noop = () => {};
let container: HTMLDivElement;
let root: Root;

function renderViewport(sourceAsset: Record<string, unknown>): void {
  const session = { sourceAsset } as unknown as ObservationSession;
  act(() => {
    root.render(
      createElement(SpatialObservatoryViewport, {
        session,
        setSession: noop,
        selectedFinding: null,
        discoveredMedia: [],
        activeDiscoveredId: null,
        setActiveDiscoveredId: noop,
        activeDiscoveredItem: null,
        uploadedMediaUrl: null,
        setUploadedMediaUrl: noop,
        commandText: '',
        setCommandText: noop,
        onRunUtterance: noop,
        onSelectFinding: noop,
        onPushToTalk: noop,
        onTriggerUpload: noop,
      })
    );
  });
}

/** Opens the layer disclosure and returns its horizon row, if it has one. */
function horizonRow(): Element | undefined {
  const toggle = container.querySelector<HTMLButtonElement>('.alb-layer-disclosure-toggle');
  expect(toggle).not.toBeNull();
  if (toggle!.getAttribute('aria-expanded') !== 'true') act(() => toggle!.click());
  return Array.from(container.querySelectorAll('.alb-layer-disclosure-item')).find(
    (li) => li.querySelector('.alb-layer-disclosure-title')?.textContent === 'Horizon and compass'
  );
}

/** The horizon row's disclosure text. */
function horizonDisclosure(): string {
  const row = horizonRow();
  expect(row).toBeDefined();
  return row!.querySelector('.alb-layer-disclosure-text')!.textContent ?? '';
}

const panelSummary = () => container.querySelector('.alb-layer-disclosure-toggle')?.textContent ?? '';

/** Every text node and attribute value the viewport renders. */
function everythingShown(): string {
  const parts = [container.textContent ?? ''];
  for (const el of Array.from(container.querySelectorAll('*'))) {
    for (const attr of Array.from(el.attributes)) parts.push(attr.value);
  }
  return parts.join('\n');
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  stub.calls = [];
  stub.clears = 0;
  stub.placed = null;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("the viewport hands the sky view the recording's place and time", () => {
  it('passes the capture time with the place, then shows the horizon disclosed for that time', () => {
    renderViewport({ observerLocation: PLACE, capturedAtUTC: CAPTURED_AT });
    expect(stub.calls).toContainEqual([PLACE.latDeg, PLACE.lonDeg, CAPTURED_AT]);
    expect(horizonDisclosure()).toMatch(/at its capture time/);
  });

  it("passes no time when the recording has none, and the horizon is disclosed for the device's time now", () => {
    renderViewport({ observerLocation: PLACE });
    expect(stub.calls).toContainEqual([PLACE.latDeg, PLACE.lonDeg, null]);
    expect(horizonDisclosure()).toMatch(/\bnow\b/);
  });

  it('passes no place without one, and the panel shows no horizon and reads as a modelled scene', () => {
    renderViewport({ observerLocation: null, capturedAtUTC: CAPTURED_AT });
    expect(stub.calls).toEqual([]);
    expect(panelSummary()).toMatch(/^Modeled scene/);
    expect(horizonRow()).toBeUndefined();
  });

  it('forgets the place and time when a recording without a location follows a located one', () => {
    renderViewport({ observerLocation: PLACE, capturedAtUTC: CAPTURED_AT });
    expect(horizonDisclosure()).toMatch(/at its capture time/);
    expect(panelSummary()).toMatch(/^Mixed evidence/);
    renderViewport({ observerLocation: null });
    expect(stub.clears).toBeGreaterThanOrEqual(1);
    expect(stub.placed).toBeNull();
    expect(panelSummary()).toMatch(/^Modeled scene/);
    expect(horizonRow()).toBeUndefined();
  });

  it("shows and logs neither the recording's place nor its time", () => {
    const methods = ['log', 'info', 'warn', 'error', 'debug'] as const;
    const spies = methods.map((m) => vi.spyOn(console, m).mockImplementation(() => {}));
    try {
      renderViewport({ observerLocation: PLACE, capturedAtUTC: CAPTURED_AT });
      horizonDisclosure();
      const logged = spies.flatMap((spy) => spy.mock.calls.map((args) => args.map(String).join(' '))).join('\n');
      const shown = everythingShown();
      for (const piece of ['31.2', '31°', '149.0', '149°', '2027', '02-06', '17:00', '17h']) {
        expect(shown, piece).not.toContain(piece);
        expect(logged, piece).not.toContain(piece);
      }
      expect(horizonDisclosure()).not.toMatch(/[0-9]/);
    } finally {
      for (const spy of spies) spy.mockRestore();
    }
  });
});
