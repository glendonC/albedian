// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LOOK_ICON_PATHS } from '../../look/index.js';
import type { Arrival } from '../arrival.js';
import { cornerPaths } from '../cornerPaths.js';
import { WorkingScreen } from '../WorkingScreen.js';

// The working screen (arrive, approved 2026-11-12, laptop screen 3) and the failed job on it (failed, decided
// 2026-11-12). Every size and colour is the approved pages' (#l-work in .cache/uiux/arrive/index.html, #w-failed in
// .cache/uiux/web1/index.html); every word is theirs or the job's own.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const motionCss = fs.readFileSync(path.resolve(here, '../../styles/motion.css'), 'utf8');

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

const BYTES = 2_382_523;
const stage = (name: string, unit: string, done: number, total: number | null) => ({ stage: name, unit, done, total });
const WORKING_JOB = {
  state: 'running',
  stages: [stage('store', 'bytes', BYTES, BYTES), stage('decode', 'frames', 1, 1), stage('detect', 'frames', 1, 1), stage('solve', 'candidates', 2080, 4160)],
};
function arrival(over: Partial<Arrival> = {}): Arrival {
  return { fileName: 'night.jpg', byteSize: BYTES, stored: true, widthPx: 3024, heightPx: 4032, job: WORKING_JOB, failure: null, ...over };
}
const FAILED = arrival({
  job: { ...WORKING_JOB, state: 'failed', error: 'The solve stopped on an internal error.' },
  failure: { sentence: 'The solve stopped on an internal error.', canTryAgain: true },
});
// The 3:4 sample's place at 1440 by 900, as the approved page draws it
const RECT = { leftPx: 382.5, topPx: 0, widthPx: 675, heightPx: 900 };

const noop = () => {};
function show(props: Partial<Parameters<typeof WorkingScreen>[0]> = {}) {
  act(() => {
    root.render(createElement(WorkingScreen, { arrival: arrival(), photoRect: RECT, onTryAgain: noop, onUploadAnother: noop, ...props }));
  });
}
const q = (selector: string) => container.querySelector(selector);
const rows = () => [...container.querySelectorAll('.work-step')];
const iconPath = (el: Element | null) => el?.querySelector('svg.look-i path')?.getAttribute('d') ?? null;

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

describe('the viewfinder corners at the exact frame', () => {
  it("draws the approved page's four corners for the sample's frame: 18 px inside the photo, 18 px legs", () => {
    expect(cornerPaths(RECT)).toEqual(['M400.5 36V18H418.5', 'M1039.5 36V18H1021.5', 'M400.5 864V882H418.5', 'M1039.5 864V882H1021.5']);
  });

  it('follows the photo wherever it is drawn', () => {
    expect(cornerPaths({ leftPx: 0, topPx: 118, widthPx: 390, heightPx: 520 })).toEqual([
      'M18 154V136H36',
      'M372 154V136H354',
      'M18 602V620H36',
      'M372 602V620H354',
    ]);
  });
});

describe('WorkingScreen: the approved working screen, word for word', () => {
  it('shows the logo at 21 px, the file size line and the five rows, and no other word', () => {
    show();
    const screen = q('[data-testid="working-screen"]')!;
    const logo = screen.querySelector('.look-logo') as HTMLElement;
    expect(logo.getAttribute('aria-label')).toBe('Albedian');
    expect(logo.style.getPropertyValue('--look-logo-fs')).toBe('21px');
    expect(q('.work-size')!.textContent).toBe('3024 × 4032 · 2.4 MB');
    expect(rows().map((row) => row.querySelector('.work-step__word')!.textContent)).toEqual(['Saved', 'Opened', 'Measured', 'Matching', 'Checking']);
    expect(screen.textContent).toBe('albedian3024 × 4032 · 2.4 MBSaved2.4 MBOpenedMeasuredMatching2,080 of 4,160Checking');
    // The logo is the one button: it returns to the home, and nothing is written on it
    expect([...screen.querySelectorAll('button')].map((b) => b.className)).toEqual(['work-word']);
    expect(q('.work-why')).toBeNull();
  });

  it('marks finished rows with a check, the row being worked with a filled dot, a row not started with a hollow dot', () => {
    show();
    const [saved, opened, measured, matching, checking] = rows();
    for (const row of [saved!, opened!, measured!]) {
      expect(row.className).toBe('work-step is-done');
      expect(iconPath(row.querySelector('.work-step__mark'))).toBe(LOOK_ICON_PATHS.check);
      expect(row.querySelector('.work-step__dot')).toBeNull();
    }
    expect(matching!.className).toBe('work-step is-now');
    expect(matching!.querySelector('.work-step__dot')).not.toBeNull();
    expect(matching!.querySelector('svg')).toBeNull();
    expect(matching!.getAttribute('aria-current')).toBe('step');
    expect(checking!.className).toBe('work-step is-next');
    expect(checking!.querySelector('.work-step__dot')).not.toBeNull();
    for (const row of [saved!, opened!, measured!, checking!]) expect(row.hasAttribute('aria-current')).toBe(false);
    // The marks are drawings: a screen reader hears the words and counts only
    for (const mark of container.querySelectorAll('.work-step__mark')) expect(mark.getAttribute('aria-hidden')).toBe('true');
  });

  it("writes each row's count, and draws the bar only on the row being worked, as wide as done over total", () => {
    show();
    expect(rows().map((row) => row.querySelector('.work-step__value')!.textContent)).toEqual(['2.4 MB', '', '', '2,080 of 4,160', '']);
    const bars = [...container.querySelectorAll('.work-step__bar')];
    expect(bars).toHaveLength(1);
    expect(bars[0]!.closest('.work-step')).toBe(rows()[3]);
    expect((bars[0]!.firstElementChild as HTMLElement).style.width).toBe('50%');
  });

  it('draws the bar from the real fraction, to a thousandth of a percent', () => {
    show({ arrival: arrival({ job: { state: 'running', stages: [stage('store', 'bytes', BYTES, BYTES), stage('decode', 'frames', 1, 3)] } }) });
    expect((q('.work-step__bar')!.firstElementChild as HTMLElement).style.width).toBe('33.333%');
  });

  it('draws no bar while a total is not known, and the time passed in the count\'s place (C19 item 1)', () => {
    show({ arrival: arrival({ job: { state: 'running', stages: [stage('store', 'bytes', BYTES, BYTES), stage('decode', 'frames', 9, null)] } }) });
    expect(q('.work-step__bar')).toBeNull();
    expect(rows()[1]!.className).toBe('work-step is-now');
    expect(rows()[1]!.querySelector('.work-step__value')!.textContent).toBe('0 s');
  });

  it('draws the four corners at the photo, and none while there is no photo on screen', () => {
    show();
    const paths = [...container.querySelectorAll('svg.photo-corners path')].map((p) => p.getAttribute('d'));
    expect(paths).toEqual(cornerPaths(RECT));
    expect(q('svg.photo-corners')!.getAttribute('aria-hidden')).toBe('true');
    show({ photoRect: null });
    expect(q('svg.photo-corners')).toBeNull();
  });

  it('shows the bytes alone, and Saved as the row being worked, while the file is being sent', () => {
    show({ arrival: arrival({ stored: false, widthPx: null, heightPx: null, job: null }), photoRect: null });
    expect(q('.work-size')!.textContent).toBe('2.4 MB');
    expect(rows().map((row) => row.className)).toEqual(['work-step is-now', 'work-step is-next', 'work-step is-next', 'work-step is-next', 'work-step is-next']);
  });
});

describe('WorkingScreen: the logo returns to the home', () => {
  it('is a real button named Albedian that tells its listener once per press, while working and after a failure', () => {
    const onHome = vi.fn();
    show({ onHome });
    const logo = q('[data-testid="working-home"]') as HTMLButtonElement;
    expect(logo.tagName).toBe('BUTTON');
    expect(logo.type).toBe('button');
    expect(logo.querySelector('.look-logo')!.getAttribute('aria-label')).toBe('Albedian');
    act(() => logo.click());
    expect(onHome).toHaveBeenCalledTimes(1);
    show({ arrival: FAILED, onHome });
    act(() => (q('[data-testid="working-home"]') as HTMLButtonElement).click());
    expect(onHome).toHaveBeenCalledTimes(2);
  });
});

describe('WorkingScreen: a failed job (decided 2026-11-12)', () => {
  it("says the job's own sentence above the rows as an alert, with Try again and Upload another", () => {
    show({ arrival: FAILED });
    const why = q('.work-why')!;
    expect(why.getAttribute('role')).toBe('alert');
    expect(why.querySelector('p')!.textContent).toBe('The solve stopped on an internal error.');
    const again = why.querySelector('button.look-main')!;
    expect(again.textContent).toBe('Try again');
    expect(again.classList.contains('look-size-compact')).toBe(true);
    expect(iconPath(again)).toBe(LOOK_ICON_PATHS['arrow-clockwise']);
    expect(again.querySelector('svg.look-i')!.classList.contains('look-turn')).toBe(false);
    const another = why.querySelector('button.look-second')!;
    expect(another.textContent).toBe('Upload another');
    expect(why.querySelectorAll('button')).toHaveLength(2);
    expect(container.querySelectorAll('button')).toHaveLength(3);
    expect(q('[data-testid="working-screen"]')!.textContent).toBe(
      'albedian3024 × 4032 · 2.4 MBThe solve stopped on an internal error.Try againUpload anotherSaved2.4 MBOpenedMeasuredMatching2,080 of 4,160Checking'
    );
  });

  it('marks the stage it stopped at with an x, keeps its count and bar, and leaves the later stages not started', () => {
    show({ arrival: FAILED });
    const [, , , matching, checking] = rows();
    expect(matching!.className).toBe('work-step is-failed');
    expect(iconPath(matching!.querySelector('.work-step__mark'))).toBe(LOOK_ICON_PATHS.x);
    expect(matching!.querySelector('.work-step__value')!.textContent).toBe('2,080 of 4,160');
    expect((matching!.querySelector('.work-step__bar')!.firstElementChild as HTMLElement).style.width).toBe('50%');
    expect(matching!.hasAttribute('aria-current')).toBe(false);
    expect(checking!.className).toBe('work-step is-next');
  });

  it('starts the same work again once per press of Try again', () => {
    const onTryAgain = vi.fn();
    show({ arrival: FAILED, onTryAgain });
    act(() => (q('button.look-main') as HTMLButtonElement).click());
    expect(onTryAgain).toHaveBeenCalledTimes(1);
  });

  it('drops Try again when the same work cannot start again, and keeps Upload another', () => {
    show({ arrival: { ...FAILED, failure: { sentence: 'The solve stopped on an internal error.', canTryAgain: false } } });
    expect(q('button.look-main')).toBeNull();
    expect(q('button.look-second')!.textContent).toBe('Upload another');
  });

  it('opens its own picker from Upload another, hands the chosen file on once, and empties the picker', () => {
    const onUploadAnother = vi.fn();
    const click = vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {});
    show({ arrival: FAILED, onUploadAnother });
    const input = q('[data-testid="working-file-input"]') as HTMLInputElement;
    expect(input.type).toBe('file');
    expect(input.accept).toBe('image/*,video/*');
    expect(input.tabIndex).toBe(-1);
    expect(input.getAttribute('aria-hidden')).toBe('true');
    act(() => (q('button.look-second') as HTMLButtonElement).click());
    expect(click).toHaveBeenCalledTimes(1);
    const file = new File([new Uint8Array([0xff, 0xd8])], 'other.jpg', { type: 'image/jpeg' });
    const written: string[] = [];
    Object.defineProperty(input, 'value', { configurable: true, get: () => '', set: (v: string) => void written.push(v) });
    Object.defineProperty(input, 'files', { value: [file], configurable: true });
    act(() => {
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(onUploadAnother).toHaveBeenCalledTimes(1);
    expect(onUploadAnother).toHaveBeenCalledWith(file);
    expect(written).toEqual(['']);
  });

  it('chooses nothing when the picker closes with no file', () => {
    const onUploadAnother = vi.fn();
    show({ arrival: FAILED, onUploadAnother });
    const input = q('[data-testid="working-file-input"]') as HTMLInputElement;
    Object.defineProperty(input, 'files', { value: [], configurable: true });
    act(() => {
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(onUploadAnother).not.toHaveBeenCalled();
  });

  it('has no picker while nothing has failed: the working screen offers no other upload', () => {
    show();
    expect(q('[data-testid="working-file-input"]')).toBeNull();
  });
});

describe("the working screen's rules are the approved pages'", () => {
  it('places the logo, the size line and the rows where the approved page has them', () => {
    expect(declarationsOf('.work-word')).toEqual({ all: 'unset', position: 'absolute', left: '36px', top: '30px', cursor: 'pointer', 'pointer-events': 'auto' });
    expect(declarationsOf('.work-word:focus-visible')).toEqual({
      outline: 'var(--look-focus-width) solid var(--look-ink)',
      'outline-offset': 'var(--look-focus-offset)',
    });
    expect(declarationsOf('.work-size')).toEqual({
      position: 'absolute',
      left: '36px',
      top: '66px',
      margin: '0',
      font: '400 13px / 1.55 var(--look-font-sans)',
      'letter-spacing': '-0.005em',
      color: 'var(--look-ink-2)',
      'font-variant-numeric': 'tabular-nums',
    });
    expect(declarationsOf('.work-steps')).toEqual({
      position: 'absolute',
      left: '36px',
      bottom: '40px',
      width: '300px',
      display: 'grid',
      gap: '4px',
      margin: '0',
      padding: '0',
      'list-style': 'none',
    });
  });

  it('lays a row out as a 24 px mark, the word, and the count at the right, 15 px type with even figures', () => {
    expect(declarationsOf('.work-step')).toEqual({
      display: 'grid',
      'grid-template-columns': '24px 1fr auto',
      'grid-template-rows': '30px auto',
      'align-items': 'center',
      font: '400 15px / 1 var(--look-font-sans)',
      'letter-spacing': '-0.01em',
      'font-variant-numeric': 'tabular-nums',
    });
    expect(declarationsOf('.work-step__mark')).toEqual({ '--look-ic': '14px', display: 'grid', 'place-items': 'start', color: 'var(--look-ink-2)' });
    expect(declarationsOf('.work-step__dot')).toEqual({
      width: '7px',
      height: '7px',
      'margin-left': '3px',
      'box-sizing': 'border-box',
      'border-radius': '50%',
      border: '1px solid var(--look-ink-3)',
    });
  });

  it('colours each state as approved: done quieter, not started quietest, the worked row in ink with a filled dot', () => {
    expect(declarationsOf('.work-step.is-done')).toEqual({ color: 'var(--look-ink-2)' });
    expect(declarationsOf('.work-step.is-next')).toEqual({ color: 'var(--look-ink-3)' });
    expect(declarationsOf('.work-step.is-now .work-step__dot')).toEqual({ background: 'var(--look-ink)', 'border-color': 'var(--look-ink)' });
    expect(declarationsOf('.work-step.is-failed')).toEqual({ color: 'var(--look-ink)' });
    expect(declarationsOf('.work-step.is-failed .work-step__mark')).toEqual({ color: 'var(--look-ink)' });
  });

  it('draws the bar 2 px high under the word and the count, in ink, and grey on a stopped row', () => {
    expect(declarationsOf('.work-step__bar')).toEqual({
      'grid-column': '2 / 4',
      height: '2px',
      margin: '2px 0 8px',
      overflow: 'hidden',
      'border-radius': '2px',
      background: 'var(--look-hair)',
    });
    expect(declarationsOf('.work-step__bar b')).toEqual({ display: 'block', height: '100%', background: 'var(--look-ink)' });
    expect(declarationsOf('.work-step.is-failed .work-step__bar b')).toEqual({ background: 'var(--look-ink-3)' });
  });

  it("sets the failed job's sentence in Geist 300 at 24 px above the rows, the two buttons 10 px apart, the size line 4 px lower", () => {
    expect(declarationsOf('.work-why')).toEqual({
      position: 'absolute',
      left: '36px',
      bottom: '262px',
      width: '300px',
      display: 'grid',
      gap: '18px',
      'justify-items': 'start',
    });
    expect(declarationsOf('.work-why p')).toEqual({
      margin: '0',
      font: '300 24px / 1.2 var(--look-font-sans)',
      'letter-spacing': '-0.03em',
      'text-wrap': 'balance',
    });
    expect(declarationsOf('.work-acts')).toEqual({ display: 'flex', gap: '10px' });
    expect(declarationsOf('.work-acts .look-main')).toEqual({ '--look-ic': '15px' });
    expect(declarationsOf('.work-screen.is-failed .work-size')).toEqual({ top: '70px' });
  });

  it('lets the pointer through to the photo everywhere but its own buttons, which take it', () => {
    expect(declarationsOf('.work-screen')).toMatchObject({ position: 'fixed', inset: '0', 'pointer-events': 'none' });
    expect(declarationsOf('.work-acts .look-main, .work-acts .look-second')).toEqual({ 'pointer-events': 'auto' });
    expect(declarationsOf('.work-word')['pointer-events']).toBe('auto');
  });

  it("draws the corners in the approved page's stroke", () => {
    expect(declarationsOf('.photo-corners')).toEqual({
      position: 'fixed',
      inset: '0',
      width: '100%',
      height: '100%',
      'pointer-events': 'none',
    });
    expect(declarationsOf('.photo-corners path')).toEqual({ fill: 'none', stroke: 'rgba(244, 243, 239, 0.7)', 'stroke-width': '1.2' });
  });

  it('keeps its layout rules still: every movement of the working screen is in motion.css', () => {
    const rules = css.replace(/\/\*[\s\S]*?\*\//g, '').match(/\.(work|photo)-[^{]*\{[^}]*\}/g) ?? [];
    expect(rules.length).toBeGreaterThanOrEqual(20);
    for (const rule of rules) expect(rule).not.toMatch(/transition|animation/);
  });

  // The motion record (MOTION-READY-web.md, "Working rows" and "Failed, canceled, interrupted job")
  const motionOf = (selector: string): Record<string, string> => {
    const found: Record<string, string> = {};
    postcss.parse(motionCss).walkRules((rule) => {
      if (rule.selector.replace(/\s+/g, ' ') !== selector || rule.parent?.type !== 'root') return;
      rule.walkDecls((decl) => {
        found[decl.prop] = decl.value.replace(/\s+/g, ' ');
      });
    });
    return found;
  };
  const WORDS_IN = 'look-mo-words-in var(--look-mo-dur-words-in) var(--look-mo-ease-std)';

  it('brings the size line in, then each row 20 ms after the one above, and changes a finished step in the colour time', () => {
    expect(motionOf('.work-size')).toEqual({ animation: `${WORDS_IN} var(--look-mo-delay-words-in) both` });
    expect(motionOf('.work-step')).toEqual({
      animation: `${WORDS_IN} calc(var(--look-mo-delay-words-in) + var(--look-mo-stagger) * (var(--work-i) + 1)) both`,
      transition: 'color var(--look-mo-dur-colour) var(--look-mo-ease-std)',
    });
  });

  it("moves a stage's bar to each new report in 150 ms (MOTION-READY-web.md, Progress), never pulsing or counting", () => {
    expect(motionOf('.work-step__bar b')).toEqual({ transition: 'width var(--look-mo-dur-bar) var(--look-mo-ease-std)' });
    expect(motionCss).not.toMatch(/work-step__bar[^{]*\{[^}]*animation/);
  });

  it("brings a stopped or waiting job's sentence in, then its buttons 20 ms later", () => {
    expect(motionOf('.work-why p')).toEqual({ animation: `${WORDS_IN} both` });
    expect(motionOf('.work-why .work-acts')).toEqual({ animation: `${WORDS_IN} var(--look-mo-stagger) both` });
  });

  it('numbers each row for its place in the stagger, and loads the motion with the screen', () => {
    show();
    rows().forEach((row, i) => expect((row as HTMLElement).style.getPropertyValue('--work-i')).toBe(String(i)));
    const source = fs.readFileSync(path.resolve(here, '../WorkingScreen.tsx'), 'utf8');
    expect(source).toContain("import '../styles/motion.css';");
  });
});

// A job that waits, is canceled or was left unfinished (UIUX-jobs-QUESTION.md, D2, INTERIM): Cancel sits where Try
// again sits on a failed job; a queued job says "Queued" and its own count of jobs ahead; once pressed, Cancel stays
// pressed and greyed until the job has really stopped. A canceled or interrupted job is the failed job's screen.
describe('Cancel on a job that waits or works (D2, INTERIM)', () => {
  const QUEUED = arrival({ job: { id: 'job_a', state: 'queued', queueAhead: 1, stages: [stage('store', 'bytes', BYTES, BYTES)] } });
  const cancelButton = () => [...container.querySelectorAll('.work-acts button')].find((b) => b.textContent === 'Cancel') as HTMLButtonElement | undefined;

  it('says "Queued" and the jobs ahead, with Cancel under it and no Try again', () => {
    show({ arrival: QUEUED, onCancel: noop });
    expect(q('.work-why p')!.textContent).toBe('Queued · 1 ahead');
    expect(q('.work-why p span')!.textContent).toBe(' · 1 ahead');
    expect(cancelButton()!.className).toContain('work-cancel');
    expect([...container.querySelectorAll('.work-acts button')].map((b) => b.textContent)).toEqual(['Cancel']);
    expect(q('.work-screen')!.className).toBe('work-screen');
  });

  it('says only "Queued" when no job is ahead', () => {
    show({ arrival: arrival({ job: { ...QUEUED.job!, queueAhead: 0 } }), onCancel: noop });
    expect(q('.work-why p')!.textContent).toBe('Queued');
  });

  it('puts Cancel alone above the rows while the job works', () => {
    show({ arrival: arrival({ job: { ...WORKING_JOB, id: 'job_a' } }), onCancel: noop });
    expect(q('.work-why p')).toBeNull();
    expect(cancelButton()).toBeDefined();
  });

  it('asks once, then holds Cancel pressed and greyed until the job stops', async () => {
    const onCancel = vi.fn(() => new Promise<void>(() => {}));
    show({ arrival: arrival({ job: { ...WORKING_JOB, id: 'job_a' } }), onCancel });
    act(() => cancelButton()!.click());
    act(() => cancelButton()!.click());
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(cancelButton()!.className).toContain('is-asked');
    expect(cancelButton()!.getAttribute('aria-disabled')).toBe('true');
  });

  it('shows it held when the job record says a cancel was asked, and lets it go if the server refuses', async () => {
    show({ arrival: arrival({ job: { ...WORKING_JOB, id: 'job_a', cancelRequestedAtUTC: '2026-11-19T05:00:00Z' } }), onCancel: noop });
    expect(cancelButton()!.className).toContain('is-asked');
    let refuse: (e: Error) => void = () => {};
    const onCancel = vi.fn(() => new Promise<void>((_, reject) => (refuse = reject)));
    show({ arrival: arrival({ job: { ...WORKING_JOB, id: 'job_b' } }), onCancel });
    act(() => cancelButton()!.click());
    expect(cancelButton()!.className).toContain('is-asked');
    await act(async () => {
      refuse(new Error('no solve job with that id is stored'));
      await Promise.resolve();
    });
    expect(cancelButton()!.className).not.toContain('is-asked');
  });

  it('has no Cancel before the job exists, once it stopped, or with no way to cancel', () => {
    show({ arrival: arrival({ job: null, stored: false }), onCancel: noop });
    expect(cancelButton()).toBeUndefined();
    show({ arrival: FAILED, onCancel: noop });
    expect(cancelButton()).toBeUndefined();
    show({ arrival: arrival({ job: { ...WORKING_JOB, id: 'job_a' } }) });
    expect(cancelButton()).toBeUndefined();
  });

  it('shows a canceled job as the failed job, its word "Canceled" with Try again and Upload another', () => {
    show({ arrival: arrival({ job: { ...WORKING_JOB, state: 'canceled' }, failure: { sentence: 'Canceled', canTryAgain: true } }), onCancel: noop });
    expect(q('.work-why p')!.textContent).toBe('Canceled');
    expect([...container.querySelectorAll('.work-acts button')].map((b) => b.textContent)).toEqual(['Try again', 'Upload another']);
  });

  // UIUX-jobs-DECIDED-BY-LEAD.md: "the pill stays pressed and greyed until the job has really stopped", at every width:
  // the laptop's Cancel in .work-acts, the phone working screen's in .work-phone-go and the phone restoration's in
  // .restore-phone-go (UIUX's live read of main dd6eeeeb found the phone's two never greyed; the lead's 05:02 answer)
  it("sets the count ahead in the third ink and a held Cancel as the record draws it, at every width", () => {
    expect(declarationsOf('.work-why p span')).toEqual({ color: 'var(--look-ink-3)' });
    for (const held of ['.work-acts .look-second.is-asked', '.work-phone-go .look-second.is-asked', '.restore-phone-go .look-second.is-asked']) {
      const found: Record<string, string> = {};
      postcss.parse(css).walkRules((rule) => {
        if (rule.parent?.type !== 'root' || !rule.selectors.map((s) => s.replace(/\s+/g, ' ')).includes(held)) return;
        rule.walkDecls((decl) => void (found[decl.prop] = decl.value.replace(/\s+/g, ' ')));
      });
      expect(found, held).toEqual({
        background: 'var(--look-wash)',
        color: 'var(--look-ink-3)',
        'border-color': 'var(--look-hair)',
        cursor: 'default',
      });
    }
  });
});

// D2 at phone width (UIUX-jobs-READY.md, decided by the lead 06:11; jp-run, jp-queued): the photo in the reveal's slot,
// a close circle top left, and only the step the job is on under the photo, with Cancel compact where Ask out loud sits
describe('the working screen at phone width (D2)', () => {
  const phone = (over: Partial<Arrival> = {}, props: Partial<Parameters<typeof WorkingScreen>[0]> = {}) =>
    show({ arrival: arrival(over), phone: true, onCancel: noop, ...props });

  it('shows only the step the job is on: its word large, its count, its bar, and Cancel at the phone size', () => {
    phone({ job: { ...WORKING_JOB, id: 'job_a' } });
    expect(q('.work-screen')!.className).toBe('work-screen is-phone');
    expect(q('.work-phone-row > b')!.textContent).toBe('Matching');
    expect(q('.work-phone-row__v')!.textContent).toBe('2,080 of 4,160');
    expect((q('.work-phone-row__bar b') as HTMLElement).style.width).toBe('50%');
    const cancel = q('.work-phone-go button') as HTMLButtonElement;
    expect(cancel.textContent).toBe('Cancel');
    expect(cancel.className).toContain('look-size-phone');
    for (const gone of ['.work-steps', '.work-size', '[data-testid="working-home"]', 'svg.photo-corners']) expect(q(gone), gone).toBeNull();
  });

  it('says "Queued" and the jobs ahead in place of the step while the job waits', () => {
    phone({ job: { id: 'job_a', state: 'queued', queueAhead: 1, stages: [stage('store', 'bytes', BYTES, BYTES)] } });
    expect(q('.work-phone-row > b')!.textContent).toBe('Queued · 1 ahead');
    expect(q('.work-phone-row > b span')!.textContent).toBe(' · 1 ahead');
    expect(q('.work-phone-row__bar')).toBeNull();
    expect(q('.work-phone-go button')!.textContent).toBe('Cancel');
  });

  it('draws no bar for a step with nothing to count, and goes home from the close circle', () => {
    const onHome = vi.fn();
    phone({ job: { ...WORKING_JOB, id: 'job_a', stages: [...WORKING_JOB.stages, stage('verify', 'steps', 0, null)] } }, { onHome });
    expect(q('.work-phone-row > b')!.textContent).toBe('Checking');
    expect(q('.work-phone-row__bar')).toBeNull();
    act(() => (q('.work-phone-top button') as HTMLButtonElement).click());
    expect(onHome).toHaveBeenCalledTimes(1);
  });

  it('sets the row and the button as jp-run does', () => {
    expect(declarationsOf('.work-phone-top')).toEqual({ position: 'absolute', left: '20px', top: '60px', height: '44px', display: 'flex', 'align-items': 'center' });
    expect(declarationsOf('.work-phone-row')).toEqual({
      position: 'absolute',
      left: '24px',
      right: '24px',
      top: '668px',
      display: 'grid',
      'grid-template-columns': '1fr auto',
      'row-gap': '12px',
      'align-items': 'baseline',
      'font-variant-numeric': 'tabular-nums',
    });
    expect(declarationsOf('.work-phone-row > b')).toEqual({ font: '300 28px / 1 var(--look-font-sans)', 'letter-spacing': '-0.035em' });
    expect(declarationsOf('.work-phone-row > b span')).toEqual({ color: 'var(--look-ink-3)' });
    expect(declarationsOf('.work-phone-row__v')).toEqual({ font: '400 15px / 1 var(--look-font-sans)', 'letter-spacing': '-0.01em', color: 'var(--look-ink-2)' });
    expect(declarationsOf('.work-phone-row__bar')).toEqual({ 'grid-column': '1 / 3', height: '2px', background: 'var(--look-hair)', 'border-radius': '2px', overflow: 'hidden' });
    expect(declarationsOf('.work-phone-row__bar b')).toEqual({ display: 'block', height: '100%', background: 'var(--look-ink)' });
    expect(declarationsOf('.work-phone-go')).toEqual({
      position: 'absolute',
      left: '0',
      right: '0',
      top: '772px',
      display: 'flex',
      'justify-content': 'center',
      gap: '10px',
    });
  });
});

// D37, a stopped job at phone width (UIUX-webuxpieces-READY.md item 1, decided by the lead 12:59 on 21 Nov; sp-failed,
// sp-canceled, sp-interrupted, sp-noretry in .cache/uiux/stopped-phone): D2's row stays where it ran with A1's x before
// the step it stopped at, the job's own sentence above it as D19 anchors the phone answer, the photo's lower edge
// darkened as D19 does, and Try again with Upload another where Cancel was. Nothing new is drawn.
describe('a stopped job at phone width (D37)', () => {
  const STOPPED = arrival({
    job: { ...WORKING_JOB, state: 'failed', stages: [...WORKING_JOB.stages.slice(0, 3), stage('solve', 'candidates', 14, 364)] },
    failure: { sentence: 'The solve stopped on an internal error.', canTryAgain: true },
  });
  const buttons = () => [...container.querySelectorAll('.work-phone-go button')] as HTMLButtonElement[];

  it("keeps the row it stopped at, A1's x first, its last count and its bar, under the job's own sentence as an alert", () => {
    show({ arrival: STOPPED, phone: true });
    expect(q('.work-screen')!.className).toBe('work-screen is-phone');
    const row = q('.work-phone-row')!;
    expect(row.className).toBe('work-phone-row is-stopped');
    expect([...row.children].map((child) => child.className || child.tagName)).toEqual(['work-phone-row__x', 'B', 'work-phone-row__v', 'work-phone-row__bar']);
    expect(iconPath(q('.work-phone-row__x'))).toBe(LOOK_ICON_PATHS.x);
    expect(q('.work-phone-row__x')!.getAttribute('aria-hidden')).toBe('true');
    expect(q('.work-phone-row > b')!.textContent).toBe('Matching');
    expect(q('.work-phone-row__v')!.textContent).toBe('14 of 364');
    expect((q('.work-phone-row__bar b') as HTMLElement).style.width).toBe('3.846%');
    const why = q('.work-phone-why')!;
    expect(why.textContent).toBe('The solve stopped on an internal error.');
    expect(why.getAttribute('role')).toBe('alert');
    // The sentence comes before the row, so it is read first
    expect(why.compareDocumentPosition(row) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(q('.work-phone-fade')!.getAttribute('aria-hidden')).toBe('true');
    expect(q('.work-phone-top button')!.getAttribute('aria-label')).toBe('Close');
  });

  it('puts Try again and Upload another where Cancel was, at the phone size, each doing its own work', () => {
    const onTryAgain = vi.fn();
    const onUploadAnother = vi.fn();
    show({ arrival: STOPPED, phone: true, onTryAgain, onUploadAnother, onCancel: noop });
    expect(buttons().map((b) => b.textContent)).toEqual(['Try again', 'Upload another']);
    const [again, other] = buttons();
    expect(again!.className).toContain('look-main');
    expect(again!.className).toContain('look-size-phone');
    expect(iconPath(again!)).toBe(LOOK_ICON_PATHS['arrow-clockwise']);
    expect(other!.className).toContain('look-second');
    expect(other!.className).toContain('look-size-phone');
    act(() => again!.click());
    expect(onTryAgain).toHaveBeenCalledTimes(1);
    const input = q('[data-testid="working-file-input"]') as HTMLInputElement;
    const picked = vi.spyOn(input, 'click');
    act(() => other!.click());
    expect(picked).toHaveBeenCalledTimes(1);
    const file = new File(['x'], 'another.jpg', { type: 'image/jpeg' });
    Object.defineProperty(input, 'files', { value: [file], configurable: true });
    act(() => {
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(onUploadAnother).toHaveBeenCalledWith(file);
    expect(container.textContent).not.toContain('Cancel');
  });

  it('leaves Try again out when the runner does not allow it, so Upload another stands alone', () => {
    show({ arrival: { ...STOPPED, failure: { sentence: 'The solve stopped on an internal error.', canTryAgain: false } }, phone: true });
    expect(buttons().map((b) => b.textContent)).toEqual(['Upload another']);
  });

  it('says a canceled or interrupted job in its own words, over the same row', () => {
    show({ arrival: { ...STOPPED, job: { ...STOPPED.job!, state: 'canceled' }, failure: { sentence: 'Canceled', canTryAgain: true } }, phone: true });
    expect(q('.work-phone-why')!.textContent).toBe('Canceled');
    expect(q('.work-phone-row > b')!.textContent).toBe('Matching');
    const interrupted = 'The server stopped before this job finished.';
    show({ arrival: { ...STOPPED, job: { ...STOPPED.job!, state: 'interrupted' }, failure: { sentence: interrupted, canTryAgain: true } }, phone: true });
    expect(q('.work-phone-why')!.textContent).toBe(interrupted);
    expect(buttons().map((b) => b.textContent)).toEqual(['Try again', 'Upload another']);
  });

  it('draws no bar on a stopped step that had nothing to count, as the running row does', () => {
    show({ arrival: { ...STOPPED, job: { ...STOPPED.job!, stages: [...WORKING_JOB.stages, stage('verify', 'steps', 0, null)] } }, phone: true });
    expect(q('.work-phone-row > b')!.textContent).toBe('Checking');
    expect(q('.work-phone-row__bar')).toBeNull();
  });

  it('sets the sentence, the x, the grey bar and the darkened edge as sp-failed does', () => {
    expect(declarationsOf('.work-phone-why')).toEqual({
      position: 'absolute',
      left: '24px',
      right: '24px',
      bottom: 'calc(100% - 654px)',
      margin: '0',
      font: '300 22px / 1.2 var(--look-font-sans)',
      'letter-spacing': '-0.03em',
      'text-wrap': 'balance',
      color: 'var(--look-ink)',
    });
    expect(declarationsOf('.work-phone-row.is-stopped')).toEqual({ 'grid-template-columns': 'auto 1fr auto', 'column-gap': '8px' });
    expect(declarationsOf('.work-phone-row__x')).toEqual({ '--look-ic': '14px', 'align-self': 'center', display: 'flex', color: 'var(--look-ink)' });
    expect(declarationsOf('.work-phone-row.is-stopped .work-phone-row__bar')).toEqual({ 'grid-column': '1 / 4' });
    expect(declarationsOf('.work-phone-row.is-stopped .work-phone-row__bar b')).toEqual({ background: 'var(--look-ink-3)' });
    expect(declarationsOf('.work-phone-fade')).toEqual({
      position: 'absolute',
      left: '0',
      right: '0',
      top: '594px',
      height: '44px',
      background: 'linear-gradient(to bottom, rgba(0, 0, 0, 0), rgba(0, 0, 0, 0.82))',
      'pointer-events': 'none',
    });
  });
});
