import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ObservationSession } from '@albedian/contracts';
import type { Vec3 } from '@albedian/camera-rig';
import { CatalogStarTileLoader } from '../catalogStars/CatalogStarTileLoader.js';
import { STAR_CATALOG_BASE_URL } from '../catalogStars/field/fieldTypes.js';
import { catalogStarTint } from '../catalogStars/catalogStarTint.js';
import { usesBMinusV, type CatalogStarCoreTile } from '../catalogStars/tileCodec.js';
import type { CatalogStarView } from '../catalogStars/tileLoaderTypes.js';
import { FLIGHT_DRAW_LIMIT_MAG, buildFlightTileArrays, flightCameraOf, flightViewCone } from './flightFrame.js';
import { FLIGHT_STAR_FRAGMENT, FLIGHT_STAR_VERTEX, flightStarUniforms } from './flightStarShader.js';
import { FlightCraftLayer } from './FlightCraftLayer.js';
import { craftFromSearch, type Craft } from './crafts.js';
import { ConeLayer } from './ConeLayer.js';
import { coneLengthPc, coneStarView, dragOrbit, initialOrbit, isConeViewOn, zoomOrbit, type OrbitState } from './coneView.js';
import { starViewOfFlight, type StarView } from './starView.js';
import { RING_RADIUS_PX, RING_RADIUS_PX_PHONE, RING_STROKE, flightRings, noDistanceStarsOnScreen, type ScreenBox } from './flightRings.js';

/**
 * The flight's catalog stars (REQ-SCN-2), drawn over the There screen's panorama after Go there: Gaia DR3 and
 * Hipparcos 2007 stars to the load limit, each at its catalog distance seen from where the camera stands now (or on
 * the sky sphere when it has no usable distance), through the photo's own solve and frame. Nothing here is drawn
 * from the recording. The layer only draws: no words, no movement of its own (the camera's travel is the flight
 * animation's), and it blends over the panorama as light (screen), so the panorama stays as approved.
 */

type LayerState = 'loading' | 'drawn' | 'failed' | 'unavailable';

/** What the layer needs of a tile source: the catalog's tile loader, or a test's stand-in. */
export interface FlightTileSource {
  setView(view: CatalogStarView): void;
  dispose(): void;
}

export type OpenFlightTiles = (onTile: (tile: CatalogStarCoreTile) => void) => Promise<FlightTileSource>;

const openCatalogTiles: OpenFlightTiles = (onTile) =>
  CatalogStarTileLoader.open(STAR_CATALOG_BASE_URL, {
    onTileLoaded: onTile,
    onError: (error, key) => console.warn(`[flight stars] tile ${key.layer}/${key.pix} failed`, error),
  });

interface TileGpu {
  readonly tile: CatalogStarCoreTile;
  vao: WebGLVertexArrayObject;
  buffers: WebGLBuffer[];
  referenceKey: string;
  skySphere: number;
  noDistanceOnScreen: number;
}

interface Gpu {
  gl: WebGL2RenderingContext;
  program: WebGLProgram;
  uniforms: Record<string, WebGLUniformLocation | null>;
  tiles: Map<string, TileGpu>;
}

const UNIFORMS = ['uEye', 'uRowX', 'uRowY', 'uRowZ', 'uPix', 'uLens', 'uAffX', 'uAffY', 'uView', 'uLimit', 'uPixelRatio'] as const;

function compile(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader | null {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  return gl.getShaderParameter(shader, gl.COMPILE_STATUS) ? shader : null;
}

function createGpu(canvas: HTMLCanvasElement): Gpu | null {
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
  return { gl, program, uniforms, tiles: new Map() };
}

function tileTints(tile: CatalogStarCoreTile): Float32Array {
  const tints = new Float32Array(3 * tile.count);
  for (let i = 0; i < tile.count; i++) tints.set(catalogStarTint(tile.colorIndex[i] ?? Number.NaN, usesBMinusV(tile.flags[i] ?? 0)), 3 * i);
  return tints;
}

const keyOfReference = (r: Vec3) => r.join(',');

/** Uploads (or re-uploads, for a new reference) one tile's stars. */
function uploadTile(gpu: Gpu, tile: CatalogStarCoreTile, reference: Vec3, existing?: TileGpu): TileGpu | null {
  const { gl, program } = gpu;
  if (existing) {
    for (const b of existing.buffers) gl.deleteBuffer(b);
    gl.deleteVertexArray(existing.vao);
  }
  const arrays = buildFlightTileArrays(tile, reference);
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
  bind('aTint', tileTints(tile), 3);
  gl.bindVertexArray(null);
  return { tile, vao, buffers, referenceKey: keyOfReference(reference), skySphere: arrays.skySphereCount, noDistanceOnScreen: existing?.noDistanceOnScreen ?? 0 };
}

function drawFrame(gpu: Gpu, camera: StarView, canvas: HTMLCanvasElement, pixelRatio: number): void {
  const { gl, program, uniforms } = gpu;
  // A new star flown to rebases every tile on its place (the floating origin)
  const refKey = keyOfReference(camera.referencePc);
  for (const [key, entry] of gpu.tiles) {
    if (entry.referenceKey === refKey) continue;
    const again = uploadTile(gpu, entry.tile, camera.referencePc, entry);
    if (again) gpu.tiles.set(key, again);
  }
  const u = flightStarUniforms(camera);
  gl.viewport(0, 0, canvas.width, canvas.height);
  gl.clearColor(0, 0, 0, 1);
  gl.clear(gl.COLOR_BUFFER_BIT);
  gl.useProgram(program);
  gl.uniform3fv(uniforms.uEye!, u.uEye);
  gl.uniform3fv(uniforms.uRowX!, u.uRowX);
  gl.uniform3fv(uniforms.uRowY!, u.uRowY);
  gl.uniform3fv(uniforms.uRowZ!, u.uRowZ);
  gl.uniform3fv(uniforms.uPix!, u.uPix);
  gl.uniform3fv(uniforms.uLens!, u.uLens);
  gl.uniform3fv(uniforms.uAffX!, u.uAffX);
  gl.uniform3fv(uniforms.uAffY!, u.uAffY);
  gl.uniform2fv(uniforms.uView!, u.uView);
  gl.uniform1f(uniforms.uLimit!, FLIGHT_DRAW_LIMIT_MAG);
  gl.uniform1f(uniforms.uPixelRatio!, pixelRatio);
  // Light adds: additive blending, as the sky view's catalog stars
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.ONE, gl.ONE);
  for (const entry of gpu.tiles.values()) {
    gl.bindVertexArray(entry.vao);
    gl.drawArrays(gl.POINTS, 0, entry.tile.count);
  }
  gl.bindVertexArray(null);
}

export const FlightStarsLayer: React.FC<{
  session: ObservationSession;
  viewportWidthPx: number;
  viewportHeightPx: number;
  /** The tile source; the catalog's own by default. */
  openTiles?: OpenFlightTiles;
  /** pilot-1: a NASA craft drawn in the view; by default the one the page address asks for (`?craft=`), INTERIM. */
  craft?: Craft | null;
  /** cone-1: the third-person view of the photo's cone; by default as the page address asks (`?view=cone`), INTERIM. */
  cone?: boolean;
  /** D27 item 2: told how many drawn stars stay on the sky sphere for want of a measured distance, for the disclosure. */
  onNoDistanceCount?: (count: number) => void;
  /** At phone width the rings are 6 px across the radius (D27 item 1). */
  phone?: boolean;
}> = ({
  session,
  viewportWidthPx,
  viewportHeightPx,
  openTiles = openCatalogTiles,
  craft = typeof window === 'undefined' ? null : craftFromSearch(window.location.search),
  cone = isConeViewOn(session),
  onNoDistanceCount,
  phone = false,
}) => {
  const canvas = useRef<HTMLCanvasElement>(null);
  const gpu = useRef<Gpu | null>(null);
  const source = useRef<FlightTileSource | null>(null);
  const [state, setState] = useState<LayerState>('loading');
  const [counts, setCounts] = useState({ stars: 0, skySphere: 0, noDistanceOnScreen: 0 });
  const told = useRef(onNoDistanceCount);
  told.current = onNoDistanceCount;
  const dpr = typeof window === 'undefined' ? 1 : Math.min(2, window.devicePixelRatio || 1);
  const camera = useMemo(
    () => flightCameraOf(session, viewportWidthPx, viewportHeightPx),
    [session.observationSolution, session.sourceAsset, session.cropTransform, session.selectedFindingId, session.spatialView?.observerOriginParsec, viewportWidthPx, viewportHeightPx]
  );
  const coneLength = useMemo(() => coneLengthPc(session), [session.observationSolution]);
  const [orbit, setOrbit] = useState<OrbitState>(() => initialOrbit(coneLength));
  // The camera the stars are drawn through: the photo's own in the flight, the orbit camera in the cone view
  const view = useMemo<StarView | null>(
    () => (camera ? (cone ? coneStarView(session, orbit, viewportWidthPx, viewportHeightPx) : starViewOfFlight(camera)) : null),
    [camera, cone, orbit, session.observationSolution, viewportWidthPx, viewportHeightPx]
  );
  const viewRef = useRef(view);
  viewRef.current = view;
  const cameraRef = useRef(camera);
  cameraRef.current = camera;
  const onDrag = useCallback((dx: number, dy: number) => setOrbit((o) => dragOrbit(o, dx, dy)), []);
  const onWheel = useCallback((dy: number) => setOrbit((o) => zoomOrbit(o, dy, coneLength)), [coneLength]);

  // The GL program and the tile source, once per mount
  useEffect(() => {
    let current = true;
    const el = canvas.current;
    const made = el ? createGpu(el) : null;
    if (!made) {
      setState('failed');
      return undefined;
    }
    gpu.current = made;
    const onTile = (tile: CatalogStarCoreTile) => {
      const g = gpu.current;
      const v = viewRef.current;
      if (!current || !g || !v || !canvas.current) return;
      const key = `${tile.layer}/${tile.pix}`;
      const entry = uploadTile(g, tile, v.referencePc, g.tiles.get(key));
      if (!entry) return;
      // The flight's orientation is fixed, so the sky-sphere stars on screen are counted once per tile
      const flight = cameraRef.current;
      entry.noDistanceOnScreen = flight ? noDistanceStarsOnScreen(tile, starViewOfFlight(flight)) : 0;
      g.tiles.set(key, entry);
      let stars = 0;
      let skySphere = 0;
      let noDistanceOnScreen = 0;
      for (const t of g.tiles.values()) {
        stars += t.tile.count;
        skySphere += t.skySphere;
        noDistanceOnScreen += t.noDistanceOnScreen;
      }
      setCounts({ stars, skySphere, noDistanceOnScreen });
      drawFrame(g, v, canvas.current, dpr);
      setState('drawn');
    };
    void openTiles(onTile).then(
      (opened) => {
        if (!current) {
          opened.dispose();
          return;
        }
        source.current = opened;
        if (cameraRef.current) opened.setView(flightViewCone(cameraRef.current));
      },
      () => {
        // No stand-in stars: an unavailable catalog leaves the panorama alone rather than invented stars over it
        if (current) setState('unavailable');
      }
    );
    return () => {
      current = false;
      source.current?.dispose();
      source.current = null;
      const g = gpu.current;
      if (g) {
        for (const t of g.tiles.values()) {
          for (const b of t.buffers) g.gl.deleteBuffer(b);
          g.gl.deleteVertexArray(t.vao);
        }
        g.gl.deleteProgram(g.program);
      }
      gpu.current = null;
    };
  }, [openTiles, dpr]);

  // The photo's sky for the loader when the photo, its frame or the window changes (the flight's orientation is fixed;
  // the cone view shows the same stars, around the photo's cone)
  useEffect(() => {
    if (camera && source.current) source.current.setView(flightViewCone(camera));
  }, [camera?.wcs, camera?.storedToScreen]);

  // Every change of where the camera stands, or of the orbit, draws a frame
  useEffect(() => {
    const g = gpu.current;
    if (g && view && canvas.current && g.tiles.size > 0) drawFrame(g, view, canvas.current, dpr);
  }, [view, dpr]);

  useEffect(() => {
    told.current?.(counts.noDistanceOnScreen);
  }, [counts.noDistanceOnScreen]);

  // D27 item 1: rings on the matched stars, kept out of the disclosure, the readout and the bar
  const keepOut: ScreenBox[] = [];
  if (typeof document !== 'undefined') {
    for (const id of ['reveal-status', 'flight-readout', 'ask-bar']) {
      const r = document.querySelector(`[data-testid="${id}"]`)?.getBoundingClientRect();
      if (r && r.width > 0) keepOut.push({ left: r.left, top: r.top, right: r.right, bottom: r.bottom });
    }
  }
  const ringRadius = phone ? RING_RADIUS_PX_PHONE : RING_RADIUS_PX;
  const rings = !cone && view ? flightRings(session, view, keepOut, ringRadius) : [];

  const at = view?.observerPc ?? [0, 0, 0];
  const fromEarthPc = Math.hypot(at[0], at[1], at[2]);
  return (
    <>
    <div
      className="flight-stars"
      data-testid="flight-stars"
      data-state={state}
      data-view={cone ? 'cone' : 'flight'}
      data-stars={counts.stars}
      data-sky-sphere={counts.skySphere}
      data-no-distance-on-screen={counts.noDistanceOnScreen}
      data-from-earth-pc={fromEarthPc.toFixed(4)}
      aria-hidden="true"
      style={{ position: 'absolute', inset: 0, pointerEvents: 'none', mixBlendMode: cone ? 'normal' : 'screen', background: cone ? '#000' : undefined }}
    >
      <canvas
        ref={canvas}
        width={Math.round(viewportWidthPx * dpr)}
        height={Math.round(viewportHeightPx * dpr)}
        style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', display: 'block' }}
      />
    </div>
    {rings.length > 0 && (
      <svg className="flight-rings" data-testid="flight-rings" aria-hidden="true" width={viewportWidthPx} height={viewportHeightPx} style={{ position: 'absolute', inset: 0, pointerEvents: 'none', overflow: 'visible' }}>
        {rings.map((r) => (
          <circle key={r.findingId} data-finding-id={r.findingId} cx={r.xPx} cy={r.yPx} r={ringRadius} fill="none" stroke={RING_STROKE} strokeWidth={1} />
        ))}
      </svg>
    )}
    {cone && view && <ConeLayer session={session} view={view} lengthPc={coneLength} onDrag={onDrag} onWheel={onWheel} />}
    {!cone && craft && camera && <FlightCraftLayer camera={camera} craft={craft} />}
    </>
  );
};
