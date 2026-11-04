"""Sky dome registration: galactic frame, panorama model, committed solution, star map projection.

The web renderers sample ESO's Milky Way panorama through the solution in
apps/web/public/textures/eso0932a_registration.json (written by
scripts/register_sky_panorama.py) and the NASA SVS star map through right ascension and
declination. These tests pin the math on synthetic data, check that the committed solution
is what a fresh fit produces, and measure the star map's star images against the catalog.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image
from scipy.spatial.transform import Rotation

from services.science import sky_registration as sr

ROOT = Path(__file__).resolve().parents[2]
TEXTURES = ROOT / "apps" / "web" / "public" / "textures"
NAMES = ROOT / "apps" / "web" / "public" / "catalog" / "stars" / "gaia-dr3" / "names.json"
ARCSEC = math.pi / (180.0 * 3600.0)
GEOM = sr.PanoramaGeometry(width=720, height=360)


def _load_script():
    spec = importlib.util.spec_from_file_location("register_sky_panorama", ROOT / "scripts" / "register_sky_panorama.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _angle(a: np.ndarray, b: np.ndarray) -> float:
    return math.atan2(float(np.linalg.norm(np.cross(a, b))), float(a @ b))


def _random_stars(seed: int, n: int) -> np.ndarray:
    v = np.random.default_rng(seed).normal(size=(n, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    # stay clear of the poles, where plate carree x residuals are ill conditioned
    return v[np.abs(v[:, 2]) < 0.95]


def test_icrs_to_galactic_is_the_hipparcos_frame():
    a = sr.ICRS_TO_GALACTIC
    assert np.allclose(a @ a.T, np.eye(3), atol=1e-12)
    assert np.linalg.det(a) > 0
    ngp = a @ sr.radec_to_unit(sr.NGP_RA_DEG, sr.NGP_DEC_DEG)
    assert _angle(ngp, np.array([0.0, 0.0, 1.0])) < 0.1 * ARCSEC
    node = a @ sr.radec_to_unit(sr.NGP_RA_DEG + 90.0, 0.0)
    lon = math.radians(sr.NODE_LON_DEG)
    assert _angle(node, np.array([math.cos(lon), math.sin(lon), 0.0])) < 0.1 * ARCSEC


@settings(max_examples=40, deadline=None)
@given(
    rotvec=st.tuples(*[st.floats(min_value=-0.08, max_value=0.08)] * 3),
    y_offset=st.floats(min_value=-3.0, max_value=3.0),
    seed=st.integers(min_value=0, max_value=2**31 - 1),
)
def test_rigid_fit_recovers_a_known_rotation_and_offset(rotvec, y_offset, seed):
    truth = sr.RigidSolution(Rotation.from_rotvec(rotvec).as_matrix(), y_offset)
    galactic = _random_stars(seed, 300)
    x, y = sr.model_pixels(galactic, truth, GEOM)
    fit = sr.fit_rigid(galactic, np.stack([x, y], axis=-1), sr.RigidSolution(np.eye(3), 0.0), GEOM)
    residual = Rotation.from_matrix(fit.rotation @ truth.rotation.T).magnitude()
    assert residual < 1e-6
    assert abs(fit.y_offset_px - y_offset) < 1e-4


@settings(max_examples=30, deadline=None)
@given(seed=st.integers(min_value=0, max_value=2**31 - 1), amplitude=st.floats(min_value=0.2, max_value=3.0))
def test_tangent_offsets_invert_the_offset_table(seed, amplitude):
    """Stars moved by a table read back the table's (east, north) values at their positions."""
    rng = np.random.default_rng(seed)
    codes = rng.integers(0, 256, size=(12, 24, 2)).astype(np.float64)
    table = (codes - sr.OFFSET_TABLE_BIAS) / sr.OFFSET_TABLE_BIAS * amplitude
    solution = sr.RigidSolution(Rotation.from_rotvec(rng.normal(scale=0.03, size=3)).as_matrix(), float(rng.normal()))
    galactic = _random_stars(seed, 200)
    x, y = sr.apply_offsets(galactic, solution, table, GEOM)
    measured = sr.tangent_offsets(galactic, np.stack([x, y], axis=-1), solution, GEOM)
    lon, lat = sr.lon_lat(galactic @ solution.rotation.T)
    expected = sr.sample_offset_table(table, np.mod(0.5 - lon / (2 * math.pi), 1.0), 0.5 + lat / math.pi)
    assert np.allclose(measured, expected, atol=1e-9)


@settings(max_examples=60, deadline=None)
@given(fx=st.floats(min_value=0.0, max_value=0.999), fy=st.floats(min_value=0.0, max_value=0.999))
def test_star_centroid_finds_a_gaussian_star_to_a_tenth_of_a_pixel(fx, fy):
    height, width = 64, 128
    cx, cy = 60.0 + fx, 30.0 + fy
    gy, gx = np.mgrid[0:height, 0:width]
    image = 0.05 + 0.8 * np.exp(-((gx + 0.5 - cx) ** 2 + (gy + 0.5 - cy) ** 2) / (2 * 1.2**2))
    found = sr.star_centroid(sr.difference_of_gaussians(image), cx + 1.3, cy - 0.8, 3, 0.03)
    assert found is not None
    assert math.hypot(found[0] - cx, found[1] - cy) < 0.1


def test_offset_table_codec_round_trips_and_refuses_overflow():
    values = np.random.default_rng(3).uniform(-3.9, 3.9, size=(6 * 10, 2))
    blob = sr.encode_offset_table(values, 10, 6, 32.0)
    assert np.abs(sr.decode_offset_table(blob, 10, 6, 32.0).reshape(-1, 2) - values).max() <= 0.5 / 32.0 + 1e-12
    try:
        sr.encode_offset_table(values * 2.0, 10, 6, 32.0)
    except ValueError:
        pass
    else:
        raise AssertionError("an offset beyond the byte range must be refused")


def test_committed_panorama_solution_is_a_fresh_fit():
    script = _load_script()
    committed = (TEXTURES / "eso0932a_registration.json").read_text()
    assert script.dumps(script.solve()) == committed, "rerun scripts/register_sky_panorama.py"
    solution = json.loads(committed)
    held_out = {row["sigmaDeg"]: row for row in solution["fit"]["heldOut"]["withOffsetTable"]}
    chosen = held_out[solution["offsetTable"]["kernelSigmaDeg"]]
    # Held-out stars land within half a texel (median) and about a texel (90th percentile)
    assert chosen["medianTexels"] < 0.5 and chosen["p90Texels"] < 1.0
    assert solution["fit"]["heldOut"]["rigidOnly"]["medianTexels"] > chosen["medianTexels"]


def test_star_map_is_equatorial_plate_carree_with_ra_zero_at_the_centre():
    """Bright IAU-named stars in nasa_starmap_4k.jpg sit at RA/Dec plate carree positions."""
    rgb = np.asarray(Image.open(TEXTURES / "nasa_starmap_4k.jpg").convert("RGB"), dtype=np.float64) / 255.0
    height, width = rgb.shape[:2]
    geom = sr.PanoramaGeometry(width=width, height=height)
    dog = sr.difference_of_gaussians(rgb.mean(axis=2), inner_px=0.7, outer_px=4.0)
    errors = []
    for star in json.loads(NAMES.read_text())["names"]:
        if star["mag"] > 2.5:
            continue
        x, y = sr.frame_to_pixel(sr.radec_to_unit(star["raDeg"], star["decDeg"]), geom)
        found = sr.star_centroid(dog, float(x), float(y), 2, 0.05)
        if found is not None:
            errors.append(math.hypot(float(sr.wrap_dx(found[0] - x, width)), found[1] - y))
    assert len(errors) >= 60
    assert float(np.median(errors)) < 0.3
