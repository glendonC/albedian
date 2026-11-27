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
    as `61fa49b2` (audits: FAIL, then PASS on the second version). pointing-2a2 (`cbb32954`) adds the refusal for a
    record sent once a solve job exists. pointing-2b (`f5472c89`): the two routes (201 for a record stored now, 200 for
    the same bytes again), the record's exact bytes in `services/ingest/store_pointing.py`, its `BODY_LIMITS` row, and
    `tests/integration/test_pointing_route.py`.
  - _Requirements: REQ-IOS-1.1, REQ-IOS-4.1, REQ-IOS-6.1_

- [x] 3. pointing-3: the contract changes a pose from the record needs (design component 6)
  - `device_attitude` in `PoseEstimateSchema` with `beacon` and `beaconCentroidUncertaintyDeg` tied to the beacon
    methods; `ObservationSolutionSchema.pointingHint`; the `pointing_record` metadata container and the receipt
    rule for a place or time from the record. Both languages, fixtures, PBT both sides, as sky-1 did.
  - As decided on 2026-11-18 (`5c8d35e7`): no metadata container and no receipt change; the solution's `pointingHint`
    says where its time and place came from (`timeFrom`, `placeFrom`). The web's phrase for a `device_attitude` pose
    (`apps/web/src/services/skyLayerWords.ts`) is worded by UIUX and landed by WEB-UX in the same batch.
  - pointing-3b (2026-11-19, `ea538f98`): `usedAs` gains `moment`, said exactly when the solution's time or place came
    from the record, so a record with a place and no heading can still say it lent the photo its time and place.
    v2: the hinted centre and its uncertainty are given exactly when the hint was a search hint or the pose, and
    the verified centre's offset from it exactly on a calibrated solution whose hint gives one.
  - pointing-moment (2026-11-19, `613848a7`): the solve reads the stored record at its start (`services/ingest/jobs.py`);
    where the file holds no recorded time it takes the record's, where it holds no place the record's allowed place, for
    the searches and the predicted sky, and the hint says `moment` with `timeFrom` and `placeFrom`; the record
    lends nothing when the solution would still have no place; a record that is not this photo's is not used, with
    one warning. Tests: `tests/integration/test_pointing_moment.py`.
  - pointing-moment v2 (2026-11-19, in `613848a7`): each predicted item names the phone's pointing record for a
    lent time or place (`services/science/predicted_sky.py`), one warning says so, and the record place's stated
    accuracy, full or reduced, joins every stated uncertainty; c13-words (`aa392e92`) puts those warnings and item
    limitations in the design lane's plain words, including a lent time beside the file's own place with no bound where
    the place states no accuracy.
  - moment-blind (`6ac273c2`) and blind-words (`bbc3b916`): when the searches at a lent moment do not calibrate,
    `_solve_chain` runs the whole-sky blind search once more so a lent moment never costs a photo the solve it gets
    alone (verified across all sixty public photos in `.lead/kiro/reports/SCIENCE-INGEST/moment-blind-60.md`), and
    warns in the design lane's plain words (`BLIND_LENT_WORDS`) that the stars alone lined the photo up while what is
    predicted is drawn for what the phone recorded.
  - _Requirements: REQ-IOS-2.2, REQ-IOS-6.2_

- [x] 4. pointing-4: the record as a search hint (REQ-IOS-2.1, 2.2; P1, P2)
  - Turn a measured heading into `hint_ra_deg` and `hint_dec_deg` with a stated uncertainty; the triangle solver's
    cut radius from that uncertainty and the frame's half diagonal; a centre hint and the record's field of view for
    the phone-photo solver; `_solve_chain` passes both; the WCS wins over the hint, with a warning.
  - Waits for the device checks of mobile-1 (the axes, the quaternion direction) and mobile-2 (the timebase).
  - As built (2026-11-19, `72e8dcf3`): to the documented conventions, each device check [open] until mobile-1's
    recorded values are compared (`services/science/record_hint.py`, `tests/science/test_record_hint.py`,
    `tests/integration/test_pointing_search_hint.py`). v2 (`df019603`): when the hinted searches do not
    calibrate, the chain runs once more without the hint under the same bound, so the verified WCS wins over a hint
    outside its uncertainty, with the warning (REQ-IOS-2.2). turned-cut (`6c5bf366`) searches a photo stored turned
    upright in the picture as shown in the phone solver's first pass; p4-notes (`258837ee`) pins in
    `test_pointing_search_hint.py` that the triangle solver cuts its catalog at `triangle_radius_deg` and that
    `HINT_OUTSIDE` fires just past `uncertainty_deg` and not inside it.
  - _Requirements: REQ-IOS-2.1, 2.2, 2.3_

- [x] 5. pointing-5: a pose from the record when stars do not solve (design component 6)
  - `complete_sky` with a stored record: an `approximate` solution with a `device_attitude` pose swept over the
    heading accuracy, field of view, zoom and roll, widened by the motion window; no sensor pose without a heading.
  - As built (2026-11-19, `aa62de24`, words `ceaee030`): before any Moon pose (the lead's answer of 12:20), 21 swept
    WCS (the heading and the tilt at once to four corners, the roll either way, the record's field of view at its zoom
    5 percent either way [proposed]); no pose for the front camera, a mirrored frame, a stored frame of another size
    than the record's photo, or no field of view; the stored axes of a back-camera photo [open] until mobile-2's check
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
  - Only on an iPhone (with the owner) [open: real iPhone check, `.lead/kiro/reports/MOBILE/iphone-checks.md` steps 1
    to 7]: the back camera along minus Z; the quaternion's direction (flat, screen up, top to true north, then tilted
    up); Core Location's true heading against the attitude's azimuth of the `headingOrientation` axis; gravity's
    length at rest, for the band; the motion rate delivered. Each result goes to the design as counts and differences
    only, never a place or a time.
  - The code landed as `74c406f5` (audits: FAIL, then PASS on the second version), with `SensorRecorder.recentMotion`
    and `CoachSteadiness.moving` wrapper coverage pinned in `93291fc2` (coach-gap-1). Missing [open: real iPhone
    check]: the device checks above; none is recorded in `design.md` (components 1 and 5 still say [open] or
    [inference] for each).
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
    view per aspect ratio is not called yet [open: real iPhone check].
  - Needs first: mobile-1; the owner's yes on the capture screen's look (reference board) before it is styled.
  - Only on an iPhone [open: real iPhone check, `.lead/kiro/reports/MOBILE/iphone-checks.md` steps 6 to 8 and 21 to
    24]: the simulator has no camera. Checks: the host clock and `CMLogItem.timestamp` count the same seconds; whether
    `AVCapturePhoto.metadata` holds an EXIF time and its offset; which axis "horizontal" field of view refers to;
    `photoDimensions` for RAW (zero by zero, per Apple); what the HEIC file holds (no GPS block).
  - The code landed as `9b087d1c` (the sixth version's audit was a FAIL on tests in `OffMainActorTests.swift`
    alone; the lead landed it in the batch of 2026-11-18). Missing [open: real iPhone check]: the device checks above,
    none recorded in `design.md`, and the iOS 26 field of view per aspect ratio.
  - _Requirements: REQ-IOS-1.1, 1.2, REQ-IOS-4.3, REQ-IOS-6.1_

- [ ] 9. mobile-3: pairing and sending
  - The laptop's address typed once and kept on the phone; the local network permission; each stored capture sent
    with the core's uploader, its state kept with its reason, retried only when the person asks; a capture stays on
    the phone until the laptop has it (REQ-IOS-4.3).
  - Needs first: mobile-2, and pointing-2 on main (until then the record's route answers 404 or 405 and the app
    says so).
  - Only on an iPhone [open: real iPhone check, `.lead/kiro/reports/MOBILE/iphone-checks.md` steps 9 to 16]: the
    phone's hotspot with the laptop joined; the local network prompt; plain http to an IP address under
    `NSAllowsLocalNetworking`; a full-size HEIC upload's byte counts.
  - Built and on `main`: `NSLocalNetworkUsageDescription` and `NSAllowsLocalNetworking` in
    `apps/ios/AlbedianCapture-Info.plist`, the uploader's send of a kept capture in the core, and transport hardening
    (`1cf24ed6`, mobile-fix-1); the address typed once and kept (`48912b79`, mobile-6 `PairingKeeper`; wrong-address
    line `6b249e91` D11; printed `http://` scheme accepted without doubling `1de143ba` and `87c2650d`), can't reach the
    laptop with Try again (`48912b79`), each capture's send state kept across launches with its reason (`195203c4`,
    mobile-3p `SendLedger`), and seeing and sending captures kept from earlier sessions oldest first (`2c16057e`, D13).
    (The earlier unit named mobile-3 at `69dca733` landed the approved controls, logo, Geist and Phosphor icons.) Left
    open [open: real iPhone check]: hotspot, local network prompt and full-size HEIC upload checks on hardware.
  - _Requirements: REQ-IOS-4.1, 4.2 (upload), 4.3_

- [x] 10. mobile-4: the solve and the result
  - `POST /api/ingest/{sha256}/solve`, the job's progress from its own denominators or elapsed time, the status and
    the counts by evidence class as the server reports them, the reveal opened in the web app. Swift readers only for
    the job and solution fields shown, judged on the existing contract fixtures as a further reading.
  - Built and on `main`: the solve, its job read beside it, the status and counts by evidence class (`2944948e`,
    mobile-4 v2; runner job contract reader `51f2aa79`; `predictedSkyAtUTC` reader `86312b1b`; `measuredAtUTC`
    reader, runner-reader-measured-one; person-alignment job request reader `197d0aaf`; shared solution fixture sample counting `f045e514`; verified TAN WCS and held-out gates
    on `calibrated`, `FINDING_CLASS_EVIDENCE` class invariants across all four classes, and closed `poseEstimate`
    methods `767f9b01b5`, solread-2); "Open" on the result shows `<paired address>/?upload=<sha256>` in the system's
    Safari view (`d6e33f53`, mobile-4b).
  - Needs first: mobile-3; pointing-3 if the result shows the pointing hint.
  - Only on an iPhone: nothing beyond mobile-3's network path.
  - _Requirements: REQ-IOS-4.2 (solve), REQ-IOS-5.1, 5.2_

- [ ] 11. mobile-5: the live aim overlay, labelled predicted
  - Catalog stars and planets placed from time, place and attitude while aiming, each drawn and labelled
    `predicted`, never as detected; the source (catalog, ephemeris) and the pointing accuracy on screen.
  - Needs first: mobile-1 and mobile-2's device checks (the projection rests on them); a decision on where the
    positions come from (settled: asked from the laptop); the owner's yes on the look.
  - Only on an iPhone [open: real iPhone check, `.lead/kiro/reports/MOBILE/iphone-checks.md` steps 25 to 34]: the
    overlay and the capture coach against the real sky, with the accuracy stated.
  - Built and on `main`: positions asked from the laptop via `POST /api/science/sky-context` (`39e55e8c`, `d350a8fb`,
    `c8489254`, `d55ef833` skycontext-1 to 4; `9579708e` skycontext-5a optional `bayerDesignation`; `4b0482f9`
    skycontext-5b 512-star shipped bright tile pool with proper motion to date across northern and southern skies;
    `1e232232` skycontext-5c propagated ICRS parsec coordinates across 1800 to 2050), the rings and the source line
    with the heading's accuracy (`aab1f63b`, mobile-5), names by D20 (`22ae95c6`, aimnames-1), every ring wholly
    inside the preview (`7129309e`, drift-1), and the capture coach under it (D29: `62734c7a`, `9847bfd9`, `fe217a20`,
    `68eef7ed`, `9183920e`, `585689e6`, `33c41e04`, `93291fc2` coach-gap-1). Verified live in the simulator across
    northern and southern skies (`.lead/kiro/reports/MOBILE/southern-aim-live.md`).
  - Motion and tag-rule hardening on `main`: tagsweep-m (`5672b756`, AUDIT-C's tag sweep of 21 Nov, items 10 and 11:
    the rings, their tag and their source line come from one read a frame, and no ring stands once the line under the
    preview says there is no place or heading); tagsweep-m2 (`660a82cc`: the source line stands from its rings' first
    frame, while only the missing-sensor line words in); aimexit-m (`c9eb0226`, the lead, 21 Nov 13:48: at the press,
    and on coming back, the rings and the rest fade on the stage's timing while the tag and the source line stay at
    full until the rings' last frame); aimexit-m2 (`7fac1cfc`, 22 Nov: `CaptureStage` keeps `AimScreen` live through
    its leaving and animates `hidden`, with `.transition(.identity)` on the tag); holdwait-m (`ae2ee784`, the lead,
    22 Nov 22:17: Hold still and the exposure length word in only once the tag and source line have gone at the rings'
    last frame, 0.45 s normal and 0.42 s under Reduce Motion, verified at 30 fps in
    `.lead/kiro/reports/MOBILE/reduce-motion-lockon-m.md`). Left open [open: real iPhone check]: iPhone checks 25 to
    34 on hardware.
  - _Requirements: REQ-IOS-3.1, 3.2_

- [x] 12. mobile-6: the look from the reference board
  - The screens styled from the board the owner says yes to (the owner asked on 2026-11-11 for clean typography,
    no extra text and clean colour); no change in behaviour.
  - Needs first: the design-mobile lane's board and the owner's yes.
  - Built and on `main` from the records: controls, logo, Geist and Phosphor (`69dca733`), home with Credits, Credits
    (`aa615fa0` source citations), Connect, aiming, after the shot, can't reach the laptop and the result (`48912b79`,
    with Safari links `d6e33f53`), failed job and refused photo (`1ee9cf39`, D7/mobile-8), wrong address (`6b249e91`,
    D11), refused camera with Settings link and missing sensors (`f84a9649`, `e5c29107`, D8), kept photos (`2c16057e`,
    D13), queued solve (`5663a3d3`, D32), zero count omitted on the result (`c5b909f4`, result-0), bottom-aligned rows
    (`3c116d10`), and approved screen/in-screen motion and Reduce Motion dissolves (`c917a6ee`, `f4995eb2`, `ff111251`,
    `bcb1181a`, `51c69c3182`; simulator screen sweep `sweep-m`).
  - Only on an iPhone [open: real iPhone check, `.lead/kiro/reports/MOBILE/iphone-checks.md` step 20]: how it reads
    outdoors at night (dark adaptation, brightness).
  - _Requirements: REQ-IOS-3.2 (the labels stay legible)_

- [ ] 13. mobile-7: night exposures (open question 1 of the design)
  - A custom long exposure or a bracket, each frame its own photo and record; stacking stays on the laptop, in a
    later spec.
  - Settled 2026-11-18 (design 8.1): one long exposure at `maxExposureDuration`, ISO from the meter within the
    format's range, one photo and one record, with the approved hold-still screen; no set of frames.
  - Needs first: mobile-2; that later spec.
  - Only on an iPhone [open: real iPhone check, `.lead/kiro/reports/MOBILE/iphone-checks.md` steps 21 to 24 and 31]:
    every camera exposure part on hardware.
  - Built and on `main`: one long exposure at the format's longest duration with the approved hold-still screen
    (`e43836c4`), and the 0.45 s normal / 0.42 s Reduce Motion wait before "Hold still" and the length word in after
    the Predicted tag leaves (`ae2ee784`, holdwait-m). Left open [open: real iPhone check]: iPhone checks 21 to 24 and
    31 on hardware.
  - _Requirements: REQ-IOS-1.1, 1.2_

- [x] 14. mobile-8: the iPhone after Send (UIUX-iphone-solve-APPROVED and -READY, the owner's yes of 2026-11-18)
  - The wait with the job's current step and its own count, a result with no star matched in the web's own sentence
    with its counts, and a failed job (or a refused photo) in its own sentence with "Try again".
  - Built and on `main`: `1ee9cf39` (mobile-8), `6ed5fe48` and `b612b44f` (C17 `device_attitude` wording), `5663a3d3`
    (D32 queued row), `51c69c3182` (progress line curve, instant under Reduce Motion), and `767f9b01b5` (solread-2
    closed `poseEstimate.method` fallback in `OutcomeSentence` with no fabricated Moon or Sun claim).
  - Only on an iPhone [open: real iPhone check, `.lead/kiro/reports/MOBILE/iphone-checks.md` steps 10 to 13]: the
    wait's steps over the hotspot on a full-size photo.
  - _Requirements: REQ-IOS-4.2 (solve), REQ-IOS-5.1_

- [ ] 15. lockon-m: the lock-on on the iPhone's wait, as UIUX-iphone-lockon-READY draws it
  - Early amber rings over the photo while the solve runs, from the job's predicted sky (platform-10b) placed through
    its pose's own WCS, or the phone's own record projected through the sky context until it arrives; C13's source
    line beside them; on a calibrated solve each matched ring travels onto its detection, the rest leave, then the
    result (MOTION section 5); the swap to the laptop's sky in one correction (ruled 19 Nov 18:05).
  - Built and on `main`: `7ceee806` (lockon-m1 `EarlyPose`), `03ad9f6d` (lockon-m2 phone early rings and C13 sentence),
    `06278d26` and `72763fde` (lockon-m3/m3b pairing by `catalogSourceId`), `c01223f0` and `3ff0682d` (lockon-m4/m4b
    travel and kept step row), `1d67b003` (lockon-m4c unmatched `frameOut` and Reduce Motion dissolve), `d8c3a796`,
    `d3d8bdcc`, `582ac16e`, `3812db0f`, `c3b9fa4d` and `e036b364` (lockon-m5a to m5f laptop `PredictedEarlySky` WCS
    placement and single-correction swap), `6b40ef54` (tagrule-1), `50cadbeb` (swaparrive-1 brightest-first stagger
    and post-travel arrival), `a20adc1b36` (earlymark-side-1 full-width `EarlyMark` Canvas frame for long southern Bayer
    names), and `767f9b01b5` (solread-2 `PredictedEarlySky` contract hash, class and coordinate bounds). Shown end to
    end on real solves in the simulator across northern and southern skies under normal and `AXSReduceMotionEnabled = 1`
    modes (`.lead/kiro/reports/MOBILE/lockon-m5.md`, `.lead/kiro/reports/MOBILE/reduce-motion-lockon-m.md`).
  - tagsweep-m (`5672b756`, AUDIT-C's tag sweep of 21 Nov, items 5 and 9, as the lead ruled them): the source line
    stays at full through the pass until the early rings' last frame, and on a swap it keeps the phone's words until
    the phone's rings have gone, at the end of the shared rings' travel.
  - Only on an iPhone [open: real iPhone check, `.lead/kiro/reports/MOBILE/iphone-checks.md` steps 35 and 36]: the
    early rings against the real sky and the pass on hardware.
  - _Requirements: REQ-IOS-3.1, 5.1_
