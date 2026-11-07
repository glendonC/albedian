import { Quaternion, Vector3 } from 'three';
import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import { ICRS_TO_SCENE_QUATERNION } from '../../catalogStars/CatalogStarField.js';
import { ICRS_TO_GALACTIC } from '../../sky/skyRegistration.js';
import moment from '../cislunar/landingMoment.json';
import {
  EARTH_MOON_SEPARATION_DEG,
  KM_PER_UNIT,
  LANDING_CAMERA_SPEED_KM_PER_SEC,
  LANDING_CAMERA_TRAVEL_KM,
  LANDING_CAMERA_TRAVEL_SECONDS,
  MOON_RADIUS_KM,
  landingCameraPositionAt,
  landingPose,
} from '../cislunar/landingScene.js';

/**
 * The landing's geometry, checked without a GPU: where the camera stands, what it sees, and that
 * each mesh, turned as the renderer turns it, faces where JPL Horizons says the body faced at the
 * moment (landingMoment.json). The rotation models agree with Horizons within 0.01 deg
 * (bodyOrientation.test.ts); these tests check that the mesh frames and the ICRS-to-scene turn
 * keep that agreement.
 */
const pose = landingPose();
const DEG = Math.PI / 180;
const TOLERANCE_DEG = 0.01;
const lonDiff = (a: number, b: number) => ((((a - b) % 360) + 540) % 360) - 180;
const toScene = (v: readonly number[]) => new Vector3(v[0]!, v[1]!, v[2]!).applyQuaternion(ICRS_TO_SCENE_QUATERNION);
/** Longitude and planetocentric latitude of a scene direction on a mesh turned by `q`. */
function subPoint(q: Quaternion, direction: Vector3) {
  const m = direction.clone().normalize().applyQuaternion(q.clone().invert());
  // Mesh frame to body frame: x stays, y (90 E) is -z, z (north) is +y
  const [x, y, z] = [m.x, -m.z, m.y];
  return { lon: ((Math.atan2(y, x) / DEG) % 360 + 360) % 360, lat: Math.atan2(z, Math.hypot(x, y)) / DEG };
}
const geocentric = (geodeticDeg: number, [a, , c]: number[]) =>
  Math.atan(((c! * c!) / (a! * a!)) * Math.tan(geodeticDeg * DEG)) / DEG;

describe('landing geometry at 2025-03-07 11:00 UTC', () => {
  it('stands the camera beyond the far side of the Moon, on the line from the galactic centre through the Earth', () => {
    const towardEarth = pose.earth.position.clone().sub(pose.camera.position);
    expect(towardEarth.angleTo(toScene(ICRS_TO_GALACTIC.slice(0, 3)))).toBeLessThan(1e-9);
    const fromMoon = pose.camera.position.clone().sub(pose.moon.position);
    expect(fromMoon.dot(pose.earth.position.clone().sub(pose.moon.position))).toBeLessThan(0);
    expect(pose.camera.aboveFarSideKm).toBeGreaterThan(10_000);
    expect(pose.camera.aboveFarSideKm).toBeLessThan(25_000);
  });

  it('shows the Moon at the composed separation from the Earth, at its true size', () => {
    const towardEarth = pose.earth.position.clone().sub(pose.camera.position);
    const towardMoon = pose.moon.position.clone().sub(pose.camera.position);
    expect(Math.abs(towardEarth.angleTo(towardMoon) / DEG - EARTH_MOON_SEPARATION_DEG)).toBeLessThan(1e-6);
    expect(pose.moon.position.length() * KM_PER_UNIT).toBeCloseTo(Math.hypot(...moment.moonKm), 3);
    const apparentDiameterDeg = (2 * Math.asin(MOON_RADIUS_KM / (towardMoon.length() * KM_PER_UNIT))) / DEG;
    expect(apparentDiameterDeg).toBeGreaterThan(10);
    expect(apparentDiameterDeg).toBeLessThan(14);
  });

  it('keeps galactic north up', () => {
    const forward = new Vector3(0, 0, -1).applyQuaternion(pose.camera.quaternion);
    const up = new Vector3(0, 1, 0).applyQuaternion(pose.camera.quaternion);
    const north = toScene(ICRS_TO_GALACTIC.slice(6, 9));
    const northInView = north.clone().sub(forward.clone().multiplyScalar(north.dot(forward))).normalize();
    expect(up.dot(northInView)).toBeGreaterThan(1 - 1e-12);
  });

  it("turns the Moon's mesh to face the Earth and the Sun where Horizons says", () => {
    const seen = moment.checks.moonSeenFromEarthCentre;
    const subEarth = subPoint(pose.moon.quaternion, pose.earth.position.clone().sub(pose.moon.position));
    const subSun = subPoint(pose.moon.quaternion, pose.moon.sunDirection);
    expect(Math.abs(lonDiff(subEarth.lon, seen.subObserverLonDeg))).toBeLessThan(TOLERANCE_DEG);
    expect(Math.abs(subEarth.lat - seen.subObserverLatDeg)).toBeLessThan(TOLERANCE_DEG);
    expect(Math.abs(lonDiff(subSun.lon, seen.subSolarLonDeg))).toBeLessThan(TOLERANCE_DEG);
    expect(Math.abs(subSun.lat - seen.subSolarLatDeg)).toBeLessThan(TOLERANCE_DEG);
  });

  it("turns the Earth's mesh to face the Moon and the Sun where Horizons says", () => {
    const seen = moment.checks.earthSeenFromMoonCentre;
    const subMoon = subPoint(pose.earth.quaternion, pose.moon.position.clone().sub(pose.earth.position));
    const subSun = subPoint(pose.earth.quaternion, pose.earth.sunDirection);
    expect(Math.abs(lonDiff(subMoon.lon, seen.subObserverLonDeg))).toBeLessThan(TOLERANCE_DEG);
    expect(Math.abs(subMoon.lat - geocentric(seen.subObserverLatDeg, seen.radiiKm))).toBeLessThan(TOLERANCE_DEG);
    expect(Math.abs(lonDiff(subSun.lon, seen.subSolarLonDeg))).toBeLessThan(TOLERANCE_DEG);
    expect(Math.abs(subSun.lat - geocentric(seen.subSolarLatDeg, seen.radiiKm))).toBeLessThan(TOLERANCE_DEG);
  });

  it('moves the camera 1,000 km over 30 s (33.3 km/s) past the far side with true differential parallax', () => {
    expect(LANDING_CAMERA_TRAVEL_KM).toBe(1000);
    expect(LANDING_CAMERA_TRAVEL_SECONDS).toBe(30);
    expect(LANDING_CAMERA_SPEED_KM_PER_SEC).toBeCloseTo(1000 / 30, 9);

    const start = landingCameraPositionAt(0, pose);
    expect(start.distanceTo(pose.camera.position)).toBeLessThan(1e-12);

    const lateral = new Vector3(-1, 0, 0).applyQuaternion(pose.camera.quaternion);
    const forward = new Vector3(0, 0, -1).applyQuaternion(pose.camera.quaternion);
    const mid = landingCameraPositionAt(15, pose);
    const end = landingCameraPositionAt(LANDING_CAMERA_TRAVEL_SECONDS, pose);

    expect(mid.clone().sub(start).dot(lateral) * KM_PER_UNIT).toBeCloseTo(500, 9);
    expect(end.clone().sub(start).dot(lateral) * KM_PER_UNIT).toBeCloseTo(LANDING_CAMERA_TRAVEL_KM, 9);
    expect(Math.abs(end.clone().sub(start).dot(forward))).toBeLessThan(1e-12);
    expect(end.distanceTo(start) * KM_PER_UNIT).toBeCloseTo(LANDING_CAMERA_TRAVEL_KM, 9);

    // Real parallax: the Moon (17,209 km away) shifts by ~3.33 deg while the Earth (395,273 km away) shifts by ~0.145 deg
    const moonDirStart = pose.moon.position.clone().sub(start).normalize();
    const moonDirEnd = pose.moon.position.clone().sub(end).normalize();
    const earthDirStart = pose.earth.position.clone().sub(start).normalize();
    const earthDirEnd = pose.earth.position.clone().sub(end).normalize();
    const moonParallaxDeg = moonDirStart.angleTo(moonDirEnd) / DEG;
    const earthParallaxDeg = earthDirStart.angleTo(earthDirEnd) / DEG;

    expect(moonParallaxDeg).toBeGreaterThan(3.2);
    expect(moonParallaxDeg).toBeLessThan(3.5);
    expect(earthParallaxDeg).toBeGreaterThan(0.13);
    expect(earthParallaxDeg).toBeLessThan(0.16);
    expect(moonParallaxDeg / earthParallaxDeg).toBeGreaterThan(20);
  });

  it('PBT-LANDING-PARALLAX: keeps the camera on its 1,000 km lateral track and both bodies separated in frame', () => {
    fc.assert(
      fc.property(fc.double({ min: 0, max: 600, noNaN: true }), (t) => {
        const pos = landingCameraPositionAt(t, pose);
        const displacementKm = pos.distanceTo(pose.camera.position) * KM_PER_UNIT;
        expect(displacementKm).toBeGreaterThanOrEqual(0);
        expect(displacementKm).toBeLessThanOrEqual(LANDING_CAMERA_TRAVEL_KM + 1e-6);
        const sepDeg =
          pose.earth.position.clone().sub(pos).angleTo(pose.moon.position.clone().sub(pos)) / DEG;
        expect(sepDeg).toBeGreaterThanOrEqual(EARTH_MOON_SEPARATION_DEG - 1e-6);
        expect(sepDeg).toBeLessThan(12);
      }),
      { numRuns: 100 }
    );
  });
});
