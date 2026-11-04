"""Dust brick pilot (data-3): FITS reading, map sampling, brick packing and ray closure.

`scripts/bake_dust_bricks.py` resamples the Edenhofer et al. (2024) 3D dust map, HEALPix sky
pixels times log-spaced distance shells, onto heliocentric Galactic Cartesian bricks for a
ray-marched volume. The synthetic tests pin the geometry; the real-data tests read the map from
the shared cache (`.cache/dust/`) and skip when it is absent.
"""

from __future__ import annotations

import importlib.util
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from services.science import catalog_tiles as ct

ROOT = Path(__file__).resolve().parents[2]
EDENHOFER_REL = ".cache/dust/edenhofer2024/mean_and_std_healpix.fits"
EDENHOFER = ROOT / EDENHOFER_REL
needs_edenhofer = pytest.mark.local_dataset(EDENHOFER_REL)


def _load_bake():
    spec = importlib.util.spec_from_file_location("bake_dust_bricks", ROOT / "scripts" / "bake_dust_bricks.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bake = _load_bake()


# =============================================================================================
# A minimal FITS writer for synthetic maps (primary HDU, IMAGE and one-column BINTABLE)
# =============================================================================================


def _card(key: str, value: object) -> bytes:
    if isinstance(value, bool):
        text = f"{'T' if value else 'F':>20}"
    elif isinstance(value, (int, float)):
        text = f"{value:>20}"
    else:
        text = f"'{value:<8}'"
    return f"{key:<8}= {text}".ljust(80).encode("ascii")


def _header(cards: list[tuple[str, object]]) -> bytes:
    raw = b"".join(_card(k, v) for k, v in cards) + b"END".ljust(80)
    return raw + b" " * (-len(raw) % 2880)


def _data(array: np.ndarray) -> bytes:
    raw = array.astype(">f4").tobytes()
    return raw + b"\0" * (-len(raw) % 2880)


def write_synthetic_map(path: Path, mean: np.ndarray, std: np.ndarray, boundaries: np.ndarray, nside: int) -> None:
    shells, pixels = mean.shape
    image = lambda name: [  # noqa: E731
        ("XTENSION", "IMAGE"),
        ("BITPIX", -32),
        ("NAXIS", 2),
        ("NAXIS1", pixels),
        ("NAXIS2", shells),
        ("PCOUNT", 0),
        ("GCOUNT", 1),
        ("EXTNAME", name),
        ("NSIDE", nside),
        ("ORDERING", "NEST"),
    ]
    table = [
        ("XTENSION", "BINTABLE"),
        ("BITPIX", 8),
        ("NAXIS", 2),
        ("NAXIS1", 4),
        ("NAXIS2", len(boundaries)),
        ("PCOUNT", 0),
        ("GCOUNT", 1),
        ("TFIELDS", 1),
        ("TTYPE1", "radial pixel boundaries"),
        ("TFORM1", "E"),
        ("EXTNAME", "RADIAL PIXEL BOUNDARIES"),
    ]
    with open(path, "wb") as fh:
        fh.write(_header([("SIMPLE", True), ("BITPIX", 8), ("NAXIS", 0), ("EXTEND", True)]))
        fh.write(_header(image("MEAN")) + _data(mean))
        fh.write(_header(image("STD.")) + _data(std))
        fh.write(_header(table) + _data(boundaries))


ORDER = 1  # nside 2, 48 pixels
BOUNDS = np.array([10.0, 20.0, 40.0, 80.0, 160.0])


@pytest.fixture
def synthetic_map(tmp_path: Path):
    pixels = 12 * 4**ORDER
    shells = len(BOUNDS) - 1
    mean = (1000.0 * np.arange(shells)[:, None] + np.arange(pixels)[None, :]).astype(np.float32)
    std = (mean / 10.0).astype(np.float32)
    path = tmp_path / "synthetic_mean_and_std_healpix.fits"
    write_synthetic_map(path, mean, std, BOUNDS, 2**ORDER)
    return bake.load_healpix_dust_map(path), mean


def _galactic_xyz(lon_deg: float, lat_deg: float, d: float) -> tuple[float, float, float]:
    lon, lat = math.radians(lon_deg), math.radians(lat_deg)
    return d * math.cos(lat) * math.cos(lon), d * math.cos(lat) * math.sin(lon), d * math.sin(lat)


# =============================================================================================
# FITS and sampling
# =============================================================================================


def test_fits_reader_finds_each_hdu_with_its_shape_and_offset(tmp_path: Path, synthetic_map):
    dust, mean = synthetic_map
    hdus = bake.read_fits_hdus(tmp_path / "synthetic_mean_and_std_healpix.fits")
    assert [h.header.get("EXTNAME") for h in hdus] == [None, "MEAN", "STD.", "RADIAL PIXEL BOUNDARIES"]
    assert hdus[1].shape == (4, 48) and hdus[1].bitpix == -32
    assert all(h.data_offset % 2880 == 0 for h in hdus)
    assert np.array_equal(np.asarray(dust.mean), mean)
    assert np.array_equal(dust.boundaries_pc, BOUNDS) and dust.order == ORDER


def test_map_sampling_picks_the_galactic_pixel_and_the_shell(synthetic_map):
    dust, mean = synthetic_map
    for lon, lat, d in ((10.0, 5.0, 15.0), (200.0, -40.0, 55.0), (300.0, 80.0, 159.0), (90.0, 0.0, 20.0)):
        x, y, z = _galactic_xyz(lon, lat, d)
        pix = int(ct.radec_to_nest(ORDER, [lon], [lat])[0])
        shell = int(np.searchsorted(BOUNDS, d, side="right") - 1)
        assert bake.sample_map(dust, np.array([x]), np.array([y]), np.array([z]))[0] == mean[shell, pix]
    # Inside the innermost shell and beyond the outermost, the map has no 3D density
    for d in (9.99, 160.0, 400.0):
        assert np.isnan(bake.sample_map(dust, *map(np.array, ([d], [0.0], [0.0])))[0])


def test_columns_from_the_sun_sum_each_shell_once(synthetic_map):
    dust, mean = synthetic_map
    lon, lat = 123.0, -17.0
    pix = int(ct.radec_to_nest(ORDER, [lon], [lat])[0])
    expected = float(np.sum(mean[:, pix].astype(np.float64) * np.diff(BOUNDS)))
    assert bake.map_column(dust, lon, lat) == pytest.approx(expected, rel=1e-12)


# =============================================================================================
# Bricks
# =============================================================================================


def _blob(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    """A Gaussian cloud 60 pc from the Sun, zero inside 10 pc and beyond 160 pc like the map."""
    r = np.sqrt(x * x + y * y + z * z)
    value = 0.01 * np.exp(-((x - 60.0) ** 2 + y**2 + z**2) / (2 * 15.0**2))
    return np.where((r >= 10.0) & (r < 160.0), value, np.nan)


def test_bricks_keep_values_above_the_threshold_and_drop_only_below_it():
    build = bake.build_bricks(_blob, voxel_pc=4.0, outer_radius_pc=160.0, brick=8)
    assert build.n_voxels % 8 == 0 and build.n_voxels * 4.0 >= 2 * 160.0
    threshold = 1e-4
    kept = {key for key, peak in build.peaks.items() if peak > threshold}
    assert 0 < len(kept) < len(build.peaks)
    for key, data in build.bricks.items():
        x, y, z = build.voxel_centers(key)
        truth = np.nan_to_num(_blob(x, y, z))
        assert data.dtype == np.float16
        assert np.allclose(data.astype(np.float64), truth, rtol=1e-3, atol=1e-7)
        assert build.peaks[key] == pytest.approx(float(truth.max()), rel=1e-3)
    dropped_peak = max((p for k, p in build.peaks.items() if k not in kept), default=0.0)
    assert dropped_peak <= threshold


def test_brick_rays_reproduce_a_uniform_shell_column():
    rho = 0.002

    def uniform(x, y, z):
        r = np.sqrt(x * x + y * y + z * z)
        return np.where((r >= 10.0) & (r < 160.0), rho, np.nan)

    build = bake.build_bricks(uniform, voxel_pc=2.0, outer_radius_pc=160.0, brick=16)
    for lon, lat in ((0.0, 0.0), (77.0, 33.0), (250.0, -61.0)):
        column = bake.brick_column(build, lon, lat, inner_pc=10.0, outer_pc=160.0)
        # Nearest-voxel sampling can shift each end of the ray by up to half a voxel diagonal
        assert column == pytest.approx(rho * 150.0, abs=rho * 2.0 * math.sqrt(3))


def test_two_level_base_keeps_block_means_and_full_detail_matches_the_bricks():
    build = bake.build_bricks(_blob, voxel_pc=4.0, outer_radius_pc=160.0, brick=8)
    levels = bake.two_level(build, factor=4)
    for key, data in build.bricks.items():
        fine = data.astype(np.float64)
        assert levels.base[key].shape == (2, 2, 2)
        # Each base voxel is the mean of its 4-cubed block, so the brick's total dust is unchanged
        assert levels.base[key].sum() * 4**3 == pytest.approx(fine.sum(), rel=1e-12, abs=1e-15)
    for lon, lat in ((0.0, 0.0), (10.0, 5.0), (200.0, -30.0)):
        full = bake.brick_column(build, lon, lat, inner_pc=10.0, outer_pc=160.0)
        detail = bake.two_level_column(build, levels, lon, lat, inner_pc=10.0, outer_pc=160.0, residual_threshold=-math.inf)
        assert detail == pytest.approx(full, rel=1e-12, abs=1e-15)
    # Base only, toward the cloud: block means smooth the profile but keep most of the column
    full = bake.brick_column(build, 0.0, 0.0, inner_pc=10.0, outer_pc=160.0)
    base_only = bake.two_level_column(build, levels, 0.0, 0.0, inner_pc=10.0, outer_pc=160.0, residual_threshold=math.inf)
    assert 0.5 * full < base_only < 1.5 * full


# =============================================================================================
# Real data: Edenhofer et al. (2024)
# =============================================================================================


def test_local_dataset_marker_skips_with_its_reason_only_where_a_path_is_absent(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text((ROOT / "tests" / "conftest.py").read_text())
    (tmp_path / ".cache" / "present").mkdir(parents=True)
    (tmp_path / "tests" / "test_marked.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.local_dataset('.cache/present')\n"
        "def test_present():\n    pass\n\n"
        "@pytest.mark.local_dataset('.cache/present', '.cache/absent/map.fits')\n"
        "def test_absent():\n    pass\n"
    )
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-rs", "-p", "no:cacheprovider", "tests"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stdout[-2000:]
    assert "1 passed, 1 skipped" in run.stdout
    assert "needs the public dataset .cache/absent/map.fits, downloaded into the shared .cache and not in git" in run.stdout


def test_no_unexpected_skips_allows_a_local_dataset_skip_only_for_its_own_missing_path(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text((ROOT / "tests" / "conftest.py").read_text())
    (tmp_path / ".cache" / "present").mkdir(parents=True)
    (tmp_path / "tests" / "test_kinds.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.local_dataset('.cache/absent/map.fits')\n"
        "def test_dataset_absent():\n    pass\n\n"
        "@pytest.mark.local_dataset('.cache/present')\n"
        "def test_dataset_present_but_skips_itself():\n    pytest.skip('not ready')\n\n"
        "@pytest.mark.local_dataset('.cache/absent/map.fits')\n"
        "@pytest.mark.skipif(True, reason='switched off')\n"
        "def test_dataset_absent_but_a_skipif_fires_first():\n    pass\n"
    )
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-rs", "-p", "no:cacheprovider", "--no-unexpected-skips", "tests"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    # The marker's own skip stands; a skip in the body fails, and a skipif, which fires during setup
    # ahead of the marker's skip, is reported as an error
    assert run.returncode == 1, run.stdout[-2000:]
    assert "1 failed, 1 skipped, 1 error" in run.stdout
    assert "needs the public dataset .cache/absent/map.fits, downloaded into the shared .cache and not in git" in run.stdout
    assert run.stdout.count("only tests marked owner_media may skip") >= 2


@pytest.fixture(scope="module")
def edenhofer():
    return bake.load_healpix_dust_map(EDENHOFER)


@needs_edenhofer
def test_real_map_layout(edenhofer):
    assert edenhofer.order == 8  # nside 256
    assert edenhofer.mean.shape == (516, 786432)
    assert edenhofer.boundaries_pc[0] == pytest.approx(68.62, abs=0.01)
    assert edenhofer.boundaries_pc[-1] == pytest.approx(1248.10, abs=0.01)
    assert np.all(np.diff(edenhofer.boundaries_pc) > 0)


@needs_edenhofer
def test_real_map_dust_concentrates_toward_the_galactic_plane(edenhofer):
    """The map is in Galactic coordinates: most of the column lies at low |b|."""
    rng = np.random.default_rng(1)
    lon = rng.uniform(0, 360, 1500)
    lat = np.degrees(np.arcsin(rng.uniform(-1, 1, 1500)))  # uniform on the sphere
    columns = bake.map_columns(edenhofer, lon, lat)
    assert columns[0] == pytest.approx(bake.map_column(edenhofer, lon[0], lat[0]), rel=1e-12)
    low = np.abs(lat) < 10.0
    # |b| < 10 deg is 17.4% of the sky; dust that follows the disk puts far more column there
    assert columns[low].sum() / columns.sum() > 0.4


@needs_edenhofer
def test_real_map_rays_through_bricks_match_the_healpix_columns(edenhofer):
    """Closure on real data: 4 pc bricks of the inner 300 pc against the map's own columns."""
    outer = 300.0
    build = bake.build_bricks(
        lambda x, y, z: bake.sample_map(edenhofer, x, y, z), voxel_pc=4.0, outer_radius_pc=outer, brick=16
    )
    inner = float(edenhofer.boundaries_pc[0])
    rng = np.random.default_rng(2)
    rel = []
    for lon, lat in zip(rng.uniform(0, 360, 400), np.degrees(np.arcsin(rng.uniform(-1, 1, 400))), strict=True):
        truth = bake.map_column(edenhofer, lon, lat, outer_pc=outer)
        got = bake.brick_column(build, lon, lat, inner_pc=inner, outer_pc=outer)
        rel.append(abs(got - truth) / truth)
    rel = np.array(rel)
    # Measured on 2026-11-04 with nearest-voxel sampling: median 2.5%, 95th percentile 16%. A frame,
    # axis or shell error moves most columns by tens of percent, so these bounds catch it.
    assert np.median(rel) < 0.05
    assert np.percentile(rel, 95) < 0.25
