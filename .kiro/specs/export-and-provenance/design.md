# Design: Export & Provenance

## Architecture
- **Contracts:** `ExportArtifactSchema` in `@albedian/contracts`
- **Backend:** `services/media/runtime.py` (`export_variant_with_provenance`)

## Correctness Properties
- **Property PBT-SYNC-1:** Exported and comparison media variants preserve monotonic frame timestamps, source duration within 1 frame tolerance, and untouched original audio stream (`-c:a copy`) with packet PTS preservation verified via `framemd5`.
