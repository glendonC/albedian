// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MediaVariantSchema, type MediaVariant, type ObservationSession, type UploadJob } from '@albedian/contracts';
import { executeVoiceOrTextCommand } from '@albedian/observation';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { readRestorations } from '../../services/apiClient.js';
import { RevealPhoto } from '../RevealPhoto.js';
import { withShownRestoration } from '../shownRestoration.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

// voice-restore-one (LEAD ANSWER to WEB-UX 17:26 on 24 Nov; SUBMISSION's finding, reproduced live on main 27482af0 on
// rp-b-01: with the restored version on screen, "make the sky clearer" answered "No restored version of this recording
// exists yet."). Voice read session.mediaVariants, which the app never filled: the restored version the screen shows
// lived in RevealPhoto alone. Now RevealPhoto reports it and the session holds exactly that one, so voice finds it.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const fixtures = path.resolve(here, '../../../../../tests/fixtures');
const media = JSON.parse(fs.readFileSync(path.join(fixtures, 'media_contract.json'), 'utf8')) as { valid: { MediaVariant: Array<Record<string, unknown>> } };
const template = media.valid.MediaVariant.find((v) => v.kind === 'restoration')!;
const jobs = (JSON.parse(fs.readFileSync(path.join(fixtures, 'job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const succeeded = structuredClone(jobs.find((job) => job.kind === 'restoration' && job.state === 'succeeded')!);
const session = poseSession(sample('plate_calibrated'));
const sha = session.sourceAsset!.sha256;
const variantOf = (id: string, createdAtUTC: string, byteSize: number, of = sha) =>
  MediaVariantSchema.parse({ ...template, id, createdAtUTC, byteSize, sourceSha256: of, sourceAssetId: `src_${of.slice(0, 16)}`, uri: `/api/media/${of}/variants/${id}/file` });
const OLDER = variantOf('var_0000000000000001', '2026-11-18T18:20:00Z', 14_000_000);
const NEWER = variantOf(succeeded.variantId!, '2026-11-19T18:20:00Z', 14_600_000);
const done = { phase: 'done' as const, job: succeeded, variantUrl: `/api/media/${sha}/variants/${succeeded.variantId}/file`, orientation: { quarterTurnsClockwise: 0 as const, mirrored: false } };

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
const answer = (variants: unknown[]) => vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants, attempts: [] }), { status: 200 }));
const flush = async () => {
  for (let i = 0; i < 5; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
};
/** RevealPhoto as the app draws it, with what it reports of the restored version it shows. */
async function show(extra: Record<string, unknown>, reported: Array<MediaVariant | null>) {
  act(() =>
    root.render(
      createElement(RevealPhoto, {
        session,
        onSelectFinding: () => {},
        onHome: () => {},
        viewportWidthPx: 1440,
        viewportHeightPx: 900,
        onShownRestoration: (v: MediaVariant | null) => reported.push(v),
        ...extra,
      } as unknown as Parameters<typeof RevealPhoto>[0]),
    ),
  );
  await flush();
}
const clearer = (s: ObservationSession) => executeVoiceOrTextCommand(s, 'make the sky clearer', 'text');

describe('voice finds the restored version the screen shows', () => {
  it('on main the app holds no restored version after Restore, so voice refuses (the finding, as reproduced live)', () => {
    expect(session.mediaVariants).toEqual([]);
    expect(clearer(session).outcome).toMatchObject({ status: 'rejected', reason: 'missing_source', explanation: 'This photo has not been restored.' });
  });

  it('after Restore, the laptop compare shows the newest ready restoration: it is reported, and voice then finds it', async () => {
    answer([OLDER, NEWER]);
    const reported: Array<MediaVariant | null> = [];
    await show({ restoration: done }, reported);
    expect(container.querySelector('[data-testid="restore-compare"]')).not.toBeNull();
    const shown = reported.at(-1)!;
    expect(shown).not.toBeNull();
    expect(shown.id).toBe(NEWER.id);
    const result = clearer(withShownRestoration(session, shown, null));
    expect(result.outcome.status).toBe('completed');
    expect(result.session.activeVariantId).toBe(NEWER.id);
  });

  it('the row pressed is the one reported, and none once the compare is left', async () => {
    answer([OLDER, NEWER]);
    const reported: Array<MediaVariant | null> = [];
    await show({ restoration: done }, reported);
    const rows = [...container.querySelectorAll<HTMLButtonElement>('[data-testid="restore-versions"] button')];
    act(() => rows[1]!.click());
    expect(reported.at(-1)!.id).toBe(OLDER.id);
    await show({ restoration: done, originalPixels: true }, reported);
    expect(container.querySelector('[data-testid="restore-compare"]')).toBeNull();
    expect(reported.at(-1)).toBeNull();
  });

  it('the phone shows its stored restorations at once (D10 item 5): the shown one is reported with no Restore pressed', async () => {
    answer([OLDER, NEWER]);
    const phone = { ...session, cropTransform: session.cropTransform } as ObservationSession;
    const reported: Array<MediaVariant | null> = [];
    await show({ session: phone, phone: true, viewportWidthPx: 390, viewportHeightPx: 844, restoration: { phase: 'idle' } }, reported);
    expect(container.querySelector('[data-testid="restore-compare"]')).not.toBeNull();
    expect(reported.at(-1)!.id).toBe(NEWER.id);
  });

  it('nothing restored on screen reports none, and voice keeps its refusal', async () => {
    answer([]);
    const reported: Array<MediaVariant | null> = [];
    await show({ restoration: { phase: 'idle' } }, reported);
    expect(reported.length).toBeGreaterThan(0);
    expect(reported.every((v) => v === null)).toBe(true);
    expect(clearer(withShownRestoration(session, null, null)).outcome).toMatchObject({ status: 'rejected', reason: 'missing_source' });
  });
});

describe('withShownRestoration', () => {
  it('puts in the version shown, swaps the one it put in before, takes it out when none is shown, and hands back the same session when nothing changes', () => {
    const one = withShownRestoration(session, NEWER, null);
    expect(one.mediaVariants.map((v) => v.id)).toEqual([NEWER.id]);
    expect(withShownRestoration(one, NEWER, NEWER.id)).toBe(one);
    const other = withShownRestoration(one, OLDER, NEWER.id);
    expect(other.mediaVariants.map((v) => v.id)).toEqual([OLDER.id]);
    expect(withShownRestoration(other, null, OLDER.id).mediaVariants).toEqual([]);
    expect(withShownRestoration(session, null, null)).toBe(session);
  });

  it("leaves the session's own variants alone, restorations included, and never adds one twice", () => {
    const own = { ...session, mediaVariants: [OLDER] } as ObservationSession;
    // Nothing shown: the session's own restoration stays
    expect(withShownRestoration(own, null, null)).toBe(own);
    // Shown and already held: nothing to add
    expect(withShownRestoration(own, OLDER, null)).toBe(own);
    // Another shown: put beside it; then taken out again, the session's own one stays
    const both = withShownRestoration(own, NEWER, null);
    expect(both.mediaVariants.map((v) => v.id)).toEqual([OLDER.id, NEWER.id]);
    expect(withShownRestoration(both, null, NEWER.id).mediaVariants.map((v) => v.id)).toEqual([OLDER.id]);
  });

  it("never takes another recording's variant, nor anything but a restoration", () => {
    const elsewhere = variantOf('var_00000000000000e1', '2026-11-19T18:20:00Z', 1, 'e'.repeat(64));
    expect(withShownRestoration(session, elsewhere, null)).toBe(session);
    const copyTemplate = media.valid.MediaVariant.find((v) => v.kind === 'display_copy')!;
    const copy = MediaVariantSchema.parse({ ...copyTemplate, id: 'var_00000000000000c0', sourceSha256: sha, sourceAssetId: `src_${sha.slice(0, 16)}`, uri: `/api/media/${sha}/variants/var_00000000000000c0/file` });
    expect(withShownRestoration(session, copy, null)).toBe(session);
    const withCopy = { ...session, mediaVariants: [copy] } as ObservationSession;
    expect(withShownRestoration(withCopy, NEWER, null).mediaVariants.map((v) => v.id)).toEqual([copy.id, NEWER.id]);
  });
});

describe('readRestorations', () => {
  it('keeps the whole variant the route lists, as the session needs it', async () => {
    answer([OLDER, NEWER]);
    const list = await readRestorations(sha);
    expect(list.map((v) => v.variant)).toEqual([NEWER, OLDER]);
  });
});
