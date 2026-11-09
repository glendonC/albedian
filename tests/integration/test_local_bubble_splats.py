"""Local Bubble splats (data-8): `apps/web/public/models/lb_splats.json` rebaked from the O'Neill et al. (2024) shell table.

`scripts/bake_local_bubble_splats.py` writes one splat per vertex of `local_bubble_oneill2024.glb` from the table that
mesh came from (Harvard Dataverse doi:10.7910/DVN/INB1RB, CC0). The splats replace a legacy file made from a mesh of
unrecorded origin, so the provenance gap for lb_splats.json closes here.

What each value is, and what these tests hold it to:
- centre: the mesh vertex, exactly as float32 (checked without the table);
- orientation: the third splat axis along the table's shell normal, turned to point away from the Sun and averaged per
  cell (without the table: it points outward; with it: it is that mean normal);
- thickness along the normal: each row's line-of-sight thickness times |n . r|, averaged per cell, as a uniform
  slab's sigma, width / sqrt(12) (with the table; and on a made-up table without it);
- footprint across the normal: the cell's side at the vertex distance as a slab sigma, grid geometry and a
  presentation value (checked without the table);
- opacity: 1 - 10^(-0.4 A_G), with A_G the cell's mean integrated shell extinction (with the table), a presentation
  mapping of a measured quantity that the record and doc say is one;
- colour: one neutral grey, simulated.
Values are compared within half a rounding step, never byte for byte (COMMON.md: platform-stable tests).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

from services.contracts.provenance import AssetProvenanceRecord

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "apps" / "web" / "public" / "models"
FITS_REL = ".cache/dust/oneill2024/ONeill2024_LocalBubble_ShellProperties_A0.5.fits"
DOC = ROOT / "docs" / "science" / "LOCAL_BUBBLE_SPLATS.md"
N_CELLS = 12 * 48 * 48
HALF_STEP = 0.5e-4 + 1e-9  # half of the 4-decimal rounding step, and a hair for the binary float


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {rel}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bake = _load("bake_local_bubble_splats", "scripts/bake_local_bubble_splats.py")
vp = _load("verify_provenance_for_lb_splats", "scripts/verify_provenance.py")


@pytest.fixture(scope="module")
def rows() -> np.ndarray:
    text = (MODELS / "lb_splats.json").read_text(encoding="utf-8")
    assert text.count("\n") == 1 and text.endswith("\n")
    data = json.loads(text)
    assert len(data) == N_CELLS and all(len(r) == 14 for r in data)
    return np.array(data, dtype=np.float64)


@pytest.fixture(scope="module")
def vertices() -> np.ndarray:
    return bake.read_glb_positions((MODELS / "local_bubble_oneill2024.glb").read_bytes())


def _third_axes(quats: np.ndarray) -> np.ndarray:
    x, y, z, w = (quats[:, k] for k in range(4))
    return np.column_stack([2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y)])


# =============================================================================================
# Pieces of the bake
# =============================================================================================


def test_normals_turn_outward_before_they_are_averaged() -> None:
    radial = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    normals = np.array([[-0.6, 0.8, 0.0], [0.0, 0.6, 0.8]])
    assert np.allclose(bake.outward(normals, radial), [[0.6, -0.8, 0.0], [0.0, 0.6, 0.8]])


def test_frame_quaternions_put_the_third_axis_on_the_normal() -> None:
    normals = np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.6, 0.0, 0.8], [0.0, -0.6, -0.8]])
    q = bake.frame_quaternions(normals)
    assert np.allclose(np.linalg.norm(q, axis=1), 1.0)
    assert np.allclose(_third_axes(q), normals, atol=1e-12)


def test_a_synthetic_table_becomes_exactly_its_splats() -> None:
    """Made up, without the cache: two cells. Cell 0 has two rows on the line of sight r = +x with one shell normal,
    the second written inward, each 50 pc thick along the line of sight at |n . r| = 0.6, A_G 0.5 and 1.5; cell 1 has
    one row seen head-on, 20 pc thick, A_G 0.1."""
    n0 = np.array([0.6, 0.8, 0.0])
    radial = np.array([[1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    normal = np.array([n0, -n0, [0.0, 0.0, 1.0]])
    cells = bake.cell_means(np.array([0, 0, 1]), radial, normal, np.array([50.0, 50.0, 20.0]), np.array([0.5, 1.5, 0.1]), 2)
    assert np.allclose(cells["normal"], [n0, [0.0, 0.0, 1.0]])  # the inward one turned outward before averaging
    assert np.allclose(cells["thick_normal_pc"], [30.0, 20.0])  # 50 pc x 0.6 along the normal; head-on unchanged
    assert np.allclose(cells["ag_mag"], [1.0, 0.1]) and list(cells["rows"]) == [2, 1]
    rows = bake.splat_rows(np.array([[1.7, 0.0, 0.0], [0.0, 0.0, 2.0]], dtype=np.float32), cells, N_CELLS)
    slab = 1 / math.sqrt(12)
    assert rows[0][:6] == [1.7, 0.0, 0.0, bake.NEUTRAL_GREY, bake.NEUTRAL_GREY, bake.NEUTRAL_GREY]
    assert rows[0][6] == rows[0][7] == round(math.sqrt(4 * math.pi / N_CELLS) * 1.7 * slab, 4)
    assert (rows[0][8], rows[1][8]) == (round(0.30 * slab, 4), round(0.20 * slab, 4))
    assert rows[0][13] == pytest.approx(round(1 - 10 ** (-0.4 * 1.0), 4), abs=1e-12) == 0.6019  # magnitudes: 0.4
    assert np.allclose(_third_axes(np.array([rows[0][9:13]])), [n0], atol=2e-4)


def test_centres_are_written_as_the_shortest_decimal_of_their_float32() -> None:
    for value in (np.float32(1.2345678), np.float32(-0.1), np.float32(3.0)):
        text = bake.f32_text(value)
        assert np.float32(text) == value and len(repr(text)) <= len(repr(float(value)))


# =============================================================================================
# The committed splats, without the table
# =============================================================================================


def test_one_splat_sits_on_each_mesh_vertex_exactly(rows: np.ndarray, vertices: np.ndarray) -> None:
    assert vertices.shape == (N_CELLS, 3)
    assert np.array_equal(rows[:, :3].astype(np.float32), vertices)


def test_colour_is_one_neutral_grey(rows: np.ndarray) -> None:
    assert np.all(rows[:, 3:6] == bake.NEUTRAL_GREY)


def test_orientation_points_away_from_the_sun(rows: np.ndarray, vertices: np.ndarray) -> None:
    q = rows[:, 9:13]
    assert np.allclose(np.linalg.norm(q, axis=1), 1.0, atol=3e-4)
    axes = _third_axes(q / np.linalg.norm(q, axis=1, keepdims=True))
    radial = vertices / np.linalg.norm(vertices, axis=1, keepdims=True)
    assert np.all(np.sum(axes * radial, axis=1) > 0.0)


def test_footprint_across_the_shell_is_the_cell_side_as_a_slab_sigma(rows: np.ndarray, vertices: np.ndarray) -> None:
    side = math.sqrt(4 * math.pi / N_CELLS) * np.linalg.norm(vertices.astype(np.float64), axis=1)
    expected = side / math.sqrt(12)
    assert np.all(np.abs(rows[:, 6] - expected) <= HALF_STEP)
    assert np.array_equal(rows[:, 6], rows[:, 7])


def test_thickness_and_opacity_stay_within_the_papers_ranges(rows: np.ndarray) -> None:
    """O'Neill et al. (2024) Table 1: line-of-sight thickness 3 to 357 pc, A_G below 0.001 to 2.8 mag over all
    directions. Projected onto the normal the thickness can only shrink, so it stays under 357 pc."""
    thickness_pc = rows[:, 8] * math.sqrt(12) * 100
    assert thickness_pc.min() >= 0 and thickness_pc.max() <= 357 + 0.02
    opacity = rows[:, 13]
    assert opacity.min() >= 0 and opacity.max() <= 1 - 10 ** (-0.4 * 2.8)


# =============================================================================================
# Record, gaps and doc
# =============================================================================================


def test_record_entry_states_what_is_measured_and_what_is_presentation() -> None:
    record = AssetProvenanceRecord.model_validate_json((MODELS / "PROVENANCE.json").read_text(encoding="utf-8"))
    entry = next(f for f in record.files if f.path == "lb_splats.json")
    assert entry.sha256 == hashlib.sha256((MODELS / "lb_splats.json").read_bytes()).hexdigest()
    assert entry.sources == ("oneill-2024-local-bubble",)
    assert entry.produced_by.script == "scripts/bake_local_bubble_splats.py"
    assert entry.note == bake.SPLATS_NOTE  # the committed record says what the bake says, checked without the cache
    for words in ("Measured:", "line-of-sight thickness times |n . r|", "peak distance d", "19 to 36 rows",
                  "Grid geometry, a presentation value", "Presentation mapping of a measured quantity",
                  "1 - 10^(-0.4 A_G)", "Simulated:", "neutral grey"):  # fmt: skip
        assert words in (entry.note or "")
    assert [f.path for f in record.files] == ["lb_splats.json", "local_bubble_oneill2024.glb"]


def test_the_gap_for_lb_splats_is_closed() -> None:
    gaps = {g["path"] for g in json.loads((ROOT / "scripts" / "provenance_gaps.json").read_text(encoding="utf-8"))["gaps"]}
    assert gaps == set(vp.BASELINE_GAPS) == {
        "apps/web/public/models/nasa_lro_moon_3d.glb",
        "apps/web/public/textures/bruneton_atmo_lut.png",
    }


def test_doc_states_sources_mapping_and_limits() -> None:
    doc = DOC.read_text(encoding="utf-8")
    assert "\u2014" not in doc
    for required in ("doi:10.7910/DVN/INB1RB", "CC0", "arXiv:2403.04961", "1 - 10^(-0.4 A_G)", "sqrt(12)",
                     "neutral grey", "simulated", "27,648", "100 pc", "19 to 36", "|n . r|", "grid geometry",
                     "peak distance"):  # fmt: skip
        assert required in doc


# =============================================================================================
# Real data: the cached shell table
# =============================================================================================


@pytest.mark.local_dataset(FITS_REL)
def test_axes_thickness_and_opacity_are_the_tables_cell_means(rows: np.ndarray) -> None:
    cols = bake.mesh.read_shell_fits_table(bake.mesh.verify_input(ROOT / ".cache" / "dust" / "oneill2024"))
    cells = bake.coarse_cell_means(cols)
    assert int(cells["rows"].sum()) == 786_432 - 40  # every finite row of the table, the 40 blank ones left out
    axes = _third_axes(rows[:, 9:13] / np.linalg.norm(rows[:, 9:13], axis=1, keepdims=True))
    assert np.max(np.linalg.norm(axes - cells["normal"], axis=1)) < 1e-3
    assert np.all(np.abs(rows[:, 8] - cells["thick_normal_pc"] / 100 / math.sqrt(12)) <= HALF_STEP)
    assert cells["rows"].min() == 19 and cells["rows"].max() == 36 and np.median(cells["rows"]) == 30
    assert np.all(np.abs(rows[:, 13] - (1 - 10 ** (-0.4 * cells["ag_mag"]))) <= HALF_STEP)


@pytest.mark.local_dataset(FITS_REL)
def test_rebake_matches_within_rounding_and_the_record_rebuilds() -> None:
    assert bake.verify_outputs(ROOT / ".cache" / "dust" / "oneill2024", MODELS) == []
