"""Tests for the O'Neill et al. (2024) Local Bubble 3D mesh bake (`data-1e`).

`scripts/bake_local_bubble_mesh.py` reads the Harvard Dataverse FITS table
`ONeill2024_LocalBubble_ShellProperties_A0.5.fits` (786,432 HEALPix `nside=256`
sky directions with heliocentric Galactic coordinates and shell distances in pc),
aggregates valid directions onto a closed `nside=48` HEALPix 2-manifold
(27,648 vertices, 55,292 outward triangles), converts pc to scene units
(`1 unit = 100 pc`), and writes `apps/web/public/models/local_bubble_oneill2024.glb`
along with `apps/web/public/models/PROVENANCE.json`.
"""

from __future__ import annotations

import importlib.util
import json
import math
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

from services.contracts.provenance import AssetProvenanceRecord, sha256_file

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "bake_local_bubble_mesh.py"
MODELS_DIR = ROOT / "apps" / "web" / "public" / "models"
GLB_PATH = MODELS_DIR / "local_bubble_oneill2024.glb"
PROV_PATH = MODELS_DIR / "PROVENANCE.json"
CACHE_DIR = ROOT / ".cache" / "dust" / "oneill2024"
CACHE_FITS = ".cache/dust/oneill2024/ONeill2024_LocalBubble_ShellProperties_A0.5.fits"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bake = _load_module("bake_local_bubble_mesh", SCRIPT_PATH)


def _parse_glb(path: Path) -> tuple[dict, np.ndarray, np.ndarray, np.ndarray]:
    """Parse a glTF 2.0 binary `.glb` and return `(gltf_json, positions, normals, triangles)`."""
    raw = path.read_bytes()
    magic, version, total_len = struct.unpack_from("<4sII", raw, 0)
    assert magic == b"glTF"
    assert version == 2
    assert total_len == len(raw)

    json_len, json_type = struct.unpack_from("<II", raw, 12)
    assert json_type == 0x4E4F534A  # JSON
    gltf = json.loads(raw[20 : 20 + json_len].decode("utf-8"))

    bin_offset = 20 + json_len
    bin_len, bin_type = struct.unpack_from("<II", raw, bin_offset)
    assert bin_type == 0x004E4942  # BIN
    bin_chunk = raw[bin_offset + 8 : bin_offset + 8 + bin_len]

    views = gltf["bufferViews"]
    accessors = gltf["accessors"]
    prim = gltf["meshes"][0]["primitives"][0]

    pos_acc = accessors[prim["attributes"]["POSITION"]]
    norm_acc = accessors[prim["attributes"]["NORMAL"]]
    idx_acc = accessors[prim["indices"]]

    pos_view = views[pos_acc["bufferView"]]
    norm_view = views[norm_acc["bufferView"]]
    idx_view = views[idx_acc["bufferView"]]

    positions = np.frombuffer(
        bin_chunk,
        dtype="<f4",
        count=pos_acc["count"] * 3,
        offset=pos_view.get("byteOffset", 0),
    ).reshape((pos_acc["count"], 3))
    normals = np.frombuffer(
        bin_chunk,
        dtype="<f4",
        count=norm_acc["count"] * 3,
        offset=norm_view.get("byteOffset", 0),
    ).reshape((norm_acc["count"], 3))
    indices = np.frombuffer(
        bin_chunk,
        dtype="<u2",
        count=idx_acc["count"],
        offset=idx_view.get("byteOffset", 0),
    ).reshape((-1, 3))
    return gltf, positions.copy(), normals.copy(), indices.copy()


def _write_synthetic_shell_fits(
    path: Path,
    *,
    nside_in: int = 16,
    nan_rows: int = 5,
    columns: tuple[str, ...] = ("x", "y", "z", "l", "b", "d"),
    row_override: int | None = None,
    duplicate_first_row: bool = False,
    force_all_nan_first_cell: bool = False,
    force_nonpositive_d: bool = False,
) -> None:
    """Write a minimal FITS file with a primary HDU and a BINTABLE HDU 1 of float64 columns."""
    n_rows = row_override if row_override is not None else 12 * nside_in * nside_in
    faces = np.repeat(np.arange(12, dtype=np.int64), nside_in * nside_in)
    ij = np.arange(nside_in * nside_in, dtype=np.int64)
    ix = np.tile(ij // nside_in, 12)
    iy = np.tile(ij % nside_in, 12)

    if n_rows != len(faces):
        faces = np.resize(faces, n_rows)
        ix = np.resize(ix, n_rows)
        iy = np.resize(iy, n_rows)

    if duplicate_first_row and n_rows >= 2:
        faces[1], ix[1], iy[1] = faces[0], ix[0], iy[0]

    fx = (ix.astype(np.float64) + 0.5) / nside_in
    fy = (iy.astype(np.float64) + 0.5) / nside_in
    ux, uy, uz = bake.face_xy_to_vec(faces, fx, fy)
    l_deg = (np.degrees(np.arctan2(uy, ux)) + 360.0) % 360.0
    b_deg = np.degrees(np.arcsin(np.clip(uz, -1.0, 1.0)))
    d_pc = 200.0 + 50.0 * uz

    if nan_rows > 0:
        d_pc[:nan_rows] = np.nan
    if force_all_nan_first_cell:
        # For nside_in=16 -> nside_out=4, each coarse cell holds a 4x4 block of fine pixels.
        mask = (faces == 0) & (ix < 4) & (iy < 4)
        d_pc[mask] = np.nan
    if force_nonpositive_d:
        d_pc[-1] = -10.0

    x_pc = d_pc * ux
    y_pc = d_pc * uy
    z_pc = d_pc * uz

    col_arrays = {
        "x": x_pc,
        "y": y_pc,
        "z": z_pc,
        "l": l_deg,
        "b": b_deg,
        "d": d_pc,
    }

    def _pad_header(cards: list[str]) -> bytes:
        lines = [c.ljust(80)[:80] for c in cards]
        lines.append("END".ljust(80))
        while len(lines) % 36 != 0:
            lines.append(" " * 80)
        return "".join(lines).encode("ascii")

    primary_hdr = _pad_header(
        [
            "SIMPLE  =                    T",
            "BITPIX  =                    8",
            "NAXIS   =                    0",
            "EXTEND  =                    T",
        ]
    )

    n_cols = len(columns)
    row_bytes = n_cols * 8
    ext_cards = [
        "XTENSION= 'BINTABLE'",
        "BITPIX  =                    8",
        "NAXIS   =                    2",
        f"NAXIS1  = {row_bytes:20d}",
        f"NAXIS2  = {n_rows:20d}",
        "PCOUNT  =                    0",
        "GCOUNT  =                    1",
        f"TFIELDS = {n_cols:20d}",
    ]
    for idx, col_name in enumerate(columns, start=1):
        ext_cards.append(f"TTYPE{idx:<3d}= '{col_name}'")
        ext_cards.append(f"TFORM{idx:<3d}= 'D'")
    ext_hdr = _pad_header(ext_cards)

    table = np.empty((n_rows, n_cols), dtype=">f8")
    for idx, col_name in enumerate(columns):
        table[:, idx] = col_arrays[col_name]
    data_bytes = table.tobytes()
    pad_len = (-len(data_bytes)) % 2880
    if pad_len:
        data_bytes += b"\x00" * pad_len

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(primary_hdr + ext_hdr + data_bytes)


def _assert_watertight_outward_manifold(positions: np.ndarray, triangles: np.ndarray, nside: int) -> None:
    expected_v = 12 * nside * nside
    expected_f = 24 * nside * nside - 4
    expected_e = 36 * nside * nside - 6
    assert positions.shape == (expected_v, 3)
    assert triangles.shape == (expected_f, 3)

    # No degenerate triangles, and every vertex appears at least 3 times.
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    assert np.all((a != b) & (b != c) & (c != a))
    vertex_counts = np.bincount(triangles.ravel(), minlength=expected_v)
    assert int(vertex_counts.min()) >= 3

    # Vectorised half-edge check: every directed edge (u, v) is unique, and its reverse (v, u) exists.
    u = np.concatenate([a, b, c]).astype(np.int64)
    v = np.concatenate([b, c, a]).astype(np.int64)
    directed_keys = u * expected_v + v
    reverse_keys = v * expected_v + u
    sorted_directed = np.sort(directed_keys)
    assert len(sorted_directed) == 2 * expected_e
    assert np.all(np.diff(sorted_directed) != 0), "duplicate directed half-edge found"
    assert np.array_equal(sorted_directed, np.sort(reverse_keys)), "unpaired boundary half-edge found"

    # Euler characteristic V - E + F == 2.
    assert expected_v - expected_e + expected_f == 2

    # Every triangle's right-hand normal points outward from the heliocentric origin.
    v0 = positions[a]
    v1 = positions[b]
    v2 = positions[c]
    cross = np.cross(v1 - v0, v2 - v0)
    center = (v0 + v1 + v2) / 3.0
    dots = np.einsum("ij,ij->i", cross, center)
    assert np.all(dots > 0.0), f"{int(np.sum(dots <= 0.0))} inward or degenerate triangles"


def test_healpix_triangulation_is_closed_outward_two_manifold():
    nside = 4
    n_verts = 12 * nside * nside
    faces = np.repeat(np.arange(12, dtype=np.int64), nside * nside)
    ij = np.arange(nside * nside, dtype=np.int64)
    ix = np.tile(ij // nside, 12)
    iy = np.tile(ij % nside, 12)
    ux, uy, uz = bake.face_xy_to_vec(faces, (ix + 0.5) / nside, (iy + 0.5) / nside)
    unit_pos = np.column_stack([ux, uy, uz]).astype(np.float32)

    tris = bake.build_healpix_triangles(nside, unit_pos)
    assert tris.dtype == np.uint16
    assert len(unit_pos) == n_verts
    _assert_watertight_outward_manifold(unit_pos, tris, nside)


def test_synthetic_shell_fits_round_trip_and_nan_infill(tmp_path: Path):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "models"
    fits_path = in_dir / bake.INPUT.filename
    _write_synthetic_shell_fits(fits_path, nside_in=16, nan_rows=5)

    summary = bake.bake_local_bubble_mesh(
        in_dir,
        out_dir,
        nside_in=16,
        nside_out=4,
        verify_input_hashes=False,
    )
    assert summary["vertices"] == 192
    assert summary["triangles"] == 380
    assert summary["nanInputPixels"] == 5

    glb_path = out_dir / "local_bubble_oneill2024.glb"
    prov_path = out_dir / "PROVENANCE.json"
    assert glb_path.is_file()
    assert prov_path.is_file()

    gltf, positions, normals, triangles = _parse_glb(glb_path)
    assert gltf["asset"]["version"] == "2.0"
    _assert_watertight_outward_manifold(positions, triangles, nside=4)

    assert np.all(np.isfinite(positions))
    assert np.all(np.isfinite(normals))

    radii = np.linalg.norm(positions, axis=1)
    unit_pos = positions / radii[:, None]
    expected_radii = (200.0 + 50.0 * unit_pos[:, 2]) / bake.PC_PER_UNIT
    assert np.max(np.abs(radii - expected_radii)) < 0.03

    norm_lengths = np.linalg.norm(normals, axis=1)
    assert np.max(np.abs(norm_lengths - 1.0)) < 1e-4
    outward_cos = np.einsum("ij,ij->i", normals, unit_pos)
    assert float(np.min(outward_cos)) > 0.85

    record = AssetProvenanceRecord.model_validate(json.loads(prov_path.read_text("utf-8")))
    assert record.files[0].path == "local_bubble_oneill2024.glb"
    assert record.files[0].sha256 == sha256_file(glb_path)
    assert bake.verify_outputs(in_dir, out_dir, nside_in=16, nside_out=4, verify_input_hashes=False) == []


def test_bake_refuses_corrupt_fits_inputs(tmp_path: Path):
    out_dir = tmp_path / "models"

    # 1. Missing required column `d`.
    bad_cols = tmp_path / "bad_cols"
    _write_synthetic_shell_fits(bad_cols / bake.INPUT.filename, nside_in=4, columns=("x", "y", "z", "l", "b"))
    with pytest.raises(ValueError, match="missing required FITS column"):
        bake.bake_local_bubble_mesh(bad_cols, out_dir, nside_in=4, nside_out=2, verify_input_hashes=False)

    # 2. Wrong row count for nside_in.
    bad_rows = tmp_path / "bad_rows"
    _write_synthetic_shell_fits(bad_rows / bake.INPUT.filename, nside_in=4, row_override=100)
    with pytest.raises(ValueError, match="expected 192 rows"):
        bake.bake_local_bubble_mesh(bad_rows, out_dir, nside_in=4, nside_out=2, verify_input_hashes=False)

    # 3. Duplicate fine HEALPix pixel coverage.
    dup_pix = tmp_path / "dup_pix"
    _write_synthetic_shell_fits(dup_pix / bake.INPUT.filename, nside_in=4, duplicate_first_row=True)
    with pytest.raises(ValueError, match="1-to-1 HEALPix grid"):
        bake.bake_local_bubble_mesh(dup_pix, out_dir, nside_in=4, nside_out=2, verify_input_hashes=False)

    # 4. Coarse cell with all NaN fine pixels.
    all_nan = tmp_path / "all_nan"
    _write_synthetic_shell_fits(
        all_nan / bake.INPUT.filename,
        nside_in=16,
        nan_rows=0,
        force_all_nan_first_cell=True,
    )
    with pytest.raises(ValueError, match="empty or all-NaN"):
        bake.bake_local_bubble_mesh(all_nan, out_dir, nside_in=16, nside_out=4, verify_input_hashes=False)

    # 5. Non-positive shell distance.
    neg_d = tmp_path / "neg_d"
    _write_synthetic_shell_fits(
        neg_d / bake.INPUT.filename,
        nside_in=4,
        nan_rows=0,
        force_nonpositive_d=True,
    )
    with pytest.raises(ValueError, match="positive shell distance"):
        bake.bake_local_bubble_mesh(neg_d, out_dir, nside_in=4, nside_out=2, verify_input_hashes=False)


def test_committed_local_bubble_glb_and_provenance_are_valid():
    assert GLB_PATH.is_file(), f"missing {GLB_PATH}"
    assert PROV_PATH.is_file(), f"missing {PROV_PATH}"

    record = AssetProvenanceRecord.model_validate(json.loads(PROV_PATH.read_text("utf-8")))
    assert len(record.sources) == 1
    src = record.sources[0]
    assert src.id == "oneill-2024-local-bubble"
    assert src.license.spdx == "CC0-1.0"
    assert src.doi == "10.7910/DVN/INB1RB"
    assert "10.3847/1538-4357/ad47b5" in (src.citation or "")
    assert src.sha256 == bake.INPUT.sha256

    files_by_path = {f.path: f for f in record.files}
    assert "local_bubble_oneill2024.glb" in files_by_path
    glb_entry = files_by_path["local_bubble_oneill2024.glb"]
    assert glb_entry.sha256 == sha256_file(GLB_PATH)
    assert glb_entry.sources == ("oneill-2024-local-bubble",)
    assert glb_entry.produced_by.method == "script"
    assert glb_entry.produced_by.script == "scripts/bake_local_bubble_mesh.py"

    gltf, positions, normals, triangles = _parse_glb(GLB_PATH)
    assert gltf["nodes"][0]["name"] == "LocalBubble_ONeill2024"
    _assert_watertight_outward_manifold(positions, triangles, nside=bake.DEFAULT_NSIDE_OUT)

    assert np.all(np.isfinite(positions))
    assert np.all(np.isfinite(normals))

    radii = np.linalg.norm(positions, axis=1)
    assert 0.70 <= float(radii.min()) <= 0.85
    assert 5.50 <= float(radii.max()) <= 6.20
    assert 1.60 <= float(np.median(radii)) <= 1.80

    unit_pos = positions / radii[:, None]
    norm_lengths = np.linalg.norm(normals, axis=1)
    assert float(np.max(np.abs(norm_lengths - 1.0))) < 1e-4
    outward_cos = np.einsum("ij,ij->i", normals, unit_pos)
    assert float(np.min(outward_cos)) > 0.0
    assert float(np.median(outward_cos)) > 0.80

    # O'Neill et al. (2024) Northern Galactic Chimney extends substantially further than the plane.
    sin_b = unit_pos[:, 2]
    north_cap = radii[sin_b > math.sin(math.radians(60.0))]
    plane_belt = radii[np.abs(sin_b) < math.sin(math.radians(20.0))]
    assert float(np.mean(north_cap)) > float(np.mean(plane_belt)) * 1.20
    assert float(np.max(north_cap)) > 5.0

    # The provenance gap for local_bubble_oneill2024.glb is closed in both files.
    gaps_json = json.loads((ROOT / "scripts" / "provenance_gaps.json").read_text("utf-8"))
    gap_paths = {g["path"] for g in gaps_json["gaps"]}
    assert "apps/web/public/models/local_bubble_oneill2024.glb" not in gap_paths

    vp = _load_module("verify_provenance_for_lb", ROOT / "scripts" / "verify_provenance.py")
    assert "apps/web/public/models/local_bubble_oneill2024.glb" not in vp.BASELINE_GAPS
    assert len(vp.BASELINE_GAPS) == 2

    # data/downloads.json registers the Harvard Dataverse FITS input with matching size & sha256.
    downloads = json.loads((ROOT / "data" / "downloads.json").read_text("utf-8"))["downloads"]
    by_path = {entry["path"]: entry for entry in downloads}
    assert CACHE_FITS in by_path
    dl_entry = by_path[CACHE_FITS]
    assert dl_entry["bytes"] == bake.INPUT.size
    assert dl_entry["sha256"] == bake.INPUT.sha256
    assert dl_entry["licence"] == "CC0-1.0"


@pytest.mark.local_dataset(CACHE_FITS)
def test_cached_oneill2024_fits_matches_baked_mesh():
    fits_path = ROOT / CACHE_FITS
    assert fits_path.stat().st_size == bake.INPUT.size
    assert sha256_file(fits_path) == bake.INPUT.sha256
    assert bake.verify_outputs(CACHE_DIR, MODELS_DIR) == []
