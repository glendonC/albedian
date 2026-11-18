"""The planets' apparent magnitudes (services/science/ephemeris.py `apparent_magnitude`, Mallama and Hilton 2018).

Covers:
- Against JPL Horizons' stored rows (tests/fixtures/horizons_phase_rts.json.gz), which come from the same formulas
  (Horizons' manual cites the paper), so they check this code, not the model: within an allowance computed per row
  from Horizons' printing, the formula's slope over the phase angles' difference and the aberration Horizons' angle
  carries, and this ephemeris' distance bounds; Mars also within the 0.21 mag its left-out terms can make, Neptune
  after 2000.0 also within the phase term of the paper's Eq. 17, which Horizons' rows follow
- Each formula's stated range from one table: one sample inside each end, one just outside, refused outside;
  Saturn's ring tilt and its contrary-sign rule; Neptune's date pieces
- No uncertainty is stated; only the planets have a formula; Uranus' flattening from the report's radii
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import gzip
import json
import math
from pathlib import Path

import erfa
import numpy as np
import pytest

from services.science import ephemeris
from services.science.catalog import RIGID_ABERRATION_MAX_ARCSEC, ra_dec_to_unit_vector, time_scales
from services.science.ephemeris import (
    BODY_BY_NAME,
    SATURN_POLE,
    URANUS_FLATTENING,
    URANUS_POLE,
    EphemerisOutOfRange,
    MagnitudeOutOfRange,
    _magnitude_terms,
    apparent_magnitude,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "horizons_phase_rts.json.gz"
MONTHS = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
# Horizons prints APmag to 3 decimals
APMAG_PRINT = 0.5e-3
# What leaving out Mars' two correction terms can change: the paper's longitude excursions (0.060) plus the seasonal
# term's whole range of variation (Table 3, 0.15) [inference: that term takes the value 0 somewhere in its range]
MARS_LEFT_OUT_MAG = 0.060 + 0.15
MOMENT, LAT, LON = "2031-08-30T03:01:00Z", 30.0, -40.0
TT = time_scales(MOMENT).tt
STEP = 1e-9


def _iso(text: str) -> str:
    date, clock = text.strip().split()
    year, month, day = date.split("-")
    return f"{year}-{MONTHS[month]:02d}-{day}T{clock[:8]}Z"


def _phase_rows() -> list[tuple[dict, list[str]]]:
    data = json.loads(gzip.decompress(FIXTURE.read_bytes()).decode("utf-8"))
    out = []
    for request in data["requests"]:
        if request["kind"].startswith("phase") and request["body"] != "Moon":
            result = json.loads(request["response"])["result"]
            table = result[result.index("$$SOE") + 5: result.index("$$EOE")]
            out += [(request, [cell.strip() for cell in line.split(",")]) for line in table.strip().splitlines()]
    return out


def _slope(name: str, phase: float, tt: tuple[float, float], to_observer: np.ndarray, to_sun: np.ndarray) -> float:
    """The formula's slope at `phase` (mag per degree), one-sided where a step would leave its range."""
    step = 1e-3
    here, _, _ = _magnitude_terms(name, phase, tt, to_observer, to_sun)
    try:
        ahead, _, _ = _magnitude_terms(name, phase + step, tt, to_observer, to_sun)
    except MagnitudeOutOfRange:
        behind, _, _ = _magnitude_terms(name, phase - step, tt, to_observer, to_sun)
        return abs(here - behind) / step
    return abs(ahead - here) / step


def test_the_magnitudes_match_horizons_within_an_allowance_computed_for_each_row() -> None:
    checked, refused = 0, []
    for request, row in _phase_rows():
        name, lat, lon, moment = request["body"], request["latDeg"], request["lonDeg"], _iso(row[0])
        try:
            got = apparent_magnitude(name, moment, lat, lon) if lat is not None else apparent_magnitude(name, moment)
        except MagnitudeOutOfRange:
            refused.append((name, row[4], float(row[7])))
            continue
        scales, _, observer, topocentric = ephemeris._observer(moment, lat, lon)
        body = BODY_BY_NAME[name]
        vector, helio = ephemeris._astrometric_au(body, scales.tt, observer)
        _, theory_km = ephemeris._theory_bound(body, scales.tt, vector, helio, vector)
        r_km, d_km = float(np.linalg.norm(helio)) * ephemeris.AU_KM, float(np.linalg.norm(vector)) * ephemeris.AU_KM
        distance_mag = 5.0 / math.log(10.0) * (
            theory_km / r_km + (theory_km + ephemeris._place_bound_km(scales, topocentric)) / d_km
        )
        angle = abs(got.phase_angle_deg - float(row[7])) + RIGID_ABERRATION_MAX_ARCSEC / 3600.0
        allowance = APMAG_PRINT + _slope(name, got.phase_angle_deg, scales.tt, -vector, -helio) * angle + distance_mag
        if name == "Mars":
            allowance += MARS_LEFT_OUT_MAG
        if name == "Neptune" and float(erfa.epj(*scales.tt)) > 2000.0:
            a = float(row[7])
            allowance += 7.944e-3 * a + 9.617e-5 * a * a
        assert abs(got.magnitude - float(row[4])) <= allowance, (name, moment, got.magnitude, row[4], allowance)
        checked += 1
    # Mercury beyond the observed span, where Horizons extrapolates and this code refuses
    assert refused and all(name == "Mercury" and not 2.0 < phase < 170.0 for name, _, phase in refused), refused
    assert checked == 7 * 64 - len(refused)


def test_horizons_rows_for_neptune_after_2000_follow_the_papers_eq_17_not_eq_16() -> None:
    """Measured, so the allowance above names a real difference: after 2000.0 at least one stored row lies further
    from Eq. 16 than printing and the bounds allow, and within that once Eq. 17's phase term is added."""
    beyond = 0
    for request, row in _phase_rows():
        if request["body"] != "Neptune":
            continue
        moment = _iso(row[0])
        got = apparent_magnitude("Neptune", moment, request["latDeg"], request["lonDeg"]) if request["latDeg"] is not None \
            else apparent_magnitude("Neptune", moment)
        a = float(row[7])
        if got.equation.endswith("(after 2000.0)") and abs(got.magnitude - float(row[4])) > 2.0 * APMAG_PRINT:
            beyond += 1
            assert abs(got.magnitude + 7.944e-3 * a + 9.617e-5 * a * a - float(row[4])) <= 2.0 * APMAG_PRINT
    assert beyond >= 1


def _at_latitude(pole: np.ndarray, latitude_deg: float) -> np.ndarray:
    across = np.cross(pole, [1.0, 0.0, 0.0])
    across /= float(np.linalg.norm(across))
    lat = math.radians(latitude_deg)
    return math.cos(lat) * across + math.sin(lat) * pole


POLE = ephemeris._pole(SATURN_POLE, TT)
ANY = _at_latitude(POLE, 10.0)
# Each formula's range, from one table: (planet, phase angle, expected equation or None for refused)
PHASE_EDGES = [
    ("Mercury", 2.0, None), ("Mercury", 2.0 + STEP, "Eq. 2"), ("Mercury", 170.0 - STEP, "Eq. 2"), ("Mercury", 170.0, None),
    ("Venus", 0.0, None), ("Venus", STEP, "Eq. 3"), ("Venus", 163.7, "Eq. 3"), ("Venus", 163.7 + STEP, "Eq. 4"),
    ("Venus", 179.0 - STEP, "Eq. 4"), ("Venus", 179.0, None),
    ("Mars", -STEP, None), ("Mars", 0.0, "Eq. 6"), ("Mars", 50.0, "Eq. 6"), ("Mars", 50.0 + STEP, None),
    ("Jupiter", -STEP, None), ("Jupiter", 0.0, "Eq. 8"), ("Jupiter", 12.0, "Eq. 8"), ("Jupiter", 12.0 + STEP, None),
    ("Saturn", -STEP, None), ("Saturn", 0.0, "Eq. 10"), ("Saturn", 6.5 - STEP, "Eq. 10"), ("Saturn", 6.5, None),
    # Eq. 14 and Eq. 16 refuse no phase angle: past the paper's rounded 3.2 and 1.9 degrees they still answer
    ("Uranus", 3.3, "Eq. 14"), ("Neptune", 1.96, "Eq. 16 (after 2000.0)"),
]


@pytest.mark.parametrize(("name", "phase", "expected"), PHASE_EDGES)
def test_each_formula_at_each_end_of_its_phase_angle_range(name: str, phase: float, expected: str | None) -> None:
    if expected is None:
        with pytest.raises(MagnitudeOutOfRange):
            _magnitude_terms(name, phase, TT, ANY, ANY)
        return
    _, equation, _ = _magnitude_terms(name, phase, TT, ANY, ANY)
    assert equation == expected


@pytest.mark.parametrize(("tilt", "accepted"), [(0.0, True), (27.0 - 1e-6, True), (27.0 + 1e-6, False)])
def test_saturn_at_each_end_of_its_ring_tilt_range(tilt: float, accepted: bool) -> None:
    direction = _at_latitude(POLE, tilt)
    if not accepted:
        with pytest.raises(MagnitudeOutOfRange):
            _magnitude_terms("Saturn", 1.0, TT, direction, direction)
        return
    terms, _, _ = _magnitude_terms("Saturn", 1.0, TT, direction, direction)
    sin_beta = math.sin(math.radians(tilt))
    assert terms == pytest.approx(-8.914 - 1.825 * sin_beta + 0.026 - 0.378 * sin_beta * math.exp(-2.25), abs=1e-9)


def test_saturn_refuses_exact_ring_tilt_27_and_treats_both_south_as_lit(monkeypatch: pytest.MonkeyPatch) -> None:
    """27.0 degrees is outside `beta < 27`, and both observer and Sun south of the ring plane share a sign."""
    terms, _, _ = _magnitude_terms("Saturn", 2.0, TT, _at_latitude(POLE, -20.0), _at_latitude(POLE, -5.0))
    sin_10 = math.sin(math.radians(10.0))
    assert terms == pytest.approx(-8.914 - 1.825 * sin_10 + 0.052 - 0.378 * sin_10 * math.exp(-4.5), abs=1e-9)
    monkeypatch.setattr(ephemeris, "_latitude_deg", lambda pole, direction: 27.0)
    with pytest.raises(MagnitudeOutOfRange, match="beta < 27"):
        _magnitude_terms("Saturn", 1.0, TT, ANY, ANY)
    monkeypatch.setattr(ephemeris, "_latitude_deg", lambda pole, direction: 27.0 - 1e-9)
    _, eq, _ = _magnitude_terms("Saturn", 1.0, TT, ANY, ANY)
    assert eq == "Eq. 10"


def test_saturns_rings_lit_from_one_side_and_seen_from_the_other_count_as_edge_on() -> None:
    """The effective tilt is sqrt(betaE betaS) when the two have the same sign and 0 when contrary (the rings
    backlit) or when either lies in the ring plane: the observer 20 degrees north and the Sun 5 degrees south (or 0)
    give the edge-on value, and a small shared tilt above zero still adds the ring term."""
    terms, _, _ = _magnitude_terms("Saturn", 2.0, TT, _at_latitude(POLE, 20.0), _at_latitude(POLE, -5.0))
    assert terms == pytest.approx(-8.914 + 0.026 * 2.0, abs=1e-12)
    in_ring_plane = np.array([0.0, POLE[2], -POLE[1]])
    try:
        on_plane, _, _ = _magnitude_terms("Saturn", 2.0, TT, _at_latitude(POLE, 10.0), in_ring_plane)
    except ZeroDivisionError as exc:  # pragma: no cover - fails if beta_e * beta_s is replaced by division
        pytest.fail(f"_magnitude_terms divided by a zero ring-plane latitude: {exc}")
    assert on_plane == pytest.approx(-8.914 + 0.026 * 2.0, abs=1e-12)
    tiny_rad = math.radians(5e-4)
    tiny_dir = _at_latitude(POLE, 5e-4)
    tiny_terms, _, _ = _magnitude_terms("Saturn", 2.0, TT, tiny_dir, tiny_dir)
    assert tiny_terms == pytest.approx(
        -8.914 - 1.825 * math.sin(tiny_rad) + 0.052 - 0.378 * math.sin(tiny_rad) * math.exp(-4.5), abs=1e-12
    )
    terms, _, _ = _magnitude_terms("Saturn", 2.0, TT, _at_latitude(POLE, 20.0), _at_latitude(POLE, 5.0))
    sin_beta = math.sin(math.radians(10.0))
    assert terms == pytest.approx(-8.914 - 1.825 * sin_beta + 0.052 - 0.378 * sin_beta * math.exp(-4.5), abs=1e-9)


def test_latitude_clamps_dot_products_slightly_past_unit_length() -> None:
    """`_latitude_deg` clamps rounding slightly past +-1 before `math.asin` rather than raising a domain error."""
    slightly_long_pole = np.array([1.0 + 1e-12, 0.0, 0.0])
    try:
        north = ephemeris._latitude_deg(slightly_long_pole, np.array([1.0, 0.0, 0.0]))
        south = ephemeris._latitude_deg(slightly_long_pole, np.array([-1.0, 0.0, 0.0]))
    except ValueError as exc:  # pragma: no cover - fails if the [-1, 1] clamp is widened
        pytest.fail(f"_latitude_deg did not clamp a dot product slightly past +-1: {exc}")
    assert north == pytest.approx(90.0, abs=1e-12) and south == pytest.approx(-90.0, abs=1e-12)


def _tt_at_epoch(epoch: float) -> tuple[float, float]:
    return tuple(float(v) for v in erfa.epj2jd(epoch))


@pytest.mark.parametrize(("epoch", "expected", "value"), [
    (1980.0 - 1e-6, "Eq. 16 (before 1980.0)", -6.89),
    (1980.0, "Eq. 16 (1980.0 to 2000.0)", -6.89),
    (1980.0 + 1e-6, "Eq. 16 (1980.0 to 2000.0)", -6.89),
    (1990.0, "Eq. 16 (1980.0 to 2000.0)", -6.89 - 0.054),
    (2000.0 - 1e-6, "Eq. 16 (1980.0 to 2000.0)", -6.89 - 0.108),
    (2000.0, "Eq. 16 (1980.0 to 2000.0)", -6.89 - 0.108),
    (2000.0 + 1e-6, "Eq. 16 (after 2000.0)", -7.00),
])
def test_neptune_at_each_end_of_its_date_pieces(epoch: float, expected: str, value: float) -> None:
    terms, equation, _ = _magnitude_terms("Neptune", 1.0, _tt_at_epoch(epoch), ANY, ANY)
    assert equation == expected
    assert terms == pytest.approx(value, abs=1e-6)


def test_each_planets_polynomial_and_pole_follow_the_published_coefficients() -> None:
    """Every coefficient, power, sign, pole rate and distance logarithm is pinned against Mallama and Hilton (2018)
    and Archinal et al. (2018), and `PlanetMagnitude` is frozen."""
    assert SATURN_POLE == (40.589, -0.036, 83.537, -0.004)
    assert URANUS_POLE == (257.311, 0.0, -15.175, 0.0)
    for centuries in (-1.0, 0.0, 1.0):
        tt_c = (2451545.0, centuries * 36525.0)
        expected_sat = np.asarray(ra_dec_to_unit_vector(40.589 - 0.036 * centuries, 83.537 - 0.004 * centuries))
        assert np.allclose(ephemeris._pole(SATURN_POLE, tt_c), expected_sat, atol=1e-15)
        expected_ura = np.asarray(ra_dec_to_unit_vector(257.311, -15.175))
        assert np.allclose(ephemeris._pole(URANUS_POLE, tt_c), expected_ura, atol=1e-15)

    a_merc = 120.0
    t_merc, eq_merc, _ = _magnitude_terms("Mercury", a_merc, TT, ANY, ANY)
    assert eq_merc == "Eq. 2"
    assert t_merc == pytest.approx(
        -0.613 + 6.3280e-02 * a_merc - 1.6336e-03 * a_merc**2 + 3.3644e-05 * a_merc**3
        - 3.4265e-07 * a_merc**4 + 1.6893e-09 * a_merc**5 - 3.0334e-12 * a_merc**6,
        abs=1e-12,
    )

    a_v1 = 100.0
    t_v1, eq_v1, _ = _magnitude_terms("Venus", a_v1, TT, ANY, ANY)
    assert eq_v1 == "Eq. 3"
    assert t_v1 == pytest.approx(
        -4.384 - 1.044e-03 * a_v1 + 3.687e-04 * a_v1**2 - 2.814e-06 * a_v1**3 + 8.938e-09 * a_v1**4,
        abs=1e-12,
    )

    a_v2 = 170.0
    t_v2, eq_v2, _ = _magnitude_terms("Venus", a_v2, TT, ANY, ANY)
    assert eq_v2 == "Eq. 4"
    assert t_v2 == pytest.approx(236.05828 - 2.81914 * a_v2 + 8.39034e-03 * a_v2**2, abs=1e-11)

    a_mars = 40.0
    t_mars, eq_mars, lo_mars = _magnitude_terms("Mars", a_mars, TT, ANY, ANY)
    assert eq_mars == "Eq. 6" and "0.060" in lo_mars
    assert t_mars == pytest.approx(-1.601 + 0.02267 * a_mars - 0.0001302 * a_mars**2, abs=1e-12)

    a_jup = 10.0
    t_jup, eq_jup, _ = _magnitude_terms("Jupiter", a_jup, TT, ANY, ANY)
    assert eq_jup == "Eq. 8"
    assert t_jup == pytest.approx(-9.395 - 3.7e-04 * a_jup + 6.16e-04 * a_jup**2, abs=1e-12)

    u_pole = ephemeris._pole(URANUS_POLE, TT)
    d_obs, d_sun = _at_latitude(u_pole, 45.0), _at_latitude(u_pole, -25.0)
    t_ura, eq_ura, _ = _magnitude_terms("Uranus", 1.5, TT, d_obs, d_sun)
    assert eq_ura == "Eq. 14"
    g_obs = math.degrees(math.atan(math.tan(math.radians(45.0)) / (1.0 - URANUS_FLATTENING) ** 2))
    g_sun = math.degrees(math.atan(math.tan(math.radians(25.0)) / (1.0 - URANUS_FLATTENING) ** 2))
    assert t_ura == pytest.approx(-7.110 - 8.4e-04 * (g_obs + g_sun) / 2.0, abs=1e-12)

    got = apparent_magnitude("Saturn", MOMENT, LAT, LON)
    with pytest.raises(FrozenInstanceError):
        got.magnitude = 0.0  # type: ignore[misc]
    scales, _, observer, _ = ephemeris._observer(MOMENT, LAT, LON)
    vec, helio = ephemeris._astrometric_au(BODY_BY_NAME["Saturn"], scales.tt, observer)
    terms, _, _ = _magnitude_terms("Saturn", got.phase_angle_deg, scales.tt, -vec, -helio)
    assert got.magnitude == pytest.approx(
        5.0 * math.log10(float(np.linalg.norm(helio)) * float(np.linalg.norm(vec))) + terms, abs=1e-12
    )


@pytest.mark.parametrize(("moment", "accepted"), [
    ("1799-12-31T23:59:59Z", False),
    ("1800-01-01T00:00:00Z", True),
    ("2050-12-31T23:59:59Z", True),
    ("2051-01-01T00:00:00Z", False),
])
def test_apparent_magnitude_holds_to_the_ephemeris_span(moment: str, accepted: bool) -> None:
    if not accepted:
        with pytest.raises(EphemerisOutOfRange):
            apparent_magnitude("Mars", moment, LAT, LON)
        return
    assert apparent_magnitude("Mars", moment, LAT, LON).obs_utc_iso == moment


def test_mercury_beyond_its_observed_span_is_refused_by_the_public_function() -> None:
    for request, row in _phase_rows():
        if request["body"] == "Mercury" and not 2.0 < float(row[7]) < 170.0:
            with pytest.raises(MagnitudeOutOfRange, match="Mallama and Hilton"):
                apparent_magnitude("Mercury", _iso(row[0]), request["latDeg"], request["lonDeg"]) \
                    if request["latDeg"] is not None else apparent_magnitude("Mercury", _iso(row[0]))
            return
    pytest.fail("the fixture holds no Mercury row beyond the observed span")


def test_no_uncertainty_is_stated_and_the_record_says_why() -> None:
    for name in ("Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune"):
        got = apparent_magnitude(name, MOMENT, LAT, LON)
        assert got.one_sigma_mag is None and got.frame == "topocentric"
        assert any("no residual" in text for text in got.limitations), name
        assert any("Mallama and Hilton (2018)" in text for text in got.limitations), name
        assert "Seen from the given place at sea level (topocentric)." in got.limitations, name
        assert not any("Earth's centre." in text for text in got.limitations), name
        assert any("Neptune brightened" in text for text in got.limitations) == (name == "Neptune"), name
    assert any("left out" in text for text in apparent_magnitude("Mars", MOMENT).limitations)
    venus_geo = apparent_magnitude("Venus", MOMENT)
    assert venus_geo.frame == "geocentric"
    assert "Seen from the Earth's centre." in venus_geo.limitations
    assert not any("topocentric" in text for text in venus_geo.limitations)


def test_a_place_is_both_coordinates_or_neither() -> None:
    # One coordinate alone names no place and no centre: refused as such, not read as a number
    for lat, lon in ((LAT, None), (None, LON)):
        with pytest.raises(ValueError, match="together, or neither"):
            apparent_magnitude("Mars", MOMENT, lat, lon)


@pytest.mark.parametrize("name", ["Sun", "Moon", "Pluto"])
def test_only_the_planets_have_a_formula(name: str) -> None:
    with pytest.raises(KeyError):
        apparent_magnitude(name, MOMENT, LAT, LON)
    with pytest.raises(KeyError):
        _magnitude_terms(name, 1.0, TT, ANY, ANY)


def test_uranus_flattening_is_the_reports_radii_whose_digits_the_paper_prints() -> None:
    """The paper prints f = 0.0022927; the report's radii give 0.022927: the same five digits."""
    assert URANUS_FLATTENING == 1.0 - 24973.0 / 25559.0
    assert f"{URANUS_FLATTENING:.6f}" == "0.022927" and "0.022927"[2:] == "0.0022927"[3:]
