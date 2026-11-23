// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FAILURE_SENTENCES, INTERRUPTED_SENTENCE, type UploadJob } from '@albedian/contracts';
import { CLIP_UNCHANGED, UNCHANGED, preRestorationRefusalsOf, refusalSentence, restorationOf, useRestoration, type Restoration } from '../restoration.js';

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
      orientation: { quarterTurnsClockwise: 0, mirrored: false },
    });
    expect(restorationOf(restoration('canceled'), sha256)).toMatchObject({ phase: 'stopped', sentence: 'Canceled' });
    expect(restorationOf(restoration('interrupted'), sha256)).toMatchObject({ phase: 'stopped', sentence: INTERRUPTED_SENTENCE });
    expect(restorationOf(restoration('failed'), sha256)).toMatchObject({ phase: 'stopped', sentence: restoration('failed').error });
    // Refused: its sentence comes from the attempt (read after), the photo unchanged, and no Try again
    expect(restorationOf(restoration('rejected'), sha256)).toMatchObject({ phase: 'stopped', sentence: null, subline: 'Your photo is unchanged.', canTryAgain: false });
    expect(restorationOf(restoration('rejected'), sha256, 'video')).toMatchObject({ phase: 'stopped', sentence: null, subline: 'Your clip is unchanged.', canTryAgain: false });
    expect(restorationOf(restoration('failed'), sha256)).toMatchObject({ canTryAgain: true, subline: null });
  });

  it('treats deterministic pre-restoration refusals in FAILURE_SENTENCES ending ", so it was not restored." as refusals, with no Try again', () => {
    const failed = restoration('failed');
    const preRefusals = FAILURE_SENTENCES.filter((s) => s.endsWith(', so it was not restored.'));
    expect(preRefusals.length).toBeGreaterThanOrEqual(1);
    for (const sentence of preRefusals) {
      expect(restorationOf({ ...failed, error: sentence }, sha256)).toEqual({
        phase: 'stopped',
        job: { ...failed, error: sentence },
        sentence,
        subline: 'Your photo is unchanged.',
        canTryAgain: false,
      });
      expect(restorationOf({ ...failed, error: sentence }, sha256, 'video')).toEqual({
        phase: 'stopped',
        job: { ...failed, error: sentence },
        sentence,
        subline: 'Your clip is unchanged.',
        canTryAgain: false,
      });
    }
    for (const sentence of FAILURE_SENTENCES.filter((s) => !s.endsWith(', so it was not restored.') && !s.includes('{'))) {
      expect(restorationOf({ ...failed, error: sentence }, sha256)).toMatchObject({
        phase: 'stopped',
        sentence,
        subline: null,
        canTryAgain: true,
      });
    }
    // Only sentences in FAILURE_SENTENCES count; an interrupted job never counts even if its error text matches
    expect(restorationOf({ ...failed, error: 'Not in contract, so it was not restored.' }, sha256)).toMatchObject({
      phase: 'stopped',
      subline: null,
      canTryAgain: true,
    });
    expect(restorationOf({ ...restoration('interrupted'), error: preRefusals[0]! }, sha256)).toMatchObject({
      phase: 'stopped',
      subline: null,
      canTryAgain: true,
    });
    // C36 (UIUX-nearblackrestore-READY.md) enters the set automatically once nearblack-0 lands in FAILURE_SENTENCES
    const c36 = "This photo's sky is so close to black that its faintest noise is cut off, so it was not restored.";
    expect(preRestorationRefusalsOf([...FAILURE_SENTENCES, c36]).has(c36)).toBe(true);
    expect(UNCHANGED).toBe('Your photo is unchanged.');
    expect(CLIP_UNCHANGED).toBe('Your clip is unchanged.');
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
  const Probe = ({ sha, mediaKind }: { sha: string | null; mediaKind?: 'image' | 'video' }) => {
    latest = useRestoration(sha, { pollMs: 0, mediaKind });
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
  const phase = (): Restoration['phase'] => latest.restoration.phase;
  /** Waits until the hook's state passes `check`, failing after two seconds with the phase it stood in. */
  const until = async (check: () => boolean) => {
    for (let waited = 0; !check(); waited += 10) {
      if (waited >= 2000) throw new Error(`the restoration stood at ${phase()}`);
      await act(async () => await new Promise((r) => setTimeout(r, 10)));
    }
  };

  it('is idle, then starts, works with each record, and compares once the job succeeds, turned as its variant record says', async () => {
    const answers = [json(running, 202), json(running), json(succeeded), json({ variant: { displayOrientation: { quarterTurnsClockwise: 1, mirrored: false } } })];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answers.shift()!);
    act(() => root.render(createElement(Probe, { sha: sha256 })));
    expect(phase()).toBe('idle');
    act(() => latest.start());
    await until(() => phase() === 'done');
    expect(phase()).toBe('done');
    expect(latest.restoration.phase === 'done' && latest.restoration.variantUrl).toBe(`/api/media/${sha256}/variants/${succeeded.variantId}/file`);
    expect(latest.restoration.phase === 'done' && latest.restoration.orientation).toEqual({ quarterTurnsClockwise: 1, mirrored: false });
  });

  it("stops with the server's own sentence when the start is refused", async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({ detail: 'Restoring a clip is not built yet.' }, 501));
    act(() => root.render(createElement(Probe, { sha: sha256 })));
    act(() => latest.start());
    await until(() => phase() === 'stopped');
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
    await until(() => latest.restoration.phase === 'working' && latest.restoration.job !== null);
    expect(phase()).toBe('working');
    await act(async () => {
      await latest.cancel();
    });
    await until(() => phase() === 'stopped');
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
    await until(() => phase() === 'stopped');
    expect(calls).toContain(`/api/media/${sha256}/variants`);
    expect(latest.restoration).toMatchObject({
      phase: 'stopped',
      sentence: 'The restoration showed 2 points of light that are not in your photo, so it was not kept.',
      subline: 'Your photo is unchanged.',
      canTryAgain: false,
    });
  });

  it('stops with no Try again and the upload unchanged when a job fails with a pre-restoration refusal, naming a clip on video', async () => {
    const profileNotCarried = FAILURE_SENTENCES.find((s) => s.endsWith(', so it was not restored.'))!;
    const preRefused = { ...restoration('failed'), error: profileNotCarried };
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => (String(input).endsWith('/restore') ? json(running, 202) : json(preRefused)));
    act(() => root.render(createElement(Probe, { sha: sha256 })));
    act(() => latest.start());
    await until(() => phase() === 'stopped');
    expect(latest.restoration).toEqual({
      phase: 'stopped',
      job: preRefused,
      sentence: profileNotCarried,
      subline: 'Your photo is unchanged.',
      canTryAgain: false,
    });

    // On a video, both a gate refusal and a pre-restoration refusal say "Your clip is unchanged."
    const gateRefused = restoration('rejected');
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith('/restore')) return json(gateRefused, 200);
      if (url.endsWith('/variants')) {
        return json({
          variants: [],
          attempts: [{ id: gateRefused.attemptId, status: 'rejected', rejection: 'frame_times_differ' }],
        });
      }
      return json(gateRefused);
    });
    act(() => root.render(createElement(Probe, { sha: 'a'.repeat(64), mediaKind: 'video' })));
    act(() => latest.start());
    await until(() => phase() === 'stopped' && latest.restoration.phase === 'stopped' && latest.restoration.sentence !== null);
    expect(latest.restoration).toEqual({
      phase: 'stopped',
      job: gateRefused,
      sentence: "The restored clip's frame times did not match your clip's, so it was not kept.",
      subline: 'Your clip is unchanged.',
      canTryAgain: false,
    });
  });

  it('forgets everything, and stops following, when the photo changes', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => (String(input).endsWith('/restore') ? json(running, 202) : json(running)));
    act(() => root.render(createElement(Probe, { sha: sha256 })));
    act(() => latest.start());
    await until(() => latest.restoration.phase === 'working' && latest.restoration.job !== null);
    expect(phase()).toBe('working');
    act(() => root.render(createElement(Probe, { sha: 'f'.repeat(64) })));
    expect(phase()).toBe('idle');
    // Nothing comes back: an absence has no state to wait on, so the old job's next reads are given time; a slower
    // machine only shortens what this sees, so it cannot fail for load
    await act(async () => await new Promise((r) => setTimeout(r, 20)));
    expect(phase()).toBe('idle');
  });
});
