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

1. `ObservationSession` — Root aggregate coordinating the 4+1 orthogonal state machines, active selection, observer context, and history.
2. `SourceAsset` — Immutable content-addressed original photograph or video (`sha256`, dimensions, codec, FPS, audio stream metadata).
3. `MediaVariant` — Derivative media asset produced by a `ProcessingAttempt`, aligned to the canonical `SourceAsset` timeline.
4. `ProcessingGraph` — Acyclic, typed DAG of deterministic and prior-assisted media operations (`tone_curve`, `spatial_denoise`, `temporal_stack`, `star_protect_merge`, `region_mask`).
5. `ProcessingAttempt` — Versioned execution instance of a `ProcessingGraph` with real timing, memory, and quality evaluation metrics.
6. `ObservationSolution` — Versioned astrometric calibration record (`calibrated`, `predicted_only`, `unresolved`, `ambiguous`) with WCS parameters and held-out pixel residuals.
7. `CelestialFinding` — A detected, catalog-matched, predicted, or simulated astronomical object with stable ID and `EvidenceClass`.
   An `observed` finding has a sky position only when its solution's WCS passed held-out verification; without one it is located by its pixel `sourceDetection` alone.
8. `CatalogReference` — Pinned external catalog record (`Gaia DR3`, `Hipparcos`, `JPL Horizons`) with epoch, astrometric parameters, and proper motion.
9. `ScientificEvidence` — Audit trail linking pixel measurements, WCS residuals, Bailer-Jones distance intervals, and explicit limitations.
10. `SpatialViewState` — Current observer origin (parsecs), simulation epoch, camera pose, scale policy, and active overlays.
11. `CameraCheckpoint` — Immutable snapshot of media time, playback state, audio gain, crop, selected finding, and camera rig for exact restoration.
12. `VoiceAction` — Schema-validated `AlbedianAction` command bound to `ActionContext` and `expectedViewRevision`.
13. `NavigationHistory` — Reversible stack of `CameraCheckpoint` snapshots supporting full (`checkpoint`) and continuous (`partial`) backtracking.
14. `ExportArtifact` — Verified output media file and machine-readable provenance manifest (`sourceSha256`, `outputSha256`, audio sync verification, WCS & catalog citations).

---

## 3. Architecture Decision Summary (ADRs)

- **ADR-0001 — Observation-Centered Modular Monolith:** TypeScript monorepo (`pnpm` workspaces) + unified Python FastAPI scientific/media service (`services/api.py`) sharing strict Zod/Pydantic schemas (`packages/contracts`).
- **ADR-0002 — Four Classes of Scientific Evidence (`EvidenceClass`):** Every finding and marker is typed as `observed`, `catalog_matched`, `predicted`, or `simulated` (`packages/evidence`).
- **ADR-0003 — Hybrid 3D Spatial Engine (`@sparkjsdev/spark` + Custom WebGL2 Shaders):** Combines anisotropic 3D Gaussian Splats (`@sparkjsdev/spark`) for volumetric nebulae and the Local Bubble with analytical-derivative GPU ray-marching, pre-baked Bruneton atmospheric LUTs, and relativistic Schwarzschild geodesic shaders (`apps/web/src/spatial/SparkPortalRenderer.ts`).
- **ADR-0004 — Context-Bound Voice Direct Manipulation:** Spoken and typed intents resolve deictic pronouns (`"that one"`, `"take me there"`, `"go back"`) against `ActionContext` and `expectedViewRevision`.

---

## 4. Engineering Standards, Verification & Catalog Attributions

- **Verification Gates:** All changes require passing `pnpm typecheck`, `pnpm test` (Vitest + `fast-check` property-based tests), and `uv run pytest` (Python + `hypothesis` property-based tests).
- **Third-Party Astronomical Data & Licenses:**
  - **ESA Gaia DR3 (`I/355/gaiadr3`) & Bailer-Jones et al. (`I/352`):** Credit: ESA/Gaia/DPAC (CC BY-SA 3.0 IGO).
  - **CDS Hipparcos (`I/239`) & SIMBAD:** Credit: Centre de Données astronomiques de Strasbourg.
  - **NASA LRO LOLA & JPL Horizons Ephemerides:** Public domain U.S. government scientific records.
  - **Precomputed Atmospheric Scattering Formulation:** Adapted from Eric Bruneton (2017/2020, BSD-3-Clause).
