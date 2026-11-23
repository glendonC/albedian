# Tasks: upload jobs that queue, recover and tell the truth

Each task is one unit: one commit, tests first, mutants on its risky lines, properties under 20 seeds, all six gates
run by the lead, an independent auditor's verdict, then the lead lands it. Unit ids are `platform-<n>`. Section
numbers refer to `design.md`. "Beside" names what can run at the same time without touching the same files.

- [x] 0. platform-0: this spec (docs only), with the lead's seven rulings written in.

- [x] 1. platform-1: the job contract (REQ-JOB-2, 3, 4, 5.8, 6; P-JOB-9) [decided 2026-11-11: this lane's unit]
  - `packages/contracts/src/ingest.ts`, `services/contracts/ingest.py`, `build_ingest_samples` and
    `tests/fixtures/ingest_contract.json`, the TS and Python contract tests, `scripts/verify-contracts.mjs` (3.8).
  - After contracts-1, which edits the same two files for finite numbers and calendar days. It types the media
    kinds' `request` and ids with restore-1's `RestoreRequestSchema` and `MEDIA_ID_PATTERNS`, and imports
    `JOB_ID_PATTERN` from `media.ts` (3.8), so it also waits for restore-1 [proposed]; if the lead wants it sooner,
    it lands with the solve kind only and a small contract unit adds the media kinds before restore-2.
  - Beside the sky, solver, catalog and ephemeris lanes, and beside tasks 2 and 3.
  - _Requirements: REQ-JOB-2.2, 2.4, 2.6, 3.2, 3.7, 4.2, 5.8, 6.1_

- [x] 2. platform-2: one body limit table for every route (REQ-JOB-8; P-JOB-10)
  - The limit middleware and its table in `services/api.py` (3.7); `tests/integration/test_body_limits.py`;
    `tests/fixtures/api_surface.json` regenerated (`ALBEDIAN_UPDATE_API_SURFACE=1`).
  - Measures the largest real body of each route from its tests and the web's calls and reports them; the limits
    follow from those numbers.
  - Needs nothing first. Beside every lane, except one that changes routes in the same window, since both
    regenerate `api_surface.json`: the lead sequences them.
  - _Requirements: REQ-JOB-8.1 to 8.3, REQ-JOB-9_

- [x] 3. platform-3: the solve chain in one function (REQ-JOB-1.5)
  - A pure refactor of `services/ingest/jobs.py`: `solve_upload(ctx)` holds the chain (3.4) behind a small context
    the old `run_solve_job` builds, so every output is the same. Characterization: the six existing solve job test
    files pass before and after, plus a test that the stored job and solution of a synthetic upload are equal field
    for field before and after (floats within a stated tolerance); a mutation check shows the test catches a break.
  - After sky-3 lands (`kiro/sky` edits `run_solve_job`). Beside tasks 1 and 2.
  - _Requirements: REQ-JOB-1.5_

- [x] 4. platform-4: the job store, checked reads and the start-up sweep (REQ-JOB-3, 5; P-JOB-3, P-JOB-6)
  - `services/ingest/job_store.py` (3.1): per-attempt records, partial outputs and publishing, the `flock`, today's
    records read in place, the sweep with unnamed-result removal; `services/api.py` lifespan calls the sweep; the
    reads in `services/ingest/router.py` go through the store and check the solution with
    `parse_observation_solution`.
  - After task 1 and contracts-1 (the Python solution parser).
  - The solution read checked by contracts-1's parser, in its own unit after it, platform-4b [decided 2026-11-13]:
    a stored solution the contract refuses is never served (409 with one plain sentence); a start solves it again.
  - _Requirements: REQ-JOB-3.1 to 3.5, REQ-JOB-5.1 to 5.8, REQ-JOB-9_
  - [x] platform-4c (2026-11-18): `newest_solution_id`, which design 3.1 names for the restoration's manifest and
    platform-4 left out.

- [x] 5. platform-5: the runner and its routes (REQ-JOB-1, 2, 3.6, 4, 7; P-JOB-1, 2, 4, 5, 7, 8)
  - `services/ingest/runner.py` (3.2); the solve kind registered (3.4); `wait=false` and the cancel route (3.6);
    `tests/fixtures/api_surface.json` regenerated.
  - The stop hook and the process group in `services/science/bounded_worker.py` (3.3) [decided 2026-11-11: added to
    this lane's files].
  - The fix of `_write_json`'s leftover scratch file (jobs.py:86-94, design section 1), with a test that fails a
    `json.dump` and finds no scratch left [decided 2026-11-11].
  - Every failure the solve kind stores is one of its fixed sentences. Of the fourteen failure texts today's solve
    job can store, the job contract refuses nine (found from the code and pinned by
    `test_every_failure_text_todays_solve_job_can_store_is_judged` in `tests/integration/test_job_contract.py`): no
    PGM header; an unexpected frame size with its maximum value; a last frame cut short; ffmpeg's time limit; "could
    not read the file" with the decoder's own words, and with "no frame bytes produced"; a frame of another size than
    the stored one; tile grids that do not give one image; a tile grid that does not cover the stored image. Each gets
    its kind's fixed sentence, which drops the decoder's own words and the sizes; the test's two lists change with
    it on purpose [decided 2026-11-13, lead's request platform-3-v4].
  - Sets the solve kind's time bound from measured real-case durations with `uptime`'s load, and measures a solve
    beside a running fake media job to answer the open question on slots.
  - After tasks 3 and 4. The restoration lane wires its kinds and routes to the runner only after this lands.
  - Split by the lead (2026-11-18): [x] 5s, the solve's time-limit and storing sentences in the job contract;
    [x] 5a, the runner, the solve kind, the routes, the stop hook, the scratch fix and the nine texts mapped;
    [x] 5b, the measured time bound and the slots question; [x] 5c, `run_solve_job` as the runner's thin wrapper.
  - _Requirements: REQ-JOB-1.1 to 1.4, 1.6, REQ-JOB-2.1 to 2.6, REQ-JOB-3.6, 3.7, REQ-JOB-4.1 to 4.5,
    REQ-JOB-7.1 to 7.4_

- [x] 6. platform-6: revisions, the digest guard and stale solves run again (unit 10i; REQ-JOB-6; P-JOB-11)
  - The solve producer's revision and file list, `tests/fixtures/producer_digests.json`,
    `tests/integration/test_producer_revisions.py`, `count_stale`, the rerun rule at start, the last good solution
    served while a newer solve runs (3.5).
  - After task 5. The restoration's producer joins the guard when its kinds land.
  - _Requirements: REQ-JOB-6.1 to 6.5_

- [x] 7. platform-7: the start no longer waits (REQ-JOB-7.5)
  - Remove the waiting form of the solve start and update the tests that used it.
  - After the web's own lane changes the two calls of `design.md` 3.9.
  - _Requirements: REQ-JOB-7.5_

- [x] 8. platform-or: a child whose server is gone ends its group (REQ-JOB-3.8)
  - Found for the night batch (the lead's unit of 21 Nov 12:38): a caller killed mid-call left its child running
    the call with no bound (a probe of the worker itself). The child watches its caller and ends its group once the caller is gone
    (`design.md` 3.3); tests in `tests/science/test_bounded_worker.py`.
  - _Requirements: REQ-JOB-3.8_

- [x] 9. platform-or2: a child whose caller pipe closes leaves silently and ends its group (REQ-JOB-3.8, REQ-JOB-9.1)
  - Found in `night-batch` step 2 (`kill server --slow-upload`): killing uvicorn mid-report closed the pipe under
    `conn.send`, writing a traceback with file paths to the server log and leaving `_serve` before `_watch_parent`'s
    0.5 s poll could kill the child's group (`design.md` 3.3); tests in `tests/science/test_bounded_worker.py`.
  - _Requirements: REQ-JOB-3.8, REQ-JOB-9.1_

- [x] 10. platform-or3: guarded server-thread solve decodes and quiet resource-tracker cleanup (REQ-JOB-3.8, REQ-JOB-9.1)
  - Found in `media-kill-audit`: server-thread `ffmpeg` and `ffprobe` calls in `services/ingest/jobs.py` ran outside
    `BoundedWorker` and `guarded_call` so `SIGKILL` to uvicorn mid-decode left them running, and killing a child's
    spawn pool caused `multiprocessing.resource_tracker` to log a `UserWarning` naming its stdlib file path while
    unlinking semaphores (`design.md` 3.3, 3.4); tests in `tests/science/test_bounded_worker.py`.
  - _Requirements: REQ-JOB-3.8, REQ-JOB-9.1_

## Not units of this lane
- The web follows jobs (`apps/web/src/services/apiClient.ts`, the two calls of `design.md` 3.9), by the web's own
  lane, after task 5 [decided 2026-11-11].
- The display copy and restoration kinds (`design.md` 3.10), by lane `restore`, in restore-2 after task 5; the
  restoration spec's lines that must change first are listed in `design.md` section 7.

## Order
- Done: task 2. After contracts-1 and restore-1: task 1. After sky-3: task 3. Then 4, 5, 6 in order; 7 after the web.
- Also queued for this lane, outside this spec: the capture time from a photo's GPS time stamp when the EXIF offset
  tag is missing (`services/ingest/metadata.py`, `services/ingest/router.py` `_asset`), after sky-3 lands; beside
  tasks 1 and 2.
