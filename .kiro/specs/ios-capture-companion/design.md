# Design: iOS Capture Companion

Requirements: `requirements.md` in this folder, confirmed by the owner on 2026-11-11 (`.lead/APPROVALS.md`). Every
line reference below was read at `main` `b59ae181`. Every Apple fact was read at Apple's developer documentation on
2026-11-11; a path like `coremotion/cmattitude` means `developer.apple.com/documentation/coremotion/cmattitude`.
Labels: [verified] read at that source, [inference], [proposed], [open].

## Overview

```
iPhone (apps/ios, later units)                                  Laptop (pnpm field, unchanged solvers)
  aim screen: preview, device motion at 100 Hz, heading, place
    live overlay of predicted stars and planets (REQ-IOS-3)
  shutter: AVCapturePhoto, its bytes as the camera wrote them
    pointing record (packages/contracts/src/pointing.ts)
    SHA-256 of the photo bytes, written into the record
  both kept on the phone until sent (REQ-IOS-4.3)
  send over the local network
    POST /api/ingest (unchanged), the photo bytes      ------>  IngestReceipt
    PUT  /api/ingest/{sha256}/pointing (new), the record ---->  stored beside the receipt
    POST /api/ingest/{sha256}/solve (unchanged)        ------>  run_solve_job
                                                                  record -> search hint (narrows only)
                                                                  no stars solved -> poseEstimate from the record
  GET /api/ingest/jobs/{id} and .../solution          <------  status, counts by evidence class (REQ-IOS-5)
```

This unit (pointing-1) builds only the record's contract. No app code, no route, no change to any solver.

## Components

### 1. What the app records at the shutter (apps/ios, unit mobile-1)

| Value | Apple API | What it means physically | Accuracy the device states |
|---|---|---|---|
| Attitude | `CMDeviceMotion.attitude.quaternion` from `CMMotionManager.startDeviceMotionUpdates(using: .xTrueNorthZVertical)` | The device's rotation relative to a frame whose Z axis is vertical and whose X axis points to the geographic north pole (`coremotion/cmattitudereferenceframe/xtruenorthzvertical`); a unit quaternion `q.x*i + q.y*j + q.z*k + q.w` (`coremotion/cmquaternion`) | None in degrees. The frame needs a magnetometer and location services, and Core Motion may ask the person to move the phone to calibrate [verified]. `CMDeviceMotion.headingAccuracy` is declared from iOS 27 with no description of its meaning or unit (`coremotion/cmdevicemotion/headingaccuracy`, empty abstract) [verified], so it is not used [open: until Apple documents it] |
| Gravity | `CMDeviceMotion.gravity` | The gravity vector in the device's own frame, in g (`coremotion/cmdevicemotion/gravity`, `coremotion/cmacceleration/x`) | None |
| Rotation rate | `CMDeviceMotion.rotationRate` | The device's rotation rate about its axes, gyroscope bias removed, in radians per second (`coremotion/cmdevicemotion/rotationrate`, `coremotion/getting-raw-gyroscope-events`) | None |
| Sample time | `CMLogItem.timestamp` | Seconds since the device booted (`coremotion/cmlogitem/timestamp`) | None |
| True heading | `CLLocationManager.startUpdatingHeading()`, `CLHeading.trueHeading` | Degrees from true north, 90 east, of the device axis `headingOrientation` names (the top of the device in portrait by default); valid only with location updates on; negative when it could not be determined (`corelocation/clheading/trueheading`, `corelocation/cllocationmanager/headingorientation`) | `CLHeading.headingAccuracy`: "the maximum deviation (measured in degrees) between the reported heading and the true geomagnetic heading"; negative means invalid, from an uncalibrated device or magnetic interference (`corelocation/clheading/headingaccuracy`) [verified] |
| Place | `CLLocation.coordinate`, `altitude`, `ellipsoidalAltitude`, `timestamp`; `CLLocationManager.accuracyAuthorization`; `CLLocation.sourceInformation` | WGS 84 latitude and longitude (`corelocation/cllocationcoordinate2d`); height above mean sea level (EGM 2008 in most cases) and above the WGS 84 ellipsoid; full or reduced accuracy granted; whether the system simulated the location in software | `horizontalAccuracy`: a radius in metres, negative means invalid. `verticalAccuracy`: about one standard deviation, 0 or negative means both altitudes are invalid [verified] |
| Field of view | `AVCaptureDevice.activeFormat.videoFieldOfView`, or `geometricDistortionCorrectedVideoFieldOfView` while `isGeometricDistortionCorrectionEnabled` (on by default where supported) | The active format's horizontal field of view in degrees; zero when unknown (`avfoundation/avcapturedevice/format/videofieldofview`) | None. From iOS 26, `videoFieldOfView(for:geometricDistortionCorrected:)` gives it per aspect ratio for formats with dynamic aspect ratios [verified]; the app uses it for those [proposed] |
| Zoom | `AVCaptureDevice.videoZoomFactor` | A crop around the image centre from 1.0; "a value of 2.0 doubles the size of an image's subject (and halves the field of view)" (`avfoundation/avcapturedevice/videozoomfactor`) | None |
| Lens | `AVCaptureDevice.deviceType`, `position`; `activePrimaryConstituent` for a virtual camera | Which physical camera took the photo (wide angle, ultra wide, telephoto) and which side it faces | None |
| Photo size | `AVCaptureResolvedPhotoSettings.photoDimensions` | The delivered photo's size in pixels, set "depending on device orientation and capture session configuration" | None |
| Distortion | `AVCapturePhotoSettings.isAutoContentAwareDistortionCorrectionEnabled` | Whether the output may warp the photo content-aware; the app leaves it off (its default) [proposed] | None |
| Photo time | `AVCapturePhoto.timestamp` | The capture time, "always synchronized to the masterClock time" of the session; the session's `synchronizationClock` is documented for lining up capture timestamps with "external data sources such as Core Motion samples" (`avfoundation/avcapturesession/synchronizationclock`) | None |
| Photo bytes | `AVCapturePhoto.fileDataRepresentation()` | The photo, its metadata and attachments flattened into a file of the requested type | Not applicable |

What the record keeps, and why each value is there:
- The true-north attitude, not only Core Location's heading: `CLHeading` measures one device axis, and aimed at the
  sky the camera axis is far from it (the top of the phone may point near the zenith, where an axis's azimuth is
  undefined). The attitude gives the direction of every device axis, the camera's among them [inference].
- Core Location's heading and accuracy beside it: `headingAccuracy` is the only heading accuracy Apple states in
  degrees. Apple does not say that Core Location's heading and Core Motion's true-north frame share one estimate
  [open]; keeping both lets the server compare the attitude's azimuth of the `headingOrientation` axis with
  `trueHeadingDeg`, and unit mobile-1 checks that on a device.
- `headingAccuracy` is stated against the geomagnetic heading. The true heading adds the declination model's error,
  which Apple does not state [open].
- The motion sample nearest the photo, matched on one timebase: the photo's timestamp converted from the session's
  synchronization clock to the host clock. That the host clock and `CMLogItem.timestamp` count the same seconds since
  boot is [inference]; unit mobile-1 checks it on a device before any record is trusted.
- The photo's own time and place: the app adds nothing to the photo (REQ-IOS-1.2), so it carries no GPS block from
  the app. Whether `AVCapturePhoto.metadata` holds an EXIF DateTimeOriginal with its offset is not stated (the page
  says only that it "may include image orientation, Exif camera properties") [open: checked on a device in mobile-1].
  The record's `capturedAtDeviceClockUTC` is the device clock at the photo's timestamp either way.
- The device's hardware model (`utsname.machine`) and `UIDevice.systemVersion`, so a camera profile and the API
  version behind each value can be named later [proposed]. The companion never sends the device's name
  (`UIDevice.name`, which holds the name a person gave the device [inference]): a name is personal, and the contract's
  two device labels are exact patterns that no such name fits.

### 2. The pointing record contract (`packages/contracts/src/pointing.ts`, `services/contracts/pointing.py`; this unit)

`PointingRecordSchema` (format `albedian.pointing-record`, version 1), mirrored by `PointingRecord`:
- `sourceAssetSha256`: the photo's SHA-256 over the bytes the camera wrote and the app sends (not 64 zeros).
- `capturedAtDeviceClockUTC`: the device clock at the photo's timestamp, a calendar UTC instant.
- `device`: `hardwareModel`, a machine identifier (letters, then two numbers joined by a comma, as in `iPhone18,1`
  [inference]) of at most `POINTING_LABEL_MAX_UTF16 = 32` characters [proposed], and `systemVersion`, one to three
  groups of one to four digits joined by dots, at most `POINTING_SYSTEM_VERSION_MAX_UTF16 = 14` characters (what that
  pattern allows); nothing with a space, an apostrophe or any
  letter outside ASCII, so no name a person gave the device fits.
- `camera`: `position`, `deviceType` (the physical lens), `activeFormatDimensionsPx`, `photoDimensionsPx` (each side
  from 1 to `POINTING_DIMENSION_MAX_PX`),
  `fieldOfView` (`reported` with `horizontalDeg` in (0, 180) and `distortionCorrected`, or `unknown` where the device
  wrote zero), `videoZoomFactor` at least 1, `contentAwareDistortionCorrection`.
- `motion`: `sampleOffsetFromPhotoSec` (within `MOTION_SAMPLE_OFFSET_LIMIT_SEC`), `gravityDeviceG` (its length from
  `GRAVITY_LENGTH_MIN_G` to `GRAVITY_LENGTH_MAX_G`), `rotationRateDeviceRadPerSec`, and a `window` (`startOffsetSec`
  no later than `endOffsetSec`, from one sample to `POINTING_SAMPLE_COUNT_MAX`, `maxRotationRateRadPerSec`).
- `heading`: `measured` (the attitude in `x_true_north_z_vertical` as a unit quaternion, `trueHeadingDeg` in [0,
  360), `headingAccuracyDeg` from 0 to `HEADING_ACCURACY_LIMIT_DEG`, `headingOrientation`, `eventOffsetFromPhotoSec`)
  or `none` with a reason: `no_magnetometer`, `no_place`, `no_heading_event`, `heading_invalid`, or
  `accuracy_outside_limit`, which alone carries `reportedHeadingAccuracyDeg`, above the limit. A measured heading
  needs an allowed place; `no_place` needs a record with no place.
- `location`: `allowed` (`latDeg`, `lonDeg`, `horizontalAccuracyM`, `accuracyAuthorization`, an optional `altitude`
  with a positive `verticalAccuracyM`, `fixOffsetFromPhotoSec`) or `none` with `not_allowed`, `no_fix` or
  `simulated_by_software`.
- `evidence`: role `pointing_hint` and a fixed statement: the record is a hint for the solvers and the basis of an
  approximate pose, never a measurement of the sky; anything placed from it is `predicted`; a finding becomes
  `catalog_matched` only through held-out verification.

Limits, each [proposed] with its reason in both files:
- `HEADING_ACCURACY_LIMIT_DEG = 25`: the triangle solver keeps catalog stars within 68 deg of a hint
  (`solve_and_verify_field`, `services/science/solver.py:427` to `433`); the iPhone main camera in
  `PUBLISHED_CAMERA_FOV_DEG` (`services/science/phone_photo_solver.py:96`) has an 84.06 deg diagonal, 42.03 deg to a
  corner; an azimuth error up to 25.97 deg keeps every corner inside the cut. Apple states no limit. The ultra wide
  (120 deg, line 98) keeps its corners only once pointing-4 widens the cut by the hint's uncertainty.
- `MOTION_SAMPLE_OFFSET_LIMIT_SEC = 0.05`: five periods at the 100 Hz Apple calls the usual device-motion maximum
  (`coremotion/getting-processed-device-motion-data`).
- `UNIT_QUATERNION_TOLERANCE = 1e-6`: Apple's quaternion is a unit one; a quaternion further off is not an attitude.
- `GRAVITY_LENGTH_MIN_G = 0.9` and `GRAVITY_LENGTH_MAX_G = 1.1`: Apple gives gravity in G's
  (`coremotion/cmacceleration/x`) and states no magnitude; Core Motion's gravity is the direction of gravity at about
  one g [inference], so a length near 9.81 means metres per second squared were written and one near zero says no
  direction. Mobile-1 measures the real spread on a device, and the band follows it.
- `POINTING_DIMENSION_MAX_PX = 32768` (2^15, about four times the long side of a 48 MP frame at 4:3, 8000 px
  [inference]) and `POINTING_SAMPLE_COUNT_MAX = 360000` (an hour at 100 Hz): bounds, not measurements, so every
  whole number in the record is a safe integer on both sides.

The wire rules (`.lead/briefs/WIRE_RULES.md`) hold for the record, and both files implement them themselves until
the shared wire module is on main: camelCase keys only (the Python mirror reads no field name off the wire), every
number finite and every whole number bounded, the two device labels exact ASCII patterns (so a length is the same in
UTF-16 units and in code points, and a lone surrogate never fits), and a calendar UTC instant with no year 0. The
fixture carries generated wire faults, each with the one verdict both sides give.

The contract stores the device's numbers as given and derives nothing: no pitch, no field of view after zoom, no
camera direction. Those belong to the server units below, where their conventions are tested.

### 3. Tying the record to the photo
- The app hashes the exact `fileDataRepresentation()` bytes with CryptoKit `SHA256` (`cryptokit/sha256`) and sends
  those bytes unchanged; the hash goes into the record, never into the photo (no EXIF or XMP is added).
- The server's `IngestStore` already names a stored file by the SHA-256 of its bytes (`services/ingest/router.py`),
  so the record's `sourceAssetSha256` is the stored asset's key. A record whose hash names no stored file is refused.

### 4. Receiving both on the laptop (unit pointing-2)
- The photo: `POST /api/ingest`, unchanged (the file as the body, its type as Content-Type,
  `services/ingest/router.py:157` to `204`).
- The record: a new `PUT /api/ingest/{sha256}/pointing` with the record as a JSON body, and `GET` of the same path
  [proposed]. Reason: the upload route takes the raw file as its body for every client, the web app included; a
  multipart body or a header would change it and mix the photo's bytes with the record. A separate route keeps the
  photo byte for byte, validates the record against its own schema, and ties them by the hash in the path.
- Rules [proposed]: 404 when no file with that hash is stored; 422 when the record fails the contract or its
  `sourceAssetSha256` differs from the path (the refusal lists paths and the contract's own messages, never a value);
  200 for the same record again; 409 for a different record for the same photo (a record is immutable, like the
  asset), and 409 once a solve job exists for that photo, so a solution never changes under the person. The app
  sends upload, record, then solve.
- Storage: beside the receipt in the ingest store (`ALBEDIAN_INGEST_DIR`, outside git), private like the photo;
  never logged (REQ-IOS-6.1).
- Network: the laptop on the phone's hotspot. The app declares `NSLocalNetworkUsageDescription`
  (`bundleresources/information-property-list/nslocalnetworkusagedescription`) and `NSAllowsLocalNetworking`, which
  lets App Transport Security allow `.local` names and IP addresses; from iOS 17 an IP address is otherwise refused
  by default (`bundleresources/information-property-list/nsapptransportsecurity/nsallowslocalnetworking`) [verified].
  Upload progress counts the bytes sent (REQ-IOS-4.2).
- Contract change for this unit: none beyond `PointingRecordSchema`; the GET returns the stored record.

### 5. From a record to a search hint (unit pointing-4, science)
- The camera axis in the device frame: the back camera looks along the device's minus Z axis, the front camera along
  plus Z [inference: Apple shows the device axes only in an illustration on `coremotion/cmmotionmanager`; mobile-1
  checks it on a device].
- The quaternion's direction (reference frame to device frame, or the reverse) is not stated in
  `coremotion/cmquaternion` or `coremotion/cmattitude` [open]. Mobile-1 fixes it with a device test (the phone flat,
  screen up, top to true north, then the top tilted up), and the server's conversion is tested against that test's
  recorded values.
- With a measured heading: the camera axis turned by the attitude gives the frame centre's altitude and azimuth; the
  photo's up direction gives the roll. With the place and time, `altaz_to_icrs` (`services/science/catalog.py`) gives
  `hint_ra_deg` and `hint_dec_deg` for `solve_and_verify_field` (`services/science/solver.py:409` to `410`).
- The cut radius: today a fixed 68 deg (`solver.py:430`); this unit makes it the frame's half diagonal plus the
  hint's stated uncertainty (the heading accuracy, and the tilt error Apple does not state [open]) [proposed], so a
  wide frame keeps its corners.
- The phone-photo solver takes no centre hint today (component 8). It does take `fov_diagonal_deg`
  (`phone_photo_solver.py:1455` to `1470`), which the record can give: for a rectilinear frame,
  `tan(d / 2) = tan(h / 2) * hypot(w, H) / w / zoom` with `h` the horizontal field of view along the format width
  `w` [inference: Apple says the zoom crops around the centre; the axis "horizontal" refers to is checked in
  mobile-1].
- The hint only narrows the search: held-out verification is unchanged (REQ-IOS-2.1, P1, P2). When a verified WCS
  centre lies outside the hint's stated uncertainty, the WCS wins and a warning records it (REQ-IOS-2.2).

### 6. When stars do not solve: a pose from the record (units pointing-3 and pointing-5)
The pose estimate of spec `predicted-sky-for-every-upload` is lined up from a beacon. A pose from the record has
none, so unit pointing-3 (contract only, both languages, fixtures) needs exactly these changes:
- `PoseEstimateSchema.method` gains `device_attitude`; `beacon` and `beaconCentroidUncertaintyDeg` become optional,
  present exactly when the method is one of the three beacon methods (`POSE_METHOD_BEACONS` in
  `packages/contracts/src/astronomy.ts`, `PoseEstimate` in `services/science/wcs_models.py`).
- `ObservationSolutionSchema` gains `pointingHint` (optional): `sourceAssetSha256` (the record's photo, equal to the
  solution's `sourceSha256`), `usedAs` (`search_hint`, `pose_estimate`, or both), the hint's `centerAltDeg`,
  `centerAzDeg` and `uncertaintyDeg`, and `verifiedCenterOffsetDeg` present only on a `calibrated` solution
  (REQ-IOS-2.2). Rules: `pose_estimate` only with a `poseEstimate` whose method is `device_attitude`; a pose with
  that method only with a `pointingHint` that says so.
- The place and time: a photo from the app carries no GPS, so `SourceAsset.observerLocation` would come from the
  record. `METADATA_CONTAINERS` (`packages/contracts/src/media.ts`, `services/contracts/ingest.py`) gains
  `pointing_record`, and the receipt rule "observerLocation must be the location hint, or absent" gains: or, when
  the file holds no place, the record's allowed place with source `pointing_record`. The same for `capturedAtUTC`
  when the photo holds no recorded time.

Then unit pointing-5 (science): `complete_sky` (`services/science/predicted_sky.py`) takes the stored record. With a
measured heading and no calibrated solve, the status is `approximate` with a `device_attitude` pose: the nominal WCS
from the record, swept over the heading's accuracy, the field of view and zoom, and the roll, its findings
`predicted` and drawn with their ring by the existing web path (REQ-SKY-6). Without a heading, no sensor pose: the
existing layers apply (a beacon pose, else `predicted_only`), with the reason in a warning.

### 7. Heading missing or poor, location off, the phone moving
- No heading (any reason): the record has no attitude. The solve gets no centre hint, only the field of view; no
  sensor pose is drawn. Before the shutter the app says the compass is not ready and sets
  `showsDeviceMovementDisplay`, so the system shows its calibration display when the device needs movement
  (`coremotion/cmmotionmanager/showsdevicemovementdisplay`) [verified].
- Heading accuracy outside the limit: the same, and the record keeps the reported accuracy, so the person is told the
  number.
- Location off or no fix: no place and no heading (true north needs the place). The solve runs blind and the
  solution omits `observerLocation` (REQ-IOS-6.2); no predicted sky, with the existing warning (REQ-SKY-2).
- Reduced accuracy: kept with its stated radius. A place off by 10 km moves the sky by 10 / 6371 rad, about 0.09 deg
  [inference: arithmetic], small against a phone frame.
- A location the system simulated in software: refused as a place (`simulated_by_software`).
- The phone moving: the record keeps the window's largest rotation rate. Pointing-5 widens the pose's uncertainty by
  that rate times the window's length [proposed], and draws no sensor pose above a level set from real captures
  [open]; the app says "hold still" while the gyroscope reads motion [proposed].
- The motion stream not running at the shutter: no valid record can be written (the sample would be outside the
  offset limit), so the app sends the photo alone and says the pointing was not recorded [proposed].

### 8. The open questions of the requirements
1. Night mode for third-party apps [open]. No Night mode control was found in the pages read:
   `AVCapturePhotoSettings` lists `photoQualityPrioritization`, whose techniques include "preserving detail in low
   light", and no Night mode switch; `AVCaptureDevice` has `automaticallyEnablesLowLightBoostWhenAvailable` [verified].
   A search of developer.apple.com found Night mode named only for Apple's Camera app (WWDC21 session 10160) [read in
   the search result's summary, not at the source]. Design: the app takes its own exposures, a custom long exposure
   (`setExposureModeCustom(duration:iso:completionHandler:)`, up to `activeFormat.maxExposureDuration`) or a bracket
   (`AVCapturePhotoBracketSettings`), each frame its own upload with its own record; stacking on the laptop is a later
   spec [proposed].
2. Whether the phone-photo solver accepts the hint: no, today. `solve_phone_photo` takes `fov_diagonal_deg` and no
   centre (`services/science/phone_photo_solver.py:1455`), the triangle solver takes `hint_ra_deg` and
   `hint_dec_deg` (`services/science/solver.py:409`), and `_solve_chain` passes a hint to neither
   (`services/ingest/jobs.py:346`) [verified by reading]. Pointing-4 adds it.
3. Where the app lives and which lane owns it: the lead's to settle; `apps/ios` and a `mobile` lane stay proposed.
4. A RAW-only capture [open, for mobile-1]: Apple says `photoDimensions` then "has zero width and zero height"
   (`avfoundation/avcaptureresolvedphotosettings/photodimensions`, as AUDIT lane D read it). The contract keeps
   positive sizes, so if mobile-1 takes RAW the record needs the RAW frame's own size, named apart.

## Error handling
- The PUT refuses a record that fails the contract with 422 and the contract's own messages; nothing is stored.
- A record for a photo not stored: 404. A different record for a stored one, or after a solve: 409.
- `photoDimensionsPx` that match neither order of the stored file's width and height: 422, since the record
  describes another capture [proposed; the order follows the device orientation, per Apple's page].
- The laptop unreachable: the app keeps the photo and its record on the phone and says so (REQ-IOS-4.3).
- Any exception while turning a record into a hint or a pose: the solve runs without it, and a warning says the
  pointing could not be used, so a bad record never blocks a solve (the pattern of `complete_sky`'s fallback).

## Testing strategy
- This unit: both contract files judged on the shared fixture (valid samples, one named fault per rule, the
  contract's own words), P4 under 20 seeds in each language, and mutants on every refinement and limit.
- Pointing-2: route tests (stored, refused, conflict), the record never in a log line, P3 through the upload path.
- Pointing-3: the contract parity tests extended as sky-1 did (fixtures, PBT both sides).
- Pointing-4 and 5: hypothesis properties P1 and P2 over generated records; the pose from synthetic records at the
  synthetic moment and open-ocean place; mutants on the cut radius, the sweep and the status rule.
- Mobile-1: XCTest of record building from recorded sensor samples, and on-device checks of each [open] item above
  (axes, quaternion direction, timebase, Core Location against Core Motion azimuth, EXIF time in the photo).
- Real data: the owner's first capture with the app through a lead-held script that prints counts and statuses only.
