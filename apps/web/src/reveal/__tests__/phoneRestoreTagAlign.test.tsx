// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CelestialFinding, ObservationSession, ObservationSolution, UploadJob } from '@albedian/contracts';
import { poseSession, sample, POSE } from '../../components/__tests__/poseSessions.js';
import type { AlignPose } from '../alignPose.js';
import { EARLY_SOURCE_WORDS } from '../earlyPose.js';
import { UNCHANGED } from '../restoration.js';
import { RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

// tagalign-1, from FIELD's walk (walk-0410.md, break one), DECIDED-BY-LEAD 21:08 on 22 Nov by the tag rule and D38: at
// phone width a Restore over a refused line-up keeps the kept alignment's predicted rings drawn through the wait and
// the stop, which had no Predicted tag on screen (the restore lead stood only for predicted marks among the reveal's
// own). The lead also stands while the kept alignment's rings are drawn: the Predicted tag at full and, in the pose
// sentence's place, the alignment's own source line; no new words.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const jobs = (JSON.parse(fs.readFileSync(path.resolve(here, '../../../../../tests/fixtures/job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const done = jobs.find((job) => job.kind === 'restoration' && job.state === 'succeeded')!;
const { finishedAtUTC: _f, attemptId: _a, variantId: _v, ...rest } = structuredClone(done);
const still = (stage: string) => ({ stage, unit: 'frames', done: 1, total: 1 });
const RUNNING = { ...rest, state: 'running', stages: [still('decode'), still('measure')] } as unknown as UploadJob;
const REFUSED = {
  ...rest,
  state: 'rejected',
  stages: [still('decode'), still('measure'), still('restore'), still('encode'), { stage: 'write', unit: 'bytes', done: 900, total: 900 }, still('verify')],
} as unknown as UploadJob;
const REASON = 'The restoration changed how bright a star is, so it was not kept.';

const base = sample('horizon_predicted_only');
const template = base.findings.find((f) => f.evidenceClass === 'predicted')!;
const starRef = POSE.findings.find((f) => f.catalogRef?.objectType === 'star')!.catalogRef!;
const target = (id: string, title: string, alt: number, az: number, mag: number) =>
  ({ ...template, id, title, skyPosition: { ...template.skyPosition!, altDeg: alt, azDeg: az }, catalogRef: { ...starRef, apparentMagnitude: mag } }) as CelestialFinding;
function unmatched(): ObservationSession {
  const session = poseSession(base);
  return {
    ...session,
    sourceAsset: { ...session.sourceAsset!, width: 3024, height: 4032, displayOrientation: undefined, cameraMetadata: { focalLength35mmMm: 26 } },
    observationSolution: {
      ...session.observationSolution!,
      findings: [...session.observationSolution!.findings.filter((f) => f.evidenceClass !== 'predicted'), target('finding-predicted-star-hip-113368', 'Fomalhaut', 20, 150, 1.16), target('finding-predicted-star-hip-107315', 'Enif', 40, 145, 2.38)],
    } as ObservationSolution,
  } as ObservationSession;
}
const KEPT: AlignPose = { azDeg: 150, altDeg: 30, rollDeg: 0, fovXDeg: 60, aspect: 4 / 3 };

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants: [], attempts: [] }), { status: 200 }));
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});
const q = (s: string) => container.querySelector(s);
async function showPhone(extra: Record<string, unknown>, kept = true) {
  const session = unmatched();
  act(() =>
    root.render(
      createElement(RevealPhoto, {
        session,
        onSelectFinding: () => {},
        onHome: () => {},
        viewportWidthPx: 390,
        viewportHeightPx: 844,
        phone: true,
        onShowPredictedSky: () => {},
        onRestore: () => {},
        onCancelRestore: () => {},
        ...(kept ? { alignedFrom: { sha256: session.sourceAsset!.sha256, pose: KEPT, from: { kind: 'brightest', name: 'Fomalhaut' } } } : {}),
        ...extra,
      } as unknown as Parameters<typeof RevealPhoto>[0])
    )
  );
  for (let i = 0; i < 5; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
}
const leadOf = (lead: Element) => [...lead.children].map((el) => [el.className, el.textContent]);
const TAG = ['look-tag look-tag--predicted', expect.stringContaining('Predicted')];

describe('the Predicted tag over a refused line-up\'s kept rings through a phone restoration (tagalign-1)', () => {
  it('stands the tag and the alignment\'s source line above the wait\'s row while the kept rings are drawn', async () => {
    await showPhone({ restoration: { phase: 'working', job: RUNNING } });
    expect(q('[data-testid="early-marks"]')).not.toBeNull();
    const lead = q('.restore-phone-lead')!;
    expect(lead).not.toBeNull();
    expect(leadOf(lead)).toEqual([TAG, ['reveal-phone-estimate__source', EARLY_SOURCE_WORDS.alignment]]);
    expect(lead.compareDocumentPosition(q('.restore-phone-wait .work-phone-row')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("heads a refusal's own block with them, the reason and the photo unchanged under, and brings Line it up again back at 772 with or without predictedSky", async () => {
    await showPhone({ onSolveFromHere: () => {}, restoration: { phase: 'stopped', job: REFUSED, sentence: REASON, subline: UNCHANGED, canTryAgain: false } });
    expect(q('[data-testid="early-marks"]')).not.toBeNull();
    const why = q('.restore-phone-why')!;
    const lead = why.querySelector('.restore-phone-why__lead')!;
    expect(why.firstElementChild).toBe(lead);
    expect(leadOf(lead)).toEqual([TAG, ['reveal-phone-estimate__source', EARLY_SOURCE_WORDS.alignment]]);
    expect([...why.querySelectorAll(':scope > p')].map((p) => p.textContent)).toEqual([REASON, 'Your photo is unchanged.']);
    const again = q('.restore-phone-go button') as HTMLButtonElement;
    expect(again.textContent).toBe('Line it up again');
    expect(again.className).toContain('look-main');
    expect(again.className).toContain('look-size-phone');

    // Also on a record-lent photo whose file has no capture time or place (!status.predictedSky)
    const bare = unmatched();
    const withoutTimeOrPlace = {
      ...bare,
      sourceAsset: { ...bare.sourceAsset!, capturedAtUTC: undefined, observerLocation: undefined },
      observationSolution: { ...bare.observationSolution!, status: 'failed', poseEstimate: undefined },
    } as unknown as ObservationSession;
    await showPhone({
      session: withoutTimeOrPlace,
      alignedFrom: { sha256: withoutTimeOrPlace.sourceAsset!.sha256, pose: KEPT, from: { kind: 'brightest', name: 'Fomalhaut' } },
      onSolveFromHere: () => {},
      restoration: { phase: 'stopped', job: REFUSED, sentence: REASON, subline: UNCHANGED, canTryAgain: false },
    });
    const bareAgain = q('.restore-phone-go button') as HTMLButtonElement;
    expect(bareAgain.textContent).toBe('Line it up again');
    act(() => bareAgain.click());
    expect(q('.restore-phone-stopped')).toBeNull();
    expect([...container.querySelectorAll('button')].some((b) => b.textContent === 'Solve from here')).toBe(true);
  });

  it('draws no source line of the alignment without its kept rings', async () => {
    await showPhone({ restoration: { phase: 'working', job: RUNNING } }, false);
    expect(q('[data-testid="early-marks"]')).toBeNull();
    expect(container.textContent).not.toContain(EARLY_SOURCE_WORDS.alignment);
  });

  it('phone-stopped-answer-1: under an open answer after a refusal over kept alignment rings, steps RestorePhoneStopped aside while keeping Predicted tag, alignment source line, and Line it up again', async () => {
    const origBcr = HTMLElement.prototype.getBoundingClientRect;
    const spy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (this.getAttribute('data-testid') === 'reveal-phone-estimate') {
        return { left: 24, top: 688, right: 366, bottom: 756, width: 342, height: 68, x: 24, y: 688, toJSON: () => ({}) } as DOMRect;
      }
      return origBcr.call(this);
    });
    try {
      await showPhone({
        onSolveFromHere: () => {},
        restoration: { phase: 'stopped', job: REFUSED, sentence: REASON, subline: UNCHANGED, canTryAgain: false },
        answer: { heard: 'Stop', sentence: 'Listening stopped.', answer: null },
      });
      expect(q('[data-testid="early-marks"]')).not.toBeNull();
      expect(q('.restore-phone-stopped')).toBeNull();
      const lead = q('[data-testid="reveal-phone-estimate"]')!;
      expect(lead).not.toBeNull();
      expect(leadOf(lead)).toEqual([TAG, ['reveal-phone-estimate__source', EARLY_SOURCE_WORDS.alignment]]);
      expect((q('[data-testid="reveal-answer"]') as HTMLElement).style.bottom).toBe(`${844 - 688 + 16}px`);
      expect(q('.restore-phone-go button')!.textContent).toBe('Line it up again');
    } finally {
      spy.mockRestore();
    }
  });

  it('phone-alignrefused-answer-1: before restoration, steps PhoneAlignRefused aside under an open answer on both photo and clip, keeps Predicted + alignment source 16 px below AnswerPanel, and reopens aligning cleanly from Line it up again', async () => {
    const origBcr = HTMLElement.prototype.getBoundingClientRect;
    let estTop = 701;
    const spy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (this.getAttribute('data-testid') === 'reveal-phone-estimate') {
        return { left: 24, top: estTop, right: 366, bottom: estTop + 55, width: 342, height: 55, x: 24, y: estTop, toJSON: () => ({}) } as DOMRect;
      }
      return origBcr.call(this);
    });
    try {
      const answer = { heard: 'Take me to Zorblax', sentence: "Nothing in this photo's data answers that.", answer: null };
      // Bare upload without capture time or place (!status.predictedSky) before restoration (phase: 'idle')
      const bare = unmatched();
      const withoutTimeOrPlace = {
        ...bare,
        sourceAsset: { ...bare.sourceAsset!, capturedAtUTC: undefined, observerLocation: undefined },
        observationSolution: { ...bare.observationSolution!, status: 'failed', poseEstimate: undefined },
      } as unknown as ObservationSession;
      let closedCount = 0;
      await showPhone({
        session: withoutTimeOrPlace,
        alignedFrom: { sha256: withoutTimeOrPlace.sourceAsset!.sha256, pose: KEPT, from: { kind: 'brightest', name: 'Fomalhaut' } },
        onSolveFromHere: () => {},
        onCloseAnswer: () => {
          closedCount += 1;
        },
        restoration: { phase: 'idle' },
        answer,
      });
      expect(q('[data-testid="early-marks"]')).not.toBeNull();
      expect(q('[data-testid="reveal-phone-scroll"]')).toBeNull();
      expect(q('.reveal-status.is-unmatched.is-phone.is-refused')).toBeNull();
      const photoLead = q('[data-testid="reveal-phone-estimate"]')!;
      expect(photoLead).not.toBeNull();
      expect(photoLead.className).toBe('reveal-phone-estimate');
      expect(leadOf(photoLead)).toEqual([TAG, ['reveal-phone-estimate__source', EARLY_SOURCE_WORDS.alignment]]);
      expect((q('[data-testid="reveal-answer"]') as HTMLElement).style.bottom).toBe(`${844 - 701 + 16}px`);
      const again = q('.restore-phone-go button') as HTMLButtonElement;
      expect(again.textContent).toBe('Line it up again');
      expect(again.className).toContain('look-main');
      expect(again.className).toContain('look-size-phone');

      // Pressing Line it up again closes the open answer and enters PhoneAligningBlock cleanly
      act(() => again.click());
      expect(closedCount).toBe(1);
      expect(q('[data-testid="reveal-phone-estimate"]')).toBeNull();
      expect([...container.querySelectorAll('button')].some((b) => b.textContent === 'Solve from here')).toBe(true);

      // Unmatched clip over kept alignment rings before restoration (phase: 'idle') + open answer
      estTop = 583;
      const clipBase = unmatched();
      const clipSha = 'b'.repeat(64);
      const clipSession = {
        ...clipBase,
        sourceAsset: { ...clipBase.sourceAsset!, sha256: clipSha, mediaKind: 'video', width: 1920, height: 1080 },
      } as ObservationSession;
      await showPhone({
        session: clipSession,
        alignedFrom: { sha256: clipSha, pose: { ...KEPT, aspect: 16 / 9 }, from: { kind: 'brightest', name: 'Fomalhaut' } },
        onSolveFromHere: () => {},
        restoration: { phase: 'idle' },
        answer: { heard: 'where is Polaris?', sentence: "Nothing in this clip's data answers that.", answer: null },
      });
      expect(q('[data-testid="early-marks"]')).not.toBeNull();
      expect(q('[data-testid="reveal-phone-scroll"]')).toBeNull();
      expect(q('.align-refused__block')).toBeNull();
      const clipLead = q('[data-testid="reveal-phone-estimate"]')!;
      expect(clipLead).not.toBeNull();
      expect(clipLead.className).toBe('reveal-phone-estimate is-clip');
      expect(leadOf(clipLead)).toEqual([TAG, ['reveal-phone-estimate__source', EARLY_SOURCE_WORDS.alignment]]);
      expect((q('[data-testid="reveal-answer"]') as HTMLElement).style.bottom).toBe(`${844 - 583 + 16}px`);
      expect(q('.restore-phone-go button')!.textContent).toBe('Line it up again');

      // While a restoration is running or stopped retryably (canTryAgain: true), phoneAlignRefusedUnderAnswer does not
      // mount a duplicate .restore-phone-go over Cancel or Try again
      await showPhone({
        session: clipSession,
        alignedFrom: { sha256: clipSha, pose: { ...KEPT, aspect: 16 / 9 }, from: { kind: 'brightest', name: 'Fomalhaut' } },
        restoration: { phase: 'working', job: RUNNING },
        answer: { heard: 'where is Polaris?', sentence: "Nothing in this clip's data answers that.", answer: null },
      });
      expect([...container.querySelectorAll('.restore-phone-go button')].map((b) => b.textContent)).toEqual(['Cancel']);
      await showPhone({
        session: clipSession,
        alignedFrom: { sha256: clipSha, pose: { ...KEPT, aspect: 16 / 9 }, from: { kind: 'brightest', name: 'Fomalhaut' } },
        restoration: { phase: 'stopped', job: { ...RUNNING, state: 'failed' } as unknown as UploadJob, sentence: 'Stopped.', subline: null, canTryAgain: true },
        answer: { heard: 'where is Polaris?', sentence: "Nothing in this clip's data answers that.", answer: null },
      });
      expect([...container.querySelectorAll('.restore-phone-go button')].map((b) => b.textContent)).toEqual(['Try again']);
      expect(q('.restore-phone-stopped')).not.toBeNull();
    } finally {
      spy.mockRestore();
    }
  });
});
