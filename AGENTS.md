# Albedian Agent Organization & Coordination Contract

**Product:** Albedian: *See (Beyond) the Night Sky.*  
**Architecture:** Observation-Centered Modular Monolith with Explicit Domain Contracts  

---

## 1. Core Non-Negotiables for All Agents

1. **Real Observation First (`SourceAsset` Immutability):** Never mutate, overwrite, or silently replace the original source image, video, timestamps, or audio stream. Every processed variant must trace to a content-addressed `SourceAsset` (`sha256`).
2. **Four Classes of Scientific Evidence:** Every finding, marker, and visualization must carry an explicit `EvidenceClass`:
   - `observed`: Measured directly from original pixels (centroid, flux, SNR, frame/time).
   - `catalog_matched`: Associated with an external astronomical catalog (`Gaia DR3`, `HIP`, `JPL`) only after an independently verified WCS calibration and held-out residual check.
   - `predicted`: Computed from ephemeris/catalog + time/location/pose assumptions; never rendered as if recovered from camera pixels.
   - `simulated`: Catalog-geometry 3D navigation, modeled bodies and scenes, alternative viewpoints, or historical epoch models; must display explicit disclosure that the layer or view is drawn or computed by the app and not measured in the source recording, with each layer naming its own source and limits.
3. **No Fake Computation or AI Theater:** Never animate fake progress percentages, fake star detections, or decorative agent cursors. Progress indicators must be driven by observable job denominators or show elapsed time in an indeterminate state.
4. **Voice Is Direct Manipulation:** LLMs translate natural language into schema-validated `AlbedianAction` payloads (`packages/contracts`). Authoritative validators and deterministic domain services execute all scientific and media operations.
5. **Reversible Navigation:** Leaving the source recording for calibrated sky or 3D space saves an immutable `CameraCheckpoint` (`sourceAssetId`, `sourceTimeSec`, `playbackWasPlaying`, `audioGain`, `mediaVariantId`, `cropTransform`, `selectedFindingId`, `cameraRig`). `Back to Moment` restores that exact state.

---

## 2. Specialized Agent Workstreams & Ownership

| Agent Identifier | Responsibility Domain | Owned Directories | Primary Contracts & Deliverables |
|---|---|---|---|
| `architecture` | System boundaries, ADRs, cross-service schemas, integration verification | `packages/contracts/`, `packages/observation/`, `packages/evidence/`, `docs/architecture/` | TypeScript/Zod & Python Pydantic domain schemas, state machines, invariants |
| `science` | Astrometry, source extraction (`DAOStarFinder`/SEP/matched filter), blind & hint-assisted WCS solver, held-out verification, Gaia DR3 subset, ephemerides, Bailer-Jones distance priors | `services/science/`, `docs/science/`, `research/experiments/` | `ObservationSolution`, `CelestialFinding`, `CatalogReference`, `ScientificEvidence`, deterministic astrometry & ephemeris workers |
| `media` | Content-addressed ingestion, FFmpeg/OpenCV pipelines, exposure/tone/temporal denoise DAG, region masks, audio-preserving remux export | `services/media/` | `MediaExperimentRuntime`, `ProcessingGraph`, `ProcessingAttempt`, `MediaVariant`, `ExportArtifact` |
| `spatial` | Camera rig, WCS pixel↔world projection, off-axis anchor lock, quaternion SLERP, log-tan-half-FOV interpolation, floating-origin 3D starfield, WWT vs Three.js adapter benchmark | `packages/camera-rig/`, `apps/web/src/spatial/` | `ObservationCamera`, `SourceProjection`, `AngularSkyCamera`, `SpatialViewCamera`, `AnchorConstraint`, signature transition |
| `experience` | Observational instrument UI shell, design tokens, synchronized A/B wipe player, Web Audio bus, progressive disclosure inspectors, reduced-motion & keyboard accessibility | `packages/design-system/`, `apps/web/src/` | Media-first Reveal/Explore shell, markers by `EvidenceClass`, contextual inspectors |
| `voice` | Speech transport abstraction, deictic context binding (`that one`, `there`, `back`), revision-checked action validator, interruption & barge-in | `services/voice/` | `ActionContext`, `AlbedianAction` dispatcher, `ActionOutcome` validator |
| `verification` | Property-based tests (`fast-check` & `hypothesis`), golden fixtures, contract parity, E2E verification, Kiro hooks | `tests/`, `scripts/`, `.kiro/hooks/` | 7 PBT suites, deterministic astronomical & media fixtures, CI verification scripts |

---

## 3. Delegation & Integration Protocol

1. **Contracts First:** No parallel implementation begins on a cross-boundary feature until its types and runtime validators exist in `packages/contracts/`.
2. **Scoped File Ownership:** Subagents must only modify files within their assigned ownership scope. Cross-cutting contract changes must be escalated to the Principal Orchestrator.
3. **Verification Gate Before Merge:** Every work package must pass the six gates that CI runs (`pnpm typecheck`, `pnpm test`, `uv run pytest`, `pnpm verify:contracts`, `pnpm verify:docs` and `pnpm build`) before the Principal Orchestrator integrates it.
4. **Honest Reporting:** Distinguish **Verified external capability**, **Engineering inference**, **Proposed design**, **Unresolved technical question**, and **Locally implemented and tested capability** in all reports and documentation.
