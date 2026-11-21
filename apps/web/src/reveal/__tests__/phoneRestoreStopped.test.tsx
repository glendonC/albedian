// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { orientedSize, type ObservationSession, type UploadJob } from '@albedian/contracts';
import { LOOK_ICON_PATHS } from '../../look/index.js';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { phoneSlotCrop } from '../phoneSlot.js';
import { UNCHANGED } from '../restoration.js';
import { RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

// D38 item 5 with the ruling of 15:43 on 21 Nov (UIUX-restorephone-READY.md, last section, item 2): a stopped
// restoration at phone width in D37's layout with D21's sentences. A failure: the job's own sentence, the stopped row,
// Try again alone at 772. A refusal: the reason's sentence with "Your photo is unchanged." under it (14 px, ink 66,
// 10 px apart as on the laptop) in D37's rising block, the stopped row at 668, and Ask out loud back at 772.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const jobs = (JSON.parse(fs.readFileSync(path.resolve(here, '../../../../../tests/fixtures/job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const done = jobs.find((job) => job.kind === 'restoration' && job.state === 'succeeded')!;
const { finishedAtUTC: _f, attemptId: _a, variantId: _v, ...rest } = structuredClone(done);
const still = (stage: string) => ({ stage, unit: 'frames', done: 1, total: 1 });
/** A still that failed while restoring: opened and measured, then stopped. */
const FAILED = { ...rest, state: 'failed', stages: [still('decode'), still('measure')] } as unknown as UploadJob;
/** A still the gate refused: every step reported, stopped at Checking. */
const REFUSED = {
  ...rest,
  state: 'rejected',
  stages: [still('decode'), still('measure'), still('restore'), still('encode'), { stage: 'write', unit: 'bytes', done: 900, total: 900 }, still('verify')],
} as unknown as UploadJob;
const REASON = 'The restoration changed how bright a star is, so it was not kept.';
const FAILURE = 'The restoration stopped on an internal error.';

function slotted(session: ObservationSession): ObservationSession {
  const asset = session.sourceAsset!;
  const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
  return { ...session, cropTransform: { ...session.cropTransform, ...phoneSlotCrop(shown.widthPx, shown.heightPx, 390, 844) } } as ObservationSession;
}
const photo = slotted(poseSession(sample('plate_calibrated')));

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
async function showPhone(extra: Record<string, unknown> = {}) {
  act(() =>
    root.render(
      createElement(RevealPhoto, { session: photo, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: 390, viewportHeightPx: 844, phone: true, onRestore: () => {}, ...extra })
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

describe('a stopped restoration at phone width (D38 item 5)', () => {
  it("shows a failure as D37 does: the job's own sentence, the stopped row, Try again alone where Ask out loud was", async () => {
    const onRestore = vi.fn();
    const told = vi.fn();
    await showPhone({ restoration: { phase: 'stopped', job: FAILED, sentence: FAILURE, subline: null, canTryAgain: true }, onRestore, onPhoneFootChange: told });
    const why = q('.restore-phone-why')!;
    expect(why.getAttribute('role')).toBe('alert');
    expect([...why.querySelectorAll('p')].map((p) => p.textContent)).toEqual([FAILURE]);
    const row = q('.restore-phone-stopped .work-phone-row')!;
    expect(row.className).toBe('work-phone-row is-stopped');
    expect(row.querySelector('.work-phone-row__x svg.look-i path')!.getAttribute('d')).toBe(LOOK_ICON_PATHS.x);
    expect(row.querySelector('b')!.textContent).toBe('Restoring');
    // A still has nothing to count: no count and no bar, as on its wait
    expect(row.querySelector('.work-phone-row__bar')).toBeNull();
    expect(why.compareDocumentPosition(row) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(q('.restore-phone-stopped .work-phone-fade')!.getAttribute('aria-hidden')).toBe('true');
    const again = button('Try again')!;
    expect(again.closest('.restore-phone-go')).not.toBeNull();
    expect(again.className).toContain('look-size-phone');
    act(() => again.click());
    expect(onRestore).toHaveBeenCalledTimes(1);
    expect(told).toHaveBeenLastCalledWith(true);
    expect(q('[data-testid="reveal-legend"]')).toBeNull();
    expect(button('Restore')).toBeUndefined();
  });

  it('shows a refusal with its reason and the photo unchanged under it, no button of its own, and frees Ask out loud', async () => {
    const told = vi.fn();
    await showPhone({ restoration: { phase: 'stopped', job: REFUSED, sentence: REASON, subline: UNCHANGED, canTryAgain: false }, onPhoneFootChange: told });
    expect([...q('.restore-phone-why')!.querySelectorAll('p')].map((p) => [p.className, p.textContent])).toEqual([
      ['restore-phone-why__s', REASON],
      ['restore-phone-why__sub', 'Your photo is unchanged.'],
    ]);
    expect(q('.restore-phone-stopped .work-phone-row > b')!.textContent).toBe('Checking');
    expect(button('Try again')).toBeUndefined();
    expect(button('Restore')).toBeUndefined();
    expect(q('.restore-phone-stopped .restore-phone-go')).toBeNull();
    expect(told).not.toHaveBeenCalledWith(true);
  });

  it("takes the no-match screen's place too, as the wait does", async () => {
    const noMatch = slotted(poseSession(sample('horizon_predicted_only')));
    await showPhone({ session: noMatch, restoration: { phase: 'stopped', job: FAILED, sentence: FAILURE, subline: null, canTryAgain: true } });
    expect(q('[data-testid="reveal-phone-scroll"]')).toBeNull();
    expect(q('.restore-phone-why p')!.textContent).toBe(FAILURE);
  });

  it('shows nothing of it over Real sky or on a clip', async () => {
    const stopped = { phase: 'stopped', job: FAILED, sentence: FAILURE, subline: null, canTryAgain: true };
    await showPhone({ restoration: stopped, realSky: true });
    expect(q('.restore-phone-stopped')).toBeNull();
    await showPhone({ restoration: stopped, session: slotted(poseSession(sample('plate_calibrated'), 'video')) });
    expect(q('.restore-phone-stopped')).toBeNull();
  });

  it("sets the block as D37's, its two lines 10 px apart as the laptop's refusal", () => {
    expect(declarationsOf('.restore-phone-why')).toEqual({ position: 'absolute', left: '24px', right: '24px', bottom: 'calc(100% - 654px)', display: 'grid', gap: '10px' });
    expect(declarationsOf('.restore-phone-why__s')).toEqual({
      margin: '0',
      font: '300 22px / 1.2 var(--look-font-sans)',
      'letter-spacing': '-0.03em',
      'text-wrap': 'balance',
      color: 'var(--look-ink)',
    });
    expect(declarationsOf('.restore-phone-why__sub')).toEqual({ margin: '0', 'font-size': '14px', 'line-height': '1.45', color: 'var(--look-ink-2)', 'letter-spacing': '-0.005em' });
  });
});
