# Design: restoration and export for every upload

Requirements: `requirements.md` in this folder. Every line reference below was read at `main` `dcdbd65d`.
The local ffmpeg is 8.1.1; CI runs Ubuntu's 6.1.1 and a pinned 8.1 build (`.github/workflows/gates.yml`).

Labels: [tested] measured or run for this spec, with what it ran on; [verified] read at the named source;
[inference]; [proposed]; [open]; [decided 2026-11-11] a ruling by the lead on this spec, with its reason
(`requirements.md`, "Rulings by the lead"). The experiments behind every [tested] line were prototypes run on
2026-11-11 from the lane's ignored tool folder (`.lanetools/restore-0/`), one process at a time, on crops. They
are evidence for choices, not product code: each unit rebuilds what it needs test first.

## Overview

```
POST /api/media/{sha256}/restore  (kind: display_copy | restoration)        services/media/router.py
  job: background, one at a time, child process, time bound                   services/media/jobs.py
     1  decode by the file's own colour tags -> linear light                  services/media/colour.py, decode.py
     2  confirmed stars and their footprints, on the original's own luma      services/media/stars.py
     3  noise table, per channel and intensity                                services/media/noise.py
     4  clips: register neighbours, merge only where they agree               services/media/temporal.py
     5  spatial noise reduction, footprints kept                              services/media/spatial.py
     6  dissolve points the original does not confirm                         services/media/gate.py
     7  lift, bounded by noise and quantisation                               services/media/tone.py
     8  encode with the original's transfer; clips: frame times, audio copy   services/media/encode.py, clip_io.py
     9  gate, on the stored file decoded back                                 services/media/gate.py
    10  ready: link the variant and its records into place; else remove it    services/media/variants.py
GET variants, records, file, export, manifest                                services/media/router.py
```

A display copy runs steps 1, 8 and 10 only, with an exact comparison in place of the gate.

## Measured facts

What the experiments ran on: the five real phone night photos under `.cache/commons-southern/` (CC BY-SA, three
cameras; photos 1, 2 and 4 are one phone's night mode), as 768 px crops of the upper middle unless said otherwise;
one synthetic 768 px scene with known truth (sky gradient, 80 stars, a foreground with edges and fine texture,
signal dependent noise shared between neighbours); one synthetic clip built like the owner's; and the owner's three
distinct clips, as counts only.

- **M1, colour tags** [tested, five photos]. Four carry an ICC profile: three a 536 byte version 4 profile whose
  three curves are parametric type 3 with the sRGB parameters, one a version 2 profile with 1024 entry tables. One
  has no profile. All five say EXIF ColorSpace 1.
- **M2, noise depends on signal** [tested, five photos, 1024 px crops]. In linear light the fine-scale sigma rises
  1.5 to 10 times across the intensities of one crop, and more than 60 times on a crop that runs from dark ground
  to bright light. In code values it is flat for one camera (2.4 codes at every level) and rises about 2.4 times
  for another. The night mode photos mostly have 0.2 to 0.6 codes of fine noise, under one code step.
- **M3, no two parameter line** [tested, same crops]. A fit of variance = a x intensity + b in linear light misses
  by 20 to 96 percent (relative rms) and gives a negative b in most fits.
- **M4, neighbours share noise** [tested, same crops]. The correlation between neighbouring pixels in the flattest
  tenth of blocks is 0.22 to 0.73.
- **M5, the detector needs its noise calibrated** [tested, crops of photos 3, 4 and 5]. Called as the solve job
  calls it, the detector reports 480 to 806 point sources per crop (0.59 megapixels) and forced photometry confirms
  all but 2. The spread of its matched-filter response is 1.9 to 3.7 times the pixel sigma it divides by. With that
  spread as the noise, tile by tile, it reports 13 to 77 per crop on the five photos.
- **M6, the options table** [tested, synthetic scene and five crops]: section 5.
- **M7, the chosen filter end to end** [tested, synthetic scene and five crops, 16 bit store, variant threshold 6].
  Fine sky noise left: 1 to 12 percent of the original's. Unconfirmed points per crop before the dissolve passes:
  0, 2, 0, 2 and 8; passes needed: 0, 1, 0, 2 and 1; unconfirmed after them: 0 on every crop. On the synthetic
  scene: 51.2 dB against truth in the sky and 39.6 dB in the foreground, where the lift alone gives 38.3 and 38.7.
- **M8, 8 bits contour** [tested, photo 2's crop, no lift]. The same variant showed 50 unconfirmed points stored
  at 8 bits and 1 stored at 16 bits.
- **M9, a clipping lift invents points** [tested, photo 4's crop]. A plain gain of 4 clipped a bright object and
  the detector found 9 points on the flat top that the original did not confirm at those places.
- **M10, painted stars** [tested, five crops, 40 painted per crop, variant threshold 6]. With the detector's own
  statistic, refused of 40 by painted peak (codes of 255): photo 1: 7 at 4, 39 at 9, 40 at 14; photo 2: 25 at 3,
  40 at 6; photo 3: 0 at 9, 16 at 14; photo 4 (lifted 6.3 times): 0 at 14; photo 5: 9 at 6, 20 at 14. With the
  local background at the star scale removed first: photos 1, 2, 3 and 5: 38 to 40 at 6; photo 4: 31 at 9, 39 at
  14. That statistic flagged 11 to 137 points of the honest variants.
- **M11, thresholds** [tested, five crops, 8 bit variants]. Unconfirmed at variant thresholds 4.75, 6 and 8:
  photo 1: 4, 0, 0; photo 3: 0, 0, 0; photo 4: 5, 2, 0; photo 5: 10, 3, 0 (photo 2 is M8).
- **M12, flux** [tested, synthetic scene and five crops, footprints from each star's own profile]. Every confirmed
  star's flux on the variant, lift undone, was within 2.53 empty-aperture sigma of its flux on the original (worst
  per image: 0.57, 1.57, 1.38, 2.53, 1.22 and 2.15; medians 0.16 to 0.57). For stars above 10 sigma the median
  ratio per image was 0.953 to 1.003.
- **M13, cost** [tested, one core, float64 numpy]. Per megapixel: detect and confirm 0.4 to 0.6 s, noise table
  0.02 to 0.05 s, filter 0.8 to 1.2 s. A 16 bit PNG of a 12.2 megapixel photo: 46 MB at zlib level 6 in 4.2 s, 48
  MB at level 1 in 0.9 s; the same picture at 8 bits is 5 MB.
- **M14, clip input and output** [tested, ffmpeg 8.1.1, a synthetic clip: 8 bit HEVC tagged BT.2020 and HLG, time
  base 1/600, frame gaps of 17 and 22 ticks, video starting 0.27 s after the audio, AAC stereo]. Asking for the
  stream's own pixel format returned the decoder's own samples (150 of 150 frame MD5s equal ffmpeg's `framemd5`,
  no scale filter inserted). `showinfo` gave every frame's integer time. Frames piped back and stamped through
  `settb` and `setpts` came out with exactly the source's 150 frame times; the copied audio had the source's packet
  hash and all 259 packet times; `setparams` carried the four colour tags into the H.264 stream. A display rotation
  given for the piped input did not reach the output; a second pass that copies the streams with
  `-display_rotation` on the file did, with the video packets unchanged.
- **M15, the owner's clips** [tested, counts only]. Six files, three distinct clips. All three: QuickTime, HEVC
  Main, `yuv420p` (8 bits), limited range, matrix BT.2020 non-constant, transfer HLG, primaries BT.2020, time base
  1/600, 2 to 8 distinct frame gaps, one AAC stereo stream, no other stream, no display rotation; 2.1 to 2.7
  megapixels; 300 to 900 frames.
- **M16, the owner's frames** [tested, 16 consecutive frames from each clip, one megapixel crops, numbers only].
  Luma median 17 to 26 of 255, where video black is 16. In two clips one code holds 48 and 95 percent of the
  frame and fine detail is exactly zero at 83 and 98 percent of pixels; 37 to 96 percent of pixels repeat exactly
  from one frame to the next. The scene moves 6 to 19 px per frame. Whole-frame shifts add up to within 0.07 and
  0.13 px on two clips and miss by 4 px on the third. On the one clip with measurable noise, an average of 8
  registered frames leaves 52 percent of the fine noise, where independent frames would leave 35. One frame gives
  97 to 655 confirmed points per crop, and the 8 frame average shows 90 to 415 points that the single frame does
  not confirm.
- **M17, Pillow and 16 bits** [tested, Pillow 13.0.0]. It opens a 16 bit RGB PNG as 8 bit RGB and cannot write a
  16 bit RGB array. A PNG written with numpy and zlib is read back exactly by ffmpeg as `rgb48le`.
- **M18, pictures** [tested, looked at]. On a sensor-noisy photo the chosen filter gave the cleanest sky with the
  stars kept as points. On a night mode photo lifted 16 times, a variant of the filter with a fixed edge width
  turned the phone's own blotches into flat facets; the chosen rule (edge width measured on the guide) did not.

## Components

### 1. Colour decode and encode (`services/media/colour.py`, `services/media/decode.py`)

Pure functions in `colour.py`; file reading in `decode.py`. Output of a decode: the stored samples as integers
(for the detector and the exact checks), linear light as float32 `H x W x 3`, and a `ColourDescription` (section
14) with its assumptions.

**Stills at 8 bits (JPEG, PNG, TIFF)**: Pillow, as the solve job does (`services/ingest/jobs.py:321-323`), mode RGB.
A grey image is read as three equal channels, and an image with transparency is refused [proposed].
**Stills at 16 bits (PNG, TIFF)**: ffmpeg to `rgb48le` raw, because Pillow drops 16 bit RGB to 8 (M17).
**HEIC**: the tile path of the solve job (`jobs.py:241-308`), with each tile decoded in its own pixel format
instead of grey, placed plane by plane on the grid, then cropped to the stored size. It needs ffprobe 7.1 or later
for the tile grid; on an older ffmpeg a tiled HEIC fails with the solve job's own sentence (`jobs.py:208-211`).
**Clips**: section 10.

Transfer of a still, in this order: the ICC profile's `rTRC`, `gTRC` and `bTRC` curves when the file has a profile
(for a HEIC: the `colr` property of the primary item, which `read_heif` in `services/ingest/metadata.py` must
return; that file is outside `services/media/`, and `tasks.md` names it for restore-2); else EXIF ColorSpace 1 as
sRGB;
else sRGB as a recorded assumption (`field: transfer`, reason "the file has no ICC profile and no EXIF
ColorSpace").
- ICC parametric curves, types 0 to 4, parameters as s15Fixed16 in the order g, a, b, c, d, e, f [verified:
  ICC.1:2022 clause 10.18, Tables 67 and 68]: type 0 `Y = X^g`; type 1 `Y = (aX + b)^g` for `X >= -b/a`, else 0;
  type 2 `Y = (aX + b)^g + c` for `X >= -b/a`, else c; type 3 `Y = (aX + b)^g` for `X >= d`, else `Y = cX`; type 4
  `Y = (aX + b)^g + e` for `X >= d`, else `Y = cX + f`. Domain and range 0 to 1.
- ICC table curves (`curv`): no entry is the identity, one entry is a plain gamma, more entries are a table read
  by linear interpolation [inference: ICC.1:2022 clause 10.6, not re-read; the unit reads it first]. A table is
  inverted by interpolation, after a check that it is strictly increasing; one that is not fails the job.
- The profile's bytes are carried into the variant unchanged. Primaries are never converted: the pipeline works in
  the file's own primaries, so no gamut step can add or remove colour.

Transfer functions, code value `V` and linear light `L`, both 0 to 1:
- sRGB: `L = V / 12.92` for `V <= 0.04045`, else `L = ((V + 0.055) / 1.055)^2.4`; `V = 12.92 L` for
  `L <= 0.0031308`, else `V = 1.055 L^(1/2.4) - 0.055` [verified: the two segment form in Rec. ITU-T H.273
  (07/2024) Table 3, value 13; the constants in the W3C CSS Color 4 sample code, read through a fetch summary].
- BT.709 (H.273 values 1, 6, 14, 15): `V = 1.099 L^0.45 - 0.099` for `L >= 0.018`, else `V = 4.500 L` [verified:
  Rec. ITU-R BT.709-6 (06/2015) item 1.2]. The inverse: `L = V / 4.5` for `V < 0.081`, else
  `L = ((V + 0.099) / 1.099)^(1/0.45)`.
- HLG (H.273 value 18): `V = sqrt(3) L^0.5` for `L <= 1/12`, else `V = a ln(12 L - b) + c`, with
  `a = 0.17883277`, `b = 0.28466892`, `c = 0.55991073` [verified: H.273 Table 3]. The inverse: `L = V^2 / 3` for
  `V <= 0.5`, else `L = (exp((V - c) / a) + b) / 12`. This is scene light; no display rendering is applied.
- PQ (H.273 value 16): `V = ((c1 + c2 L^n) / (1 + c3 L^n))^m`, with `c1 = 107/128`, `c2 = 2413/128`,
  `c3 = 2392/128`, `m = 2523/32`, `n = 1305/8192`, and `L = 1` standing for 10 000 candelas per square metre
  [verified: H.273 Table 3]. The inverse: `p = V^(1/m)`, `L = (max(p - c1, 0) / (c2 - c3 p))^(1/n)`.
- Below 0 and above 1 (limited range footroom and headroom, chroma noise at black): each function is continued as
  an odd function below 0 and by its end slope above 1 [proposed: clipping at black would raise the dark level and
  break the exact round trip; H.273 does the same for value 13 with a matrix].

Y'CbCr to R'G'B', for `n` bit samples `D`:
- Limited range: `Y' = (D_Y / 2^(n-8) - 16) / 219`, `Cb = (D_Cb / 2^(n-8) - 128) / 224`, the same for Cr
  [verified: BT.709-6 item 3.4; H.273 equations 30 to 32]. Full range: `Y' = D_Y / (2^n - 1)`,
  `Cb = (D_Cb - 2^(n-1)) / (2^n - 1)` [verified: H.273 equations 36 to 38].
- `R' = Y' + 2 (1 - K_R) Cr`, `B' = Y' + 2 (1 - K_B) Cb`, `G' = (Y' - K_R R' - K_B B') / (1 - K_R - K_B)`
  [verified: H.273 equations 45 to 47, solved for R', G', B'], with `K_R` and `K_B`: BT.709 0.2126 and 0.0722;
  BT.601 0.299 and 0.114; BT.2020 non-constant luminance 0.2627 and 0.0593 [verified: H.273 Table 4, values 1, 5
  and 6, 9; the BT.2020 divisors 1.8814 and 1.4746 in Rec. ITU-R BT.2020-2 (10/2015) Table 4 are the same
  numbers].
- Chroma at 4:2:0 and 4:2:2 is brought to full size by linear interpolation at the stream's own siting (ffprobe's
  `chroma_location`) [proposed: the simplest interpolation that honours the siting and has no negative weight].
  BT.2020 sites chroma on the top left luma sample and BT.709 on alternate luma samples of the line [verified:
  BT.2020-2 Table 5; BT.709-6 item 4.3]. An untagged siting is assumed `left` and recorded [inference: the common
  default of H.264 and HEVC streams; the unit checks it at the codec standard before coding].
- Going back, chroma is averaged down with the transpose of the same weights, values are rounded to the nearest
  code and kept inside the range's legal codes.
- An untagged range is assumed limited for Y'CbCr video and full for a JPEG or a HEIC still; an untagged matrix is
  assumed BT.709 for frames 720 lines or taller and BT.601 below, and BT.601 for a JPEG or HEIC still
  [inference: common practice, not a standard's rule]. Each assumption is recorded. An untagged transfer in a clip
  is assumed BT.709 and recorded.

Refused, each with a plain reason naming the format: more than 10 bits in a clip, an alpha plane, a Bayer or other
raw layout, a constant luminance or ICtCp matrix, any transfer not listed above, an interlaced clip.

Encode is the same chain backwards with the original's own description, so decode then encode with nothing between
returns RGB stills exactly and Y'CbCr within the tolerances of P-RST-6.

### 2. What the job reuses from the solve job

- The store and the asset record: width, height, frame rate, `audioStreams` with each stream's packet hash,
  `displayOrientation` (`services/ingest/router.py:95-141`, `services/ingest/probe.py:178-208`).
- The HEIC tile grid reader (`services/ingest/jobs.py:224-266`), imported, not copied.
- `BoundedWorker` (`services/science/bounded_worker.py`) for the time bound, and the atomic JSON writer
  (`services/ingest/jobs.py:82-96`).
- The solution id, read from `solution.json` when it exists, for the manifest.
- Not reused: the solve job's detections. It calls the detector without the tile calibration (M5) and, for a clip,
  on a stack of 8 frames (`jobs.py:521-523`), while restoration needs every frame's own points.

### 3. Confirmed stars and footprints (`services/media/stars.py`)

Input: the original frame's own luma codes. For a Y'CbCr source that is the luma plane as decoded; for an RGB
still it is `_to_luma_2d` of the RGB codes, as the solve job feeds the detector (`jobs.py:322-323`).

1. Tiles of 256 px with 32 px overlap [proposed: 256 keeps the detector's background window, about 28 px, an
   order of magnitude inside a tile; 32 covers the detector's 4 px border, its 7 px peak window and the match
   radius].
2. Per tile, the matched noise: `sigma_m = 1.4826 x MAD` of the detector's own matched-filter response (its
   kernel `_build_gaussian_psf_kernel(1.15, 3)` on the luma minus the background of
   `_estimate_background_and_noise`). The factor 1.4826 turns a median absolute deviation into a sigma for a
   normal distribution.
3. `detect_point_sources(tile, snr_threshold=4.75, noise_floor=sigma_m)`, keeping detections with `snr >= 4.75`,
   then `verify_candidates_on_original_pixels(tile, candidates, noise_floor=sigma_m)`. A star is a candidate with
   a verified detection within 2.5 px. 4.75 is the detector's own floor (`services/science/detector.py:316`,
   `390`). Duplicates from overlapping tiles within 1.5 px are merged, keeping the higher `snr` [proposed: under
   the detector's own 7 px spacing between peaks].
4. Clipped regions: samples at or above the nominal peak code of the original's range, grown by 3 px [proposed].
   Points inside them are left out everywhere and counted (REQ-RST-6.8, M9).
5. Footprint of a star: the smallest whole radius `r >= 1` at which the median of the background-subtracted luma
   over the ring from `r - 0.5` to `r + 0.5` is no more than one sigma of the tile, at most 12 px [proposed cap;
   on one of five crops the median star reached it]. The weight is 1 inside the radius with a 2 px cosine edge;
   overlapping footprints take the larger weight. Measured radii: medians of 3 to 12 px per crop (M12).

The two detector helpers are private names of `services/science/detector.py`. `services/media/runtime.py:30-36`
imports private names from the same lane today. restore-3 imports the two helpers for now; the lane that owns the
detector will publish one shared function for the matched-filter noise, and restore switches to it when it lands
[decided 2026-11-11: the same measure is needed to stop the detection flood in the solver (M5)].

The statistic of steps 2 and 3 is the one restore-4 chooses for the gate (section 7): the stars, the points of the
dissolve passes and the points of the gate are all found with the same one (REQ-RST-6.7). The steps above describe
it with the detector's own statistic, which is where restore-3 starts.

For a clip frame made with neighbours, the points confirmed on its original frames together (section 7) are stars
of that frame as well, with footprints by the same rule measured on those frames together.

Output: the stars (place, `snr`, radius), the footprint map `M` in 0 to 1, the clipped map, and the counts found
and confirmed.

### 4. The noise table (`services/media/noise.py`)

Input: the linear frame, the footprint map, the clipped map. For a clip: 8 frames spread evenly over the clip by
index, `n_k = round((k + 0.5) N / 8)` [proposed: 8 is the solve job's own frame budget, `jobs.py:44`], their
blocks pooled.

Per channel:
1. `w1 = plane - B3(plane)`, the first scale of the undecimated wavelet transform with the separable kernel
   `[1, 4, 6, 4, 1] / 16` [inference: the starlet transform of Starck and Murtagh, named from memory]. For white
   noise of sigma 1 its sigma is `sqrt(1 - 2 (6/16)^2 + (70/256)^2) = 0.891` [tested: the arithmetic of the
   kernel].
2. Blocks of 8 by 8 [proposed: the transform block of JPEG and of most video frames; smaller blocks hold too few
   samples for a variance, larger ones are rarely flat]. A block touching a footprint or a clipped region is left
   out. Per block: its mean of `B3(plane)` and its energy, the mean of `w1^2`.
3. Twelve intensity bins of equal block count [proposed]; a bin with fewer than 20 blocks is dropped [proposed].
4. Per bin: the 25th percentile of the block energies, times the factor that turns that percentile into the mean
   for white normal noise through this same estimator. The factor is computed once by a seeded simulation in a
   test and kept as a named constant with that test as its source. The lowest quarter follows the flattest blocks,
   so texture in the others does not raise the estimate [inference: the lower envelope idea of Liu, Szeliski,
   Kang, Zitnick and Freeman 2008, named from memory; the paper could not be read].
5. A least-squares fit that never decreases with intensity (pool adjacent violators), weighted by block counts
   [proposed: light noise does not fall as light rises, and one clipped or perfectly flat bin must not pull its
   neighbours down].
6. A floor at the rounding noise: one code step through the transfer's slope at that intensity, divided by
   `sqrt(12)`, times 0.891.

It cannot be measured, and says which, when: fewer than 3 bins survive (`too_few_flat_blocks`); more than half of
a channel's `w1` samples outside footprints are exactly zero (`fine_detail_mostly_zero`; M16 shows 83 and 98
percent on two of the owner's clips); or a side is under 64 px (`frame_too_small`) [proposed: 64 blocks at
least]. Then steps 4 and 5 of the pipeline are skipped and the lift is bounded by quantisation alone
(REQ-RST-4.3).

A two parameter line (variance = a x intensity + b) was tried and dropped: it misses by 20 to 96 percent on the
five photos (M3).

The stabilised luma used by the filter's guide: `t(I) = integral from 0 to I of du / sigma(u)`, with `sigma` the
table of the luma (sigma of `w1` divided by 0.891), by the trapezoid rule on 4096 points [proposed: far finer
than the table's 12 bins], sigma read by linear interpolation between bins and held at the end values outside
them. After it, one unit is one noise sigma at any intensity.

### 5. Spatial noise reduction (`services/media/spatial.py`)

**Options compared** (M6) [tested: one synthetic scene with known truth and the five crops; every option kept
confirmed stars under footprints, was lifted by the same curve (gain 4 at black), stored at 16 bits and gated at
variant threshold 6 with the lift undone. A to E used one footprint rule, a fixed width from the detector's size
estimate; F is shown with that rule and with section 3's. A, B and C ran on code values, D, E and F in linear
light. Strengths are sensible defaults, not tuned optima.]

| Option | Fine sky noise left, five crops | PSNR, sky and foreground (dB), synthetic | Unconfirmed points, five crops | Seconds per megapixel | Risk of inventing structure |
|---|---|---|---|---|---|
| Lift only | 100 percent | 38.3 and 38.7 | not run | 0 | none |
| A. Gaussian blur, sigma 2 (today's method) | 4 to 24 percent | 46.8 and 38.8 | 0, 0, 0, 0, 0 | 0.13 | none: linear; it blurs every edge |
| B. Bilateral filter | 18 to 63 percent | 45.5 and 41.2 | 0, 0, 0, 0, 1 | 0.9 to 1.0 | low: can flatten soft gradients into steps and leaves lone outliers |
| C. Guided filter, self guided | 26 to 69 percent | 45.9 and 39.9 | 0, 0, 2, 0, 1 | 0.17 to 0.19 | low: halos beside strong edges |
| D. Wavelet scales, gain from local power | 11 to 23 percent | 47.4 and 31.7 | 19, 21, 114, 6, 35 | 0.6 | high: took the textured foreground for noise and left hundreds of points |
| E. Wavelet scales, keep above 3 sigma | 5 percent or less | 48.0 and 30.3 | 202, 60, 1210, 142, 108 | 0.4 to 0.5 | highest: every survivor of the threshold is a lone point |
| F. Edge-avoiding a trous filter (chosen) | 1 to 12 percent | 51.2 and 39.6 | 0, 1, 0, 2, 4 with the others' footprints; 0, 2, 0, 2, 8 with section 3's; 0 after the dissolve passes | 0.8 to 1.2 | low: an average with positive weights; an edge the guide sees stops it |

Sources of the methods: A is the current code (`services/media/runtime.py:261`). B: Tomasi and Manduchi 1998. C:
He, Sun and Tang 2013; the same local rule as `scipy.signal.wiener`. D and E: wavelet shrinkage on the starlet
transform, E being the k-sigma rule common in astronomy. F: Dammertz, Sewtz, Hanika and Lensch 2010,
"Edge-Avoiding A-Trous Wavelet Transform for fast Global Illumination Filtering" [all inference: named from memory;
the papers that were fetched could not be read. Each unit reads the paper it builds on first.] All six need only
numpy and scipy.

**Why F** [proposed]. It gives the cleanest sky by a wide margin (51.2 dB against 45.5 to 48.0), it keeps an edge
the guide can see, every output sample is an average of original samples with positive weights (REQ-RST-5.2), and
the few points it leaves the dissolve pass removes. It loses to the bilateral filter on fine foreground texture
(39.6 against 41.2 dB): texture weaker than the edge width is smoothed like noise. If the lead's before and after
pictures show foreground loss that matters, B is the fallback with the same interface.

**The filter.** Inputs: the linear frame `x` (three channels), the stabilised luma `g_0 = t(luma)` (section 4), the
footprint map `M`.

```
cur = x
for level i = 0 .. L-1, with step s = 2^i:
    g_(i+1) = B3_s(g_i)                    # the B3 kernel with s-1 zeros between its taps
    d_i = 1.4826 x MAD over the frame of g_(i+1)(p + (s, 0)) - g_(i+1)(p)     # the guide's own noise at this lag
    for each of the 25 taps (a, b), a and b in -2 .. 2, with kernel weight h_a h_b:
        q = p + s (a, b)
        w(p, q) = h_a h_b x exp(-(g_(i+1)(q) - g_(i+1)(p))^2 / (2 (k_r d_i)^2)) x (1 - M(q))
    smooth(p) = sum_q w(p, q) cur(q) / sum_q w(p, q)        # cur(p) where the sum of weights is under 1e-6
    cur = M x + (1 - M) smooth
```

- The same weights serve the three channels, so colour noise goes with luma noise and no channel is filtered
  alone.
- `(1 - M(q))` leaves star light out of the averages, so a star is neither smoothed nor spread into a halo.
- `L = 3` [proposed]: taps reach 14 px. Measured noise left per scale, after over before, on the five crops:
  scale 1: 0.01 to 0.12; scale 2: 0.08 to 0.34; scale 3: 0.36 to 0.55; scale 4: 0.74 to 0.91. A fourth level would
  reach 30 px, where clouds and sky gradients live.
- `k_r = 2` [proposed]: a difference of one sigma of the guide keeps 88 percent of its weight, a difference of
  six keeps 1 percent.
- `d_i` is measured on the guide at each level, on this frame. A fixed width in pixel-noise units was tried: on a
  night mode photo the phone's own blotches were many pixel sigmas high, were kept as edges and came out as flat
  facets (M18).
- The frame's borders are mirrored. The floor of 1e-6 on the sum of weights only guards a division where every
  tap lies under a footprint [proposed].
- When the noise could not be measured the step is skipped (section 4).

Known limit: the edge-stopping term makes the filter non-linear at the star scale, so a faint compact bump the
guide can see is smoothed less than the noise around it and can become a point the original does not confirm
(M7: 0 to 8 per crop). Section 7 removes them. Plain linear smoothing leaves none (option A) but keeps no edge.

### 6. The lift (`services/media/tone.py`)

`f(x) = asinh(x / beta) / asinh(1 / beta)` on each channel in linear light, the asinh stretch of Lupton, Blanton,
Fekete, Hogg, O'Mullane, Szalay and Wherry 2004, "Preparing Red-Green-Blue (RGB) Images from CCD Data" [verified:
the arXiv abstract, astro-ph/0312483, which introduces an asinh stretch to show faint objects while keeping the
structure of brighter ones]. It is linear near black with slope `g0 = 1 / (beta asinh(1 / beta))`, never clips,
maps 0 to 0 and 1 to 1, and has the exact inverse `x = beta sinh(y asinh(1 / beta))`.

It is applied to each channel alone, not to a luminance with the colour ratios kept as Lupton does [proposed: a
per channel curve is exactly invertible, which the gate needs to undo it; dark colours keep their ratios where
values are far below `beta`; bright colours move toward white].

`beta` is solved per file, by bisection on `log(beta)`, as the largest of these (a larger `beta` is a weaker lift):
1. **Target.** With `exposureEv` in the request: `g0 = 2^exposureEv`. Without it: `f(m) = T`, where `m` is the
   median of the frame's linear luminance (for a clip, of the sampled frames) and `T` is linear light, by the
   file's own transfer, for the code 43/255 of the way from black to peak (0.024 for sRGB) [proposed: 43 is the
   median of the medians of the five real night photos, which are 15, 36, 43, 53 and 56 of 255; tested on five
   photos only]. When `m` is already at or above the target there is no lift.
2. **Noise bound.** With `d` the median luminance of the darkest quarter of the frame, `s_o` the original's fine
   noise there (the noise table) and `s_r` the fine noise measured at the same pixels of the working image after
   noise reduction: `E'(f(d)) x f'(d) x s_r <= E'(d) x s_o`, where `E'` is the slope of the file's transfer encode.
   In words: lifted and encoded, what is left of the noise is no larger in code values than the original's noise
   was.
3. **Quantisation bound.** `E'(f(d)) x f'(d) / E'(d) <= 4`: one code step of the original at the dark level
   becomes at most 4 steps of the same depth [proposed: a bound is needed because a frame recorded at black has
   no noise to bound the lift (M16) and its codec blocks are one step high; 4 is a first value to be set from
   pictures, see ruling 10 in the requirements].
4. **Maximum.** `g0 <= 64`, six stops [proposed].

The record's `decidedBy` names which of the four held, or `none`. On the five crops with the default target the
lift at black came out as 1.0, 1.0, 1.6, 2.6 and 6.3 times, each decided by the target [tested; the prototype
applied the target and the noise bound only]. For an HLG clip at black the quantisation bound works out to a gain
of at most 16 at black, since HLG is a square root there [inference: arithmetic on the formula of section 1].

The target code and the quantisation bound stay [proposed], as do the flux tolerance of section 7 and the encoder
quality of section 10: restore-4 and restore-7 write before and after pictures for the lead and the owner to
judge, and the values are set from those [decided 2026-11-11: these are choices a person has to look at].

### 7. The dissolve passes and the gate (`services/media/gate.py`)

**The statistic** [decided 2026-11-11: the detector's own statistic refused none of 40 painted stars on the most
lifted crop (M10), so it cannot be the gate as it stands]. Unit restore-4 chooses the statistic by measurement.
The first candidate is the detector on the luma with its local background at the star scale removed: the luma
minus its B3 smoothing at steps 1 and 2, that is, the first two wavelet scales only, the same on the variant and on
the original. Measured for that candidate (the numbers are in M10): on four of the five crops it refused nearly
every painted star at a peak of 6 codes; on the most lifted crop it still let some through at the two higher
peaks tried there; and on every crop it flagged points of the honest variants, because the edge-stopping filter
leaves compact structure that statistic sees.
- What it has to reach (REQ-RST-6.7): on every real photo of the corpus, lifted as the job lifts it, a
  star-shaped patch (a Gaussian of sigma 1.3 px) painted into the stored variant with a peak of at least five
  times the variant's pixel noise around it and at least 4 codes of 255 is refused in every trial. The pixel noise
  is 1.4826 x MAD of the stored variant's luma minus its local median, in the patch's tile [proposed, both
  numbers and the measure: at the nearest peaks tried, the candidate at threshold 6 refused 38 and 39 of 40 on two
  of the five crops and 40 of 40 on the other three, so the unit has a threshold or a statistic to find, or a
  value to restate with its numbers].
- Honest points the stricter statistic flags are dissolved by the passes below, so an honest run still ends
  `ready`. The unit reports, per real photo: the painted stars refused, the honest points flagged and dissolved,
  and what the dissolving costs in visible detail (the before and after pictures and the fine sky noise left).
- If no statistic meets both on the real photos, the unit says "unsolved on real data" with the numbers, and the
  job keeps answering that restorations are not built: none is served.
- The attempt records which statistic ran (`statistic` in the gate result, section 14). A statistic that is
  neither of the two listed there is a contract change and lands first as its own unit.

**Finding the variant's points.** The image is brought to the original's code scale: for the gate, the stored
file decoded (section 1) with its lift undone and encoded with the original's transfer as unrounded codes; for a
dissolve pass, the working image encoded the same way. Then the luma as in section 3, the chosen statistic, the
same tiles, each tile's `sigma_m` measured on this image, `detect_point_sources(tile, snr_threshold=6,
noise_floor=sigma_m)` keeping `snr >= 6`, and clipped regions left out.

**Confirming.** `verify_candidates_on_original_pixels(original tile, points, noise_floor=the original tile's
sigma_m)`. A point is confirmed when a verified detection lies within 2.5 px [proposed: the function looks 2 px
around a candidate; half a pixel more for the centroid]. The function's own floor of 4.75 applies.

Why 6 against 4.75 [proposed]: with the detector's own statistic, at equal thresholds three of five crops showed 4
to 10 unconfirmed points from chance disagreement near the threshold; at 6 they showed 0 to 3, and at 8 none
(M11). These are the starting values for the statistic restore-4 chooses.

**Confirming a clip's point on its frames together** [decided 2026-11-11: a real star too faint for one frame is
in the original pixels of many frames, so showing it paints nothing]. For frame `k` made with help from
registered neighbours:
- The frames together, `J_k`: the plain mean of the original luma of frame `k` and of each neighbour the merge
  used for it, each moved by the same shift and the same linear interpolation as in section 10, with no agreement
  weight and no filter. A block where a neighbour failed registration is left out of the mean there. `J_k` is made
  of original pixels only.
- A point of the variant frame is confirmed when `verify_candidates_on_original_pixels` confirms it on frame
  `k` alone, as for a still, or on `J_k` with `J_k`'s own tile noise, at the same confirmation threshold.
- Guard 1, nothing from the neighbours alone: a point confirmed only on `J_k` must agree with each original
  frame that went into `J_k`, frame `k` first. Its aperture flux on each of those frames, registered, must lie
  within 3 empty-aperture sigma of that frame of its flux on `J_k` [proposed: the flux rule of the gate, turned on
  each frame]. A bright light that the neighbours carry and frame `k` does not fails this on frame `k`. A flash
  that one neighbour carries fails it on that neighbour, although its share in the mean is small [inference,
  arithmetic with one noise unit for both measures: to be confirmed on a mean of seven it needs about 12.6 times
  one frame's noise, and it then stands about 10.8 of that noise above its flux on the mean]. Such a point is not
  confirmed, and the merge's own weights keep it out of the frame (section 10). What no single frame can
  contradict is a light too faint for one frame to tell from its noise, carried by most of the window: that is
  the star this rule exists for. The cost: one frame's own noise can break the agreement by chance, for a little
  under one real faint star in a hundred [inference: arithmetic for seven frames of equal noise]; such a star is
  dissolved, never painted. Test: P-RST-14.
- Guard 2, nothing fixed to the sensor. `U_k` is the plain mean of the same original frames with no shift. A star
  moves with the scene: its light adds up on `J_k` and is spread along the motion on `U_k`. A hot pixel or a codec
  block stays on its sensor pixel: it adds up on `U_k` and is spread on `J_k`. So where the registration says the
  scene moved more than 5 px across the window (the largest distance between the shifts of two of its frames)
  [proposed: twice the match radius, so that a point spread along the motion loses a clear part of its response],
  a point whose matched-filter response (section 3, step 2; the largest value within 1 px of its place) is larger
  on `U_k` than on `J_k` by more than 3 `sigma_m` of its tile on `J_k` [proposed] is fixed to the sensor and is
  not a confirmed star, whichever image confirmed it. It gets no footprint, and it is dissolved like any
  unconfirmed point. A star's response is the larger on `J_k`, so the guard leaves stars alone, bright or faint;
  P-RST-24 tests both sides. Where the scene moved less, this guard cannot tell and does not apply. Test:
  P-RST-24.
- A point the frames together do not confirm is dissolved; if it remains in the stored file, the gate refuses it.
- Flux (below) for a star confirmed only on `J_k` is compared with its flux on `J_k`, against the noise of empty
  apertures on `J_k`.
- For a still, and for a clip frame whose neighbours all failed registration, nothing changes: the one original
  frame decides.
Measured on the owner's clips with the detector's own statistic (M16): an average of 8 registered frames showed
90 to 415 points per crop that the single frame does not confirm. How many of those the frames together confirm
and how many guard 2 refuses is for restore-7 to report.

**Dissolve passes** (pipeline, before the lift and the encode). For each unconfirmed point of the working image:
its footprint by the rule of section 3 measured on the working image, at least 3 px and one pixel wider on each
later pass, and inside it the working image is replaced by `flat`, the same B3 cascade of the filter's input (the
original for a still, the merged frame for a clip) with every weight 1 and no footprints (plain linear smoothing,
computed once when first needed). At most three passes. Each pass's count goes into the record. Measured with the
detector's own statistic: passes needed 0, 1, 0, 2 and 1 on the five crops (M7).

This erases compact light that the original pixels do not confirm: for a still its one frame, for a clip frame
made with neighbours its frames together.

**The gate** (on the stored file). `rejected` with `unconfirmed_points` when any point is unconfirmed, in any
frame. Then flux: for every confirmed star of section 3 whose footprint touches neither the border nor a clipped
region, the aperture sum of the linear luminance minus the median of an annulus, aperture radius the star's
footprint radius (at least 2 px), annulus from 3 to 7 px beyond it [proposed]. `F_o` on the original, `F_v` on the
variant with the lift undone. `sigma_F`: 1.4826 x MAD of the same aperture sum at 60 empty places of the original,
the first 60 points of a fixed lattice that are 12 px clear of every footprint and clipped region (no random
draw) [proposed: 60 and 12 are the prototype's values]. `rejected` with `star_flux_changed` when any star has
`|F_v - F_o| > 3 sigma_F` [proposed: measured worst
2.53 on six images, medians 0.16 to 0.57 (M12); 3 leaves a margin and is to be re-measured on whole photos].

**What the detector's own statistic can see** [tested, M10]. It refuses a painted star when its matched response,
lift undone, reaches the variant threshold times the tile's `sigma_m`. For a star-shaped patch of sigma 1.3 px
that is a peak of about `6 sigma_m / 2.29` (2.29 is the kernel's response to that shape, by arithmetic). Where the
variant's leftover noise is blotchy and lifted, `sigma_m` is large and a visible painted star passes: none of 40
at 14 codes on the most lifted crop. That is why the statistic is to be chosen by restore-4 (above).

**The painted star test** (REQ-RST-6.7, P-RST-11). On the stored honest variant of a seeded synthetic scene, and
of every real photo of the corpus lifted as the job lifts it: add a Gaussian patch (sigma 1.3 px) to the stored
samples at a place 12 px clear of every footprint and clipped region, with a peak at the stated sensitivity, write
it back, run the gate: `rejected`, unconfirmed count at least 1, the patch's place among them, in every trial (40
places per photo, on a fixed lattice). And at half that peak the test records, without asserting, how many were
refused, so the limit stays visible.

### 8. Encoding and storing a still (`services/media/encode.py`, `services/media/png16.py`)

- A restoration of a still is a PNG with 16 bits per channel, and the gate runs on that stored file [decided
  2026-11-11: what is served is the file the gate ran on; M8 is why 16 bits]. Pillow cannot write one (M17), so
  `png16.py` writes it with numpy and zlib: `IHDR` (depth 16, colour type 2, no interlace), an `iCCP` chunk with
  the original's profile when it has one, one `IDAT` with every row filtered by `Up`, `IEND`; zlib level 6
  [proposed: 46 MB in 4.2 s for 12 megapixels, against 48 MB in 0.9 s at level 1 (M13)]. No time stamp and no
  text chunk, so two runs write the same bytes. It is read back through ffmpeg as `rgb48le` (M17).
- The size is the cost: 46 MB where an 8 bit PNG is 5 MB. A smaller 8 bit rendition for a phone on a slow link is
  not part of this spec [open]; if one is ever served, it is gated as its own file (REQ-RST-6.13).
- A display copy of an 8 bit original is an 8 bit PNG written by Pillow with the profile; of a deeper one, a 16
  bit PNG by `png16.py`.
- Display orientation is not baked in and not written into the PNG: the variant record carries the original's
  `displayOrientation` and the web applies it as it does for an original.

### 9. Storage and serving (`services/media/variants.py`, `services/media/router.py`)

```
<ingest root>/<aa>/<sha256>/                 0700   (exists)
  original.<ext>                             0400   (exists, never touched)
  record.json, job.json, solution.json              (exist)
  variants/                                  0700
    <variantId>/                             0700
      variant.<png|mp4|mov>                  0400
      variant.json                           0400   MediaVariant
      graph.json                             0400   ProcessingGraph
      attempt.json                           0400   ProcessingAttempt, status ready
  attempts/                                  0700
    <attemptId>.json                         0400   every finished attempt: ready, rejected or failed
  restore-jobs/                              0700
    <jobId>.json                             0600 while it runs, 0400 once finished
```

- Files are written under the store root's `.partial/` (`services/ingest/store.py:190-193`), hashed while
  written, then linked into place with `os.link`, which fails when the name exists, so nothing is overwritten;
  folders through the store's own `_private_folder` rule (`store.py:256-265`). The store's sweep of old partial
  files already covers a crashed job (`store.py:151-166`).
- Ids: `graph_<h>` and `var_<h>` with `h` the first 16 hex digits of the SHA-256 of the canonical JSON of
  `{sourceSha256, kind, pipelineVersion, request}`; `att_<h2>` and `rjob_<h2>` with `h2` from the graph id and the
  attempt's start time. `pipelineVersion` is one constant in `services/media/`, raised by any unit that changes
  pixels.
- `GET /api/media/{sha256}/variants`: `{variants: [MediaVariant], attempts: [ProcessingAttempt]}`.
  `GET .../variants/{variantId}`: `{variant, graph, attempt}`. `GET .../variants/{variantId}/file`: the bytes with
  `Cache-Control: private, max-age=31536000, immutable`, `X-Content-Type-Options: nosniff`,
  `Content-Disposition: inline`, as the original is served (`services/ingest/router.py:226-234`), and byte
  ranges for clips. A hash, variant id or job id that does not match its pattern is 422; an unknown one 404.
- There is no listing across uploads: a variant is reached only through its original's full hash, as an original
  is.
- `tests/fixtures/api_surface.json` gains the new routes and loses the old one in the units that add and remove
  them.

### 10. Clips (`services/media/clip_io.py`, `services/media/temporal.py`)

Every ffmpeg feature named here is used on both CI builds by a test of the unit; where the older build lacks one,
the job fails with a plain capability sentence, as a tiled HEIC does today. Already in code that the tests of
`main` run on both builds [verified: `services/media/discovery.py:232-250`, `services/media/runtime.py:112-126`
and `497-509`, `services/ingest/probe.py:195-196`]: `-noautorotate`, `showinfo`, `-fps_mode passthrough`,
`libx264`, `-c:a copy`, the `hash` and `framemd5` muxers. Run here on 8.1.1 only (M14), and on 6.1.1 [inference:
all are older than 6.1 as remembered from the release notes, not run]: `settb`, `setpts`, `setparams`,
`-enc_time_base` with a rational, `-copyts`, `-map 1:a?`, `-display_rotation`. `libx265` is in no test today.

**Probe.** `ffprobe -show_streams -count_packets` on the original: pixel format, range, matrix, transfer,
primaries, chroma siting, time base, and the number of video packets, which is the denominator of the `decode`
stage. More than one video stream, or an interlaced one, is refused.

**Decode, streamed.** One process:
`ffmpeg -v info -nostdin -noautorotate -i <original> -map 0:v:0 -vf showinfo -fps_mode passthrough -f rawvideo
-pix_fmt <the stream's own> pipe:1`. The job reads exactly one frame's bytes at a time from the pipe, while a
thread reads the log and takes each frame's integer `pts` from its `showinfo` line. Frame `k` of the pipe gets the
`k`-th time. The job fails with "The frame times could not be kept." when the counts differ, a time is missing, or
times do not strictly increase. Tested on ffmpeg 8.1.1 (M14); `-noautorotate`, `showinfo` and `-fps_mode
passthrough` are already used on both CI builds (`services/media/discovery.py:232-250`,
`services/media/runtime.py:508-509`).

**Registration** (per frame `k` and neighbour `j`, `|j - k| <= 3`) [proposed: three frames either side is a fifth
of a second at 30 frames per second; the scene moves 6 to 19 px per frame in the owner's clips (M16), so a longer
window would leave little overlap].
1. A whole-frame shift from the phase correlation of the two frames' mid-scale luma detail (wavelet scales 2 and
   3), with a Hann window and a parabola fit for the sub-pixel part. Shifts between frames further apart are sums
   of neighbouring shifts.
2. Quality, both measured: the closure error, `|s(k, k+2) - s(k, k+1) - s(k+1, k+2)|`, must be at most 0.5 px
   [proposed]; and the residual shift of each 128 px block, measured again by phase correlation after the
   whole-frame shift, must be at most 0.5 px for that block to be used [proposed: half a pixel is where a star of
   two or three pixels starts to smear; 128 px is a first block size, large enough to hold structure in a dark
   frame]. A neighbour that fails closure is not used for that frame; a block that
   fails is not used from that neighbour. Measured closure on the owner's clips: 0.07, 0.13 and 4.09 px (M16), so
   one of three would get no temporal step at the frames tested.
3. The neighbour is moved by `scipy.ndimage.shift` with linear interpolation, in linear light [linear: its
   weights are never negative, so REQ-RST-5.2 holds; cubic has negative lobes].
A whole-frame shift is exact only for a small turn of the camera. It is the first version; see section 18.

**Merge, only where frames agree.** For frame `F` and each registered neighbour `N_j`, per pixel:
`D2 = mean over a 5 by 5 window [proposed] and the three channels of (N_j - F)^2`, `v = 2 sigma^2` with `sigma` from the noise
table at the pixel's own smoothed intensity, `e = max(D2 - v, 0)`, and the weight `a_j = v / (e + v)`. Then
`out = (F + sum_j a_j N_j) / (1 + sum_j a_j)`.
- Where the two agree within the noise, `a_j` is 1; where they differ by four noise sigmas it is 0.11; by six,
  0.05; it is below one tenth beyond 4.3 sigma, the number P-RST-22 tests. This is the pairwise rule of the HDR+
  burst merge, written per pixel, with the measured difference reduced by its expected noise first [inference:
  Hasinoff and others 2016, named from memory; the paper could not be read. Reducing by the noise and the tuning
  factor 1 are [proposed]].
- `a_j` is set to 0 in a block that failed registration, and at every sample that is a codec copy: equal in all
  three planes to the previous original frame at the same place while the shift between those two frames is over
  half a pixel. Measured share of exact repeats on the owner's clips: 37 to 96 percent (M16).
- The frame's own weight is 1 and no `a_j` exceeds 1, so the frame's own sample always weighs most (REQ-RST-7.4).
- When the noise could not be measured there is no merge.

**Per frame, after the merge:** the stars of that frame, confirmed on the original frame alone or on its original
frames together (sections 3 and 7), the spatial filter (section 5), the dissolve passes (section 7), then the
clip's one lift (section 6).

**Frames in a pool of worker processes** [decided 2026-11-11: the product may use the laptop's cores at run time;
the limit of two processes in the lane contract is for agents' test runs, not for the app]. The job's child
decodes the clip once, in order, and hands the frames out in runs of consecutive frames, each run with the three
neighbours either side that its window needs [proposed: runs of 8 frames]. Each worker does everything between
decode and encode for its run and returns the finished frames; the child writes them to the one encoder in frame
order. The pool has a stated default size, half the machine's cores and at least one [proposed], and is bounded:
no more runs are handed out than there are workers, so memory does not grow with the clip. The result must not
depend on the pool's size: a test runs one clip with one worker and with two and compares the stored files'
hashes. Two, because agents' test runs stay inside the lane's limit of two processes; only the timed run on the
owner's clips uses the default pool, and the unit asks the lead for a time to run it [proposed].

**Encode, streamed.** Linear light back to the stream's own Y'CbCr (section 1), written frame by frame to:

```
ffmpeg -y -v error -copyts
  -f rawvideo -pix_fmt <same> -s <W>x<H> -framerate <nominal> -i pipe:0
  -i <original>
  -map 0:v:0 -map 1:a?
  -vf settb=<time base>,setpts='<expression>',setparams=range=..:colorspace=..:color_trc=..:color_primaries=..
  -fps_mode passthrough -enc_time_base <time base> -video_track_timescale <its denominator>
  -c:v libx264 -crf 16 -preset medium -pix_fmt <same>
  -c:a copy -map_metadata -1 <partial file>
```

- Frame times: `setpts` gets an expression in the frame count `N` that returns the source's own integer times: the
  first time, plus `N` times the commonest gap, plus one `gte(N, k)` step for every frame whose gap differs,
  written as a balanced tree of brackets so the evaluator never recurses deeply. 971 characters for 150 frames
  with uneven gaps [tested, M14]. `-enc_time_base` keeps the encoder from rounding times to the nominal rate, and
  `-copyts` keeps the audio's own times, so the offset between video and audio is the source's. An expression over
  100 000 characters fails the job with a plain reason [proposed: one argument is limited to 131072 bytes on
  Linux; inference]; the owner's clips need a few thousand.
- Audio: `-map 1:a? -c:a copy` copies every audio stream. The container is the original's own family (QuickTime
  stays QuickTime, MP4 stays MP4) [proposed: the best chance that every audio codec copies]. Streams that are
  neither video nor audio are not copied, and the record counts them.
- `-map_metadata -1`: no container tag of the original (capture time, place) reaches the variant (REQ-RST-9.4)
  [decided 2026-11-11: an export is made to be shared; the owner can ask for their own metadata later].
- `-crf 16`, preset `medium` [proposed: the x264 default preset; 16 is below the range commonly called visually
  lossless; inference, the FFmpeg wiki page could not be read]. The gate measures what the encode did to star
  flux; if 16 fails the tolerance on real clips the unit lowers it and says so. The value stays [proposed] until
  it is set from restore-7's before and after pictures (section 6).
- A 10 bit clip: `libx265` with `-pix_fmt yuv420p10le`, the colour fields in `-x265-params` as well, `-tag:v
  hvc1`. The job asks `ffmpeg -encoders` first and fails plainly without the encoder.
- Two runs must write the same bytes (REQ-RST-5.4). x264 is expected to give the same stream for the same input,
  settings and thread count [inference]; if the unit's test shows otherwise it pins `-threads`.
- Display orientation: a second pass copies the streams into the final partial file with `-display_rotation`,
  `-display_hflip` or `-display_vflip` on the input [tested, 8.1.1: the matrix is written and the video packets
  are unchanged; the option on the piped input is ignored (M14)]. The angle is the one that makes ingest's own
  reader (`display_matrix_hint`, `services/ingest/metadata.py`) return the original's `displayOrientation`; a test
  tries all eight orientations.

**Checks on the stored clip, before the gate:** the video packet times equal the source's list exactly; each
audio stream's packet hash (`ffmpeg -map 0:<index> -c copy -f hash -hash sha256`, as
`services/ingest/probe.py:195-196` computes it) equals the one stored at ingest and its packet times equal the
source's; the display orientation reads back the same. Any difference is `rejected` with `frame_times_differ` or
`audio_differs`.

**The gate on a clip:** the variant and the original are decoded side by side, and section 7 runs on each pair of
frames. The gate holds the same window of original frames as the merge and registers them again by the same rule,
so the frames together and the unshifted mean of section 7 are rebuilt from original pixels alone, never taken
from the pipeline.

**Memory.** Each worker holds its run, the window's neighbours and a fixed number of working frames, as float32.
For the largest of the owner's clips that is about 32 MB per frame. The child and each worker record their peak
resident size (`resource.getrusage`), and P-RST-17 holds the bound.

**What to expect on the owner's clips** [tested, M16]. Two of three are recorded almost entirely at black, with
nothing measurable to reduce and one code step above black to lift; their honest result is a nearly unchanged clip
and a plain note saying why. The third has structure, and the temporal step gains less than independent frames
would. A unit that cannot show an improvement there says "unsolved on real data".

### 11. The job (`services/media/jobs.py`)

- `POST /api/media/{sha256}/restore` validates the request and computes the ids. When that variant is stored it
  answers 200 with the finished job record that made it. Else it writes a `queued` job record, hands the job to
  the one background worker thread and answers 202 with the record. The thread runs the work through a
  `BoundedWorker` child, as the solve job runs its
  searches (`services/ingest/jobs.py:576-606`): the child gets the store root, the hash and the request, sends
  progress events, and returns records. Pixels never reach the API process; for a clip they pass only between the
  child and its pool of workers (section 10).
- States: `queued`, `running`, `succeeded` (the attempt is `ready`), `rejected` (it ran to the end and a check
  said no), `failed`.
- Stages and their counts (REQ-RST-8.1): `decode` frames of the counted video packets; `measure` frames of the
  sampled frames; `restore` frames; `encode` frames; `write` bytes with a null total until the file is closed;
  `verify` frames. The job record is written when a stage completes and, for a clip, at most once a second while
  it advances [proposed].
- Time bound: `120 s + 6 s x megapixels x frames`, divided for a clip by the number of workers [proposed: three
  times the prototype's measured still rate of about 2 s per megapixel (M13), and a base for starting the child,
  probing and hashing]. That is about 3 minutes for a 12 megapixel photo. It is a bound, not the target.
- Speed targets [decided 2026-11-11 that there are targets and who reports them; the numbers are proposed].
  restore-3 reports the still path's seconds per megapixel on one core (decode, stars, noise table, filter and
  lift) and brings it to at most 0.5 s per megapixel [proposed; the prototype needs 1.2 to 1.9 for the stars, the
  noise table and the filter alone, M13]. restore-7 reports the time for each of the owner's clips against the
  target of a 15 second clip of 2.4 megapixel frames in under 10 minutes on this laptop [proposed]. A unit that
  misses its target says what dominates the time, with the seconds per step.
- At the bound, or on any failure, the partial file is removed and an attempt record with status `failed` is
  stored.
- The original is hashed at the start and at the end (REQ-RST-1.2).
- Fixed failure sentences, the only text a failed job stores:
  "The file could not be decoded."; "This kind of file is not supported yet: <format>." with `<format>` from a
  fixed list of format names; "This ffmpeg cannot do what this file needs: <capability>."; "A display copy is not
  made for a clip."; "The frame times could not be kept."; "An audio stream could not be copied unchanged.";
  "The original changed while the job ran."; "The restoration stopped at its time limit."; "The restoration
  stopped on an internal error."

### 12. Export and the manifest (`services/media/router.py`, `services/media/variants.py`)

- `GET /api/media/{sha256}/variants/{variantId}/export`: the stored file with
  `Content-Disposition: attachment; filename="albedian-<variantId>.<ext>"`. The name holds no title.
- `GET /api/media/{sha256}/variants/{variantId}/manifest`: the manifest, as an attachment named
  `albedian-<variantId>.manifest.json`. It is built on request from the three stored records and the solution id
  read at that moment, so it is the same for the same stored state and holds no clock of its own.
- Fields: `format` ("albedian.export-manifest"), `formatVersion` (1), `sourceSha256`, `variant`, `graph`,
  `attempt`, `observationSolutionId` when `solution.json` exists, `disclosure`.
- It holds no title, file name, capture time, place or path: the three records hold none (P-RST-19). The exported
  file carries no container tag of the original either [decided 2026-11-11: an export is made to be shared; the
  owner can ask for their own metadata later].
- A photo's export is the variant's PNG. A JPEG export is not offered [open].

### 13. The display copy

Steps: decode (section 1), encode (section 8), store (section 9). The graph has the two steps `decode`, whose
parameters are the colour description's fields, each `measured` from the file's tags or `default` with the
assumption's reason, and `encode`, whose parameters are the container, the bit depth and the zlib level.
Verification: decode the written PNG and compare every sample with the decoded original; equal, or `rejected` with
`copy_differs`. Its job stages are `decode`, `encode`, `write` and `verify`, one frame each.

For a HEIC the "decoded pixels" are R'G'B' after the matrix and chroma interpolation of section 1, at 8 bits: what
a correct viewer shows, kept without loss from there on. Its stored luma and chroma samples are not what the PNG
holds, and the disclosure says "re-encoded".

Chrome and Firefox do not draw HEIC; Safari does from version 17 [verified: caniuse.com/heif, read through a fetch
on 2026-11-11]. The client asks for a display copy, through the same route as any other variant, after an upload
whose stored type is `image/heic` or `image/tiff`; ingest makes nothing on its own [decided 2026-11-11: one route
for every variant]. Wiring the web app to it is a unit of the `experience` owner. A clip gets no display copy in
this spec [open].

### 14. The contract (`packages/contracts/src/media.ts`, `services/contracts/media.py`)

`SourceAssetSchema`, `AudioStreamDescriptorSchema`, the orientation helpers and the patterns stay as they are.

**Removed, because nothing produces or enforces them:**
- `ProcessingNodeSchema.inputNodeIds`, `maskId`, `modelSha256`, `preservePointSources`; `ProcessingGraphSchema.
  parentGraphId`, `profileName`, `fidelityPolicy` and `FidelityPolicySchema` (five flags nothing enforces);
  `validateProcessingGraphDAG` and `computeNodeCacheKeys` with their tests [decided 2026-11-11: nothing branches
  or caches, and the contract must not describe what does not exist].
  `ProcessingNodeSchema` and `ProcessingOperationKindSchema` give way to `GraphStepSchema` and `GRAPH_STEP_KINDS`.
- `AttemptEvaluationMetricsSchema` whole: `evaluationMethod`, `hasPairedReference`, `shadowSnrGainDb`,
  `estimatedNoiseReductionPct`, `pointSourceRetentionRatio`, `falsePointArtifactCount`,
  `faceRegionStabilityScore`, `temporalFlickerIndex`, `audioSyncOffsetMs`, `validityNotes`.
- `ProcessingAttemptSchema.parentAttemptId`, `label`, `cacheKey`, `gpuMs`, `outputVariantId`, `metrics`, and the
  statuses `evaluating`, `canceled` and `superseded`.
- `MediaVariantSchema.label`, `summaryNote`, `frameTimestampsMs`, `audioStreamCopiedFromSource`,
  `audioSyncOffsetMs`.
- `ExportArtifactSchema.sessionId`, `outputUri`, `containerFormat`, `videoCodec`, `audioPreserved`, `audioCodec`,
  `audioSyncOffsetMs`, `processingGraphId`, `catalogCitations`, `epistemicSummary` (with `strictFidelityEnforced`).

**The new shapes (Zod).** `sha256()` is `z.string().regex(new RegExp(SHA256_HEX_PATTERN))`; `utcInstant()` is the
ingest contract's.

```ts
export const VARIANT_KINDS = ['display_copy', 'restoration'] as const;
export const PARAMETER_ORIGINS = ['measured', 'requested', 'default'] as const;
export const GRAPH_STEP_KINDS = [
  'decode', 'find_stars', 'measure_noise', 'register', 'temporal_merge', 'spatial_denoise',
  'dissolve_unconfirmed', 'lift', 'encode', 'copy_audio',
] as const;
export const MEDIA_ID_PATTERNS = {
  graph: '^graph_[0-9a-f]{16}$', variant: '^var_[0-9a-f]{16}$',
  attempt: '^att_[0-9a-f]{16}$', job: '^rjob_[0-9a-f]{16}$',
} as const;
export const DISCLOSURES = {
  display_copy: "The original's own pixels, re-encoded without loss so a browser can show them. Nothing was restored.",
  restoration: "Restored from the original's own pixels with deterministic filters. Nothing was generated.",
} as const;

export const RestoreRequestSchema = z
  .object({ kind: z.enum(VARIANT_KINDS), exposureEv: z.number().min(0).max(6).optional() })
  .strict()
  .refine((r) => r.kind === 'restoration' || r.exposureEv === undefined, { message: 'a display copy takes no option' });

export const GraphParameterSchema = z
  .object({
    name: z.string().min(1).max(64),
    value: z.union([z.number().finite(), z.string().max(256), z.boolean()]),
    origin: z.enum(PARAMETER_ORIGINS),
    basis: z.string().min(1).max(300), // what it was measured from, the request field, or the source or reason
  })
  .strict();

export const GraphStepSchema = z
  .object({
    kind: z.enum(GRAPH_STEP_KINDS),
    version: z.string().regex(/^\d+\.\d+\.\d+$/),
    parameters: z.array(GraphParameterSchema).max(64),
  })
  .strict();

export const ProcessingGraphSchema = z
  .object({
    id: z.string().regex(new RegExp(MEDIA_ID_PATTERNS.graph)),
    sourceSha256: sha256(),
    kind: z.enum(VARIANT_KINDS),
    pipelineVersion: z.string().regex(/^\d+\.\d+\.\d+$/),
    request: RestoreRequestSchema,
    steps: z.array(GraphStepSchema).min(2).max(16),
  })
  .strict(); // refined: kind equals request.kind; the first step is decode; a display copy is exactly decode, encode;
             // a restoration holds lift and encode once each, in pipeline order, and no step twice

export const ColourDescriptionSchema = z
  .object({
    model: z.enum(['rgb', 'ycbcr']),
    bitDepth: z.union([z.literal(8), z.literal(10), z.literal(16)]),
    range: z.enum(['full', 'limited']),
    matrix: z.enum(['identity', 'bt601', 'bt709', 'bt2020nc']),
    transfer: z.enum(['srgb', 'bt709', 'hlg', 'pq', 'icc_parametric', 'icc_table']),
    primaries: z.enum(['bt709', 'p3_d65', 'bt2020', 'icc', 'unspecified']),
    chromaSubsampling: z.enum(['444', '422', '420']),
    chromaSiting: z.enum(['left', 'center', 'topleft']).optional(),
    iccProfileSha256: sha256().optional(),
    assumptions: z
      .array(
        z
          .object({
            field: z.enum(['range', 'matrix', 'transfer', 'primaries', 'chromaSiting']),
            assumed: z.string().min(1).max(32),
            reason: z.string().min(1).max(200),
          })
          .strict()
      )
      .max(5),
  })
  .strict(); // refined: rgb has matrix identity and subsampling 444; chromaSiting is present exactly when not 444

export const NoiseTableSchema = z
  .object({
    intensities: z.array(z.number().min(0).max(1)).min(3).max(24), // linear light, increasing
    sigma: z.object({ r: z.array(z.number().nonnegative()), g: z.array(z.number().nonnegative()), b: z.array(z.number().nonnegative()) }).strict(),
    blockCounts: z.array(z.number().int().positive()),
  })
  .strict(); // refined: all five arrays have one length; intensities strictly increase

export const GATE_STATISTICS = ['detector', 'detector_local_background_removed'] as const; // a third lands here first

export const IntegrityGateResultSchema = z
  .object({
    statistic: z.enum(GATE_STATISTICS),
    variantThresholdSnr: z.number().positive(),
    confirmThresholdSnr: z.number().positive(),
    tilePx: z.number().int().positive(),
    matchRadiusPx: z.number().positive(),
    framesChecked: z.number().int().positive(),
    variantPointCount: z.number().int().nonnegative(),
    unconfirmedPointCount: z.number().int().nonnegative(),
    framesWithUnconfirmed: z.number().int().nonnegative(),
    clippedPointsLeftOut: z.number().int().nonnegative(),
    starsMeasured: z.number().int().nonnegative(),
    starsOutsideFluxTolerance: z.number().int().nonnegative(),
    fluxToleranceSigma: z.number().positive(),
    worstFluxChangeSigma: z.number().nonnegative().optional(), // absent when no star was measured
    medianFluxRatio: z.number().positive().optional(),
    passed: z.boolean(),
  })
  .strict(); // refined: passed exactly when both counts of failures are 0; unconfirmed <= variantPointCount;
             // framesWithUnconfirmed <= framesChecked and is 0 exactly when unconfirmedPointCount is 0

export const RestorationMeasurementsSchema = z
  .object({
    colour: ColourDescriptionSchema,
    noise: NoiseTableSchema.optional(),
    noiseNotMeasured: z.enum(['too_few_flat_blocks', 'fine_detail_mostly_zero', 'frame_too_small']).optional(),
    fineNoiseLeft: z.number().nonnegative().optional(), // after over before, darkest quarter
    stars: z.object({ found: z.number().int().nonnegative(), confirmed: z.number().int().nonnegative() }).strict().optional(),
    dissolvedPerPass: z.array(z.number().int().nonnegative()).max(3).optional(),
    lift: z
      .object({
        gainAtBlack: z.number().min(1),
        decidedBy: z.enum(['none', 'target', 'requested', 'noise_bound', 'quantisation_bound', 'maximum']),
      })
      .strict()
      .optional(),
    clip: z
      .object({
        frameCount: z.number().int().positive(),
        framesWithNeighbours: z.number().int().nonnegative(),
        medianClosureErrorPx: z.number().nonnegative().optional(),
        exactRepeatShare: z.number().min(0).max(1),
        pointsConfirmedOnFramesTogether: z.number().int().nonnegative(), // confirmed by no single frame alone
        pointsFixedToSensor: z.number().int().nonnegative(), // refused: same sensor pixel while the scene moved
        streamsNotCopied: z.number().int().nonnegative(),
      })
      .strict()
      .optional(),
    peakMemoryBytes: z.number().int().positive().optional(),
    notes: z.array(z.string().min(1).max(300)).max(8), // plain sentences for the person; none holds a path, time or place
  })
  .strict(); // refined: exactly one of noise and noiseNotMeasured for a restoration

export const ATTEMPT_STATUSES = ['ready', 'rejected', 'failed'] as const;
export const REJECTION_REASONS = ['unconfirmed_points', 'star_flux_changed', 'frame_times_differ', 'audio_differs', 'copy_differs'] as const;

export const ProcessingAttemptSchema = z
  .object({
    id: z.string().regex(new RegExp(MEDIA_ID_PATTERNS.attempt)),
    sourceSha256: sha256(),
    graphId: z.string().regex(new RegExp(MEDIA_ID_PATTERNS.graph)),
    jobId: z.string().regex(new RegExp(MEDIA_ID_PATTERNS.job)),
    status: z.enum(ATTEMPT_STATUSES),
    startedAtUTC: utcInstant(),
    finishedAtUTC: utcInstant(),
    elapsedMs: z.number().nonnegative(),
    cpuMs: z.number().nonnegative(),
    variantId: z.string().regex(new RegExp(MEDIA_ID_PATTERNS.variant)).optional(),
    measured: RestorationMeasurementsSchema.optional(),
    gate: IntegrityGateResultSchema.optional(),
    rejection: z.enum(REJECTION_REASONS).optional(),
    failureReason: z.string().min(1).max(200).optional(),
  })
  .strict(); // refined: ready has variantId and measured and no rejection or failureReason; rejected has rejection
             // and measured and no variantId; failed has failureReason and no variantId; a gate is present for a
             // ready or rejected restoration, absent for a display copy; ready implies gate.passed when a gate is
             // present; rejection unconfirmed_points or star_flux_changed implies a gate that did not pass

export const MediaVariantSchema = z
  .object({
    id: z.string().regex(new RegExp(MEDIA_ID_PATTERNS.variant)),
    kind: z.enum(VARIANT_KINDS),
    sourceSha256: sha256(),
    sourceAssetId: z.string().min(1),
    graphId: z.string().regex(new RegExp(MEDIA_ID_PATTERNS.graph)),
    attemptId: z.string().regex(new RegExp(MEDIA_ID_PATTERNS.attempt)),
    sha256: sha256(),
    uri: z.string().min(1),
    byteSize: z.number().int().positive(),
    mimeType: z.enum(['image/png', 'video/mp4', 'video/quicktime']),
    width: z.number().int().positive(),
    height: z.number().int().positive(),
    durationMs: z.number().nonnegative(),
    frameCount: z.number().int().positive(),
    frameTimesSha256: sha256().optional(), // of the time base and the integer frame times; a clip has it, a still not
    audioStreams: z.array(AudioStreamDescriptorSchema),
    colour: ColourDescriptionSchema,
    displayOrientation: DisplayOrientationSchema.optional(),
    disclosure: z.string().min(1),
    createdAtUTC: utcInstant(),
  })
  .strict(); // refined: disclosure equals DISCLOSURES[kind]; a still has frameCount 1, durationMs 0, no audio and
             // no frameTimesSha256; a clip has frameTimesSha256

export const RESTORE_JOB_STAGES = ['decode', 'measure', 'restore', 'encode', 'write', 'verify'] as const;
export const RESTORE_JOB_STATES = ['queued', 'running', 'succeeded', 'rejected', 'failed'] as const;

export const RestoreJobSchema = z
  .object({
    id: z.string().regex(new RegExp(MEDIA_ID_PATTERNS.job)),
    sourceAssetSha256: sha256(),
    request: RestoreRequestSchema,
    state: z.enum(RESTORE_JOB_STATES),
    createdAtUTC: utcInstant(),
    startedAtUTC: utcInstant().optional(),
    finishedAtUTC: utcInstant().optional(),
    stages: z.array(
      z
        .object({
          stage: z.enum(RESTORE_JOB_STAGES),
          unit: z.enum(['frames', 'bytes']),
          done: z.number().int().nonnegative(),
          total: z.number().int().positive().nullable(),
        })
        .strict()
    ),
    attemptId: z.string().regex(new RegExp(MEDIA_ID_PATTERNS.attempt)).optional(),
    variantId: z.string().regex(new RegExp(MEDIA_ID_PATTERNS.variant)).optional(),
    error: z.string().min(1).max(200).optional(),
  })
  .strict(); // refined as SolveJob is: fields fit the state; times run created, started, finished; stages once each
             // in pipeline order; done <= total; write is in bytes and every other stage in frames

export const ExportManifestSchema = z
  .object({
    format: z.literal('albedian.export-manifest'),
    formatVersion: z.literal(1),
    sourceSha256: sha256(),
    variant: MediaVariantSchema,
    graph: ProcessingGraphSchema,
    attempt: ProcessingAttemptSchema,
    observationSolutionId: z.string().min(1).max(128).optional(),
    disclosure: z.string().min(1),
  })
  .strict(); // refined: the four hashes and ids agree across the parts; attempt.status is ready; disclosure equals
             // variant.disclosure

export const ExportArtifactSchema = z
  .object({
    variantId: z.string().regex(new RegExp(MEDIA_ID_PATTERNS.variant)),
    variantSha256: sha256(),
    exportedAtUTC: utcInstant(),
  })
  .strict(); // what a session remembers of a download; the manifest is the record of how the file was made
```

**Python.** `services/contracts/media.py` mirrors every shape as a strict Pydantic model on the `_Model` base of
`services/contracts/ingest.py:111-124` (camelCase aliases, no unknown key, null only where Zod allows it), with the
same rules as model validators and the same messages. The constants (`VARIANT_KINDS`, `GRAPH_STEP_KINDS`, the id
patterns, the two disclosure sentences, the stage and state lists) are mirrored and compared through the fixture.
`Sha256`, `UtcInstant`, `Text`, `AudioStreamDescriptor` and `DisplayOrientation` are the ingest module's own.

```python
Semver = Annotated[str, StringConstraints(pattern=r"^\d+\.\d+\.\d+$")]
GraphId = Annotated[str, StringConstraints(pattern=r"^graph_[0-9a-f]{16}$")]
VariantId = Annotated[str, StringConstraints(pattern=r"^var_[0-9a-f]{16}$")]
AttemptId = Annotated[str, StringConstraints(pattern=r"^att_[0-9a-f]{16}$")]
RestoreJobId = Annotated[str, StringConstraints(pattern=r"^rjob_[0-9a-f]{16}$")]
Count = Annotated[int, Field(ge=0)]
Positive = Annotated[int, Field(gt=0)]
NonNegative = Annotated[float, Field(ge=0)]
VariantKind = Literal["display_copy", "restoration"]

class RestoreRequest(_Model):
    kind: VariantKind
    exposure_ev: Optional[Annotated[float, Field(ge=0, le=6)]] = None        # refused for a display copy

class GraphParameter(_Model):
    name: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    value: Union[StrictBool, StrictFloat, StrictInt, Annotated[str, StringConstraints(max_length=256)]]
    origin: Literal["measured", "requested", "default"]
    basis: Annotated[str, StringConstraints(min_length=1, max_length=300)]

class GraphStep(_Model):
    kind: Literal["decode", "find_stars", "measure_noise", "register", "temporal_merge", "spatial_denoise",
                  "dissolve_unconfirmed", "lift", "encode", "copy_audio"]
    version: Semver
    parameters: Annotated[list[GraphParameter], Field(max_length=64)]

class ProcessingGraph(_Model):
    id: GraphId
    source_sha256: Sha256
    kind: VariantKind
    pipeline_version: Semver
    request: RestoreRequest
    steps: Annotated[list[GraphStep], Field(min_length=2, max_length=16)]

class ColourAssumption(_Model):
    field: Literal["range", "matrix", "transfer", "primaries", "chromaSiting"]
    assumed: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    reason: Annotated[str, StringConstraints(min_length=1, max_length=200)]

class ColourDescription(_Model):
    model: Literal["rgb", "ycbcr"]
    bit_depth: Literal[8, 10, 16]
    range: Literal["full", "limited"]
    matrix: Literal["identity", "bt601", "bt709", "bt2020nc"]
    transfer: Literal["srgb", "bt709", "hlg", "pq", "icc_parametric", "icc_table"]
    primaries: Literal["bt709", "p3_d65", "bt2020", "icc", "unspecified"]
    chroma_subsampling: Literal["444", "422", "420"]
    chroma_siting: Optional[Literal["left", "center", "topleft"]] = None
    icc_profile_sha256: Optional[Sha256] = None
    assumptions: Annotated[list[ColourAssumption], Field(max_length=5)]

class NoiseSigma(_Model):
    r: list[NonNegative]
    g: list[NonNegative]
    b: list[NonNegative]

class NoiseTable(_Model):
    intensities: Annotated[list[Annotated[float, Field(ge=0, le=1)]], Field(min_length=3, max_length=24)]
    sigma: NoiseSigma
    block_counts: list[Positive]

class IntegrityGateResult(_Model):
    statistic: Literal["detector", "detector_local_background_removed"]
    variant_threshold_snr: Annotated[float, Field(gt=0)]
    confirm_threshold_snr: Annotated[float, Field(gt=0)]
    tile_px: Positive
    match_radius_px: Annotated[float, Field(gt=0)]
    frames_checked: Positive
    variant_point_count: Count
    unconfirmed_point_count: Count
    frames_with_unconfirmed: Count
    clipped_points_left_out: Count
    stars_measured: Count
    stars_outside_flux_tolerance: Count
    flux_tolerance_sigma: Annotated[float, Field(gt=0)]
    worst_flux_change_sigma: Optional[NonNegative] = None
    median_flux_ratio: Optional[Annotated[float, Field(gt=0)]] = None
    passed: bool

class StarCounts(_Model):
    found: Count
    confirmed: Count

class Lift(_Model):
    gain_at_black: Annotated[float, Field(ge=1)]
    decided_by: Literal["none", "target", "requested", "noise_bound", "quantisation_bound", "maximum"]

class ClipMeasurements(_Model):
    frame_count: Positive
    frames_with_neighbours: Count
    median_closure_error_px: Optional[NonNegative] = None
    exact_repeat_share: Annotated[float, Field(ge=0, le=1)]
    points_confirmed_on_frames_together: Count
    points_fixed_to_sensor: Count
    streams_not_copied: Count

class RestorationMeasurements(_Model):
    colour: ColourDescription
    noise: Optional[NoiseTable] = None
    noise_not_measured: Optional[Literal["too_few_flat_blocks", "fine_detail_mostly_zero", "frame_too_small"]] = None
    fine_noise_left: Optional[NonNegative] = None
    stars: Optional[StarCounts] = None
    dissolved_per_pass: Optional[Annotated[list[Count], Field(max_length=3)]] = None
    lift: Optional[Lift] = None
    clip: Optional[ClipMeasurements] = None
    peak_memory_bytes: Optional[Positive] = None
    notes: Annotated[list[Annotated[str, StringConstraints(min_length=1, max_length=300)]], Field(max_length=8)]

class ProcessingAttempt(_Model):
    id: AttemptId
    source_sha256: Sha256
    graph_id: GraphId
    job_id: RestoreJobId
    status: Literal["ready", "rejected", "failed"]
    started_at_utc: UtcInstant = Field(alias="startedAtUTC")
    finished_at_utc: UtcInstant = Field(alias="finishedAtUTC")
    elapsed_ms: NonNegative
    cpu_ms: NonNegative
    variant_id: Optional[VariantId] = None
    measured: Optional[RestorationMeasurements] = None
    gate: Optional[IntegrityGateResult] = None
    rejection: Optional[Literal["unconfirmed_points", "star_flux_changed", "frame_times_differ", "audio_differs",
                                "copy_differs"]] = None
    failure_reason: Optional[Annotated[str, StringConstraints(min_length=1, max_length=200)]] = None

class MediaVariant(_Model):
    id: VariantId
    kind: VariantKind
    source_sha256: Sha256
    source_asset_id: Text
    graph_id: GraphId
    attempt_id: AttemptId
    sha256: Sha256
    uri: Text
    byte_size: Positive
    mime_type: Literal["image/png", "video/mp4", "video/quicktime"]
    width: Positive
    height: Positive
    duration_ms: NonNegative
    frame_count: Positive
    frame_times_sha256: Optional[Sha256] = None
    audio_streams: list[AudioStreamDescriptor]
    colour: ColourDescription
    display_orientation: Optional[DisplayOrientation] = None
    disclosure: Text
    created_at_utc: UtcInstant = Field(alias="createdAtUTC")

class RestoreStageProgress(_Model):
    nullable: ClassVar[frozenset[str]] = frozenset({"total"})
    stage: Literal["decode", "measure", "restore", "encode", "write", "verify"]
    unit: Literal["frames", "bytes"]
    done: Count
    total: Optional[Positive]

class RestoreJob(_Model):
    id: RestoreJobId
    source_asset_sha256: Sha256
    request: RestoreRequest
    state: Literal["queued", "running", "succeeded", "rejected", "failed"]
    created_at_utc: UtcInstant = Field(alias="createdAtUTC")
    started_at_utc: Optional[UtcInstant] = Field(default=None, alias="startedAtUTC")
    finished_at_utc: Optional[UtcInstant] = Field(default=None, alias="finishedAtUTC")
    stages: list[RestoreStageProgress]
    attempt_id: Optional[AttemptId] = None
    variant_id: Optional[VariantId] = None
    error: Optional[Annotated[str, StringConstraints(min_length=1, max_length=200)]] = None

class ExportManifest(_Model):
    format: Literal["albedian.export-manifest"]
    format_version: Literal[1]
    source_sha256: Sha256
    variant: MediaVariant
    graph: ProcessingGraph
    attempt: ProcessingAttempt
    observation_solution_id: Optional[Annotated[str, StringConstraints(min_length=1, max_length=128)]] = None
    disclosure: Text

class ExportArtifact(_Model):
    variant_id: VariantId
    variant_sha256: Sha256
    exported_at_utc: UtcInstant = Field(alias="exportedAtUTC")
```

Each refinement noted beside the Zod shapes is a `model_validator(mode="after")` here with the same message.
`AudioStreamDescriptor` is a loose model in the ingest module (it drops unknown keys), as its Zod schema is not
strict; every new shape is strict on both sides.

**Fixture.** `tests/fixtures/media_contract.json`, written from `build_media_samples()` in the Python module under
an environment switch, as the ingest fixture is (`tests/integration/test_ingest_contract.py:1-8`), and parsed by
`packages/contracts/src/__tests__/media.test.ts`. Valid samples: a request of each kind; a display copy and a
restoration of a still, each with graph, attempt and variant; a clip restoration; a rejected attempt of each
rejection reason; a failed attempt; a job in each state; a manifest. Invalid samples, each with its reason: one per
refinement above. All values are synthetic (hashes typed for the test, no real upload).

**The same unit adapts** every file that reads a removed field [verified: a search at `dcdbd65d` finds them in
these files and in `services/media/models.py`, which restore-9 removes]:
`packages/contracts/src/spatial-and-voice.ts` (the session's four arrays),
`packages/observation/src/reducer.ts` (`media.restore` picks a variant by kind, not by `profileName`),
`packages/observation/src/checkpoints.ts` (`validateMediaTimelineSync` loses its list of frame times),
`packages/observation/src/emptySession.ts`, `packages/observation/src/testing/syntheticSessions.ts`, the tests of
those files, and `scripts/verify-contracts.mjs` (adds `RestoreRequestSchema`, `RestoreJobSchema`,
`IntegrityGateResultSchema` and `ExportManifestSchema`). Those are shared files; `tasks.md` names them for
restore-1, so they are approved when the spec lands [decided 2026-11-11: one yes for the plan]. The session's
`media.restore` stays honest: it still refuses plainly when no variant exists. The web app is wired to the new
routes later, by its own lane [decided 2026-11-11: the session must not claim a variant it does not have, and the
web app is another lane's files].

### 15. Corrections of old code (the survey's section 4)

| Item | Where | Outcome |
|---|---|---|
| `shadowSnrGainDb`, no signal term | `runtime.py:337-344` | Cut. The record carries the noise table and `fineNoiseLeft`. |
| Thresholds 3.0, 3.3 and 3.5 that all resolve to one gate | `detector.py:236-237`, `316` | The media code passes 4.75 and 6 and says the floor is the detector's. The detector is not changed. |
| `status` always `ready`; ids of a graph, variant and cache that do not exist | `models.py:106-116` | Made real: the gate decides the status; graph and variant are stored records; the cache key is cut. |
| `faceRegionStabilityScore` | `runtime.py:380-383` | Cut. Nothing detects a face. |
| Metrics clamped before they are reported | `models.py:41-56` | Cut with the metrics; the new record stores what was measured. |
| `strictFidelityEnforced: True`, `allowSyntheticSky=False` literals | `models.py:162`, `runtime.py:429` | Cut. The list of step kinds and the gate result take their place. |
| Scale guessed from `max > 1.5`; horizon fallback at 55 percent | `silhouette.py:46`, `69`, `137`, `171` | Not used by the new path: no sky mask is guessed. Only `runtime.py`, the package's `__init__.py` and `tests/integration/test_frontier_upgrades.py` import `silhouette.py` today, so it is removed with the runtime. |
| Seeded dither | `silhouette.py:162-168` | Cut (REQ-RST-4.8). |
| Scripts with fixed curves, a default camera and duration, a figure beside the wrong picture, detection on a stretched picture | `scripts/render_clip_variants.py`, `scripts/run_real_media_experiment.py` | Removed, or rewritten as thin callers of the job (REQ-RST-11.3). |
| A silent export records the frames' hash as `sourceSha256` | `runtime.py:515` | Cut with the old export; the variant record carries the store's hash. |
| The `temporal` branch pastes a stack into every frame | `runtime.py:294-331` | Replaced by section 10. |
| Variants matched to clips by file name | `discovery.py:350-382` | Left to the owner of discovery; the new variants are linked by hash only. Noted for the lead. |

### 16. Error handling

- A request for an unknown hash: 404. A body that is not a `RestoreRequest`: 422. A display copy asked for a clip:
  the job fails with its sentence.
- Unsupported colour or pixel format, or an ffmpeg without a needed feature: `failed` with the fixed sentence and
  the format or capability name from a fixed list. ffmpeg's own messages are never stored: they can hold a path
  (`services/ingest/jobs.py:207-211` does the same).
- The noise cannot be measured: not a failure. The attempt goes on without noise reduction and says so.
- Registration fails for a frame: not a failure. That frame gets no temporal step, and the record counts it.
- The gate or a check says no: `rejected`, the partial file removed, the attempt stored with its counts.
- The time bound, a crash of the child, an unexpected exception: `failed` with the generic sentence; partial
  files removed; no job record is left in the state `running`.
- The original's hash differs at the start or the end: `failed`; nothing is linked into place.
- A second request for the same graph while the first runs returns the running job.

### 17. Testing strategy

- **Unit tests** per module under `tests/media/` (new folder), each written first and seen failing for the stated
  reason: `test_colour.py` (the four transfer functions against the formulas above at seeded points; the three
  matrices; both ranges; chroma siting; ICC parametric types 0 to 4 and a table), `test_decode.py`, `test_stars.py`,
  `test_noise.py`, `test_spatial.py`, `test_tone.py`, `test_gate.py`, `test_png16.py`, `test_clip_io.py`,
  `test_temporal.py`, `test_variants.py`, `test_restore_job.py`, `test_export.py`; contract tests in
  `tests/integration/test_media_contract.py` and `packages/contracts/src/__tests__/media.test.ts`.
- **Properties** P-RST-1 to P-RST-24, hypothesis and fast-check, 20 seeds each, rare cases seeded. The two guards
  of a clip's confirmation each have their own test: P-RST-14 (nothing from the neighbours alone) and P-RST-24
  (nothing fixed to a sensor pixel while the scene moves); P-RST-23 is the faint star the frames together confirm.
- **Platform-stable**: no float array is hashed or compared byte for byte; files written by the job are compared
  by hash only against a second run on the same machine.
- **Mutants on the risky lines**, each shown killed: the footprint term `(1 - M(q))`; the composite `M x + (1 - M)
  smooth`; the gate's comparison `unconfirmed == 0`; the variant threshold and the confirmation call's
  `noise_floor`; the lift undone before the gate; the noise bound's direction; the isotonic fit's direction; the
  own-frame weight 1 in the merge; the codec-copy rule; the frames together built from original frames and not
  from the merged frame; each frame's agreement in guard 1; the comparison and the scene-moved test in guard 2;
  the `setpts` step
  for a differing gap; `-c:a copy`; the mode 0400 and the `os.link`; the status rule that only `ready` is served;
  each refinement of the contract.
- **Real data**: `local_dataset` tests on the five photos (and the corpus once it lands) assert that the honest
  run ends `ready`, that a painted star at the stated sensitivity is refused in every trial (REQ-RST-6.7), that the
  flux tolerance holds and that the stated noise reduction is reached; `owner_media` tests run the clip path on
  the owner's clips and assert with fixed messages, printing counts only. Reports give counts, never a name.
- **The pool**: one synthetic clip run with one worker and with two gives stored files with the same hash.
- **Both ffmpeg builds**: every clip test runs in both CI jobs; a test that needs a feature only the newer build
  has takes the branch the build allows and asserts the plain refusal on the other, as the HEIC tests do
  (`tests/integration/test_ci_ffmpeg.py`).
- **Pictures for a person**: `scripts/restore_review.py` (new, in restore-4) runs the job on the real photos
  and clips and writes, per input, a side by side PNG of original, lift only and variant, with crops at full size,
  under `.cache/restore-review/<unit>/`, which git ignores. Nothing under it is ever added. Its console output is
  counts and paths inside `.cache/` only.

### 18. A new dependency: OpenCV, proposed as its own unit, not assumed

Nothing above needs a new dependency. One would help in two places:
- **What it would replace.** The whole-frame shift of section 10 by a fitted homography or dense flow
  (`findTransformECC`, `calcOpticalFlowFarneback`, `remap`), which follows a turning camera where a shift cannot;
  and the numpy loops of section 5 by compiled filters, if the prototype's speed holds in the real code.
- **Licence and size.** OpenCV 4.5 and later is Apache 2.0, and `opencv-python-headless` is a wheel of some tens
  of megabytes [inference: neither was checked at its source here; the unit that proposes it reads the licence
  file and the wheel size first].
- **Without it.** Temporal noise reduction works only while a shift explains the motion, and is off elsewhere, by
  the measured quality of section 10; the clip costs what M13 says.
- It would be one unit, with the lead's yes: the dependency alone, then a second unit that uses it, each gated as
  before. It adds no learned model.
