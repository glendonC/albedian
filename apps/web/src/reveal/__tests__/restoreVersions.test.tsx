// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MediaVariantSchema, type UploadJob } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { NO_PLAYBACK_NOTE, madeAtWords, readRestorations } from '../../services/apiClient.js';
import { NO_COPY_LINE, RestoreVersions } from '../RestoreParts.js';
import { RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

// D36 (UIUX-restorations-READY.md, decided by the lead 13:45): a photo with two or more ready restorations lists them
// under Download, newest first: the time each was made, its size, a check on the one shown, and under each older one
// "Made by an earlier version of Albedian."; a row pressed shows it in the compare, and Download saves the one shown.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const fixtures = path.resolve(here, '../../../../../tests/fixtures');
const media = JSON.parse(fs.readFileSync(path.join(fixtures, 'media_contract.json'), 'utf8')) as { valid: { MediaVariant: Array<Record<string, unknown>> } };
const template = media.valid.MediaVariant.find((v) => v.kind === 'restoration')!;
const jobs = (JSON.parse(fs.readFileSync(path.join(fixtures, 'job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const succeeded = structuredClone(jobs.find((job) => job.kind === 'restoration' && job.state === 'succeeded')!);
const session = poseSession(sample('plate_calibrated'));
const sha = session.sourceAsset!.sha256;
const variant = (id: string, createdAtUTC: string, byteSize: number) => ({
  ...template,
  id,
  createdAtUTC,
  byteSize,
  sourceSha256: sha,
  sourceAssetId: `src_${sha.slice(0, 16)}`,
  uri: `/api/media/${sha}/variants/${id}/file`,
});
const OLDER = variant('var_0000000000000001', '2026-11-18T18:20:00Z', 14_000_000);
const NEWER = variant(succeeded.variantId!, '2026-11-19T18:20:00Z', 14_600_000);

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
const answer = (variants: unknown[]) => vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants, attempts: [] }), { status: 200 }));
async function showDone(extra: Record<string, unknown> = {}) {
  act(() =>
    root.render(
      createElement(RevealPhoto, {
        session,
        onSelectFinding: () => {},
        onHome: () => {},
        viewportWidthPx: 1440,
        viewportHeightPx: 900,
        restoration: { phase: 'done', job: succeeded, variantUrl: `/api/media/${sha}/variants/${succeeded.variantId}/file`, orientation: { quarterTurnsClockwise: 0, mirrored: false } },
        ...extra,
      })
    )
  );
  for (let i = 0; i < 5; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
}

describe('readRestorations', () => {
  it('lists the stored restorations newest first, and nothing else the route lists', async () => {
    expect(MediaVariantSchema.safeParse(OLDER).success).toBe(true);
    // A valid display copy of this upload, newest of all, is not a restoration
    const copyTemplate = media.valid.MediaVariant.find((v) => v.kind === 'display_copy')!;
    const copy = { ...copyTemplate, id: 'var_00000000000000c0', createdAtUTC: '2026-11-20T00:00:00Z', sourceSha256: sha, sourceAssetId: `src_${sha.slice(0, 16)}`, uri: `/api/media/${sha}/variants/var_00000000000000c0/file` };
    expect(MediaVariantSchema.safeParse(copy).success).toBe(true);
    answer([OLDER, NEWER, copy, { nonsense: true }]);
    const list = await readRestorations(sha);
    expect(list.map((v) => v.id)).toEqual([NEWER.id, OLDER.id]);
    expect(list[0]!.byteSize).toBe(14_600_000);
  });
});

describe('madeAtWords', () => {
  it("writes the time in the viewer's local time as '19 Nov, 13:20'", () => {
    const at = new Date(2026, 10, 19, 13, 20, 59);
    expect(madeAtWords(at.toISOString())).toBe('19 Nov, 13:20');
    expect(madeAtWords(new Date(2026, 0, 3, 7, 5).toISOString())).toBe('3 Jan, 07:05');
  });
});

describe('the list under Download (D36)', () => {
  it('lists two ready restorations newest first, the shown one checked, the older one said to be by an earlier version', async () => {
    answer([OLDER, NEWER]);
    await showDone();
    const rows = [...container.querySelectorAll<HTMLButtonElement>('[data-testid="restore-versions"] button')];
    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.querySelector('.restore-versions__size')!.textContent)).toEqual(['14.6 MB', '14.0 MB']);
    expect(rows.map((r) => r.getAttribute('aria-pressed'))).toEqual(['true', 'false']);
    expect(rows[0]!.querySelector('.restore-versions__mark svg')).not.toBeNull();
    expect(rows[1]!.querySelector('.restore-versions__mark svg')).toBeNull();
    expect(rows.map((r) => r.querySelector('.restore-versions__older')?.textContent ?? null)).toEqual([null, 'Made by an earlier version of Albedian.']);
    // The list sits under Download, with no heading
    const download = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Download')!;
    expect(download.compareDocumentPosition(q('[data-testid="restore-versions"]')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('shows a pressed row in the compare, moves the check, and Download saves the one shown', async () => {
    answer([OLDER, NEWER]);
    await showDone();
    const rows = () => [...container.querySelectorAll<HTMLButtonElement>('[data-testid="restore-versions"] button')];
    act(() => rows()[1]!.click());
    expect(rows().map((r) => r.getAttribute('aria-pressed'))).toEqual(['false', 'true']);
    expect((q('[data-testid="restore-compare"] .restore-compare__pic img') as HTMLImageElement).getAttribute('src')).toBe(`/api/media/${sha}/variants/${OLDER.id}/file`);
    const saved: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      saved.push(this.getAttribute('href')!);
    });
    act(() => ([...container.querySelectorAll('button')].find((b) => b.textContent === 'Download') as HTMLButtonElement).click());
    expect(saved).toEqual([`/api/media/${sha}/variants/${OLDER.id}/export`]);
  });

  it('shows the newest again after the compare is left and come back to (AUDIT-C, 17:14)', async () => {
    answer([OLDER, NEWER]);
    await showDone();
    const pressed = () => [...container.querySelectorAll<HTMLButtonElement>('[data-testid="restore-versions"] button')].map((r) => r.getAttribute('aria-pressed'));
    act(() => [...container.querySelectorAll<HTMLButtonElement>('[data-testid="restore-versions"] button')][1]!.click());
    expect(pressed()).toEqual(['false', 'true']);
    // Original pixels leaves the compare; coming back shows the newest
    await showDone({ originalPixels: true });
    expect(q('[data-testid="restore-compare"]')).toBeNull();
    await showDone({ originalPixels: false });
    expect(pressed()).toEqual(['true', 'false']);
  });

  it('shows no list with one ready restoration, including on RestoreVersions directly', async () => {
    answer([NEWER]);
    await showDone();
    expect(q('[data-testid="restore-versions"]')).toBeNull();
    act(() =>
      root.render(
        createElement(RestoreVersions, {
          versions: [{ id: NEWER.id, createdAtUTC: NEWER.createdAtUTC as string, byteSize: NEWER.byteSize as number, orientation: { quarterTurnsClockwise: 0, mirrored: false } }],
          shownId: NEWER.id,
          onShow: () => {},
        })
      )
    );
    expect(q('[data-testid="restore-versions"]')).toBeNull();
  });

  it('keeps the row of a version no copy that plays here matched: no check, not pressable, its line under the earlier one (UIUX 02:25)', async () => {
    const turned = { quarterTurnsClockwise: 0, mirrored: false } as const;
    const row = (id: string, createdAtUTC: string, noCopy: boolean) => ({ id, createdAtUTC, byteSize: 14_000_000, orientation: turned, noCopy });
    const shown: string[] = [];
    const draw = (versions: ReturnType<typeof row>[], shownId: string) =>
      act(() => root.render(createElement(RestoreVersions, { versions, shownId, onShow: (id: string) => shown.push(id) })));
    draw([row('var_00000000000000b2', '2026-11-25T08:00:00Z', false), row('var_00000000000000b1', '2026-11-24T08:00:00Z', true)], 'var_00000000000000b1');
    const rows = Array.from(container.querySelectorAll('.restore-versions__row')) as HTMLButtonElement[];
    expect(rows.map((r) => [r.getAttribute('aria-pressed'), r.getAttribute('aria-disabled'), r.querySelector('.restore-versions__mark svg') !== null])).toEqual([
      ['false', null, false],
      ['false', 'true', false],
    ]);
    expect(Array.from(rows[1]!.querySelectorAll('.restore-versions__older')).map((s) => s.textContent)).toEqual([
      'Made by an earlier version of Albedian.',
      'No copy that plays here matched it closely enough, so it is not shown; Download saves it.',
    ]);
    act(() => rows[1]!.click());
    act(() => rows[0]!.click());
    expect(shown).toEqual(['var_00000000000000b2']);
    // One ready restoration with no copy: the list stands with its row, so it says why nothing is shown
    draw([row('var_00000000000000b3', '2026-11-25T09:00:00Z', true)], 'var_00000000000000b3');
    expect(container.querySelectorAll('.restore-versions__row')).toHaveLength(1);
    expect(container.querySelector('.restore-versions__older')!.textContent).toBe(NO_COPY_LINE);
  });

  it('never draws a version whose record says no copy met its gate: the older one is shown, and alone none is (the 22:19 ruling)', async () => {
    // Its own attempt, whose notes say no copy met its gate (the samples otherwise share one)
    const NOTED = { ...NEWER, attemptId: 'att_00000000000000a1' };
    const noted = { id: NOTED.attemptId, measured: { notes: [NO_PLAYBACK_NOTE] } };
    const listing = (variants: unknown[]) =>
      vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants, attempts: [noted] }), { status: 200 }));
    listing([NOTED, OLDER]);
    await showDone();
    expect((q('[data-testid="restore-compare"] .restore-compare__pic img') as HTMLImageElement).getAttribute('src')).toBe(`/api/media/${sha}/variants/${OLDER.id}/file`);
    const rows = Array.from(container.querySelectorAll('.restore-versions__row'));
    expect(rows.map((r) => r.getAttribute('aria-disabled'))).toEqual(['true', null]);
    expect(rows.map((r) => r.getAttribute('aria-pressed'))).toEqual(['false', 'true']);
    act(() => root.unmount());
    vi.restoreAllMocks();
    root = createRoot(container);
    listing([NOTED]);
    await showDone();
    expect(q('[data-testid="restore-compare"]')).toBeNull();
    expect(container.querySelectorAll('.restore-versions__row')).toHaveLength(1);
    expect(container.querySelector('.restore-versions__older')!.textContent).toBe(NO_COPY_LINE);
  });

  it("plays a pressed version from its playback copy when the copy's route serves it (clip-display-copy)", async () => {
    const clipTemplate = media.valid.MediaVariant.find((v) => v.kind === 'restoration' && v.playbackCopy !== undefined)!;
    const copyUri = `/api/media/${sha}/variants/${OLDER.id}/playback`;
    const older = { ...clipTemplate, id: OLDER.id, createdAtUTC: OLDER.createdAtUTC, sourceSha256: sha, sourceAssetId: `src_${sha.slice(0, 16)}`, uri: OLDER.uri, playbackCopy: { ...(clipTemplate.playbackCopy as Record<string, unknown>), uri: copyUri } };
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) =>
      String(input) === copyUri
        ? new Response('x', { status: 206, headers: { 'content-type': 'video/mp4' } })
        : new Response(JSON.stringify({ variants: [older, NEWER], attempts: [] }), { status: 200 })
    );
    await showDone();
    const rows = () => [...container.querySelectorAll<HTMLButtonElement>('[data-testid="restore-versions"] button')];
    act(() => rows()[1]!.click());
    expect((q('[data-testid="restore-compare"] .restore-compare__pic img') as HTMLImageElement).getAttribute('src')).toBe(copyUri);
  });

  it('never draws a clip version while its copy is made: the older one is drawn and checked and Download saves it, then the copy is drawn (playback-firstneed)', async () => {
    const bareClip = media.valid.MediaVariant.find((v) => v.kind === 'restoration' && v.mimeType === 'video/mp4' && v.playbackCopy === undefined)!;
    const withCopy = media.valid.MediaVariant.find((v) => v.kind === 'restoration' && v.playbackCopy !== undefined)!;
    const newest = { ...bareClip, id: NEWER.id, createdAtUTC: NEWER.createdAtUTC, byteSize: NEWER.byteSize, sourceSha256: sha, sourceAssetId: `src_${sha.slice(0, 16)}`, uri: NEWER.uri };
    const copyUri = `/api/media/${sha}/variants/${NEWER.id}/playback`;
    let release: (answer: Response) => void = () => {};
    const started = new Promise<Response>((resolve) => (release = resolve));
    const posts: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (init?.method === 'POST' && url === copyUri) {
        posts.push(url);
        return started;
      }
      if (url === copyUri) return new Response('x', { status: 206, headers: { 'content-type': 'video/mp4' } });
      if (url === `/api/media/${sha}/variants/${NEWER.id}`) {
        return new Response(JSON.stringify({ variant: { ...newest, playbackCopy: { ...(withCopy.playbackCopy as Record<string, unknown>), uri: copyUri } } }), { status: 200 });
      }
      return new Response(JSON.stringify({ variants: [newest, OLDER], attempts: [] }), { status: 200 });
    });
    await showDone();
    const img = () => (q('[data-testid="restore-compare"] .restore-compare__pic img') as HTMLImageElement).getAttribute('src');
    const rows = () => [...container.querySelectorAll<HTMLButtonElement>('[data-testid="restore-versions"] button')];
    expect(posts).toEqual([copyUri]);
    expect(img()).toBe(`/api/media/${sha}/variants/${OLDER.id}/file`);
    expect(rows().map((r) => r.getAttribute('aria-busy'))).toEqual(['true', null]);
    expect(rows().map((r) => r.querySelector('.restore-versions__mark svg') !== null)).toEqual([false, true]);
    expect(rows().map((r) => r.getAttribute('aria-pressed'))).toEqual(['false', 'true']);
    const saved: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      saved.push(this.getAttribute('href')!);
    });
    act(() => ([...container.querySelectorAll('button')].find((b) => b.textContent === 'Download') as HTMLButtonElement).click());
    expect(saved).toEqual([`/api/media/${sha}/variants/${OLDER.id}/export`]);
    // The copy in place: the newest is drawn from it and takes the check
    release(new Response(JSON.stringify({ detail: 'that variant has a playback copy' }), { status: 409 }));
    for (let i = 0; i < 5; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
    expect(img()).toBe(copyUri);
    expect(rows().map((r) => r.getAttribute('aria-busy'))).toEqual([null, null]);
    expect(rows().map((r) => r.querySelector('.restore-versions__mark svg') !== null)).toEqual([true, false]);
  });

  it('keeps the version drawn while a pressed row waits for its copy, not the newest that can be drawn (versionwait 22:20 item 2)', async () => {
    const bareClip = media.valid.MediaVariant.find((v) => v.kind === 'restoration' && v.mimeType === 'video/mp4' && v.playbackCopy === undefined)!;
    const newest = { ...bareClip, id: NEWER.id, createdAtUTC: '2026-11-20T18:20:00Z', byteSize: NEWER.byteSize, sourceSha256: sha, sourceAssetId: `src_${sha.slice(0, 16)}`, uri: NEWER.uri };
    const middle = variant('var_0000000000000002', '2026-11-19T00:00:00Z', 14_300_000);
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) =>
      init?.method === 'POST' ? new Promise<Response>(() => {}) : new Response(JSON.stringify({ variants: [newest, middle, OLDER], attempts: [] }), { status: 200 })
    );
    await showDone();
    const img = () => (q('[data-testid="restore-compare"] .restore-compare__pic img') as HTMLImageElement).getAttribute('src');
    const rows = () => [...container.querySelectorAll<HTMLButtonElement>('[data-testid="restore-versions"] button')];
    expect(img()).toBe(`/api/media/${sha}/variants/${middle.id}/file`);
    act(() => rows()[2]!.click());
    expect(img()).toBe(`/api/media/${sha}/variants/${OLDER.id}/file`);
    // The newest pressed while its copy is made: the oldest stays drawn and checked, the newest pressed and pending
    act(() => rows()[0]!.click());
    expect(img()).toBe(`/api/media/${sha}/variants/${OLDER.id}/file`);
    expect(rows().map((r) => r.getAttribute('aria-pressed'))).toEqual(['true', 'false', 'false']);
    expect(rows().map((r) => r.getAttribute('aria-busy'))).toEqual(['true', null, null]);
    expect(rows().map((r) => r.querySelector('.restore-versions__mark svg') !== null)).toEqual([false, false, true]);
  });

  it('supports 3+ pipeline revisions (1.6 -> 1.7 -> 1.8), keeps the wipe split steady across version switches, and exports each selected variant', async () => {
    const V16 = variant('var_0000000000000016', '2026-11-18T18:20:00Z', 13_800_000);
    const V17 = {
      ...variant('var_0000000000000017', '2026-11-19T18:20:00Z', 14_200_000),
      displayOrientation: { quarterTurnsClockwise: 1, mirrored: false },
    };
    const V18 = variant(succeeded.variantId!, '2026-11-20T18:20:00Z', 14_600_000);
    answer([V16, V17, V18]);
    await showDone();

    const compare = () => q('[data-testid="restore-compare"]') as HTMLElement;
    for (let waited = 0; compare().style.getPropertyValue('--split') !== '50%'; waited += 10) {
      if (waited >= 2000) throw new Error(`split stood at ${compare().style.getPropertyValue('--split')}`);
      await act(async () => await new Promise((r) => setTimeout(r, 10)));
    }

    const rows = () => [...container.querySelectorAll<HTMLButtonElement>('[data-testid="restore-versions"] button')];
    expect(rows()).toHaveLength(3);
    expect(rows().map((r) => r.querySelector('.restore-versions__size')!.textContent)).toEqual(['14.6 MB', '14.2 MB', '13.8 MB']);
    expect(rows().map((r) => r.getAttribute('aria-pressed'))).toEqual(['true', 'false', 'false']);
    expect(rows().map((r) => r.querySelector('.restore-versions__older')?.textContent ?? null)).toEqual([
      null,
      'Made by an earlier version of Albedian.',
      'Made by an earlier version of Albedian.',
    ]);

    // Move the wipe to Restored (0%), then switch V18 -> V17 -> V16: --split must stay at 0%, never jump back to 50%
    act(() => ([...container.querySelectorAll('.restore-compare__label')].find((b) => b.textContent === 'Restored') as HTMLButtonElement).click());
    expect(compare().style.getPropertyValue('--split')).toBe('0%');

    const saved: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      saved.push(this.getAttribute('href')!);
    });
    const download = () => [...container.querySelectorAll('button')].find((b) => b.textContent === 'Download') as HTMLButtonElement;

    act(() => rows()[1]!.click());
    for (let i = 0; i < 4; i++) await act(async () => await new Promise((r) => setTimeout(r, 20)));
    expect(compare().style.getPropertyValue('--split')).toBe('0%');
    expect(rows().map((r) => r.getAttribute('aria-pressed'))).toEqual(['false', 'true', 'false']);
    const picV17 = compare().querySelector('.restore-compare__pic img') as HTMLImageElement;
    expect(picV17.getAttribute('src')).toBe(`/api/media/${sha}/variants/${V17.id}/file`);
    expect(picV17.style.transform).toBe('rotate(90deg)');
    act(() => download().click());

    act(() => rows()[2]!.click());
    for (let i = 0; i < 4; i++) await act(async () => await new Promise((r) => setTimeout(r, 20)));
    expect(compare().style.getPropertyValue('--split')).toBe('0%');
    expect(rows().map((r) => r.getAttribute('aria-pressed'))).toEqual(['false', 'false', 'true']);
    expect((compare().querySelector('.restore-compare__pic img') as HTMLImageElement).getAttribute('src')).toBe(`/api/media/${sha}/variants/${V16.id}/file`);
    act(() => download().click());

    expect(saved).toEqual([
      `/api/media/${sha}/variants/${V17.id}/export`,
      `/api/media/${sha}/variants/${V16.id}/export`,
    ]);
  });
});
