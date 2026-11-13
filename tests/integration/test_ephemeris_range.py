"""The ephemeris answers only for the moments its method and stated errors hold for: 1800 to 2050.

The planets' documented errors (ERFA eraPlan94's notes) cover 1800 to 2050, and the ephemeris answers for that span.
Outside it, the library refuses with EphemerisOutOfRange, the routes answer 422 with the range, and discovery gives a
clip from outside it no ephemerides and no horizon pose, saying why. Inside it, each moment is put on TT and UT1 by
ERFA's rules (services/science/catalog.py `time_scales`), with the bounds the stated uncertainties use.

Covers:
- The library refuses a moment one second outside the range at either end and answers at both edges
- sky-context, radiometry-audit, ephemeris and solve-horizon answer 422 with the range outside it
- Discovery gives a synthetic clip dated 2051 no ephemerides, no horizon pose and an ephemerisError
- The Espenak and Meeus (2006) Delta T segments used before 1960 meet within a tenth of a second from 1860, and at the
  older joins (-500, 500, 1600, 1700, 1800) by less than the bound stated on either side
- Before 1800 the polynomials reproduce NASA's Table 1 of Delta T (Morrison and Stephenson 2004): within the 4 s the
  page claims from -500 to 500 (with its -500 value changed to 17203.7, as the page says), and within the table's own
  standard error to 1800; the bound there is three times the larger standard error of the span, and before -500 three
  times Morrison and Stephenson's 0.8 u^2
- PBT: from 1800 to 2050 every body answers with a finite position and a positive uncertainty, and the moment's time
  scales follow the rules: before 1960 the moment is UT1 and TT - UT1 is Espenak and Meeus' Delta T, with no UT1
  bound; from 2017 to mid 2027 TT - UTC is 69.184 s (IERS Bulletin C 72: UTC - TAI = -37 s from 2017 January 1, and TT
  - TAI = 32.184 s) with UT1 within 0.9 s and TT exact; later, TT carries a bound of at least one leap second
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import math

from fastapi.testclient import TestClient
from hypothesis import example, given, settings, strategies as st
import numpy as np
import pytest

from services.api import app
from services.science.catalog import delta_t_bound_s, espenak_meeus_delta_t_s, time_scales
from services.science.ephemeris import BODY_NAMES, compute_solar_system_ephemerides

# A synthetic place that points at nobody (the open North Atlantic, as in the other tests)
PLACE = {"lat_deg": 30.0, "lon_deg": -40.0}
BEFORE, FIRST, LAST, AFTER = "1799-12-31T23:59:59Z", "1800-01-01T00:00:00Z", "2050-12-31T23:59:59Z", "2051-01-01T00:00:00Z"
START = datetime(1800, 1, 1, tzinfo=timezone.utc)
SPAN_S = (datetime(2051, 1, 1, tzinfo=timezone.utc) - START).total_seconds() - 1.0


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


@pytest.mark.parametrize("join", [1860, 1900, 1920, 1941])
def test_the_delta_t_segments_meet(join: int) -> None:
    assert espenak_meeus_delta_t_s(join - 1e-9) == pytest.approx(espenak_meeus_delta_t_s(join), abs=0.1)


@pytest.mark.parametrize("join", [-500, 500, 1600, 1700, 1800])
def test_the_older_delta_t_segments_meet_within_their_stated_bounds(join: int) -> None:
    step = abs(espenak_meeus_delta_t_s(join - 1e-9) - espenak_meeus_delta_t_s(join))
    assert step < min(delta_t_bound_s(join - 1e-9), delta_t_bound_s(join))


@pytest.mark.parametrize("join", [-500, 500, 1600, 1700, 1800, 1860, 1900, 1920, 1941])
def test_each_delta_t_expression_answers_from_its_own_first_year(join: int) -> None:
    # The spans are half open: a join year is the later expression's, and a microyear before it the earlier one's. The
    # two differ there by 0.0009 s (1941) to 0.25 s (1600), at least a hundred times a microyear's change of either
    at = espenak_meeus_delta_t_s(float(join))
    assert abs(at - espenak_meeus_delta_t_s(join + 1e-6)) * 100.0 < abs(at - espenak_meeus_delta_t_s(join - 1e-6))


# NASA, "Polynomial Expressions for Delta T" (eclipse.gsfc.nasa.gov/SEhelp/deltatpoly2004.html, read at source
# 2026-11-13), written here from the page and not from the code: each span (its first year, the year it ends before)
# with its expression as printed
NASA_EXPRESSIONS = (
    (-500, 500, lambda y: (lambda u: 10583.6 - 1014.41 * u + 33.78311 * u**2 - 5.952053 * u**3 - 0.1798452 * u**4
                           + 0.022174192 * u**5 + 0.0090316521 * u**6)(y / 100)),
    (500, 1600, lambda y: (lambda u: 1574.2 - 556.01 * u + 71.23472 * u**2 + 0.319781 * u**3 - 0.8503463 * u**4
                           - 0.005050998 * u**5 + 0.0083572073 * u**6)((y - 1000) / 100)),
    (1600, 1700, lambda y: (lambda t: 120 - 0.9808 * t - 0.01532 * t**2 + t**3 / 7129)(y - 1600)),
    (1700, 1800, lambda y: (lambda t: 8.83 + 0.1603 * t - 0.0059285 * t**2 + 0.00013336 * t**3
                            - t**4 / 1174000)(y - 1700)),
    (1800, 1860, lambda y: (lambda t: 13.72 - 0.332447 * t + 0.0068612 * t**2 + 0.0041116 * t**3 - 0.00037436 * t**4
                            + 0.0000121272 * t**5 - 0.0000001699 * t**6 + 0.000000000875 * t**7)(y - 1800)),
    (1860, 1900, lambda y: (lambda t: 7.62 + 0.5737 * t - 0.251754 * t**2 + 0.01680668 * t**3 - 0.0004473624 * t**4
                            + t**5 / 233174)(y - 1860)),
    (1900, 1920, lambda y: (lambda t: -2.79 + 1.494119 * t - 0.0598939 * t**2 + 0.0061966 * t**3
                            - 0.000197 * t**4)(y - 1900)),
    (1920, 1941, lambda y: (lambda t: 21.20 + 0.84493 * t - 0.076100 * t**2 + 0.0020936 * t**3)(y - 1920)),
    (1941, 1961, lambda y: (lambda t: 29.07 + 0.407 * t - t**2 / 233 + t**3 / 2547)(y - 1950)),
)


@pytest.mark.parametrize(("first", "end", "expression"), NASA_EXPRESSIONS)
def test_each_delta_t_expression_is_the_one_nasa_prints_across_its_span(first: int, end: int, expression) -> None:
    # Its first year, three inside and a microyear before its end; 1961 itself belongs to the last span (the code
    # answers up to it, test_before_500_bce_..._no_later_than_1961)
    years = [first + (end - first) * k / 4 for k in range(4)] + [end - 1e-6] + ([float(end)] if end == 1961 else [])
    for year in years:
        assert espenak_meeus_delta_t_s(year) == pytest.approx(expression(year), rel=1e-12, abs=1e-9), year


# NASA, "Values of Delta T Derived from Historical Records", Table 1 (eclipse.gsfc.nasa.gov/SEhelp/deltat2004.html,
# read at source 2026-11-11): year, Delta T and its standard error, seconds
TABLE1 = (
    (-500, 17190, 430), (-400, 15530, 390), (-300, 14080, 360), (-200, 12790, 330), (-100, 11640, 290),
    (0, 10580, 260), (100, 9600, 240), (200, 8640, 210), (300, 7680, 180), (400, 6700, 160), (500, 5710, 140),
    (600, 4740, 120), (700, 3810, 100), (800, 2960, 80), (900, 2200, 70), (1000, 1570, 55), (1100, 1090, 40),
    (1200, 740, 30), (1300, 490, 20), (1400, 320, 20), (1500, 200, 20), (1600, 120, 20), (1700, 9, 5),
    (1750, 13, 2), (1800, 14, 1),
)


@pytest.mark.parametrize(("year", "delta_t", "standard_error"), TABLE1)
def test_before_1800_the_polynomials_reproduce_table_1_and_the_bound_is_its_standard_error(
    year: int, delta_t: float, standard_error: float
) -> None:
    value = espenak_meeus_delta_t_s(year)
    if year <= 500:
        # The page: the -500 to 500 polynomial "reproduces the values in Table 1 with an error not larger than 4
        # seconds", with the value for -500 changed "from 17190 to 17203.7"
        assert abs(value - (17203.7 if year == -500 else delta_t)) <= 4.0
    else:
        assert abs(value - delta_t) <= standard_error
    if year < 1800:
        following = next(error for y, _, error in TABLE1 if y > year)
        assert delta_t_bound_s(year) == 3.0 * max(standard_error, following)
    if year > -500:
        # One step before a tabulated year, the span that ends there
        previous = [error for y, _, error in TABLE1 if y < year][-1]
        assert delta_t_bound_s(year - 1e-9) == 3.0 * max(previous, standard_error)


def test_the_delta_t_bound_takes_each_later_eras_value_from_its_first_instant() -> None:
    # From 1900 three times NASA's 0.1 s, from 1800 three times its 1 s ("Uncertainty in Delta T", Table 2), before
    # 1800 Table 1's last span (1750 to 1800, standard errors 2 and 1): each start inside its era, one step before it
    # in the earlier one
    for year, bound in ((1900.0, 0.3), (1900.0 - 1e-9, 3.0), (1800.0, 3.0), (1800.0 - 1e-9, 6.0)):
        assert delta_t_bound_s(year) == pytest.approx(bound, rel=1e-12), year


def test_before_500_bce_delta_t_is_morrison_and_stephensons_parabola_and_no_later_than_1961() -> None:
    # NASA's "Polynomial Expressions for Delta T": before -500, -20 + 32 u^2, u = (year - 1820) / 100
    for year in (-600.0, -1000.0, -3000.0):
        assert espenak_meeus_delta_t_s(year) == pytest.approx(-20.0 + 32.0 * ((year - 1820.0) / 100.0) ** 2, rel=1e-12)
    # From 1960 the moment is UTC: the expressions are not asked for after 1961, and 1961.0 itself is the last year
    # they answer for (the edge, and one step past it)
    assert math.isfinite(espenak_meeus_delta_t_s(1961.0))
    for year in (1961.0 + 1e-9, 1961.5):
        with pytest.raises(ValueError, match="before 1961 only"):
            espenak_meeus_delta_t_s(year)


def test_the_utc_era_starts_at_1960_and_the_moment_before_it_is_ut1() -> None:
    # Before 1960 there was no UTC: the moment is UT1, with no UT1 bound and TT from Delta T; from its first second the
    # moment is UTC, with UT1 within 0.9 s
    before, first = time_scales("1959-12-31T23:59:59Z"), time_scales("1960-01-01T00:00:00Z")
    assert (before.ut1_bound_s, before.tt_bound_s) == (0.0, delta_t_bound_s(1959.0 + 364.5 / 365.25))
    assert first.ut1_bound_s == 0.9


@pytest.mark.parametrize("moment", ["1850-03-14T18:00:00Z", "1995-05-01T12:00:00Z", "2031-08-30T03:01:00Z"])
def test_a_moment_is_the_same_instant_however_its_utc_is_written_and_its_time_scales_are_read_only(moment: str) -> None:
    """The same UTC instant written with Z, with +00:00, with no offset (taken as UTC) and in another offset gives the
    same TT and UT1 and the same bounds; the record the cache hands back cannot be changed by a caller."""
    base = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    plus_two = (base + timedelta(hours=2)).replace(tzinfo=timezone(timedelta(hours=2))).isoformat()
    spellings = [moment, moment.replace("Z", "+00:00"), moment[:-1], plus_two]
    first = time_scales(spellings[0])
    for spelling in spellings[1:]:
        scales = time_scales(spelling)
        assert (scales.tt, scales.ut1, scales.tt_bound_s, scales.ut1_bound_s) == (
            first.tt, first.ut1, first.tt_bound_s, first.ut1_bound_s), spelling
    with pytest.raises(AttributeError):
        first.tt_bound_s = 0.0  # type: ignore[misc]


def test_before_500_bce_the_bound_is_three_times_morrison_and_stephensons_standard_error() -> None:
    # NASA, "Uncertainty in Delta T": sigma = 0.8 * t^2 s, t = (year - 1820) / 100; at -500 it is Table 1's 430 s
    for year in (-500.0 - 1e-9, -600.0, -1000.0, -2000.0):
        assert delta_t_bound_s(year) == pytest.approx(3.0 * 0.8 * ((year - 1820.0) / 100.0) ** 2, rel=1e-12)
    assert delta_t_bound_s(-500.0 - 1e-9) / 3.0 == pytest.approx(430.0, abs=1.0)


def _seconds(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) + (a[1] - b[1])) * 86400.0


@settings(max_examples=60, deadline=None)
@given(st.floats(min_value=0.0, max_value=SPAN_S).map(lambda s: START + timedelta(seconds=s)), st.sampled_from(BODY_NAMES))
@example(datetime(1800, 1, 1, tzinfo=timezone.utc), "Moon")
@example(datetime(1959, 12, 31, 23, 59, tzinfo=timezone.utc), "Moon")
@example(datetime(2026, 11, 4, 3, tzinfo=timezone.utc), "Sun")
@example(datetime(2027, 7, 1, tzinfo=timezone.utc), "Moon")
@example(datetime(2050, 12, 31, 23, tzinfo=timezone.utc), "Neptune")
def test_pbt_every_body_answers_from_1800_to_2050_on_the_stated_time_scales(when: datetime, body: str) -> None:
    iso = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    (finding,) = compute_solar_system_ephemerides(obs_utc_iso=iso, **PLACE, bodies=(body,))
    assert all(math.isfinite(v) for v in (finding.ra_deg, finding.dec_deg, finding.alt_deg, finding.az_deg))
    assert math.isfinite(finding.uncertainty_arcsec) and finding.uncertainty_arcsec > 0.0
    assert finding.distance_lower_au < finding.distance_au < finding.distance_upper_au
    scales = time_scales(iso)
    if when.year < 1960:
        year = when.year + (when.timetuple().tm_yday - 0.5) / 365.25
        assert _seconds(scales.tt, scales.ut1) == pytest.approx(espenak_meeus_delta_t_s(year), abs=1e-4)
        assert scales.ut1_bound_s == 0.0 and scales.tt_bound_s > 0.0
    else:
        assert scales.ut1_bound_s == 0.9
        if datetime(2017, 1, 1, tzinfo=timezone.utc) <= when < datetime(2027, 7, 1, tzinfo=timezone.utc):
            assert _seconds(scales.tt, scales.ut1) == pytest.approx(69.184, abs=1e-4)
            assert scales.tt_bound_s == 0.0
        elif when >= datetime(2027, 7, 1, tzinfo=timezone.utc):
            assert scales.tt_bound_s >= 1.0
