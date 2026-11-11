# Requirements: a sky for every upload

## Introduction

Today an uploaded photo or clip with fewer than six usable stars ends `unresolved` and the person sees nothing
about their sky (`services/ingest/jobs.py` `run_solve_job`; `services/science/solver.py`
`_build_unresolved_solution`). The owner decided on 2026-11-10 (`.lead/APPROVALS.md`) that every photo or clip
must give a sky, visible stars or not, daytime included, in three layers, each labelled by its evidence class:

1. Always, from the file's capture time and place: the sky predicted for that moment (`predicted`).
2. Lined up over the photo when the pointing is known: from the Moon or Sun plus the horizon in the frame, or, for
   a head-worn clip with the Moon and no horizon, from the camera's field of view plus an assumed level head
   (the owner said "Ok sure" to that assumption). Labelled predicted and approximate, with its uncertainty. The
   iOS app's sensors are a later source of pointing (spec `ios-capture-companion`).
3. Exact (`catalog_matched`) only when enough stars are recorded and held-out verification passes, unchanged.

Facts this spec rests on [verified by reading the code at `main` `db972af7`]:
- The ingest path reads a recorded capture time (EXIF DateTimeOriginal with its offset, or QuickTime
  `com.apple.quicktime.creationdate`) and a place (EXIF GPS, or QuickTime ISO 6709) onto `SourceAsset`
  (`services/ingest/router.py` `_asset`) and passes both to every solver.
- `ObservationSolution` already allows `predicted_only` and `approximate` and requires `observedAtUTC` and
  `observerLocation` for both and for any `predicted` finding (`packages/contracts/src/astronomy.ts`,
  `services/science/wcs_models.py`).
- `services/science/horizon_solver.py` fits a pose from the Moon and a horizon line, but only clips found on the
  laptop by `services/media/discovery.py` reach it; uploads never do. It has no Sun path, and its `approximate`
  pose carries no uncertainty.
- The ephemeris (`services/science/ephemeris.py`) has the Moon and five planets but no Sun.
- The web draws nothing predicted over the photo: in `source_2d` it places only findings with a
  `sourceDetection` (`apps/web/src/components/CelestialMarkerOverlay.tsx` `projectFindingToViewport`).
- The first real case, the owner's newest head-worn clip (Meta Ray-Ban Display glasses, 15 s, 1344 x 1792, the
  file carries a recorded capture time and a place): no stars recorded, the Moon an overexposed spot in the first
  part, head motion, a few ground lights late in the clip. Ingest reads its capture time as recorded and its place
  [tested 2026-11-10 with `.lead/kiro/clip_facts.zsh`, which prints presence only].

Not in scope: the iOS app; pointing from phone sensors; solving on the phone; any change to the plate solvers or
to the held-out verification gate; per-frame tracking of head motion through a clip.

## Glossary

- **Predicted sky**: catalog stars, the Sun, the Moon and the planets placed for a time and place by deterministic
  ephemeris and catalog math (`predicted`), never measured in the recording.
- **Pose estimate**: an approximate camera pointing (where the frame's centre pointed, its roll, its field of
  view) from one bright body and stated assumptions, never checked against stars; it places predicted objects in
  the frame (`approximate`).
- **Swept assumptions**: each assumption a pose rests on (field of view, head roll, horizon altitude, the body's
  centre in the frame) taken at its stated limits, so the drawn uncertainty covers every value inside them.

## Requirements

### REQ-SKY-1: The predicted sky for every upload with a capture time and place
**User Story:** As someone who uploads a photo or clip of a night, or a day, I want to see what was up in my sky at
that moment even when my camera recorded no stars.
- **Acceptance Criteria:**
  1. WHEN an upload's solve job finishes and the `SourceAsset` has a recorded `capturedAtUTC` and an
     `observerLocation`, and the moment lies inside the ephemeris range (1800-01-01 to 2050-12-31 UTC), THE solution
     SHALL carry `predicted` findings for: every star of the pinned all-sky bright list
     (`services/science/catalog.py` `get_all_sky_catalog_stars`) above the horizon, carried to the date by its
     proper motion; and the Sun, the Moon, Venus, Mars, Jupiter, Saturn and Uranus when above the horizon.
  2. WHEN that solution is not `calibrated` and no pose estimate is found (REQ-SKY-3, REQ-SKY-4), THE status SHALL
     be `predicted_only`; the observed detections SHALL stay as pixel-only `observed` findings; the solver's
     `quality`, its warnings and its `solver` identity SHALL be kept.
  3. WHEN that solution is `calibrated`, THE predicted findings SHALL leave out every object already present as a
     `catalog_matched` finding (same catalog id), and the status, WCS and quality SHALL be unchanged.
  4. THE moment of the predicted sky SHALL be the moment of the solution's frame: `capturedAtUTC` plus
     `frameTimeSec`, written as the solution's `observedAtUTC`.
  5. Each predicted finding SHALL carry its catalog or ephemeris uncertainty (`skyPosition.uncertaintyArcsec`), its
     altitude and azimuth, and limitations that name its source (the catalog release or the ephemeris model) and say
     it was not measured in the recording.
  6. THE solution's warnings SHALL include one plain sentence for the Sun's place at that moment, by the USNO
     definitions (Sun's centre altitude: day above -0.833 deg; civil twilight to -6; nautical to -12; astronomical
     to -18; night below): for day, that the stars listed were above the horizon but outshone by daylight.
  7. THE solve job's stage list SHALL stay as it is (`store`, `decode`, `detect`, `solve`, `verify`); the predicted
     sky is computed after the solve and needs no new progress stage.

### REQ-SKY-2: Say why when there is no predicted sky
**User Story:** As someone whose file lacks a time or a place, I want to know why there is no sky, in plain words.
- **Acceptance Criteria:**
  1. WHEN the `SourceAsset` has no recorded `capturedAtUTC`, THE solution SHALL omit `observedAtUTC`, carry no
     predicted finding, and its warnings SHALL say the file has no recorded capture time, so no sky can be
     predicted. The same for a missing place.
  2. WHEN the file's time is only the file's own written time (QuickTime `creation_time`, marked `file_written`)
     or an EXIF time without a UTC offset, THE solution SHALL treat it as no capture time (REQ-SKY-2.1); no other
     time (the upload time, the laptop's clock) SHALL stand in.
  3. WHEN the moment lies outside the ephemeris range, THE solution SHALL carry no predicted finding and say so.
  4. No warning, log line, job record or error SHALL contain the capture time, the place, or the file name.

### REQ-SKY-3: Lined up from the Moon, a known field of view and a level head (head-worn clips)
**User Story:** As someone who filmed the Moon with camera glasses and no horizon in view, I want the predicted
sky lined up over my clip, with an honest sense of how far off it may be.
- **Acceptance Criteria:**
  1. WHEN the solution is not `calibrated`, the predicted sky exists (REQ-SKY-1), the predicted Moon is more than
     5 deg above the horizon, the camera model matches a camera profile of kind `head_worn`
     (`services/science/camera_profiles.py`) and one frame shows exactly one bright spot that passes the Moon test
     (REQ-SKY-3.3), THE solution SHALL be `approximate` with a WCS fitted so that the topocentric Moon
     lands on the blob's centroid and the horizon is level in the upright frame (the frame as displayed, after
     `displayOrientation`).
  2. THE camera profile SHALL state the diagonal field of view as a range with its source, and each value's label:
     [verified] only when read at the maker's published specification for that exact model, else [open] with the
     reason. The assumed head roll SHALL be a stated range around level (proposed: within 10 deg either way).
  3. THE Moon test SHALL find the Moon by its half-maximum contour, not by saturation (decided 2026-11-11: the
     owner's clip is limited-range video whose Moon is a soft peak, never a flat clipped core): the frame's brightest
     peak, at least 200 of 255 and at least 10 noise units above the sky; the connected region above half its height
     over the sky; that region's equivalent diameter at least 0.8 times the Moon's predicted angular diameter at the
     profile's widest field of view and at most 8 times at the narrowest; and no other region above the same level
     with half its area or more. WHEN two or more spots could be the Moon, or none passes, THE solution SHALL stay
     `predicted_only` and say "not confirmed as the Moon".
  4. FOR a clip, THE job SHALL search frames spread over the whole clip (not only its first frames) at stored pixel
     size, and SHALL use one frame: the solution's `frameTimeSec` SHALL be that frame's time. No frames are stacked
     for this path.
  5. THE pose estimate SHALL record: the method, the nominal centre altitude and azimuth, roll and field of view,
     every assumption in plain words (the field of view range, no zoom, a level head), the field of view's source, the swept WCS (each assumption at its limits and
     middle), the largest angular offset of the frame centre across the sweep, and an added angular term for the
     Moon's centroid (overexposure and phase bias, at least the Moon's predicted radius).
  6. THE observed Moon blob SHALL be an `observed` finding with its measured centroid and no sky position; any
     detector detection within the blob's radius SHALL be left out so the Moon is not listed twice.
  7. A pose estimate SHALL never set `passedIndependentVerification`, never make any finding `catalog_matched`,
     and never give an `observed` finding a sky position.

### REQ-SKY-4: Lined up from the Moon or Sun plus the horizon (uploads)
**User Story:** As someone who photographed the Moon or the Sun above a skyline, I want the predicted sky lined up
over my photo, as the laptop's own clips already get it.
- **Acceptance Criteria:**
  1. WHEN the solution is not `calibrated`, the predicted sky exists, and REQ-SKY-3 found no pose, THE job SHALL try
     the Moon plus horizon pose (`services/science/horizon_solver.py`) on the upload's frame; a pose it returns
     SHALL make the solution `approximate` with the same fields and rules as REQ-SKY-3.5 to 3.7.
  2. THE horizon pose's swept assumptions SHALL include the skyline's true altitude (proposed: 0 to 3 deg above the
     true horizon), the horizon row's measured uncertainty, and the Moon's centroid term.
  3. WHEN the Sun is more than 5 deg up, THE beacon SHALL be the Sun, never the Moon, and the horizon SHALL be found
     whether the sky is brighter or darker than the ground. (After REQ-SKY-4.1 and 4.2; no real daytime case yet.)
  4. Clips found on the laptop (`services/media/discovery.py`) SHALL get the same pose estimate fields.

### REQ-SKY-5: The pose estimate in the contract
**User Story:** As the web app and the iOS app, we need one shape for an approximate pointing and its uncertainty.
- **Acceptance Criteria:**
  1. `ObservationSolution` SHALL gain an optional `poseEstimate` in `packages/contracts/src/astronomy.ts` (Zod) and
     `services/science/wcs_models.py` (Python), allowed only when the status is `approximate` and a `wcs` is
     present, with at least two swept WCS of the same image size as the nominal WCS.
  2. A `predicted` or `simulated` finding SHALL never carry a `sourceDetection` (Zod and Python).
  3. TS and Python SHALL accept and refuse the same samples, including a new `approximate` fixture sample with a
     pose estimate in `tests/fixtures/python_contract_solutions.json`.
  4. Once REQ-SKY-4.4 lands, an `approximate` solution SHALL require a `poseEstimate` (a later, separate unit).

### REQ-SKY-6: What the person sees
**User Story:** As someone who is not an astronomer, I want to see my sky and to know, in plain words, how each part
of it was found.
- **Acceptance Criteria:**
  1. AFTER an upload, THE status line SHALL say in plain words which layer the result is, never a raw status name:
     matched to N stars in the photo (`calibrated`); lined up from the Moon or Sun, within about N deg, not checked
     against stars (`approximate`); for `predicted_only`, what the solver found ("Too few points of light to match
     to stars." under 6 detections, else "Found N points of light, but could not match them to stars for sure.")
     and that the sky shown is the one predicted for the photo's time and place; or why there is no sky (REQ-SKY-2).
  2. WHEN the solution is `approximate` with a `poseEstimate`, THE photo view SHALL draw each predicted finding that
     projects inside the frame through the nominal WCS, with the predicted marker (dashed circle) and a ring whose
     radius covers its position under every swept WCS plus the centroid term.
  3. THE photo view SHALL show, beside those markers, a disclosure naming the basis (for example "Lined up from the
     Moon, the glasses' field of view and a level head; not checked against stars") and the uncertainty.
  4. FOR a clip, THE stage SHALL show the frame the session's clock names, and every marker SHALL show only while
     the clip is not playing and the clock is within max(one frame, 0.05 s) of the marker's own frame; otherwise the
     view SHALL say, in seconds, where the sky is lined up and the words that go there ("Lined up 3.4 seconds into
     the clip. Say or type "jump to 3.4 seconds" to see it.").
  5. WHEN the solution is `predicted_only` or `approximate`, "Show Predicted Sky" SHALL open the sky for the
     solution's time and place, as it does today for the laptop's clips.
  6. No predicted marker SHALL be drawn over the photo without a `poseEstimate`.

### REQ-SKY-7: Real data and no invented values
- **Acceptance Criteria:**
  1. Each unit that changes the science or the job SHALL report the owner's head-worn clip through the real upload
     path (an ingest store outside `data/`) as counts and statuses only: status, findings per evidence class, the
     pose method, the frame used (seconds into the clip), the frame-centre offset bound, and time to finish. Never its
     name, capture time or place.
  2. No value in code, tests or docs SHALL be invented: every field of view, assumption limit and threshold names its
     source or is labelled [proposed] or [open] with the reason.

## Properties to test (derived from the requirements)

Python properties use hypothesis, TS properties fast-check; each runs under 20 seeds before a unit is ready, with
rare required cases seeded by `@example` or `examples`.

- **P-SKY-1** (REQ-SKY-1.1, 1.4): for any moment in range and any place, every predicted finding is above the
  horizon at the solution's `observedAtUTC` (recomputing its altitude from its RA and Dec gives at least 0), and
  every star of the bright list that is above the horizon is present exactly once.
- **P-SKY-2** (REQ-SKY-1.2, 1.3, 3.7, 5.2): for any solver outcome, adding the predicted sky or a pose never changes
  an `observed` finding's pixels, never adds a sky position to an `observed` finding, never creates a
  `catalog_matched` finding, never sets `passedIndependentVerification`, and a `calibrated` input keeps its status,
  WCS, quality and every non-predicted finding byte for byte.
- **P-SKY-3** (REQ-SKY-2.1 to 2.3): for any asset lacking a recorded time, a place, or a moment in range, the solution
  has no predicted finding, no `observedAtUTC` when the time is missing, and the status the solver gave.
- **P-SKY-4** (REQ-SKY-1.6): the sky-phase sentence is a pure function of the Sun's altitude, and its band changes
  exactly at -0.833, -6, -12 and -18 deg.
- **P-SKY-5** (REQ-SKY-3.1, 3.5): for any synthetic truth (centre altitude and azimuth with the Moon in frame, field
  of view inside the profile's range, roll inside the stated range) and a frame with a Moon blob drawn where the
  truth puts it, the estimator returns `approximate`, the topocentric Moon projects onto the blob within 0.5 px
  under every swept WCS, and each in-frame predicted star's true pixel lies inside its drawn ring.
- **P-SKY-6** (REQ-SKY-3.3): a frame with two bright spots of comparable half-maximum area, or with only spots
  smaller or larger than the Moon test allows, gives no pose (`predicted_only`, "not confirmed as the Moon").
- **P-SKY-7** (REQ-SKY-5.1 to 5.3): TS and Python accept and refuse the same generated solutions with and without a
  pose estimate (status not `approximate`, no `wcs`, fewer than two swept WCS, a swept WCS of another size, a
  predicted finding with a `sourceDetection`).
- **P-SKY-8** (REQ-SKY-6.2, 6.6): for any `approximate` solution with a pose estimate, every predicted marker drawn
  over the photo is at the nominal WCS projection of its sky position (mapped through crop and display
  orientation), and its ring radius is at least the largest distance to its projections under the swept WCS; with no
  pose estimate, no predicted marker is drawn over the photo.
- **P-SKY-9** (REQ-SKY-6.4): for a clip, a marker is visible only when the clip is not playing and the clock is
  within max(one frame, 0.05 s) of the marker's own frame, and typing the command the line quotes lands inside that
  window.

## Decisions taken by the lead (2026-11-10) [proposed, open to the owner]
- Head roll assumed level within 10 deg either way; the skyline 0 to 3 deg above the true horizon.
- The head-worn field of view range for the Meta Ray-Ban Display glasses: the maker publishes 100 deg for the
  Ray-Ban Meta glasses' ultra-wide camera without naming the axis (ray-ban.com), and no figure was found for the
  Display model; its clip frames (1344 x 1792) are smaller than the 1440 x 1920 video size retailers list, so a crop
  may narrow it [inference]. The profile takes 80 to 110 deg diagonal, the range the phone benchmark already uses
  for its modelled head-worn class (`research/benchmarks/phone_photos/cameras.py`), labelled [open].
- An ambiguous plate solve becomes `predicted_only` when the predicted sky is added (its quality and warnings stay),
  so the person is offered the sky.
