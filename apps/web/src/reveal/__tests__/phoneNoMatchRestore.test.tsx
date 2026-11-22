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
import { PhoneNoMatch } from '../PhoneNoMatch.js';
import { phoneSlotCrop } from '../phoneSlot.js';
import { UNCHANGED } from '../restoration.js';
import type { RevealStatus } from '../revealStatus.js';
import { RevealPhoto } from '../RevealPhoto.js';
import { winningPointer } from './winningPointer.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

// d38-2b, by the lead's yes of 05:02 on 22 Nov to EXPORT-d38-2-FILES.md and UIUX's live read of main dd6eeeeb (a
// no-match photo could not be restored at phone width): on the phone no-match screen, Restore, compact and outlined,
// after its own actions in the laptop's built order (Show predicted sky, Line it up yourself, Restore), 16 px under Show
// predicted sky (UIUX-nomatch-restore-READY.md) or 10 px under Line it up yourself (UIUX-align-READY.md point 1),
// reached by scrolling, the warnings after it. And the refusal edge, DECIDED-BY-LEAD the same hour on UIUX's proposal:
// after a refusal on a phone no-match photo, the no-match screen's own main action comes back at 772, the stop's
// sentence and "Your photo is unchanged." above it, as the 15:38 ruling brings Ask out loud back on the reveal.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const jobs = (JSON.parse(fs.readFileSync(path.resolve(here, '../../../../../tests/fixtures/job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const done = jobs.find((job) => job.kind === 'restoration' && job.state === 'succeeded')!;
const { finishedAtUTC: _f, attemptId: _a, variantId: _v, ...rest } = structuredClone(done);
const still = (stage: string) => ({ stage, unit: 'frames', done: 1, total: 1 });
const RUNNING = { ...rest, state: 'running', stages: [still('decode'), still('measure')] } as unknown as UploadJob;
const REFUSED = {
  ...rest,
  state: 'rejected',
  stages: [still('decode'), still('measure'), still('restore'), still('encode'), { stage: 'write', unit: 'bytes', done: 900, total: 900 }, still('verify')],
} as unknown as UploadJob;
const FAILED = { ...rest, state: 'failed', stages: [still('decode'), still('measure'), { stage: 'restore', unit: 'frames', done: 0, total: 1 }] } as unknown as UploadJob;
const REASON = 'The restoration changed how bright a star is, so it was not kept.';

function slotted(session: ObservationSession): ObservationSession {
  const asset = session.sourceAsset!;
  const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
  return { ...session, cropTransform: { ...session.cropTransform, ...phoneSlotCrop(shown.widthPx, shown.heightPx, 390, 844) } } as ObservationSession;
}
const noMatch = slotted(poseSession(sample('horizon_predicted_only')));
const matched = slotted(poseSession(sample('plate_calibrated')));

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
  vi.restoreAllMocks();
});
const q = (s: string) => container.querySelector(s);
const button = (label: string) => [...container.querySelectorAll('button')].find((b) => b.textContent === label) as HTMLButtonElement | undefined;
const order = (scope: Element) => [...scope.querySelectorAll('button, .reveal-status__warns')].map((el) => (el.tagName === 'BUTTON' ? el.textContent : 'warnings'));
async function showPhone(extra: Record<string, unknown>) {
  act(() =>
    root.render(
      createElement(RevealPhoto, {
        session: noMatch,
        onSelectFinding: () => {},
        onHome: () => {},
        viewportWidthPx: 390,
        viewportHeightPx: 844,
        phone: true,
        restoration: { phase: 'idle' },
        onRestore: () => {},
        onCancelRestore: () => {},
        onShowPredictedSky: () => {},
        ...extra,
      } as unknown as Parameters<typeof RevealPhoto>[0])
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
const STATUS: RevealStatus = { kind: 'unmatched', lines: ['No stars matched.'], warnings: ['One warning.'], predictedSky: true } as unknown as RevealStatus;

describe('Restore on the phone no-match screen (d38-2b)', () => {
  it('offers Restore after the screen\'s own actions, the warnings after it, and asks once pressed', async () => {
    const onRestore = vi.fn();
    await showPhone({ onRestore });
    const screen = q('[data-testid="reveal-phone-scroll"]')!;
    const labels = order(screen);
    expect(labels[0]).toBe('Show predicted sky');
    expect(labels.slice(labels.indexOf('Restore'))).toEqual(['Restore', 'warnings']);
    const restore = button('Restore')!;
    expect(restore.className).toContain('look-second');
    expect(restore.className).toContain('look-size-phone');
    expect(winningPointer(restore)).toBe('auto');
    act(() => restore.click());
    expect(onRestore).toHaveBeenCalledTimes(1);
  });

  it('sits 16 px under Show predicted sky, or 10 px under Line it up yourself, in the laptop\'s order', () => {
    act(() => root.render(createElement(PhoneNoMatch, { status: STATUS, onShowPredictedSky: () => {}, onRestore: () => {} })));
    expect(order(container)).toEqual(['Show predicted sky', 'Restore', 'warnings']);
    expect(button('Restore')!.parentElement!.className).toBe('reveal-phone-go reveal-phone-go--restore');
    act(() => root.render(createElement(PhoneNoMatch, { status: STATUS, onShowPredictedSky: () => {}, onAlign: () => {}, onRestore: () => {} })));
    expect(order(container)).toEqual(['Show predicted sky', 'Line it up yourself', 'Restore', 'warnings']);
    expect(declarationsOf('.reveal-phone-go--restore')).toEqual({ 'margin-top': '16px' });
    expect(declarationsOf('.reveal-phone-go--more + .reveal-phone-go--restore')).toEqual({ 'margin-top': '10px' });
    act(() => root.render(createElement(PhoneNoMatch, { status: STATUS, onShowPredictedSky: () => {} })));
    expect(button('Restore')).toBeUndefined();
  });

  it('is not offered with no way to restore, while it asks or works, or once one is ready', async () => {
    await showPhone({ onRestore: undefined });
    expect(button('Restore')).toBeUndefined();
    await showPhone({ restoration: { phase: 'asking' } });
    expect(button('Restore')).toBeUndefined();
    await showPhone({ restoration: { phase: 'working', job: RUNNING } });
    expect(q('[data-testid="reveal-phone-scroll"]')).toBeNull();
    expect(button('Restore')).toBeUndefined();
  });
});

describe("after a refusal on a phone no-match photo, the screen's main action comes back (DECIDED-BY-LEAD 05:02)", () => {
  it('stands Show predicted sky at 772 under the stop\'s sentence and "Your photo is unchanged.", taking the press', async () => {
    const onShowPredictedSky = vi.fn();
    await showPhone({ restoration: { phase: 'stopped', job: REFUSED, sentence: REASON, subline: UNCHANGED, canTryAgain: false }, onShowPredictedSky });
    expect([...container.querySelectorAll('.restore-phone-why p')].map((p) => p.textContent)).toEqual([REASON, 'Your photo is unchanged.']);
    const back = q('.restore-phone-go button') as HTMLButtonElement;
    expect(back.textContent).toBe('Show predicted sky');
    expect(back.className).toContain('look-main');
    expect(back.className).toContain('look-size-phone');
    expect(winningPointer(back)).toBe('auto');
    act(() => back.click());
    expect(onShowPredictedSky).toHaveBeenCalledTimes(1);
    expect(button('Try again')).toBeUndefined();
    expect(button('Restore')).toBeUndefined();
  });

  it('leaves 772 to Try again after a failure, and to Ask out loud on a matched photo', async () => {
    await showPhone({ restoration: { phase: 'stopped', job: FAILED, sentence: 'The restoration stopped.', subline: null, canTryAgain: true } });
    expect([...container.querySelectorAll('.restore-phone-go button')].map((b) => b.textContent)).toEqual(['Try again']);
    await showPhone({ session: matched, restoration: { phase: 'stopped', job: REFUSED, sentence: REASON, subline: UNCHANGED, canTryAgain: false } });
    expect(container.querySelectorAll('.restore-phone-go button')).toHaveLength(0);
  });
});
