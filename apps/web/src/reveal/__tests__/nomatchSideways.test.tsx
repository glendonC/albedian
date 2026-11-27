// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import fc from 'fast-check';
import postcss from 'postcss';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { orientedSize, type ObservationSession } from '@albedian/contracts';
import { choosePhotoOnHome } from '../../components/__tests__/homeEntry.js';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { readLeadingMarginPx, SAFE_AREA_LEFT_VAR } from '../../components/phoneSheet.js';
import { sourcePhotoDisplayRect } from '../../components/sourcePhotoRect.js';
import { PhoneNoMatch } from '../PhoneNoMatch.js';
import { phoneSlotCrop } from '../phoneSlot.js';
import type { RevealStatus } from '../revealStatus.js';
import { RevealPhoto } from '../RevealPhoto.js';

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

// nomatch-sideways-one (UIUX-phone-sideways-READY.md item 8, DECIDED-BY-LEAD at the line stamped 03:48 on 27 Nov; FIELD's
// break on public rp-b-05: held on its side a photo with no match showed only the photo and the top bar, every action
// below the window): 8a the photo whole, fit to the window's height, its leading edge at the leading margin (62 with the
// insets, 24 without); 8b one column beside it, from the photo's trailing edge plus 24 px to the trailing margin, its top
// at 64, the sentence, 16 px under it the actions in rows (10 apart in a row, 8 between rows), 16 px under them the
// warnings; 8d the column scrolls inside itself with the column foot fade, the photo staying put; 8e a clip unchanged

const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const rootCss = fs.readFileSync(path.resolve(here, '../../styles/albedian.css'), 'utf8');
const SEED = process.env.ALBEDIAN_FC_SEED ? { seed: Number(process.env.ALBEDIAN_FC_SEED) } : {};
const STATUS: RevealStatus = { kind: 'unmatched', lines: ['No stars matched.'], warnings: ['One warning.'], predictedSky: true } as unknown as RevealStatus;

function declarationsOf(selector: string): Record<string, string> {
  const found: Record<string, string> = {};
  let rules = 0;
  postcss.parse(css).walkRules((rule) => {
    if (rule.selector.replace(/\s+/g, ' ') !== selector || rule.parent?.type !== 'root') return;
    rules += 1;
    rule.walkDecls((decl) => {
      found[decl.prop] = decl.value.replace(/\s+/g, ' ');
    });
  });
  expect(rules, selector).toBe(1);
  return found;
}

/** The no-match photo cropped as the viewport crops it: with the leading edge held on its side. */
function cropped(session: ObservationSession, w: number, h: number, leadingPx?: number): ObservationSession {
  const asset = session.sourceAsset!;
  const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
  return { ...session, cropTransform: { ...session.cropTransform, ...phoneSlotCrop(shown.widthPx, shown.heightPx, w, h, asset.mediaKind, leadingPx) } } as ObservationSession;
}
function shownRect(session: ObservationSession, w: number, h: number) {
  const asset = session.sourceAsset!;
  const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
  return sourcePhotoDisplayRect(shown.widthPx, shown.heightPx, w, h, session.cropTransform);
}

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
  document.documentElement.style.removeProperty('--phone-scroll');
  document.documentElement.style.removeProperty(SAFE_AREA_LEFT_VAR);
  vi.restoreAllMocks();
});
const q = (s: string) => container.querySelector<HTMLElement>(s);

describe('nomatch-sideways-one: the no-match photo held on its side', () => {
  it('8a: stands a sideways photo given a leading edge whole at that edge, fit to the height, its vertical place unchanged', () => {
    // FIELD's rp-b-05 lab copy (1296 by 1334) at 874 by 292 with the 62 px insets: the record's photo 284 wide at 62
    const rect = sourcePhotoDisplayRect(1296, 1334, 874, 292, phoneSlotCrop(1296, 1334, 874, 292, 'image', 62));
    expect(rect.leftPx).toBeCloseTo(62, 6);
    expect(rect.widthPx).toBeCloseTo((1296 / 1334) * 292, 6);
    expect(Math.round(rect.widthPx)).toBe(284);
    expect(rect.topPx).toBeCloseTo(0, 6);
    expect(rect.heightPx).toBeCloseTo(292, 6);
    fc.assert(
      fc.property(fc.integer({ min: 100, max: 8000 }), fc.integer({ min: 100, max: 8000 }), fc.integer({ min: 560, max: 960 }), fc.integer({ min: 280, max: 500 }), fc.integer({ min: 24, max: 80 }), (w, h, viewW, viewH, lead) => {
        const centred = sourcePhotoDisplayRect(w, h, viewW, viewH, phoneSlotCrop(w, h, viewW, viewH, 'image'));
        const led = sourcePhotoDisplayRect(w, h, viewW, viewH, phoneSlotCrop(w, h, viewW, viewH, 'image', lead));
        expect(led.leftPx).toBeCloseTo(lead, 6);
        expect(led.widthPx).toBeCloseTo(centred.widthPx, 6);
        expect(led.heightPx).toBeCloseTo(centred.heightPx, 6);
        expect(led.topPx).toBeCloseTo(centred.topPx, 6);
      }),
      SEED
    );
  });

  it('8a, 8e: keeps the upright slot and a clip as they were, whatever leading edge is given', () => {
    for (const [w, h, kind] of [[390, 844, 'image'], [390, 480, 'image'], [874, 292, 'video'], [667, 320, 'video']] as const) {
      expect(phoneSlotCrop(1296, 1334, w, h, kind, 62), `${w}x${h} ${kind}`).toEqual(phoneSlotCrop(1296, 1334, w, h, kind));
    }
  });

  it('8a: reads the leading margin as 24 px, or the leading safe-area inset where it is wider', () => {
    expect(postcss.parse(rootCss).nodes.some((n) => n.type === 'rule' && n.selector === ':root' && n.nodes.some((d) => d.type === 'decl' && d.prop === SAFE_AREA_LEFT_VAR && d.value === 'env(safe-area-inset-left, 0px)'))).toBe(true);
    expect(readLeadingMarginPx()).toBe(24);
    document.documentElement.style.setProperty(SAFE_AREA_LEFT_VAR, '62px');
    expect(readLeadingMarginPx()).toBe(62);
    document.documentElement.style.setProperty(SAFE_AREA_LEFT_VAR, '10px');
    expect(readLeadingMarginPx()).toBe(24);
  });

  it('8b, 8d: lays the screen out as one column at its left edge that scrolls alone, the photo staying put', () => {
    act(() => root.render(createElement(PhoneNoMatch, { status: STATUS, onShowPredictedSky: () => {}, onAlign: () => {}, onRestore: () => {}, columnLeftPx: 370 })));
    const screen = q('[data-testid="reveal-phone-scroll"]')!;
    expect(screen.className).toBe('reveal-phone-scroll is-column');
    expect(screen.style.left).toBe('370px');
    expect(document.documentElement.dataset.phoneScroll).toBeUndefined();
    screen.scrollTop = 120;
    act(() => screen.dispatchEvent(new Event('scroll')));
    expect(document.documentElement.style.getPropertyValue('--phone-scroll')).toBe('');
    // The order is upright's: the sentence, Show predicted sky (filled), Line it up yourself, Restore, the warnings
    const order = [...screen.querySelectorAll('.reveal-phone-lead, button, .reveal-status__warns')].map((el) => (el.tagName === 'BUTTON' ? el.textContent : el.className));
    expect(order).toEqual(['reveal-phone-lead', 'Show predicted sky', 'Line it up yourself', 'Restore', 'reveal-status__warns']);
    expect(screen.querySelector('button')!.className).toContain('look-main');
    // Upright: the whole screen scrolls and the photo moves up with it
    act(() => root.render(createElement(PhoneNoMatch, { status: STATUS, onShowPredictedSky: () => {} })));
    const upright = q('[data-testid="reveal-phone-scroll"]')!;
    expect(upright.className).toBe('reveal-phone-scroll');
    expect(upright.style.left).toBe('');
    expect(document.documentElement.dataset.phoneScroll).toBe('');
  });

  it('8b, 8d: holds the column\'s rules as recorded', () => {
    expect(declarationsOf('.reveal-phone-scroll.is-column')).toEqual({
      top: '64px',
      right: 'max(24px, env(safe-area-inset-right))',
      bottom: 'max(12px, env(safe-area-inset-bottom))',
      '-webkit-mask-image': 'linear-gradient(to bottom, #000 calc(100% - 44px), transparent)',
      'mask-image': 'linear-gradient(to bottom, #000 calc(100% - 44px), transparent)',
    });
    expect(declarationsOf('.reveal-phone-scroll.is-column .reveal-status.is-unmatched.is-phone')).toEqual({
      display: 'flex',
      'flex-wrap': 'wrap',
      'align-items': 'flex-start',
      'column-gap': '10px',
      'row-gap': '8px',
      padding: '0 0 44px',
    });
    // 16 px from the sentence to the actions and from the actions to the warnings: 8 of margin and the 8 between rows
    expect(declarationsOf('.reveal-phone-scroll.is-column .reveal-phone-lead')).toEqual({ 'flex-basis': '100%', 'min-height': '0', 'margin-bottom': '8px' });
    expect(declarationsOf('.reveal-phone-scroll.is-column .reveal-phone-go')).toEqual({ display: 'block', margin: '0' });
    expect(declarationsOf('.reveal-phone-scroll.is-column .reveal-status__warns')).toEqual({ 'flex-basis': '100%', margin: '8px 0 0' });
    // The sentence keeps upright's face: Geist 300, 22 px, 1.2
    expect(declarationsOf('.reveal-status.is-phone .reveal-status__lead')).toEqual({ margin: '0', font: '300 22px / 1.2 var(--look-font-sans)' });
  });

  it('8b: the reveal stands the column 24 px past the photo\'s trailing edge held on its side, and only there', () => {
    const noMatch = poseSession(sample('horizon_predicted_only'));
    const show = (session: ObservationSession, w: number, h: number) =>
      act(() =>
        root.render(
          createElement(RevealPhoto, { session, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: w, viewportHeightPx: h, phone: true, restoration: { phase: 'idle' }, onShowPredictedSky: () => {} } as unknown as Parameters<typeof RevealPhoto>[0])
        )
      );
    for (const [w, h, lead] of [[874, 292, 62], [874, 402, 62], [667, 320, 24]] as const) {
      const session = cropped(noMatch, w, h, lead);
      show(session, w, h);
      const rect = shownRect(session, w, h);
      const screen = q('[data-testid="reveal-phone-scroll"]')!;
      expect(screen.className, `${w}x${h}`).toBe('reveal-phone-scroll is-column');
      expect(parseFloat(screen.style.left), `${w}x${h}`).toBeCloseTo(lead + rect.widthPx + 24, 3);
    }
    for (const [w, h] of [[390, 844], [390, 480], [360, 400]] as const) {
      show(cropped(noMatch, w, h), w, h);
      expect(q('[data-testid="reveal-phone-scroll"]')!.className, `${w}x${h}`).toBe('reveal-phone-scroll');
    }
    // 8e: a clip with no match keeps its upright screen
    const clip = poseSession(sample('horizon_predicted_only'), 'video');
    show(cropped(clip, 874, 292), 874, 292);
    expect(q('[data-testid="reveal-status"]')!.className).toContain('is-clip');
    expect(q('[data-testid="reveal-phone-scroll"]')!.className).toBe('reveal-phone-scroll');
  });
});

describe('nomatch-sideways-one: the viewport crops the no-match photo to the leading margin held on its side', () => {
  const setWindow = (w: number, h: number) => {
    Object.defineProperty(window, 'innerWidth', { value: w, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: h, configurable: true });
    (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({
      matches: w <= 720 ? query.includes('max-width: 720px') : h <= 500 && query.includes('max-height: 500px') && !query.includes('portrait'),
      addEventListener: () => {},
      removeEventListener: () => {},
    });
  };
  const noop = () => {};
  function openViewport(session: ObservationSession) {
    const props = (url: string | null) => ({
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
    });
    act(() => root.render(createElement(SpatialObservatoryViewport, props(null))));
    choosePhotoOnHome(container);
    act(() => root.render(createElement(SpatialObservatoryViewport, props('blob:photo'))));
  }

  it('stands the photo at 24 px with no inset and the column 24 px past it, the upright window unchanged', () => {
    const noMatch = poseSession(sample('horizon_predicted_only'));
    setWindow(874, 292);
    openViewport(noMatch);
    const led = shownRect(cropped(noMatch, 874, 292, 24), 874, 292);
    const screen = q('[data-testid="reveal-phone-scroll"]')!;
    expect(screen.className).toBe('reveal-phone-scroll is-column');
    expect(parseFloat(screen.style.left)).toBeCloseTo(24 + led.widthPx + 24, 3);
    act(() => root.unmount());
    root = createRoot(container);
    setWindow(390, 844);
    openViewport(noMatch);
    expect(q('[data-testid="reveal-phone-scroll"]')!.className).toBe('reveal-phone-scroll');
  });
});
