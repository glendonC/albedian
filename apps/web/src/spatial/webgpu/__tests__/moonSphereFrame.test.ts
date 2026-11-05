import { SphereGeometry, Vector3 } from 'three';
import { describe, expect, it } from 'vitest';

/**
 * The Moon material (cislunar/bodyMaterials.ts) builds its tangent frame from the sphere's local
 * position: east = normalize(cross(+y, up)), north = cross(up, east). Its maps run from longitude
 * -180 at the left edge to +180 at the right, north at the top (scripts/bake_moon_maps.py). This
 * pins the convention against three's SphereGeometry layout, so a mirrored or rotated map, or a
 * flipped relief, fails here rather than on screen.
 */
const Y = new Vector3(0, 1, 0);
const frame = (up: Vector3) => {
  const east = new Vector3().crossVectors(Y, up).normalize();
  return { east, north: new Vector3().crossVectors(up, east) };
};

describe('Moon sphere frame against the map layout', () => {
  const geometry = new SphereGeometry(1, 64, 32);
  const position = geometry.getAttribute('position');
  const uvs = geometry.getAttribute('uv');
  const columns = 65;
  const at = (row: number, column: number) => row * columns + column;
  const point = (i: number) => new Vector3(position.getX(i), position.getY(i), position.getZ(i));

  it('puts each vertex at the longitude and latitude its uv names on the map', () => {
    for (let row = 1; row < 32; row++) {
      for (let column = 0; column < columns; column++) {
        const i = at(row, column);
        const p = point(i);
        const lonFromUv = -180 + 360 * uvs.getX(i);
        const latFromUv = -90 + 180 * uvs.getY(i);
        // Body frame from the mesh frame: x stays, y (90 E) is -z, z (north) is +y
        const lon = (Math.atan2(-p.z, p.x) * 180) / Math.PI;
        const lat = (Math.asin(Math.max(-1, Math.min(1, p.y))) * 180) / Math.PI;
        expect(Math.abs((((lon - lonFromUv) % 360) + 540) % 360 - 180)).toBeLessThan(1e-4);
        expect(Math.abs(lat - latFromUv)).toBeLessThan(1e-4);
      }
    }
  });

  it("points the material's east and north where u and v grow", () => {
    for (let row = 2; row <= 30; row++) {
      for (let column = 0; column < columns - 1; column++) {
        const p = point(at(row, column));
        const { east, north } = frame(p.clone().normalize());
        const towardNextU = point(at(row, column + 1)).sub(p);
        const towardHigherV = point(at(row - 1, column)).sub(p); // rows run from v = 1 (north) down
        expect(towardNextU.dot(east)).toBeGreaterThan(0);
        expect(towardHigherV.dot(north)).toBeGreaterThan(0);
      }
    }
  });

  it('is seen unmirrored from outside: with north up, east is to the right', () => {
    const up = new Vector3(1, 0, 0); // longitude 0 on the equator
    const { east, north } = frame(up);
    const forward = up.clone().negate(); // a viewer outside, looking at the centre
    const right = new Vector3().crossVectors(forward, north);
    expect(right.dot(east)).toBeCloseTo(1, 12);
  });
});
