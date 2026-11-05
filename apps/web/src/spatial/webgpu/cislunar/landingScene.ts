import { Matrix4, Quaternion, Vector3 } from 'three';
import { earthIcrfToBody, julianDateUtc, moonIcrfToBody, type Mat3 } from '../../bodyOrientation.js';
import { ICRS_TO_SCENE_QUATERNION } from '../../catalogStars/CatalogStarField.js';
import { ICRS_TO_GALACTIC } from '../../sky/skyRegistration.js';
import moment from './landingMoment.json';

/**
 * The landing (scene 01 on the WebGPU host) at one real moment, 2025-03-07 11:00 UTC, at true
 * scale: the Earth and the Moon where JPL Horizons puts them (landingMoment.json, made by
 * scripts/plan_landing_moment.py), turned by their IAU rotation models (bodyOrientation.ts) and
 * lit by that moment's Sun, seen from a simulated camera beyond the Moon.
 *
 * The camera stands on the line from the galactic centre through the Earth, so the Earth sits
 * exactly in front of the centre of the Milky Way; its distance is the one composition choice,
 * set so the Moon appears EARTH_MOON_SEPARATION_DEG from the Earth. Galactic north is up.
 */

/** Scene units: one unit is 1000 km. */
export const KM_PER_UNIT = 1000;
/** Mean radii: the Earth's (IUGG) and the Moon's (IAU). */
export const EARTH_RADIUS_KM = 6371.0;
export const MOON_RADIUS_KM = 1737.4;
/** Composition: the Moon's centre this far from the Earth's, as the camera sees them. */
export const EARTH_MOON_SEPARATION_DEG = 8.5;

export interface BodyPose {
  /** Scene position of the body's centre */
  position: Vector3;
  /** Orientation of its SphereGeometry mesh (+y north, +x longitude 0, -z longitude 90 E) */
  quaternion: Quaternion;
  /** Unit scene direction from the body to the Sun */
  sunDirection: Vector3;
}

export interface LandingPose {
  utc: string;
  earth: BodyPose;
  moon: BodyPose;
  camera: { position: Vector3; quaternion: Quaternion; distanceFromEarthKm: number; aboveFarSideKm: number };
}

const DEG = Math.PI / 180;

function matrixFromRows(m: Mat3): Matrix4 {
  return new Matrix4().set(m[0], m[1], m[2], 0, m[3], m[4], m[5], 0, m[6], m[7], m[8], 0, 0, 0, 0, 1);
}

/** Body frame (x longitude 0, y longitude 90 E, z north) to SphereGeometry's mesh frame. */
const MESH_FROM_BODY = matrixFromRows([1, 0, 0, 0, 0, 1, 0, -1, 0]);
const SCENE_FROM_ICRS = new Matrix4().makeRotationFromQuaternion(ICRS_TO_SCENE_QUATERNION);

const toScene = (icrf: readonly number[], scale = 1) =>
  new Vector3(icrf[0]! * scale, icrf[1]! * scale, icrf[2]! * scale).applyQuaternion(ICRS_TO_SCENE_QUATERNION);

/** Mesh orientation for a body whose ICRF-to-body rotation is given. */
export function meshQuaternion(icrfToBody: Mat3): Quaternion {
  const icrfFromBody = matrixFromRows(icrfToBody).transpose();
  const bodyFromMesh = MESH_FROM_BODY.clone().transpose();
  return new Quaternion().setFromRotationMatrix(SCENE_FROM_ICRS.clone().multiply(icrfFromBody).multiply(bodyFromMesh));
}

function angleDeg(a: Vector3, b: Vector3): number {
  return a.angleTo(b) / DEG;
}

export function landingPose(): LandingPose {
  const jdUtc = julianDateUtc(moment.utc);
  const jdTdb = jdUtc + moment.tdbMinusUtcSeconds / 86400;
  const moonKm = moment.moonKm as [number, number, number];
  const sunKm = moment.sunKm as [number, number, number];

  const galacticCentre = toScene(ICRS_TO_GALACTIC.slice(0, 3));
  const galacticNorth = toScene(ICRS_TO_GALACTIC.slice(6, 9));
  const earthPos = new Vector3(0, 0, 0);
  const moonPos = toScene(moonKm, 1 / KM_PER_UNIT);

  // Camera on the line from the galactic centre through the Earth, as far out as puts the Moon
  // EARTH_MOON_SEPARATION_DEG from the Earth; the separation shrinks as the camera backs away
  const cameraAt = (distanceKm: number) => galacticCentre.clone().multiplyScalar(-distanceKm / KM_PER_UNIT);
  const separation = (distanceKm: number) => {
    const c = cameraAt(distanceKm);
    return angleDeg(earthPos.clone().sub(c), moonPos.clone().sub(c));
  };
  const moonDistanceKm = moonPos.length() * KM_PER_UNIT;
  let near = moonDistanceKm + MOON_RADIUS_KM * 1.5;
  let far = moonDistanceKm * 2;
  for (let i = 0; i < 80; i++) {
    const mid = (near + far) / 2;
    if (separation(mid) > EARTH_MOON_SEPARATION_DEG) near = mid;
    else far = mid;
  }
  const distanceFromEarthKm = (near + far) / 2;
  const cameraPos = cameraAt(distanceFromEarthKm);

  // Look between the two bodies, with galactic north up
  const towardEarth = earthPos.clone().sub(cameraPos).normalize();
  const towardMoon = moonPos.clone().sub(cameraPos).normalize();
  const target = cameraPos.clone().add(towardEarth.clone().add(towardMoon));
  const cameraQuaternion = new Quaternion().setFromRotationMatrix(
    new Matrix4().lookAt(cameraPos, target, galacticNorth)
  );

  return {
    utc: moment.utc,
    earth: {
      position: earthPos,
      quaternion: meshQuaternion(earthIcrfToBody(jdUtc, moment.tdbMinusUtcSeconds)),
      sunDirection: toScene(sunKm).normalize(),
    },
    moon: {
      position: moonPos,
      quaternion: meshQuaternion(moonIcrfToBody(jdTdb)),
      sunDirection: toScene([sunKm[0] - moonKm[0], sunKm[1] - moonKm[1], sunKm[2] - moonKm[2]]).normalize(),
    },
    camera: {
      position: cameraPos,
      quaternion: cameraQuaternion,
      distanceFromEarthKm,
      aboveFarSideKm: cameraPos.distanceTo(moonPos) * KM_PER_UNIT - MOON_RADIUS_KM,
    },
  };
}
