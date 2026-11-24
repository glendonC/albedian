// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { HOME_SCREEN, choosePhotoOnHome } from './homeEntry.js';

/**
 * While the home covers the canvas no frame of the scene can be seen, so the viewport tells its renderer to stop
 * drawing, and to draw again when the home leaves (web-home-idle-1). The renderer is a stand-in recording each change of
 * its drawing state, in order, starting from the first instruction: being told what it is already doing changes
 * nothing, as in both real renderers (rendererDrawing.test.ts).
 */

const stub = vi.hoisted(() => ({ drawing: [] as boolean[], showcase: [] as boolean[], log: [] as string[] }));

vi.mock('../../spatial/SparkPortalRenderer.js', () => ({
  SparkPortalRenderer: class {
    private drawingNow: boolean | null = null;
    setDrawing(drawing: boolean) {
      stub.log.push(`drawing:${drawing}`);
      if (drawing === this.drawingNow) return;
      this.drawingNow = drawing;
      stub.drawing.push(drawing);
    }
    setObserverCoordinates() {}
    clearObserverCoordinates() {}
    showSkyView() {
      stub.log.push('sky');
    }
    setShowcaseMode(enabled: boolean) {
      stub.showcase.push(enabled);
      stub.log.push(`showcase:${enabled}`);
    }
    transitionToShowcaseScene(sceneKey: string) {
      stub.log.push(`scene:${sceneKey}`);
    }
    holdClock() {}
    nudgeMovement() {}
    nudgeLook() {}
    dragLook() {}
    zoomByWheel() {}
    resize() {}
    dispose() {}
    getRenderLayers() {
      return null;
    }
    getCameraSkyPose() {
      return null;
    }
  },
}));
vi.mock('../../audio/ObservatoryAudioEngine.js', () => ({
  observatoryAudio: { toggle: vi.fn(), setMasterVolume: vi.fn(), setScaleRegime: vi.fn(), playDetentClick: vi.fn() },
}));
vi.mock('../../audio/SkyAudioFeed.js', () => ({ skyAudio: { follow: vi.fn(), setEvidenceClass: vi.fn() } }));
vi.mock('../CoalescingBar.js', () => ({ CoalescingBar: () => null }));

const { SpatialObservatoryViewport } = await import('../SpatialObservatoryViewport.js');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const noop = () => {};
let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  stub.drawing.length = 0;
  stub.showcase.length = 0;
  stub.log.length = 0;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  window.history.replaceState(null, '', '/');
});

/** A session with no photo on screen yet (the home left, the receipt not come), and one with the photo on screen. */
const NO_PHOTO = {} as unknown as ObservationSession;
const PHOTO = { sourceAsset: {} } as unknown as ObservationSession;

function renderViewport(uploadRefusal: { fileName: string; detail: string | null } | null = null, session: ObservationSession = NO_PHOTO): void {
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
        uploadRefusal,
      })
    );
  });
}

describe('the scene under the home stops drawing', () => {
  it('tells the renderer to stop drawing as soon as it is attached under the home', () => {
    renderViewport();
    expect(container.querySelector(HOME_SCREEN)).not.toBeNull();
    expect(stub.drawing).toEqual([false]);
  });

  it('draws again when a photo is chosen and the home leaves', () => {
    renderViewport();
    choosePhotoOnHome(container);
    expect(container.querySelector(HOME_SCREEN)).toBeNull();
    expect(stub.drawing).toEqual([false, true]);
  });

  it('stays still under the photo the home leaves to, which covers it wholly (portal-pause-one)', () => {
    renderViewport(null, PHOTO);
    choosePhotoOnHome(container);
    expect(container.querySelector(HOME_SCREEN)).toBeNull();
    expect(stub.drawing).toEqual([false]);
    // and draws again once nothing covers it, told in the same commit (a layout effect), before that screen is painted
    renderViewport(null, NO_PHOTO);
    expect(stub.drawing).toEqual([false, true]);
    renderViewport(null, PHOTO);
    expect(stub.drawing).toEqual([false, true, false]);
  });

  it('stops again when a refused upload brings the home back', () => {
    renderViewport();
    choosePhotoOnHome(container);
    renderViewport({ fileName: 'notes.pdf', detail: 'not a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file' });
    expect(container.querySelector(HOME_SCREEN)).not.toBeNull();
    expect(stub.drawing).toEqual([false, true, false]);
  });

  it('draws from the start where the address skips the home (?view=observatory), the one way past it without a photo', () => {
    window.history.replaceState(null, '', '/?view=observatory');
    renderViewport();
    expect(container.querySelector(HOME_SCREEN)).toBeNull();
    expect(stub.drawing).toEqual([true]);
    // and no landing scene is ever asked for (home-cycle-1: the home's scene cycle was drawn by nothing)
    expect(stub.log.some((event) => event.startsWith('scene:') || event === 'showcase:true')).toBe(false);
  });

  it('asks for no landing scene under the home, where nothing draws (home-cycle-1)', () => {
    vi.useFakeTimers();
    try {
      renderViewport();
      act(() => void vi.advanceTimersByTime(30_000));
      expect(stub.drawing).toEqual([false]);
      expect(stub.log.some((event) => event.startsWith('scene:') || event === 'showcase:true')).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it('draws again on leaving the home, starting no landing scene', () => {
    renderViewport();
    const before = stub.log.length;
    choosePhotoOnHome(container);
    const after = stub.log.slice(before);
    expect(after).toContain('drawing:true');
    expect(after.some((event) => event.startsWith('scene:') || event === 'showcase:true')).toBe(false);
  });
});
