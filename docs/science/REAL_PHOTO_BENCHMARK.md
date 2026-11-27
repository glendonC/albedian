# Real photo benchmark

Every photo of the real photo corpus ([REAL_PHOTO_CORPUS.md](REAL_PHOTO_CORPUS.md)) goes through the product's real upload path, and every solution the product claims is judged against an independent solver, astrometry.net. It is the kept, repeatable measurement that each later solver change is judged by. It changes no solver code.

Labels: [tested] by `tests/science/test_real_photo_benchmark.py`; [measured] in the kept run named beside it; [verified] read at the source named; [inference]; [proposed] a choice made here that a later unit may change; [open] not settled.

## Headline

**On main at `5653cb0ee` the product calibrates 12 of the 60 photos; the check judges 11 of them correct and cannot check the twelfth, and one matched star among them is not there.** In run `submission-count-5653cb0ee` (27 November; every record names that commit, with no uncommitted changes) no claim is wrong or a false solve as a whole. Of the 405 catalog_matched findings in the 11 claims the check can judge, it confirms 397; 7 are the right star placed badly, and finding `gaia-dr3-574112955678493824` in `rp-b-14` names a star that is not there: the named star lies 33.87 px from its detection, just past twice its 16.69 px tolerance, and no other catalog star lies within 77.0 px of it. `rp-b-07` calibrates, but the check did not solve that photo, so its claim is unchecked [measured].

On 11 November (run `realsky2-main`, main at commit `e65dc9ca`, the commit every record of that run names), photo `rp-b-06` was the only photo of 60 that the product calibrated. Of its 17 catalog_matched findings, the check confirmed 14. Finding `hip-84345` named a wrong star: the named star lay 9.31 px from the detection, while another catalog star lay 2.86 px from it. Finding `hip-86032` named a star that is not there: the named star lay 17.82 px from the detection and no catalog star lay within its 6.52 px tolerance. Finding `gaia-dr3-1201067439457715072` was the right star placed badly, 7.25 px against a 6.43 px tolerance [measured].

## What is measured, and how

**The product path.** For each photo, `research/benchmarks/real_photos/measure.py` posts the file's bytes to `POST /api/ingest` on a TestClient of `services.api.app`, with a temporary upload store inside the run's folder (never `data/`), then calls `POST /api/ingest/{sha256}/solve` and reads `GET /api/ingest/{sha256}/solution`: the same calls an upload in the app makes. The record keeps:

- what the stored asset carries, as booleans and labels only: a recorded capture time, a UTC offset, a place, the camera make and model and which camera fields are present, and where each came from. Never a time, an offset or a coordinate [tested];
- the job's state and stages with their real denominators, and the seconds for the upload and for the solve;
- the solution's status, the solver that produced it, its findings per evidence class, its quality numbers and its warnings (a warning that holds a capture value would be replaced by a note; none did);
- for each `catalog_matched` finding, its measured pixel, its FWHM and the catalog star it names, so the claim can be judged later without running the job again.

**The independent check.** `research/benchmarks/real_photos/oracle.py` runs astrometry.net 0.97's `solve-field` on the photo file, reads the solution back with astrometry.net's own `wcsinfo`, and turns each catalog star into a pixel with its own `wcs-rd2xy`. It calls nothing from the repository's WCS, detector or solver code, so a mistake in one cannot hide in the other [tested: the module's only repository imports are the corpus root and `services.media.guarded_call`, which itself imports only the standard library; importing `services.media` runs its package file and loads `services.science` into the process, and `oracle.py` names none of it]. astrometry.net is installed on this laptop for tests only and is never a dependency of the app; its 4100-series index files (built from Tycho-2) are listed in `data/downloads.json`.

Each photo gets a small, stated set of attempts, in order, until one solves:

| Attempt | Downsample | Image width searched | Source |
|---|---|---|---|
| `ds4-w25-120` | 4 | 25 to 120 deg | the lead's first run, which solved two earlier phone photos in 3 s and 5 s |
| `ds4-hint35` (only when the file has a FocalLengthIn35mmFilm tag) | 4 | 0.8 to 1.25 times the width that tag gives | [proposed] |
| `ds2-w10-120` | 2 | 10 to 120 deg | [proposed]: twice the pixels, so more faint stars, and fields down to a 2x zoom |

Every attempt adds `--crpix-center --tweak-order 3`, as the lead ran it, and is stopped at 60 s of wall-clock time [proposed: twelve times the slower of the lead's two solves]. The `--cpulimit` flag did not stop a long search when the lead first ran the check, so every external program `oracle.py` starts (each `solve-field` attempt, the five quick astrometry.net programs, and `git` for `code_state` with git redirect variables removed) runs under the repository's one guard for a program and everything it starts (`services/media/guarded_call.py`, shared with the clip path and capture-time discovery). The guard starts `solve-field` in a process group of its own and ends that whole group with SIGKILL when the program ends, at the bound, on a stop, and when the measuring process goes away for any reason, a SIGKILL that no code can catch included; an engine left behind by a program that has already exited goes with the group [tested]. In the measuring process SIGTERM, SIGHUP and SIGINT are only noted (a handler that does nothing, with Python's wake-up descriptor) and read at points the code chooses: before each photo, before and while waiting for each attempt, and once more when the command ends. Stopping is then ordinary code: the attempt's call is stopped, the folder is removed, and the run ends; a stop that lands as the program exits is a stop like any other [tested: twenty of twenty]. A signal the measuring process was started to ignore (as `nohup` ignores SIGHUP) stays ignored, and the handlers, the signal mask and the wake-up descriptor the run found are what it leaves. A stop read during the upload phase ends the run once the photo in progress is done. A program that cannot be started and a guard that does not start each end the run with the shared caller's own typed cause; so does a guard ended from outside when its program then ends by itself, and otherwise the program runs on until the attempt's bound, when its group is ended (the shared guard's stated limit) [tested]. The program's output and then its error stream make the attempt's log; solve-field writes every line the log's parser reads to its output, so the order of the two does not change what is read [tested]. Each scratch folder holds a lock file that the measuring process and the attempt's guard hold while they live, so a later start removes what a dead run left and never a live run's folder [tested: a table of endings, each stop signal alone, each ordered pair and all three, before the program starts, while its call starts, while it runs, as it exits leaving a child in its group, and during cleanup; the pairs again by natural timing; a SIGKILL of the measuring process and of the guard; each leaves no process of the group and no guard, with the signal state as found].

Three things changed from the third version of this benchmark, which had a supervisor of its own. A stop ends the group at once with SIGKILL, where the supervisor sent SIGTERM and waited 5 s: everything the engine writes is in the scratch folder, which is removed. A measuring process killed by SIGKILL leaves its attempt's folder, whose lock is then free, and the next start removes it; the supervisor removed it at once. A stop signal sent to the guard itself ends the call even when the measuring process was started with that signal ignored; the guard runs in a session of its own with no terminal, so such a signal is only ever sent to it on purpose.

Each attempt runs in a scratch folder inside the run's folder, which holds the config file, the temporary files and every output and is removed afterwards. Every folder this benchmark removes is made by `oracle.scratch_folder`, which removes exactly the folder that same call made, under a name it minted, and refuses any other [tested]. The bound is wall-clock time, so on a loaded machine an attempt does less work before it is stopped [inference].

astrometry.net writes FITS pixel coordinates, whose first pixel's centre is (1, 1); the repository's detections use array coordinates, whose first pixel's centre is (0, 0). The check subtracts one on each axis [tested on a made-up TAN header: its reference point comes back one pixel less on each axis].

**Judging a claim.** `research/benchmarks/real_photos/score.py` restates the simulated benchmark's rule (`research/benchmarks/phone_photos/score.py`) without importing its truth model. A claim is a `calibrated` solution. For each of its `catalog_matched` findings, the pixel the check's WCS gives that star is compared with the finding's measured pixel; the star is confirmed within max(4 px, 2 FWHM).

| Outcome | Meaning |
|---|---|
| `correct` | the median offset over the claim's matched findings is at most 3 px |
| `imprecise` | over 3 px, but at least half the findings are confirmed |
| `wrong` | under half confirmed, or a claim that names no star |
| `false_solve` | any claim on a tier D photo, whatever the check says |
| `unchecked` | a claim on a photo the check did not solve: never counted as correct |
| `unresolved`, `timeout`, `error` | no claim: not calibrated, stopped at the job's 180 s bound, or the upload or job failed |

**Each finding is also judged by itself**, against the catalog stars to V 8 (the repository's star tiles) that the check's WCS places near its detection [tested]:

| Verdict | Meaning |
|---|---|
| `confirmed` | the named star lies within tolerance of the detection |
| `wrong_star` | it does not, and another catalog star does: the claim names the wrong star |
| `misplaced` | no catalog star is within tolerance; the named star is the nearest one and within twice the tolerance [proposed]: the right star, placed badly |
| `no_star` | no catalog star within tolerance, and the named one is not the nearest or lies further: no star is where the detection is |

Another catalog star counts wherever it lies, inside the frame or not: the stars are taken from the frame and a margin around it of twice the claim's largest tolerance and half a pixel, the farthest a verdict looks, so a wrong star at the frame's edge is not judged misplaced [tested at each edge and corner]. A nearest other star farther than that is the nearest among those stars.

A `wrong_star` or `no_star` finding is a false claim of the product whatever the claim's outcome, and every claim holding one heads the run's summary by photo id. Records keep each finding's star, offset, tolerance, nearest other star and verdict.

**What the check cannot tell.** It judges a position, not a name: a finding is confirmed when the star it names lies where the check puts that star. When two catalog stars lie within tolerance of one detection, it cannot say which of them the detection is. Another star to V 8 falls within tolerance of a random point by chance: about 2.9% per finding at `rp-b-06`'s density (2,928 such stars in its frame, mean tolerance 6.22 px), so a `wrong_star` verdict can, rarely, be chance [measured, run `realsky2-main`].

**Two diagnostics, not the product path.**

1. *GPS time.* The upload path takes a capture time only from EXIF DateTimeOriginal with OffsetTimeOriginal (`services/ingest/metadata.py`). For a photo whose file has a GPS time stamp (GPSDateStamp and GPSTimeStamp, UTC by definition) and a place but no UTC offset tag, the phone photo solver is called directly with the GPS time, as the job's own phone passes call it (the field from the file's 35 mm tag first, when it has one, then the generic fields), under the job's own 180 s bound. Its claim is judged like any other.
2. *Ranking and field.* For a photo the check solved, the repository's own ranking of detections (`_rank_stellar_candidates`, called as `solve_phone_photo` calls it) is scored against the check's WCS: how many of the 65 detections the search uses lie within max(4 px, 2 FWHM) of a catalog star to V 8 (the repository's own star tiles, placed by the check's WCS), against what chance alone would give; and at what rank the brightest stars in the frame sit. Then the diagonal field the job assumes (from the file's 35 mm tag, or the nearest of the solver's eight generic fields) against the check's own, corner to corner.

**Running it.**

```
uv run python -m research.benchmarks.real_photos oracle --label <label>
uv run python -m research.benchmarks.real_photos measure --label <label> [--workers 1] [--oracle-label <label>]
```

Records go under `.cache/solver-bench/real-photos/<label>/` (`upload/`, `oracle/`, `diagnostics/`, then `frames.jsonl`, `summary.json` and `summary.md`); a photo already recorded is not run again, so a stopped run goes on where it stopped. `--workers` is 1 by default and at most 2, for this shared laptop. `ALBEDIAN_REAL_PHOTO_BENCH` moves the whole bench root; the tests point it at pytest's temporary folder, so no test reaches a kept record.

## The check and its limits

- It is one solver. A photo it does not solve is not shown unsolvable, and its failures are not ours to explain; a claim on such a photo is `unchecked`, never correct.
- Its index files cover wide fields only: the 4100 series (Tycho-2) has quads from about 22 arcmin to about 33 deg [verified: data.astrometry.net/4100/ lists 4107 to 4119, and its README names Tycho-2].
- Its records were made in run `realsky2-full` and reused by run `realsky2-main` (same photos, same attempts). It solved 27 of 60 photos: 8 of 13 in tier A, 19 of 22 in tier B, none of the 14 Moon and Sun frames of tier C and none of the 11 tier D frames [measured]. It solved none of the five full-frame 14 to 16 mm frames (`rp-a-01`, `rp-a-02`, `rp-a-03`, `rp-a-08`, `rp-a-09`), each wider than about 95 deg [measured; the cause, lens distortion over so wide a field, is inference]. Of the five earlier phone photos it solved four: `rp-b-01` and `rp-b-02`, as the lead did, and `rp-b-04` (a 5x zoom) and `rp-b-05` (a crop), not `rp-b-03` [measured].
- How finely it judges: the median distance between its paired sources and index stars is 1.3 px over the tier A photos it solved and 1.7 px over tier B; on four photos it is over 3 px (`rp-a-11` 4.0, `rp-a-12` 7.7, `rp-b-08` 3.3, `rp-b-11` 3.2), so the 3 px bound cannot be judged there and their ranking rows below are rough [measured].
- astrometry.net does not state a licence for these index files: its readme points to a file `GETTING-INDEXES` that neither its code repository's top level nor the index folder holds. They are used only on this machine by this check and are never shipped [verified: the pages named in `data/downloads.json`, read 2026-11-11].
- Timings are not evidence of speed: the machine was shared. The one-minute load average after each upload had a median of 20.5 and reached 32.2 in run `realsky2-main` (24.3 and 243.3 in run `realsky2-full`) [measured].

## Outcomes on main on 27 November (run `submission-count-5653cb0ee`)

Run `submission-count-5653cb0ee`, kept under `.cache/solver-bench/real-photos/submission-count-5653cb0ee/`: main at commit `5653cb0ee` on 2026-11-27, all 60 photos of `corpus.json` through the upload path with one worker, every claim judged against the check's records of run `realsky2-full`. Every record and the summary name that commit, with no uncommitted changes, and the product's optional astrometry.net stage was off (`ALBEDIAN_ASTROMETRY_NET` unset) [measured]. Only the upload and score phases ran, so neither diagnostic was made:

```
uv run python -m research.benchmarks.real_photos measure --label submission-count-5653cb0ee --workers 1 --oracle-label realsky2-full --phases upload,score
```

| Tier | Photos | Solved by the check | Our statuses | Correct | Imprecise | Wrong | False solves | Unchecked | Stopped at the bound | Median s | Worst s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 13 | 8 | calibrated 1, predicted_only 2, unresolved 10 | 1 | 0 | 0 | 0 | 0 | 0 | 20.4 | 72.3 |
| B | 22 | 19 | calibrated 11, predicted_only 3, unresolved 8 | 10 | 0 | 0 | 0 | 1 | 0 | 11.7 | 54.2 |
| C | 14 | 0 | predicted_only 11, unresolved 3 | 0 | 0 | 0 | 0 | 0 | 0 | 20.8 | 65.3 |
| D | 11 | 0 | predicted_only 6, unresolved 5 | 0 | 0 | 0 | 0 | 0 | 0 | 13.4 | 69.4 |
| all | 60 | 27 | calibrated 12, predicted_only 22, unresolved 26 | 11 | 0 | 0 | 0 | 1 | 0 | 18.7 | 72.3 |

On 19 November two photos calibrated, `rp-a-10` and `rp-b-01` (run `si-search2-all60`, [PHONE_PHOTO_BENCHMARK.md](PHONE_PHOTO_BENCHMARK.md)). The last solver change before this run is association-grow-one (`2d3054f5d`): when the phone solver's first look at a search's candidate does not pass the held-out gate, it grows the first association and looks once more, and takes that look only when it passes; the gate's bounds are unchanged. Seconds are the solve call's, at a one-minute load of 11.7 to 62.8, so they are not evidence of speed [measured].

| Photo | Tier | Judged | Findings confirmed | Misplaced | Wrong star | No star | Median offset px | Worst offset px | Check residual px |
|---|---|---|---|---|---|---|---|---|---|
| `rp-a-10` | A | correct | 19 of 19 | 0 | 0 | 0 | 0.81 | 2.58 | 0.825 |
| `rp-b-01` | B | correct | 43 of 43 | 0 | 0 | 0 | 2.22 | 3.14 | 1.876 |
| `rp-b-02` | B | correct | 46 of 46 | 0 | 0 | 0 | 2.46 | 6.00 | 2.286 |
| `rp-b-06` | B | correct | 20 of 21 | 1 | 0 | 0 | 3.00 | 8.25 | 2.294 |
| `rp-b-07` | B | unchecked | not judged | | | | | | |
| `rp-b-08` | B | correct | 39 of 43 | 4 | 0 | 0 | 2.99 | 7.21 | 3.305 |
| `rp-b-09` | B | correct | 32 of 33 | 1 | 0 | 0 | 1.05 | 6.81 | 0.885 |
| `rp-b-10` | B | correct | 23 of 23 | 0 | 0 | 0 | 2.40 | 6.40 | 2.41 |
| `rp-b-12` | B | correct | 39 of 39 | 0 | 0 | 0 | 0.47 | 1.56 | 0.823 |
| `rp-b-14` | B | correct | 34 of 35 | 0 | 0 | 1 | 2.84 | 33.87 | 2.747 |
| `rp-b-16` | B | correct | 44 of 44 | 0 | 0 | 0 | 1.73 | 5.42 | 1.651 |
| `rp-b-22` | B | correct | 58 of 59 | 1 | 0 | 0 | 0.95 | 7.61 | 1.043 |

The 8 findings the check does not confirm [measured]:

- **Misplaced**, the right star placed between one and two tolerances off: `gaia-dr3-1487434040320076800` in `rp-b-06` (8.25 px against a 6.39 px tolerance); `hip-61932`, `hip-61084`, `hip-59747` and `hip-85792` in `rp-b-08` (7.14 px against 6.46, 7.09 against 6.54, 6.81 against 6.31 and 7.21 against 6.24); `gaia-dr3-5926920079553285760` in `rp-b-09` (6.81 against 6.53); `hip-87261` in `rp-b-22` (7.61 against 4.83).
- **No star**: `gaia-dr3-574112955678493824` in `rp-b-14`, 33.87 px from its detection against a 16.69 px tolerance, with no other catalog star within 77.0 px. Why the product matched that detection to that star is [open].

## Outcomes on main on 11 November (run `realsky2-main`)

Run `realsky2-main`, kept under `.cache/solver-bench/real-photos/realsky2-main/`: main as it was on 2026-11-11, all 60 photos through the upload path with one worker, both diagnostics, every claim judged against the check's records of run `realsky2-full`. Every upload and diagnostics record and the summary name commit `e65dc9ca`, with no uncommitted changes [measured]. "Before" is run `realsky2-full`, which measured the branch's earlier base (commit `dcdbd65d` with the corpus on top), before main's solver changes; it is given in brackets where it differs.

| Tier | Photos | Solved by the check | Our statuses | Correct | Imprecise | Wrong | False solves | Unchecked | Stopped at the 180 s bound | Median s | Worst s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 13 | 8 | unresolved 11, predicted_only 2 | 0 | 0 | 0 | 0 | 0 | 0 (before 7) | 20.5 (186.4) | 84.0 (212.8) |
| B | 22 | 19 | calibrated 1, unresolved 14, predicted_only 7 | 0 | 1 | 0 | 0 | 0 | 1 (9) | 21.6 (130.1) | 196.4 |
| C | 14 | 0 | unresolved 6, predicted_only 8 | 0 | 0 | 0 | 0 | 0 | 0 (3) | 13.0 (46.5) | 89.5 (184.5) |
| D | 11 | 0 | unresolved 5, predicted_only 6 | 0 | 0 | 0 | 0 | 0 | 0 (3) | 16.0 (35.7) | 59.5 (191.0) |
| all | 60 | 27 | calibrated 1, unresolved 36, predicted_only 23 | 0 | 1 | 0 | 0 | 0 | 1 (22) | 17.7 (105.0) | 196.4 (212.8) |

`predicted_only` is a solve that found nothing on a photo with a recorded time and place, to which the predicted sky was added; it is not a claim. Seconds are the solve call's, upload excluded. The statuses and the claims are the same in both runs; main ends its searches far sooner. The check solved its photos with `ds4-w25-120` 20 times, `ds4-hint35` 4 times and `ds2-w10-120` 3 times (tier A: 6, 1, 1; tier B: 14, 3, 2) [measured].

**No claim is wrong or a false solve as a whole** [measured]. **Unsolved on real data**: one claim in 60 photos, and it is imprecise and holds two false findings.

| `rp-b-06` finding | Offset from the check's place for the named star | Tolerance | Nearest other catalog star | Verdict |
|---|---|---|---|---|
| 14 findings | within tolerance | | | confirmed |
| `gaia-dr3-1201067439457715072` | 7.25 px | 6.43 px | 29.93 px | misplaced |
| `hip-84345` | 9.31 px | 6.52 px | 2.86 px | **wrong star** |
| `hip-86032` | 17.82 px | 6.52 px | 17.13 px | **no star** |

`rp-b-06` (iPhone 11, tier B) is the first calibrated solution of a real photo through the upload path; its median offset of 3.99 px makes it `imprecise`, not `correct`, and the check's own residual on it is 2.29 px [measured, identical in both runs]. The product's own quality reports a held-out median residual of 3.48 px over all 19 held-out pairs, while its gate took the median of the 11 inliers only [code: `evaluate_coincidence_gate` keeps residuals within the inlier gate].

## Diagnostic 1: the GPS time stamp (not the product path)

Nine corpus files carry a GPS time stamp and a place but no UTC offset tag, so the upload path gives them no capture time. Run `realsky2-main`; the statuses, inliers and verdicts are the same in `realsky2-full`, the seconds differ (before in brackets).

| Photo | Tier | Upload path | With the GPS time | Held-out inliers | Judged | Seconds |
|---|---|---|---|---|---|---|
| `rp-a-02` | A | unresolved | calibrated | 12 of 17 | unchecked | 29.7 (17.2) |
| `rp-b-01` | B | unresolved | calibrated | 40 of 41 | **correct**: 46 of 46 findings confirmed, median 2.21 px, worst 3.14 px | 16.9 (17.2) |
| `rp-b-02` | B | unresolved | unresolved | 0 of 2 | no claim | 18.4 (16.2) |
| `rp-b-04` | B | unresolved | unresolved | 0 of 0 | no claim | 12.3 (10.7) |
| `rp-b-14` | B | unresolved | unresolved | 0 of 0 | no claim | 56.7 (45.5) |
| `rp-b-16` | B | unresolved | unresolved | 0 of 0 | no claim | 20.7 (19.5) |
| `rp-c-11` | C | unresolved | unresolved | 0 of 0 | no claim | 15.2 (8.9) |
| `rp-c-13` | C | unresolved | unresolved | 0 of 0 | no claim | 12.4 (10.0) |
| `rp-c-14` | C | unresolved | unresolved | 0 of 0 | no claim | 9.7 (12.0) |

[measured] Reading the stamp turns `rp-b-01` into a solve the independent check confirms finding by finding, with no wrong star and no missing one: the only `correct` solution of a real photo measured so far, and it is off the product path. `rp-a-02` also calibrates, on a 15 mm full-frame field the check cannot solve, so it stays unchecked.

## Diagnostic 2: the ranked pool and the field (not the product path)

For the 27 photos the check solved; the 23 whose check residual is at most 3 px are summarised, every row is in each run's `summary.md`. Every figure below is the same in both runs [measured]:

- **The pool is mostly stars.** Of the 65 detections the search uses, a median of 55 lie on a catalog star to V 8 (from 2 to 65; at least 50 on 15 photos). Over all detections the share on a star is 5.4% at the median, 5.2 times what chance gives. Four photos have fewer than 33 of 65 on a star: `rp-a-05` (17), `rp-b-13` (2), `rp-b-14` (32), `rp-b-18` (28).
- **The brightest stars are missing.** Of the five brightest catalog stars in each frame (115 in all), 46 rank in the top 65, 8 rank 66 to 85, 33 are detected but left out of the pool, and 28 are not detected at all. On 10 of the 23 photos none of the five brightest is in the top 65: `rp-a-04`, `rp-a-05`, `rp-a-06`, `rp-a-07`, `rp-b-04`, `rp-b-09`, `rp-b-10`, `rp-b-14`, `rp-b-15`, `rp-b-18`. On `rp-b-01` they rank 63 to 85, as the survey found.
- **The field from the 35 mm tag** is within 5% of the check's on 15 of the 25 tagged photos and off by more than 10% on 6, all crops or zooms: `rp-a-13` (+48.5%), `rp-b-05` (+61.6%), `rp-b-11` (+21.3%), `rp-b-13` (+156.8%), `rp-b-15` (+10.3%), `rp-b-17` (-29.9%). The eight generic fields come within 2% of the check's on 4 photos; five fields are under 60 deg, below the smallest generic one (82 deg).

## What stops real photos, ranked

Each line names the number behind it, from run `realsky2-main` [measured] (run `realsky2-full` in brackets where it differs), and the photos it applies to.

1. **No time and place on the upload path.** Of the 27 photos the check solved, only 9 reach the phone solver with both (11 have no capture time, 13 no place); the other 18 get the blind search, whose catalog is Orion and Taurus plus the brightest stars [code: `solve_phone_photo`]. Reading the GPS stamp gives one more photo a correct solve (`rp-b-01`) and one an unchecked one (`rp-a-02`) out of the nine it applies to.
2. **With time and place, the search still finds nothing** on 8 of those 9 (`rp-a-06`, `rp-a-07`, `rp-b-05`, `rp-b-08`, `rp-b-09`, `rp-b-10`, `rp-b-13`, `rp-b-22`); `rp-b-06` calibrates, imprecisely and with two false findings. Seven of the eight have none of their five brightest stars in the search pool (`rp-a-06`, `rp-a-07`, `rp-b-09`, `rp-b-10`) or a field the tag misstates by over 8% (`rp-b-05`, `rp-b-13`, `rp-b-22`).
3. **The brightest stars are left out of the pool**: on 10 of 23 reliably checked photos none of the five brightest ranks in the top 65; 61 of 115 such stars are either undetected (28) or detected and ranked out (33).
4. **The detection flood**: on 43 of 60 photos the job counts over 20,000 detections (up to 164,818). On main only 1 of the 60 jobs stops at the 180 s bound, `rp-b-14` (before: 22, 11 of them on photos the check solved), so the bound no longer explains the failures.
5. **A field the tag misstates**: off by over 10% on 6 of 25 tagged photos (crops and zooms, listed above); the generic list fits only 4 of 27.
6. **False findings in the one claim**: `rp-b-06` names a wrong star (`hip-84345`) and a star that is not there (`hip-86032`), places a third badly, and puts stars 3.99 px from the check at the median, over the 3 px bound.

## Privacy

Records, this page and the reports hold booleans and labels about a photo's time and place, never the values: no coordinate and no capture time of any photo [tested on made-up files and, in the end-to-end test, against the corpus file's own values]. A product warning that held a capture value would be replaced by a note; in runs `realsky2-full` and `realsky2-main` none did [measured]. The check's records keep the sky position of each field's centre under the cache only, never in git.

## Limits and open questions

- Run `submission-count-5653cb0ee` shows main as it was on 2026-11-27 and run `realsky2-main` as it was on 2026-11-11; "What stops real photos, ranked" is from the earlier run and was not measured again. Each later solver change should be measured with the commands above, and its records will name its commit [open].
- The check's records were made in run `realsky2-full`, before records named a commit; that run's code is the branch's first version, whose three modules' SHA-256 values its report gives.
- No ground truth beyond the check: the judged claims are judged by one solver, and 33 of 60 photos have no check at all [open].
- The check's attempt set and 60 s bound are [proposed]; a wider set might solve the five ultra-wide frames.
- Under heavy load the check's wall-clock bound and the job's 180 s bound do less work; which photos stop at a bound may differ on a quiet machine [inference].

