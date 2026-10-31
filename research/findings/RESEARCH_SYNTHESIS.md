# Albedian Research Synthesis & Evidence Classification

**Date:** 2026-10-31  
**Source Documents Preserved Intact:**
1. [`research/originals/Frontier_Media_Restoration_Research_2026-10-31.md`](../originals/Frontier_Media_Restoration_Research_2026-10-31.md)
2. [`research/originals/Observable_Universe_Research_Extension_2026-10-31.md`](../originals/Observable_Universe_Research_Extension_2026-10-31.md)
3. [`research/originals/Albedian_Spatial_Experience_Motion_Voice_Research_2026-10-31.md`](../originals/Albedian_Spatial_Experience_Motion_Voice_Research_2026-10-31.md)

---

## 1. Epistemic Distinction of Research Findings

To prevent conflating literature findings with product implementation, every capability across the three reports is categorized into five states:

| Category | Code | Definition |
|---|---|---|
| **Verified External Capability** | `[V]` | Confirmed by peer-reviewed literature, official documentation, or public open-source repositories. |
| **Engineering Inference** | `[I]` | Technically reasoned synthesis across established primitives; requires local measurement. |
| **Proposed Design** | `[D]` | Deliberate product, state-machine, visual, or API contract choice for Albedian. |
| **Unresolved Technical Question** | `[U]` | Empirical performance, solve-rate, usability, or hardware question requiring falsifiable experimentation. |
| **Locally Implemented & Tested** | `[L]` | Verified by executable code and automated tests in this repository. |

---

## 2. Reconciled Findings Across the Three Reports

### 2.1 Media Restoration (`Frontier_Media_Restoration_Research_2026-10-31.md`)
- **`[V]` Physical Imaging Limits:** Shot noise scales as $\sqrt{N}$; combining $N$ independent aligned observations of a static region improves SNR up to $\sqrt{N}$ subject to read noise, quantization, ISP smoothing, and codec compression. Pixels clipped at digital black contain no recoverable photon signal.
- **`[V]` Model Transfer Limits:** RAW-domain models (*Learning to See in the Dark*, AIM 2025 RAW) do not transfer directly to post-ISP 8-bit H.264/HEVC consumer MP4s. Single-image models (*DarkIR*) risk temporal flicker on video without temporal consistency constraints.
- **`[D]` Core Computing Primitive — Media Experiment Runtime:** Content-addressed execution system transforming an immutable `SourceAsset` and a restoration goal into versioned `ProcessingAttempt` branches via a typed `ProcessingGraph` DAG, preserving original audio tracks and producing reproducible `ExportArtifact` manifests.
- **`[D]` UI Architecture:** Hybrid media-first workspace (~75–80% viewport dedicated to the recording) with a compact bottom attempt strip and an on-demand `Processing Graph` overlay. Permanent node-graph defaults are rejected.

### 2.2 Astronomical Calibration & Observable Universe (`Observable_Universe_Research_Extension_2026-10-31.md`)
- **`[V]` Existing Substitutes:** Standalone photo plate-solving (SkySafari 8, Sideris, Plate Solver, Astrometry.net) and 3D catalog flight (WorldWide Telescope, Gaia Sky, NASA Eyes) already exist. Albedian's differentiation must come from **continuous, evidence-aware integration** from the user's personal recording (including video/audio and restoration attempts) into calibrated sky context and back.
- **`[V]` Late-2026 Infrastructure Facts:**
  - ESA Gaia DR4 is scheduled for December 2, 2026 (two days before the Kiroween deadline); Albedian must pin a **Gaia DR3 / Hipparcos bright-star catalog snapshot** rather than depending on DR4.
  - CelesTrak catalog IDs exceeded 5-digit NORAD numbers in July 2026; satellite elements must use modern OMM/GP formats rather than legacy fixed-width TLEs, and satellite identification remains a candidate-ranking investigation (`P2`) rather than a guaranteed ID.
- **`[D]` Core Scientific Primitive — Observation Solution:** A versioned, independently verified mapping (`ObservationSolution`) alongside the Media Experiment Runtime containing source detections, WCS calibration, held-out inlier residuals, catalog matches (`CelestialFinding`), and ephemerides.
- **`[D]` Four Classes of Scientific Truth:** `observed`, `catalog_matched`, `predicted`, and `simulated`. Plus explicit diagnostic statuses: `calibrated`, `predicted_only`, `unresolved`, and `ambiguous`.

### 2.3 Spatial Experience, Motion & Voice (`Albedian_Spatial_Experience_Motion_Voice_Research_2026-10-31.md`)
- **`[D]` Orthogonal Session State Map:** Modeling the application as one enum fails when restoration runs concurrently with sky inspection. `ObservationSession` coordinates four orthogonal state machines (`Media`, `Restoration`, `Calibration`, `Navigation`) plus an independent `Voice` machine.
- **`[I]` + `[D]` Signature Two-Phase Transition:**
  - **Phase A (`Photo → Registered Sky`):** Selected star centroid $p_0 = (u, v)$ is locked in screen space via off-axis frustum shift while camera orientation SLERPs and FOV interpolates in $\log(\tan(\text{FOV}/2))$ space. Zero 3D parallax is introduced on foreground terrain.
  - **Phase B (`Registered Sky → 3D Catalog Geometry`):** Explicit disclosure (`Catalog Geometry · Distance Estimates`) marks the boundary where external Gaia/Hipparcos 3D coordinates and Bailer-Jones distance intervals introduce true stellar parallax around a double-precision floating origin.
- **`[D]` Voice as Direct State Manipulation:** Speech or typed commands resolve through `ActionContext` (binding deictic pronouns like *"that one"*, *"there"*, *"back"* with `expectedViewRevision` guards) into validated `AlbedianAction` union payloads.

---

## 3. Actionable Engineering Requirements

1. **14 First-Class Domain Entities:** Implemented with strict runtime schemas (`Zod` in TypeScript, `Pydantic` in Python) in `packages/contracts` and `services/`.
2. **Independent WCS Verification Gate:** Require $\ge 6$ spatially distributed inliers and held-out median pixel residual $\le 3.0\text{ px}$ (at 2K canonical scale) before transitioning `Calibration` to `calibrated`.
3. **Anti-Circularity Restoration Rule:** Never inject catalog-predicted stars into media restoration outputs. Freeze source-only detections before evaluating whether a restoration branch preserves point-source SNR.
4. **7 Property-Based Test Suites:** Enforce mathematical and state invariants across Camera Projection, Navigation Checkpoints, Evidence Classification, Processing DAGs, Voice Action Revisions, Media Synchronization, and Scientific Coordinates.
