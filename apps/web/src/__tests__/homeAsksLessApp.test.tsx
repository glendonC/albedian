// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../spatial/SparkPortalRenderer.js', () => ({
  SparkPortalRenderer: class {
    setObserverCoordinates() {}
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
      return null;
    }
    getCameraSkyPose() {
      return null;
    }
  },
}));
vi.mock('../audio/ObservatoryAudioEngine.js', () => ({
  observatoryAudio: { toggle: vi.fn(() => false), setMasterVolume: vi.fn(), setScaleRegime: vi.fn(), playDetentClick: vi.fn() },
}));
vi.mock('../audio/SkyAudioFeed.js', () => ({ skyAudio: { follow: vi.fn(), setEvidenceClass: vi.fn() } }));
vi.mock('../components/CoalescingBar.js', () => ({ CoalescingBar: () => null }));

// home-asks-less-one (DECIDED-BY-LEAD 20:30 on 27 Nov): the home no longer asks for what it never shows. The list of
// media found in the server's uploads (GET /api/media/discover, 4,338,629 bytes on a phone's home on main da534d4d8,
// the uploads' places and capture times among it) is asked only when a discovered item is chosen, and no screen
// chooses one, so a browser opening the home never receives it.

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
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('home-asks-less-one: the app on its home', () => {
  it('never asks the server for its discovered media', async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        calls.push(String(input));
        return new Response(JSON.stringify({}), { status: 404 });
      })
    );
    const { App } = await import('../App.js');
    await act(async () => {
      root.render(createElement(App));
    });
    for (let i = 0; i < 10; i++) await act(async () => await new Promise((r) => setTimeout(r, 10)));
    expect(container.querySelector('.home-screen')).not.toBeNull();
    expect(calls.filter((url) => url.includes('/api/media/discover'))).toEqual([]);
  });
});
