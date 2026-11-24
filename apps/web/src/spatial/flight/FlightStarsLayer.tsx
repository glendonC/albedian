import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ObservationSession } from '@albedian/contracts';
import type { Vec3 } from '@albedian/camera-rig';
import { CatalogStarTileLoader } from '../catalogStars/CatalogStarTileLoader.js';
import { STAR_CATALOG_BASE_URL } from '../catalogStars/field/fieldTypes.js';
import { catalogStarTint } from '../catalogStars/catalogStarTint.js';
import { usesBMinusV, type CatalogStarCoreTile } from '../catalogStars/tileCodec.js';
import type { CatalogStarView } from '../catalogStars/tileLoaderTypes.js';
import { FLIGHT_DRAW_LIMIT_MAG, buildFlightTileArrays, flightCameraOf } from './flightFrame.js';
import { ARRIVAL_BLOOM_FRAGMENT, ARRIVAL_BLOOM_VERTEX, FLIGHT_STAR_FRAGMENT, FLIGHT_STAR_VERTEX, flightStarUniforms } from './flightStarShader.js';
import { arrivalBloomOf, arrivalCraftFraming, arrivalProgress, arrivalShiftOf, arrivalStarOfTile, arrivalViewCone, arrivedFlightCamera, type ArrivalBloom, type ArrivalStar } from './arrival.js';
import { FlightCraftLayer } from './FlightCraftLayer.js';
import { lightCandidateOfFinding, type LightCandidate } from './craftLight.js';
import { FLIGHT_MILKY_WAY_URL, createFlightDome, disposeFlightDome, drawFlightDome, type FlightDomeGpu } from '../sky/flightMilkyWay.js';
import { loadPanoramaRegistration } from '../sky/skyRegistration.js';
import { laptopCraftFraming, type CraftFraming } from './craftScene.js';
import { craftFromSearch, type Craft } from './crafts.js';
import { ConeLayer } from './ConeLayer.js';
import { CONE_ORBIT_START, coneGeometryOf, coneStarView, dragConeOrbit, zoomConeOrbit, type ConeOrbit } from './coneView.js';
import { starViewOfFlight, starViewOfSteer, steerStartOf, type StarView } from './starView.js';
import { STEER_START_SPEED_PC_S, SteerHands, steeringSpeedLine } from './steering.js';
import { threeFigures } from '../../reveal/flightView.js';
import { changeSpeed, followRows, steerCamera, type SteerState, type WcsCameraRows } from '@albedian/camera-rig';
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
  /**
   * arrival-star-1: the bloom's quad and its program, made at its first draw (undefined until then); null where it would
   * not build (the stars still draw).
   */
  bloom?: { program: WebGLProgram; vao: WebGLVertexArrayObject; uniforms: Record<string, WebGLUniformLocation | null> } | null;
  /** milkyway-flight-one: the starless glow as a dome while steering, once it has loaded. */
  dome: FlightDomeGpu | null;
}

const UNIFORMS = ['uEye', 'uRowX', 'uRowY', 'uRowZ', 'uPix', 'uLens', 'uAffX', 'uAffY', 'uView', 'uLimit', 'uPixelRatio', 'uGain'] as const;

/** D28 item 5: in the cone view the catalog stars are the faint far field, at 45 percent; in flight, as the sky view. */
export const CONE_FIELD_GAIN = 0.45;

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
  return { gl, program, uniforms, tiles: new Map(), dome: null };
}

/** The bloom's program and its quad (two triangles over the square the glow fills). */
function createBloom(gl: WebGL2RenderingContext): NonNullable<Gpu['bloom']> | null {
  const vs = compile(gl, gl.VERTEX_SHADER, ARRIVAL_BLOOM_VERTEX);
  const fs = compile(gl, gl.FRAGMENT_SHADER, ARRIVAL_BLOOM_FRAGMENT);
  const program = gl.createProgram();
  if (!vs || !fs || !program) return null;
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return null;
  const vao = gl.createVertexArray();
  const buffer = gl.createBuffer();
  const at = gl.getAttribLocation(program, 'aCorner');
  if (!vao || !buffer || at < 0) return null;
  gl.bindVertexArray(vao);
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(at);
  gl.vertexAttribPointer(at, 2, gl.FLOAT, false, 0, 0);
  gl.bindVertexArray(null);
  const uniforms: Record<string, WebGLUniformLocation | null> = {};
  for (const name of ['uCenter', 'uRadius', 'uView', 'uTint']) uniforms[name] = gl.getUniformLocation(program, name);
  return { program, vao, uniforms };
}

/** The star flown to, from the first loaded tile that holds its row. */
function arrivalStarOfTiles(gpu: Gpu, referencePc: Vec3): ArrivalStar | null {
  for (const entry of gpu.tiles.values()) {
    const star = arrivalStarOfTile(entry.tile, referencePc);
    if (star) return star;
  }
  return null;
}

function tileTints(tile: CatalogStarCoreTile): Float32Array {
  const tints = new Float32Array(3 * tile.count);
  for (let i = 0; i < tile.count; i++) tints.set(catalogStarTint(tile.colorIndex[i] ?? Number.NaN, usesBMinusV(tile.flags[i] ?? 0)), 3 * i);
  return tints;
}

const keyOfReference = (r: Vec3) => r.join(',');

/** The camera while the person steers: where it stands and its axes, its speed, and the chase camera's look. */
type SteerNow = SteerState & { chase: WcsCameraRows };

/** Light years in a parsec, as the readout converts (RevealPhoto.tsx). */
const LY_PER_PC = 3.261563777;

/** The boxes the rings keep out of (D27 item 1): the disclosure, the readouts, the bar and the pills, as laid out now. */
function keepOutBoxes(): ScreenBox[] {
  const boxes: ScreenBox[] = [];
  if (typeof document === 'undefined') return boxes;
  for (const id of ['reveal-status', 'flight-readout', 'cone-readout', 'ask-bar', 'flight-pills']) {
    const r = document.querySelector(`[data-testid="${id}"]`)?.getBoundingClientRect();
    if (r && r.width > 0) boxes.push({ left: r.left, top: r.top, right: r.right, bottom: r.bottom });
  }
  return boxes;
}

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

function drawFrame(gpu: Gpu, camera: StarView, canvas: HTMLCanvasElement, pixelRatio: number, gain = 1, bloom: ArrivalBloom | null = null, dome = false): void {
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
  // While steering the Milky Way turns with the camera: drawn first, opaque, the stars added over it
  if (dome && gpu.dome) drawFlightDome(gl, gpu.dome, camera, camera.viewportWidthPx, camera.viewportHeightPx, pixelRatio);
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
  gl.uniform1f(uniforms.uGain!, gain);
  // Light adds: additive blending, as the sky view's catalog stars
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.ONE, gl.ONE);
  for (const entry of gpu.tiles.values()) {
    gl.bindVertexArray(entry.vao);
    gl.drawArrays(gl.POINTS, 0, entry.tile.count);
  }
  // arrival-star-1: the star flown to, its glow added as light around its own dot
  if (bloom && bloom.diameterPx > 0) {
    if (gpu.bloom === undefined) gpu.bloom = createBloom(gl);
  }
  if (bloom && gpu.bloom && bloom.diameterPx > 0) {
    const b = gpu.bloom;
    gl.useProgram(b.program);
    gl.uniform2f(b.uniforms.uCenter!, bloom.xPx, bloom.yPx);
    gl.uniform1f(b.uniforms.uRadius!, bloom.diameterPx / 2);
    gl.uniform2f(b.uniforms.uView!, camera.viewportWidthPx, camera.viewportHeightPx);
    gl.uniform3f(b.uniforms.uTint!, bloom.tint[0] * gain, bloom.tint[1] * gain, bloom.tint[2] * gain);
    gl.bindVertexArray(b.vao);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
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
  /** Where the screen frames the craft; the laptop's (D26 item 2) by default, the phone's sky band (D30 item 1). */
  craftFraming?: CraftFraming;
  /** pilot-1e: whether the person steers (a press on the sky hands over and takes back the steering). */
  steering?: boolean;
  /** Told of a press on the sky (a click or tap that is not a drag), in the flight view. */
  onPressSky?: () => void;
  /** Told once when a drag on the sky starts (D30 item 6: on the phone a drag takes the steering; a tap never does). */
  onDragSky?: () => void;
  /** milkyway-flight-one: told true once the steering dome has drawn, false when steering stops. */
  onSkyDome?: (drawn: boolean) => void;
  /** While steering, told where the camera stands and how fast it flies, each frame; none when steering stops. */
  onSteer?: (state: { fromEarthPc: number; toStarPc: number; speedPcPerS: number } | null) => void;
}> = ({
  session,
  viewportWidthPx,
  viewportHeightPx,
  openTiles = openCatalogTiles,
  craft = typeof window === 'undefined' ? null : craftFromSearch(window.location.search),
  cone = false,
  onNoDistanceCount,
  phone = false,
  craftFraming,
  steering = false,
  onPressSky,
  onDragSky,
  onSteer,
  onSkyDome,
}) => {
  const canvas = useRef<HTMLCanvasElement>(null);
  const gpu = useRef<Gpu | null>(null);
  const source = useRef<FlightTileSource | null>(null);
  const [state, setState] = useState<LayerState>('loading');
  const [counts, setCounts] = useState({ stars: 0, skySphere: 0, noDistanceOnScreen: 0 });
  const told = useRef(onNoDistanceCount);
  told.current = onNoDistanceCount;
  const dpr = typeof window === 'undefined' ? 1 : Math.min(2, window.devicePixelRatio || 1);
  // The photo's own camera, and the same with its frame moved as Phase B brings the star to its resting place
  // (arrival-star-1, look gap two): stars, rings, the bloom, the craft and steering all see the moved one
  const photoCamera = useMemo(
    () => flightCameraOf(session, viewportWidthPx, viewportHeightPx),
    [session.observationSolution, session.sourceAsset, session.cropTransform, session.selectedFindingId, session.spatialView?.observerOriginParsec, viewportWidthPx, viewportHeightPx]
  );
  const shift = useMemo(
    () => (photoCamera ? arrivalShiftOf(session, viewportWidthPx, viewportHeightPx, phone) : null),
    [photoCamera, session.spatialView?.transitionPhase, session.spatialView?.transitionProgress, phone]
  );
  const camera = useMemo(() => (photoCamera && shift && !cone ? arrivedFlightCamera(photoCamera, shift.nowPx) : photoCamera), [photoCamera, shift?.nowPx[0], shift?.nowPx[1], cone]);
  const fullShift: readonly [number, number] = shift ? shift.fullPx : [0, 0];
  const photoCameraRef = useRef(photoCamera);
  photoCameraRef.current = photoCamera;
  const fullShiftRef = useRef(fullShift);
  fullShiftRef.current = fullShift;
  const geometry = useMemo(() => coneGeometryOf(session), [session.observationSolution, session.sourceAsset]);
  // craft-light-one: the star flown to, as the craft's key where it is the brightest source
  const keyCandidates = useMemo((): readonly LightCandidate[] => {
    const picked = session.observationSolution?.findings.find((f) => f.id === session.selectedFindingId);
    const candidate = picked ? lightCandidateOfFinding(picked) : null;
    return candidate ? [candidate] : [];
  }, [session.observationSolution, session.selectedFindingId]);
  const [orbit, setOrbit] = useState<ConeOrbit>(CONE_ORBIT_START);
  // Each time the cone view opens it starts beside the cone (D28 item 8)
  useEffect(() => {
    if (cone) setOrbit(CONE_ORBIT_START);
  }, [cone]);
  // pilot-1e: the person's own camera, handed over where the flight's stands, turned and moved each frame
  // The person's input turns the craft (steer.rows); the chase camera's look follows it (MOTION k13: 0.25 s, locked
  // under reduced motion), so in a turn the craft leads off the centre
  // steer-frame-one (DECIDED-BY-LEAD 15:59): the state React holds is the hand-over's start only; each frame's camera is
  // held in a ref and drawn by the one frame loop (stars, bloom, rings, craft), the page told only when the readout's
  // numbers change
  const [steer, setSteer] = useState<SteerNow | null>(null);
  const steerNow = useRef<SteerNow | null>(null);
  const frameDraw = useRef<((s: SteerNow) => void) | null>(null);
  const craftDraw = useRef<((s: { positionPc: Vec3; rows: WcsCameraRows; craftRows?: WcsCameraRows }) => void) | null>(null);
  const toldKey = useRef('');
  const [craftSeen, setCraftSeen] = useState(Boolean(craft));
  useEffect(() => {
    if (craft) setCraftSeen(true);
  }, [craft]);
  const reducedRef = useRef(session.reducedMotion === true);
  reducedRef.current = session.reducedMotion === true;
  const hands = useRef(new SteerHands(() => reducedRef.current));
  const flying = steering && !cone && camera !== null;
  useEffect(() => {
    if (!flying || !cameraRef.current) {
      setSteer(null);
      return undefined;
    }
    hands.current = new SteerHands(() => reducedRef.current);
    const start = steerStartOf(cameraRef.current);
    const first: SteerNow = { ...start, speedPcPerS: STEER_START_SPEED_PC_S, chase: start.rows };
    steerNow.current = first;
    toldKey.current = '';
    setSteer(first);
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = Math.min(0.1, Math.max(0, (now - last) / 1000));
      last = now;
      const { input, speedDirection } = hands.current.frame(dt);
      const prev = steerNow.current;
      if (prev) {
        const next = steerCamera(prev, input, dt);
        const moved: SteerNow = { ...next, speedPcPerS: changeSpeed(prev.speedPcPerS, speedDirection, dt), chase: followRows(prev.chase, next.rows, dt, { reduced: reducedRef.current }) };
        steerNow.current = moved;
        frameDraw.current?.(moved);
      }
      raf = window.requestAnimationFrame(tick);
    };
    raf = window.requestAnimationFrame(tick);
    // W, S and the arrows are steering's while it lasts; Space stays Ask out loud, Esc Back to Moment
    const typing = (e: KeyboardEvent) => e.target instanceof HTMLElement && (e.target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName));
    const down = (e: KeyboardEvent) => {
      if (typing(e) || !SteerHands.takes(e.code)) return;
      hands.current.keyDown(e.code);
      e.preventDefault();
      e.stopPropagation();
    };
    const up = (e: KeyboardEvent) => {
      if (!SteerHands.takes(e.code)) return;
      hands.current.keyUp(e.code);
      e.stopPropagation();
    };
    window.addEventListener('keydown', down, true);
    window.addEventListener('keyup', up, true);
    return () => {
      window.cancelAnimationFrame(raf);
      window.removeEventListener('keydown', down, true);
      window.removeEventListener('keyup', up, true);
    };
  }, [flying]);
  const toldSteer = useRef(onSteer);
  toldSteer.current = onSteer;
  // The page hears of the camera only when a number the readout shows would change (its three figures, its speed line)
  const tell = (s: SteerNow | null) => {
    if (!s) {
      toldKey.current = '';
      toldSteer.current?.(null);
      return;
    }
    const ref = cameraRef.current?.referencePc ?? [0, 0, 0];
    const report = {
      fromEarthPc: Math.hypot(s.positionPc[0], s.positionPc[1], s.positionPc[2]),
      toStarPc: Math.hypot(s.positionPc[0] - ref[0], s.positionPc[1] - ref[1], s.positionPc[2] - ref[2]),
      speedPcPerS: s.speedPcPerS,
    };
    const key = `${threeFigures(report.toStarPc * LY_PER_PC)}|${threeFigures(report.fromEarthPc * LY_PER_PC)}|${steeringSpeedLine(report.speedPcPerS)}`;
    if (key === toldKey.current) return;
    toldKey.current = key;
    toldSteer.current?.(report);
  };
  useEffect(() => {
    tell(steer);
  }, [steer]);
  // A press on the sky (not a drag) in the flight view; while steering, a drag turns
  const sky = useRef<HTMLDivElement>(null);
  const toldPress = useRef(onPressSky);
  toldPress.current = onPressSky;
  const toldDrag = useRef(onDragSky);
  toldDrag.current = onDragSky;
  useEffect(() => {
    const el = sky.current;
    if (!el) return undefined;
    let start: { x: number; y: number; t: number } | null = null;
    let lastMove = { x: 0, y: 0, t: 0 };
    let dragged = false;
    const downP = (e: PointerEvent) => {
      start = { x: e.clientX, y: e.clientY, t: performance.now() };
      lastMove = { ...start };
      dragged = false;
      el.setPointerCapture?.(e.pointerId);
    };
    const moveP = (e: PointerEvent) => {
      if (!start) return;
      if (!dragged && Math.hypot(e.clientX - start.x, e.clientY - start.y) > 4) {
        dragged = true;
        toldDrag.current?.();
      }
      const now = performance.now();
      if (dragged) hands.current.drag(e.clientX - lastMove.x, e.clientY - lastMove.y, (now - lastMove.t) / 1000);
      lastMove = { x: e.clientX, y: e.clientY, t: now };
    };
    const upP = () => {
      if (start && !dragged) toldPress.current?.();
      hands.current.release();
      start = null;
    };
    el.addEventListener('pointerdown', downP);
    el.addEventListener('pointermove', moveP);
    el.addEventListener('pointerup', upP);
    el.addEventListener('pointercancel', upP);
    return () => {
      el.removeEventListener('pointerdown', downP);
      el.removeEventListener('pointermove', moveP);
      el.removeEventListener('pointerup', upP);
      el.removeEventListener('pointercancel', upP);
    };
  }, [cone]);
  const gain = cone ? CONE_FIELD_GAIN : 1;
  const gainRef = useRef(gain);
  gainRef.current = gain;
  const coneRef = useRef(cone);
  coneRef.current = cone;
  // The camera the stars are drawn through: the photo's own in the flight, the orbit camera in the cone view
  const view = useMemo<StarView | null>(
    () => (camera ? (cone ? coneStarView(session, orbit, viewportWidthPx, viewportHeightPx, phone) : steer ? starViewOfSteer(camera, { positionPc: steer.positionPc, rows: steer.chase }) : starViewOfFlight(camera)) : null),
    [camera, cone, orbit, phone, steer, session.observationSolution, viewportWidthPx, viewportHeightPx]
  );
  const viewRef = useRef(view);
  viewRef.current = view;
  // The star flown to, found in its own catalog row; its bloom as the camera sees it now (none in the cone view)
  const [arrival, setArrival] = useState<ArrivalStar | null>(null);
  const arrivalRef = useRef(arrival);
  arrivalRef.current = arrival;
  const bloom = useMemo(() => (!cone && arrival && view ? arrivalBloomOf(arrival, view) : null), [arrival, view, cone]);
  const bloomRef = useRef(bloom);
  bloomRef.current = bloom;
  const referenceKey = camera ? keyOfReference(camera.referencePc) : '';
  const domeRef = useRef(flying);
  domeRef.current = flying;
  const cameraRef = useRef(camera);
  cameraRef.current = camera;
  const onDrag = useCallback((dx: number, dy: number) => setOrbit((o) => dragConeOrbit(o, dx, dy)), []);
  const onWheel = useCallback((dy: number) => setOrbit((o) => zoomConeOrbit(o, dy)), []);

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
      const found = arrivalStarOfTile(tile, v.referencePc);
      if (found) setArrival(found);
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
      drawFrame(g, v, canvas.current, dpr, gainRef.current, found && !coneRef.current ? arrivalBloomOf(found, v) : bloomRef.current, domeRef.current);
      setState('drawn');
    };
    void openTiles(onTile).then(
      (opened) => {
        if (!current) {
          opened.dispose();
          return;
        }
        source.current = opened;
        if (photoCameraRef.current) opened.setView(arrivalViewCone(photoCameraRef.current, fullShiftRef.current));
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
        if (g.bloom) g.gl.deleteProgram(g.bloom.program);
        if (g.dome) disposeFlightDome(g.gl, g.dome);
      }
      gpu.current = null;
    };
  }, [openTiles, dpr]);

  // The photo's sky for the loader when the photo, its frame or the window changes (the flight's orientation is fixed;
  // the cone view shows the same stars, around the photo's cone)
  // The cone holds the sky the whole move to the resting place shows, asked once per flight, not every frame
  useEffect(() => {
    if (photoCamera && source.current) source.current.setView(arrivalViewCone(photoCamera, fullShift));
  }, [photoCamera?.wcs, photoCamera?.storedToScreen, fullShift[0], fullShift[1]]);

  // A new star flown to: its row among the tiles already loaded
  useEffect(() => {
    const g = gpu.current;
    setArrival(g && camera ? arrivalStarOfTiles(g, camera.referencePc) : null);
  }, [referenceKey]);

  // Every change of where the camera stands, or of the orbit, draws a frame
  useEffect(() => {
    const g = gpu.current;
    if (g && view && canvas.current && g.tiles.size > 0) drawFrame(g, view, canvas.current, dpr, gain, bloom, flying);
  }, [view, dpr, gain, bloom, flying]);

  // milkyway-flight-one: the glow for the steering dome, loaded the first time the person steers
  const toldDome = useRef(onSkyDome);
  toldDome.current = onSkyDome;
  useEffect(() => {
    const g = gpu.current;
    toldDome.current?.(flying && !!g?.dome);
    if (!flying || !g || g.dome) return undefined;
    let current = true;
    const img = new Image();
    img.decoding = 'async';
    const loaded = new Promise<HTMLImageElement>((resolve, reject) => {
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error('flight dome'));
    });
    img.src = FLIGHT_MILKY_WAY_URL;
    void Promise.all([loaded, loadPanoramaRegistration()]).then(
      ([image, registration]) => {
        const now = gpu.current;
        if (!current || !now || now.dome) return;
        now.dome = createFlightDome(now.gl, image, registration);
        const v = viewRef.current;
        if (v && canvas.current && now.tiles.size > 0) drawFrame(now, v, canvas.current, dpr, gainRef.current, bloomRef.current, domeRef.current);
        toldDome.current?.(!!now.dome && domeRef.current);
      },
      () => undefined
    );
    return () => {
      current = false;
    };
  }, [flying, dpr]);

  useEffect(() => {
    told.current?.(counts.noDistanceOnScreen);
  }, [counts.noDistanceOnScreen]);

  // D27 item 1: rings on the matched stars, kept out of the disclosure, the readout and the bar
  const keepOut = keepOutBoxes();
  const ringRadius = phone ? RING_RADIUS_PX_PHONE : RING_RADIUS_PX;
  const rings = !cone && view && !steer ? flightRings(session, view, keepOut, ringRadius) : [];
  const layerEl = useRef<HTMLDivElement>(null);
  const steerRings = useRef<SVGSVGElement>(null);
  // steer-frame-one: each frame while steering, drawn by the loop with no React render: the stars and the bloom, the
  // rings (set in their own svg), the craft, the layer's distance, and the page's numbers when they change
  useEffect(() => {
    frameDraw.current = (s) => {
      const cam = cameraRef.current;
      if (!cam) return;
      const v = starViewOfSteer(cam, { positionPc: s.positionPc, rows: s.chase });
      const g = gpu.current;
      const star = arrivalRef.current;
      if (g && canvas.current && g.tiles.size > 0) drawFrame(g, v, canvas.current, dpr, gainRef.current, star ? arrivalBloomOf(star, v) : null, domeRef.current);
      const svg = steerRings.current;
      if (svg) svg.innerHTML = flightRings(session, v, keepOutBoxes(), ringRadius).map((r) => `<circle data-finding-id="${r.findingId}" cx="${r.xPx}" cy="${r.yPx}" r="${ringRadius}" fill="none" stroke="${RING_STROKE}" stroke-width="1"></circle>`).join('');
      craftDraw.current?.({ positionPc: s.positionPc, rows: s.chase, craftRows: s.rows });
      layerEl.current?.setAttribute('data-from-earth-pc', Math.hypot(s.positionPc[0], s.positionPc[1], s.positionPc[2]).toFixed(4));
      tell(s);
    };
    return () => {
      frameDraw.current = null;
    };
  });

  const at = view?.observerPc ?? [0, 0, 0];
  const fromEarthPc = Math.hypot(at[0], at[1], at[2]);
  // craft-switch-motion-1: once a craft was shown in this flight its layer stays, so a switch to Your view frames it out
  // The craft's place: below and left of the star at the arrival, on the star's own move (15:50); while the person
  // steers, pilot-1e's framing
  const baseFraming = craftFraming ?? (camera ? laptopCraftFraming(camera) : undefined);
  const craftPlace = baseFraming && shift && !steer ? arrivalCraftFraming(baseFraming, viewportWidthPx, viewportHeightPx, phone, arrivalProgress(session)) : baseFraming;
  return (
    <>
    <div
      ref={layerEl}
      className="flight-stars"
      data-testid="flight-stars"
      data-state={state}
      data-view={cone ? 'cone' : 'flight'}
      data-stars={counts.stars}
      data-sky-sphere={counts.skySphere}
      data-no-distance-on-screen={counts.noDistanceOnScreen}
      data-from-earth-pc={fromEarthPc.toFixed(4)}
      data-bloom-px={bloom ? bloom.diameterPx.toFixed(1) : undefined}
      data-bloom-at={bloom ? `${bloom.xPx.toFixed(1)},${bloom.yPx.toFixed(1)}` : undefined}
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
    {!cone && steer && (
      <svg key="steering" ref={steerRings} className="flight-rings" data-testid="flight-rings" aria-hidden="true" width={viewportWidthPx} height={viewportHeightPx} style={{ position: 'absolute', inset: 0, pointerEvents: 'none', overflow: 'visible' }} />
    )}
    {rings.length > 0 && (
      <svg key="flight" className="flight-rings" data-testid="flight-rings" aria-hidden="true" width={viewportWidthPx} height={viewportHeightPx} style={{ position: 'absolute', inset: 0, pointerEvents: 'none', overflow: 'visible' }}>
        {rings.map((r) => (
          <circle key={r.findingId} data-finding-id={r.findingId} cx={r.xPx} cy={r.yPx} r={ringRadius} fill="none" stroke={RING_STROKE} strokeWidth={1} />
        ))}
      </svg>
    )}
    {cone && view && geometry && <ConeLayer session={session} view={view} geometry={geometry} keepOut={keepOut} onDrag={onDrag} onWheel={onWheel} />}
    {!cone && (onPressSky || onDragSky || steering) && (
      <div ref={sky} className="flight-sky" data-testid="flight-sky" data-steering={steer ? 'true' : 'false'} aria-hidden="true" style={{ position: 'absolute', inset: 0, pointerEvents: 'auto', touchAction: 'none', cursor: steer ? 'grab' : 'pointer' }} />
    )}
    {!cone && (craft || craftSeen) && camera && <FlightCraftLayer camera={camera} craft={craft ?? null} keyCandidates={keyCandidates} framing={craftPlace} phone={phone} steer={steer ? { positionPc: steer.positionPc, rows: steer.chase, craftRows: steer.rows } : null} drawRef={craftDraw} />}
    </>
  );
};
