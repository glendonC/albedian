import { distortionFoldRadiusRad, wcsCameraRows, type OrbitCamera, type PhotoPinhole, type Vec3, type WcsCameraRows } from '@albedian/camera-rig';
import type { Affine2, FlightCamera } from './flightFrame.js';

/**
 * Any camera the star layer draws through: camera rows, a tangent-plane pinhole with an optional radial lens model,
 * the image's frame on the window, and where the camera stands against the floating origin. The flight's camera is the
 * photo's own solve; the cone view's is an orbit camera with a plain pinhole.
 */
export interface StarView {
  readonly rows: WcsCameraRows;
  readonly crpixX: number;
  readonly crpixY: number;
  readonly scaleRadPerPx: number;
  readonly k1: number;
  readonly k2: number;
  /** The lens model's fold radius in the tangent plane; infinite where it has none. */
  readonly foldRad: number;
  readonly storedToScreen: Affine2;
  readonly viewportWidthPx: number;
  readonly viewportHeightPx: number;
  readonly observerPc: Vec3;
  readonly referencePc: Vec3;
}

/** The flight's camera: the photo's own solve, standing where the session's camera stands. */
export function starViewOfFlight(camera: FlightCamera): StarView {
  return {
    rows: wcsCameraRows(camera.wcs),
    crpixX: camera.wcs.crpixX,
    crpixY: camera.wcs.crpixY,
    scaleRadPerPx: (camera.wcs.scaleDegPerPx * Math.PI) / 180,
    k1: camera.wcs.radialDistortionK1,
    k2: camera.wcs.radialDistortionK2,
    foldRad: distortionFoldRadiusRad(camera.wcs),
    storedToScreen: camera.storedToScreen,
    viewportWidthPx: camera.viewportWidthPx,
    viewportHeightPx: camera.viewportHeightPx,
    observerPc: camera.observerPc,
    referencePc: camera.referencePc,
  };
}

/** An orbit camera as a plain pinhole filling the window, `fovVerticalDeg` from top to bottom. */
export function starViewOfOrbit(orbit: OrbitCamera, fovVerticalDeg: number, viewportWidthPx: number, viewportHeightPx: number, referencePc: Vec3): StarView {
  return {
    rows: orbit.rows,
    crpixX: viewportWidthPx / 2,
    crpixY: viewportHeightPx / 2,
    scaleRadPerPx: Math.tan(((fovVerticalDeg * Math.PI) / 180) / 2) / (viewportHeightPx / 2),
    k1: 0,
    k2: 0,
    foldRad: Number.POSITIVE_INFINITY,
    storedToScreen: [1, 0, 0, 0, 1, 0],
    viewportWidthPx,
    viewportHeightPx,
    observerPc: orbit.positionPc,
    referencePc,
  };
}

/** The view as the pinhole the three.js layers draw through (its lens model left out). */
export function pinholeOfView(view: StarView): PhotoPinhole {
  return {
    rows: view.rows,
    crpixX: view.crpixX,
    crpixY: view.crpixY,
    scaleRadPerPx: view.scaleRadPerPx,
    storedToScreen: view.storedToScreen,
    viewportWidthPx: view.viewportWidthPx,
    viewportHeightPx: view.viewportHeightPx,
  };
}

/**
 * The flight's camera handed to the person (D26 item 6): the photo's own lens and frame on the window, turned and
 * moved as they steer, so the moment control passes nothing jumps.
 */
export function starViewOfSteer(camera: FlightCamera, steer: { positionPc: Vec3; rows: WcsCameraRows }): StarView {
  return { ...starViewOfFlight(camera), rows: steer.rows, observerPc: steer.positionPc };
}

/** Where steering starts: the flight camera as it stands, the photo's own axes. */
export function steerStartOf(camera: FlightCamera): { positionPc: Vec3; rows: WcsCameraRows } {
  return { positionPc: camera.observerPc, rows: wcsCameraRows(camera.wcs) };
}
