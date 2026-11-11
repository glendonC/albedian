# Tasks: iOS Capture Companion

Each task is one unit: one commit, tests first, mutants on its risky lines, properties under 20 seeds, all six gates,
an independent `albedian-auditor` verdict, then the lead lands it. Server units are `pointing-<n>`; app units are
`mobile-<n>`, in the lane the lead names for `apps/ios` (open question 3 of `requirements.md`). Design: `design.md`
in this folder.

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
  - Waits for mobile-1's device checks of the axes, the quaternion direction and the timebase.
  - _Requirements: REQ-IOS-2.1, 2.2, 2.3_

- [ ] 5. pointing-5: a pose from the record when stars do not solve (design component 6)
  - `complete_sky` with a stored record: an `approximate` solution with a `device_attitude` pose swept over the
    heading accuracy, field of view, zoom and roll, widened by the motion window; no sensor pose without a heading.
  - _Requirements: REQ-IOS-2.3; spec `predicted-sky-for-every-upload` REQ-SKY-3 to 6_

- [ ] 6. mobile-1: capture with the pointing (REQ-IOS-1, REQ-IOS-6), after the owner's UI session
  - The capture screen, the record built from Core Motion, Core Location and AVFoundation as design component 1
    says, the photo kept byte for byte; on-device checks of every [open] item in the design.
  - _Requirements: REQ-IOS-1.1 to 1.3, REQ-IOS-6.1, 6.2_

- [ ] 7. mobile-2: the live aim overlay, labelled predicted (REQ-IOS-3)
  - _Requirements: REQ-IOS-3.1, 3.2_

- [ ] 8. mobile-3: send to the laptop and show the result (REQ-IOS-4, REQ-IOS-5)
  - Pairing with the laptop's address, upload progress from bytes sent, the record, the solve, status and counts by
    evidence class as the server reports them, the reveal opened in the web app.
  - _Requirements: REQ-IOS-4.1 to 4.3, REQ-IOS-5.1, 5.2_
