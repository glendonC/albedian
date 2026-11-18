"""Rise, transit and set (services/science/ephemeris.py `rise_transit_set`, USNO's convention).

Covers:
- Against JPL Horizons' stored rise, transit and set tables (tests/fixtures/horizons_phase_rts.json.gz: the Sun, the
  Moon, Mars and Saturn over three synthetic three-day spans at four open-ocean places). Horizons prints the minute
  step after each event, so the event lies in the 60 s before its printed time; beyond that window each event is
  allowed the gap between Horizons' threshold (Saemundsson's refraction, the true solar radius) and USNO's, and this
  ephemeris' stated position bound, over the event's own rate, plus the search's 1 s
- At each timed rise or set the centre sits on USNO's threshold; at each transit the centre is on the meridian; the
  spread is the measured refraction spread over the computed rate
- A graze near the southern polar circle at the December solstice: no time is stated, and the result says so;
  a clear crossing just past the grazing band has its time
- No rise or set in the midnight Sun; the search refuses a span past the ephemeris'; an unknown body is refused
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime
import gzip
import json
import math
from pathlib import Path

import pytest

from services.science import catalog, ephemeris, pose_estimate
from services.science.ephemeris import (
    EVENT_TOLERANCE_S,
    GRAZING_DEG,
    HORIZON_REFRACTION_RANGE_95_DEG,
    HORIZON_REFRACTION_SPREAD_DEG,
    MOON_MEAN_RADIUS_KM,
    USNO_HORIZON_REFRACTION_DEG,
    USNO_SUN_RADIUS_DEG,
    EphemerisOutOfRange,
    compute_solar_system_ephemerides,
    rise_transit_set,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "horizons_phase_rts.json.gz"
MONTHS = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
HORIZONS_STEP_S = 60.0
# The sky turns about 15 arcsec per second at the equator; the Moon, moving east, a little less: 14 is taken
SKY_TURN_ARCSEC_PER_S = 14.0
MOMENT, LAT, LON = "2031-08-30T03:01:00Z", 30.0, -40.0


def _iso(text: str) -> str:
    date, clock = text.strip().split()
    year, month, day = date.split("-")
    return f"{year}-{MONTHS[month]:02d}-{day}T{clock[:8]}Z"


def _seconds(a: str, b: str) -> float:
    def parse(text: str) -> datetime:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))

    return (parse(a) - parse(b)).total_seconds()


def _rts_requests() -> list[dict]:
    data = json.loads(gzip.decompress(FIXTURE.read_bytes()).decode("utf-8"))
    return [r for r in data["requests"] if r["kind"] == "rts"]


def _events(request: dict) -> list[tuple[str, str]]:
    result = json.loads(request["response"])["result"]
    table = result[result.index("$$SOE") + 5: result.index("$$EOE")]
    words = {"r": "rise", "s": "set", "t": "transit"}
    rows = [[cell.strip() for cell in line.split(",")] for line in table.strip().splitlines()]
    return [(_iso(row[0]), words[row[2]]) for row in rows if row[2] in words]


def test_every_stored_horizons_rise_transit_and_set_is_matched_within_its_allowance() -> None:
    refraction_gap = abs(catalog.airless_altitude_deg(0.0) + USNO_HORIZON_REFRACTION_DEG)
    checked = 0
    for request in _rts_requests():
        name, lat, lon = request["body"], request["latDeg"], request["lonDeg"]
        events = _events(request)
        # Searches every 48 hours cover the span: each answers for 26 hours either side of its moment
        found = []
        offset = 0.0
        while offset <= _seconds(events[-1][0], events[0][0]) + 48.0 * 3600.0:
            moment = ephemeris._shifted_iso(events[0][0], offset)
            found += [e for e in rise_transit_set(name, moment, lat, lon).events if e.utc_iso is not None]
            offset += 48.0 * 3600.0
        for printed, kind in events:
            ours = min((e for e in found if e.kind == kind), key=lambda e: abs(_seconds(printed, e.utc_iso)))
            lag = _seconds(printed, ours.utc_iso)
            (record,) = compute_solar_system_ephemerides(ours.utc_iso, lat, lon, bodies=(name,))
            if kind == "transit":
                allowance = EVENT_TOLERANCE_S + record.uncertainty_arcsec / (
                    SKY_TURN_ARCSEC_PER_S * math.cos(math.radians(record.dec_deg)))
            else:
                # USNO's 16 arcmin is the Sun's radius at about 1 au [inference]; the Moon's radius is the same here
                radius = USNO_SUN_RADIUS_DEG * abs(1.0 / record.distance_au - 1.0) if name == "Sun" else 0.0
                gap = refraction_gap + radius + record.uncertainty_arcsec / 3600.0
                allowance = EVENT_TOLERANCE_S + gap / abs(ours.altitude_rate_deg_per_min) * 60.0
            assert -allowance <= lag <= HORIZONS_STEP_S + allowance, (name, request["site"], kind, printed, lag)
            checked += 1
    assert checked == 419


def _height(name: str, iso: str, lat: float, lon: float) -> float:
    """The centre's airless altitude above USNO's threshold, from the record alone (independent of the search)."""
    (record,) = compute_solar_system_ephemerides(iso, lat, lon, bodies=(name,))
    radius = {"Sun": USNO_SUN_RADIUS_DEG}.get(name, 0.0)
    if name == "Moon":
        radius = math.degrees(math.asin(MOON_MEAN_RADIUS_KM / (record.distance_au * ephemeris.AU_KM)))
    return record.alt_deg + USNO_HORIZON_REFRACTION_DEG + radius


@pytest.mark.parametrize("name", ["Sun", "Moon", "Mars"])
def test_each_timed_rise_and_set_sits_on_usnos_threshold_and_states_its_spread(name: str) -> None:
    result = rise_transit_set(name, MOMENT, LAT, LON)
    timed = [e for e in result.events if e.kind in ("rise", "set") and e.utc_iso is not None]
    assert len(timed) >= 2
    for event in timed:
        rate_per_s = event.altitude_rate_deg_per_min / 60.0
        assert abs(_height(name, event.utc_iso, LAT, LON)) <= abs(rate_per_s) * EVENT_TOLERANCE_S
        assert (rate_per_s > 0.0) == (event.kind == "rise")
        before = _height(name, ephemeris._shifted_iso(event.utc_iso, -30.0), LAT, LON)
        after = _height(name, ephemeris._shifted_iso(event.utc_iso, 30.0), LAT, LON)
        assert event.altitude_rate_deg_per_min == pytest.approx((after - before) / 60.0 * 60.0, rel=1e-6)
        assert event.refraction_spread_s == pytest.approx(HORIZON_REFRACTION_SPREAD_DEG / abs(rate_per_s), rel=1e-12)
        assert "Schaefer and Liller 1990" in event.note
    assert result.convention == ephemeris.RISE_SET_CONVENTION and "Hills, buildings" in result.convention


def test_above_threshold_uses_solar_and_lunar_radii_and_point_planets() -> None:
    """`_above_threshold` adds USNO's 16 arcmin radius for the Sun, the Moon's topocentric radius for the Moon, and
    0 for a planet, and carries the centre's airless altitude for transit notes."""
    for name in ("Sun", "Moon", "Mars"):
        (finding,) = compute_solar_system_ephemerides(MOMENT, LAT, LON, bodies=(name,))
        got = ephemeris._above_threshold(ephemeris.BODY_BY_NAME[name], MOMENT, LAT, LON)
        assert got[0] == pytest.approx(_height(name, MOMENT, LAT, LON), abs=1e-12)
        assert got[1] == pytest.approx(math.sin(math.radians(finding.az_deg)), abs=1e-12)
        assert len(got) == 3 and got[2] == pytest.approx(finding.alt_deg, abs=1e-12)


def test_each_transit_is_the_centre_crossing_the_meridian_from_east_to_west() -> None:
    for name in ("Sun", "Moon", "Saturn"):
        transits = [e for e in rise_transit_set(name, MOMENT, LAT, LON).events if e.kind == "transit"]
        assert transits
        for event in transits:
            (centre,) = compute_solar_system_ephemerides(event.utc_iso, LAT, LON, bodies=(name,))
            assert f"about {centre.alt_deg:+.1f} degrees up (airless)" in event.note
            (before,) = compute_solar_system_ephemerides(ephemeris._shifted_iso(event.utc_iso, -EVENT_TOLERANCE_S),
                                                         LAT, LON, bodies=(name,))
            (after,) = compute_solar_system_ephemerides(ephemeris._shifted_iso(event.utc_iso, EVENT_TOLERANCE_S),
                                                        LAT, LON, bodies=(name,))
            assert math.sin(math.radians(before.az_deg)) > 0.0 >= math.sin(math.radians(after.az_deg)), event


def _lowest_sun_height(lat: float, midnight: str) -> float:
    """The Sun's lowest height above the threshold within 90 minutes of `midnight` (one-minute samples)."""
    return min(_height("Sun", ephemeris._shifted_iso(midnight, 60.0 * k), lat, 0.0) for k in range(-90, 91))


# Local midnight at longitude 0 near the December solstice (synthetic), where the Sun's lowest point sits near the
# horizon around 65.7 degrees south
MIDNIGHT = "2031-12-21T00:00:00Z"


def _latitude_with_lowest(target: float) -> float:
    """The southern latitude where the Sun's lowest height above the threshold is `target` (bisection)."""
    lo, hi = -67.5, -64.0
    for _ in range(30):
        mid = (lo + hi) / 2.0
        if _lowest_sun_height(mid, MIDNIGHT) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# The grazing band's edges, from one table: (the Sun's lowest height above the threshold, what the result holds near
# that midnight). Inside the band no time is stated; just outside it, a dip has its times and a near-miss is no event
MARGIN = 0.01
GRAZE_EDGES = [
    (-(GRAZING_DEG + MARGIN), "timed rise and set"),
    (-(GRAZING_DEG - MARGIN), "untimed rise and set"),
    (GRAZING_DEG - MARGIN, "a graze"),
    (GRAZING_DEG + MARGIN, "nothing"),
]


@pytest.mark.parametrize(("lowest", "expected"), GRAZE_EDGES)
def test_the_sun_near_the_polar_circle_at_each_edge_of_the_grazing_band(lowest: float, expected: str) -> None:
    lat = _latitude_with_lowest(lowest)
    near = [e for e in rise_transit_set("Sun", MIDNIGHT, lat, 0.0).events
            if e.kind != "transit" and (e.utc_iso is None or abs(_seconds(e.utc_iso, MIDNIGHT)) < 6 * 3600)]
    kinds = sorted((e.kind, e.utc_iso is not None) for e in near)
    if expected == "timed rise and set":
        assert kinds == [("rise", True), ("set", True)]
        assert all(e.refraction_spread_s > 60.0 and "Schaefer and Liller 1990" in e.note for e in near)
    elif expected == "untimed rise and set":
        assert ("rise", False) in kinds and ("set", False) in kinds
        assert all("No time stated" in e.note and "half the 95% range" in e.note for e in near if e.utc_iso is None)
    elif expected == "a graze":
        assert kinds == [("grazes", True)]
        assert "Its lowest point" in near[0].note and "may set briefly" in near[0].note and "no rise or set" in near[0].note
        # Its time is the turning point: the lowest of one-minute samples, sampled independently of the search
        samples = [(_height("Sun", ephemeris._shifted_iso(MIDNIGHT, 60.0 * k), lat, 0.0), 60.0 * k)
                   for k in range(-90, 91)]
        assert abs(_seconds(near[0].utc_iso, MIDNIGHT) - min(samples)[1]) <= 60.0
    else:
        assert kinds == []


def test_the_midnight_sun_and_polar_night_have_no_rise_or_set() -> None:
    events = rise_transit_set("Sun", MIDNIGHT, -80.0, 0.0).events
    assert not [e for e in events if e.kind in ("rise", "set", "grazes")]
    assert [e for e in events if e.kind == "transit"]
    north_night = rise_transit_set("Sun", MIDNIGHT, 80.0, 0.0).events
    assert not [e for e in north_night if e.kind in ("rise", "set", "grazes")]
    assert [e for e in north_night if e.kind == "transit"]


def _highest_sun_height(lat: float, noon: str) -> float:
    return max(_height("Sun", ephemeris._shifted_iso(noon, 60.0 * k), lat, 0.0) for k in range(-90, 91))


NOON = "2031-12-21T12:00:00Z"


def _latitude_with_highest(target: float) -> float:
    """The northern latitude where the Sun's highest height above the threshold near `NOON` is `target`."""
    lo, hi = 66.0, 70.0
    for _ in range(30):
        mid = (lo + hi) / 2.0
        if _highest_sun_height(mid, NOON) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


@pytest.mark.parametrize(("highest", "expected"), [
    (GRAZING_DEG + MARGIN, "timed rise and set"),
    (GRAZING_DEG - MARGIN, "untimed rise and set"),
    (-(GRAZING_DEG - MARGIN), "a graze"),
    (-(GRAZING_DEG + MARGIN), "nothing"),
])
def test_the_sun_in_polar_winter_at_each_edge_of_the_grazing_band(highest: float, expected: str) -> None:
    lat = _latitude_with_highest(highest)
    near = [e for e in rise_transit_set("Sun", NOON, lat, 0.0).events
            if e.kind != "transit" and (e.utc_iso is None or abs(_seconds(e.utc_iso, NOON)) < 6 * 3600)]
    kinds = sorted((e.kind, e.utc_iso is not None) for e in near)
    if expected == "timed rise and set":
        assert kinds == [("rise", True), ("set", True)]
        assert all(e.refraction_spread_s > 60.0 and "Schaefer and Liller 1990" in e.note for e in near)
    elif expected == "untimed rise and set":
        assert ("rise", False) in kinds and ("set", False) in kinds
        assert all("No time stated" in e.note and "half the 95% range" in e.note for e in near if e.utc_iso is None)
    elif expected == "a graze":
        assert kinds == [("grazes", True)]
        assert "Its highest point" in near[0].note and "may rise briefly" in near[0].note
    else:
        assert kinds == []


def test_equivalent_utc_writings_give_the_same_events() -> None:
    """A moment written with Z, +00:00, no offset (taken as UTC, as catalog.py does) or +02:00 gives the same events."""
    ref = rise_transit_set("Sun", "2031-08-30T03:01:00Z", LAT, LON)
    for text in ("2031-08-30T03:01:00+00:00", "2031-08-30T03:01:00", "2031-08-30T05:01:00+02:00"):
        got = rise_transit_set("Sun", text, LAT, LON)
        assert got.events == ref.events


def test_a_graze_on_the_next_night_leaves_an_earlier_clear_crossing_timed_and_keeps_the_graze() -> None:
    """When one night dips below `-GRAZING_DEG` and the next night stays just above the threshold without crossing,
    the first night keeps both its set and rise times and the second night reports its graze."""
    result = rise_transit_set("Sun", "2031-10-25T12:00:00Z", -76.89, 0.0)
    non_transits = [(e.kind, e.utc_iso is not None) for e in result.events if e.kind != "transit"]
    assert non_transits == [("set", True), ("rise", True), ("grazes", True)], result.events


def test_a_shallow_dip_under_ten_minutes_reports_untimed_set_and_rise_at_every_sample_phase() -> None:
    """A dip of `-0.002` degree lasts under ten minutes, shorter than `EVENT_SAMPLE_S`; shifting the searched moment
    across the ten-minute grid still finds the untimed set and rise on both nights in the 52-hour window."""
    lat = _latitude_with_lowest(-0.002)
    for shift in (0.0, 120.0, 240.0, 360.0, 480.0):
        moment = ephemeris._shifted_iso(MIDNIGHT, shift)
        kinds = [e.kind for e in rise_transit_set("Sun", moment, lat, 0.0).events if e.kind in ("rise", "set") and e.utc_iso is None]
        assert kinds == ["set", "rise", "set", "rise"], (shift, kinds)


def test_a_crossing_at_the_pole_whose_refraction_range_spans_more_than_a_day_states_no_time() -> None:
    """At the North Pole near the March equinox the Sun crosses USNO's threshold at 0.00027 degree per minute, so the
    95% range of measured horizon refraction (0.64 degree) spans more than a whole day; no rise time is stated."""
    result = rise_transit_set("Sun", "2031-03-19T00:00:00Z", 90.0, 0.0)
    rises = [e for e in result.events if e.kind == "rise"]
    assert len(rises) == 1
    assert rises[0].utc_iso is None and rises[0].refraction_spread_s is None
    assert "No time stated" in rises[0].note and "95% range" in rises[0].note


RTS_SPAN_EDGES = [
    ("0001-01-01T00:00:00Z", False),
    ("1800-01-02T01:59:59Z", False),
    ("1800-01-02T02:00:00Z", True),
    ("2050-12-30T21:59:59Z", True),
    ("2050-12-30T22:00:00Z", False),
    ("9999-12-31T23:00:00Z", False),
]


@pytest.mark.parametrize(("moment", "accepted"), RTS_SPAN_EDGES)
def test_the_search_holds_each_end_of_its_window_to_the_ephemeris_span(moment: str, accepted: bool) -> None:
    if not accepted:
        with pytest.raises(EphemerisOutOfRange) as caught:
            rise_transit_set("Sun", moment, LAT, LON)
        names = [entry.name for entry in caught.traceback]
        assert names[names.index("require_ephemeris_range") - 1] == "rise_transit_set", names
        return
    got = rise_transit_set("Sun", moment, LAT, LON)
    assert got.obs_utc_iso == moment


def test_an_unknown_body_is_refused_and_records_are_frozen() -> None:
    with pytest.raises(KeyError, match="Unsupported Solar System body"):
        rise_transit_set("Pluto", MOMENT, LAT, LON)
    got = rise_transit_set("Sun", MOMENT, LAT, LON)
    with pytest.raises(FrozenInstanceError):
        got.body = "Moon"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        got.events[0].kind = "none"  # type: ignore[misc]


def test_the_constants_name_their_sources() -> None:
    assert MOON_MEAN_RADIUS_KM == pose_estimate.MOON_RADIUS_KM
    assert (USNO_HORIZON_REFRACTION_DEG, USNO_SUN_RADIUS_DEG) == (34.0 / 60.0, 16.0 / 60.0)
    assert (HORIZON_REFRACTION_SPREAD_DEG, HORIZON_REFRACTION_RANGE_95_DEG) == (0.16, 0.64)
    assert GRAZING_DEG == HORIZON_REFRACTION_RANGE_95_DEG / 2.0
    assert (
        ephemeris.EVENT_SEARCH_HOURS,
        ephemeris.EVENT_SAMPLE_S,
        EVENT_TOLERANCE_S,
        ephemeris.RATE_STEP_S,
    ) == (26.0, 600.0, 1.0, 30.0)


def test_crossing_and_extremum_brackets_and_tolerances() -> None:
    """`_crossing` and `_extremum` converge to within `EVENT_TOLERANCE_S` on roots at or just past the lower sample,
    on sharp off-centre peaks and troughs, and on brackets already narrower than `EVENT_TOLERANCE_S`."""
    assert 0.0 <= ephemeris._crossing(lambda t: t, 0.0, 600.0, 0.0) <= EVENT_TOLERANCE_S
    assert 0.0 <= ephemeris._crossing(lambda t: 5e-7 - t, 0.0, 600.0, 5e-7) <= EVENT_TOLERANCE_S

    t_hi, v_hi = ephemeris._extremum(lambda t: 5.0 - abs(t - 137.25), 0.0, 1200.0, True)
    assert abs(t_hi - 137.25) <= EVENT_TOLERANCE_S and v_hi == pytest.approx(5.0, abs=EVENT_TOLERANCE_S)
    t_lo, v_lo = ephemeris._extremum(lambda t: -3.0 + abs(t - 1042.75), 0.0, 1200.0, False)
    assert abs(t_lo - 1042.75) <= EVENT_TOLERANCE_S and v_lo == pytest.approx(-3.0, abs=EVENT_TOLERANCE_S)

    assert ephemeris._extremum(lambda t: 10.0 if t < 0.25 else 1.0, 0.0, 0.5, True)[1] == 10.0
    assert ephemeris._extremum(lambda t: 1.0 if t < 0.25 else 10.0, 0.0, 0.5, True)[1] == 10.0


def test_rise_transit_set_grid_grazing_and_rate_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Grid-sample zeros, window-edge samples, flat-topped turning points, adjacent-graze isolation on both sides,
    the exact `GRAZING_DEG` edge, and the polar refraction-rate boundary hold on analytic altitude profiles."""
    span = ephemeris.EVENT_SEARCH_HOURS * 3600.0

    def run_profile(fn) -> tuple[ephemeris.SkyEvent, ...]:
        def stub(body: object, iso: str, lat_deg: float, lon_deg: float) -> tuple[float, float, float]:
            offset = _seconds(iso, MOMENT)
            h, e = fn(offset)
            return h, e, h - USNO_HORIZON_REFRACTION_DEG

        monkeypatch.setattr(ephemeris, "_above_threshold", stub)
        try:
            return rise_transit_set("Mars", MOMENT, LAT, LON).events
        except Exception as exc:  # pragma: no cover - fails cleanly if a grid index or bracket is corrupted
            pytest.fail(f"rise_transit_set raised on valid profile: {exc}")

    # 1. Exact grid-sample meridian crossing (east == 0 at offset 0) and tiny positive east (5e-7 at offset 0)
    t_exact = [e for e in run_profile(lambda t: (10.0, -t / span)) if e.kind == "transit"]
    assert len(t_exact) == 1 and abs(_seconds(t_exact[0].utc_iso, MOMENT)) <= EVENT_TOLERANCE_S
    t_tiny = [e for e in run_profile(lambda t: (10.0, 5e-7 - max(0.0, t) / 3600.0)) if e.kind == "transit"]
    assert len(t_tiny) == 1 and 0.0 <= _seconds(t_tiny[0].utc_iso, MOMENT) <= EVENT_TOLERANCE_S

    # 2. Window start k=0 inside grazing band decreasing (not an interior turn), and k=1 interior high graze
    k0_events = run_profile(lambda t: (0.20 - 10.0 * (t + span) / (2.0 * span), -1.0))
    assert [(e.kind, e.utc_iso is not None) for e in k0_events] == [("set", True)]

    def k1_high_graze(t: float) -> tuple[float, float]:
        if t < -span + 1500.0:
            return -0.15 - ((t - (-span + 600.0)) / 600.0) ** 2 * 0.85, -1.0
        return -1.0 + 6.0 * max(0.0, t - (span - 3600.0)) / 3600.0, -1.0

    k1_events = run_profile(k1_high_graze)
    k1_grazes = [e for e in k1_events if e.kind == "grazes"]
    assert len(k1_grazes) == 1 and abs(_seconds(k1_grazes[0].utc_iso, MOMENT) - (-span + 600.0)) <= EVENT_TOLERANCE_S

    # 3. Symmetric flat-topped high graze and flat-bottomed low graze across two adjacent 10-minute samples
    assert [(e.kind, e.utc_iso is not None) for e in run_profile(
        lambda t: (-0.15 - ((t - 300.0) / 600.0) ** 2 * 0.40, -1.0)
    )] == [("grazes", True)]
    assert [(e.kind, e.utc_iso is not None) for e in run_profile(
        lambda t: (0.15 + ((t - 300.0) / 600.0) ** 2 * 0.40, -1.0)
    )] == [("grazes", True)]

    # 4. Exact zero at -span, at interior sample t=0 (set), and at +span; plus shallow 5e-7 plateau crossing
    def exact_zeros(t: float) -> tuple[float, float]:
        if t <= 0.0:
            return -5.0 * (t / span) * (1.0 + t / span) * 4.0, -1.0
        return -5.0 * (t / span) * (1.0 - t / span) * 4.0, -1.0

    assert [e.kind for e in run_profile(exact_zeros)] == ["rise", "set"]
    assert [e.kind for e in run_profile(lambda t: (5e-7 if abs(t) <= 900.0 else -1.0, -1.0))] == ["rise", "set"]

    # 5. Grazing trough before crossings[0], graze on night 1 before clear night 2, and crossing/graze near +span
    def start_mid_dip(t: float) -> tuple[float, float]:
        if t < -span + 5400.0:
            return -0.15 + ((t - (-span + 1800.0)) / 1800.0) ** 2 * 0.10, -1.0
        return 5.0 - max(0.0, t - (span - 5400.0)) / 540.0, -1.0

    mid_dip_events = run_profile(start_mid_dip)
    assert [(e.kind, e.utc_iso is not None) for e in mid_dip_events] == [("rise", False), ("set", True)]
    assert _seconds(mid_dip_events[1].utc_iso, MOMENT) > 21.0 * 3600.0

    def night1_graze_then_clear_night2(t: float) -> tuple[float, float]:
        if t < -20000.0:
            return 0.10 + ((t + 60000.0) / 20000.0) ** 2 * 5.0, -1.0
        return 5.0 - (1.0 - ((t - 20000.0) / 20000.0) ** 2) * 10.0, -1.0

    assert [(e.kind, e.utc_iso is not None) for e in run_profile(night1_graze_then_clear_night2)] == [
        ("grazes", True), ("set", True), ("rise", True)
    ]

    def clear_night1_then_final_graze(t: float) -> tuple[float, float]:
        if t < 20000.0:
            return 5.0 - (1.0 - ((t + 20000.0) / 20000.0) ** 2) * 10.0, -1.0
        return 0.10 + ((t - (span - 3600.0)) / 20000.0) ** 2 * 5.0, -1.0

    assert [(e.kind, e.utc_iso is not None) for e in run_profile(clear_night1_then_final_graze)] == [
        ("set", True), ("rise", True), ("grazes", True)
    ]

    # 6. Two-sided grazing oscillation (-0.10 before rise, +0.25 after rise) names the closer 0.10 in its note
    osc = run_profile(lambda t: (0.075 + 0.175 * math.sin(t / 7200.0), -1.0))
    osc_rises = [e for e in osc if e.kind == "rise"]
    assert osc_rises and all(e.utc_iso is None and "within 0.10 degree" in e.note for e in osc_rises)

    # 7. Exact GRAZING_DEG = 0.32 edge: crossing dip to -0.32 is timed; non-crossing low at +0.32 is not a graze
    dip_exact = run_profile(lambda t: (-GRAZING_DEG + max(0.0, abs(t) - 60.0) / 1800.0, -1.0))
    assert [(e.kind, e.utc_iso is not None) for e in dip_exact] == [("set", True), ("rise", True)]
    assert list(run_profile(lambda t: (GRAZING_DEG + max(0.0, abs(t) - 60.0) / 1800.0, -1.0))) == []

    # 8. Exact polar rate boundary: |rate| * 1440 == 0.64 is untimed; 1% faster is timed
    edge_rate_per_s = HORIZON_REFRACTION_RANGE_95_DEG / 1440.0 / 60.0
    at_edge = run_profile(lambda t: (edge_rate_per_s * t, -1.0))
    assert len(at_edge) == 1 and at_edge[0].utc_iso is None and "95% range" in at_edge[0].note
    above_edge = run_profile(lambda t: (edge_rate_per_s * 1.01 * t, -1.0))
    assert len(above_edge) == 1 and above_edge[0].utc_iso is not None
