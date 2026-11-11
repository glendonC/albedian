# Requirements: upload jobs that queue, recover and tell the truth

## Introduction

Work on a stored upload runs as a job. Today there is one kind, the solve, and it is a blocking request: the client
waits for the whole solve, nothing queues it, nothing can stop it, a restart leaves it `running` for good, two
requests for one file run it twice, and nothing records which code produced a stored solution. The restoration spec
(`.kiro/specs/restoration-for-every-upload/`, on `main` `b59ae181`) adds two more kinds: a display copy and a
restoration of an upload. The lead decided on 2026-11-11 (`.lead/briefs/platform.md`, decisions 1 to 7) that every
kind uses one runner, built once, and ruled on this spec the same day (see "Rulings by the lead on this spec").

Labels: [verified] read at the named source; [tested]; [inference]; [proposed] a choice with its reason; [open];
[decided 2026-11-11] a ruling by the lead on this spec, with its reason.

Facts this spec rests on [verified by reading the code at `main` `dcdbd65d`; line numbers in `design.md`]:
- `POST /api/ingest/{sha256}/solve` runs the whole solve inside the request (`services/ingest/router.py`
  `start_ingest_solve_job`) and answers only when it is over. It returns a stored job early only when that job
  `succeeded` and a solution file exists; in every other case it starts a new run.
- The job id is fixed per file (`job_` and the first 16 hex characters of the hash), and every run rewrites the same
  `job.json` and `solution.json`. Two runs of one file therefore write the same two files in turn [inference: no
  test covers it].
- The `queued` state is written and replaced at once: the wait for the one solve child happens while the job already
  says `running`, and the 180 s search bound starts only once that child is free (`services/ingest/jobs.py`
  `run_solve_job`, `_SOLVE_LOCK`).
- Nothing runs at start-up (`services/api.py` `create_app` has no start-up hook), so a job a restart caught in
  `queued` or `running` stays so until the file is solved again.
- A stored job is checked against the contract when read; a stored solution is not (`read_solution`).
- The web calls the solve once and treats the answer as final (`apps/web/src/services/apiClient.ts`
  `uploadAndSolveMedia`; at `main` `b59ae181` as well); it never calls `GET /api/ingest/jobs/{job_id}`.
- The solution names its solver and a version (`solver.name`, `solver.version`), but the versions are typed by hand
  and the solution names only the last search that ran, not the chain of searches and the predicted sky after it.

Not in scope: the science inside any job (solvers, predicted sky, pose, restoration filters); the display copy and
restoration kinds themselves (lane `restore` builds them on this runner); the web's change to follow jobs (its own
lane, `design.md` 3.9); body limits of routes other lanes own beyond the one shared table in `services/api.py`; more
than one server process (one uvicorn process today; the design stays correct with more but queues per process).

## Glossary

- **Job**: one attempt at one kind of work on one stored upload, with a stored record.
- **Kind**: the work a job does: `solve`, `display_copy` or `restoration`. The runner knows a kind only by what it
  declares (REQ-JOB-1.4); it never knows its science.
- **Subject**: what a kind adds to the file's hash to name one piece of work, for example a restoration's graph id.
  The solve has none.
- **Active job**: a job in `queued` or `running`.
- **Producer**: the name and revision of the code a job runs, recorded on the job, so a result from older code can be
  found.
- **Result**: an output a kind serves (a solution, a variant). A result is served only when a `succeeded` job names
  it.
- **Commit point**: the write that makes a job `succeeded`. Every result is made visible before it, so a reader that
  follows a `succeeded` record always finds its results whole.
- **Interrupted**: a job that was active when the process that ran it stopped.

## Requirements

### REQ-JOB-1: One runner for every kind of work on a stored upload
**User Story:** As the person who uploaded a file, I want solving and restoring it to behave the same way: they wait
their turn, show real progress, can be stopped and never run twice at once.
- **Acceptance Criteria:**
  1. THE runner SHALL run every job through a queue per group with one slot each: one group for `solve`, one for
     `display_copy` and `restoration` [proposed: the solve's 180 s bound was measured with one solve at a time, the
     restoration spec asks for one restoration at a time (REQ-RST-8.7), and a solve should not wait minutes behind a
     clip's restoration].
  2. THE runner SHALL start the queued jobs of a group in the order they were created.
  3. WHEN a start is asked for a file, kind and subject that already have an active job, THE answer SHALL be that
     job (the request joins it); no second attempt SHALL be created, in this process or in another process on the
     same store.
  4. A kind SHALL plug in by declaring its name, group, stages with their units, a time bound computed from the
     stored asset and the request, its producer, how to find its results from its job record, and one function
     that does the work through a context the runner gives it. THE runner SHALL import no science or media module.
  5. THE solve chain's own steps (decode, detect, the searches, the predicted sky and any pose after it) SHALL stay in
     one function in `services/ingest/jobs.py` that the runner calls, so lanes that change the chain change one place.
  6. Export SHALL not be a job while it only reads stored files and builds its manifest on request (restoration spec,
     design section 12) [proposed: nothing long runs, so there is nothing to queue, bound or cancel].

### REQ-JOB-2: A real queued state and progress from real denominators
**User Story:** As someone waiting for a result, I want to know whether my job is waiting or working, and how far
along it is, without any number that was not counted.
- **Acceptance Criteria:**
  1. A job SHALL be `queued` from its creation until the runner gives it a slot, and `running` only from then;
     `startedAtUTC` SHALL be the moment it got the slot.
  2. WHILE a job is `queued`, THE served record SHALL say how many jobs are ahead of it in its group's queue in this
     process, counted at the moment of the answer, and this count SHALL never be stored.
  3. Every stage SHALL report `done` and `total` in the unit its kind declares for it (`bytes`, `frames`,
     `candidates`, `steps`), with `total` null until it is known. No percentage, no estimate and no time remaining
     SHALL be stored or served. A stage that did not run SHALL be absent.
  4. WHEN a stage runs more than once in one job (the solve's searches: the phone solver with a hinted field, again
     with a generic field, then the plate solver), THE stage SHALL carry the pass it is on (a number from 1 and a
     short label), and `done` SHALL go down only when the pass number goes up.
  5. WHILE a job runs in this process, a read of it SHALL return its latest progress, not only the last progress
     written to disk.
  6. FOR a clip, THE solve job SHALL record which frames it decoded (first frame index, frame count, and their times
     in seconds into the clip).

### REQ-JOB-3: Every job ends, and a restart says what happened
**User Story:** As someone whose laptop restarted the server mid-job, I want the job to say it was interrupted, not
to say it is running forever.
- **Acceptance Criteria:**
  1. WHEN the API starts, THE runner SHALL find every stored job left `queued` or `running` by a process that no
     longer holds it.
  2. Such a `running` job SHALL become `interrupted` with the plain reason "The server stopped before this job
     finished." and SHALL never run again by itself [decided 2026-11-11: a file that stopped the server must not
     stop it again at every start].
  3. Such a `queued` job SHALL run, in its creation order, since none of its work had started.
  4. WHEN a read finds a `running` job that no live process holds, it SHALL be served and stored as `interrupted` by
     the same rule, so a job never stays `running` even without a restart.
  5. A `succeeded`, `rejected`, `failed`, `canceled` or `interrupted` job SHALL never change again; running the work
     again SHALL make a new job with a new id.
  6. EACH kind SHALL have a time bound on the whole job, computed by the kind from the stored asset and the request;
     a job past its bound SHALL end `failed` with the kind's time-limit sentence. THE solve's own search bound SHALL
     keep its meaning: a search stopped at 180 s still ends the job `succeeded` with an `unresolved` solution.
  7. `rejected` SHALL be a terminal state for kinds whose work ends in a check that can refuse (the display copy's
     comparison and the restoration's gate); the solve never ends `rejected`.

### REQ-JOB-4: Cancel
**User Story:** As someone who started the wrong file, I want to stop the job and know that it stopped.
- **Acceptance Criteria:**
  1. A cancel of a `queued` job SHALL make it `canceled` at once.
  2. A cancel of a `running` job SHALL record when it was asked, stop the work at the next point the kind can stop (a
     child process and every process it started are stopped at once; work in the server process stops between
     steps), and then make the job `canceled`.
  3. WHEN the commit point was passed before the work stopped, THE job SHALL end `succeeded`, and its record SHALL
     still show when the cancel was asked.
  4. A cancel of a finished job SHALL change nothing and say the job had already finished.
  5. No result of a `canceled` job SHALL ever be served.

### REQ-JOB-5: What is stored is whole and checked
**User Story:** As the web app, I want every record and solution I am served to be one the contract accepts, and
never half written.
- **Acceptance Criteria:**
  1. Every job record SHALL be checked against the contract before it is written and when it is read.
  2. Every solution SHALL be checked against the contract (the Python parser of the contracts lane,
     `parse_observation_solution` in `services/contracts/astronomy.py`) before it is written and when it is read.
  3. Every write SHALL be atomic: a reader SHALL see either the whole old file or the whole new one, after any crash.
     A result made of several files (a variant folder) SHALL become visible in one step.
  4. Results SHALL be written once and made visible before the commit point; a `succeeded` job SHALL always have
     every result it names, whole and valid; a result no `succeeded` job names SHALL never be served.
  5. A `failed`, `canceled` or `interrupted` attempt SHALL keep its job record and lose its partial outputs and any
     result it made visible [decided 2026-11-11]. Records a kind keeps for every finished attempt (the restoration's
     attempt records, REQ-RST-1.7) are records, not results, and stay.
  6. WHEN a stored record or solution fails the contract on read, it SHALL not be served; the answer SHALL say in
     plain words that the stored result could not be read, and a new start SHALL run the work again.
  7. Records and solutions written by today's code (`job.json`, `solution.json`) SHALL still be read and served, and
     SHALL never be rewritten.
  8. THE solution id SHALL stay `sol_` and the hash's first 16 hex characters for every solve of one upload; the job
     record ties each stored solution file to the producer and revision that made it [decided 2026-11-11: the web
     holds the solution id as the identity of that upload's solution].

### REQ-JOB-6: Results know which code made them
**User Story:** As the owner, when the solver improves I want my old uploads found and solved again, not served an old
answer as if it were current.
- **Acceptance Criteria:**
  1. Every job SHALL record its producer: a name and a revision number, which a unit raises when it changes results
     [decided 2026-11-11].
  2. A guard test SHALL pin a digest of each producer's source files with its revision, and SHALL fail when the
     digest changes unless the same commit raises the revision or deliberately updates the pinned digest
     [decided 2026-11-11: nobody forgets to raise it, and a comment edit does not rerun every stored solve].
  3. WHEN a start is asked and the newest `succeeded` job of that kind and subject ran an older revision, or names no
     producer (a job from today's code), THE start SHALL make a new job; otherwise it SHALL answer the `succeeded`
     job.
  4. THE store SHALL be able to count the uploads whose newest solve is from an older revision (unit 10i).
  5. WHILE a new solve runs, THE last good solution SHALL still be served, and the answer SHALL let the client tell
     that a newer solve is running.

### REQ-JOB-7: The request does not wait for the work
**User Story:** As the web app, I want to start a job and get it back at once, then follow it.
- **Acceptance Criteria:**
  1. A solve start with `wait=false` SHALL answer at once with the job (202 for a new job, 200 for one it joined or a
     current `succeeded` one). A display copy or restoration start answers at once in the same way (its route is the
     restoration spec's, REQ-RST-1.1).
  2. A solve start without `wait=false` SHALL keep today's answer (the job when it has finished), so the web keeps
     working until its own lane changes it [decided 2026-11-11]; the client leaving early SHALL not cancel the job.
  3. `GET /api/ingest/jobs/{job_id}` SHALL serve any job of any kind.
  4. Cancel SHALL be `POST /api/ingest/jobs/{job_id}/cancel` for any kind (the API allows only GET and POST across
     origins).
  5. Once the web follows jobs by polling, the waiting form SHALL be removed in its own unit.

### REQ-JOB-8: Bounded inputs
**User Story:** As the owner, I want a request that is too large refused with a plain reason before it costs memory.
- **Acceptance Criteria:**
  1. Every route SHALL have a stated body limit, enforced while the body arrives (with or without a
     `Content-Length`), in one place in `services/api.py`.
  2. THE upload route SHALL keep its own limits (256 MiB for a still, 2 GiB for a clip, `services/ingest/store.py`);
     routes that take no body (solve, cancel, every GET) SHALL refuse any body.
  3. A refused request SHALL answer 413 with one plain sentence naming the limit in bytes, and no path, file name,
     capture time or place.

### REQ-JOB-9: Nothing personal in records, errors or logs
- **Acceptance Criteria:**
  1. No job record, stored reason, served error or log line SHALL contain a path, a file name, a capture time or a
     place, as today (`tests/integration/test_solution_text_holds_no_place_or_time.py`,
     `tests/integration/test_no_place_or_time_in_urls.py`); the start-up sweep SHALL log counts only.

## Properties to test (derived from the requirements)

Python properties use hypothesis, TS properties fast-check; each runs under 20 seeds before a unit is ready, with rare
required cases seeded by `@example` or `examples`.

- **P-JOB-1** (REQ-JOB-3.5, 3.7, 4, 5.1): for any sequence of runner events (start, slot, progress, cancel, refusal,
  rejection, error, deadline, commit, process death, restart), every state change is a row of the transition table
  in `design.md`, the terminal states never change, and every record written passes the contract.
- **P-JOB-2** (REQ-JOB-1.3): for any number of starts of one file, kind and subject, from threads of one process and
  from two processes, exactly one attempt is created and the kind's work runs once; starts with different subjects
  each get their own job.
- **P-JOB-3** (REQ-JOB-3, 5.3 to 5.5): for a process killed after any one of a job's writes, after the restart sweep
  no record is `running` without a live holder, every visible result is named by a `succeeded` record and is whole
  and valid, no partial output of the job is left, and every record that was terminal before is unchanged byte for
  byte.
- **P-JOB-4** (REQ-JOB-4): for a cancel at any point (queued, each stage, inside the child, after the results, after
  the commit), the job ends `canceled` exactly when the cancel came before the commit point, else `succeeded` with
  the cancel time recorded; no process the job started is left; a `canceled` job serves no result.
- **P-JOB-5** (REQ-JOB-3.6): a job past its bound ends `failed` with its kind's sentence, within the bound plus the
  stated stop allowance; a solve whose search stops at its own bound still ends `succeeded` with an `unresolved`
  solution.
- **P-JOB-6** (REQ-JOB-3.1 to 3.3, 5.6, 5.7): for a store holding jobs in every state, records that fail the contract
  and records from today's code, the sweep makes each unheld `running` job `interrupted`, runs each unheld `queued`
  job in creation order, leaves every other record byte for byte, and serves today's records as solve jobs with no
  producer.
- **P-JOB-7** (REQ-JOB-2.3, 2.4): for any run, every stage is one its kind declares, in its unit, with `done` at most
  `total`, and `done` decreases only when the pass number increases.
- **P-JOB-8** (REQ-JOB-1.1, 1.2): for any arrival of jobs of every kind, each group starts its jobs in creation order
  and never runs more than one at once.
- **P-JOB-9** (REQ-JOB-2, 3, 4, 5.8, 6): TS and Python accept and refuse the same job samples: every state and kind,
  passes, frame spans, producers, subjects, requests, queue counts and cancel times, and one named fault for each
  refinement.
- **P-JOB-10** (REQ-JOB-8): for any route and any body length, a body over the route's limit is refused with 413 and
  the plain sentence before more than the limit is read, and a body inside it reaches the route unchanged.
- **P-JOB-11** (REQ-JOB-6.2): changing one byte of any file a producer lists fails the guard test unless the pinned
  revision or the pinned digest changes with it; a file the producer does not list does not.

## Decisions taken by the lead (2026-11-11), as `.lead/briefs/platform.md` records them
1. One runner for every kind, with stated concurrency, one job per file and kind, a time bound, cancel, and real
   progress only.
2. Jobs survive a restart: left `queued` or `running`, they are marked interrupted with a plain reason, or resumed
   where that is safe.
3. Stored records and solutions are checked on write and read; a solution names its solver and version.
4. Starting a job returns at once; the client polls, or the design says why a stream is better; the web's blocking
   call keeps working until the web changes.
5. The job contract changes first, in its own unit, with a fixture both sides judge.
6. Every route states and enforces its body limit.
7. Nothing personal in logs or errors.

## Rulings by the lead on this spec (2026-11-11)

The spec was accepted as the plan with seven rulings. Each is written in where it belongs and marked
[decided 2026-11-11] there.

1. **Restart.** Only `queued` jobs resume; an unheld `running` job becomes `interrupted` with a plain reason and is
   never rerun by itself. REQ-JOB-3.2.
2. **The producer's version.** An explicit revision number per producer, raised by a unit that changes results, plus
   a guard test that pins the digest of the producer's source files. REQ-JOB-6.1, 6.2; P-JOB-11; `design.md` 3.5.
3. **Who builds what.** The job contract is this lane's unit platform-1, after the contracts lane's first unit.
   `services/science/bounded_worker.py` (the stop hook) is added to this lane's files. The web's polling change
   belongs to the web's own lane; the blocking start keeps working until then. REQ-JOB-7.2; `design.md` 3.3, 3.9;
   `tasks.md`.
4. **What a stopped attempt keeps.** A failed or canceled attempt keeps its record and loses its partial outputs.
   The solution id stays stable per upload, and the job record ties each stored solution file to its producer and
   revision. REQ-JOB-5.5, 5.8.
5. **One runner for every kind.** The restoration spec's display copy and restoration run on this runner, and its
   "one restoration at a time" is this queue. This spec fits its kinds, states, stages and routes to that spec and
   lists, in `design.md` section 7, every line of that spec that must change to agree. The runner for the solve kind
   lands before the restoration lane wires its job and routes to it. REQ-JOB-1, 3.7.
6. **The lock key.** It may include what the kind needs (the subject). REQ-JOB-1.3.
7. **The scratch file left by `_write_json`.** Its fix and its test go into the runner unit. `tasks.md`, platform-5.

## What is open
- [open] The solve kind's whole-job bound: its step bounds sum to 300 s, but detection, stacking and the predicted sky
  run in the server process untimed. Set in platform-5 from measured real-case durations.
- [open] Whether a solve slows enough beside a running restoration to need one slot for both groups; measured in
  platform-5 once the restoration exists.
