import React, { useEffect, useMemo, useRef, useState } from 'react';
import type { ObservationSession } from '@albedian/contracts';
import type { Vec3 } from '@albedian/camera-rig';
import { CatalogStarTileLoader } from '../catalogStars/CatalogStarTileLoader.js';
import { STAR_CATALOG_BASE_URL } from '../catalogStars/field/fieldTypes.js';
import { catalogStarTint } from '../catalogStars/catalogStarTint.js';
import { usesBMinusV, type CatalogStarCoreTile } from '../catalogStars/tileCodec.js';
import type { CatalogStarView } from '../catalogStars/tileLoaderTypes.js';
import { FLIGHT_DRAW_LIMIT_MAG, buildFlightTileArrays, flightCameraOf, flightViewCone, type FlightCamera } from './flightFrame.js';
import { FLIGHT_STAR_FRAGMENT, FLIGHT_STAR_VERTEX, flightStarUniforms } from './flightStarShader.js';
import { FlightCraftLayer } from './FlightCraftLayer.js';
import { craftFromSearch, type Craft } from './crafts.js';

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
  return { tile, vao, buffers, referenceKey: keyOfReference(reference), skySphere: arrays.skySphereCount };
}

function drawFrame(gpu: Gpu, camera: FlightCamera, canvas: HTMLCanvasElement, pixelRatio: number): void {
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
}> = ({ session, viewportWidthPx, viewportHeightPx, openTiles = openCatalogTiles, craft = typeof window === 'undefined' ? null : craftFromSearch(window.location.search) }) => {
  const canvas = useRef<HTMLCanvasElement>(null);
  const gpu = useRef<Gpu | null>(null);
  const source = useRef<FlightTileSource | null>(null);
  const [state, setState] = useState<LayerState>('loading');
  const [counts, setCounts] = useState({ stars: 0, skySphere: 0 });
  const dpr = typeof window === 'undefined' ? 1 : Math.min(2, window.devicePixelRatio || 1);
  const camera = useMemo(
    () => flightCameraOf(session, viewportWidthPx, viewportHeightPx),
    [session.observationSolution, session.sourceAsset, session.cropTransform, session.selectedFindingId, session.spatialView?.observerOriginParsec, viewportWidthPx, viewportHeightPx]
  );
  const cameraRef = useRef(camera);
  cameraRef.current = camera;

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
      const cam = cameraRef.current;
      if (!current || !g || !cam || !canvas.current) return;
      const key = `${tile.layer}/${tile.pix}`;
      const entry = uploadTile(g, tile, cam.referencePc, g.tiles.get(key));
      if (!entry) return;
      g.tiles.set(key, entry);
      let stars = 0;
      let skySphere = 0;
      for (const t of g.tiles.values()) {
        stars += t.tile.count;
        skySphere += t.skySphere;
      }
      setCounts({ stars, skySphere });
      drawFrame(g, cam, canvas.current, dpr);
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

  // The window's sky for the loader when the photo, its frame or the window changes (the flight's orientation is fixed)
  useEffect(() => {
    if (camera && source.current) source.current.setView(flightViewCone(camera));
  }, [camera?.wcs, camera?.storedToScreen]);

  // Every change of where the camera stands draws a frame
  useEffect(() => {
    const g = gpu.current;
    if (g && camera && canvas.current && g.tiles.size > 0) drawFrame(g, camera, canvas.current, dpr);
  }, [camera, dpr]);

  const fromEarthPc = camera ? Math.hypot(camera.observerPc[0], camera.observerPc[1], camera.observerPc[2]) : 0;
  return (
    <>
    <div
      className="flight-stars"
      data-testid="flight-stars"
      data-state={state}
      data-stars={counts.stars}
      data-sky-sphere={counts.skySphere}
      data-from-earth-pc={fromEarthPc.toFixed(4)}
      aria-hidden="true"
      style={{ position: 'absolute', inset: 0, pointerEvents: 'none', mixBlendMode: 'screen' }}
    >
      <canvas
        ref={canvas}
        width={Math.round(viewportWidthPx * dpr)}
        height={Math.round(viewportHeightPx * dpr)}
        style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', display: 'block' }}
      />
    </div>
    {craft && camera && <FlightCraftLayer camera={camera} craft={craft} />}
    </>
  );
};
