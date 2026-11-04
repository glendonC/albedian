"""The Moon's maps for the WebGPU landing (scripts/bake_moon_maps.py): NASA SVS 4720 colour and a
normal map from the LOLA heights with true slopes.

The rebake from the 264 MB of SVS inputs (`--fetch` downloads them into .cache/moon-svs4720/) runs
where they are cached; CI has no copy and skips it through the local_dataset marker. The other
tests check the normal construction on surfaces with known answers and the committed files
against physical bounds.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
TEXTURES = ROOT / "apps" / "web" / "public" / "textures"
MOON_RADIUS_M = 1_737_400.0
SVS_CACHE = ".cache/moon-svs4720"


def _load_script():
    spec = importlib.util.spec_from_file_location("bake_moon_maps", ROOT / "scripts" / "bake_moon_maps.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bake = _load_script()


def _grid(rows: int, columns: int) -> tuple[np.ndarray, np.ndarray]:
    lat = np.radians(90.0 - (np.arange(rows) + 0.5) * 180.0 / rows)[:, None]
    lon = np.radians(-180.0 + (np.arange(columns) + 0.5) * 360.0 / columns)[None, :]
    return np.broadcast_arrays(lat, lon)


def test_a_smooth_sphere_has_upright_normals():
    normals = bake.surface_normals(np.full((90, 180), MOON_RADIUS_M))
    assert np.abs(normals[..., 0]).max() < 1e-12
    assert np.abs(normals[..., 1]).max() < 1e-12
    assert np.abs(normals[..., 2] - 1.0).max() < 1e-12


def _tilt(rise_per_radian: float, radius: np.ndarray) -> np.ndarray:
    """Normal component along the rise of r(angle) = R (1 + s angle): the ground climbs dr per
    r d(angle) walked, a slope of R s / r, so the normal leans back by atan of that."""
    slope = MOON_RADIUS_M * rise_per_radian / radius
    return -slope / np.hypot(1.0, slope)


def test_ground_rising_to_the_east_tilts_the_normal_west_by_the_true_slope():
    lat, lon = _grid(180, 360)
    radius = MOON_RADIUS_M * (1.0 + 0.1 * lon)
    normals = bake.surface_normals(radius)
    rows, columns = slice(89, 91), slice(10, -10)  # the equator, clear of the wrap at 180 deg
    assert np.allclose(normals[rows, columns, 0], _tilt(0.1, radius[rows, columns]), atol=1e-4)
    assert np.abs(normals[rows, columns, 1]).max() < 1e-4


def test_ground_rising_to_the_north_tilts_the_normal_south():
    lat, lon = _grid(180, 360)
    radius = MOON_RADIUS_M * (1.0 + 0.1 * lat)
    normals = bake.surface_normals(radius)
    rows = slice(60, 120)
    assert np.allclose(normals[rows, :, 1], _tilt(0.1, radius[rows]), atol=1e-4)
    assert np.abs(normals[rows, :, 0]).max() < 1e-9


def test_rows_at_the_poles_stay_physical_on_rough_ground():
    # 100 m of height noise on a 4-per-degree grid: in the first row the longitude samples are
    # 33 m apart, so a one-sample stencil would make slopes near 80 degrees from noise alone
    rng = np.random.default_rng(7)
    radius = MOON_RADIUS_M + rng.normal(0.0, 100.0, size=(720, 1440))
    normals = bake.surface_normals(radius)
    assert np.isfinite(normals).all()
    slopes = np.degrees(np.arccos(np.clip(normals[..., 2], -1.0, 1.0)))
    assert slopes[:4].max() < 15.0 and slopes[-4:].max() < 15.0
    assert np.allclose(np.linalg.norm(normals, axis=-1), 1.0, atol=1e-12)


def test_normals_survive_encoding_within_one_code_step():
    rng = np.random.default_rng(3)
    normals = rng.normal(size=(64, 64, 3))
    normals[..., 2] = np.abs(normals[..., 2])
    normals /= np.linalg.norm(normals, axis=-1, keepdims=True)
    decoded = bake.decode_normals(bake.encode_normals(normals))
    assert np.abs(decoded - normals).max() <= 0.5 / 127.5 + 1e-12


def test_the_committed_normal_map_has_lunar_slopes_and_no_exaggeration():
    with Image.open(TEXTURES / bake.NORMAL_OUTPUT) as image:
        assert image.format == "WEBP" and image.size == (5760, 2880) and image.mode == "RGB"
        normals = bake.decode_normals(np.asarray(image))
    length = np.linalg.norm(normals, axis=-1)
    assert np.abs(length - 1.0).max() < 0.02  # unit vectors up to 8-bit rounding
    slopes = np.degrees(np.arccos(np.clip(normals[..., 2] / length, -1.0, 1.0)))
    # LOLA at a 3.8 km baseline: median slope 3.6 deg, steepest crater walls under 40 deg. A
    # map exaggerated 2.4 times, as the old mesh's relief was, would put the median near 8.5 deg.
    assert 2.0 < float(np.median(slopes)) < 6.0
    assert float(slopes.max()) < 40.0


def test_the_committed_colour_map_is_the_8k_svs_map_without_metadata():
    with Image.open(TEXTURES / bake.COLOUR_OUTPUT) as image:
        assert image.format == "JPEG" and image.size == (8192, 4096) and image.mode == "RGB"
        assert not image.getexif()
        assert "icc_profile" not in image.info


@pytest.mark.local_dataset(f"{SVS_CACHE}/lroc_color_16bit_srgb_8k.tif", f"{SVS_CACHE}/ldem_16_uint.tif")
def test_the_committed_maps_rebake_exactly_from_the_pinned_svs_inputs():
    cache = ROOT / SVS_CACHE
    # Lossless: every committed normal equals the one the LOLA heights give
    with Image.open(TEXTURES / bake.NORMAL_OUTPUT) as image:
        committed = np.asarray(image)
    assert np.array_equal(committed, bake.normal_pixels(bake.pinned_input(cache, bake.HEIGHT_INPUT)))
    # The colour map re-encodes to the same bytes
    colour = bake.bake_colour(bake.pinned_input(cache, bake.COLOUR_INPUT))
    assert colour == (TEXTURES / bake.COLOUR_OUTPUT).read_bytes()


def test_the_download_registry_lists_the_pinned_svs_inputs():
    # tests/conftest.py accepts a local_dataset path only if data/downloads.json lists it
    registry = json.loads((ROOT / "data" / "downloads.json").read_text())
    by_path = {entry["path"]: entry for entry in registry["downloads"]}
    for name, sha256 in (bake.COLOUR_INPUT, bake.HEIGHT_INPUT):
        entry = by_path[f"{SVS_CACHE}/{name}"]
        assert (entry["url"], entry["sha256"]) == (f"{bake.SVS_4720}/{name}", sha256)
        assert (entry["licence"], entry["licenceUrl"]) == (bake.NASA_GUIDELINES["spdx"], bake.NASA_GUIDELINES["url"])


def test_the_textures_record_pins_both_maps_and_the_svs_inputs_they_come_from():
    record = json.loads((TEXTURES / "PROVENANCE.json").read_text())
    sources = {s["id"]: s for s in record["sources"]}
    files = {f["path"]: f for f in record["files"]}
    for output, source_id, (name, sha256) in (
        (bake.COLOUR_OUTPUT, "nasa-svs-4720-lroc-color-8k", bake.COLOUR_INPUT),
        (bake.NORMAL_OUTPUT, "nasa-svs-4720-ldem-16", bake.HEIGHT_INPUT),
    ):
        entry = files[output]
        assert entry["sources"] == [source_id]
        assert entry["producedBy"] == {"method": "script", "script": "scripts/bake_moon_maps.py", "command": "uv run python scripts/bake_moon_maps.py"}
        assert entry["sha256"] == bake.sha256_file(TEXTURES / output)
        assert sources[source_id]["sha256"] == sha256
        assert sources[source_id]["url"].endswith(name)
        assert sources[source_id]["credit"] == "NASA's Scientific Visualization Studio"
        assert "aesthetics, not science" in sources[source_id]["note"]
