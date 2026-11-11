import fs from 'node:fs';
import path from 'node:path';
import {
  ObservationSolutionSchema,
  type CelestialFinding,
  type IngestReceipt,
  type ObservationSession,
  type ObservationSolution,
} from '@albedian/contracts';
import { buildSessionFromIngestReceipt } from '../../services/apiClient';

// The solutions the Python side writes (tests/integration/test_unregistered_detections.py), synthetic throughout
const FIXTURE = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, '../../../../../tests/fixtures/python_contract_solutions.json'), 'utf8')
) as Record<string, unknown>;

export const FIXTURE_NAMES = Object.keys(FIXTURE);
export const sample = (name: string): ObservationSolution => ObservationSolutionSchema.parse(FIXTURE[name]);
export const POSE = sample('pose_moon_level_head');
// The pose sample's frame: its WCS has crpix at the centre of a 960 x 540 frame (the Python builder's WIDTH and HEIGHT)
export const FRAME = { widthPx: 960, heightPx: 540 };
export const CLIP_FPS = 30;

export const predictedOf = (solution: ObservationSolution): CelestialFinding[] =>
  solution.findings.filter((f) => f.evidenceClass === 'predicted');
export const starsOf = (solution: ObservationSolution): CelestialFinding[] =>
  predictedOf(solution).filter((f) => f.id.startsWith('finding-predicted-star-'));

/** A synthetic clip's frame rate and length: 30 fps and 10 s unless a test names others. */
export interface ClipFacts {
  fpsNumerator: number;
  fpsDenominator: number;
  durationMs: number;
}
const CLIP: ClipFacts = { fpsNumerator: CLIP_FPS, fpsDenominator: 1, durationMs: 10_000 };

/** A receipt for a synthetic upload of the pose sample's frame size, with the fixtures' synthetic moment and place. */
export function poseReceipt(solution: ObservationSolution, mediaKind: 'image' | 'video', clipFacts: ClipFacts = CLIP): IngestReceipt {
  const clip = mediaKind === 'video';
  const timeSource = clip
    ? { container: 'quicktime' as const, tags: ['com.apple.quicktime.creationdate'] }
    : { container: 'exif' as const, tags: ['DateTimeOriginal', 'OffsetTimeOriginal'] };
  const placeSource = clip
    ? { container: 'quicktime' as const, tags: ['com.apple.quicktime.location.ISO6709'] }
    : { container: 'exif' as const, tags: ['GPSLatitude', 'GPSLongitude'] };
  return {
    sourceAsset: {
      id: solution.sourceAssetId,
      title: clip ? 'synthetic-pose.mov' : 'synthetic-pose.png',
      mediaKind,
      sha256: solution.sourceSha256!,
      uri: clip ? '/synthetic/pose.mov' : '/synthetic/pose.png',
      width: FRAME.widthPx,
      height: FRAME.heightPx,
      durationMs: clip ? clipFacts.durationMs : 0,
      ...(clip ? { fpsNumerator: clipFacts.fpsNumerator, fpsDenominator: clipFacts.fpsDenominator } : {}),
      videoCodec: clip ? 'h264' : 'png',
      audioStreams: [],
      capturedAtUTC: '2030-01-01T02:00:00Z',
      observerLocation: { latDeg: 30.0, lonDeg: -40.0 },
      metadataSources: { capturedAtUTC: timeSource, observerLocation: placeSource },
      privacyClass: 'private',
      immutable: true,
    },
    hints: {
      captureTime: {
        localDateTime: '2030-01-01T02:00:00',
        utcOffsetMinutes: 0,
        utc: '2030-01-01T02:00:00Z',
        marks: 'recorded',
        source: timeSource,
      },
      location: { latDeg: 30.0, lonDeg: -40.0, source: placeSource },
    },
    alreadyStored: false,
  };
}

/** The upload builder's session for a solution, as a still or a clip of the pose sample's frame size. */
export function poseSession(
  solution: ObservationSolution = POSE,
  mediaKind: 'image' | 'video' = 'image',
  clipFacts: ClipFacts = CLIP
): ObservationSession {
  return buildSessionFromIngestReceipt(poseReceipt(solution, mediaKind, clipFacts), { solution });
}

/**
 * The pose sample as a clip lined up on the frame at `frameTimeSec`: its observed Moon measured on that frame, and
 * one more observed point measured on another frame (`otherFrameTimeSec`), as a detector run on another frame gives.
 */
export function clipPoseSolution(frameTimeSec: number, otherFrameTimeSec: number): ObservationSolution {
  const moon = POSE.findings.find((f) => f.id === 'finding-observed-moon')!;
  const late: CelestialFinding = {
    ...moon,
    id: 'finding-observed-other-frame',
    title: 'Point source (synthetic, another frame)',
    sourceDetection: { ...moon.sourceDetection!, xPx: 300, yPx: 400, frameTimeSec: otherFrameTimeSec },
  };
  return ObservationSolutionSchema.parse({
    ...POSE,
    frameTimeSec,
    findings: [
      ...POSE.findings.map((f) =>
        f.sourceDetection ? { ...f, sourceDetection: { ...f.sourceDetection, frameTimeSec } } : f
      ),
      late,
    ],
  });
}
