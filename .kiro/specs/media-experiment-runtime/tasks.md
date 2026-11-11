# Tasks: Media Experiment Runtime

- [x] 1. Define `ProcessingGraph`, `ProcessingAttempt`, and `MediaVariant` contracts in `packages/contracts`.
- [x] 2. Implement DAG validator, topological sorter, and content-addressed cache key generator.
- [ ] 3. Implement Python media restoration workers (`services/media/runtime.py`) with star-point protection and foreground mask support.
  - Unchecked 2026-11-11 after reading the code at `dcdbd65d`: the worker takes arrays held in memory and reduces them to one grey plane (`services/media/runtime.py:222-224`), reads no stored upload and writes no variant, its false point count gates nothing because every attempt is written `ready` (`services/media/models.py:108`, against REQ-MER-2.2), and the route cannot pass a foreground mask (`services/media/router.py:92-96`); spec `restoration-for-every-upload` covers it (REQ-RST-1, REQ-RST-4, REQ-RST-6).
- [ ] 4. Verify DAG invariants and timeline synchronization via `fast-check` and `hypothesis` property-based tests.
  - Unchecked 2026-11-11 after reading the code at `dcdbd65d`: the fast-check half exists (PBT-DAG-1 in `packages/contracts/src/__tests__/contracts.test.ts:64`, PBT-SYNC-1 in `packages/observation/src/__tests__/observation.test.ts:658`), but no hypothesis test names a graph or a timeline, since Python has neither, and PBT-SYNC-1 checks typed test records, never the frame times of a written file; spec `restoration-for-every-upload` covers it (P-RST-15, P-RST-16, P-RST-20).
