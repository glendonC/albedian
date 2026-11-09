"""The Moon mesh (data-9): `apps/web/public/models/nasa_lro_moon_3d.glb` baked from LOLA heights at true scale.

`scripts/bake_moon_mesh.py` builds a 1 degree latitude-longitude sphere from `ldem_16_uint.tif` (NASA's CGI Moon Kit,
SVS 4720, registered and pinned by `scripts/bake_moon_maps.py`), each vertex's radius the mean LOLA height in the
1 degree box around it, 10 units = 1737.4 km. It replaces a Blender mesh of unrecorded origin with relief about 2.4
times the real range, so the provenance gap for the Moon closes here.

Without the cache these tests hold the committed mesh to its layout, its conventions (the ones the replaced glb
used, so the app's colour and bump maps still line up), true scale, and the places LOLA's highest and lowest points
are; with the cache, every vertex radius equals its box mean within 1 m. Values are compared within a stated
tolerance, never byte for byte (COMMON.md: platform-stable tests).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from services.contracts.provenance import AssetProvenanceRecord

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "apps" / "web" / "public" / "models"
LDEM_REL = ".cache/moon-svs4720/ldem_16_uint.tif"
KM_PER_UNIT = 173.74  # 10 units = 1737.4 km


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {rel}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bake = _load("bake_moon_mesh", "scripts/bake_moon_mesh.py")
vp = _load("verify_provenance_for_moon_mesh", "scripts/verify_provenance.py")


@pytest.fixture(scope="module")
def mesh() -> dict[str, np.ndarray]:
    return bake.read_glb((MODELS / bake.OUT_NAME).read_bytes())


def _lat_lon(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    r = np.linalg.norm(points, axis=1)
    return np.degrees(np.arcsin(points[:, 1] / r)), np.degrees(np.arctan2(-points[:, 2], points[:, 0]))


def _unit(lon_deg: float, lat_deg: float) -> np.ndarray:
    lon, lat = np.radians(lon_deg), np.radians(lat_deg)
    return np.array([np.cos(lat) * np.cos(lon), np.sin(lat), -np.cos(lat) * np.sin(lon)])


# =============================================================================================
# Pieces of the bake
# =============================================================================================


def test_box_means_wrap_the_seam_and_average_the_caps() -> None:
    """A made-up height field: column j holds j, row i adds 10,000 times i; each box mean is its centre's value."""
    rows, columns = 180 * 16, 360 * 16
    field = np.arange(columns, dtype=np.float64)[None, :] + 10_000.0 * np.arange(rows, dtype=np.float64)[:, None]
    means = bake.box_mean_heights(field)
    assert means.shape == (181, 360)
    # Interior boxes: row r spans field rows 16r - 8 to 16r + 7; column c spans 16c - 8 to 16c + 7, wrapping at c = 0
    assert means[1, 1] == pytest.approx(16 * 1 - 0.5 + 10_000 * (16 * 1 - 0.5))
    seam = np.concatenate([np.arange(columns - 8, columns), np.arange(8)]).mean()
    assert means[90, 0] == pytest.approx(seam + 10_000 * (16 * 90 - 0.5))
    assert np.all(means[0] == field[:8].mean()) and np.all(means[180] == field[-8:].mean())


def test_a_made_up_height_field_builds_the_conventions_at_true_scale() -> None:
    """Without the cache: a flat Moon at the file's zero height (1727.4 km) with one box 2,000 half-metres higher at
    latitude 0, longitude -179. The bump must stand exactly 1 km proud, and the UVs follow each vertex's position."""
    means = np.zeros((181, 360))
    means[90, 1] = 2000.0
    positions, normals, uvs, triangles = bake.build_mesh(means)
    assert positions.shape == (65_341, 3) and triangles.size == 3 * 128_880
    # Every vertex is used but the two pole copies at longitude +180 (indices 360 and 65,340): each pole band's
    # triangles take the copies of columns 0 to 359
    assert set(range(65_341)) - set(np.unique(triangles).tolist()) == {360, 65_340}
    radius_km = np.linalg.norm(positions.astype(np.float64), axis=1) * KM_PER_UNIT
    bump = 90 * 361 + 1
    assert radius_km[bump] == pytest.approx(1728.4, abs=1e-3)
    assert np.allclose(np.delete(radius_km, bump), 1727.4, atol=1e-3)
    lat, lon = _lat_lon(positions.astype(np.float64))
    assert lat[bump] == pytest.approx(0.0, abs=1e-4) and lon[bump] == pytest.approx(-179.0, abs=1e-4)
    off_pole = np.abs(lat) < 89.9
    du = np.abs(uvs[:, 0] - (lon + 180) / 360)[off_pole]
    assert np.max(np.minimum(du, 1 - du)) < 1e-6
    assert np.max(np.abs(uvs[:, 1] - (90 - lat) / 180)) < 1e-6
    # The seam's and the poles' copies share one normal, checked here without the cache
    grid = normals.reshape(181, 361, 3)
    assert np.array_equal(grid[:, 0], grid[:, 360])
    assert np.all(grid[0] == grid[0, 0]) and np.all(grid[180] == grid[180, 0])


# =============================================================================================
# The committed mesh, without the cache
# =============================================================================================


def test_layout_is_a_one_degree_grid_with_nothing_but_geometry(mesh: dict[str, np.ndarray]) -> None:
    assert set(mesh) == {"POSITION", "NORMAL", "TEXCOORD_0", "indices"}
    assert mesh["POSITION"].shape == (361 * 181, 3) and mesh["indices"].size == 3 * 128_880
    gltf_len = int.from_bytes((MODELS / bake.OUT_NAME).read_bytes()[12:16], "little")
    gltf = json.loads((MODELS / bake.OUT_NAME).read_bytes()[20 : 20 + gltf_len])
    assert "materials" not in gltf and "images" not in gltf and "textures" not in gltf


def test_uvs_follow_the_replaced_meshs_conventions(mesh: dict[str, np.ndarray]) -> None:
    """+y north, longitude 0 toward +x and 90 E toward -z, u = (longitude + 180) / 360, v = (90 - latitude) / 180."""
    lat, lon = _lat_lon(mesh["POSITION"].astype(np.float64))
    u, v = mesh["TEXCOORD_0"][:, 0].astype(np.float64), mesh["TEXCOORD_0"][:, 1].astype(np.float64)
    assert np.max(np.abs(v - (90 - lat) / 180)) < 1e-6
    off_pole = np.abs(lat) < 89.9
    du = np.abs(u - (lon + 180) / 360)[off_pole]
    assert np.max(np.minimum(du, 1 - du)) < 1e-6
    assert u.min() == 0.0 and u.max() == 1.0


def test_true_scale_relief_and_outward_normals(mesh: dict[str, np.ndarray]) -> None:
    """Measured on the committed mesh: radius 1729.6 to 1746.1 km (ldem_16 can hold 1727.4 to 1760.2 km), a 16.5 km
    span, under 1 percent of the radius; the replaced mesh spanned 2.7 percent."""
    points = mesh["POSITION"].astype(np.float64)
    radius_km = np.linalg.norm(points, axis=1) * KM_PER_UNIT
    assert 1727.4 <= radius_km.min() and radius_km.max() <= 1727.4 + 65535 * 0.0005
    assert abs(np.median(radius_km) - 1737.4) < 2.0
    assert (radius_km.max() - radius_km.min()) / 1737.4 < 0.01
    normals = mesh["NORMAL"].astype(np.float64)
    assert np.allclose(np.linalg.norm(normals, axis=1), 1.0, atol=1e-6)
    assert np.min(np.sum(normals * points, axis=1) / np.linalg.norm(points, axis=1)) > 0.9


def test_seam_and_pole_copies_share_one_normal(mesh: dict[str, np.ndarray]) -> None:
    """The seam column is doubled and each pole is a row of copies; their normals must match, or the light breaks there."""
    normals = mesh["NORMAL"].reshape(181, 361, 3)
    assert np.array_equal(normals[:, 0], normals[:, 360])
    assert np.all(normals[0] == normals[0, 0]) and np.all(normals[180] == normals[180, 0])
    assert normals[0, 0, 1] > 0.99 and normals[180, 0, 1] < -0.99  # +y north, -y south


@pytest.mark.parametrize(("place", "lon", "lat", "highest"), [("peak", -158.7, 5.4, True), ("deepest", -172.5, -70.3, False)])
def test_lolas_extremes_fall_on_extreme_vertices(mesh: dict[str, np.ndarray], place: str, lon: float, lat: float, highest: bool) -> None:
    """LOLA's highest and lowest points (158.7 W 5.4 N, 172.5 W 70.3 S, as bake_moon_maps.py finds them in the data)
    sit on vertices in the top or bottom 0.1 percent of radii (measured: 99.99th and 0.06th percentiles). Box means
    move the single most extreme vertex a few degrees away, so the test asks for the rank, not the place."""
    points = mesh["POSITION"].astype(np.float64)
    radius = np.linalg.norm(points, axis=1)
    nearest = int(np.argmax((points / radius[:, None]) @ _unit(lon, lat)))
    rank = float(np.mean(radius < radius[nearest]))
    assert rank > 0.999 if highest else rank < 0.001


# =============================================================================================
# Record, gaps
# =============================================================================================


def test_record_entry_names_lola_the_scale_the_size_and_the_caveat() -> None:
    record = AssetProvenanceRecord.model_validate_json((MODELS / "PROVENANCE.json").read_text(encoding="utf-8"))
    entry = next(f for f in record.files if f.path == bake.OUT_NAME)
    glb = (MODELS / bake.OUT_NAME).read_bytes()
    assert entry.sha256 == hashlib.sha256(glb).hexdigest()
    assert entry.sources == ("nasa-svs-4720-ldem-16",)
    assert entry.produced_by.script == "scripts/bake_moon_mesh.py"
    for words in ("true scale", "65,341 vertices", f"{len(glb):,} bytes", "10 units = 1737.4 km", "designed for use in 3D rendering software", "gridded data products", "no exaggeration"):
        assert words in (entry.note or "")
    textures = json.loads((ROOT / "apps" / "web" / "public" / "textures" / "PROVENANCE.json").read_text(encoding="utf-8"))
    same = next(s for s in textures["sources"] if s["id"] == "nasa-svs-4720-ldem-16")
    ours = next(s for s in record.model_dump(mode="json", by_alias=True, exclude_none=True)["sources"] if s["id"] == "nasa-svs-4720-ldem-16")
    assert ours == same


def test_the_record_rebuilds_without_the_cache() -> None:
    """The models record is what this bake writes from the committed mesh and the other bakes' entries: LOLA first,
    so the Local Bubble bakes, which keep their source last, rebuild the same record."""
    from services.contracts.provenance import dumps_record

    text = (MODELS / "PROVENANCE.json").read_text(encoding="utf-8")
    existing = AssetProvenanceRecord.model_validate_json(text)
    assert text == dumps_record(bake.build_record(existing, (MODELS / bake.OUT_NAME).read_bytes()))
    assert [s.id for s in existing.sources][0] == "nasa-svs-4720-ldem-16"


def test_the_gap_for_the_moon_is_closed() -> None:
    gaps = {g["path"] for g in json.loads((ROOT / "scripts" / "provenance_gaps.json").read_text(encoding="utf-8"))["gaps"]}
    assert gaps == set(vp.BASELINE_GAPS) == {"apps/web/public/textures/bruneton_atmo_lut.png"}


def test_doc_states_source_scale_conventions_and_caveat() -> None:
    doc = (ROOT / "docs" / "science" / "MOON_MESH.md").read_text(encoding="utf-8")
    assert "\u2014" not in doc
    for required in ("ldem_16_uint.tif", "SVS 4720", "1727.4 km", "10 units = 1737.4 km", "65,341", "128,880",
                     "designed for use in 3D rendering software", "colour map", "+y", "(longitude + 180) / 360",
                     "(90 - latitude) / 180"):  # fmt: skip
        assert required in doc
    # SVS's "optimized for aesthetics, not science" belongs to its colour map: the doc may name it only beside that map
    for at in [i for i in range(len(doc)) if doc.startswith("aesthetics", i)]:
        assert "colour map" in doc[at : at + 120], doc[at - 80 : at + 120]


# =============================================================================================
# Real data: the cached heights
# =============================================================================================


@pytest.mark.local_dataset(LDEM_REL)
def test_every_vertex_radius_is_its_box_mean(mesh: dict[str, np.ndarray]) -> None:
    heights = bake.read_heights(bake.maps.pinned_input(ROOT / ".cache" / "moon-svs4720", bake.maps.HEIGHT_INPUT))
    means = bake.box_mean_heights(heights)
    expected_m = bake.maps.HEIGHT_BASE_M + bake.maps.HEIGHT_STEP_M * np.concatenate([means, means[:, :1]], axis=1).reshape(-1)
    radius_m = np.linalg.norm(mesh["POSITION"].astype(np.float64), axis=1) * KM_PER_UNIT * 1000
    assert np.max(np.abs(radius_m - expected_m)) < 1.0


@pytest.mark.local_dataset(LDEM_REL)
def test_rebake_matches_within_tolerance_and_the_record_rebuilds() -> None:
    assert bake.verify_outputs(ROOT / ".cache" / "moon-svs4720", MODELS) == []
