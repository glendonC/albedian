import { ALBEDIAN_COLORS } from '@albedian/design-system';
import * as THREE from 'three';

/**
 * The observer's horizon in the sky view: the astronomical horizon (altitude 0) as a line, the
 * ground below it dimmed rather than hidden, and the compass points N, E, S and W, as planetarium
 * software draws them (Stellarium's horizon line, ground and cardinal points). It is built in the
 * observer's local frame (x east, y zenith, z south) and mounted on the camera's parent, which the
 * renderer turns into that frame (observerPose.ts). Drawn in the predicted accent, as the disclosure
 * classes it. No terrain and no refraction: real hills hide more, and refraction shows stars near
 * the horizon slightly higher than drawn [inference; refraction is not modelled here].
 */

/** Inside the catalog star mount (58) and the sky dome (900), so it draws in front of both. */
export const HORIZON_RADIUS = 40;

export const COMPASS_POINTS = [
  { label: 'N', azDeg: 0 },
  { label: 'E', azDeg: 90 },
  { label: 'S', azDeg: 180 },
  { label: 'W', azDeg: 270 },
] as const;

/** How much of the sky below the horizon still shows through the ground. */
const GROUND_OPACITY = 0.62;
/** Compass letters stand this far above the horizon, degrees, and this tall, degrees. */
const LETTER_ALTITUDE_DEG = 1.6;
const LETTER_HEIGHT_DEG = 2.2;
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
  ctx.font = '600 96px Geist, system-ui, -apple-system, "Segoe UI", sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = ALBEDIAN_COLORS.accentPredicted;
  ctx.fillText(label, 64, 68);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
};

/** The horizon line, the dimmed ground and, where `labelTexture` draws them, the compass letters. */
export function createObserverHorizon(labelTexture: CompassLabelTexture = () => null): THREE.Group {
  const group = new THREE.Group();
  group.name = 'observer-horizon';
  const colour = new THREE.Color(ALBEDIAN_COLORS.accentPredicted);

  const ground = new THREE.Mesh(
    // The lower half of a sphere: polar angle from 90 deg (the horizon) to 180 deg (the nadir)
    new THREE.SphereGeometry(HORIZON_RADIUS, 96, 24, 0, Math.PI * 2, Math.PI / 2, Math.PI / 2),
    new THREE.MeshBasicMaterial({
      color: ALBEDIAN_COLORS.voidObsidian,
      transparent: true,
      opacity: GROUND_OPACITY,
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
    new THREE.LineBasicMaterial({ color: colour, transparent: true, opacity: 0.8, depthTest: false, depthWrite: false })
  );
  line.name = 'observer-horizon-line';
  line.renderOrder = 11;
  line.frustumCulled = false;
  group.add(line);

  const letterSize = 2 * HORIZON_RADIUS * Math.tan((LETTER_HEIGHT_DEG * DEG) / 2);
  for (const point of COMPASS_POINTS) {
    const map = labelTexture(point.label);
    if (!map) continue;
    const sprite = new THREE.Sprite(
      new THREE.SpriteMaterial({ map, transparent: true, depthTest: false, depthWrite: false })
    );
    sprite.name = `observer-compass-${point.label}`;
    sprite.position.copy(localDirection(point.azDeg, LETTER_ALTITUDE_DEG).multiplyScalar(HORIZON_RADIUS));
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
