// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { UploadJob } from '@albedian/contracts';
import { elapsedWords } from '../elapsedRows.js';
import type { Arrival } from '../arrival.js';
import { WorkingScreen } from '../WorkingScreen.js';
import { RestoreSteps } from '../RestoreParts.js';

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
