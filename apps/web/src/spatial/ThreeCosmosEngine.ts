import * as THREE from 'three';
import type { CelestialFinding, ObservationSession } from '@albedian/contracts';
import {
  ALL_SKY_CONSTELLATION_EDGES,
  GAIA_BACKGROUND_STARFIELD_3D,
  bpRpToStarColorHex,
} from './allSkyStarfield.js';
import type { ProjectedMarker } from './MarkerOverlay.js';

function createStarSpriteTexture(): THREE.CanvasTexture | null {
  if (typeof document === 'undefined') return null;
  const canvas = document.createElement('canvas');
  canvas.width = 64;
  canvas.height = 64;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;

  const grad = ctx.createRadialGradient(32, 32, 0.5, 32, 32, 28);
  grad.addColorStop(0, 'rgba(255, 255, 255, 1)');
  grad.addColorStop(0.18, 'rgba(245, 247, 250, 0.92)');
  grad.addColorStop(0.45, 'rgba(124, 196, 223, 0.25)');
  grad.addColorStop(1, 'rgba(0, 0, 0, 0)');
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 64, 64);

  const tex = new THREE.CanvasTexture(canvas);
  tex.needsUpdate = true;
  return tex;
}

export interface ThreeCosmosRenderParams {
  width: number;
  height: number;
  session: ObservationSession;
  selectedFinding: CelestialFinding | undefined;
  mode: 'frustum_3d' | 'space_3d';
  orbitYawRad: number;
  orbitPitchRad: number;
  orbitDistanceScale: number;
  videoOrImageSource: HTMLVideoElement | HTMLImageElement | null;
  theme?: 'dark' | 'light';
}

export class ThreeCosmosEngine {
  private renderer: THREE.WebGLRenderer | null = null;
  private scene: THREE.Scene;
  private camera: THREE.PerspectiveCamera;
  private starTexture: THREE.CanvasTexture | null = null;
  private dynamicGroup: THREE.Group;
  private rafId = 0;

  constructor(container: HTMLElement) {
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x05070b);

    this.camera = new THREE.PerspectiveCamera(48, 16 / 9, 0.1, 4000);
    this.dynamicGroup = new THREE.Group();
    this.scene.add(this.dynamicGroup);

    this.starTexture = createStarSpriteTexture();
    this.buildStaticAllSkyStarfield();

    try {
      this.renderer = new THREE.WebGLRenderer({
        antialias: true,
        preserveDrawingBuffer: true,
        powerPreference: 'high-performance',
      });
      this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      this.renderer.xr.enabled = true;
      this.renderer.domElement.style.width = '100%';
      this.renderer.domElement.style.height = '100%';
      this.renderer.domElement.style.display = 'block';
      container.appendChild(this.renderer.domElement);
      this.startAnimationLoop();
    } catch {
      this.renderer = null;
    }
  }

  private startAnimationLoop(): void {
    if (typeof window === 'undefined' || !this.renderer) return;
    const tick = () => {
      if (!this.renderer) return;
      this.renderer.render(this.scene, this.camera);
      this.rafId = window.requestAnimationFrame(tick);
    };
    this.rafId = window.requestAnimationFrame(tick);
  }

  public async enterWebXRSession(): Promise<boolean> {
    if (!this.renderer || typeof navigator === 'undefined' || !('xr' in navigator)) {
      return false;
    }
    try {
      const xr = (navigator as Navigator & {
        xr?: {
          requestSession: (mode: string, opts?: Record<string, unknown>) => Promise<unknown>;
        };
      }).xr;
      if (!xr) return false;
      const session = await xr.requestSession('immersive-vr', {
        optionalFeatures: ['local-floor', 'bounded-floor', 'hand-tracking'],
      });
      await (this.renderer.xr as unknown as { setSession: (s: unknown) => Promise<void> }).setSession(
        session
      );
      return true;
    } catch {
      return false;
    }
  }

  private buildStaticAllSkyStarfield(): void {
    const count = GAIA_BACKGROUND_STARFIELD_3D.length;
    const positions = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3);

    GAIA_BACKGROUND_STARFIELD_3D.forEach((s, idx) => {
      positions[idx * 3] = s.cartesianParsec[0];
      positions[idx * 3 + 1] = s.cartesianParsec[1];
      positions[idx * 3 + 2] = s.cartesianParsec[2];

      const col = new THREE.Color(s.colorHex || bpRpToStarColorHex(s.bpRp));
      colors[idx * 3] = col.r;
      colors[idx * 3 + 1] = col.g;
      colors[idx * 3 + 2] = col.b;
    });

    const starGeo = new THREE.BufferGeometry();
    starGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    starGeo.setAttribute('color', new THREE.BufferAttribute(colors, 3));

    const starMat = new THREE.PointsMaterial({
      size: 3.4,
      map: this.starTexture ?? undefined,
      vertexColors: true,
      transparent: true,
      opacity: 0.85,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    this.scene.add(new THREE.Points(starGeo, starMat));

    // Subtle 3D Constellation Filaments
    const namedMap = new Map(
      GAIA_BACKGROUND_STARFIELD_3D.filter((s) => Boolean(s.name)).map((s) => [s.id, s])
    );
    const edgeCoords: number[] = [];
    for (const edge of ALL_SKY_CONSTELLATION_EDGES) {
      const a = namedMap.get(edge.fromId);
      const b = namedMap.get(edge.toId);
      if (!a || !b) continue;
      edgeCoords.push(
        a.cartesianParsec[0],
        a.cartesianParsec[1],
        a.cartesianParsec[2],
        b.cartesianParsec[0],
        b.cartesianParsec[1],
        b.cartesianParsec[2]
      );
    }
    if (edgeCoords.length > 0) {
      const edgeGeo = new THREE.BufferGeometry();
      edgeGeo.setAttribute('position', new THREE.Float32BufferAttribute(edgeCoords, 3));
      this.scene.add(
        new THREE.LineSegments(
          edgeGeo,
          new THREE.LineBasicMaterial({
            color: 0x7cc4df,
            transparent: true,
            opacity: 0.18,
          })
        )
      );
    }

    // Earth Origin Marker at [0, 0, 0] ("You on Earth")
    const earthGeo = new THREE.SphereGeometry(1.6, 20, 20);
    const earthMat = new THREE.MeshBasicMaterial({ color: 0x7cc4df });
    this.scene.add(new THREE.Mesh(earthGeo, earthMat));

    // Clean Concentric Cosmic Distance Rings around Earth (25 pc, 75 pc, 150 pc)
    for (const radiusPc of [25, 75, 150]) {
      const segs = 96;
      const pts: number[] = [];
      for (let i = 0; i < segs; i++) {
        const a0 = (i / segs) * Math.PI * 2;
        const a1 = ((i + 1) / segs) * Math.PI * 2;
        pts.push(
          Math.cos(a0) * radiusPc,
          0,
          Math.sin(a0) * radiusPc,
          Math.cos(a1) * radiusPc,
          0,
          Math.sin(a1) * radiusPc
        );
      }
      const ringGeo = new THREE.BufferGeometry();
      ringGeo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
      this.scene.add(
        new THREE.LineSegments(
          ringGeo,
          new THREE.LineBasicMaterial({
            color: radiusPc === 75 ? 0x7cc4df : 0x3b4454,
            transparent: true,
            opacity: radiusPc === 75 ? 0.28 : 0.16,
          })
        )
      );
    }
  }

  public renderFrame(params: ThreeCosmosRenderParams): ProjectedMarker[] {
    const {
      width,
      height,
      session,
      selectedFinding,
      orbitYawRad,
      orbitPitchRad,
      orbitDistanceScale,
    } = params;

    if (this.renderer) {
      const currentSize = this.renderer.getSize(new THREE.Vector2());
      if (currentSize.x !== width || currentSize.y !== height) {
        this.renderer.setSize(width, height, false);
      }
    }

    this.camera.aspect = Math.max(0.2, width / Math.max(1, height));
    this.camera.updateProjectionMatrix();

    while (this.dynamicGroup.children.length > 0) {
      const child = this.dynamicGroup.children[0]!;
      this.dynamicGroup.remove(child);
    }

    const findingsWith3D = session.observationSolution.findings.filter(
      (f) => f.cartesianParsecICRS && f.inferredDistancePc && f.inferredDistancePc.median > 0
    );

    const targetCoords: number[] = [];
    const targetColors: number[] = [];

    for (const f of findingsWith3D) {
      const [fx, fy, fz] = f.cartesianParsecICRS!;
      const dist = Math.hypot(fx, fy, fz);
      const scale = dist < 0.1 ? 18.0 / Math.max(1e-6, dist) : 1.0;
      const vx = fx * scale;
      const vy = fy * scale;
      const vz = fz * scale;

      targetCoords.push(vx, vy, vz);
      const col = new THREE.Color(
        f.id === selectedFinding?.id
          ? '#7CC4DF'
          : bpRpToStarColorHex(f.catalogRef?.colorIndexBpRp ?? 0.15)
      );
      targetColors.push(col.r, col.g, col.b);

      // Draw ONE clean sightline beam from Earth [0,0,0] to the selected target star
      if (f.id === selectedFinding?.id) {
        const lineGeo = new THREE.BufferGeometry().setFromPoints([
          new THREE.Vector3(0, 0, 0),
          new THREE.Vector3(vx, vy, vz),
        ]);
        this.dynamicGroup.add(
          new THREE.Line(
            lineGeo,
            new THREE.LineBasicMaterial({
              color: 0x7cc4df,
              transparent: true,
              opacity: 0.72,
            })
          )
        );
      }
    }

    if (targetCoords.length > 0) {
      const nodeGeo = new THREE.BufferGeometry();
      nodeGeo.setAttribute('position', new THREE.Float32BufferAttribute(targetCoords, 3));
      nodeGeo.setAttribute('color', new THREE.Float32BufferAttribute(targetColors, 3));
      this.dynamicGroup.add(
        new THREE.Points(
          nodeGeo,
          new THREE.PointsMaterial({
            size: 6.2,
            map: this.starTexture ?? undefined,
            vertexColors: true,
            transparent: true,
            depthWrite: false,
            blending: THREE.AdditiveBlending,
          })
        )
      );
    }

    let lookTarget = new THREE.Vector3(0, 0, 0);
    if (
      selectedFinding?.cartesianParsecICRS &&
      Math.hypot(...selectedFinding.cartesianParsecICRS) >= 1
    ) {
      const [sx, sy, sz] = selectedFinding.cartesianParsecICRS;
      lookTarget = new THREE.Vector3(sx * 0.45, sy * 0.45, sz * 0.45);
    }

    const baseDist = Math.max(135, lookTarget.length() * 1.45);
    const radius = baseDist * orbitDistanceScale;
    const camOffset = new THREE.Vector3(
      Math.sin(orbitYawRad) * Math.cos(orbitPitchRad) * radius,
      Math.sin(orbitPitchRad) * radius,
      Math.cos(orbitYawRad) * Math.cos(orbitPitchRad) * radius
    );

    this.camera.position.copy(lookTarget).add(camOffset);
    this.camera.lookAt(lookTarget);
    this.camera.updateMatrixWorld();

    if (this.renderer) {
      this.renderer.render(this.scene, this.camera);
    }

    const projected: ProjectedMarker[] = [];
    for (const f of findingsWith3D) {
      const [fx, fy, fz] = f.cartesianParsecICRS!;
      const d = Math.hypot(fx, fy, fz);
      const s = d < 0.1 ? 18.0 / Math.max(1e-6, d) : 1.0;
      const worldPos = new THREE.Vector3(fx * s, fy * s, fz * s);

      const distancePc = worldPos.distanceTo(this.camera.position);
      const ndc = worldPos.clone().project(this.camera);
      const visible =
        ndc.z >= -1 && ndc.z <= 1 && Math.abs(ndc.x) <= 0.95 && Math.abs(ndc.y) <= 0.95;
      const screenX = (ndc.x * 0.5 + 0.5) * width;
      const screenY = (-ndc.y * 0.5 + 0.5) * height;

      projected.push({
        finding: f,
        screenX,
        screenY,
        distancePc,
        visible,
      });
    }

    return projected;
  }

  public dispose(): void {
    if (this.rafId && typeof window !== 'undefined') {
      window.cancelAnimationFrame(this.rafId);
    }
    if (this.starTexture) {
      this.starTexture.dispose();
    }
    if (this.renderer) {
      this.renderer.dispose();
      this.renderer.domElement.remove();
      this.renderer = null;
    }
  }
}
