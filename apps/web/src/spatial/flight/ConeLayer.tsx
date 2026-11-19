import React, { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import type { ObservationSession } from '@albedian/contracts';
import type { StarView } from './starView.js';
import { coneSceneOf } from './coneView.js';

/**
 * cone-1: the photo's view cone drawn over the star layer in the third-person view: its four faces faintly, its edges
 * from Earth to the base, and the photo on the base, through the same orbit camera the stars are drawn with. It
 * follows a drag (turn and tilt) and the wheel (closer and farther) by telling its parent, and has no movement of its
 * own. The look here is the There screen's plain white at low opacity [proposed] until the design lane's record.
 */

type ConeState = 'loading' | 'drawn' | 'failed';

interface ConeGpu {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  camera: THREE.Camera;
  faces: THREE.Mesh;
  edges: THREE.LineSegments;
  photo: THREE.Mesh;
}

function drawCone(gpu: ConeGpu, session: ObservationSession, view: StarView, lengthPc: number): boolean {
  const scene = coneSceneOf(session, view, lengthPc);
  if (!scene) return false;
  const a = scene.apexFromCamera;
  const c = scene.cornersFromCamera;
  const faces = new Float32Array(4 * 9);
  for (let i = 0; i < 4; i++) faces.set([...a, ...c[i]!, ...c[(i + 1) % 4]!], 9 * i);
  gpu.faces.geometry.setAttribute('position', new THREE.BufferAttribute(faces, 3));
  const edges = new Float32Array(8 * 6);
  for (let i = 0; i < 4; i++) {
    edges.set([...a, ...c[i]!], 6 * i);
    edges.set([...c[i]!, ...c[(i + 1) % 4]!], 6 * (4 + i));
  }
  gpu.edges.geometry.setAttribute('position', new THREE.BufferAttribute(edges, 3));
  const g = gpu.photo.geometry;
  g.setAttribute('position', new THREE.BufferAttribute(scene.plane.positions, 3));
  g.setAttribute('uv', new THREE.BufferAttribute(scene.plane.uvs, 2));
  if (!g.index) {
    const idx: number[] = [];
    const { cols, rows } = scene.plane;
    for (let j = 0; j < rows - 1; j++) {
      for (let i = 0; i < cols - 1; i++) {
        const k = j * cols + i;
        idx.push(k, k + cols, k + 1, k + 1, k + cols, k + cols + 1);
      }
    }
    g.setIndex(idx);
  }
  gpu.camera.projectionMatrix.fromArray(scene.clipMatrix);
  gpu.camera.projectionMatrixInverse.copy(gpu.camera.projectionMatrix).invert();
  gpu.renderer.render(gpu.scene, gpu.camera);
  return true;
}

export const ConeLayer: React.FC<{
  session: ObservationSession;
  view: StarView;
  lengthPc: number;
  onDrag: (dxPx: number, dyPx: number) => void;
  onWheel: (deltaY: number) => void;
}> = ({ session, view, lengthPc, onDrag, onWheel }) => {
  const canvas = useRef<HTMLCanvasElement>(null);
  const holder = useRef<HTMLDivElement>(null);
  const gpu = useRef<ConeGpu | null>(null);
  const [state, setState] = useState<ConeState>('loading');
  const dpr = typeof window === 'undefined' ? 1 : Math.min(2, window.devicePixelRatio || 1);
  const uri = session.sourceAsset?.uri;

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
    renderer.setSize(view.viewportWidthPx, view.viewportHeightPx, false);
    renderer.setClearColor(0x000000, 0);
    const scene = new THREE.Scene();
    const camera = new THREE.Camera();
    camera.matrixAutoUpdate = false;
    const white = (opacity: number) => ({ color: 0xffffff, transparent: true, opacity, depthWrite: false });
    const faces = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial({ ...white(0.06), side: THREE.DoubleSide }));
    const edges = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial(white(0.5)));
    const photo = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide, transparent: true, opacity: 0.92 }));
    for (const o of [faces, edges, photo]) o.frustumCulled = false;
    scene.add(faces, edges, photo);
    gpu.current = { renderer, scene, camera, faces, edges, photo };
    let current = true;
    if (uri) {
      new THREE.TextureLoader().load(
        uri,
        (texture) => {
          if (!current || !gpu.current) return;
          texture.colorSpace = THREE.SRGBColorSpace;
          (photo.material as THREE.MeshBasicMaterial).map = texture;
          (photo.material as THREE.MeshBasicMaterial).needsUpdate = true;
        },
        undefined,
        () => {}
      );
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
    g.renderer.setSize(view.viewportWidthPx, view.viewportHeightPx, false);
    setState(drawCone(g, session, view, lengthPc) ? 'drawn' : 'failed');
  });

  // A drag turns and tilts, the wheel comes closer or goes back; nothing keeps moving after the hand stops
  useEffect(() => {
    const el = holder.current;
    if (!el) return undefined;
    let last: { x: number; y: number } | null = null;
    const down = (e: PointerEvent) => {
      last = { x: e.clientX, y: e.clientY };
      el.setPointerCapture?.(e.pointerId);
    };
    const move = (e: PointerEvent) => {
      if (!last) return;
      onDrag(e.clientX - last.x, e.clientY - last.y);
      last = { x: e.clientX, y: e.clientY };
    };
    const up = () => {
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

  return (
    <div ref={holder} className="flight-cone" data-testid="flight-cone" data-state={state} aria-hidden="true" style={{ position: 'absolute', inset: 0, pointerEvents: 'auto', touchAction: 'none', cursor: 'grab' }}>
      <canvas ref={canvas} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', display: 'block' }} />
    </div>
  );
};
