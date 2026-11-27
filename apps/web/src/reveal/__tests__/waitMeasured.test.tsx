// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import postcss from 'postcss';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession, SourceDetection } from '@albedian/contracts';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { choosePhotoOnHome } from '../../components/__tests__/homeEntry.js';
import { sourcePixelToViewport } from '../../components/sourcePhotoRect.js';
import type { Arrival } from '../arrival.js';
import { MeasuredMarks, measuredMarksOf } from '../MeasuredMarks.js';
import { phoneSlotCrop } from '../phoneSlot.js';

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

// wait-measured-one (UIUX-first-use-wow-READY.md item 3, RULED at the lead's line stamped 03:22 on 25 Nov; the lead's
// order after the restart on 27 Nov): the wait shows what it has found. When Measured ends the solve's record holds
// measuredAtUTC and GET /api/ingest/jobs/{job_id}/measured serves the detections (PLATFORM's wait-detections-one): each
// stands on the photo at its measured place as the Observed class's glyph, brightest first as marks arrive. Nothing
// before the stamp, nothing for another photo, nothing on a stopped job's dimmed photo.

const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');

/** A 3:4 phone photo as an upload leaves it once the receipt has arrived. */
function storedPhoto(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  return {
    ...base,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', mimeType: 'image/jpeg', uri: 'blob:photo', width: 3024, height: 4032, durationMs: 0 },
    spatialView: { ...base.spatialView, mode: 'source_2d' },
  } as ObservationSession;
}
const detection = (xPx: number, yPx: number, flux: number): SourceDetection => ({
  assetId: 'src_0123456789abcdef',
  frameTimeSec: 0,
  xPx,
  yPx,
  flux,
  snr: 12,
  fwhmPx: 2.4,
  centroidUncertaintyPx: 0.2,
});
// Three points of light, the brightest second in the list; the last at the photo's left edge
const DETECTIONS = [detection(1500, 2000, 400), detection(300, 500, 9000), detection(0, 3000, 50)];
const JOB = 'job_0123456789abcdef';
const STAMP = '2026-11-27T12:00:03Z';

let container: HTMLDivElement;
let root: Root;
let served: { status: number; body: unknown };
let calls: string[];
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  calls = [];
  served = { status: 200, body: { sourceAssetSha256: storedPhoto().sourceAsset!.sha256, detections: DETECTIONS } };
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    if (url.endsWith('/measured')) return new Response(JSON.stringify(served.body), { status: served.status });
    return new Response(JSON.stringify({ variants: [], attempts: [] }), { status: 200 });
  });
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  delete (window as unknown as Record<string, unknown>).matchMedia;
  vi.restoreAllMocks();
});
const q = (s: string) => container.querySelector(s);
const settle = () => act(async () => await new Promise((r) => setTimeout(r, 0)));
const measuredCalls = () => calls.filter((c) => c.endsWith('/measured'));

describe('wait-measured-one: where each measured point of light stands', () => {
  it('places each at its stored pixel on the photo as shown, brightest first', () => {
    const session = storedPhoto();
    const marks = measuredMarksOf(session, DETECTIONS, 1440, 900);
    const at = (d: SourceDetection) => sourcePixelToViewport(d.xPx, d.yPx, 3024, 4032, 1440, 900, session.cropTransform, session.sourceAsset!.displayOrientation);
    expect(marks.map((m) => [m.rank, m.xPx, m.yPx])).toEqual([
      [0, at(DETECTIONS[1]!).xPx, at(DETECTIONS[1]!).yPx],
      [1, at(DETECTIONS[0]!).xPx, at(DETECTIONS[0]!).yPx],
      [2, at(DETECTIONS[2]!).xPx, at(DETECTIONS[2]!).yPx],
    ]);
  });

  it('turns a photo stored on its side upright before placing its points', () => {
    const base = storedPhoto();
    const turned = { ...base, sourceAsset: { ...base.sourceAsset!, width: 4032, height: 3024, displayOrientation: { quarterTurnsClockwise: 1, mirrored: false } } } as ObservationSession;
    const [mark] = measuredMarksOf(turned, [detection(100, 200, 10)], 1440, 900);
    const upright = sourcePixelToViewport(100, 200, 4032, 3024, 1440, 900, turned.cropTransform, turned.sourceAsset!.displayOrientation);
    const unturned = sourcePixelToViewport(100, 200, 4032, 3024, 1440, 900, turned.cropTransform);
    expect([mark!.xPx, mark!.yPx]).toEqual([upright.xPx, upright.yPx]);
    expect(Math.hypot(upright.xPx - unturned.xPx, upright.yPx - unturned.yPx)).toBeGreaterThan(10);
  });

  it('draws none outside the photo as the window shows it (the phone\'s slot crops a wide photo\'s sides)', () => {
    const base = storedPhoto();
    const wide = { ...base, sourceAsset: { ...base.sourceAsset!, width: 6000, height: 4000 } } as ObservationSession;
    const slotted = { ...wide, cropTransform: { ...wide.cropTransform, ...phoneSlotCrop(6000, 4000, 390, 844, 'image') } } as ObservationSession;
    const marks = measuredMarksOf(slotted, [detection(3000, 2000, 10), detection(10, 2000, 20)], 390, 844);
    expect(marks).toHaveLength(1);
    expect(marks[0]!.xPx).toBeCloseTo(195, 0);
  });
});

describe('wait-measured-one: the marks from the job\'s route', () => {
  const show = (extra: Record<string, unknown> = {}) =>
    act(() => root.render(createElement(MeasuredMarks, { session: storedPhoto(), jobId: JOB, measuredAtUTC: STAMP, viewportWidthPx: 1440, viewportHeightPx: 900, ...extra })));

  it('asks nothing before the stamp, then once, and draws each as the Observed glyph in arrival order', async () => {
    show({ measuredAtUTC: undefined });
    await settle();
    expect(measuredCalls()).toEqual([]);
    expect(q('[data-testid="measured-marks"]')).toBeNull();
    show();
    await settle();
    show();
    await settle();
    expect(measuredCalls()).toEqual([`/api/ingest/jobs/${JOB}/measured`]);
    const layer = q('[data-testid="measured-marks"]') as SVGSVGElement;
    expect(layer.getAttribute('data-evidence-class')).toBe('observed');
    expect(layer.getAttribute('aria-hidden')).toBe('true');
    expect(layer.style.getPropertyValue('--mark-n')).toBe('3');
    const marks = [...layer.querySelectorAll('.measured-mark')] as SVGGElement[];
    expect(marks.map((m) => m.style.getPropertyValue('--mark-i'))).toEqual(['0', '1', '2']);
    // LOOK_GLYPHS.observed: a ring of 5.5 at stroke 1.3 and a filled centre of 1.4
    const circles = [...marks[0]!.querySelectorAll('circle')];
    expect(circles.map((c) => [c.getAttribute('r'), c.getAttribute('fill'), c.getAttribute('stroke-width')])).toEqual([
      ['5.5', 'none', '1.3'],
      ['1.4', 'currentColor', null],
    ]);
    expect(marks[0]!.getAttribute('transform')).toContain('scale(1)');
  });

  it('draws the glyph as the reveal scales its ring on the phone', async () => {
    show({ phone: true });
    await settle();
    expect(q('.measured-mark')!.getAttribute('transform')).toContain('scale(0.75)');
  });

  it.each([
    ['the route does not answer', () => (served = { status: 404, body: { detail: 'not found' } })],
    ['the answer names another photo', () => (served = { status: 200, body: { sourceAssetSha256: 'b'.repeat(64), detections: DETECTIONS } })],
    ['the answer is not the contract\'s', () => (served = { status: 200, body: { detections: DETECTIONS } })],
  ])('draws nothing when %s', async (_label, setUp) => {
    setUp();
    show();
    await settle();
    expect(q('[data-testid="measured-marks"]')).toBeNull();
  });

  it('asks the server nothing for a job id that is not one', async () => {
    show({ jobId: '../../etc' });
    await settle();
    expect(measuredCalls()).toEqual([]);
  });

  it('asks again for a new job, and drops the old one\'s marks', async () => {
    show();
    await settle();
    show({ jobId: 'job_fedcba9876543210' });
    expect(q('[data-testid="measured-marks"]')).toBeNull();
    await settle();
    expect(measuredCalls()).toEqual([`/api/ingest/jobs/${JOB}/measured`, '/api/ingest/jobs/job_fedcba9876543210/measured']);
    expect(q('[data-testid="measured-marks"]')).not.toBeNull();
  });

  it('holds the rules: under the working rows, taking no press, in the Observed colour, arriving as marks arrive', () => {
    const rule = (selector: string) => {
      const found: Record<string, string> = {};
      postcss.parse(css).walkRules((r) => {
        if (r.selector === selector && r.parent?.type === 'root') r.walkDecls((d) => void (found[d.prop] = d.value.replace(/\s+/g, ' ')));
      });
      return found;
    };
    expect(rule('.measured-marks')).toEqual({
      '--mark-step': 'max(0ms, calc((var(--look-mo-span-marks) - var(--look-mo-dur-mark)) / max(1, var(--mark-n) - 1)))',
      position: 'fixed',
      inset: '0',
      'z-index': '30',
      width: '100%',
      height: '100%',
      overflow: 'visible',
      'pointer-events': 'none',
      color: 'var(--look-ev-observed)',
    });
    expect(rule('.measured-mark')).toEqual({ animation: 'look-mo-frame-in var(--look-mo-dur-mark) linear calc(var(--mark-i) * var(--mark-step)) both' });
  });
});

describe('wait-measured-one: on the working screen', () => {
  const noop = () => {};
  const arrival = (over: Partial<Arrival> = {}): Arrival => ({
    fileName: 'night.jpg',
    byteSize: 2_382_523,
    stored: true,
    widthPx: 3024,
    heightPx: 4032,
    job: { id: JOB, state: 'running', stages: [{ stage: 'detect', unit: 'px', done: 1, total: 1 }], measuredAtUTC: STAMP },
    failure: null,
    ...over,
  });
  const render = (extra: Record<string, unknown>, url: string | null = 'blob:photo') =>
    act(() =>
      root.render(
        createElement(SpatialObservatoryViewport, {
          session: storedPhoto(),
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
          ...extra,
        })
      )
    );
  const past = (extra: Record<string, unknown>) => {
    render(extra, null);
    choosePhotoOnHome(container);
    render(extra);
  };

  it('stands the marks over the waiting photo, under the working rows, once Measured has ended', async () => {
    past({ arrival: arrival() });
    await settle();
    const layer = q('[data-testid="measured-marks"]')!;
    const working = q('[data-testid="working-screen"]')!;
    expect(layer.compareDocumentPosition(working) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(layer.querySelectorAll('.measured-mark')).toHaveLength(3);
  });

  it('draws none before the stamp or on a stopped job\'s dimmed photo', async () => {
    past({ arrival: arrival({ job: { id: JOB, state: 'running', stages: [] } }) });
    await settle();
    expect(q('[data-testid="measured-marks"]')).toBeNull();
    render({ arrival: arrival({ failure: { sentence: 'The solve stopped on an internal error.', canTryAgain: true } }) });
    await settle();
    expect(q('[data-testid="measured-marks"]')).toBeNull();
  });
});
