import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { RenderLayerSetSchema } from '@albedian/contracts';
import fc from 'fast-check';
import * as THREE from 'three';
import { describe, expect, it } from 'vitest';
import { julianDateUtc } from '../bodyOrientation.js';
import { icrsToScene } from '../catalogStars/CatalogStarField.js';
import { raDecDegToVec } from '../catalogStars/healpix.js';
import {
  ObserverSky,
  altAzDeg,
  defaultObserverLook,
  localFrameQuaternionFor,
  observerAxes,
  observerClockFor,
  observerFrameQuaternion,
  observerHorizonVisible,
  type ObserverAxes,
} from '../observerPose.js';
import { COMPASS_POINTS, HORIZON_RADIUS, createObserverHorizon, localDirection } from '../webgl/observerHorizon.js';
import { webglRenderLayersFor } from '../webglRenderLayers.js';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

const DEG = Math.PI / 180;
const scene = (raDeg: number, decDeg: number) => icrsToScene(raDecDegToVec(raDeg, decDeg));
const toIcrs = (v: THREE.Vector3): [number, number, number] => [-v.z, -v.x, v.y];
const azDiff = (a: number, b: number) => Math.abs(((a - b + 540) % 360) - 180);

/** The direction a camera faces with yaw and pitch (Euler YXZ) under a parent turned by `frame`. */
function facing(frame: THREE.Quaternion, yaw: number, pitch: number): THREE.Vector3 {
  const parent = new THREE.Group();
  parent.quaternion.copy(frame);
  const camera = new THREE.PerspectiveCamera();
  camera.rotation.order = 'YXZ';
  camera.rotation.set(pitch, yaw, 0);
  parent.add(camera);
  parent.updateMatrixWorld(true);
  return camera.getWorldDirection(new THREE.Vector3());
}

/**
 * Siding Spring Observatory (Anglo-Australian Telescope, lat -31.2770, lon 149.0661) on
 * 2027-02-06 17:00 UTC, a moonless night (Sun -29.1 deg, Moon -29.4 deg). The altitudes and
 * azimuths come from .lanetools/render8/altaz_independent.py, a separate Python script written from
 * Meeus, Astronomical Algorithms (GMST eq. 12.4, horizontal coordinates eq. 13.5 and 13.6, IAU 1976
 * precession eq. 21.3 and 21.4) and checked against the book's examples 12.a, 12.b, 13.b and 21.b.
 * Positions are ICRS J2000: Hipparcos for Acrux and Gacrux, SIMBAD's optical centres for the
 * Clouds, and galactic longitude 0, latitude 0 for the galactic centre.
 */
const SITE = { latDeg: -31.277, lonDeg: 149.0661 };
const JD = julianDateUtc('2027-02-06T17:00:00Z');
const EXPECTED = [
  { name: 'galactic centre', raDeg: 266.405, decDeg: -28.9362, altDeg: 17.534, azDeg: 113.699 },
  { name: 'Acrux', raDeg: 186.6496, decDeg: -63.0991, altDeg: 57.773, azDeg: 174.633 },
  { name: 'Gacrux', raDeg: 187.7915, decDeg: -57.1132, altDeg: 63.502, azDeg: 170.895 },
  { name: 'Large Magellanic Cloud', raDeg: 80.894, decDeg: -69.756, altDeg: 25.879, azDeg: 202.294 },
  { name: 'Small Magellanic Cloud', raDeg: 13.187, decDeg: -72.829, altDeg: 14.33, azDeg: 183.889 },
];
// The script prints three decimals; the formulas agree with Meeus to under 0.001 deg
const TOLERANCE_DEG = 0.01;

const site = fc.record({
  latDeg: fc.double({ min: -89.9, max: 89.9, noNaN: true }),
  lonDeg: fc.double({ min: -180, max: 180, noNaN: true }),
});
// 1950 to 2100
const jd = fc.double({ min: 2433282.5, max: 2488069.5, noNaN: true });

describe('observer pose: where the sky stands for a place and time', () => {
  it('puts the southern sky where an independent calculation does, at Siding Spring', () => {
    const axes = observerAxes(SITE, JD);
    for (const e of EXPECTED) {
      const { altDeg, azDeg } = altAzDeg(axes, raDecDegToVec(e.raDeg, e.decDeg));
      expect(Math.abs(altDeg - e.altDeg), `${e.name} altitude`).toBeLessThan(TOLERANCE_DEG);
      expect(azDiff(azDeg, e.azDeg), `${e.name} azimuth`).toBeLessThan(TOLERANCE_DEG);
    }
  });

  it('turns the camera so yaw is minus azimuth and pitch is altitude', () => {
    const frame = observerFrameQuaternion(observerAxes(SITE, JD));
    for (const e of EXPECTED) {
      const d = facing(frame, -e.azDeg * DEG, e.altDeg * DEG);
      expect(d.angleTo(scene(e.raDeg, e.decDeg)) / DEG, e.name).toBeLessThan(TOLERANCE_DEG);
    }
  });

  it('keeps zenith, north and east a right-handed orthonormal set, with the zenith at 90 deg', () => {
    fc.assert(
      fc.property(site, jd, (s, j) => {
        const { zenith, north, east } = observerAxes(s, j);
        const [z, n, e] = [zenith, north, east].map((v) => new THREE.Vector3(...v));
        for (const v of [z!, n!, e!]) expect(Math.abs(v.length() - 1)).toBeLessThan(1e-12);
        expect(Math.abs(z!.dot(n!)) + Math.abs(z!.dot(e!)) + Math.abs(n!.dot(e!))).toBeLessThan(1e-12);
        expect(e!.clone().cross(n!).distanceTo(z!)).toBeLessThan(1e-12);
        expect(altAzDeg(observerAxes(s, j), zenith).altDeg).toBeGreaterThan(90 - 1e-6);
      }),
      { numRuns: 300, seed: SEED, examples: [[SITE, JD], [{ latDeg: 0, lonDeg: 0 }, 2451545.0]] }
    );
  });

  it("stands the Earth's south pole of date at altitude minus latitude due south, the north pole at latitude due north", () => {
    fc.assert(
      fc.property(site, jd, (s, j) => {
        const axes = observerAxes(s, j);
        // The pole of date: the Earth's axis after precession, so north at azimuth 0 holds to rounding
        const pole = axes.poleOfDate;
        const south = altAzDeg(axes, [-pole[0], -pole[1], -pole[2]]);
        const northPole = altAzDeg(axes, pole);
        expect(Math.abs(south.altDeg + s.latDeg)).toBeLessThan(1e-9);
        expect(Math.abs(northPole.altDeg - s.latDeg)).toBeLessThan(1e-9);
        expect(azDiff(south.azDeg, 180)).toBeLessThan(1e-6);
        expect(azDiff(northPole.azDeg, 0)).toBeLessThan(1e-6);
      }),
      { numRuns: 300, seed: SEED, examples: [[SITE, JD]] }
    );
  });

  it('turns the sky once per sidereal day, westward', () => {
    const axes0 = observerAxes(SITE, JD);
    const sidereal = 0.99726957;
    const later = altAzDeg(observerAxes(SITE, JD + sidereal), raDecDegToVec(266.405, -28.9362));
    const now = altAzDeg(axes0, raDecDegToVec(266.405, -28.9362));
    // A sidereal day later the sky stands where it stood (precession over a day: under 0.0001 deg)
    expect(Math.abs(later.altDeg - now.altDeg)).toBeLessThan(0.001);
    // An hour later the galactic centre, east of the meridian, has climbed
    expect(altAzDeg(observerAxes(SITE, JD + 1 / 24), raDecDegToVec(266.405, -28.9362)).altDeg).toBeGreaterThan(now.altDeg + 5);
  });

  it('opens a newly placed sky on the meridian, half way up, so it never opens on the ground', () => {
    fc.assert(
      fc.property(site, jd, (s, j) => {
        const look = defaultObserverLook(s.latDeg);
        // Toward the celestial equator: due south north of the equator, due north south of it
        expect(look).toMatchObject({ azDeg: s.latDeg >= 0 ? 180 : 0, altDeg: 45 });
        const axes = observerAxes(s, j);
        const d = facing(observerFrameQuaternion(axes), look.yaw, look.pitch);
        const { altDeg, azDeg } = altAzDeg(axes, toIcrs(d));
        expect(Math.abs(altDeg - 45)).toBeLessThan(1e-9);
        expect(azDiff(azDeg, look.azDeg)).toBeLessThan(1e-9);
      }),
      { numRuns: 300, seed: SEED, examples: [[SITE, JD], [{ latDeg: 0, lonDeg: 0 }, 2451545.0]] }
    );
  });

  it('shows the horizon only in the placed sky view, and not in flight', () => {
    expect(observerHorizonVisible({ regime: 'earth_capture', flying: false, placed: true })).toBe(true);
    expect(observerHorizonVisible({ regime: 'earth_capture', flying: false, placed: false })).toBe(false);
    expect(observerHorizonVisible({ regime: 'earth_capture', flying: true, placed: true })).toBe(false);
    for (const regime of ['cislunar', 'solar_neighborhood', 'deep_sky', 'planetary_surface'] as const) {
      expect(observerHorizonVisible({ regime, flying: false, placed: true }), regime).toBe(false);
    }
  });

  it('turns the camera parent into the observer frame only in the located sky view', () => {
    const frame = observerFrameQuaternion(observerAxes(SITE, JD));
    expect(localFrameQuaternionFor('earth_capture', frame).angleTo(frame)).toBeLessThan(1e-12);
    expect(localFrameQuaternionFor('earth_capture', null).angleTo(new THREE.Quaternion())).toBeLessThan(1e-12);
    for (const regime of ['cislunar', 'solar_neighborhood', 'deep_sky', 'planetary_surface'] as const) {
      expect(localFrameQuaternionFor(regime, frame).angleTo(new THREE.Quaternion()), regime).toBeLessThan(1e-12);
    }
  });

  it('takes a place and time once, refreshes on the device clock each second, and holds a capture time', () => {
    const sky = new ObserverSky();
    expect(sky.refresh(0)).toBeNull();
    for (const [lat, lon] of [[91, 0], [Number.NaN, 0], [0, Number.POSITIVE_INFINITY]] as const) {
      expect(sky.set(lat, lon, null)).toBe(false);
    }
    expect(sky.due(1e12)).toBe(false);
    expect(sky.set(SITE.latDeg, SITE.lonDeg, null)).toBe(true);
    expect(sky.set(SITE.latDeg + 0.001, SITE.lonDeg, null)).toBe(false);
    const t0 = Date.parse('2027-02-06T17:00:00Z');
    const now = sky.refresh(t0)!.clone();
    expect(now.angleTo(observerFrameQuaternion(observerAxes(SITE, JD)))).toBeLessThan(1e-9);
    expect(sky.due(t0 + 999)).toBe(false);
    expect(sky.due(t0 + 1000)).toBe(true);
    // An hour on the device clock turns the frame by 15 deg of sidereal time
    expect(sky.refresh(t0 + 3_600_000)!.angleTo(now) / DEG).toBeGreaterThan(10);
    // A capture time holds the frame whatever the clock says, and never falls due
    expect(sky.set(SITE.latDeg, SITE.lonDeg, '2027-02-06T17:00:00Z')).toBe(true);
    expect(sky.set(SITE.latDeg, SITE.lonDeg, '2027-02-06T17:00:00Z')).toBe(false);
    expect(sky.refresh(0)!.angleTo(now)).toBeLessThan(1e-9);
    expect(sky.due(1e15)).toBe(false);
    expect(sky.clock.kind).toBe('capture');
  });

  it('forgets the place and time when cleared, so a recording without a location keeps none', () => {
    const sky = new ObserverSky();
    expect(sky.clear()).toBe(false);
    sky.set(SITE.latDeg, SITE.lonDeg, '2027-02-06T17:00:00Z');
    sky.refresh(0);
    expect(sky.clear()).toBe(true);
    expect(sky.site).toBeNull();
    expect(sky.frame).toBeNull();
    expect(sky.clock).toEqual({ kind: 'now' });
    expect(sky.due(1e15)).toBe(false);
    expect(sky.refresh(0)).toBeNull();
    expect(sky.clear()).toBe(false);
    // The same place comes back as new, not as unchanged
    expect(sky.set(SITE.latDeg, SITE.lonDeg, null)).toBe(true);
  });

  it('uses the capture time when it is a time, else the device clock now', () => {
    expect(observerClockFor('2027-02-06T17:00:00Z')).toEqual({ kind: 'capture', jdUtc: JD });
    for (const bad of [null, undefined, '', 'yesterday']) expect(observerClockFor(bad)).toEqual({ kind: 'now' });
  });
});

describe('observer horizon: the line, the ground and the compass points', () => {
  const axes: ObserverAxes = observerAxes(SITE, JD);
  const frame = observerFrameQuaternion(axes);
  const sceneToAltAz = (local: THREE.Vector3) => altAzDeg(axes, toIcrs(local.clone().applyQuaternion(frame)));

  it('draws the horizon line at altitude 0 all the way round', () => {
    // The vertices are stored as 32-bit floats: 40 units to about 4e-6, about 1e-5 deg
    const FLOAT32_DEG = 1e-4;
    const horizon = createObserverHorizon();
    const line = horizon.getObjectByName('observer-horizon-line') as THREE.LineLoop;
    expect(line).toBeDefined();
    const pos = line.geometry.getAttribute('position');
    expect(pos.count).toBeGreaterThanOrEqual(360);
    const azimuths: number[] = [];
    for (let i = 0; i < pos.count; i++) {
      const v = new THREE.Vector3().fromBufferAttribute(pos, i);
      expect(Math.abs(v.length() - HORIZON_RADIUS)).toBeLessThan(1e-4);
      const { altDeg, azDeg } = sceneToAltAz(v);
      expect(Math.abs(altDeg)).toBeLessThan(FLOAT32_DEG);
      azimuths.push(azDeg);
    }
    azimuths.sort((a, b) => a - b);
    const gaps = azimuths.map((a, i) => (i === 0 ? a + 360 - azimuths[azimuths.length - 1]! : a - azimuths[i - 1]!));
    expect(Math.max(...gaps)).toBeLessThan(1.01);
  });

  it('dims only what lies below the horizon', () => {
    const ground = createObserverHorizon().getObjectByName('observer-horizon-ground') as THREE.Mesh;
    expect(ground).toBeDefined();
    const pos = ground.geometry.getAttribute('position');
    for (let i = 0; i < pos.count; i++) expect(pos.getY(i)).toBeLessThanOrEqual(1e-9);
    const mat = ground.material as THREE.MeshBasicMaterial;
    expect(mat.transparent).toBe(true);
    expect(mat.opacity).toBeGreaterThan(0);
    expect(mat.opacity).toBeLessThan(1);
  });

  it('puts N, E, S and W at azimuths 0, 90, 180 and 270 on the horizon', () => {
    expect(COMPASS_POINTS).toEqual([
      { label: 'N', azDeg: 0 },
      { label: 'E', azDeg: 90 },
      { label: 'S', azDeg: 180 },
      { label: 'W', azDeg: 270 },
    ]);
    for (const p of COMPASS_POINTS) {
      const { altDeg, azDeg } = sceneToAltAz(localDirection(p.azDeg, 0));
      expect(Math.abs(altDeg), p.label).toBeLessThan(1e-9);
      expect(azDiff(azDeg, p.azDeg), p.label).toBeLessThan(1e-9);
    }
    // Each letter stands just above its point; without a canvas none is drawn
    const drawn: string[] = [];
    const horizon = createObserverHorizon((label) => (drawn.push(label), new THREE.Texture()));
    expect(drawn).toEqual(['N', 'E', 'S', 'W']);
    for (const p of COMPASS_POINTS) {
      const sprite = horizon.getObjectByName(`observer-compass-${p.label}`)!;
      const { altDeg, azDeg } = sceneToAltAz(sprite.position);
      expect(altDeg, p.label).toBeGreaterThan(0);
      expect(altDeg, p.label).toBeLessThan(3);
      expect(azDiff(azDeg, p.azDeg), p.label).toBeLessThan(1e-9);
    }
    expect(createObserverHorizon().getObjectByName('observer-compass-N')).toBeUndefined();
  });
});

describe('the sky view discloses its layers, the horizon as predicted only when placed', () => {
  const HORIZON_SOURCES = [
    'IAU 1976 precession (Lieske et al. 1977)',
    'IAU 1982 sidereal time (Aoki et al. 1982)',
    "Location and capture time from the recording's metadata, else this device's clock",
  ];

  it('declares the panorama, the catalog stars, the horizon and the optics once the sky view is placed', () => {
    for (const observerTime of ['now', 'capture'] as const) {
      const layers = webglRenderLayersFor({ regime: 'earth_capture', blackHole: false, observerTime });
      expect(RenderLayerSetSchema.safeParse(layers).success).toBe(true);
      expect(layers!.map((l) => l.kind)).toEqual(['sky_background', 'catalog_stars', 'sky_overlay', 'camera_optics']);
      const horizon = layers!.find((l) => l.kind === 'sky_overlay')!;
      expect(horizon.evidenceClass).toBe('predicted');
      // Fixed text: no place, no time and no figure of the recording can reach it
      expect(horizon.sources).toEqual(HORIZON_SOURCES);
      expect(`${horizon.title} ${horizon.disclosure}`).not.toMatch(/[0-9]/);
      expect(horizon.disclosure).toMatch(/geometric/);
      expect(horizon.disclosure).toMatch(/slightly higher/);
    }
  });

  it('declares no horizon without a place, so the panel reads as a modelled scene', () => {
    for (const observerTime of [null, undefined]) {
      const layers = webglRenderLayersFor({ regime: 'earth_capture', blackHole: false, observerTime })!;
      expect(RenderLayerSetSchema.safeParse(layers).success).toBe(true);
      expect(layers.map((l) => l.kind)).toEqual(['sky_background', 'catalog_stars', 'camera_optics']);
      expect(layers.every((l) => l.evidenceClass === 'simulated')).toBe(true);
    }
  });

  it("says whether the horizon is for the capture time or the device's time now", () => {
    const text = (observerTime: 'now' | 'capture') =>
      webglRenderLayersFor({ regime: 'earth_capture', blackHole: false, observerTime })!.find((l) => l.kind === 'sky_overlay')!
        .disclosure;
    expect(text('now')).toMatch(/\bnow\b/);
    expect(text('now')).not.toMatch(/capture time/);
    expect(text('capture')).toMatch(/capture time/);
    expect(text('capture')).not.toMatch(/\bnow\b/);
  });
});

describe('the WebGL renderer wears the observer frame', () => {
  const src = fs.readFileSync(fileURLToPath(new URL('../SparkPortalRenderer.ts', import.meta.url).href), 'utf8');

  it('sets the camera parent from the observer frame, not to identity, whenever it enters a regime', () => {
    expect(src).not.toMatch(/localFrame\.rotation\.set\(0, 0, 0\)/);
    const from = src.indexOf('public jumpToRegime(');
    const jump = src.slice(from, src.indexOf('\n  }\n', from));
    expect(jump).toContain('localFrameQuaternionFor(regime, this.observer.frame, this.localFrame.quaternion);');
    expect(jump).not.toMatch(/quaternion\.identity\(\)/);
  });

  it('drops the dead southern flips, horizon lookups and target focus', () => {
    expect(src).not.toMatch(/skyMoonMesh|captureHorizonLandscapeDome|captureHorizonSplatMesh|rebuildCaptureHorizonSplatMesh/);
    expect(src).not.toMatch(/focusSkyTarget/);
  });

  it('opens a new place, and every return to the sky view, on the default look', () => {
    expect(src).toMatch(/if \(newPlace\) this\.lookAtDefault\(\);/);
    expect(src).toMatch(/if \(regime === 'earth_capture'\) this\.lookAtDefault\(\);/);
  });

  it('decides horizon visibility and the layers only from whether the observer is placed', () => {
    expect(src).toContain('observerHorizonVisible(');
    expect(src).toMatch(/observerTime: this\.observer\.frame \? this\.observer\.clock\.kind : null/);
  });
});

