# Albedian Local Media Ingestion Directory

## Privacy & Git Isolation Guarantee

All files placed inside `data/uploads/` and `data/artifacts/` are **strictly gitignored** (see root [`.gitignore`](../.gitignore)):
- `data/uploads/<folder>/`: drop your personal night videos (`.mp4`, `.mov`) and photos (`.jpg`, `.png`, `.heic`, `.dng`) here. Original bytes, audio tracks, faces, and GPS metadata never leave your machine and are never committed to Git.
- `data/uploads/public_samples/`: drop rights-cleared or public dark-sky comparison images/videos here.
- `data/artifacts/`: the output directory where `scripts/run_real_media_experiment.py` writes content-addressed probes, side-by-side comparison frames, audio-preserved restored MP4s, WCS plate-solving diagnostics, and JSON provenance manifests.

## Running the Real-Footage Experiment Suite (`E1` to `E6`)

Once you place one or more clips or photos in a folder under `data/uploads/` (for example `data/uploads/public_samples/`), run:

```bash
uv run python scripts/run_real_media_experiment.py data/uploads/<folder>
```

For each file, the script executes:
1. **Immutable Content Probe (`E1`):** Computes `SHA-256`, probes codec/dimensions/FPS/audio streams via `ffprobe`, and measures low-percentile luminance (1%, 5%, 50%, 95%) and digital-black clipping fraction to determine recoverability.
2. **Multi-Branch Media Experiment Runtime (`E2`, `E4`):** Runs `original`, `tone_only`, `star_protected` (shadow tone transfer + background denoise + observed star core protection), and `temporal` (multi-frame aligned stack) and saves side-by-side comparison crops.
3. **Astrometric Detection & Independent WCS Verification (`EXP-01`, `EXP-02`):** Extracts candidate stars (`SNR >= 3.5`), attempts geometric quad plate solving against the Gaia DR3 / Hipparcos catalog, and reports `calibrated`, `ambiguous`, `predicted_only`, or `unresolved` with held-out pixel residuals.
4. **Audio-Preserving Export (`E6` / `EXP-10`):** Exports the restored video with your original audio re-encoded to AAC at 128 kb/s, not copied bit for bit (`services/media/runtime.py`), and writes a complete provenance manifest to `data/artifacts/<filename>_manifest.json`.
