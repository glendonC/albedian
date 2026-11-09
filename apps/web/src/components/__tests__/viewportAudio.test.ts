// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession, RenderLayerSet } from '@albedian/contracts';
import {
  AUDIO_CREDIT_LINES,
  AUDIO_DISCLOSURE,
  AUDIO_DISCLOSURE_WITHOUT_MUSIC,
  AUDIO_NOTICES_URL,
  appHeardMusic,
  appMusicAvailability,
  audioCreditLines,
  audioDisclosure,
} from '../../audio/audioCredits.js';
import { RECORDING } from '../../audio/music/directions.js';
import { appMusicSetting } from '../../audio/music/musicSetting.js';
import { appSoundtrackMoment } from '../../audio/music/soundtrackMoment.js';
import type { CameraSkyPose } from '../../spatial/cosmosRenderer.js';
import { webglRenderLayersFor } from '../../spatial/webglRenderLayers.js';

/**
 * The observatory viewport's audio wiring (render-6g's viewport half), on the real component: the
 * renderer and the audio engine are stand-ins, so the test sees what the viewport asks of them.
 * While audio is on, the viewport says what the sound is, credits the works the engine ships with
 * a link to their notices, and feeds the renderer's camera pose to the sky audio every frame; the
 * voices carry the evidence class of the star layer the renderer declares.
 */

const stub = vi.hoisted(() => ({
  pose: {
    cameraWorldQuaternion: [0, 0, 0, 1],
    icrsWorldQuaternion: [-0.5, 0.5, 0.5, 0.5],
    fovDeg: 56,
    aspect: 1.6,
    limitingMagnitude: 6.5,
  } as const,
  layers: null as unknown,
  soundOn: false,
  onRegimeChange: null as ((regime: string) => void) | null,
  barValue: null as number | null,
}));

vi.mock('../../spatial/SparkPortalRenderer.js', () => ({
  SparkPortalRenderer: class {
    constructor(_host: HTMLElement, onRegimeChange?: (regime: string) => void) {
      stub.onRegimeChange = onRegimeChange ?? null;
    }
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
      return stub.layers;
    }
    getCameraSkyPose() {
      return stub.pose;
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

vi.mock('../../audio/SkyAudioFeed.js', () => ({
  skyAudio: { follow: vi.fn(), setEvidenceClass: vi.fn() },
}));

// The volume bar draws on a 2D canvas, which jsdom lacks; the stand-in keeps the value it is given
vi.mock('../CoalescingBar.js', () => ({
  CoalescingBar: (props: { value: number }) => {
    stub.barValue = props.value;
    return null;
  },
}));

const { SpatialObservatoryViewport } = await import('../SpatialObservatoryViewport.js');
const { skyAudio } = await import('../../audio/SkyAudioFeed.js');

declare global {
  // React's flag for tests that drive updates through act()
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const noop = () => {};
const session = { sourceAsset: { observerLocation: null } } as unknown as ObservationSession;
const CISLUNAR_LAYERS = webglRenderLayersFor({ regime: 'cislunar', blackHole: false }) as RenderLayerSet;

let container: HTMLDivElement;
let root: Root;

function renderViewport(): void {
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

function audioButton(): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Audio');
  expect(button).toBeDefined();
  return button!;
}

function clickAudio(): void {
  act(() => audioButton().click());
}

function frames(n: number): void {
  for (let i = 0; i < n; i++) act(() => vi.advanceTimersToNextFrame());
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame'] });
  stub.layers = CISLUNAR_LAYERS;
  stub.soundOn = false;
  vi.mocked(skyAudio.follow).mockClear();
  vi.mocked(skyAudio.setEvidenceClass).mockClear();
  stub.barValue = null;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe('the viewport while audio is on', () => {
  it('says what the sound is, credits the works the engine ships and links their notices', () => {
    renderViewport();
    expect(document.getElementById('alb-audio-disclosure')).toBeNull();
    expect(audioButton().hasAttribute('aria-describedby')).toBe(false);
    expect(container.querySelector('a.alb-audio-notices')).toBeNull();

    clickAudio();
    const note = document.getElementById('alb-audio-disclosure');
    expect(note).not.toBeNull();
    expect(note!.getAttribute('role')).toBe('note');
    expect(note!.textContent).toBe(AUDIO_DISCLOSURE);
    expect(audioButton().getAttribute('aria-describedby')).toBe('alb-audio-disclosure');
    const credits = Array.from(container.querySelectorAll('.alb-audio-credits li'), (li) => li.textContent);
    expect(credits).toEqual([...AUDIO_CREDIT_LINES]);
    const notices = container.querySelector<HTMLAnchorElement>('a.alb-audio-notices');
    expect(notices).not.toBeNull();
    expect(notices!.getAttribute('href')).toBe(AUDIO_NOTICES_URL);
    expect(notices!.textContent).toBe('Notices');
    expect(notices!.getAttribute('target')).toBe('_blank');
    expect(notices!.getAttribute('rel')).toContain('noopener');

    clickAudio();
    expect(document.getElementById('alb-audio-disclosure')).toBeNull();
    expect(audioButton().hasAttribute('aria-describedby')).toBe(false);
    expect(container.querySelector('.alb-audio-credits')).toBeNull();
    expect(container.querySelector('a.alb-audio-notices')).toBeNull();
  });

  it("feeds the renderer's camera pose to the sky audio every frame, and stops when audio goes off", () => {
    renderViewport();
    frames(3);
    expect(skyAudio.follow).not.toHaveBeenCalled();

    clickAudio();
    frames(4);
    expect(skyAudio.follow).toHaveBeenCalledTimes(4);
    for (const [pose] of vi.mocked(skyAudio.follow).mock.calls) {
      expect(pose).toBe(stub.pose as unknown as CameraSkyPose);
    }

    clickAudio();
    vi.mocked(skyAudio.follow).mockClear();
    frames(4);
    expect(skyAudio.follow).not.toHaveBeenCalled();
  });

  it("gives the voices the declared star layer's evidence class, and none where the view declares none", () => {
    const starLayer = CISLUNAR_LAYERS.find((layer) => layer.kind === 'catalog_stars');
    expect(starLayer).toBeDefined();
    renderViewport();
    expect(skyAudio.setEvidenceClass).toHaveBeenLastCalledWith(starLayer!.evidenceClass);

    // Every declared view is simulated throughout, so a set built from the cislunar one with only its
    // star layer's class changed shows that the class is the star layer's, not another layer's
    stub.layers = CISLUNAR_LAYERS.map((layer) =>
      layer.kind === 'catalog_stars' ? { ...layer, evidenceClass: 'catalog_matched' as const } : layer
    );
    act(() => stub.onRegimeChange?.('cislunar'));
    expect(skyAudio.setEvidenceClass).toHaveBeenLastCalledWith('catalog_matched');

    // The sky view declares its layers too (webglRenderLayers.ts), so its voices carry its star layer's class
    stub.layers = webglRenderLayersFor({ regime: 'earth_capture', blackHole: false });
    expect(stub.layers).not.toBeNull();
    act(() => stub.onRegimeChange?.('earth_capture'));
    expect(skyAudio.setEvidenceClass).toHaveBeenLastCalledWith('simulated');
  });
});

describe('the music beside the Audio control', () => {
  it('shows the stored volume on the Audio bar whether audio is on or off', () => {
    renderViewport();
    expect(stub.barValue).toBe(0.42);
    clickAudio();
    expect(stub.barValue).toBe(0.42);
    clickAudio();
    expect(stub.barValue).toBe(0.42);
  });

  it("gives the soundtrack the moment, which the app's engine follows: arriving on the landing page, then looking around after Launch", () => {
    act(() => appSoundtrackMoment.set('travel'));
    renderViewport();
    expect(appSoundtrackMoment.get()).toBe('arrive');
    const launch = container.querySelector<HTMLButtonElement>('[data-testid="enter-observatory-btn"]');
    expect(launch).not.toBeNull();
    act(() => launch!.click());
    expect(appSoundtrackMoment.get()).toBe('look_around');
  });

  it('shows the Music setting beside Audio while audio is on', () => {
    renderViewport();
    expect(container.querySelector('select[aria-label="Music"]')).toBeNull();
    clickAudio();
    expect(container.querySelector('.alb-zzzz-inline-control select[aria-label="Music"]')).not.toBeNull();
    clickAudio();
    expect(container.querySelector('select[aria-label="Music"]')).toBeNull();
  });

  it('says what the sound is for the Music setting: the soundtrack, a pinned piece, or no music', () => {
    renderViewport();
    clickAudio();
    const note = () => document.getElementById('alb-audio-disclosure')!.textContent;
    expect(appMusicSetting.get()).toBe('soundtrack');
    expect(note()).toBe(audioDisclosure('soundtrack'));
    try {
      act(() => appMusicSetting.set('recording'));
      expect(note()).toBe(audioDisclosure('recording'));
      act(() => appMusicSetting.set('off'));
      expect(note()).toBe(audioDisclosure('off'));
    } finally {
      act(() => appMusicSetting.set('soundtrack'));
    }
  });

  it('says no music plays and shows no Music control where none can play', () => {
    renderViewport();
    clickAudio();
    try {
      act(() => appMusicAvailability.set(false));
      expect(document.getElementById('alb-audio-disclosure')!.textContent).toBe(AUDIO_DISCLOSURE_WITHOUT_MUSIC);
      expect(container.querySelector('select[aria-label="Music"]')).toBeNull();
    } finally {
      act(() => appMusicAvailability.set(true));
    }
    expect(container.querySelector('select[aria-label="Music"]')).not.toBeNull();
  });

  it('credits each piece once it has been heard, and keeps it', () => {
    renderViewport();
    clickAudio();
    act(() => appHeardMusic.add('recording'));
    const credits = () => Array.from(container.querySelectorAll('.alb-audio-credits li'), (li) => li.textContent);
    expect(credits()).toEqual([...audioCreditLines(appHeardMusic.get())]);
    expect(credits()).toContain(RECORDING.credit);
    clickAudio();
    clickAudio();
    expect(credits()).toContain(RECORDING.credit);
  });
});
