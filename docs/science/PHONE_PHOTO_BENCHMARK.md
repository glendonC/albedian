# Phone-Photo Benchmark

A benchmark for Albedian's plate solver (roadmap Track 1, M3, first part). It renders real Gaia DR3
and Hipparcos 2007 stars from the G 12 catalog tiles through modelled phone cameras, so every frame
comes with its exact ground truth: the camera, its attitude, the moment and the place. Hard negatives
are scored on false solves.

Everything it renders is simulated (`evidenceClass: simulated`). A solve here measures how a solver
behaves on modelled phone photos; it never shows that a real photo solves. Real frames are reported
separately below, with no ground truth yet.

Code: `research/benchmarks/phone_photos/`. Tests: `tests/science/test_phone_photo_*.py` and
`tests/science/test_real_clip_baseline.py`.

## Run it

```bash
uv run pytest tests/science/test_phone_photo_benchmark.py            # the CI subset, about 10 s
uv run python -m research.benchmarks.phone_photos run --suite full --solver today-api --workers 8 --timeout 120
uv run python -m research.benchmarks.phone_photos.real_clips         # local real-clip corpus only
```

The full run writes `summary.json`, `frames.jsonl` and `summary.md` under
`.cache/solver-bench/<suite>-<solver>/` (ignored by git); `--save-frames` adds each frame's JPEG and
truth. The summary records the software versions, since JPEG bytes can change with libjpeg-turbo.

## What a frame is

A `FrameSpec` holds every choice behind a frame, drawn from the suite's seed (20261104), so a frame
rebuilds exactly from its spec: camera, lens, moment and place, pointing, sky, ground, sensor and
processing. Its truth is a `CameraTruth`: pinhole intrinsics, a Brown-Conrady or Kannala-Brandt lens,
the attitude (azimuth, altitude, roll) and an `ObservationFrame` (UTC, latitude, longitude). The truth
maps any pixel to ICRS and back, and it shares no code with the solver's own `WCSModel`, so a mistake
in one cannot hide in the other.

The chain from a catalog direction to a pixel [tested]:

- proper motion from the tiles' J2016.0 to the frame's date;
- IAU 1976 precession, annual aberration, nutation by Meeus's short series (stated good to 0.5 arcsec
  in longitude and 0.1 arcsec in obliquity), IAU 1982 sidereal time with the equation of the
  equinoxes, the local horizon, and Saemundsson's refraction;
- the camera's attitude, lens and intrinsics.

Checked against ERFA's own test values (`t_erfa_c.c`) and Meeus's worked examples: precession
angles exact to 1e-18 rad, sidereal time to 3e-15 rad, nutation within 0.03 arcsec of `eraNut80`,
Meeus's theta Persei apparent place within 0.17 arcsec, his Venus altitude and azimuth within
0.0001 degree [tested]. UT1 is taken equal to UTC; the real difference stays under 0.9 s, which
turns the sky by at most 13.5 arcsec, a convention of the benchmark rather than a property of real
photos [inference].

The lens inverses are exact inside each lens's valid region (where it is one-to-one); dense sweeps
over the tested coefficient ranges give worst round trips of 3e-14 (Brown-Conrady, 15,300 lenses)
and 6e-15 (Kannala-Brandt, 5,355 lenses) [tested].

## Cameras

Fields of view are the makers' published specifications (quoted verbatim in `cameras.py`):

| Camera | Diagonal field | Source |
|---|---|---|
| iPhone 18 Pro, 48MP Fusion Main | 84.06 deg, from its 24 mm equivalent | apple.com/iphone-18-pro/specs |
| iPhone 18 Pro, 48MP Fusion Ultra Wide | 120 deg | apple.com/iphone-18-pro/specs |
| Pixel 11 Pro, wide | 82 deg | store.google.com/product/pixel_11_pro_specs |
| Pixel 11 Pro, ultrawide | 123 deg | store.google.com/product/pixel_11_pro_specs |
| Galaxy S25 Ultra, wide | 85 deg | samsung.com (Lebanon S25 series comparison, updated 2025-01-28) |
| Galaxy S25 Ultra, ultra-wide | 120 deg | samsung.com (same page) |
| Head-worn video camera (a modelled class, no product) | swept 80 to 110 deg [open] | none |

Entrance pupils come from the field, the f-number and the sensor size where the maker gives one;
Apple publishes no sensor size, so the iPhone pupils assume typical sensor classes [inference].
Residual distortion after a phone's correction, and the fisheye of an uncorrected ultra wide, are
sampled ranges [inference]; makers publish neither.

## Scene, optics, sensor and processing

- **Sky:** natural brightness and its airmass law, and moonlight, from Krisciunas and Schaefer (1991,
  PASP 103, 1033); artificial light as a parametric glow with domes toward towns that fade with
  altitude [inference: shaped after Garstang's model, not computed from it]; zenith totals from about
  22 down to 17.5 mag/arcsec^2.
- **Moon and Sun:** the Moon from Meeus chapter 47 truncated to its largest terms, within 0.0032
  degree of his example 47.a and 28 arcsec of `eraMoon98` [tested], topocentric, lit at its true
  phase; the Sun from Meeus chapter 25. Frames are taken with the Sun below -18 degrees.
- **Stars:** every tile star down to a per-frame limit where its peak would fall below a fifth of the
  sky noise (at most G 12); V from G and BP-RP by Riello et al. (2021); colour from a temperature
  relation [inference]. Rendered stars land on their truth pixels within 4e-7 px [tested].
- **Optics:** a two-part Gaussian PSF growing toward the corners, a 2 % halo, glare and a mirrored
  ghost around very bright sources, vignetting, extinction and cloud [inference].
- **Sensor:** Bayer mosaic, shot and read noise, dark current, hot pixels, full well, 12-bit
  quantization, Earth-rotation trails sampled over the exposure; ranges per native pixel
  [inference: makers publish none].
- **Processing:** demosaic, white balance, lens-shading correction, a night-mode lift that keeps
  the brightest half percent below clipping, black clip, sRGB or HLG tone curve, smoothing,
  sharpening, 8-bit JPEG with 4:2:0 chroma [inference: a generic pipeline].
- **Ground:** procedural hills, trees and buildings with lit windows and lamps [inference].

At phone binning the PSF is often undersampled; a star's pixel centroid then leans up to about
0.1 px toward its pixel's centre. That is physics the solvers face, not a rendering error [tested].

## Hard negatives

Two kinds:

- **A real sky behind them:** overcast, aircraft lights in cloud, the Moon in haze. They keep their
  true camera, so a claim that matches the truth is correct (unexpected, not false) and only a wrong
  claim is false.
- **No sky at all:** a photo of a screen (a random-dot screensaver or a dark interface), random
  star-like dots, a dark frame. Any claim is false.

## Scoring

Each claim (a solution the solver says passed its held-out verification) is judged two ways: the
field error, where the claimed WCS puts the sky seen at a grid of sky pixels against where the truth
puts it; and match precision, the share of the solver's catalog matches that sit on that star's true
pixel within max(4 px, 2 FWHM, 1.5 trail lengths).

| Outcome | Meaning |
|---|---|
| correct | median field error at most 3 px, the bound the product's own held-out gate promises |
| imprecise | the right stars (match precision at least 0.5), but the field off by more than 3 px |
| wrong | a claim on the wrong stars |
| false-solve | a claim on a frame with no sky behind it |
| unresolved, timeout, error | no claim |

A false claim is any claim that is not correct. Track 1's bar is zero false claims on the hard
negatives. Time per frame is the solver's wall time on one worker process.

## Results: baseline 18-star triangle solver (simulated)

The full suite, seed 20261104: 210 positives over the seven cameras (30 each) and 60 hard negatives
(10 of each kind), at 2016 x 1512 or 1512 x 2016 for the phones and 1440 x 1920 for the head-worn
camera. Solver adapter `today-api`: the baseline 18-star triangle solver as exposed by
`POST /api/science/solve`, on its default 18-star catalog, blind, with a 120 s limit per frame; 8 worker
processes on an 18-core arm64 Mac. Machine-readable:
`research/benchmarks/phone_photos/results/today-solver-baseline.json`.
Any later change to the solver or its gate needs a re-run before these numbers describe it.

| Frames | Count | correct | imprecise | wrong | unresolved | timeout |
|---|---|---|---|---|---|---|
| Positives | 210 | 0 | 0 | 1 | 159 | 50 |
| Hard negatives | 60 | 0 | 0 | 0 | 42 | 18 |

What it shows [tested, on simulated frames only]:

- **Solve rate 0 %.** No camera fared better: 0 correct on each. Time per frame: median 15 s; the 95th
  percentile and the maximum sit at the 120 s limit. Timeouts gather on the wide fields: the iPhone
  Ultra Wide (17 of 30), the Pixel ultrawide (13), the Galaxy ultra-wide (10) and the head-worn
  camera (9), against 0 or 1 for the main cameras.
- **Catalog coverage is not the only problem.** Even on the fields its 18 stars cover, it solved
  nothing: Orion 0 of 6, Taurus 0 of 9. Why is not diagnosed here [open]; M2 replaces this solver.
- **It certified a wrong sky once.** On frame `pos-063` (Pixel 11 Pro wide, a 30 s exposure of
  Cassiopeia under a dark sky, 15,555 detections) it returned `calibrated` with 7 held-out inliers
  and a self-reported median residual of 2.4 px, and placed the frame on Orion and Gemini, about
  70 degrees from the truth: it had matched Capella, Elnath, Bellatrix, Mintaka, Alnitak, Rigel,
  Betelgeuse, Pollux and Hatsya to noise. At that density a random point lies within 3 px of a
  detection 15 % of the time. The held-out gate (at least 6 inliers, median at most 3 px) does not
  weigh that chance, so a search over thousands of hypotheses finds one that passes.
- **No false solve on the hard negatives**, but 18 of the 60 hit the time limit before the solver
  finished, and the case above shows the gate can pass noise. Zero false solves here is therefore no
  evidence that the gate is safe [inference]. A replacement needs an acceptance test that weighs each
  match against its chance of coincidence (the Bayesian decision of Lang et al. 2010, AJ 139, 1782,
  is one) and, as the roadmap asks, agreement between two independent paths [proposed].

Two adapters were not run on the full suite: `today-allsky` (the 71-star all-sky list) and
`horizon-moon`. Blind, the baseline triangle solver builds its triangles from only the 18 brightest stars of
whatever catalog it gets (`services/science/solver.py`, the `cat_pool` line) [verified, read in
code], so the larger list adds few triangles to a frame; and the horizon and Moon solver never claims
held-out verification by design, so the benchmark would score every frame as no claim [inference].
On the CI subset, `today-api` solved 0 of 4 positives and claimed nothing on its 4 negatives.

## Results: G 12 tile-backed phone-photo solver (simulated)

Adapter `phone-photo` (`services/science/phone_photo_solver.py`, roadmap Track 1 M2) replaces the
18-star list with the local Gaia DR3 and Hipparcos 2007 `L0` and `L1` star tiles, normalizes local
background noise in a 6x6 tile grid to counteract corner lens-shading amplification, downweights
dark-silhouette ground lamps and integer-locked Bayer hot-pixel impulses, searches visible horizon
pairs via a vectorized triad attitude voter when capture time and observer location are present (with
scale-invariant ICRS 3D triangle matching when time or place is unknown), refines 3D attitude, focal
scale, and `BrownConrady` or `KannalaBrandt` radial distortion (`PhoneCameraWCS`), and gates every
claim on both the Bayesian coincidence log-odds model of Lang et al. (2010, AJ 139, 1782) and
agreement between independent training and held-out refits [tested, on simulated frames only]:

| Subset | Positives (`correct` / total) | False claims | Hard negatives (`false-solve` / total) | Match precision on claims |
|---|---|---|---|---|
| CI suite (`ci-00..07`) | 3 / 4 (`ci-00`, `ci-01`, `ci-02`) | 0 | 0 / 4 | 1.00 |
| Stratified full sample (24 positives, 20 negatives) | 18 / 24 (75.0 %) | 0 | 0 / 20 | 1.00 |

What it shows [tested, on simulated frames only]:

- **CI phone positives (`ci-00`, `ci-01`, `ci-02`) all solve `correct`.** `ci-00` (iPhone 18 Pro
  Main, Orion) solves with 56 held-out inliers and 0.15 px median field error; `ci-01` (Pixel 11 Pro
  ultrawide, 123 deg field, suburban sky with hill silhouette) solves with 16 held-out inliers; and
  `ci-02` (Galaxy S25 Ultra wide, Cygnus) solves with 57 held-out inliers and 0.08 px median field
  error. `ci-03` (a single `1/30 s` head-worn video frame with a 0.63 mm pupil where the brightest
  star deposits fewer than 5 electrons in green against 8.8 e read noise) honestly returns
  `unresolved`.
- **Frame `pos-063` (where `today-api` certified a wrong sky) solves `correct`.** On `pos-063`
  (Pixel 11 Pro wide, 30 s exposure of Cassiopeia with 10,357 detections), `phone-photo` recovers
  Cassiopeia with 58 held-out inliers, 0.18 px median field error (0.43 px 95th percentile), and
  100 % match precision, while rejecting dense random-dot fields (`evaluate_coincidence_gate`).
- **Rectilinear and fisheye ultra-wide lenses.** On the stratified full-resolution sample,
  `phone-photo` solves 11 of 11 iPhone 18 Pro Main frames (median field error 0.09 to 0.28 px), 4 of
  4 Pixel 11 Pro Wide frames (median field error 0.14 to 0.24 px), and 3 of 9 iPhone 18 Pro Ultra
  Wide frames including uncorrected 120 deg `KannalaBrandt` fisheye frames (`pos-032`, `pos-034`,
  `pos-035`, with median field errors of 0.62 px, 0.31 px, and 0.08 px), with zero false claims and
  zero false solves across all tested positives and hard negatives.

## Real clips (no ground truth)

The baseline 18-star solver on the three local real-clip captures (`real_clips.py`): six single
frames spread over each clip and one five-frame stack from its middle (using the frame stacker), on the
decoded luma plane of the original frames, with a 150 s limit per run. Ids are neutral; nothing
finer than this table is published.

| Clip | Capture format | Runs | Statuses | Detections (min / median / max) | Claims |
|---|---|---|---|---|---|
| A | HEVC, 8-bit, HLG (ARIB STD-B67), portrait | 7 | 6 unresolved, 1 ambiguous | 161 / 621 / 1,192 | 0 |
| B | HEVC, 8-bit, HLG (ARIB STD-B67), portrait | 7 | 5 unresolved, 2 ambiguous | 6,705 / 7,374 / 7,966 | 0 |
| C | HEVC, 8-bit, HLG (ARIB STD-B67), portrait | 7 | 4 timeouts, 3 unresolved | 534 / 2,521 / 3,117 | 0 |

Detections count the runs that finished; a timeout records none. **Unsolved on real data**
[tested]: no verified solution in any of the 21 runs. Likely reasons [inference]: the default
catalog covers only Orion and Taurus; most detections are not stars but encoding noise in frames
whose sky decodes close to black, and lights on the ground; and the triangle search runs past
150 s on some frames. At clip B's detection density, the simulated false claim above shows that a
claim, had one come, could not have been trusted.

## What real frames need for ground truth

For the golden-corpus field capture protocol [proposed]:

1. The original files from the camera app (HEIC or JPEG, and RAW where the phone offers it) with
   untouched metadata: capture time from a phone clock checked against network time, location,
   lens and exposure; a tripod or a braced phone; each field shot with both the main and the
   ultra wide camera.
2. An independent solution for each frame: a narrow-field camera co-mounted on the same bracket and
   solved locally, or a long exposure of the same field that a local blind solver verifies; and one
   frame per session with a landmark of known azimuth on the horizon.
3. The real hard negatives from the same phone: overcast skies, aircraft, the Moon in haze or cloud,
   a screen, a dark frame with the lens covered; these score false solves on real pixels.

## Known limits

- Video compression is JPEG standing in for HEVC [open].
- No diffuse Milky Way, zodiacal light or light of stars fainter than G 12 [open].
- One generic image pipeline; real night modes merge many frames and align on the ground [inference].
- The head-worn camera's field and aperture are swept, not known [open].
- Detection counts are not recorded when a solver times out.
