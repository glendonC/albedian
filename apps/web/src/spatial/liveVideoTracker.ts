import { CelestialFinding } from '@albedian/contracts';

export interface AspectFitRect {
  x: number;
  y: number;
  width: number;
  height: number;
  scaleX: number;
  scaleY: number;
}

/**
 * Compute an aspect-preserving rectangle (`object-fit: contain`) for rendering
 * portrait (e.g. 1344x1792 Meta Ray-Ban Display Glasses) or landscape (1920x1080)
 * observations inside the spatial stage without geometric distortion.
 */
export function computeAspectFitRect(
  srcWidth: number,
  srcHeight: number,
  viewportWidth: number,
  viewportHeight: number
): AspectFitRect {
  const safeSrcW = Math.max(1, srcWidth);
  const safeSrcH = Math.max(1, srcHeight);
  const safeVpW = Math.max(1, viewportWidth);
  const safeVpH = Math.max(1, viewportHeight);

  const srcAspect = safeSrcW / safeSrcH;
  const vpAspect = safeVpW / safeVpH;

  let drawW = safeVpW;
  let drawH = safeVpH;
  if (srcAspect < vpAspect) {
    // Portrait or narrower than viewport: fit height, pillarbox left/right
    drawH = safeVpH;
    drawW = Math.round(drawH * srcAspect);
  } else {
    // Wider than viewport: fit width, letterbox top/bottom
    drawW = safeVpW;
    drawH = Math.round(drawW / srcAspect);
  }

  const x = Math.round((safeVpW - drawW) * 0.5);
  const y = Math.round((safeVpH - drawH) * 0.5);

  return {
    x,
    y,
    width: drawW,
    height: drawH,
    scaleX: drawW / safeSrcW,
    scaleY: drawH / safeSrcH,
  };
}

let sharedProbeCanvas: HTMLCanvasElement | null = null;

/**
 * Real-time 60fps optical beacon + 2D horizon silhouette tracker for wearable video playback.
 * Tracks camera translation `(duPx, dvPx)` on an offscreen 160x120 buffer so that both
 * observed reticles and WCS constellation overlays move smoothly with the observer's head.
 */
export function computeLiveVideoTrackingOffset(params: {
  videoEl: HTMLVideoElement | null;
  moonFinding: CelestialFinding | undefined;
  srcWidth: number;
  srcHeight: number;
}): [number, number] {
  const { videoEl, moonFinding, srcWidth, srcHeight } = params;
  if (
    !videoEl ||
    videoEl.readyState < 2 ||
    typeof document === 'undefined'
  ) {
    return [0, 0];
  }

  const probeW = 160;
  const probeH = 120;
  if (!sharedProbeCanvas) {
    sharedProbeCanvas = document.createElement('canvas');
    sharedProbeCanvas.width = probeW;
    sharedProbeCanvas.height = probeH;
  }
  const octx = sharedProbeCanvas.getContext('2d', { willReadFrequently: true });
  if (!octx) return [0, 0];

  octx.drawImage(videoEl, 0, 0, probeW, probeH);
  const imgData = octx.getImageData(0, 0, probeW, probeH).data;

  // 1. Primary tracker: upper-sky celestial beacon (e.g. Moon) when visible
  if (moonFinding?.sourceDetection) {
    const skyRows = Math.floor(probeH * 0.25);
    let maxLuma = 0;
    for (let i = 0; i < probeW * skyRows * 4; i += 4) {
      const l = 0.2126 * imgData[i]! + 0.7152 * imgData[i + 1]! + 0.0722 * imgData[i + 2]!;
      if (l > maxLuma) maxLuma = l;
    }

    if (maxLuma >= 75) {
      const thresh = maxLuma * 0.65;
      let sumX = 0;
      let sumY = 0;
      let sumW = 0;
      for (let y = 0; y < skyRows; y++) {
        for (let x = 0; x < probeW; x++) {
          const idx = (y * probeW + x) * 4;
          const l = 0.2126 * imgData[idx]! + 0.7152 * imgData[idx + 1]! + 0.0722 * imgData[idx + 2]!;
          if (l >= thresh) {
            const wgt = l - thresh + 1;
            sumX += x * wgt;
            sumY += y * wgt;
            sumW += wgt;
          }
        }
      }
      if (sumW > 0) {
        const liveSrcX = (sumX / sumW) * (srcWidth / probeW);
        const liveSrcY = (sumY / sumW) * (srcHeight / probeH);
        return [
          liveSrcX - moonFinding.sourceDetection.xPx,
          liveSrcY - moonFinding.sourceDetection.yPx,
        ];
      }
    }
  }

  // 2. Fallback tracker: 2D horizon / foreground silhouette centroid in middle band
  const yStart = Math.floor(probeH * 0.35);
  const yEnd = Math.floor(probeH * 0.75);
  let gradSumX = 0;
  let gradSumY = 0;
  let gradWeight = 0;

  for (let y = yStart; y < yEnd; y++) {
    for (let x = 1; x < probeW - 1; x++) {
      const idxAbove = ((y - 1) * probeW + x) * 4;
      const idxBelow = ((y + 1) * probeW + x) * 4;
      const lAbove =
        0.2126 * imgData[idxAbove]! +
        0.7152 * imgData[idxAbove + 1]! +
        0.0722 * imgData[idxAbove + 2]!;
      const lBelow =
        0.2126 * imgData[idxBelow]! +
        0.7152 * imgData[idxBelow + 1]! +
        0.0722 * imgData[idxBelow + 2]!;
      const dy = Math.max(0, lBelow - lAbove);
      if (dy > 8) {
        gradSumX += x * dy;
        gradSumY += y * dy;
        gradWeight += dy;
      }
    }
  }

  if (gradWeight > 0) {
    const normX = gradSumX / gradWeight / probeW - 0.5;
    const normY = gradSumY / gradWeight / probeH - 0.55;
    return [-normX * srcWidth * 0.18, -normY * srcHeight * 0.18];
  }

  return [0, 0];
}
