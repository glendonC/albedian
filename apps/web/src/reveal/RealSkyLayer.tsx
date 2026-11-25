import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { hasVerifiedWcs, type ObservationSession } from '@albedian/contracts';
import type { PhotoRect } from '../components/sourcePhotoRect.js';
import { PHONE_SLOT } from './phoneSlot.js';
import { heldPanoramaRegistration, panoramaRegistrationOnce } from '../spatial/sky/skyRegistration.js';
import { realSkyGrid } from './realSkyGrid.js';
import { keptSkyPicture, keptSkyPictureNow, prepareSkyPicture, skyProgram, warmPrograms } from '../spatial/sky/skyPicture.js';
import { FLIGHT_DOME_FRAGMENT, FLIGHT_DOME_VERTEX, FLIGHT_GLOW_FRAGMENT, FLIGHT_MILKY_WAY_URL } from '../spatial/sky/flightMilkyWay.js';

/** ESO's Milky Way panorama (eso0932a) as the app serves it, byte for byte as ESO's large file (textures/PROVENANCE.json). */
export const REAL_SKY_PANORAMA_URL = '/textures/eso_milkyway_panorama.jpg';

/** The grid's cell, in CSS pixels: the mapping is smooth at this scale, so each cell is drawn linearly in between. */
export const REAL_SKY_STEP_PX = 8;

/** Whether this browser can draw the layer (WebGL 2): where it cannot, Real sky cannot open. */
export function canDrawRealSky(): boolean {
  return typeof window !== 'undefined' && 'WebGL2RenderingContext' in window;
}

/** Whether the session shows Real sky: the measured sky view of a verified solve of a photo, where it can be drawn. */
export function isRealSkyShown(session: ObservationSession): boolean {
  const solution = session.observationSolution;
  return (
    session.sourceAsset !== undefined &&
    session.spatialView?.mode === 'registered_sky' &&
    solution !== undefined &&
    solution.status === 'calibrated' &&
    hasVerifiedWcs(solution) &&
    canDrawRealSky()
  );
}

/** The side columns' shade (reveal, approved): black at `edge` at the window's edges, `near` at `nearPx` outside the photo. */
export interface RealSkyShade {
  edge: number;
  near: number;
  nearPx: number;
}

export const REAL_SKY_SHADE: Readonly<RealSkyShade> = { edge: 0.74, near: 0.5, nearPx: 82 };

/**
 * The side columns' shade, none over the photo, so the words beside it stay legible; the stops follow the photo's own
 * frame.
 */
export function realSkyShade(rect: PhotoRect, viewportWidthPx: number, shade: Readonly<RealSkyShade> = REAL_SKY_SHADE): string {
  const left = rect.leftPx;
  const right = rect.leftPx + rect.widthPx;
  const px = (n: number) => `${Math.round(n * 10) / 10}px`;
  const black = (a: number) => `rgba(0, 0, 0, ${a})`;
  return `linear-gradient(to right, ${black(shade.edge)} 0px, ${black(shade.near)} ${px(left - shade.nearPx)}, ${black(0)} ${px(left)}, ${black(0)} ${px(right)}, ${black(shade.near)} ${px(right + shade.nearPx)}, ${black(shade.edge)} ${px(viewportWidthPx)})`;
}

/**
 * At phone width (reveal, approved 2026-11-12, iPhone 2; #p-real .sky::after): the bands above and below the photo's
 * slot shaded, none over the photo: 70 percent black at the top, 30 at 6 px above the slot, clear from 32 px inside it to
 * 38 px before its foot, 55 percent 22 px under it and 78 at the window's foot. There (fly, approved, iPhone;
 * #p-there .sky::after) the clear part ends 78 px before the slot's foot, 60 percent 2 px under it and 85 at the foot,
 * under the taller readout.
 */
export const PHONE_REAL_SKY_SHADE = { top: 0.7, aboveSlot: 0.3, belowSlot: 0.55, foot: 0.78 } as const;
const PHONE_THERE_SHADE = { belowSlot: 0.6, foot: 0.85, clearEndsPx: 78, belowPx: 2 } as const;
export function phoneRealSkyShade(viewportHeightPx: number, { there = false }: { there?: boolean } = {}): string {
  const s = PHONE_REAL_SKY_SHADE;
  const top = PHONE_SLOT.topPx;
  const bottom = PHONE_SLOT.topPx + PHONE_SLOT.heightPx;
  const clearEnds = bottom - (there ? PHONE_THERE_SHADE.clearEndsPx : 38);
  const below = bottom + (there ? PHONE_THERE_SHADE.belowPx : 22);
  const belowShade = there ? PHONE_THERE_SHADE.belowSlot : s.belowSlot;
  const foot = there ? PHONE_THERE_SHADE.foot : s.foot;
  const black = (a: number) => `rgba(0, 0, 0, ${a})`;
  return `linear-gradient(to bottom, ${black(s.top)} 0px, ${black(s.aboveSlot)} ${top - 6}px, ${black(0)} ${top + 32}px, ${black(0)} ${clearEnds}px, ${black(belowShade)} ${below}px, ${black(foot)} ${viewportHeightPx}px)`;
}

/**
 * There at phone width with D30's readout (UIUX-phone3d-READY.md item 2): the top bar's shade as before, clear from 32 px
 * inside the slot, then darkening from 70 px above the readout's top to 62 percent 10 px under it and 86 at the foot,
 * so the readout reads over the sky however tall its lines make it.
 */
export function phoneThereScrim(viewportHeightPx: number, readoutTopPx: number): string {
  const s = PHONE_REAL_SKY_SHADE;
  const top = PHONE_SLOT.topPx;
  const black = (a: number) => `rgba(0, 0, 0, ${a})`;
  return `linear-gradient(to bottom, ${black(s.top)} 0px, ${black(s.aboveSlot)} ${top - 6}px, ${black(0)} ${top + 32}px, ${black(0)} ${readoutTopPx - 70}px, ${black(0.62)} ${readoutTopPx + 10}px, ${black(0.86)} ${viewportHeightPx}px)`;
}

/**
 * The Credits page's line for what Real sky does to ESO's panorama (CC BY 4.0 asks that changes be indicated), in C29's
 * words (UIUX-webuxpieces-READY.md item 5: a change noted and briefly described, as Creative Commons' recommended
 * practice has it), with the layer's own numbers.
 */
export function realSkyChanges(shade: Readonly<RealSkyShade> = REAL_SKY_SHADE): string {
  return `In Real sky: redrawn to line up with a photo the app has matched, its pixels otherwise unchanged, and darkened beside the photo, up to ${Math.round(shade.edge * 100)} percent black (${Math.round(PHONE_REAL_SKY_SHADE.foot * 100)} on a phone).`;
}

const VERTEX = `#version 300 es
in vec2 aPos;
in vec2 aUv;
uniform vec2 uView;
uniform vec2 uOrigin;
out vec2 vUv;
void main() {
  vUv = aUv;
  vec2 p = aPos - uOrigin;
  gl_Position = vec4(p.x / uView.x * 2.0 - 1.0, 1.0 - p.y / uView.y * 2.0, 0.0, 1.0);
}`;

// The panorama's own pixels, unchanged: no grade (the approved picture shows them as ESO publishes them)
const FRAGMENT = `#version 300 es
precision highp float;
in vec2 vUv;
uniform sampler2D uPanorama;
out vec4 outColor;
void main() {
  outColor = vec4(texture(uPanorama, vUv).rgb, 1.0);
}`;

type LayerState = 'loading' | 'drawn' | 'failed';

/**
 * arrival-star-1: the window area the panorama covers while the flight brings the star flown to to its resting place
 * (look gap two): the window and the strip the whole move uncovers; the window alone with no move.
 */
export function realSkyArea(viewW: number, viewH: number, fullShiftPx: readonly [number, number] | null): { x0: number; y0: number; widthPx: number; heightPx: number } {
  const [dx, dy] = fullShiftPx ?? [0, 0];
  const x0 = Math.min(0, -dx) || 0;
  const y0 = Math.min(0, -dy) || 0;
  return { x0, y0, widthPx: Math.max(viewW, viewW - dx) - x0, heightPx: Math.max(viewH, viewH - dy) - y0 };
}

type RealSkyArea = ReturnType<typeof realSkyArea>;

/**
 * Draws the panorama's texture through the grid at `widthPx` by `heightPx` device pixels; false when the browser refuses
 * the program or has lost the context. Nothing here waits for the GPU (realsky-upload-one: no getError on the frame).
 */
function drawGrid(gl: WebGL2RenderingContext, texture: WebGLTexture, grid: NonNullable<ReturnType<typeof realSkyGrid>>, area: RealSkyArea, widthPx: number, heightPx: number, fragment: string): boolean {
  const program = skyProgram(gl, VERTEX, fragment);
  if (!program) return false;
  gl.useProgram(program);

  // Two triangles per cell
  const indices = new Uint32Array((grid.cols - 1) * (grid.rows - 1) * 6);
  let n = 0;
  for (let j = 0; j < grid.rows - 1; j++) {
    for (let i = 0; i < grid.cols - 1; i++) {
      const a = j * grid.cols + i;
      const b = a + 1;
      const c = a + grid.cols;
      const d = c + 1;
      indices.set([a, c, b, b, c, d], n);
      n += 6;
    }
  }
  const buffers: WebGLBuffer[] = [];
  const bind = (name: string, data: Float32Array) => {
    const buffer = gl.createBuffer();
    if (buffer) buffers.push(buffer);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
    const at = gl.getAttribLocation(program, name);
    gl.enableVertexAttribArray(at);
    gl.vertexAttribPointer(at, 2, gl.FLOAT, false, 0, 0);
  };
  bind('aPos', grid.positions);
  bind('aUv', grid.uvs);
  const elements = gl.createBuffer();
  if (elements) buffers.push(elements);
  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, elements);
  gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, indices, gl.STATIC_DRAW);

  gl.activeTexture(gl.TEXTURE0);
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.viewport(0, 0, widthPx, heightPx);
  gl.uniform2f(gl.getUniformLocation(program, 'uView'), area.widthPx, area.heightPx);
  gl.uniform2f(gl.getUniformLocation(program, 'uOrigin'), area.x0, area.y0);
  gl.uniform1i(gl.getUniformLocation(program, 'uPanorama'), 0);
  gl.clearColor(0, 0, 0, 1);
  gl.clear(gl.COLOR_BUFFER_BIT);
  gl.drawElements(gl.TRIANGLES, indices.length, gl.UNSIGNED_INT, 0);
  // The draw keeps what it needs until the GPU is done with it; the kept context gathers no buffers across draws
  for (const buffer of buffers) gl.deleteBuffer(buffer);
  return !gl.isContextLost();
}

/** Where sky pictures cannot be kept: the image element uploaded whole into the canvas's own context, as before. */
function loadImage(url: string): Promise<HTMLImageElement> {
  const image = new Image();
  image.decoding = 'async';
  const loaded = new Promise<HTMLImageElement>((resolve, reject) => {
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('panorama'));
  });
  image.src = url;
  return loaded;
}

function drawOwn(canvas: HTMLCanvasElement, image: HTMLImageElement, grid: NonNullable<ReturnType<typeof realSkyGrid>>, area: RealSkyArea, fragment: string): boolean {
  const gl = canvas.getContext('webgl2', { antialias: false, premultipliedAlpha: false });
  if (!gl) return false;
  const texture = gl.createTexture();
  if (!texture) return false;
  gl.bindTexture(gl.TEXTURE_2D, texture);
  // Row 0 of the image is the top, at v = 1
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, image);
  gl.generateMipmap(gl.TEXTURE_2D);
  // Around the sky the image wraps; over the poles it does not
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  return drawGrid(gl, texture, grid, area, canvas.width, canvas.height, fragment);
}

/**
 * DECIDED-BY-LEAD 19:45 (no painted frame of Go there or Back to Moment without a sky): a change that resizes the
 * canvas clears it, so where the picture is already up the layer draws again at once, before the browser paints; null
 * when it is not (the draw then waits for the picture, as before).
 */
function paintNow(canvas: HTMLCanvasElement, url: string, session: ObservationSession, viewW: number, viewH: number, area: RealSkyArea, fragment: string = FRAGMENT, pose = false): boolean | null {
  const picture = keptSkyPictureNow(url);
  const registration = heldPanoramaRegistration();
  if (!picture || !registration) return null;
  const grid = realSkyGrid(session, viewW, viewH, registration, REAL_SKY_STEP_PX, area, pose);
  if (!grid) return false;
  return picture.paint(canvas, (gl, texture) => drawGrid(gl, texture, grid, area, canvas.width, canvas.height, fragment));
}

/**
 * Draws `url`'s picture through the photo's solve into the canvas: from the page's kept context where this browser keeps
 * sky pictures (realsky-upload-one), otherwise from an image element in the canvas's own context. Resolves false when
 * the browser refuses a step, and undefined when the layer stopped wanting the draw first.
 */
async function drawRealSky(canvas: HTMLCanvasElement, url: string, session: ObservationSession, viewW: number, viewH: number, area: RealSkyArea, wanted: () => boolean, fragment: string = FRAGMENT, pose = false): Promise<boolean | undefined> {
  const [registration, picture] = await Promise.all([panoramaRegistrationOnce(), keptSkyPicture(url)]);
  const grid = realSkyGrid(session, viewW, viewH, registration, REAL_SKY_STEP_PX, area, pose);
  if (!wanted()) return undefined;
  if (!grid) return false;
  if (picture) return picture.paint(canvas, (gl, texture) => drawGrid(gl, texture, grid, area, canvas.width, canvas.height, fragment));
  const image = await loadImage(url);
  if (!wanted()) return undefined;
  return drawOwn(canvas, image, grid, area, fragment);
}

let realSkyWarmed = false;

/**
 * realsky-upload-one: starts the uploads of the photograph and of the flight's starless glow while the reveal is at rest,
 * before Real sky or Go there asks for them.
 */
export function prepareRealSky(): void {
  // The registration too where the pictures are kept, so a draw can use both in the frame that asks
  void keptSkyPicture(REAL_SKY_PANORAMA_URL)
    .then((picture) => (picture ? panoramaRegistrationOnce() : undefined))
    .catch(() => undefined);
  prepareSkyPicture(FLIGHT_MILKY_WAY_URL);
  // Their programs, and the steering dome's over the same glow, linked once a page at rest too, so no first draw links
  if (realSkyWarmed) return;
  realSkyWarmed = warmPrograms([
    [VERTEX, FRAGMENT],
    [VERTEX, FLIGHT_GLOW_FRAGMENT],
    [FLIGHT_DOME_VERTEX, FLIGHT_DOME_FRAGMENT],
  ]);
}

/**
 * Real sky (reveal, approved 2026-11-12, laptop 2): the panorama fills the window through the photo's solve and the
 * app's registration, the side columns shaded. It fades in over the photo in 0.9 s once drawn (none with reduced
 * motion), and fades out the same way when `leaving`.
 */
export const RealSkyLayer: React.FC<{
  session: ObservationSession;
  rect: PhotoRect | null;
  viewportWidthPx: number;
  viewportHeightPx: number;
  leaving?: boolean;
  /** Told whether the panorama is drawn: false while it loads, when it fails, and as the layer leaves. */
  onDrawn?: (drawn: boolean) => void;
  /** In flight: told whether the starless glow is drawn, false while it loads, when it fails, and as the layer leaves. */
  onGlowDrawn?: (drawn: boolean) => void;
  /** In flight: told whether the glow failed, so the photograph has left (flight-sky-line-state-one); false as it leaves. */
  onGlowFailed?: (failed: boolean) => void;
  /** At phone width: the bands above and below the photo's slot shaded instead of the side columns. */
  phone?: boolean;
  /** There, after Go there: on the phone, the shade under the taller readout. */
  there?: boolean;
  /**
   * arrival-star-1 (look gap two): the flight's move of the view toward the star's resting place, whole and so far; the
   * panorama moves with the stars, its shade does not.
   */
  shift?: { readonly fullPx: readonly [number, number]; readonly nowPx: readonly [number, number] } | null;
  /** There on the phone: the readout's measured top, which D30's scrim rises to meet; the slot's shade without it. */
  readoutTopPx?: number | null;
}> = ({ session, rect, viewportWidthPx, viewportHeightPx, leaving = false, onDrawn, onGlowDrawn, onGlowFailed, phone = false, there = false, readoutTopPx = null, shift = null }) => {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [state, setState] = useState<LayerState>('loading');
  const moving = shift !== null && (shift.fullPx[0] !== 0 || shift.fullPx[1] !== 0);
  const area = realSkyArea(viewportWidthPx, viewportHeightPx, moving ? shift!.fullPx : null);
  // milkyway-flight-one: in flight the starless glow in a canvas of its own, asked for at the first Go there
  const glowCanvas = useRef<HTMLCanvasElement>(null);
  const [glowState, setGlowState] = useState<LayerState | 'idle'>('idle');
  const glowWanted = there || glowState !== 'idle';
  const dpr = typeof window === 'undefined' ? 1 : Math.min(2, window.devicePixelRatio || 1);
  // The reveal's status follows what is on screen (the auditor's verdict on unit-realsky-1)
  const told = useRef(onDrawn);
  told.current = onDrawn;
  useEffect(() => {
    told.current?.(state === 'drawn');
  }, [state]);
  useEffect(() => () => told.current?.(false), []);
  // milkyway-flight-one: in flight the photograph is hidden, so what the flight's line names is the glow (AUDIT-D 18:18)
  const toldGlow = useRef(onGlowDrawn);
  toldGlow.current = onGlowDrawn;
  useEffect(() => {
    toldGlow.current?.(glowState === 'drawn');
  }, [glowState]);
  useEffect(() => () => toldGlow.current?.(false), []);
  const toldGlowFailed = useRef(onGlowFailed);
  toldGlowFailed.current = onGlowFailed;
  useEffect(() => {
    toldGlowFailed.current?.(glowState === 'failed');
  }, [glowState]);
  useEffect(() => () => toldGlowFailed.current?.(false), []);

  // A layout effect: a new size clears the canvas in this commit, so it is drawn again before the browser paints
  useLayoutEffect(() => {
    const target = canvas.current;
    if (!target) return undefined;
    const now = paintNow(target, REAL_SKY_PANORAMA_URL, session, viewportWidthPx, viewportHeightPx, area);
    if (now !== null) {
      setState(now ? 'drawn' : 'failed');
      return undefined;
    }
    let current = true;
    void drawRealSky(target, REAL_SKY_PANORAMA_URL, session, viewportWidthPx, viewportHeightPx, area, () => current && canvas.current === target)
      .then((drawn) => {
        if (drawn !== undefined) setState(drawn ? 'drawn' : 'failed');
      })
      .catch(() => {
        if (current) setState('failed');
      });
    return () => {
      current = false;
    };
  }, [session.observationSolution, session.sourceAsset, session.cropTransform, viewportWidthPx, viewportHeightPx, area.x0, area.y0, area.widthPx, area.heightPx]);

  useLayoutEffect(() => {
    if (!glowWanted) return undefined;
    const target = glowCanvas.current;
    if (!target) return undefined;
    // go-there-predicted-1 (ruling A): at a predicted star the glow through the pose estimate's own WCS too
    const now = paintNow(target, FLIGHT_MILKY_WAY_URL, session, viewportWidthPx, viewportHeightPx, area, FLIGHT_GLOW_FRAGMENT, true);
    if (now !== null) {
      setGlowState(now ? 'drawn' : 'failed');
      return undefined;
    }
    let current = true;
    setGlowState('loading');
    void drawRealSky(target, FLIGHT_MILKY_WAY_URL, session, viewportWidthPx, viewportHeightPx, area, () => current && glowCanvas.current === target, FLIGHT_GLOW_FRAGMENT, true)
      .then((drawn) => {
        if (drawn !== undefined) setGlowState(drawn ? 'drawn' : 'failed');
      })
      .catch(() => {
        if (current) setGlowState('failed');
      });
    return () => {
      current = false;
    };
  }, [glowWanted, session.observationSolution, session.sourceAsset, session.cropTransform, viewportWidthPx, viewportHeightPx, area.x0, area.y0, area.widthPx, area.heightPx]);

  return (
    <div className={`real-sky${state === 'drawn' && !leaving ? ' is-shown' : ''}${there ? ' is-there' : ''}${glowState === 'drawn' ? ' is-glow-drawn' : glowState === 'failed' ? ' is-glow-failed' : ''}`} data-testid="real-sky" data-state={state} aria-hidden="true">
      <canvas
        ref={canvas}
        className="real-sky__canvas"
        width={Math.round(area.widthPx * dpr)}
        height={Math.round(area.heightPx * dpr)}
        style={moving ? { left: area.x0, top: area.y0, width: area.widthPx, height: area.heightPx, transform: `translate(${shift!.nowPx[0]}px, ${shift!.nowPx[1]}px)` } : undefined}
      />
      {glowWanted && (
        <canvas
          ref={glowCanvas}
          className={glowState === 'drawn' ? 'real-sky__glow is-drawn' : 'real-sky__glow'}
          data-state={glowState === 'idle' ? 'loading' : glowState}
          width={Math.round(area.widthPx * dpr)}
          height={Math.round(area.heightPx * dpr)}
          style={moving ? { left: area.x0, top: area.y0, width: area.widthPx, height: area.heightPx, transform: `translate(${shift!.nowPx[0]}px, ${shift!.nowPx[1]}px)` } : undefined}
        />
      )}
      {rect && <div className="real-sky__shade" style={{ backgroundImage: phone ? (there && readoutTopPx !== null ? phoneThereScrim(viewportHeightPx, readoutTopPx) : phoneRealSkyShade(viewportHeightPx, { there })) : realSkyShade(rect, viewportWidthPx) }} />}
    </div>
  );
};
