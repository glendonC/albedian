"""Earth maps (data-5): a Natural Earth water mask, NASA's Blue Marble cloud layer, an ETOPO 2022
height map and NASA's Blue Marble Next Generation base map for March 2004.

`scripts/bake_earth_maps.py` reads public downloads kept in the shared `.cache/earth-maps/`, not in
git, and writes textures to `apps/web/public/textures/` with their provenance entries. The
synthetic tests pin the shapefile reader and the polygon fill; the real-data tests read the
downloads through the `local_dataset` marker, so they skip, with their reason, where those are absent.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
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
ETOPO_REL = f"{CACHE}/ETOPO_2022_v1_60s_N90W180_surface.tif"
DAY_REL = f"{CACHE}/world.200403.3x21600x10800.jpg"
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


def test_record_update_leaves_every_entry_where_it_stands(tmp_path: Path):
    # textures/PROVENANCE.json is shared with scripts/bake_moon_maps.py, whose --verify compares the whole file, so
    # a rebake here replaces its own entries in place, keeps every other entry where it is and appends a new one
    def source(source_id: str, title: str = "Before") -> dict:
        licence = {"spdx": "CC0-1.0", "name": "Creative Commons Zero v1.0 Universal", "url": "https://example.org/cc0"}
        return {"id": source_id, "title": title, "url": "https://example.org/", "version": "1", "license": licence, "credit": "Example"}

    def entry(path: str, source_id: str, note: str = "Before") -> dict:
        return {"path": path, "sha256": "ab" * 32, "sources": [source_id], "producedBy": {"method": "downloaded-as-is"}, "note": note}

    record = tmp_path / "PROVENANCE.json"
    sources = [source("other-a"), source("mine"), source("other-b")]
    files = [entry("a.jpg", "other-a"), entry("mine.jpg", "mine"), entry("b.jpg", "other-b")]
    record.write_text(json.dumps({"format": "albedian.asset-provenance", "formatVersion": 1, "sources": sources, "files": files, "fileSets": []}))
    bake.update_record(record, [source("mine", "Rebaked"), source("new")], [entry("mine.jpg", "mine", "Rebaked"), entry("new.jpg", "new")])
    written = json.loads(record.read_text(encoding="utf-8"))
    assert [s["id"] for s in written["sources"]] == ["other-a", "mine", "other-b", "new"]
    assert [f["path"] for f in written["files"]] == ["a.jpg", "mine.jpg", "b.jpg", "new.jpg"]
    assert written["sources"][1]["title"] == "Rebaked" and written["files"][1]["note"] == "Rebaked"


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


# =============================================================================================
# Relief: ETOPO 2022 heights packed into an RGB8 PNG (R high byte, G low byte, B zero)
# =============================================================================================


def test_heights_round_trip_to_the_metre_through_two_bytes():
    heights = np.array([[-11_000.0, -10_361.2, -0.4, 0.6], [8_848.9, 54_535.0, 1234.5, 1235.5]])
    rgb = bake.encode_heights(heights)
    assert rgb.dtype == np.uint8 and rgb.shape == (2, 4, 3)
    assert not rgb[..., 2].any()
    np.testing.assert_array_equal(bake.decode_heights(rgb), np.round(heights))


@pytest.mark.parametrize("bad", [-11_000.6, 54_535.6, float("nan"), float("inf")])
def test_heights_the_encoding_cannot_hold_are_refused(bad):
    with pytest.raises(ValueError, match="between -11000 and 54535 m"):
        bake.encode_heights(np.array([[0.0, bad]]))


def test_etopo_reader_refuses_a_grid_it_does_not_expect(tmp_path: Path):
    path = tmp_path / "small.tif"
    Image.fromarray(np.zeros((5, 10), dtype=np.float32)).save(path)
    with pytest.raises(ValueError, match="expected a 21600x10800 float grid"):
        bake.read_etopo(path)


def test_committed_height_map_is_rgb_with_an_empty_blue_channel():
    image = Image.open(TEXTURES / bake.HEIGHT_NAME)
    assert image.mode == "RGB" and image.size == (4096, 2048)
    assert not np.asarray(image)[..., 2].any()


needs_etopo = pytest.mark.local_dataset(ETOPO_REL)


@pytest.fixture(scope="module")
def etopo_heights():
    return bake.area_mean_heights(bake.read_etopo(ROOT / ETOPO_REL), 4096, 2048)


@needs_etopo
def test_committed_height_map_is_the_rounded_area_mean(etopo_heights):
    committed = bake.decode_heights(np.asarray(Image.open(TEXTURES / bake.HEIGHT_NAME)))
    np.testing.assert_array_equal(committed, np.round(etopo_heights))


@needs_etopo
@pytest.mark.parametrize(
    "place,lat,lon,low,high",
    [
        ("Everest", 27.9881, 86.9250, 6_000, 8_849),
        ("Challenger Deep", 11.3733, 142.5917, -10_935, -9_500),
        ("Death Valley, below sea level", 36.25, -116.82, -86, 0),
    ],
)
def test_real_heights_at_known_places(etopo_heights, place, lat, lon, low, high):
    height, width = etopo_heights.shape
    value = etopo_heights[int((90 - lat) / 180 * height), int((lon + 180) / 360 * width)]
    assert low <= value < high, place


@needs_etopo
def test_area_means_keep_the_grids_mean_height(etopo_heights):
    def weighted_mean(grid):
        rows = grid.shape[0]
        weights = np.cos(np.radians(90 - (np.arange(rows) + 0.5) * 180 / rows))[:, None]
        return float((grid * weights).sum() / (weights.sum() * grid.shape[1]))

    full = np.asarray(bake.read_etopo(ROOT / ETOPO_REL), dtype=np.float64)
    assert weighted_mean(etopo_heights) == pytest.approx(weighted_mean(full), abs=5.0)


@needs_etopo
def test_etopo_download_matches_its_pinned_checksum():
    digest = hashlib.sha256((ROOT / ETOPO_REL).read_bytes()).hexdigest()
    assert digest == bake.INPUTS["etopo"].sha256


def test_height_map_record_cites_etopo_under_cc0():
    from services.contracts.provenance import AssetProvenanceRecord

    record = AssetProvenanceRecord.model_validate_json((TEXTURES / "PROVENANCE.json").read_text(encoding="utf-8"))
    entry = next(entry for entry in record.files if entry.path == bake.HEIGHT_NAME)
    source = next(source for source in record.sources if source.id == "noaa-etopo-2022-60s")
    assert entry.sources == ("noaa-etopo-2022-60s",)
    assert entry.produced_by.method == "script" and entry.produced_by.script == "scripts/bake_earth_maps.py"
    assert "R * 256 + G - 11000" in entry.note and "floor" in entry.note
    assert source.license.spdx == "CC0-1.0" and "Not to be used for navigation" in source.license.conditions
    assert source.doi == "10.25921/fd45-gt74"
    assert "ETOPO 2022 15 Arc-Second Global Relief Model" in source.citation
    assert "doi:10.25921/fd45-gt74" in source.citation


# =============================================================================================
# Day map: NASA's Blue Marble Next Generation base map for March 2004, which has no baked relief
# shading. It replaced the November 2004 base map, which nothing had drawn.
# =============================================================================================

NOVEMBER_BASE_NAME = "earth_day_bluemarble_2004-11_base_8k.jpg"
NOVEMBER_SHADED_NAME = "earth_day_bluemarble_2004-11_8k.jpg"
LEGACY_UNRECORDED_EARTH = ("earth_clouds.png", "earth_specular.jpg", "earth_normal.jpg")


def test_day_map_is_the_march_2004_base_map():
    assert bake.DAY_NAME == "earth_day_bluemarble_2004-03_base_8k.jpg"
    assert bake.INPUTS["day"].filename == "world.200403.3x21600x10800.jpg"
    assert "/bmng-base/march/" in bake.INPUTS["day"].url


def test_the_november_base_map_is_gone_with_its_record_entry_and_source():
    from services.contracts.provenance import AssetProvenanceRecord

    record = AssetProvenanceRecord.model_validate_json((TEXTURES / "PROVENANCE.json").read_text(encoding="utf-8"))
    gaps = json.loads((ROOT / "scripts" / "provenance_gaps.json").read_text(encoding="utf-8"))["gaps"]
    gap_paths = {entry["path"] for entry in gaps}
    for old_name in (NOVEMBER_BASE_NAME, NOVEMBER_SHADED_NAME, *LEGACY_UNRECORDED_EARTH):
        assert not (TEXTURES / old_name).exists(), old_name
        assert old_name not in {entry.path for entry in record.files}, old_name
        assert f"apps/web/public/textures/{old_name}" not in gap_paths, old_name
    assert "nasa-bmng-2004-11-base" not in {source.id for source in record.sources}
    assert "nasa-bmng-2004-11-topo-bathy" not in {source.id for source in record.sources}


def test_committed_day_map_is_8k_rgb():
    image = Image.open(TEXTURES / bake.DAY_NAME)
    assert image.mode == "RGB" and image.size == (8192, 4096)


@pytest.fixture(scope="module")
def day_and_water_1k():
    """The committed day map and water mask, both box-averaged to 1024x512: RGB levels, and water fractions."""
    rgb = np.asarray(Image.open(TEXTURES / bake.DAY_NAME).convert("RGB").resize((1024, 512), Image.Resampling.BOX), dtype=np.float64)
    water = np.asarray(Image.open(TEXTURES / bake.WATER_NAME).resize((1024, 512), Image.Resampling.BOX), dtype=np.float64)
    return rgb, water / 255.0


def _blueness(rgb: np.ndarray) -> np.ndarray:
    """Blue minus red as a share of the three levels: about 0.67 on NASA's ocean blue, about 0 on land."""
    return (rgb[..., 2] - rgb[..., 0]) / np.maximum(rgb.sum(axis=-1), 1.0)


def _correlation_at_shift(score: np.ndarray, water: np.ndarray, dx: int, dy: int) -> float:
    """Pearson r between the water fraction and the score moved dx columns (wrapping) and dy rows (cropped)."""
    moved, rows = np.roll(score, dx, axis=1), score.shape[0]
    a, b = moved[max(dy, 0) : rows + min(dy, 0)], water[max(-dy, 0) : rows + min(-dy, 0)]
    return float(np.corrcoef(a.ravel(), b.ravel())[0, 1])


def test_committed_day_map_registers_with_the_water_mask(day_and_water_1k):
    # The renderer lays the day map, the water mask and the height map on one grid, from longitude -180 and
    # latitude 90. Blue against water peaks with no shift (r 0.94 measured) and drops a pixel (0.35 degrees)
    # either way, or half a turn round the globe.
    rgb, water = day_and_water_1k
    score = _blueness(rgb)
    aligned = _correlation_at_shift(score, water, 0, 0)
    assert aligned > 0.9
    for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1), (512, 0)]:
        assert _correlation_at_shift(score, water, dx, dy) < aligned, (dx, dy)


def test_committed_day_map_shows_no_sea_ice_north_of_60n(day_and_water_1k):
    # NASA gives the deep ocean one uniform blue, and this March map shows the high northern seas as open water;
    # Hudson Bay is dark open-water blue, not ice (stated in the map's record). An ice or snow colour: bright, blue
    # no stronger than red.
    rgb, water = day_and_water_1k
    latitude = 90.0 - (np.arange(rgb.shape[0]) + 0.5) * 180.0 / rgb.shape[0]
    far_north_sea = (water == 1.0) & (latitude > 60.0)[:, None]
    looks_icy = (rgb.mean(axis=2) > 150) & (_blueness(rgb) < 0.2)
    assert far_north_sea.sum() > 50_000
    assert looks_icy[far_north_sea].mean() < 0.001
    hudson_bay = rgb[int((90 - 60.0) / 180 * 512), int((-85.0 + 180) / 360 * 1024)]
    assert hudson_bay.max() < 40 and _blueness(hudson_bay) > 0.5


@pytest.mark.parametrize(
    "shelf,lat,lon",
    [("Ross Ice Shelf", -81.5, -175.0), ("Ronne Ice Shelf", -78.5, -61.0), ("Filchner Ice Shelf", -79.0, -40.0)],
)
def test_antarctic_ice_shelves_are_water_in_the_mask_and_white_in_the_map(day_and_water_1k, shelf, lat, lon):
    # Natural Earth's ocean runs under the floating ice shelves, and the base map draws them as ice, so south of
    # 60 degrees S "open sea" in the mask is not all open water in the map (stated in the map's record)
    rgb, water = day_and_water_1k
    row, column = int((90 - lat) / 180 * 512), int((lon + 180) / 360 * 1024)
    assert water[row, column] == 1.0, shelf
    assert rgb[row, column].min() > 200 and _blueness(rgb[row, column]) < 0.05, shelf


@pytest.mark.local_dataset(DAY_REL)
def test_committed_day_map_is_the_base_map_resampled():
    rebuilt = np.asarray(Image.open(io.BytesIO(bake.resample_day_map(ROOT / DAY_REL))), dtype=np.int16)
    committed = np.asarray(Image.open(TEXTURES / bake.DAY_NAME), dtype=np.int16)
    # JPEG decoders may differ by a level here and there between platforms, never by more on average
    assert np.abs(rebuilt - committed).mean() < 0.5


@pytest.mark.local_dataset(DAY_REL)
def test_base_day_map_download_matches_its_pinned_checksum():
    assert hashlib.sha256((ROOT / DAY_REL).read_bytes()).hexdigest() == bake.INPUTS["day"].sha256


def test_day_map_record_cites_the_unshaded_march_base_map():
    from services.contracts.provenance import AssetProvenanceRecord

    record = AssetProvenanceRecord.model_validate_json((TEXTURES / "PROVENANCE.json").read_text(encoding="utf-8"))
    entry = next(entry for entry in record.files if entry.path == bake.DAY_NAME)
    source = next(source for source in record.sources if source.id == "nasa-bmng-2004-03-base")
    assert entry.sources == ("nasa-bmng-2004-03-base",)
    assert entry.produced_by.method == "script" and entry.produced_by.script == "scripts/bake_earth_maps.py"
    assert source.credit == "NASA Earth Observatory"
    assert source.license.spdx == "LicenseRef-NASA-Media-Guidelines"
    assert source.version == "March 2004, 21600x10800 JPEG"
    assert "shading" in source.note and source.sha256 == bake.INPUTS["day"].sha256
    assert bake.INPUTS["day"].url in source.note
    assert "https://science.nasa.gov/earth/earth-observatory/blue-marble-next-generation/" in source.note
    # NASA's sentence verbatim, with the page's own quotation marks
    assert "please credit “NASA Earth Observatory.”" in source.note
    # NASA's two limitations, and what was measured on this map, each said where it holds
    assert "deep oceans are not in the source data and are given a uniform blue" in source.note
    assert "where snow lies for less than three or four months" in source.note
    assert "north of latitude 60 degrees N it shows no sea ice, under 0.1 per cent of the open sea there" in source.note
    assert "Hudson Bay is dark open-water blue, not ice" in source.note
    assert "Around Antarctica the water mask covers the ice shelves, the Ross and Ronne-Filchner among them, which the map draws white" in source.note
