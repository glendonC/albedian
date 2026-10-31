# Tasks: Observation Session & Source Lifecycle

- [x] 1. Define `ObservationSession`, `SourceAsset`, `CameraCheckpoint`, and `NavigationHistory` schemas in `packages/contracts`.
- [x] 2. Implement pure session reducer and checkpoint stack in `packages/observation`.
- [x] 3. Implement audio pause-on-departure and exact PTS restoration in `packages/observation`.
- [x] 4. Verify checkpoint reversibility with `fast-check` property-based tests (`PBT-NAV-1`).
