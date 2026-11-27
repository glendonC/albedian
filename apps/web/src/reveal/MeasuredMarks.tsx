import React, { useEffect, useState } from 'react';
import { LOOK_GLYPHS } from '@albedian/design-system';
import { MeasuredDetectionsSchema, type ObservationSession, type SourceDetection } from '@albedian/contracts';
import { sessionPhotoRect, sourcePixelToViewport } from '../components/sourcePhotoRect.js';
import { useKeepClear, WORKING_KEEP_CLEAR } from './keepClear.js';
import type { Box } from './predictedSkyMarks.js';

/**
 * wait-measured-one (UIUX-first-use-wow-READY.md item 3, RULED at the lead's line stamped 03:22 on 25 Nov; the lead's
 * order of 27 Nov after the restart): the wait shows what it has found. When the solve's Measured stage ends its job
 * record holds `measuredAtUTC`, and `GET /api/ingest/jobs/{job_id}/measured` serves the detections it measured
 * (PLATFORM's wait-detections-one, on main): each a point of light measured in the photo's own pixels, observed
 * evidence with no sky claim. Each appears on the photo at its measured place as the Observed class's glyph
 * (LOOK_GLYPHS.observed), brightest first as marks arrive (MOTION-READY-web.md `--mo-mark`: 67 ms each, all within
 * 330 ms). Nothing is drawn that the route did not serve; nothing before the stamp, nothing when the answer names
 * another photo.
 */

/** A job id as the runner writes it, before it goes into a request path; anything else asks the server nothing. */
const JOB_ID = /^[A-Za-z0-9_-]{1,80}$/;

/** The detections a running solve measured for this photo, once its record says they are published; none before. */
export function useMeasuredDetections(jobId: string | undefined, sha256: string | undefined, measuredAtUTC: string | undefined): readonly SourceDetection[] {
  const [shown, setShown] = useState<{ key: string; detections: readonly SourceDetection[] } | null>(null);
  const key = jobId && sha256 && measuredAtUTC && JOB_ID.test(jobId) ? `${jobId}|${sha256}` : null;
  useEffect(() => {
    if (key === null) return undefined;
    let left = false;
    void (async () => {
      try {
        const res = await fetch(`/api/ingest/jobs/${jobId}/measured`);
        if (!res.ok) return;
        const parsed = MeasuredDetectionsSchema.safeParse(await res.json());
        if (!left && parsed.success && parsed.data.sourceAssetSha256 === sha256) setShown({ key, detections: parsed.data.detections });
      } catch {
        // No answer: nothing is drawn
      }
    })();
    return () => {
      left = true;
    };
  }, [key]);
  return shown !== null && shown.key === key ? shown.detections : NONE;
}
const NONE: readonly SourceDetection[] = [];

/**
 * M1 (UIUX-first-use-wow-READY.md, item 3's measured marks, RULED 12:08 on 27 Nov): the wait draws the brightest hundred
 * detections inside the shown photo, by measured flux, as astrometry.net's own source plot never draws every one and the
 * app's solver matches from about the brightest hundred; fewer when fewer were measured, none when none were.
 */
export const MEASURED_MARKS_MAX = 100;

export interface MeasuredMark {
  xPx: number;
  yPx: number;
  /** Its place in the arrival, brightest (by measured flux) first, among the marks drawn. */
  rank: number;
  /** The detection it draws, as the route served it: a matched star's own detection is its finding's sourceDetection. */
  detection: SourceDetection;
}

/**
 * Whether two detections are the same measured centre: the route and the solution write it to float noise apart (9e-13
 * px measured on rp-b-01, its 43 matched stars), while another detection stands 6 px or more away.
 */
export const SAME_DETECTION_PX = 0.01;
export const sameDetection = (a: Pick<SourceDetection, 'xPx' | 'yPx'>, b: Pick<SourceDetection, 'xPx' | 'yPx'>) =>
  Math.abs(a.xPx - b.xPx) <= SAME_DETECTION_PX && Math.abs(a.yPx - b.yPx) <= SAME_DETECTION_PX;

const GLYPH = LOOK_GLYPHS.observed;
/**
 * M2: the glyph's scale on the photo, the size the reveal draws its rings at that width, so a matched mark changes
 * drawing in place at its own size: one unit a pixel on the laptop (its ring 5.5, by the reveal's ring of 6), on the
 * phone as the reveal scales its own ring there (4.5 for 6).
 */
const scaleOf = (phone: boolean) => (phone ? 0.75 : 1);
/** How far a mark's drawing reaches from its centre: the glyph's ring and half its stroke, at its scale. */
const reachOf = (phone: boolean) => (5.5 + GLYPH.strokeWidth / 2) * scaleOf(phone);
/** Whether two boxes meet; touching counts, as a ring that touches a kept place is not drawn (UIUX-keepclear-READY.md). */
const meets = (a: Box, b: Box) => a.x0 <= b.x1 && b.x0 <= a.x1 && a.y0 <= b.y1 && b.y0 <= a.y1;

/**
 * Where the wait's marks stand on screen: each detection's stored pixel through the photo's turn and crop (as the
 * reveal places a finding's), the brightest hundred inside the photo as the window shows it; then a mark under a kept
 * place is not drawn and no other takes its turn (M1), as the reveal's rings.
 */
export function measuredMarksOf(
  session: ObservationSession,
  detections: readonly SourceDetection[],
  viewportWidthPx: number,
  viewportHeightPx: number,
  phone = false,
  clear: readonly Box[] = []
): MeasuredMark[] {
  const asset = session.sourceAsset;
  if (!asset || detections.length === 0) return [];
  const rect = sessionPhotoRect(session, viewportWidthPx, viewportHeightPx);
  if (!rect) return [];
  const x0 = Math.max(0, rect.leftPx);
  const y0 = Math.max(0, rect.topPx);
  const x1 = Math.min(viewportWidthPx, rect.leftPx + rect.widthPx);
  const y1 = Math.min(viewportHeightPx, rect.topPx + rect.heightPx);
  const r = reachOf(phone);
  return [...detections]
    .sort((a, b) => b.flux - a.flux)
    .map((detection) => ({ detection, at: sourcePixelToViewport(detection.xPx, detection.yPx, asset.width, asset.height, viewportWidthPx, viewportHeightPx, session.cropTransform, asset.displayOrientation) }))
    .filter(({ at }) => at.xPx >= x0 && at.xPx <= x1 && at.yPx >= y0 && at.yPx <= y1)
    .slice(0, MEASURED_MARKS_MAX)
    .filter(({ at }) => !clear.some((box) => meets({ x0: at.xPx - r, x1: at.xPx + r, y0: at.yPx - r, y1: at.yPx + r }, box)))
    .map(({ detection, at }, rank) => ({ xPx: at.xPx, yPx: at.yPx, rank, detection }));
}

/** One mark's drawing: the Observed class's glyph at its place and scale. */
const MarkGlyph: React.FC<{ mark: MeasuredMark; phone: boolean; className: string }> = ({ mark, phone, className }) => (
  <g className={className} transform={`translate(${mark.xPx} ${mark.yPx}) scale(${scaleOf(phone)})`} style={{ '--mark-i': mark.rank } as React.CSSProperties}>
    {GLYPH.parts.map((part, index) =>
      part.kind === 'circle' ? (
        <circle key={index} r={part.r} fill={part.filled ? 'currentColor' : 'none'} stroke={part.filled ? undefined : 'currentColor'} strokeWidth={part.filled ? undefined : GLYPH.strokeWidth} />
      ) : (
        <path key={index} d={part.d} fill="none" stroke="currentColor" strokeWidth={GLYPH.strokeWidth} />
      )
    )}
  </g>
);

export const MeasuredMarks: React.FC<{
  session: ObservationSession;
  jobId: string | undefined;
  measuredAtUTC: string | undefined;
  viewportWidthPx: number;
  viewportHeightPx: number;
  phone?: boolean;
  /** M5: a failure or Cancel ended the wait: every mark leaves where it stands on frame out, with the wait's words. */
  leaving?: boolean;
  /** Told the marks drawn, so the reveal can hand them over in place at its first frame (M4). */
  onDrawn?: (marks: readonly MeasuredMark[]) => void;
}> = ({ session, jobId, measuredAtUTC, viewportWidthPx, viewportHeightPx, phone = false, leaving = false, onDrawn }) => {
  const detections = useMeasuredDetections(jobId, session.sourceAsset?.sha256, measuredAtUTC);
  const clear = useKeepClear(detections.length > 0, WORKING_KEEP_CLEAR);
  const marks = measuredMarksOf(session, detections, viewportWidthPx, viewportHeightPx, phone, clear);
  useEffect(() => {
    if (!leaving) onDrawn?.(marks);
  });
  if (marks.length === 0) return null;
  return (
    <svg
      className={leaving ? 'measured-marks is-leaving' : 'measured-marks'}
      data-testid="measured-marks"
      data-evidence-class="observed"
      aria-hidden="true"
      focusable="false"
      style={{ '--mark-n': marks.length } as React.CSSProperties}
    >
      {marks.map((mark) => (
        <MarkGlyph key={mark.rank} mark={mark} phone={phone} className="measured-mark" />
      ))}
    </svg>
  );
};

/**
 * M4 and M5 at the reveal's first frame, by the lock-on's rows with nothing travelling (a matched star's place is its
 * own detection): the wait's marks stand where they stood; a matched star's mark changes to its matched drawing in place
 * (this drawing out on the colour time while the reveal's mark comes in over it), and every other mark (all of them when
 * nothing matched) leaves where it stands on frame out.
 */
export const MeasuredHandOver: React.FC<{ marks: readonly MeasuredMark[]; matched: ReadonlySet<number>; phone?: boolean }> = ({ marks, matched, phone = false }) =>
  marks.length === 0 ? null : (
    <svg className="measured-marks is-handing" data-testid="measured-handover" data-evidence-class="observed" aria-hidden="true" focusable="false">
      {marks.map((mark) => (
        <MarkGlyph key={mark.rank} mark={mark} phone={phone} className={matched.has(mark.rank) ? 'measured-mark is-changing' : 'measured-mark is-leaving'} />
      ))}
    </svg>
  );
