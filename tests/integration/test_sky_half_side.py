"""The phone's half-side copies of the two sky pictures the loader keeps (sky-half-side-1): scripts/make_sky_half_side.py.

- each copy is its picture at exactly half the width and height, close to a 2 by 2 mean of it (a Lanczos filter and the
  encoding move a pixel only a little; decoded pixels differ a little across platforms, so within a stated tolerance);
- each copy decodes to a quarter of its picture's memory;
- each copy's record entry pins its bytes, names its picture's sources and the script's own words for what changed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scripts import make_sky_half_side as half

ROOT = Path(__file__).resolve().parents[2]
TEXTURES = ROOT / "apps" / "web" / "public" / "textures"
RECORD = json.loads((TEXTURES / "PROVENANCE.json").read_text(encoding="utf-8"))
PAIRS = [
    ("eso_milkyway_panorama.jpg", "eso_milkyway_panorama_3k.jpg"),
    ("eso_milkyway_diffuse_6k.webp", "eso_milkyway_diffuse_3k.webp"),
]


@pytest.mark.parametrize(("source", "copy"), PAIRS)
def test_each_copy_is_its_picture_at_half_the_side(source: str, copy: str) -> None:
    with Image.open(TEXTURES / source) as a, Image.open(TEXTURES / copy) as b:
        assert b.size == (a.size[0] // 2, a.size[1] // 2) == (3000, 1500)
        big = np.asarray(a.convert("RGB"), dtype=np.float64)
        small = np.asarray(b.convert("RGB"), dtype=np.float64)
    box = big.reshape(small.shape[0], 2, small.shape[1], 2, 3).mean(axis=(1, 3))
    diff = np.abs(box - small)
    # measured 25 Nov on macOS: panorama mean 3.10, 99th percentile 15.75 (Lanczos keeps its stars sharper than a mean);
    # glow mean 0.14, 99th percentile 0.75; the bounds leave room for another platform's decoder, not for another picture
    mean_bound, p99_bound = (4.0, 25.0) if copy.endswith(".jpg") else (0.5, 3.0)
    assert diff.mean() < mean_bound and np.percentile(diff, 99) < p99_bound, (copy, round(diff.mean(), 3), np.percentile(diff, 99))


@pytest.mark.parametrize(("source", "copy"), PAIRS)
def test_each_copy_takes_a_quarter_of_the_memory(source: str, copy: str) -> None:
    with Image.open(TEXTURES / source) as a, Image.open(TEXTURES / copy) as b:
        assert b.size[0] * b.size[1] * 4 == a.size[0] * a.size[1] == 18_000_000


@pytest.mark.parametrize(("source", "copy"), PAIRS)
def test_each_copy_is_recorded_with_its_pictures_sources_and_what_changed(source: str, copy: str) -> None:
    files = {f["path"]: f for f in RECORD["files"]}
    entry = files[copy]
    assert entry["sha256"] == hashlib.sha256((TEXTURES / copy).read_bytes()).hexdigest()
    assert entry["sources"] == files[source]["sources"]
    assert entry["producedBy"]["script"] == "scripts/make_sky_half_side.py"
    assert entry["producedBy"]["changes"] == {c["output"]: c["changes"] for c in half.COPIES}[copy]
    assert "half its width and height (3000 by 1500)" in entry["producedBy"]["changes"]
    assert hashlib.sha256((TEXTURES / source).read_bytes()).hexdigest() in entry["note"]
