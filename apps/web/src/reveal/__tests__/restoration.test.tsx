// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { INTERRUPTED_SENTENCE, type UploadJob } from '@albedian/contracts';
import { restorationOf, useRestoration, type Restoration } from '../restoration.js';

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
    // No record words a refused restoration yet (QUESTION filed): it stops with no sentence
    expect(restorationOf(restoration('rejected'), sha256)).toMatchObject({ phase: 'stopped', sentence: null });
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
    expect(latest.restoration).toEqual({ phase: 'stopped', job: null, sentence: 'Restoring a clip is not built yet.' });
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
