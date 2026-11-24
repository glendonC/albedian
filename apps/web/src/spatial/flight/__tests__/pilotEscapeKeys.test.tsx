// @vitest-environment jsdom
import { act, createElement, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';

const calls = vi.hoisted(() => ({
  nudgeLook: vi.fn(),
  nudgeMovement: vi.fn(),
}));

vi.mock('../../../reveal/RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../../reveal/RealSkyLayer.js')>();
  const { createElement: h, useEffect } = await import('react');
  return {
    ...original,
    canDrawRealSky: () => true,
    RealSkyLayer: (props: { onDrawn?: (drawn: boolean) => void }) => {
      useEffect(() => {
        props.onDrawn?.(true);
      }, [props.onDrawn]);
      return h('div', { className: 'real-sky', 'data-testid': 'real-sky', 'data-state': 'drawn' });
    },
  };
});

vi.mock('../../SparkPortalRenderer.js', () => ({
  SparkPortalRenderer: class {
    setObserverCoordinates() {}
    clearObserverCoordinates() {}
    setShowcaseMode() {}
    transitionToShowcaseScene() {}
    showSkyView() {}
    setDrawing() {}
    holdClock() {}
    nudgeMovement(...args: unknown[]) {
      calls.nudgeMovement(...args);
    }
    nudgeLook(...args: unknown[]) {
      calls.nudgeLook(...args);
    }
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
  },
}));
vi.mock('../../../audio/ObservatoryAudioEngine.js', () => ({
  observatoryAudio: { toggle: vi.fn(() => false), setMasterVolume: vi.fn(), setScaleRegime: vi.fn(), playDetentClick: vi.fn() },
}));
vi.mock('../../../audio/SkyAudioFeed.js', () => ({ skyAudio: { follow: vi.fn(), setEvidenceClass: vi.fn() } }));
vi.mock('../../../components/CoalescingBar.js', () => ({ CoalescingBar: () => null }));

const { SpatialObservatoryViewport } = await import('../../../components/SpatialObservatoryViewport.js');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  calls.nudgeLook.mockReset();
  calls.nudgeMovement.mockReset();
  window.history.replaceState({}, '', '/?view=observatory');
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  vi.stubGlobal('matchMedia', (query: string) => {
    const maxW = /max-width:\s*(\d+)px/.exec(query);
    const matches = query === '(prefers-reduced-motion: reduce)' || (maxW ? window.innerWidth <= Number(maxW[1]) : false);
    return {
      matches,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    };
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  window.history.replaceState({}, '', '/');
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function spaceSession(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const s0 = {
    ...base,
    reducedMotion: true,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', mimeType: 'image/jpeg', uri: 'blob:photo', width: 1920, height: 1080, durationMs: 0 },
    spatialView: { ...base.spatialView, mode: 'source_2d' },
  } as ObservationSession;
  const star = s0.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
  return dispatchAlbedianAction(
    { ...s0, selectedFindingId: star.id },
    {
      type: 'space.enter',
      requestId: 'enter-3d',
      expectedViewRevision: s0.viewRevision,
      findingId: star.id,
      distancePolicy: 'measured_or_estimated',
    },
  ).session;
}

function setViewportSize(w: number, h: number) {
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(w);
  vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(h);
}

function key(target: EventTarget, init: KeyboardEventInit) {
  const ev = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
  act(() => {
    target.dispatchEvent(ev);
  });
  return ev;
}

function Harness({ initial, onState }: { initial: ObservationSession; onState: (s: ObservationSession) => void }) {
  const [session, setSession] = useState(initial);
  onState(session);
  const noop = () => {};
  return createElement(SpatialObservatoryViewport, {
    session,
    setSession,
    selectedFinding: null,
    discoveredMedia: [],
    activeDiscoveredId: null,
    setActiveDiscoveredId: noop,
    activeDiscoveredItem: null,
    uploadedMediaUrl: 'blob:photo',
    setUploadedMediaUrl: noop,
    commandText: '',
    setCommandText: noop,
    onRunUtterance: noop,
    onSelectFinding: noop,
    onPushToTalk: noop,
  });
}

describe('pilot-escape-keys-1: CraftPicker and DetailsSheet keyboard handling inside SpatialObservatoryViewport', () => {
  it('closes CraftPicker on Escape without Back to Moment, yields Escape to KeyboardHelp first, and blocks ArrowUp/ArrowDown camera nudges while CraftPicker is open', async () => {
    setViewportSize(1440, 900);
    let current = spaceSession();
    await act(async () => {
      root.render(createElement(Harness, { initial: current, onState: (s) => (current = s) }));
    });
    expect(current.spatialView.mode).toBe('catalog_3d');

    const trigger = document.querySelector('[data-testid="craft-picker"] > button') as HTMLButtonElement;
    expect(trigger).not.toBeNull();
    trigger.focus();
    await act(async () => trigger.click());
    expect(document.querySelector('[data-testid="craft-picker-panel"]')).not.toBeNull();

    // While CraftPicker (role="listbox") is open, ArrowDown and ArrowUp move option focus and never nudge the 3D camera
    key(trigger, { key: 'ArrowDown' });
    key(document.activeElement!, { key: 'ArrowDown' });
    key(document.activeElement!, { key: 'ArrowUp' });
    expect(calls.nudgeLook).not.toHaveBeenCalled();

    // Even a synthetic ArrowUp/ArrowDown that reaches window without defaultPrevented is blocked while [role="listbox"] is open
    key(window, { key: 'ArrowUp', ctrlKey: true });
    key(window, { key: 'ArrowDown', ctrlKey: true });
    expect(calls.nudgeLook).not.toHaveBeenCalled();

    // Open KeyboardHelp ("?") while CraftPicker is already open: first Escape closes KeyboardHelp, second closes CraftPicker, third returns to Moment
    key(window, { key: '?' });
    expect(document.querySelector('.keys-help [role="dialog"]')).not.toBeNull();

    key(window, { key: 'Escape' });
    expect(document.querySelector('.keys-help [role="dialog"]')).toBeNull();
    expect(document.querySelector('[data-testid="craft-picker-panel"]')).not.toBeNull();
    expect(current.spatialView.mode).toBe('catalog_3d');

    key(window, { key: 'Escape' });
    // craft-switch-motion-1: the list leaves as a card (section eight), inert from the press, gone when its exit ends
    {
      const leavingPanel = document.querySelector('[data-testid="craft-picker-panel"]')!;
      expect(leavingPanel.className).toContain('is-leaving');
      expect(leavingPanel.hasAttribute('inert')).toBe(true);
      await act(async () => leavingPanel.dispatchEvent(new Event('animationend', { bubbles: true })));
      expect(document.querySelector('[data-testid="craft-picker-panel"]')).toBeNull();
    }
    expect(document.activeElement).toBe(trigger);
    expect(current.spatialView.mode).toBe('catalog_3d');

    // Once CraftPicker is closed, ArrowUp and ArrowDown nudge the 3D camera normally, while pre-prevented events do not
    const prevented = new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true, cancelable: true });
    prevented.preventDefault();
    act(() => {
      window.dispatchEvent(prevented);
    });
    expect(calls.nudgeLook).not.toHaveBeenCalled();

    key(window, { key: 'ArrowUp' });
    key(window, { key: 'ArrowDown' });
    expect(calls.nudgeLook.mock.calls).toEqual([
      [0, 0.06],
      [0, -0.06],
    ]);

    // Now Escape with no popup open triggers Back to Moment
    key(window, { key: 'Escape' });
    expect(current.spatialView.mode).toBe('source_2d');
  });

  it('closes phone CraftPicker and phone DetailsSheet on Escape without triggering Back to Moment', async () => {
    setViewportSize(390, 844);
    let current = spaceSession();
    await act(async () => {
      root.render(createElement(Harness, { initial: current, onState: (s) => (current = s) }));
    });
    await act(async () => {
      window.dispatchEvent(new Event('resize'));
    });
    expect(current.spatialView.mode).toBe('catalog_3d');

    // 1. Phone CraftPicker sheet
    const craftBtn = document.querySelector('[data-testid="craft-picker"] > button') as HTMLButtonElement;
    craftBtn.focus();
    await act(async () => craftBtn.click());
    const craftSheet = document.querySelector('[data-testid="craft-picker-panel"]') as HTMLElement;
    expect(craftSheet).not.toBeNull();
    expect([craftSheet.getAttribute('role'), craftSheet.getAttribute('aria-modal')]).toEqual(['dialog', 'true']);
    const craftOpts = [...craftSheet.querySelectorAll<HTMLButtonElement>('[role="option"]')];
    expect(document.activeElement).toBe(craftOpts[0]);
    expect(key(craftOpts[0]!, { key: 'Tab', shiftKey: true }).defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(craftOpts.at(-1));
    expect(key(craftOpts.at(-1)!, { key: 'Tab' }).defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(craftOpts[0]);

    key(window, { key: 'Escape' });
    // craft-switch-motion-1: the list leaves as a card (section eight), inert from the press, gone when its exit ends
    {
      const leavingPanel = document.querySelector('[data-testid="craft-picker-panel"]')!;
      expect(leavingPanel.className).toContain('is-leaving');
      expect(leavingPanel.hasAttribute('inert')).toBe(true);
      await act(async () => leavingPanel.dispatchEvent(new Event('animationend', { bubbles: true })));
      expect(document.querySelector('[data-testid="craft-picker-panel"]')).toBeNull();
    }
    expect(document.activeElement).toBe(craftBtn);
    expect(current.spatialView.mode).toBe('catalog_3d');

    // 2. Phone DetailsSheet
    const detailsBtn = document.querySelector('[data-testid="details-circle"]') as HTMLButtonElement;
    expect(detailsBtn).not.toBeNull();
    detailsBtn.focus();
    await act(async () => detailsBtn.click());
    const detailsSheet = document.querySelector('[data-testid="details-sheet"]') as HTMLElement;
    expect(detailsSheet).not.toBeNull();
    expect([detailsSheet.getAttribute('role'), detailsSheet.getAttribute('aria-modal')]).toEqual(['dialog', 'true']);
    const sheetClose = detailsSheet.querySelector('[data-testid="details-circle"]') as HTMLButtonElement;
    expect(document.activeElement).toBe(sheetClose);
    expect(key(sheetClose, { key: 'Tab' }).defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(sheetClose);

    key(window, { key: 'Escape' });
    expect(document.querySelector('[data-testid="details-sheet"]')).toBeNull();
    expect(document.activeElement).toBe(document.querySelector('[data-testid="details-circle"]'));
    expect(current.spatialView.mode).toBe('catalog_3d');

    // 3. Second Escape with sheets closed triggers Back to Moment
    key(window, { key: 'Escape' });
    expect(current.spatialView.mode).toBe('source_2d');
  });
});
