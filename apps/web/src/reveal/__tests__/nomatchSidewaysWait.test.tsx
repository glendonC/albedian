// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { choosePhotoOnHome } from '../../components/__tests__/homeEntry.js';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import type { Arrival } from '../arrival.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});
vi.mock('../../spatial/SparkPortalRenderer.js', () => ({
  SparkPortalRenderer: class {
    setObserverCoordinates() {}
    clearObserverCoordinates() {}
    showSkyView() {}
    setDrawing() {}
    holdClock() {}
    nudgeMovement() {}
    nudgeLook() {}
    dragLook() {}
    zoomByWheel() {}
    resize() {}
    dispose() {}
    getRenderLayers() {
      return [];
    }
    getCameraSkyPose() {
      return null;
    }
  },
}));
vi.mock('../../audio/ObservatoryAudioEngine.js', () => ({
  observatoryAudio: { toggle: vi.fn(() => false), setMasterVolume: vi.fn(), setScaleRegime: vi.fn(), playDetentClick: vi.fn() },
}));
vi.mock('../../audio/SkyAudioFeed.js', () => ({ skyAudio: { follow: vi.fn(), setEvidenceClass: vi.fn() } }));
vi.mock('../../components/CoalescingBar.js', () => ({ CoalescingBar: () => null }));
const { SpatialObservatoryViewport } = await import('../../components/SpatialObservatoryViewport.js');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// nomatch-sideways-wait-one (found walking wait-measured-one on 27 Nov): nomatch-sideways-one stands a no-match photo held
// sideways whole at the leading margin, keyed on the session reading as unmatched; before its solve ends a session reads
// so too, so held sideways the waiting photo stood at the leading margin where the sideways record stands it whole and
// centred (UIUX-phone-sideways-READY.md item 2). The leading margin is the no-match screen's only, never the wait's.

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
  delete (window as unknown as Record<string, unknown>).matchMedia;
  delete document.documentElement.dataset.phoneScroll;
  vi.restoreAllMocks();
});

const setWindow = (w: number, h: number) => {
  Object.defineProperty(window, 'innerWidth', { value: w, configurable: true });
  Object.defineProperty(window, 'innerHeight', { value: h, configurable: true });
  (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({
    matches: w <= 720 ? query.includes('max-width: 720px') : h <= 500 && query.includes('max-height: 500px'),
    addEventListener: () => {},
    removeEventListener: () => {},
  });
};
const noop = () => {};
const props = (session: ObservationSession, extra: Record<string, unknown>, url: string | null) => ({
  session,
  setSession: noop,
  selectedFinding: null,
  discoveredMedia: [],
  activeDiscoveredId: null,
  setActiveDiscoveredId: noop,
  activeDiscoveredItem: null,
  uploadedMediaUrl: url,
  setUploadedMediaUrl: noop,
  commandText: '',
  setCommandText: noop,
  onRunUtterance: noop,
  onSelectFinding: noop,
  onPushToTalk: noop,
  ...extra,
});
function open(session: ObservationSession, extra: Record<string, unknown>) {
  act(() => root.render(createElement(SpatialObservatoryViewport, props(session, extra, null))));
  choosePhotoOnHome(container);
  act(() => root.render(createElement(SpatialObservatoryViewport, props(session, extra, 'blob:photo'))));
}
const photo = () => container.querySelector<HTMLImageElement>('[data-testid="source-photo-stage"] img')!;
const waiting = (): Arrival => ({
  fileName: 'night.jpg',
  byteSize: 2_382_523,
  stored: true,
  widthPx: null,
  heightPx: null,
  job: { id: 'job_0123456789abcdef', state: 'running', stages: [] },
  failure: null,
});

describe('nomatch-sideways-wait-one: the waiting photo held sideways stands centred', () => {
  it('centres the waiting photo, and keeps the leading margin for the no-match screen itself', () => {
    // A session that reads as unmatched (the predicted-only sample), as one does before its solve ends
    const session = poseSession(sample('horizon_predicted_only'));
    for (const [w, h] of [[874, 292], [874, 402], [667, 320]] as const) {
      setWindow(w, h);
      open(session, { arrival: waiting() });
      expect(container.querySelector('[data-testid="working-screen"]'), `${w}x${h}`).not.toBeNull();
      const img = photo();
      const left = parseFloat(img.style.left);
      const width = parseFloat(img.style.width);
      expect(left + width / 2, `${w}x${h} wait`).toBeCloseTo(w / 2, 3);
      act(() => root.unmount());
      root = createRoot(container);
      open(session, { arrival: null });
      expect(container.querySelector('.reveal-phone-scroll.is-column'), `${w}x${h}`).not.toBeNull();
      expect(parseFloat(photo().style.left), `${w}x${h} no-match`).toBeCloseTo(24, 3);
      act(() => root.unmount());
      root = createRoot(container);
    }
  });
});
