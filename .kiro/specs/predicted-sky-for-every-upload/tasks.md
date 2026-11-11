# Tasks: a sky for every upload

Each task is one unit: one commit, tests first, mutants on its risky lines, properties under 20 seeds, all six gates,
an independent `albedian-auditor` verdict, then the lead lands it. Unit ids are `sky-<n>`. Status as of the Kiro
lead's checkpoint of 2026-11-11 (the lead's `.lead/HANDOFF.md` has the commits, verdicts and next steps).

- [x] 1. sky-1: the pose estimate in the contract (REQ-SKY-5.1 to 5.3; P-SKY-7)
  - `PoseEstimateSchema` and the two solution refinements in `packages/contracts/src/astronomy.ts`; the finding
    refinement (no `sourceDetection` on predicted or simulated); the method tied to its beacon; finite offsets.
  - The Python mirror in `services/science/wcs_models.py`; the fixture sample `pose_moon_level_head`; the TS
    fixture test; `scripts/verify-contracts.mjs` schema list.
  - Landed as `cd10d044` (one FAIL, then PASS).
  - _Requirements: REQ-SKY-5.1, 5.2, 5.3_

- [x] 2. sky-2: the predicted sky for every upload with a time and place (REQ-SKY-1, REQ-SKY-2; P-SKY-1 to P-SKY-4)
  - The Sun in `services/science/ephemeris.py`; `services/science/predicted_sky.py`; the call in
    `services/ingest/jobs.py`; `docs/science/PREDICTED_SKY.md`.
  - Landed as `eeab4d15` (PASS). Real case: the owner's head-worn clip ends `predicted_only` with 65 predicted
    findings.
  - _Requirements: REQ-SKY-1.1 to 1.7, REQ-SKY-2.1 to 2.4, REQ-SKY-7_

- [x] 3. sky-3: lined up by taking a bright spot to be the Moon, with a camera profile and a level head (REQ-SKY-3;
  P-SKY-5, P-SKY-6)
  - `services/science/camera_profiles.py`, `services/science/pose_estimate.py`, the spread frames for clips in
    `services/ingest/jobs.py`, `complete_sky` with a pose, `docs/science/PREDICTED_SKY.md` (layer 2, head-worn).
  - Landed as `0a65da83` (audits: FAIL, then PASS on its fifth version, after the lead's decisions A to F). Real
    case: the owner's clip ends `approximate`, lined up from its frame no. 97, 3.51 s into the clip by that frame's
    own timestamp, within about 9 degrees, with no star matched.
  - _Requirements: REQ-SKY-3.1 to 3.12, REQ-SKY-7_

- [x] 4. sky-5: what the person sees (REQ-SKY-6; P-SKY-8, P-SKY-9)
  - `apps/web/src/services/skyLayerWords.ts`, `apps/web/src/components/poseProjection.ts`, the overlay branch, the
    clip clock and gate, the disclosure line, the banners in `apiClient.ts`.
  - Landed as `9b0778c7` (audits: FAIL, FAIL, PASS). Since sky-3 landed (`0a65da83`), the head-worn pose gives the
    lined-up drawing its poses on main; the drawing's own tests still use the synthetic fixture.
  - _Requirements: REQ-SKY-6.1 to 6.8_

- [ ] 5. sky-4: the Moon plus horizon pose for uploads and laptop clips, with its uncertainty (REQ-SKY-4.1, 4.2, 4.4)
  - Wire `solve_horizon_and_beacon_pose` into `complete_sky`; its findings from `predicted_sky_findings`
    (characterization test); its `poseEstimate`; the topocentric Moon; the half-maximum Moon test of sky-3.
  - Then a separate small unit: `approximate` requires `poseEstimate` in both contracts (REQ-SKY-5.4).
  - Starts after ephemeris-2 is on main: the Moon record becomes topocentric there, and sky-3's own parallax lines
    (`services/science/pose_estimate.py` `topocentric_moon`, its assumption sentence that names the shift and its
    module docstring, and the pose solver's text in `services/science/predicted_sky.py`) are removed in that unit
    by the ephemeris lane, as the lead allowed, so they are not lowered twice. Not in sky-4.
  - Tests to add there, left by the audits of sky-3 and sky-3b: a clip whose frames mix Moon-not-up and by-day
    refusals answers by day (REQ-SKY-3.12); a clip with a spot that gave no pose and another frame not confirmed
    answers with the spot's reason, and with several such spots names the first one's time (REQ-SKY-3.12, verdict
    `sky-3b-v2.md` note (a)); and a test that kills each of the six mutants the sky-3 audit left
    surviving (verdict `sky-3-v5.md` item 8): B2, the frame time not less the first frame's timestamp, and B5, the
    file's start time left out of the seek (both need a clip whose first frame is after 0 s); B4, the seek at the
    frame's own timestamp instead of halfway; C7, E1 and E2, the 2 px edge margin on the top, bottom and right edges
    (REQ-SKY-3.3, 3.4).
  - Tests to add there, left by the audit of sky-3b's third version (verdict `sky-3b-v3.md`): by day with the Moon
    down, the reason given is that the Moon was not up (REQ-SKY-3.10); a rival is judged at its brightest point after
    the same smoothing, not at its raw maximum (REQ-SKY-3.3, the audit's M1); and `OTHER_FRAMES_KEPT` says the spot
    was taken to be the Moon, with a test whose solver keeps detections from other frames (REQ-SKY-3.11; the
    one-sentence fix of `services/science/predicted_sky.py` goes with it). And, from the table of every statement of
    REQ-SKY-3, P-SKY-5 and P-SKY-6 that the fourth version of sky-3b made (its report has the counts), the four
    statements no test of the three files pins: the smoothing's width of 2.5 px (REQ-SKY-3.3); the words of the
    level-head and beacon-term assumptions (REQ-SKY-3.5); each fit started at the closed form's pose (REQ-SKY-3.9).
  - And from sky-3b's fifth version (verdict `sky-3b-v4.md` and every number of that table moved one step each way):
    the fit's 0.001 deg bound on the closed form's pose (REQ-SKY-3.9); the probe's share of the one time bound
    (REQ-SKY-3.4); the microsecond within which two frame times are the same frame (REQ-SKY-3.6); the spot's subtitle
    and second limitation held word for word (REQ-SKY-3.11).
  - Also there: `docs/science/PREDICTED_SKY.md` labels the roll claim (that the level-pose count did not change
    between the swept rolls) "[tested, not proven]", while the design derives the one-pose test from the closed form
    [inference] (verdict `sky-3b.md`, left alone (3)); bring the two into line.
  - _Requirements: REQ-SKY-4.1, 4.2, 4.4, 5.4_

- [ ] 6. sky-6: the Sun as the beacon in daytime photos (REQ-SKY-4.3)
  - After a real daytime photo with the Sun and a skyline exists to test on.
  - _Requirements: REQ-SKY-4.3_
