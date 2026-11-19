import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import type { ObservationSession, ObservationSolution } from '@albedian/contracts';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { skyLayerSentence } from '../../services/skyLayerWords.js';
import { ESTIMATE_MARKS_LINE, evidenceCounts, revealStatus } from '../revealStatus.js';

// The reveal's status block and legend (reveal, approved 2026-11-12; estimate and cautions, decided the same night;
// the lead's answer 1 of 2026-11-18 for a sky that is neither matched nor lined up). Every word and number is the
// solution's own.

const samples = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, '../../../../../tests/fixtures/python_contract_solutions.json'), 'utf8')
) as Record<string, ObservationSolution>;

function sessionWith(solution: ObservationSolution, over: Partial<ObservationSession> = {}): ObservationSession {
  const base = createSyntheticCalibratedSession();
  return {
    ...base,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 3024, height: 4032, durationMs: 0 },
    observationSolution: solution,
    spatialView: { ...base.spatialView, mode: 'source_2d' },
    ...over,
  } as ObservationSession;
}
const calibrated = samples['plate_calibrated']!;
const withQuality = (quality: Partial<NonNullable<ObservationSolution['quality']>>): ObservationSolution => ({
  ...calibrated,
  quality: { ...calibrated.quality!, ...quality },
});

describe('revealStatus: a calibrated photo, as the approved picture words it', () => {
  it("writes the solver's own counts: matched stars, and held-out stars that landed", () => {
    const status = revealStatus(sessionWith(withQuality({ matchedInlierCount: 17, heldOutInliers: 11, heldOutPairCount: 19 })));
    expect(status.tag).toBeNull();
    expect(status.lines).toEqual(['Calibrated · 17 matched', '11 of 19 held-out stars landed']);
  });

  it('groups thousands, and leaves the held-out line out when no star was held out', () => {
    const status = revealStatus(sessionWith(withQuality({ matchedInlierCount: 1043, heldOutInliers: 0, heldOutPairCount: 0 })));
    expect(status.lines).toEqual(['Calibrated · 1,043 matched']);
  });

  it("puts the solve's warnings under it, word for word and in order, and none when there is none", () => {
    const warned = { ...calibrated, warnings: ['The Sun was about 36 degrees below the horizon: night.', 'A second warning.'] };
    expect(revealStatus(sessionWith(warned)).warnings).toEqual(['The Sun was about 36 degrees below the horizon: night.', 'A second warning.']);
    expect(revealStatus(sessionWith({ ...calibrated, warnings: [] })).warnings).toEqual([]);
  });
});

describe('revealStatus: not matched to stars', () => {
  it.each(Object.keys(samples).filter((key) => samples[key]!.status !== 'calibrated'))(
    "says the app's own sentence for %s, with no tag, when no pose lines the sky up over the photo",
    (key) => {
      const solution = { ...samples[key]!, poseEstimate: undefined } as ObservationSolution;
      const session = sessionWith(solution);
      const status = revealStatus(session);
      expect(status.tag).toBeNull();
      expect(status.lines).toEqual([skyLayerSentence(solution, session.sourceAsset, { starSearchRan: true })]);
      expect(status.warnings).toEqual(solution.warnings ?? []);
    }
  );

  it('never calls a photo calibrated on its status word alone: without a verified solve it gets the plain sentence', () => {
    const unverified = withQuality({ passedIndependentVerification: false });
    const status = revealStatus(sessionWith(unverified));
    expect(status.lines.join(' ')).not.toContain('Calibrated');
  });

  it('never calls a photo calibrated on a passed check alone either: the status must say calibrated too', () => {
    // The contract refuses such a solution, so it is given after the session is built: the screen checks for itself
    const session = sessionWith(calibrated);
    const odd = { ...session, observationSolution: { ...calibrated, status: 'ambiguous' } } as ObservationSession;
    expect(revealStatus(odd).lines.join(' ')).not.toContain('Calibrated');
  });

  it('speaks of points of light only when a star search ran', () => {
    const predictedOnly = Object.values(samples).find((s) => s.status === 'predicted_only');
    if (!predictedOnly) return;
    const session = sessionWith({ ...predictedOnly, poseEstimate: undefined } as ObservationSolution);
    expect(revealStatus(session, false).lines).toEqual([skyLayerSentence(session.observationSolution, session.sourceAsset, { starSearchRan: false })]);
  });

  it('has nothing to say before there is a solution', () => {
    const session = sessionWith(calibrated, { observationSolution: undefined });
    expect(revealStatus(session).lines).toEqual([skyLayerSentence(undefined, session.sourceAsset, { starSearchRan: true })]);
  });
});

describe('the estimate line and the legend', () => {
  it('keeps the decided line for a sky lined up by an estimate, word for word', () => {
    expect(ESTIMATE_MARKS_LINE).toBe('No star in it is matched yet; every mark is drawn from the catalog.');
  });

  it("counts the photo's findings by class, in the approved order, and lists only the classes it has", () => {
    const finding = (evidenceClass: string, n: number) => Array.from({ length: n }, (_, i) => ({ ...calibrated.findings[0]!, id: `finding-${evidenceClass}-${i}`, evidenceClass }));
    const mixed = { ...calibrated, findings: [...finding('predicted', 45), ...finding('observed', 1204), ...finding('catalog_matched', 17)] } as ObservationSolution;
    expect(evidenceCounts(mixed)).toEqual([
      { evidenceClass: 'observed', count: 1204, text: '1,204' },
      { evidenceClass: 'catalog_matched', count: 17, text: '17' },
      { evidenceClass: 'predicted', count: 45, text: '45' },
    ]);
    const unmatched = { ...calibrated, findings: [...finding('observed', 31371), ...finding('predicted', 471)] } as ObservationSolution;
    expect(evidenceCounts(unmatched).map((row) => [row.evidenceClass, row.text])).toEqual([
      ['observed', '31,371'],
      ['predicted', '471'],
    ]);
    expect(evidenceCounts({ ...calibrated, findings: [] })).toEqual([]);
    expect(evidenceCounts(undefined)).toEqual([]);
  });
});
