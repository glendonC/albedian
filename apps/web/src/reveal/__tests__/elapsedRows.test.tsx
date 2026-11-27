// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { UploadJob } from '@albedian/contracts';
import { elapsedWords } from '../elapsedRows.js';
import type { Arrival } from '../arrival.js';
import { WorkingScreen } from '../WorkingScreen.js';
import { RestorePhoneWait, RestoreSteps } from '../RestoreParts.js';

// C19 item 1 (UIUX-walkasks-READY.md, decided 12:59): a working row with no total shows the time passed in the count's
// place, "12 s" then "1 min 5 s", from when that step began, once a second, with no bar; a count replaces it at once.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  vi.useFakeTimers();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});
const stage = (name: string, unit: string, done: number, total: number | null) => ({ stage: name, unit, done, total });
const BYTES = 2_382_523;
const arrival = (stages: ReturnType<typeof stage>[]): Arrival =>
  ({ fileName: 'night.jpg', byteSize: BYTES, stored: true, widthPx: 3024, heightPx: 4032, job: { id: 'job_0123456789abcdef', state: 'running', stages }, failure: null }) as Arrival;
const searching = [stage('store', 'bytes', BYTES, BYTES), stage('decode', 'frames', 1, 1), stage('detect', 'frames', 1, 1), stage('solve', 'candidates', 0, null)];
const counted = [stage('store', 'bytes', BYTES, BYTES), stage('decode', 'frames', 1, 1), stage('detect', 'frames', 1, 1), stage('solve', 'candidates', 2080, 4160)];
const values = () => [...container.querySelectorAll('.work-step__value')].map((v) => v.textContent);
const show = (a: Arrival) => act(() => root.render(createElement(WorkingScreen, { arrival: a, photoRect: null, onTryAgain: () => {}, onUploadAnother: () => {} })));

describe('elapsedWords', () => {
  it('writes seconds under a minute, then minutes and seconds', () => {
    expect([0, 12, 59.9, 60, 65, 600].map(elapsedWords)).toEqual(['0 s', '12 s', '59 s', '1 min 0 s', '1 min 5 s', '10 min 0 s']);
  });
});

describe('the time passed on a working row with no total', () => {
  it('counts the Matching row from when it began, once a second, with no bar, until the job reports a count', () => {
    show(arrival(searching));
    expect(values().at(-2)).toBe('0 s');
    act(() => vi.advanceTimersByTime(12_000));
    expect(values().at(-2)).toBe('12 s');
    act(() => vi.advanceTimersByTime(53_000));
    expect(values().at(-2)).toBe('1 min 5 s');
    expect(container.querySelectorAll('.work-step.is-now .work-step__bar b')).toHaveLength(0);
    show(arrival(counted));
    expect(values().at(-2)).toBe('2,080 of 4,160');
  });

  // FIELD's walk-0410, item 4: a still's one frame is nothing to count (D10), so its Measured row times itself
  it("times a still's Measured row, never Measured 0 of 1", () => {
    show(arrival([stage('store', 'bytes', BYTES, BYTES), stage('decode', 'frames', 1, 1), stage('detect', 'frames', 0, 1)]));
    const now = () => container.querySelector('.work-step.is-now .work-step__value')?.textContent;
    expect(now()).toBe('0 s');
    act(() => vi.advanceTimersByTime(9_000));
    expect(now()).toBe('9 s');
    expect(container.querySelectorAll('.work-step.is-now .work-step__bar b')).toHaveLength(0);
  });

  it("times the restoration's steps the same way", () => {
    const job = { id: 'job_7e6f5a4b3c2d1e0f9a8b7c6d', kind: 'restoration', state: 'running', stages: [stage('decode', 'frames', 1, 1), stage('measure', 'frames', 0, null)] } as unknown as UploadJob;
    act(() => root.render(createElement(RestoreSteps, { job })));
    const now = () => container.querySelector('.work-step.is-now .work-step__value')?.textContent;
    expect(now()).toBe('0 s');
    act(() => vi.advanceTimersByTime(7_000));
    expect(now()).toBe('7 s');
  });

  // AUDIT-C's gap on elapsed-2 (.lead/kiro/verdicts/web-ux-elapsed-2-c25-nearblack.md): Try again on a stopped
  // restoration starts a new job, which the steps, staying mounted, time from its own start
  it("times a restoration's Try again from the new job's own start", () => {
    const job = { id: 'job_7e6f5a4b3c2d1e0f9a8b7c6d', kind: 'restoration', state: 'running', stages: [stage('decode', 'frames', 1, 1), stage('measure', 'frames', 0, null)] } as unknown as UploadJob;
    act(() => root.render(createElement(RestoreSteps, { job })));
    const now = () => container.querySelector('.work-step.is-now .work-step__value')?.textContent;
    act(() => vi.advanceTimersByTime(9_000));
    expect(now()).toBe('9 s');
    act(() => root.render(createElement(RestoreSteps, { job: { ...job, id: 'job_0f9e8d7c6b5a4f3e2d1c0b9a' } as UploadJob })));
    expect(now()).toBe('0 s');
  });

  it('keeps counting a step from its start when it leaves the timed state for a record and comes back (FIELD walk-0620)', () => {
    show(arrival(searching));
    act(() => vi.advanceTimersByTime(1_000));
    expect(values().at(-2)).toBe('1 s');
    // A record with a count, then one with none again, as between the pose try and the search
    show(arrival(counted));
    act(() => vi.advanceTimersByTime(2_000));
    show(arrival(searching));
    expect(values().at(-2)).toBe('3 s');
    act(() => vi.advanceTimersByTime(4_000));
    expect(values().at(-2)).toBe('7 s');
  });

  it('times a new job from its own start', () => {
    show(arrival(searching));
    act(() => vi.advanceTimersByTime(9_000));
    expect(values().at(-2)).toBe('9 s');
    const again = arrival(searching);
    show({ ...again, job: { ...again.job!, id: 'job_fedcba9876543210' } } as Arrival);
    expect(values().at(-2)).toBe('0 s');
  });
});

// C19 item 1a (UIUX-walkasks-READY.md, DECIDED-BY-LEAD 12:28 on 27 Nov; EXPORT's walk saw "Measured 7 of 8" stand 15 s
// with nothing on the row moving): a working row that reports a total, whose count has not moved for 10 s, shows the
// seconds passed since its step began, in item 1's words and type, 12 px left of its count, the count's right edge
// unchanged; once shown it stays for the rest of that step, once a second; never an estimate; the bar only the count
describe('the seconds passed beside a count that stands still', () => {
  const at = (done: number) => [stage('store', 'bytes', BYTES, BYTES), stage('decode', 'frames', 1, 1), stage('detect', 'frames', 1, 1), stage('solve', 'candidates', done, 4160)];
  const since = () => [...container.querySelectorAll('.work-step__since')].map((v) => v.textContent);
  const nowRow = () => container.querySelector('.work-step.is-now')!;

  it('stands beside a count unmoved for ten seconds, left of it, and stays for the rest of the step', () => {
    show(arrival(at(2080)));
    act(() => vi.advanceTimersByTime(9_000));
    expect(since()).toEqual([]);
    act(() => vi.advanceTimersByTime(1_000));
    expect(since()).toEqual(['10 s']);
    // In the row being worked, just before its count, which keeps its words and its bar
    const shown = nowRow().querySelector('.work-step__since')!;
    expect(shown.nextElementSibling?.className).toBe('work-step__value');
    expect(nowRow().querySelector('.work-step__value')?.textContent).toBe('2,080 of 4,160');
    expect((nowRow().querySelector('.work-step__bar b') as HTMLElement).style.width).not.toBe('');
    act(() => vi.advanceTimersByTime(55_000));
    expect(since()).toEqual(['1 min 5 s']);
    // The count moves: the seconds stay for the rest of that step
    show(arrival(at(2100)));
    act(() => vi.advanceTimersByTime(1_000));
    expect(since()).toEqual(['1 min 6 s']);
    expect(nowRow().querySelector('.work-step__value')?.textContent).toBe('2,100 of 4,160');
    // The step ends: they go with it
    show(arrival([...at(4160).slice(0, 3), stage('solve', 'candidates', 4160, 4160), stage('verify', 'candidates', 0, 40)]));
    expect(since()).toEqual([]);
  });

  it('never stands while the count moves within ten seconds', () => {
    show(arrival(at(100)));
    for (let i = 1; i <= 8; i += 1) {
      act(() => vi.advanceTimersByTime(9_000));
      show(arrival(at(100 + i * 10)));
    }
    act(() => vi.advanceTimersByTime(9_000));
    expect(since()).toEqual([]);
  });

  it('counts from when the step began, not from when its count last moved', () => {
    // Matching with no total for 3 s, then its count, which then stands still
    show(arrival(searching));
    act(() => vi.advanceTimersByTime(3_000));
    show(arrival(at(2080)));
    act(() => vi.advanceTimersByTime(9_000));
    expect(since()).toEqual([]);
    act(() => vi.advanceTimersByTime(1_000));
    expect(since()).toEqual(['13 s']);
  });

  it('starts again for a new job', () => {
    show(arrival(at(2080)));
    act(() => vi.advanceTimersByTime(12_000));
    expect(since()).toEqual(['12 s']);
    const again = arrival(at(2080));
    show({ ...again, job: { ...again.job!, id: 'job_fedcba9876543210' } } as Arrival);
    expect(since()).toEqual([]);
    act(() => vi.advanceTimersByTime(10_000));
    expect(since()).toEqual(['10 s']);
  });

  it("stands on the restoration's steps the same way", () => {
    const job = (done: number) => ({ id: 'job_7e6f5a4b3c2d1e0f9a8b7c6d', kind: 'restoration', state: 'running', stages: [stage('decode', 'frames', 328, 328), stage('measure', 'frames', done, 8)] }) as unknown as UploadJob;
    act(() => root.render(createElement(RestoreSteps, { job: job(7) })));
    act(() => vi.advanceTimersByTime(9_000));
    expect(since()).toEqual([]);
    act(() => vi.advanceTimersByTime(6_000));
    expect(since()).toEqual(['15 s']);
    expect(nowRow().querySelector('.work-step__value')?.textContent).toBe('7 of 8');
  });

  it("stands on the phone's one row, the working screen's and the restoration's, left of its count", () => {
    act(() => root.render(createElement(WorkingScreen, { arrival: arrival(at(2080)), photoRect: null, onTryAgain: () => {}, onUploadAnother: () => {}, phone: true })));
    const phoneSince = () => container.querySelector('.work-phone-row .work-phone-row__since');
    act(() => vi.advanceTimersByTime(9_000));
    expect(phoneSince()).toBeNull();
    act(() => vi.advanceTimersByTime(1_000));
    expect(phoneSince()?.textContent).toBe('10 s');
    expect(phoneSince()?.nextElementSibling?.className).toBe('work-phone-row__v');
    expect(container.querySelector('.work-phone-row__v')?.textContent).toBe('2,080 of 4,160');
    act(() => root.unmount());
    root = createRoot(container);
    const job = { id: 'job_7e6f5a4b3c2d1e0f9a8b7c6d', kind: 'restoration', state: 'running', stages: [stage('decode', 'frames', 328, 328), stage('measure', 'frames', 7, 8)] } as unknown as UploadJob;
    act(() => root.render(createElement(RestorePhoneWait, { job, onCancel: () => {} })));
    act(() => vi.advanceTimersByTime(10_000));
    expect(phoneSince()?.textContent).toBe('10 s');
    expect(phoneSince()?.nextElementSibling?.className).toBe('work-phone-row__v');
  });

  it('is never drawn beside a size in bytes, which reports no total', () => {
    const job = { id: 'job_7e6f5a4b3c2d1e0f9a8b7c6d', kind: 'restoration', state: 'running', stages: [stage('decode', 'frames', 328, 328), stage('measure', 'frames', 8, 8), stage('restore', 'frames', 328, 328), stage('encode', 'frames', 328, 328), stage('write', 'bytes', 14_000_000, null)] } as unknown as UploadJob;
    act(() => root.render(createElement(RestoreSteps, { job })));
    const size = nowRow().querySelector('.work-step__value')?.textContent;
    expect(size).toMatch(/MB$/);
    act(() => vi.advanceTimersByTime(30_000));
    expect(since()).toEqual([]);
    expect(nowRow().querySelector('.work-step__value')?.textContent).toBe(size);
  });

  it('is never drawn beside a row with no total, which shows the time in its place', () => {
    show(arrival(searching));
    act(() => vi.advanceTimersByTime(30_000));
    expect(since()).toEqual([]);
    expect(values().at(-2)).toBe('30 s');
  });
});

describe('the seconds beside a still count: their type and place', () => {
  const read = (name: string) => fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), `../../styles/${name}`), 'utf8');
  const css = read('reveal.css');
  const motionCss = read('motion.css');
  function rulesOf(selector: string, sheet = css): Record<string, string> {
    const found: Record<string, string> = {};
    postcss.parse(sheet).walkRules((rule) => {
      if (rule.selector.replace(/\s+/g, ' ') !== selector || rule.parent?.type !== 'root') return;
      rule.walkDecls((decl) => {
        found[decl.prop] = decl.value.replace(/\s+/g, ' ');
      });
    });
    return found;
  }
  it('stand 12 px left of the count at ink 44 percent in the row\'s tabular figures, wording in as they first appear', () => {
    for (const sel of ['.work-step__since', '.work-phone-row__since']) {
      expect(rulesOf(sel)['margin-right'], sel).toBe('12px');
      expect(rulesOf(sel).color, sel).toBe('rgba(244, 243, 239, 0.44)');
      expect(rulesOf(sel).animation, sel).toBeUndefined();
    }
    // Their words in live in motion.css with every movement of the working screen (workingScreen.test.tsx)
    expect(rulesOf('.work-step__since, .work-phone-row__since', motionCss).animation).toBe('look-mo-words-in var(--look-mo-dur-words-in) var(--look-mo-ease-std) both');
    expect(rulesOf('.work-phone-row__since').font).toBe(rulesOf('.work-phone-row__v').font);
    // A column of their own before the count's, so the count's right edge stays the row's; the bar spans them all
    expect(rulesOf('.work-step')['grid-template-columns']).toBe('24px 1fr auto auto');
    expect(rulesOf('.work-step__bar')['grid-column']).toBe('2 / -1');
    expect(rulesOf('.work-phone-row')['grid-template-columns']).toBe('1fr auto auto');
    expect(rulesOf('.work-phone-row__bar')['grid-column']).toBe('1 / -1');
  });
});
