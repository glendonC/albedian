import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { ObservationSession } from '@albedian/contracts';
import type { Vec3 } from '@albedian/camera-rig';
import { CatalogStarTileLoader } from '../catalogStars/CatalogStarTileLoader.js';
import { STAR_CATALOG_BASE_URL } from '../catalogStars/field/fieldTypes.js';
import { catalogStarTint } from '../catalogStars/catalogStarTint.js';
import { usesBMinusV, type CatalogStarCoreTile } from '../catalogStars/tileCodec.js';
import type { CatalogStarView } from '../catalogStars/tileLoaderTypes.js';
import { FLIGHT_DRAW_LIMIT_MAG, buildFlightTileArrays, flightCameraOf, flightViewCone } from '../flight/flightFrame.js';
import { FLIGHT_STAR_FRAGMENT, FLIGHT_STAR_VERTEX, flightStarUniforms } from '../flight/flightStarShader.js';
import { createFlightFinish, type FlightFinish } from '../flight/flightFinish.js';
import { starViewOfFlight, type StarView } from '../flight/starView.js';

/**
 * realsky-points-one (DECIDED-BY-LEAD 24 Nov 20:35, choice B; words 20:37): Real sky's stars, the Gaia and Hipparcos
 * stars at their catalog places, drawn from Earth through the photo's own solve by the flight's own vertex shader (so
 * each star sits where the flight's first frame puts it, at its catalog magnitude, through the one transfer) and
 * finished by the flight's finish (the predicted sky's bloom and ACES), on a canvas of their own that RealSkyLayer
 * shows over the starless glow, light added (reveal.css). Their depth is the flight's, one depth for the skies.
 * Drawn once every tile the view wants has come (or failed), then only when the view changes, never every frame.
 */

/** Where the tiles come from: the catalog's loader, or a test's stand-in. */
export interface RealSkyTileSource {
  setView(view: CatalogStarView): void;
  dispose(): void;
  /** How far the view's tiles have come; without it the stars count as drawn at the first tile. */
  getStats?(): { tilesWanted: number; tilesInFlight: number; tilesLoaded: number; failures: number };
}

export type OpenRealSkyTiles = (onTile: (tile: CatalogStarCoreTile) => void, onError: () => void) => Promise<RealSkyTileSource>;

const openCatalogTiles: OpenRealSkyTiles = (onTile, onError) =>
  CatalogStarTileLoader.open(STAR_CATALOG_BASE_URL, {
    onTileLoaded: onTile,
    onError: (error, key) => {
      console.warn(`[real sky stars] tile ${key.layer}/${key.pix} failed`, error);
      onError();
    },
  });

/** The finish for a canvas, its context, its size and pixel ratio (flightFinish.ts); a test may stand in for it. */
export type MakeRealSkyFinish = typeof createFlightFinish;

/** The area the layer covers, in window CSS pixels (RealSkyLayer's realSkyArea). */
export interface RealSkyStarsArea {
  readonly x0: number;
  readonly y0: number;
  readonly widthPx: number;
  readonly heightPx: number;
}

/** Where Real sky is seen from, and its floating origin: the Sun, as near to Earth as the catalog places tell. */
const earth = (): Vec3 => [0, 0, 0];

/**
 * The photo's own solve as the stars' camera, from Earth with the Sun as the floating origin (so a star's offset is its
 * catalog place and its magnitude its catalog one), its frame moved onto `area`; none without a verified solve.
 */
export function realSkyStarView(session: ObservationSession, viewportWidthPx: number, viewportHeightPx: number, area: RealSkyStarsArea): StarView | null {
  const camera = flightCameraOf(session, viewportWidthPx, viewportHeightPx);
  if (!camera) return null;
  const m = camera.storedToScreen;
  return {
    ...starViewOfFlight(camera),
    storedToScreen: [m[0], m[1], m[2] - area.x0, m[3], m[4], m[5] - area.y0],
    viewportWidthPx: area.widthPx,
    viewportHeightPx: area.heightPx,
    observerPc: earth(),
    referencePc: earth(),
  };
}

/**
 * The sky the tiles are asked for: the window's, to the stars' depth. Not the strip the flight's move uncovers: by then
 * the flight's own stars are drawn, so nothing loads during the move.
 */
export function realSkyStarCone(session: ObservationSession, viewportWidthPx: number, viewportHeightPx: number, limitMag: number): CatalogStarView | null {
  const camera = flightCameraOf(session, viewportWidthPx, viewportHeightPx);
  return camera ? flightViewCone(camera, limitMag) : null;
}

const UNIFORMS = ['uEye', 'uRowX', 'uRowY', 'uRowZ', 'uPix', 'uLens', 'uAffX', 'uAffY', 'uView', 'uLimit', 'uPixelRatio', 'uGain', 'uLinear'] as const;

interface TileGpu {
  vao: WebGLVertexArrayObject;
  buffers: WebGLBuffer[];
  count: number;
}

interface Gpu {
  gl: WebGL2RenderingContext;
  program: WebGLProgram;
  uniforms: Record<string, WebGLUniformLocation | null>;
  tiles: Map<string, TileGpu>;
  finish: FlightFinish | null;
}

function compile(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader | null {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  return gl.getShaderParameter(shader, gl.COMPILE_STATUS) ? shader : null;
}

function createGpu(canvas: HTMLCanvasElement, makeFinish: (canvas: HTMLCanvasElement, gl: WebGL2RenderingContext) => FlightFinish | null): Gpu | null {
  const gl = canvas.getContext('webgl2', { antialias: false, premultipliedAlpha: true, alpha: false });
  if (!gl) return null;
  const vs = compile(gl, gl.VERTEX_SHADER, FLIGHT_STAR_VERTEX);
  const fs = compile(gl, gl.FRAGMENT_SHADER, FLIGHT_STAR_FRAGMENT);
  const program = gl.createProgram();
  if (!vs || !fs || !program) return null;
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return null;
  const uniforms: Record<string, WebGLUniformLocation | null> = {};
  for (const name of UNIFORMS) uniforms[name] = gl.getUniformLocation(program, name);
  return { gl, program, uniforms, tiles: new Map(), finish: makeFinish(canvas, gl) };
}

/** One tile's stars placed from Earth (the Sun as the floating origin), as the flight uploads them. */
function uploadTile(gpu: Gpu, tile: CatalogStarCoreTile): TileGpu | null {
  const { gl, program } = gpu;
  const arrays = buildFlightTileArrays(tile, earth());
  const tints = new Float32Array(3 * tile.count);
  for (let i = 0; i < tile.count; i++) tints.set(catalogStarTint(tile.colorIndex[i] ?? Number.NaN, usesBMinusV(tile.flags[i] ?? 0)), 3 * i);
  const vao = gl.createVertexArray();
  if (!vao) return null;
  gl.bindVertexArray(vao);
  const buffers: WebGLBuffer[] = [];
  const bind = (name: string, data: Float32Array, size: number) => {
    const at = gl.getAttribLocation(program, name);
    const buffer = gl.createBuffer();
    if (!buffer || at < 0) return;
    buffers.push(buffer);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(at);
    gl.vertexAttribPointer(at, size, gl.FLOAT, false, 0, 0);
  };
  bind('aPos', arrays.positions, 3);
  bind('aPlaced', arrays.placed, 1);
  bind('aEarthPc', arrays.earthDistancePc, 1);
  bind('aMag', arrays.magnitude, 1);
  bind('aTint', tints, 3);
  gl.bindVertexArray(null);
  return { vao, buffers, count: tile.count };
}

/** Every star in linear light into the finish's buffer, then bloomed and tone mapped onto the canvas. */
function drawStars(gpu: Gpu, view: StarView, canvas: HTMLCanvasElement, pixelRatio: number, limitMag: number): void {
  const { gl, program, uniforms, finish } = gpu;
  const u = flightStarUniforms(view);
  finish?.begin();
  gl.viewport(0, 0, canvas.width, canvas.height);
  gl.clearColor(0, 0, 0, 1);
  gl.clear(gl.COLOR_BUFFER_BIT);
  gl.useProgram(program);
  gl.uniform1f(uniforms.uLinear!, finish ? 1 : 0);
  gl.uniform3fv(uniforms.uEye!, u.uEye);
  gl.uniform3fv(uniforms.uRowX!, u.uRowX);
  gl.uniform3fv(uniforms.uRowY!, u.uRowY);
  gl.uniform3fv(uniforms.uRowZ!, u.uRowZ);
  gl.uniform3fv(uniforms.uPix!, u.uPix);
  gl.uniform3fv(uniforms.uLens!, u.uLens);
  gl.uniform3fv(uniforms.uAffX!, u.uAffX);
  gl.uniform3fv(uniforms.uAffY!, u.uAffY);
  gl.uniform2fv(uniforms.uView!, u.uView);
  gl.uniform1f(uniforms.uLimit!, limitMag);
  gl.uniform1f(uniforms.uPixelRatio!, pixelRatio);
  gl.uniform1f(uniforms.uGain!, 1);
  // Light adds, as the flight's and the sky view's stars
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.ONE, gl.ONE);
  for (const entry of gpu.tiles.values()) {
    gl.bindVertexArray(entry.vao);
    gl.drawArrays(gl.POINTS, 0, entry.count);
  }
  gl.bindVertexArray(null);
  finish?.end();
}

export type RealSkyStarsState = 'loading' | 'drawn' | 'failed';

export const RealSkyStars: React.FC<{
  session: ObservationSession;
  viewportWidthPx: number;
  viewportHeightPx: number;
  /** The area RealSkyLayer's canvases cover, the move's strip included. */
  area: RealSkyStarsArea;
  /** Whether the layer shows them (RealSkyLayer decides, by what else is drawn). */
  shown: boolean;
  /** The canvas's place while the flight moves the view, as RealSkyLayer places its other canvases. */
  style?: React.CSSProperties;
  limitMag?: number;
  /** Told 'drawn' once every tile the view wants has come and the stars are drawn, 'failed' when none can be. */
  onState?: (state: RealSkyStarsState) => void;
  openTiles?: OpenRealSkyTiles;
  makeFinish?: MakeRealSkyFinish;
}> = ({ session, viewportWidthPx, viewportHeightPx, area, shown, style, limitMag = FLIGHT_DRAW_LIMIT_MAG, onState, openTiles = openCatalogTiles, makeFinish }) => {
  const canvas = useRef<HTMLCanvasElement>(null);
  const gpu = useRef<Gpu | null>(null);
  const source = useRef<RealSkyTileSource | null>(null);
  const [state, setState] = useState<RealSkyStarsState>('loading');
  const [count, setCount] = useState(0);
  const dpr = typeof window === 'undefined' ? 1 : Math.min(2, window.devicePixelRatio || 1);
  const view = useMemo(
    () => realSkyStarView(session, viewportWidthPx, viewportHeightPx, area),
    [session.observationSolution, session.sourceAsset, session.cropTransform, viewportWidthPx, viewportHeightPx, area.x0, area.y0, area.widthPx, area.heightPx]
  );
  const cone = useMemo(
    () => realSkyStarCone(session, viewportWidthPx, viewportHeightPx, limitMag),
    [session.observationSolution, session.sourceAsset, session.cropTransform, viewportWidthPx, viewportHeightPx, limitMag]
  );
  const viewRef = useRef(view);
  viewRef.current = view;
  const coneRef = useRef(cone);
  coneRef.current = cone;
  const limitRef = useRef(limitMag);
  limitRef.current = limitMag;
  const told = useRef(onState);
  told.current = onState;
  useEffect(() => {
    told.current?.(state);
  }, [state]);
  const finishMaker = useRef(makeFinish);
  finishMaker.current = makeFinish;

  // The tiles once a mount, and the program and its finish at the first tile (none where the catalog cannot be had)
  useEffect(() => {
    let current = true;
    let settling = 0;
    // Once every tile the view wants has come or failed: drawn if any came, failed if none did
    const settle = () => {
      if (settling) return;
      settling = requestAnimationFrame(() => {
        settling = 0;
        const g = gpu.current;
        const v = viewRef.current;
        const stats = source.current?.getStats?.();
        if (!current || !v || !canvas.current) return;
        if (stats && (stats.tilesInFlight > 0 || stats.tilesLoaded + stats.failures < stats.tilesWanted)) return;
        if (!g || g.tiles.size === 0) {
          if (stats) setState('failed');
          return;
        }
        drawStars(g, v, canvas.current, dpr, limitRef.current);
        let stars = 0;
        for (const t of g.tiles.values()) stars += t.count;
        setCount(stars);
        setState('drawn');
      });
    };
    const onTile = (tile: CatalogStarCoreTile) => {
      const el = canvas.current;
      const v = viewRef.current;
      if (!current || !el || !v) return;
      if (!gpu.current) {
        gpu.current = createGpu(el, (c, gl) => (finishMaker.current ?? createFlightFinish)(c, gl, v.viewportWidthPx, v.viewportHeightPx, dpr));
        if (!gpu.current) {
          setState('failed');
          return;
        }
      }
      const g = gpu.current;
      const key = `${tile.layer}/${tile.pix}`;
      if (!g.tiles.has(key)) {
        const entry = uploadTile(g, tile);
        if (entry) g.tiles.set(key, entry);
      }
      settle();
    };
    void openTiles(onTile, settle).then(
      (opened) => {
        if (!current) {
          opened.dispose();
          return;
        }
        source.current = opened;
        if (coneRef.current) opened.setView(coneRef.current);
      },
      () => {
        // No stand-in stars: a catalog that cannot be had leaves Real sky to the photograph
        if (current) setState('failed');
      }
    );
    return () => {
      current = false;
      if (settling) cancelAnimationFrame(settling);
      source.current?.dispose();
      source.current = null;
      const g = gpu.current;
      if (g) {
        for (const t of g.tiles.values()) {
          for (const b of t.buffers) g.gl.deleteBuffer(b);
          g.gl.deleteVertexArray(t.vao);
        }
        g.gl.deleteProgram(g.program);
        g.finish?.dispose();
      }
      gpu.current = null;
    };
  }, [openTiles, dpr]);

  // A new sky to cover: its tiles asked for
  useEffect(() => {
    if (cone) source.current?.setView(cone);
  }, [cone]);

  // A new view: drawn again before the browser paints (a new size clears the canvas)
  useLayoutEffect(() => {
    const g = gpu.current;
    if (!g || !view || !canvas.current) return;
    g.finish?.setSize(view.viewportWidthPx, view.viewportHeightPx, dpr);
    if (state === 'drawn') drawStars(g, view, canvas.current, dpr, limitMag);
  }, [view, limitMag, dpr]);

  return (
    <canvas
      ref={canvas}
      className={state === 'drawn' && shown ? 'real-sky__stars is-shown' : 'real-sky__stars'}
      data-testid="real-sky-stars"
      data-state={state}
      data-stars={count}
      width={Math.round(area.widthPx * dpr)}
      height={Math.round(area.heightPx * dpr)}
      style={style}
    />
  );
};
