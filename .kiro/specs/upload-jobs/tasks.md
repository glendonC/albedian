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

- [ ] 3. platform-3: the solve chain in one function (REQ-JOB-1.5)
  - A pure refactor of `services/ingest/jobs.py`: `solve_upload(ctx)` holds the chain (3.4) behind a small context
    the old `run_solve_job` builds, so every output is the same. Characterization: the six existing solve job test
    files pass before and after, plus a test that the stored job and solution of a synthetic upload are equal field
    for field before and after (floats within a stated tolerance); a mutation check shows the test catches a break.
  - After sky-3 lands (`kiro/sky` edits `run_solve_job`). Beside tasks 1 and 2.
  - _Requirements: REQ-JOB-1.5_

- [ ] 4. platform-4: the job store, checked reads and the start-up sweep (REQ-JOB-3, 5; P-JOB-3, P-JOB-6)
  - `services/ingest/job_store.py` (3.1): per-attempt records, partial outputs and publishing, the `flock`, today's
    records read in place, the sweep with unnamed-result removal; `services/api.py` lifespan calls the sweep; the
    reads in `services/ingest/router.py` go through the store and check the solution with
    `parse_observation_solution`.
  - After task 1 and contracts-1 (the Python solution parser).
  - _Requirements: REQ-JOB-3.1 to 3.5, REQ-JOB-5.1 to 5.8, REQ-JOB-9_

- [ ] 5. platform-5: the runner and its routes (REQ-JOB-1, 2, 3.6, 4, 7; P-JOB-1, 2, 4, 5, 7, 8)
  - `services/ingest/runner.py` (3.2); the solve kind registered (3.4); `wait=false` and the cancel route (3.6);
    `tests/fixtures/api_surface.json` regenerated.
  - The stop hook and the process group in `services/science/bounded_worker.py` (3.3) [decided 2026-11-11: added to
    this lane's files].
  - The fix of `_write_json`'s leftover scratch file (jobs.py:86-94, design section 1), with a test that fails a
    `json.dump` and finds no scratch left [decided 2026-11-11].
  - Sets the solve kind's time bound from measured real-case durations with `uptime`'s load, and measures a solve
    beside a running fake media job to answer the open question on slots.
  - After tasks 3 and 4. The restoration lane wires its kinds and routes to the runner only after this lands.
  - _Requirements: REQ-JOB-1.1 to 1.4, 1.6, REQ-JOB-2.1 to 2.6, REQ-JOB-3.6, 3.7, REQ-JOB-4.1 to 4.5,
    REQ-JOB-7.1 to 7.4_

- [ ] 6. platform-6: revisions, the digest guard and stale solves run again (unit 10i; REQ-JOB-6; P-JOB-11)
  - The solve producer's revision and file list, `tests/fixtures/producer_digests.json`,
    `tests/integration/test_producer_revisions.py`, `count_stale`, the rerun rule at start, the last good solution
    served while a newer solve runs (3.5).
  - After task 5. The restoration's producer joins the guard when its kinds land.
  - _Requirements: REQ-JOB-6.1 to 6.5_

- [ ] 7. platform-7: the start no longer waits (REQ-JOB-7.5)
  - Remove the waiting form of the solve start and update the tests that used it.
  - After the web's own lane changes the two calls of `design.md` 3.9.
  - _Requirements: REQ-JOB-7.5_

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
