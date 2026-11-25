// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { orientedSize, type CelestialFinding, type ObservationSession, type ObservationSolution, type UploadJob } from '@albedian/contracts';
import { POSE, poseSession, sample } from '../../components/__tests__/poseSessions.js';
import type { AlignPose } from '../alignPose.js';
import { phoneSlotCrop } from '../phoneSlot.js';
import { UNCHANGED } from '../restoration.js';
import { NO_COPY_LINE } from '../RestoreParts.js';
import { NO_PLAYBACK_NOTE } from '../../services/apiClient.js';
import { RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

// Every render's compare picture and Download press, the passing render between two uploads included (the DOM after
// act holds only the last render, so these are read from the props each render hands down)
const seen = vi.hoisted(() => ({ compareSrcs: [] as string[], downloads: [] as Array<() => void> }));
vi.mock('../RestoreParts.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RestoreParts.js')>();
  const { createElement: make } = await import('react');
  const RestoreCompare = (props: Parameters<typeof original.RestoreCompare>[0]) => {
    seen.compareSrcs.push(props.src);
    return make(original.RestoreCompare, props);
  };
  return { ...original, RestoreCompare };
});
vi.mock('../../look/index.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../look/index.js')>();
  const { createElement: make } = await import('react');
  const MainAction = (props: Parameters<typeof original.MainAction>[0]) => {
    if (props.label === 'Download' && props.onClick) seen.downloads.push(props.onClick as () => void);
    return make(original.MainAction, props);
  };
  return { ...original, MainAction };
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const fixtures = path.resolve(here, '../../../../../tests/fixtures');
const media = JSON.parse(fs.readFileSync(path.join(fixtures, 'media_contract.json'), 'utf8')) as {
  valid: { MediaVariant: Array<Record<string, unknown>> };
};
const variantTemplate = media.valid.MediaVariant.find((v) => v.kind === 'restoration')!;
const jobs = (JSON.parse(fs.readFileSync(path.join(fixtures, 'job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const doneJobTemplate = jobs.find((job) => job.kind === 'restoration' && job.state === 'succeeded')!;
const { finishedAtUTC: _f, attemptId: _a, variantId: _v, ...restJob } = structuredClone(doneJobTemplate);
const stillStage = (stage: string) => ({ stage, unit: 'frames', done: 1, total: 1 });
const RUNNING_JOB = { ...restJob, state: 'running', stages: [stillStage('decode'), stillStage('measure')] } as unknown as UploadJob;
const FAILED_JOB = { ...restJob, state: 'failed', stages: [stillStage('decode'), stillStage('measure')] } as unknown as UploadJob;
const REFUSED_JOB = {
  ...restJob,
  state: 'rejected',
  stages: [stillStage('decode'), stillStage('measure'), stillStage('restore'), stillStage('encode'), { stage: 'write', unit: 'bytes', done: 900, total: 900 }, stillStage('verify')],
} as unknown as UploadJob;

const W = 390;
const H = 844;
const SHA_A = 'a'.repeat(64);
const SHA_B = 'b'.repeat(64);

function makeVariant(sha: string, id: string, createdAtUTC: string, byteSize = 14_000_000) {
  return {
    ...variantTemplate,
    id,
    createdAtUTC,
    byteSize,
    sourceSha256: sha,
    sourceAssetId: `src_${sha.slice(0, 16)}`,
    uri: `/api/media/${sha}/variants/${id}/file`,
  };
}

function calibratedFor(sha: string, phone = true): ObservationSession {
  const base = poseSession(sample('plate_calibrated'));
  const asset = { ...base.sourceAsset!, sha256: sha, id: `src_${sha.slice(0, 16)}` };
  const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
  return {
    ...base,
    sourceAsset: asset,
    cropTransform: phone ? { ...base.cropTransform, ...phoneSlotCrop(shown.widthPx, shown.heightPx, W, H) } : base.cropTransform,
  } as ObservationSession;
}

function unmatchedFor(sha: string): ObservationSession {
  const base = poseSession(sample('horizon_predicted_only'));
  const predTemplate = base.observationSolution!.findings.find((f) => f.evidenceClass === 'predicted')!;
  const starRef = POSE.findings.find((f) => f.catalogRef?.objectType === 'star')!.catalogRef!;
  const fomalhaut: CelestialFinding = {
    ...predTemplate,
    id: 'finding-predicted-star-hip-113368',
    title: 'Fomalhaut',
    skyPosition: { ...predTemplate.skyPosition!, altDeg: 20, azDeg: 150 },
    catalogRef: { ...starRef, apparentMagnitude: 1.16 },
  };
  return {
    ...base,
    sourceAsset: {
      ...base.sourceAsset!,
      sha256: sha,
      id: `src_${sha.slice(0, 16)}`,
      width: 3024,
      height: 4032,
      displayOrientation: undefined,
      cameraMetadata: { focalLength35mmMm: 26 },
    },
    cropTransform: { ...base.cropTransform, ...phoneSlotCrop(3024, 4032, W, H) },
    observationSolution: {
      ...base.observationSolution!,
      findings: [fomalhaut],
    } as ObservationSolution,
  } as ObservationSession;
}

const KEPT_POSE: AlignPose = { azDeg: 150, altDeg: 30, rollDeg: 0, fovXDeg: 60, aspect: 3 / 4 };

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
  vi.restoreAllMocks();
});

const q = (s: string) => container.querySelector(s);
const flush = async () => {
  for (let i = 0; i < 5; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
};

describe('phone-compare-assetkey-1', () => {
  it('resets scrollTop and --phone-scroll when switching directly between two unmatched phone uploads (PhoneNoMatch and PhoneAlignRefused) or two scrollable matched uploads', async () => {
    const olderA = makeVariant(SHA_A, 'var_00000000000000a1', '2026-11-18T18:20:00Z');
    const newerA = makeVariant(SHA_A, 'var_00000000000000a2', '2026-11-19T18:20:00Z');
    const olderB = makeVariant(SHA_B, 'var_00000000000000b1', '2026-11-18T19:20:00Z');
    const newerB = makeVariant(SHA_B, 'var_00000000000000b2', '2026-11-19T19:20:00Z');
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes(`/api/media/${SHA_A}/variants`)) return new Response(JSON.stringify({ variants: [], attempts: [] }), { status: 200 });
      if (url.includes(`/api/media/${SHA_B}/variants`)) return new Response(JSON.stringify({ variants: [], attempts: [] }), { status: 200 });
      return new Response(JSON.stringify({ variants: [], attempts: [] }), { status: 200 });
    });

    const renderPhone = (session: ObservationSession, extra: Record<string, unknown> = {}) =>
      act(() =>
        root.render(
          createElement(RevealPhoto, {
            session,
            onSelectFinding: () => {},
            onHome: () => {},
            viewportWidthPx: W,
            viewportHeightPx: H,
            phone: true,
            onShowPredictedSky: () => {},
            onSolveFromHere: () => {},
            onRestore: () => {},
            restoration: { phase: 'idle' },
            ...extra,
          } as unknown as Parameters<typeof RevealPhoto>[0]),
        ),
      );

    // 1. PhoneNoMatch: upload A scrolled to 140 px -> switch directly to unmatched upload B -> resets to 0
    renderPhone(unmatchedFor(SHA_A));
    await flush();
    const scrollA = q('[data-testid="reveal-phone-scroll"]') as HTMLElement;
    scrollA.scrollTop = 140;
    act(() => {
      scrollA.dispatchEvent(new Event('scroll'));
    });
    expect(scrollA.scrollTop).toBe(140);
    expect(document.documentElement.style.getPropertyValue('--phone-scroll')).toBe('140px');

    renderPhone(unmatchedFor(SHA_B));
    await flush();
    const scrollB = q('[data-testid="reveal-phone-scroll"]') as HTMLElement;
    expect(scrollB.scrollTop).toBe(0);
    expect(document.documentElement.style.getPropertyValue('--phone-scroll')).toBe('');
    expect(document.documentElement.dataset.phoneScroll).toBe('');

    // 2. PhoneAlignRefused: upload A scrolled to 115 px -> switch directly to refused upload B -> resets to 0
    renderPhone(unmatchedFor(SHA_A), {
      alignedFrom: { sha256: SHA_A, pose: KEPT_POSE, from: { kind: 'brightest', name: 'Fomalhaut' } },
    });
    await flush();
    const refusedScrollA = q('[data-testid="reveal-phone-scroll"]') as HTMLElement;
    refusedScrollA.scrollTop = 115;
    act(() => {
      refusedScrollA.dispatchEvent(new Event('scroll'));
    });
    expect(document.documentElement.style.getPropertyValue('--phone-scroll')).toBe('115px');

    renderPhone(unmatchedFor(SHA_B), {
      alignedFrom: { sha256: SHA_B, pose: KEPT_POSE, from: { kind: 'brightest', name: 'Fomalhaut' } },
    });
    await flush();
    const refusedScrollB = q('[data-testid="reveal-phone-scroll"]') as HTMLElement;
    expect(refusedScrollB.scrollTop).toBe(0);
    expect(document.documentElement.style.getPropertyValue('--phone-scroll')).toBe('');
    expect(document.documentElement.dataset.phoneScroll).toBe('');

    // 3. Matched scrollable phone uploads (phoneVersionsShown): upload A scrolled to 92 px -> upload B resets to 0
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes(SHA_A)) return new Response(JSON.stringify({ variants: [olderA, newerA], attempts: [] }), { status: 200 });
      return new Response(JSON.stringify({ variants: [olderB, newerB], attempts: [] }), { status: 200 });
    });
    renderPhone(calibratedFor(SHA_A));
    await flush();
    const rootEl = q('[data-testid="reveal-photo"]') as HTMLElement;
    rootEl.scrollTop = 92;
    act(() => {
      rootEl.dispatchEvent(new Event('scroll'));
    });
    expect(document.documentElement.style.getPropertyValue('--phone-scroll')).toBe('92px');

    renderPhone(calibratedFor(SHA_B));
    await flush();
    expect((q('[data-testid="reveal-photo"]') as HTMLElement).scrollTop).toBe(0);
    expect(document.documentElement.style.getPropertyValue('--phone-scroll')).toBe('');
    expect(document.documentElement.dataset.phoneScroll).toBe('');
  });

  it('steps phoneCompare aside when an upload with stored versions reruns into working or stopped, including Line it up again after a refusal', async () => {
    const olderA = makeVariant(SHA_A, 'var_00000000000000a1', '2026-11-18T18:20:00Z');
    const newerA = makeVariant(SHA_A, 'var_00000000000000a2', '2026-11-19T18:20:00Z');
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants: [olderA, newerA], attempts: [] }), { status: 200 }));

    const session = calibratedFor(SHA_A);
    const renderPhone = (extra: Record<string, unknown> = {}) =>
      act(() =>
        root.render(
          createElement(RevealPhoto, {
            session,
            onSelectFinding: () => {},
            onHome: () => {},
            viewportWidthPx: W,
            viewportHeightPx: H,
            phone: true,
            onRestore: () => {},
            onCancelRestore: () => {},
            onShowPredictedSky: () => {},
            onSolveFromHere: () => {},
            ...extra,
          } as unknown as Parameters<typeof RevealPhoto>[0]),
        ),
      );

    // Idle with 2 stored versions: phoneCompare and RestoreVersions show
    renderPhone({ restoration: { phase: 'idle' } });
    await flush();
    expect(q('[data-testid="restore-compare"]')).not.toBeNull();
    expect(q('.restore-phone-versions')).not.toBeNull();
    expect([...container.querySelectorAll('button')].some((b) => b.textContent === 'Download')).toBe(true);

    // Rerunning into working: phoneCompare steps aside for RestorePhoneWait + Cancel alone
    renderPhone({ restoration: { phase: 'working', job: RUNNING_JOB } });
    expect(q('[data-testid="restore-compare"]')).toBeNull();
    expect(q('.restore-phone-versions')).toBeNull();
    expect([...container.querySelectorAll('button')].some((b) => b.textContent === 'Download')).toBe(false);
    expect(q('.restore-phone-wait')).not.toBeNull();
    expect([...container.querySelectorAll('button')].some((b) => b.textContent === 'Cancel')).toBe(true);

    // Rerunning into stopped (retryable failure): phoneCompare stays off for RestorePhoneStopped + Try again alone
    renderPhone({ restoration: { phase: 'stopped', job: FAILED_JOB, sentence: 'Stopped.', subline: null, canTryAgain: true } });
    expect(q('[data-testid="restore-compare"]')).toBeNull();
    expect(q('.restore-phone-versions')).toBeNull();
    expect([...container.querySelectorAll('button')].some((b) => b.textContent === 'Download')).toBe(false);
    expect(q('.restore-phone-stopped')).not.toBeNull();
    expect([...container.querySelectorAll('button')].some((b) => b.textContent === 'Try again')).toBe(true);

    // Stopped refusal over keptRefused on unmatched upload with stored versions: Line it up again enters aligning without phoneCompare snapping back on
    const unmatched = unmatchedFor(SHA_A);
    renderPhone({
      session: unmatched,
      alignedFrom: { sha256: SHA_A, pose: KEPT_POSE, from: { kind: 'brightest', name: 'Fomalhaut' } },
      restoration: { phase: 'stopped', job: REFUSED_JOB, sentence: 'Refused.', subline: UNCHANGED, canTryAgain: false },
    });
    await flush();
    expect(q('[data-testid="restore-compare"]')).toBeNull();
    const again = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Line it up again') as HTMLButtonElement;
    expect(again).toBeDefined();
    act(() => again.click());
    expect(q('[data-testid="restore-compare"]')).toBeNull();
    expect([...container.querySelectorAll('button')].some((b) => b.textContent === 'Solve from here')).toBe(true);
  });

  it('draws no compare on the phone for a lone version whose record says no copy met its gate, its row standing (UIUX 02:25)', async () => {
    const lone = { ...makeVariant(SHA_A, 'var_00000000000000a3', '2026-11-20T18:20:00Z'), attemptId: 'att_00000000000000a3' };
    const attempts = [{ id: 'att_00000000000000a3', measured: { notes: [NO_PLAYBACK_NOTE] } }];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants: [lone], attempts }), { status: 200 }));
    act(() =>
      root.render(
        createElement(RevealPhoto, {
          session: calibratedFor(SHA_A),
          onSelectFinding: () => {},
          onHome: () => {},
          viewportWidthPx: W,
          viewportHeightPx: H,
          phone: true,
          onRestore: () => {},
          onCancelRestore: () => {},
          onShowPredictedSky: () => {},
          onSolveFromHere: () => {},
          restoration: { phase: 'idle' },
        } as unknown as Parameters<typeof RevealPhoto>[0]),
      ),
    );
    await flush();
    expect(q('[data-testid="restore-compare"]')).toBeNull();
    expect(container.querySelector('.restore-versions__older')?.textContent).toBe(NO_COPY_LINE);
  });

  it('resets pickedVersion and avoids cross-asset variant URLs when switching directly between two uploads that both stay in compareOn', async () => {
    const olderA = makeVariant(SHA_A, 'var_00000000000000a1', '2026-11-18T18:20:00Z');
    const newerA = makeVariant(SHA_A, 'var_00000000000000a2', '2026-11-19T18:20:00Z');
    const olderB = makeVariant(SHA_B, 'var_00000000000000b1', '2026-11-18T19:20:00Z');
    const newerB = makeVariant(SHA_B, 'var_00000000000000b2', '2026-11-19T19:20:00Z');

    let resolveB: ((res: Response) => void) | null = null;
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input);
      if (url.includes(`/api/media/${SHA_A}/variants`)) {
        return Promise.resolve(new Response(JSON.stringify({ variants: [olderA, newerA], attempts: [] }), { status: 200 }));
      }
      return new Promise<Response>((resolve) => {
        resolveB = resolve;
      });
    });

    const doneA = {
      phase: 'done' as const,
      job: { ...doneJobTemplate, variantId: newerA.id } as UploadJob,
      variantUrl: `/api/media/${SHA_A}/variants/${newerA.id}/file`,
      orientation: { quarterTurnsClockwise: 0 as const, mirrored: false },
    };
    const doneB = {
      phase: 'done' as const,
      job: { ...doneJobTemplate, variantId: newerB.id } as UploadJob,
      variantUrl: `/api/media/${SHA_B}/variants/${newerB.id}/file`,
      orientation: { quarterTurnsClockwise: 0 as const, mirrored: false },
    };

    // 1. Phone: pick olderA on upload A, then switch directly to upload B (also phase: 'done')
    const render = (session: ObservationSession, restoration: typeof doneA, phone: boolean, extra: Record<string, unknown> = {}) =>
      act(() =>
        root.render(
          createElement(RevealPhoto, {
            session,
            restoration,
            onSelectFinding: () => {},
            onHome: () => {},
            viewportWidthPx: phone ? W : 1440,
            viewportHeightPx: phone ? H : 900,
            phone,
            ...extra,
          }),
        ),
      );

    render(calibratedFor(SHA_A, true), doneA, true);
    await flush();
    const phoneRowsA = [...container.querySelectorAll<HTMLButtonElement>('.restore-phone-versions .restore-versions__row')];
    act(() => phoneRowsA[1]!.click());
    expect(q('.restore-compare__pic img')!.getAttribute('src')).toBe(`/api/media/${SHA_A}/variants/${olderA.id}/file`);

    // Synchronous transition render before readRestorations(SHA_B) resolves: never requests SHA_B with olderA.id
    render(calibratedFor(SHA_B, true), doneB, true);
    expect(q('.restore-compare__pic img')!.getAttribute('src')).toBe(`/api/media/${SHA_B}/variants/${newerB.id}/file`);

    resolveB!(new Response(JSON.stringify({ variants: [olderB, newerB], attempts: [] }), { status: 200 }));
    await flush();
    const phoneRowsB = [...container.querySelectorAll<HTMLButtonElement>('.restore-phone-versions .restore-versions__row')];
    expect(phoneRowsB.map((r) => r.getAttribute('aria-pressed'))).toEqual(['true', 'false']);

    let exportedHref: string | null = null;
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      exportedHref = this.getAttribute('href');
    });
    const downloadBtn = () => [...container.querySelectorAll('button')].find((b) => b.textContent === 'Download') as HTMLButtonElement;
    act(() => downloadBtn().click());
    expect(exportedHref).toBe(`/api/media/${SHA_B}/variants/${newerB.id}/export`);

    // 2. Laptop: SHA_A (pick olderA) -> SHA_B (done) -> SHA_A (done), with compareOn staying true throughout
    act(() => root.unmount());
    root = createRoot(container);
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      const list = url.includes(SHA_A) ? [olderA, newerA] : [olderB, newerB];
      return new Response(JSON.stringify({ variants: list, attempts: [] }), { status: 200 });
    });

    render(calibratedFor(SHA_A, false), doneA, false);
    await flush();
    act(() => container.querySelectorAll<HTMLButtonElement>('.restore-versions__row')[1]!.click());
    expect([...container.querySelectorAll<HTMLButtonElement>('.restore-versions__row')].map((r) => r.getAttribute('aria-pressed'))).toEqual(['false', 'true']);

    render(calibratedFor(SHA_B, false), doneB, false);
    // Even synchronously before readRestorations(SHA_B) settles, Download exports SHA_B's own doneVariant, never olderA.id
    act(() => downloadBtn().click());
    expect(exportedHref).toBe(`/api/media/${SHA_B}/variants/${newerB.id}/export`);
    await flush();
    expect([...container.querySelectorAll<HTMLButtonElement>('.restore-versions__row')].map((r) => r.getAttribute('aria-pressed'))).toEqual(['true', 'false']);
    exportedHref = null;
    act(() => downloadBtn().click());
    expect(exportedHref).toBe(`/api/media/${SHA_B}/variants/${newerB.id}/export`);

    // Returning to SHA_A while compareOn stayed true also starts on newerA, not stale olderA
    render(calibratedFor(SHA_A, false), doneA, false);
    await flush();
    expect([...container.querySelectorAll<HTMLButtonElement>('.restore-versions__row')].map((r) => r.getAttribute('aria-pressed'))).toEqual(['true', 'false']);

    // Toggling Original pixels on SHA_A leaves compareOn briefly and resets pickedVersion to newerA
    act(() => container.querySelectorAll<HTMLButtonElement>('.restore-versions__row')[1]!.click());
    expect([...container.querySelectorAll<HTMLButtonElement>('.restore-versions__row')].map((r) => r.getAttribute('aria-pressed'))).toEqual(['false', 'true']);
    render(calibratedFor(SHA_A, false), doneA, false, { originalPixels: true });
    render(calibratedFor(SHA_A, false), doneA, false, { originalPixels: false });
    await flush();
    expect([...container.querySelectorAll<HTMLButtonElement>('.restore-versions__row')].map((r) => r.getAttribute('aria-pressed'))).toEqual(['true', 'false']);
  });

  it('draws a photo whose asset carries no sha256 yet, with nothing stored, at both widths (the viewport suites render such sessions)', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise<Response>(() => {}));
    for (const phone of [false, true]) {
      const session = calibratedFor(SHA_A, phone);
      const { sha256: _sha, ...noSha } = session.sourceAsset!;
      const bare = { ...session, sourceAsset: noSha } as unknown as ObservationSession;
      expect(() =>
        act(() =>
          root.render(
            createElement(RevealPhoto, {
              session: bare,
              onSelectFinding: () => {},
              onHome: () => {},
              viewportWidthPx: phone ? W : 1440,
              viewportHeightPx: phone ? H : 900,
              phone,
            } as unknown as Parameters<typeof RevealPhoto>[0]),
          ),
        ),
      ).not.toThrow();
      expect(q('[data-testid="reveal-photo"]')).not.toBeNull();
      expect(q('[data-testid="restore-compare"]')).toBeNull();
    }
  });

  it("names only the shown upload's own variants in every render after a direct switch, the passing render before its effects included", async () => {
    const olderA = makeVariant(SHA_A, 'var_00000000000000a1', '2026-11-18T18:20:00Z');
    const newerA = makeVariant(SHA_A, 'var_00000000000000a2', '2026-11-19T18:20:00Z');
    // Upload B's stored restorations are never read in this test: every render of B runs before they could arrive
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) =>
      String(input).includes(`/api/media/${SHA_A}/variants`)
        ? Promise.resolve(new Response(JSON.stringify({ variants: [olderA, newerA], attempts: [] }), { status: 200 }))
        : new Promise<Response>(() => {}),
    );
    const doneB = {
      phase: 'done' as const,
      job: { ...doneJobTemplate, variantId: 'var_00000000000000b2' } as UploadJob,
      variantUrl: `/api/media/${SHA_B}/variants/var_00000000000000b2/file`,
      orientation: { quarterTurnsClockwise: 0 as const, mirrored: false },
    };
    const doneA = {
      phase: 'done' as const,
      job: { ...doneJobTemplate, variantId: newerA.id } as UploadJob,
      variantUrl: `/api/media/${SHA_A}/variants/${newerA.id}/file`,
      orientation: { quarterTurnsClockwise: 0 as const, mirrored: false },
    };
    const render = (session: ObservationSession, restoration: unknown, phone: boolean) =>
      act(() =>
        root.render(
          createElement(RevealPhoto, {
            session,
            restoration,
            onSelectFinding: () => {},
            onHome: () => {},
            onRestore: () => {},
            viewportWidthPx: phone ? W : 1440,
            viewportHeightPx: phone ? H : 900,
            phone,
          } as unknown as Parameters<typeof RevealPhoto>[0]),
        ),
      );
    const fromA = (url: string) => url.includes(SHA_A) || url.includes('var_00000000000000a');

    // Phone: A idle shows its stored versions in the compare; B idle has none read yet, so no compare is drawn for B
    render(calibratedFor(SHA_A, true), { phase: 'idle' }, true);
    await flush();
    expect(q('[data-testid="restore-compare"]')).not.toBeNull();
    seen.compareSrcs.length = 0;
    render(calibratedFor(SHA_B, true), { phase: 'idle' }, true);
    expect(seen.compareSrcs.filter(fromA)).toEqual([]);
    expect(q('[data-testid="restore-compare"]')).toBeNull();

    // Phone, both done: B gets a compare of its own (the wipe and zoom start over), not A's carried across
    render(calibratedFor(SHA_A, true), doneA, true);
    await flush();
    const phoneCompareA = q('[data-testid="restore-compare"]');
    expect(phoneCompareA).not.toBeNull();
    render(calibratedFor(SHA_B, true), doneB, true);
    expect(q('[data-testid="restore-compare"]')).not.toBeNull();
    expect(q('[data-testid="restore-compare"]')).not.toBe(phoneCompareA);

    // Laptop: A done with its older row pressed, then straight to B done; every Download handed down for B saves B's own
    act(() => root.unmount());
    root = createRoot(container);
    render(calibratedFor(SHA_A, false), doneA, false);
    await flush();
    act(() => container.querySelectorAll<HTMLButtonElement>('.restore-versions__row')[1]!.click());
    expect(q('.restore-compare__pic img')!.getAttribute('src')).toBe(`/api/media/${SHA_A}/variants/${olderA.id}/file`);
    const laptopCompareA = q('[data-testid="restore-compare"]');
    seen.compareSrcs.length = 0;
    seen.downloads.length = 0;
    render(calibratedFor(SHA_B, false), doneB, false);
    expect(q('[data-testid="restore-compare"]')).not.toBeNull();
    expect(q('[data-testid="restore-compare"]')).not.toBe(laptopCompareA);
    expect(seen.compareSrcs.length).toBeGreaterThan(0);
    expect(seen.compareSrcs.filter(fromA)).toEqual([]);
    expect(seen.downloads.length).toBeGreaterThan(0);
    const saved: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      saved.push(this.getAttribute('href') ?? '');
    });
    for (const press of seen.downloads) press();
    expect(new Set(saved)).toEqual(new Set([`/api/media/${SHA_B}/variants/var_00000000000000b2/export`]));
  });
});
