// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SampleRecordSchema, type ObservationSession, type ObservationSolution, type SampleAsset } from '@albedian/contracts';
import { HOME_SCREEN } from '../components/__tests__/homeEntry.js';
import { isSampleShown, openSample, readySample, sampleAddressed } from '../services/openSample.js';
import { HomeScreen } from '../components/HomeScreen.js';
import { OWN_PHOTO, photoNames, SAMPLE_PHOTO } from '../reveal/sampleWords.js';
import { SampleCredit, sampleCreditLine } from '../reveal/SampleCredit.js';

vi.mock('../spatial/SparkPortalRenderer.js', () => ({
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
// Real sky's layer drawn at once, so its sentence shows (as revealPhoto.test.tsx mocks it)
vi.mock('../reveal/RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../reveal/RealSkyLayer.js')>();
  const { useEffect } = await import('react');
  return {
    ...original,
    // jsdom has no WebGL: Real sky shows wherever the session asks for it on a calibrated solve
    isRealSkyShown: (session: ObservationSession) => session.spatialView?.mode === 'registered_sky' && session.observationSolution?.status === 'calibrated',
    RealSkyLayer: (props: { onDrawn?: (drawn: boolean) => void }) => {
      useEffect(() => props.onDrawn?.(true), []);
      return null;
    },
  };
});

const { App } = await import('../App.js');
const { RevealPhoto } = await import('../reveal/RevealPhoto.js');
const { KeyboardHelp } = await import('../reveal/KeyboardHelp.js');
const { SpatialObservatoryViewport } = await import('../components/SpatialObservatoryViewport.js');
const { choosePhotoOnHome } = await import('../components/__tests__/homeEntry.js');
const { phoneSlotCrop } = await import('../reveal/phoneSlot.js');
const { isRealSkyShown } = await import('../reveal/RealSkyLayer.js');
const { dispatchAlbedianAction } = await import('@albedian/observation');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// sample-reveal-one (UIUX-sample-reveal-READY.md, RULED at the lead's line stamped 03:48 on 27 Nov; PLATFORM's routes note):
// the sample night's reveal says what it is from its first frame: the photo view's pill reads Sample photo (keyboard
// help's line with it), "your photo" in what a person reads names it "the sample photo", the photo carries Commons'
// courtesy credit from the sample's own record (photo view only), its address is ?sample=<id> and reloading reopens
// it, and Restore is not offered. Built from GET /api/samples, /asset and /solution as an upload's is from its receipt.

const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../styles/reveal.css'), 'utf8');
const record = SampleRecordSchema.parse(JSON.parse(fs.readFileSync(path.resolve(here, '../../../../data/samples.json'), 'utf8')));
const night = record.samples[0]!;
const ID = night.id;
const sha256 = night.photo.sha256;
const assetId = `src_${sha256.slice(0, 16)}`;
const solutionId = `sol_${sha256.slice(0, 16)}`;
const asset: SampleAsset = {
  id: assetId,
  title: 'rp-b-12.jpg',
  mediaKind: 'image',
  sha256,
  uri: `/api/samples/${ID}/original`,
  byteSize: night.photo.bytes,
  mimeType: night.photo.mimeType,
  width: 4000,
  height: 2252,
  durationMs: 0,
  videoCodec: 'mjpeg',
  audioStreams: [],
  privacyClass: 'public_sample',
  immutable: true,
} as SampleAsset;
const solutions = JSON.parse(fs.readFileSync(path.resolve(here, '../../../../tests/fixtures/python_contract_solutions.json'), 'utf8')) as Record<string, ObservationSolution>;
const base = solutions['plate_calibrated']!;
const solution: ObservationSolution = {
  ...base,
  id: solutionId,
  sourceAssetId: assetId,
  sourceSha256: sha256,
  findings: base.findings.map((f) => ({
    ...f,
    observationSolutionId: solutionId,
    sourceDetection: f.sourceDetection ? { ...f.sourceDetection, assetId } : undefined,
    evidence: { ...f.evidence, sourceAssetSha256: sha256, wcsSolutionId: solutionId },
  })),
};
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
type Routes = { record?: () => Response; asset?: () => Response; solution?: () => Response };
function serve(routes: Routes = {}) {
  const calls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/media/discover') return json({ items: [] });
      calls.push(`${init?.method ?? 'GET'} ${url}`);
      if (url === '/api/samples') return (routes.record ?? (() => json(record)))();
      if (url === `/api/samples/${ID}/asset`) return (routes.asset ?? (() => json(asset)))();
      if (url === `/api/samples/${ID}/solution`) return (routes.solution ?? (() => json(solution)))();
      return json({ detail: 'not found' }, 404);
    })
  );
  return calls;
}

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  Object.defineProperty(URL, 'createObjectURL', { value: () => 'blob:chosen', configurable: true, writable: true });
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.history.replaceState({}, '', '/');
  delete (URL as unknown as Record<string, unknown>).createObjectURL;
});
const q = (selector: string) => container.querySelector<HTMLElement>(selector);
const button = (label: string) => [...container.querySelectorAll('button')].find((b) => b.textContent === label);
async function openAppAt(address: string, routes: Routes = {}) {
  window.history.replaceState({}, '', address);
  const calls = serve(routes);
  await act(async () => {
    root.render(createElement(App));
  });
  return calls;
}
async function waitUntil(check: () => void) {
  for (let waited = 0; ; waited += 10) {
    await act(async () => await new Promise((r) => setTimeout(r, 10)));
    try {
      return check();
    } catch (error) {
      if (waited >= 2000) throw error;
    }
  }
}
const settle = () => act(async () => await new Promise((r) => setTimeout(r, 30)));

describe('the sample night\'s address (item 4)', () => {
  it('names a sample by its id only, once', () => {
    expect(sampleAddressed(`?sample=${ID}`)).toBe(ID);
    expect(sampleAddressed('?sample=..%2Fetc')).toBeNull();
    expect(sampleAddressed('?sample=Night-RP')).toBeNull();
    expect(sampleAddressed(`?sample=${ID}&sample=${ID}`)).toBeNull();
    expect(sampleAddressed(`?upload=${sha256}`)).toBeNull();
  });

  it('opens the sample from its record, its asset and its kept solve, and nothing else', async () => {
    const calls = serve();
    const opened = await openSample(ID);
    expect(opened!.night).toEqual(night);
    expect(opened!.session.sourceAsset!.privacyClass).toBe('public_sample');
    expect(opened!.session.sourceAsset!.uri).toBe(`/api/samples/${ID}/original`);
    expect(opened!.session.observationSolution!.sourceSha256).toBe(sha256);
    expect(isSampleShown(opened!.session, night)).toBe(true);
    expect(isSampleShown(opened!.session, null)).toBe(false);
    // Anyone's own upload of the same photo is theirs, not the sample
    const own = { ...opened!.session, sourceAsset: { ...opened!.session.sourceAsset!, privacyClass: 'private' } } as ObservationSession;
    expect(isSampleShown(own, night)).toBe(false);
    expect(calls).toEqual(['GET /api/samples', `GET /api/samples/${ID}/asset`, `GET /api/samples/${ID}/solution`]);
  });

  it.each([
    ['the asset does not answer', { asset: () => json({ detail: 'not found' }, 404) }],
    ['the solution does not answer', { solution: () => json({ detail: 'not found' }, 404) }],
    ['the solution names another photo', { solution: () => json({ ...solution, sourceSha256: 'b'.repeat(64) }) }],
    ['the solution names another asset', { solution: () => json({ ...solution, sourceAssetId: 'src_bbbbbbbbbbbbbbbb' }) }],
    ['the asset is not the record\'s photo', { asset: () => json({ ...asset, byteSize: night.photo.bytes + 1 }) }],
    ['the record does not list it', { record: () => json({ ...record, samples: [{ ...night, id: 'night-other' }] }) }],
  ] as const)('opens nothing when %s', async (_label, routes) => {
    serve(routes as Routes);
    expect(await openSample(ID)).toBeNull();
  });

  it('opens the night the address names when the record lists another first', async () => {
    serve({ record: () => json({ ...record, samples: [{ ...night, id: 'night-other' }, night] }) });
    expect((await openSample(ID))!.night.id).toBe(ID);
  });

  it('asks the server nothing for a value that is no sample id', async () => {
    const calls = serve();
    expect(await openSample('../etc')).toBeNull();
    expect(calls).toEqual([]);
  });
});

describe('the app opened at ?sample=<id>', () => {
  it('shows the sample\'s reveal: Sample photo, its credit, the sample photo\'s words, and no Restore', async () => {
    const calls = await openAppAt(`/?sample=${ID}`);
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    expect(q(HOME_SCREEN)).toBeNull();
    const photo = q('[data-testid="source-photo-stage"] img') as HTMLImageElement;
    expect(photo.getAttribute('src')).toBe(`/api/samples/${ID}/original`);
    expect(photo.getAttribute('alt')).toBe('The sample photo');
    // Item 1: the photo view's pill
    expect([...container.querySelectorAll('.reveal-nav .look-views__item')].map((b) => b.textContent)).toEqual(['Sample photo', 'Real sky', '3D']);
    // Item 3: the credit, a link to the source page in a new tab, 24 px left of the Credits link (no width in jsdom)
    const credit = q('[data-testid="sample-credit"]') as HTMLAnchorElement;
    expect(credit.textContent).toBe(`${night.credit.author} / Wikimedia Commons / ${night.credit.licence}`);
    expect(credit.getAttribute('href')).toBe(night.credit.sourceUrl);
    expect(credit.getAttribute('target')).toBe('_blank');
    expect(credit.getAttribute('rel')).toBe('noopener noreferrer');
    expect(credit.className).toBe('reveal-sample-credit');
    expect(credit.style.right).toBe(`${32 + 0 + 24}px`);
    // Item 5: Restore is not offered, and nothing is said of it
    expect(button('Restore')).toBeUndefined();
    // Item 1: keyboard help names the views as the pill does
    act(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: '?' })));
    expect(container.textContent).toContain('Sample photo, Real sky, 3D');
    // The address's three reads (beside the home's readiness read of the same three), and nothing sent
    for (const read of ['GET /api/samples', `GET /api/samples/${ID}/asset`, `GET /api/samples/${ID}/solution`]) expect(calls).toContain(read);
    expect(calls.some((call) => call.startsWith('POST') || call.includes('/api/ingest'))).toBe(false);
  });

  it('lands on the home with no reveal when the sample\'s asset or solution does not answer', async () => {
    await openAppAt(`/?sample=${ID}`, { asset: () => json({ detail: 'not found' }, 404) });
    await settle();
    await waitUntil(() => expect(q(HOME_SCREEN)).not.toBeNull());
    expect(q('[data-testid="reveal-marks"]')).toBeNull();
  });

  it('never takes a sample with ?upload=: an upload named beside it is the one opened', async () => {
    const calls = await openAppAt(`/?upload=${sha256}&sample=${ID}`);
    await waitUntil(() => expect(q(HOME_SCREEN)).not.toBeNull());
    // The upload was asked for (and is not stored here); the sample's reveal never opened
    expect(calls).toContain(`GET /api/ingest/${sha256}`);
    expect(q('[data-testid="sample-credit"]')).toBeNull();
    expect(q('[data-testid="reveal-marks"]')).toBeNull();
  });
});

describe('the sample photo\'s names and credit (items 1 to 3)', () => {
  it('names the photo by what it is', () => {
    expect(photoNames(true)).toBe(SAMPLE_PHOTO);
    expect(photoNames(false)).toBe(OWN_PHOTO);
    expect(SAMPLE_PHOTO).toEqual({ view: 'Sample photo', the: 'the sample photo', The: 'The sample photo', alt: 'The sample photo' });
    expect(OWN_PHOTO).toEqual({ view: 'Your photo', the: 'your photo', The: 'Your photo', alt: 'Your photo as uploaded' });
  });

  it('writes the credit in Commons\' courtesy form from the record, and none for a site it cannot name', () => {
    expect(sampleCreditLine(night)).toBe(`${night.credit.author} / Wikimedia Commons / ${night.credit.licence}`);
    expect(sampleCreditLine({ ...night, credit: { ...night.credit, sourceUrl: 'https://example.org/photo' } })).toBeNull();
    act(() => root.render(createElement(SampleCredit, { night, topPx: 638 })));
    const phone = q('[data-testid="sample-credit"]')!;
    expect(phone.className).toBe('reveal-sample-credit is-phone');
    expect(phone.style.top).toBe('638px');
  });

  it('lists the views in keyboard help as the pill names them', () => {
    act(() => root.render(createElement(KeyboardHelp, { threeD: false, photoView: 'Sample photo' })));
    act(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: '?' })));
    expect(container.textContent).toContain('Sample photo, Real sky, 3D');
    expect(container.textContent).not.toContain('Your photo, Real sky, 3D');
  });

  it('places Real sky by the sample photo\'s solve', async () => {
    const opened = await (serve(), openSample(ID));
    act(() =>
      root.render(
        createElement(RevealPhoto, { session: opened!.session as ObservationSession, onSelectFinding: () => {}, onHome: () => {}, realSky: true, sample: night, restoration: { phase: 'idle' } } as unknown as Parameters<typeof RevealPhoto>[0])
      )
    );
    const words = container.textContent ?? '';
    expect(words).toContain("placed by the sample photo's solve");
    expect(words).not.toContain("your photo's solve");
  });

  it('holds the credit\'s rules: Geist 12 at ink 66 percent on the Credits line, the Credits page\'s link states, the phone\'s line', () => {
    const rule = (selector: string) => {
      const found: Record<string, string> = {};
      postcss.parse(css).walkRules((r) => {
        if (r.selector === selector && r.parent?.type === 'root') r.walkDecls((d) => void (found[d.prop] = d.value));
      });
      return found;
    };
    expect(rule('.reveal-sample-credit')).toEqual({
      position: 'fixed',
      bottom: '32px',
      height: 'var(--look-h-home)',
      'z-index': '30',
      display: 'inline-flex',
      'align-items': 'center',
      font: '400 12px / 1.3 var(--look-font-sans)',
      'white-space': 'nowrap',
      color: 'var(--look-ink-2)',
      'text-decoration': 'none',
      'pointer-events': 'auto',
    });
    expect(rule('.reveal-sample-credit:hover')).toEqual({ color: 'var(--look-ink)' });
    expect(rule('.reveal-sample-credit:focus-visible')).toEqual({ outline: 'var(--look-focus-width) solid var(--look-ink)', 'outline-offset': 'var(--look-focus-offset)' });
    expect(rule('.reveal-sample-credit.is-phone')).toEqual({ right: '24px', bottom: 'auto', height: 'auto', 'line-height': '16px' });
  });
});

describe('the credit stands with the photo view only, laptop and phone (item 3)', () => {
  const setWindow = (w: number, h: number) => {
    Object.defineProperty(window, 'innerWidth', { value: w, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: h, configurable: true });
    (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({
      matches: w <= 720 ? query.includes('max-width: 720px') : h <= 500 && query.includes('max-height: 500px'),
      addEventListener: () => {},
      removeEventListener: () => {},
    });
  };
  const noop = () => {};
  function openViewport(session: ObservationSession, shown: typeof night | null) {
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
      sample: shown,
    });
    act(() => root.render(createElement(SpatialObservatoryViewport, props(null))));
    choosePhotoOnHome(container);
    act(() => root.render(createElement(SpatialObservatoryViewport, props(`/api/samples/${ID}/original`))));
  }
  const slotted = (session: ObservationSession, w: number, h: number) =>
    ({ ...session, cropTransform: { ...session.cropTransform, ...phoneSlotCrop(4000, 2252, w, h, 'image') } }) as ObservationSession;
  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).matchMedia;
  });

  it('stands right-aligned at the photo\'s foot on the phone held upright, and not held sideways', async () => {
    serve();
    const opened = (await openSample(ID))!;
    setWindow(390, 844);
    openViewport(slotted(opened.session, 390, 844), night);
    const credit = q('[data-testid="sample-credit"]')!;
    expect(credit.className).toBe('reveal-sample-credit is-phone');
    // The phone's photo slot runs 118 to 638 at 390 by 844
    expect(credit.style.top).toBe('638px');
    expect([...container.querySelectorAll('.reveal-phone-top .look-views__item')].map((b) => b.textContent)).toEqual(['Sample photo', 'Real sky', '3D']);
    act(() => root.unmount());
    root = createRoot(container);
    setWindow(874, 292);
    openViewport(slotted(opened.session, 874, 292), night);
    expect(q('[data-testid="sample-credit"]')).toBeNull();
    expect([...container.querySelectorAll('.reveal-phone-top .look-views__item')].map((b) => b.textContent)[0]).toBe('Sample photo');
  });

  it('is not drawn for anyone\'s own photo, which keeps Your photo and Restore', async () => {
    serve();
    const opened = (await openSample(ID))!;
    setWindow(1512, 900);
    openViewport(opened.session, null);
    expect(q('[data-testid="sample-credit"]')).toBeNull();
    expect([...container.querySelectorAll('.reveal-nav .look-views__item')].map((b) => b.textContent)[0]).toBe('Your photo');
    expect(button('Restore')).not.toBeUndefined();
  });
});

describe('the sample\'s reveal on Real sky (items 1 and 3)', () => {
  it('keeps the Sample photo label and leaves the credit with the photo view', async () => {
    serve();
    const opened = (await openSample(ID))!;
    const sky = dispatchAlbedianAction(opened.session, {
      type: 'sky.show',
      requestId: 'test-real-sky',
      expectedViewRevision: opened.session.viewRevision,
      mode: 'measured',
      observationSolutionId: opened.session.observationSolution!.id,
    }).session;
    expect(isRealSkyShown(sky)).toBe(true);
    Object.defineProperty(window, 'innerWidth', { value: 1512, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: 900, configurable: true });
    const props = (session: ObservationSession, url: string | null) => ({
      session,
      setSession: () => {},
      selectedFinding: null,
      discoveredMedia: [],
      activeDiscoveredId: null,
      setActiveDiscoveredId: () => {},
      activeDiscoveredItem: null,
      uploadedMediaUrl: url,
      setUploadedMediaUrl: () => {},
      commandText: '',
      setCommandText: () => {},
      onRunUtterance: () => {},
      onSelectFinding: () => {},
      onPushToTalk: () => {},
      sample: night,
    });
    act(() => root.render(createElement(SpatialObservatoryViewport, props(opened.session, null))));
    choosePhotoOnHome(container);
    act(() => root.render(createElement(SpatialObservatoryViewport, props(opened.session, `/api/samples/${ID}/original`))));
    expect(q('[data-testid="sample-credit"]')).not.toBeNull();
    act(() => root.render(createElement(SpatialObservatoryViewport, props(sky, `/api/samples/${ID}/original`))));
    expect(q('[data-testid="sample-credit"]')).toBeNull();
    expect([...container.querySelectorAll('.reveal-nav .look-views__item')].map((b) => b.textContent)[0]).toBe('Sample photo');
  });
});

describe('the flight names the sample photo (item 2, in SPATIAL\'s files by the lead\'s ruling of 05:51)', () => {
  it('writes the cone view\'s lines and its pill with the photo\'s names, today\'s words by default', async () => {
    const { coneLines, CONE_LINES } = await import('../spatial/flight/coneView.js');
    const { FlightPills, ConeReadout } = await import('../spatial/flight/ConePieces.js');
    expect(coneLines(SAMPLE_PHOTO).view).toBe("Sample photo's view: the part of the sky the sample photo holds, from its solve, out to its farthest matched star with a measured distance.");
    expect(coneLines(SAMPLE_PHOTO).rings).toBe('Rings: the stars the sample photo matched.');
    expect(CONE_LINES.view).toBe("Your photo's view: the part of the sky your photo holds, from its solve, out to its farthest matched star with a measured distance.");
    expect(CONE_LINES.rings).toBe('Rings: the stars your photo matched.');
    act(() => root.render(createElement(FlightPills, { craftPill: null, cone: false, onCone: () => {}, photoView: 'Sample photo' })));
    expect(q('[data-testid="cone-pill"]')!.textContent).toBe("Sample photo's view");
    act(() => root.render(createElement(FlightPills, { craftPill: null, cone: false, onCone: () => {} })));
    expect(q('[data-testid="cone-pill"]')!.textContent).toBe("Your photo's view");
    const geometry = { insideCount: 3, farthestPc: null, nearestPc: null } as unknown as Parameters<typeof ConeReadout>[0]['geometry'];
    for (const phone of [false, true]) {
      act(() => root.render(createElement(ConeReadout, { geometry, phone, photoView: 'Sample photo' })));
      expect(q('[data-testid="cone-readout"]')!.getAttribute('aria-label')).toBe("Sample photo's view");
      expect(container.textContent).toContain("Sample photo's view");
      expect(container.textContent).not.toContain("Your photo's view");
      // Anyone's own photo keeps today's words: the readout's name by default
      act(() => root.render(createElement(ConeReadout, { geometry, phone })));
      expect(q('[data-testid="cone-readout"]')!.getAttribute('aria-label')).toBe("Your photo's view");
    }
  });
});

// sample-pill-one (UIUX-first-use-wow-READY.md item 2 and P1 to P5, RULED 12:25 on 27 Nov): Try a sample night beside
// Upload, shown only once the sample's asset and its kept solve both answer; its press opens the sample's finished reveal
// and the address names it
describe('sample-pill-one: Try a sample night on the home', () => {
  const homeCss = fs.readFileSync(path.resolve(here, '../styles/home.css'), 'utf8');
  const rulesIn = (params: string | null, selector: string) => {
    const found: Record<string, string> = {};
    postcss.parse(homeCss).walkRules((r) => {
      const parent = r.parent as postcss.AtRule | postcss.Root;
      const inParams = parent.type === 'atrule' ? (parent as postcss.AtRule).params : null;
      if (r.selector === selector && inParams === params) r.walkDecls((d) => void (found[d.prop] = d.value.replace(/\s+/g, ' ')));
    });
    return found;
  };
  const pill = () => [...container.querySelectorAll('button')].find((b) => b.textContent === 'Try a sample night');

  it('stands after Upload on its row at the home size, only once offered, and takes the press', () => {
    const onTrySample = vi.fn();
    act(() => root.render(createElement(HomeScreen, { soundOn: false, onToggleSound: () => {}, onFileChosen: () => {}, onTrySample })));
    const row = q('.home-hero .home-row')!;
    expect([...row.children].map((c) => c.className)).toEqual([expect.stringContaining('look-main'), 'look-second look-size-home home-sample', 'home-refusal']);
    act(() => pill()!.click());
    expect(onTrySample).toHaveBeenCalledTimes(1);
    act(() => root.render(createElement(HomeScreen, { soundOn: false, onToggleSound: () => {}, onFileChosen: () => {} })));
    expect(pill()).toBeUndefined();
  });

  it('words in as it mounts when the sample answers after the home is drawn (P2)', () => {
    act(() => root.render(createElement(HomeScreen, { soundOn: false, onToggleSound: () => {}, onFileChosen: () => {} })));
    act(() => root.render(createElement(HomeScreen, { soundOn: false, onToggleSound: () => {}, onFileChosen: () => {}, onTrySample: () => {} })));
    expect(pill()!.className).toBe('look-second look-size-home home-sample is-arriving');
    expect(rulesIn(null, '.home-sample.is-arriving')).toEqual({ animation: 'look-mo-words-in var(--look-mo-dur-words-in) var(--look-mo-ease-std) both' });
  });

  it('takes no press while a file is held over the window (P5)', () => {
    const onTrySample = vi.fn();
    act(() => root.render(createElement(HomeScreen, { soundOn: false, onToggleSound: () => {}, onFileChosen: () => {}, onTrySample })));
    const enter = new Event('dragenter') as DragEvent;
    Object.defineProperty(enter, 'dataTransfer', { value: { types: ['Files'], items: [{ kind: 'file' }] } });
    act(() => void window.dispatchEvent(enter));
    expect(q('[data-testid="home-drop"]')).not.toBeNull();
    act(() => pill()!.click());
    expect(onTrySample).not.toHaveBeenCalled();
  });

  it('holds its place: 10 px after Upload, the message under the row in the narrow band, beside or under Upload on the phone', () => {
    expect(rulesIn(null, '.home-row')).toMatchObject({ 'align-items': 'center', gap: '10px' });
    expect(rulesIn('(min-width: 721px) and (max-width: 838px)', '.home-row:has(.home-sample)')).toEqual({ display: 'grid', 'grid-template-columns': 'auto 1fr', 'column-gap': '10px', 'row-gap': '18px', 'justify-items': 'start' });
    expect(rulesIn('(min-width: 721px) and (max-width: 838px)', '.home-row:has(.home-sample) .home-refusal:not(:empty)')).toEqual({
      position: 'static',
      'grid-column': '1 / -1',
      transform: 'none',
      width: 'auto',
      'max-width': 'calc(100vw - 48px - 22px - var(--home-credit-w, 0px) - 32px)',
    });
    expect(rulesIn('(max-width: 720px)', '.home-row:has(.home-sample)')).toEqual({ 'grid-template-columns': 'auto 1fr', 'column-gap': '10px' });
    expect(rulesIn('(max-width: 720px)', '.home-hero .look-second')).toEqual({ '--look-h': 'var(--look-h-phone)', '--look-fs': 'var(--look-fs-phone)' });
    expect(rulesIn('(max-width: 346px)', '.home-row:has(.home-sample)')).toEqual({ 'grid-template-columns': 'auto', 'row-gap': '10px' });
    expect(rulesIn('(max-width: 346px)', '.home-row:has(.home-sample) .home-refusal:not(:empty)')).toEqual({ 'margin-top': '8px' });
  });

  // sample-pill-refusal-one (UIUX built-four-1127.md, P1 and P5): a refusal under the row spans both its columns; were
  // both sized by their content (auto auto) a message wider than Upload, 10 px and the pill would grow each to share its
  // width and slide the pill right (29.6 px after Upload on the phone, 21.9 at 800 wide, measured in the page). Upload's
  // column is its own width and the pill's is the one flexible track, so a wider message's room lands after the pill
  it('keeps the pill 10 px after Upload while a refused upload\'s message stands under the row (P5)', () => {
    for (const band of ['(min-width: 721px) and (max-width: 838px)', '(max-width: 720px)']) {
      expect(rulesIn(band, '.home-row:has(.home-sample) .home-refusal:not(:empty)')['grid-column']).toBe('1 / -1');
      const tracks = (rulesIn(band, '.home-row:has(.home-sample)')['grid-template-columns'] ?? '').split(' ');
      expect(tracks).toHaveLength(2);
      expect(tracks[0]).toBe('auto');
      expect(tracks[1]).toMatch(/^\d+(\.\d+)?fr$/);
    }
    expect(rulesIn('(max-width: 720px)', '.home-row')).toMatchObject({ display: 'grid', 'justify-items': 'start' });
    expect(rulesIn('(min-width: 721px) and (max-width: 838px)', '.home-row:has(.home-sample)')['justify-items']).toBe('start');
  });

  it('offers the record\'s first night only when its asset and its solve both answer', async () => {
    serve();
    expect((await readySample())!.night.id).toBe(ID);
    serve({ solution: () => json({ detail: 'not found' }, 404) });
    expect(await readySample()).toBeNull();
    serve({ record: () => json({ detail: 'down' }, 503) });
    expect(await readySample()).toBeNull();
  });

  it('opens the sample\'s finished reveal from the home and names it in the address', async () => {
    await openAppAt('/');
    await waitUntil(() => expect(pill()).not.toBeUndefined());
    await act(async () => pill()!.click());
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    expect(window.location.search).toBe(`?sample=${ID}`);
    expect([...container.querySelectorAll('.reveal-nav .look-views__item')].map((b) => b.textContent)[0]).toBe('Sample photo');
    expect(q('[data-testid="sample-credit"]')).not.toBeNull();
    expect(button('Restore')).toBeUndefined();
  });

  it('draws no pill when the sample does not answer', async () => {
    await openAppAt('/', { asset: () => json({ detail: 'not found' }, 404) });
    await settle();
    await settle();
    expect(q(HOME_SCREEN)).not.toBeNull();
    expect(pill()).toBeUndefined();
  });
});
