import { describe, expect, it } from 'vitest';
import { ObservationSessionSchema, type SolveJob } from '@albedian/contracts';
import {
  buildSessionFromDiscoveredMedia,
  buildSessionFromIngestReceipt,
  formatSolveJobProgressBanner,
  type DiscoveredMediaItem,
} from '../services/apiClient';
import { POSE, poseReceipt, sample } from '../components/__tests__/poseSessions';

const RAW_STATUS = /calibrated|approximate|predicted_only|unresolved|ambiguous|pending|Sky solution/i;
const JOB_ID = 'job_0123456789abcdef';

function job(state: SolveJob['state'], error?: string): SolveJob {
  return {
    id: JOB_ID,
    sourceAssetSha256: POSE.sourceSha256!,
    state,
    createdAtUTC: '2030-01-01T02:05:00Z',
    startedAtUTC: '2030-01-01T02:05:01Z',
    ...(state === 'running' ? {} : { finishedAtUTC: '2030-01-01T02:05:09Z' }),
    stages: [
      { stage: 'store', unit: 'bytes', done: 4096, total: 4096 },
      { stage: 'decode', unit: 'frames', done: 1, total: 1 },
      { stage: 'detect', unit: 'frames', done: 1, total: 1 },
      { stage: 'solve', unit: 'candidates', done: 3, total: 3 },
      { stage: 'verify', unit: 'steps', done: 0, total: 1 },
    ],
    ...(state === 'succeeded' ? { solutionId: POSE.id } : {}),
    ...(error ? { error } : {}),
  };
}
const STAGES = 'store: 4096/4096 bytes · decode: 1/1 frames · detect: 1/1 frames · solve: 3/3 candidates · verify: 0/1 steps';

describe('the upload banners say the layer in plain words (REQ-SKY-6.1)', () => {
  it('keeps the stage counts and replaces the raw status with the sentence', () => {
    const receipt = poseReceipt(POSE, 'image');
    const banner = formatSolveJobProgressBanner('synthetic-pose.png', job('succeeded'), POSE, receipt.sourceAsset);
    expect(banner).toBe(
      `Loaded "synthetic-pose.png" (${JOB_ID}, ${STAGES}). Lined up from the Moon, within about 3 degrees. Not checked against stars.`
    );
    const session = buildSessionFromIngestReceipt(receipt, { job: job('succeeded'), solution: POSE });
    expect(session.lastStatusBanner).toBe(banner);
    const withoutJob = buildSessionFromIngestReceipt(receipt, { solution: sample('horizon_predicted_only') });
    expect(withoutJob.lastStatusBanner).toMatch(
      /^Loaded "synthetic-pose\.png" \(SHA-256 [0-9a-f]{12}…\)\. Too few points of light to match to stars\. Showing the sky predicted for your photo's time and place\.$/
    );
  });

  it('says why there is no sky from the asset the receipt names', () => {
    const receipt = poseReceipt(POSE, 'image');
    const { capturedAtUTC: _time, metadataSources, ...rest } = receipt.sourceAsset;
    const timeless = {
      ...receipt,
      sourceAsset: { ...rest, metadataSources: { observerLocation: metadataSources!.observerLocation } },
      hints: { location: receipt.hints.location },
    };
    const solution = { ...sample('plate_too_few_sources'), sourceAssetId: POSE.sourceAssetId, sourceSha256: POSE.sourceSha256 };
    const session = buildSessionFromIngestReceipt(timeless, { job: job('succeeded'), solution });
    expect(session.lastStatusBanner).toContain('Your photo does not say when it was taken, so its sky cannot be shown.');
    expect(session.lastStatusBanner).toContain(STAGES);
  });

  it('says a failed solve stopped, with the stage counts and the reason the server gave', () => {
    const receipt = poseReceipt(POSE, 'video');
    const failed = job('failed', 'The decoder could not read the file: corrupt stream');
    const session = buildSessionFromIngestReceipt(receipt, { job: failed });
    ObservationSessionSchema.parse(session);
    expect(session.lastStatusBanner).toBe(
      `Stored "synthetic-pose.mov" (${JOB_ID}, ${STAGES}). The sky solve for your clip stopped before it finished: The decoder could not read the file: corrupt stream.`
    );
  });

  it('never prints a raw status in any banner, running, loaded or failed', () => {
    const receipt = poseReceipt(POSE, 'image');
    for (const name of ['pose_moon_level_head', 'horizon_approximate', 'horizon_predicted_only', 'plate_calibrated', 'plate_too_few_sources']) {
      const banner = formatSolveJobProgressBanner('synthetic-pose.png', job('succeeded'), sample(name), receipt.sourceAsset);
      expect(banner, name).not.toMatch(RAW_STATUS);
    }
    expect(formatSolveJobProgressBanner('synthetic-pose.png', job('running'))).not.toMatch(RAW_STATUS);
    expect(formatSolveJobProgressBanner('synthetic-pose.png', job('failed', 'stopped'))).not.toMatch(RAW_STATUS);
  });

  it('says the layer for a discovered clip and opens it at the solved frame with the predicted layer on', () => {
    const item: DiscoveredMediaItem = {
      id: 'synthetic',
      title: 'Synthetic',
      filename: 'synthetic.mov',
      sha256: POSE.sourceSha256!,
      mediaKind: 'video',
      width: 960,
      height: 540,
      durationSec: 15,
      fps: 29.917,
      videoCodec: 'hevc',
      capturedAtUTC: '2030-01-01T02:00:00Z',
      latDeg: 30.0,
      lonDeg: -40.0,
      cameraModel: null,
      urls: { origUrl: '/synthetic.png', v1Url: '/synthetic.png', v2Url: '/synthetic.png' },
      solution: { ...sample('horizon_predicted_only'), frameTimeSec: 3 },
    };
    const session = buildSessionFromDiscoveredMedia(item);
    expect(session.lastStatusBanner).toMatch(
      /^Loaded "synthetic\.mov" \(SHA-256 [0-9a-f]{12}…\)\. Too few points of light to match to stars\. Showing the sky predicted for your clip's time and place\.$/
    );
    expect(session.currentSourceTimeSec).toBe(3);
    expect(session.isPlaying).toBe(false);
    expect(session.spatialView.activeOverlays.predictedEphemerides).toBe(true);
    expect(session.navigationHistory.anchorMomentCheckpoint.sourceTimeSec).toBe(3);
  });
});
