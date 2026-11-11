# Design: a sky for every upload

Requirements: `requirements.md` in this folder. Every line reference below was read at `main` `db972af7`.

## Overview

The plate solvers stay exactly as they are. After the solve chain returns (or stops at its time limit), the job
completes the solution in the parent process with up to two more layers, then writes `solution.json` once:

```
run_solve_job (services/ingest/jobs.py)
  decode, detect, _solve_chain (child process, 180 s bound)          -> solver solution (unchanged)
  complete_sky(solver solution, asset, frames)                        -> final solution
    calibrated?            -> add predicted objects not matched (layer 1); done
    no time or place?      -> add the plain reason to warnings; done (REQ-SKY-2)
    moment out of range?   -> add the reason; done
    pose = head_worn_moon_pose(...)  if the camera profile is head_worn  (layer 2, REQ-SKY-3)
        or horizon pose (Moon or Sun plus horizon)                       (layer 2, REQ-SKY-4)
    pose found?            -> approximate: nominal WCS, poseEstimate, observed beacon, predicted sky
    else                   -> predicted_only: solver findings, solver quality, predicted sky
  write solution.json, emit succeeded
```

Layer 1 needs no contract change: `predicted_only` with `predicted` findings is already valid
(`tests/fixtures/python_contract_solutions.json` sample `horizon_predicted_only`). Layer 2 needs the new
`poseEstimate` (REQ-SKY-5), so the contract unit lands before it.

## Components

### 1. The Sun in the ephemeris (`services/science/ephemeris.py`)
- Add `"Sun"` to `compute_solar_system_ephemerides`: its geocentric position is minus the Earth-Moon barycentre's
  heliocentric position from the same Standish elements (`PLANET_ELEMENTS["Earth"]`), so it adds no new model.
- Uncertainty: the barycentre's error bound from the module's existing bound for the elements, plus the barycentre
  to geocentre offset (`BARYCENTRE_OFFSET_KM`) at one AU; the limitation says both. `object_type` `"sun"`, id
  `finding-predicted-sun`. The default `bodies` tuple is unchanged, so no existing caller changes output; the new
  module asks for the Sun explicitly.

### 2. The predicted sky (`services/science/predicted_sky.py`, new)
- `frame_moment(captured_at_utc, frame_time_sec) -> str`: the recorded start plus the frame's offset, in the
  contract's UTC form (the same rule as `services/media/discovery.py` `_moment_plus`, which moves here and is
  imported back).
- `sky_phase(sun_alt_deg) -> SkyPhase(band, sentence)`: pure; bands at -0.833, -6, -12, -18 deg (USNO, "Rise, Set,
  and Twilight Definitions", aa.usno.navy.mil/faq/RST_defs; read it at source before citing). The sentence names the
  Sun's altitude rounded to a whole degree and, for day, says the stars listed were above the horizon but outshone.
- `predicted_sky_findings(moment, lat, lon, *, solution_id, source_sha256, exclude_ids=frozenset()) ->
  list[CelestialFindingRecord]`: the pinned bright list carried to the date (`propagate_star_epoch`), the Sun, Moon
  and five planets, each above the horizon only, ids as the horizon solver writes them
  (`finding-predicted-<star id>`, `finding-predicted-<body>`), limitations naming the catalog release or ephemeris
  model and saying "not measured in the recording". Star uncertainty reuses `PREDICTED_STAR_UNCERTAINTY_ARCSEC`
  (55 arcsec; its derivation is in `horizon_solver.py`), moved here and imported back.
- `complete_sky(solution, asset, *, pose=None) -> ObservationSolution` (pure on its inputs): applies REQ-SKY-1.2,
  1.3 and REQ-SKY-2 and returns a new solution (`dataclasses.replace`), never mutating the solver's. Its warnings
  never contain a time, a place or a file name (REQ-SKY-2.4): they say "the recorded capture time" and "the
  recorded place".
- An `ambiguous` plate solve becomes `predicted_only` with its quality and warnings kept (requirements, decisions).

### 3. Camera profiles (`services/science/camera_profiles.py`, new)
- `CameraProfile(model, kind, fov_diagonal_deg_range, fov_label, fov_source, notes)`, looked up by the exact
  `SourceAsset.cameraMetadata.model` string. One entry now: `"Meta Ray-Ban Display Glasses"` (the QuickTime model tag
  of the owner's clip), kind `head_worn`, 80 to 110 deg, `[open]`, with the source note from the requirements. No
  entry is added for a model nobody has uploaded.
- Kept apart from `phone_photo_solver.py`'s `PUBLISHED_CAMERA_FOV_DEG`, which solver 10k edits.

### 4. The head-worn Moon pose (`services/science/pose_estimate.py`, new)
- `find_moon_blob(luma, *, min_diameter_px, max_diameter_px) -> MoonBlob | Refusal(reason)` (as built in sky-3 v3,
  2026-11-11; the first rule, saturated pixels at 250 of 255, refused the owner's limited-range clip, whose Moon is a
  soft peak): the candidate is the brightest peak after a 2.5 px Gaussian; its own code must be at least 200 of 255
  and at least 10 noise units (1.4826 MAD) above the frame median [proposed]; its region is the 8-connected pixels
  above median + (peak - median) / 2, the half-maximum contour that follows a resolved disk's edge whatever the
  exposure; a region's equivalent diameter is `2 sqrt(area / pi)`. Refuse with "not confirmed as the Moon" when
  another region above that level has half its area or more. The centroid is the background-subtracted flux-weighted
  centroid over the region dilated by two pixels, its FWHM from second moments as in `horizon_solver.py`.
- Diameter limits: the Moon's predicted angular diameter `2 asin(1737.4 km / distance)` divided by the profile's
  scale at its widest field of view, times 0.8 for the minimum; at its narrowest field of view, times 8 for the
  maximum [proposed: wide enough for overexposure bloom, narrow enough to refuse a lit cloud or twilight band].
- Scale from a diagonal field of view for the rectilinear (TAN) model: `scale_rad_per_px = 2 tan(fov / 2) / d`
  with `d` the frame diagonal in pixels.
- The Moon's topocentric direction: the ephemeris position turned to altitude and azimuth, lowered by
  `moon_parallax_shift_arcsec`, turned back with `altaz_to_icrs` (refraction is left out for the Moon and the stars
  alike, so it cancels to first order; stated in the limitations).
- The fit, for one (field of view, roll) pair: unknowns `crval_ra`, `crval_dec`, `rotation_deg` at fixed scale,
  `parity -1`, `k1 = k2 = 0`, `crpix` the frame centre (the conventions of `horizon_solver.py` `_wcs_for`).
  Residuals: the Moon's projection minus the blob centroid (2), and the roll constraint (1): two points at the
  frame centre's altitude, 10 deg either side in azimuth, mapped to the upright frame (`displayOrientation`), have
  a row difference equal to `tan(roll)` times their column difference. `scipy.optimize.least_squares(method="lm")`
  from the closed-form start (centre altitude from the blob's row offset times the scale, azimuth from its column
  offset over the cosine of the altitude). A fit whose largest residual is above 0.5 px is refused.
- The sweep: field of view at the profile's two limits and middle, roll at -10, 0 and +10 deg [proposed]: nine fits.
  Nominal is the middle field of view with roll 0. `maxCenterOffsetDeg` is the largest angle between the nominal
  and any swept frame centre; `beaconCentroidUncertaintyDeg` is the Moon's predicted radius (an overexposed spot's
  centroid sits somewhere on the disk, and a partial phase moves it toward the lit limb). The assumptions also say
  the camera was not zoomed: the owner's clip's Moon region measures about 3 times its predicted size, from bloom,
  motion blur or a zoom the file does not record [open].
- Returns a `PoseResult(wcs, pose_estimate, beacon_finding, frame_time_sec)` or the refusal reason.
- For a clip: `pose_from_frames(frames_with_times, ...)` tries each frame in time order and keeps the first that
  passes the Moon test and the fit; the frames come from component 6.

### 5. The horizon pose for uploads (`services/science/horizon_solver.py`, unit sky-4)
- The job calls `solve_horizon_and_beacon_pose` on the upload's frame when no head-worn pose was found; its star
  and body findings switch to `predicted_sky_findings` (a characterization test shows the same ids, positions and
  limitations before and after for the synthetic cases in `tests/integration/test_no_default_moment.py`).
- Its `approximate` result gains a `poseEstimate`: the four-constraint fit re-run with the horizon at 0 and 3 deg
  true altitude [proposed], the horizon row at plus and minus its measured uncertainty (the half width of the
  gradient peak), giving the swept WCS; the Moon's topocentric position replaces the geocentric one it now carries
  with a stated offset. Discovery (`services/media/discovery.py`) gets the same fields through the same function.
- Then `approximate` requires `poseEstimate` in both contracts (REQ-SKY-5.4).
- The Sun beacon and a horizon edge of either sign are unit sky-6, after a real daytime photo exists to test on.

### 6. Frames for the pose search (`services/ingest/jobs.py`)
- Photos: the decoded luma frame.
- Clips: up to 16 frames spread evenly over the clip by frame index (`n_k = round((k + 0.5) N / 16)` for a clip of
  `N` frames from its probed frame rate and duration), each decoded at stored size by an accurate seek to half a
  frame before `n_k / fps` (`ffmpeg -ss <t> -i <file> -frames:v 1 -noautorotate -pix_fmt gray`), no scale
  filter, inside the existing decode timeout. The frame's time is `n_k / fps`. These frames are never stacked
  (scientific-integrity rule 6 is about stacking; a single frame needs no de-skew).
- `complete_sky` runs after the bounded solve and before `solution.json` is written; the job's stages are unchanged.

### 7. The contract (`packages/contracts/src/astronomy.ts`, `services/science/wcs_models.py`, unit sky-1)

```ts
export const PoseEstimateSchema = z.object({
  method: z.enum(['moon_camera_profile_level_head', 'moon_horizon', 'sun_horizon']),
  beacon: z.enum(['moon', 'sun']),
  centerAltDeg: z.number().min(-90).max(90),
  centerAzDeg: z.number().min(0).max(360),
  rollDeg: z.number().min(-180).max(180),
  fovDiagonalDeg: z.number().positive().max(175),
  fovSource: z.string().min(1),
  assumptions: z.array(z.string().min(1)).min(1),
  sweptWcs: z.array(WCSCalibrationModelSchema).min(2).max(27),
  maxCenterOffsetDeg: z.number().nonnegative(),
  beaconCentroidUncertaintyDeg: z.number().nonnegative(),
});
```

- `ObservationSolutionSchema` gains `poseEstimate: PoseEstimateSchema.optional()`, with refinements: present only
  when `status === 'approximate'` and `wcs` is present; every swept WCS has the nominal WCS's `crpixX` and
  `crpixY` (the same frame). `CelestialFindingSchema` refuses a `sourceDetection` on `predicted` and `simulated`.
- Python mirrors it: a frozen `PoseEstimate` dataclass with `to_contract_dict`, `ObservationSolution.pose_estimate`
  defaulting to `None`, the same rules with the same messages, and `CelestialFindingRecord.__post_init__` refusing a
  detection on a predicted or simulated finding. Nothing produces a pose estimate in this unit.
- Fixture: `build_contract_samples` (`tests/integration/test_unregistered_detections.py`) gains a synthetic sample
  `pose_moon_level_head` (the synthetic moment and open-ocean place the other samples use); the TS test
  (`packages/contracts/src/__tests__/observation-solution.test.ts`) parses every sample and runs P-SKY-7.
- `scripts/verify-contracts.mjs` lists exported schema names: add `PoseEstimateSchema` there.

### 8. The web (`apps/web/src`, unit sky-5, after sky-1 lands)
- `apps/web/src/services/skyLayerWords.ts` (new, pure): `skyLayerSentence(solution, asset, search)`, where `search`
  says whether a star search ran for this solution (REQ-SKY-6.8), gives the plain line of
  REQ-SKY-6.1; `formatSolveJobProgressBanner` and both session builders in `apiClient.ts` use it instead of
  `Sky solution: <status>`.
- `apps/web/src/components/poseProjection.ts` (new, pure): `projectThroughPose(finding, solution)` returns the
  source pixel through the nominal WCS (`worldToPixelWCS` from `@albedian/camera-rig`) and the ring radius in source
  pixels (largest distance to the swept projections plus `beaconCentroidUncertaintyDeg / scaleDegPerPx`), or `null`
  when there is no pose estimate, the point is behind the camera or outside the frame.
- `CelestialMarkerOverlay.tsx`: in `source_2d`, a `predicted` finding with a pose projection is placed through
  `sourcePixelToViewport` (crop and display orientation, as detections are), drawn with the predicted marker and a
  faint dashed ring scaled by the same mapping; the `predictedEphemerides` toggle hides them, and the session
  builders turn it on for an `approximate` upload. The stage seeks a clip to the session's clock; every marker on a
  clip shows only while it is not playing and the clock is within max(one frame, 0.05 s) of the marker's own frame
  (P-SKY-9), with the line "Lined up 3.4 seconds into the clip. Say or type "jump to 3.4 seconds" to see it."
  otherwise. The ring's centroid term is converted at the finding's own place (8 bearings), not at the centre's
  scale, since a wide TAN frame's pixels per degree grow off axis.
- The disclosure line under the markers names the basis from `poseEstimate.method` and `assumptions`, and "within
  about N deg" from `maxCenterOffsetDeg + beaconCentroidUncertaintyDeg`, rounded up to a whole degree.

### 9. Docs (`docs/science/PREDICTED_SKY.md`, new, with sky-2 and extended by sky-3)
- The three layers in plain words, the sky phase bands and their source, the Moon test, the sweep and what the ring
  covers (only values inside the stated ranges, a rectilinear lens), the camera profile and its labels, and what is
  [open]. Linked from `docs/science/` where the other solver docs are listed, so `pnpm verify:docs` checks it.

## Error handling
- No time, no place, out of range: the solver's solution with one more plain warning (REQ-SKY-2).
- A decode failure of a spread frame: that frame is skipped; with none decoded, no head-worn pose, and the warning
  says the frames could not be read (no path in it).
- A fit that does not converge or exceeds its residual limit: no pose, with the reason in the warning.
- Any exception inside `complete_sky`: the job keeps the solver's solution unchanged and adds the generic warning
  "The predicted sky could not be computed." so the upload still ends `succeeded` with what the solver found. A test
  injects a failure and checks that fallback; the real-case run must show no such warning, so a bug cannot hide.

## Testing strategy
- Python unit tests per component under `tests/science/` (`test_predicted_sky.py`, `test_pose_estimate.py`,
  `test_camera_profiles.py`) and job tests under `tests/integration/` (`test_ingest_predicted_sky.py`), each written
  first and seen failing for the stated reason; hypothesis properties P-SKY-1 to P-SKY-6 under 20 seeds.
- Contract parity P-SKY-7 in both languages over the shared fixture plus generated faults.
- Web: vitest for `skyLayerWords.ts`, `poseProjection.ts` (fast-check P-SKY-8) and the overlay (P-SKY-9).
- Mutants on the risky lines: the status rule in `complete_sky`, the exclude set for calibrated solutions, the Moon
  test's three conditions, the sweep's ends, the ring radius, the clip time gate, the contract refinements.
- Real data: `.lead/kiro/real_case.zsh 1` runs the owner's head-worn clip through `run_solve_job` with a temporary
  ingest store outside `data/` and prints counts and statuses only (REQ-SKY-7.1). It lives in the lead's ignored
  folder so no path to the owner's file enters the repository.
