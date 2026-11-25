// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { appHeardMusic } from '../../audio/audioCredits.js';
import { SOUNDTRACK } from '../../audio/music/soundtrack.js';
import { choosePhotoOnHome, HOME_SCREEN } from './homeEntry.js';

// credits-reach-one (UIUX-credits-reach-READY.md, DECIDED-BY-LEAD 17:39 on 24 Nov): on every laptop screen after the
// home, the word Credits at the bottom right as a small text link in the disclosure lines' style, always in view, as map
// products keep their attribution on the map; it opens the Credits page over the moment without leaving it, at the Sound
// row once music has played, the music playing on, and back returns to exactly that moment. The Sound circle, the home's
// pill and the phone's audio strip stay as they are.

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
const { CreditsPage } = await import('../CreditsPage.js');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const LINK = 'button.reveal-credits';
const PAGE = '[data-testid="credits-page"]';

let container: HTMLDivElement;
let root: Root;
let fetchSpy: MockInstance<typeof fetch>;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  // The provenance records are not served here: the page says so, which is all these tests need of its rows
  fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{}', { status: 404 }));
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  fetchSpy.mockRestore();
  Object.defineProperty(window, 'innerWidth', { value: 1024, configurable: true });
});

function mount(width = 1440) {
  Object.defineProperty(window, 'innerWidth', { value: width, configurable: true });
  Object.defineProperty(window, 'innerHeight', { value: width < 768 ? 844 : 900, configurable: true });
  const base = createSyntheticCalibratedSession();
  let current = {
    ...base,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', mimeType: 'image/jpeg', uri: 'blob:photo', width: 3024, height: 4032, durationMs: 0 },
    mediaVariants: [],
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
  return { render, session: () => current };
}
const flush = async () => {
  for (let i = 0; i < 4; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
};

describe('the Credits link on the laptop after the home', () => {
  it('stands on the screens after the home, not on the home', () => {
    const v = mount();
    expect(container.querySelector(HOME_SCREEN)).not.toBeNull();
    expect(container.querySelector(LINK)).toBeNull();
    choosePhotoOnHome(container);
    v.render();
    const link = container.querySelector<HTMLButtonElement>(LINK)!;
    expect(link).not.toBeNull();
    expect(link.textContent).toBe('Credits');
    expect(link.getAttribute('aria-label')).toBe('Credits');
    expect(link.type).toBe('button');
    // In the tab order after the foot's controls: after the ask bar's buttons and the views row in the page's order
    const ask = container.querySelector('.ask-bar');
    if (ask) expect(ask.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('is not on the phone, which keeps its audio strip', () => {
    // A phone's window, as the layout reads it (usePhoneLayout's media queries)
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('max-width') || query.includes('max-height'), addEventListener: () => {}, removeEventListener: () => {} }));
    try {
      const v = mount(390);
      choosePhotoOnHome(container);
      v.render();
      expect(container.querySelector('.reveal-phone-top')).not.toBeNull();
      expect(container.querySelector(LINK)).toBeNull();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('opens the Credits page over the moment, which stays as it was, and back (or Esc) returns to it with the link focused', async () => {
    const v = mount();
    choosePhotoOnHome(container);
    v.render();
    const before = v.session();
    const reveal = container.querySelector('[data-testid="reveal-photo"]');
    expect(reveal).not.toBeNull();
    act(() => container.querySelector<HTMLButtonElement>(LINK)!.click());
    await flush();
    const page = document.querySelector<HTMLElement>(PAGE)!;
    expect(page).not.toBeNull();
    // Over the moment, not in its place: the reveal is still there under it, untouched, and the session unchanged
    expect(container.querySelector('[data-testid="reveal-photo"]')).toBe(reveal);
    expect(v.session()).toBe(before);
    // The moment under the page takes no pointer and no keyboard focus while the page stands over it, and the page is
    // not inside the moment, so the moment's inertness never takes the page with it
    expect(container.querySelector('.alb-root')!.hasAttribute('inert')).toBe(true);
    expect(container.querySelector(PAGE)).toBeNull();
    // Keys go to the page only: a key the moment listens for never reaches it
    const heard: string[] = [];
    const listen = (event: KeyboardEvent) => heard.push(event.key);
    window.addEventListener('keydown', listen);
    try {
      act(() => void window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true })));
      expect(heard).toEqual([]);
      // Esc closes the page and is not heard by the moment either (so Back to Moment and the card keep their place)
      act(() => void window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
      expect(heard).toEqual([]);
      // Leaving, the page takes nothing more: the moment's keys are its own again
      act(() => void window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true })));
      expect(heard).toEqual(['ArrowLeft']);
    } finally {
      window.removeEventListener('keydown', listen);
    }
    await act(async () => await new Promise((r) => setTimeout(r, 600)));
    expect(document.querySelector(PAGE)).toBeNull();
    expect(v.session()).toBe(before);
    expect(container.querySelector('.alb-root')!.hasAttribute('inert')).toBe(false);
    expect(document.activeElement).toBe(container.querySelector(LINK));
  });
});

describe("the Credits page opened over a moment starts at the Sound row once music has played", () => {
  it('scrolls its Sound row to the top when asked to start there, and stays at its top otherwise', async () => {
    const shown = (startAtSound: boolean) => {
      // The records are served from disk here, so the rows are laid out
      fetchSpy.mockImplementation(async (input) => {
        const url = String(input);
        const file = path.resolve(here, '../../../public', `.${url}`);
        return fs.existsSync(file) ? new Response(fs.readFileSync(file, 'utf8'), { status: 200 }) : new Response('{}', { status: 404 });
      });
      act(() => root.render(createElement(CreditsPage, { onBack: () => {}, startAtSound })));
    };
    const offset = vi.spyOn(HTMLElement.prototype, 'offsetTop', 'get').mockImplementation(function (this: HTMLElement) {
      return this.matches('li[data-row="Sound"]') ? 700 : 0;
    });
    try {
      shown(true);
      await flush();
      const page = document.querySelector<HTMLElement>(PAGE)!;
      expect(page.querySelector('li[data-row="Sound"]')).not.toBeNull();
      // The row's top at the page's own top padding (32 px)
      expect(page.scrollTop).toBe(668);
      act(() => root.render(createElement('div')));
      shown(false);
      await flush();
      expect(document.querySelector<HTMLElement>(PAGE)!.scrollTop).toBe(0);
    } finally {
      offset.mockRestore();
    }
  });

  it('starts there from the link when a piece has played this visit', async () => {
    const v = mount();
    choosePhotoOnHome(container);
    v.render();
    appHeardMusic.add(SOUNDTRACK[0]!.id);
    act(() => container.querySelector<HTMLButtonElement>(LINK)!.click());
    await flush();
    expect(document.querySelector(PAGE)!.getAttribute('data-start')).toBe('sound');
  });
});

describe("the link's look: the disclosure lines' own style, at the views row's right edge, on the foot row's middle", () => {
  it('sets it as the record has it', () => {
    const decls = (selector: string) => {
      const found: Record<string, string> = {};
      postcss.parse(css).walkRules((rule) => {
        if (rule.parent?.type === 'root' && rule.selectors.map((s) => s.replace(/\s+/g, ' ')).join(', ') === selector) rule.walkDecls((d) => void (found[d.prop] = d.value));
      });
      return found;
    };
    expect(decls('.reveal-credits')).toEqual({
      all: 'unset',
      position: 'fixed',
      right: '32px',
      // The foot row's buttons stand 32 px from the bottom at the home size's height (48 px): the link's box is as tall, so its
      // middle is theirs
      bottom: '32px',
      height: 'var(--look-h-home)',
      'z-index': '30',
      display: 'inline-flex',
      'align-items': 'center',
      font: '400 12px / 1.3 var(--look-font-sans)',
      color: 'var(--look-ink-2)',
      cursor: 'pointer',
      'pointer-events': 'auto',
    });
    expect(decls('.reveal-credits:hover, .reveal-credits:focus-visible')).toEqual({ 'text-decoration': 'underline' });
    expect(decls('.reveal-credits:focus-visible')).toEqual({ outline: 'var(--look-focus-width) solid var(--look-ink)', 'outline-offset': 'var(--look-focus-offset)' });
  });
});
