# Tasks: restoration and export for every upload

Each task is one unit: one commit, tests first and seen failing for the stated reason, mutants on its risky lines,
its properties under 20 seeds, all six gates, an independent audit, then the lead lands it. Unit ids are
`restore-<n>`. The files this document names for a unit are approved by the lead when the spec lands, the shared
ones it lists included; a unit asks only before touching a file this document does not name for it [decided
2026-11-11: one yes for the plan, so a unit does not wait for a second]. Every unit that changes pixels reports
real photos and, for clips, the owner's clips as counts only (REQ-RST-12), and writes its before and after
pictures under `.cache/restore-review/<unit>/`.

- [x] 0. restore-0: this spec, and the corrections of the two older specs' task lists (decision 12)
  - `.kiro/specs/restoration-for-every-upload/requirements.md`, `design.md`, `tasks.md`;
    `.kiro/specs/media-experiment-runtime/tasks.md`; `.kiro/specs/export-and-provenance/tasks.md`.
  - Docs only. Measurements behind it: `design.md`, "Measured facts". Landed on `main` as `b59ae181`.

- [x] 1. restore-1: the contracts (REQ-RST-10; P-RST-20)
  - `packages/contracts/src/media.ts`: the shapes of `design.md` section 14, and the removals listed there. No job
    schema and no job id pattern of its own: it adds the upload job runner's `JOB_ID_PATTERN`, which moves here
    (the upload jobs spec, design 3.8); the job is `UploadJobSchema` (platform-1).
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
  - Landed on `main` as `e9cdd008`.

- [x] 2a. restore-2a: colour, the still decode, the 16 bit PNG, the variant store and the display copy as plain
  functions (REQ-RST-1.2, 1.3, 1.5, 1.6, REQ-RST-2.1 to 2.4, REQ-RST-3.1 to 3.6; P-RST-1, 3, 4, 5, 6) [decided
  2026-11-12 by the lead: the part of restore-2 that needs no runner, so restore-4 need not wait for platform-5]
  - New: `services/media/colour.py` (the four transfer functions, the matrices, the ranges, chroma up at its siting
    and back down by the least squares left inverse of the same weights, ICC curves and a profile's tone curves),
    `services/media/decode.py` (stills at 8 and 16 bits, HEIC tiles in colour), `services/media/png16.py`,
    `services/media/variants.py` (ids, the store, the display copy's decode, encode, verify and store); tests under
    `tests/media/`; two synthetic colour HEIC fixtures under `tests/fixtures/media/`.
  - Outside `services/media/`, named here for this unit: `services/ingest/metadata.py` (`read_heif` returns the
    primary item's colour properties).
  - Not in this unit: `services/media/jobs.py`, `services/media/router.py`, `tests/fixtures/api_surface.json`.
  - Real data: a display copy of each of the five photos compares equal; the HEIC tests use the sips fixtures, the
    refusal asserted where ffprobe cannot report tile grids.
  - Done when: a display copy of a generated 8 or 16 bit PNG or TIFF, and of a HEIC, decodes to exactly the
    decoded original and is stored with modes 0400 and 0700 without overwriting; a changed sample fails
    verification; the original's hash and mode are untouched.
  - _Requirements: REQ-RST-1.2, 1.3, 1.5, 1.6, 2.1 to 2.4, 3.1 to 3.6_
  - Landed on `main` as `ee2436c5`.

- [x] 2b. restore-2b: the job and the routes (REQ-RST-1.1, 1.4, 1.7, 2.5 to 2.7, REQ-RST-8; P-RST-18, 19)
  - New: `services/media/jobs.py` (registers the kinds `display_copy` and `restoration` on the upload job runner,
    the upload jobs spec, design 3.10, calling restore-2a's functions); tests under `tests/media/`.
  - Changed: `services/media/router.py` (the restore, list, records and file routes; a job is read through the
    runner's `GET /api/ingest/jobs/{job_id}`, not a route here; the old array route stays until restore-9),
    `tests/fixtures/api_surface.json`.
  - It waits for platform-5 [decided 2026-11-11: the runner for the solve kind lands before the restoration lane
    wires its job and routes to it].
  - The job runs kind `display_copy` end to end for stills and refuses it for clips; kind `restoration` answers
    with a plain "not built yet" until restore-4's statistic is chosen and wired.
  - The client asks for a display copy through the same route as any other variant; ingest makes nothing on its
    own, with a test that an upload alone leaves no `variants/` folder [decided 2026-11-11: one route for every
    variant]. A clip gets no display copy in this spec [open].
  - Done when: a HEIC upload gets a PNG the browser draws, listed and served by its hash.
  - _Requirements: REQ-RST-1.1, 1.4, 1.7, 2.5 to 2.7, 8.1 to 8.8, 8.10_

- [x] 3. restore-3: the photo path as pure functions (REQ-RST-4, REQ-RST-5, REQ-RST-6.1, 6.2, REQ-RST-8.9; P-RST-2,
  7, 8, 9, 10, 12)
  - New: `services/media/stars.py`, `services/media/noise.py`, `services/media/spatial.py`,
    `services/media/tone.py`; tests under `tests/media/`. These take arrays and return arrays and records; the
    lift takes the transfer's slope as an argument, so the unit imports nothing from restore-2a or restore-2b.
  - The detector's matched-filter noise: the unit imports `matched_filter_noise` of
    `services/science/detector.py` once unit realsky-3 has landed it, and until then the two private helpers, as
    `services/media/runtime.py` does today [decided 2026-11-11: the same measure is needed to stop the detection
    flood in the solver].
  - Speed: the unit reports the still path's seconds per megapixel on one core, per step, and brings it to the
    target [decided 2026-11-11 that it does; proposed: at most 0.5 s per megapixel on one core]. If it misses, it
    says what dominates the time.
  - Real data: on the five photos, the noise table per channel, the fine noise left, the stars found and
    confirmed, the footprint radii and the seconds per megapixel, against the numbers of `design.md` M5, M7, M12
    and M13.
  - Done when: the properties hold; the constants test of REQ-RST-4.7 passes; the noise table matches a known
    synthetic noise within 20 percent; no output sample leaves the range of its inputs.
  - _Requirements: REQ-RST-4.1 to 4.8, 5.1 to 5.4, 6.1, 6.2, 8.9_
  - Landed on `main` as `0145388b`, with restore-3b as `1ce89d58`.

- [x] 4. restore-4: the integrity gate, and photos restored end to end (REQ-RST-6, REQ-RST-12; P-RST-11, 13)
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
  - Built [decided 2026-11-18 by the lead: rulings on restore-4's measurements]: `services/media/gate.py`,
    `services/media/encode.py`, `services/media/photo.py` (the photo path in the job's order), `scripts/restore_review.py`;
    the gate as design section 7 now says. Kind `restoration` is wired in `jobs.py` by restore-2b, since `jobs.py`
    comes with the runner. Its new settings get names in the media contract first, as restore-4c.
  - restore-4e [decided 2026-11-19 by the lead: AUDIT-B's findings A and B on restore-4]: a star painted 3 to 12 px
    from one of the original's stars is refused in every trial on every real photo, because the point search leaves
    kept regions out and every sample of a kept core must be the original's as stored (design section 7); a test
    holds the flux tolerance at 3 sigma_F from both sides. Before restore-2b serves anything.
  - restore-4f [decided 2026-11-19 by the lead: AUDIT-B's finding C on restore-4e, model B of the trials]: a single
    sample or a cluster of up to 3 x 3 standing the stated sensitivity over both its surroundings and the original's
    own samples is refused in every trial on every real photo; kept edges are stored as the gate computes them, so
    every kept sample is checked exactly (design section 7). Before any person sees a restoration.
  - restore-4g-docs [decided 2026-11-19 by the lead]: P-RST-11's text states what the gate refuses and its open
    limit, raised samples with partners on both rings, with AUDIT-B's numbers from the restore-4f v2 re-read.
  - [ ] restore-4g: the gate hardened against that pattern, its trials added to P-RST-11 (during the trip).

- [x] 5. restore-5: clip input and output (REQ-RST-3.7, REQ-RST-7.5 to 7.7, 7.9, 7.10; P-RST-15, 16, 17)
  - New: `services/media/clip_io.py` (probe, the streamed decode with each frame's time, the streamed encode with
    the frame times stamped and the audio copied, the rotation pass, the checks on the stored clip); tests under
    `tests/media/`. It moves bytes, planes and times only, and imports nothing from restore-2a, restore-2b or restore-3.
  - Every ffmpeg feature it uses is exercised on both CI builds: `-fps_mode passthrough`, `showinfo`,
    `-noautorotate`, `settb`, `setpts`, `setparams`, `-enc_time_base`, `-copyts`, `-map 1:a?`, the `hash` muxer,
    `-display_rotation`, `libx264`; `libx265` where present, with the plain refusal where not.
  - Real data: the owner's clips copied through decode and encode with no processing: frame counts, whether frame
    times and audio hashes are equal, peak memory, time. Counts only.
  - Done when: for any list of frame times the stored clip has exactly those times; every audio stream has the
    original's packet hash and times; peak memory does not grow with the clip's length; all eight display
    orientations read back.
  - _Requirements: REQ-RST-3.7, 7.5, 7.6, 7.7, 7.9, 7.10_
  - Landed on `main` as `be094f02`.

- [x] 6. restore-6: registration and the temporal step (REQ-RST-7.1 to 7.4, 7.8, REQ-RST-6.10, 6.11; P-RST-14, 22)
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
  - Done 2026-11-18 [tested: `tests/media/test_temporal.py`]: the merge adds a test of each neighbour's difference at
    the scale of a point, since the pairwise weight alone let a point drawn into every neighbour show. Unsolved on
    real data: on all three of the owner's clips more than half of the luma's fine detail is exactly zero, so the
    noise is not measured and no merge runs, and a whole-frame shift misses the closure bound (restore-6 report).
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
  - What the restore-5 audit found [decided 2026-11-11]: a job that ends a run stops its ffmpeg and ffprobe
    children by their own process ids (a hung encoder outlived the worker in the audit); every decode and encode
    child has a time bound of its own; and the variant is written under a partial name and renamed into place
    only when its checks pass (a killed worker left a playable short clip at the output path).
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

- [x] 8. restore-8: export and the manifest (REQ-RST-9; P-RST-21)
  - Changed: `services/media/router.py` (the export and manifest routes), `services/media/variants.py` (the
    manifest builder), `tests/fixtures/api_surface.json`; tests under `tests/media/`.
  - An export carries no capture time, place, file name or container tag from the original [decided 2026-11-11:
    an export is made to be shared; the owner can ask for their own metadata later], with a test on a synthetic
    upload planted with all four.
  - Done when: the exported bytes hash to the manifest's variant hash; the manifest parses in both languages and
    holds no title, time, place or path; a rejected attempt has nothing to export.
  - Done 2026-11-19 for stills [tested: `tests/media/test_export.py`]: `GET .../variants/{variantId}/export` answers
    one zip stored without compression, `<variantId>.zip`, holding `<variantId>.png` byte for byte and
    `<variantId>.png.json`, the manifest, as review row D22 decided (`.lead/uiux/UIUX-export-READY.md`; section 12's
    two `albedian-` attachments are replaced by it); `GET .../manifest` answers the same sidecar. The manifest names
    the newest solution id the job store names. A synthetic upload planted with a file name, a capture time, a place,
    a camera make, tEXt, iTXt, tIME and eXIf chunks and text in every kind of ICC text tag exports none of them;
    restore-8b builds the profile anew from its colour data alone (a fresh header, fixed text in the description
    and copyright, each colour tag at its defined length), in a restoration and in a display copy (REQ-RST-9.4,
    decided 2026-11-19; `tests/media/test_profile_text.py`). restore-8c writes zip64 past 2 GiB, as Python's zipfile
    does, so a long clip fits. A clip's export waits for restore-7's stored file and its export row (501 until then)
    [open].
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

## Open, not scheduled

- `levels` of the spatial step measured per frame, from the noise's own correlation length in sky regions, judged
  from pictures by the owner [open; decided 2026-11-12: listed here, not built now]. Until then it is a default
  whose reason P-RST-2 gives.

## Order and what can run in parallel

- restore-1 first, alone: everything else builds on its shapes.
- Then restore-3 and restore-5 together: they share no file, and neither waits for the upload job runner.
- restore-2a after restore-3, in the same lane; it needs no runner [decided 2026-11-12 by the lead].
- restore-2b after platform-5 of the upload jobs spec, the runner and its routes [decided 2026-11-11].
- restore-4 after restore-2a and restore-3, without restore-2b; it wires kind `restoration` only once restore-2b
  has landed [decided 2026-11-12 by the lead]. restore-6 after restore-3; it can run beside restore-4.
- restore-7 after restore-4, restore-5 and restore-6. restore-8 after restore-4; it can run beside restore-7
  (`router.py` and `variants.py` against `jobs.py`, `gate.py` and `encode.py`).
- restore-9 last.

## Not units of this lane

- The web app is wired to the new routes by its own lane (`apps/web/src/`, owner `experience`): it asks for a
  display copy after a HEIC or TIFF upload, shows variants and offers the export, once restore-2b has landed
  [decided 2026-11-11: the web app is that lane's files].
- One shared function for the detector's matched-filter noise, `matched_filter_noise` in
  `services/science/detector.py`: unit realsky-3 of the REALSKY lane; restore switches to it when it lands
  [decided 2026-11-11: the solver needs the same measure to stop its detection flood].
- A smaller 8 bit rendition of a still for a phone on a slow link: not part of this spec [open]; if one is ever
  served, it is gated as its own file.
- OpenCV as a dependency (`design.md` section 18): proposed, not scheduled.
