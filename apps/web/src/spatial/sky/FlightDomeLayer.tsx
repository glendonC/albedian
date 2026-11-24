import React, { useEffect, useRef, useState } from 'react';
import { loadPanoramaRegistration } from './skyRegistration.js';
import { FLIGHT_MILKY_WAY_URL, createFlightDome, disposeFlightDome, drawFlightDome, type DomeCamera, type FlightDomeGpu } from './flightMilkyWay.js';

/**
 * milkyway-flight-one's steering dome in a canvas of its own (flight-stars-finish-one, 16:08): while the person steers,
 * the starless glow drawn as a dome turned with the camera, behind the star layer, so the Milky Way stays at its own
 * level, ungraded, while the stars in front of it go through the predicted sky's bloom and tone mapping. The star
 * layer's one frame loop draws it each frame through `drawRef`, with that frame's camera.
 */

/** The camera a dome frame is drawn through, with the window it fills. */
export type DomeView = DomeCamera & { readonly viewportWidthPx: number; readonly viewportHeightPx: number };

type DomeState = 'loading' | 'drawn' | 'failed';

export const FlightDomeLayer: React.FC<{
  /** The steering camera now; none draws nothing. */
  view: DomeView | null;
  viewportWidthPx: number;
  viewportHeightPx: number;
  /** Filled with this layer's draw, for the star layer's frame loop. */
  drawRef?: React.MutableRefObject<((view: DomeView) => void) | null>;
  /** Told true once the dome has drawn, false while it loads, when it fails, and as it leaves. */
  onDrawn?: (drawn: boolean) => void;
}> = ({ view, viewportWidthPx, viewportHeightPx, drawRef, onDrawn }) => {
  const canvas = useRef<HTMLCanvasElement>(null);
  const gpu = useRef<{ gl: WebGL2RenderingContext; dome: FlightDomeGpu } | null>(null);
  const [state, setState] = useState<DomeState>('loading');
  const dpr = typeof window === 'undefined' ? 1 : Math.min(2, window.devicePixelRatio || 1);
  const viewRef = useRef(view);
  viewRef.current = view;
  const told = useRef(onDrawn);
  told.current = onDrawn;

  const draw = (v: DomeView | null): void => {
    const g = gpu.current;
    const el = canvas.current;
    if (!g || !el || !v) return;
    g.gl.viewport(0, 0, el.width, el.height);
    drawFlightDome(g.gl, g.dome, v, v.viewportWidthPx, v.viewportHeightPx, dpr);
  };

  // The glow and the registration, once a mount, into this canvas's own context
  useEffect(() => {
    const el = canvas.current;
    const gl = el?.getContext('webgl2', { antialias: false, premultipliedAlpha: false }) ?? null;
    if (!gl) {
      setState('failed');
      return undefined;
    }
    let current = true;
    const image = new Image();
    image.decoding = 'async';
    const loaded = new Promise<HTMLImageElement>((resolve, reject) => {
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error('flight dome'));
    });
    image.src = FLIGHT_MILKY_WAY_URL;
    void Promise.all([loaded, loadPanoramaRegistration()]).then(
      ([img, registration]) => {
        if (!current) return;
        const dome = createFlightDome(gl, img, registration);
        if (!dome) {
          setState('failed');
          return;
        }
        gpu.current = { gl, dome };
        draw(viewRef.current);
        setState('drawn');
      },
      () => {
        if (current) setState('failed');
      }
    );
    return () => {
      current = false;
      if (gpu.current) disposeFlightDome(gl, gpu.current.dome);
      gpu.current = null;
    };
  }, []);

  // A new camera or window from the page draws a frame; the frame loop draws the rest
  useEffect(() => {
    draw(view);
  }, [view, viewportWidthPx, viewportHeightPx]);

  useEffect(() => {
    if (!drawRef) return undefined;
    drawRef.current = draw;
    return () => {
      drawRef.current = null;
    };
  });

  useEffect(() => {
    told.current?.(state === 'drawn');
  }, [state]);
  useEffect(() => () => told.current?.(false), []);

  return (
    <canvas
      ref={canvas}
      data-testid="flight-dome"
      data-state={state}
      aria-hidden="true"
      width={Math.round(viewportWidthPx * dpr)}
      height={Math.round(viewportHeightPx * dpr)}
      style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', display: 'block', pointerEvents: 'none' }}
    />
  );
};
