import React, { useEffect, useRef, useState } from 'react';
import type { ObservationSession } from '@albedian/contracts';
import { getEpistemicBadgeDescriptor } from '@albedian/evidence';
import {
  decodedFrameMatches,
  isSourcePhotoShown,
  orientedSize,
  sourcePhotoDisplayRect,
  useViewportSize,
} from './sourcePhotoRect.js';

export interface SourcePhotoStageProps {
  session: ObservationSession;
  viewportWidthPx?: number;
  viewportHeightPx?: number;
  /** Told after each load whether the browser's frame disagrees with the asset's orientation. */
  onFrameMismatch?: (mismatch: boolean) => void;
}

/**
 * The person's own photo or clip, drawn as uploaded in the source view (AGENTS.md section 1: real
 * observation first). It covers the 3D canvas so no modeled scene shows around or through it, and
 * it applies no filter, blend or tone change: the pixels on screen are the file's pixels. Markers
 * use the same rectangle (sourcePhotoRect.ts), so each sits on the pixel it was measured at.
 *
 * A phone stores a portrait photo turned on its side with an orientation tag. The browser draws the
 * photo upright by its default (image-orientation: from-image), reading the same tags the ingest
 * reads into `displayOrientation`; the box is fitted to that upright shape and the markers are turned
 * by the same `displayOrientation`. If the decoded frame has the other shape, the two disagree, so the
 * stage says so and the markers are hidden rather than misplaced.
 */
export const SourcePhotoStage: React.FC<SourcePhotoStageProps> = ({
  session,
  viewportWidthPx,
  viewportHeightPx,
  onFrameMismatch,
}) => {
  const windowSize = useViewportSize();
  const viewW = viewportWidthPx ?? windowSize.widthPx;
  const viewH = viewportHeightPx ?? windowSize.heightPx;
  const [failedUri, setFailedUri] = useState<string | null>(null);
  const [natural, setNatural] = useState<{ uri: string; widthPx: number; heightPx: number } | null>(null);
  const [mismatchUri, setMismatchUri] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  const asset = session.sourceAsset;
  const frameTimeSec = session.observationSolution?.frameTimeSec ?? 0;

  const shown = asset !== undefined && isSourcePhotoShown(session);
  const seekToSolvedFrame = (video: HTMLVideoElement) => {
    if (Number.isFinite(frameTimeSec) && frameTimeSec >= 0) video.currentTime = frameTimeSec;
  };

  // A clip shows the frame the sky was solved on. Each video element seeks when its own metadata
  // loads (onLoadedMetadata below), so a clip that leaves the source view and comes back lands on
  // that frame again; one already loaded seeks here when the solved frame changes.
  useEffect(() => {
    const video = videoRef.current;
    if (video && video.readyState >= 1) seekToSolvedFrame(video);
  }, [asset?.uri, frameTimeSec, shown]);

  if (!asset || !shown) return null;

  // The receipt's measured size, turned upright, is the frame the photo is shown in; the decoded
  // size (already upright) stands in only while the receipt has not arrived
  const measured = asset.width > 1 && asset.height > 1;
  const naturalForUri = natural && natural.uri === asset.uri ? natural : null;
  const upright = orientedSize(asset.width, asset.height, asset.displayOrientation);
  const shownW = measured ? upright.widthPx : naturalForUri?.widthPx ?? upright.widthPx;
  const shownH = measured ? upright.heightPx : naturalForUri?.heightPx ?? upright.heightPx;
  const rect = sourcePhotoDisplayRect(shownW, shownH, viewW, viewH, session.cropTransform);
  const reportDecodedSize = (decodedW: number, decodedH: number) => {
    setNatural({ uri: asset.uri, widthPx: decodedW, heightPx: decodedH });
    const mismatch = measured && !decodedFrameMatches(decodedW, decodedH, upright.widthPx, upright.heightPx);
    setMismatchUri(mismatch ? asset.uri : null);
    onFrameMismatch?.(mismatch);
  };
  const mediaStyle: React.CSSProperties = {
    position: 'absolute',
    left: `${rect.leftPx}px`,
    top: `${rect.topPx}px`,
    width: `${rect.widthPx}px`,
    height: `${rect.heightPx}px`,
    maxWidth: 'none',
    display: 'block',
  };
  const failed = failedUri === asset.uri;
  const badge = getEpistemicBadgeDescriptor('observed');

  return (
    <div
      className="alb-source-stage"
      data-testid="source-photo-stage"
      data-evidence-class="observed"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 2,
        overflow: 'hidden',
        backgroundColor: '#000000',
        pointerEvents: 'none',
      }}
    >
      {failed ? (
        <p
          data-testid="source-photo-unavailable"
          role="status"
          style={{
            position: 'absolute',
            left: '50%',
            top: '50%',
            transform: 'translate(-50%, -50%)',
            width: 'min(420px, calc(100vw - 32px))',
            margin: 0,
            textAlign: 'center',
            fontSize: '14px',
            lineHeight: 1.5,
            color: 'var(--alb-text-secondary)',
          }}
        >
          This browser cannot display {asset.mimeType ?? 'this file type'}. The file was stored and solved as
          uploaded; open the app in a browser that shows it, such as Safari on iPhone or Mac, to see it here.
        </p>
      ) : asset.mediaKind === 'video' ? (
        <video
          ref={videoRef}
          src={asset.uri}
          muted
          playsInline
          preload="auto"
          aria-label="Your clip as uploaded"
          style={mediaStyle}
          onError={() => setFailedUri(asset.uri)}
          onLoadedMetadata={(e) => {
            seekToSolvedFrame(e.currentTarget);
            reportDecodedSize(e.currentTarget.videoWidth, e.currentTarget.videoHeight);
          }}
        />
      ) : (
        <img
          src={asset.uri}
          alt="Your photo as uploaded"
          draggable={false}
          style={mediaStyle}
          onError={() => setFailedUri(asset.uri)}
          onLoad={(e) => reportDecodedSize(e.currentTarget.naturalWidth, e.currentTarget.naturalHeight)}
        />
      )}
      {mismatchUri === asset.uri && !failed && (
        <p
          data-testid="source-photo-orientation-mismatch"
          role="status"
          style={{
            position: 'absolute',
            left: '50%',
            bottom: '16px',
            transform: 'translateX(-50%)',
            width: 'min(420px, calc(100vw - 32px))',
            margin: 0,
            textAlign: 'center',
            fontSize: '13px',
            lineHeight: 1.5,
            color: 'var(--alb-text-secondary)',
          }}
        >
          This browser turned the photo differently from its orientation tag, so the markers are hidden
          rather than shown in the wrong places.
        </p>
      )}
      <div
        className="alb-layer-disclosure"
        data-testid="source-photo-layer-label"
        aria-label="What this view shows"
        style={{ pointerEvents: 'none' }}
      >
        <span
          style={{
            fontFamily: 'var(--alb-font-micro)',
            fontSize: '10.5px',
            letterSpacing: '0.22em',
            textTransform: 'uppercase',
            color: 'var(--alb-text-secondary)',
          }}
        >
          <span title={badge.label}>{badge.shortCode}</span> · Your {asset.mediaKind === 'video' ? 'clip' : 'photo'} ·{' '}
          {failed ? 'not shown by this browser' : 'original pixels'}
        </span>
        <p className="alb-layer-credits">
          {failed
            ? 'Each marker sits where its star was measured in the stored file.'
            : 'Shown as uploaded: not enhanced or retouched. Each marker sits on the pixel it was measured at.'}
        </p>
      </div>
    </div>
  );
};
