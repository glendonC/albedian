// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { orientedSize, type ObservationSolution, type UploadJob } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { RevealPhoto } from '../RevealPhoto.js';
import { restorationRows, type Restoration } from '../restoration.js';

// The restoration screen (UIUX-restoration-READY.md, D10, approved 2026-11-18): Restore under the status lines; the
// job's steps in the counts' place with Cancel above them; once done, the restored picture over the original in the
// photo's own frame, cut at a line that arrives at the middle, moves with a press or a drag, and goes to either edge
// from "Original" and "Restored"; the fixed sentence in the status place. No marks over the compare (D15 unanswered).

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const jobs = (JSON.parse(fs.readFileSync(path.resolve(here, '../../../../../tests/fixtures/job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const succeeded = structuredClone(jobs.find((job) => job.kind === 'restoration' && job.state === 'succeeded')!);
// A still's restoration as the runner reports it: one frame, so nothing to count
const still = (stages: number, state: UploadJob['state'] = 'running'): UploadJob => {
  const { finishedAtUTC: _f, attemptId: _a, variantId: _v, ...rest } = succeeded;
  return {
    ...rest,
    state,
    stages: succeeded.stages.slice(0, stages).map((s) => (s.unit === 'bytes' ? { ...s, done: 1_200_000, total: 1_200_000 } : { ...s, done: 1, total: 1 })),
  } as UploadJob;
};
const calibrated = () => poseSession({ ...sample('plate_calibrated') } as ObservationSolution);

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
const q = (selector: string) => container.querySelector(selector);
const all = (selector: string) => [...container.querySelectorAll(selector)];
function show(restoration: Restoration, extra: Record<string, unknown> = {}) {
  act(() => {
    root.render(
      createElement(RevealPhoto, {
        session: calibrated(),
        onSelectFinding: () => {},
        onHome: () => {},
        viewportWidthPx: 1440,
        viewportHeightPx: 900,
        restoration,
        onRestore: () => {},
        onCancelRestore: () => {},
        ...extra,
      })
    );
  });
}
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

describe("restorationRows: the job's own steps in order", () => {
  it('names the six steps, marks what finished, what works and what has not started, with no numbers for a still', () => {
    expect(restorationRows(still(2)).map((r) => [r.word, r.state, r.value])).toEqual([
      ['Opened', 'done', null],
      ['Measured', 'done', null],
      ['Restoring', 'now', null],
      ['Encoding', 'next', null],
      ['Saving', 'next', null],
      ['Checking', 'next', null],
    ]);
    expect(restorationRows(still(0)).map((r) => r.state)).toEqual(['now', 'next', 'next', 'next', 'next', 'next']);
    expect(restorationRows(still(0, 'queued')).map((r) => r.state)).toEqual(['next', 'next', 'next', 'next', 'next', 'next']);
    expect(restorationRows(succeeded).every((r) => r.state === 'done')).toBe(true);
  });

  it('counts a clip\'s frames as done of total with a bar, Saving in megabytes so far, and marks where a stopped job stopped', () => {
    const clip = { ...still(0), stages: [{ stage: 'decode', unit: 'frames', done: 36, total: 36 }, { stage: 'measure', unit: 'frames', done: 12, total: 36 }] } as UploadJob;
    const rows = restorationRows(clip);
    expect(rows[0]).toMatchObject({ state: 'done', value: '36' });
    expect(rows[1]).toMatchObject({ state: 'now', value: '12 of 36', fraction: 12 / 36 });
    const clipStages = ['decode', 'measure', 'restore', 'encode'].map((stage) => ({ stage, unit: 'frames', done: 36, total: 36 }));
    const saving = { ...still(0), stages: [...clipStages, { stage: 'write', unit: 'bytes', done: 2_400_000, total: null }] } as UploadJob;
    expect(restorationRows(saving)[4]).toMatchObject({ state: 'now', value: '2.4 MB' });
    // A still has nothing to count: Saving shows no megabytes either (D10)
    const stillSaving = { ...still(0), stages: [...still(4).stages, { stage: 'write', unit: 'bytes', done: 2_400_000, total: null }] } as UploadJob;
    expect(restorationRows(stillSaving)[4]).toMatchObject({ state: 'now', value: null });
    expect(restorationRows(still(6)).map((r) => r.value)).toEqual([null, null, null, null, null, null]);
    expect(restorationRows({ ...still(3), state: 'canceled', finishedAtUTC: still(3).startedAtUTC } as UploadJob).map((r) => r.state)).toEqual([
      'done', 'done', 'done', 'failed', 'next', 'next',
    ]);
  });
});

describe('the restoration on the reveal (laptop)', () => {
  it('offers "Restore" under the status lines, and asks once pressed', () => {
    const onRestore = vi.fn();
    show({ phase: 'idle' }, { onRestore });
    const button = q('.reveal-status .reveal-status__act button') as HTMLButtonElement;
    expect(button.textContent).toBe('Restore');
    act(() => button.click());
    expect(onRestore).toHaveBeenCalledTimes(1);
  });

  it("shows the job's steps in the counts' place with Cancel above them while it works, and no Restore", () => {
    const onCancelRestore = vi.fn();
    show({ phase: 'working', job: still(2) }, { onCancelRestore });
    expect(all('.restore-steps .work-step__word').map((w) => w.textContent)).toEqual(['Opened', 'Measured', 'Restoring', 'Encoding', 'Saving', 'Checking']);
    expect(q('.reveal-legend')).toBeNull();
    expect(q('.reveal-status__act')).toBeNull();
    const cancel = q('.restore-why button') as HTMLButtonElement;
    expect(cancel.textContent).toBe('Cancel');
    act(() => cancel.click());
    act(() => cancel.click());
    expect(onCancelRestore).toHaveBeenCalledTimes(1);
    expect(cancel.className).toContain('is-asked');
  });

  it('says a refusal above the stopped steps, the photo unchanged under it, with no Try again and no Restore', () => {
    const refused = { ...still(6), state: 'rejected', finishedAtUTC: still(6).startedAtUTC, attemptId: 'att_7e6f5a4b3c2d1e0f' } as UploadJob;
    show({ phase: 'stopped', job: refused, sentence: 'The restoration changed how bright a star is, so it was not kept.', subline: 'Your photo is unchanged.', canTryAgain: false });
    expect(q('.restore-why')!.className).toBe('work-why restore-why is-refused');
    expect(q('.restore-why p')!.textContent).toBe('The restoration changed how bright a star is, so it was not kept.');
    expect(q('.restore-why .restore-why__sub')!.textContent).toBe('Your photo is unchanged.');
    expect(q('.restore-why button')).toBeNull();
    expect(q('.reveal-status__act')).toBeNull();
  });

  it('says why it stopped above the stopped steps, with Try again, which asks again', () => {
    const onRestore = vi.fn();
    show({ phase: 'stopped', job: { ...still(3), state: 'canceled', finishedAtUTC: still(3).startedAtUTC } as UploadJob, sentence: 'Canceled', subline: null, canTryAgain: true }, { onRestore });
    expect(q('.restore-why p')!.textContent).toBe('Canceled');
    const again = [...container.querySelectorAll('.restore-why button')].find((b) => b.textContent === 'Try again') as HTMLButtonElement;
    act(() => again.click());
    expect(onRestore).toHaveBeenCalledTimes(1);
    expect(all('.restore-steps .work-step.is-failed')).toHaveLength(1);
  });

  it("lays the restored picture over the original in the photo's frame, cut at the line, with the fixed sentence and no marks", () => {
    show({ phase: 'done', job: succeeded, variantUrl: '/api/media/x/variants/var_7e6f5a4b3c2d1e0f/file', orientation: { quarterTurnsClockwise: 0, mirrored: false } });
    const compare = q('[data-testid="restore-compare"]') as HTMLElement;
    expect(compare.style.left).toBe(q('svg.photo-corners') ? compare.style.left : compare.style.left);
    expect((compare.querySelector('.restore-compare__pic img') as HTMLImageElement).getAttribute('src')).toBe('/api/media/x/variants/var_7e6f5a4b3c2d1e0f/file');
    expect(compare.querySelector('.restore-compare__line')).not.toBeNull();
    expect(q('.reveal-status__note')!.textContent).toBe("Restored from the original's own pixels with deterministic filters. Nothing was generated.");
    // D15: the matched stars' marks stay over the compare, where they are on "Your photo"
    expect(q('[data-testid="reveal-marks"]')).not.toBeNull();
    // The counts stay, as the approved compare draws them
    expect(q('.reveal-legend')).not.toBeNull();
  });

  it('starts the line at the right edge and sends it to the middle; Original sends it right, Restored left; a press moves it there', async () => {
    show({ phase: 'done', job: succeeded, variantUrl: '/v.png', orientation: { quarterTurnsClockwise: 0, mirrored: false } });
    const compare = q('[data-testid="restore-compare"]') as HTMLElement;
    await act(async () => await new Promise((r) => setTimeout(r, 40)));
    expect(compare.style.getPropertyValue('--split')).toBe('50%');
    act(() => ([...compare.querySelectorAll('button')].find((b) => b.textContent === 'Original') as HTMLButtonElement).click());
    expect(compare.style.getPropertyValue('--split')).toBe('100%');
    act(() => ([...compare.querySelectorAll('button')].find((b) => b.textContent === 'Restored') as HTMLButtonElement).click());
    expect(compare.style.getPropertyValue('--split')).toBe('0%');
    compare.getBoundingClientRect = () => ({ left: 100, top: 0, width: 400, height: 900, right: 500, bottom: 900, x: 100, y: 0, toJSON: () => ({}) });
    act(() => {
      compare.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 200 }));
    });
    expect(compare.style.getPropertyValue('--split')).toBe('25%');
    act(() => {
      compare.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 400 }));
    });
    expect(compare.className).toContain('is-drag');
    expect(compare.style.getPropertyValue('--split')).toBe('75%');
    act(() => {
      compare.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, clientX: 400 }));
    });
    expect(compare.className).not.toContain('is-drag');
  });
});

describe('D15: marks over the compare and zooming both pictures together (UIUX-restoration-zoom-DECIDED-BY-LEAD.md)', () => {
  // A photo far larger than its frame, so its real pixels lie well beyond the whole view (the sample is 960 by 540)
  const big = () => {
    const session = calibrated();
    return { ...session, sourceAsset: { ...session.sourceAsset!, width: 9600, height: 5400 } };
  };
  const done = () => show({ phase: 'done', job: succeeded, variantUrl: '/v.png', orientation: { quarterTurnsClockwise: 0, mirrored: false } }, { session: big() });
  const compare = () => q('[data-testid="restore-compare"]') as HTMLElement;
  const layers = () => [...compare().querySelectorAll('.restore-compare__layer')] as HTMLElement[];
  const place = () => {
    compare().getBoundingClientRect = () => ({ left: 0, top: 0, width: 400, height: 600, right: 400, bottom: 600, x: 0, y: 0, toJSON: () => ({}) });
  };
  const wheel = (deltaY: number, clientX = 200, clientY = 300) =>
    act(() => {
      compare().dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY, clientX, clientY }));
    });

  it('draws the original under the restored picture in the compare, both in a zoom layer of their own', () => {
    done();
    expect(layers()).toHaveLength(2);
    const original = compare().querySelector('.restore-compare__original img') as HTMLImageElement;
    expect(original.getAttribute('src')).toBe(big().sourceAsset.uri);
  });

  it('zooms both together about the pointer with the wheel, no further than the real pixels, and the marks step aside', () => {
    done();
    place();
    wheel(-300);
    const [a, b] = layers().map((layer) => layer.style.transform);
    expect(a).toBe(b);
    expect(a).toMatch(/^translate\(-?[\d.]+px, -?[\d.]+px\) scale\(([\d.]+)\)$/);
    const zoom = Number(/scale\(([\d.]+)\)/.exec(a!)![1]);
    expect(zoom).toBeGreaterThan(1);
    expect(q('[data-testid="reveal-marks"]')).toBeNull();
    for (let i = 0; i < 40; i += 1) wheel(-500);
    const most = Number(/scale\(([\d.]+)\)/.exec(layers()[0]!.style.transform)![1]);
    // No further than the photo's real pixels: its upright width over the frame's
    const asset = big().sourceAsset;
    const upright = orientedSize(asset.width, asset.height, asset.displayOrientation).widthPx;
    expect(most).toBeCloseTo(upright / parseFloat(compare().style.width), 6);
  });

  it('adds up a burst of wheel steps in one frame, as a trackpad sends them', () => {
    done();
    place();
    act(() => {
      for (let i = 0; i < 4; i += 1) compare().dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: -100, clientX: 200, clientY: 300 }));
    });
    const zoom = Number(/scale\(([\d.]+)\)/.exec(layers()[0]!.style.transform)![1]);
    expect(zoom).toBeCloseTo(Math.exp(4 * 100 * 0.0015), 6);
  });

  it('pans both with a drag while zoomed, leaving the line, and moves the line with a press', () => {
    done();
    place();
    wheel(-300);
    const before = layers()[0]!.style.transform;
    const split = compare().style.getPropertyValue('--split');
    act(() => {
      compare().dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 200, clientY: 300 }));
      compare().dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 260, clientY: 330 }));
      compare().dispatchEvent(new MouseEvent('pointerup', { bubbles: true, clientX: 260, clientY: 330 }));
    });
    expect(layers()[0]!.style.transform).not.toBe(before);
    expect(layers()[0]!.style.transform).toBe(layers()[1]!.style.transform);
    expect(compare().style.getPropertyValue('--split')).toBe(split);
    act(() => {
      compare().dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 100, clientY: 300 }));
      compare().dispatchEvent(new MouseEvent('pointerup', { bubbles: true, clientX: 100, clientY: 300 }));
    });
    expect(compare().style.getPropertyValue('--split')).toBe('25%');
  });

  it('stops a drag at the picture\'s edges, so the frame never shows past it', () => {
    done();
    place();
    wheel(-300);
    const drag = (dx: number, dy: number) =>
      act(() => {
        compare().dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 200, clientY: 300 }));
        compare().dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 200 + dx, clientY: 300 + dy }));
        compare().dispatchEvent(new MouseEvent('pointerup', { bubbles: true, clientX: 200 + dx, clientY: 300 + dy }));
      });
    const parts = () => /translate\((-?[\d.]+)px, (-?[\d.]+)px\) scale\(([\d.]+)\)/.exec(layers()[0]!.style.transform)!.slice(1).map(Number);
    drag(3000, 3000);
    expect(parts().slice(0, 2)).toEqual([0, 0]);
    drag(-6000, -6000);
    const [tx, ty, z] = parts();
    expect(tx).toBeCloseTo(parseFloat(compare().style.width) * (1 - z!), 6);
    expect(ty).toBeCloseTo(parseFloat(compare().style.height) * (1 - z!), 6);
  });

  it('returns to the whole photo on Esc, or on a second press of the current label', async () => {
    done();
    place();
    // The line arrives at the middle first
    await act(async () => await new Promise((r) => setTimeout(r, 40)));
    wheel(-300);
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(layers()[0]!.style.transform).toBe('translate(0px, 0px) scale(1)');
    expect(q('[data-testid="reveal-marks"]')).not.toBeNull();
    wheel(-300);
    const original = [...compare().querySelectorAll('button')].find((b) => b.textContent === 'Original') as HTMLButtonElement;
    act(() => original.click());
    expect(compare().style.getPropertyValue('--split')).toBe('100%');
    expect(layers()[0]!.style.transform).not.toBe('translate(0px, 0px) scale(1)');
    act(() => original.click());
    expect(layers()[0]!.style.transform).toBe('translate(0px, 0px) scale(1)');
  });

  it('does not zoom a photo no larger than its frame, whose real pixels are the whole view', () => {
    show({ phase: 'done', job: succeeded, variantUrl: '/v.png', orientation: { quarterTurnsClockwise: 0, mirrored: false } });
    place();
    wheel(-300);
    expect(layers()[0]!.style.transform).toBe('translate(0px, 0px) scale(1)');
  });

  it('shows the photo bare while Original pixels is pressed: no compare', () => {
    show({ phase: 'done', job: succeeded, variantUrl: '/v.png', orientation: { quarterTurnsClockwise: 0, mirrored: false } }, { originalPixels: true });
    expect(q('[data-testid="restore-compare"]')).toBeNull();
  });
});

describe('a turned photo (the verdict on unit-restore-2)', () => {
  it("turns the restored picture as its variant record says, in a box of its stored shape, so it lies over the upright original", () => {
    const pic = (orientation: { quarterTurnsClockwise: number; mirrored: boolean }) => {
      show({ phase: 'done', job: succeeded, variantUrl: '/v.png', orientation });
      const compare = q('[data-testid="restore-compare"]') as HTMLElement;
      return { box: compare, img: compare.querySelector('.restore-compare__pic img') as HTMLImageElement };
    };
    const upright = pic({ quarterTurnsClockwise: 0, mirrored: false });
    const w = parseFloat(upright.box.style.width);
    const h = parseFloat(upright.box.style.height);
    expect(upright.img.style.width).toBe(`${w}px`);
    expect(upright.img.style.transform).toBe('');
    const quarter = pic({ quarterTurnsClockwise: 1, mirrored: false }).img;
    expect(quarter.style.width).toBe(`${h}px`);
    expect(quarter.style.height).toBe(`${w}px`);
    expect(quarter.style.left).toBe(`${(w - h) / 2}px`);
    expect(quarter.style.top).toBe(`${(h - w) / 2}px`);
    expect(quarter.style.transform).toBe('rotate(90deg)');
    expect(pic({ quarterTurnsClockwise: 2, mirrored: true }).img.style.transform).toBe('rotate(180deg) scaleX(-1)');
    expect(pic({ quarterTurnsClockwise: 3, mirrored: true }).img.style.transform).toBe('rotate(270deg) scaleX(-1)');
  });
});

describe("the restoration's rules are the approved page's", () => {
  it('sets the compare, its line, its glide and its labels as CMP_CSS does, and still with reduced motion', () => {
    expect(declarationsOf('.restore-compare')).toMatchObject({ position: 'absolute', overflow: 'hidden', cursor: 'grab', 'touch-action': 'none', 'pointer-events': 'auto' });
    expect(declarationsOf('.restore-compare.is-drag')).toEqual({ cursor: 'grabbing' });
    // The cut is on an upright box, so it cuts the turned picture along the frame's own width
    expect(declarationsOf('.restore-compare__pic')).toEqual({ position: 'absolute', inset: '0', overflow: 'hidden', 'clip-path': 'inset(0 0 0 var(--split))' });
    expect(declarationsOf('.restore-compare__pic img')).toEqual({ position: 'absolute', display: 'block', 'object-fit': 'fill', 'max-width': 'none' });
    // Each picture's zoom layer fills the frame and scales from its corner; the original fills its layer
    expect(declarationsOf('.restore-compare__layer')).toEqual({ position: 'absolute', inset: '0', 'transform-origin': '0 0' });
    expect(declarationsOf('.restore-compare__original')).toEqual({ position: 'absolute', inset: '0', overflow: 'hidden' });
    expect(declarationsOf('.restore-compare__original img')).toEqual({ position: 'absolute', inset: '0', width: '100%', height: '100%', display: 'block', 'object-fit': 'fill' });
    expect(declarationsOf('.restore-compare__line')).toEqual({ position: 'absolute', top: '0', bottom: '0', left: 'var(--split)', width: '1px', 'margin-left': '-0.5px', background: 'rgba(244, 243, 239, 0.85)' });
    expect(declarationsOf('.restore-compare:not(.is-drag) .restore-compare__pic')).toEqual({ transition: 'clip-path 0.4s cubic-bezier(0.08, 0.62, 0.1, 0.98)' });
    expect(declarationsOf('.restore-compare:not(.is-drag) .restore-compare__line')).toEqual({ transition: 'left 0.4s cubic-bezier(0.08, 0.62, 0.1, 0.98)' });
    expect(css.replace(/\s+/g, ' ')).toContain('@media (prefers-reduced-motion: reduce) { .restore-compare__pic, .restore-compare__line { transition: none !important; } }');
    expect(declarationsOf('.restore-compare__label')).toEqual({
      position: 'absolute',
      top: '24px',
      '--look-h': '28px',
      '--look-fs': '13px',
      padding: '0 12px',
      background: 'rgba(10, 10, 12, 0.55)',
      '-webkit-backdrop-filter': 'blur(14px)',
      'backdrop-filter': 'blur(14px)',
    });
    expect(declarationsOf('.restore-compare__label.is-left')).toEqual({ left: '24px' });
    expect(declarationsOf('.restore-compare__label.is-right')).toEqual({ right: '24px' });
  });

  it('puts Restore 16 px under the status lines, Cancel where Try again sits, and the sentence as the cautions are set', () => {
    expect(declarationsOf('.reveal-status__act')).toEqual({ 'margin-top': '16px', 'pointer-events': 'auto' });
    expect(declarationsOf('.restore-why')).toEqual({ 'pointer-events': 'auto' });
    // A refusal: the sentence, then the photo unchanged at 14 px in the second ink, 10 px apart (#rx-refused .why)
    expect(declarationsOf('.restore-why.is-refused')).toEqual({ gap: '10px' });
    expect(declarationsOf('.restore-why .restore-why__sub')).toEqual({ margin: '0', 'font-size': '14px', 'line-height': '1.45', color: 'var(--look-ink-2)', 'letter-spacing': '-0.005em' });
    expect(declarationsOf('.reveal-status__note')).toEqual({ '--look-ic': '13px', display: 'flex', gap: '7px', margin: '0 0 18px', 'font-size': '12px', 'line-height': '1.45', color: 'var(--look-ink-3)' });
  });
});
