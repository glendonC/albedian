// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';

// go-there-predicted-glow-one: on the predicted sky the flight's glow goes up and its programs link while the sky is at
// rest, as a photo's do (RevealPhoto prepares them on a verified solve or a pose estimate only), and ESO's photograph,
// which a flight from the predicted sky never draws, does not go up.

const prepared = vi.hoisted(() => ({ sky: [] as Array<boolean | undefined>, flight: 0 }));
vi.mock('../../../reveal/RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../../reveal/RealSkyLayer.js')>();
  const { createElement: h } = await import('react');
  return {
    ...original,
    prepareRealSky: (photograph?: boolean) => void prepared.sky.push(photograph),
    RealSkyLayer: () => h('div', { className: 'real-sky', 'data-testid': 'real-sky', 'data-state': 'drawn' }),
  };
});
vi.mock('../prepareFlight.js', () => ({ prepareFlight: () => void (prepared.flight += 1) }));
vi.mock('../../SparkPortalRenderer.js', () => ({
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
      return [{ kind: 'catalog_stars', evidenceClass: 'simulated', label: 'Catalog stars', sources: [] }];
    }
    getCameraSkyPose() {
      return null;
    }
    lookAtSky() {}
    setSkyPicture(picture: string, _limitMag: number, onShown?: (shown: string) => void) {
      onShown?.(picture);
    }
  },
}));
vi.mock('../../../audio/ObservatoryAudioEngine.js', () => ({
  observatoryAudio: { toggle: vi.fn(() => false), setMasterVolume: vi.fn(), setScaleRegime: vi.fn(), playDetentClick: vi.fn() },
}));
vi.mock('../../../audio/SkyAudioFeed.js', () => ({ skyAudio: { follow: vi.fn(), setEvidenceClass: vi.fn() } }));
vi.mock('../../../components/CoalescingBar.js', () => ({ CoalescingBar: () => null }));

const { SpatialObservatoryViewport } = await import('../../../components/SpatialObservatoryViewport.js');
const { poseSession, sample } = await import('../../../components/__tests__/poseSessions.js');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  prepared.sky = [];
  prepared.flight = 0;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});
const noop = () => {};

/** The predicted-only sample opened into its predicted sky, as Show predicted sky does. */
function predictedSky(): ObservationSession {
  const session = poseSession(sample('horizon_predicted_only'), 'image');
  return dispatchAlbedianAction(session, { type: 'sky.show', requestId: 'show', expectedViewRevision: session.viewRevision, mode: 'predicted', observationSolutionId: session.observationSolution!.id }).session;
}
function renderViewport(session: ObservationSession) {
  act(() => {
    root.render(
      createElement(SpatialObservatoryViewport, {
        session, setSession: noop, selectedFinding: null, discoveredMedia: [], activeDiscoveredId: null, setActiveDiscoveredId: noop,
        activeDiscoveredItem: null, uploadedMediaUrl: 'blob:photo', setUploadedMediaUrl: noop, commandText: '', setCommandText: noop,
        onRunUtterance: noop, onSelectFinding: noop, onPushToTalk: noop,
      })
    );
  });
}

describe('the predicted sky prepares its flight at rest (go-there-predicted-glow-one)', () => {
  it('sends the glow alone up and links the flight\'s programs while the predicted sky is on screen', () => {
    renderViewport(predictedSky());
    expect(prepared.sky).toEqual([false]);
    expect(prepared.flight).toBe(1);
  });

  it('prepares nothing for the predicted-only photo itself, before its sky is shown', () => {
    renderViewport(poseSession(sample('horizon_predicted_only'), 'image'));
    expect(prepared.sky).toEqual([]);
    expect(prepared.flight).toBe(0);
  });
});
