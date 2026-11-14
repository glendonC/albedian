"""Characterization and property-based surface tests for `services.science.sky_registration`.

Pins the public module path, constants, frozen dataclasses, all 22 callable signatures,
behavioral invariants, edge guards, and Hypothesis round-trip / orthogonality properties
across coordinate frames, plate carree projection, difference-of-Gaussians star centroiding,
Kabsch and soft-L1 rigid fitting, spherical Gaussian kernel smoothing, and offset-table
encoding and bilinear sampling. When sibling `sky_reg_*.py` modules are present, also verifies
pure facade re-exports, `__all__` completeness, zero namespace leakage, and an acyclic import
graph.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import math
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from scipy.spatial.transform import Rotation

from services.science import sky_registration as sr

SCIENCE_DIR = Path(__file__).resolve().parents[2] / "services" / "science"
ARCSEC_RAD = math.pi / (180.0 * 3600.0)

EXPECTED_CONSTANTS = {
    "ICRS_TO_GALACTIC",
    "NGP_RA_DEG",
    "NGP_DEC_DEG",
    "NODE_LON_DEG",
    "OFFSET_TABLE_BIAS",
    "CENTROID_BOX_HALF",
    "CENTROID_WINDOW_PX",
    "CENTROID_ITERATIONS",
}

EXPECTED_CLASSES = {
    "PanoramaGeometry",
    "RigidSolution",
}

EXPECTED_FUNCTIONS = {
    "radec_to_unit",
    "lon_lat",
    "unit_from_lon_lat",
    "frame_to_pixel",
    "pixel_to_frame",
    "east_north",
    "wrap_dx",
    "difference_of_gaussians",
    "star_centroid",
    "kabsch",
    "model_pixels",
    "pixel_residuals",
    "fit_rigid",
    "tangent_offsets",
    "smooth_offsets",
    "offset_table_frame",
    "encode_offset_table",
    "decode_offset_table",
    "sample_offset_table",
    "apply_offsets",
}

EXPECTED_PUBLIC_SYMBOLS = EXPECTED_CONSTANTS | EXPECTED_CLASSES | EXPECTED_FUNCTIONS

# Parameter tuples: (name, kind, default_or_empty, annotation_or_empty)
_PK = inspect.Parameter.POSITIONAL_OR_KEYWORD
_EMPTY = inspect.Parameter.empty

EXPECTED_SIGNATURES: dict[str, tuple[list[tuple[str, object, object, object]], object]] = {
    "PanoramaGeometry": (
        [("width", _PK, _EMPTY, "int"), ("height", _PK, _EMPTY, "int")],
        None,
    ),
    "RigidSolution": (
        [("rotation", _PK, _EMPTY, "np.ndarray"), ("y_offset_px", _PK, _EMPTY, "float")],
        None,
    ),
    "radec_to_unit": (
        [("ra_deg", _PK, _EMPTY, _EMPTY), ("dec_deg", _PK, _EMPTY, _EMPTY)],
        "np.ndarray",
    ),
    "lon_lat": (
        [("v", _PK, _EMPTY, "np.ndarray")],
        "tuple[np.ndarray, np.ndarray]",
    ),
    "unit_from_lon_lat": (
        [("lon", _PK, _EMPTY, _EMPTY), ("lat", _PK, _EMPTY, _EMPTY)],
        "np.ndarray",
    ),
    "frame_to_pixel": (
        [
            ("p", _PK, _EMPTY, "np.ndarray"),
            ("geom", _PK, _EMPTY, "PanoramaGeometry"),
            ("y_offset_px", _PK, 0.0, "float"),
        ],
        "tuple[np.ndarray, np.ndarray]",
    ),
    "pixel_to_frame": (
        [
            ("x", _PK, _EMPTY, _EMPTY),
            ("y", _PK, _EMPTY, _EMPTY),
            ("geom", _PK, _EMPTY, "PanoramaGeometry"),
            ("y_offset_px", _PK, 0.0, "float"),
        ],
        "np.ndarray",
    ),
    "east_north": (
        [("p", _PK, _EMPTY, "np.ndarray")],
        "tuple[np.ndarray, np.ndarray]",
    ),
    "wrap_dx": (
        [("dx", _PK, _EMPTY, "np.ndarray"), ("width", _PK, _EMPTY, "float")],
        "np.ndarray",
    ),
    "difference_of_gaussians": (
        [
            ("luminance", _PK, _EMPTY, "np.ndarray"),
            ("inner_px", _PK, 1.0, "float"),
            ("outer_px", _PK, 8.0, "float"),
        ],
        "np.ndarray",
    ),
    "star_centroid": (
        [
            ("dog", _PK, _EMPTY, "np.ndarray"),
            ("x", _PK, _EMPTY, "float"),
            ("y", _PK, _EMPTY, "float"),
            ("radius", _PK, _EMPTY, "int"),
            ("min_peak", _PK, _EMPTY, "float"),
        ],
        "tuple[float, float, float] | None",
    ),
    "kabsch": (
        [("source", _PK, _EMPTY, "np.ndarray"), ("target", _PK, _EMPTY, "np.ndarray")],
        "np.ndarray",
    ),
    "model_pixels": (
        [
            ("galactic", _PK, _EMPTY, "np.ndarray"),
            ("solution", _PK, _EMPTY, "RigidSolution"),
            ("geom", _PK, _EMPTY, "PanoramaGeometry"),
        ],
        "tuple[np.ndarray, np.ndarray]",
    ),
    "pixel_residuals": (
        [
            ("galactic", _PK, _EMPTY, "np.ndarray"),
            ("measured_xy", _PK, _EMPTY, "np.ndarray"),
            ("solution", _PK, _EMPTY, "RigidSolution"),
            ("geom", _PK, _EMPTY, "PanoramaGeometry"),
        ],
        "np.ndarray",
    ),
    "fit_rigid": (
        [
            ("galactic", _PK, _EMPTY, "np.ndarray"),
            ("measured_xy", _PK, _EMPTY, "np.ndarray"),
            ("start", _PK, _EMPTY, "RigidSolution"),
            ("geom", _PK, _EMPTY, "PanoramaGeometry"),
            ("scale_px", _PK, 1.0, "float"),
        ],
        "RigidSolution",
    ),
    "tangent_offsets": (
        [
            ("galactic", _PK, _EMPTY, "np.ndarray"),
            ("measured_xy", _PK, _EMPTY, "np.ndarray"),
            ("solution", _PK, _EMPTY, "RigidSolution"),
            ("geom", _PK, _EMPTY, "PanoramaGeometry"),
        ],
        "np.ndarray",
    ),
    "smooth_offsets": (
        [
            ("star_frame", _PK, _EMPTY, "np.ndarray"),
            ("star_offsets", _PK, _EMPTY, "np.ndarray"),
            ("query_frame", _PK, _EMPTY, "np.ndarray"),
            ("sigma_rad", _PK, _EMPTY, "float"),
            ("prior_weight", _PK, _EMPTY, "float"),
            ("chunk", _PK, 4096, "int"),
        ],
        "np.ndarray",
    ),
    "offset_table_frame": (
        [("table_width", _PK, _EMPTY, "int"), ("table_height", _PK, _EMPTY, "int")],
        "np.ndarray",
    ),
    "encode_offset_table": (
        [
            ("offsets", _PK, _EMPTY, "np.ndarray"),
            ("table_width", _PK, _EMPTY, "int"),
            ("table_height", _PK, _EMPTY, "int"),
            ("scale", _PK, _EMPTY, "float"),
        ],
        "bytes",
    ),
    "decode_offset_table": (
        [
            ("blob", _PK, _EMPTY, "bytes"),
            ("table_width", _PK, _EMPTY, "int"),
            ("table_height", _PK, _EMPTY, "int"),
            ("scale", _PK, _EMPTY, "float"),
        ],
        "np.ndarray",
    ),
    "sample_offset_table": (
        [
            ("table", _PK, _EMPTY, "np.ndarray"),
            ("u", _PK, _EMPTY, _EMPTY),
            ("v", _PK, _EMPTY, _EMPTY),
        ],
        "np.ndarray",
    ),
    "apply_offsets": (
        [
            ("galactic", _PK, _EMPTY, "np.ndarray"),
            ("solution", _PK, _EMPTY, "RigidSolution"),
            ("table", _PK, _EMPTY, "np.ndarray"),
            ("geom", _PK, _EMPTY, "PanoramaGeometry"),
        ],
        "tuple[np.ndarray, np.ndarray]",
    ),
}


@st.composite
def unit_vectors(draw: st.DrawFn, max_abs_z: float = 1.0) -> np.ndarray:
    lon = draw(st.floats(min_value=-math.pi + 1e-6, max_value=math.pi - 1e-6, allow_nan=False, allow_infinity=False))
    z = draw(st.floats(min_value=-max_abs_z, max_value=max_abs_z, allow_nan=False, allow_infinity=False))
    r = math.sqrt(max(0.0, 1.0 - z * z))
    return np.array([r * math.cos(lon), r * math.sin(lon), z], dtype=np.float64)


# =============================================================================================
# 1. Public surface, constants, dataclasses, signatures, and modular DAG structure
# =============================================================================================


def test_module_is_regular_python_file_and_exports_expected_surface() -> None:
    module_path = Path(sr.__file__).resolve()
    assert module_path == SCIENCE_DIR / "sky_registration.py"
    assert module_path.is_file()

    assert len(EXPECTED_PUBLIC_SYMBOLS) == 30
    assert len(EXPECTED_SIGNATURES) == 22
    assert set(EXPECTED_SIGNATURES) == EXPECTED_CLASSES | EXPECTED_FUNCTIONS

    for symbol in EXPECTED_PUBLIC_SYMBOLS:
        assert hasattr(sr, symbol), f"missing public symbol on sky_registration: {symbol}"


def test_public_constants_and_frozen_dataclasses() -> None:
    expected_ag = np.array(
        [
            [-0.0548755604162154, -0.8734370902348850, -0.4838350155487132],
            [+0.4941094278755837, -0.4448296299600112, +0.7469822444972189],
            [-0.8676661490190047, -0.1980763734312015, +0.4559837761750669],
        ],
        dtype=np.float64,
    )
    assert isinstance(sr.ICRS_TO_GALACTIC, np.ndarray)
    assert sr.ICRS_TO_GALACTIC.shape == (3, 3)
    assert sr.ICRS_TO_GALACTIC.dtype == np.float64
    assert np.array_equal(sr.ICRS_TO_GALACTIC, expected_ag)

    assert sr.NGP_RA_DEG == 192.85948
    assert sr.NGP_DEC_DEG == 27.12825
    assert sr.NODE_LON_DEG == 32.93192
    assert sr.OFFSET_TABLE_BIAS == 128.0
    assert isinstance(sr.OFFSET_TABLE_BIAS, float)
    assert sr.CENTROID_BOX_HALF == 4
    assert isinstance(sr.CENTROID_BOX_HALF, int)
    assert sr.CENTROID_WINDOW_PX == 1.5
    assert sr.CENTROID_ITERATIONS == 6
    assert isinstance(sr.CENTROID_ITERATIONS, int)

    assert dataclasses.is_dataclass(sr.PanoramaGeometry)
    assert dataclasses.is_dataclass(sr.RigidSolution)
    geom = sr.PanoramaGeometry(width=6000, height=3000)
    assert (geom.width, geom.height) == (6000, 3000)
    with pytest.raises(dataclasses.FrozenInstanceError):
        geom.width = 100  # type: ignore[misc]

    sol = sr.RigidSolution(rotation=np.eye(3), y_offset_px=-1.25)
    assert sol.y_offset_px == -1.25
    assert np.array_equal(sol.rotation, np.eye(3))
    with pytest.raises(dataclasses.FrozenInstanceError):
        sol.y_offset_px = 0.0  # type: ignore[misc]


@pytest.mark.parametrize("name", sorted(EXPECTED_SIGNATURES))
def test_all_twenty_two_callable_signatures_match_contract(name: str) -> None:
    obj = getattr(sr, name)
    assert callable(obj)
    sig = inspect.signature(obj)
    expected_params, expected_return = EXPECTED_SIGNATURES[name]
    actual_params = [
        (p.name, p.kind, p.default, p.annotation)
        for p in sig.parameters.values()
    ]
    assert actual_params == expected_params
    assert sig.return_annotation == expected_return


def test_modular_facade_reexports_and_acyclic_sibling_graph_when_split() -> None:
    sibling_files = sorted(SCIENCE_DIR.glob("sky_reg_*.py"))
    if hasattr(sr, "__all__") or sibling_files:
        assert hasattr(sr, "__all__")
        assert len(sr.__all__) == len(set(sr.__all__))
        assert set(sr.__all__) == EXPECTED_PUBLIC_SYMBOLS
        public_attrs = {n for n in dir(sr) if not n.startswith("_")}
        assert public_attrs == EXPECTED_PUBLIC_SYMBOLS

    if not sibling_files:
        return

    facade_tree = ast.parse((SCIENCE_DIR / "sky_registration.py").read_text(encoding="utf-8"))
    for node in facade_tree.body:
        assert not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)), (
            "sky_registration.py must remain a pure re-export facade when sky_reg_*.py modules exist"
        )

    sibling_stems = {p.stem for p in sibling_files}
    assert "sky_registration" not in sibling_stems
    assert all(not stem.startswith("catalog_tile_") for stem in sibling_stems)

    # Build internal dependency graph among sky_reg_*.py and assert strict acyclicity
    edges: dict[str, set[str]] = {stem: set() for stem in sibling_stems}
    exported_origins: dict[str, object] = {}
    for path in sibling_files:
        stem = path.stem
        mod = importlib.import_module(f"services.science.{stem}")
        for sym in EXPECTED_PUBLIC_SYMBOLS:
            if hasattr(mod, sym):
                exported_origins[sym] = getattr(mod, sym)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module != "services.science.sky_registration", (
                    f"{stem} must not import back from facade sky_registration"
                )
                if node.module.startswith("services.science.sky_reg_"):
                    target = node.module.removeprefix("services.science.")
                    assert target in sibling_stems
                    assert target != stem
                    edges[stem].add(target)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "services.science.sky_registration"
                    if alias.name.startswith("services.science.sky_reg_"):
                        target = alias.name.removeprefix("services.science.")
                        edges[stem].add(target)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_name: str) -> None:
        assert node_name not in visiting, f"cycle in sky_reg_* imports at {node_name}"
        if node_name in visited:
            return
        visiting.add(node_name)
        for dep in edges[node_name]:
            visit(dep)
        visiting.remove(node_name)
        visited.add(node_name)

    for stem in sorted(sibling_stems):
        visit(stem)

    for sym in EXPECTED_PUBLIC_SYMBOLS:
        assert sym in exported_origins, f"{sym} not found in any sky_reg_*.py module"
        assert getattr(sr, sym) is exported_origins[sym]


# =============================================================================================
# 2. Coordinate frames, spherical unit vectors, and tangent triads
# =============================================================================================


def test_icrs_to_galactic_frame_orthogonality_determinant_and_poles() -> None:
    ag = sr.ICRS_TO_GALACTIC
    assert np.allclose(ag @ ag.T, np.eye(3), atol=1e-14)
    assert np.allclose(ag.T @ ag, np.eye(3), atol=1e-14)
    assert float(np.linalg.det(ag)) == pytest.approx(1.0, abs=1e-14)

    ngp = ag @ sr.radec_to_unit(sr.NGP_RA_DEG, sr.NGP_DEC_DEG)
    assert np.linalg.norm(ngp - np.array([0.0, 0.0, 1.0])) < 0.1 * ARCSEC_RAD

    node = ag @ sr.radec_to_unit(sr.NGP_RA_DEG + 90.0, 0.0)
    node_lon = math.radians(sr.NODE_LON_DEG)
    expected_node = np.array([math.cos(node_lon), math.sin(node_lon), 0.0])
    assert np.linalg.norm(node - expected_node) < 0.1 * ARCSEC_RAD


def test_radec_and_lon_lat_cardinal_points_and_pole_clamping() -> None:
    assert np.allclose(sr.radec_to_unit(0.0, 0.0), [1.0, 0.0, 0.0], atol=1e-15)
    assert np.allclose(sr.radec_to_unit(90.0, 0.0), [0.0, 1.0, 0.0], atol=1e-15)
    assert np.allclose(sr.radec_to_unit(180.0, 0.0), [-1.0, 0.0, 0.0], atol=1e-15)
    assert np.allclose(sr.radec_to_unit(0.0, 90.0), [0.0, 0.0, 1.0], atol=1e-15)
    assert np.allclose(sr.radec_to_unit(0.0, -90.0), [0.0, 0.0, -1.0], atol=1e-15)

    batch = sr.radec_to_unit([0.0, 90.0], [0.0, 0.0])
    assert batch.shape == (2, 3)

    # Slight numerical overshoot beyond +-1 in z must clamp cleanly to +-pi/2 without NaN
    overshoot = np.array([[0.0, 0.0, 1.000001], [0.0, 0.0, -1.000001]])
    lon, lat = sr.lon_lat(overshoot)
    assert not np.any(np.isnan(lon)) and not np.any(np.isnan(lat))
    assert np.allclose(lat, [math.pi / 2.0, -math.pi / 2.0], atol=1e-15)


@settings(max_examples=80, deadline=None)
@given(u=unit_vectors(), v=unit_vectors())
def test_pbt_icrs_galactic_isometry_and_spherical_round_trip(u: np.ndarray, v: np.ndarray) -> None:
    ag = sr.ICRS_TO_GALACTIC
    gu = ag @ u
    gv = ag @ v
    assert float(np.linalg.norm(gu)) == pytest.approx(1.0, abs=1e-13)
    assert float(gu @ gv) == pytest.approx(float(u @ v), abs=1e-13)
    assert np.allclose(ag.T @ gu, u, atol=1e-13)

    lon, lat = sr.lon_lat(u)
    assert -math.pi <= float(lon) <= math.pi
    assert -math.pi / 2.0 <= float(lat) <= math.pi / 2.0
    recovered = sr.unit_from_lon_lat(lon, lat)
    # arcsin near |z| ~ 1 has float64 sensitivity ~1e-8 rad
    assert np.allclose(recovered, u, atol=1e-7)


@settings(max_examples=80, deadline=None)
@given(p=unit_vectors(max_abs_z=0.999))
def test_pbt_east_north_forms_right_handed_orthonormal_tangent_basis(p: np.ndarray) -> None:
    east, north = sr.east_north(p)
    assert float(np.linalg.norm(east)) == pytest.approx(1.0, abs=1e-13)
    assert float(np.linalg.norm(north)) == pytest.approx(1.0, abs=1e-13)
    assert float(east @ p) == pytest.approx(0.0, abs=1e-13)
    assert float(north @ p) == pytest.approx(0.0, abs=1e-13)
    assert float(east @ north) == pytest.approx(0.0, abs=1e-13)
    assert float(east[2]) == 0.0
    assert float(north[2]) >= 0.0
    # Right-handed spherical basis: east x north == outward radial unit vector p
    assert np.allclose(np.cross(east, north), p, atol=1e-12)


# =============================================================================================
# 3. Plate carree equirectangular projection and longitude seam wrapping
# =============================================================================================


def test_frame_to_pixel_and_pixel_to_frame_orientations_and_seam() -> None:
    geom = sr.PanoramaGeometry(width=360, height=180)
    y0 = -1.5
    # Centre (lon=0, lat=0) -> (W/2, H/2 + y0)
    x, y = sr.frame_to_pixel(np.array([1.0, 0.0, 0.0]), geom, y0)
    assert (float(x), float(y)) == pytest.approx((180.0, 90.0 + y0), abs=1e-12)

    # East (lon=+pi/2) increases to the left -> x = W/4; West (lon=-pi/2) -> x = 3W/4
    xe, ye = sr.frame_to_pixel(np.array([0.0, 1.0, 0.0]), geom, y0)
    xw, yw = sr.frame_to_pixel(np.array([0.0, -1.0, 0.0]), geom, y0)
    assert (float(xe), float(ye)) == pytest.approx((90.0, 90.0 + y0), abs=1e-12)
    assert (float(xw), float(yw)) == pytest.approx((270.0, 90.0 + y0), abs=1e-12)

    # North pole -> y = y0; South pole -> y = H + y0
    _, yn = sr.frame_to_pixel(np.array([0.0, 0.0, 1.0]), geom, y0)
    _, ys = sr.frame_to_pixel(np.array([0.0, 0.0, -1.0]), geom, y0)
    assert float(yn) == pytest.approx(y0, abs=1e-12)
    assert float(ys) == pytest.approx(180.0 + y0, abs=1e-12)

    # Anti-centre (lon=pi) wraps into [0, W) at x = 0.0
    xs_seam, _ = sr.frame_to_pixel(np.array([-1.0, 0.0, 0.0]), geom, y0)
    assert float(xs_seam) == pytest.approx(0.0, abs=1e-12)


@settings(max_examples=80, deadline=None)
@given(
    p=unit_vectors(max_abs_z=0.999),
    width=st.integers(min_value=64, max_value=8000),
    height=st.integers(min_value=32, max_value=4000),
    y_offset=st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False),
)
def test_pbt_equirectangular_pixel_frame_round_trip(
    p: np.ndarray, width: int, height: int, y_offset: float
) -> None:
    geom = sr.PanoramaGeometry(width=width, height=height)
    x, y = sr.frame_to_pixel(p, geom, y_offset)
    assert 0.0 <= float(x) < float(width)
    assert y_offset <= float(y) <= float(height) + y_offset
    recovered = sr.pixel_to_frame(x, y, geom, y_offset)
    assert np.allclose(recovered, p, atol=1e-7)


@settings(max_examples=80, deadline=None)
@given(
    dx=st.floats(min_value=-1e5, max_value=1e5, allow_nan=False, allow_infinity=False),
    width=st.floats(min_value=10.0, max_value=6000.0, allow_nan=False, allow_infinity=False),
    turns=st.integers(min_value=-5, max_value=5),
)
def test_pbt_wrap_dx_range_and_periodicity(dx: float, width: float, turns: int) -> None:
    wrapped = float(sr.wrap_dx(np.array(dx), width))
    assert -width / 2.0 <= wrapped < width / 2.0
    shifted = float(sr.wrap_dx(np.array(dx + turns * width), width))
    assert shifted == pytest.approx(wrapped, abs=1e-8)
    # Exact half-width maps to -width / 2
    assert float(sr.wrap_dx(np.array(width / 2.0), width)) == pytest.approx(-width / 2.0, abs=1e-12)


# =============================================================================================
# 4. Difference-of-Gaussians filtering and subpixel star centroiding
# =============================================================================================


def test_difference_of_gaussians_flat_zero_x_wrap_and_sigma_response() -> None:
    flat = np.full((24, 48), 0.37, dtype=np.float64)
    assert np.allclose(sr.difference_of_gaussians(flat), 0.0, atol=1e-14)

    # Impulse at column 0 must wrap across the seam into column width - 1
    seam_img = np.zeros((24, 48), dtype=np.float64)
    seam_img[12, 0] = 1.0
    dog_default = sr.difference_of_gaussians(seam_img)
    assert dog_default[12, 47] == pytest.approx(dog_default[12, 1], abs=1e-14)
    assert dog_default[12, 0] > dog_default[12, 1] > 0.0

    # Changing inner_px or outer_px changes the response
    dog_wider_inner = sr.difference_of_gaussians(seam_img, inner_px=2.0, outer_px=8.0)
    dog_narrow_outer = sr.difference_of_gaussians(seam_img, inner_px=1.0, outer_px=4.0)
    assert dog_wider_inner[12, 0] < dog_default[12, 0]
    assert dog_narrow_outer[12, 0] < dog_default[12, 0]


def test_star_centroid_seam_wrap_and_boundary_and_threshold_guards() -> None:
    height, width = 40, 80
    gy, gx = np.mgrid[0:height, 0:width]

    # Star right near the x = 0 / width seam (cx = 0.25, cy = 20.35), searched from x = 79.1
    cx_true, cy_true = 0.25, 20.35
    dx_seam = sr.wrap_dx(gx + 0.5 - cx_true, width)
    img = 0.04 + 0.9 * np.exp(-(dx_seam**2 + (gy + 0.5 - cy_true) ** 2) / (2.0 * 1.3**2))
    dog = sr.difference_of_gaussians(img)

    found = sr.star_centroid(dog, 79.1, 20.9, radius=3, min_peak=0.02)
    assert found is not None
    fx, fy, peak = found
    assert abs(float(sr.wrap_dx(np.array(fx - cx_true), width))) < 0.05
    assert abs(fy - cy_true) < 0.05
    assert peak > 0.02

    # Threshold guard: strict inequality peak <= min_peak returns None
    assert sr.star_centroid(dog, 79.1, 20.9, radius=3, min_peak=peak) is None
    assert sr.star_centroid(dog, 79.1, 20.9, radius=3, min_peak=peak - 1e-6) is not None

    # Top and bottom row-margin guards: rows[0] >= CENTROID_BOX_HALF + 1 and rows[-1] <= H - CENTROID_BOX_HALF - 2
    radius = 2
    min_valid_yi = sr.CENTROID_BOX_HALF + 1 + radius
    max_valid_yi = height - sr.CENTROID_BOX_HALF - 2 - radius
    uniform_peak = np.zeros((height, width), dtype=np.float64)
    uniform_peak[:, 40] = 0.5
    assert sr.star_centroid(uniform_peak, 40.2, float(min_valid_yi - 1) + 0.1, radius, 0.1) is None
    assert sr.star_centroid(uniform_peak, 40.2, float(min_valid_yi) + 0.1, radius, 0.1) is not None
    assert sr.star_centroid(uniform_peak, 40.2, float(max_valid_yi + 1) + 0.1, radius, 0.1) is None
    assert sr.star_centroid(uniform_peak, 40.2, float(max_valid_yi) + 0.1, radius, 0.1) is not None

    # Non-positive clipped refinement box guard (all dog values negative, min_peak below them)
    negative_dog = np.full((height, width), -0.1, dtype=np.float64)
    assert sr.star_centroid(negative_dog, 40.0, 20.0, radius=2, min_peak=-0.5) is None


# =============================================================================================
# 5. Kabsch SVD rotation, rigid panorama residuals, robust fit, and tangent offsets
# =============================================================================================


@settings(max_examples=60, deadline=None)
@given(
    rotvec=st.tuples(
        st.floats(min_value=-1.5, max_value=1.5, allow_nan=False, allow_infinity=False),
        st.floats(min_value=-1.5, max_value=1.5, allow_nan=False, allow_infinity=False),
        st.floats(min_value=-1.5, max_value=1.5, allow_nan=False, allow_infinity=False),
    ),
    seed=st.integers(min_value=0, max_value=2**31 - 1),
)
def test_pbt_kabsch_recovers_proper_rotation_and_rejects_improper_reflection(
    rotvec: tuple[float, float, float], seed: int
) -> None:
    rng = np.random.default_rng(seed)
    pts = rng.normal(size=(25, 3))
    pts /= np.linalg.norm(pts, axis=1, keepdims=True)
    rot_true = Rotation.from_rotvec(rotvec).as_matrix()

    rot_fit = sr.kabsch(pts, pts @ rot_true.T)
    assert np.allclose(rot_fit @ rot_fit.T, np.eye(3), atol=1e-12)
    assert float(np.linalg.det(rot_fit)) == pytest.approx(1.0, abs=1e-12)
    assert np.allclose(rot_fit, rot_true, atol=1e-11)

    # Improper reflection of target must still yield det == +1 in SO(3)
    reflected = (pts @ rot_true.T) * np.array([1.0, 1.0, -1.0])
    rot_reflected = sr.kabsch(pts, reflected)
    assert np.allclose(rot_reflected @ rot_reflected.T, np.eye(3), atol=1e-12)
    assert float(np.linalg.det(rot_reflected)) == pytest.approx(1.0, abs=1e-12)


def test_pixel_residuals_seam_wrap_and_tangent_offset_signs() -> None:
    geom = sr.PanoramaGeometry(width=360, height=180)
    sol = sr.RigidSolution(rotation=np.eye(3), y_offset_px=0.0)

    # Star near the seam (modelled x ~ 0.5, y = 90) measured across seam at x = 359.5 -> dx = -1.0
    lon_near_seam = math.pi - math.radians(0.5)
    star = sr.unit_from_lon_lat(lon_near_seam, 0.0)[None, :]
    mx, my = sr.model_pixels(star, sol, geom)
    assert float(mx[0]) == pytest.approx(0.5, abs=1e-12)
    measured = np.array([[359.5, float(my[0]) + 2.0]])
    res = sr.pixel_residuals(star, measured, sol, geom)
    assert np.allclose(res, [[-1.0, 2.0]], atol=1e-11)

    # Tangent offsets: east = -dx * cos(lat), north = -dy
    lat = math.radians(60.0)  # cos(lat) = 0.5
    high_lat_star = sr.unit_from_lon_lat(0.0, lat)[None, :]
    hx, hy = sr.model_pixels(high_lat_star, sol, geom)
    # Measured 4 px to the left (dx = -4 -> east = +2) and 3 px above (dy = -3 -> north = +3)
    toff = sr.tangent_offsets(high_lat_star, np.array([[float(hx[0]) - 4.0, float(hy[0]) - 3.0]]), sol, geom)
    assert np.allclose(toff, [[2.0, 3.0]], atol=1e-11)


def test_fit_rigid_is_robust_to_gross_outliers() -> None:
    rng = np.random.default_rng(42)
    geom = sr.PanoramaGeometry(width=720, height=360)
    truth = sr.RigidSolution(Rotation.from_rotvec([0.03, -0.04, 0.02]).as_matrix(), y_offset_px=-1.3)

    stars = rng.normal(size=(240, 3))
    stars /= np.linalg.norm(stars, axis=1, keepdims=True)
    stars = stars[np.abs(stars[:, 2]) < 0.9]
    x, y = sr.model_pixels(stars, truth, geom)
    xy = np.stack([x, y], axis=-1)
    # Corrupt 10 percent of stars with 30 px outliers
    n_outliers = len(xy) // 10
    xy[:n_outliers, 0] = (xy[:n_outliers, 0] + 30.0) % geom.width
    xy[:n_outliers, 1] += 25.0

    fitted = sr.fit_rigid(stars, xy, sr.RigidSolution(np.eye(3), 0.0), geom, scale_px=0.5)
    unrobust = sr.fit_rigid(stars, xy, sr.RigidSolution(np.eye(3), 0.0), geom, scale_px=1e4)
    rot_err = Rotation.from_matrix(fitted.rotation @ truth.rotation.T).magnitude()
    unrobust_rot_err = Rotation.from_matrix(unrobust.rotation @ truth.rotation.T).magnitude()
    assert rot_err < 1e-3
    assert rot_err < 0.05 * unrobust_rot_err
    assert abs(fitted.y_offset_px - truth.y_offset_px) < 0.1
    assert abs(fitted.y_offset_px - truth.y_offset_px) < 0.05 * abs(unrobust.y_offset_px - truth.y_offset_px)


# =============================================================================================
# 6. Spherical kernel smoothing, held-out verification, and offset-table codec/sampling
# =============================================================================================


def test_smooth_offsets_analytical_kernel_prior_shrinkage_and_chunk_invariance() -> None:
    # Single star queried at its own position: weight = 1.0, so output = offset / (1 + prior_weight)
    star = sr.unit_from_lon_lat([0.4], [-0.3])
    offset = np.array([[2.4, -1.2]])
    out_single = sr.smooth_offsets(star, offset, star, sigma_rad=0.1, prior_weight=0.5)
    assert np.allclose(out_single, offset / 1.5, atol=1e-13)

    # Two equatorial stars symmetric around lon = 0 at +-delta: query at lon = 0 has weight exp(-(1 - cos(delta)) / sigma^2)
    delta = 0.08
    sigma = 0.12
    prior = 0.4
    two_stars = sr.unit_from_lon_lat([-delta, delta], [0.0, 0.0])
    # Pure north offsets stay parallel to [0, 0, 1] along the equator
    north_offsets = np.array([[0.0, 1.5], [0.0, 2.5]])
    query_equator = sr.unit_from_lon_lat([0.0], [0.0])
    w = math.exp(-(1.0 - math.cos(delta)) / (sigma * sigma))
    expected_north = w * (1.5 + 2.5) / (2.0 * w + prior)
    out_two = sr.smooth_offsets(two_stars, north_offsets, query_equator, sigma_rad=sigma, prior_weight=prior)
    assert float(out_two[0, 0]) == pytest.approx(0.0, abs=1e-13)
    assert float(out_two[0, 1]) == pytest.approx(expected_north, abs=1e-13)

    # Chunk parameter must not alter numerical results
    rng = np.random.default_rng(19)
    s_dirs = rng.normal(size=(40, 3))
    s_dirs /= np.linalg.norm(s_dirs, axis=1, keepdims=True)
    q_dirs = rng.normal(size=(25, 3))
    q_dirs /= np.linalg.norm(q_dirs, axis=1, keepdims=True)
    s_off = rng.normal(scale=1.2, size=(40, 2))
    full = sr.smooth_offsets(s_dirs, s_off, q_dirs, sigma_rad=0.2, prior_weight=0.5, chunk=4096)
    chunked = sr.smooth_offsets(s_dirs, s_off, q_dirs, sigma_rad=0.2, prior_weight=0.5, chunk=7)
    assert np.allclose(full, chunked, atol=1e-14)


def test_held_out_offset_table_reduces_smooth_distortion_residuals() -> None:
    rng = np.random.default_rng(108)
    geom = sr.PanoramaGeometry(width=720, height=360)
    sol = sr.RigidSolution(Rotation.from_rotvec([0.02, -0.01, 0.03]).as_matrix(), y_offset_px=0.5)

    stars = rng.normal(size=(400, 3))
    stars /= np.linalg.norm(stars, axis=1, keepdims=True)
    stars = stars[np.abs(stars[:, 2]) < 0.9]
    frame = stars @ sol.rotation.T
    lon, lat = sr.lon_lat(frame)
    # Smooth synthetic stitching distortion field on the sphere
    true_east = 1.6 * np.sin(2.0 * lon) * np.cos(lat)
    true_north = -1.2 * np.cos(lon) * np.sin(2.0 * lat)
    x0, y0 = sr.frame_to_pixel(frame, geom, sol.y_offset_px)
    measured = np.stack([(x0 - true_east / np.cos(lat)) % geom.width, y0 - true_north], axis=-1)

    train, held = stars[::2], stars[1::2]
    xy_train, xy_held = measured[::2], measured[1::2]
    train_off = sr.tangent_offsets(train, xy_train, sol, geom)
    held_before = sr.tangent_offsets(held, xy_held, sol, geom)

    table_w, table_h, scale = 90, 45, 32.0
    grid = sr.offset_table_frame(table_w, table_h)
    smoothed = sr.smooth_offsets(train @ sol.rotation.T, train_off, grid, sigma_rad=math.radians(8.0), prior_weight=0.2)
    table = sr.decode_offset_table(sr.encode_offset_table(smoothed, table_w, table_h, scale), table_w, table_h, scale)

    hx, hy = sr.apply_offsets(held, sol, table, geom)
    _, held_lat = sr.lon_lat(held @ sol.rotation.T)
    dx_after = sr.wrap_dx(xy_held[:, 0] - hx, geom.width) * np.cos(held_lat)
    dy_after = xy_held[:, 1] - hy

    rms_before = float(np.sqrt(np.mean(np.sum(held_before**2, axis=1))))
    rms_after = float(np.sqrt(np.mean(dx_after**2 + dy_after**2)))
    assert rms_after < 0.3 * rms_before


def test_offset_table_frame_codec_sampling_and_polar_apply_offsets() -> None:
    w, h, scale = 8, 4, 32.0
    grid = sr.offset_table_frame(w, h)
    assert grid.shape == (w * h, 3)
    assert np.allclose(np.linalg.norm(grid, axis=1), 1.0, atol=1e-14)
    # Row 0 sits at v = 0.5 / h (southern hemisphere, z < 0); top row sits at z > 0
    assert np.all(grid[:w, 2] < 0.0)
    assert np.all(grid[-w:, 2] > 0.0)

    # Exact encoding bounds: code = rint(val * scale) + 128 in [0, 255]
    zeros = np.zeros((h, w, 2), dtype=np.float64)
    assert sr.encode_offset_table(zeros, w, h, scale) == bytes([128] * (w * h * 2))
    extremes = np.zeros((1, 2, 2), dtype=np.float64)
    extremes[0, 0] = [-128.0 / scale, 127.0 / scale]
    blob = sr.encode_offset_table(extremes, 2, 1, scale)
    assert list(blob[:2]) == [0, 255]
    assert np.allclose(sr.decode_offset_table(blob, 2, 1, scale), extremes, atol=1e-15)

    with pytest.raises(ValueError, match="encodable range"):
        sr.encode_offset_table(np.array([[[128.0 / scale, 0.0]]]), 1, 1, scale)
    with pytest.raises(ValueError, match="encodable range"):
        sr.encode_offset_table(np.array([[[-129.0 / scale, 0.0]]]), 1, 1, scale)

    # Clamping in v outside [0, 1] and wrapping in u across integer periods
    rng = np.random.default_rng(5)
    table = rng.uniform(-2.0, 2.0, size=(h, w, 2))
    assert np.allclose(sr.sample_offset_table(table, 0.5 / w, -0.4), table[0, 0], atol=1e-14)
    assert np.allclose(sr.sample_offset_table(table, 0.5 / w, 1.7), table[h - 1, 0], atol=1e-14)
    assert np.allclose(
        sr.sample_offset_table(table, 0.37 + 3.0, 0.42),
        sr.sample_offset_table(table, 0.37, 0.42),
        atol=1e-14,
    )

    # Polar cos(lat) floor (1e-3) in apply_offsets prevents division by zero at the poles
    geom = sr.PanoramaGeometry(width=360, height=180)
    sol = sr.RigidSolution(np.eye(3), 0.0)
    const_table = np.broadcast_to(np.array([0.05, 0.25]), (h, w, 2)).copy()
    poles = np.array([[0.0, 0.0, 1.0], [0.0, 0.0, -1.0]])
    px, py = sr.apply_offsets(poles, sol, const_table, geom)
    assert np.all(np.isfinite(px)) and np.all(np.isfinite(py))
    # At lat = +-pi/2, x = 180, cos_lat clamped to 1e-3 -> (180 - 0.05 / 1e-3) % 360 == 130.0
    assert np.allclose(px, [130.0, 130.0], atol=1e-11)
    assert np.allclose(py, [-0.25, 179.75], atol=1e-11)


@settings(max_examples=60, deadline=None)
@given(
    codes=st.lists(st.integers(min_value=0, max_value=255), min_size=24, max_size=24),
    scale=st.sampled_from([16.0, 32.0, 64.0]),
)
def test_pbt_offset_table_codec_exact_byte_round_trip(codes: list[int], scale: float) -> None:
    blob = bytes(codes)
    decoded = sr.decode_offset_table(blob, 4, 3, scale)
    assert decoded.shape == (3, 4, 2)
    reencoded = sr.encode_offset_table(decoded, 4, 3, scale)
    assert reencoded == blob
