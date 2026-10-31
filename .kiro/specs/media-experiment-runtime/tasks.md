# Tasks: Media Experiment Runtime

- [x] 1. Define `ProcessingGraph`, `ProcessingAttempt`, and `MediaVariant` contracts in `packages/contracts`.
- [x] 2. Implement DAG validator, topological sorter, and content-addressed cache key generator.
- [x] 3. Implement Python media restoration workers (`services/media/runtime.py`) with star-point protection and foreground mask support.
- [x] 4. Verify DAG invariants and timeline synchronization via `fast-check` and `hypothesis` property-based tests.
