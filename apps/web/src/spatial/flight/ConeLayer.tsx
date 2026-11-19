import React, { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import type { ObservationSession } from '@albedian/contracts';
import { pinholeScreenOf, type PhotoPinhole, type Vec3 } from '@albedian/camera-rig';
import { pinholeOfView, type StarView } from './starView.js';
import { coneSceneOf, pinchWheelDelta, type ConeGeometry } from './coneView.js';
import { placeFinding } from './flightFrame.js';
import type { ScreenBox } from './flightRings.js';

/**
 * cone-1 as D28 draws it (UIUX-cone-READY.md): the cone's faces in white at 3.5 percent, its four edges 1 px white at
 * 45 percent, the far end dashed (3 4) at 30 percent, Earth a 3 px dot with its name, the photo on its cross-section at
 * 92 percent in a 1.2 px frame at 90 percent, every matched star a white dot with D27's ring (dashed for one with no
 * usable distance, drawn at the far end along its direction), the picked star with its mark and name; no ring or name
 * under the disclosure, the readout or the bar. All through the orbit camera the stars are drawn with. It follows a
 * drag and the wheel by telling its parent, and has no movement of its own.
 */

type ConeState = 'loading' | 'drawn' | 'failed';

const INK = '#f4f3ef';

/** A segment cut to the part at least `nearZ` in front of the camera (camera-relative points), or none. */
function clipSegment(pin: PhotoPinhole, a: Vec3, b: Vec3, nearZ: number): [Vec3, Vec3] | null {
  const za = a[0] * pin.rows.z[0] + a[1] * pin.rows.z[1] + a[2] * pin.rows.z[2];
  const zb = b[0] * pin.rows.z[0] + b[1] * pin.rows.z[1] + b[2] * pin.rows.z[2];
  if (za <= nearZ && zb <= nearZ) return null;
  if (za > nearZ && zb > nearZ) return [a, b];
  const t = (nearZ - za) / (zb - za);
  const c: Vec3 = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  return za > nearZ ? [a, c] : [c, b];
}

/** A polygon cut to the part at least `nearZ` in front of the camera (one plane of Sutherland and Hodgman), on screen. */
function clipPolygon(pin: PhotoPinhole, points: Vec3[], nearZ: number): Array<{ xPx: number; yPx: number }> {
  const z = (p: Vec3) => p[0] * pin.rows.z[0] + p[1] * pin.rows.z[1] + p[2] * pin.rows.z[2];
  const out: Vec3[] = [];
  for (let i = 0; i < points.length; i++) {
    const a = points[i]!;
    const b = points[(i + 1) % points.length]!;
    const inA = z(a) > nearZ;
    const inB = z(b) > nearZ;
    if (inA) out.push(a);
    if (inA !== inB) {
      const t = (nearZ - z(a)) / (z(b) - z(a));
      out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]);
    }
  }
  return out.flatMap((p) => pinholeScreenOf(pin, p) ?? []);
}

const pt = (p: { xPx: number; yPx: number }) => `${p.xPx.toFixed(1)},${p.yPx.toFixed(1)}`;

function inside(box: ScreenBox, x: number, y: number, r: number): boolean {
  return x + r >= box.left && x - r <= box.right && y + r >= box.top && y - r <= box.bottom;
}

/** The cone's line work, Earth and the stars as SVG, through the orbit camera. */
export function coneOverlay(session: ObservationSession, view: StarView, geometry: ConeGeometry, keepOut: readonly ScreenBox[]): React.ReactElement[] {
  const scene = coneSceneOf(session, view, geometry);
  if (!scene) return [];
  const pin = pinholeOfView(view);
  const out: React.ReactElement[] = [];
  const a = scene.apexFromCamera;
  const far = scene.farFromCamera;
  // Cut what passes behind the camera a little in front of it (a hundredth of its distance from Earth), so no point
  // projects to a coordinate too large to draw
  const nearZ = Math.max(1e-6, 0.01 * Math.hypot(a[0], a[1], a[2]));
  far.forEach((c, i) => {
    const face = clipPolygon(pin, [a, c, far[(i + 1) % 4]!], nearZ);
    if (face.length >= 3) out.push(<polygon key={`face-${i}`} points={face.map(pt).join(' ')} fill={INK} fillOpacity={0.035} />);
  });
  far.forEach((c, i) => {
    const seg = clipSegment(pin, a, c, nearZ);
    const p = seg && pinholeScreenOf(pin, seg[0]);
    const q = seg && pinholeScreenOf(pin, seg[1]);
    if (p && q) out.push(<line key={`edge-${i}`} x1={p.xPx} y1={p.yPx} x2={q.xPx} y2={q.yPx} stroke={INK} strokeOpacity={0.45} strokeWidth={1} />);
  });
  far.forEach((c, i) => {
    const seg = clipSegment(pin, c, far[(i + 1) % 4]!, nearZ);
    const p = seg && pinholeScreenOf(pin, seg[0]);
    const q = seg && pinholeScreenOf(pin, seg[1]);
    if (p && q) out.push(<line key={`far-${i}`} x1={p.xPx} y1={p.yPx} x2={q.xPx} y2={q.yPx} stroke={INK} strokeOpacity={0.3} strokeWidth={1} strokeDasharray="3 4" />);
  });
  const frame = clipPolygon(pin, scene.photoFromCamera, nearZ);
  if (frame.length >= 3) out.push(<polygon key="photo-frame" data-testid="cone-photo-frame" points={frame.map(pt).join(' ')} fill="none" stroke={INK} strokeOpacity={0.9} strokeWidth={1.2} />);
  // Your stars: at their catalog places, or at the far end along their direction when they have no usable distance
  const cam = view.observerPc;
  let picked: { xPx: number; yPx: number; title: string } | null = null;
  for (const f of session.observationSolution?.findings ?? []) {
    if (f.evidenceClass !== 'catalog_matched') continue;
    const p = placeFinding(f);
    const world: Vec3 = p.kind === 'at_distance' ? p.positionPc : (() => {
      const d = p.direction;
      const along = d[0] * geometry.axis[0] + d[1] * geometry.axis[1] + d[2] * geometry.axis[2];
      const t = along > 0 ? geometry.lengthPc / along : geometry.lengthPc;
      return [d[0] * t, d[1] * t, d[2] * t] as Vec3;
    })();
    const s = pinholeScreenOf(pin, [world[0] - cam[0], world[1] - cam[1], world[2] - cam[2]]);
    if (!s || s.xPx < 0 || s.xPx > view.viewportWidthPx || s.yPx < 0 || s.yPx > view.viewportHeightPx) continue;
    if (f.id === session.selectedFindingId) {
      picked = { ...s, title: f.title };
      continue;
    }
    if (keepOut.some((box) => inside(box, s.xPx, s.yPx, 6))) continue;
    out.push(
      <g key={`star-${f.id}`} data-testid="cone-star" data-finding-id={f.id} data-placement={p.kind}>
        <circle cx={s.xPx} cy={s.yPx} r={1.8} fill={INK} />
        <circle cx={s.xPx} cy={s.yPx} r={6} fill="none" stroke={INK} strokeOpacity={0.7} strokeDasharray={p.kind === 'at_distance' ? undefined : '2 2'} />
      </g>
    );
  }
  if (picked && !keepOut.some((box) => inside(box, picked!.xPx, picked!.yPx, 10))) {
    out.push(
      <g key="picked" data-testid="cone-picked">
        <circle cx={picked.xPx} cy={picked.yPx} r={10} fill="none" stroke={INK} strokeWidth={1.4} />
        <circle cx={picked.xPx} cy={picked.yPx} r={2.6} fill={INK} />
        <text x={picked.xPx + 16} y={picked.yPx + 5} fill={INK} fontFamily="var(--look-font-sans)" fontSize={15} fontWeight={500} style={{ paintOrder: 'stroke', stroke: 'rgba(0,0,0,.75)', strokeWidth: 3 }}>
          {picked.title}
        </text>
      </g>
    );
  }
  const earth = pinholeScreenOf(pin, a);
  if (earth) {
    out.push(
      <g key="earth" data-testid="cone-earth">
        <circle cx={earth.xPx} cy={earth.yPx} r={3} fill={INK} />
        <text x={earth.xPx - 10} y={earth.yPx + 22} textAnchor="end" fill={INK} fillOpacity={0.8} fontFamily="var(--look-font-sans)" fontSize={13}>
          Earth
        </text>
      </g>
    );
  }
  return out;
}

export const ConeLayer: React.FC<{
  session: ObservationSession;
  view: StarView;
  geometry: ConeGeometry;
  keepOut: readonly ScreenBox[];
  onDrag: (dxPx: number, dyPx: number) => void;
  onWheel: (deltaY: number) => void;
}> = ({ session, view, geometry, keepOut, onDrag, onWheel }) => {
  const canvas = useRef<HTMLCanvasElement>(null);
  const holder = useRef<HTMLDivElement>(null);
  const gpu = useRef<{ renderer: THREE.WebGLRenderer; scene: THREE.Scene; camera: THREE.Camera; photo: THREE.Mesh } | null>(null);
  const [state, setState] = useState<ConeState>('loading');
  const dpr = typeof window === 'undefined' ? 1 : Math.min(2, window.devicePixelRatio || 1);
  const uri = session.sourceAsset?.uri;

  // The photo on its cross-section, through three.js (its pixels each in their solved direction)
  useEffect(() => {
    const el = canvas.current;
    if (!el) return undefined;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ canvas: el, alpha: true, antialias: true, premultipliedAlpha: true });
    } catch {
      setState('failed');
      return undefined;
    }
    renderer.setPixelRatio(dpr);
    renderer.setClearColor(0x000000, 0);
    const scene = new THREE.Scene();
    const camera = new THREE.Camera();
    camera.matrixAutoUpdate = false;
    const photo = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide, transparent: true, opacity: 0.92, depthWrite: false }));
    photo.frustumCulled = false;
    scene.add(photo);
    gpu.current = { renderer, scene, camera, photo };
    let current = true;
    if (uri) {
      new THREE.TextureLoader().load(uri, (texture) => {
        if (!current || !gpu.current) return;
        texture.colorSpace = THREE.SRGBColorSpace;
        const m = photo.material as THREE.MeshBasicMaterial;
        m.map = texture;
        m.needsUpdate = true;
        setState('loading');
      });
    }
    return () => {
      current = false;
      gpu.current = null;
      renderer.dispose();
    };
  }, [uri, dpr]);

  useEffect(() => {
    const g = gpu.current;
    if (!g) return;
    const scene = coneSceneOf(session, view, geometry);
    if (!scene) {
      setState('failed');
      return;
    }
    g.renderer.setSize(view.viewportWidthPx, view.viewportHeightPx, false);
    const geo = g.photo.geometry;
    geo.setAttribute('position', new THREE.BufferAttribute(scene.plane.positions, 3));
    geo.setAttribute('uv', new THREE.BufferAttribute(scene.plane.uvs, 2));
    if (!geo.index) {
      const idx: number[] = [];
      const { cols, rows } = scene.plane;
      for (let j = 0; j < rows - 1; j++) {
        for (let i = 0; i < cols - 1; i++) {
          const k = j * cols + i;
          idx.push(k, k + cols, k + 1, k + 1, k + cols, k + cols + 1);
        }
      }
      geo.setIndex(idx);
    }
    g.camera.projectionMatrix.fromArray(scene.clipMatrix);
    g.camera.projectionMatrixInverse.copy(g.camera.projectionMatrix).invert();
    g.renderer.render(g.scene, g.camera);
    setState('drawn');
  });

  // A drag turns round Earth, the wheel (a trackpad's pinch arrives as one) or two fingers' pinch on a touch screen
  // (D30 item 7) comes closer; nothing moves after the hand
  useEffect(() => {
    const el = holder.current;
    if (!el) return undefined;
    let last: { x: number; y: number } | null = null;
    const fingers = new Map<number, { x: number; y: number }>();
    const spread = () => {
      const [a, b] = [...fingers.values()];
      return a && b ? Math.hypot(a.x - b.x, a.y - b.y) : 0;
    };
    const down = (e: PointerEvent) => {
      fingers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      last = fingers.size === 1 ? { x: e.clientX, y: e.clientY } : null;
      el.setPointerCapture?.(e.pointerId);
    };
    const move = (e: PointerEvent) => {
      if (fingers.size >= 2 && fingers.has(e.pointerId)) {
        const before = spread();
        fingers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        onWheel(pinchWheelDelta(before, spread()));
        return;
      }
      if (!last) return;
      onDrag(e.clientX - last.x, e.clientY - last.y);
      last = { x: e.clientX, y: e.clientY };
    };
    const up = (e: PointerEvent) => {
      fingers.delete(e.pointerId);
      last = null;
    };
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      onWheel(e.deltaY);
    };
    el.addEventListener('pointerdown', down);
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerup', up);
    el.addEventListener('pointercancel', up);
    el.addEventListener('wheel', wheel, { passive: false });
    return () => {
      el.removeEventListener('pointerdown', down);
      el.removeEventListener('pointermove', move);
      el.removeEventListener('pointerup', up);
      el.removeEventListener('pointercancel', up);
      el.removeEventListener('wheel', wheel);
    };
  }, [onDrag, onWheel]);

  const parts = coneOverlay(session, view, geometry, keepOut);
  const isUnder = (e: React.ReactElement) => /^(face|edge|far)-/.test(String(e.key));
  return (
    <div ref={holder} className="flight-cone" data-testid="flight-cone" data-state={state} aria-hidden="true" style={{ position: 'absolute', inset: 0, pointerEvents: 'auto', touchAction: 'none', cursor: 'grab' }}>
      <svg width={view.viewportWidthPx} height={view.viewportHeightPx} style={{ position: 'absolute', inset: 0, overflow: 'visible' }}>
        {parts.filter(isUnder)}
      </svg>
      <canvas ref={canvas} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', display: 'block' }} />
      <svg width={view.viewportWidthPx} height={view.viewportHeightPx} style={{ position: 'absolute', inset: 0, overflow: 'visible' }}>
        {parts.filter((e) => !isUnder(e))}
      </svg>
    </div>
  );
};
