import React, { useEffect, useRef, useState } from 'react';
import type { ObservationSession, SourceAsset } from '@albedian/contracts';
import { getEpistemicBadgeDescriptor } from '@albedian/evidence';
import { goToMomentSentence, poseBasisSentence, poseUncertaintySentence } from '../services/skyLayerWords.js';
import { linedUpSky } from './poseProjection.js';
import {
  decodedFrameMatches,
  isSourcePhotoShown,
  orientedSize,
  sourcePhotoDisplayRect,
  useViewportSize,
} from './sourcePhotoRect.js';

/** The reveal's edge fade: clear at each side edge, whole from 56 px in. */
export const SIDE_FADE = 'linear-gradient(to right, transparent 0, #000 56px, #000 calc(100% - 56px), transparent 100%)';
/** The fade's size with Original pixels pressed: its two 56 px edges outside the photo, so none of it shows. */
const SIDE_FADE_OPEN = 'calc(100% + 112px) 100%';

export interface SourcePhotoStageProps {
  session: ObservationSession;
  viewportWidthPx?: number;
  viewportHeightPx?: number;
  /** Told after each load whether the browser's frame disagrees with the asset's orientation. */
  onFrameMismatch?: (mismatch: boolean) => void;
  /** The photo alone, with no layer label: the working screen, whose approved picture has none (arrive, 2026-11-12). */
  bare?: boolean;
  /** The photo at 55 percent: a failed job's screen, where it stays in its place (failed, decided 2026-11-12). */
  dimmed?: boolean;
  /** The photo's left and right edges fade into the black over 56 px (reveal, approved 2026-11-12); the pixels between are untouched. */
  fadeSides?: boolean;
  /**
   * Original pixels pressed (the motion record): the fade's transparent edges move 56 px past each side, so the photo is
   * whole, on the words-out time; released, they come back on the words-in time. The fade's own string is unchanged.
   */
  fadeOpen?: boolean;
  /** Real sky is drawn over the photo: the photo stays beneath it. */
  underSky?: boolean;
  /** Told the clip's element as it mounts and unmounts, for the clip's player (INTERIM: UIUX-clip-QUESTION.md). */
  onVideo?: (video: HTMLVideoElement | null) => void;
  /** At phone width, the mask that fades the photo's slot top and bottom and hides it beyond (reveal, approved, iPhone 1). */
  endsMask?: string;
}

/**
 * The person's own photo or clip, drawn as uploaded in the source view (AGENTS.md section 1: real
 * observation first). It covers the 3D canvas so no modeled scene shows around or through it, and
 * it applies no filter, blend or tone change: the pixels on screen are the file's pixels (a failed job's screen
 * draws them at 55 percent, the one change, and only there). Markers
 * use the same rectangle (sourcePhotoRect.ts), so each sits on the pixel it was measured at.
 *
 * A phone stores a portrait photo turned on its side with an orientation tag. The browser draws the
 * photo upright by its default (image-orientation: from-image), reading the same tags the ingest
 * reads into `displayOrientation`; the box is fitted to that upright shape and the markers are turned
 * by the same `displayOrientation`. If the decoded frame has the other shape, the two disagree, so the
 * stage says so and the markers are hidden rather than misplaced.
 */
/**
 * The notice for a file the browser cannot draw (C18, decided by the lead 12:47): the kind's plain name, never a media
 * type: HEIC photos, HEVC clips, or this kind of file where there is no plain name.
 */
/** Plays a clip element; a browser that refuses (it is muted, so none should) leaves it where it stands. */
function playAgain(video: HTMLVideoElement): void {
  const started = video.play();
  if (started && typeof started.catch === 'function') started.catch(() => {});
}

export function undrawableNotice(asset: Pick<SourceAsset, 'mediaKind' | 'mimeType' | 'videoCodec'>): string {
  const clip = asset.mediaKind === 'video';
  const type = (asset.mimeType ?? '').toLowerCase();
  const codec = (asset.videoCodec ?? '').toLowerCase();
  const format = !clip && (type === 'image/heic' || type === 'image/heif') ? 'HEIC photos' : clip && (codec === 'hevc' || codec === 'h265') ? 'HEVC clips' : 'this kind of file';
  return `This browser cannot show ${format}. Your ${clip ? 'clip' : 'photo'} was stored and solved as it is; open Albedian in Safari on a Mac or an iPhone to see it here.`;
}

export const SourcePhotoStage: React.FC<SourcePhotoStageProps> = ({
  session,
  viewportWidthPx,
  viewportHeightPx,
  onFrameMismatch,
  bare = false,
  dimmed = false,
  fadeSides = false,
  fadeOpen = false,
  underSky = false,
  onVideo,
  endsMask,
}) => {
  const windowSize = useViewportSize();
  const viewW = viewportWidthPx ?? windowSize.widthPx;
  const viewH = viewportHeightPx ?? windowSize.heightPx;
  const [failedUri, setFailedUri] = useState<string | null>(null);
  const [natural, setNatural] = useState<{ uri: string; widthPx: number; heightPx: number } | null>(null);
  const [mismatchUri, setMismatchUri] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  const asset = session.sourceAsset;
  const clockSec = session.currentSourceTimeSec;

  // Under Real sky the photo stays drawn beneath it, so the two cross-fade and the photo is there to come back to
  const shown = asset !== undefined && (isSourcePhotoShown(session) || underSky);
  const seekToClock = (video: HTMLVideoElement) => {
    if (Number.isFinite(clockSec) && clockSec >= 0) video.currentTime = clockSec;
  };

  // A clip shows the frame the session's clock names, the one every marker's time gate reads
  // (poseProjection.ts shownAtFrame). The builders open a clip with the clock on the solved frame
  // (apiClient.ts); a seek by voice or the command box (media.seek) and Back to Moment set it. Each
  // video element seeks when its own metadata loads (onLoadedMetadata below), so a clip that leaves
  // the source view and comes back lands on the clock's frame again; one already loaded seeks here
  // when the clock changes.
  useEffect(() => {
    const video = videoRef.current;
    if (video && video.readyState >= 1) seekToClock(video);
  }, [asset?.uri, clockSec, shown]);

  // Back to Moment (clip-playing-checkpoint-1, AGENTS.md 1.5): when the recording comes back on screen, from the sky
  // (where the clip stayed drawn under it, and may have played on) or drawn again from 3D (onLoadedMetadata below), the
  // clip stands at the restored frame and plays again if the restored session says it was playing. This runs only when
  // the recording comes back on screen (a new clip element has no metadata yet at its first draw), so a clip on screen
  // all along is never started here: its own Play and pause are the person's
  const onSource = isSourcePhotoShown(session);
  useEffect(() => {
    const video = videoRef.current;
    if (!onSource || !video || video.readyState < 1) return;
    seekToClock(video);
    if (session.isPlaying && video.paused) playAgain(video);
  }, [onSource]);

  if (!asset || !shown) return null;

  // The receipt's measured size, turned upright, is the frame the photo is shown in. The decoded size
  // (as the browser drew it) stands in while the receipt has not arrived, and when the browser turned the
  // photo differently from its tag, so the box always has the shape of what is on screen
  const measured = asset.width > 1 && asset.height > 1;
  const naturalForUri = natural && natural.uri === asset.uri ? natural : null;
  const upright = orientedSize(asset.width, asset.height, asset.displayOrientation);
  const mismatched = mismatchUri === asset.uri;
  const useDecoded = (!measured || mismatched) && naturalForUri !== null;
  const shownW = useDecoded ? naturalForUri.widthPx : upright.widthPx;
  const shownH = useDecoded ? naturalForUri.heightPx : upright.heightPx;
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
    // The browser turns the photo by its tag; set here so no stylesheet can switch that off
    imageOrientation: 'from-image',
    ...(dimmed ? { opacity: 0.55 } : {}),
    // A job that stops dims the photo on the view time (the motion record), so the transition stands before it dims
    ...(fadeSides ? {} : { transition: 'opacity var(--look-mo-dur-view) var(--look-mo-ease-std)' }),
    ...(fadeSides
      ? {
          maskImage: SIDE_FADE,
          WebkitMaskImage: SIDE_FADE,
          maskSize: fadeOpen ? SIDE_FADE_OPEN : '100% 100%',
          WebkitMaskSize: fadeOpen ? SIDE_FADE_OPEN : '100% 100%',
          maskPosition: 'center',
          WebkitMaskPosition: 'center',
          maskRepeat: 'no-repeat',
          WebkitMaskRepeat: 'no-repeat',
          transition: fadeOpen
            ? 'mask-size var(--look-mo-dur-words-out) var(--look-mo-ease-std)'
            : 'mask-size var(--look-mo-dur-words-in) var(--look-mo-ease-std) var(--look-mo-delay-words-in)',
        }
      : {}),
    ...(endsMask ? { maskImage: endsMask, WebkitMaskImage: endsMask } : {}),
  };
  const failed = failedUri === asset.uri;
  const kind = asset.mediaKind === 'video' ? 'clip' : 'photo';
  const badge = getEpistemicBadgeDescriptor('observed');
  // The predicted sky lined up by a pose (poseProjection.ts): drawn now, waiting for the clip to go to its
  // frame, or not there. The markers are hidden over a mismatched frame, so nothing is said about them then
  const linedUp = mismatched ? { kind: 'none' as const } : linedUpSky(session);
  const predictedBadge = getEpistemicBadgeDescriptor('predicted');

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
          {undrawableNotice(asset)}
        </p>
      ) : asset.mediaKind === 'video' ? (
        <video
          ref={(el) => {
            videoRef.current = el;
            onVideo?.(el);
          }}
          src={asset.uri}
          muted
          playsInline
          preload="auto"
          aria-label="Your clip as uploaded"
          style={mediaStyle}
          onError={() => setFailedUri(asset.uri)}
          onLoadedMetadata={(e) => {
            seekToClock(e.currentTarget);
            reportDecodedSize(e.currentTarget.videoWidth, e.currentTarget.videoHeight);
            // Drawn again at Back to Moment from 3D with the restored session playing: it plays from the restored frame
            if (session.isPlaying && onSource && e.currentTarget.paused) playAgain(e.currentTarget);
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
      {mismatched && !failed && (
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
          This browser turned your {kind} differently from how it was taken, so its marks are hidden rather than drawn
          in the wrong places.
        </p>
      )}
      {!bare && (
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
          <span title={badge.label}>{badge.shortCode}</span> · Your {kind} ·{' '}
          {failed ? 'not shown by this browser' : mismatched ? 'as this browser turned it' : 'original pixels'}
        </span>
        <p className="alb-layer-credits">
          {/* A marker's light, never its star: only a catalog_matched finding is a known star, and a marker may sit
              on a point of light no star was matched to or on a bright spot taken to be the Moon */}
          {failed
            ? linedUp.kind === 'shown'
              ? 'Each solid marker sits where its light was measured in the stored file; the dashed ones are drawn by the app.'
              : 'Each marker sits where its light was measured in the stored file.'
            : mismatched
              ? 'Not enhanced or retouched. The markers are hidden because this view disagrees with the orientation tag.'
              : linedUp.kind === 'shown'
                ? 'Shown as uploaded: not enhanced or retouched. Each solid marker sits on the pixel it was measured at; the dashed ones are drawn by the app.'
                : 'Shown as uploaded: not enhanced or retouched. Each marker sits on the pixel it was measured at.'}
        </p>
        {linedUp.kind === 'shown' && (
          <p
            className="alb-layer-credits"
            data-testid="lined-up-sky-disclosure"
            data-evidence-class="predicted"
            role="note"
          >
            <span title={predictedBadge.label} style={{ fontFamily: 'var(--alb-font-mono)' }}>
              {predictedBadge.shortCode}
            </span>{' '}
            · {poseBasisSentence(linedUp.pose, asset.mediaKind)} {poseUncertaintySentence(linedUp.pose)}
          </p>
        )}
        {linedUp.kind === 'go_to' && (
          <p
            className="alb-layer-credits"
            data-testid="lined-up-sky-go-to"
            role="status"
            style={{ fontVariantNumeric: 'tabular-nums' }}
          >
            {goToMomentSentence(linedUp.frameTimeSec)}
          </p>
        )}
      </div>
      )}
    </div>
  );
};
