// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { UploadJob } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { RevealPhoto } from '../RevealPhoto.js';
import type { Restoration } from '../restoration.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

// cliprows-cancel-one (UIUX gap eight, DECIDED-BY-LEAD 13:09 on 24 Nov, UIUX-restoration-READY.md): while a clip's steps
// count together on the laptop, Cancel stands 16 px above the restoring rows' top and rises with them as their bars are
// added, so no row ever runs under it; no new look or word. The rows keep their foot 40 px up and 36 px in, 300 px wide;
// a still's restoration, and a stopped one, are drawn as before (Cancel's block at bottom 262 px).

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const jobs = (JSON.parse(fs.readFileSync(path.resolve(here, '../../../../../tests/fixtures/job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const succeeded = structuredClone(jobs.find((job) => job.kind === 'restoration' && job.state === 'succeeded')!);
const running = (stages: UploadJob['stages']): UploadJob => {
  const { finishedAtUTC: _f, attemptId: _a, variantId: _v, ...rest } = succeeded;
  return { ...rest, state: 'running', stages } as UploadJob;
};
// The public clip's 328 frames, three steps counting at once (gap eight's capture at 150 s)
const counting = running([
  { stage: 'decode', unit: 'frames', done: 292, total: 328 },
  { stage: 'measure', unit: 'frames', done: 120, total: 328 },
  { stage: 'restore', unit: 'frames', done: 40, total: 328 },
]);
const clip = poseSession(sample('plate_calibrated'), 'video');
const photo = poseSession(sample('plate_calibrated'));

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ variants: [], attempts: [] }), { status: 200 }));
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});
const show = (session: typeof clip, restoration: Restoration) =>
  act(() =>
    root.render(
      createElement(RevealPhoto, {
        session,
        onSelectFinding: () => {},
        onHome: () => {},
        viewportWidthPx: 1440,
        viewportHeightPx: 900,
        restoration,
        onRestore: () => {},
        onCancelRestore: () => {},
      })
    )
  );
function declarationsOf(selector: string): Record<string, string> {
  const found: Record<string, string> = {};
  let n = 0;
  postcss.parse(css).walkRules((rule) => {
    if (rule.selector.replace(/\s+/g, ' ') !== selector || rule.parent?.type !== 'root') return;
    n += 1;
    rule.walkDecls((decl) => {
      found[decl.prop] = decl.value.replace(/\s+/g, ' ');
    });
  });
  expect(n, selector).toBe(1);
  return found;
}

describe("a clip's restoring rows under Cancel on the laptop (gap eight)", () => {
  it("stands Cancel's block on the rows, Cancel first, in one column with its foot 40 px up", () => {
    show(clip, { phase: 'working', job: counting });
    const wait = container.querySelector('.restore-wait');
    expect(wait).not.toBeNull();
    expect([...wait!.children].map((child) => child.className)).toEqual(['work-why restore-why', 'work-steps restore-steps']);
    expect(wait!.querySelector('.work-why button')?.textContent).toBe('Cancel');
    expect(wait!.querySelectorAll('.work-steps .work-step__bar')).toHaveLength(3);
  });

  it('sets the column 16 px apart, where the rows stood, and lets both parts flow in it', () => {
    expect(declarationsOf('.restore-wait')).toEqual({
      position: 'absolute',
      left: '36px',
      bottom: '40px',
      width: '300px',
      display: 'flex',
      'flex-direction': 'column',
      gap: '16px',
    });
    expect(declarationsOf('.restore-wait .work-why')).toEqual({ position: 'static', width: 'auto' });
    expect(declarationsOf('.restore-wait .work-steps')).toEqual({ position: 'static', width: 'auto' });
    // The rows' own place, which the column takes over, is unchanged
    const steps = declarationsOf('.work-steps');
    expect([steps.position, steps.left, steps.bottom, steps.width]).toEqual(['absolute', '36px', '40px', '300px']);
  });

  it("leaves a still's restoration as it was: Cancel's block and the rows each in their own place", () => {
    show(photo, { phase: 'working', job: counting });
    expect(container.querySelector('.restore-wait')).toBeNull();
    const why = container.querySelector('.work-why');
    const steps = container.querySelector('.work-steps');
    expect(why?.parentElement).toBe(steps?.parentElement);
    expect(declarationsOf('.work-why').bottom).toBe('262px');
  });

  it("leaves a stopped clip's restoration as it was", () => {
    show(clip, { phase: 'stopped', job: { ...counting, state: 'canceled' } as UploadJob, sentence: 'You canceled it.', subline: null, canTryAgain: true });
    expect(container.querySelector('.restore-wait')).toBeNull();
    expect(container.querySelector('.work-why')).not.toBeNull();
  });
});
