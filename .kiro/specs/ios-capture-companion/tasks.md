# Tasks: iOS Capture Companion

Each task is one unit: one commit, tests first, mutants on its risky lines, properties under 20 seeds, all six gates,
an independent `albedian-auditor` verdict, then the lead lands it. Server units are `pointing-<n>`; app units are
`mobile-<n>`, in the `mobile` lane that owns `apps/ios` (open question 3 of `requirements.md`, settled by the lead on
2026-11-11). Design: `design.md` in this folder.

- [x] 0. The owner confirms `requirements.md` in this folder (2026-11-11, `.lead/APPROVALS.md`).

- [ ] 1. pointing-1: the design, these tasks and the pointing record contract (REQ-IOS-7; P4)
  - `design.md`, `tasks.md`; `PointingRecordSchema` in `packages/contracts/src/pointing.ts` (exported from the
    package index), `PointingRecord` in `services/contracts/pointing.py`, the shared fixture
    `tests/fixtures/pointing_contract.json`, `tests/integration/test_pointing_contract.py`,
    `packages/contracts/src/__tests__/pointing.test.ts`, the schema name in `scripts/verify-contracts.mjs`.
  - Built on branch `claude/lane-pointing-4f7fd0`; not yet audited.
  - _Requirements: REQ-IOS-1.2, 1.3, REQ-IOS-2 (the evidence statement), REQ-IOS-6.2, REQ-IOS-7.1_

- [ ] 2. pointing-2: receive the record (REQ-IOS-4.1, REQ-IOS-6.1; P3)
  - `PUT` and `GET /api/ingest/{sha256}/pointing` in `services/ingest/router.py`, the record stored beside the
    receipt by `services/ingest/store.py`; the rules of design component 4 (404, 422, 409, 200); no record value in
    any log or error. P3: the stored bytes and SHA-256 equal the uploaded ones, with a record and without.
  - _Requirements: REQ-IOS-1.1, REQ-IOS-4.1, REQ-IOS-6.1_

- [ ] 3. pointing-3: the contract changes a pose from the record needs (design component 6)
  - `device_attitude` in `PoseEstimateSchema` with `beacon` and `beaconCentroidUncertaintyDeg` tied to the beacon
    methods; `ObservationSolutionSchema.pointingHint`; the `pointing_record` metadata container and the receipt
    rule for a place or time from the record. Both languages, fixtures, PBT both sides, as sky-1 did.
  - _Requirements: REQ-IOS-2.2, REQ-IOS-6.2_

- [ ] 4. pointing-4: the record as a search hint (REQ-IOS-2.1, 2.2; P1, P2)
  - Turn a measured heading into `hint_ra_deg` and `hint_dec_deg` with a stated uncertainty; the triangle solver's
    cut radius from that uncertainty and the frame's half diagonal; a centre hint and the record's field of view for
    the phone-photo solver; `_solve_chain` passes both; the WCS wins over the hint, with a warning.
  - Waits for the device checks of mobile-1 (the axes, the quaternion direction) and mobile-2 (the timebase).
  - _Requirements: REQ-IOS-2.1, 2.2, 2.3_

- [ ] 5. pointing-5: a pose from the record when stars do not solve (design component 6)
  - `complete_sky` with a stored record: an `approximate` solution with a `device_attitude` pose swept over the
    heading accuracy, field of view, zoom and roll, widened by the motion window; no sensor pose without a heading.
  - _Requirements: REQ-IOS-2.3; spec `predicted-sky-for-every-upload` REQ-SKY-3 to 6_

- [ ] 6. mobile-0: the app's project, these mobile tasks and the tested core
  - `apps/ios/`: an Xcode project for an iPhone app (iOS 26.0, simulator builds only, no signing team), the Swift
    package `AlbedianCaptureCore` (the pointing record built from sensor readings, its wire JSON, a third reading of
    the shared fixture, the upload client for the proposed `PUT /api/ingest/{sha256}/pointing`), one plain screen in
    system default style to run the pipeline by hand with sample values; the `mobile` row in `AGENTS.md`.
  - Needs first: pointing-1 (the contract and its fixture).
  - Only on an iPhone: nothing; this unit reads no sensor and no camera.
  - _Requirements: REQ-IOS-1.2, 1.3, REQ-IOS-4.2 (bytes sent), REQ-IOS-6.1 (no device name, nothing logged),
    REQ-IOS-7.1 (P4 in Swift)_

- [ ] 7. mobile-1: the sensors, and their checks on a device
  - Core Motion device motion at 100 Hz in `xTrueNorthZVertical` (else the frame there is, and no heading), with
    `showsDeviceMovementDisplay`; Core Location heading (its `headingOrientation` from the interface orientation)
    and place (when in use, `accuracyAuthorization`, `sourceInformation`); a ring buffer of samples for the window;
    all mapped onto the core's `SensorCapture`, behind protocols so the mapping is tested on the Mac with recorded
    synthetic readings. A check screen in system default style. Usage descriptions for motion and location.
  - Needs first: mobile-0.
  - Only on an iPhone (with the owner): the back camera along minus Z; the quaternion's direction (flat, screen up,
    top to true north, then tilted up); Core Location's true heading against the attitude's azimuth of the
    `headingOrientation` axis; gravity's length at rest, for the band; the motion rate delivered. Each result goes
    to the design as counts and differences only, never a place or a time.
  - _Requirements: REQ-IOS-1.2, 1.3, REQ-IOS-6.2_

- [ ] 8. mobile-2: the camera, and the photo exactly as written
  - `AVCaptureSession` with a physical back camera, `AVCapturePhotoOutput` in HEIC or JPEG, the bytes of
    `fileDataRepresentation()` kept unchanged and hashed, nothing added to them; the photo's timestamp moved from
    the session's `synchronizationClock` to the host clock; the field of view (with distortion correction, and the
    iOS 26 per-aspect-ratio call for dynamic formats), zoom, lens and photo size read at the shutter; content-aware
    distortion correction left off; the record built at the shutter; photo and record kept in the app's own storage
    until sent (REQ-IOS-4.3). P3 on the phone: the stored bytes and SHA-256 equal the bytes the camera wrote.
  - Needs first: mobile-1; the owner's yes on the capture screen's look (reference board) before it is styled.
  - Only on an iPhone: the simulator has no camera. Checks: the host clock and `CMLogItem.timestamp` count the same
    seconds; whether `AVCapturePhoto.metadata` holds an EXIF time and its offset; which axis "horizontal" field of
    view refers to; `photoDimensions` for RAW (zero by zero, per Apple); what the HEIC file holds (no GPS block).
  - _Requirements: REQ-IOS-1.1, 1.2, REQ-IOS-4.3, REQ-IOS-6.1_

- [ ] 9. mobile-3: pairing and sending
  - The laptop's address typed once and kept on the phone; the local network permission; each stored capture sent
    with the core's uploader, its state kept with its reason, retried only when the person asks; a capture stays on
    the phone until the laptop has it (REQ-IOS-4.3).
  - Needs first: mobile-2, and pointing-2 on main (until then the record's route answers 404 or 405 and the app
    says so).
  - Only on an iPhone: the phone's hotspot with the laptop joined; the local network prompt; plain http to an IP
    address under `NSAllowsLocalNetworking`; a full-size HEIC upload's byte counts.
  - _Requirements: REQ-IOS-4.1, 4.2 (upload), 4.3_

- [ ] 10. mobile-4: the solve and the result
  - `POST /api/ingest/{sha256}/solve`, the job's progress from its own denominators or elapsed time, the status and
    the counts by evidence class as the server reports them, the reveal opened in the web app. Swift readers only for
    the job and solution fields shown, judged on the existing contract fixtures as a further reading.
  - Needs first: mobile-3; pointing-3 if the result shows the pointing hint.
  - Only on an iPhone: nothing beyond mobile-3's network path.
  - _Requirements: REQ-IOS-4.2 (solve), REQ-IOS-5.1, 5.2_

- [ ] 11. mobile-5: the live aim overlay, labelled predicted
  - Catalog stars and planets placed from time, place and attitude while aiming, each drawn and labelled
    `predicted`, never as detected; the source (catalog, ephemeris) and the pointing accuracy on screen.
  - Needs first: mobile-1 and mobile-2's device checks (the projection rests on them); a decision [open] on where
    the positions come from (a star subset and an ephemeris on the phone, each tested against the laptop's, or
    positions asked from the laptop); the owner's yes on the look.
  - Only on an iPhone: the overlay against the real sky, with the accuracy it states.
  - _Requirements: REQ-IOS-3.1, 3.2_

- [ ] 12. mobile-6: the look from the reference board
  - The screens styled from the board the owner says yes to (the owner asked on 2026-11-11 for clean typography,
    no extra text and clean colour); no change in behaviour.
  - Needs first: the design-mobile lane's board and the owner's yes.
  - Only on an iPhone: how it reads outdoors at night (dark adaptation, brightness).
  - _Requirements: REQ-IOS-3.2 (the labels stay legible)_

- [ ] 13. mobile-7: night exposures (open question 1 of the design)
  - A custom long exposure or a bracket, each frame its own photo and record; stacking stays on the laptop, in a
    later spec.
  - Needs first: mobile-2; that later spec.
  - Only on an iPhone: every part.
  - _Requirements: REQ-IOS-1.1, 1.2_
