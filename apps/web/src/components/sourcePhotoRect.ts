import { useEffect, useState } from 'react';
import type { DisplayOrientation, ObservationSession } from '@albedian/contracts';

/** Where the photo is drawn on screen, in CSS pixels from the viewport's top left. */
export interface PhotoRect {
  leftPx: number;
  topPx: number;
  widthPx: number;
  heightPx: number;
}

/** The session's zoom (1 = fitted) and pan (fractions of the fitted photo's width and height). */
export interface PhotoCrop {
  zoom?: number;
  panX?: number;
  panY?: number;
}

/**
 * The size a stored frame is shown at: a quarter or three-quarter turn swaps width and height.
 * Detections and the WCS stay in stored pixels; only display applies the orientation.
 */
export function orientedSize(
  storedWidthPx: number,
  storedHeightPx: number,
  orientation?: DisplayOrientation
): { widthPx: number; heightPx: number } {
  const turned = (orientation?.quarterTurnsClockwise ?? 0) % 2 === 1;
  return turned
    ? { widthPx: storedHeightPx, heightPx: storedWidthPx }
    : { widthPx: storedWidthPx, heightPx: storedHeightPx };
}

/**
 * Where a stored pixel is shown once the frame is oriented as the camera meant it, in the contract's
 * canonical form: mirror left to right if `mirrored`, then turn clockwise a quarter at a time.
 */
export function orientStoredPoint(
  xPx: number,
  yPx: number,
  storedWidthPx: number,
  storedHeightPx: number,
  orientation?: DisplayOrientation
): { xPx: number; yPx: number } {
  let x = orientation?.mirrored ? storedWidthPx - xPx : xPx;
  let y = yPx;
  let height = storedHeightPx;
  let width = storedWidthPx;
  for (let turn = 0; turn < (orientation?.quarterTurnsClockwise ?? 0); turn++) {
    // A clockwise quarter turn sends (x, y) in a width x height frame to (height - y, x) in height x width
    [x, y] = [height - y, x];
    [width, height] = [height, width];
  }
  return { xPx: x, yPx: y };
}

/**
 * The largest rectangle with the photo's shape that fits the viewport, centred: the whole photo
 * shows, letterboxed or pillarboxed, and nothing is cropped or stretched.
 */
export function fitSourceToViewport(
  sourceWidthPx: number,
  sourceHeightPx: number,
  viewportWidthPx: number,
  viewportHeightPx: number
): PhotoRect {
  const srcW = Math.max(1, sourceWidthPx);
  const srcH = Math.max(1, sourceHeightPx);
  const viewW = Math.max(1, viewportWidthPx);
  const viewH = Math.max(1, viewportHeightPx);
  const scale = Math.min(viewW / srcW, viewH / srcH);
  const widthPx = srcW * scale;
  const heightPx = srcH * scale;
  return {
    leftPx: (viewW - widthPx) / 2,
    topPx: (viewH - heightPx) / 2,
    widthPx,
    heightPx,
  };
}

/** The fitted rectangle after the session's zoom about the photo centre and its pan. */
export function sourcePhotoDisplayRect(
  sourceWidthPx: number,
  sourceHeightPx: number,
  viewportWidthPx: number,
  viewportHeightPx: number,
  crop?: PhotoCrop
): PhotoRect {
  const fitted = fitSourceToViewport(sourceWidthPx, sourceHeightPx, viewportWidthPx, viewportHeightPx);
  const zoom = crop?.zoom ?? 1;
  const panX = crop?.panX ?? 0;
  const panY = crop?.panY ?? 0;
  const widthPx = fitted.widthPx * zoom;
  const heightPx = fitted.heightPx * zoom;
  const centreX = fitted.leftPx + fitted.widthPx * (0.5 + panX);
  const centreY = fitted.topPx + fitted.heightPx * (0.5 + panY);
  return {
    leftPx: centreX - widthPx / 2,
    topPx: centreY - heightPx / 2,
    widthPx,
    heightPx,
  };
}

/**
 * Where a stored pixel of the photo lands on screen: turned by the photo's display orientation, then
 * placed in the same rectangle the photo is drawn in.
 */
export function sourcePixelToViewport(
  xPx: number,
  yPx: number,
  sourceWidthPx: number,
  sourceHeightPx: number,
  viewportWidthPx: number,
  viewportHeightPx: number,
  crop?: PhotoCrop,
  orientation?: DisplayOrientation
): { xPx: number; yPx: number } {
  const shown = orientedSize(sourceWidthPx, sourceHeightPx, orientation);
  const p = orientStoredPoint(xPx, yPx, sourceWidthPx, sourceHeightPx, orientation);
  const rect = sourcePhotoDisplayRect(shown.widthPx, shown.heightPx, viewportWidthPx, viewportHeightPx, crop);
  return {
    xPx: rect.leftPx + (p.xPx / Math.max(1, shown.widthPx)) * rect.widthPx,
    yPx: rect.topPx + (p.yPx / Math.max(1, shown.heightPx)) * rect.heightPx,
  };
}

/**
 * Whether the frame the browser decoded has the shape the orientation implies. A browser that turns
 * the photo differently from the ingest (or not at all) shows a frame of the other shape.
 */
export function decodedFrameMatches(
  decodedWidthPx: number,
  decodedHeightPx: number,
  shownWidthPx: number,
  shownHeightPx: number
): boolean {
  if (decodedWidthPx <= 0 || decodedHeightPx <= 0) return true;
  const decoded = decodedWidthPx / decodedHeightPx;
  const shown = shownWidthPx / Math.max(1, shownHeightPx);
  return Math.abs(decoded - shown) <= 0.01 * shown;
}

/** The photo is drawn in the source view of a session that has one; the sky and 3D views draw the renderer. */
export function isSourcePhotoShown(session: ObservationSession): boolean {
  if (!session.sourceAsset) return false;
  return (session.spatialView?.mode ?? 'source_2d') === 'source_2d';
}

/** The browser window's size in CSS pixels, kept current on resize and rotation. */
export function useViewportSize(): { widthPx: number; heightPx: number } {
  const read = () => ({
    widthPx: typeof window === 'undefined' ? 1440 : window.innerWidth,
    heightPx: typeof window === 'undefined' ? 900 : window.innerHeight,
  });
  const [size, setSize] = useState(read);
  useEffect(() => {
    const onResize = () => setSize(read());
    window.addEventListener('resize', onResize);
    window.addEventListener('orientationchange', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      window.removeEventListener('orientationchange', onResize);
    };
  }, []);
  return size;
}
