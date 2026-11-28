// @vitest-environment jsdom
import fc from 'fast-check';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { orientedSize, type ObservationSession } from '@albedian/contracts';
import { choosePhotoOnHome } from '../../components/__tests__/homeEntry.js';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { SAFE_AREA_LEFT_VAR, SAFE_AREA_RIGHT_VAR } from '../../components/phoneSheet.js';
import { sourcePhotoDisplayRect } from '../../components/sourcePhotoRect.js';
import { leastColumnPx, useNoMatchColumnLeastPx } from '../noMatchColumn.js';
import { NOMATCH_COLUMN_GAP_PX, NOMATCH_COLUMN_LEAST_PX, noMatchPhotoRoomPx, phoneSlotCrop } from '../phoneSlot.js';

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

// nomatch-sideways-wide-one (UIUX-phone-sideways-READY.md 8f and 8g, DECIDED-BY-LEAD at the line stamped 12:29 on 27 Nov;
// WEB-UX's measure on public rp-a-07, 3:2: fit to the height a wide no-match photo left its column 115 px at 667 by 320
// and every action below the window). 8f: the photo gives way before an action does; the column's least width is the
// width at which the sentence and every action stand on the first screen, never less than 284; the photo whole, fit to
// the room left, its leading edge at the leading margin, centred in the height; a near-square photo unchanged. 8g: the
// photo keeps its place and size while the column's content changes.

const SEED = process.env.ALBEDIAN_FC_SEED ? { seed: Number(process.env.ALBEDIAN_FC_SEED) } : {};
// rp-a-07's shape (3:2) and FIELD's rp-b-05 lab copy (near square)
const WIDE = [3000, 2000] as const;
const SQUARE = [1296, 1334] as const;

function placed(photo: readonly [number, number], w: number, h: number, lead?: number, room?: number) {
  return sourcePhotoDisplayRect(photo[0], photo[1], w, h, phoneSlotCrop(photo[0], photo[1], w, h, 'image', lead, room));
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
  document.documentElement.style.removeProperty(SAFE_AREA_RIGHT_VAR);
  vi.restoreAllMocks();
});
const q = (s: string) => container.querySelector<HTMLElement>(s);

describe('nomatch-sideways-wide-one 8f: the photo gives way before an action does', () => {
  it('the record\'s own measure: rp-a-07 at 667 by 320 is 311 by 207 at 24, centred in the height, its column 284 from 359 to 643', () => {
    expect(NOMATCH_COLUMN_LEAST_PX).toBe(284);
    expect(NOMATCH_COLUMN_GAP_PX).toBe(24);
    const room = noMatchPhotoRoomPx(667, 24, 24);
    expect(room).toBe(311);
    const rect = placed(WIDE, 667, 320, 24, room);
    expect(rect.leftPx).toBeCloseTo(24, 6);
    expect(rect.widthPx).toBeCloseTo(311, 6);
    expect(Math.round(rect.heightPx)).toBe(207);
    expect(rect.topPx).toBeCloseTo((320 - rect.heightPx) / 2, 6);
    expect(rect.leftPx + rect.widthPx + 24).toBeCloseTo(359, 6);
    expect(667 - 24 - (rect.leftPx + rect.widthPx + 24)).toBeCloseTo(284, 6);
  });

  it('with the insets the 3:2 photo leaves its column 284 at 874 by 402 and 288 at 874 by 292, where the height holds it', () => {
    const tall = placed(WIDE, 874, 402, 62, noMatchPhotoRoomPx(874, 62, 62));
    expect(tall.widthPx).toBeCloseTo(442, 6);
    expect(tall.topPx).toBeCloseTo((402 - tall.heightPx) / 2, 6);
    expect(874 - 62 - (tall.leftPx + tall.widthPx + 24)).toBeCloseTo(284, 6);
    const short = placed(WIDE, 874, 292, 62, noMatchPhotoRoomPx(874, 62, 62));
    expect(short.widthPx).toBeCloseTo(438, 6);
    expect(short.heightPx).toBeCloseTo(292, 6);
    expect(short.topPx).toBeCloseTo(0, 6);
    expect(874 - 62 - (short.leftPx + short.widthPx + 24)).toBeCloseTo(288, 6);
  });

  it('a near-square photo is unchanged at the three sizes (its column is already wider)', () => {
    for (const [w, h, lead] of [[874, 292, 62], [874, 402, 62], [667, 320, 24]] as const) {
      expect(placed(SQUARE, w, h, lead, noMatchPhotoRoomPx(w, lead, lead)), `${w}x${h}`).toEqual(placed(SQUARE, w, h, lead));
    }
  });

  it('stands any photo whole in the room at the leading margin, centred in the height, as large as the room and the height allow', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 100, max: 8000 }),
        fc.integer({ min: 100, max: 8000 }),
        fc.integer({ min: 600, max: 960 }),
        fc.integer({ min: 280, max: 500 }),
        fc.integer({ min: 24, max: 62 }),
        fc.integer({ min: 24, max: 62 }),
        fc.integer({ min: 284, max: 340 }),
        (pw, ph, w, h, lead, trail, least) => {
          const room = noMatchPhotoRoomPx(w, lead, trail, least);
          expect(room).toBe(w - lead - trail - 24 - least);
          const rect = placed([pw, ph], w, h, lead, room);
          const free = placed([pw, ph], w, h, lead);
          expect(rect.leftPx).toBeCloseTo(lead, 6);
          expect(rect.widthPx).toBeLessThanOrEqual(room + 1e-6);
          expect(rect.heightPx).toBeLessThanOrEqual(h + 1e-6);
          expect(rect.widthPx / rect.heightPx).toBeCloseTo(pw / ph, 6);
          expect(rect.topPx).toBeCloseTo((h - rect.heightPx) / 2, 6);
          expect(Math.abs(rect.widthPx - room) < 1e-6 || Math.abs(rect.heightPx - h) < 1e-6).toBe(true);
          // The column from the photo's trailing edge plus 24 to the trailing margin is never narrower than its least width
          expect(w - trail - (rect.leftPx + rect.widthPx + 24)).toBeGreaterThanOrEqual(least - 1e-6);
          // A photo the height already holds inside the room stands as before
          if (free.widthPx <= room) expect(rect).toEqual(free);
        }
      ),
      SEED
    );
  });

  it('upright (402 by 650 the app\'s Open sheet among them), a clip, and a photo with no leading edge ignore the room', () => {
    for (const [w, h, kind] of [[390, 844, 'image'], [390, 480, 'image'], [402, 650, 'image'], [874, 292, 'video'], [667, 320, 'video']] as const) {
      expect(phoneSlotCrop(WIDE[0], WIDE[1], w, h, kind, 24, 100), `${w}x${h} ${kind}`).toEqual(phoneSlotCrop(WIDE[0], WIDE[1], w, h, kind));
    }
    expect(phoneSlotCrop(WIDE[0], WIDE[1], 667, 320, 'image', undefined, 100)).toEqual(phoneSlotCrop(WIDE[0], WIDE[1], 667, 320, 'image'));
    expect(noMatchPhotoRoomPx(300, 24, 24)).toBe(0);
  });
});

/**
 * A column laid out by a stand-in for the browser: its top at 64, its foot at `footPx`, the sentence's foot and the
 * actions' rows a function of the width the probe is given; the warnings far below, which the first screen never needs.
 */
function columnWith(actionsFootAt: (widthPx: number) => number, footPx = 271) {
  const host = document.createElement('div');
  host.innerHTML =
    '<div class="reveal-phone-scroll is-column" data-testid="reveal-phone-scroll" style="left: 370px"><div class="reveal-status is-unmatched is-phone">' +
    '<div class="reveal-phone-lead"><p class="reveal-status__lead">Found 31,371 points of light, but could not match them to stars for sure.</p></div>' +
    '<div class="reveal-phone-go"><button>Show predicted sky</button></div><div class="reveal-phone-go reveal-phone-go--more"><button>Line it up yourself</button></div>' +
    '<div class="reveal-phone-go reveal-phone-go--restore"><button>Restore</button></div><ul class="reveal-status__warns"><li>One warning.</li></ul></div></div>';
  document.body.appendChild(host);
  const tried: number[] = [];
  const probes: Array<{ testid: string | null; hidden: string | null; visibility: string; left: string; inert: boolean }> = [];
  const widthOf = (el: Element) => parseFloat((el.closest('.is-column') as HTMLElement | null)?.style.width ?? '') || 0;
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    const column = this.closest('.is-column');
    const top = column ? 64 : 0;
    let bottom = top;
    if (this.matches('.is-column')) {
      const el = this as HTMLElement;
      tried.push(widthOf(this));
      probes.push({ testid: el.getAttribute('data-testid'), hidden: el.getAttribute('aria-hidden'), visibility: el.style.visibility, left: el.style.left, inert: el.inert });
      bottom = footPx;
    } else if (this.matches('.reveal-phone-lead')) bottom = actionsFootAt(widthOf(this)) - 60;
    else if (this.matches('.reveal-phone-go')) bottom = actionsFootAt(widthOf(this));
    else if (this.matches('.reveal-status__warns')) bottom = 900;
    return { top, bottom, left: 0, right: 0, width: 0, height: bottom - top, x: 0, y: top, toJSON: () => ({}) } as DOMRect;
  });
  vi.spyOn(Element.prototype, 'clientHeight', 'get').mockImplementation(function (this: HTMLElement) {
    return this.matches('.is-column') ? footPx - 64 : 0;
  });
  return { host, column: host.querySelector<HTMLElement>('.is-column')!, tried, probes };
}

describe('nomatch-sideways-wide-one 8f: the column\'s least width, read from the column laid out', () => {
  it('is 284 where the sentence and every action already stand at 284, read at 284 alone', () => {
    const { host, column, tried } = columnWith(() => 255);
    expect(leastColumnPx(column, 442)).toBe(284);
    expect(tried).toEqual([284]);
    expect(host.children).toHaveLength(1);
    host.remove();
  });

  it('is the least width at which the actions end at or above the column\'s foot, the warnings not counted', () => {
    // Three lines of the sentence to 300 px (the actions to 282, past the 271 foot), two from there (to 255)
    const { host, column } = columnWith((width) => (width >= 300 ? 255 : 282));
    expect(leastColumnPx(column, 442)).toBe(300);
    // At the foot exactly the action stands; half a pixel past it too (sub-pixel layout), not one past
    const exact = columnWith((width) => (width >= 310 ? 270 : 271.4));
    expect(leastColumnPx(exact.column, 442)).toBe(284);
    const past = columnWith((width) => (width >= 310 ? 271 : 272));
    expect(leastColumnPx(past.column, 442)).toBe(310);
    host.remove();
    exact.host.remove();
    past.host.remove();
  });

  it('stays 284 where no width up to the widest would hold them, or the widest is no wider: the column then scrolls (8d)', () => {
    const never = columnWith(() => 400);
    expect(leastColumnPx(never.column, 442)).toBe(284);
    const narrow = columnWith((width) => (width >= 300 ? 255 : 282));
    expect(leastColumnPx(narrow.column, 284)).toBe(284);
    expect(narrow.tried).toEqual([]);
    never.host.remove();
    narrow.host.remove();
  });

  it('lays a hidden copy out, never the column itself, and takes the copy away', () => {
    const { host, column, probes } = columnWith((width) => (width >= 330 ? 255 : 282));
    expect(leastColumnPx(column, 442)).toBe(330);
    expect(probes.length).toBeGreaterThan(2);
    for (const probe of probes) expect(probe).toEqual({ testid: null, hidden: 'true', visibility: 'hidden', left: 'auto', inert: true });
    expect(column.style.width).toBe('');
    expect(column.style.left).toBe('370px');
    expect(host.querySelectorAll('.is-column')).toHaveLength(1);
    host.remove();
  });
});

describe('nomatch-sideways-wide-one 8g: the photo keeps its place and size while the column\'s content changes', () => {
  function Probe(props: { active: boolean; w: number; h: number; out: number[] }) {
    const px = useNoMatchColumnLeastPx(props.active, props.w, 24, 24, props.h);
    props.out.push(px);
    return null;
  }
  it('measures once the no-match screen stands, keeps that width while another block takes its place, and again for new words or a new window', () => {
    const layout = { least: 300 };
    const { host, tried } = columnWith((width) => (width >= layout.least ? 255 : 282));
    const out: number[] = [];
    act(() => root.render(createElement(Probe, { active: true, w: 667, h: 320, out })));
    expect(out.at(-1)).toBe(300);
    const measured = tried.length;
    // Line it up yourself's block (or its refusal, or a voice answer) takes the column's place: nothing is measured
    const lead = host.querySelector('.reveal-phone-lead')!;
    lead.remove();
    layout.least = 340;
    act(() => root.render(createElement(Probe, { active: true, w: 667, h: 320, out })));
    expect(out.at(-1)).toBe(300);
    expect(tried.length).toBe(measured);
    // Back on the no-match screen with the same words: the same width, unmeasured
    host.querySelector('.reveal-status')!.prepend(lead);
    act(() => root.render(createElement(Probe, { active: true, w: 667, h: 320, out })));
    expect(out.at(-1)).toBe(300);
    expect(tried.length).toBe(measured);
    // New words are measured again
    host.querySelector('.reveal-status__lead')!.textContent = 'Too few points of light to match to stars.';
    act(() => root.render(createElement(Probe, { active: true, w: 667, h: 320, out })));
    expect(out.at(-1)).toBe(340);
    // Any screen but the sideways no-match one: 284 and nothing measured, in the same window too
    const before = tried.length;
    act(() => root.render(createElement(Probe, { active: false, w: 667, h: 320, out })));
    expect(out.at(-1)).toBe(284);
    act(() => root.render(createElement(Probe, { active: false, w: 402, h: 650, out })));
    expect(out.at(-1)).toBe(284);
    expect(tried.length).toBe(before);
    // A new window reads 284 until it is measured, in the same commit, before the browser paints
    act(() => root.render(createElement(Probe, { active: true, w: 667, h: 320, out })));
    expect(out.at(-1)).toBe(340);
    out.length = 0;
    layout.least = 296;
    act(() => root.render(createElement(Probe, { active: true, w: 874, h: 402, out })));
    expect(out).toEqual([284, 296]);
    host.remove();
  });
});

describe('nomatch-sideways-wide-one: the viewport stands a wide no-match photo in the room its column leaves', () => {
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
  const props = (session: ObservationSession, url: string | null) =>
    ({
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
    }) as unknown as Parameters<typeof SpatialObservatoryViewport>[0];
  function wide(): ObservationSession {
    const base = poseSession(sample('horizon_predicted_only'));
    return { ...base, sourceAsset: { ...base.sourceAsset!, width: WIDE[0], height: WIDE[1] } } as ObservationSession;
  }
  function open(session: ObservationSession) {
    act(() => root.render(createElement(SpatialObservatoryViewport, props(session, null))));
    choosePhotoOnHome(container);
    act(() => root.render(createElement(SpatialObservatoryViewport, props(session, 'blob:photo'))));
  }
  function photoLeftOfColumn(session: ObservationSession, w: number, h: number, lead: number, room: number) {
    const shown = orientedSize(session.sourceAsset!.width, session.sourceAsset!.height, session.sourceAsset!.displayOrientation);
    const rect = sourcePhotoDisplayRect(shown.widthPx, shown.heightPx, w, h, phoneSlotCrop(shown.widthPx, shown.heightPx, w, h, 'image', lead, room));
    return rect.leftPx + rect.widthPx + 24;
  }

  it('at 667 by 320 the 3:2 photo is 311 wide at 24 and its column stands 24 past it, at 359', () => {
    const session = wide();
    setWindow(667, 320);
    open(session);
    const screen = q('[data-testid="reveal-phone-scroll"]');
    if (!screen) throw new Error(`no column: ${container.innerHTML.slice(0, 400)}`);
    expect(screen.className).toBe('reveal-phone-scroll is-column');
    expect(parseFloat(screen.style.left)).toBeCloseTo(359, 3);
    expect(parseFloat(screen.style.left)).toBeCloseTo(photoLeftOfColumn(session, 667, 320, 24, 311), 3);
  });

  it('where the sentence and actions would not stand at 284 the column widens to its least width and the photo gives way', () => {
    // The browser's layout stood in for: the hidden copy's sentence and actions end at 282 below 320 px, at 255 from it
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      const probe = this.closest<HTMLElement>('.is-column[aria-hidden="true"]');
      const width = parseFloat(probe?.style.width ?? '') || 0;
      const bottom = !probe ? 0 : this === probe ? 308 : this.matches('.reveal-phone-lead, .reveal-phone-go') ? (width >= 320 ? 255 : 282) : 0;
      const top = probe ? 64 : 0;
      return { top, bottom, left: 0, right: 0, width: 0, height: bottom - top, x: 0, y: top, toJSON: () => ({}) } as DOMRect;
    });
    vi.spyOn(Element.prototype, 'clientHeight', 'get').mockImplementation(function (this: Element) {
      return this.matches('.is-column[aria-hidden="true"]') ? 308 - 64 - 30 : 0;
    });
    const session = wide();
    setWindow(667, 320);
    open(session);
    const screen = q('[data-testid="reveal-phone-scroll"]')!;
    expect(container.querySelectorAll('.is-column')).toHaveLength(1);
    // 667 less the two 24 px margins, the 24 px gap and the 320 px column: the photo 275 wide, the column at 323
    expect(parseFloat(screen.style.left)).toBeCloseTo(24 + 275 + 24, 3);
    expect(parseFloat(screen.style.left)).toBeCloseTo(photoLeftOfColumn(session, 667, 320, 24, 275), 3);
  });

  it('with the trailing inset of 62 the room is narrower by it, as the column\'s own trailing margin is', () => {
    const session = wide();
    document.documentElement.style.setProperty(SAFE_AREA_LEFT_VAR, '62px');
    document.documentElement.style.setProperty(SAFE_AREA_RIGHT_VAR, '62px');
    setWindow(874, 402);
    open(session);
    expect(parseFloat(q('[data-testid="reveal-phone-scroll"]')!.style.left)).toBeCloseTo(62 + 442 + 24, 3);
  });
});
