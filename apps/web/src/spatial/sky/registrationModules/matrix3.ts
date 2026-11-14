import * as THREE from 'three';
import { ICRS_TO_SCENE_QUATERNION } from '../../catalogStars/CatalogStarField.js';
import type { Vec3 } from '../../catalogStars/healpix.js';
import { ICRS_TO_GALACTIC, type Mat3Rows, type PanoramaRegistration } from './types.js';

export function multiplyRows(a: Mat3Rows, b: Mat3Rows): Mat3Rows {
  return [
    a[0] * b[0] + a[1] * b[3] + a[2] * b[6],
    a[0] * b[1] + a[1] * b[4] + a[2] * b[7],
    a[0] * b[2] + a[1] * b[5] + a[2] * b[8],
    a[3] * b[0] + a[4] * b[3] + a[5] * b[6],
    a[3] * b[1] + a[4] * b[4] + a[5] * b[7],
    a[3] * b[2] + a[4] * b[5] + a[5] * b[8],
    a[6] * b[0] + a[7] * b[3] + a[8] * b[6],
    a[6] * b[1] + a[7] * b[4] + a[8] * b[7],
    a[6] * b[2] + a[7] * b[5] + a[8] * b[8],
  ];
}

export function transposeRows(m: Mat3Rows): Mat3Rows {
  return [m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8]];
}

export function applyRows(m: Mat3Rows, v: Vec3): Vec3 {
  return [
    m[0] * v[0] + m[1] * v[1] + m[2] * v[2],
    m[3] * v[0] + m[4] * v[1] + m[5] * v[2],
    m[6] * v[0] + m[7] * v[1] + m[8] * v[2],
  ];
}

/** Scene axes to ICRS: the inverse of the rotation the catalog star mounts carry. */
export function sceneToIcrsRows(): Mat3Rows {
  const e = new THREE.Matrix4().makeRotationFromQuaternion(ICRS_TO_SCENE_QUATERNION).elements;
  // Matrix4 elements are column-major; the transpose of the rotation is its inverse
  return [e[0]!, e[1]!, e[2]!, e[4]!, e[5]!, e[6]!, e[8]!, e[9]!, e[10]!];
}

/** Scene axes to the panorama's fitted frame. */
export function sceneToPanoramaRows(registration: PanoramaRegistration): Mat3Rows {
  return multiplyRows(registration.galacticToPanorama, multiplyRows(ICRS_TO_GALACTIC, sceneToIcrsRows()));
}
