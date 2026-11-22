// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { orientedSize, type ObservationSession, type UploadJob } from '@albedian/contracts';
import { POSE, poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { phoneSlotCrop } from '../phoneSlot.js';
import { UNCHANGED } from '../restoration.js';
import { RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

// The tag rule through a restoration's wait and stop at phone width (AUDIT-B's FAIL of d38-3; EXPORT-d38tag-QUESTION.md,
// recommended): while D38's row and its button hold the foot and predicted marks are drawn, item A's lead stays above
// the row: the Predicted tag at full, then on an estimate the pose's first sentence (UIUX-tagphone-READY.md item A). In
// the wait it is one block rising from 14 px above the row; in a stop it heads the stop's own block.

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
const REASON = 'The restoration changed how bright a star is, so it was not kept.';

function slotted(session: ObservationSession): ObservationSession {
  const asset = session.sourceAsset!;
  const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
  return { ...session, cropTransform: { ...session.cropTransform, ...phoneSlotCrop(shown.widthPx, shown.heightPx, 390, 844) } } as ObservationSession;
}
const estimate = slotted(poseSession(POSE));
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
async function showPhone(extra: Record<string, unknown>) {
  act(() =>
    root.render(
      createElement(RevealPhoto, { session: estimate, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: 390, viewportHeightPx: 844, phone: true, onRestore: () => {}, onCancelRestore: () => {}, ...extra })
    )
  );
  for (let i = 0; i < 5; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
}
const tags = () => [...container.querySelectorAll('.look-tag')].map((t) => t.textContent);
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

describe('the Predicted tag through the phone restoration (the tag rule)', () => {
  it("keeps an estimate's tag and pose sentence above the wait's row while its predicted marks stay", async () => {
    await showPhone({ restoration: { phase: 'idle' } });
    const sentence = q('.reveal-phone-estimate__source')!.textContent;
    await showPhone({ restoration: { phase: 'working', job: RUNNING } });
    expect(container.querySelectorAll('.reveal-mark.is-predicted').length).toBeGreaterThan(0);
    const lead = q('.restore-phone-lead')!;
    expect([...lead.children].map((el) => el.className)).toEqual(['look-tag look-tag--predicted', 'reveal-phone-estimate__source']);
    expect(lead.children[1]!.textContent).toBe(sentence);
    expect(tags()).toContain('Predicted');
    // Above the row, which still holds the counts' place
    expect(lead.compareDocumentPosition(q('.restore-phone-wait .work-phone-row')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(q('[data-testid="reveal-phone-estimate"]')).toBeNull();
  });

  it("heads a stop's own block with them, the stop's sentence and the photo unchanged under", async () => {
    await showPhone({ restoration: { phase: 'stopped', job: REFUSED, sentence: REASON, subline: UNCHANGED, canTryAgain: false } });
    const why = q('.restore-phone-why')!;
    const lead = why.querySelector('.restore-phone-why__lead')!;
    expect(why.firstElementChild).toBe(lead);
    expect([...lead.children].map((el) => el.className)).toEqual(['look-tag look-tag--predicted', 'reveal-phone-estimate__source']);
    expect([...why.querySelectorAll(':scope > p')].map((p) => p.textContent)).toEqual([REASON, 'Your photo is unchanged.']);
    expect(q('.restore-phone-lead')).toBeNull();
  });

  it('draws no lead where no predicted mark is drawn', async () => {
    const noPredicted = {
      ...matched,
      observationSolution: { ...matched.observationSolution!, findings: matched.observationSolution!.findings.filter((f) => f.evidenceClass !== 'predicted') },
    } as ObservationSession;
    await showPhone({ session: noPredicted, restoration: { phase: 'working', job: RUNNING } });
    expect(container.querySelectorAll('.reveal-mark.is-predicted')).toHaveLength(0);
    expect(q('.restore-phone-lead')).toBeNull();
  });

  // UIUX-tagphone-READY.md item D (decided 13:08 on 21 Nov): where the phone card sheet covers a layer's tag, the covered
  // tag stays drawn until the sheet covers it and the card's own tag stands in for it (AUDIT-C's open point on tagd: the
  // restoration's lead is item A's tag, so a star picked during the wait or after a stop leaves it drawn under the sheet)
  it('item D: under a picked star the lead stays drawn, with the wait or the stop, all drawn before the card so its sheet covers them', async () => {
    const star = estimate.observationSolution!.findings.find((f) => f.evidenceClass === 'predicted')!;
    const picked = { ...estimate, selectedFindingId: star.id };
    await showPhone({ session: picked, restoration: { phase: 'working', job: RUNNING } });
    const card = q('[data-testid="star-card"]')!;
    expect(card).not.toBeNull();
    const lead = q('.restore-phone-lead')!;
    expect(lead).not.toBeNull();
    expect(lead.firstElementChild!.getAttribute('data-evidence-class')).toBe('predicted');
    // The opaque sheet (no stacking of its own) paints over what comes before it in the page: the lead, the row, Cancel
    for (const part of [lead, q('.restore-phone-wait')!]) expect(part.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await showPhone({ session: picked, restoration: { phase: 'stopped', job: REFUSED, sentence: REASON, subline: UNCHANGED, canTryAgain: false } });
    const stopped = q('.restore-phone-stopped')!;
    expect(stopped.querySelector('.restore-phone-why__lead .look-tag')!.getAttribute('data-evidence-class')).toBe('predicted');
    expect(stopped.compareDocumentPosition(q('[data-testid="star-card"]')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('sets the lead as item A does, rising from 14 px above the row, and 14 px above the stop sentence in a stop', () => {
    expect(declarationsOf('.restore-phone-lead')).toEqual({
      position: 'absolute',
      left: '24px',
      right: '24px',
      bottom: 'calc(100% - 654px)',
      display: 'flex',
      'flex-direction': 'column',
      'align-items': 'flex-start',
      'pointer-events': 'none',
    });
    expect(declarationsOf('.restore-phone-why__lead')).toEqual({ display: 'flex', 'flex-direction': 'column', 'align-items': 'flex-start', 'margin-bottom': '4px' });
  });
});
