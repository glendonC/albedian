import * as THREE from 'three';
import { earthIcrfToBody, julianDateUtc } from './bodyOrientation.js';
import { icrsToScene } from './catalogStars/CatalogStarField.js';
import type { SpatialScaleRegime } from './SparkPortalRenderer.js';

/**
 * Where the sky stands for an observer at a place and time: the zenith, north and east as ICRS
 * directions, from the Earth's orientation in bodyOrientation.ts (IAU 1976 precession, IAU 1982
 * Greenwich mean sidereal time, UT1 taken as UTC). Nutation and polar motion (sized in
 * bodyOrientation.ts), refraction and the height of the site are left out.
 *
 * The sky view turns its camera's parent into the observer's frame (x east, y zenith, z south), so
 * the camera's yaw is minus the azimuth and its pitch is the altitude.
 */

type Vec3 = readonly [number, number, number];

/**
 * TDB minus UTC in seconds: TT - UTC is 69.184 s while there are 37 leap seconds (since 2017), and
 * TDB - TT is under 2 ms. It only sets the precession epoch here, where a minute moves the pole by
 * under 0.000001 deg.
 */
export const TDB_MINUS_UTC_SECONDS = 69.184;

export interface ObserverSite {
  /** Geodetic latitude, degrees, north positive. */
  latDeg: number;
  /** Longitude, degrees, east positive. */
  lonDeg: number;
}

/** An observer's local axes as ICRS unit vectors, and the Earth's north pole of date. */
export interface ObserverAxes {
  zenith: Vec3;
  north: Vec3;
  east: Vec3;
  poleOfDate: Vec3;
}

/** The time the sky is drawn for: the recording's capture time, or the device clock now. */
export type ObserverClock = { kind: 'capture'; jdUtc: number } | { kind: 'now' };

const DEG = Math.PI / 180;

/** The capture time when it parses as a time, else the device clock now. */
export function observerClockFor(capturedAtUtc: string | null | undefined): ObserverClock {
  if (!capturedAtUtc) return { kind: 'now' };
  try {
    return { kind: 'capture', jdUtc: julianDateUtc(capturedAtUtc) };
  } catch {
    return { kind: 'now' };
  }
}

/** Zenith, north and east for a site at a Julian date in UTC, in ICRS. */
export function observerAxes(site: ObserverSite, jdUtc: number): ObserverAxes {
  const m = earthIcrfToBody(jdUtc, TDB_MINUS_UTC_SECONDS);
  const sinLat = Math.sin(site.latDeg * DEG);
  const cosLat = Math.cos(site.latDeg * DEG);
  const sinLon = Math.sin(site.lonDeg * DEG);
  const cosLon = Math.cos(site.lonDeg * DEG);
  // Body frame to ICRS is the transpose: rows of m are the body axes in ICRS
  const toIcrs = (x: number, y: number, z: number): Vec3 => [
    m[0] * x + m[3] * y + m[6] * z,
    m[1] * x + m[4] * y + m[7] * z,
    m[2] * x + m[5] * y + m[8] * z,
  ];
  return {
    zenith: toIcrs(cosLat * cosLon, cosLat * sinLon, sinLat),
    north: toIcrs(-sinLat * cosLon, -sinLat * sinLon, cosLat),
    east: toIcrs(-sinLon, cosLon, 0),
    poleOfDate: toIcrs(0, 0, 1),
  };
}

/** Altitude and azimuth (from north through east, in [0, 360)) of an ICRS direction, degrees. */
export function altAzDeg(axes: ObserverAxes, icrs: Vec3): { altDeg: number; azDeg: number } {
  const dot = (a: Vec3) => a[0] * icrs[0] + a[1] * icrs[1] + a[2] * icrs[2];
  const up = dot(axes.zenith);
  const n = dot(axes.north);
  const e = dot(axes.east);
  return {
    // atan2 keeps full precision near the zenith, where asin loses half the digits
    altDeg: Math.atan2(up, Math.hypot(n, e)) / DEG,
    azDeg: (((Math.atan2(e, n) / DEG) % 360) + 360) % 360,
  };
}

/** Turns the observer's local axes (x east, y zenith, z south) into scene axes. */
export function observerFrameQuaternion(axes: ObserverAxes, target = new THREE.Quaternion()): THREE.Quaternion {
  const scene = (v: Vec3) => icrsToScene([v[0], v[1], v[2]]);
  const east = scene(axes.east);
  const zenith = scene(axes.zenith);
  const south = scene(axes.north).negate();
  return target.setFromRotationMatrix(new THREE.Matrix4().makeBasis(east, zenith, south));
}

/**
 * The camera parent's rotation for a regime: the observer frame in the sky view once a location is
 * known; otherwise none, so celestial north stays up there and the modelled scenes keep their axes.
 */
export function localFrameQuaternionFor(
  regime: SpatialScaleRegime,
  observerFrame: THREE.Quaternion | null,
  target = new THREE.Quaternion()
): THREE.Quaternion {
  return regime === 'earth_capture' && observerFrame ? target.copy(observerFrame) : target.identity();
}

/**
 * Where the sky view looks when a place arrives: the meridian on the celestial equator's side
 * (due south north of the equator, due north south of it), half way up, so it never opens on the
 * ground [proposed convention].
 */
export function defaultObserverLook(latDeg: number): { azDeg: number; altDeg: number; yaw: number; pitch: number } {
  const azDeg = latDeg >= 0 ? 180 : 0;
  const altDeg = 45;
  // The sky view's camera angles under the observer frame: yaw is minus the azimuth, pitch the altitude
  return { azDeg, altDeg, yaw: -azDeg * DEG, pitch: altDeg * DEG };
}

/** The horizon shows only in the sky view, outside a flight, once the observer is placed. */
export function observerHorizonVisible(view: { regime: SpatialScaleRegime; flying: boolean; placed: boolean }): boolean {
  return view.regime === 'earth_capture' && !view.flying && view.placed;
}

const JD_UNIX_EPOCH = 2440587.5;
const MS_PER_DAY = 86_400_000;
/** The device clock moves the sky by 0.004 deg a second, well under a pixel, so once a second is enough. */
const NOW_REFRESH_MS = 1000;

/**
 * The sky view's observer: the place, the time the sky is drawn for and the frame it stands in.
 * Unplaced until `set` gets a place; then the frame follows the capture time, or the device clock.
 */
export class ObserverSky {
  site: ObserverSite | null = null;
  clock: ObserverClock = { kind: 'now' };
  frame: THREE.Quaternion | null = null;
  private refreshedAtMs = Number.NEGATIVE_INFINITY;

  /** Takes a place and capture time; false when it is no place, or the same place and time as now. */
  set(latDeg: number, lonDeg: number, capturedAtUtc: string | null | undefined): boolean {
    if (!Number.isFinite(latDeg) || !Number.isFinite(lonDeg) || Math.abs(latDeg) > 90) return false;
    const clock = observerClockFor(capturedAtUtc);
    const sameClock =
      clock.kind === 'now' ? this.clock.kind === 'now' : this.clock.kind === 'capture' && this.clock.jdUtc === clock.jdUtc;
    const site = this.site;
    if (site && sameClock && Math.abs(latDeg - site.latDeg) < 0.01 && Math.abs(lonDeg - site.lonDeg) < 0.01) return false;
    this.site = { latDeg, lonDeg };
    this.clock = clock;
    return true;
  }

  /** Forgets the place and time, as for a recording without a location; false when none was set. */
  clear(): boolean {
    if (!this.site) return false;
    this.site = null;
    this.clock = { kind: 'now' };
    this.frame = null;
    this.refreshedAtMs = Number.NEGATIVE_INFINITY;
    return true;
  }

  /** Whether the frame should move on: running on the device clock and a second since the last. */
  due(nowMs: number): boolean {
    return this.site !== null && this.clock.kind === 'now' && nowMs - this.refreshedAtMs >= NOW_REFRESH_MS;
  }

  /** Recomputes the frame for the capture time, or for `nowMs` on the device clock; null unplaced. */
  refresh(nowMs: number): THREE.Quaternion | null {
    if (!this.site) return null;
    const jdUtc = this.clock.kind === 'capture' ? this.clock.jdUtc : nowMs / MS_PER_DAY + JD_UNIX_EPOCH;
    this.frame = observerFrameQuaternion(observerAxes(this.site, jdUtc), this.frame ?? undefined);
    this.refreshedAtMs = nowMs;
    return this.frame;
  }
}
