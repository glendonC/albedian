import React, { useEffect, useRef, useState } from 'react';
import { loadPanoramaRegistration, type PanoramaRegistration } from './skyRegistration.js';
import { FLIGHT_MILKY_WAY_URL, createFlightDome, createFlightDomeOn, disposeFlightDome, drawFlightDome, type DomeCamera, type FlightDomeGpu } from './flightMilkyWay.js';
import { keptSkyPicture, skyPictureSourceOf, type KeptSkyPicture } from './skyPicture.js';

/**
 * milkyway-flight-one's steering dome in a canvas of its own (flight-stars-finish-one, 16:08): while the person steers,
 * the starless glow drawn as a dome turned with the camera, behind the star layer, so the Milky Way stays at its own
 * level, ungraded, while the stars in front of it go through the predicted sky's bloom and tone mapping. The star
 * layer's one frame loop draws it each frame through `drawRef`, with that frame's camera.
 */

/** The camera a dome frame is drawn through, with the window it fills. */
export type DomeView = DomeCamera & { readonly viewportWidthPx: number; readonly viewportHeightPx: number };

type DomeState = 'loading' | 'drawn' | 'failed';

/**
 * realsky-upload-one (LEAD ANSWER 19:21): where the page keeps sky pictures, the dome is drawn from the kept glow in the
 * page's kept context, its program and offset table made there once for the page, and each frame handed to this canvas
 * without a copy: the first steer decodes, uploads and links nothing. Elsewhere the glow goes into this canvas's own
 * context, as before.
 */
const keptDomes = new WeakMap<WebGLTexture, FlightDomeGpu | null>();
function keptDome(gl: WebGL2RenderingContext, glow: WebGLTexture, registration: PanoramaRegistration): FlightDomeGpu | null {
  if (!keptDomes.has(glow)) keptDomes.set(glow, createFlightDomeOn(gl, glow, registration));
  return keptDomes.get(glow)!;
}

/**
 * steer-hitch-one (LEAD ANSWER 27 Nov 03:02, AUDIT-D's catch): the steering dome's first paint through the kept context,
 * at the first steer, made its program (compiled, linked and waited for) and its first frame on the GPU (the draw's
 * pipeline and the frame handed over), the long task of the hand-over in the takes' CPU profiles: 7 to 10 ms on this
 * laptop, and 67 to 267 ms when a screenshot waited on the GPU in the same frame. Here the same paint is done once while
 * the flight rests (FlightStarsLayer calls it beside prepareCraftSky), at the window's size, into a canvas no one sees,
 * so the first steer's paint finds its program made and its pipeline warm. Where pictures cannot be kept it does nothing.
 */
export function prepareFlightDome(viewportWidthPx: number, viewportHeightPx: number, pixelRatio: number): void {
  if (typeof document === 'undefined') return;
  void Promise.all([keptSkyPicture(FLIGHT_MILKY_WAY_URL), loadPanoramaRegistration()]).then(
    ([picture, registration]) => {
      if (!picture) return;
      const target = document.createElement('canvas');
      target.width = Math.round(viewportWidthPx * pixelRatio);
      target.height = Math.round(viewportHeightPx * pixelRatio);
      // Any camera warms the same program and pipeline: looking along +z through a plain pinhole filling the window
      const view: DomeView = {
        rows: { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] },
        crpixX: viewportWidthPx / 2,
        crpixY: viewportHeightPx / 2,
        scaleRadPerPx: 1 / Math.max(1, viewportHeightPx),
        k1: 0,
        k2: 0,
        storedToScreen: [1, 0, 0, 0, 1, 0],
        viewportWidthPx,
        viewportHeightPx,
      };
      picture.paint(target, (gl, glow) => {
        const dome = keptDome(gl, glow, registration);
        if (!dome) return false;
        gl.viewport(0, 0, target.width, target.height);
        drawFlightDome(gl, dome, view, viewportWidthPx, viewportHeightPx, pixelRatio);
        return true;
      });
    },
    () => undefined
  );
}

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
  const kept = useRef<{ picture: KeptSkyPicture; registration: PanoramaRegistration } | null>(null);
  const [state, setState] = useState<DomeState>('loading');
  const dpr = typeof window === 'undefined' ? 1 : Math.min(2, window.devicePixelRatio || 1);
  const viewRef = useRef(view);
  viewRef.current = view;
  const told = useRef(onDrawn);
  told.current = onDrawn;

  // False when the kept road cannot draw (its context lost or the dome refused)
  const draw = (v: DomeView | null): boolean => {
    const el = canvas.current;
    const k = kept.current;
    if (k) {
      if (!el || !v) return true;
      return k.picture.paint(el, (gl, glow) => {
        const dome = keptDome(gl, glow, k.registration);
        if (!dome) return false;
        gl.viewport(0, 0, el.width, el.height);
        drawFlightDome(gl, dome, v, v.viewportWidthPx, v.viewportHeightPx, dpr);
        return true;
      });
    }
    const g = gpu.current;
    if (!g || !el || !v) return true;
    g.gl.viewport(0, 0, el.width, el.height);
    drawFlightDome(g.gl, g.dome, v, v.viewportWidthPx, v.viewportHeightPx, dpr);
    return true;
  };

  // The kept glow where the page keeps sky pictures; otherwise the glow and the registration, once a mount, into this
  // canvas's own context, as before
  useEffect(() => {
    let current = true;
    let own: WebGL2RenderingContext | null = null;
    const fromOwn = () => {
      const el = canvas.current;
      const gl = el?.getContext('webgl2', { antialias: false, premultipliedAlpha: false }) ?? null;
      if (!gl) {
        setState('failed');
        return;
      }
      own = gl;
      const image = new Image();
      image.decoding = 'async';
      const loaded = new Promise<HTMLImageElement>((resolve, reject) => {
        image.onload = () => resolve(image);
        image.onerror = () => reject(new Error('flight dome'));
      });
      image.src = skyPictureSourceOf(FLIGHT_MILKY_WAY_URL);
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
    };
    void Promise.all([keptSkyPicture(FLIGHT_MILKY_WAY_URL), loadPanoramaRegistration()]).then(
      ([picture, registration]) => {
        if (!current) return;
        if (!picture) {
          fromOwn();
          return;
        }
        kept.current = { picture, registration };
        setState(draw(viewRef.current) ? 'drawn' : 'failed');
      },
      () => {
        if (current) setState('failed');
      }
    );
    return () => {
      current = false;
      // The kept dome stays for the page; this canvas's own is freed
      if (gpu.current && own) disposeFlightDome(own, gpu.current.dome);
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
