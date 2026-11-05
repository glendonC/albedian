# Albedian Reproducible Technical Experiment Registry

Each unresolved technical question identified in the foundational research is tracked here as a falsifiable experiment with explicit hypotheses, datasets/fixtures, baselines, metrics, failure conditions, and reproduction commands.

---

## Summary Matrix

| ID | Title | Priority | Domain | Status | Reproduction Script / Test |
|---|---|---|---|---|---|
| **EXP-01** | Consumer Photo Plate Solving & Independent WCS Verification | P0 | Science | Synthetic only, narrower than stated | `uv run pytest tests/integration/test_science_experiments.py -k exp01` |
| **EXP-02** | Low-Light Video Star Detection & Motion-Compensated Temporal Stacking | P0 | Science / Media | Synthetic only, narrower than stated | `uv run pytest tests/integration/test_science_experiments.py -k exp02` |
| **EXP-03** | Catalog Alignment & False-Match Rejection Under Injected Artifacts | P0 | Science | Synthetic only, narrower than stated | `uv run pytest tests/integration/test_science_experiments.py -k exp03` |
| **EXP-04** | Astronomy-Aware Point Protection vs. Conventional Restoration | P0 | Media / Science | Implemented & Verified | `uv run pytest tests/integration/test_science_experiments.py -k exp04` |
| **EXP-05** | Image-to-Sky Camera Reprojection & Anchor Screen-Space Stability (X1) | P0 | Spatial | Not tested: its check cannot fail | `pnpm vitest run packages/camera-rig/src/__tests__/experiments.test.ts -t EXP-05` |
| **EXP-06** | Image-to-3D Continuity, Floating-Origin Precision & Exact Checkpoint Return (X3) | P0 | Spatial | Partly tested: its drift check cannot fail | `pnpm vitest run packages/camera-rig/src/__tests__/experiments.test.ts -t EXP-06` |
| **EXP-07** | Renderer Adapter Benchmark: WWT WebGL Adapter vs. Custom Three.js/WebGL Rig (X2) | P0 | Spatial | Not measured: no renderer runs | `pnpm vitest run packages/camera-rig/src/__tests__/experiments.test.ts -t EXP-07` |
| **EXP-08** | Stellar Navigation Accuracy & Proper-Motion Epoch Propagation (X8) | P1 | Science / Spatial | Implemented & Verified | `uv run pytest tests/integration/test_science_experiments.py -k exp08` |
| **EXP-09** | Voice Deictic Context Resolution, Ambiguity Rejection & Barge-In Cancellation (X7) | P1 | Voice | Implemented & Verified | `pnpm vitest run packages/observation/src/__tests__/voice-experiments.test.ts` |
| **EXP-10** | Video Restoration DAG Execution & Audio-Preserving Remux Export (E6) | P0 | Media | Implemented & Verified | `uv run pytest tests/integration/test_science_experiments.py -k exp10` |
| **EXP-11** | Browser Frame Pacing & Memory Under 1k / 10k / 50k Star Catalog Workloads (X9) | P1 | Spatial / Perf | Partly measured: Node frame preparation only | `pnpm vitest run packages/camera-rig/src/__tests__/experiments.test.ts -t EXP-11` |
| **EXP-12** | Epistemic Boundary Comprehension: Observed vs. Catalog Matched vs. Predicted vs. Simulated (X5) | P1 | Experience | Not run: needs a study with testers | `pnpm vitest run packages/evidence/src/__tests__/evidence.test.ts` |

### What each relabelled test checks today

- **EXP-01:** the test renders the solver's own 18 stars at 960 x 540 through its own WCS, and the solver matches triplets, not quads; a calibration needs 4 training and 6 held-out matches, so 8 sources can never calibrate. Nothing is solved against Gaia DR3 or Hipparcos on a 1920 x 1080 consumer frame.
- **EXP-02:** the frames get Gaussian read noise only (`services/science/detector.py`); there is no Poisson shot noise.
- **EXP-03:** the test injects 12 single hot pixels; there are no streaks and no magnitude check.
- **EXP-05 and EXP-06:** `anchorDriftPx` is float rounding for any camera (`packages/camera-rig/src/projection3d.ts`), so a drift check passes whatever the rig does.
- **EXP-07:** no WWT adapter or renderer runs; the test compares two computed drift values, and the recommended engine is a fixed string. The ADR it cites does not exist.
- **EXP-11:** the test times 50,000-star frame preparation in Node; it measures no browser frame pacing and no memory.
- **EXP-12:** the comprehension study needs testers; the test checks only that four badge descriptors differ.

---

## Detailed Experiment Specifications

### EXP-01: Consumer Photo Plate Solving & Independent WCS Verification
- **Hypothesis:** A consumer night-sky scene with $\ge 8$ detectable point sources above local background noise can be blind/hint-solved via triangle matching against the Gaia DR3 / Hipparcos bright-star catalog and verified on held-out detections with median residual $\le 2.0\text{ px}$, while near-black/featureless scenes deterministically resolve to `unresolved` with zero false solves.
- **Fixtures:** Golden starfield fixture (`Orion / Winter Hexagon` wide-angle consumer frame, $1920 \times 1080$, known gnomonic + radial distortion $k_1$) and negative fixtures (clipped black frame, random hot-pixel noise frame).
- **Baseline:** Nearest-neighbor unverified matching without held-out split.
- **Measurement:** Inlier count, held-out median and p95 pixel residual (px), angular residual (arcsec), false-positive rate on negative fixtures.
- **Failure Condition:** Any negative/noise fixture marked `calibrated`, or held-out median residual $> 3.0\text{ px}$ on valid starfields.

### EXP-02: Low-Light Video Star Detection & Temporal Aggregation
- **Hypothesis:** Aligning and median/mean-stacking $N = 8\text{–}16$ low-SNR frames with sub-pixel translation/rotation registration increases point-source SNR by $\approx 2.2\times\text{–}3.5\times$ over a single frame and enables valid WCS calibration where single frames fail.
- **Fixtures:** Multi-frame synthetic consumer low-light video sequence with Poisson shot noise, read noise, and slight handheld drift + foreground horizon mask.
- **Baseline:** Single highest-SNR frame star extraction.

### EXP-03: Catalog Alignment & False-Match Rejection
- **Hypothesis:** Injecting 15 synthetic hot pixels / airplane streaks into a valid starfield does not corrupt the WCS solution or produce false `catalog_matched` labels when 3-sigma residual gating and magnitude consistency checks are enforced.

### EXP-04: Astronomy-Aware Point Protection vs. Conventional Restoration
- **Hypothesis:** Conventional aggressive spatial denoising (`gaussian` / `bilateral` without point protection) attenuates faint stars by $> 35\%$ peak flux, whereas Albedian's star-preserving adaptive restoration branch retains $\ge 90\%$ of source-detected point-source SNR while reducing background sky variance by $\ge 50\%$.

### EXP-05: Image-to-Sky Camera Reprojection & Anchor Screen-Space Stability
- **Hypothesis:** Off-axis frustum shift coupled with quaternion SLERP and $\log(\tan(\text{FOV}/2))$ interpolation keeps the selected anchor star within $\le 0.5\text{ CSS px}$ of its starting screen coordinate throughout the entire $t \in [0, 1]$ `Photo → Registered Sky` transition.

### EXP-06: Image-to-3D Continuity, Floating-Origin Precision & Exact Checkpoint Return
- **Hypothesis:** Rebasing double-precision parsec coordinates (`Float64Array`) to a camera-relative `Float32Array` origin keeps relative positional representation error $< 10^{-5}\text{ pc}$ during interstellar travel up to $1000\text{ pc}$, and popping `CameraCheckpoint` restores `sourceTimeSec`, `cropTransform`, `audioGain`, and `selectedFindingId` with zero loss.

### EXP-07: Renderer Adapter Benchmark (WWT Adapter vs. Custom Three.js/WebGL Rig)
- **Hypothesis:** While WorldWide Telescope WebGL Engine excels at multi-wavelength survey tiling, it does not expose off-axis frustum anchor locking or per-frame SIP polynomial distortion blending. A custom Three.js/WebGL camera rig achieves $0.00\text{ px}$ anchor drift during Phase A handoff and sub-millisecond frame preparation for 10,000 stars, while retaining Aladin/WWT deep-link compatibility for survey inspection.
- **Decision Recorded in `ADR-0003`:** Use the custom WCS-locked Three.js/WebGL camera rig as the primary Phase A/B transition engine and provide an explicit Survey Layer adapter for multi-wavelength inspection.
