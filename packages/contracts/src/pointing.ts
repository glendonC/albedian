import { z } from 'zod';
import { INGEST_PATTERNS } from './ingest.js';
import { SHA256_HEX_PATTERN } from './media.js';

/**
 * The pointing record (spec `ios-capture-companion`, REQ-IOS-1.2, 1.3, 2, 6 and 7): what the phone's own sensors
 * said at the moment it took a photo, kept beside the photo and never inside it, and tied to it by the photo's
 * SHA-256. It says what was measured and how well, and never more:
 * - the device's attitude only with a heading, and a heading only with the accuracy the device stated for it and
 *   only when that accuracy is inside `HEADING_ACCURACY_LIMIT_DEG`; otherwise an explicit "no heading" and why;
 * - a place only when the person allowed it, with the accuracy the device stated for it;
 * - every angle with its unit and frame in its field's name or type, and every accuracy the device's own number.
 * Its evidence statement is fixed: the record is a hint for the solvers and the basis of an approximate pose, never
 * a measurement of the sky. Anything placed from it is `predicted`; a finding becomes `catalog_matched` only through
 * the unchanged held-out verification.
 *
 * Sources, read at Apple's developer documentation on 2026-11-11 (paths under developer.apple.com/documentation):
 * - attitude: coremotion/cmattitude/quaternion and coremotion/cmquaternion ("q.x*i + q.y*j + q.z*k + q.w", a unit
 *   quaternion); its frame coremotion/cmattitudereferenceframe/xtruenorthzvertical ("Z axis is vertical and the X
 *   axis points to the geographic north pole"; needs a magnetometer and location services);
 * - gravity and rotation rate: coremotion/cmdevicemotion/gravity ("expressed in the device's reference frame"),
 *   coremotion/cmacceleration/x ("in G's"), coremotion/getting-raw-gyroscope-events ("radians per second");
 *   sample times coremotion/cmlogitem/timestamp ("seconds since the device booted");
 * - heading: corelocation/clheading/trueheading (degrees from true north, 90 is east; "negative" when it could not
 *   be determined; needs location updates), corelocation/clheading/headingaccuracy ("maximum deviation (measured in
 *   degrees)"; "negative" means invalid), corelocation/cllocationmanager/headingorientation (the device axis the
 *   heading is measured along; faceUp, faceDown and unknown "have no effect");
 * - place: corelocation/cllocationcoordinate2d (WGS 84), corelocation/cllocation/horizontalaccuracy (radius in
 *   metres; "negative" is invalid), corelocation/cllocation/verticalaccuracy (0 or negative means the altitudes
 *   are invalid), corelocation/cllocation/altitude and ellipsoidalaltitude, corelocation/claccuracyauthorization,
 *   corelocation/cllocationsourceinformation/issimulatedbysoftware;
 * - camera: avfoundation/avcapturedevice/format/videofieldofview (horizontal, degrees; "zero if the format's field
 *   of view is unknown"), its geometricdistortioncorrectedvideofieldofview, avfoundation/avcapturedevice/
 *   videozoomfactor (from 1.0, a crop around the centre), avfoundation/avcaptureresolvedphotosettings/
 *   photodimensions, avfoundation/avcapturephotosettings/isautocontentawaredistortioncorrectionenabled.
 *
 * Mirrored in Python by `services/contracts/pointing.py`. The shared samples in
 * `tests/fixtures/pointing_contract.json` are written by the Python side and parsed here.
 *
 * The wire rules (.lead/briefs/WIRE_RULES.md) are held here until the shared wire module is on main: camelCase keys
 * only, every whole number bounded so it is a safe integer, the two device labels exact ASCII patterns (a length
 * counts the same in UTF-16 units and code points, and a lone surrogate never fits), and no year 0.
 */

export const POINTING_RECORD_FORMAT = 'albedian.pointing-record';
export const POINTING_RECORD_FORMAT_VERSION = 1;

/**
 * The largest heading accuracy a record keeps a heading with, in degrees [proposed]. The triangle solver keeps the
 * catalog stars within 68 deg of a pointing hint (`services/science/solver.py`, `solve_and_verify_field`), and the
 * iPhone main camera the phone solver lists has an 84.06 deg diagonal (`iphone 18 pro, 48mp fusion main` in
 * `PUBLISHED_CAMERA_FOV_DEG`, `services/science/phone_photo_solver.py`), so 42.03 deg from the centre to a corner,
 * and the app is an iPhone app. A heading error moves the
 * frame centre in azimuth by at most the error, so an error up to 68 - 42.03 = 25.97 deg keeps every corner inside
 * that cut; 25 is the whole degree below. Apple states no limit of its own.
 */
export const HEADING_ACCURACY_LIMIT_DEG = 25;

/**
 * How far from the photo's timestamp the motion sample may lie, in seconds [proposed]. Apple puts the device-motion
 * rate's maximum at "usually at least 100 Hz" (coremotion/getting-processed-device-motion-data); the app asks for
 * 100 Hz, so a running stream has a sample within 0.01 s of any moment, and five update periods leave room for a late
 * one. A sample further away means the stream was not running at the shutter.
 */
export const MOTION_SAMPLE_OFFSET_LIMIT_SEC = 0.05;

/**
 * How far the attitude quaternion's norm may lie from one [proposed]. Apple states the quaternion is a unit one;
 * doubles written by the device are within about 1e-15 of it, and even single-precision values within about 1e-7.
 * A quaternion further off is not an attitude (a dropped or scaled component, or angles written in its place).
 */
export const UNIT_QUATERNION_TOLERANCE = 1e-6;

/**
 * The band gravity's length must lie in, in g [proposed]. Apple gives gravity in G's (coremotion/cmacceleration/x)
 * and states no magnitude; Core Motion's gravity is the direction of gravity at about one g [inference], so a length
 * near 9.81 means metres per second squared were written, and one near zero says no direction. Unit mobile-1
 * measures the spread on a device and the band follows it.
 */
export const GRAVITY_LENGTH_MIN_G = 0.9;
export const GRAVITY_LENGTH_MAX_G = 1.1;

/**
 * The largest side of a photo or of the camera format, in pixels [proposed]: 2^15, about four times the long side of a
 * 48 MP frame the phone solver lists at 4:3 (8000 px [inference]). A bound that keeps it a safe integer, not a
 * measurement.
 */
export const POINTING_DIMENSION_MAX_PX = 32768;
/** The most samples a motion window holds [proposed]: an hour at the 100 Hz the app asks for, far past any exposure. */
export const POINTING_SAMPLE_COUNT_MAX = 360000;
/** The longest hardware model, in UTF-16 units [proposed]; it is ASCII, so in code points too. */
export const POINTING_LABEL_MAX_UTF16 = 32;
/** The longest system version, in UTF-16 units: what its pattern allows, three groups of four digits and two dots. */
export const POINTING_SYSTEM_VERSION_MAX_UTF16 = 14;
/**
 * The two device labels as exact patterns, so that a name a person gave the device never fits: the machine
 * identifier `utsname.machine` gives, letters then two numbers joined by a comma ("iPhone18,1" [inference]), and
 * UIDevice.systemVersion, one to three groups of digits joined by dots [proposed].
 */
export const POINTING_HARDWARE_MODEL_PATTERN = '^[A-Za-z]+[0-9]+,[0-9]+$';
export const POINTING_SYSTEM_VERSION_PATTERN = '^[0-9]{1,4}(\\.[0-9]{1,4}){0,2}$';

/** AVCaptureDevice.position of the camera that took the photo. */
export const CAMERA_POSITIONS = ['back', 'front'] as const;
/**
 * The physical camera that took the photo, as AVCaptureDevice.DeviceType names it (builtInWideAngleCamera,
 * builtInUltraWideCamera, builtInTelephotoCamera). A virtual camera (dual, dual wide, triple) is recorded as its
 * active primary constituent at the shutter, since its field of view is that lens's.
 */
export const CAMERA_DEVICE_TYPES = ['wide_angle', 'ultra_wide', 'telephoto'] as const;
/** CLDeviceOrientation values that set the heading's reference axis; faceUp, faceDown and unknown set none. */
export const HEADING_ORIENTATIONS = ['portrait', 'portrait_upside_down', 'landscape_left', 'landscape_right'] as const;
/**
 * Why a record has no heading: the device has no magnetometer or no true-north frame (`no_magnetometer`); the record
 * has no place, and true north needs one (`no_place`); no heading event arrived (`no_heading_event`); the device said
 * its heading or accuracy was invalid, a negative value (`heading_invalid`); or the accuracy it stated is outside
 * `HEADING_ACCURACY_LIMIT_DEG` (`accuracy_outside_limit`, which carries that accuracy).
 */
export const NO_HEADING_REASONS = ['no_magnetometer', 'no_place', 'no_heading_event', 'heading_invalid', 'accuracy_outside_limit'] as const;
/**
 * Why a record has no place: the person did not allow location (`not_allowed`); no location with a valid accuracy
 * arrived (`no_fix`); or the system said it simulated the location in software (`simulated_by_software`).
 */
export const NO_PLACE_REASONS = ['not_allowed', 'no_fix', 'simulated_by_software'] as const;
/** CLAccuracyAuthorization: a place under reduced accuracy states its own, larger, horizontal accuracy. */
export const ACCURACY_AUTHORIZATIONS = ['full', 'reduced'] as const;

export const POINTING_HINT_STATEMENT =
  'The phone sensors say where the phone pointed. This record is a hint for the solvers and the basis of an approximate pose, never a measurement of the sky: anything placed from it is predicted, and a finding becomes catalog_matched only through held-out verification.';

/** The contract's own rules, worded the same on both sides. */
export const POINTING_RULES = {
  headingNeedsPlace: 'A heading needs the place: true north is found from the device location, so a record with no place carries no heading.',
  headingInsideLimit: 'A measured heading has an accuracy inside the limit; outside it, the record says there is no heading and why.',
  noPlaceReasonNeedsNoPlace: 'No heading for want of a place needs a record with no place.',
  outsideLimitCarriesAccuracy: 'A heading left out for its accuracy carries the accuracy the device reported, and that accuracy is outside the limit.',
  onlyOutsideLimitReportsAccuracy: 'Only a heading left out for its accuracy carries a reported accuracy.',
  unitQuaternion: 'The attitude is a unit quaternion, as Core Motion reports it.',
  gravityNearOneG: 'Gravity is given in g, so its length lies inside the stated band around one g: a zero vector, or one written in metres per second squared, is refused.',
  sampleAtThePhoto: 'The motion sample is the one at the photo: its time lies within the limit of the photo timestamp.',
  windowRunsForward: 'The motion window runs forward: it ends no earlier than it starts.',
  placeholderSha256: 'A SHA-256 of 64 zeros is a placeholder, not a hash.',
} as const;

const finite = () => z.number().finite();
const dimension = () => z.number().int().positive().max(POINTING_DIMENSION_MAX_PX);
const dimensions = () => z.object({ width: dimension(), height: dimension() }).strict();
/** The device as a machine identifier and a system version, never by a name a person gave it. */
const DeviceIdentitySchema = z
  .object({
    hardwareModel: z.string().max(POINTING_LABEL_MAX_UTF16).regex(new RegExp(POINTING_HARDWARE_MODEL_PATTERN)),
    systemVersion: z.string().max(POINTING_SYSTEM_VERSION_MAX_UTF16).regex(new RegExp(POINTING_SYSTEM_VERSION_PATTERN)),
  })
  .strict();

/** A vector in the device's own frame (Core Motion's x, y and z axes of the device). */
const DeviceVectorSchema = z.object({ x: finite(), y: finite(), z: finite() }).strict();

/** CMAttitude.quaternion as the device reported it: x, y, z, w with norm one. q and -q are the same attitude. */
export const AttitudeQuaternionSchema = z
  .object({ x: finite(), y: finite(), z: finite(), w: finite() })
  .strict()
  .refine((q) => Math.abs(Math.sqrt(q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w) - 1) <= UNIT_QUATERNION_TOLERANCE, {
    message: POINTING_RULES.unitQuaternion,
  });
export type AttitudeQuaternion = z.infer<typeof AttitudeQuaternionSchema>;

/**
 * A heading the device measured: the attitude of the motion sample in the true-north frame (which carries the
 * heading of every device axis, the camera's among them), Core Location's true heading of the axis
 * `headingOrientation` names, and the accuracy Core Location stated for it. The heading event's time is given as its
 * offset from the photo on the device clock; it may precede the photo by any amount, since a still phone sends no new
 * heading events.
 */
const MeasuredHeadingSchema = z
  .object({
    state: z.literal('measured'),
    attitude: z
      .object({
        referenceFrame: z.literal('x_true_north_z_vertical'),
        quaternion: AttitudeQuaternionSchema,
      })
      .strict(),
    trueHeadingDeg: z.number().min(0).lt(360),
    headingAccuracyDeg: z.number().nonnegative().finite(),
    headingOrientation: z.enum(HEADING_ORIENTATIONS),
    eventOffsetFromPhotoSec: finite(),
  })
  .strict();

/** No heading, and why; with `accuracy_outside_limit`, the accuracy the device stated, which is outside the limit. */
const NoHeadingSchema = z
  .object({
    state: z.literal('none'),
    reason: z.enum(NO_HEADING_REASONS),
    reportedHeadingAccuracyDeg: z.number().nonnegative().finite().optional(),
  })
  .strict();

export const PointingHeadingSchema = z.discriminatedUnion('state', [MeasuredHeadingSchema, NoHeadingSchema]);
export type PointingHeading = z.infer<typeof PointingHeadingSchema>;

/**
 * A place the person allowed: WGS 84 latitude and longitude, the horizontal accuracy the device stated (a radius in
 * metres), the accuracy the person granted, the altitudes only when the device called them valid (a positive vertical
 * accuracy, one standard deviation), and the fix's time as its offset from the photo on the device clock.
 */
const AllowedPlaceSchema = z
  .object({
    state: z.literal('allowed'),
    latDeg: z.number().min(-90).max(90),
    lonDeg: z.number().min(-180).max(180),
    horizontalAccuracyM: z.number().nonnegative().finite(),
    accuracyAuthorization: z.enum(ACCURACY_AUTHORIZATIONS),
    altitude: z
      .object({
        aboveMeanSeaLevelM: finite(),
        aboveEllipsoidM: finite(),
        verticalAccuracyM: z.number().positive().finite(),
      })
      .strict()
      .optional(),
    fixOffsetFromPhotoSec: finite(),
  })
  .strict();

const NoPlaceSchema = z.object({ state: z.literal('none'), reason: z.enum(NO_PLACE_REASONS) }).strict();

export const PointingPlaceSchema = z.discriminatedUnion('state', [AllowedPlaceSchema, NoPlaceSchema]);
export type PointingPlace = z.infer<typeof PointingPlaceSchema>;

/**
 * The camera that took the photo and its active format at the shutter: the format's horizontal field of view as
 * AVFoundation reported it (the distortion-corrected one when geometric distortion correction was on), or `unknown`
 * where it reported zero; the zoom factor (a centre crop from 1.0); whether content-aware distortion correction was
 * on (it bends the image away from a single rectilinear projection); and the format's and photo's sizes in pixels.
 */
export const PointingCameraSchema = z
  .object({
    position: z.enum(CAMERA_POSITIONS),
    deviceType: z.enum(CAMERA_DEVICE_TYPES),
    activeFormatDimensionsPx: dimensions(),
    photoDimensionsPx: dimensions(),
    fieldOfView: z.discriminatedUnion('state', [
      z.object({ state: z.literal('reported'), horizontalDeg: z.number().positive().lt(180), distortionCorrected: z.boolean() }).strict(),
      z.object({ state: z.literal('unknown') }).strict(),
    ]),
    videoZoomFactor: z.number().min(1).finite(),
    contentAwareDistortionCorrection: z.boolean(),
  })
  .strict();
export type PointingCamera = z.infer<typeof PointingCameraSchema>;

/**
 * The Core Motion device-motion sample nearest the photo's timestamp (the same sample whose attitude a measured
 * heading carries): its offset from the photo on one timebase, gravity in the device frame in units of g, and the
 * bias-removed rotation rate in the device frame in radians per second. The window gives the samples the app read
 * over the span it states, meant to cover the exposure, and the largest rotation rate magnitude among them: whether
 * the phone was still while the photo was taken.
 */
export const PointingMotionSchema = z
  .object({
    sampleOffsetFromPhotoSec: finite(),
    gravityDeviceG: DeviceVectorSchema,
    rotationRateDeviceRadPerSec: DeviceVectorSchema,
    window: z
      .object({
        startOffsetSec: finite(),
        endOffsetSec: finite(),
        sampleCount: z.number().int().positive().max(POINTING_SAMPLE_COUNT_MAX),
        maxRotationRateRadPerSec: z.number().nonnegative().finite(),
      })
      .strict(),
  })
  .strict();
export type PointingMotion = z.infer<typeof PointingMotionSchema>;

export const PointingRecordSchema = z
  .object({
    format: z.literal(POINTING_RECORD_FORMAT),
    formatVersion: z.literal(POINTING_RECORD_FORMAT_VERSION),
    // The photo's SHA-256 over the bytes the camera wrote and the app sends, never bytes inside the photo
    sourceAssetSha256: z
      .string()
      .regex(new RegExp(SHA256_HEX_PATTERN))
      .refine((value) => !/^0+$/.test(value), { message: POINTING_RULES.placeholderSha256 }),
    // The device clock's reading at the photo's timestamp, in UTC, on a real calendar day; no year 0, which the Python
    // datetime has not either
    capturedAtDeviceClockUTC: z
      .string()
      .datetime()
      .regex(new RegExp(INGEST_PATTERNS.utcInstant))
      .refine((value) => !value.startsWith('0000'), { message: 'No device clock reads year 0.' }),
    device: DeviceIdentitySchema,
    camera: PointingCameraSchema,
    motion: PointingMotionSchema,
    heading: PointingHeadingSchema,
    location: PointingPlaceSchema,
    evidence: z.object({ role: z.literal('pointing_hint'), statement: z.literal(POINTING_HINT_STATEMENT) }).strict(),
  })
  .strict()
  .superRefine((record, ctx) => {
    const issue = (path: (string | number)[], message: string) => ctx.addIssue({ code: z.ZodIssueCode.custom, path, message });
    const { heading, location, motion } = record;
    if (heading.state === 'measured') {
      // True north is computed from the place (xTrueNorthZVertical, CLHeading.trueHeading)
      if (location.state !== 'allowed') issue(['heading'], POINTING_RULES.headingNeedsPlace);
      if (heading.headingAccuracyDeg > HEADING_ACCURACY_LIMIT_DEG) issue(['heading', 'headingAccuracyDeg'], POINTING_RULES.headingInsideLimit);
    } else {
      if (heading.reason === 'no_place' && location.state === 'allowed') issue(['heading', 'reason'], POINTING_RULES.noPlaceReasonNeedsNoPlace);
      const reported = heading.reportedHeadingAccuracyDeg;
      if (heading.reason === 'accuracy_outside_limit') {
        if (reported === undefined || reported <= HEADING_ACCURACY_LIMIT_DEG) {
          issue(['heading', 'reportedHeadingAccuracyDeg'], POINTING_RULES.outsideLimitCarriesAccuracy);
        }
      } else if (reported !== undefined) {
        issue(['heading', 'reportedHeadingAccuracyDeg'], POINTING_RULES.onlyOutsideLimitReportsAccuracy);
      }
    }
    const gravity = motion.gravityDeviceG;
    // The same arithmetic, in the same order, as the Pydantic validator
    const length = Math.sqrt(gravity.x * gravity.x + gravity.y * gravity.y + gravity.z * gravity.z);
    if (length < GRAVITY_LENGTH_MIN_G || length > GRAVITY_LENGTH_MAX_G) issue(['motion', 'gravityDeviceG'], POINTING_RULES.gravityNearOneG);
    if (Math.abs(motion.sampleOffsetFromPhotoSec) > MOTION_SAMPLE_OFFSET_LIMIT_SEC) {
      issue(['motion', 'sampleOffsetFromPhotoSec'], POINTING_RULES.sampleAtThePhoto);
    }
    if (motion.window.endOffsetSec < motion.window.startOffsetSec) issue(['motion', 'window'], POINTING_RULES.windowRunsForward);
  });
export type PointingRecord = z.infer<typeof PointingRecordSchema>;
