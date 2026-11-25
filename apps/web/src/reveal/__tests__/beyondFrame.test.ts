import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { ObservationSolutionSchema, type CelestialFinding, type ObservationSession, type ObservationSolution } from '@albedian/contracts';
import { pixelToWorldWCS } from '@albedian/camera-rig';
import { buildSessionFromIngestReceipt } from '../../services/apiClient';
import { POSE, poseReceipt, poseSession, predictedOf } from '../../components/__tests__/poseSessions.js';
import { beyondFrameSentence, liesBeyondTheFrame } from '../beyondFrame.js';
import { revealMarks } from '../revealMarks.js';

/**
 * outside-frame-words-one (DECIDED-BY-LEAD 24 Nov 20:09; UIUX-readout-nodistance-READY.md): a predicted object in the
 * recording's sky but beyond its frame is said to be so, as the reveal places marks. Over the real solve of the public
 * corpus photo rp-b-01 (tests/fixtures/rp_b_01_voice_solve.json, calibrated and verified, its stored 3024 x 4032
 * frame) and the pose sample (lined up by a pose estimate).
 */
const VIEW = { w: 1440, h: 900 };
const RP_B_01 = ObservationSolutionSchema.parse(
  JSON.parse(fs.readFileSync(path.resolve(__dirname, '../../../../../tests/fixtures/rp_b_01_voice_solve.json'), 'utf8'))
);

function realPhoto(solution: ObservationSolution = RP_B_01, mediaKind: 'image' | 'video' = 'image'): ObservationSession {
  const receipt = poseReceipt(solution, mediaKind);
  return buildSessionFromIngestReceipt({ ...receipt, sourceAsset: { ...receipt.sourceAsset, width: 3024, height: 4032 } }, { solution });
}
const picked = (session: ObservationSession, finding: CelestialFinding) => ({ ...session, selectedFindingId: finding.id });
const marked = (session: ObservationSession, finding: CelestialFinding) =>
  revealMarks(picked(session, finding), VIEW.w, VIEW.h).some((mark) => mark.finding.id === finding.id);

describe('a predicted object beyond the frame, as the reveal places marks', () => {
  it('on the real calibrated solve: beyond exactly where the picked object gets no mark, and both happen', () => {
    const session = realPhoto();
    const predicted = predictedOf(RP_B_01);
    const beyond = predicted.filter((f) => liesBeyondTheFrame(session, f) === true).map((f) => f.title);
    const inside = predicted.filter((f) => liesBeyondTheFrame(session, f) === false).map((f) => f.title);
    expect(beyond.length + inside.length).toBe(predicted.length);
    expect(beyond.length).toBeGreaterThan(0);
    expect(inside.length).toBeGreaterThan(0);
    for (const finding of predicted) expect(marked(session, finding), finding.title).toBe(liesBeyondTheFrame(session, finding) === false);
  });

  it('on a pose-lined sky: an object the pose puts outside the frame is beyond, and is never drawn', () => {
    const session = poseSession(POSE);
    const predicted = predictedOf(POSE);
    expect(predicted.some((f) => liesBeyondTheFrame(session, f) === true)).toBe(true);
    for (const finding of predicted) {
      if (liesBeyondTheFrame(session, finding) === true) expect(marked(session, finding), finding.title).toBe(false);
    }
  });

  it('a place just past an edge is beyond, just inside is not, on the real solve and on the pose', () => {
    // A predicted object put at a pixel through each sky's own WCS (its sky position only is changed)
    const at = (solution: ObservationSolution, u: number, v: number): CelestialFinding => {
      const template = predictedOf(solution)[0]!;
      const sky = pixelToWorldWCS(u, v, solution.wcs!);
      return { ...template, id: 'finding-predicted-probe', skyPosition: { ...template.skyPosition!, raDeg: sky.raDeg, decDeg: sky.decDeg } };
    };
    const real = realPhoto();
    // The stored frame is 3024 wide and 4032 high: past the right edge but within the height is beyond
    expect(liesBeyondTheFrame(real, at(RP_B_01, 3100, 200))).toBe(true);
    expect(liesBeyondTheFrame(real, at(RP_B_01, 3000, 4000))).toBe(false);
    expect(liesBeyondTheFrame(real, at(RP_B_01, 200, 4100))).toBe(true);
    const lined = poseSession(POSE);
    // The pose sample's frame is 960 x 540, its edge half a pixel past the last pixel centre, as projectThroughPose reads it
    expect(liesBeyondTheFrame(lined, at(POSE, 480, 270))).toBe(false);
    expect(liesBeyondTheFrame(lined, at(POSE, 959.4, 539.4))).toBe(false);
    expect(liesBeyondTheFrame(lined, at(POSE, 965, 270))).toBe(true);
    expect(liesBeyondTheFrame(lined, at(POSE, 480, -5))).toBe(true);
  });

  it('says nothing where the frame\'s place in the sky is not known, nor of a matched star', () => {
    const at = realPhoto();
    const withSolution = (solution: object) => ({ ...at, observationSolution: { ...at.observationSolution!, ...solution } }) as ObservationSession;
    const unverified = withSolution({ quality: { ...RP_B_01.quality, passedIndependentVerification: false } });
    const predictedOnly = withSolution({ status: 'predicted_only', wcs: undefined });
    for (const session of [unverified, predictedOnly]) {
      for (const finding of predictedOf(RP_B_01)) expect(liesBeyondTheFrame(session, finding), finding.title).toBeNull();
    }
    const star = RP_B_01.findings.find((f) => f.evidenceClass === 'catalog_matched')!;
    expect(liesBeyondTheFrame(realPhoto(), star)).toBeNull();
  });
});

describe("UIUX's sentence", () => {
  const session = realPhoto();
  const beyond = (title: string) => {
    const finding = RP_B_01.findings.find((f) => f.title === title)!;
    expect(liesBeyondTheFrame(session, finding), title).toBe(true);
    return finding;
  };
  const outside = predictedOf(RP_B_01).filter((f) => liesBeyondTheFrame(session, f) === true);

  it('names the object, its class words first, within 90 units, else says it', () => {
    for (const finding of outside) {
      const said = beyondFrameSentence(session, finding)!;
      expect(said.length, finding.title).toBeLessThanOrEqual(90);
      expect(said).toMatch(/^Predicted, not measured: .+ lies beyond this photo's edges, so it has no mark on it\.$/);
    }
    expect(beyondFrameSentence(session, outside[0]!)).not.toBeNull();
  });

  it('the long name falls to it, the Moon takes its article, a bracket is cut', () => {
    const forms = Object.fromEntries(outside.map((f) => [f.title, beyondFrameSentence(session, f)]));
    for (const [title, said] of Object.entries(forms)) {
      if (title === 'Large Magellanic Cloud') {
        // "the Large Magellanic Cloud" with the class words is 103 units, so it says "it"
        expect(said).toBe("Predicted, not measured: it lies beyond this photo's edges, so it has no mark on it.");
      }
      if (title.startsWith('Moon')) expect(said).toBe("Predicted, not measured: the Moon lies beyond this photo's edges, so it has no mark on it.");
      if (title.endsWith('(Predicted Ephemeris)')) expect(said).not.toContain('(');
    }
    expect(beyond('Large Magellanic Cloud')).toBeDefined();
  });

  it('a clip is named a clip', () => {
    const clip = realPhoto(RP_B_01, 'video');
    const finding = beyond('Large Magellanic Cloud');
    expect(beyondFrameSentence(clip, finding)).toBe("Predicted, not measured: it lies beyond this clip's edges, so it has no mark on it.");
  });

  it('says nothing of an object in the frame', () => {
    for (const finding of predictedOf(RP_B_01).filter((f) => liesBeyondTheFrame(session, f) === false)) {
      expect(beyondFrameSentence(session, finding), finding.title).toBeNull();
    }
  });
});
