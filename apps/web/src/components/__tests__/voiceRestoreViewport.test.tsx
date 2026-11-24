// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MediaVariantSchema, type MediaVariant, type ObservationSession } from '@albedian/contracts';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { choosePhotoOnHome } from './homeEntry.js';

// voice-restore-one: the viewport keeps in the session exactly the restored version RevealPhoto says it shows. RevealPhoto
// stands in here and keeps the props the viewport hands it; the rest of the viewport is the real one.

const seen = vi.hoisted(() => ({ props: undefined as undefined | { onShownRestoration?: (variant: MediaVariant | null) => void } }));
vi.mock('../../reveal/RevealPhoto.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../reveal/RevealPhoto.js')>();
  return {
    ...original,
    RevealPhoto: (props: { onShownRestoration?: (variant: MediaVariant | null) => void }) => {
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
const here = path.dirname(fileURLToPath(import.meta.url));
const media = JSON.parse(fs.readFileSync(path.resolve(here, '../../../../../tests/fixtures/media_contract.json'), 'utf8')) as { valid: { MediaVariant: Array<Record<string, unknown>> } };
const template = media.valid.MediaVariant.find((v) => v.kind === 'restoration')!;

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

describe('the viewport keeps the restored version on screen in the session', () => {
  it('takes what RevealPhoto reports, and lets it go when RevealPhoto reports none', () => {
    const base = createSyntheticCalibratedSession();
    let current = {
      ...base,
      sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', mimeType: 'image/jpeg', uri: 'blob:photo', width: 3024, height: 4032, durationMs: 0 },
      mediaVariants: [],
      spatialView: { ...base.spatialView, mode: 'source_2d' },
    } as ObservationSession;
    const setSession = (update: ObservationSession | ((prev: ObservationSession) => ObservationSession)) => {
      current = typeof update === 'function' ? update(current) : update;
    };
    const noop = () => {};
    const render = (uploadedMediaUrl: string | null) =>
      act(() =>
        root.render(
          createElement(SpatialObservatoryViewport, {
            session: current,
            setSession,
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
          } as unknown as Parameters<typeof SpatialObservatoryViewport>[0]),
        ),
      );
    render(null);
    choosePhotoOnHome(container);
    render('blob:photo');
    expect(seen.props?.onShownRestoration).toBeTypeOf('function');
    const sha = current.sourceAsset!.sha256;
    const variant = MediaVariantSchema.parse({ ...template, sourceSha256: sha, sourceAssetId: `src_${sha.slice(0, 16)}`, uri: `/api/media/${sha}/variants/${String(template.id)}/file` });
    // Nothing shown while nothing is held is no update at all
    const before = current;
    act(() => seen.props!.onShownRestoration!(null));
    expect(current).toBe(before);
    act(() => seen.props!.onShownRestoration!(variant));
    expect(current.mediaVariants.map((v) => v.id)).toEqual([variant.id]);
    // The app draws again with the new session, as React does
    render('blob:photo');
    act(() => seen.props!.onShownRestoration!(null));
    expect(current.mediaVariants).toEqual([]);
  });
});
