import { craftPlacementInView, photoCameraClipMatrix, wcsCameraRows, type PhotoPinhole, type Vec3 } from '@albedian/camera-rig';
import type { FlightCamera } from './flightFrame.js';
import { CRAFT_FRAMING, modelToIcrs, type Craft } from './crafts.js';

/**
 * pilot-1: the craft's scene for its renderer. The scene is laid out in metres along ICRS axes around the camera,
 * which keeps the photo's orientation and stands where the flight's camera stands: the craft a few metres ahead of it
 * at the framing point, turned to face the star flown to. Its stars stay the star layer's: metres never move a star.
 */

/** The flight camera as the pinhole the craft is drawn through (the solve's lens model left out for a near object). */
export function photoPinholeOf(camera: FlightCamera): PhotoPinhole {
  return {
    rows: wcsCameraRows(camera.wcs),
    crpixX: camera.wcs.crpixX,
    crpixY: camera.wcs.crpixY,
    scaleRadPerPx: (camera.wcs.scaleDegPerPx * Math.PI) / 180,
    storedToScreen: camera.storedToScreen,
    viewportWidthPx: camera.viewportWidthPx,
    viewportHeightPx: camera.viewportHeightPx,
  };
}

export interface CraftScene {
  /** Model space to camera-relative ICRS metres, column-major 4 by 4 (three.js Matrix4.fromArray). */
  readonly modelMatrix: number[];
  /** Camera-relative ICRS metres to clip space, column-major 4 by 4. */
  readonly clipMatrix: number[];
  readonly near: number;
  readonly far: number;
  /** Unit vector from the craft toward the Sun: the one real light out there. */
  readonly toSun: Vec3;
}

/**
 * The craft's scene for a model whose bounding sphere has `modelCentre` and `modelRadius` in its own units, or none
 * when the camera has no star to fly to (the reference is the Sun).
 */
export function craftSceneOf(camera: FlightCamera, craft: Craft, modelCentre: Vec3, modelRadius: number): CraftScene | null {
  const o = camera.observerPc;
  const t = camera.referencePc;
  const along: Vec3 = [t[0] - o[0], t[1] - o[1], t[2] - o[2]];
  const toTarget = Math.hypot(along[0], along[1], along[2]);
  if (!(toTarget > 0)) return null;
  const heading: Vec3 = [along[0] / toTarget, along[1] / toTarget, along[2] / toTarget];
  const pin = photoPinholeOf(camera);
  const placed = craftPlacementInView(
    pin,
    { xPx: CRAFT_FRAMING.xFraction * camera.viewportWidthPx, yPx: CRAFT_FRAMING.yFraction * camera.viewportHeightPx },
    CRAFT_FRAMING.diameterPx,
    modelRadius,
    heading
  );
  const r = modelToIcrs(craft, placed.right, placed.up, placed.forward);
  const c = placed.centreFromCameraM;
  // x_world = R (x_model - modelCentre) + c
  const tx = c[0] - (r[0]! * modelCentre[0] + r[1]! * modelCentre[1] + r[2]! * modelCentre[2]);
  const ty = c[1] - (r[3]! * modelCentre[0] + r[4]! * modelCentre[1] + r[5]! * modelCentre[2]);
  const tz = c[2] - (r[6]! * modelCentre[0] + r[7]! * modelCentre[1] + r[8]! * modelCentre[2]);
  const modelMatrix = [r[0]!, r[3]!, r[6]!, 0, r[1]!, r[4]!, r[7]!, 0, r[2]!, r[5]!, r[8]!, 0, tx, ty, tz, 1];
  const distance = Math.hypot(c[0], c[1], c[2]);
  const near = Math.max(1e-3, (distance - modelRadius) * 0.5);
  const far = (distance + modelRadius) * 2;
  // At the Sun itself (the flight's first frame) the Sun is behind the camera, where the flight left from
  const fromSun = Math.hypot(o[0], o[1], o[2]);
  const toSun: Vec3 = fromSun > 0 ? [-o[0] / fromSun, -o[1] / fromSun, -o[2] / fromSun] : [-heading[0], -heading[1], -heading[2]];
  return { modelMatrix, clipMatrix: photoCameraClipMatrix(pin, near, far), near, far, toSun };
}
