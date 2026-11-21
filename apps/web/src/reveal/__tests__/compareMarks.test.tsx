// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { orientedSize, type ObservationSession, type UploadJob } from '@albedian/contracts';
import { POSE, poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { phoneSlotCrop } from '../phoneSlot.js';
import { RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

// UIUX-restorephone-READY.md, last section, item 1 (ruled 15:38, decided by the lead 15:43 on 21 Nov; D15's pictures
// rs-cmp and rsp-cmp): over the restore compare only the matched marks are drawn, at every width; predicted marks (an
// estimate photo's, or a matched photo's unmatched predicted stars) are not drawn over it, so no predicted layer stands
// without its tag. They show, with their tag, wherever the compare is not on screen (AUDIT-B's FAIL of phonecompare-1).

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const fixtures = path.resolve(here, '../../../../../tests/fixtures');
const media = JSON.parse(fs.readFileSync(path.join(fixtures, 'media_contract.json'), 'utf8')) as { valid: { MediaVariant: Array<Record<string, unknown>> } };
const template = media.valid.MediaVariant.find((v) => v.kind === 'restoration')!;
const jobs = (JSON.parse(fs.readFileSync(path.join(fixtures, 'job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const succeeded = structuredClone(jobs.find((job) => job.kind === 'restoration' && job.state === 'succeeded')!);

function slotted(session: ObservationSession): ObservationSession {
  const asset = session.sourceAsset!;
  const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
  return { ...session, cropTransform: { ...session.cropTransform, ...phoneSlotCrop(shown.widthPx, shown.heightPx, 390, 844) } } as ObservationSession;
}
/** A stored restoration of this photo itself (the route lists only the upload's own). */
function readyFor(session: ObservationSession) {
  const sha = session.sourceAsset!.sha256;
  const id = 'var_0000000000000002';
  return { ...template, id, createdAtUTC: '2026-11-19T18:20:00Z', sourceSha256: sha, sourceAssetId: `src_${sha.slice(0, 16)}`, uri: `/api/media/${sha}/variants/${id}/file` };
}
const estimate = poseSession(POSE);
const matched = poseSession(sample('plate_calibrated'));

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
const count = (s: string) => container.querySelectorAll(s).length;
const answer = (variants: unknown[]) =>
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants, attempts: [] }), { status: 200 }));
async function show(props: Record<string, unknown>) {
  act(() => root.render(createElement(RevealPhoto, { onSelectFinding: () => {}, onHome: () => {}, ...props } as unknown as Parameters<typeof RevealPhoto>[0])));
  for (let i = 0; i < 5; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
}
const phone = (session: ObservationSession) => ({ session: slotted(session), phone: true, viewportWidthPx: 390, viewportHeightPx: 844 });
const done = (session: ObservationSession) => ({
  session,
  viewportWidthPx: 1440,
  viewportHeightPx: 900,
  restoration: { phase: 'done', job: succeeded, variantUrl: `/api/media/${session.sourceAsset!.sha256}/variants/${succeeded.variantId}/file`, orientation: { quarterTurnsClockwise: 0, mirrored: false } },
});

describe('only the matched marks over the restore compare (the ruling of 15:43)', () => {
  it("draws an estimate photo's predicted marks on the phone, and none of them over its compare", async () => {
    answer([]);
    await show(phone(estimate));
    expect(count('.reveal-mark.is-predicted')).toBeGreaterThan(0);
    act(() => root.unmount());
    root = createRoot(container);
    answer([readyFor(slotted(estimate))]);
    await show(phone(estimate));
    expect(q('[data-testid="restore-compare"]')).not.toBeNull();
    expect(count('.reveal-mark.is-predicted')).toBe(0);
    expect(count('[data-testid^="reveal-mark-ring-"]')).toBe(0);
  });

  it("keeps a matched photo's matched marks over the phone compare, its unmatched predicted ones not", async () => {
    answer([readyFor(slotted(matched))]);
    await show(phone(matched));
    expect(q('[data-testid="restore-compare"]')).not.toBeNull();
    expect(count('.reveal-mark.is-catalog')).toBeGreaterThan(0);
    expect(count('.reveal-mark.is-predicted')).toBe(0);
  });

  it('holds on the laptop compare after its own Restore too, matched marks kept', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants: [], attempts: [] }), { status: 200 }));
    await show({ session: estimate, viewportWidthPx: 1440, viewportHeightPx: 900, restoration: { phase: 'idle' }, onRestore: () => {} });
    expect(count('.reveal-mark.is-predicted')).toBeGreaterThan(0);
    await show(done(estimate));
    expect(q('[data-testid="restore-compare"]')).not.toBeNull();
    expect(count('.reveal-mark.is-predicted')).toBe(0);
    await show(done(matched));
    expect(q('[data-testid="restore-compare"]')).not.toBeNull();
    expect(count('.reveal-mark.is-catalog')).toBeGreaterThan(0);
    expect(count('.reveal-mark.is-predicted')).toBe(0);
  });

  it('brings the predicted marks back with their tag once the compare is left (Original pixels shows the photo bare)', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants: [], attempts: [] }), { status: 200 }));
    await show(done(estimate));
    expect(count('.reveal-mark.is-predicted')).toBe(0);
    await show({ ...done(estimate), restoration: { phase: 'idle' } });
    expect(q('[data-testid="restore-compare"]')).toBeNull();
    expect(count('.reveal-mark.is-predicted')).toBeGreaterThan(0);
    expect([...container.querySelectorAll('.look-tag')].map((t) => t.textContent)).toContain('Predicted');
  });
});
