// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { orientedSize, type ObservationSession, type UploadJob } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { phoneSlotCrop } from '../phoneSlot.js';
import { RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

// D38 (UIUX-restorephone-READY.md, decided by the lead 13:32 on 21 Nov; rp-rest, rp-scroll, rp-wait in
// .cache/uiux/restore-phone/screen): on a matched phone reveal, Restore compact and outlined, centred 16 px under Ask
// out loud (top 832), reached by scrolling the whole screen under the fixed top bar; while it works, the counts give
// way to D2's phone row with D10's step words, and Cancel stands where Ask out loud sat. Nothing new is drawn.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const jobs = (JSON.parse(fs.readFileSync(path.resolve(here, '../../../../../tests/fixtures/job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const done = jobs.find((job) => job.kind === 'restoration' && job.state === 'succeeded')!;
const { finishedAtUTC: _f, attemptId: _a, variantId: _v, ...rest } = structuredClone(done);
/** A still being restored: opened and measured (one frame each), now restoring. */
const RUNNING = {
  ...rest,
  state: 'running',
  stages: [
    { stage: 'decode', unit: 'frames', done: 1, total: 1 },
    { stage: 'measure', unit: 'frames', done: 1, total: 1 },
  ],
} as unknown as UploadJob;
const QUEUED = { ...rest, state: 'queued', queueAhead: 1, stages: [] } as unknown as UploadJob;

const W = 390;
const H = 844;
function slotted(session: ObservationSession): ObservationSession {
  const asset = session.sourceAsset!;
  const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
  return { ...session, cropTransform: { ...session.cropTransform, ...phoneSlotCrop(shown.widthPx, shown.heightPx, W, H) } } as ObservationSession;
}
const photo = slotted(poseSession(sample('plate_calibrated')));

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  // No stored restoration: the variants route lists none
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants: [], attempts: [] }), { status: 200 }));
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});
const q = (s: string) => container.querySelector(s);
const button = (label: string) => [...container.querySelectorAll('button')].find((b) => b.textContent === label) as HTMLButtonElement | undefined;
async function showPhone(extra: Record<string, unknown> = {}) {
  act(() =>
    root.render(
      createElement(RevealPhoto, {
        session: photo,
        onSelectFinding: () => {},
        onHome: () => {},
        viewportWidthPx: W,
        viewportHeightPx: H,
        phone: true,
        restoration: { phase: 'idle' },
        onRestore: () => {},
        ...extra,
      })
    )
  );
  for (let i = 0; i < 5; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
}

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

describe('Restore on a matched phone reveal (D38 item 1)', () => {
  it('offers Restore compact and outlined under Ask out loud, the screen made long enough to scroll to it', async () => {
    const onRestore = vi.fn();
    await showPhone({ onRestore });
    const restore = button('Restore')!;
    expect(restore.className).toContain('look-second');
    expect(restore.className).toContain('look-size-phone');
    expect(restore.closest('.restore-phone-rest')).not.toBeNull();
    const screen = q('[data-testid="reveal-photo"]')!;
    expect(screen.className).toContain('is-scroll');
    expect(screen.querySelector('.reveal-phone-room')!.getAttribute('aria-hidden')).toBe('true');
    // The counts stay as on the reveal
    expect(q('[data-testid="reveal-legend"]')).not.toBeNull();
    act(() => restore.click());
    expect(onRestore).toHaveBeenCalledTimes(1);
  });

  it('moves the photo and Ask out loud with the screen as it scrolls, and lets them go when Restore leaves', async () => {
    await showPhone();
    const screen = q('[data-testid="reveal-photo"]') as HTMLElement;
    expect(document.documentElement.dataset.phoneScroll).toBe('');
    screen.scrollTop = 92;
    act(() => {
      screen.dispatchEvent(new Event('scroll'));
    });
    expect(document.documentElement.style.getPropertyValue('--phone-scroll')).toBe('92px');
    await showPhone({ restoration: { phase: 'working', job: RUNNING }, onCancelRestore: () => {} });
    expect(document.documentElement.dataset.phoneScroll).toBeUndefined();
    expect(document.documentElement.style.getPropertyValue('--phone-scroll')).toBe('');
    expect(q('[data-testid="reveal-photo"]')!.className).not.toContain('is-scroll');
  });

  it('is not offered with no way to restore, while one is ready or running, over Real sky, a picked star, an answer, or on a clip', async () => {
    await showPhone({ onRestore: undefined });
    expect(button('Restore')).toBeUndefined();
    await showPhone({ restoration: { phase: 'asking' } });
    expect(button('Restore')).toBeUndefined();
    await showPhone({ realSky: true });
    expect(button('Restore')).toBeUndefined();
    const star = photo.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched')!;
    await showPhone({ session: { ...photo, selectedFindingId: star.id } });
    expect(button('Restore')).toBeUndefined();
    await showPhone({ answer: { heard: 'restore it', sentence: 'Ask again.', answer: null } });
    expect(button('Restore')).toBeUndefined();
    await showPhone({ session: slotted(poseSession(sample('plate_calibrated'), 'video')) });
    expect(button('Restore')).toBeUndefined();
    expect(q('[data-testid="reveal-photo"]')!.className).not.toContain('is-scroll');
  });

  it('gives way to the compare once a restoration is ready', async () => {
    const media = JSON.parse(fs.readFileSync(path.resolve(here, '../../../../../tests/fixtures/media_contract.json'), 'utf8')) as {
      valid: { MediaVariant: Array<Record<string, unknown>> };
    };
    const sha = photo.sourceAsset!.sha256;
    const id = 'var_0000000000000002';
    const ready = { ...media.valid.MediaVariant.find((v) => v.kind === 'restoration')!, id, sourceSha256: sha, sourceAssetId: `src_${sha.slice(0, 16)}`, uri: `/api/media/${sha}/variants/${id}/file` };
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants: [ready], attempts: [] }), { status: 200 }));
    await showPhone();
    expect(q('[data-testid="restore-compare"]')).not.toBeNull();
    expect(button('Restore')).toBeUndefined();
  });
});

describe('the restoration working at phone width (D38 item 2)', () => {
  it("gives the counts' place to the step the job is on, with the time passed and no bar, and Ask out loud's to Cancel", async () => {
    const onCancelRestore = vi.fn(() => new Promise<void>(() => {}));
    const told = vi.fn();
    await showPhone({ restoration: { phase: 'working', job: RUNNING }, onCancelRestore, onPhoneFootChange: told });
    expect(q('[data-testid="reveal-legend"]')).toBeNull();
    const row = q('.restore-phone-wait .work-phone-row')!;
    expect(row.querySelector('b')!.textContent).toBe('Restoring');
    expect(row.querySelector('.work-phone-row__v')!.textContent).toMatch(/^\d+ s$/);
    expect(row.querySelector('.work-phone-row__bar')).toBeNull();
    expect(told).toHaveBeenLastCalledWith(true);
    const cancel = button('Cancel')!;
    expect(cancel.closest('.restore-phone-go')).not.toBeNull();
    expect(cancel.className).toContain('look-second');
    expect(cancel.className).toContain('look-size-phone');
    act(() => cancel.click());
    act(() => cancel.click());
    expect(onCancelRestore).toHaveBeenCalledTimes(1);
    expect(button('Cancel')!.className).toContain('is-asked');
    expect(button('Cancel')!.getAttribute('aria-disabled')).toBe('true');
    expect(button('Restore')).toBeUndefined();
  });

  it('says "Queued" and the jobs ahead while the job waits, as D2 does', async () => {
    await showPhone({ restoration: { phase: 'working', job: QUEUED }, onCancelRestore: () => {} });
    expect(q('.restore-phone-wait .work-phone-row > b')!.textContent).toBe('Queued · 1 ahead');
    expect(button('Cancel')).not.toBeUndefined();
  });

  it("takes the no-match screen's place too while it works, so its row and Cancel hold the foot alone", async () => {
    const noMatch = slotted(poseSession(sample('horizon_predicted_only')));
    await showPhone({ session: noMatch, restoration: { phase: 'idle' } });
    expect(q('[data-testid="reveal-phone-scroll"]')).not.toBeNull();
    await showPhone({ session: noMatch, restoration: { phase: 'working', job: RUNNING }, onCancelRestore: () => {} });
    expect(q('[data-testid="reveal-phone-scroll"]')).toBeNull();
    expect(q('.restore-phone-wait .work-phone-row > b')!.textContent).toBe('Restoring');
    expect(button('Cancel')).not.toBeUndefined();
    expect(button('Show predicted sky')).toBeUndefined();
  });

  it('frees Ask out loud again once nothing of the restoration holds the foot', async () => {
    const told = vi.fn();
    await showPhone({ onPhoneFootChange: told });
    expect(told).not.toHaveBeenCalledWith(true);
  });
});

describe("D38's rules are rp-rest's and rp-scroll's", () => {
  it('sets Restore at 832, the screen scrolling, and Ask out loud moving with it', () => {
    expect(declarationsOf('.restore-phone-rest')).toEqual({ position: 'absolute', left: '0', right: '0', top: '832px', display: 'flex', 'justify-content': 'center' });
    expect(declarationsOf('.reveal-photo.is-phone.is-scroll')).toEqual({ 'overflow-y': 'auto', 'overscroll-behavior': 'contain', 'pointer-events': 'auto' });
    expect(declarationsOf('.reveal-phone-room')).toEqual({ position: 'absolute', left: '0', top: '0', width: '1px', height: '936px', 'pointer-events': 'none' });
    expect(declarationsOf(':root[data-phone-scroll] .ask-bar.is-phone')).toEqual({ translate: '0 calc(-1 * var(--phone-scroll, 0px))' });
  });
});
