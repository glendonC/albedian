"""Earth maps (data-5): a water mask from Natural Earth and NASA's Blue Marble cloud layer.

`scripts/bake_earth_maps.py` reads public downloads kept in the shared `.cache/earth-maps/`, not in
git, and writes textures to `apps/web/public/textures/` with their provenance entries. The
synthetic tests pin the shapefile reader and the polygon fill; the real-data tests read the
downloads through the `local_dataset` marker, so they skip, with their reason, where those are absent.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import struct
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
CACHE = ".cache/earth-maps"
OCEAN_REL, LAKES_REL, CLOUDS_REL = f"{CACHE}/ne_10m_ocean.zip", f"{CACHE}/ne_10m_lakes.zip", f"{CACHE}/cloud_combined_2048.jpg"
TEXTURES = ROOT / "apps" / "web" / "public" / "textures"


def _load_bake():
    spec = importlib.util.spec_from_file_location("bake_earth_maps", ROOT / "scripts" / "bake_earth_maps.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bake = _load_bake()


# =============================================================================================
# A minimal shapefile writer for synthetic layers (Polygon records only)
# =============================================================================================


def _polygon_record(rings: list[list[tuple[float, float]]], shape_type: int = 5) -> bytes:
    points = [point for ring in rings for point in ring]
    starts, total = [], 0
    for ring in rings:
        starts.append(total)
        total += len(ring)
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    content = struct.pack("<i4d2i", shape_type, min(xs), min(ys), max(xs), max(ys), len(rings), len(points))
    content += struct.pack(f"<{len(starts)}i", *starts)
    content += b"".join(struct.pack("<2d", *point) for point in points)
    return content


def _shapefile_zip(path: Path, layer: str, records: list[bytes]) -> Path:
    body = b"".join(struct.pack(">2i", n + 1, len(content) // 2) + content for n, content in enumerate(records))
    header = struct.pack(">7i", 9994, 0, 0, 0, 0, 0, (100 + len(body)) // 2)
    header += struct.pack("<2i8d", 1000, 5, -180.0, -90.0, 180.0, 90.0, 0.0, 0.0, 0.0, 0.0)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{layer}.shp", header + body)
    return path


def _box(lon0: float, lat0: float, lon1: float, lat1: float) -> list[tuple[float, float]]:
    """A closed ring, clockwise as shapefile outer rings are."""
    return [(lon0, lat0), (lon0, lat1), (lon1, lat1), (lon1, lat0), (lon0, lat0)]


def test_shapefile_reader_returns_each_polygon_with_its_rings(tmp_path: Path):
    outer, hole = _box(-90, 0, 0, 45), list(reversed(_box(-60, 10, -30, 30)))
    archive = _shapefile_zip(tmp_path / "t.zip", "layer", [_polygon_record([outer, hole]), _polygon_record([_box(10, -10, 20, 0)])])
    polygons = bake.read_shapefile_polygons(archive, "layer")
    assert len(polygons) == 2
    assert [len(ring) for ring in polygons[0]] == [5, 5]
    np.testing.assert_array_equal(polygons[0][0], np.array(outer))
    np.testing.assert_array_equal(polygons[1][0], np.array(_box(10, -10, 20, 0)))


def test_shapefile_reader_refuses_shapes_it_does_not_handle(tmp_path: Path):
    archive = _shapefile_zip(tmp_path / "t.zip", "layer", [_polygon_record([_box(0, 0, 10, 10)], shape_type=15)])
    with pytest.raises(ValueError, match="shape type 15"):
        bake.read_shapefile_polygons(archive, "layer")


def test_fill_covers_a_box_exactly_and_nothing_else():
    # On an 8x4 grid each pixel spans 45 degrees: the box covers columns 2 and 3 of row 1
    cover = bake.fill_polygons([[np.array(_box(-90, 0, 0, 45))]], width=8, height=4)
    expected = np.zeros((4, 8))
    expected[1, 2:4] = 1.0
    np.testing.assert_allclose(cover, expected, atol=1e-12)


def test_fill_leaves_holes_open_and_measures_partial_pixels():
    outer = _box(-180, -90, 0, 90)
    hole = list(reversed(_box(-135, -45, -45, 45)))
    cover = bake.fill_polygons([[np.array(outer), np.array(hole)]], width=4, height=2)
    # Each pixel spans 90 by 90 degrees; the hole takes a quarter of each of the four western pixels
    np.testing.assert_allclose(cover, [[0.75, 0.75, 0, 0], [0.75, 0.75, 0, 0]], atol=1e-12)
    half = bake.fill_polygons([[np.array(_box(0, 0, 45, 90))]], width=4, height=2, subrows=8)
    np.testing.assert_allclose(half, [[0, 0, 0.5, 0], [0, 0, 0, 0]], atol=1e-12)


def test_fill_reaches_both_sides_of_the_antimeridian():
    polygons = [[np.array(_box(170, -10, 180, 10))], [np.array(_box(-180, -10, -170, 10))]]
    cover = bake.fill_polygons(polygons, width=36, height=18)
    assert cover[8:10, 35].min() == pytest.approx(1.0) and cover[8:10, 0].min() == pytest.approx(1.0)
    assert cover[:, 1:35].max() == 0.0


def test_mask_encoding_rounds_coverage_to_eight_bits():
    np.testing.assert_array_equal(bake.coverage_to_mask(np.array([[0.0, 0.5, 1.0, 0.001]])), [[0, 128, 255, 0]])


# =============================================================================================
# Real data: Natural Earth 1:10m ocean and lakes, NASA's Blue Marble cloud layer
# =============================================================================================

needs_natural_earth = pytest.mark.local_dataset(OCEAN_REL, LAKES_REL)


@pytest.fixture(scope="module")
def water_1k():
    return bake.water_cover(ROOT / OCEAN_REL, ROOT / LAKES_REL, width=1024, height=512)


def _at(cover: np.ndarray, lat: float, lon: float) -> float:
    height, width = cover.shape
    return float(cover[int((90 - lat) / 180 * height), int((lon + 180) / 360 * width)])


@needs_natural_earth
@pytest.mark.parametrize(
    "place,lat,lon",
    [
        ("central Pacific", 0.0, -150.0),
        ("Mediterranean", 35.0, 18.0),
        ("Caspian Sea", 42.0, 51.0),
        ("Lake Baikal", 53.5, 108.0),
        ("Lake Superior", 47.7, -87.5),
        ("Lake Victoria", -1.0, 33.0),
    ],
)
def test_real_mask_marks_seas_and_lakes_as_water(water_1k, place, lat, lon):
    assert _at(water_1k, lat, lon) == pytest.approx(1.0), place


@needs_natural_earth
@pytest.mark.parametrize(
    "place,lat,lon",
    [
        ("Death Valley, below sea level", 36.25, -116.82),
        ("Turpan Depression, below sea level", 42.8, 89.5),
        ("central Australia", -25.0, 134.0),
        ("Tibetan Plateau", 32.0, 88.0),
        ("East Antarctica", -80.0, 60.0),
    ],
)
def test_real_mask_keeps_land_dry_even_below_sea_level(water_1k, place, lat, lon):
    assert _at(water_1k, lat, lon) == pytest.approx(0.0), place


@needs_natural_earth
def test_real_mask_covers_about_seventy_one_percent_of_the_sphere(water_1k):
    height = water_1k.shape[0]
    weights = np.cos(np.radians(90 - (np.arange(height) + 0.5) * 180 / height))[:, None]
    fraction = float((water_1k * weights).sum() / (weights.sum() * water_1k.shape[1]))
    assert 0.70 < fraction < 0.72


@needs_natural_earth
def test_committed_mask_is_what_the_bake_makes():
    committed = np.asarray(Image.open(TEXTURES / bake.WATER_NAME))
    rebuilt = bake.coverage_to_mask(bake.water_cover(ROOT / OCEAN_REL, ROOT / LAKES_REL, width=4096, height=2048))
    np.testing.assert_array_equal(committed, rebuilt)


def test_committed_clouds_are_nasas_file_byte_for_byte():
    assert hashlib.sha256((TEXTURES / bake.CLOUDS_NAME).read_bytes()).hexdigest() == bake.INPUTS["clouds"].sha256


@pytest.mark.local_dataset(OCEAN_REL, LAKES_REL, CLOUDS_REL)
def test_downloads_match_their_pinned_checksums():
    for name in ("ocean", "lakes", "clouds"):
        source = bake.INPUTS[name]
        assert hashlib.sha256((ROOT / CACHE / source.filename).read_bytes()).hexdigest() == source.sha256, name


def test_record_entries_cite_the_bake_and_the_sources():
    from services.contracts.provenance import AssetProvenanceRecord

    record = AssetProvenanceRecord.model_validate_json((TEXTURES / "PROVENANCE.json").read_text(encoding="utf-8"))
    files = {entry.path: entry for entry in record.files}
    sources = {source.id: source for source in record.sources}
    water = files[bake.WATER_NAME]
    assert water.produced_by.method == "script" and water.produced_by.script == "scripts/bake_earth_maps.py"
    assert sorted(water.sources) == ["natural-earth-10m-lakes", "natural-earth-10m-ocean"]
    assert files[bake.CLOUDS_NAME].produced_by.method == "downloaded-as-is"
    assert sources["natural-earth-10m-ocean"].license.spdx.startswith("LicenseRef-")
    assert "world base map" in sources["natural-earth-10m-lakes"].license.conditions
    assert "2001" in sources["nasa-blue-marble-2002-clouds"].note
