"""The ephemeris answers only for the moments its method and stated errors hold for: 1800 to 2050.

The planets' elements and their published errors (Standish and Williams 1992, as JPL's Solar System Dynamics group
publishes them) hold for 1800 to 2050, and the Moon's bound is measured here over the same span against the
benchmark's Meeus 47 reference. Outside it, the library refuses with EphemerisOutOfRange, the routes answer 422 with
the range, and discovery gives a clip from outside it no ephemerides and no horizon pose, saying why.

Covers:
- The library refuses a moment one second outside the range at either end and answers at both edges
- sky-context, radiometry-audit, ephemeris and solve-horizon answer 422 with the range outside it
- Discovery gives a synthetic clip dated 2051 no ephemerides, no horizon pose and an ephemerisError
- PBT: from 1800 to 2050 the Moon lies within its stated bound of the Meeus 47 reference, with TT from the Espenak and
  Meeus (2006) delta-T polynomials, whose segments meet within a tenth of a second
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import math

from fastapi.testclient import TestClient
from hypothesis import example, given, settings, strategies as st
import numpy as np
import pytest

from research.benchmarks.phone_photos import scene as sc
from services.api import app
from services.science.ephemeris import AU_KM, MOON_UNCERTAINTY_ARCSEC, compute_solar_system_ephemerides

# A synthetic place that points at nobody (the open North Atlantic, as in the other tests)
PLACE = {"lat_deg": 30.0, "lon_deg": -40.0}
BEFORE, FIRST, LAST, AFTER = "1799-12-31T23:59:59Z", "1800-01-01T00:00:00Z", "2050-12-31T23:59:59Z", "2051-01-01T00:00:00Z"
START = datetime(1800, 1, 1, tzinfo=timezone.utc)
SPAN_S = (datetime(2051, 1, 1, tzinfo=timezone.utc) - START).total_seconds() - 1.0


def delta_t_s(year: float) -> float:
    """TT minus UT in seconds: the polynomial expressions of Espenak and Meeus (2006), Five Millennium Canon of
    Solar Eclipses (NASA TP-2006-214141), for 1800 to 2050."""
    if year < 1860:
        t = year - 1800
        return (13.72 - 0.332447 * t + 0.0068612 * t**2 + 0.0041116 * t**3 - 0.00037436 * t**4
                + 0.0000121272 * t**5 - 0.0000001699 * t**6 + 0.000000000875 * t**7)
    if year < 1900:
        t = year - 1860
        return 7.62 + 0.5737 * t - 0.251754 * t**2 + 0.01680668 * t**3 - 0.0004473624 * t**4 + t**5 / 233174
    if year < 1920:
        t = year - 1900
        return -2.79 + 1.494119 * t - 0.0598939 * t**2 + 0.0061966 * t**3 - 0.000197 * t**4
    if year < 1941:
        t = year - 1920
        return 21.20 + 0.84493 * t - 0.076100 * t**2 + 0.0020936 * t**3
    if year < 1961:
        t = year - 1950
        return 29.07 + 0.407 * t - t**2 / 233 + t**3 / 2547
    if year < 1986:
        t = year - 1975
        return 45.45 + 1.067 * t - t**2 / 260 - t**3 / 718
    if year < 2005:
        t = year - 2000
        return 63.86 + 0.3345 * t - 0.060374 * t**2 + 0.0017275 * t**3 + 0.000651814 * t**4 + 0.00002373599 * t**5
    t = year - 2000
    return 62.92 + 0.32217 * t + 0.005589 * t**2


def test_the_library_answers_inside_the_range_and_refuses_outside_it() -> None:
    for inside in (FIRST, LAST):
        assert len(compute_solar_system_ephemerides(obs_utc_iso=inside, **PLACE)) == 6
    for outside in (BEFORE, AFTER):
        # EphemerisOutOfRange is a ValueError, so callers that already catch ValueError keep working
        with pytest.raises(ValueError, match="1800-01-01 to 2050-12-31") as refused:
            compute_solar_system_ephemerides(obs_utc_iso=outside, **PLACE)
        assert refused.type.__name__ == "EphemerisOutOfRange"


def test_the_routes_answer_422_with_the_range_outside_it() -> None:
    client = TestClient(app)
    frame = np.zeros((360, 640))
    digest = hashlib.sha256(frame.tobytes()).hexdigest()
    for moment, expected in ((LAST, 200), (AFTER, 422), (BEFORE, 422)):
        place = {"latDeg": 30.0, "lonDeg": -40.0}
        responses = [
            client.post("/api/science/sky-context", json={"observedAtUTC": moment, **place}),
            client.post(
                "/api/science/radiometry-audit",
                json={"observedAtUTC": moment, **place, "targetSepDeg": 40.0, "targetAltDeg": 50.0},
            ),
            client.post("/api/science/ephemeris", json={"observedAtUTC": moment, **place}),
            client.post(
                "/api/science/solve-horizon",
                json={"observedAtUTC": moment, **place, "image_luma": frame.tolist(), "sourceAssetId": f"src_{digest[:16]}",
                      "sourceSha256": digest},
            ),
        ]
        assert [r.status_code for r in responses] == [expected] * 4, moment
        if expected == 422:
            assert all("1800-01-01 to 2050-12-31" in r.json()["detail"] for r in responses)


def test_discovery_gives_a_clip_from_outside_the_range_no_ephemerides(tmp_path, monkeypatch) -> None:
    from PIL import Image

    from services.media import discovery

    (tmp_path / "uploads").mkdir()
    Image.fromarray(np.zeros((90, 160), dtype=np.uint8)).save(tmp_path / "uploads" / "synthetic-2051.png")
    probe = {
        "streams": [{"codec_type": "video", "width": 160, "height": 90, "codec_name": "png"}],
        "format": {"tags": {"location": "+30.0000-040.0000/", "com.apple.quicktime.creationdate": AFTER}},
    }
    monkeypatch.setattr(discovery, "_probe_media_metadata", lambda path: probe)
    item = discovery.discover_uploaded_media(tmp_path / "uploads", tmp_path / "public", tmp_path / "artifacts")[0]
    payload = item.to_contract_dict()
    assert "1800-01-01 to 2050-12-31" in payload["ephemerisError"]
    assert (payload["ephemerides"], payload["observationSolution"]) == ([], None)


@pytest.mark.parametrize("join", [1860, 1900, 1920, 1941, 1961, 1986, 2005])
def test_the_delta_t_segments_meet(join: int) -> None:
    assert delta_t_s(join - 1e-9) == pytest.approx(delta_t_s(join), abs=0.1)


@settings(max_examples=60, deadline=None)
@given(st.floats(min_value=0.0, max_value=SPAN_S).map(lambda s: START + timedelta(seconds=s)))
@example(datetime(1800, 1, 1, tzinfo=timezone.utc))
# The largest error a six-hourly scan of five two-year windows found: 1,217.6 arcsec, inside the stated 1,300
@example(datetime(1851, 3, 14, 18, tzinfo=timezone.utc))
@example(datetime(2050, 12, 31, 23, tzinfo=timezone.utc))
def test_pbt_the_moon_holds_its_bound_from_1800_to_2050(when: datetime) -> None:
    iso = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    (moon,) = compute_solar_system_ephemerides(obs_utc_iso=iso, **PLACE, bodies=("Moon",))
    jd_utc = 2440587.5 + when.timestamp() / 86400.0
    year = when.year + (when.timetuple().tm_yday - 0.5) / 365.25
    reference = sc.moon_geocentric_icrs_km(jd_utc + delta_t_s(year) / 86400.0)
    ra, dec = math.radians(moon.ra_deg), math.radians(moon.dec_deg)
    ours = np.array([math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec)])
    cos_sep = float(ours @ (reference / np.linalg.norm(reference)))
    assert math.degrees(math.acos(min(1.0, cos_sep))) * 3600.0 <= MOON_UNCERTAINTY_ARCSEC
    assert moon.distance_lower_au * AU_KM <= float(np.linalg.norm(reference)) <= moon.distance_upper_au * AU_KM
