# Albedian Architecture Overview

## 1. System Topology

```mermaid
flowchart TD
    subgraph Client["Browser Client (@albedian/web)"]
        Shell["Observational Instrument Shell (Reveal & Explore)"]
        Compare["Synchronized A/B Media & Web Audio Bus"]
        Spatial["WCS-Locked Camera Rig & 3D Starfield (@albedian/camera-rig)"]
        VoiceUI["Context-Bound Voice & Command Bar"]
        Store["ObservationSession Reducer (@albedian/observation)"]
    end

    subgraph Shared["Shared Domain Packages"]
        Contracts["@albedian/contracts (14 Entities + Zod Validators)"]
        Evidence["@albedian/evidence (4 Evidence Classes + Guards)"]
        Design["@albedian/design-system (Tokens, Markers, Motion)"]
    end

    subgraph Backend["Modular Monolith Service (services/api.py)"]
        MediaSvc["Media Experiment Runtime (services/media)"]
        ScienceSvc["Observation Solution Engine (services/science)"]
        VoiceSvc["Intent Parser & Action Validator (services/voice)"]
    end

    Shell --> Store
    Compare --> Store
    Spatial --> Store
    VoiceUI --> Store
    Store --> Contracts
    Store --> Evidence
    Shell --> Design
    Store <-->|REST / Event Stream| Backend
```

## 2. The 14 Core Domain Entities

Defined authoritatively in [`packages/contracts/src/index.ts`](../../packages/contracts/src/index.ts):

1. `ObservationSession`: Root aggregate coordinating the 4+1 orthogonal state machines, active selection, observer context, and history.
2. `SourceAsset`: Immutable content-addressed original photograph or video (`sha256`, dimensions, codec, FPS, audio stream metadata).
3. `MediaVariant`: Derivative media asset produced by a `ProcessingAttempt`, aligned to the canonical `SourceAsset` timeline (`audioSyncOffsetMs` is optional when not measured, and `validateMediaTimelineSync` fails closed when a source with audio omits `audioSyncOffsetMs`). [tested]
4. `ProcessingGraph`: Acyclic, typed DAG of deterministic and prior-assisted media operations (`tone_curve`, `spatial_denoise`, `temporal_stack`, `star_protect_merge`, `region_mask`).
5. `ProcessingAttempt`: Versioned execution instance of a `ProcessingGraph`, recording real `startedAtUTC` and `completedAtUTC` timestamps around execution, worker-thread CPU time (`cpuMs`, via `time.thread_time()`), and measured quality evaluation metrics; unmeasured fields (`peakMemoryBytes`, `pointSourceRetentionRatio`, `faceRegionStabilityScore`, `temporalFlickerIndex`) are omitted rather than filled with placeholder values (`services/media/runtime.py`). [tested]
6. `ObservationSolution`: Versioned astrometric calibration record (`calibrated`, `predicted_only`, `unresolved`, `ambiguous`) with WCS parameters and held-out pixel residuals; `observedAtUTC` and `observerLocation` are optional when a recording lacks a verified capture timestamp or GPS coordinates, and `/api/ingest/{sha256}/solve`, `/api/ingest/jobs/{job_id}`, and `/api/ingest/{sha256}/solution` execute `SolveJob` with real byte, frame, and candidate-triplet denominators. [tested] An optional `poseEstimate` states how an `approximate` solution's WCS was lined up from the Moon or the Sun and how far it may be off, as 2 to 27 swept WCS on that WCS's `crpixX` and `crpixY`; only an `approximate` solution with a WCS carries one, and no solver produces one yet. [tested]
7. `CelestialFinding`: A detected, catalog-matched, predicted, or simulated astronomical object with stable ID and `EvidenceClass`.
   An `observed` finding has a sky position only when its solution's WCS passed held-out verification; without one it is located by its pixel `sourceDetection` alone. A `catalog_matched` finding needs that verified WCS too.
8. `CatalogReference`: Pinned external catalog record (`Gaia DR3`, `Hipparcos`, `JPL Horizons`) with epoch, astrometric parameters, and proper motion.
9. `ScientificEvidence`: Audit trail linking pixel measurements, WCS residuals, Bailer-Jones distance intervals, and explicit limitations.
10. `SpatialViewState`: Current observer origin (parsecs), simulation epoch (`SpaceEpoch.yearCE`, rejecting astronomical year `0`), optional `observationEpochISO` when the source recording has no verified capture timestamp, camera pose, scale policy, and active overlays. [tested]
11. `CameraCheckpoint`: Immutable snapshot of media time, playback state, audio gain, crop, selected finding, and camera rig for exact restoration.
12. `VoiceAction`: Schema-validated `AlbedianAction` command bound to `ActionContext` and `expectedViewRevision`. `VoiceCommandRequest` caps `utterance` at 2,000 UTF-16 code units without echoing the raw utterance on validation failure, both parsers reject year `0`, `ui.overlay` is refused as `not_connected` until an overlay consumer exists, and predicted bodies are evaluated at `observedAtUTC`. [tested]
13. `NavigationHistory`: Reversible stack of `CameraCheckpoint` snapshots supporting full (`checkpoint`) and continuous (`partial`) backtracking.
14. `ExportArtifact`: Verified output media file and machine-readable provenance manifest (`sourceSha256`, `outputSha256`, audio stream copy via `-c:a copy` with `framemd5` packet PTS verification, WCS & catalog citations). [tested]

---

## 3. Architecture Decision Summary (ADRs)

- **ADR-0001 Observation-Centered Modular Monolith:** TypeScript monorepo (`pnpm` workspaces) + unified Python FastAPI scientific/media service (`services/api.py`) sharing strict Zod/Pydantic schemas (`packages/contracts`).
- **ADR-0002 Four Classes of Scientific Evidence (`EvidenceClass`):** Every finding and marker is typed as `observed`, `catalog_matched`, `predicted`, or `simulated` (`packages/evidence`).
- **ADR-0003 Hybrid 3D Spatial Engine (`@sparkjsdev/spark` + Custom WebGL2 Shaders):** Combines anisotropic 3D Gaussian splats (`@sparkjsdev/spark`) on a Local Bubble mesh of unrecorded origin with analytical-derivative GPU ray-marching, a pre-baked Bruneton atmospheric lookup table, and an approximate Schwarzschild bending of light around a hand-set disk (`apps/web/src/spatial/SparkPortalRenderer.ts`).
- **ADR-0004 Context-Bound Voice Direct Manipulation:** Spoken and typed intents resolve deictic pronouns (`"that one"`, `"take me there"`, `"go back"`) against `ActionContext` and `expectedViewRevision`.

---

## 4. Engineering Standards, Verification & Catalog Attributions

- **Verification Gates:** All changes require passing `pnpm typecheck`, `pnpm test` (Vitest + `fast-check` property-based tests), `uv run pytest` (Python + `hypothesis` property-based tests), `pnpm verify:contracts`, `pnpm verify:docs`, `pnpm verify:provenance`, and `pnpm build`.
- **Third-Party Astronomical Data & Licenses:**
  - **ESA Gaia DR3 (`I/355/gaiadr3`) & Bailer-Jones et al. (`I/352`):** Credit: ESA/Gaia/DPAC (CC BY-NC 3.0 IGO, as [docs/science/CATALOG_STAR_TILES.md](../science/CATALOG_STAR_TILES.md) records).
  - **ESA Hipparcos 2007 (van Leeuwen 2007, through the ESA archive):** Credit: ESA (CC BY-NC 3.0 IGO). SIMBAD is not queried.
  - **NASA LRO LOLA and LROC, through the NASA SVS CGI Moon Kit:** NASA media, not subject to copyright in the United States.
  - **Ephemerides:** the Standish and Williams (1992) approximate Keplerian elements (JPL Solar System Dynamics) for the planets and Meeus (1998) chapter 47 for the Moon. No finding uses JPL Horizons data; only the landing scene's baked Sun and Moon positions and check values (sub-observer and sub-solar points, pole and phase angles) come from it, through `scripts/plan_landing_moment.py` (the NASA/JPL Horizons API, ephemeris DE441), recorded in `apps/web/src/spatial/webgpu/cislunar/landingMoment.json`. The terms for Horizons' answers and for DE440 and DE441 are [open].
  - **Precomputed Atmospheric Scattering Formulation:** Adapted from Eric Bruneton (2017/2020, BSD-3-Clause).
