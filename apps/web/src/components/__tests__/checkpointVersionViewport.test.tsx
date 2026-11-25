// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { EMPTY_MEDIA_VARIANT_ID, returnCheckpointOf } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { withPickedVersion } from '../../reveal/shownRestoration.js';
import { choosePhotoOnHome } from './homeEntry.js';

// checkpoint-version-one (DECIDED-BY-LEAD 18:46 on 24 Nov): the viewport writes the row RevealPhoto says is pressed to
// the session's active variant, so the moment its own Real sky pill saves carries it, and its photo pill (Back to
// Moment) brings it back. RevealPhoto stands in here and keeps the props the viewport hands it.

const seen = vi.hoisted(() => ({ props: undefined as undefined | { onPickVersion?: (id: string | null) => void } }));
vi.mock('../../reveal/RevealPhoto.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../reveal/RevealPhoto.js')>();
  return {
    ...original,
    RevealPhoto: (props: { onPickVersion?: (id: string | null) => void }) => {
      seen.props = props;
      return null;
    },
  };
});
vi.mock('../../spatial/SparkPortalRenderer.js', () => ({
  SparkPortalRenderer: class {
    setObserverCoordinates() {}
    clearObserverCoordinates() {}
    setShowcaseMode() {}
    transitionToShowcaseScene() {}
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
vi.mock('../CoalescingBar.js', () => ({ CoalescingBar: () => null }));

const { SpatialObservatoryViewport } = await import('../SpatialObservatoryViewport.js');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
// Real sky opens where the browser can draw it (canDrawRealSky): jsdom has no WebGL2, so the test says it has
(window as unknown as { WebGL2RenderingContext?: unknown }).WebGL2RenderingContext = class {};

const OLDER = 'var_0000000000000001';

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

function mount() {
  const base = createSyntheticCalibratedSession();
  let current = {
    ...base,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', mimeType: 'image/jpeg', uri: 'blob:photo', width: 3024, height: 4032, durationMs: 0 },
    mediaVariants: [],
    activeVariantId: EMPTY_MEDIA_VARIANT_ID,
    selectedFindingId: null,
    spatialView: { ...base.spatialView, mode: 'source_2d' },
  } as ObservationSession;
  const noop = () => {};
  const render = (uploadedMediaUrl: string | null = 'blob:photo') =>
    act(() =>
      root.render(
        createElement(SpatialObservatoryViewport, {
          session: current,
          setSession: (update: ObservationSession | ((prev: ObservationSession) => ObservationSession)) => {
            current = typeof update === 'function' ? update(current) : update;
          },
          selectedFinding: null,
          discoveredMedia: [],
          activeDiscoveredId: null,
          setActiveDiscoveredId: noop,
          activeDiscoveredItem: null,
          uploadedMediaUrl,
          setUploadedMediaUrl: noop,
          commandText: '',
          setCommandText: noop,
          onRunUtterance: noop,
          onSelectFinding: noop,
          onPushToTalk: noop,
        } as unknown as Parameters<typeof SpatialObservatoryViewport>[0])
      )
    );
  render(null);
  choosePhotoOnHome(container);
  render();
  const pill = (label: string) => [...container.querySelectorAll<HTMLButtonElement>('.reveal-nav button')].find((b) => b.textContent === label);
  return { render, pill, session: () => current };
}

describe('withPickedVersion', () => {
  it('makes the row pressed the active variant, none once let go, and the same session when nothing changes', () => {
    const s = mount().session();
    const picked = withPickedVersion(s, OLDER);
    expect(picked.activeVariantId).toBe(OLDER);
    expect(withPickedVersion(picked, OLDER)).toBe(picked);
    expect(withPickedVersion(picked, null).activeVariantId).toBe(EMPTY_MEDIA_VARIANT_ID);
    expect(withPickedVersion(s, null)).toBe(s);
  });
});

describe('the viewport keeps the row pressed through its own Real sky pill and Back to Moment', () => {
  it('writes the row RevealPhoto reports to the session, saves it on leaving, and brings it back', () => {
    const v = mount();
    expect(seen.props?.onPickVersion).toBeTypeOf('function');
    act(() => seen.props!.onPickVersion!(OLDER));
    expect(v.session().activeVariantId).toBe(OLDER);
    v.render();
    // Real sky, the viewport's own pill: the moment saved carries the row pressed
    expect(v.pill('Real sky')?.disabled).toBe(false);
    act(() => v.pill('Real sky')!.click());
    expect(v.session().spatialView.mode).toBe('registered_sky');
    expect(returnCheckpointOf(v.session())!.mediaVariantId).toBe(OLDER);
    v.render();
    // Back to the photo: Back to Moment restores it
    act(() => v.pill('Your photo')!.click());
    expect(v.session().spatialView.mode).toBe('source_2d');
    expect(v.session().activeVariantId).toBe(OLDER);
  });

  it('lets it go when RevealPhoto says so', () => {
    const v = mount();
    act(() => seen.props!.onPickVersion!(OLDER));
    v.render();
    act(() => seen.props!.onPickVersion!(null));
    expect(v.session().activeVariantId).toBe(EMPTY_MEDIA_VARIANT_ID);
  });
});
