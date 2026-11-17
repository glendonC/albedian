"""Characterization and property-based surface tests for `services.science.deep_sky_findings`.

Pins the public module path, constants, `DeepSkyPrediction` frozen dataclass, all 12
callable signatures, OpenNGC caching and cross-reference rules, zero-magnitude and
zero-RA `is not None` handling, scalar WCS projection fallback, frame pixel boundary
strictness (`0.0 <= x < width`, `0.0 <= y < height`), polar declination clamping,
keep-rule priority and tie-break ordering, and Hypothesis properties. When sibling
`deep_sky_*.py` modules are present, also verifies pure facade re-exports, `__all__`
completeness, zero stdlib/third-party namespace leakage, and an acyclic import graph.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import importlib
import inspect
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from services.science import deep_sky_findings as dsf
from services.science.catalog import get_catalog_stars
from services.science.wcs_models import (
    CelestialFindingRecord,
    ObservationSolution,
    SolutionQuality,
    SolverIdentity,
    SourceDetection,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCIENCE_DIR = REPO_ROOT / "services" / "science"
SYNTH_SHA = hashlib.sha256(b"synthetic-deep-sky-modular-surface").hexdigest()
SYNTH_UTC = "2031-08-30T03:01:00Z"

EXPECTED_CONSTANTS = {
    "OPENNGC_PATH",
    "OPENNGC_PROVIDER",
    "OPENNGC_CITATION",
    "OPENNGC_LICENCE_NOTE",
    "DUPLICATE_TYPE",
    "NONEXISTENT_TYPE",
    "POSITION_PRECISION_ARCSEC",
    "VERIFICATION_METHOD",
    "LIMITATIONS",
    "MIN_MAJOR_AXIS_PX",
}

EXPECTED_CLASSES = {
    "DeepSkyPrediction",
}

EXPECTED_FUNCTIONS = {
    "openngc_table",
    "finding_id",
    "display_name",
    "duplicated_name",
    "catalog_reference",
    "decision_magnitude",
    "keep_reason",
    "faintest_matched_magnitude",
    "predict_deep_sky_findings",
}

EXPECTED_UNDERSCORE_HELPERS = {
    "_DESIGNATION",
    "_candidate_rows",
    "_project",
}

EXPECTED_PUBLIC_SYMBOLS = EXPECTED_CONSTANTS | EXPECTED_CLASSES | EXPECTED_FUNCTIONS

_PK = inspect.Parameter.POSITIONAL_OR_KEYWORD
_EMPTY = inspect.Parameter.empty

EXPECTED_SIGNATURES: dict[str, tuple[list[tuple[str, object, object, object]], object]] = {
    "DeepSkyPrediction": (
        [
            ("findings", _PK, _EMPTY, "tuple[CelestialFindingRecord, ...]"),
            ("not_included", _PK, _EMPTY, "int"),
        ],
        None,
    ),
    "openngc_table": ([], "dict[str, Any]"),
    "finding_id": ([("name", _PK, _EMPTY, "str")], "str"),
    "display_name": (
        [("name", _PK, _EMPTY, "str"), ("common_names", _PK, _EMPTY, "list[str]")],
        "str",
    ),
    "duplicated_name": (
        [("row", _PK, _EMPTY, "list[Any]"), ("at", _PK, _EMPTY, "dict[str, int]")],
        "str | None",
    ),
    "_candidate_rows": ([], "list[list[Any]]"),
    "catalog_reference": ([("row", _PK, _EMPTY, "list[Any]")], "dict[str, Any]"),
    "_project": (
        [
            ("solution", _PK, _EMPTY, "ObservationSolution"),
            ("ra", _PK, _EMPTY, "np.ndarray"),
            ("dec", _PK, _EMPTY, "np.ndarray"),
        ],
        "tuple[np.ndarray, np.ndarray]",
    ),
    "decision_magnitude": ([("row", _PK, _EMPTY, "list[Any]")], "float | None"),
    "keep_reason": (
        [
            ("row", _PK, _EMPTY, "list[Any]"),
            ("faintest_matched_magnitude", _PK, _EMPTY, "float | None"),
            ("major_axis_px", _PK, _EMPTY, "float"),
        ],
        "str | None",
    ),
    "faintest_matched_magnitude": (
        [("solution", _PK, _EMPTY, "ObservationSolution")],
        "float | None",
    ),
    "predict_deep_sky_findings": (
        [
            ("solution", _PK, _EMPTY, "ObservationSolution"),
            ("width", _PK, _EMPTY, "int"),
            ("height", _PK, _EMPTY, "int"),
        ],
        "DeepSkyPrediction",
    ),
}

SYNTH_COLUMNS = [
    "name",
    "type",
    "raDeg",
    "decDeg",
    "constellation",
    "majorAxisArcmin",
    "vMag",
    "bMag",
    "messier",
    "ngc",
    "ic",
    "commonNames",
]


def _make_verified_solution(
    wcs: Any,
    findings: list[CelestialFindingRecord] | None = None,
    status: str = "calibrated",
    passed: bool = True,
) -> ObservationSolution:
    quality = SolutionQuality(
        detected_candidate_count=20,
        matched_inlier_count=12 if passed else 0,
        held_out_inliers=7 if passed else 0,
        median_residual_px=0.4 if passed else None,
        p95_residual_px=0.9 if passed else None,
        median_residual_arcsec=0.1 if passed else None,
        passed_independent_verification=passed,
        held_out_pair_count=7 if passed else 0,
    )
    return ObservationSolution(
        id="sol_synth",
        source_asset_id="src_synth",
        source_sha256=SYNTH_SHA,
        frame_time_sec=0.0,
        revision=1,
        status=status,
        observed_at_utc=SYNTH_UTC,
        observer_lat=15.0,
        observer_lon=-30.0,
        wcs=wcs,
        quality=quality,
        solver=SolverIdentity(name="synthetic-wcs-test", version="1.0", catalog_release="test"),
        findings=findings or [],
    )


def _matched_star_finding(fid: str, mag: float) -> CelestialFindingRecord:
    star = dataclasses.replace(get_catalog_stars()[0], mag=mag)
    det = SourceDetection(
        x_px=10.0,
        y_px=10.0,
        flux=1000.0,
        snr=12.0,
        fwhm_px=2.1,
        centroid_uncertainty_px=0.2,
    )
    return CelestialFindingRecord(
        id=f"finding-{fid}",
        observation_solution_id="sol_synth",
        evidence_class="catalog_matched",
        title=f"Star {fid}",
        subtitle="Test matched star",
        ra_deg=10.0,
        dec_deg=20.0,
        uncertainty_arcsec=1.0,
        source_detection=det,
        catalog_star=star,
        pixel_residual_px=0.5,
        angular_residual_arcsec=1.0,
        verified_by_holdout=True,
        verification_method="synthetic held-out test",
        limitations=("Synthetic test finding.",),
        source_sha256=SYNTH_SHA,
    )


# =============================================================================================
# 1. Public surface, constants, dataclasses, signatures, and modular DAG structure
# =============================================================================================


def test_module_path_and_complete_exported_symbol_set() -> None:
    module_path = Path(dsf.__file__).resolve()
    assert module_path == SCIENCE_DIR / "deep_sky_findings.py"
    assert module_path.is_file()

    assert len(EXPECTED_PUBLIC_SYMBOLS) == 20
    assert len(EXPECTED_SIGNATURES) == 12
    for symbol in EXPECTED_PUBLIC_SYMBOLS | EXPECTED_UNDERSCORE_HELPERS:
        assert hasattr(dsf, symbol), f"missing symbol on deep_sky_findings: {symbol}"


def test_public_constants_cache_identity_and_frozen_dataclass() -> None:
    assert dsf.OPENNGC_PATH == REPO_ROOT / "apps" / "web" / "public" / "catalog" / "deepsky" / "openngc.json"
    assert dsf.OPENNGC_PATH.is_file()
    assert dsf.OPENNGC_PROVIDER == "OpenNGC"
    assert dsf.OPENNGC_CITATION == "OpenNGC, Mattia Verga (github.com/mattiaverga/OpenNGC), CC BY-SA 4.0"
    assert dsf.OPENNGC_LICENCE_NOTE == "OpenNGC CC BY-SA 4.0"
    assert dsf.DUPLICATE_TYPE == "Dup"
    assert dsf.NONEXISTENT_TYPE == "NonEx"
    assert dsf.POSITION_PRECISION_ARCSEC == pytest.approx(0.15, abs=0.0)
    assert isinstance(dsf.POSITION_PRECISION_ARCSEC, float)
    assert dsf.MIN_MAJOR_AXIS_PX == pytest.approx(3.0, abs=0.0)
    assert isinstance(dsf.MIN_MAJOR_AXIS_PX, float)
    assert dsf.VERIFICATION_METHOD == (
        "OpenNGC catalog position projected through the solved WCS, which passed held-out verification; not measured "
        "in the image"
    )
    assert len(dsf.LIMITATIONS) == 4
    assert isinstance(dsf.LIMITATIONS, tuple)

    first_table = dsf.openngc_table()
    second_table = dsf.openngc_table()
    assert first_table is second_table
    assert hasattr(dsf.openngc_table, "cache_info")

    assert dataclasses.is_dataclass(dsf.DeepSkyPrediction)
    pred = dsf.DeepSkyPrediction(findings=(), not_included=2)
    assert pred.warning == "2 more catalog objects in this frame are not included"
    with pytest.raises(dataclasses.FrozenInstanceError):
        pred.not_included = 0  # type: ignore[misc]


@pytest.mark.parametrize("name", sorted(EXPECTED_SIGNATURES))
def test_all_twelve_callable_signatures_match_contract(name: str) -> None:
    obj = getattr(dsf, name)
    assert callable(obj)
    sig = inspect.signature(obj)
    expected_params, expected_return = EXPECTED_SIGNATURES[name]
    actual_params = [(p.name, p.kind, p.default, p.annotation) for p in sig.parameters.values()]
    assert actual_params == expected_params
    assert sig.return_annotation == expected_return


def test_modular_facade_reexports_and_acyclic_sibling_graph_when_split() -> None:
    sibling_files = [
        p for p in sorted(SCIENCE_DIR.glob("deep_sky_*.py")) if p.name != "deep_sky_findings.py"
    ]
    if hasattr(dsf, "__all__") or sibling_files:
        assert hasattr(dsf, "__all__")
        assert len(dsf.__all__) == len(set(dsf.__all__))
        assert set(dsf.__all__) == EXPECTED_PUBLIC_SYMBOLS
        public_attrs = {n for n in dir(dsf) if not n.startswith("_")}
        assert public_attrs == EXPECTED_PUBLIC_SYMBOLS

    if not sibling_files:
        return

    facade_tree = ast.parse((SCIENCE_DIR / "deep_sky_findings.py").read_text(encoding="utf-8"))
    for node in facade_tree.body:
        assert not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)), (
            "deep_sky_findings.py must remain a pure re-export facade when deep_sky_*.py modules exist"
        )

    sibling_stems = {p.stem for p in sibling_files}
    edges: dict[str, set[str]] = {stem: set() for stem in sibling_stems}

    for path in sibling_files:
        stem = path.stem
        mod = importlib.import_module(f"services.science.{stem}")
        assert hasattr(mod, "__all__"), f"{stem}.py must declare explicit __all__"
        assert len(mod.__all__) == len(set(mod.__all__))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module_name = node.module or ""
                assert module_name != "services.science.deep_sky_findings", (
                    f"{stem} must not import back from facade deep_sky_findings"
                )
                if node.level == 1 and module_name == "deep_sky_findings":
                    pytest.fail(f"{stem} must not import back from facade deep_sky_findings")
                target = None
                if node.level == 1 and module_name.startswith("deep_sky_"):
                    target = module_name
                elif module_name.startswith("services.science.deep_sky_"):
                    target = module_name.removeprefix("services.science.")
                if target is not None:
                    assert target in sibling_stems
                    assert target != stem
                    edges[stem].add(target)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "services.science.deep_sky_findings"
                    if alias.name.startswith("services.science.deep_sky_"):
                        target = alias.name.removeprefix("services.science.")
                        assert target in sibling_stems
                        edges[stem].add(target)

    state: dict[str, int] = {stem: 0 for stem in sibling_stems}

    def visit(node_name: str) -> None:
        state[node_name] = 1
        for dep in edges[node_name]:
            assert state[dep] != 1, f"cycle detected involving {node_name} -> {dep}"
            if state[dep] == 0:
                visit(dep)
        state[node_name] = 2

    for stem in sorted(sibling_stems):
        if state[stem] == 0:
            visit(stem)


# =============================================================================================
# 2. Designation formatting, duplicate cross-references, zero-value guards, and keep boundaries
# =============================================================================================


DISPLAY_NAME_ROWS = [
    ("common_wins_over_ngc", "NGC1976", ["Great Orion Nebula", "M42"], "Great Orion Nebula"),
    ("ngc_strip_leading_zeros", "NGC0001", [], "NGC 1"),
    ("ic_strip_leading_zeros", "IC0010", [], "IC 10"),
    ("ngc_all_zeros_keeps_last_digit", "NGC0000", [], "NGC 0"),
    ("ngc_component_suffix", "NGC5195A", [], "NGC 5195A"),
    ("ic_ned_component", "IC0080 NED01", [], "IC 80 NED01"),
    ("prefix_anchor_required", "XNGC0100", [], "XNGC0100"),
    ("non_ngc_ic_unchanged", "Mel022", [], "Mel022"),
]


@pytest.mark.parametrize(
    ("label", "raw_name", "common_names", "expected"),
    DISPLAY_NAME_ROWS,
    ids=[r[0] for r in DISPLAY_NAME_ROWS],
)
def test_display_name_and_finding_id_edge_table(
    label: str, raw_name: str, common_names: list[str], expected: str
) -> None:
    assert label
    assert dsf.display_name(raw_name, common_names) == expected
    assert dsf.finding_id("IC0080 NED01 B") == "finding-openngc-IC0080-NED01-B"


def test_synthetic_catalog_zero_values_duplicates_and_keep_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    synth_table = {
        "release": "20260501-synth",
        "columns": SYNTH_COLUMNS,
        "types": {"G": "Galaxy", "Neb": "Nebula", "Dup": "Duplicate", "NonEx": "Nonexistent"},
        "rows": [
            # raDeg == 0.0 and vMag == 0.0 must be treated as valid floats, never falsy missing values
            ["NGC0001", "G", 0.0, 0.0, "Psc", 0.5, 0.0, 1.5, None, None, None, None],
            # vMag is None, bMag == 0.0: decision_magnitude is 0.0, apparentMagnitude omitted
            ["IC0002", "Neb", 1.0, 0.0, "Cet", None, None, 0.0, None, None, None, None],
            # Both commonNames and messier set: "name" wins over "messier", "magnitude", and "size"
            ["NGC0003", "G", 2.0, 0.0, "And", 20.0, 1.0, 1.2, 31, None, None, ["Named Galaxy"]],
            # Messier set without commonNames: "messier" wins over "magnitude" and "size"
            ["NGC0004", "G", 3.0, 0.0, "Tri", 20.0, 1.0, None, 33, None, None, []],
            # Duplicate and NonEx rows plus missing-RA row excluded from _candidate_rows
            ["IC0099", "Dup", 0.0, 0.0, "Psc", None, None, None, None, ["9999", "0001"], ["0002"], None],
            ["IC0100", "Dup", 1.0, 0.0, "Cet", None, None, None, None, ["9999"], ["0002"], None],
            ["IC0101", "Dup", 1.0, 0.0, "Cet", None, None, None, None, None, None, None],
            ["NGC0102", "NonEx", 5.0, 0.0, "Peg", None, None, None, None, None, None, None],
            ["NGC0103", "G", None, 0.0, "Peg", None, None, None, None, None, None, None],
        ],
    }
    for mod_name in ("services.science.deep_sky_findings", "services.science.deep_sky_catalog", "services.science.deep_sky_selection"):
        try:
            mod = importlib.import_module(mod_name)
        except ModuleNotFoundError:
            continue
        if hasattr(mod, "openngc_table"):
            monkeypatch.setattr(mod, "openngc_table", lambda: synth_table)

    at = {col: idx for idx, col in enumerate(SYNTH_COLUMNS)}
    candidates = dsf._candidate_rows()
    assert [r[0] for r in candidates] == ["NGC0001", "IC0002", "NGC0003", "NGC0004"]

    # NGC cross-reference checked before IC; skips missing NGC9999 and finds NGC0001
    assert dsf.duplicated_name(synth_table["rows"][4], at) == "NGC0001"
    assert dsf.duplicated_name(synth_table["rows"][5], at) == "IC0002"
    assert dsf.duplicated_name(synth_table["rows"][6], at) is None

    # Zero vMag preserved in CatalogReference and decision_magnitude
    ref_zero_v = dsf.catalog_reference(candidates[0])
    assert ref_zero_v["apparentMagnitude"] == pytest.approx(0.0, abs=0.0)
    assert ref_zero_v["commonName"] == "NGC 1"
    assert ref_zero_v["releaseVersion"] == "20260501-synth"
    assert dsf.decision_magnitude(candidates[0]) == pytest.approx(0.0, abs=0.0)

    # Zero bMag used for decision_magnitude when vMag is None, but never exposed as apparentMagnitude
    ref_zero_b = dsf.catalog_reference(candidates[1])
    assert "apparentMagnitude" not in ref_zero_b
    assert dsf.decision_magnitude(candidates[1]) == pytest.approx(0.0, abs=0.0)

    # Priority order and zero/infinite/threshold boundaries on keep_reason
    assert dsf.keep_reason(candidates[2], 5.0, 10.0) == "name"
    assert dsf.keep_reason(candidates[3], 5.0, 10.0) == "messier"
    assert dsf.keep_reason(candidates[0], 0.0, math.nan) == "magnitude"
    assert dsf.keep_reason(candidates[0], -0.001, 3.0) == "size"
    assert dsf.keep_reason(candidates[0], -0.001, 2.9999) is None
    assert dsf.keep_reason(candidates[0], -0.001, math.inf) is None
    assert dsf.keep_reason(candidates[0], -0.001, -math.inf) is None
    assert dsf.keep_reason(candidates[0], -0.001, math.nan) is None


# =============================================================================================
# 3. Projection fallback, exact pixel frame boundaries, polar clamping, and sort tie-breaking
# =============================================================================================


class _ScalarOnlyLinearWCS:
    """Test WCS without `world_to_pixel_batch` to exercise the scalar `world_to_pixel` path."""

    scale_deg_per_px = 0.01  # 1 deg = 100 px; 1 arcmin = 5/3 px

    def world_to_pixel(self, ra_deg: float, dec_deg: float) -> tuple[float, float]:
        if ra_deg > 300.0:
            return (math.nan, math.nan)
        return (ra_deg * 100.0, (dec_deg + 1.0) * 100.0)


def test_scalar_wcs_fallback_frame_edge_strictness_faintest_filter_and_sort_ties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    synth_table = {
        "release": "20260501-edges",
        "columns": SYNTH_COLUMNS,
        "types": {"G": "Galaxy"},
        "rows": [
            # Inside left/top inclusive boundary: x=0.0, y=0.0 (ra=0.0, dec=-1.0); mag 5.0 (kept by mag==5.0)
            ["NGC0020", "G", 0.0, -1.0, "Psc", None, 5.0, None, None, None, None, []],
            # Same magnitude 5.0 as NGC0020 -> tie broken alphabetically ("NGC0010" before "NGC0020")
            ["NGC0010", "G", 0.5, -0.5, "Psc", None, 5.0, None, None, None, None, []],
            # Brighter negative magnitude -0.5 -> comes first
            ["NGC0030", "G", 0.2, -0.5, "Psc", None, -0.5, None, None, None, None, []],
            # No magnitude, majorAxisArcmin = 1.8 arcmin -> span = 1.8 * (100/60) = 3.0 px -> kept by size, ordered last!
            ["NGC0040", "G", 0.3, -0.5, "Psc", 1.8, None, None, None, None, None, []],
            # Inside frame, no name/messier, mag None, majorAxisArcmin = 1.74 -> span = 2.9 px < 3.0 -> NOT kept (counted in not_included)
            ["NGC0050", "G", 0.4, -0.5, "Psc", 1.74, None, None, None, None, None, []],
            # Outside right exclusive boundary: x == width == 100.0 (ra=1.0, dec=-0.5) -> excluded!
            ["NGC0060", "G", 1.0, -0.5, "Psc", 10.0, 1.0, None, 1, None, None, ["On Right Edge"]],
            # Outside bottom exclusive boundary: y == height == 80.0 (ra=0.5, dec=-0.2) -> excluded!
            ["NGC0070", "G", 0.5, -0.2, "Psc", 10.0, 1.0, None, 2, None, None, ["On Bottom Edge"]],
            # Outside negative x/y boundaries -> excluded!
            ["NGC0080", "G", -0.001, -0.5, "Psc", 10.0, 1.0, None, 3, None, None, ["Left Outside"]],
            ["NGC0081", "G", 0.5, -1.001, "Psc", 10.0, 1.0, None, 4, None, None, ["Top Outside"]],
            # Non-finite projection -> excluded!
            ["NGC0090", "G", 350.0, -0.5, "Psc", 10.0, 1.0, None, 5, None, None, ["NaN Projected"]],
        ],
    }
    for mod_name in (
        "services.science.deep_sky_findings",
        "services.science.deep_sky_catalog",
        "services.science.deep_sky_selection",
        "services.science.deep_sky_projection",
    ):
        try:
            mod = importlib.import_module(mod_name)
        except ModuleNotFoundError:
            continue
        if hasattr(mod, "openngc_table"):
            monkeypatch.setattr(mod, "openngc_table", lambda: synth_table)

    # Include two catalog_matched stars (mags 3.2 and 5.0) and one predicted star with mag 9.9 (must be ignored!)
    predicted_star = CelestialFindingRecord(
        id="finding-predicted-star",
        observation_solution_id="sol_synth",
        evidence_class="predicted",
        title="Faint Predicted Star",
        subtitle="Predicted star",
        ra_deg=12.0,
        dec_deg=5.0,
        uncertainty_arcsec=12.0,
        catalog_star=dataclasses.replace(get_catalog_stars()[0], mag=9.9),
        verification_method="predicted sky",
        limitations=("Predicted.",),
        source_sha256=SYNTH_SHA,
    )
    sol = _make_verified_solution(
        wcs=_ScalarOnlyLinearWCS(),
        findings=[
            _matched_star_finding("a", 3.2),
            _matched_star_finding("b", 5.0),
            predicted_star,
        ],
    )
    assert dsf.faintest_matched_magnitude(sol) == pytest.approx(5.0, abs=1e-12)

    # Defense-in-depth gate: calibrated status without a verified WCS returns empty prediction
    calibrated_without_wcs = dataclasses.replace(sol, id="sol_no_wcs", findings=[])
    object.__setattr__(calibrated_without_wcs, "wcs", None)
    assert not calibrated_without_wcs.has_verified_wcs
    assert dsf.predict_deep_sky_findings(calibrated_without_wcs, width=100, height=80) == dsf.DeepSkyPrediction(
        findings=(), not_included=0
    )

    # Scalar _project strict length check
    with pytest.raises(ValueError):
        dsf._project(sol, np.array([0.1, 0.2]), np.array([0.0]))

    pred = dsf.predict_deep_sky_findings(sol, width=100, height=80)
    assert [f.id for f in pred.findings] == [
        "finding-openngc-NGC0030",
        "finding-openngc-NGC0010",
        "finding-openngc-NGC0020",
        "finding-openngc-NGC0040",
    ]
    assert pred.not_included == 1
    assert pred.warning == "1 more catalog object in this frame is not included"


def test_polar_declination_clamping_for_major_axis_span(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded_decs: list[float] = []

    class _RecordingPoleWCS:
        def world_to_pixel_batch(self, ra: np.ndarray, dec: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            recorded_decs.extend(float(d) for d in dec)
            return np.full_like(ra, 50.0, dtype=np.float64), (90.0 - np.asarray(dec, dtype=np.float64)) * 100.0

    synth_table = {
        "release": "20260501-pole",
        "columns": SYNTH_COLUMNS,
        "types": {"G": "Galaxy"},
        "rows": [
            ["NGC9001", "G", 10.0, 89.9, "UMi", 60.0, None, None, None, None, None, []],
            ["NGC9002", "G", 10.0, -89.9, "Oct", 60.0, None, None, None, None, None, []],
        ],
    }
    for mod_name in (
        "services.science.deep_sky_findings",
        "services.science.deep_sky_catalog",
        "services.science.deep_sky_selection",
        "services.science.deep_sky_projection",
    ):
        try:
            mod = importlib.import_module(mod_name)
        except ModuleNotFoundError:
            continue
        if hasattr(mod, "openngc_table"):
            monkeypatch.setattr(mod, "openngc_table", lambda: synth_table)

    sol = _make_verified_solution(wcs=_RecordingPoleWCS())
    pred = dsf.predict_deep_sky_findings(sol, width=100, height=20000)
    assert len(pred.findings) == 2
    # dec + 0.5 deg at +89.9 clamps to +90.0; dec - 0.5 deg at -89.9 clamps to -90.0
    assert max(recorded_decs) <= 90.0
    assert min(recorded_decs) >= -90.0
    assert any(math.isclose(d, 90.0, abs_tol=1e-12) for d in recorded_decs)
    assert any(math.isclose(d, -90.0, abs_tol=1e-12) for d in recorded_decs)


@settings(max_examples=50, deadline=None)
@given(
    prefix=st.sampled_from(["NGC", "IC"]),
    zeros=st.integers(min_value=0, max_value=4),
    number=st.integers(min_value=1, max_value=9999),
    suffix=st.sampled_from(["", "A", "B", " NED01"]),
)
@example(prefix="NGC", zeros=3, number=1, suffix="")
@example(prefix="IC", zeros=2, number=80, suffix=" NED01")
def test_pbt_display_name_and_finding_id_invariants(
    prefix: str, zeros: int, number: int, suffix: str
) -> None:
    raw = f"{prefix}{'0' * zeros}{number}{suffix}"
    shown = dsf.display_name(raw, [])
    assert shown == f"{prefix} {number}{suffix}"
    fid = dsf.finding_id(raw)
    assert fid.startswith("finding-openngc-")
    assert " " not in fid
