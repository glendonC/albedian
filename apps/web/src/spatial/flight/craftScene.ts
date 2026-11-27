import { craftPlacementInView, photoCameraClipMatrix, wcsCameraRows, type PhotoPinhole, type Vec3, type WcsCameraRows } from '@albedian/camera-rig';
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

/** Where a screen frames the craft: its centre in window pixels and its bounding sphere's diameter across. */
export interface CraftFraming {
  readonly xPx: number;
  readonly yPx: number;
  readonly diameterPx: number;
  /** The height of the frame the craft is framed in, px: the window on the laptop, the sky above the readout on a phone. */
  readonly frameHeightPx?: number;
}

/** The laptop window D26's frame was approved at, CSS px. */
export const LAPTOP_FRAME_PX = { width: 1440, height: 900 } as const;

/**
 * craft-frame-share-one (DECIDED-BY-LEAD 27 Nov 12:04): how much larger than D26's 320 px the craft is framed on a
 * larger window: the smaller of the width's and the height's ratio to 1440 by 900, never below 1, so the craft keeps
 * the laptop's approved share of the frame on a larger window (the film's 1920 by 1080 frames it at 384 px) and stays
 * 320 px on any smaller one.
 */
export function laptopFrameShare(viewportWidthPx: number, viewportHeightPx: number): number {
  return Math.max(1, Math.min(viewportWidthPx / LAPTOP_FRAME_PX.width, viewportHeightPx / LAPTOP_FRAME_PX.height));
}

/**
 * D26 item 2 on the laptop: centred across, its middle at 62 percent of the window's height, 320 px across, grown with
 * a larger window by `laptopFrameShare`.
 */
export function laptopCraftFraming(camera: FlightCamera): CraftFraming {
  return {
    xPx: CRAFT_FRAMING.xFraction * camera.viewportWidthPx,
    yPx: CRAFT_FRAMING.yFraction * camera.viewportHeightPx,
    diameterPx: CRAFT_FRAMING.diameterPx * laptopFrameShare(camera.viewportWidthPx, camera.viewportHeightPx),
    frameHeightPx: camera.viewportHeightPx,
  };
}

/**
 * D30 item 1 at phone width: the sky's height is the band from the top bar's foot to the readout's top, as NASA's Eyes
 * frames the craft it follows inside its own band; the craft centred across, its middle at 62 percent of that band
 * (D26's 62 percent), 184 px across.
 */
export const PHONE_SKY_TOP_PX = 104;
export const PHONE_CRAFT_DIAMETER_PX = 184;
export function phoneCraftFraming(viewportWidthPx: number, readoutTopPx: number): CraftFraming {
  return {
    xPx: viewportWidthPx / 2,
    yPx: PHONE_SKY_TOP_PX + CRAFT_FRAMING.yFraction * (readoutTopPx - PHONE_SKY_TOP_PX),
    diameterPx: PHONE_CRAFT_DIAMETER_PX,
    frameHeightPx: readoutTopPx - PHONE_SKY_TOP_PX,
  };
}

/**
 * How wide a craft looks from behind at rest, in its model's units: the larger of its bounding box's extents along the
 * model's own up and right (forward x up), the box's size along each model axis given. A long part (Voyager's boom)
 * makes the bounding sphere far wider than the craft looks, so the framing's "across" (D26 item 2, D30 item 1) is this.
 */
export function restExtentM(craft: Craft, boxSize: Vec3): number {
  const f = craft.modelForward;
  const u = craft.modelUp;
  const r: Vec3 = [f[1] * u[2] - f[2] * u[1], f[2] * u[0] - f[0] * u[2], f[0] * u[1] - f[1] * u[0]];
  const along = (a: Vec3) => Math.abs(a[0]) * boxSize[0] + Math.abs(a[1]) * boxSize[1] + Math.abs(a[2]) * boxSize[2];
  return Math.max(along(u), along(r));
}

/**
 * Half of what the screen frames for a craft (D26 item 2, D30 item 1): its body's radius where the design lane rules it
 * framed by the body (18:35); else half of how wide it looks from behind; else its bounding sphere.
 */
export function craftFrameRadiusM(craft: Craft, boxSize: Vec3, sphereRadiusM: number): number {
  return craft.frameByBodyRadiusM ?? (restExtentM(craft, boxSize) / 2 || sphereRadiusM);
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
export function craftSceneOf(
  camera: FlightCamera,
  craft: Craft,
  modelCentre: Vec3,
  modelRadius: number,
  /**
   * While the person steers (pilot-1e): where the camera stands and its axes, and the craft's own axes when the chase
   * camera's look lags behind them (MOTION k13); the craft flies along its own heading, framed as at rest from its axes.
   */
  steer?: { positionPc: Vec3; rows: WcsCameraRows; craftRows?: WcsCameraRows },
  /** Where the screen frames the craft; the laptop's by default. */
  framing: CraftFraming = laptopCraftFraming(camera),
  /** Half of how wide the craft looks from behind (`restExtentM` / 2), framed at the screen's size; the sphere's by default. */
  frameRadiusM: number = modelRadius
): CraftScene | null {
  const o = steer ? steer.positionPc : camera.observerPc;
  let heading: Vec3;
  if (steer) {
    heading = (steer.craftRows ?? steer.rows).z;
  } else {
    const t = camera.referencePc;
    const along: Vec3 = [t[0] - o[0], t[1] - o[1], t[2] - o[2]];
    const toTarget = Math.hypot(along[0], along[1], along[2]);
    if (!(toTarget > 0)) return null;
    heading = [along[0] / toTarget, along[1] / toTarget, along[2] / toTarget];
  }
  const pin = steer ? { ...photoPinholeOf(camera), rows: steer.rows } : photoPinholeOf(camera);
  // The craft's place behind which the camera stands at once: framed from the craft's own axes (at rest they are the
  // camera's), then seen through the camera's, so in a turn the craft leads off the centre
  const framePin = steer?.craftRows ? { ...pin, rows: steer.craftRows } : pin;
  const placed = craftPlacementInView(
    framePin,
    { xPx: framing.xPx, yPx: framing.yPx },
    framing.diameterPx,
    frameRadiusM,
    heading,
    frameRadiusM !== modelRadius
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
