"""Characterization and modular-surface tests for scripts/plan_landing_moment.py.

Pins the public attribute inventory, exact callable signatures, Horizons URL builders,
SHA-256 cache validation and mocked HTTP client behavior, CSV and header parsers,
galactic anti-centre angular separation geometry, full synthetic-cache moment assembly,
and CLI --offline / --verify behavior across both package import and file-spec loading.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib
import importlib.util
import inspect
import io
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from types import ModuleType

from hypothesis import given, settings
from hypothesis import strategies as st
import numpy as np
import pytest

from services.science.sky_registration import ICRS_TO_GALACTIC

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "plan_landing_moment.py"

HORIZONS_PARAMETERS = {
    "format",
    "COMMAND",
    "OBJ_DATA",
    "MAKE_EPHEM",
    "EPHEM_TYPE",
    "CENTER",
    "START_TIME",
    "STOP_TIME",
    "STEP_SIZE",
    "TLIST",
    "TIME_TYPE",
    "REF_PLANE",
    "REF_SYSTEM",
    "VEC_TABLE",
    "VEC_CORR",
    "OUT_UNITS",
    "QUANTITIES",
    "ANG_FORMAT",
    "CSV_FORMAT",
    "EXTRA_PREC",
}

EXPECTED_CONSTANTS = {
    "REPO_ROOT",
    "API",
    "USER_AGENT",
    "DEFAULT_MOMENT",
    "DEFAULT_CACHE",
    "OUT",
    "FORMAT",
    "OBSERVER_QUANTITIES",
    "ICRS_TO_GALACTIC",
}

EXPECTED_SIGNATURES = {
    "query_url": "(params: 'dict[str, str]') -> 'str'",
    "fetch": "(url: 'str', cache: 'Path', offline: 'bool') -> 'dict'",
    "table": "(entry: 'dict') -> 'tuple[list[str], list[list[str]]]'",
    "vectors": "(command: 'str', start: 'str', stop: 'str', step: 'str') -> 'str'",
    "observer": "(command: 'str', center: 'str', moment: 'str') -> 'str'",
    "galactic_anticentre_separation_deg": "(position_km: 'np.ndarray') -> 'float'",
    "observer_checks": "(entry: 'dict') -> 'dict'",
    "header_value": "(entry: 'dict', label: 'str') -> 'str'",
    "ephemeris_source": "(entry: 'dict') -> 'str'",
    "plan": "(moment: 'str', cache: 'Path', offline: 'bool') -> 'dict'",
    "main": "() -> 'None'",
}

EXPECTED_PUBLIC_SYMBOLS = EXPECTED_CONSTANTS | set(EXPECTED_SIGNATURES)

SYNTHETIC_MOMENT = "2031-08-30 03:00"
SYNTHETIC_RETRIEVED_UTC = "2031-08-30T03:05:00Z"


def _load_via_spec(module_name: str = "plan_landing_moment_spec_under_test") -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=("package_import", "spec_from_file"))
def landing_module(request: pytest.FixtureRequest) -> ModuleType:
    if request.param == "package_import":
        return importlib.import_module("scripts.plan_landing_moment")
    return _load_via_spec()


def _icrf_from_galactic_separation(sep_deg: float, radius_km: float = 384_400.0) -> np.ndarray:
    """Deterministic ICRF position vector at an exact angle from the galactic anti-centre."""
    rad = math.radians(sep_deg)
    galactic_unit = np.array([-math.cos(rad), math.sin(rad), 0.0], dtype=float)
    return radius_km * (np.asarray(ICRS_TO_GALACTIC).T @ galactic_unit)


def _write_cache_entry(cache: Path, url: str, result_text: str, retrieved_utc: str = SYNTHETIC_RETRIEVED_UTC) -> Path:
    digest = hashlib.sha256(url.encode()).hexdigest()[:24]
    path = cache / f"{digest}.json"
    cache.mkdir(parents=True, exist_ok=True)
    entry = {
        "url": url,
        "retrievedUtc": retrieved_utc,
        "response": {
            "signature": {"source": "NASA/JPL Horizons API", "version": "1.2"},
            "result": result_text,
        },
    }
    path.write_text(json.dumps(entry, indent=1), encoding="utf-8")
    return path


def _vectors_result_text(target_line: str, stamped_vectors: list[tuple[str, list[float]]]) -> str:
    body_lines = [
        f"2463110.500000000, {stamp}, {xyz[0]!r}, {xyz[1]!r}, {xyz[2]!r}, 0.1, 0.2, 0.3,"
        for stamp, xyz in stamped_vectors
    ]
    return "\n".join(
        [
            "*******************************************************************************",
            target_line,
            "Center body name: Earth (399)                     {source: DE441}",
            "*******************************************************************************",
            "JDTDB, Calendar Date (UT ), X, Y, Z, VX, VY, VZ,",
            "*******************************************************************************",
            "$$SOE",
            *body_lines,
            "$$EOE",
            "*******************************************************************************",
        ]
    )


def _observer_result_text(
    *,
    target_line: str,
    pole_equ: str,
    radii_line: str | None,
    values: dict[str, float],
) -> str:
    columns = [
        "Date__(UT)__HR:MN",
        "ObsSub-LON",
        "ObsSub-LAT",
        "SunSub-LON",
        "SunSub-LAT",
        "NP.ang",
        "delta",
        "S-T-O",
        "TDB-UT",
        "N.Pole-RA",
        "N.Pole-DC",
        "GlxLon",
        "GlxLat",
    ]
    row = ["2031-Aug-30 03:00", *[f"{values[col]}" for col in columns[1:]]]
    header = [
        "*******************************************************************************",
        target_line,
        f"Target pole/equ : {pole_equ}",
    ]
    if radii_line is not None:
        header.append(radii_line)
    header.extend(
        [
            "*******************************************************************************",
            ", ".join(columns),
            "*******************************************************************************",
            "$$SOE",
            ", ".join(row),
            "$$EOE",
            "*******************************************************************************",
        ]
    )
    return "\n".join(header)


def _populate_synthetic_moment_cache(
    mod: ModuleType,
    cache: Path,
    *,
    moment: str = SYNTHETIC_MOMENT,
    centre_stamp_override: str | None = None,
) -> dict[str, object]:
    when = dt.datetime.strptime(moment, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)
    start = (when - dt.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
    stop = (when + dt.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
    sun_stop = (when + dt.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")

    moon_rows: list[tuple[str, list[float]]] = []
    expected_seps: list[float] = []
    for offset_h in range(-24, 25):
        instant = when + dt.timedelta(hours=offset_h)
        stamp = f"A.D. {instant.strftime('%Y-%b-%d %H:%M:%S.0000')}"
        if offset_h == 0 and centre_stamp_override is not None:
            stamp = centre_stamp_override
        angle_deg = math.hypot(0.25, 0.5 * offset_h)
        vec = _icrf_from_galactic_separation(angle_deg, radius_km=384_400.0 + 10.0 * offset_h)
        xyz = [float(vec[0]), float(vec[1]), float(vec[2])]
        moon_rows.append((stamp, xyz))
        expected_seps.append(round(mod.galactic_anticentre_separation_deg(np.array(xyz)), 4))

    sun_row_0 = [148_000_000.0, 21_000_000.0, 9_000_000.0]
    sun_row_1 = [147_998_500.0, 21_001_250.0, 9_000_500.0]

    moon_obs_vals = {
        "ObsSub-LON": 1.125,
        "ObsSub-LAT": -1.25,
        "SunSub-LON": 2.375,
        "SunSub-LAT": -0.625,
        "NP.ang": 358.5,
        "delta": 0.002569555,
        "S-T-O": 1.875,
        "TDB-UT": 69.1855,
        "N.Pole-RA": 270.25,
        "N.Pole-DC": 2.625,
        "GlxLon": 180.125,
        "GlxLat": -0.25,
    }
    earth_obs_vals = {
        "ObsSub-LON": 3.125,
        "ObsSub-LAT": 0.875,
        "SunSub-LON": -2.125,
        "SunSub-LAT": -1.75,
        "NP.ang": 357.25,
        "delta": 0.002569125,
        "S-T-O": 178.125,
        "TDB-UT": 69.1855,
        "N.Pole-RA": 0.75,
        "N.Pole-DC": 3.375,
        "GlxLon": 0.125,
        "GlxLat": 0.25,
    }

    _write_cache_entry(
        cache,
        mod.vectors("301", start, stop, "1 h"),
        _vectors_result_text("Target body name: Moon (301)                      {source: DE441}", moon_rows),
        retrieved_utc="2031-08-30T03:05:01Z",
    )
    _write_cache_entry(
        cache,
        mod.vectors("10", moment, sun_stop, "1 m"),
        _vectors_result_text(
            "Target body name: Sun (10)                        {source: DE441}",
            [
                (f"A.D. {when.strftime('%Y-%b-%d %H:%M:%S.0000')}", sun_row_0),
                (f"A.D. {(when + dt.timedelta(minutes=1)).strftime('%Y-%b-%d %H:%M:%S.0000')}", sun_row_1),
            ],
        ),
        retrieved_utc="2031-08-30T03:05:02Z",
    )
    _write_cache_entry(
        cache,
        mod.observer("301", "500@399", moment),
        _observer_result_text(
            target_line="Target body name: Moon (301)                      {source: DE441}",
            pole_equ="MOON_ME {IAU_MOON}",
            radii_line=None,
            values=moon_obs_vals,
        ),
        retrieved_utc="2031-08-30T03:05:03Z",
    )
    _write_cache_entry(
        cache,
        mod.observer("399", "500@301", moment),
        _observer_result_text(
            target_line="Target body name: Earth (399)                     {source: DE441}",
            pole_equ="ITRF93 (high precision) {East-longitude positive}",
            radii_line="Target radii    : 6378.137, 6378.137, 6356.752 km {Equator, meridian, pole}",
            values=earth_obs_vals,
        ),
        retrieved_utc="2031-08-30T03:05:04Z",
    )
    return {
        "centre_moon_km": moon_rows[24][1],
        "sun_km": sun_row_0,
        "separations": expected_seps,
        "moon_obs_vals": moon_obs_vals,
        "earth_obs_vals": earth_obs_vals,
    }


def test_public_symbols_constants_and_signatures_across_both_loaders(landing_module: ModuleType) -> None:
    for symbol in EXPECTED_PUBLIC_SYMBOLS:
        assert hasattr(landing_module, symbol), f"missing public attribute {symbol}"

    if hasattr(landing_module, "__all__"):
        assert set(landing_module.__all__) == EXPECTED_PUBLIC_SYMBOLS

    assert landing_module.REPO_ROOT == ROOT
    assert landing_module.API == "https://ssd.jpl.nasa.gov/api/horizons.api"
    assert landing_module.USER_AGENT == "Albedian/0.1 (research prototype)"
    assert landing_module.DEFAULT_MOMENT == "2025-03-07 11:00"
    assert landing_module.DEFAULT_CACHE == ROOT / ".cache" / "horizons"
    assert landing_module.OUT == ROOT / "apps" / "web" / "src" / "spatial" / "webgpu" / "cislunar" / "landingMoment.json"
    assert landing_module.FORMAT == "albedian.landing-moment"
    assert landing_module.OBSERVER_QUANTITIES == "14,15,17,20,24,30,32,33"
    assert np.array_equal(landing_module.ICRS_TO_GALACTIC, ICRS_TO_GALACTIC)

    for fn_name, expected_sig in EXPECTED_SIGNATURES.items():
        fn = getattr(landing_module, fn_name)
        assert callable(fn)
        assert str(inspect.signature(fn)) == expected_sig


def test_query_url_vectors_and_observer_parameter_contracts(landing_module: ModuleType) -> None:
    raw_url = landing_module.query_url({"COMMAND": "'301'", "CENTER": "'500@399'"})
    assert raw_url.startswith("https://ssd.jpl.nasa.gov/api/horizons.api?format=json&")
    assert urllib.parse.parse_qs(urllib.parse.urlparse(raw_url).query) == {
        "format": ["json"],
        "COMMAND": ["'301'"],
        "CENTER": ["'500@399'"],
    }

    vec_url = landing_module.vectors("301", "2031-08-29 03:00", "2031-08-31 03:00", "1 h")
    vec_parsed = urllib.parse.urlparse(vec_url)
    assert (vec_parsed.scheme, vec_parsed.netloc, vec_parsed.path) == (
        "https",
        "ssd.jpl.nasa.gov",
        "/api/horizons.api",
    )
    vec_qs = urllib.parse.parse_qs(vec_parsed.query, keep_blank_values=True)
    assert vec_qs == {
        "format": ["json"],
        "COMMAND": ["'301'"],
        "OBJ_DATA": ["'NO'"],
        "MAKE_EPHEM": ["'YES'"],
        "EPHEM_TYPE": ["'VECTORS'"],
        "CENTER": ["'500@399'"],
        "START_TIME": ["'2031-08-29 03:00'"],
        "STOP_TIME": ["'2031-08-31 03:00'"],
        "STEP_SIZE": ["'1 h'"],
        "TIME_TYPE": ["'UT'"],
        "REF_PLANE": ["'FRAME'"],
        "REF_SYSTEM": ["'ICRF'"],
        "VEC_TABLE": ["'2'"],
        "VEC_CORR": ["'NONE'"],
        "OUT_UNITS": ["'KM-S'"],
        "CSV_FORMAT": ["'YES'"],
    }

    obs_url = landing_module.observer("399", "500@301", "2031-08-30 03:00")
    obs_parsed = urllib.parse.urlparse(obs_url)
    assert (obs_parsed.scheme, obs_parsed.netloc, obs_parsed.path) == (
        "https",
        "ssd.jpl.nasa.gov",
        "/api/horizons.api",
    )
    obs_qs = urllib.parse.parse_qs(obs_parsed.query, keep_blank_values=True)
    assert obs_qs == {
        "format": ["json"],
        "COMMAND": ["'399'"],
        "OBJ_DATA": ["'NO'"],
        "MAKE_EPHEM": ["'YES'"],
        "EPHEM_TYPE": ["'OBSERVER'"],
        "CENTER": ["'500@301'"],
        "TLIST": ["'2031-08-30 03:00'"],
        "TIME_TYPE": ["'UT'"],
        "QUANTITIES": ["'14,15,17,20,24,30,32,33'"],
        "ANG_FORMAT": ["'DEG'"],
        "CSV_FORMAT": ["'YES'"],
        "EXTRA_PREC": ["'YES'"],
    }

    assert set(vec_qs) | set(obs_qs) == HORIZONS_PARAMETERS
    for qs in (vec_qs, obs_qs):
        for key, vals in qs.items():
            assert len(vals) == 1
            if key != "format":
                assert vals[0].startswith("'") and vals[0].endswith("'")


def test_fetch_cache_hit_validation_offline_guard_and_mocked_urlopen(
    landing_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = landing_module.observer("301", "500@399", SYNTHETIC_MOMENT)
    expected_name = f"{hashlib.sha256(url.encode()).hexdigest()[:24]}.json"
    assert len(expected_name) == 24 + len(".json")

    sleep_calls: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda sec: sleep_calls.append(sec))

    def _forbid_network(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("urlopen must not be called when cached or offline")

    monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)

    uncached_dir = tmp_path / "uncached"
    with pytest.raises(SystemExit, match="not cached, and --offline or --verify makes no request"):
        landing_module.fetch(url, uncached_dir, offline=True)
    assert not uncached_dir.exists()

    cache_dir = tmp_path / "cache"
    written_path = _write_cache_entry(cache_dir, url, "cached-body")
    assert written_path.name == expected_name
    hit = landing_module.fetch(url, cache_dir, offline=True)
    assert hit["url"] == url
    assert hit["retrievedUtc"] == SYNTHETIC_RETRIEVED_UTC
    assert hit["response"]["result"] == "cached-body"
    assert sleep_calls == []

    # Cache entry with mismatched url must not be accepted as a hit
    written_path.write_text(
        json.dumps({"url": url + "&EXTRA=1", "retrievedUtc": SYNTHETIC_RETRIEVED_UTC, "response": {"result": "stale"}}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="not cached"):
        landing_module.fetch(url, cache_dir, offline=True)

    # Cache entry missing retrievedUtc must not be accepted as a hit
    written_path.write_text(
        json.dumps({"url": url, "response": {"result": "missing-timestamp"}}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="not cached"):
        landing_module.fetch(url, cache_dir, offline=True)

    # Mocked online fetch writes indent=1 JSON, attaches User-Agent, and spaces requests by 1.0 s
    captured_requests: list[tuple[urllib.request.Request, float | None]] = []

    class _FakeResponse(io.BytesIO):
        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *_exc: object) -> None:
            self.close()

    def _mock_urlopen(req: urllib.request.Request, timeout: float | None = None) -> _FakeResponse:
        captured_requests.append((req, timeout))
        payload = {"signature": {"source": "NASA/JPL Horizons API", "version": "1.2"}, "result": "$$SOE\nrow\n$$EOE"}
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", _mock_urlopen)
    fresh_cache = tmp_path / "fresh"
    fetched = landing_module.fetch(url, fresh_cache, offline=False)

    assert len(captured_requests) == 1
    req, timeout = captured_requests[0]
    assert req.full_url == url
    assert req.get_header("User-agent") == "Albedian/0.1 (research prototype)"
    assert timeout == 60
    assert sleep_calls == [1.0]
    assert fetched["url"] == url
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", fetched["retrievedUtc"])
    saved_file = fresh_cache / expected_name
    assert saved_file.is_file()
    assert saved_file.read_text(encoding="utf-8") == json.dumps(fetched, indent=1)

    # Online response without 'result' raises SystemExit without writing cache or sleeping again
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda req, timeout=None: _FakeResponse(b'{"error": "unknown target"}'),
    )
    bad_cache = tmp_path / "bad_response"
    with pytest.raises(SystemExit, match="Horizons returned no result"):
        landing_module.fetch(url, bad_cache, offline=False)
    assert not (bad_cache / expected_name).exists()
    assert sleep_calls == [1.0]


def test_table_observer_checks_header_value_and_ephemeris_source_parsers(landing_module: ModuleType) -> None:
    missing_soe = {"url": "https://ssd.jpl.nasa.gov/api/horizons.api?format=json", "response": {"result": "no markers"}}
    with pytest.raises(SystemExit, match="Horizons table missing"):
        landing_module.table(missing_soe)

    sample_entry = {
        "url": "https://ssd.jpl.nasa.gov/api/horizons.api?format=json",
        "response": {
            "result": "\n".join(
                [
                    "Target body name: Earth (399)                     {source: DE441}",
                    "Target pole/equ : ITRF93 (high precision) {East-longitude positive}",
                    "Target radii    : 6378.137, 6378.137, 6356.752 km {Equator, meridian, pole}",
                    "",
                    "  ColA , ColB , ColC  ",
                    "*******************************************************************************",
                    "",
                    "$$SOE",
                    " 10 , 20 , 30 ",
                    " 40 , 50 , 60 ",
                    "$$EOE",
                    "Post-table label: ignored",
                ]
            )
        },
    }
    names, rows = landing_module.table(sample_entry)
    assert names == ["ColA", "ColB", "ColC"]
    assert rows == [["10", "20", "30"], ["40", "50", "60"]]

    assert landing_module.header_value(sample_entry, "Target pole/equ") == "ITRF93 (high precision)"
    assert landing_module.header_value(sample_entry, "Target radii") == "6378.137, 6378.137, 6356.752 km"
    with pytest.raises(SystemExit, match="no 'Post-table label' line"):
        landing_module.header_value(sample_entry, "Post-table label")
    with pytest.raises(SystemExit, match="no 'Missing header' line"):
        landing_module.header_value(sample_entry, "Missing header")

    assert landing_module.ephemeris_source(sample_entry) == "DE441"
    no_source_entry = {
        "url": sample_entry["url"],
        "response": {"result": "Target body name: Earth (399)\nColA\n***\n$$SOE\n1\n$$EOE"},
    }
    with pytest.raises(SystemExit, match="no ephemeris source"):
        landing_module.ephemeris_source(no_source_entry)

    # observer_checks requires exactly 1 row and maps all 12 distinct columns
    with pytest.raises(SystemExit, match="expected one row"):
        landing_module.observer_checks(sample_entry)

    distinct_vals = {
        "ObsSub-LON": 0.125,
        "ObsSub-LAT": -0.25,
        "SunSub-LON": 0.375,
        "SunSub-LAT": -0.5,
        "NP.ang": 358.75,
        "delta": 0.0025,
        "S-T-O": 1.625,
        "TDB-UT": 69.185,
        "N.Pole-RA": 270.5,
        "N.Pole-DC": 2.75,
        "GlxLon": 180.375,
        "GlxLat": -0.875,
    }
    obs_entry = {
        "url": sample_entry["url"],
        "response": {
            "result": _observer_result_text(
                target_line="Target body name: Moon (301) {source: DE441}",
                pole_equ="MOON_ME",
                radii_line=None,
                values=distinct_vals,
            )
        },
    }
    assert landing_module.observer_checks(obs_entry) == {
        "subObserverLonDeg": 0.125,
        "subObserverLatDeg": -0.25,
        "subSolarLonDeg": 0.375,
        "subSolarLatDeg": -0.5,
        "northPoleAngleDeg": 358.75,
        "northPoleRaDeg": 270.5,
        "northPoleDecDeg": 2.75,
        "rangeAu": 0.0025,
        "phaseAngleDeg": 1.625,
        "tdbMinusUtSeconds": 69.185,
        "galacticLonDeg": 180.375,
        "galacticLatDeg": -0.875,
    }


def test_galactic_anticentre_separation_cardinal_directions(landing_module: ModuleType) -> None:
    rot = np.asarray(ICRS_TO_GALACTIC)
    anticentre_icrf = rot.T @ np.array([-1.0, 0.0, 0.0])
    centre_icrf = rot.T @ np.array([1.0, 0.0, 0.0])
    north_pole_icrf = rot.T @ np.array([0.0, 0.0, 1.0])
    south_pole_icrf = rot.T @ np.array([0.0, 0.0, -1.0])
    plane_90_icrf = rot.T @ np.array([0.0, 1.0, 0.0])

    assert landing_module.galactic_anticentre_separation_deg(384_400.0 * anticentre_icrf) == pytest.approx(
        0.0, abs=1e-12
    )
    assert landing_module.galactic_anticentre_separation_deg(384_400.0 * centre_icrf) == pytest.approx(
        180.0, abs=1e-12
    )
    assert landing_module.galactic_anticentre_separation_deg(384_400.0 * north_pole_icrf) == pytest.approx(
        90.0, abs=1e-12
    )
    assert landing_module.galactic_anticentre_separation_deg(384_400.0 * south_pole_icrf) == pytest.approx(
        90.0, abs=1e-12
    )
    assert landing_module.galactic_anticentre_separation_deg(384_400.0 * plane_90_icrf) == pytest.approx(
        90.0, abs=1e-12
    )
    assert landing_module.galactic_anticentre_separation_deg(_icrf_from_galactic_separation(37.5)) == pytest.approx(
        37.5, abs=1e-11
    )


@settings(deadline=None, max_examples=100)
@given(
    vx=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    vy=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    vz=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    scale=st.floats(min_value=1e-2, max_value=1e4, allow_nan=False, allow_infinity=False),
)
def test_pbt_galactic_anticentre_separation_scale_invariance_and_antipodal_sum(
    vx: float, vy: float, vz: float, scale: float
) -> None:
    mod = importlib.import_module("scripts.plan_landing_moment")
    vec = np.array([vx, vy, vz], dtype=float)
    if float(np.linalg.norm(vec)) < 1e-6:
        vec = np.array([384_400.0, 0.0, 0.0], dtype=float)

    sep = mod.galactic_anticentre_separation_deg(vec)
    scaled_sep = mod.galactic_anticentre_separation_deg(scale * vec)
    opposite_sep = mod.galactic_anticentre_separation_deg(-vec)

    assert 0.0 <= sep <= 180.0
    assert scaled_sep == pytest.approx(sep, abs=1e-9)
    assert sep + opposite_sep == pytest.approx(180.0, abs=1e-9)


def test_plan_and_cli_main_end_to_end_with_synthetic_cache(
    landing_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("offline plan/main must never call urlopen")),
    )

    cache_dir = tmp_path / "horizons_cache"
    fixture = _populate_synthetic_moment_cache(landing_module, cache_dir, moment=SYNTHETIC_MOMENT)

    record = landing_module.plan(SYNTHETIC_MOMENT, cache_dir, offline=True)
    assert record["format"] == "albedian.landing-moment"
    assert record["version"] == 1
    assert record["utc"] == "2031-08-30T03:00:00Z"
    assert record["tdbMinusUtcSeconds"] == 69.1855
    assert record["frame"] == "ICRF, geocentric, geometric (no light time or aberration), km"
    assert record["moonKm"] == fixture["centre_moon_km"]
    assert record["sunKm"] == fixture["sun_km"]
    assert record["moonFromGalacticAnticentreDeg"] == 0.25
    assert record["window"] == {
        "fromUtc": "2031-08-29 03:00",
        "toUtc": "2031-08-31 03:00",
        "stepHours": 1,
        "moonFromGalacticAnticentreDeg": fixture["separations"],
    }
    # Verify 4-decimal rounding is preserved on non-integer-decimal window entries
    assert record["window"]["moonFromGalacticAnticentreDeg"][23] == round(math.hypot(0.25, -0.5), 4)
    assert record["checks"]["moonSeenFromEarthCentre"]["bodyFrame"] == "MOON_ME"
    assert record["checks"]["moonSeenFromEarthCentre"]["latitudes"] == "planetocentric, sphere"
    assert record["checks"]["earthSeenFromMoonCentre"]["bodyFrame"] == "ITRF93 (high precision)"
    assert record["checks"]["earthSeenFromMoonCentre"]["latitudes"] == "geodetic on the target ellipsoid"
    assert record["checks"]["earthSeenFromMoonCentre"]["radiiKm"] == [6378.137, 6378.137, 6356.752]
    assert record["horizons"]["service"] == "NASA/JPL Horizons API"
    assert record["horizons"]["version"] == "1.2"
    assert record["horizons"]["ephemeris"] == "DE441"
    assert record["horizons"]["userAgent"] == "Albedian/0.1 (research prototype)"
    assert [q["what"] for q in record["horizons"]["queries"]] == [
        "Moon vectors, the moment +-24 h",
        "Sun vector at the moment",
        "Moon seen from the Earth's centre",
        "Earth seen from the Moon's centre",
    ]
    assert [q["retrievedUtc"] for q in record["horizons"]["queries"]] == [
        "2031-08-30T03:05:01Z",
        "2031-08-30T03:05:02Z",
        "2031-08-30T03:05:03Z",
        "2031-08-30T03:05:04Z",
    ]

    # Centre-row timestamp mismatch in Moon window raises SystemExit
    bad_window_cache = tmp_path / "bad_window_cache"
    _populate_synthetic_moment_cache(
        landing_module,
        bad_window_cache,
        moment=SYNTHETIC_MOMENT,
        centre_stamp_override="A.D. 2031-Aug-30 04:00:00.0000",
    )
    with pytest.raises(SystemExit, match="window row 24 is"):
        landing_module.plan(SYNTHETIC_MOMENT, bad_window_cache, offline=True)

    # CLI main(): --offline writes JSON to monkeypatched OUT under monkeypatched REPO_ROOT
    out_file = tmp_path / "apps" / "web" / "src" / "spatial" / "webgpu" / "cislunar" / "landingMoment.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(landing_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(landing_module, "OUT", out_file)

    monkeypatch.setattr(
        sys,
        "argv",
        ["plan_landing_moment.py", "--moment", SYNTHETIC_MOMENT, "--cache", str(cache_dir), "--offline"],
    )
    landing_module.main()
    wrote_out = capsys.readouterr().out
    expected_json = json.dumps(record, indent=2) + "\n"
    assert out_file.read_text(encoding="utf-8") == expected_json
    assert "wrote apps/web/src/spatial/webgpu/cislunar/landingMoment.json: 2031-08-30T03:00:00Z" in wrote_out

    # CLI main(): --verify passes when OUT matches rebuild from cache (and implies offline without --offline)
    monkeypatch.setattr(
        sys,
        "argv",
        ["plan_landing_moment.py", "--moment", SYNTHETIC_MOMENT, "--cache", str(cache_dir), "--verify"],
    )
    landing_module.main()
    verify_out = capsys.readouterr().out
    assert "apps/web/src/spatial/webgpu/cislunar/landingMoment.json matches the rebuild from the cache" in verify_out

    # CLI main(): --verify raises SystemExit when OUT differs from rebuild
    out_file.write_text(expected_json.replace('"version": 1', '"version": 2'), encoding="utf-8")
    with pytest.raises(SystemExit, match="differs from the rebuild from the cache"):
        landing_module.main()

    # CLI main(): --verify alone refuses network requests when cache is empty
    monkeypatch.setattr(
        sys,
        "argv",
        ["plan_landing_moment.py", "--moment", SYNTHETIC_MOMENT, "--cache", str(tmp_path / "empty_cache"), "--verify"],
    )
    with pytest.raises(SystemExit, match="not cached"):
        landing_module.main()
