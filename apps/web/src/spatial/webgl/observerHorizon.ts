import * as THREE from 'three';

/**
 * The observer's horizon in the sky view: the astronomical horizon (altitude 0) as a line, the
 * ground below it dimmed rather than hidden, and the compass points N, E, S and W, as planetarium
 * software draws them (Stellarium's horizon line, ground and cardinal points). It is built in the
 * observer's local frame (x east, y zenith, z south) and mounted on the camera's parent, which the
 * renderer turns into that frame (observerPose.ts). Its look is D33's (.lead/uiux/UIUX-predsky-READY.md item 3,
 * decided 12:45; changed by WEB-UX with the lead's yes of 13:04): a 1 px line in ink at 32 percent, the ground #050506
 * at 90 percent with nothing drawn on it, and each compass letter in Geist 13 at ink 66 percent under the line with a
 * 6 px tick above it, as the design page draws them (.cache/uiux/predsky/build.mjs). The layer stays disclosed as
 * predicted. No terrain and no refraction: real hills hide more, and refraction shows stars near the horizon slightly
 * higher than drawn [inference; refraction is not modelled here].
 */

/** Inside the catalog star mount (58) and the sky dome (900), so it draws in front of both. */
export const HORIZON_RADIUS = 40;

export const COMPASS_POINTS = [
  { label: 'N', azDeg: 0 },
  { label: 'E', azDeg: 90 },
  { label: 'S', azDeg: 180 },
  { label: 'W', azDeg: 270 },
] as const;

/** D33's horizon: the line, the ground and the letters' ink. */
export const HORIZON_LOOK = {
  lineColour: '#f4f3ef',
  lineOpacity: 0.32,
  groundColour: '#050506',
  groundOpacity: 0.9,
  letterInk: 'rgba(244, 243, 239, 0.66)',
} as const;
/**
 * A compass label is a square drawn about its point on the horizon, this many pixels high on the screen at the sky
 * view's opening field of view (56 degrees high) on a 900 px window; it scales with zoom as the stars do [proposed:
 * the record sizes it in pixels, which a sprite in the sky keeps only at that field of view].
 */
export const LABEL_PX = 48;
const OPENING_PX_PER_DEG = 900 / 56;
const LABEL_HEIGHT_DEG = LABEL_PX / OPENING_PX_PER_DEG;
/** From the design page: the tick from 10 to 4 px above the point, 1.2 px wide; the letter's baseline 18 px under it. */
export const LABEL_MARKS = { tickTopPx: 10, tickFootPx: 4, tickWidthPx: 1.2, letterBaselinePx: 18, letterPx: 13 } as const;
const DEG = Math.PI / 180;

/** A unit direction in the observer's local frame from azimuth (north through east) and altitude. */
export function localDirection(azDeg: number, altDeg: number, target = new THREE.Vector3()): THREE.Vector3 {
  const a = azDeg * DEG;
  const h = altDeg * DEG;
  return target.set(Math.sin(a) * Math.cos(h), Math.sin(h), -Math.cos(a) * Math.cos(h));
}

/** A texture with one compass letter on a transparent background; null where none can be drawn. */
export type CompassLabelTexture = (label: string) => THREE.Texture | null;

/** Draws a compass letter on a canvas, in the browser; null where there is no 2D canvas. */
export const canvasCompassLabel: CompassLabelTexture = (label) => {
  if (typeof document === 'undefined') return null;
  const canvas = document.createElement('canvas');
  canvas.width = 128;
  canvas.height = 128;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  // Canvas pixels per screen pixel; the point on the horizon is the canvas's centre
  const k = 128 / LABEL_PX;
  const m = LABEL_MARKS;
  ctx.fillStyle = HORIZON_LOOK.letterInk;
  ctx.fillRect(64 - (m.tickWidthPx * k) / 2, 64 - m.tickTopPx * k, m.tickWidthPx * k, (m.tickTopPx - m.tickFootPx) * k);
  ctx.font = `400 ${m.letterPx * k}px Geist, system-ui, -apple-system, "Segoe UI", sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'alphabetic';
  ctx.fillText(label, 64, 64 + m.letterBaselinePx * k);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
};

/** The horizon line, the dimmed ground and, where `labelTexture` draws them, the compass letters. */
export function createObserverHorizon(labelTexture: CompassLabelTexture = () => null): THREE.Group {
  const group = new THREE.Group();
  group.name = 'observer-horizon';
  const colour = new THREE.Color(HORIZON_LOOK.lineColour);

  const ground = new THREE.Mesh(
    // The lower half of a sphere: polar angle from 90 deg (the horizon) to 180 deg (the nadir)
    new THREE.SphereGeometry(HORIZON_RADIUS, 96, 24, 0, Math.PI * 2, Math.PI / 2, Math.PI / 2),
    new THREE.MeshBasicMaterial({
      color: HORIZON_LOOK.groundColour,
      transparent: true,
      opacity: HORIZON_LOOK.groundOpacity,
      side: THREE.BackSide,
      depthTest: false,
      depthWrite: false,
    })
  );
  ground.name = 'observer-horizon-ground';
  // After the sky dome and the catalog stars, so it dims both
  ground.renderOrder = 10;
  ground.frustumCulled = false;
  group.add(ground);

  const points: THREE.Vector3[] = [];
  for (let i = 0; i < 720; i++) points.push(localDirection(i / 2, 0).multiplyScalar(HORIZON_RADIUS));
  const line = new THREE.LineLoop(
    new THREE.BufferGeometry().setFromPoints(points),
    new THREE.LineBasicMaterial({ color: colour, transparent: true, opacity: HORIZON_LOOK.lineOpacity, depthTest: false, depthWrite: false })
  );
  line.name = 'observer-horizon-line';
  line.renderOrder = 11;
  line.frustumCulled = false;
  group.add(line);

  const letterSize = 2 * HORIZON_RADIUS * Math.tan((LABEL_HEIGHT_DEG * DEG) / 2);
  for (const point of COMPASS_POINTS) {
    const map = labelTexture(point.label);
    if (!map) continue;
    const sprite = new THREE.Sprite(
      new THREE.SpriteMaterial({ map, transparent: true, depthTest: false, depthWrite: false })
    );
    sprite.name = `observer-compass-${point.label}`;
    // Centred on its point of the horizon: the tick stands above the line, the letter under it
    sprite.position.copy(localDirection(point.azDeg, 0).multiplyScalar(HORIZON_RADIUS));
    sprite.scale.setScalar(letterSize);
    sprite.renderOrder = 12;
    group.add(sprite);
  }
  return group;
}

/** Frees the horizon's geometry, materials and letter textures. */
export function disposeObserverHorizon(group: THREE.Group): void {
  group.traverse((o) => {
    if (o instanceof THREE.Mesh || o instanceof THREE.Line || o instanceof THREE.Sprite) {
      o.geometry.dispose();
      const material = o.material as THREE.Material & { map?: THREE.Texture | null };
      material.map?.dispose();
      material.dispose();
    }
  });
}
