# Design: Media Experiment Runtime

## Architecture
- **Service:** `services/media/runtime.py`
- **Contracts:** `ProcessingGraph`, `ProcessingNode`, `ProcessingAttempt`, `MediaVariant` in `@albedian/contracts`

## Pipeline Stages
1. **Ingest & Probe:** Compute SHA-256, inspect dimensions, bit depth, luminance histogram, noise floor estimate, and audio streams.
2. **DAG Validation & Cache Lookup:** Verify DAG acyclicity and compute SHA-256 node cache keys.
3. **Deterministic Workers:**
   - **Natural Branch:** Shadow/midtone logarithmic curve + chroma/luma bilateral denoise + star-centroid protection mask.
   - **Temporal Branch:** Multi-frame motion-compensated alignment + robust median/mean sky stack + foreground protection mask.
4. **Evaluation:** Compute SNR improvement in static shadow regions, point-source retention ratio, and false-point artifact audit.

## Correctness Properties
- **Property PBT-DAG-1:** Every accepted `ProcessingGraph` is a valid DAG; any cycle is rejected; mutating any node parameter invalidates only its transitive descendants; `sourceSha256` is invariant.
