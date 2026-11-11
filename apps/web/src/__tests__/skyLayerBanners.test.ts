import { describe, expect, it } from 'vitest';
import { ObservationSessionSchema, type ObservationSession, type ObservationSolution, type SolveJob } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import {
  buildSessionFromDiscoveredMedia,
  buildSessionFromIngestReceipt,
  formatSolveJobProgressBanner,
  type DiscoveredMediaItem,
} from '../services/apiClient';
import { linedUpSky } from '../components/poseProjection';
import { POSE, poseReceipt, predictedOf, sample } from '../components/__tests__/poseSessions';

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
const LINED_UP =
  "Lined up by taking a bright spot to be the Moon, with the camera's field of view and a level head, within about 3 degrees; not checked against stars.";

/** A predicted_only solution whose solver says it detected `n`, as an upload's plate solver or the laptop's Moon and horizon solver writes it. */
function unmatched(n: number): ObservationSolution {
  const base = sample('horizon_predicted_only');
  return { ...base, quality: { ...base.quality!, detectedCandidateCount: n } };
}

/** A synthetic clip as discovery on the laptop lists it, with the solution its own solver gave. */
function discovered(solution: ObservationSolution): DiscoveredMediaItem {
  return {
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
    solution,
  };
}
const DISCOVERED_PREFIX = /^Loaded "synthetic\.mov" \(SHA-256 [0-9a-f]{12}…\)\. /;
const afterPrefix = (banner: string | undefined, prefix: RegExp): string => {
  expect(banner).toMatch(prefix);
  return banner!.replace(prefix, '');
};

describe('the upload banners say the layer in plain words (REQ-SKY-6.1)', () => {
  it('keeps the stage counts and replaces the raw status with the sentence', () => {
    const receipt = poseReceipt(POSE, 'image');
    const session = buildSessionFromIngestReceipt(receipt, { job: job('succeeded'), solution: POSE });
    expect(session.lastStatusBanner).toBe(`Loaded "synthetic-pose.png" (${JOB_ID}, ${STAGES}). ${LINED_UP}`);
    // The banner function gives the same line when told what the upload builder tells it: a star search ran
    const banner = formatSolveJobProgressBanner(
      'synthetic-pose.png',
      job('succeeded'),
      { solution: POSE, starSearchRan: true },
      receipt.sourceAsset
    );
    expect(banner).toBe(session.lastStatusBanner);
    const withoutJob = buildSessionFromIngestReceipt(receipt, { solution: sample('horizon_predicted_only') });
    expect(withoutJob.lastStatusBanner).toMatch(
      /^Loaded "synthetic-pose\.png" \(SHA-256 [0-9a-f]{12}…\)\. Too few points of light to match to stars\. You can open the sky predicted for your photo's time and place\.$/
    );
  });

  it("says what an upload's star search found, with a job and without one, and that its predicted sky can be opened", () => {
    for (const mediaKind of ['image', 'video'] as const) {
      const receipt = poseReceipt(POSE, mediaKind);
      const kind = mediaKind === 'video' ? 'clip' : 'photo';
      for (const options of [{ job: job('succeeded') }, {}]) {
        const few = buildSessionFromIngestReceipt(receipt, { ...options, solution: unmatched(4) });
        expect(few.lastStatusBanner).toMatch(
          new RegExp(`\\)\\. Too few points of light to match to stars\\. You can open the sky predicted for your ${kind}'s time and place\\.$`)
        );
        const many = buildSessionFromIngestReceipt(receipt, { ...options, solution: unmatched(40) });
        expect(many.lastStatusBanner).toMatch(
          new RegExp(
            `\\)\\. Found 40 points of light, but could not match them to stars for sure\\. You can open the sky predicted for your ${kind}'s time and place\\.$`
          )
        );
        // Nothing is drawn over the photo until the person opens the predicted sky (verdict note b)
        for (const session of [few, many]) expect(session.lastStatusBanner).not.toMatch(/showing/i);
      }
    }
  });

  it('says of points of light only what its caller says a star search found: the banner function reads no count or solver name for it', () => {
    const receipt = poseReceipt(POSE, 'video');
    for (const n of [0, 4, 40]) {
      const told = (starSearchRan: boolean): string =>
        formatSolveJobProgressBanner('synthetic-pose.mov', job('succeeded'), { solution: unmatched(n), starSearchRan }, receipt.sourceAsset);
      expect(told(false), `count ${n}`).toBe(
        `Loaded "synthetic-pose.mov" (${JOB_ID}, ${STAGES}). You can open the sky predicted for your clip's time and place.`
      );
      expect(told(true), `count ${n}`).toBe(
        `Loaded "synthetic-pose.mov" (${JOB_ID}, ${STAGES}). ${
          n < 6 ? 'Too few points of light to match to stars.' : `Found ${n} points of light, but could not match them to stars for sure.`
        } You can open the sky predicted for your clip's time and place.`
      );
    }
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
    // The stopped line is the failed job's whatever came with it, a solution included
    expect(
      formatSolveJobProgressBanner('synthetic-pose.mov', failed, { solution: POSE, starSearchRan: true }, receipt.sourceAsset)
    ).toBe(session.lastStatusBanner);
  });

  it('never prints a raw status in any banner, running, loaded or failed', () => {
    const receipt = poseReceipt(POSE, 'image');
    for (const name of ['pose_moon_level_head', 'horizon_approximate', 'horizon_predicted_only', 'plate_calibrated', 'plate_too_few_sources']) {
      for (const starSearchRan of [true, false]) {
        const banner = formatSolveJobProgressBanner(
          'synthetic-pose.png',
          job('succeeded'),
          { solution: sample(name), starSearchRan },
          receipt.sourceAsset
        );
        expect(banner, name).not.toMatch(RAW_STATUS);
        expect(banner, name).toMatch(/^Loaded "synthetic-pose\.png" /);
      }
    }
    expect(formatSolveJobProgressBanner('synthetic-pose.png', job('running'))).not.toMatch(RAW_STATUS);
    expect(formatSolveJobProgressBanner('synthetic-pose.png', job('failed', 'stopped'))).not.toMatch(RAW_STATUS);
  });
});

describe('the banner of a clip discovered on the laptop (verdict finding 2)', () => {
  it('makes no statement about points of light, whatever count its solver wrote, and opens at the solved frame with the predicted layer on', () => {
    const session = buildSessionFromDiscoveredMedia(discovered({ ...sample('horizon_predicted_only'), frameTimeSec: 3 }));
    // Discovery runs only the Moon and horizon solver, which looks for no other point of light: the line says only
    // that the predicted sky can be opened
    expect(session.lastStatusBanner).toMatch(
      /^Loaded "synthetic\.mov" \(SHA-256 [0-9a-f]{12}…\)\. You can open the sky predicted for your clip's time and place\.$/
    );
    expect(session.currentSourceTimeSec).toBe(3);
    expect(session.isPlaying).toBe(false);
    expect(session.spatialView.activeOverlays.predictedEphemerides).toBe(true);
    expect(session.navigationHistory.anchorMomentCheckpoint.sourceTimeSec).toBe(3);
    // That solver writes 1 or 0 as its count, whether it found the Moon; no number there is a count of points of light
    for (const n of [0, 1, 5, 6, 40, 1000]) {
      const line = afterPrefix(buildSessionFromDiscoveredMedia(discovered(unmatched(n))).lastStatusBanner, DISCOVERED_PREFIX);
      expect(line, `count ${n}`).toBe("You can open the sky predicted for your clip's time and place.");
    }
  });

  it('says nothing of points of light, of a sky being shown, or of a spot being the Moon, whatever solution the clip comes with', () => {
    const horizonPose: ObservationSolution = { ...POSE, poseEstimate: { ...POSE.poseEstimate!, method: 'moon_horizon' } };
    const lines = new Map<string, string>();
    for (const [name, solution] of [
      ['horizon_predicted_only', sample('horizon_predicted_only')],
      ['horizon_approximate', sample('horizon_approximate')],
      ['a Moon and horizon pose', horizonPose],
      ['plate_too_few_sources', sample('plate_too_few_sources')],
    ] as const) {
      const line = afterPrefix(buildSessionFromDiscoveredMedia(discovered(solution)).lastStatusBanner, DISCOVERED_PREFIX);
      lines.set(name, line);
      expect(line, name).not.toMatch(/points? of light|showing|too few|found/i);
      expect(line, name).not.toMatch(RAW_STATUS);
      expect(line.replace(/taking a bright spot to be the (Moon|Sun)/g, ''), name).not.toMatch(/Moon|Sun/);
    }
    expect(lines.get('horizon_approximate')).toBe(
      'Lined up roughly, not checked against stars. How far off it may be is not known, so the sky is not drawn over your clip.'
    );
    expect(lines.get('a Moon and horizon pose')).toBe(
      'Lined up by taking a bright spot to be the Moon, with the skyline taken to be level, within about 3 degrees; not checked against stars.'
    );
    expect(lines.get('plate_too_few_sources')).toBe('No sky could be worked out for your clip.');
  });

  it('claims no lining up for a pose estimate that arrives without its WCS, and draws nothing from it (decision 4)', () => {
    // The contract refuses the shape; a discovered clip's solution is not parsed, so it could arrive
    const { wcs: _wcs, ...poseWithoutWcs } = POSE;
    const session = buildSessionFromDiscoveredMedia(discovered(poseWithoutWcs as ObservationSolution));
    expect(afterPrefix(session.lastStatusBanner, DISCOVERED_PREFIX)).toBe("You can open the sky predicted for your clip's time and place.");
    expect(predictedOf(session.observationSolution!).length).toBeGreaterThan(0);
    expect(linedUpSky(session).kind).toBe('none');
  });
});

describe('the line that offers the predicted sky (verdict note b)', () => {
  const OFFER = /You can open the sky predicted for your (photo|clip)'s time and place\.$/;
  const showPredicted = (session: ObservationSession) =>
    dispatchAlbedianAction(session, {
      type: 'sky.show',
      requestId: 'show-predicted',
      expectedViewRevision: session.viewRevision,
      mode: 'predicted',
    });

  it('is said only where nothing predicted is drawn over the photo yet and opening the predicted sky works', () => {
    const { wcs: _wcs, ...poseWithoutWcs } = POSE;
    const offered = [
      buildSessionFromIngestReceipt(poseReceipt(POSE, 'image'), { solution: unmatched(4) }),
      buildSessionFromIngestReceipt(poseReceipt(POSE, 'video'), { job: job('succeeded'), solution: unmatched(40) }),
      buildSessionFromDiscoveredMedia(discovered(sample('horizon_predicted_only'))),
      buildSessionFromDiscoveredMedia(discovered(poseWithoutWcs as ObservationSolution)),
    ];
    for (const session of offered) {
      expect(session.lastStatusBanner).toMatch(OFFER);
      // Not shown yet: the photo view draws no predicted marker for it
      expect(session.spatialView.mode).toBe('source_2d');
      expect(linedUpSky(session).kind).toBe('none');
      // And it opens, by the action that the Show Predicted Sky button and the voice path dispatch
      const opened = showPredicted(session);
      expect(opened.outcome.status).toBe('completed');
      expect(opened.session.spatialView.mode).toBe('registered_sky');
      expect(opened.session.spatialView.activeOverlays.predictedEphemerides).toBe(true);
    }
  });

  it('is not said where the sky is already lined up over the photo, matched to stars, or cannot be opened', () => {
    const upload = (solution: ObservationSolution) => buildSessionFromIngestReceipt(poseReceipt(solution, 'image'), { solution });
    const linedUp = upload(POSE);
    expect(linedUpSky(linedUp).kind).toBe('shown');
    const calibrated = upload(sample('plate_calibrated'));
    const unresolved = upload(sample('plate_too_few_sources'));
    for (const session of [linedUp, calibrated, unresolved]) expect(session.lastStatusBanner).not.toMatch(/You can open/);
    // An unresolved sky has no predicted sky to open, and its line does not offer one
    expect(showPredicted(unresolved).outcome.status).toBe('rejected');
  });
});
