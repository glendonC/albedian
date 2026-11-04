"""The landing moment (scripts/plan_landing_moment.py): one real hour, from JPL Horizons, when the
Moon seen from the Earth's centre lies near the galactic anti-centre, so that from beyond the Moon
the Earth stands in front of the centre of the Milky Way.

The renderer's rotation models are checked against the same file in
apps/web/src/spatial/__tests__/bodyOrientation.test.ts.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest

from services.science.sky_registration import ICRS_TO_GALACTIC

ROOT = Path(__file__).resolve().parents[2]
MOMENT_FILE = ROOT / "apps" / "web" / "src" / "spatial" / "webgpu" / "cislunar" / "landingMoment.json"
AU_KM = 149_597_870.7

# Every parameter the script sends to Horizons; nothing else, and nothing about the owner
HORIZONS_PARAMETERS = {
    "format", "COMMAND", "OBJ_DATA", "MAKE_EPHEM", "EPHEM_TYPE", "CENTER", "START_TIME", "STOP_TIME",
    "STEP_SIZE", "TLIST", "TIME_TYPE", "REF_PLANE", "REF_SYSTEM", "VEC_TABLE", "VEC_CORR", "OUT_UNITS",
    "QUANTITIES", "ANG_FORMAT", "CSV_FORMAT", "EXTRA_PREC",
}


def _load_script():
    spec = importlib.util.spec_from_file_location("plan_landing_moment", ROOT / "scripts" / "plan_landing_moment.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _moment() -> dict:
    return json.loads(MOMENT_FILE.read_text())


def _anticentre_separation_deg(position_km) -> float:
    unit = np.asarray(position_km, dtype=float)
    unit /= np.linalg.norm(unit)
    return math.degrees(math.acos(-(np.asarray(ICRS_TO_GALACTIC) @ unit)[0]))


def test_the_moon_lies_near_the_galactic_anticentre_at_the_moment():
    moment = _moment()
    assert moment["format"] == "albedian.landing-moment"
    assert moment["utc"] == "2025-03-07T11:00:00Z"
    separation = _anticentre_separation_deg(moment["moonKm"])
    assert separation < 1.0
    assert separation == pytest.approx(moment["moonFromGalacticAnticentreDeg"], abs=1e-4)
    # Horizons' own galactic coordinates of the Moon (apparent, from the Earth's centre) agree
    seen = moment["checks"]["moonSeenFromEarthCentre"]
    assert abs((seen["galacticLonDeg"] - 180.0 + 180.0) % 360.0 - 180.0) < 1.0
    assert abs(seen["galacticLatDeg"]) < 1.0


def test_the_moment_is_within_an_hour_of_the_closest_pass_in_its_window():
    moment = _moment()
    separations = moment["window"]["moonFromGalacticAnticentreDeg"]
    assert len(separations) == 49
    closest = separations.index(min(separations))
    assert abs(closest - len(separations) // 2) <= 1
    assert separations[len(separations) // 2] == moment["moonFromGalacticAnticentreDeg"]


def test_distances_time_scale_and_lighting_are_physical():
    moment = _moment()
    moon_km = np.linalg.norm(moment["moonKm"])
    sun_km = np.linalg.norm(moment["sunKm"])
    assert 356_000 < moon_km < 407_000  # perigee to apogee
    assert 1.47e8 < sun_km < 1.53e8  # perihelion to aphelion
    # Horizons' range (apparent) and the geometric vector agree to well under the Moon's radius
    seen = moment["checks"]["moonSeenFromEarthCentre"]
    assert abs(seen["rangeAu"] * AU_KM - moon_km) < 100
    # TT - UTC is 69.184 s since 2017; TDB - TT stays within 2 ms
    assert moment["tdbMinusUtcSeconds"] == pytest.approx(69.184, abs=0.002)
    # Both bodies lit from one Sun: their phase angles from Horizons sum to 180 deg within the
    # Sun's parallax between the Earth and the Moon (under 0.2 deg)
    earth_seen = moment["checks"]["earthSeenFromMoonCentre"]
    assert seen["phaseAngleDeg"] + earth_seen["phaseAngleDeg"] == pytest.approx(180.0, abs=0.2)


def test_every_query_is_the_horizons_api_with_the_research_user_agent_and_nothing_else():
    moment = _moment()
    assert moment["horizons"]["service"] == "NASA/JPL Horizons API"
    assert moment["horizons"]["userAgent"] == "Albedian/0.1 (research prototype)"
    queries = moment["horizons"]["queries"]
    assert len(queries) == 4
    for query in queries:
        url = urlparse(query["url"])
        assert (url.scheme, url.netloc, url.path) == ("https", "ssd.jpl.nasa.gov", "/api/horizons.api")
        assert set(parse_qs(url.query)) <= HORIZONS_PARAMETERS
        assert query["retrievedUtc"].endswith("Z")


def test_offline_mode_makes_no_request(tmp_path):
    script = _load_script()
    with pytest.raises(SystemExit, match="not cached"):
        script.fetch(script.observer("301", "500@399", "2025-03-07 11:00"), tmp_path, offline=True)
    assert list(tmp_path.iterdir()) == []
