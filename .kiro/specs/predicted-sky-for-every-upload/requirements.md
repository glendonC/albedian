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

### REQ-SKY-3: Lined up by taking a bright spot to be the Moon, a known field of view and a level head (head-worn)
**User Story:** As someone who filmed the Moon with camera glasses and no horizon in view, I want the predicted
sky lined up over my clip, with an honest sense of how far off it may be.
- **Acceptance Criteria** (as landed in sky-3, `tasks.md` task 3). The lead's decisions after its audit are written
  in: A (two level poses) as 3.8 and P-SKY-5 (b); B (the frame's own time) as 3.4; C (taken to be the Moon) as 3.3,
  3.10 and 3.11; D (the bound and the numbers) as 3.5, 3.6, 3.7 and 3.9; E and F were about the unit's tests and
  docs. Each criterion names the tests that check it, in `tests/science/test_pose_estimate.py`, except the names
  after *ingest* (`tests/integration/test_ingest_predicted_sky.py`) or *profiles*
  (`tests/science/test_camera_profiles.py`), up to the next semicolon or full stop.
  1. WHEN the solution is not `calibrated`, the predicted sky exists (REQ-SKY-1), the camera model matches a camera
     profile of kind `head_worn` (`services/science/camera_profiles.py`), the stored frame is not mirrored, the
     predicted Moon is more than 5 deg above the horizon and the Sun is not up (3.10) both at the solution's moment
     and at the own moment of one searched frame (3.4), and in that frame the brightest spot passes the Moon test
     (3.3), exactly one level pose exists at every swept value (3.8) and all nine fits are accepted (3.9), THE
     solution SHALL be `approximate`, with a nominal WCS fitted so that the topocentric
     Moon lands on the spot's centroid and the horizon is level in the upright frame (the frame as displayed, after
     `displayOrientation`). The Moon and the Sun are checked first at the solution's moment, before any frame is
     read, then again at each frame's own moment. Tests:
     `test_one_fit_is_the_pointing_that_roll_and_field_of_view_give`,
     `test_the_moon_is_lined_up_where_it_is_seen_from_the_place_not_from_the_earths_centre`; *ingest*
     `test_a_head_worn_clip_with_the_moon_is_lined_up_from_it`; each condition unmet:
     `test_a_calibrated_solution_never_takes_the_pose_path`,
     `test_without_a_head_worn_profile_there_is_no_pose_and_the_reason_is_given`,
     `test_a_mirrored_frame_or_no_frame_read_gives_no_pose`,
     `test_with_the_moon_down_there_is_no_pose_and_no_frame_is_read`, `test_a_moon_only_5_degrees_up_is_not_enough`,
     `test_a_frame_whose_moment_is_past_2050_is_not_searched`; the Sun at the solution's moment, before any frame is
     read: `test_by_day_no_moon_pose_is_tried_and_the_reason_is_given`.
  2. THE camera profile SHALL state the diagonal field of view as a range with its source, and each value's label:
     [verified] only when read at the maker's published specification for that exact model, else [open] with the
     reason; it SHALL be found by the asset's exact camera model string. The assumed head roll SHALL be within 10 deg
     either way [proposed]. Tests: *profiles* `test_the_glasses_profile_is_keyed_by_their_exact_model_tag`,
     `test_only_the_exact_model_string_has_a_profile`,
     `test_a_profile_states_an_ordered_range_inside_the_contracts_bounds_with_its_label_and_source`,
     `test_the_source_note_says_where_the_figure_was_read_and_what_is_open`; the head roll:
     `test_the_sweep_takes_the_profiles_limits_and_middle_and_rolls_of_ten_degrees_either_way`.
  3. THE Moon test SHALL find the spot by its half-maximum contour, not by saturation (decided 2026-11-11: the
     owner's clip is limited-range video whose Moon is a soft peak, never a flat clipped core). The candidate is the
     frame's brightest point after a 2.5 px Gaussian. It passes when its own code is at least 200 of 255 and at least
     10 noise units above the sky (the whole frame's median, and 1.4826 times its median absolute deviation); its
     region (the 8-connected pixels above the sky plus half the peak's height) has an equivalent diameter
     `2 sqrt(area / pi)` of at least 0.8 times the Moon's predicted angular diameter at the profile's widest field of
     view and at most 8 times it at the narrowest; and that region grown by 2 px stays off the frame's edge. Every
     other spot (each group of pixels at 200 or more outside the candidate's region, judged at its own brightest
     point after the same smoothing) SHALL be judged by the same test on its own, the edge aside, whatever its area
     beside the candidate's; a
     region that holds the candidate's own peak is the candidate. WHEN the candidate fails, its region is cut by the
     edge ("the brightest spot is cut by the edge of the frame") or another spot passes ("more than one spot passes
     the Moon test on its own"), THE frame SHALL give no pose; with no frame passing, the solution SHALL stay
     `predicted_only` and say "not confirmed as the Moon", with the rule in plain words. Tests:
     `test_a_peak_under_200_is_refused_and_200_is_enough`,
     `test_the_peak_must_be_at_least_10_times_the_noise_above_the_sky`,
     `test_one_hot_pixel_brighter_than_the_moon_is_not_taken_for_it`,
     `test_the_region_is_above_the_half_peak_level_and_the_centroid_is_measured_over_it_grown_by_two`,
     `test_the_blob_must_be_at_least_its_minimum_and_at_most_its_maximum_equivalent_diameter`,
     `test_the_size_limits_are_the_moons_predicted_diameter_at_the_profiles_two_limits`,
     `test_a_spot_cut_by_the_edge_of_the_frame_is_refused` (the 2 px margin is pinned on the left edge only; on the
     other three edges only a spot touching the edge is, verdict sky-3-v5 item 8; tests for sky-4, task 5),
     `test_a_rival_is_judged_by_the_moon_test_on_its_own_not_by_its_area_beside_the_first`,
     `test_every_other_spot_is_judged_by_the_same_limits_on_its_own`,
     `test_a_lamp_twice_the_moons_width_beside_a_moon_sized_spot_is_refused`,
     `test_a_rival_cut_by_the_frames_edge_still_refuses_and_the_moons_own_halo_is_no_rival`,
     `test_a_peak_under_200_gives_no_pose_with_the_rule_in_plain_words`; P-SKY-6. That a rival is judged at its
     brightest point after the same smoothing, not at its raw maximum, no named test pins (the audit found that a
     rival taken at its raw maximum passes them all, and that a small rival with one brighter pixel at the edge of
     its core tells the two apart) [open, verdict sky-3b-v3 note (a); a test for sky-4, task 5]. Nor does any test
     pin the smoothing's width of 2.5 px: with a Gaussian of 1.5 px every test of the three files still passes [open;
     a test for sky-4, task 5].
  4. FOR a clip, THE job SHALL take the clip's own frames from its video packets' presentation timestamps (ffprobe,
     sorted, without the packets flagged D to discard), spread n = min(N, 16) of its N frames over the whole clip by
     index (`floor((k + 0.5) N / n)` for k = 0 to n - 1, so a clip of fewer than 16 frames gives each frame once), and
     decode each alone at stored pixel size, the display matrix left as
     metadata (`-noautorotate`) and the file's timestamps kept, keeping a frame only when the decoded frame's own
     timestamp is the one asked for and its size is the stored size. THE solution's `frameTimeSec` SHALL be the used
     frame's own presentation timestamp less the first frame's, so its time and its spot are one frame's. The frames
     are tried in time order, the first that gives a pose is used, and none are stacked; a photo uses its one decoded
     frame. A frame that fails or is not the one asked for is skipped; the probe and every decode share the decoder's
     one time bound, and a timeout ends the search without raising. Tests:
     `test_frames_are_tried_in_time_order_and_the_first_that_passes_is_kept`; *ingest*
     `test_on_a_clip_with_uneven_frame_times_each_time_is_the_own_time_of_the_frame_decoded`,
     `test_the_spread_frames_are_the_frames_their_index_names`,
     `test_the_frame_list_is_the_packets_presentation_timestamps_without_discarded_ones`,
     `test_a_decoded_frame_whose_own_timestamp_is_not_the_one_asked_for_is_skipped`,
     `test_a_clip_stored_turned_is_decoded_as_stored`,
     `test_a_spread_frame_that_fails_or_comes_back_at_another_size_is_skipped_and_the_search_goes_on`,
     `test_a_spread_frame_that_times_out_ends_the_search_without_raising`,
     `test_every_spread_frame_shares_the_decoders_one_time_bound`,
     `test_a_photo_from_the_glasses_with_the_moon_is_lined_up_from_its_own_frame`,
     `test_a_head_worn_clip_whose_spread_frames_cannot_be_read_keeps_the_predicted_sky_and_says_why`. No test has a
     clip whose first frame is after 0 s, so the subtraction of the first frame's timestamp and the file's start
     time in the seek are not pinned [open, verdict sky-3-v5 item 8; tests for sky-4, task 5]. Nor is the probe's
     share of the one time bound: the *ingest* test `test_every_spread_frame_shares_the_decoders_one_time_bound`
     records the decodes' time bounds only, and a probe given the whole bound passes every test of the three files
     [open, verdict sky-3b-v4 finding 2; a test for sky-4, task 5].
  5. THE pose estimate SHALL record: the method, the nominal centre altitude and azimuth, roll and field of view,
     every assumption in plain words (the field of view range, no zoom, a level head, a rectilinear lens, the spot
     taken to be the Moon, the beacon term, the topocentric Moon), the field of view's source, the nine swept WCS
     (3.9), `maxCenterOffsetDeg` (the largest angle between the nominal centre and any swept one), and
     `beaconCentroidUncertaintyDeg`: the Moon's predicted radius plus the Moon's own stated position uncertainty, read
     from the ephemeris record at run time, never a typed number. THE warning's "within about N degrees" SHALL have
     N = ceil(`maxCenterOffsetDeg` + `beaconCentroidUncertaintyDeg`). Tests:
     `test_the_beacon_term_is_the_moons_radius_plus_its_stated_uncertainty_read_at_run_time`,
     `test_the_sweep_takes_the_profiles_limits_and_middle_and_rolls_of_ten_degrees_either_way`,
     `test_the_approximate_solution_carries_the_pose_the_moon_and_the_predicted_sky`. The words of two assumptions,
     the level head and the beacon term, no test pins: each reworded, every test of the three files still passes
     [open; a test for sky-4, task 5].
  6. THE spot SHALL be an `observed` finding (`finding-observed-moon`) with its measured centroid and no sky
     position, `verifiedByIndependentHoldout` false, and a verification method that claims no held-out check
     ("Measured in the frame's pixels: ... nothing was matched to a catalog and no held-out check was made"); a
     detector detection of the same frame within the spot's radius SHALL be left out so the spot is not listed twice.
     Tests: `test_the_approximate_solution_carries_the_pose_the_moon_and_the_predicted_sky`,
     `test_under_a_pose_only_the_detectors_observed_findings_are_kept`. Two frame times are the same frame within a
     microsecond; that microsecond doubled or halved passes the tests 3.6 names, which put other frames' detections
     seconds away [open, a test for sky-4, task 5].
  7. A pose estimate SHALL never set `passedIndependentVerification`, never make any finding `catalog_matched` and
     never give an `observed` finding a sky position; its quality SHALL count no matched star and no held-out pair
     (`matchedInlierCount` 0: an assumed beacon is matched to no catalog). Tests:
     `test_the_approximate_solution_carries_the_pose_the_moon_and_the_predicted_sky`; *ingest*
     `test_a_head_worn_clip_with_the_moon_is_lined_up_from_it`; P-SKY-5 (a).
  8. THE level-head pointings that see the Moon at the spot SHALL be found in closed form (design component 4) at
     each of the nine swept values: none, one or two. WHEN any swept value has two, THE pose SHALL be refused before
     any fit, the solution staying `predicted_only` and saying that the bright spot's place in the frame "fits two
     different directions of view with a level head inside the stated field of view and roll, so which way the
     camera faced is not known"; WHEN none has two but any has none, saying that "the stated ranges of field of view
     (...) and head roll (...) could not all be fitted with a level head". Tests:
     `test_the_closed_form_finds_every_level_pose_the_independent_search_finds`,
     `test_a_level_pose_count_that_is_not_one_stays_not_one_at_any_wider_field_of_view`,
     `test_two_level_poses_refuse_the_pose_with_a_plain_sentence`,
     `test_two_poses_at_one_swept_value_and_none_at_another_refuse_for_the_two_directions`,
     `test_a_swept_value_with_no_level_pose_refuses_with_a_sentence_that_is_true`,
     `test_one_fit_refuses_a_pixel_with_two_level_poses_or_none`; P-SKY-5 (b).
  9. THE sweep SHALL be nine fits: the field of view at the profile's two limits and middle, the roll at -10, 0 and
     +10 deg [proposed]; the nominal is the middle field of view at roll 0. Each fit SHALL start at the closed form's
     pose and SHALL be refused when its largest residual exceeds 0.5 px, its upright frame shows the sky upside down,
     or its centre leaves the closed form's pose by more than 0.001 deg [proposed]; a fitted centre past a pole SHALL
     be brought back inside the contract's range as the same pointing. Any refused fit SHALL refuse the pose, saying
     that the fit did not put the Moon on the bright spot. Tests:
     `test_the_sweep_takes_the_profiles_limits_and_middle_and_rolls_of_ten_degrees_either_way`,
     `test_the_fit_is_accepted_only_up_to_half_a_pixel`, `test_a_fit_that_shows_the_sky_upside_down_is_refused`,
     `test_a_fit_that_leaves_the_closed_forms_pose_is_refused`, `test_any_refused_swept_fit_refuses_the_pose`,
     `test_a_refused_fit_gives_no_pose_with_the_reason`,
     `test_a_centre_past_a_pole_is_the_same_pointing_brought_back_inside_the_range`,
     `test_a_fit_that_ends_past_a_pole_is_brought_back_inside_the_contracts_range`. That each fit starts at the
     closed form's pose no test pins: a fit started 2 deg higher is polished back to that pose, which the 0.001 deg
     rule holds, and every test of the three files still passes [open; a test for sky-4, task 5]. Nor is that
     rule's 0.001 deg: `test_a_fit_that_leaves_the_closed_forms_pose_is_refused` moves the centre by a whole degree,
     and the bound doubled or halved passes the tests 3.9 names [open, verdict sky-3b-v4 finding 1; a test for sky-4,
     task 5].
  10. WHEN the Sun's centre is above -50/60 deg (the USNO sunrise and sunset altitude, the day limit of
      REQ-SKY-1.6) at the solution's moment or at a frame's own moment, no Moon pose SHALL be tried there. The Moon
      is checked before the Sun, at the solution's moment as at each frame's own: when the predicted Moon is not more
      than 5 deg up, the reason given SHALL be that the Moon was not up (3.12's kinds), by day or not. THE solution
      SHALL say that "the Sun was above the horizon at the frame's moment, and by day a bright spot may be the Sun"
      when the Moon is more than 5 deg up and the Sun is up at the solution's moment, and otherwise where the order
      of 3.12 comes to it. Test: `test_by_day_no_moon_pose_is_tried_and_the_reason_is_given` (the Moon 15 deg up).
      By day with the Moon down, the usual daytime case, no named test sees which reason is given [open, not yet
      tested; a test for sky-4, `tasks.md` task 5].
  11. Nothing in one frame proves the spot is the Moon, so no title, subtitle, warning, limitation, assumption or
      verification text SHALL say it is; those that name the spot as the Moon say it was taken to be the Moon. The
      spot's title SHALL be "Bright Spot (Taken To Be The Moon)"; the warning SHALL begin "Lined up by taking the
      bright spot at (x, y) px <where> to be the Moon"; the spot's first limitation SHALL say that a lamp or another
      light of similar size would be taken the same way; its verification text, the pose's assumption about the spot
      and the limitation on each predicted object inside the frame SHALL say it was taken to be the Moon. Tests:
      `test_no_text_says_the_bright_spot_is_the_moon`,
      `test_the_approximate_solution_carries_the_pose_the_moon_and_the_predicted_sky`,
      `test_a_soft_peaked_moon_at_244_is_lined_up_from_it`; the pose's assumption about the spot:
      `test_the_sweep_takes_the_profiles_limits_and_middle_and_rolls_of_ten_degrees_either_way`. One landed sentence
      breaks the rule: `OTHER_FRAMES_KEPT` (`services/science/predicted_sky.py`), the spot's limitation whenever
      detections from other frames of the clip are kept, calls the spot "this frame's Moon pixel" without "taken to
      be", and `test_no_text_says_the_bright_spot_is_the_moon` builds a solver with no detections, so it never sees
      that text [open, a known gap, verdict sky-3b-v3 note (c); the sentence and its test are fixed in sky-4, task 5].
      Nor is the rule held for every wording: `test_no_text_says_the_bright_spot_is_the_moon` refuses a list of
      phrases, and the spot's subtitle or second limitation reworded to name the spot as the Moon in other words
      ("The Moon, measured at", "the Moon has no sky position") passes the tests 3.11 names [open; a test for sky-4,
      task 5, that holds those two texts word for word].
  12. WHEN no searched frame gives a pose, THE solution SHALL stay `predicted_only` (REQ-SKY-1.2) with one more
      warning, the one that says most: a spot that passed the Moon test but gave no pose (3.8, 3.9), with the time of
      the first such frame in time order; else not confirmed as the Moon, counting the frames searched; else the Moon
      not up in every
      frame; else by day (so frames that mix the Moon not up and by day answer by day). Tests, each with frames of
      one kind only: `test_a_swept_value_with_no_level_pose_refuses_with_a_sentence_that_is_true` and
      `test_a_refused_fit_gives_no_pose_with_the_reason` (one frame whose spot gave no pose, with its time),
      `test_a_spot_not_confirmed_as_the_moon_gives_no_pose_with_the_reason` (two frames not confirmed, counted),
      `test_a_frame_whose_moment_is_past_2050_is_not_searched` (one frame not confirmed, "in the frame searched"),
      `test_with_the_moon_down_there_is_no_pose_and_no_frame_is_read` (one frame with the Moon not up) and
      `test_by_day_no_moon_pose_is_tried_and_the_reason_is_given` (one frame by day). No test mixes kinds of refusal
      across frames, so the order between kinds is not yet tested, nor which frame's time is said when several spots
      gave no pose; these are tests for sky-4 (`tasks.md` task 5) [open].

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
     matched to N stars in the photo (`calibrated`); lined up by taking a bright spot to be the Moon or the Sun,
     within about N deg, not checked against stars (`approximate`); for `predicted_only`, what the solver found when
     a star search ran ("Too few points of light to match to stars." under 6 detections, else "Found N points of
     light, but could not match them to stars for sure.") and that the predicted sky can be opened ("You can open the
     sky predicted for your photo's time and place."), never that it is being shown, since nothing is drawn over the
     photo until the person opens it; or why there is no sky (REQ-SKY-2).
  2. WHEN the solution is `approximate` with a `poseEstimate`, THE photo view SHALL draw each predicted finding that
     projects inside the frame through the nominal WCS, with the predicted marker (dashed circle) and a ring whose
     radius covers its position under every swept WCS plus the centroid term.
  3. THE photo view SHALL show, beside those markers, a disclosure naming the basis (for example "Lined up by taking
     a bright spot to be the Moon, with the camera's field of view and a level head; not checked against stars") and
     the uncertainty. Nothing in one frame proves the spot is the Moon, so no sentence states it as fact.
  4. FOR a clip, THE stage SHALL show the frame the session's clock names, and every marker SHALL show only while
     the clip is not playing and the clock is within max(one frame, 0.05 s) of the marker's own frame; otherwise the
     view SHALL say, in seconds, where the sky is lined up and the words that go there ("Lined up 3.4 seconds into
     the clip. Say or type "jump to 3.4 seconds" to see it.").
  5. WHEN the solution is `predicted_only` or `approximate`, "Show Predicted Sky" SHALL open the sky for the
     solution's time and place, as it does today for the laptop's clips.
  6. No predicted marker SHALL be drawn over the photo without a `poseEstimate`.
  7. No sentence SHALL call a marker's object a star unless its finding is `catalog_matched`: an unmatched point is a
     point of light, and a marker sits "where its light was measured".
  8. A session builder whose solver ran no star search (clips discovered on the laptop go only through the Moon and
     horizon solver) SHALL make no statement about points of light. Whether a search ran is an explicit input, never
     inferred from a solver name or a count.

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
- **P-SKY-5** (REQ-SKY-3.1, 3.5, 3.7 to 3.9), two properties in `tests/science/test_pose_estimate.py` over one
  draw of synthetic night truths across the whole stated range: the Moon 5 to 90 deg up, seen from places in both
  hemispheres along any bearing from the point under the Moon (so its azimuth near 0 and 360 deg too); its pixel
  anywhere in the frame inside a margin that keeps the drawn Moon whole, corners included; field of view 80 to
  110 deg; roll within 10 deg; any of the four display turns; the camera at either level pose when there are two,
  found by the test's own search, not by the closed form; the Moon drawn soft at 1 to 3 times its size, blurred
  0.5 to 2.5 px, peaking at 200 to 255; the frame 0 to 15 s into the clip.
  (a) `test_pbt_p_sky_5_whenever_the_answer_is_approximate_the_truth_lies_inside_its_bound`: the answer is
  `predicted_only` with no WCS and no pose, or `approximate`; when `approximate`, its frame time and moment are the
  frame's own, the spot's centroid is within 0.25 px of the drawn Moon's centre, the topocentric Moon projects within
  0.5 px of that centroid under the nominal and every swept WCS, every predicted object whose true pixel is in the
  frame lies inside its ring (the largest distance from its nominal pixel to its nine swept pixels, plus the beacon
  term in pixels at the nominal scale), the true centre is within `maxCenterOffsetDeg` plus what the centroid's own
  miss moves the pose by, verification is not passed and no finding is `catalog_matched`.
  (b) `test_pbt_p_sky_5_whenever_two_level_poses_exist_the_answer_is_never_approximate`: with the Moon 40 to 90 deg
  up and two level poses at the truth's field of view and roll, the answer is `predicted_only` with no WCS and no
  pose, and its reason is the two directions of view or the ranges not all fitted (two poses can meet and vanish at a
  wider field of view). How often each outcome occurs by Moon altitude band is in `docs/science/PREDICTED_SKY.md`.
- **P-SKY-6** (REQ-SKY-3.3), two properties in `tests/science/test_pose_estimate.py`; in both the answer is
  `predicted_only` with no WCS, no pose and no spot finding, and its warning says "not confirmed as the Moon".
  (a) `test_pbt_p_sky_6_two_moons_that_each_pass_the_moon_test_give_no_pose`: two soft spots, of radius 4 to 20 px
  and 0.3 to 1 times that, peaking at 200 to 255, each passing the Moon test on its own by the test's own rule
  whatever their areas beside each other: the Moon test's reason is "more than one spot passes the Moon test on its
  own". (b) `test_pbt_p_sky_6_only_point_lights_smaller_than_the_moon_test_allows_give_no_pose`: one to six point
  lights at 200 to 255, the brightest one's region at half its peak under the minimum diameter: the reason names
  that size under the minimum. A spot over the maximum is pinned by the example test
  `test_a_lit_cloud_or_a_band_of_twilight_wider_than_eight_moons_is_refused_on_its_size`, not by a property.
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

## Decisions taken by the lead (2026-11-11), after the audits of sky-5
- What the detector found is "points of light"; only a `catalog_matched` finding is called a star (REQ-SKY-6.7).
- A `predicted_only` line offers the predicted sky ("You can open ...") and never says it is shown (REQ-SKY-6.1).
- The line for a lined-up sky says a bright spot was taken to be the Moon or the Sun (REQ-SKY-6.1, 6.3).
- The stage follows the session clock; a clip's markers show within max(one frame, 0.05 s) of their own frame, and
  otherwise the line names the time in seconds and quotes the command to type (REQ-SKY-6.4).
- A builder whose solver ran no star search says nothing about points of light (REQ-SKY-6.8).

## Decisions taken by the lead (2026-11-11), after the audit of sky-3
Written in as REQ-SKY-3.3 to 3.11 and P-SKY-5 (`.lead/kiro/requests/sky-3-v5-fix.md`, decisions A to D):
- A. Two level poses at any swept value refuse the line-up; none at any swept value refuses with a true sentence
  (REQ-SKY-3.8); P-SKY-5 (a) covers the whole stated range, and P-SKY-5 (b) checks that two poses never end
  `approximate` for the Moon 40 to 90 deg up (two poses can occur a little lower, about 35 deg up at a corner at
  110 deg, by the closed form [inference]; there 3.8's tests stand alone).
- B. `frameTimeSec` is the decoded frame's own presentation timestamp, and each spread frame is one real frame of the
  clip (REQ-SKY-3.4).
- C. Every title, warning, limitation, assumption and verification text that names the spot as the Moon says it was
  taken to be the Moon (REQ-SKY-3.11; the lead's ruling of 2026-11-12); no Moon pose while the Sun is up
  (REQ-SKY-3.10); more than one spot that passes the Moon test on its own, or a spot cut by the frame's edge,
  refuses (REQ-SKY-3.3).
- D. The bound's beacon term includes the Moon's own ephemeris uncertainty, read at run time (REQ-SKY-3.5); the
  spot claims no held-out check (REQ-SKY-3.6); an assumed beacon counts no matched star (REQ-SKY-3.7); a centre
  fitted past a pole is brought back inside the range (REQ-SKY-3.9).
