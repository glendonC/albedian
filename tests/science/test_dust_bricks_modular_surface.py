"""Surface and behavior characterization for `scripts.bake_dust_bricks`.

Pins the public and test-imported constants, dataclasses, FITS binary readers,
HEALPix dust-map sampling and exact radial column integration, sparse Cartesian
brick packing, thresholding, ray-marching, two-level block-mean residuals,
little-endian Fortran-order `.f16` serialization, and CLI execution using purely
synthetic FITS inputs with no `.cache/dust` downloads required.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from dataclasses import fields, is_dataclass
from pathlib import Path

import numpy as np
import pytest

import scripts.bake_dust_bricks as bdb
from services.science import catalog_tiles as ct

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "bake_dust_bricks.py"

EXPECTED_CONSTANTS = (
    "ROOT_DIR",
    "DUST_CACHE",
    "EDENHOFER_PATH",
    "ONEILL_SHELL_PATH",
    "DEFAULT_OUT",
    "A_V_PER_ZGR23",
    "FITS_BLOCK",
    "_FITS_DTYPES",
    "Sampler",
)
EXPECTED_CLASSES = (
    "FitsHdu",
    "HealpixDustMap",
    "BrickBuild",
    "TwoLevel",
)
EXPECTED_FUNCTIONS = (
    "_card_value",
    "read_fits_hdus",
    "_named",
    "fits_image",
    "fits_float_column",
    "load_healpix_dust_map",
    "sample_map",
    "map_columns",
    "map_column",
    "build_bricks",
    "brick_column",
    "two_level",
    "two_level_column",
    "write_bricks",
    "peak_rss_gb",
    "random_directions",
    "pilot_map",
    "pilot_shell",
    "main",
)


def _load_via_spec(module_name: str = "bake_dust_bricks_spec_check"):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def _fits_card(key: str, value: object | None = None, comment: str | None = None) -> bytes:
    if value is None:
        return key.ljust(80).encode("ascii")
    if isinstance(value, bool):
        body = f"{'T' if value else 'F':>20}"
    elif isinstance(value, (int, float)):
        body = f"{value:>20}"
    else:
        escaped = str(value).replace("'", "''")
        body = f"'{escaped:<8}'"
    if comment:
        body = f"{body} / {comment}"
    return f"{key:<8}= {body}".ljust(80).encode("ascii")


def _fits_header_bytes(cards: list[bytes]) -> bytes:
    raw = b"".join(cards) + b"END".ljust(80)
    return raw + b" " * (-len(raw) % 2880)


def _fits_payload_bytes(raw: bytes, pad_byte: bytes = b"\0") -> bytes:
    return raw + pad_byte * (-len(raw) % 2880)


def _write_synthetic_edenhofer(
    path: Path,
    mean: np.ndarray,
    std: np.ndarray,
    boundaries: np.ndarray,
    *,
    nside: int,
    ordering: str = "NEST",
) -> None:
    shells, pixels = mean.shape

    def image_hdu(name: str, arr: np.ndarray) -> bytes:
        cards = [
            _fits_card("XTENSION", "IMAGE"),
            _fits_card("BITPIX", -32),
            _fits_card("NAXIS", 2),
            _fits_card("NAXIS1", pixels),
            _fits_card("NAXIS2", shells),
            _fits_card("PCOUNT", 0),
            _fits_card("GCOUNT", 1),
            _fits_card("EXTNAME", name),
            _fits_card("NSIDE", nside),
            _fits_card("ORDERING", ordering),
        ]
        return _fits_header_bytes(cards) + _fits_payload_bytes(arr.astype(">f4").tobytes())

    table_cards = [
        _fits_card("XTENSION", "BINTABLE"),
        _fits_card("BITPIX", 8),
        _fits_card("NAXIS", 2),
        _fits_card("NAXIS1", 4),
        _fits_card("NAXIS2", len(boundaries)),
        _fits_card("PCOUNT", 0),
        _fits_card("GCOUNT", 1),
        _fits_card("TFIELDS", 1),
        _fits_card("TTYPE1", "radial pixel boundaries"),
        _fits_card("TFORM1", "E"),
        _fits_card("EXTNAME", "RADIAL PIXEL BOUNDARIES"),
    ]
    primary = _fits_header_bytes(
        [
            _fits_card("SIMPLE", True),
            _fits_card("BITPIX", 8),
            _fits_card("NAXIS", 0),
            _fits_card("EXTEND", True),
        ]
    )
    table = _fits_header_bytes(table_cards) + _fits_payload_bytes(boundaries.astype(">f4").tobytes())
    path.write_bytes(primary + image_hdu("MEAN", mean) + image_hdu("STD.", std) + table)


def _write_synthetic_shell(path: Path, cube: np.ndarray, *, cdelt1: float = 2.0) -> None:
    nz, ny, nx = cube.shape
    primary = _fits_header_bytes(
        [
            _fits_card("SIMPLE", True),
            _fits_card("BITPIX", 8),
            _fits_card("NAXIS", 0),
            _fits_card("EXTEND", True),
        ]
    )
    shell_cards = [
        _fits_card("XTENSION", "IMAGE"),
        _fits_card("BITPIX", -32),
        _fits_card("NAXIS", 3),
        _fits_card("NAXIS1", nx),
        _fits_card("NAXIS2", ny),
        _fits_card("NAXIS3", nz),
        _fits_card("PCOUNT", 0),
        _fits_card("GCOUNT", 1),
        _fits_card("CDELT1", cdelt1),
        _fits_card("EXTNAME", "SHELL"),
    ]
    path.write_bytes(primary + _fits_header_bytes(shell_cards) + _fits_payload_bytes(cube.astype(">f4").tobytes()))


# =============================================================================================
# 1. Public and test-imported module surface
# =============================================================================================


def test_public_and_test_imported_surface_on_package_and_spec_load() -> None:
    spec_mod = _load_via_spec()
    for mod in (bdb, spec_mod):
        assert isinstance(mod.__doc__, str) and "Edenhofer" in mod.__doc__ and "ZGR23" in mod.__doc__
        for name in (*EXPECTED_CONSTANTS, *EXPECTED_CLASSES, *EXPECTED_FUNCTIONS):
            assert hasattr(mod, name), f"missing {name} on {mod.__name__}"
        for cls_name in EXPECTED_CLASSES:
            cls = getattr(mod, cls_name)
            assert isinstance(cls, type) and is_dataclass(cls)
        for fn_name in EXPECTED_FUNCTIONS:
            assert callable(getattr(mod, fn_name))

    assert bdb.ROOT_DIR == ROOT
    assert bdb.DUST_CACHE == ROOT / ".cache" / "dust"
    assert bdb.EDENHOFER_PATH == bdb.DUST_CACHE / "edenhofer2024" / "mean_and_std_healpix.fits"
    assert bdb.ONEILL_SHELL_PATH == bdb.DUST_CACHE / "oneill2024" / "ONeill2024_LocalBubble_Shell_xyz.fits"
    assert bdb.DEFAULT_OUT == bdb.DUST_CACHE / "bricks-pilot"
    assert bdb.A_V_PER_ZGR23 == 2.8
    assert bdb.FITS_BLOCK == 2880
    assert bdb._FITS_DTYPES == {8: ">u1", 16: ">i2", 32: ">i4", 64: ">i8", -32: ">f4", -64: ">f8"}
    assert bdb.ct is ct and spec_mod.ct is ct

    assert [f.name for f in fields(bdb.FitsHdu)] == ["header", "data_offset", "shape", "bitpix"]
    assert [f.name for f in fields(bdb.HealpixDustMap)] == ["mean", "std", "boundaries_pc", "order"]
    assert [f.name for f in fields(bdb.BrickBuild)] == ["voxel_pc", "brick", "n_voxels", "bricks", "peaks", "seconds"]
    assert [f.name for f in fields(bdb.TwoLevel)] == ["factor", "base", "residual_peaks"]


# =============================================================================================
# 2. FITS binary header, image, and float-column parsing
# =============================================================================================


def test_card_value_parses_quoted_strings_escaped_quotes_and_comments() -> None:
    quoted = _fits_card("EXTNAME", "O'Neill / Shell", comment="ignored").decode("ascii")
    assert bdb._card_value(quoted) == "O'Neill / Shell"
    numeric = _fits_card("NAXIS1", 786432, comment="pixels").decode("ascii")
    assert bdb._card_value(numeric) == "786432"
    padded = f"{'ORDERING':<8}= 'NEST    ' / healpix scheme".ljust(80)
    assert bdb._card_value(padded) == "NEST"


def test_fits_reader_handles_multiblock_headers_all_bitpix_dtypes_and_errors(tmp_path: Path) -> None:
    comment_cards = [_fits_card(f"COMMENT item {i}") for i in range(45)]
    primary_cards = [
        _fits_card("SIMPLE", True),
        _fits_card("BITPIX", 8),
        _fits_card("NAXIS", 0),
        _fits_card("EXTEND", True),
        *comment_cards,
    ]
    cube_i16 = np.arange(24, dtype=np.int16).reshape(2, 3, 4)
    img_i16_cards = [
        _fits_card("XTENSION", "IMAGE"),
        _fits_card("BITPIX", 16),
        _fits_card("NAXIS", 3),
        _fits_card("NAXIS1", 4),
        _fits_card("NAXIS2", 3),
        _fits_card("NAXIS3", 2),
        _fits_card("EXTNAME", "INT16_CUBE"),
    ]
    grid_f64 = np.linspace(0.25, 4.0, 6, dtype=np.float64).reshape(2, 3)
    img_f64_cards = [
        _fits_card("XTENSION", "IMAGE"),
        _fits_card("BITPIX", -64),
        _fits_card("NAXIS", 2),
        _fits_card("NAXIS1", 3),
        _fits_card("NAXIS2", 2),
        _fits_card("EXTNAME", "FLOAT64_GRID"),
    ]
    col_values = np.array([16.0, 32.0, 64.0, 128.0, 256.0], dtype=np.float32)
    col_cards = [
        _fits_card("XTENSION", "BINTABLE"),
        _fits_card("BITPIX", 8),
        _fits_card("NAXIS", 2),
        _fits_card("NAXIS1", 4),
        _fits_card("NAXIS2", len(col_values)),
        _fits_card("PCOUNT", 0),
        _fits_card("GCOUNT", 1),
        _fits_card("TFIELDS", 1),
        _fits_card("TFORM1", "E"),
        _fits_card("EXTNAME", "BOUNDS"),
    ]
    fits_path = tmp_path / "multi.fits"
    fits_path.write_bytes(
        _fits_header_bytes(primary_cards)
        + _fits_header_bytes(img_i16_cards)
        + _fits_payload_bytes(cube_i16.astype(">i2").tobytes())
        + _fits_header_bytes(img_f64_cards)
        + _fits_payload_bytes(grid_f64.astype(">f8").tobytes())
        + _fits_header_bytes(col_cards)
        + _fits_payload_bytes(col_values.astype(">f4").tobytes())
    )

    hdus = bdb.read_fits_hdus(fits_path)
    assert len(hdus) == 4
    assert hdus[0].shape == () and hdus[0].data_offset == 5760
    h_i16 = bdb._named(hdus, "INT16_CUBE")
    assert h_i16.shape == (2, 3, 4) and h_i16.bitpix == 16
    assert np.array_equal(np.asarray(bdb.fits_image(fits_path, h_i16)), cube_i16)

    h_f64 = bdb._named(hdus, "FLOAT64_GRID")
    assert h_f64.shape == (2, 3) and h_f64.bitpix == -64
    assert np.allclose(np.asarray(bdb.fits_image(fits_path, h_f64)), grid_f64)

    col_out = bdb.fits_float_column(fits_path, bdb._named(hdus, "BOUNDS"))
    assert col_out.dtype == np.float64
    assert np.allclose(col_out, col_values.astype(np.float64))

    with pytest.raises(ValueError, match="no HDU named"):
        bdb._named(hdus, "MISSING")

    with pytest.raises(ValueError, match="expected a one-column float table"):
        bdb.fits_float_column(fits_path, h_f64)

    truncated = tmp_path / "truncated.fits"
    truncated.write_bytes(b"SIMPLE  =                    T" + b" " * 100)
    with pytest.raises(ValueError, match="header runs past the end"):
        bdb.read_fits_hdus(truncated)


# =============================================================================================
# 3. Synthetic HealpixDustMap validation, sampling, and exact radial columns
# =============================================================================================


def test_load_healpix_dust_map_validates_ordering_nside_and_shape(tmp_path: Path) -> None:
    bounds = np.array([16.0, 32.0, 64.0], dtype=np.float32)
    good = np.ones((2, 48), dtype=np.float32)

    ring_path = tmp_path / "ring.fits"
    _write_synthetic_edenhofer(ring_path, good, good, bounds, nside=2, ordering="RING")
    with pytest.raises(ValueError, match="nested HEALPix ordering"):
        bdb.load_healpix_dust_map(ring_path)

    bad_nside = tmp_path / "bad_nside.fits"
    _write_synthetic_edenhofer(bad_nside, np.ones((2, 108), dtype=np.float32), np.ones((2, 108), dtype=np.float32), bounds, nside=3)
    with pytest.raises(ValueError, match="power of two"):
        bdb.load_healpix_dust_map(bad_nside)

    bad_shape = tmp_path / "bad_shape.fits"
    _write_synthetic_edenhofer(bad_shape, np.ones((3, 48), dtype=np.float32), np.ones((3, 48), dtype=np.float32), bounds, nside=2)
    with pytest.raises(ValueError, match="does not match"):
        bdb.load_healpix_dust_map(bad_shape)


def test_sample_map_and_exact_ray_columns_with_truncation_and_duplicates(tmp_path: Path) -> None:
    order = 1  # nside = 2 -> 48 pixels
    bounds = np.array([16.0, 32.0, 64.0, 128.0], dtype=np.float64)
    shells, pixels = len(bounds) - 1, 12 * 4**order
    mean = (100.0 + 1000.0 * np.arange(shells)[:, None] + np.arange(pixels)[None, :]).astype(np.float32)
    std = (0.25 * mean).astype(np.float32)
    fits_path = tmp_path / "syn_dust.fits"
    _write_synthetic_edenhofer(fits_path, mean, std, bounds, nside=2**order)
    dust = bdb.load_healpix_dust_map(fits_path)

    # Exact shell boundaries: inner edge is included, outer edge is excluded.
    pix_x = int(ct.radec_to_nest(order, [0.0], [0.0])[0])
    pts_x = np.array([[15.999, 16.0, 32.0], [64.0, 127.999, 128.0]])
    pts_zero = np.zeros_like(pts_x)
    sampled_mean = bdb.sample_map(dust, pts_x, pts_zero, pts_zero, field="mean")
    sampled_std = bdb.sample_map(dust, pts_x, pts_zero, pts_zero, field="std")
    assert sampled_mean.shape == (2, 3)
    assert np.isnan(sampled_mean[0, 0]) and np.isnan(sampled_mean[1, 2])
    assert sampled_mean[0, 1] == mean[0, pix_x]
    assert sampled_mean[0, 2] == mean[1, pix_x]
    assert sampled_mean[1, 0] == mean[2, pix_x]
    assert sampled_mean[1, 1] == mean[2, pix_x]
    assert sampled_std[0, 2] == std[1, pix_x]

    # Galactic axes (+y -> l=90, -x -> l=180, +z -> b=+90, -z -> b=-90).
    for (x, y, z), (lon, lat) in (
        ((0.0, 24.0, 0.0), (90.0, 0.0)),
        ((-24.0, 0.0, 0.0), (180.0, 0.0)),
        ((0.0, 0.0, 24.0), (0.0, 90.0)),
        ((0.0, 0.0, -24.0), (0.0, -90.0)),
    ):
        pix = int(ct.radec_to_nest(order, [lon], [lat])[0])
        got = bdb.sample_map(dust, np.array([x]), np.array([y]), np.array([z]))[0]
        assert got == mean[0, pix]

    # Vectorized map_columns with repeated directions and partial shell truncation via outer_pc.
    lons = np.array([0.0, 90.0, 0.0, 270.0])
    lats = np.array([0.0, 0.0, 0.0, -45.0])
    cols_full = bdb.map_columns(dust, lons, lats)
    assert cols_full.shape == (4,)
    assert cols_full[0] == cols_full[2]
    for i, (lon, lat) in enumerate(zip(lons, lats, strict=True)):
        assert cols_full[i] == pytest.approx(bdb.map_column(dust, float(lon), float(lat)), rel=1e-12)

    # Truncating at outer_pc = 48.0 covers full shell 0 (width 16) + half of shell 1 (width 16) + 0 of shell 2.
    partial_expected = float(mean[0, pix_x]) * 16.0 + float(mean[1, pix_x]) * 16.0
    assert bdb.map_column(dust, 0.0, 0.0, outer_pc=48.0) == pytest.approx(partial_expected, rel=1e-12)
    assert bdb.map_column(dust, 0.0, 0.0, outer_pc=16.0) == 0.0


# =============================================================================================
# 4. Sparse Cartesian brick packing, thresholding, ray-marching, two-level, and serialization
# =============================================================================================


def _shell_sampler(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    r = np.sqrt(x * x + y * y + z * z)
    val = np.where(x >= 0.0, 0.02, 0.0005)
    return np.where((r >= 16.0) & (r < 64.0), val, np.nan)


def test_sparse_brick_thresholding_two_level_errors_and_fortran_f16_serialization(tmp_path: Path) -> None:
    build = bdb.build_bricks(_shell_sampler, voxel_pc=8.0, outer_radius_pc=64.0, brick=4)
    assert build.n_voxels == 16
    assert build.half_extent_pc == 64.0
    assert len(build.axis()) == 16

    # Strict `>` threshold: a brick whose peak equals the threshold is dropped.
    all_keys = set(build.bricks)
    low_peak = min(build.peaks.values())
    high_peak = max(build.peaks.values())
    assert low_peak < high_peak
    kept_strict = set(build.kept(low_peak))
    assert 0 < len(kept_strict) < len(all_keys)
    assert all(build.peaks[k] > low_peak for k in kept_strict)
    assert all(build.peaks[k] <= low_peak for k in (all_keys - kept_strict))

    # Ray along +x hits high-density bricks (0.02); ray along -x hits low-density bricks (0.0005).
    col_pos = bdb.brick_column(build, 0.0, 0.0, inner_pc=16.0, outer_pc=64.0, threshold=low_peak, step_pc=1.0)
    col_neg = bdb.brick_column(build, 180.0, 0.0, inner_pc=16.0, outer_pc=64.0, threshold=low_peak, step_pc=1.0)
    assert col_pos > 0.5
    assert col_neg == 0.0

    # Two-level factor divisibility check and residual switching.
    with pytest.raises(ValueError, match="divide the brick size"):
        bdb.two_level(build, factor=3)
    levels = bdb.two_level(build, factor=2)
    assert set(levels.base) == all_keys
    assert set(levels.residual_peaks) == all_keys
    col_fine = bdb.two_level_column(build, levels, 0.0, 0.0, inner_pc=16.0, outer_pc=64.0, residual_threshold=-math.inf, step_pc=1.0)
    assert col_fine == pytest.approx(col_pos, rel=1e-12)

    # Pin exact (bz, by, bx) brick order and Fortran (x-fastest) float16 byte layout in write_bricks.
    synth = bdb.BrickBuild(voxel_pc=4.0, brick=2, n_voxels=4)
    brick_a = np.zeros((2, 2, 2), dtype=np.float16)
    brick_a[1, 0, 0] = np.float16(1.5)  # x=1, y=0, z=0 -> index 1 in Fortran order, index 4 in C order
    brick_a[0, 1, 0] = np.float16(2.5)  # x=0, y=1, z=0 -> index 2 in Fortran order
    brick_a[0, 0, 1] = np.float16(3.5)  # x=0, y=0, z=1 -> index 4 in Fortran order, index 1 in C order
    brick_b = np.full((2, 2, 2), np.float16(0.5))
    brick_drop = np.full((2, 2, 2), np.float16(0.1))
    synth.bricks = {(1, 0, 1): brick_b, (0, 1, 0): brick_a, (0, 0, 0): brick_drop}
    synth.peaks = {(1, 0, 1): 0.5, (0, 1, 0): 3.5, (0, 0, 0): 0.1}

    out_dir = tmp_path / "written"
    index = bdb.write_bricks(synth, out_dir, threshold=0.1, meta={"source": "synthetic-unit"})
    # (0, 0, 0) has peak == 0.1 so dropped; (0, 1, 0) has bz=0 before (1, 0, 1) with bz=1.
    assert index["bricks"] == [[0, 1, 0], [1, 0, 1]]
    assert index["format"] == "albedian.dust-bricks-pilot"
    assert index["voxelPc"] == 4.0
    assert index["brick"] == 2
    assert index["nVoxels"] == 4
    assert index["halfExtentPc"] == 8.0
    assert index["source"] == "synthetic-unit"

    raw_f16 = (out_dir / "bricks.f16").read_bytes()
    assert index["bricksSha256"] == hashlib.sha256(raw_f16).hexdigest()
    unpacked = np.frombuffer(raw_f16, dtype="<f2")
    assert len(unpacked) == 16
    # First 8 values belong to brick_a in x-fastest (Fortran) order: [0, 1.5, 2.5, 0, 3.5, 0, 0, 0]
    assert list(unpacked[:8]) == [0.0, 1.5, 2.5, 0.0, 3.5, 0.0, 0.0, 0.0]
    assert list(unpacked[8:]) == [0.5] * 8
    assert json.loads((out_dir / "index.json").read_text(encoding="utf-8")) == index


# =============================================================================================
# 5. Runner helpers and CLI behavior without `.cache/dust`
# =============================================================================================


def test_runner_helpers_pilot_functions_and_main_without_cache_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert bdb.peak_rss_gb() > 0.0
    lon_a, lat_a = bdb.random_directions(32, seed=7)
    lon_b, lat_b = bdb.random_directions(32, seed=7)
    assert np.array_equal(lon_a, lon_b) and np.array_equal(lat_a, lat_b)
    assert np.all((lon_a >= 0.0) & (lon_a < 360.0))
    assert np.all((lat_a >= -90.0) & (lat_a <= 90.0))

    # Subprocess CLI argument checks (--help and invalid option) never touch `.cache/dust`.
    help_proc = subprocess.run([sys.executable, str(SCRIPT_PATH), "--help"], capture_output=True, text=True, check=False)
    assert help_proc.returncode == 0
    for flag in ("--voxel", "--brick", "--thresholds", "--rays", "--out", "--write"):
        assert flag in help_proc.stdout

    bad_proc = subprocess.run([sys.executable, str(SCRIPT_PATH), "--no-such-flag"], capture_output=True, text=True, check=False)
    assert bad_proc.returncode != 0

    # End-to-end synthetic `main()` both without `--write` and with `--write`.
    order = 1
    bounds = np.array([16.0, 64.0, 128.0], dtype=np.float32)
    mean = (10.0 + np.arange(2 * 48, dtype=np.float32).reshape(2, 48)) * 1e-3
    std = (0.1 * mean).astype(np.float32)
    eden_fits = tmp_path / "syn_edenhofer.fits"
    _write_synthetic_edenhofer(eden_fits, mean, std, bounds, nside=2**order)

    shell_cube = np.full((8, 8, 8), np.nan, dtype=np.float32)
    shell_cube[0:4, 0:4, 0:4] = 0.002
    shell_cube[4:8, 4:8, 4:8] = 0.0002
    shell_fits = tmp_path / "syn_shell.fits"
    _write_synthetic_shell(shell_fits, shell_cube, cdelt1=2.0)

    monkeypatch.setattr(bdb, "EDENHOFER_PATH", eden_fits)
    monkeypatch.setattr(bdb, "ONEILL_SHELL_PATH", shell_fits)

    report_only_dir = tmp_path / "out_report_only"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scripts/bake_dust_bricks.py",
            "--voxel",
            "32",
            "--brick",
            "4",
            "--thresholds",
            "0.001",
            "0.05",
            "--rays",
            "12",
            "--out",
            str(report_only_dir),
        ],
    )
    assert bdb.main() == 0
    summary_report = json.loads((report_only_dir / "summary.json").read_text(encoding="utf-8"))
    assert [p.name for p in report_only_dir.iterdir()] == ["summary.json"]
    assert summary_report["command"].startswith("uv run python scripts/bake_dust_bricks.py --voxel 32")
    assert len(summary_report["edenhofer"]) == 1
    eden_entry = summary_report["edenhofer"][0]
    assert eden_entry["voxelPc"] == 32.0 and eden_entry["brick"] == 4
    assert [r["threshold"] for r in eden_entry["rows"]] == [None, 0.001, 0.05]
    assert [r["residualThreshold"] for r in eden_entry["twoLevel"]] == [None, 0.001, 0.05]
    shell_entry = summary_report["oneillShell"]
    assert shell_entry["cube"] == [8, 8, 8]
    assert shell_entry["voxelPc"] == 2.0
    assert shell_entry["bricksTotal"] == 8
    assert [r["bricks"] for r in shell_entry["rows"]] == [1, 0]

    write_dir = tmp_path / "out_with_write"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scripts/bake_dust_bricks.py",
            "--voxel",
            "32",
            "--brick",
            "4",
            "--thresholds",
            "0.001",
            "--rays",
            "8",
            "--out",
            str(write_dir),
            "--write",
        ],
    )
    assert bdb.main() == 0
    brick_subdir = write_dir / "edenhofer2024-32pc-t0.001"
    assert (brick_subdir / "bricks.f16").is_file()
    assert (brick_subdir / "index.json").is_file()
