# Tasks: iOS Capture Companion

Each task is one unit: one commit, tests first, mutants on its risky lines, properties under 20 seeds, all six gates,
an independent `albedian-auditor` verdict, then the lead lands it. Server units are `pointing-<n>`; app units are
`mobile-<n>`, in the `mobile` lane that owns `apps/ios` (open question 3 of `requirements.md`, settled by the lead on
2026-11-11). Design: `design.md` in this folder.

- [x] 0. The owner confirms `requirements.md` in this folder (2026-11-11, `.lead/APPROVALS.md`).

- [x] 1. pointing-1: the design, these tasks and the pointing record contract (REQ-IOS-7; P4)
  - `design.md`, `tasks.md`; `PointingRecordSchema` in `packages/contracts/src/pointing.ts` (exported from the
    package index), `PointingRecord` in `services/contracts/pointing.py`, the shared fixture
    `tests/fixtures/pointing_contract.json`, `tests/integration/test_pointing_contract.py`,
    `packages/contracts/src/__tests__/pointing.test.ts`, the schema name in `scripts/verify-contracts.mjs`.
  - Landed as `ff8f2630` (audits: FAIL three times, then PASS on the fourth version).
  - _Requirements: REQ-IOS-1.2, 1.3, REQ-IOS-2 (the evidence statement), REQ-IOS-6.2, REQ-IOS-7.1_

- [x] 2. pointing-2: receive the record (REQ-IOS-4.1, REQ-IOS-6.1; P3)
  - `PUT` and `GET /api/ingest/{sha256}/pointing` in `services/ingest/router.py`, the record stored beside the
    receipt by `services/ingest/store.py`; the rules of design component 4 (404, 422, 409, 200); no record value in
    any log or error. P3: the stored bytes and SHA-256 equal the uploaded ones, with a record and without.
  - Split in three. pointing-2a, the route's reply contract (`packages/contracts/src/pointing-reply.ts`,
    `services/contracts/pointing_reply.py`, the shared fixture `tests/fixtures/pointing_reply_contract.json`), landed
    as `61fa49b2` (audits: FAIL, then PASS on the second version). pointing-2a2 adds the refusal for a record sent
    once a solve job exists. pointing-2b: the two routes (201 for a record stored now, 200 for the same bytes again),
    the record's exact bytes in `services/ingest/store_pointing.py`, its `BODY_LIMITS` row, and
    `tests/integration/test_pointing_route.py`.
  - _Requirements: REQ-IOS-1.1, REQ-IOS-4.1, REQ-IOS-6.1_

- [x] 3. pointing-3: the contract changes a pose from the record needs (design component 6)
  - `device_attitude` in `PoseEstimateSchema` with `beacon` and `beaconCentroidUncertaintyDeg` tied to the beacon
    methods; `ObservationSolutionSchema.pointingHint`; the `pointing_record` metadata container and the receipt
    rule for a place or time from the record. Both languages, fixtures, PBT both sides, as sky-1 did.
  - As decided on 2026-11-18: no metadata container and no receipt change; the solution's `pointingHint` says where
    its time and place came from (`timeFrom`, `placeFrom`). The web's phrase for a `device_attitude` pose
    (`apps/web/src/services/skyLayerWords.ts`) is worded by UIUX and landed by WEB-UX in the same batch.
  - pointing-3b (2026-11-19): `usedAs` gains `moment`, said exactly when the solution's time or place came from the
    record, so a record with a place and no heading can still say it lent the photo its time and place.
    v2: the hinted centre and its uncertainty are given exactly when the hint was a search hint or the pose, and
    the verified centre's offset from it exactly on a calibrated solution whose hint gives one.
  - pointing-moment (2026-11-19): the solve reads the stored record at its start (`services/ingest/jobs.py`); where
    the file holds no recorded time it takes the record's, where it holds no place the record's allowed place, for
    the searches and the predicted sky, and the hint says `moment` with `timeFrom` and `placeFrom`; the record
    lends nothing when the solution would still have no place; a record that is not this photo's is not used, with
    one warning. Tests: `tests/integration/test_pointing_moment.py`.
  - pointing-moment v2 (2026-11-19, after the read): each predicted item names the phone's pointing record for a
    lent time or place (`services/science/predicted_sky.py`), one warning says so, and the record place's stated
    accuracy, full or reduced, joins every stated uncertainty (its words [proposed] for the design lane).
  - _Requirements: REQ-IOS-2.2, REQ-IOS-6.2_

- [x] 4. pointing-4: the record as a search hint (REQ-IOS-2.1, 2.2; P1, P2)
  - Turn a measured heading into `hint_ra_deg` and `hint_dec_deg` with a stated uncertainty; the triangle solver's
    cut radius from that uncertainty and the frame's half diagonal; a centre hint and the record's field of view for
    the phone-photo solver; `_solve_chain` passes both; the WCS wins over the hint, with a warning.
  - Waits for the device checks of mobile-1 (the axes, the quaternion direction) and mobile-2 (the timebase).
  - As built (2026-11-19): to the documented conventions, each device check [open] until mobile-1's recorded values
    are compared (`services/science/record_hint.py`, `tests/science/test_record_hint.py`,
    `tests/integration/test_pointing_search_hint.py`). v2 (AUDIT's read of 12:58): when the hinted searches do not
    calibrate, the chain runs once more without the hint under the same bound, so the verified WCS wins over a hint
    outside its uncertainty, with the warning (REQ-IOS-2.2).
  - _Requirements: REQ-IOS-2.1, 2.2, 2.3_

- [x] 5. pointing-5: a pose from the record when stars do not solve (design component 6)
  - `complete_sky` with a stored record: an `approximate` solution with a `device_attitude` pose swept over the
    heading accuracy, field of view, zoom and roll, widened by the motion window; no sensor pose without a heading.
  - As built (2026-11-19): before any Moon pose (the lead's answer of 12:20), 21 swept WCS (the heading and the tilt
    at once to four corners, the roll either way, the record's field of view at its zoom 5 percent either way
    [proposed]); no pose for the front camera, a mirrored frame, a stored frame of another size than the record's
    photo, or no field of view; the stored axes of a back-camera photo [open] until mobile-2's check
    (`services/science/record_hint.py`, `services/science/predicted_sky.py`, `tests/science/test_record_pose.py`).
    Unsolved on real data: no photo with a pointing record is stored yet.
  - _Requirements: REQ-IOS-2.3; spec `predicted-sky-for-every-upload` REQ-SKY-3 to 6_

- [x] 6. mobile-0: the app's project, these mobile tasks and the tested core
  - `apps/ios/`: an Xcode project for an iPhone app (iOS 26.0, simulator builds only, no signing team), the Swift
    package `AlbedianCaptureCore` (the pointing record built from sensor readings, its wire JSON, a third reading of
    the shared fixture, the upload client for the proposed `PUT /api/ingest/{sha256}/pointing`), one plain screen in
    system default style to run the pipeline by hand with sample values; the `mobile` row in `AGENTS.md`.
  - Needs first: pointing-1 (the contract and its fixture).
  - Only on an iPhone: nothing; this unit reads no sensor and no camera.
  - Landed as `893a074c` (audits: FAIL six times, then PASS on the seventh version).
  - _Requirements: REQ-IOS-1.2, 1.3, REQ-IOS-4.2 (bytes sent), REQ-IOS-6.1 (no device name, nothing logged),
    REQ-IOS-7.1 (P4 in Swift)_

- [ ] 7. mobile-1: the sensors, and their checks on a device
  - Core Motion device motion at 100 Hz in `xTrueNorthZVertical` (else the frame there is, and no heading), with
    `showsDeviceMovementDisplay`; Core Location heading (its `headingOrientation` from the interface orientation)
    and place (when in use, `accuracyAuthorization`, `sourceInformation`); a ring buffer of samples for the window;
    all mapped onto the core's `SensorCapture`, behind protocols so the mapping is tested on the Mac with recorded
    synthetic readings (`SensorRecorder`, `MotionRing`, `SensorMapping`; the app's `CoreMotionSource` and
    `CoreLocationSource`). The check screen and the usage descriptions come with mobile-2 (the lead's split of
    2026-11-12), so the device checks below are run once mobile-2 is on the phone.
  - Needs first: mobile-0.
  - Only on an iPhone (with the owner): the back camera along minus Z; the quaternion's direction (flat, screen up,
    top to true north, then tilted up); Core Location's true heading against the attitude's azimuth of the
    `headingOrientation` axis; gravity's length at rest, for the band; the motion rate delivered. Each result goes
    to the design as counts and differences only, never a place or a time.
  - The code landed as `74c406f5` (audits: FAIL, then PASS on the second version). Missing: the device checks
    above; none is recorded in `design.md` (components 1 and 5 still say [open] or [inference] for each).
  - _Requirements: REQ-IOS-1.2, 1.3, REQ-IOS-6.2_

- [ ] 8. mobile-2: the camera, and the photo exactly as written
  - `AVCaptureSession` with a physical back camera, `AVCapturePhotoOutput` in HEIC or JPEG, the bytes of
    `fileDataRepresentation()` kept unchanged and hashed, nothing added to them; the photo's timestamp moved from
    the session's `synchronizationClock` to the host clock; the field of view (with distortion correction, and the
    iOS 26 per-aspect-ratio call for dynamic formats), zoom, lens and photo size read at the shutter; content-aware
    distortion correction left off; the record built at the shutter; photo and record kept in the app's own storage
    until sent (REQ-IOS-4.3). P3 on the phone: the stored bytes and SHA-256 equal the bytes the camera wrote. A
    check screen in system default style (the sensors' readings, then the camera's), and the usage descriptions
    for the camera, motion and location (`PhotoClock`, `CaptureStore`, `CheckReadout` and the uploader's send of a
    kept record in the core; the app's `CameraSession`, `CaptureCheckScreen` and `CaptureButton`). The iOS 26 field of
    view per aspect ratio is not called yet [open].
  - Needs first: mobile-1; the owner's yes on the capture screen's look (reference board) before it is styled.
  - Only on an iPhone: the simulator has no camera. Checks: the host clock and `CMLogItem.timestamp` count the same
    seconds; whether `AVCapturePhoto.metadata` holds an EXIF time and its offset; which axis "horizontal" field of
    view refers to; `photoDimensions` for RAW (zero by zero, per Apple); what the HEIC file holds (no GPS block).
  - The code landed as `9b087d1c` (the sixth version's audit was a FAIL on tests in `OffMainActorTests.swift`
    alone; the lead landed it in the batch of 2026-11-18). Missing: the device checks above, none recorded in
    `design.md`, and the iOS 26 field of view per aspect ratio, still [open].
  - _Requirements: REQ-IOS-1.1, 1.2, REQ-IOS-4.3, REQ-IOS-6.1_

- [ ] 9. mobile-3: pairing and sending
  - The laptop's address typed once and kept on the phone; the local network permission; each stored capture sent
    with the core's uploader, its state kept with its reason, retried only when the person asks; a capture stays on
    the phone until the laptop has it (REQ-IOS-4.3).
  - Needs first: mobile-2, and pointing-2 on main (until then the record's route answers 404 or 405 and the app
    says so).
  - Only on an iPhone: the phone's hotspot with the laptop joined; the local network prompt; plain http to an IP
    address under `NSAllowsLocalNetworking`; a full-size HEIC upload's byte counts.
  - Not on `main` as written. On `main`: `NSLocalNetworkUsageDescription` and `NSAllowsLocalNetworking` in
    `apps/ios/AlbedianCapture-Info.plist`, the uploader's send of a kept capture in the core, and the transport's
    hardening (`1cf24ed6`, mobile-fix-1). The unit reported and landed under the name mobile-3 (`69dca733`, the
    approved controls, logo, Geist and Phosphor icons) is not this task. Missing: the address is not kept on the
    phone (`apps/ios/AlbedianCapture/PipelineModel.swift` starts it empty each launch), and no screen sends the
    stored captures, keeps each one's state with its reason, or retries on the person's request.
  - Built (mobile-6 and mobile-3p, 2026-11-18): the address typed once and kept, the send with its states, can't reach
    the laptop with Try again, each capture's send state kept with its reason (`SendLedger`). Not built: seeing and
    sending again captures kept from earlier sessions, which has no design record (asked of the lead).
  - _Requirements: REQ-IOS-4.1, 4.2 (upload), 4.3_

- [x] 10. mobile-4: the solve and the result
  - `POST /api/ingest/{sha256}/solve`, the job's progress from its own denominators or elapsed time, the status and
    the counts by evidence class as the server reports them, the reveal opened in the web app. Swift readers only for
    the job and solution fields shown, judged on the existing contract fixtures as a further reading.
  - Built: the solve, its job read beside it, the status and counts by evidence class (mobile-4 v2); "Open" on the
    result shows `<paired address>/?upload=<sha256>` in the system's Safari view (mobile-4b).
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
  - Built and on main: positions asked from the laptop (the sky-context route, skycontext-1 to 4), the rings and
    the source line with the heading's accuracy (mobile-5), names by D20 (aimnames-1), every ring wholly inside the
    preview (drift-1); the capture coach under it (D29, coach-0 to 3). Left open: iPhone checks 25 to 29.
  - tagsweep-m (AUDIT-C's tag sweep of 21 Nov, items 10 and 11): the rings, their tag and their source line come
    from one read a frame, and no ring stands once the line under the preview says there is no place or heading.
    tagsweep-m2: the source line stands from its rings' first frame (only the line saying why words in). aimexit-m
    (the lead, 21 Nov 13:48): at the press, and on coming back, the rings and the rest fade on the stage's timing
    while the tag and the source line stay at full until the rings' last frame.
  - _Requirements: REQ-IOS-3.1, 3.2_

- [x] 12. mobile-6: the look from the reference board
  - The screens styled from the board the owner says yes to (the owner asked on 2026-11-11 for clean typography,
    no extra text and clean colour); no change in behaviour.
  - Needs first: the design-mobile lane's board and the owner's yes.
  - Built from the records: home with Credits, Credits, Connect, aiming without the overlay, after the shot, can't
    reach the laptop, the result (its "Open" and the Credits links are mobile-4b). Not built: the
    states with no record (a refused send, a failed job, a bad address, no camera), asked of the lead on
    2026-11-18; the box is ticked once those land.
  - Landed (19 Nov): a failed job and a refused photo (D7, mobile-8), a wrong address (D11), the camera refused and
    the sensors missing (D8), kept photos (D13), a queued solve (D32); the zero count left out of the result
    (result-0). Left open: iPhone check 20 (outdoors at night).
  - Only on an iPhone: how it reads outdoors at night (dark adaptation, brightness).
  - _Requirements: REQ-IOS-3.2 (the labels stay legible)_

- [ ] 13. mobile-7: night exposures (open question 1 of the design)
  - A custom long exposure or a bracket, each frame its own photo and record; stacking stays on the laptop, in a
    later spec.
  - Settled 2026-11-18 (design 8.1): one long exposure at `maxExposureDuration`, ISO from the meter within the
    format's range, one photo and one record, with the approved hold-still screen; no set of frames.
  - Needs first: mobile-2; that later spec.
  - Only on an iPhone: every part.
  - Built and on main (e43836c4): one long exposure at the format's longest duration with the approved hold-still
    screen. Left open: iPhone checks 21 to 24 and 31.
  - _Requirements: REQ-IOS-1.1, 1.2_

- [x] 14. mobile-8: the iPhone after Send (UIUX-iphone-solve-APPROVED and -READY, the owner's yes of 2026-11-18)
  - The wait with the job's current step and its own count, a result with no star matched in the web's own sentence
    with its counts, and a failed job (or a refused photo) in its own sentence with "Try again".
  - Only on an iPhone: the wait's steps over the hotspot on a full-size photo.
  - _Requirements: REQ-IOS-4.2 (solve), REQ-IOS-5.1_

- [ ] 15. lockon-m: the lock-on on the iPhone's wait, as UIUX-iphone-lockon-READY draws it
  - Early amber rings over the photo while the solve runs, from the job's predicted sky (platform-10b) placed through
    its pose's own WCS, or the phone's own record projected through the sky context until it arrives; C13's source
    line beside them; on a calibrated solve each matched ring travels onto its detection, the rest leave, then the
    result (MOTION section 5); the swap to the laptop's sky in one correction (ruled 19 Nov 18:05).
  - Built and on main: lockon-m1 to m5f, solread-1b, tagrule-1, swaparrive-1. Shown end to end on a real solve in
    the simulator (.lead/kiro/reports/MOBILE/lockon-m5.md).
  - tagsweep-m (AUDIT-C's tag sweep of 21 Nov, items 5 and 9, as the lead ruled them): the source line stays at full
    through the pass until the early rings' last frame, and on a swap it keeps the phone's words until the phone's
    rings have gone, at the end of the shared rings' travel.
  - Only on an iPhone: the early rings against the real sky and the pass (iPhone checks 35 and 36).
  - _Requirements: REQ-IOS-3.1, 5.1_
