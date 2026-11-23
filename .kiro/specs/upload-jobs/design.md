# Design: upload jobs that queue, recover and tell the truth

Requirements: `requirements.md` in this folder. Every line reference below was read at `main` `dcdbd65d`, except
those marked `kiro/sky` (read at `kiro/sky` `6925ff06`, unit sky-3, not landed) and those marked `b59ae181` (read
at `main` `b59ae181`: the web client after sky-5, and the restoration spec, cited as REQ-RST-n, P-RST-n and
"restoration design section n").

Labels as in `requirements.md`: [verified], [tested], [inference], [proposed], [open], [decided 2026-11-11].

## 1. What the code does today

### `services/ingest/router.py`
- `get_ingest_store` (:44-46) builds a new `IngestStore` from the environment on every request, and each one sweeps
  stale partial uploads when it opens (`services/ingest/store.py:139`). Tests replace it through FastAPI's dependency
  overrides.
- `POST /api/ingest/{sha256}/solve` (:237-248): 404 when the file is not stored; returns the stored job with 200 only
  when it is `succeeded` and `solution.json` exists (:244-246); otherwise runs `run_solve_job` to the end in a server
  thread (`run_in_threadpool`, :247) and answers 201 with the finished job. A second POST while a solve runs starts a
  second run (nothing checks for `queued` or `running`).
- `GET /api/ingest/jobs/{job_id}` (:251-259) reads the job file; `GET /api/ingest/{sha256}/solution` (:262-272) reads
  `solution.json` unchecked. The receipt carries `solveJobId` when `job.json` exists (:187-189, :213-215).

### `services/ingest/jobs.py`
- One fixed job id per file: `job_` and the hash's first 16 hex characters (`job_id_for_sha256`, :75-79). Every run
  rewrites `job.json` (:503) and `solution.json` (:625) under the upload's folder. The solution id is
  `sol_` and the same 16 characters (:556).
- `_write_json` (:82-96): writes a scratch file with `O_EXCL`, fsyncs it, `os.replace`s it over the target, sets the
  mode, fsyncs the folder. Atomic for readers. One gap [verified by reading]: the scratch is removed only in the
  `finally` around the replace (:90-94), so a failure inside `json.dump` (a full disk) leaves `.<name>-<hex>.tmp`
  behind, and nothing sweeps it.
- `read_solve_job` (:99-106) and `read_solve_job_by_id` (:109-125) check the record with `SolveJob.model_validate`;
  a record that fails raises out of the route as a 500. `read_solution` (:128-133) checks nothing.
- `run_solve_job` (:447-635) is bookkeeping and science in one function: it builds the stage map (:469-475), `emit`
  checks and writes each record (:477-506), writes `queued` (:508) and replaces it with `running` at the first decode
  callback (:513), so `queued` lasts only microseconds. Decode (:516), stacking and detection (:521-531) run in the
  server thread. The searches run in the one shared child (`_SOLVE_WORKER`, :51) under one process lock
  (`_SOLVE_LOCK`, :52, taken at :582), so a second upload waits in `running` with no word that it waits, and its
  180 s bound (`SOLVE_TIME_LIMIT_SEC`, :46) starts only once the lock is held (:581). A stop at the bound gives an
  `unresolved` solution naming the search that was running (:591-606). `complete_sky` runs after (:620-623). The
  solution is written (:625) before `succeeded` (:627), so the job record is already the commit point. A
  `SolveJobError` ends `failed` with a stored reason, its path replaced (:628-630, :67-72); any other exception ends
  `failed` with a generic reason and is raised again (:631-635).
- Progress is written to disk only at milestones (`persist=` at :513, :519, :541); the rest reaches only the
  `on_transition` callback, which the route does not pass, so a poll would see only milestones.
- `_solve_chain` (:346-444) holds the order of searches. The phone solver can run twice (hinted, then generic,
  :371-398) and the plate solver after it (:399-414); each reports its own candidate counts into the one `solve`
  stage (:536-541), so `done` falls back when the next search starts and nothing says which search it is.
- Clips: the first `MAX_VIDEO_SOLVE_FRAMES` (8, :44) frames are decoded (:340) and stacked; nothing records which.
- `kiro/sky` adds `_spread_frame_indices` and `_decode_spread_frames` (`kiro/sky` :352-402) and a `pose_frames`
  closure inside `run_solve_job` (`kiro/sky` :677-690) passed to `complete_sky`: the chain grows inside the same
  function, which is why the chain's steps must move into one function the runner calls (3.4).

### `services/ingest/store.py`
- Content-addressed folders `<root>/<aa>/<sha256>/` (`folder`, :168-172), owner-only modes (:40-41, :256-265).
  Uploads stream into `<root>/.partial/<hex>.part` (:190-193), swept after a day (:151-166). `commit` links the
  partial into place with `os.link`, never over an existing file (:236-254); `write_record` writes `record.json`
  once by linking a fsynced scratch (:274-292). The size caps are 256 MiB for a still and 2 GiB for a clip
  (:31-32), checked from `Content-Length` and again while the body streams (:182-213).

### `services/science/bounded_worker.py`
- One `spawn` child (:63) that runs one call at a time and stays up between calls (:55-82). `call` (:89-133) polls the
  pipe every `POLL_SEC` (0.25 s, :20), forwards progress events, and on its deadline calls `close` and raises
  `WorkerTimeout`. `close` (:135-149) terminates the child, kills it after `STOP_GRACE_SEC` (2.0 s, :19). There is no
  way for another thread to stop a call early, and nothing stops processes the child itself started.

### `services/api.py`
- `create_app` (:172-207) adds the host check, CORS (`allow_methods=["GET", "POST"]`, :200;
  `allow_headers=["Content-Type"]`, :201) and the foreign-origin guard, then the routers (:205-206). No start-up hook,
  no body limit outside the upload route. `get_health` (:158-169) returns a typed version `"0.2.0"` (:164).

### The contract
- `packages/contracts/src/ingest.ts`: `JOB_STAGES` (:140), `JobStageProgressSchema` (:144-155), `JOB_STATES` with
  `queued`, `running`, `succeeded`, `failed`, `canceled` (:158), `SolveJobSchema` (:160-200) with the field rules per
  state (:180-186), time order (:190-195) and stage order (:196-199); the receipt's `solveJobId` (:215). It imports
  its hash pattern from `media.ts` (:2-9), so `media.ts` cannot import from `ingest.ts` without a cycle.
- `services/contracts/ingest.py` mirrors it: `JOB_STAGES` (:78), `JOB_STATES` (:80), `JobStageProgress` (:303-314),
  `SolveJob` (:317-349); the shared samples are written by `build_ingest_samples` (valid jobs :499-514, named faults
  :592-606) into `tests/fixtures/ingest_contract.json`. It imports no other contract module (:18-25).

### The web (`apps/web/src/services/apiClient.ts`, at `b59ae181`)
- `uploadAndSolveMedia` (:456-526): `POST /api/ingest` (:465), then `POST /api/ingest/{sha256}/solve` (:496), which
  waits for the finished job; `failed` builds a failed session (:502); otherwise `GET /api/ingest/{sha256}/solution`
  (:513). `formatSolveJobProgressBanner` (:309-331) can show stage counts, but the web only ever holds the finished
  job, so no progress is shown while a solve runs.

## 2. Overview

```
POST /api/ingest/{sha}/solve?wait=false          POST /api/media/{sha}/restore   (restoration spec, REQ-RST-1.1)
  runner.start(store, sha, kind, subject, request)
    under the (file, kind, subject) lock: newest job of that key?
      active                                     -> answer it (join)                          200
      succeeded, current revision, results valid -> answer it                                 200
      else                                       -> new job, queued, stored, in its group     202
  the group's slot is free (FIFO by createdAt)
    running: startedAtUTC, producer; kind.run(ctx)
      ctx.progress / ctx.pass_ / ctx.check_cancel / ctx.remaining_s / ctx.child
      ctx.partial(name) -> ctx.publish(partial, result path)    whole, before the commit point
    commit: record succeeded or rejected (names its results)    <- the commit point
GET  /api/ingest/jobs/{id}            -> live record from memory, or the stored one, any kind
POST /api/ingest/jobs/{id}/cancel     -> canceled, or cancel requested, any kind
API start-up: sweep; unheld running -> interrupted, unheld queued -> queued again, unnamed results removed
```

## 3. Components

### 3.1 The job store (`services/ingest/job_store.py`, new)
Layout, inside the upload's existing folder:

```
<root>/.partial/<job_id>-<name>.part         partial outputs of a running job (the store's own partial folder)
<root>/<aa>/<sha256>/
  original.<ext>  record.json                 unchanged
  job.json        solution.json               today's records: read, never written again
  jobs/<job_id>.json                          every kind's job records; 0600 while active, 0400 once terminal
  jobs/<kind>[.<subject>].lock                the lock of one piece of work
  solutions/<job_id>.json                     solve results, 0400
  variants/<variantId>/                       restoration and display copy results (restoration design section 9)
  attempts/<attemptId>.json                   restoration attempt records, kept for every finished attempt
```

[proposed: one folder of job records for every kind, so the sweep and `GET /api/ingest/jobs/{id}` need to know no
kind; results stay where their kind's spec puts them; the modes are the restoration spec's (design section 9).]

- **Ids.** `job_` + the hash's first 16 hex characters + 16 random hex characters (32 in all; the contract allows 16
  to 64, `JOB_ID_PATTERN`) [proposed: lookup by id still finds the folder by the hash prefix, as
  `read_solve_job_by_id` does at jobs.py:109-125, and a new attempt never reuses an id]. Today's 16-character ids
  resolve to `job.json`. The solution id stays `sol_<hash16>` for every solve of one upload [decided 2026-11-11];
  the solution file is named by its job, so the job record ties the file to its producer and revision.
- **Atomic records.** `_write_record(path, payload)`: check the payload against the contract, write a scratch file in
  the same folder with `O_EXCL` and mode 0600, fsync, `os.replace` onto the target, fsync the folder, and on a
  terminal state set mode 0400. The scratch is removed in a `finally` that covers the whole write (closing the gap in
  jobs.py:86-94). POSIX `rename` replaces the name in one step, so a reader sees the old or the new file [inference
  from the POSIX `rename()` specification; it is what `_write_json` relies on today].
- **Outputs.** `ctx.partial(name)` gives a path in `<root>/.partial/`, named with the job id, on the same file system
  as the upload's folder. `ctx.publish(partial, path)` makes it visible in one step under a root the kind declares:
  a file by `os.link` (fails if the name exists, as `store.py:247` does for an original), a folder by `os.rename`
  onto a name that does not exist [inference from the POSIX `rename()` specification: a directory renamed onto a
  missing name appears whole or not at all], then mode 0400 on files and 0700 on folders and a fsync of the parent.
  A kind publishes its results only after its last check, right before the commit.
- **Order of writes for one attempt**: (1) record `queued`; (2) record `running`; (3..k) records at progress
  milestones; (k+1..m) results published, and kept records written (attempt records); (m+1) record `succeeded` or
  `rejected`, which names the results. The commit point is write m+1.
- **The lock.** `fcntl.flock(LOCK_EX | LOCK_NB)` on the key's lock file, taken when the attempt is created and held
  by the runner until the attempt is terminal [proposed: the kernel releases a `flock` when its process dies, so "is
  this job still held by a live process" can be answered after any crash, with no heartbeat and no clock; `fcntl` is
  in the standard library on macOS and Linux, where this project runs and is tested]. Inside one process the runner
  also keeps an in-memory map from the key to the active job, under one `threading.Lock`, because a `flock` belongs
  to the open file, not the thread [inference from the `flock(2)` manual; pinned by P-JOB-2's two-process case].
- **Newest attempt.** The newest record of that key by `createdAtUTC`; the runner creates attempts under the lock and
  makes each `createdAtUTC` at least one millisecond after the previous one (the contract's resolution, `format_utc`
  in `services/contracts/ingest.py:100-104`), so the order is total [proposed].
- **Reads.** `read_job(path)` parses and checks the record; a record that fails is reported as unreadable (never
  served, never rewritten). Today's `job.json` is judged by `SolveJob`, today's contract, and served as it is: it
  has no producer, could not have had one, and is never given one [decided 2026-11-13].
  `read_solution(job)` reads `solutions/<job_id>.json` (or `solution.json` for today's records) and checks it with
  `parse_observation_solution` (contracts-1); it fails closed. `newest_solution_id(sha)` serves the restoration's
  manifest (restoration design section 12).
- **Sweep**, at start-up and when a read finds an unheld `running` job: an unheld `running` job becomes `interrupted`
  (stored); an unheld `queued` job is handed back to the runner; each kind's results that no `succeeded` record
  names are removed (each kind says, from a record, which paths are its results); partial outputs whose job is not
  running are removed at once, not after a day; stray `.*.tmp` scratch files are removed. It logs counts per state
  only.
- As built in platform-4 [tested]: `services/ingest/job_store.py`. Ids, atomic records, partials and publishing,
  the lock and the sweep as above, with these choices. The store makes only its own folders (`jobs/`,
  `solutions/`, `.partial/`) inside an upload's folder or the root, never an upload's folder, refuses `.` and `..`
  before touching disk, publishes only to `<upload>/solutions/<job_id>.json`, and never goes through a link below
  the root (each step checked with `lstat`, each scan refusing links, each read opened with `O_NOFOLLOW`). Record
  writes and sweep passes on an upload's `jobs/` folder serialize under a bounded `flock` on that folder (and a
  `flock` on each scratch file while it is written), so an ended record is never replaced across concurrent writers
  and a live writer's scratch is never removed by a concurrent sweep. Every read checks both the contract (wire
  names only on `job.json` as well as `jobs/<id>.json`, UTF-8 regular files) and that the record's id and upload
  hash match its path. A queued job whose lock is held keeps its partial outputs. The sweep runs once at the app's
  start (its lifespan, on the store the ingest routes use) and is idempotent under concurrent starts; the sweep on
  a read that finds an unheld `running` job comes with the runner (platform-5), which is the first writer of these
  records. Reads: the job route serves today's 16-character ids from `job.json` and the store's 32-character ids
  from `jobs/`, each checked, and answers a record or bucket that cannot be read or fails the contract with 409 and
  one plain sentence; the receipt and re-upload then name no job and a start runs the solve again.
  `read_solution_unchecked` is today's unchecked read behind the store's one function (applying catalog-2b's
  in-memory designation upgrade now on main), named so; it is checked in
  the unit after contracts-1 lands (`parse_observation_solution`), which also answers a stored solution the contract
  refuses with a plain refusal [decided 2026-11-12, lead's answer to platform-4].
- As built in platform-4b [tested]: the store's one solution read is `read_solution`, applying catalog-2b's
  in-memory `upgrade_stored_solution` and checking both the upload's own hash/asset id and
  `parse_observation_solution`: a stored `solution.json` whose folder or file cannot be read as a regular UTF-8 file,
  is not finite RFC 8259 JSON, names another upload, or fails the contract is `Unreadable`, left on disk as it is and
  never served. `GET /api/ingest/{sha256}/solution` answers it with 409 and `UNREADABLE_SOLUTION` (the answer route
  takes the same constant), and a start whose job succeeded runs the solve again when its solution is refused
  [decided 2026-11-13, lead's answer to platform-4b].

- As built in platform-4c [tested]: `JobStore.newest_solution_id(sha256)` gives the solution id of the newest
  succeeded solve (a job of the runner's or today's `job.json`) when its solution reads whole and the contract
  takes it, else None; today's `solution.json` with no succeeded job names none, and a solve that runs does not
  hide the last good one. The restoration's export manifest reads it (restoration design section 12).

### 3.2 The runner (`services/ingest/runner.py`, new)
- `JobKind` (a frozen dataclass), all the runner knows of a kind:
  - `name` and `group` (`solve`; `media` for `display_copy` and `restoration`);
  - `stages`: the ordered stage names, each with the unit or units it may report;
  - `subject(request) -> str | None`: what the lock key adds (a restoration's graph id; none for the solve);
  - `time_limit_s(asset, request) -> float` (the restoration's rule: restoration design section 11);
  - `producer`: `{name, revision}` (3.5);
  - `result_paths(record) -> list[path]` and `result_roots`: where its results live, for publish and sweep;
  - `results_valid(record) -> bool`: whether what an ending record names is stored, whole and passes its
    contract: a `succeeded` job's results, a `rejected` job's kept attempt (platform-8);
  - `time_limit_reason`, `internal_error_reason`: the kind's own fixed sentences (the restoration's are fixed in
    restoration design section 11);
  - `run(ctx) -> KindResult`.
  Kinds register by name; the runner imports none of them; `services/api.py` registers `solve` from
  `services/ingest/jobs.py` and the restoration lane's kinds from its own module.
- `JobContext` given to `run`: `sha256`, the stored record (read only), the original's path, `request`,
  `progress(stage, unit, done, total)`, `pass_(stage, number, label)`, `check_cancel()` (raises `JobStopped` when a
  cancel or the deadline came), `remaining_s()`, `child()` (the group slot's `BoundedWorker`, with the stop event
  wired in, 3.3), `partial(name)`, `publish(partial, path)`, `keep(path, payload)` for kept records.
  `run` returns `KindResult(state, fields)`: `state` is `succeeded` or `rejected`, `fields` the kind's result ids the
  contract allows (`solutionId`; `attemptId`, `variantId`). `JobRefusal(reason)` ends the job `failed` with that
  reason (path replaced as `_stored_reason` does today); any other exception ends it `failed` with the kind's
  `internal_error_reason`, logged without the message [proposed: an exception's text can hold a path or a capture
  value].
- One runner per store root (a process-wide map keyed by the resolved root), so the per-request `IngestStore`
  (router.py:44-46) and tests that override it each get the right runner.
- Groups: one slot per group, one worker thread and its own `BoundedWorker` per slot, replacing `_SOLVE_WORKER` and
  `_SOLVE_LOCK` (jobs.py:51-52) [proposed, REQ-JOB-1.1]. A restoration's own pool of worker processes
  (REQ-RST-8.9) lives inside its child and is the kind's business. Each group's queue is a FIFO by `createdAtUTC`;
  the served record of a queued job adds `queueAhead`.
- Live records: the runner keeps the latest record of each active job in memory; every progress call updates it and
  writes to disk when a stage completes, when the pass changes, and at most once a second while a stage advances
  [proposed, the restoration's rule (restoration design section 11), used for every kind]. `GET` serves the
  in-memory record when this process holds the job, else the stored one.
- Time bound: the job's deadline is `startedAtUTC + time_limit_s(asset, request)`. `remaining_s()` gives what is left
  to steps that take a bound (a child call, an ffmpeg timeout). When the deadline passes, the runner sets the job's
  stop event, the child is stopped, and work in the runner thread stops at its next `check_cancel()`; the job ends
  `failed` with the kind's time-limit sentence and its partial outputs are removed. Work in the runner thread between
  two checks cannot be stopped from outside (Python threads cannot be killed), so the bound holds within the longest
  step between checks [inference]; the solve's long steps are the searches (in the child, stopped at once) and the
  ffmpeg decodes (bounded by `DECODE_TIMEOUT_SEC`, jobs.py:53). The restoration runs all its work in the child
  (restoration design section 11), so its bound is hard. A child call given a bound inside the job's (the media
  kinds leave five seconds for the ending) that runs past it (`WorkerTimeout`) also ends the job `failed` with the
  kind's time-limit sentence, never the internal error one (platform-9); the solve catches its search's own.
- Cancel: sets the stop event; a `queued` job becomes `canceled` at once; a `running` one gets `cancelRequestedAtUTC`
  and becomes `canceled` when the work stops, unless the commit came first; its partial outputs are removed.
- Start-up: a FastAPI lifespan in `create_app` runs the sweep for the configured store and starts the runner;
  shutdown stops the slots' children. Jobs the sweep hands back start in creation order.

- As built in platform-5a [tested]: `services/ingest/runner.py` as above, with these choices. `queueAhead` counts the
  jobs of the group that run before it, the one in the slot included. A new pass restarts its stage at `done` 0 and
  `total` null; a count that falls within one pass, a stage or unit the kind does not declare, or `done` past
  `total` fails the job with the kind's internal-error sentence. A job past its bound fails with the kind's
  time-limit sentence even when its work returned (the solve's is "The solve stopped at its time limit.", added in
  platform-5s), and an ending record that cannot be written fails it with the kind's storing sentence ("The job's
  result could not be stored."). Before the commit the runner checks the record by the contract and asks the kind
  whether its results are whole; a job that does not commit loses everything `ctx.publish` made and its partials.
  At shutdown no record is written, and since platform-7b none is even built (nothing reads the clock for a job
  that will not end here), so a job that was running is ended interrupted by the next start's sweep. A
  cancel of a job another process runs answers 409. `run_solve_job` stays as on `main`, writing today's two files,
  for the tests and scripts that call it; it becomes the thin wrapper in platform-5c [decided 2026-11-18].
  One child per group serves the whole process (`SlotWorker`), shared by the runners of every store root, as the one
  solve child was before: a child holds the solvers and their star tiles, and with one per store root a Linux run
  of this unit's tests was killed (exit 137) [inference: out of memory, from the children it kept]. A call waits for it
  while another root's job uses it, and stops waiting when its own job is stopped [tested].

### 3.3 The stop hook in the bounded worker (`services/science/bounded_worker.py`) [decided 2026-11-11: added to this lane's files]
- `call(..., stop: threading.Event | None = None)`: the poll loop (:108-125) also checks `stop` every `POLL_SEC`; when
  it is set, `close()` and raise `WorkerStopped`. A cancel or the job's deadline therefore stops a call within
  `POLL_SEC + 2 * STOP_GRACE_SEC` (0.25 + 4.0 s, from :19-20 and :143-147) [inference from the code; P-JOB-4
  measures it].
- The child is started in a new process group (`os.setpgid(0, 0)` first thing in `_serve`), and `close` signals the
  whole group (`os.killpg`), so processes the child started (the restoration's pool, an ffmpeg it runs) stop with it
  [tested: `terminate()` reaches only the child; P-JOB-4 asserts no process of the job's group is left].
- The child watches its caller (REQ-JOB-3.8, platform-or): `_serve` is given the caller's pid and starts a daemon
  thread, `_watch_parent`, that reads `os.getppid()` every `PARENT_POLL_SEC` (0.5 s); once the parent is not that
  pid, the caller has gone and the system has adopted the child, so the child sends SIGKILL to the group it leads,
  or, sharing its caller's group, ends alone. Before it, a child whose server was killed mid-call ran its call to
  its end with no bound [tested on 21 Nov: alive 30 s after its parent's SIGKILL, gone within 1 s with the watch].
- When the pipe to the caller closes (`EOFError` or `OSError` in `_serve`, as when uvicorn is killed while a call
  reports events or while the child waits between calls, found in `night-batch` step 2), `_serve` catches the closed
  pipe silently (no traceback or file path in the server log, REQ-JOB-9.1) and ends the group it leads in its
  `finally` block before returning, so no subprocess escapes before `_watch_parent`'s next 0.5 s poll (REQ-JOB-3.8,
  platform-or2) [tested].
- Before starting its first `spawn` child, `BoundedWorker` appends `ignore::UserWarning:multiprocessing.resource_tracker`
  to `PYTHONWARNINGS` (preserving earlier entries), so when a killed child's process pool (`FramePool` or
  `ProcessPoolExecutor`) leaves POSIX semaphores for Python's resource tracker to unlink at shutdown, the tracker still
  unlinks them without writing its stdlib file path to the server log (REQ-JOB-9.1, platform-or3) [tested].

### 3.4 The solve kind (`services/ingest/jobs.py`)
- `solve_upload(ctx) -> KindResult`: today's body of `run_solve_job` from decode to `complete_sky` (:509-623), moved
  as it is, with `emit` replaced by `ctx.progress`, the lock and shared worker replaced by `ctx.child()`, and
  `solution.json` replaced by a partial published as `solutions/<job_id>.json`. The chain's own steps stay here and
  in `_solve_chain`; the sky lane's spread frames and pose (`kiro/sky` :352-402, :677-690) stay inside
  `solve_upload`, so a later change to the chain changes this one function.
- Stages and units: `store` bytes, `decode` frames, `detect` frames, `solve` candidates or steps, `verify` steps, as
  today. Passes: `_solve_chain` already announces each search with `on_event("solver", name)` (:372, :387, :400,
  :416, :430). The kind turns each into `ctx.pass_("solve", n, label)` with labels `phone_photo_hinted`,
  `phone_photo_generic` and `triangle` [proposed: they name what `_solve_chain` runs today]. The search bound becomes
  `min(SOLVE_TIME_LIMIT_SEC, ctx.remaining_s())`; a stop at that bound still gives the `unresolved` solution of
  :591-606, so the job still `succeeded`.
- Frame span: for a clip, the decode records `{firstFrameIndex: 0, frameCount: len(frames), startSec: 0,
  endSec: (len(frames) - 1) / fps}` from the probed frame rate on the asset, as the job field `inputSpan`.
- Time bound: [open], set in platform-5 from measured runs. The sum of today's step bounds is 60 s decode
  (`DECODE_TIMEOUT_SEC`) + 180 s search + 60 s spread frames (`kiro/sky`, its own `DECODE_TIMEOUT_SEC` deadline) =
  300 s, but detection, stacking and `complete_sky` run in the server thread with no bound of their own and have not
  been timed on the real case; the unit reports `.lead/kiro/real_case.zsh 1` durations with `uptime`'s load.
- As built in platform-5b [tested]: `SOLVE_JOB_TIME_LIMIT_SEC` is 420 s. The steps' own bounds come to 300 s on the
  longest chain (a HEIC photo's tile probe and tile decode, 60 s each, and the search; or a clip's decode, the search
  and its spread frames). Each step timed on 13 owner clips and 20 of the 60 public photos through the runner, at load
  7 to 12: the untimed part (stacking, detection, the predicted sky less its spread frames) took at most 18.2 s (a
  24.2 MP photo's detection), about 0.75 s per megapixel for a photo and 1.6 s for a clip's 8 stacked frames; the
  longest job ran 69.9 s. The bound is 300 s and four times 18.2 s, in whole minutes [proposed: the body limits' four
  times]; it leaves 120 s for the untimed part, about 160 MP of photo or 73 MP of clip at those rates [inference]. No
  clip had a recorded time and place, so no spread frames were read: that step is held by its own 60 s bound.
- `run_solve_job` stays as a thin wrapper (start, wait, return the record) for the tests and scripts that call it.
- As built in platform-5c [tested]: `run_solve_job(store, sha256, on_transition=None)` starts the runner's solve job
  (or joins the active one, or answers the current succeeded one), waits for it and returns the record it ended
  with; `on_transition` is a listener the runner tells the record and its every change, and an error it raises
  reaches the caller once the job has ended, never stopping the job. A file not stored still raises
  FileNotFoundError. `_SOLVE_WORKER`, `_SOLVE_LOCK`, `solve_time_limit_s` (tests set `SOLVE_TIME_LIMIT_SEC`),
  `_stored_reason`, `read_solve_job` and `read_solve_job_by_id` are gone, so no code writes `job.json` or
  `solution.json` again; `_write_json` stays for tests that stand for an upload solved before the runner. A
  failed record reads the clock once. The characterization's pins were written again on purpose: the runner's
  records and results, the solve kind's fixed sentences, a job id's random half named, and the runner's
  once-a-second writes off in the test so its writes do not depend on the machine's speed.
- As built in platform-3 [tested], a pure refactor: `solve_upload(ctx)` holds the chain from the decode to
  `complete_sky`, moved as it is; `run_solve_job` keeps the job around it (the queued record, the stage table, the
  writes of `solution.json` and `job.json`, the failed record on any error) and builds the context. `SolveContext`
  holds the stored upload (hash, original, asset, size, media kind), `report(stage, persist, announce)` for a stage's
  progress, `reports_often` (a listener follows every transition) and the time bound as given. Still to come, in
  platform-4 and platform-5: `ctx.progress`, `ctx.child()`, `ctx.pass_`, `ctx.remaining_s()`, the partial solution
  under `solutions/<job_id>.json` and the `KindResult`; until then `solve_upload` returns the completed solution.
  `tests/integration/test_solve_upload_characterization.py` pins what a solve job returns and writes for 27 cases
  and a refused start (`tests/fixtures/solve_upload_characterization.json`, written from main's code before the move):
  each written solution whole, every finding in order with its texts and numbers (floats within a stated tolerance);
  its place, moment and predicted sky compared with what the case sent; each stamp as the read of the test's own
  clock that made it.

- As built in platform-or3 [tested]: every server-thread `ffmpeg` and `ffprobe` decode or packet-probe call in
  `services/ingest/jobs.py` (`_decode_with_ffmpeg`, `_heif_tile_grid`, `_decode_heif_tile_grid`, `_frame_timestamps`
  and `_decode_spread_frames`) runs through `_run_decoder` under `services/media/guarded_call.py` (`child_guard.py`),
  so killing the server mid-decode closes the guard's watched pipe and ends the decoder's group at once (REQ-JOB-3.8).

### 3.5 The producer and stale solves (unit 10i) [decided 2026-11-11]
- Each kind declares `producer = {name, revision}`: `albedian.solve_chain` with revision 1 in `jobs.py`, and the
  restoration's `pipelineVersion` (restoration design section 9) as its revision.
- Each producer lists its source files in one tuple beside its revision (for the solve: the ingest decode and job
  modules, `detector.py`, `solver.py`, `phone_photo_solver.py`, `predicted_sky.py`, `ephemeris.py`, `catalog.py`,
  `bounded_worker.py`, and `pose_estimate.py` and `camera_profiles.py` once they land).
- The guard: `tests/fixtures/producer_digests.json` pins, per producer, its revision and the SHA-256 of its listed
  files (each file's path and bytes, in the tuple's order). `tests/integration/test_producer_revisions.py` computes
  both and fails, naming the producer, when the code's revision differs from the pinned one or the digest differs
  from the pinned one. A unit that changes the chain therefore has to touch the fixture in the same commit, and the
  diff shows its choice: a raised revision (results change, stored solves rerun) or only a new digest (results do
  not change, nothing reruns). `ALBEDIAN_UPDATE_PRODUCER_DIGESTS=1` rewrites the digests and never the revision.
  It hashes source text, never float output, so it is the same on macOS and Linux.
- The job stores `{name, revision}`. A start reruns a `succeeded` job whose revision is lower than the code's, or
  which names no producer (REQ-JOB-6.3). `count_stale(store) -> {current, stale, no_producer}` serves 10i's report.
- The solution's own `solver` field is unchanged: the producer is about the chain, the solver field about the search
  that won.
- As built in platform-6 [tested]: `Producer(name, revision, files)` in `services/ingest/runner.py`; the solve's is
  `albedian.solve_chain` revision 1 over 11 files (`SOLVE_PRODUCER_FILES` in `jobs.py`: the design's list and
  `horizon_solver.py`, which `solver.py` imports for the skyline pose [proposed]; the catalog's tile modules,
  `wcs_models.py` and the designations were not listed; platform-6w widened the list, below).
  `producer_digest` frames each file by its path, a NUL and its length. The guard and P-JOB-11 are
  `tests/integration/test_producer_revisions.py`, the pin `tests/fixtures/producer_digests.json`. A start answers a
  succeeded job as current only when its producer has the kind's name and a revision no older than the kind's, and
  its results are whole; any other (today's `job.json` among them) runs again. `count_stale(uploads, kind, producer)`
  counts the uploads by their newest succeeded job: `current`, `stale`, `no_producer`. While a newer solve runs, the
  solution route serves the newest succeeded solve's solution, and the client tells a newer solve runs from the
  start's answer (202 with the job) and the receipt's `solveJobId`, which names the newest attempt [proposed: no new
  field or header; the solution route's answer itself does not say it].
- As widened in platform-6w [tested]: the solve's list holds every `services/science` module its job module
  imports (the guard reads the imports, so a new one fails it until listed), the stored solution's contract and
  its names (`astronomy.py`, `catalog_reference.py`, `designation.py`, `designation_tables.py`) and the catalog data
  it reads, with the Gaia DR3 tiles' `SHA256SUMS` standing for the tiles, which the provenance tests hold to it.
  The runner and the stores are bookkeeping and stay out. Widening changed no result: the digest was repinned and
  the revision kept.

### 3.6 The routes (`services/ingest/router.py`)

| Route | Today | After |
|---|---|---|
| `POST /api/ingest/{sha256}/solve` | runs to the end, 201 (:237-248) | `wait=false`: 202 new, 200 joined or current; no `wait`: waits for the job and answers it, as today (REQ-JOB-7.2) |
| `GET /api/ingest/jobs/{job_id}` | reads the file (:251-259) | live or stored record of any kind; 404 unknown; 409 "The stored job record could not be read." for a record that fails the contract |
| `POST /api/ingest/jobs/{job_id}/cancel` | none | any kind: 200 with the canceled or unchanged finished job, 202 with `cancelRequestedAtUTC` for a running one, 404 unknown |
| `GET /api/ingest/{sha256}/solution` | reads `solution.json` unchecked (:262-272) | the newest `succeeded` solve's solution, checked; 404 none; 409 "The stored solution could not be read." |
| `GET /api/ingest/{sha256}` | `solveJobId` from `job.json` | `solveJobId` = the newest solve attempt, active or not |

As built in platform-7 [tested]: the solve start takes no `wait`; it answers at once, 202 with a new job and 200
with the active job it joined or the current succeeded one (a `wait` sent by an older caller is ignored, as any
unknown query is). Every caller follows the job on `GET /api/ingest/jobs/{job_id}`: the web (WEB-UX unit-jobs-1),
the iPhone (MOBILE mobile-9, which lands first), the tests and `research/benchmarks/real_photos/measure.py`;
`run_solve_job` still waits, for scripts that solve in their own process. Following the job showed that the live
record of 5a could read `succeeded` before that record was on disk, so a reader could then find no solution;
since platform-7 a job's ending record is written, the commit point, before the live record shows it, and a
canceled queued job's likewise [tested].

The restoration's start route (`POST /api/media/{sha256}/restore`, REQ-RST-1.1) stays in `services/media/router.py`
and calls `runner.start`. [proposed: POST for cancel because the API allows only GET and POST across origins
(`services/api.py:200`); 409 for an unreadable stored result because the request is sound and the stored state is
what conflicts; one job read route and one cancel route for every kind, so the web follows every job one way.]

### 3.7 Body limits (`services/api.py`)
- One ASGI middleware, `BodyLimit`, with a table `BODY_LIMITS` from path template to limit, in the order the app
  matches them, first match first: the upload route keeps its own caps (store.py:31-32) and is skipped here; routes
  with no body allow 0 bytes; every other route takes 4 times the largest real body measured on its tests and the
  web's calls [proposed: room for real growth while still far below memory trouble; the measured sizes stand beside
  each row and in platform-2's report]. When that is 64 KiB or less, the limit is 64 KiB [tested in platform-2:
  uvicorn reads up to 64 KiB of a body before it stops reading the socket (`HIGH_WATER_LIMIT`,
  uvicorn/protocols/http/flow_control.py), so a lower limit saves no memory]; when it is more, it is rounded up to
  a whole MiB [proposed: readable round numbers]. A test computes every row from the measured sizes by this rule
  [tested in platform-2 v2]. There is no default: `create_app` refuses to build when a route has no row or a row
  has no route, and a request no route serves may send no body [tested in platform-2]. The restoration's
  `RestoreRequest` route gets its row when it lands.
- It refuses a declared `Content-Length` over the limit before the route runs, and counts bytes as they arrive, so
  a body with no `Content-Length`, a false one or chunked transfer is caught at the first piece that passes; that
  piece is never handed on (the route gets a disconnect, and its own answer to the cut body is dropped). A route
  with a 0-byte limit never reads a body, so for it the middleware reads ahead until the body ends or its first
  byte arrives, before the route runs. It answers 413 with "The request body is larger than the N-byte limit for
  this route." [tested in platform-2] Only the route's error over the body it cut is dropped; any other route
  error goes on to the server, which logs it with its traceback and answers 500 [tested in platform-2 v2].
- It sits inside CORS and the foreign-origin guard, not outermost after the host check as first proposed
  [tested in platform-2: CORS adds its headers to the 413 only from outside, so the web page can read the sentence;
  the guard answers 403 without reading a body].
- `tests/fixtures/api_surface.json` pins the middleware stack and the routes, so the unit regenerates it
  (`ALBEDIAN_UPDATE_API_SURFACE=1`, `tests/integration/test_api_surface.py`) and the diff is the review.

### 3.8 The contract (`packages/contracts/src/ingest.ts`, `services/contracts/ingest.py`)

```ts
export const JOB_KINDS = ['solve', 'display_copy', 'restoration'] as const;
export const JOB_STATES = ['queued', 'running', 'succeeded', 'rejected', 'failed', 'canceled', 'interrupted'] as const;
/** Each kind's stages in order, with the units each may report. */
export const JOB_STAGE_UNITS = {
  solve: { store: ['bytes'], decode: ['frames'], detect: ['frames'], solve: ['candidates', 'steps'], verify: ['steps'] },
  display_copy: { decode: ['frames'], encode: ['frames'], write: ['bytes'], verify: ['frames'] },
  restoration: { decode: ['frames'], measure: ['frames'], restore: ['frames'], encode: ['frames'], write: ['bytes'], verify: ['frames'] },
} as const;
// JOB_STAGES becomes the union of every kind's stage names; PROGRESS_UNITS is unchanged

export const JobPassSchema = z.object({
  number: z.number().int().positive(),
  label: z.string().regex(/^[a-z][a-z0-9_]{0,39}$/),
}).strict();
// JobStageProgressSchema gains: pass: JobPassSchema.optional()

export const JobProducerSchema = z.object({
  name: z.string().regex(/^[a-z][a-z0-9_.]{0,79}$/),
  revision: z.number().int().positive(),
}).strict();

export const JobInputSpanSchema = z.object({
  firstFrameIndex: z.number().int().nonnegative(),
  frameCount: z.number().int().positive(),
  startSec: z.number().finite().nonnegative(),
  endSec: z.number().finite().nonnegative(),
}).strict();   // refined: endSec >= startSec

export const UploadJobSchema = z.object({
  id, sourceAssetSha256, state, createdAtUTC, startedAtUTC?, finishedAtUTC?, stages, solutionId?, error?, // as today
  kind: z.enum(JOB_KINDS),
  producer: JobProducerSchema.optional(),
  request: RestoreRequestSchema.optional(),                                    // from media.ts (restore-1)
  graphId: z.string().regex(new RegExp(MEDIA_ID_PATTERNS.graph)).optional(),   // the subject of the media kinds
  attemptId: z.string().regex(new RegExp(MEDIA_ID_PATTERNS.attempt)).optional(),
  variantId: z.string().regex(new RegExp(MEDIA_ID_PATTERNS.variant)).optional(),
  inputSpan: JobInputSpanSchema.optional(),
  cancelRequestedAtUTC: utcInstant().optional(),
  queueAhead: z.number().int().nonnegative().optional(),
}).strict();
export const SolveJobSchema = UploadJobSchema.refine((job) => job.kind === 'solve');   // name kept for the web
```

Refinements, the same in both languages, each with a named fault in the fixture:
- Per state: `queued` has no start, finish, result id, error or cancel time; `running` has a start, no finish,
  result id or error, and may have a cancel time; `succeeded` has a start, a finish and its kind's result ids
  (`solutionId` for solve; `attemptId` and `variantId` for the media kinds) and no error; `rejected` (media kinds
  only) has a start, a finish and `attemptId`, no `variantId`, no error; `failed` has a start, a finish and an error,
  no `solutionId` or `variantId`, and may have `attemptId`; `canceled` has a finish and no result id or error;
  `interrupted` has a start, a finish and an error, and no result id [changed in platform-1's fourth version to say
  what the table says: a running job's cancel time by the lead's ruling of 2026-11-12, and the start of a failed and
  an interrupted job, which design 4 reaches only from `running`].
- Per kind: `solutionId` and `inputSpan` only for solve; `graphId`, `attemptId`, `variantId` only for the media kinds,
  which require `request` and `graphId`, with `request.kind` equal to the job's kind; a solve's `request`, when it has
  one, is the person's alignment it ran from (align-2c, D24's Solve from here), kept from its creation.
- Every stage belongs to the job's kind, appears once, in its kind's order, in one of its units (generalising
  ingest.ts:196-199).
- `queueAhead` only when `queued` (a served count: the store refuses to write it). `cancelRequestedAtUTC` only on a
  job that started, and on a job canceled after it started always [decided 2026-11-12: a record never drops a true
  thing it recorded, REQ-JOB-4.2 and 4.3, so a cancel asked of a running job stays when it then succeeds, fails, is
  refused or is interrupted], not before `startedAtUTC` nor after `finishedAtUTC`.
- `predictedSkyAtUTC` only on a solve that started, kept once recorded, not before `startedAtUTC` nor after
  `finishedAtUTC` (REQ-JOB-2.7, platform-10a). The partial it names is the solve job's own predicted sky: an
  `ObservationSolution` with status `approximate`, its nominal WCS and its `poseEstimate` (the only status a pose
  belongs to), its stars and planets `predicted` and placed through that pose, served by
  the job's predicted-sky route while the job runs [proposed: the route and its life after the job ends are the
  publishing unit's; the record holds a time, not the sky, so each read of the job stays small].
- As built in platform-10b [tested]: a solve kind calls `ctx.predicted_sky(solution)` with an approximate
  solution and its pose estimate (anything else, or a media kind, is a ValueError); the runner keeps it whole
  among the job's partials (`predicted_sky.json`, put in place in one step) and sets `predictedSkyAtUTC` on the
  live record, written at once. `GET /api/ingest/jobs/{job_id}/predicted-sky` serves it while the job runs in
  this process and 404 otherwise; it goes with the job's partials at its end. Which poses call it (the head-worn
  Moon pose found before the search, the phone's pointing record, a person's alignment) are later units.
- As built in platform-10c and 10d [tested]: before the search the solve completes an empty unresolved solution with
  the same `complete_sky` that ends it, given what the photo's pointing record gives the final sky (the lent moment,
  the record's attitude and the hint the solution states, `_sky_from_record`), and publishes the result when a pose
  places it: the head-worn Moon or horizon pose on the pose frames, read once for both tries, or a phone photo lined
  up from its record's attitude. A person's alignment does not call it yet.
- `producer` is required on a job that started and absent on one that never did, as design 4 writes it with the
  start [decided 2026-11-13]; a record today's code wrote has none and could not have had one, so the store judges
  it by `SolveJob` and never gives it one (3.1). `error` keeps its 2000 character bound.
- Python: `UploadJob` with the same fields and rules (the stage's `pass_` field has the alias `pass`, a Python
  keyword), `SolveJob` kept for today's records. Today's `job.json` lacks `kind` and a producer, so the store judges
  it by `SolveJob` (3.1), and the contract itself keeps `kind` required [proposed: a strict contract with no silent
  default; changed 2026-11-13: no upgrade to `UploadJob` on read, which would have to make up a producer].
- The media ids, the restore request and `JOB_ID_PATTERN`: `ingest.ts` imports `MEDIA_ID_PATTERNS` and
  `RestoreRequestSchema` from `media.ts` (the direction it imports today). `media.ts` cannot import from `ingest.ts`
  without a cycle, so the job id pattern that the restoration's attempt record needs (`ProcessingAttempt.jobId`)
  moves to `media.ts` beside `SHA256_HEX_PATTERN`, and `ingest.ts` imports it from there [proposed]. The Python side
  has no cycle: `services/contracts/media.py` imports `JOB_ID_PATTERN` from `services/contracts/ingest.py`.
- Fixture: `build_ingest_samples` gains a valid sample for every state and kind, a solve job with passes, a frame
  span, a producer, a cancel time and a queue count, and one fault per refinement above; both sides judge
  `tests/fixtures/ingest_contract.json` (P-JOB-9). `scripts/verify-contracts.mjs` lists the new schema names.
- The receipt's `solveJobId` (ingest.ts:215) is unchanged.
- As built in platform-1 [tested; each departure from the design above accepted by the lead on 2026-11-12, with its reason]:
  - `UploadJob` lives in `services/contracts/jobs.py` and `packages/contracts/src/jobs.ts`, on the wire module's
    helpers, not in the ingest files: Python's `media.py` imports `ingest.py`, so `ingest.py` could not import the
    restore request back, and contracts-1 edits both ingest files. The fixture is `tests/fixtures/job_contract.json`.
  - `SolveJob` and `SolveJobSchema` stay as they are: today's solve job writes the one and the web reads the other,
    and neither carries `kind` or a fixed failure sentence. The runner (task 5) writes `UploadJob`, and the store's
    upgrade on read (task 4) adds `kind`; `SolveJobSchema` becomes the solve subset then.
  - No free text: the pass label is a closed list (`phone_photo_hinted`, `phone_photo_generic`, `triangle`), and a
    failure is one of the solve kind's ten fixed sentences (today's own, with sizes, limits and the decoder's words
    left out) or, for the media kinds, a sentence of the restoration's templates. An interrupted job says "The
    server stopped before this job finished."
  - Bounds: a pass number 1 to 16; a frame span's times 0 to 86,400 s; every count a safe integer.
  - Second version, after the full audit [tested; ruled by the lead on 2026-11-12]: what each state may and must
    hold is one table on both sides (`JOB_STATE_TABLE`, with `JOB_STATE_FIELDS` and `JOB_PRE_RUN_STAGES`), read from
    the requirements and written beside them in `services/contracts/jobs.py`. For each group of kinds (solve, media)
    and state it marks every optional field, and the stages a job holds once it starts, as required, forbidden, either,
    or allowed only on a job that started, and names the failure sentences the state allows. So a job that never
    started counts no work and holds no frame span (only the solve's store stage is written at creation); an
    interrupted job started and gives only its sentence, which a failed job never gives; a result id only on the
    states that have that result; a rejected solve does not exist. The validators read the table, the fixture holds
    every state of every kind against every field, present and absent, with the table's verdict, and a test derives
    each cell again from the requirement statements. The presence rules share one message, "the fields do not fit the
    job's kind and state"; the old kind-field, media-needs and queue messages are folded into it.
  - Third version, after the second audit [tested; ruled by the lead on 2026-11-12]: a rule for "required once
    started, forbidden before" (`with_start`), for a canceled job's cancel time (REQ-JOB-4.2: a running job's cancel
    is recorded, then the job is canceled). A failed job always started (design 4 reaches failed only from running),
    so its "allowed once started" cells are "either". The other "allowed once started" cells stay: a canceled job's
    frame span and stages (canceled before the decode wrote them, REQ-JOB-2.3, 2.6). The test that reads the
    requirements again now quotes each sentence as a rule about a record and checks the table over every pattern of
    fields. An interrupted or a rejected job that started may keep its cancel time (the lead's ruling of 2026-11-12,
    written into this section's list above).
  - Fourth version, after the third audit [tested]: the per-state sentence above says what the table says (a
    running job's cancel time; the start of a failed and an interrupted job). The second reading quotes every
    sentence whole, and a test holds each to the section it names. Today's `SolveJob` agrees with the job contract on
    one job: a failed solve started (today's code sets the start before any failure) and a canceled one did not
    (`SolveJob` has no field for a cancel time, and today's code never cancels); the ingest fixture's samples say so,
    and a test judges every job-shaped sample of every fixture with the job contract as its fixture says. The
    producer: required on a job that started and absent on one that never did, in every row (the lead's ruling of
    2026-11-13, written into REQ-JOB-6.1 and this section's producer line); today's records are judged by
    `SolveJob`, not upgraded. `SolveJob`'s solution id stays a label: no job record is stored on this laptop
    (counted 2026-11-13), but the web's tests give a solve job a solution fixture's id of another form.

### 3.9 The web (its own lane) [decided 2026-11-11: not this lane's files]
- Until the web changes, the blocking start (no `wait`) keeps today's web working unchanged.
- The web's change touches exactly two calls in `uploadAndSolveMedia` (`apiClient.ts`, `b59ae181`):
  1. `POST /api/ingest/{sha256}/solve` (:496) becomes `POST /api/ingest/{sha256}/solve?wait=false`, answered at once
     with an active or finished job (202 or 200);
  2. `GET /api/ingest/{sha256}/solution` (:513) runs only after `GET /api/ingest/jobs/{id}`, polled once a second,
     reports `succeeded` [proposed: one small JSON read a second is light for a local server and close enough for
     a person watching counts move; a stream (server-sent events) would push every candidate count but needs
     reconnect logic for a laptop that sleeps, and a poll needs none]. Each poll answer goes through
     `formatSolveJobProgressBanner` (:309-331); `failed`, `interrupted` and `canceled` show the job's own reason.
- Cancel (`POST /api/ingest/jobs/{id}/cancel`) and "Run again" after `interrupted` are new controls of that lane.

### 3.10 The display copy and restoration kinds (lane `restore`)
- Registered in `services/media/jobs.py` as two kinds of group `media`: `display_copy` (stages `decode`, `encode`,
  `write`, `verify`, restoration design section 13) and `restoration` (`decode`, `measure`, `restore`, `encode`,
  `write`, `verify`, REQ-RST-8.1). Subject: the graph id, so the restoration spec's "the same request returns the
  stored variant and runs nothing" (REQ-RST-1.6) and "a second request for the same graph returns the running job"
  (restoration design section 16) are the runner's current-job answer and join.
- `run` makes one `ctx.child()` call that does all the work (restoration design section 11), sends progress events,
  assembles the variant folder in `ctx.partial(...)` and returns; the kind then writes the attempt record with
  `ctx.keep`, publishes the variant folder as `variants/<variantId>/` when the attempt is `ready`, and returns
  `succeeded` or `rejected`. Its time bound, sentences and pool are its own; the stop hook (3.3) reaches its pool.
- Export stays a read of stored files (restoration design section 12), not a job (REQ-JOB-1.6).

## 4. States and transitions

| From | Event | To | Writes | Pinned by |
|---|---|---|---|---|
| none | start; no active job for the key; no current `succeeded` one | `queued` | record | P-JOB-1, P-JOB-2 |
| `queued`/`running` | start for the same key | unchanged (joined) | none | P-JOB-2 |
| `succeeded` | start; revision current; results valid | unchanged | none | P-JOB-1 |
| any terminal | start; not `succeeded`, or revision older or none, or results unreadable | new attempt `queued` | new record | P-JOB-1, P-JOB-6 |
| `queued` | the group's slot is free (FIFO) | `running` | record: `startedAtUTC`, `producer` | P-JOB-8 |
| `queued` | cancel | `canceled` | record: `finishedAtUTC` | P-JOB-4 |
| `queued` | process died; sweep or read | `queued`, back in its queue | none | P-JOB-6 |
| `running` | stage complete, pass change, a second of progress | `running` | record | P-JOB-7 |
| `running` | cancel | `running` | record: `cancelRequestedAtUTC` | P-JOB-4 |
| `running` | work stops for the cancel before the commit | `canceled` | record; partials removed | P-JOB-4 |
| `running` | results published, then commit | `succeeded` | results, then record | P-JOB-3 |
| `running` | the kind's check refuses (media kinds) | `rejected` | attempt record, then record | P-JOB-1 |
| `running` | `JobRefusal` | `failed` (its reason) | record; partials removed | P-JOB-1 |
| `running` | any other exception, a crash of the child | `failed` (the kind's sentence) | record; partials removed | P-JOB-1 |
| `running` | deadline passed | `failed` (the kind's time-limit sentence) | record; partials removed | P-JOB-5 |
| `running` | process died; sweep or read | `interrupted` | record; partials and unnamed results removed | P-JOB-3, P-JOB-6 |
| `succeeded`, `rejected`, `failed`, `canceled`, `interrupted` | anything | unchanged | none | P-JOB-1, P-JOB-6 |

## 5. Error handling
- A record or solution that fails the contract on read: not served (409 with the plain sentence), left on disk as it
  is, and the next start makes a new attempt.
- A write that fails (disk full, permissions): the scratch is removed; a failure writing a record or a result ends
  the job `failed` with "The job's result could not be stored." when a further record write is possible; when no
  write is possible, the job stays in its last stored state and the sweep or the next read finds it unheld.
- A crash of the child: `WorkerFailed` ends the job `failed` with the kind's sentence, as today (jobs.py:631-635),
  and the slot starts a fresh child for the next job.
- The original or the record missing when a queued job starts: `failed` with "The stored file is missing."
- Nothing in any reason, record or log holds a path, a file name, a capture time or a place (REQ-JOB-9);
  `_stored_reason` (jobs.py:67-72) stays the one way a `JobRefusal` reason is stored.

## 6. Testing strategy
- Python tests under `tests/integration/` (`test_job_store.py`, `test_job_runner.py`, `test_job_routes.py`,
  `test_body_limits.py`, `test_producer_revisions.py`), each written first and seen failing for the stated reason,
  with a fake kind (a few stages, steps that wait on events, a child that starts a grandchild) so the runner is
  tested without science, and the existing solve job tests (`test_ingest_solve_job.py`,
  `test_solve_job_time_bound.py`, `test_solve_job_honest_stages.py`, `test_solve_job_heic_and_size.py`,
  `test_solve_job_orientation_and_focal.py`, `test_ingest_predicted_sky.py`) kept passing as the characterization of
  the solve kind.
- Crash cases (P-JOB-3) run the fake kind in a `spawn` child that calls `os._exit` after its n-th write, then reopen
  the store in the test process; this is a real process death, so the `flock` is released as it would be.
- Two-process starts (P-JOB-2) use two `spawn` children on one store root.
- Process tree (P-JOB-4): after a cancel or a deadline, no process of the job's process group is left (`os.killpg`
  with signal 0 raises `ProcessLookupError`).
- Contract parity P-JOB-9 in both languages over the shared fixture plus generated faults.
- Mutants on the risky lines: the transition rules, the lock check in the sweep, the commit order (results before the
  record), the unnamed-results removal, the revision comparison, the join check, the guard's two comparisons, the
  contract refinements, the limit comparison.
- Real data: `.lead/kiro/real_case.zsh 1` through the runner, reporting status, stage counts, passes, the frame span,
  time to finish and `uptime`'s load, counts only.

## 7. What the restoration spec must change to agree (for the lead; that spec is not edited here)

Lines of `.kiro/specs/restoration-for-every-upload/` at `b59ae181`: R is `requirements.md`, D is `design.md`, T is
`tasks.md`. Each item says what changes and why.

Requirements:
- R:104-105 (REQ-RST-1.3): the variant folder is assembled under the store's `.partial/` and renamed into
  `variants/<variantId>/` whole, not linked file by file, so a crash never leaves a variant folder with some of its
  files (3.1, Outputs).
- R:288-316 (REQ-RST-8): add criteria for cancel (REQ-JOB-4) and restart (REQ-JOB-3: `interrupted`, never rerun by
  itself; `queued` resumes), or a line saying the restoration job is a kind of this spec's runner and inherits them.
- R:296-298 (REQ-RST-8.3): the stop reaches every process the child started (its pool), not only the child (3.3).
- R:299-300 (REQ-RST-8.4): the job record is read by `GET /api/ingest/jobs/{job_id}`, not
  `GET /api/media/restore-jobs/{jobId}`.
- R:301-302 (REQ-RST-8.5): the fixed list gains "The server stopped before this job finished." (interrupted).
- R:307 (REQ-RST-8.7): one restoration at a time is the `media` group's one slot; a display copy waits in the same
  queue.
- R:308-309 (REQ-RST-8.8): "the bounded worker" becomes "the upload job runner, which gives the kind its bounded
  worker".
- R:401 (P-RST-1): `restore-jobs/` becomes `jobs/`.

Design:
- D:16 (overview): the job is a kind of the upload job runner (`services/ingest/runner.py`), in its `media` group.
- D:185-186 (section 2): the `BoundedWorker` and the record writer come from the runner's context, not from
  `jobs.py:82-96` directly.
- D:187 and D:708 (sections 2 and 12): the solution id comes from the job store's newest `succeeded` solve
  (`newest_solution_id`), not from `solution.json` alone; it is the same `sol_<hash16>` for every solve.
- D:506 (section 9): the folder also holds `jobs/` and `solutions/`.
- D:515-516 (section 9): `restore-jobs/<jobId>.json` becomes `jobs/<job_id>.json`, with the same modes.
- D:519-522 (section 9): the runner removes a stopped job's partial files when it ends, not after a day; the variant
  folder is renamed into place whole (as R:104-105 above).
- D:524 (section 9): the job id `rjob_<h2>` becomes the runner's `job_<hash16><16 hex>`; `graph_`, `var_`, `att_`
  ids are unchanged.
- D:531 (section 9): a job id is checked against `JOB_ID_PATTERN`.
- D:596-600 (section 10): the pool is in the child's process group, so a cancel or the bound stops it (3.3).
- D:668-674 (section 11): the route calls `runner.start(kind, subject=graphId, request)` instead of writing a record
  and handing the job to its own background thread; "200 with the finished job record that made it" is the runner's
  current-job answer.
- D:675-676 (section 11): the states gain `canceled` and `interrupted`.
- D:693-698 (section 11): the time-limit and internal-error sentences are the kind's own, passed to the runner.
- D:764 and D:902 (section 14): `MEDIA_ID_PATTERNS.job` (`^rjob_...`) is dropped; `ProcessingAttempt.jobId` uses
  `JOB_ID_PATTERN`, which moves to `media.ts` (3.8).
- D:945-972, D:1008, D:1116, D:1158-1169 (section 14): `RESTORE_JOB_STAGES`, `RESTORE_JOB_STATES`,
  `RestoreJobSchema` and the Python `RestoreJobId` and `RestoreJob` are not built: the job is `UploadJobSchema` with
  kind `display_copy` or `restoration` (platform-1), with the same stages, units and field rules.
- D:1195 (section 14): the job samples live in `tests/fixtures/ingest_contract.json`.
- D:1204 (section 14): `scripts/verify-contracts.mjs` does not add `RestoreJobSchema`.

Tasks:
- T:17-36 (restore-1): leave out the job schema and its id pattern; add `JOB_ID_PATTERN` to `media.ts`.
- T:38-57 (restore-2): `services/media/jobs.py` registers the two kinds on the runner; the job read route is not
  built there (T:43); the unit waits for platform-5 [decided 2026-11-11: the runner for the solve kind lands before
  the restoration lane wires its job and routes to it].
- T:186-191 (order): restore-2 after platform-5; restore-1, restore-3 and restore-5 do not wait.
