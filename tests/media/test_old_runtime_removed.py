"""restore-9 (REQ-RST-11.2 and 11.3): the old array route and the old runtime are gone, and the job is the only way to
restore a recording.

Task 9 of `.kiro/specs/restoration-for-every-upload/tasks.md` is done when a search for the removed names finds none;
these tests are that search, kept, so a removed name cannot come back unseen. Each promise the removed tests held is
kept by a test of the job (the characterization list in restore-9's report).
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from fastapi.testclient import TestClient

import services.media
from services.api import create_app
from services.media import models

ROOT = Path(__file__).resolve().parents[2]

# Spelt in two parts, so this file does not name them itself
REMOVED_NAMES = [first + second for first, second in (
    ("execute_restoration", "_branch"),
    ("export_video_with", "_audio_remux"),
    ("Restoration", "Metrics"),
    ("RestorationBranch", "Result"),
    ("ExportRemux", "Result"),
    ("MediaExperiment", "Runtime"),
    ("compute_2d_sky", "_silhouette_mask"),
    ("deband_shadow", "_macroblocks"),
    ("run_real_media", "_experiment"),
    ("render_clip", "_variants"),
)]
CODE_FOLDERS = ("services", "scripts", "tests", "apps/web/src", "packages", "research")
CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs"}
NOT_CODE = {"node_modules", "dist", "build", ".venv", "__pycache__", ".cache"}


def test_the_old_array_route_is_gone_and_a_restore_goes_through_the_job() -> None:
    app = create_app()
    paths = app.openapi()["paths"]
    assert "/api/media/restore" not in paths
    assert "post" in paths["/api/media/{sha256}/restore"]
    with TestClient(app) as client:
        # The body the old route took: an unrouted path takes no body at all (services/api.py, UNROUTED_BODY_LIMIT)
        refused = client.post("/api/media/restore", json={"image_luma": [[0.1] * 8] * 8, "sourceAssetId": "x"})
        unrouted = client.post("/api/media/restore")
    assert refused.status_code == 413, refused.status_code
    assert unrouted.status_code == 404, unrouted.status_code


def test_the_old_runtime_and_its_sky_mask_cannot_be_imported() -> None:
    for module in ("services.media.runtime", "services.media.silhouette"):
        assert importlib.util.find_spec(module) is None, module
    for name in REMOVED_NAMES:
        assert not hasattr(services.media, name) and not hasattr(models, name), name
    assert set(services.media.__all__) <= set(dir(services.media))
    # What discovery still takes from the old module
    assert callable(models.compute_file_sha256)


def test_no_code_names_what_restore_9_removed() -> None:
    found = []
    this_file = Path(__file__).resolve()
    for folder in CODE_FOLDERS:
        for directory, subfolders, files in os.walk(ROOT / folder):
            subfolders[:] = [name for name in subfolders if name not in NOT_CODE]
            for file_name in files:
                path = Path(directory) / file_name
                if path.suffix not in CODE_SUFFIXES or path.resolve() == this_file:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                found += [f"{path.relative_to(ROOT)}: {name}" for name in REMOVED_NAMES if name in text]
    for script in ("run_real_media_experiment.py", "render_clip_variants.py"):
        assert not (ROOT / "scripts" / script).exists(), script
    assert found == []
