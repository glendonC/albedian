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

- [ ] 3. sky-3: lined up from the Moon, a camera profile and a level head (REQ-SKY-3; P-SKY-5, P-SKY-6)
  - `services/science/camera_profiles.py`, `services/science/pose_estimate.py`, the spread frames for clips in
    `services/ingest/jobs.py`, `complete_sky` with a pose, `docs/science/PREDICTED_SKY.md` (layer 2, head-worn).
  - Built and amended four times (branch `kiro/sky`); not yet audited. Real case: the owner's clip ends
    `approximate`, lined up at 3.30 s, within about 9 degrees.
  - _Requirements: REQ-SKY-3.1 to 3.7, REQ-SKY-7_

- [ ] 4. sky-5: what the person sees (REQ-SKY-6; P-SKY-8, P-SKY-9)
  - `apps/web/src/services/skyLayerWords.ts`, `apps/web/src/components/poseProjection.ts`, the overlay branch, the
    clip clock and gate, the disclosure line, the banners in `apiClient.ts`.
  - One FAIL (clip gate, wording); fixed and amended (branch `kiro/sky-web`); a fresh audit is next.
  - _Requirements: REQ-SKY-6.1 to 6.6_

- [ ] 5. sky-4: the Moon plus horizon pose for uploads and laptop clips, with its uncertainty (REQ-SKY-4.1, 4.2, 4.4)
  - Wire `solve_horizon_and_beacon_pose` into `complete_sky`; its findings from `predicted_sky_findings`
    (characterization test); its `poseEstimate`; the topocentric Moon; the half-maximum Moon test of sky-3.
  - Then a separate small unit: `approximate` requires `poseEstimate` in both contracts (REQ-SKY-5.4).
  - _Requirements: REQ-SKY-4.1, 4.2, 4.4, 5.4_

- [ ] 6. sky-6: the Sun as the beacon in daytime photos (REQ-SKY-4.3)
  - After a real daytime photo with the Sun and a skyline exists to test on.
  - _Requirements: REQ-SKY-4.3_
