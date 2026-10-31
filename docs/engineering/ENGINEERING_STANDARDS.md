# Engineering Standards, Testing Strategy, Voice & Audio Policies

## 1. Engineering Standards

- **Strict Type Safety:** TypeScript `strict: true`, `noUncheckedIndexedAccess: true`; Python 3.14 type annotations validated alongside Pydantic models.
- **Runtime Boundary Validation:** All API inputs, job events, and LLM/voice action proposals pass through Zod (`@albedian/contracts`) or Pydantic (`services/`) schemas before execution.
- **Deterministic Reproducibility:** Every media transformation and astrometric solution logs input SHA-256 hashes, algorithm/solver versions, catalog release IDs (`Gaia DR3`), and execution durations.

---

## 2. Seven Mandatory Property-Based Testing (PBT) Domains

Implemented with `fast-check` (TypeScript) and `hypothesis` (Python):

1. **Camera Projection Round-Trip (`packages/camera-rig`):** For all valid sky coordinates $(\alpha, \delta)$ within the camera field of view and supported distortion domain ($|k_1|, |k_2| \le 0.15$), `worldToPixel(pixelToWorld(u, v))` and `pixelToWorld(worldToPixel(ra, dec))` invert within $< 10^{-4}\text{ px}$ / $< 10^{-3}\text{ arcsec}$, and anchor screen-space coordinates remain invariant ($< 0.05\text{ px}$) across Phase A interpolation $t \in [0, 1]$.
2. **Navigation Checkpoint Reversibility (`packages/observation`):** Any arbitrary sequence of `sky_registered`, `space` travel, viewpoint orbits, and epoch changes followed by `source.return` restores the exact saved `CameraCheckpoint` (`sourceAssetId`, `sourceTimeSec`, `audioGain`, `cropTransform`, `selectedFindingId`, `mediaVariantId`).
3. **Evidence Epistemic Monotonicity (`packages/evidence`):** No sequence of UI, voice, or renderer operations can promote a `predicted` or `simulated` finding to `observed` or `catalog_matched` without a valid pixel detection and verified WCS solution proof.
4. **Processing DAG Integrity (`packages/contracts` & `services/media`):** Every valid `ProcessingGraph` is acyclic, topological sort respects all edge dependencies, cycle injection is deterministically rejected, cache keys change monotonically on any parameter/mask/input hash mutation, and `SourceAsset` node hashes remain immutable.
5. **Voice Action Revision Safety (`packages/observation` & `services/voice`):** Any `AlbedianAction` carrying a stale `expectedViewRevision < currentViewRevision` or ambiguous target reference is deterministically rejected without mutating selection, camera pose, or running jobs.
6. **Media Timeline & Audio Synchronization (`services/media` & `packages/observation`):** Across all `MediaVariant` comparisons and remux exports, frame timestamps remain monotonic and audio stream offset drift stays within $\le 1\text{ frame}$ ($\le 33.3\text{ ms}$ at 30 fps) with bit-identical audio preservation on stream-copy exports.
7. **Scientific Coordinate & Distance Bounds (`services/science`):** ICRS $(\alpha, \delta) \leftrightarrow \text{AltAz}$ and parsec Cartesian transformations preserve unit norms ($\|\hat{r}\| = 1 \pm 10^{-9}$), valid angular bounds ($\alpha \in [0, 360^\circ), \delta \in [-90^\circ, 90^\circ]$), and strictly positive finite Bailer-Jones distance intervals ($0 < d_{\text{lower}} \le d_{\text{median}} \le d_{\text{upper}}$).

---

## 3. Voice & Audio Policy

- **Push-to-Talk & Text Parity:** Microphone is never active by default. Every spoken command has 100% typed command parity in the command bar.
- **Web Audio Bus Topology:**
  - `OriginalMediaSource → SourceGain → MasterGain`
  - `VoiceFeedbackSource → VoiceGain → MasterGain`
  - `NavigationCueSource → UIGain → MasterGain`
- **Departure Pause Rule:** Entering `space` mode pauses source video/audio playback and records exact `sourceTimeSec` and `audioGain` in `CameraCheckpoint`. Returning via `Back to Moment` restores the exact audio timestamp. Exported videos always preserve the untouched original audio stream.
