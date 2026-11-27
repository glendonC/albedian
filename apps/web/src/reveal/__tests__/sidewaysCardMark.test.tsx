// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import postcss from 'postcss';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CelestialFinding, ObservationSession, ObservationSolution } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { SIDEWAYS_QUERY } from '../../components/phoneSheet.js';
import { phoneSlotCrop } from '../phoneSlot.js';
import { RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// phone-sideways-three (UIUX-phone-sideways-READY.md item 5's card-mark rule, RULED at the lead's line stamped 06:27;
// UIUX-card-mark-clear-READY.md): on a phone held on its side, when the star card opens over the picked star's own mark,
// the photo and its rings slide together on the card's entrance so the mark stands 16 px clear of the card, by its top
// or its side, whichever move is shorter, and slide back on its exit; a mark the sheet covers takes no press, one beside
// it does

const W = 874;
const H = 292;
/** The sheet's height in this window: its content taller than the room, so it stands from 64 to the foot. */
const CARD_H = 228;

/**
 * A 438 by 292 photo held sideways in an 874 by 292 window: whole, fit to the height and centred, so it spans
 * x = 218..656, y = 0..292 at 1:1 and a source pixel (u, v) marks at (218 + u + 0.5, v + 0.5).
 */
function sidewaysSession(selectedFindingId: string | null): ObservationSession {
  const base = poseSession(sample('plate_calibrated'));
  const template = base.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched')!;
  const star = (id: string, title: string, xPx: number, yPx: number): CelestialFinding => ({
    ...template,
    id,
    title,
    sourceDetection: { ...template.sourceDetection!, xPx, yPx },
  });
  const findings: CelestialFinding[] = [
    // At (437, 60): up 34 is the shortest move
    star('star-high', 'Rigel', 218.5, 59.5),
    // At (291, 277): the leading side's 87 is the shortest move
    star('star-low', 'Bellatrix', 72.5, 276.5),
    // At (230, 200): beside the sheet, never under it
    star('star-beside', 'Saiph', 11.5, 199.5),
    // At (437, 250): under the sheet
    star('star-under', 'Mintaka', 218.5, 249.5),
    // At (300, 150): under the sheet where it stands, clear of it once the photo slides 87 px to the leading side
    star('star-slid-clear', 'Alnilam', 81.5, 149.5),
  ];
  return {
    ...base,
    selectedFindingId,
    sourceAsset: { ...base.sourceAsset!, width: 438, height: 292, displayOrientation: { quarterTurnsClockwise: 0, mirrored: false } },
    cropTransform: { ...base.cropTransform, ...phoneSlotCrop(438, 292, W, H) },
    observationSolution: { ...base.observationSolution!, findings } as ObservationSolution,
  } as ObservationSession;
}

let container: HTMLDivElement;
let root: Root;
const offsetHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight');
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants: [], attempts: [] }), { status: 200 }));
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
    configurable: true,
    get(this: HTMLElement) {
      return this.getAttribute('data-testid') === 'star-card' ? CARD_H : 0;
    },
  });
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.useRealTimers();
  if (offsetHeight) Object.defineProperty(HTMLElement.prototype, 'offsetHeight', offsetHeight);
});

const q = (s: string) => container.querySelector(s);
const rootStyle = (name: string) => document.documentElement.style.getPropertyValue(name);
const render = (session: ObservationSession, vw = W, vh = H) =>
  act(() =>
    root.render(
      createElement(RevealPhoto, {
        session,
        onSelectFinding: () => {},
        onHome: () => {},
        viewportWidthPx: vw,
        viewportHeightPx: vh,
        phone: true,
        restoration: { phase: 'idle' },
      })
    )
  );

describe('phone-sideways-three: the card-mark rule on the reveal held on its side', () => {
  it('slides the photo up 34 px when the card opens over a high mark, and to the leading side 87 px over a low one', () => {
    render(sidewaysSession(null));
    expect(document.documentElement.dataset.cardMark).toBe('');
    expect(rootStyle('--card-mark-x')).toBe('0px');
    expect(rootStyle('--card-mark-y')).toBe('0px');

    render(sidewaysSession('star-high'));
    expect(q('[data-testid="star-card"]')).not.toBeNull();
    expect(rootStyle('--card-mark-x')).toBe('0px');
    expect(rootStyle('--card-mark-y')).toBe('-34px');

    render(sidewaysSession('star-low'));
    expect(rootStyle('--card-mark-x')).toBe('-87px');
    expect(rootStyle('--card-mark-y')).toBe('0px');
  });

  it('slides back on the card exit, and leaves the root clean when the reveal goes', () => {
    vi.useFakeTimers();
    render(sidewaysSession('star-high'));
    expect(rootStyle('--card-mark-y')).toBe('-34px');
    render(sidewaysSession(null));
    // The card is still going down: the photo goes back with it
    expect(q('[data-testid="star-card"]')).not.toBeNull();
    expect(rootStyle('--card-mark-y')).toBe('0px');
    act(() => vi.advanceTimersByTime(2000));
    act(() => root.unmount());
    root = createRoot(container);
    expect(document.documentElement.dataset.cardMark).toBeUndefined();
    expect(rootStyle('--card-mark-x')).toBe('');
    expect(rootStyle('--card-mark-y')).toBe('');
  });

  it('takes presses only on marks the slid sheet does not cover: one beside the sheet stays pressable', () => {
    render(sidewaysSession('star-high'));
    const beside = q('[data-testid="reveal-mark-star-beside"]') as HTMLButtonElement;
    const under = q('[data-testid="reveal-mark-star-under"]') as HTMLButtonElement;
    expect(beside.hasAttribute('inert')).toBe(false);
    expect(beside.tabIndex).toBe(0);
    expect(under.hasAttribute('inert')).toBe(true);
    expect(under.tabIndex).toBe(-1);
  });

  it('reads the cover where the marks stand after the slide, not before it', () => {
    render(sidewaysSession('star-low'));
    expect(rootStyle('--card-mark-x')).toBe('-87px');
    const slid = q('[data-testid="reveal-mark-star-slid-clear"]') as HTMLButtonElement;
    expect(slid.hasAttribute('inert')).toBe(false);
    expect(slid.tabIndex).toBe(0);
  });

  it('slides back at Go there, as the card leaves with its star still picked', () => {
    vi.useFakeTimers();
    render(sidewaysSession('star-high'));
    expect(rootStyle('--card-mark-y')).toBe('-34px');
    const going = sidewaysSession('star-high');
    render({ ...going, spatialView: { ...going.spatialView, transitionPhase: 'phase_a_photo_to_sky' } } as ObservationSession);
    expect(q('[data-testid="star-card"]')).not.toBeNull();
    expect(rootStyle('--card-mark-y')).toBe('0px');
  });

  it("slides the phone's compare with the photo: its pictures, its labels and the marks over it are one sliding set (AUDIT-C's case)", async () => {
    const media = JSON.parse(fs.readFileSync(path.resolve(here, '../../../../../tests/fixtures/media_contract.json'), 'utf8')) as { valid: { MediaVariant: Array<Record<string, unknown>> } };
    const template = media.valid.MediaVariant.find((v) => v.kind === 'restoration')!;
    const session = sidewaysSession('star-high');
    const sha = session.sourceAsset!.sha256;
    const restored = { ...template, id: 'var_0000000000000002', createdAtUTC: '2026-11-19T18:20:00Z', sourceSha256: sha, sourceAssetId: `src_${sha.slice(0, 16)}`, uri: `/api/media/${sha}/variants/var_0000000000000002/file` };
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants: [restored], attempts: [] }), { status: 200 }));
    render(session);
    for (let i = 0; i < 5; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
    const compare = q('[data-testid="restore-compare"]') as HTMLElement;
    expect(compare.className).toBe('restore-compare is-phone');
    expect(q('[data-testid="star-card"]')).not.toBeNull();
    expect(rootStyle('--card-mark-y')).toBe('-34px');
    // The selectors reveal.css itself slides by the two lengths, held on its side
    const slid: string[] = [];
    postcss.parse(css).walkAtRules('media', (at) => {
      if (at.params !== SIDEWAYS_QUERY) return;
      at.walkRules((rule) => {
        if (rule.some((d) => d.type === 'decl' && d.prop === 'transform' && d.value.includes('--card-mark-x'))) slid.push(...rule.selectors);
      });
    });
    const sliding = slid.join(', ');
    const drawnOnThePhoto = [compare, ...container.querySelectorAll('.restore-compare.is-labels'), q('.reveal-marks')!, ...container.querySelectorAll('.align-layer')];
    expect(container.querySelectorAll('.restore-compare.is-labels').length).toBeGreaterThan(0);
    // The stage stands beside the reveal (SpatialObservatoryViewport), keyed by the root's mark: matched by the same set
    const stage = document.createElement('div');
    stage.className = 'alb-source-stage';
    document.body.appendChild(stage);
    try {
      for (const el of [...drawnOnThePhoto, stage]) expect(el.matches(sliding), el.className).toBe(true);
    } finally {
      stage.remove();
    }
  });

  it('moves nothing on a phone held upright (its own twin is a later unit)', () => {
    // The design size and the iPhone app's Open sheet (402 by 650)
    for (const [vw, vh] of [[390, 844], [402, 650]]) {
      render(sidewaysSession('star-high'), vw, vh);
      expect(document.documentElement.dataset.cardMark, `${vw}x${vh}`).toBeUndefined();
      expect(rootStyle('--card-mark-y'), `${vw}x${vh}`).toBe('');
    }
  });
});

const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const declsIn = (params: string, selector: string) => {
  const found: Record<string, string> = {};
  postcss.parse(css).walkAtRules('media', (at) => {
    if (at.params !== params) return;
    at.walkRules((rule) => {
      if (!rule.selectors.map((s) => s.replace(/\s+/g, ' ')).includes(selector)) return;
      rule.walkDecls((d) => void (found[d.prop] = d.value.replace(/\s+/g, ' ')));
    });
  });
  return found;
};

/** Everything drawn as the photo or on it, which slides as one (version two: the compare and the aligned rings too). */
const SLIDING = [
  ':root[data-card-mark] .alb-source-stage',
  '.reveal-photo.is-phone .reveal-marks',
  '.reveal-photo.is-phone .restore-compare.is-phone',
  '.reveal-photo.is-phone .align-layer',
];

describe('phone-sideways-three: the photo and its rings slide together on the sheet\'s own curve', () => {
  it('moves the photo stage and the marks by the same two lengths, on the sheet\'s 500 ms curve', () => {
    for (const selector of SLIDING) {
      expect(declsIn(SIDEWAYS_QUERY, selector), selector).toEqual({
        transform: 'translate(var(--card-mark-x, 0px), var(--card-mark-y, 0px))',
        transition: 'transform var(--look-mo-dur-sheet) var(--look-mo-ease-sheet)',
      });
    }
  });

  it('moves them at once under Reduce Motion', () => {
    for (const selector of SLIDING) {
      expect(declsIn(`${SIDEWAYS_QUERY} and (prefers-reduced-motion: reduce)`, selector), selector).toEqual({ transition: 'none' });
    }
  });
});
