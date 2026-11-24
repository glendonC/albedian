# Tasks: Export & Provenance

- [x] 1. Define `ExportArtifact` and provenance manifest schema in `packages/contracts`.
  - Replaced on `main` by restore-1 (`e9cdd008`, with follow-ups `2900bfcc`, `4ab96b38`, `4fcdca53`, `673a680b`, `9eac2f19`, `95a365f4`, `7e12ffd7`, `52617e5d`) with `ExportManifestSchema` in `packages/contracts/src/media.ts` and `ExportManifest` in `services/contracts/media.py`, checked on both sides against `tests/fixtures/media_contract.json` [verified].
- [x] 2. Implement FFmpeg audio-preserving remux and SHA-256 manifest generator in `services/media`.
  - Unchecked 2026-11-11 after reading the code at `dcdbd65d`: the remux exists and its audio copy is tested, but nothing outside `tests/` calls it (no route), it copies the first audio stream only (`services/media/runtime.py:495-496`) at a whole-number frame rate (`runtime.py:435`), its manifest is a dictionary in memory that never carries the solution id and takes the graph id from its caller (`services/media/models.py:141-187`, against REQ-EXP-1.2), and a silent export records the frames' hash as `sourceSha256` (`runtime.py:515`); spec `restoration-for-every-upload` covers it (REQ-RST-7, REQ-RST-9).
  - Done 2026-11-19 for stills by restore-8 (`936ae329`, with profile rebuild and stored-colour follow-ups `1f9272a7`, `9e48edc3`, `7db3f0a6`, `95a365f4`, `2ddf7b3a`) [tested: `tests/media/test_export.py`]: the manifest is built from the
    stored variant, graph and attempt with the newest solution id (`80dc06d6`, `271f7f3d`), and exported with the variant's PNG byte for byte
    in one stored zip (`GET /api/media/{sha256}/variants/{variantId}/export`). Done 2026-11-19 for clips: restore-7
    (`756ec666`) stores a clip losslessly with its audio packets copied, restore-8c (`e11757bd`, `809ed064`) writes
    zip64 past 2 GiB, restore-8c's second half (`bbe20cb7`) exports a clip's stored file with its manifest, and
    restore-9 (`66bd1cf8`) removed the old remux and its in-memory manifest. Wired to `Download` in the web app across
    laptop and phone for stills and clips (`795a4f14`, `458076da`, `59255159`, `7601d372`, `5cd47ced`, `6396608a`) [verified].
- [x] 3. Verify `EXP-10` and `PBT-SYNC-1`.
  - Unchecked 2026-11-11 after reading the code at `dcdbd65d`: EXP-10 runs (`tests/integration/test_science_experiments.py:501`), but PBT-SYNC-1 is checked only on typed test `MediaVariant` records (`packages/observation/src/__tests__/observation.test.ts:658`), and no property test reads the frame times, the duration or the audio packets of an exported file; spec `restoration-for-every-upload` covers it (P-RST-15, P-RST-16).
  - Done 2026-11-18 by restore-5 (`be094f02`) and restore-5b (`e19ea242`; design notes `98c0521f`, `ecaaaa57`) [tested: `tests/media/test_clip_io.py`]: on the stored clip file, P-RST-15 reads every
    frame time, P-RST-16 every audio packet's hash and time, and the last frame's duration (the stream's presentation end
    minus its last frame time) is kept and read by the stored-clip check. The duration is recorded, not refused: exact on
    regular frame time lists and on the owner's three clips, moved by a few ticks where gaps vary at random or the clip
    is turned (restore-5b report). EXP-10 tested the old remux, which restore-9 (`66bd1cf8`) replaced with a test of the job;
    P-RST-21 (`936ae329`, `bbe20cb7` in `tests/media/test_export.py`) and live `Download` runs on real stills and clips
    (`.lead/kiro/reports/EXPORT/download-e2e.md`, `.lead/kiro/reports/EXPORT/download-ui-click-e2e.md`) verify byte-identical
    stored zip exports, dual-language manifest parsing and stripped upload metadata [tested].
