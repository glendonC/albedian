# Albedian Local Media Ingestion Directory

## Privacy & Git Isolation Guarantee

All files placed inside `data/uploads/` and `data/artifacts/` are **strictly gitignored** (see root [`.gitignore`](../.gitignore)):
- `data/uploads/<folder>/`: drop your personal night videos (`.mp4`, `.mov`) and photos (`.jpg`, `.png`, `.heic`, `.dng`) here. Original bytes, audio tracks, faces, and GPS metadata never leave your machine and are never committed to Git.
- `data/uploads/public_samples/`: drop rights-cleared or public dark-sky comparison images/videos here.
- `data/artifacts/`: experiment output written on this machine, never committed.

## Restoring a recording

A photo or clip is restored by the app's restoration job: upload it, then `POST /api/media/{sha256}/restore`
(`services/media/jobs.py`, spec `.kiro/specs/restoration-for-every-upload/`). The original is never changed; each
variant is stored by its own SHA-256 beside the attempt that made it, with what was measured and the gate's record.

For a person to judge the result, `scripts/restore_review.py` runs the same job on photos of the public corpus or on
clips (`--clips`) and writes counts and before and after pictures under `.cache/restore-review/` (its docstring has
the options). The old experiment script and its runtime were removed in restore-9: they reported figures they had not
measured and re-encoded the audio.
