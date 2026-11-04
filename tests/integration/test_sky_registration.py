"""Sky dome registration: galactic frame, panorama model, committed solution, planets in the photograph.

The web renderers sample ESO's Milky Way panorama through the solution in
apps/web/public/textures/eso0932a_registration.json (written by
scripts/register_sky_panorama.py). These tests pin the math on synthetic data, check that the
committed solution is what a fresh fit produces, and measure the two planets the photograph
shows against the repository's ephemeris.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image
from scipy import ndimage
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation

from services.science import sky_registration as sr
from services.science.ephemeris import compute_solar_system_ephemerides

ROOT = Path(__file__).resolve().parents[2]
TEXTURES = ROOT / "apps" / "web" / "public" / "textures"
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


# A refit on another platform (Linux OpenBLAS and glibc against macOS Accelerate) can move a
# value's last rounded digit or put an offset table code on the other side of a rounding edge.
# Inputs, structure and choices must match exactly; numbers may differ only within these bounds.
REFIT_FLOAT_FLOOR = 1e-8  # radians in the rotation matrices: 2 milliarcseconds
REFIT_ROUNDING_STEPS = 2  # rounded statistics and positions: two steps of their last digit
REFIT_COUNT_SLACK = 2  # stars entering or leaving a selection at a threshold
REFIT_TABLE_CODE_SLACK = 1  # one offset table code is 1/32 texel
REFIT_TABLE_CELL_FRACTION = 0.005
REFIT_COUNT_KEYS = {"candidates", "matched", "used", "stars"}
REFIT_EXACT_FLOAT_KEYS = {
    "texelArcsec", "scale", "kernelSigmaDeg", "priorWeight", "sigmaDeg", "magnitudeLimit",
    "isolationDeg", "isolationDeltaMag", "outlierTexels", "raDeg", "decDeg", "mag",
}
# Decimal places scripts/register_sky_panorama.py rounds each value to; other numbers get the floor.
REFIT_ROUNDING_DECIMALS = {
    "yOffsetPx": 4, "rotationFromGalacticDeg": 4, "lDeg": 4, "bDeg": 4,
    "medianTexels": 3, "p90Texels": 3, "rmsTexels": 3, "maxTexels": 3, "maxAbsTexels": 3,
    "modelX": 3, "modelY": 3, "measuredX": 3, "measuredY": 3,
}


def _refit_differences(fresh, committed, path: str = "") -> list[str]:
    """Paths where a fresh fit differs from the committed one beyond platform rounding."""
    key = path.rsplit(".", 1)[-1].split("[")[0]
    if isinstance(committed, dict):
        if not isinstance(fresh, dict) or fresh.keys() != committed.keys():
            return [f"{path}: keys differ"]
        return [d for k in committed for d in _refit_differences(fresh[k], committed[k], f"{path}.{k}")]
    if isinstance(committed, list):
        if not isinstance(fresh, list) or len(fresh) != len(committed):
            return [f"{path}: length differs"]
        return [d for i, (a, b) in enumerate(zip(fresh, committed)) for d in _refit_differences(a, b, f"{path}[{i}]")]
    if path.endswith("offsetTable.data"):
        a = np.frombuffer(base64.b64decode(fresh), dtype=np.uint8).astype(int)
        b = np.frombuffer(base64.b64decode(committed), dtype=np.uint8).astype(int)
        if a.shape != b.shape:
            return [f"{path}: table sizes differ"]
        diff = np.abs(a - b)
        if diff.max() > REFIT_TABLE_CODE_SLACK or np.count_nonzero(diff) > REFIT_TABLE_CELL_FRACTION * diff.size:
            return [f"{path}: {np.count_nonzero(diff)} of {diff.size} codes differ, by at most {diff.max()}"]
        return []
    if committed is None or isinstance(committed, (bool, str)):
        return [] if fresh == committed else [f"{path}: {fresh!r} != {committed!r}"]
    if isinstance(committed, int):
        slack = REFIT_COUNT_SLACK if key in REFIT_COUNT_KEYS else 0
        ok = isinstance(fresh, int) and not isinstance(fresh, bool) and abs(fresh - committed) <= slack
        return [] if ok else [f"{path}: {fresh!r} != {committed!r}"]
    if key in REFIT_EXACT_FLOAT_KEYS:
        return [] if fresh == committed else [f"{path}: {fresh!r} != {committed!r}"]
    decimals = REFIT_ROUNDING_DECIMALS.get(key)
    tolerance = REFIT_FLOAT_FLOOR if decimals is None else REFIT_ROUNDING_STEPS * 10.0**-decimals
    ok = isinstance(fresh, (int, float)) and abs(fresh - committed) <= tolerance
    return [] if ok else [f"{path}: {fresh!r} != {committed!r} (tolerance {tolerance:g})"]


def test_refit_comparison_allows_platform_rounding_and_nothing_more():
    committed = json.loads((TEXTURES / "eso0932a_registration.json").read_text())
    assert _refit_differences(committed, committed) == []
    nudged = json.loads(json.dumps(committed))
    nudged["yOffsetPx"] += 1e-4
    nudged["fit"]["selection"]["matched"] += 1
    assert _refit_differences(nudged, committed) == []
    for change in (
        lambda s: s.__setitem__("yOffsetPx", s["yOffsetPx"] + 1e-3),
        lambda s: s["fit"]["selection"].__setitem__("matched", s["fit"]["selection"]["matched"] + 3),
        lambda s: s["offsetTable"].__setitem__("kernelSigmaDeg", 2.0),
        lambda s: s["image"].__setitem__("sha256", "0" * 64),
        lambda s: s["galacticToPanorama"][0].__setitem__(0, s["galacticToPanorama"][0][0] + 1e-6),
    ):
        broken = json.loads(json.dumps(committed))
        change(broken)
        assert _refit_differences(broken, committed), "a real change must be reported"


def test_committed_panorama_solution_is_a_fresh_fit():
    script = _load_script()
    committed = (TEXTURES / "eso0932a_registration.json").read_text()
    differences = _refit_differences(json.loads(script.dumps(script.solve())), json.loads(committed))
    assert not differences, "rerun scripts/register_sky_panorama.py; differs at: " + "; ".join(differences[:20])
    solution = json.loads(committed)
    held_out = {row["sigmaDeg"]: row for row in solution["fit"]["heldOut"]["withOffsetTable"]}
    chosen = held_out[solution["offsetTable"]["kernelSigmaDeg"]]
    # Held-out stars land within half a texel (median) and about a texel (90th percentile)
    assert chosen["medianTexels"] < 0.5 and chosen["p90Texels"] < 1.0
    assert solution["fit"]["heldOut"]["rigidOnly"]["medianTexels"] > chosen["medianTexels"]



# The photograph's two planets (sky layer disclosure; kept unretouched by the lead's decision of
# 2026-11-04) and the dates on which the repository's ephemeris puts each on its image. ESO gives
# no capture dates; these are dates of agreement.
PLANETS_IN_PANORAMA = (("Venus", "2009-01-22"), ("Jupiter", "2008-06-09"))
SATURATED = 0.9  # mean of R, G and B in [0, 1]


def _committed_registration() -> tuple[sr.RigidSolution, np.ndarray]:
    reg = json.loads((TEXTURES / "eso0932a_registration.json").read_text())
    table = reg["offsetTable"]
    offsets = sr.decode_offset_table(base64.b64decode(table["data"]), table["width"], table["height"], table["scale"])
    return sr.RigidSolution(np.array(reg["galacticToPanorama"]), float(reg["yOffsetPx"])), offsets


def _saturated_core(luminance: np.ndarray, x: float, y: float, radius: int = 20) -> tuple[float, float, int] | None:
    """Centroid and size of the saturated blob nearest (x, y) within a square of +-radius pixels."""
    x0, y0 = int(round(x)), int(round(y))
    window = luminance[y0 - radius : y0 + radius + 1, x0 - radius : x0 + radius + 1]
    labels, count = ndimage.label(window > SATURATED)
    best = None
    for k in range(1, count + 1):
        ys, xs = np.nonzero(labels == k)
        cx, cy = xs.mean() + x0 - radius, ys.mean() + y0 - radius
        if best is None or math.hypot(cx - x, cy - y) < math.hypot(best[0] - x, best[1] - y):
            best = (float(cx), float(cy), int(len(xs)))
    return best


def _planet_offset_deg(body, iso, solution, offsets, luminance, geom) -> tuple[float, int] | None:
    """Angle from the ephemeris position, mapped through the registration, to the nearest saturated blob."""
    planet = compute_solar_system_ephemerides(iso, 0.0, 0.0, bodies=(body,))[0]
    g = sr.radec_to_unit(planet.ra_deg, planet.dec_deg)[None, :] @ sr.ICRS_TO_GALACTIC.T
    x, y = sr.apply_offsets(g, solution, offsets, geom)
    core = _saturated_core(luminance, float(x[0]), float(y[0]))
    if core is None:
        return None
    _, lat = sr.lon_lat(g @ solution.rotation.T)
    dx = float(sr.wrap_dx(core[0] - float(x[0]), geom.width)) * math.cos(float(lat[0]))
    return math.hypot(dx, core[1] - float(y[0])) * 360.0 / geom.width, core[2]


def test_the_panorama_shows_venus_and_jupiter_where_the_ephemeris_puts_them():
    """Each planet is a saturated point that no catalog star explains, on the ephemeris track.

    On some hour of its date the repository's ephemeris puts the planet within 0.1 deg of a
    saturated blob of at least 20 pixels with no catalog star to magnitude 8 within 0.3 deg.
    A month earlier the planet stood elsewhere, and no such blob lies at that position, so the
    match is not something the search would find anywhere.
    """
    rgb = np.asarray(Image.open(TEXTURES / "eso_milkyway_panorama.jpg").convert("RGB"), dtype=np.float64) / 255.0
    luminance = rgb.mean(axis=2)
    geom = sr.PanoramaGeometry(width=rgb.shape[1], height=rgb.shape[0])
    solution, offsets = _committed_registration()
    icrs, _ = _load_script().load_catalog((0, 1))
    stars = cKDTree(icrs)

    for body, day in PLANETS_IN_PANORAMA:
        hours = [f"{day}T{h:02d}:00:00Z" for h in range(24)]
        measured = [(m, iso) for iso in hours if (m := _planet_offset_deg(body, iso, solution, offsets, luminance, geom))]
        (offset_deg, core_px), iso = min(measured)
        assert offset_deg < 0.1, f"{body}: nearest saturated point {offset_deg:.3f} deg from the ephemeris on {day}"
        assert core_px >= 20, f"{body}: {core_px} saturated pixels"
        planet = compute_solar_system_ephemerides(iso, 0.0, 0.0, bodies=(body,))[0]
        chord = 2.0 * math.sin(math.radians(0.3) / 2.0)
        assert stars.query_ball_point(sr.radec_to_unit(planet.ra_deg, planet.dec_deg), chord) == [], body

        earlier = (datetime.fromisoformat(iso) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        control = _planet_offset_deg(body, earlier, solution, offsets, luminance, geom)
        assert control is None or control[0] >= 0.1 or control[1] < 20, f"{body} on {earlier}: {control}"
