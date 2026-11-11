# Tasks: restoration and export for every upload

Each task is one unit: one commit, tests first and seen failing for the stated reason, mutants on its risky lines,
its properties under 20 seeds, all six gates, an independent audit, then the lead lands it. Unit ids are
`restore-<n>`. The files this document names for a unit are approved by the lead when the spec lands, the shared
ones it lists included; a unit asks only before touching a file this document does not name for it [decided
2026-11-11: one yes for the plan, so a unit does not wait for a second]. Every unit that changes pixels reports
real photos and, for clips, the owner's clips as counts only (REQ-RST-12), and writes its before and after
pictures under `.cache/restore-review/<unit>/`.

- [ ] 0. restore-0: this spec, and the corrections of the two older specs' task lists (decision 12)
  - `.kiro/specs/restoration-for-every-upload/requirements.md`, `design.md`, `tasks.md`;
    `.kiro/specs/media-experiment-runtime/tasks.md`; `.kiro/specs/export-and-provenance/tasks.md`.
  - Docs only. Measurements behind it: `design.md`, "Measured facts". Written; checked here when the lead lands
    it.

- [ ] 1. restore-1: the contracts (REQ-RST-10; P-RST-20)
  - `packages/contracts/src/media.ts`: the shapes of `design.md` section 14, and the removals listed there.
  - `services/contracts/media.py` (new) with `build_media_samples()`; `services/contracts/__init__.py`.
  - `tests/fixtures/media_contract.json` (new); `tests/integration/test_media_contract.py` (new);
    `packages/contracts/src/__tests__/media.test.ts` (new); the media cases of
    `packages/contracts/src/__tests__/contracts.test.ts`.
  - Adapted in the same unit, all shared files: `packages/contracts/src/spatial-and-voice.ts`,
    `packages/observation/src/reducer.ts`, `packages/observation/src/checkpoints.ts`,
    `packages/observation/src/emptySession.ts`, `packages/observation/src/testing/syntheticSessions.ts` and their
    tests, `scripts/verify-contracts.mjs`.
  - It also marks task 2 of `media-experiment-runtime` as superseded, since it removes the DAG validator and the
    cache keys [decided 2026-11-11: nothing branches or caches, and the contract must not describe what does not
    exist].
  - The session's `media.restore` stays honest: it still refuses plainly when no variant exists, with a test. The
    web app is wired to the new routes later, by its own lane [decided 2026-11-11: the session must not claim a
    variant it does not have, and the web app is another lane's files].
  - Done when: both languages accept and refuse the same fixture samples and the same generated faults; no
    removed field is read anywhere; `pnpm typecheck` and `pnpm verify:contracts` pass. Nothing produces a record
    yet.
  - _Requirements: REQ-RST-10.1 to 10.4_

- [ ] 2. restore-2: the variant store, the routes, the job and the display copy (REQ-RST-1, REQ-RST-2, REQ-RST-3 for
  stills, REQ-RST-8; P-RST-1, 3, 4, 5, 6, 18, 19)
  - New: `services/media/colour.py` (the four transfer functions, the matrices, the ranges, chroma siting, ICC
    curves), `services/media/decode.py` (stills at 8 and 16 bits, HEIC tiles in colour),
    `services/media/png16.py`, `services/media/variants.py`, `services/media/jobs.py`; tests under `tests/media/`.
  - Changed: `services/media/router.py` (the restore, job, list, records and file routes; the old array route
    stays until restore-9), `tests/fixtures/api_surface.json`.
  - Outside `services/media/`, named here for this unit: `services/ingest/metadata.py` (`read_heif` returns the
    primary item's colour property).
  - The job runs kind `display_copy` end to end for stills and refuses it for clips; kind `restoration` answers
    with a plain "not built yet" until restore-4.
  - The client asks for a display copy through the same route as any other variant; ingest makes nothing on its
    own, with a test that an upload alone leaves no `variants/` folder [decided 2026-11-11: one route for every
    variant]. A clip gets no display copy in this spec [open].
  - Real data: a display copy of each of the five photos compares equal; a HEIC from the real-photo corpus once
    it holds one, else a HEIC built in the test where ffmpeg can, with the refusal asserted on the older build.
  - Done when: a HEIC upload gets a PNG the browser draws, stored beside the original with modes 0400 and 0700,
    listed and served by its hash; a changed sample fails verification; the original's hash and mode are
    untouched.
  - _Requirements: REQ-RST-1.1 to 1.7, 2.1 to 2.7, 3.1 to 3.6, 8.1 to 8.8_

- [ ] 3. restore-3: the photo path as pure functions (REQ-RST-4, REQ-RST-5, REQ-RST-6.1, 6.2, REQ-RST-8.9; P-RST-2,
  7, 8, 9, 10, 12)
  - New: `services/media/stars.py`, `services/media/noise.py`, `services/media/spatial.py`,
    `services/media/tone.py`; tests under `tests/media/`. These take arrays and return arrays and records; the
    lift takes the transfer's slope as an argument, so the unit imports nothing from restore-2.
  - The detector's matched-filter noise: the unit imports the two private helpers of
    `services/science/detector.py` for now, as `services/media/runtime.py` does today. The lane that owns the
    detector will publish one shared function for it, and restore switches to it when it lands [decided
    2026-11-11: the same measure is needed to stop the detection flood in the solver].
  - Speed: the unit reports the still path's seconds per megapixel on one core, per step, and brings it to the
    target [decided 2026-11-11 that it does; proposed: at most 0.5 s per megapixel on one core]. If it misses, it
    says what dominates the time.
  - Real data: on the five photos, the noise table per channel, the fine noise left, the stars found and
    confirmed, the footprint radii and the seconds per megapixel, against the numbers of `design.md` M5, M7, M12
    and M13.
  - Done when: the properties hold; the constants test of REQ-RST-4.7 passes; the noise table matches a known
    synthetic noise within 20 percent; no output sample leaves the range of its inputs.
  - _Requirements: REQ-RST-4.1 to 4.8, 5.1 to 5.4, 6.1, 6.2, 8.9_

- [ ] 4. restore-4: the integrity gate, and photos restored end to end (REQ-RST-6, REQ-RST-12; P-RST-11, 13)
  - New: `services/media/gate.py` (the variant's points, confirmation, the dissolve passes, flux),
    `services/media/encode.py` (stills), `scripts/restore_review.py`; tests under `tests/media/`.
  - Changed: `services/media/jobs.py` (kind `restoration` for stills).
  - The gate's statistic is chosen in this unit, by measurement [decided 2026-11-11: the detector's own statistic
    refused none of 40 painted stars on the most lifted crop, so it cannot be the gate as it stands]. First
    candidate: the detector's statistic with the local background at the star scale removed. What it has to
    reach: on every real photo of the corpus, lifted as the job lifts it, a painted star at the stated sensitivity
    (REQ-RST-6.7) is refused in every trial, and the honest run still ends `ready` because the pipeline dissolves
    the points the stricter statistic flags.
  - The unit reports, per real photo: the painted stars refused, the honest points flagged and dissolved, and
    what the dissolving costs in visible detail. If no statistic meets both on the real photos, it says "unsolved
    on real data" with the numbers and leaves kind `restoration` answering "not built yet": none is served.
  - A still's variant is the 16 bit PNG and the gate runs on that stored file [decided 2026-11-11: what is served
    is the file the gate ran on]; no other rendition is written or served by this spec.
  - The default lift target, the quantisation bound and the flux tolerance stay [proposed]: the unit writes
    before and after pictures of every real photo for the lead and the owner to judge, and the values are set
    from those [decided 2026-11-11: these are choices a person has to look at].
  - Real data: each of the five photos through the job: status, points, dissolved per pass, flux, the lift and
    what decided it, time to finish; before and after pictures for the lead.
  - Done when: an honest restoration of each real photo ends `ready`; a painted star at the stated sensitivity
    ends `rejected` with its count and no file, in every trial on every real photo; a `rejected` attempt is stored
    and never listed as a variant.
  - _Requirements: REQ-RST-6.1 to 6.9, 6.12, 6.13, 12.1, 12.3, 12.4_

- [ ] 5. restore-5: clip input and output (REQ-RST-3.7, REQ-RST-7.5 to 7.7, 7.9, 7.10; P-RST-15, 16, 17)
  - New: `services/media/clip_io.py` (probe, the streamed decode with each frame's time, the streamed encode with
    the frame times stamped and the audio copied, the rotation pass, the checks on the stored clip); tests under
    `tests/media/`. It moves bytes, planes and times only, and imports nothing from restore-2 or restore-3.
  - Every ffmpeg feature it uses is exercised on both CI builds: `-fps_mode passthrough`, `showinfo`,
    `-noautorotate`, `settb`, `setpts`, `setparams`, `-enc_time_base`, `-copyts`, `-map 1:a?`, the `hash` muxer,
    `-display_rotation`, `libx264`; `libx265` where present, with the plain refusal where not.
  - Real data: the owner's clips copied through decode and encode with no processing: frame counts, whether frame
    times and audio hashes are equal, peak memory, time. Counts only.
  - Done when: for any list of frame times the stored clip has exactly those times; every audio stream has the
    original's packet hash and times; peak memory does not grow with the clip's length; all eight display
    orientations read back.
  - _Requirements: REQ-RST-3.7, 7.5, 7.6, 7.7, 7.9, 7.10_

- [ ] 6. restore-6: registration and the temporal step (REQ-RST-7.1 to 7.4, 7.8, REQ-RST-6.10, 6.11; P-RST-14, 22)
  - New: `services/media/temporal.py`; tests under `tests/media/`. Needs the noise table of restore-3.
  - It also builds, as pure functions, what the gate needs for a clip [decided 2026-11-11: a real star too faint
    for one frame is in the original pixels of many frames, so showing it paints nothing]: the frames together
    (the plain mean of the registered original frames of a window, no weight, no filter) and the unshifted mean
    of the same frames, with the test that a point drawn only into the neighbours is kept out (P-RST-14).
  - Real data: on the owner's clips, for how many frames registration held, the closure error, the share of exact
    repeats, the fine noise left with and without the step. If it shows no gain there, the report says "unsolved
    on real data" with the measured reason.
  - Done when: a point drawn only into the neighbours never appears in a frame; a neighbour that disagrees gets
    a weight under one tenth; a frame whose neighbours fail registration equals its single-frame result.
  - _Requirements: REQ-RST-7.1 to 7.4, 7.8, 6.10, 6.11_

- [ ] 7. restore-7: clips restored end to end (REQ-RST-7, REQ-RST-6.10, 6.11, REQ-RST-8.3, 8.9, REQ-RST-12.2;
  P-RST-23, 24)
  - Changed: `services/media/jobs.py` (kind `restoration` for clips: sampled frames for the noise table and the
    lift, then per frame the merge, the stars, the filter, the dissolve passes and the lift, streamed; the checks
    and the gate on the stored clip), `services/media/gate.py` (a clip's point confirmed on its original frames
    together, with the two guards), `services/media/encode.py` (clips), `scripts/restore_review.py`.
  - A clip's faint stars [decided 2026-11-11: a real star too faint for one frame is in the original pixels of
    many frames, so showing it paints nothing]: a point of a frame made with neighbours is confirmed on the
    original pixels of those frames together; what they do not confirm is dissolved, and the gate refuses it if
    it remains. Two tests, one per guard: nothing present only in the neighbours (P-RST-14, on the stored clip),
    and nothing that stays on one sensor pixel while the registration says the scene moved (P-RST-24). P-RST-23
    shows the faint star that the frames together confirm.
  - Frames run in a bounded pool of worker processes, default half the cores [proposed], with the same stored
    file whatever the pool's size [decided 2026-11-11: the product may use the laptop's cores at run time; the
    limit of two processes is for agents' test runs]. So the unit's tests use a pool of at most two workers, and
    the timed run on the owner's clips, which uses the default pool, is the one run above that limit: the unit
    asks the lead for a time to run it [proposed].
  - Real data: each of the owner's clips through the job: status, frames, points confirmed on one frame, on the
    frames together and refused as fixed to the sensor, the lift and what decided it, the notes, peak memory, and
    the time to finish against the target [proposed: a 15 second clip of 2.4 megapixel frames in under 10 minutes
    on this laptop]. Counts only. If it misses the target, the report says what dominates the time. The rate in
    the bound of `design.md` section 11 is replaced by the measured one.
  - The encoder quality stays [proposed]: the unit writes before and after pictures and short clips for the lead
    and the owner to judge, and the value is set from those [decided 2026-11-11: a choice a person has to look
    at].
  - Done when: a clip ends `ready` or `rejected` with stored counts inside its bound; a clip recorded at black
    ends with its plain note and a lift held by the quantisation bound.
  - _Requirements: REQ-RST-6.10, 6.11, 7.1 to 7.10, 8.1 to 8.5, 8.9, 12.2 to 12.4_

- [ ] 8. restore-8: export and the manifest (REQ-RST-9; P-RST-21)
  - Changed: `services/media/router.py` (the export and manifest routes), `services/media/variants.py` (the
    manifest builder), `tests/fixtures/api_surface.json`; tests under `tests/media/`.
  - An export carries no capture time, place, file name or container tag from the original [decided 2026-11-11:
    an export is made to be shared; the owner can ask for their own metadata later], with a test on a synthetic
    upload planted with all four.
  - Done when: the exported bytes hash to the manifest's variant hash; the manifest parses in both languages and
    holds no title, time, place or path; a rejected attempt has nothing to export.
  - _Requirements: REQ-RST-9.1 to 9.7_

- [ ] 9. restore-9: the corrections of old code (REQ-RST-11)
  - Removed: `POST /api/media/restore` with arrays, `execute_restoration_branch`,
    `export_video_with_audio_remux`, `RestorationMetrics`, `RestorationBranchResult`, `ExportRemuxResult` and the
    tests that only cover them (`tests/integration/test_media_measures_what_it_reports.py`, EXP-04 and EXP-10 and
    the restore part of the endpoint test in `tests/integration/test_science_experiments.py`), each replaced by a
    test of the job that covers the same promise; `scripts/render_clip_variants.py` and
    `scripts/run_real_media_experiment.py` removed or rewritten as callers of the job;
    `services/media/silhouette.py` and its two tests in `tests/integration/test_frontier_upgrades.py` removed,
    since only the old runtime uses it.
  - A characterization list in the unit's report: every removed test with the new test that replaces it, or the
    reason it describes something that no longer exists.
  - Done when: each row of `design.md` section 15 is cut or made real, and a search for the removed names finds
    none.
  - _Requirements: REQ-RST-11.1 to 11.3_

## Order and what can run in parallel

- restore-1 first, alone: everything else builds on its shapes.
- Then restore-2, restore-3 and restore-5 together: they share no file.
- restore-4 after restore-2 and restore-3. restore-6 after restore-3; it can run beside restore-4.
- restore-7 after restore-4, restore-5 and restore-6. restore-8 after restore-4; it can run beside restore-7
  (`router.py` and `variants.py` against `jobs.py`, `gate.py` and `encode.py`).
- restore-9 last.

## Not units of this lane

- The web app is wired to the new routes by its own lane (`apps/web/src/`, owner `experience`): it asks for a
  display copy after a HEIC or TIFF upload, shows variants and offers the export, once restore-2 has landed
  [decided 2026-11-11: the web app is that lane's files].
- One shared function for the detector's matched-filter noise in `services/science/detector.py`, published by the
  lane that owns the detector; restore switches to it when it lands [decided 2026-11-11: the solver needs the same
  measure to stop its detection flood].
- A smaller 8 bit rendition of a still for a phone on a slow link: not part of this spec [open]; if one is ever
  served, it is gated as its own file.
- OpenCV as a dependency (`design.md` section 18): proposed, not scheduled.
