# ADR-0001: Modular Monolith Architecture & The Three Primary Computing Systems

- **Status:** Accepted
- **Date:** 2026-10-31
- **Deciders:** Principal Orchestrator (`architecture`)

## Context

Albedian combines three computationally distinct domains:
1. **Media Restoration:** Content-addressed video/photo probing, exposure/tone/denoise DAG execution, region masking, and audio-preserving export.
2. **Scientific Astrometry:** Star point-source extraction, blind/hint-assisted WCS plate solving, independent held-out residual verification, Gaia DR3 / Hipparcos catalog matching, and Solar System ephemerides.
3. **Spatial & Voice Experience:** Continuous source-to-sky-to-3D camera transitions, 4+1 orthogonal state machines, and context-bound voice/typed direct manipulation.

Building three disconnected applications or premature microservice clusters introduces schema drift, network serialization overhead, and broken state continuity.

## Decision

We adopt a **Modular Monolith** with strict package and service boundaries coordinated through shared, versioned schemas (`packages/contracts`):

1. **Three Primary Computing Systems:**
   - **Media Experiment Runtime (`services/media`):** Owns immutable `SourceAsset`, `ProcessingGraph`, `ProcessingAttempt`, `MediaVariant`, and `ExportArtifact`.
   - **Observation Solution Service (`services/science`):** Owns `ObservationSolution`, `CelestialFinding`, `CatalogReference`, and `ScientificEvidence`.
   - **Spatial Experience Controller (`packages/observation`, `packages/camera-rig`, `apps/web`):** Owns `ObservationSession`, `SpatialViewState`, `CameraCheckpoint`, `NavigationHistory`, and `VoiceAction`.
2. **14 First-Class Domain Entities:** Never collapse media nodes, astronomical findings, and 3D scene objects into a generic `Node` or `Entity` type.
3. **Unified Python Backend (`services/api.py`):** Exposes `/api/media/*`, `/api/science/*`, and `/api/voice/*` from a single FastAPI process backed by content-addressed local storage and SQLite/JSON manifest persistence, with zero external GPU or paid API requirement for deterministic baseline operation.

## Consequences

- **Positive:** Atomic cross-domain transactions, shared content-addressed storage, instant local reproducibility for judges and developers, and clean extraction seams if individual workers scale independently later.
- **Negative:** Requires strict linting and contract tests (`scripts/verify-contracts.mjs`) so frontend and backend schemas never drift.
