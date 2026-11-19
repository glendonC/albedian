import { describe, expect, it } from 'vitest';
import { DEVICE_ATTITUDE, orientStoredPoint, type ObservationSolution, type SourceAsset } from '@albedian/contracts';
import { POSE, FRAME, poseReceipt, sample } from '../../components/__tests__/poseSessions.js';
import { projectThroughPose } from '../../components/poseProjection.js';
import { ALIGN_MAGNITUDE_LIMIT } from '../alignPose.js';
import { arrivalAfter, arrivalOf } from '../arrival.js';
import { earlyPoseOf } from '../earlyPose.js';

// The lock-on wired (D25, UIUX-lockon-READY.md point 1) to PLATFORM's partial result (platform-10a and 10b): the sky a
// running solve predicted from an early pose, an approximate solution with its pose estimate, served by the job's
// predicted sky route. The early marks are placed through that pose's own nominal WCS (poseProjection.ts, as the
// lined-up sky is), never through a pose the web works out itself, and they stay predicted.

const asset = (over: Partial<SourceAsset> = {}): SourceAsset => ({ ...poseReceipt(POSE, 'image').sourceAsset, ...over });
const PLANET = /^finding-predicted-(mercury|venus|mars|jupiter|saturn|uranus|neptune)$/;

describe('the early pose from the served predicted sky', () => {
  it('names the Moon as its source for both Moon poses', () => {
    expect(POSE.poseEstimate!.method).toBe('moon_camera_profile_level_head');
    expect(earlyPoseOf(POSE, asset())!.source).toBe('moon');
    const horizon: ObservationSolution = { ...POSE, poseEstimate: { ...POSE.poseEstimate!, method: 'moon_horizon', beacon: 'moon' } };
    expect(earlyPoseOf(horizon, asset())!.source).toBe('moon');
  });

  it("names where the iPhone pointed for the phone's device attitude, which lines up from no beacon", () => {
    const { beacon: _beacon, beaconCentroidUncertaintyDeg: _term, ...rest } = POSE.poseEstimate!;
    const phone: ObservationSolution = { ...POSE, poseEstimate: { ...rest, method: DEVICE_ATTITUDE } };
    expect(earlyPoseOf(phone, asset())!.source).toBe('pointing');
    expect(earlyPoseOf(phone, asset())!.marks.length).toBeGreaterThan(0);
  });

  it('places each named target in the stored frame through the pose, then upright, as a share of the shown photo', () => {
    const early = earlyPoseOf(POSE, asset())!;
    expect(early.marks.length).toBeGreaterThan(0);
    for (const mark of early.marks) {
      const finding = POSE.findings.find((f) => f.id === mark.id)!;
      const at = projectThroughPose(finding, POSE, FRAME)!;
      expect(mark.u).toBeCloseTo((at.xPx + 0.5) / FRAME.widthPx, 9);
      expect(mark.v).toBeCloseTo((at.yPx + 0.5) / FRAME.heightPx, 9);
      expect(mark.u).toBeGreaterThanOrEqual(0);
      expect(mark.u).toBeLessThanOrEqual(1);
    }
  });

  it('draws only the predicted stars bright enough to be named and the planets, brightest first, by their names', () => {
    const early = earlyPoseOf(POSE, asset())!;
    const placed = POSE.findings.filter((f) => projectThroughPose(f, POSE, FRAME) !== null);
    const named = placed.filter((f) => PLANET.test(f.id) || (f.id.startsWith('finding-predicted-star-') && (f.catalogRef?.apparentMagnitude ?? 99) <= ALIGN_MAGNITUDE_LIMIT));
    expect(early.marks.map((m) => m.id).sort()).toEqual(named.map((f) => f.id).sort());
    // A star too faint to be named is placed but not drawn
    expect(placed.length).toBeGreaterThan(named.length);
    // In any order the sky lists them
    const reversed = earlyPoseOf({ ...POSE, findings: [...POSE.findings].reverse() }, asset())!;
    for (const marks of [early.marks, reversed.marks]) {
      const magnitudes = marks.map((m) => POSE.findings.find((f) => f.id === m.id)!.catalogRef?.apparentMagnitude ?? -1);
      expect(magnitudes).toEqual([...magnitudes].sort((a, b) => a - b));
      expect(new Set(magnitudes).size).toBeGreaterThan(1);
    }
    for (const mark of early.marks) expect(mark.name).not.toMatch(/Predicted Ephemeris/);
  });

  it('turns the places with the photo when the file says it is shown turned', () => {
    const turned = asset({ displayOrientation: { quarterTurnsClockwise: 1, mirrored: false } });
    const early = earlyPoseOf(POSE, turned)!;
    const first = early.marks[0]!;
    const at = projectThroughPose(POSE.findings.find((f) => f.id === first.id)!, POSE, FRAME)!;
    const p = orientStoredPoint(at.xPx + 0.5, at.yPx + 0.5, FRAME.widthPx, FRAME.heightPx, turned.displayOrientation);
    // Shown 540 wide and 960 high
    expect(first.u).toBeCloseTo(p.xPx / FRAME.heightPx, 9);
    expect(first.v).toBeCloseTo(p.yPx / FRAME.widthPx, 9);
  });

  it('is none for a sky with no pose, one not approximate, a pose no record words (the Sun), or another upload', () => {
    expect(earlyPoseOf(null, asset())).toBeNull();
    expect(earlyPoseOf(POSE, undefined)).toBeNull();
    const { poseEstimate: _pose, ...noPose } = POSE;
    expect(earlyPoseOf(noPose as ObservationSolution, asset())).toBeNull();
    expect(earlyPoseOf({ ...POSE, status: 'predicted_only' }, asset())).toBeNull();
    expect(earlyPoseOf(sample('plate_calibrated'), asset())).toBeNull();
    const sun: ObservationSolution = { ...POSE, poseEstimate: { ...POSE.poseEstimate!, method: 'sun_horizon', beacon: 'sun' } };
    expect(earlyPoseOf(sun, asset())).toBeNull();
    expect(earlyPoseOf(POSE, asset({ sha256: 'f'.repeat(64) }))).toBeNull();
  });

  it('never draws a mark from a detection: an early mark is predicted only', () => {
    const early = earlyPoseOf(POSE, asset())!;
    for (const mark of early.marks) expect(POSE.findings.find((f) => f.id === mark.id)!.evidenceClass).toBe('predicted');
  });
});

describe('the arrival keeps the early sky', () => {
  it('holds the sky an early event brings while the job goes on, and none before', () => {
    const start = arrivalOf({ name: 'night.jpg', size: 10 });
    expect(start.early ?? null).toBeNull();
    const withSky = arrivalAfter(start, { kind: 'early', solution: POSE })!;
    expect(withSky.early).toBe(POSE);
    const job = { id: 'job_0123456789abcdef', state: 'running', stages: [] } as never;
    expect(arrivalAfter(withSky, { kind: 'job', job })!.early).toBe(POSE);
  });
});
