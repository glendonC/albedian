// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { INTERRUPTED_SENTENCE, type UploadJob } from '@albedian/contracts';
import { refusalSentence, restorationOf, useRestoration, type Restoration } from '../restoration.js';

// What the restoration shows, from its job's own record (UIUX-restoration-READY.md, D10): the job's steps while it
// works, the compare once it succeeded, and why it stopped; canceled and interrupted as D2 says (INTERIM).

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const fixtures = path.resolve(__dirname, '../../../../../tests/fixtures');
const jobs = (JSON.parse(fs.readFileSync(path.join(fixtures, 'job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const restoration = (state: UploadJob['state']) => structuredClone(jobs.find((job) => job.kind === 'restoration' && job.state === state)!);
const succeeded = restoration('succeeded');
const sha256 = succeeded.sourceAssetSha256;
const running: UploadJob = (() => {
  const { finishedAtUTC: _f, attemptId: _a, variantId: _v, ...rest } = succeeded;
  return { ...rest, state: 'running', stages: succeeded.stages.slice(0, 2) } as UploadJob;
})();
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

describe('restorationOf: a job record to what the screen shows', () => {
  it('works while queued or running, compares once succeeded with its variant file, and says why it stopped', () => {
    expect(restorationOf(running, sha256)).toEqual({ phase: 'working', job: running });
    expect(restorationOf(succeeded, sha256)).toEqual({
      phase: 'done',
      job: succeeded,
      variantUrl: `/api/media/${sha256}/variants/${succeeded.variantId}/file`,
    });
    expect(restorationOf(restoration('canceled'), sha256)).toMatchObject({ phase: 'stopped', sentence: 'Canceled' });
    expect(restorationOf(restoration('interrupted'), sha256)).toMatchObject({ phase: 'stopped', sentence: INTERRUPTED_SENTENCE });
    expect(restorationOf(restoration('failed'), sha256)).toMatchObject({ phase: 'stopped', sentence: restoration('failed').error });
    // Refused: its sentence comes from the attempt (read after), the photo unchanged, and no Try again
    expect(restorationOf(restoration('rejected'), sha256)).toMatchObject({ phase: 'stopped', sentence: null, subline: 'Your photo is unchanged.', canTryAgain: false });
    expect(restorationOf(restoration('failed'), sha256)).toMatchObject({ canTryAgain: true, subline: null });
  });
});

describe('refusalSentence: one plain sentence per refusal reason (UIUX-restore-stop-QUESTION.md, decided by the lead 06:11)', () => {
  it('words each reason, counting the points of light from the gate, one point singular', () => {
    expect(refusalSentence('unconfirmed_points', 3)).toBe('The restoration showed 3 points of light that are not in your photo, so it was not kept.');
    expect(refusalSentence('unconfirmed_points', 1)).toBe('The restoration showed 1 point of light that is not in your photo, so it was not kept.');
    expect(refusalSentence('star_flux_changed', null)).toBe('The restoration changed how bright a star is, so it was not kept.');
    expect(refusalSentence('frame_times_differ', null)).toBe("The restored clip's frame times did not match your clip's, so it was not kept.");
    expect(refusalSentence('audio_differs', null)).toBe("The restored clip's sound did not match your clip's, so it was not kept.");
    expect(refusalSentence('copy_differs', null)).toBe("The copy did not match your photo's pixels exactly, so it was not kept.");
  });

  it('never shows a reason code as words: a reason it does not know, or a count it lacks, gives no sentence', () => {
    expect(refusalSentence('something_new', null)).toBeNull();
    expect(refusalSentence('unconfirmed_points', null)).toBeNull();
  });
});

describe('useRestoration', () => {
  let container: HTMLDivElement;
  let root: Root;
  let latest: ReturnType<typeof useRestoration>;
  const Probe = ({ sha }: { sha: string | null }) => {
    latest = useRestoration(sha, { pollMs: 0 });
    return null;
  };
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
  const settle = () => act(async () => await new Promise((r) => setTimeout(r, 20)));
  const phase = (): Restoration['phase'] => latest.restoration.phase;

  it('is idle, then starts, works with each record, and compares once the job succeeds', async () => {
    const answers = [json(running, 202), json(running), json(succeeded)];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answers.shift()!);
    act(() => root.render(createElement(Probe, { sha: sha256 })));
    expect(phase()).toBe('idle');
    act(() => latest.start());
    await settle();
    expect(phase()).toBe('done');
    expect(latest.restoration.phase === 'done' && latest.restoration.variantUrl).toBe(`/api/media/${sha256}/variants/${succeeded.variantId}/file`);
  });

  it("stops with the server's own sentence when the start is refused", async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({ detail: 'Restoring a clip is not built yet.' }, 501));
    act(() => root.render(createElement(Probe, { sha: sha256 })));
    act(() => latest.start());
    await settle();
    expect(latest.restoration).toEqual({ phase: 'stopped', job: null, sentence: 'Restoring a clip is not built yet.', subline: null, canTryAgain: true });
  });

  it('cancels the job it follows with one POST, and ends as the record says', async () => {
    const calls: string[] = [];
    let canceled = false;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      calls.push(`${init?.method ?? 'GET'} ${url}`);
      if (url.endsWith('/restore')) return json(running, 202);
      if (url.endsWith('/cancel')) {
        canceled = true;
        return json({ ...running, cancelRequestedAtUTC: running.startedAtUTC }, 202);
      }
      return json(canceled ? restoration('canceled') : running);
    });
    act(() => root.render(createElement(Probe, { sha: sha256 })));
    act(() => latest.start());
    await settle();
    expect(phase()).toBe('working');
    await act(async () => {
      await latest.cancel();
    });
    await settle();
    expect(calls).toContain(`POST /api/ingest/jobs/${running.id}/cancel`);
    expect(latest.restoration).toMatchObject({ phase: 'stopped', sentence: 'Canceled' });
  });

  it("reads a refused job's attempt for its reason and count, and says it, the photo unchanged, with no Try again", async () => {
    const refused = restoration('rejected');
    const calls: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      calls.push(url);
      if (url.endsWith('/restore')) return json(running, 202);
      if (url.endsWith('/variants')) {
        return json({
          variants: [],
          attempts: [{ id: refused.attemptId, status: 'rejected', rejection: 'unconfirmed_points', gate: { unconfirmedPointCount: 2, passed: false } }],
        });
      }
      return json(refused);
    });
    act(() => root.render(createElement(Probe, { sha: sha256 })));
    act(() => latest.start());
    await settle();
    expect(calls).toContain(`/api/media/${sha256}/variants`);
    expect(latest.restoration).toMatchObject({
      phase: 'stopped',
      sentence: 'The restoration showed 2 points of light that are not in your photo, so it was not kept.',
      subline: 'Your photo is unchanged.',
      canTryAgain: false,
    });
  });

  it('forgets everything, and stops following, when the photo changes', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => (String(input).endsWith('/restore') ? json(running, 202) : json(running)));
    act(() => root.render(createElement(Probe, { sha: sha256 })));
    act(() => latest.start());
    await settle();
    expect(phase()).toBe('working');
    act(() => root.render(createElement(Probe, { sha: 'f'.repeat(64) })));
    expect(phase()).toBe('idle');
    await settle();
    expect(phase()).toBe('idle');
  });
});
