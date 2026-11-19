// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { Arrival } from '../arrival.js';
import { EARLY_SOURCE_WORDS, WorkingScreen, type EarlyPose } from '../WorkingScreen.js';

// The lock-on's early state (D25, UIUX-lockon-READY.md point 1; the lk-early screen): while the solve job runs and an
// early pose exists, the predicted stars it places over the photo as amber dashed rings, and at the status place
// "Matching the stars.", where the pose came from, Cancel (D2) and the job's rows. The places come from the served
// predicted sky (earlyPose.ts, lockOnWired.test.ts); these tests give them by hand, as shares of the shown photo.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');

const BYTES = 2_382_523;
const stage = (name: string, unit: string, done: number, total: number | null) => ({ stage: name, unit, done, total });
const RUNNING = { id: 'job_0123456789abcdef', state: 'running', stages: [stage('store', 'bytes', BYTES, BYTES), stage('decode', 'frames', 1, 1), stage('detect', 'frames', 1, 1), stage('solve', 'candidates', 2080, 4160)] };
const arrival = (over: Partial<Arrival> = {}): Arrival =>
  ({ fileName: 'night.jpg', byteSize: BYTES, stored: true, widthPx: 3024, heightPx: 4032, job: RUNNING, failure: null, ...over }) as Arrival;
const RECT = { leftPx: 382.5, topPx: 0, widthPx: 675, heightPx: 900 };

const EARLY: EarlyPose = {
  source: 'pointing',
  marks: [
    { id: 'finding-predicted-star-hip-71683', name: 'Rigil Kentaurus', u: 0.5, v: 0.5 },
    { id: 'finding-predicted-star-hip-68702', name: 'Hadar', u: 0.62, v: 0.38 },
    { id: 'finding-predicted-star-hip-1', name: 'Outside', u: 1.4, v: 0.5 },
  ],
};

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
function show(props: Partial<Parameters<typeof WorkingScreen>[0]> = {}) {
  act(() => {
    root.render(createElement(WorkingScreen, { arrival: arrival(), photoRect: RECT, onTryAgain: () => {}, onUploadAnother: () => {}, onCancel: () => {}, ...props }));
  });
}

describe('the early state', () => {
  it('says what is happening and where the pose came from, then Cancel and the rows, in place of the size line', () => {
    show({ early: EARLY });
    const block = q('[data-testid="work-early"]')!;
    expect(q('.work-screen')!.className).toBe('work-screen is-early');
    expect([...block.children].map((el) => el.className)).toEqual(['work-early__lead', 'work-early__from', 'work-acts', 'work-steps']);
    expect(block.querySelector('.work-early__lead')!.textContent).toBe('Matching the stars.');
    expect(block.querySelector('.work-early__from')!.textContent).toBe('Placed from where your iPhone pointed: predicted until matched.');
    expect(block.querySelector('.work-acts button')!.textContent).toBe('Cancel');
    expect(q('.work-size')).toBeNull();
    // One set of rows and one Cancel only
    expect(container.querySelectorAll('.work-steps')).toHaveLength(1);
    expect(container.querySelectorAll('.work-cancel')).toHaveLength(1);
  });

  it('draws the marks inside the frame as still amber rings with names, and none outside it', () => {
    show({ early: EARLY });
    const marks = q('[data-testid="early-marks"]')!;
    expect(marks.className).toBe('align-layer is-still');
    expect(marks.querySelectorAll('circle')).toHaveLength(2);
    expect([...marks.querySelectorAll('.align-layer__name')].map((n) => n.textContent)).toEqual(['Rigil Kentaurus', 'Hadar']);
    // The centre of the frame
    const first = marks.querySelector('circle')!;
    expect(Number(first.getAttribute('cx'))).toBeCloseTo(337.5, 6);
    expect(Number(first.getAttribute('cy'))).toBeCloseTo(450, 6);
  });

  it('words every source as decided', () => {
    expect(EARLY_SOURCE_WORDS).toEqual({
      pointing: 'Placed from where your iPhone pointed: predicted until matched.',
      moon: 'Placed from the Moon: predicted until matched.',
      alignment: 'Placed from your alignment: predicted until matched.',
      compass: "Placed from your photo's compass heading and lens: predicted until matched.",
    });
    show({ early: { ...EARLY, source: 'moon' } });
    expect(q('.work-early__from')!.textContent).toBe('Placed from the Moon: predicted until matched.');
  });

  it('is the approved working screen with no early pose, and when the job stops', () => {
    show();
    expect(q('[data-testid="work-early"]')).toBeNull();
    expect(q('[data-testid="early-marks"]')).toBeNull();
    expect(q('.work-size')).not.toBeNull();
    show({ early: EARLY, arrival: arrival({ job: { ...RUNNING, state: 'failed', error: 'x' } as Arrival['job'], failure: { sentence: 'The solve stopped on an internal error.', canTryAgain: true } }) });
    expect(q('[data-testid="work-early"]')).toBeNull();
    expect(q('[data-testid="early-marks"]')).toBeNull();
    expect(q('.work-screen')!.className).toBe('work-screen is-failed');
  });
});

describe("the early state's rules are the lk-early screen's", () => {
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
  it('places the block at the status place, the lead at 24 px light, the source at 13 px, the rows 260 wide in its flow', () => {
    expect(declarationsOf('.work-early')).toMatchObject({ position: 'absolute', left: '36px', top: '70px', width: '300px' });
    expect(declarationsOf('.work-early__lead')).toEqual({ margin: '0 0 8px', font: '300 24px / 1.2 var(--look-font-sans)', 'letter-spacing': '-0.03em', color: 'var(--look-ink)' });
    expect(declarationsOf('.work-early__from')).toEqual({ margin: '0 0 16px', 'max-width': '300px', 'font-size': '13px', 'line-height': '1.45', color: 'var(--look-ink-2)' });
    expect(declarationsOf('.work-screen.is-early .work-steps')).toEqual({ position: 'static', width: '260px' });
    expect(declarationsOf('.align-layer.is-still')).toEqual({ cursor: 'default', 'pointer-events': 'none' });
  });
});
