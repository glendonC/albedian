// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { UploadJob } from '@albedian/contracts';
import { restorationRows } from '../restoration.js';
import { RestorePhoneWait, RestoreSteps } from '../RestoreParts.js';

// clip-checking-row-one (DECIDED-BY-LEAD 12:06 on 27 Nov, from EXPORT's first-time walk): a clip's write stage has no
// total once its playback copy is made inside it (services/media/jobs.py), so it never reached a full count and stood
// "now" beside Checking: the phone's one row read "Saving 41.9 MB" for about 7 minutes while the job's own record
// counted its checking. Once Checking reports, Saving counts done: the phone's row moves on to the real count and the
// laptop's Saving row takes its check, with nothing shown that the record does not hold.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type Stage = UploadJob['stages'][number];
const frames = (stage: string, done: number, total: number | null): Stage => ({ stage, unit: 'frames', done, total }) as Stage;
// The walk's public clip (328 frames), its stored record's stages: write 41,854,982 bytes with no total
const SAVED = 41_854_982;
const before: Stage[] = [frames('decode', 328, 328), frames('measure', 8, 8), frames('restore', 328, 328), frames('encode', 328, 328)];
const write = { stage: 'write', unit: 'bytes', done: SAVED, total: null } as Stage;
const job = (stages: Stage[], state: UploadJob['state'] = 'running'): UploadJob =>
  ({ id: 'job_5a4b3c2d1e0f9a8b7c6d7e6f', kind: 'restoration', state, stages }) as unknown as UploadJob;
// As the walk read it from /api/ingest/jobs/<id> during the wait: verify at 96 of 328
const checking = job([...before, write, frames('verify', 96, 328)]);
const shape = (j: UploadJob) => restorationRows(j).map((r) => [r.word, r.state, r.value, r.fraction]);

describe("restorationRows: a clip's Saving with no total, once Checking reports", () => {
  it('counts Saving done with its megabytes and Checking as the step being worked, with its count and bar', () => {
    expect(shape(checking)).toEqual([
      ['Opened', 'done', '328', null],
      ['Measured', 'done', '8', null],
      ['Restoring', 'done', '328', null],
      ['Encoding', 'done', '328', null],
      ['Saving', 'done', '41.9 MB', null],
      ['Checking', 'now', '96 of 328', 96 / 328],
    ]);
    // Checking listed at 0 of 328 before its first frame: Saving is done, Checking its count from 0
    expect(shape(job([...before, write, frames('verify', 0, 328)])).slice(4)).toEqual([
      ['Saving', 'done', '41.9 MB', null],
      ['Checking', 'now', '0 of 328', 0],
    ]);
  });

  it('keeps Saving the step being worked until Checking reports, and where a job stopped before it', () => {
    // While the copy is made, and while the stored file is checked before verify is listed: Saving, as the record says
    expect(shape(job([...before, write])).slice(4)).toEqual([
      ['Saving', 'now', '41.9 MB', null],
      ['Checking', 'next', null, null],
    ]);
    expect(restorationRows(job([...before, write], 'canceled')).map((r) => r.state)).toEqual(['done', 'done', 'done', 'done', 'failed', 'next']);
    // Refused by the gate while checking: Saving done, Checking where it stopped
    expect(restorationRows(job([...before, write, frames('verify', 40, 328)], 'rejected')).map((r) => r.state)).toEqual([
      'done', 'done', 'done', 'done', 'done', 'failed',
    ]);
  });

  it('ticks no step by a later report when the step has a total of its own, or counts frames', () => {
    // A Saving with a total takes its tick only on its full count, as before
    const counted = { ...write, total: SAVED * 2 } as Stage;
    expect(shape(job([...before, counted, frames('verify', 96, 328)]))[4]).toEqual(['Saving', 'now', '41.9 MB', null]);
    // A frame step's tick comes from its own count only (the lead's ruling of 04:27 on 22 Nov), even with no total
    const opening = job([frames('decode', 20, null), frames('measure', 8, 8), frames('restore', 16, 328), frames('encode', 16, 328)]);
    expect(shape(opening)[0]).toEqual(['Opened', 'now', null, null]);
  });
});

describe('the rows drawn from that record', () => {
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

  it("moves the phone's one row on to Checking's own count and bar", () => {
    act(() => root.render(createElement(RestorePhoneWait, { job: checking, onCancel: () => {}, clip: true })));
    const row = container.querySelector('.work-phone-row')!;
    expect(row.querySelector('b')!.textContent).toBe('Checking');
    expect(row.querySelector('.work-phone-row__v')!.textContent).toBe('96 of 328');
    expect((row.querySelector('.work-phone-row__bar b') as HTMLElement).style.width).toBe('29.268%');
    // Before Checking reports, the row is Saving with its megabytes, as before
    act(() => root.render(createElement(RestorePhoneWait, { job: job([...before, write]), onCancel: () => {}, clip: true })));
    expect(container.querySelector('.work-phone-row b')!.textContent).toBe('Saving');
    expect(container.querySelector('.work-phone-row__v')!.textContent).toBe('41.9 MB');
  });

  it("gives the laptop's Saving row its check beside Checking's count, one step worked", () => {
    act(() => root.render(createElement(RestoreSteps, { job: checking })));
    const steps = [...container.querySelectorAll('.work-step')];
    const saving = steps[4]!;
    expect(saving.className).toBe('work-step is-done');
    expect(saving.querySelector('.work-step__word')!.textContent).toBe('Saving');
    expect(saving.querySelector('.work-step__value')!.textContent).toBe('41.9 MB');
    expect(saving.querySelector('.work-step__bar')).toBeNull();
    expect(container.querySelectorAll('.work-step.is-now')).toHaveLength(1);
    const now = container.querySelector('.work-step.is-now')!;
    expect(now.querySelector('.work-step__word')!.textContent).toBe('Checking');
    expect(now.querySelector('.work-step__value')!.textContent).toBe('96 of 328');
    expect(now.getAttribute('aria-current')).toBe('step');
  });
});
