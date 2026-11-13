// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { HOME_SCREEN, choosePhotoOnHome } from './homeEntry.js';

const stub = vi.hoisted(() => ({ soundOn: false }));

vi.mock('../../spatial/SparkPortalRenderer.js', () => ({
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
vi.mock('../../audio/ObservatoryAudioEngine.js', () => ({
  observatoryAudio: {
    toggle: vi.fn(() => (stub.soundOn = !stub.soundOn)),
    setMasterVolume: vi.fn(),
    setScaleRegime: vi.fn(),
    playDetentClick: vi.fn(),
  },
}));
vi.mock('../../audio/SkyAudioFeed.js', () => ({ skyAudio: { follow: vi.fn(), setEvidenceClass: vi.fn() } }));
vi.mock('../CoalescingBar.js', () => ({ CoalescingBar: () => null }));

const { HomeScreen } = await import('../HomeScreen.js');
const { HOME_BACKDROP, HOME_SHADE, homeBackdropChanges, homeBackdropStyle, homeShadeStyle } = await import('../homeBackdrop.js');
const { SpatialObservatoryViewport } = await import('../SpatialObservatoryViewport.js');
const { observatoryAudio } = await import('../../audio/ObservatoryAudioEngine.js');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const here = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(here, '../../..');
const homeCss = fs.readFileSync(path.resolve(here, '../../styles/home.css'), 'utf8');

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  stub.soundOn = false;
  vi.mocked(observatoryAudio.toggle).mockClear();
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

const noop = () => {};
const q = (selector: string) => container.querySelector(selector);
const buttonNamed = (name: string) =>
  [...container.querySelectorAll('button')].find((b) => (b.getAttribute('aria-label') ?? b.textContent) === name);

function renderHome(props: Partial<Parameters<typeof HomeScreen>[0]> = {}) {
  act(() => {
    root.render(createElement(HomeScreen, { soundOn: false, onToggleSound: noop, onFileChosen: noop, ...props }));
  });
}

describe('HomeScreen: the approved home, word for word', () => {
  it('shows the logo at 132 px, Upload, Credits and the sound circle, and no other word', () => {
    renderHome();
    const home = q(HOME_SCREEN)!;
    const logo = home.querySelector('.look-logo') as HTMLElement;
    expect(logo.getAttribute('aria-label')).toBe('Albedian');
    expect(logo.style.getPropertyValue('--look-logo-fs')).toBe('132px');
    const upload = home.querySelector('button.look-main')!;
    expect(upload.textContent).toBe('Upload');
    expect(upload.classList.contains('look-size-home')).toBe(true);
    expect(upload.querySelector('svg.look-i')!.classList.contains('look-turn')).toBe(false);
    expect(home.querySelector('button.look-second')!.textContent).toBe('Credits');
    expect(buttonNamed('Sound')!.classList.contains('look-icon')).toBe(true);
    // Credits, the logo's word and Upload, in the page's order: nothing else is written on the home
    expect(home.textContent).toBe('CreditsalbedianUpload');
    expect(home.querySelectorAll('button.look-main')).toHaveLength(1);
  });

  it('opens its own picker for photos and clips, and hands the chosen file on once', () => {
    const onFileChosen = vi.fn();
    const click = vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {});
    renderHome({ onFileChosen });
    const input = q('[data-testid="home-file-input"]') as HTMLInputElement;
    expect(input.type).toBe('file');
    expect(input.accept).toBe('image/*,video/*');
    act(() => (q('button.look-main') as HTMLButtonElement).click());
    expect(click).toHaveBeenCalledTimes(1);
    const file = choosePhotoOnHome(container);
    expect(onFileChosen).toHaveBeenCalledTimes(1);
    expect(onFileChosen).toHaveBeenCalledWith(file);
  });

  it('tells its listener once when Upload is pressed', () => {
    const onUploadPressed = vi.fn();
    vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {});
    renderHome({ onUploadPressed });
    act(() => (q('button.look-main') as HTMLButtonElement).click());
    expect(onUploadPressed).toHaveBeenCalledTimes(1);
  });

  it('draws its sky from the shipped panorama, framed as the mockup frames it', () => {
    renderHome();
    const sky = q('.home-sky') as HTMLElement;
    expect(sky.style.backgroundImage).toBe('url("/textures/eso_milkyway_panorama.jpg")');
    expect(sky.style.backgroundSize).toBe('156.25% auto');
    expect(sky.style.backgroundPosition).toBe('87.89% 43.95%');
    expect(sky.getAttribute('aria-hidden')).toBe('true');
  });

  it('chooses nothing when the picker closes with no file', () => {
    const onFileChosen = vi.fn();
    renderHome({ onFileChosen });
    const input = q('[data-testid="home-file-input"]') as HTMLInputElement;
    Object.defineProperty(input, 'files', { value: [], configurable: true });
    act(() => {
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(onFileChosen).not.toHaveBeenCalled();
  });

  it('empties the picker after each choice, so a browser counts the same file chosen again as a new choice', () => {
    const onFileChosen = vi.fn();
    renderHome({ onFileChosen });
    const input = q('[data-testid="home-file-input"]') as HTMLInputElement;
    // A browser fires no change for a value it already holds; the home empties it, which this records
    const written: string[] = [];
    Object.defineProperty(input, 'value', { configurable: true, get: () => '', set: (v: string) => void written.push(v) });
    const file = new File([new Uint8Array([0xff, 0xd8])], 'again.jpg', { type: 'image/jpeg' });
    Object.defineProperty(input, 'files', { value: [file], configurable: true });
    act(() => {
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(written).toEqual(['']);
    expect(onFileChosen).toHaveBeenCalledWith(file);
  });

  it('says whether sound is on, toggles it, and marks itself as the audio control', () => {
    const onToggleSound = vi.fn();
    renderHome({ soundOn: true, onToggleSound });
    const sound = buttonNamed('Sound')!;
    expect(sound.getAttribute('aria-pressed')).toBe('true');
    expect(sound.getAttribute('data-audio-control')).toBe('toggle');
    act(() => sound.click());
    expect(onToggleSound).toHaveBeenCalledTimes(1);
    renderHome({ soundOn: false });
    expect(buttonNamed('Sound')!.getAttribute('aria-pressed')).toBe('false');
  });

  it('opens Credits in place of the home and comes back to it, focus on Credits again', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    renderHome();
    act(() => (q('button.look-second') as HTMLButtonElement).click());
    expect(q(HOME_SCREEN)).toBeNull();
    expect(q('[data-testid="credits-page"]')).not.toBeNull();
    act(() => buttonNamed('Back')!.click());
    expect(q('[data-testid="credits-page"]')).toBeNull();
    expect(q(HOME_SCREEN)).not.toBeNull();
    expect(document.activeElement).toBe(q('button.look-second'));
    vi.unstubAllGlobals();
  });
});

describe('the home backdrop: the panorama the app ships, framed as the mockup frames it', () => {
  it('is the shipped file, whose provenance record names its source and credit', () => {
    expect(HOME_BACKDROP.url).toBe('/textures/eso_milkyway_panorama.jpg');
    expect(fs.existsSync(path.join(webRoot, 'public', HOME_BACKDROP.url))).toBe(true);
    const record = JSON.parse(fs.readFileSync(path.join(webRoot, 'public/textures/PROVENANCE.json'), 'utf8'));
    const file = record.files.find((f: { path: string }) => f.path === 'eso_milkyway_panorama.jpg');
    expect(file.sources).toEqual(['eso0932a']);
    const source = record.sources.find((s: { id: string }) => s.id === 'eso0932a');
    expect(source.credit).toBe('ESO/S. Brunier');
  });

  // One table for the framing: a crop at the panorama's left edge, at its right edge, and the mockup's own
  const FRAMES: ReadonlyArray<[label: string, widthFraction: number, left: number, top: number, x: string, y: string]> = [
    ['at the left edge', 0.64, 0, 0, '0%', '0%'],
    ['at the right and bottom edges', 0.64, 0.36, 0.2, '100%', '100%'],
    ['as the mockup', 0.64, 0.3164, 0.0879, '87.89%', '43.95%'],
  ];
  it.each(FRAMES)('places a crop %s', (_label, widthFraction, left, top, x, y) => {
    const style = homeBackdropStyle({ ...HOME_BACKDROP, widthFraction, left, top });
    expect(style.backgroundSize).toBe('156.25% auto');
    expect(style.backgroundPosition).toBe(`${x} ${y}`);
    expect(style.backgroundImage).toBe(`url("${HOME_BACKDROP.url}")`);
  });

  it('frames the region the mockup shows, matched against the panorama', () => {
    expect(HOME_BACKDROP).toMatchObject({ widthFraction: 0.64, left: 0.3164, top: 0.0879, aspect: 1.6 });
  });

  it('keeps the frame covering the window at any shape, and shades it toward the bottom as the mockup does', () => {
    const sky = /\.home-sky\s*\{([^}]*)\}/.exec(homeCss)?.[1] ?? '';
    expect(sky.replace(/\s+/g, ' ')).toContain('width: max(100vw, calc(100vh * 1.6))');
    expect(sky.replace(/\s+/g, ' ')).toContain('height: max(100vh, calc(100vw / 1.6))');
    // The shade is the mockup's, drawn from one constant the panorama's credit also words its change from
    expect(HOME_SHADE).toEqual([
      [0.82, 0],
      [0.2, 46],
      [0, 70],
    ]);
    expect(homeShadeStyle().backgroundImage).toBe(
      'linear-gradient(to top, rgba(0, 0, 0, 0.82) 0%, rgba(0, 0, 0, 0.2) 46%, rgba(0, 0, 0, 0) 70%)'
    );
    const shade = /\.home-shade\s*\{([^}]*)\}/.exec(homeCss)?.[1] ?? '';
    expect(shade).not.toMatch(/background/);
  });

  it('draws the shade from that constant on the home', () => {
    renderHome();
    expect((q('.home-shade') as HTMLElement).style.backgroundImage).toBe(homeShadeStyle().backgroundImage);
  });

  it("words the panorama's change from the numbers that crop and shade it", () => {
    expect(homeBackdropChanges()).toBe('Shown cropped to 64 percent of its width, and darkened toward the bottom, up to 82 percent black.');
    expect(homeBackdropChanges({ ...HOME_BACKDROP, widthFraction: 0.5 }, [[0.6, 0], [0, 50]])).toBe(
      'Shown cropped to 50 percent of its width, and darkened toward the bottom, up to 60 percent black.'
    );
    // The darkest stop is named, wherever it sits
    expect(homeBackdropChanges(HOME_BACKDROP, [[0.3, 0], [0.7, 20], [0, 60]])).toContain('up to 70 percent black');
  });

  it('moves nothing of its own: no animation, keyframes or transition in its stylesheet', () => {
    const css = homeCss.replace(/\/\*[\s\S]*?\*\//g, '');
    expect(css).not.toMatch(/\banimation\b|@keyframes|\btransition\b/);
  });

  it("keeps the browser's own file field out of sight: Upload is the only way to it", () => {
    expect((/\.home-file-input\s*\{([^}]*)\}/.exec(homeCss)?.[1] ?? '').replace(/\s+/g, ' ').trim()).toBe('display: none;');
  });
});

describe('the viewport opens on the home', () => {
  const session = { sourceAsset: { observerLocation: null } } as unknown as ObservationSession;
  function renderViewport(extra: Record<string, unknown> = {}) {
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
          ...extra,
        })
      );
    });
  }

  it('shows the home first, with no header, no app controls and no description of the hidden 3D scene', () => {
    renderViewport();
    expect(q(HOME_SCREEN)).not.toBeNull();
    expect(q('.alb-lusion-header')).toBeNull();
    expect(q('[data-testid="voice-command-hud"]')).toBeNull();
    expect(q('.alb-layer-disclosure-toggle')).toBeNull();
    expect(q('[data-testid="enter-observatory-btn"]')).toBeNull();
  });

  it('leaves the home at once when a photo is chosen, and hands the file to the app', () => {
    const onFileChosen = vi.fn();
    renderViewport({ onFileChosen });
    const file = choosePhotoOnHome(container);
    expect(q(HOME_SCREEN)).toBeNull();
    expect(q('.alb-lusion-header')).not.toBeNull();
    expect(q('[data-testid="voice-command-hud"]')).not.toBeNull();
    expect(onFileChosen).toHaveBeenCalledWith(file);
  });

  it("returns to the home from the app's logo", () => {
    renderViewport();
    choosePhotoOnHome(container);
    act(() => (q('.alb-lusion-brand-btn') as HTMLButtonElement).click());
    expect(q(HOME_SCREEN)).not.toBeNull();
  });

  it('turns the observatory sound on and off from the home and says so', () => {
    renderViewport();
    act(() => buttonNamed('Sound')!.click());
    expect(observatoryAudio.toggle).toHaveBeenCalledTimes(1);
    expect(buttonNamed('Sound')!.getAttribute('aria-pressed')).toBe('true');
  });

  it('plays no sound on Upload, since no record holds one, and tells the listener', () => {
    const onTriggerUpload = vi.fn();
    vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {});
    vi.mocked(observatoryAudio.playDetentClick).mockClear();
    renderViewport({ onTriggerUpload });
    act(() => (q('button.look-main') as HTMLButtonElement).click());
    expect(observatoryAudio.playDetentClick).not.toHaveBeenCalled();
    expect(onTriggerUpload).toHaveBeenCalledTimes(1);
  });
});

describe('the app starts an upload from the home', () => {
  it('creates the photo URL and sends the chosen file to /api/ingest', async () => {
    const { App } = await import('../../App.js');
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/media/discover') return new Response(JSON.stringify({ items: [] }), { status: 200 });
      return new Response(JSON.stringify({ detail: 'test refuses' }), { status: 415 });
    });
    vi.stubGlobal('fetch', fetchMock);
    // jsdom has no object URLs: one is lent for this test and taken back after it
    const createObjectURL = vi.fn(() => 'blob:home-test');
    const before = Object.getOwnPropertyDescriptor(URL, 'createObjectURL');
    Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, configurable: true, writable: true });
    try {
      await act(async () => {
        root.render(createElement(App));
      });
      const file = choosePhotoOnHome(container);
      expect(createObjectURL).toHaveBeenCalledWith(file);
      expect(q(HOME_SCREEN)).toBeNull();
      // The upload's first sentence names the chosen file; what follows depends on the server
      await act(async () => {
        await vi.waitFor(() => {
          expect(q('[data-testid="voice-status-banner"]')?.textContent).toContain(`"${file.name}"`);
        });
      });
    } finally {
      if (before) Object.defineProperty(URL, 'createObjectURL', before);
      else delete (URL as unknown as Record<string, unknown>).createObjectURL;
      vi.unstubAllGlobals();
    }
  });
});
