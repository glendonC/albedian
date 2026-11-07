---
inclusion: fileMatch
fileMatchPattern: "packages/**"
---

# Architecture & Domain Contract Steering

- Preserve all 14 core domain entities as distinct typed schemas in `@albedian/contracts`:
  `ObservationSession`, `SourceAsset`, `MediaVariant`, `ProcessingGraph`, `ProcessingAttempt`, `ObservationSolution`, `CelestialFinding`, `CatalogReference`, `ScientificEvidence`, `SpatialViewState`, `CameraCheckpoint`, `VoiceAction`, `NavigationHistory`, `ExportArtifact`.
- Do not collapse media DAG nodes, astronomical findings, and 3D scene objects into a single generic node type.
- Coordinate application state through the 4+1 orthogonal state machines (`Media`, `Restoration`, `Calibration`, `Navigation`, plus `Voice`) in `@albedian/observation`.
- Enforce `expectedViewRevision` on all `VoiceAction` / `AlbedianAction` dispatches so stale asynchronous results never overwrite newer user navigation. The rejection MUST be bitwise-clean: no field of `ObservationSession` may be mutated when a stale-revision action is discarded (`reason: 'stale_revision'`).
- Handle `history.back` partial fractions defensively: chaining partial backs with fractions `f₁, f₂` MUST NOT compound to exceed the inter-checkpoint distance (no overshoot beyond the prior checkpoint).
- Omit unmeasured media metrics (`peakMemoryBytes`, `pointSourceRetentionRatio`, `faceRegionStabilityScore`, `temporalFlickerIndex`, `audioSyncOffsetMs`) rather than substituting constants, and fail closed in `validateMediaTimelineSync` when a source with audio omits `audioSyncOffsetMs`.
- Leave `ObservationSolution.observedAtUTC`, `ObservationSolution.observerLocation`, and `SpatialViewState.observationEpochISO` absent when the source recording has no verified capture timestamp or location, reject astronomical year `0` in `SpaceEpoch.yearCE` and both voice parsers, and cap `VoiceCommandRequest.utterance` at 2,000 UTF-16 code units without echoing the raw utterance on validation errors.
