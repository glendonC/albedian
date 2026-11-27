import React, { useEffect, useState } from 'react';
import { LOOK_GLYPHS } from '@albedian/design-system';
import { MeasuredDetectionsSchema, type ObservationSession, type SourceDetection } from '@albedian/contracts';
import { sessionPhotoRect, sourcePixelToViewport } from '../components/sourcePhotoRect.js';

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

export interface MeasuredMark {
  xPx: number;
  yPx: number;
  /** Its place in the arrival, brightest (by measured flux) first. */
  rank: number;
}

/**
 * Where each detection stands on screen (its stored pixel through the photo's turn and crop, as the reveal places a
 * finding's), brightest first; one outside the photo as shown or the window is not drawn.
 */
export function measuredMarksOf(session: ObservationSession, detections: readonly SourceDetection[], viewportWidthPx: number, viewportHeightPx: number): MeasuredMark[] {
  const asset = session.sourceAsset;
  if (!asset || detections.length === 0) return [];
  const rect = sessionPhotoRect(session, viewportWidthPx, viewportHeightPx);
  if (!rect) return [];
  const x0 = Math.max(0, rect.leftPx);
  const y0 = Math.max(0, rect.topPx);
  const x1 = Math.min(viewportWidthPx, rect.leftPx + rect.widthPx);
  const y1 = Math.min(viewportHeightPx, rect.topPx + rect.heightPx);
  return [...detections]
    .sort((a, b) => b.flux - a.flux)
    .map((d) => sourcePixelToViewport(d.xPx, d.yPx, asset.width, asset.height, viewportWidthPx, viewportHeightPx, session.cropTransform, asset.displayOrientation))
    .filter((p) => p.xPx >= x0 && p.xPx <= x1 && p.yPx >= y0 && p.yPx <= y1)
    .map((p, rank) => ({ xPx: p.xPx, yPx: p.yPx, rank }));
}

const GLYPH = LOOK_GLYPHS.observed;
/** The glyph's scale on the photo: one unit a pixel on the laptop (its ring 5.5, by the reveal's ring of 6); on the phone
 * as the reveal scales its own ring there (4.5 for 6). */
const scaleOf = (phone: boolean) => (phone ? 0.75 : 1);

export const MeasuredMarks: React.FC<{
  session: ObservationSession;
  jobId: string | undefined;
  measuredAtUTC: string | undefined;
  viewportWidthPx: number;
  viewportHeightPx: number;
  phone?: boolean;
}> = ({ session, jobId, measuredAtUTC, viewportWidthPx, viewportHeightPx, phone = false }) => {
  const detections = useMeasuredDetections(jobId, session.sourceAsset?.sha256, measuredAtUTC);
  const marks = measuredMarksOf(session, detections, viewportWidthPx, viewportHeightPx);
  if (marks.length === 0) return null;
  const s = scaleOf(phone);
  return (
    <svg
      className="measured-marks"
      data-testid="measured-marks"
      data-evidence-class="observed"
      aria-hidden="true"
      focusable="false"
      style={{ '--mark-n': marks.length } as React.CSSProperties}
    >
      {marks.map((mark) => (
        <g key={mark.rank} className="measured-mark" transform={`translate(${mark.xPx} ${mark.yPx}) scale(${s})`} style={{ '--mark-i': mark.rank } as React.CSSProperties}>
          {GLYPH.parts.map((part, index) =>
            part.kind === 'circle' ? (
              <circle key={index} r={part.r} fill={part.filled ? 'currentColor' : 'none'} stroke={part.filled ? undefined : 'currentColor'} strokeWidth={part.filled ? undefined : GLYPH.strokeWidth} />
            ) : (
              <path key={index} d={part.d} fill="none" stroke="currentColor" strokeWidth={GLYPH.strokeWidth} />
            )
          )}
        </g>
      ))}
    </svg>
  );
};
