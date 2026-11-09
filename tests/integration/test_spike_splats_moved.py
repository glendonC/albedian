"""The WebGPU spike's two scaling splats left the tracked tree (data-10).

`snow-street.spz` and `valley.spz` came from sparkjs.dev with no licence stated there, so they cannot carry a
provenance record and are not registered in data/downloads.json. They now live only in the shared, untracked
`.cache/splats/` for local spike runs. Git history still holds both files in every commit before their removal:
deleting them from the tree does not remove them from the repository.

The same change drops three stale BASELINE_GAPS pins (earth_clouds.png, earth_normal.jpg and earth_specular.jpg,
deleted in cbf96557), so the baseline pins exactly the gaps still open, as docs/architecture/ASSET_PROVENANCE.md asks.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "apps" / "web" / "public"
CACHE = ROOT / ".cache" / "splats"
# Each file's SHA-256 as BASELINE_GAPS pinned it before this change
MOVED = {
    "snow-street.spz": "35840fd3d178388409096537e2f363c49e1502850c5bb81dbaa5cbc4ecb3ecaa",
    "valley.spz": "565768b0b758698a641521654ac0947a74c85cb69ee0f034e6c928fbefa97833",
}
STALE_EARTH = ("earth_clouds.png", "earth_normal.jpg", "earth_specular.jpg")


def _verify_provenance():
    spec = importlib.util.spec_from_file_location("verify_provenance_for_splats", ROOT / "scripts" / "verify_provenance.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load scripts/verify_provenance.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_no_splat_scene_is_tracked_or_shipped_under_public() -> None:
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--", "apps/web/public"], capture_output=True, check=True)
    assert not [p for p in tracked.stdout.decode("utf-8").split("\0") if p.endswith(".spz")]
    assert not list(PUBLIC.rglob("*.spz"))


def test_baseline_pins_exactly_the_open_gaps_and_cached_copies_keep_their_bytes() -> None:
    vp = _verify_provenance()
    gaps = {g["path"] for g in json.loads((ROOT / "scripts" / "provenance_gaps.json").read_text(encoding="utf-8"))["gaps"]}
    assert set(vp.BASELINE_GAPS) == gaps
    assert gaps == {
        "apps/web/public/textures/bruneton_atmo_lut.png",
    }
    for name in MOVED:
        assert f"apps/web/public/splats/{name}" not in vp.BASELINE_GAPS
    for name in STALE_EARTH:
        assert f"apps/web/public/textures/{name}" not in vp.BASELINE_GAPS
        assert not (PUBLIC / "textures" / name).exists()
    # Where the shared cache holds the moved files (a local spike setup, not CI), they are the bytes the pins named
    for name, pin in MOVED.items():
        copy = CACHE / name
        if copy.is_file():
            assert hashlib.sha256(copy.read_bytes()).hexdigest() == pin, name


def test_the_provenance_doc_says_where_the_splats_went_and_that_history_keeps_them() -> None:
    doc = (ROOT / "docs" / "architecture" / "ASSET_PROVENANCE.md").read_text(encoding="utf-8")
    assert "\u2014" not in doc
    for required in (".cache/splats", "git history", "one entry"):
        assert required in doc
