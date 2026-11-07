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
4. **Evaluation & Telemetry:** Record real `startedAtUTC` and `completedAtUTC` timestamps around execution, measure worker-thread CPU time (`cpuMs`, via `time.thread_time()`), and compute SNR improvement in static shadow regions and false-point artifact audit; omit unmeasured fields (`peakMemoryBytes`, `pointSourceRetentionRatio`, `faceRegionStabilityScore`, `temporalFlickerIndex`, `audioSyncOffsetMs`) rather than substituting constants.

## Correctness Properties
- **Property PBT-DAG-1:** Every accepted `ProcessingGraph` is a valid DAG; any cycle is rejected; mutating any node parameter invalidates only its transitive descendants; `sourceSha256` is invariant.
