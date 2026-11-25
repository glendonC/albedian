// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { MediaVariantSchema, type MediaVariant, type ObservationSession } from '@albedian/contracts';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { choosePhotoOnHome } from './homeEntry.js';

// voice-restore-one: the viewport keeps in the session exactly the restored version RevealPhoto says it shows. RevealPhoto
// stands in here and keeps the props the viewport hands it; the rest of the viewport is the real one.

const seen = vi.hoisted(() => ({ props: undefined as undefined | { onShownRestoration?: (variant: MediaVariant | null) => void } }));
vi.mock('../../reveal/RevealPhoto.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../reveal/RevealPhoto.js')>();
  return {
    ...original,
    RevealPhoto: (props: { onShownRestoration?: (variant: MediaVariant | null) => void }) => {
      seen.props = props;
      return null;
    },
  };
});
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

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const media = JSON.parse(fs.readFileSync(path.resolve(here, '../../../../../tests/fixtures/media_contract.json'), 'utf8')) as { valid: { MediaVariant: Array<Record<string, unknown>> } };
const template = media.valid.MediaVariant.find((v) => v.kind === 'restoration')!;

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
});

describe('the viewport keeps the restored version on screen in the session', () => {
  it('takes what RevealPhoto reports, and keeps it when RevealPhoto reports none (voice-restore-known-one)', () => {
    const base = createSyntheticCalibratedSession();
    let current = {
      ...base,
      sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', mimeType: 'image/jpeg', uri: 'blob:photo', width: 3024, height: 4032, durationMs: 0 },
      mediaVariants: [],
      spatialView: { ...base.spatialView, mode: 'source_2d' },
    } as ObservationSession;
    const setSession = (update: ObservationSession | ((prev: ObservationSession) => ObservationSession)) => {
      current = typeof update === 'function' ? update(current) : update;
    };
    const noop = () => {};
    const render = (uploadedMediaUrl: string | null) =>
      act(() =>
        root.render(
          createElement(SpatialObservatoryViewport, {
            session: current,
            setSession,
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
          } as unknown as Parameters<typeof SpatialObservatoryViewport>[0]),
        ),
      );
    render(null);
    choosePhotoOnHome(container);
    render('blob:photo');
    expect(seen.props?.onShownRestoration).toBeTypeOf('function');
    const sha = current.sourceAsset!.sha256;
    const variant = MediaVariantSchema.parse({ ...template, sourceSha256: sha, sourceAssetId: `src_${sha.slice(0, 16)}`, uri: `/api/media/${sha}/variants/${String(template.id)}/file` });
    // Nothing shown while nothing is held is no update at all
    const before = current;
    act(() => seen.props!.onShownRestoration!(null));
    expect(current).toBe(before);
    act(() => seen.props!.onShownRestoration!(variant));
    expect(current.mediaVariants.map((v) => v.id)).toEqual([variant.id]);
    // The app draws again with the new session, as React does
    render('blob:photo');
    const held = current;
    act(() => seen.props!.onShownRestoration!(null));
    expect(current).toBe(held);
    expect(current.mediaVariants.map((v) => v.id)).toEqual([variant.id]);
  });
});

// voice-restore-two (DECIDED-BY-LEAD 18:21 on 24 Nov): the viewport hands App the Restore button's own action, which
// acts only where the button could: the recording on screen, no restoration running, and says whether it did
describe("the viewport hands App the Restore button's own action", () => {
  function mount(over: Partial<ObservationSession>, kind: 'image' | 'video' = 'image') {
    const base = createSyntheticCalibratedSession();
    let current = {
      ...base,
      sourceAsset: { ...base.sourceAsset!, mediaKind: kind, mimeType: kind === 'image' ? 'image/jpeg' : 'video/mp4', uri: 'blob:photo', width: 3024, height: 4032 },
      mediaVariants: [],
      spatialView: { ...base.spatialView, mode: 'source_2d' },
      ...over,
    } as ObservationSession;
    const restoreNowRef: { current: (() => boolean) | null } = { current: null };
    const noop = () => {};
    const render = (uploadedMediaUrl: string | null) =>
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
            restoreNowRef,
          } as unknown as Parameters<typeof SpatialObservatoryViewport>[0]),
        ),
      );
    render(null);
    choosePhotoOnHome(container);
    render('blob:photo');
    return { restoreNowRef, session: () => current };
  }
  // The restoration route answers with a job that never ends here: the point is which press reaches it
  const posts = () => fetchSpy.mock.calls.filter(([url, init]) => String(url).endsWith('/restore') && (init as RequestInit | undefined)?.method === 'POST');
  let fetchSpy: MockInstance<typeof fetch>;
  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise<Response>(() => {}));
  });
  afterEach(() => fetchSpy.mockRestore());

  it('on the photo with nothing restoring: the press reaches the restoration route, once, and says it acted', () => {
    const { restoreNowRef, session } = mount({});
    expect(restoreNowRef.current).toBeTypeOf('function');
    let acted = false;
    act(() => {
      acted = restoreNowRef.current!();
    });
    expect(acted).toBe(true);
    expect(posts()).toHaveLength(1);
    expect(String(posts()[0]![0])).toBe(`/api/media/${session().sourceAsset!.sha256}/restore`);
    // Asked already: the button stands in its wait now, so a second ask does nothing
    let again = true;
    act(() => {
      again = restoreNowRef.current!();
    });
    expect(again).toBe(false);
    expect(posts()).toHaveLength(1);
  });

  it('on a clip, the same', () => {
    const { restoreNowRef } = mount({}, 'video');
    let acted = false;
    act(() => {
      acted = restoreNowRef.current!();
    });
    expect(acted).toBe(true);
    expect(posts()).toHaveLength(1);
  });

  // voice-restore-three (AUDIT-C's T8 and T9 on voice-restore-two, LEAD ANSWER to WEB-UX 19:42 on 24 Nov): tests only
  const jobs = (JSON.parse(fs.readFileSync(path.resolve(here, '../../../../../tests/fixtures/job_contract.json'), 'utf8')) as { valid: Array<Record<string, unknown>> }).valid;
  const restorationJob = (state: string) => structuredClone(jobs.find((job) => job.kind === 'restoration' && job.state === state)!);
  /** The restore route answers with this job's record; anything else the viewport asks is not found. */
  const answerRestoreWith = (state: string) =>
    fetchSpy.mockImplementation(async (url, init) =>
      String(url).endsWith('/restore') && (init as RequestInit | undefined)?.method === 'POST'
        ? new Response(JSON.stringify(restorationJob(state)), { status: 200, headers: { 'content-type': 'application/json' } })
        : new Response(JSON.stringify({ detail: 'not found' }), { status: 404, headers: { 'content-type': 'application/json' } })
    );
  const settle = async () => {
    for (let i = 0; i < 8; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
  };

  it('T8: a stop that can be tried again is tried again; a refusal that cannot (the same recording refused the same way) is not run again', async () => {
    // A stop the button offers Try again for: the server stopped before the job finished
    answerRestoreWith('interrupted');
    const retryable = mount({});
    act(() => void retryable.restoreNowRef.current!());
    await settle();
    let again = false;
    act(() => {
      again = retryable.restoreNowRef.current!();
    });
    expect(again).toBe(true);
    expect(posts()).toHaveLength(2);
    act(() => root.render(createElement('div')));
    fetchSpy.mockClear();
    // A refusal: deterministic, so no Try again, and voice does not run it again either
    answerRestoreWith('rejected');
    const refusedOnce = mount({});
    act(() => void refusedOnce.restoreNowRef.current!());
    await settle();
    expect(posts()).toHaveLength(1);
    let pressedAgain = true;
    act(() => {
      pressedAgain = refusedOnce.restoreNowRef.current!();
    });
    expect(pressedAgain).toBe(false);
    expect(posts()).toHaveLength(1);
  });

  it('T9: gone from the screen, the viewport takes its press back out of App\'s ref', () => {
    const { restoreNowRef } = mount({});
    expect(restoreNowRef.current).toBeTypeOf('function');
    act(() => root.render(createElement('div')));
    expect(restoreNowRef.current).toBeNull();
  });

  it('away from the recording (Real sky), where the button is not, it does nothing', () => {
    const base = createSyntheticCalibratedSession();
    const { restoreNowRef } = mount({ spatialView: { ...base.spatialView, mode: 'registered_sky' } } as Partial<ObservationSession>);
    let acted = true;
    act(() => {
      acted = restoreNowRef.current?.() ?? false;
    });
    expect(acted).toBe(false);
    expect(posts()).toHaveLength(0);
  });
});
