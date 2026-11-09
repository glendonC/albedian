import React, { useEffect, useRef, useState } from 'react';
import type { ObservationSession } from '@albedian/contracts';
import { getEpistemicBadgeDescriptor } from '@albedian/evidence';
import { isSourcePhotoShown, sourcePhotoDisplayRect, useViewportSize } from './sourcePhotoRect.js';

export interface SourcePhotoStageProps {
  session: ObservationSession;
  viewportWidthPx?: number;
  viewportHeightPx?: number;
}

/**
 * The person's own photo or clip, drawn as uploaded in the source view (AGENTS.md section 1: real
 * observation first). It covers the 3D canvas so no modeled scene shows around or through it, and
 * it applies no filter, blend or tone change: the pixels on screen are the file's pixels. Markers
 * use the same rectangle (sourcePhotoRect.ts), so each sits on the pixel it was measured at.
 */
export const SourcePhotoStage: React.FC<SourcePhotoStageProps> = ({
  session,
  viewportWidthPx,
  viewportHeightPx,
}) => {
  const windowSize = useViewportSize();
  const viewW = viewportWidthPx ?? windowSize.widthPx;
  const viewH = viewportHeightPx ?? windowSize.heightPx;
  const [failedUri, setFailedUri] = useState<string | null>(null);
  const [natural, setNatural] = useState<{ uri: string; widthPx: number; heightPx: number } | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  const asset = session.sourceAsset;
  const frameTimeSec = session.observationSolution?.frameTimeSec ?? 0;

  // A clip shows the frame the sky was solved on
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const seek = () => {
      if (Number.isFinite(frameTimeSec) && frameTimeSec >= 0) video.currentTime = frameTimeSec;
    };
    if (video.readyState >= 1) seek();
    video.addEventListener('loadedmetadata', seek);
    return () => video.removeEventListener('loadedmetadata', seek);
  }, [asset?.uri, frameTimeSec]);

  if (!asset || !isSourcePhotoShown(session)) return null;

  // The receipt's measured size is the frame the detections are in; the decoded size stands in
  // only while the receipt has not arrived
  const measured = asset.width > 1 && asset.height > 1;
  const naturalForUri = natural && natural.uri === asset.uri ? natural : null;
  const srcW = measured ? asset.width : naturalForUri?.widthPx ?? asset.width;
  const srcH = measured ? asset.height : naturalForUri?.heightPx ?? asset.height;
  const rect = sourcePhotoDisplayRect(srcW, srcH, viewW, viewH, session.cropTransform);
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
          onLoadedMetadata={(e) =>
            setNatural({
              uri: asset.uri,
              widthPx: e.currentTarget.videoWidth,
              heightPx: e.currentTarget.videoHeight,
            })
          }
        />
      ) : (
        <img
          src={asset.uri}
          alt="Your photo as uploaded"
          draggable={false}
          style={mediaStyle}
          onError={() => setFailedUri(asset.uri)}
          onLoad={(e) =>
            setNatural({
              uri: asset.uri,
              widthPx: e.currentTarget.naturalWidth,
              heightPx: e.currentTarget.naturalHeight,
            })
          }
        />
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
          <span title={badge.label}>{badge.shortCode}</span> · Your {asset.mediaKind === 'video' ? 'clip' : 'photo'} · original pixels
        </span>
        <p className="alb-layer-credits">
          Shown as uploaded: not enhanced or retouched. Each marker sits on the pixel it was measured at.
        </p>
      </div>
    </div>
  );
};
