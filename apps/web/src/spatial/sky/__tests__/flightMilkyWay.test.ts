import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { panoramaUvOfIcrs } from '../../../reveal/realSkyGrid.js';
import { CRAFT_SKY_URL } from '../../flight/craftLight.js';
import { parsePanoramaRegistration } from '../skyRegistration.js';
import {
  FLIGHT_DOME_FRAGMENT,
  FLIGHT_GLOW_FRAGMENT,
  FLIGHT_MILKY_WAY_LINE,
  FLIGHT_MILKY_WAY_URL,
  GLOW_SIZE_PX,
  PLANET_WINDOWS,
  domeDirectionAt,
  glowUvOfIcrs,
  planetWindowAt,
  type DomeCamera,
} from '../flightMilkyWay.js';

/**
 * milkyway-flight-one, ruled by the lead at 16:18 on 24 Nov: in flight the Milky Way is the baked starless glow at full
 * level, drawn as a dome while steering too, with the ruled line; Venus and Jupiter, which the bake puts back as
 * photographed, are filled from the glow around them in the flight's copy (reports/LOOK/milkyway-flight-one.md).
 */
const here = path.dirname(fileURLToPath(import.meta.url));
const registration = parsePanoramaRegistration(JSON.parse(fs.readFileSync(path.resolve(here, '../../../../public/textures/eso0932a_registration.json'), 'utf8')));

type V3 = [number, number, number];
const norm = (v: V3): V3 => { const n = Math.hypot(...v); return [v[0] / n, v[1] / n, v[2] / n]; };
const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const unit = (raDeg: number, decDeg: number): V3 => {
  const r = (raDeg * Math.PI) / 180;
  const d = (decDeg * Math.PI) / 180;
  return [Math.cos(d) * Math.cos(r), Math.cos(d) * Math.sin(r), Math.sin(d)];
};

// A camera like rp-b-01's solve looking at Hadar: portrait photo 3024x4032, 0.010286 deg a pixel, a mild lens, shown in
// a 1440x900 window by an affine (stored pixel to CSS pixel)
const z = unit(210.956, -60.373);
const x = norm(cross([0, 0, 1], z));
const y = cross(z, x);
const camera: DomeCamera = {
  rows: { x, y, z },
  crpixX: 1511.5,
  crpixY: 2015.5,
  scaleRadPerPx: (0.010286232 * Math.PI) / 180,
  k1: -0.031,
  k2: 0.004,
  storedToScreen: [900 / 4032, 0, 720 - (1511.5 * 900) / 4032, 0, 900 / 4032, 450 - (2015.5 * 900) / 4032],
};

/** The flight's own forward projection for a direction (flightStarShader.ts, the unplaced path), in TypeScript. */
function project(v: V3): [number, number] {
  const zz = dot(camera.rows.z as V3, v);
  const xr = dot(camera.rows.x as V3, v) / zz;
  const yr = dot(camera.rows.y as V3, v) / zz;
  const r2 = xr * xr + yr * yr;
  const k = 1 + camera.k1 * r2 + camera.k2 * r2 * r2;
  const u = camera.crpixX + (xr * k) / camera.scaleRadPerPx;
  const w = camera.crpixY - (yr * k) / camera.scaleRadPerPx;
  const m = camera.storedToScreen;
  return [m[0] * u + m[1] * w + m[2], m[3] * u + m[4] * w + m[5]];
}

describe('the flight Milky Way: the baked starless glow, with the ruled line', () => {
  it('reads the glow, never the published photograph', () => {
    expect(FLIGHT_MILKY_WAY_URL).toBe('/textures/eso_milkyway_diffuse_6k.webp');
    expect(FLIGHT_MILKY_WAY_URL).toBe(CRAFT_SKY_URL);
    expect(GLOW_SIZE_PX).toEqual({ width: 6000, height: 3000 });
  });

  it('says the line exactly as ruled', () => {
    expect(FLIGHT_MILKY_WAY_LINE).toBe("Milky Way: ESO/S. Brunier's photograph as seen from Earth, with its own stars removed.");
  });
});

describe('Venus and Jupiter, put back by the bake, are filled from the glow around them', () => {
  it('holds the windows the bake computes (scripts/bake_starless_panorama.py planet_windows)', () => {
    expect(PLANET_WINDOWS).toEqual([
      { name: 'Venus', xPx: 1851.19, yPx: 2472.21, radiusPx: 12 },
      { name: 'Jupiter', xPx: 2776.02, yPx: 1775.73, radiusPx: 10 },
    ]);
  });

  it('finds a texture coordinate inside a window and none just outside it (u right, v = 1 at the top row)', () => {
    for (const w of PLANET_WINDOWS) {
      const u = w.xPx / GLOW_SIZE_PX.width;
      const v = 1 - w.yPx / GLOW_SIZE_PX.height;
      expect(planetWindowAt(u, v)?.name).toBe(w.name);
      expect(planetWindowAt(u + (w.radiusPx - 1) / GLOW_SIZE_PX.width, v)?.name).toBe(w.name);
      expect(planetWindowAt(u + (w.radiusPx + 1) / GLOW_SIZE_PX.width, v)).toBeNull();
      expect(planetWindowAt(u, v - (w.radiusPx + 1) / GLOW_SIZE_PX.height)).toBeNull();
    }
    expect(planetWindowAt(0.5, 0.5)).toBeNull();
  });

  it('writes the same windows into the glow shader, filled from a ring outside them', () => {
    for (const w of PLANET_WINDOWS) {
      expect(FLIGHT_GLOW_FRAGMENT).toContain(`vec3(${(w.xPx / GLOW_SIZE_PX.width).toFixed(6)}, ${(1 - w.yPx / GLOW_SIZE_PX.height).toFixed(6)}, ${w.radiusPx.toFixed(1)})`);
    }
    expect(FLIGHT_GLOW_FRAGMENT).toMatch(/ringPx\s*=\s*2\.0 \* radiusPx/);
    expect(FLIGHT_DOME_FRAGMENT).toContain('fillPlanets(');
  });
});

describe('the dome while steering: each pixel looks along the flight camera’s own ray', () => {
  it('inverts the flight’s forward projection, lens included, to within a micro-radian', () => {
    let worst = 0;
    for (const [dx, dy] of [[0, 0], [0.2, 0.1], [-0.25, 0.3], [0.3, -0.35], [-0.32, -0.28], [0.05, 0.4]] as const) {
      const v = norm([z[0] + dx * x[0] + dy * y[0], z[1] + dx * x[1] + dy * y[1], z[2] + dx * x[2] + dy * y[2]]);
      const [sx, sy] = project(v);
      const d = domeDirectionAt(camera, sx, sy);
      worst = Math.max(worst, Math.acos(Math.min(1, dot(d as V3, v))));
    }
    expect(worst).toBeLessThan(1e-6);
  });

  it('turns with the steering camera: new rows, new direction for the same pixel', () => {
    const turned: DomeCamera = { ...camera, rows: { x: z, y, z: [-x[0], -x[1], -x[2]] } };
    const a = domeDirectionAt(camera, 720, 450);
    const b = domeDirectionAt(turned, 720, 450);
    expect(Math.acos(Math.min(1, dot(a as V3, b as V3)))).toBeGreaterThan(1.5);
  });

  it('samples the glow where the registration puts that direction, as Real sky does', () => {
    for (const v of [unit(210.956, -60.373), unit(266.405, -28.936), unit(83.8, -5.4), unit(10, 60)]) {
      const [u0, v0] = panoramaUvOfIcrs(v, registration);
      const [u1, v1] = glowUvOfIcrs(v, registration);
      expect(Math.abs(u1 - u0)).toBeLessThan(1e-9);
      expect(Math.abs(v1 - v0)).toBeLessThan(1e-9);
    }
  });

  it('runs the same three lens iterations in the shader as on the CPU', () => {
    expect(FLIGHT_DOME_FRAGMENT).toMatch(/for \(int i = 0; i < 3; i\+\+\)/);
    expect(FLIGHT_DOME_FRAGMENT).toContain('uniform mat3 uIcrsToPanorama;');
  });
});

describe('the credits say what the flight does to ESO’s photograph (CC BY 4.0 asks that changes be indicated)', () => {
  it('names the starless copy, its level, the planets and the dome, in plain words', async () => {
    const { milkyWayInFlightChanges } = await import('../flightMilkyWay.js');
    const line = milkyWayInFlightChanges();
    expect(line).toBe(
      'In the flight, the copy made from it with its own stars removed is drawn in its place at its own level, ungraded, with Venus and Jupiter filled from the copy around them; while you steer it is drawn on a sky turned with the camera.'
    );
    expect(line).not.toMatch(/[\u2013\u2014]/);
  });
});

describe('the Milky Way line stands whenever the Milky Way is on screen in flight', () => {
  it('shows at a star once the sky has drawn, and while steering once the dome has drawn', async () => {
    const { flightMilkyWayLineShown } = await import('../flightMilkyWay.js');
    expect(flightMilkyWayLineShown({ steering: false, glowDrawn: true, domeDrawn: false })).toBe(true);
    expect(flightMilkyWayLineShown({ steering: false, glowDrawn: false, domeDrawn: true })).toBe(false);
    expect(flightMilkyWayLineShown({ steering: true, glowDrawn: true, domeDrawn: false })).toBe(false);
    expect(flightMilkyWayLineShown({ steering: true, glowDrawn: false, domeDrawn: true })).toBe(true);
  });
});
