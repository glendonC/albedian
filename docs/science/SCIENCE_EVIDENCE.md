# Scientific Evidence Rules & Camera Projection Mathematics

## 1. Epistemic Taxonomy (`EvidenceClass`)

Every `CelestialFinding` in Albedian carries one of four mutually exclusive evidence classes:

| Class | Requirement | Visual Marker Grammar | Forbidden Claim |
|---|---|---|---|
| `observed` | Point or streak extracted from immutable `SourceAsset` pixels. A point the detector finds has `snr` at or above the threshold it was run with, `DETECTION_THRESHOLD` (4.75) unless a caller gives another: the frame's statistic over its own noise, measured tile by tile on the same pixels (section 1.1). A clipped core is reported as one point flagged saturated, its `snr` and flux capped by the clipping; a block of at most 4 pixels at the top code with nothing lit round it is a hot-pixel impulse, kept out of the solver's pool | Solid center dot (`#D1E9EF`) with hairline tick at measured `(xPx, yPx)` | Naming the star from pixel brightness alone, or giving it a sky position, without verified WCS |
| `catalog_matched` | `observed` detection linked to a pinned catalog entry via verified WCS (`heldOutInliers >= 6`, `medianResidualPx <= 3.0`) | Concentric precision ring (`#81C5CC`) around observed centroid + elbow-connected label | Claiming zero distance uncertainty or phone-measured depth |
| `predicted` | Computed from observer UTC timestamp, GPS coordinates, and ephemeris/catalog | Dashed open circle (`#D9AE75`) + `Predicted` badge | Rendering as synthetic camera pixels or claiming sensor detection |
| `simulated` | 3D catalog-geometry rendering, modeled bodies and scenes, alternate viewpoint, or non-observation epoch (`simulationEpoch != observationUTC`) | Bracketed reticle (`#ABB0D6`) + `Simulated` badge and `"Drawn or computed by the app; not measured in the source recording."` disclosure, with each layer naming its own source and limits | Presenting artistic star surfaces or unmarked non-Euclidean warps |


### 1.1 What an `observed` point's `snr` counts (realsky-3, realsky-4)

A point is kept when the frame's statistic reaches `DETECTION_THRESHOLD` (4.75) noises, or the threshold a caller gives, which acts as given. The threshold was chosen for a stated target [proposed] of at most `FALSE_TARGET_PER_MP` (0.5) false detections per megapixel on pure noise. On white noise and noise correlated at ratios 2, 3 and 3.7 it measured 0.13, 0.26, 0.07 and 0 per megapixel [tested]. The statistic is one of two, chosen per frame from measured noise and never from what is detected (`_detection_statistic` in `services/science/detector.py`); every detection records which, with the figures that chose it (`SourceDetection.frame`).

- **The kernel's statistic.** The matched response (a 1.15 px Gaussian on a 7 x 7 footprint) over that response's own spread, measured tile by tile and never under the frame's pixel noise (`matched_filter_noise`).
- **The noise-whitened matched filter.** For a star of known profile s in stationary Gaussian noise of covariance C, a linear filter w answers the star with w's and the noise with sqrt(w'Cw). By Cauchy-Schwarz, (w's)^2 <= (w'Cw)(s'C^-1 s), with equality for w proportional to C^-1 s, so no linear filter answers the star higher over its noise. For point sources in astronomical maps it is the filter that maximises the signal over the noise, the source profile over the noise's power (Tegmark and de Oliveira-Costa 1998, ApJ 500, L83, eq. 6 [read at the source]). On white noise it is the kernel. On noise shared between neighbouring pixels the kernel sums the noise's own band: per unit of a star's peak at a correlation of 2.0 px, 0.58 noises for the kernel, 1.00 for the peak pixel alone, and 2.8 to 4.4 for this filter with a white share of 5 to 1 % [tested].
- **Its covariance.** Measured on the frame's own residual: 7 x 7 patches of sky, clear of stars and holes (4 pixel noises, and 3 px around them), of clipped flats and of the edge, on a grid where no two patches share a pixel. Conditioned by Ledoit and Wolf's shrinkage (2004, J. Multivariate Anal. 88, 365, eq. 14 [read at the source]); where that estimate is not positive definite (two patches), there is no whitened filter. The same noise with and without 400 stars gives filters whose cosine is above 0.99. On a crowded real field (rp-b-19, the Milky Way) stricter star masks move it by a cosine of 0.973 to 0.996, and 3000 added faint stars by 0.9994 [tested].
- **Its background.** The whitened filter reads a residual whose background is joined by a B-spline, not straight lines: it reads the lines' kinks as stars. On two frames of noise at ratio 3.7 with 300 stars each, the lines gave 9 false detections on one and, on the other, raised the negative image until the kernel was chosen (250 stars found against 296); the spline gave none and found 293 and 296 [tested].
- **Its star**, measured on the frame's own bright, isolated, unsaturated stars, as Stetson (1987, PASP 99, 191, section II.C [read at the source]) adapts a model PSF to the observed profiles of bright stars in each frame. The rule is fixed before any threshold and never reads what the detector finds [proposed]:
  - a peak 10 pixel noises up, with no other such peak of a quarter of its height within 7 px;
  - no clipped flat in its 11 x 11 patch, and 7 px from the edge;
  - at most 400 of them, and at least 10, else the kernel.

  The stars are combined as the per-pixel median of their peak-normalised patches, fitted with an elliptical Gaussian whose widths are the profile's. A fitted width under 0.41 px (where a Gaussian's four nearest pixels hold 5 % of its peak) is no profile. Noiseless stars of 0.7, 1.0 and 1.6 px measure 0.700, 1.000 and 1.601 px. On noise, stars of 1.15 px measure 1.14 to 1.24 (wider the more the noise is correlated), of 1.6 and 2.2 px 1.62 and 2.21, and a 2.0 by 1.2 px trail 1.99 by 1.22; halves of the stars agree within 0.06 px [tested].
- **On integer data** (realsky-4b). Where the luma sits on a grid of whole steps and its noise is under 15 steps (tried only where the plain path's noise is under 60 steps), the background's 7 x 7 median is the grouped median, the window's middle value placed within its bin by the share of the middle sample in it, the formula of Python's `statistics.median_grouped` [read at the source, CPython 3.14 Lib/statistics.py]; and the pixel noise is the spread clipped at 3 kept spreads around the median until it settles, SExtractor's rule ("clipped iteratively until convergence at ±3σ around its median" [read at the source, sextractor.readthedocs.io, Background]), over 0.984846, the spread such clipping leaves a Gaussian, and never closer than 1.5 steps. On star-free integer skies the plain median was 0.34 to 0.45 codes off the true sky (0.07 to 0.27 before rounding) and the grouped one within 0.042 codes of the unrounded; the MAD read 0.40 to 1.61 times the rounded sky's own spread and the clipped spread 1.008 to 1.037 times it [tested]. A colour photo's luma, on a grid of 0.0002 codes at most, keeps the plain path. Below a bar, said and pinned: a 900 x 1300 dark 8-bit sky (1.1 codes, read noise 0.5), faintest fifth, about 2 % under main at an equal false count, and a 900 x 1300 white 8-bit sky (30 codes, read noise 2.0) under the first realsky-4 version in the two faintest fifths, whose extra stars came from a noise read 26 % low that loosened the neighbour tests kept in pixel noise (0.65 and 1.25) [tested].
- **The negative image.** The luma negated: its noise stays and its stars become holes. Each statistic's peaks there at the threshold are counted, away from any positive response's 7 x 7 footprint (a source's sidelobe) and scaled to the frame.
  - On built frames it equals the false detections within counting spread for white, correlated and integer noise [tested].
  - It assumes the noise is the same on both sides of the background. Near the black clip the dark side is cut off and it reads 0 while the false detections are 1 to 22 per 1.5 MP [tested], so it is not used there.
  - On a real photo it also counts real dark structure and misses what lies near stars, so it is not a false count there. It is used only to compare the two filters on the same frame and for the "noise_tail" flag.
- **The rule of choice** (`_use_whitened`). The whitened filter is used where four things hold on the frame's own measured stars (`_star_rating`); otherwise the kernel:
  - its median SNR gain over the kernel on those stars is at least 1.10 (`CHOICE_GAIN_MARGIN`): under 10 % is the size of what the background's own error gives it on white-like noise, so such a frame keeps the kernel for that reason;
  - that gain times the stars its peak and 3 x 3 gates keep is at least 1.10 times the stars the kernel's gates keep, and its gates keep at least one star;
  - its negative-image count is not above the kernel's by more than chance allows at 0.05 (`CHOICE_TEST_LEVEL`), by the exact test for two Poisson counts, conditional on their sum with each count's share of the area;
  - the frame is not flagged "near_black_clip".
- **The flags**, each from the frame's own pixels:
  - "near_black_clip": the background's median under 1.25 noises above the frame's lowest value. Beyond their counting spread, star-free skies left twice the target up to a measured 0.92 (floats), 1.14 (8-bit) and 1.24 (colour), and not from 1.30.
  - "noise_tail": the used statistic's negative-image peaks above the target for the frame's area. It is recorded with the result only: it fired on 22 of the public corpus's 26 photos.

  "near_black_clip" reaches the solution's warnings in plain words; "noise_tail" reaches no warning. Integer data with little noise is no flag since realsky-4b (on integer data, above).
- **What it costs, measured** [tested, realsky-4-v2 report]:
  - On integer dark frames the first realsky-4 version found fewer stars than main at main's own false count, because the background's median and the noise's MAD move in whole steps there; realsky-4b measures both on the grid (on integer data, above).
  - Against main (`b1ea12f4`, detector identical to `61e1ca2a`) on light correlation, crowding and wide stars on correlated noise (800 x 1100 frames over seeds 7301 to 7308, 7301 to 7303 for crowded and 1.8 px on 1.2 px noise, and 500 x 700 frames at seed 9301 with 1 to 5 % white share; measured on this commit at load 22.4 to 29.2) [tested, realsky-4-v2 report]:
    - Both sides (main's threshold raised to this detector's default false count, and this detector's adjusted while staying at or under main's false count there): 0.6 px 2198 against 2179 (+2.38 a seed, se 1.86), crowded 5336 against 5062 (+91.33 a seed, se 15.62), 0.8 px (whitened) 2117 against 2110 (+0.88 a seed, se 1.53; pinned within the 10-stars-a-seed exception), and 1.4 px wide (whitened) 100, 100, 99 against 96, 95, 95, all at or above main; 0.7 px (kernel kept) 2099 against 2123 (-3.00 a seed, se 1.86), 1.8 px wide (kernel kept) 96, 99, 99 against 98, 96, 96, 2.4 px wide (kernel kept) 98, 99, 100 against 99, 99, 98, and 1.8 px on 1.2 px noise (kernel kept) 687 against 695 (-2.67 a seed, se 2.19), within the 4-stars-a-frame kernel-gates spread owed by the gates-levels unit.
    - Raise only (whichever has more false at 4.75 raised in 0.25 steps): 0.6 px 2150 against 2179 (-3.62 a seed, se 1.92), crowded 4855 against 5062 (-69.00 a seed, se 20.66), 0.7 px 2037 against 2123 (-10.75 a seed, se 2.16), 0.8 px 2103 against 2110 (-0.88 a seed, se 1.69), 1.4 px wide 97, 96, 96 against 96, 95, 95, 1.8 px wide 92, 93, 94 against 98, 96, 96, 2.4 px wide 98, 99, 100 against 99, 99, 98, and 1.8 px on 1.2 px noise 676 against 695 (-6.33 a seed).
  - Where the noise differs across a frame, one covariance is the frame's average: half white and half correlated, the white half found 158 stars against main's 177.
  - Near a bright glow both detectors make false detections.
  - Positions are measured from the original pixels, no worse than main's on built stars and against the catalog on a crowded real field.
- **Deferred follow-up units** [open]:
  - `realsky-4c` [open]: choosing a per-frame threshold so the negative image stays within `FALSE_TARGET_PER_MP` where doing so loses no calibrated photo.
  - `realsky-4d` [open]: a false-detection estimate that real photos (with dark dust lanes and crowded starlight) can confirm, and whether `"noise_tail"` or negative-image figures enter `quality.detection` or user-facing warnings.
  - `gates-levels` [open]: setting `_measure_peak_photometry`'s cardinal-neighbour tests (`0.65` and `1.25` pixel noises, inherited from main) from the stated target in the statistic's own noise, which owns the <= 4 stars/frame kernel-gates spread at `0.7 px`, `1.8 px`, `2.4 px` and `corr_1.2_wide`, the `0.8 px` exception ceiling, and the two faintest fifths of `white 8-bit`.

---

## 2. Four Explicit Coordinate Systems

1. **Image Pixel Coordinates:** $p = (u, v) \in [0, W) \times [0, H)$ at canonical frame timestamp $t_{\text{media}}$.
2. **Undistorted Camera Ray:** Unit vector $\hat{r}_{\text{cam}}(u, v) \in S^2$ after inverting focal scale and radial polynomial lens distortion ($k_1$, $k_2$; the camera model has no SIP terms):
   $$x_n = \frac{u - c_x}{f}, \quad y_n = \frac{v - c_y}{f}, \quad r^2 = x_n^2 + y_n^2$$
   $$x_d = x_n(1 + k_1 r^2 + k_2 r^4), \quad y_d = y_n(1 + k_1 r^2 + k_2 r^4)$$
3. **Celestial Direction (ICRS / J2000 / Epoch $T$):** Right ascension $\alpha$ and declination $\delta$ mapped via gnomonic (TAN) projection and tangent-plane CD matrix around reference point $(\alpha_0, \delta_0)$:
   $$\hat{r}_{\text{sky}}(\alpha, \delta) = \begin{bmatrix} \cos\delta \cos\alpha \\ \cos\delta \sin\alpha \\ \sin\delta \end{bmatrix}$$
4. **Cartesian Scientific Space (Parsecs, Floating Origin):**
   $$\mathbf{X}_i(T) = d_i \cdot \hat{r}_{\text{sky}}(\alpha_i(T), \delta_i(T)), \quad \mathbf{X}_{\text{local}, i} = \mathbf{X}_i(T) - \mathbf{C}_{\text{origin}}$$
   where $d_i$ is the Bailer-Jones median distance estimate (in parsecs) with confidence interval $[d_{\text{lower}}, d_{\text{upper}}]$, and $\mathbf{C}_{\text{origin}} \in \mathbb{R}^3$ is stored in IEEE-754 double precision (`Float64Array`) before subtracting to form camera-relative `Float32Array` GPU coordinates.

---

## 3. Anchor-Locked Signature Transition Math

During **Phase A (`Photo to Registered Sky`)**, for a selected star $P$ at initial normalized device coordinates $\text{NDC}_0 = (s_x, s_y) \in [-1, 1]^2$:
- Orientation interpolates via unit quaternion SLERP: $q(t) = \text{slerp}(q_0, q_1, \sigma(t))$.
- Field of view interpolates in logarithmic tangent half-angle space so apparent zoom rate is constant:
  $$\theta(t) = 2 \arctan\left( \exp\left( (1 - \sigma(t)) \ln\tan\frac{\theta_0}{2} + \sigma(t) \ln\tan\frac{\theta_1}{2} \right) \right)$$
- Off-axis frustum shift $(\Delta_x(t), \Delta_y(t))$ is solved at each frame $t \in [0, 1]$ so that:
  $$\Pi(q(t), \theta(t), \Delta(t), \hat{r}_{\text{sky}}(P)) \equiv \text{NDC}_0$$
  preserving the anchor star's screen coordinate to within $< 10^{-6}\text{ NDC}$ ($< 0.01\text{ px}$; `anchorDriftPx` in `packages/camera-rig/src/projection3d.ts` measures the IEEE-754 floating-point round-trip residual of `rawNdc + (anchorTargetNdc - rawNdc)`).
