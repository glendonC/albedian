# Tasks: Export & Provenance

- [x] 1. Define `ExportArtifact` and provenance manifest schema in `packages/contracts`.
- [ ] 2. Implement FFmpeg audio-preserving remux and SHA-256 manifest generator in `services/media`.
  - Unchecked 2026-11-11 after reading the code at `dcdbd65d`: the remux exists and its audio copy is tested, but nothing outside `tests/` calls it (no route), it copies the first audio stream only (`services/media/runtime.py:495-496`) at a whole-number frame rate (`runtime.py:435`), its manifest is a dictionary in memory that never carries the solution id and takes the graph id from its caller (`services/media/models.py:141-187`, against REQ-EXP-1.2), and a silent export records the frames' hash as `sourceSha256` (`runtime.py:515`); spec `restoration-for-every-upload` covers it (REQ-RST-7, REQ-RST-9).
- [ ] 3. Verify `EXP-10` and `PBT-SYNC-1`.
  - Unchecked 2026-11-11 after reading the code at `dcdbd65d`: EXP-10 runs (`tests/integration/test_science_experiments.py:501`), but PBT-SYNC-1 is checked only on typed test `MediaVariant` records (`packages/observation/src/__tests__/observation.test.ts:658`), and no property test reads the frame times, the duration or the audio packets of an exported file; spec `restoration-for-every-upload` covers it (P-RST-15, P-RST-16).
