"""The Moon each pose path lines up is the topocentric Moon of ERFA's own chain, shifted once and never twice.

The ephemeris record is topocentric whenever a place is given (services/science/ephemeris.py), so a pose path takes it
as it is. The reference here is ERFA's own chain, built in this file from ERFA's routines and nothing of the
ephemeris module or catalog.py: the Moon from eraMoon98 and the Earth from eraEpv00 at the moment its light left
(light time by iteration from the place's barycentric position, eraApco13's `eb`), which gives the astrometric
direction from the place; and that same Moon handed to eraAtco13 as a source with its parallax, which ERFA's eraPmpx
turns into the direction from the place by subtracting the place's position (exact for a near body: it subtracts the
vectors), then light deflection, aberration, precession, nutation and the Earth's rotation, airless (no pressure),
UT1 taken as UTC. Moments from 1960, where UTC has its leap seconds and eraAtco13 reads the moment as the ephemeris
does, to 2050, where the ephemeris ends.

Covers:
- PBT over moments and places, seeded with the Moon near the horizon, where a second parallax shift is largest (about
  a degree): the head-worn pose's Moon (`pose_estimate.topocentric_moon`) and the horizon pose's Moon (the record
  `horizon_solver` asks for) lie within the topocentric record's stated uncertainty of ERFA's chain, in ICRS and in
  altitude and azimuth, and both carry that uncertainty; the head-worn pose's one frame rotation
  (`pose_estimate.pose_frame`, the rigid frame rotation anchored on the Moon) takes ERFA's own topocentric Moon to
  ERFA's observed Moon within the same, and the rigid frame rotation under it equals ERFA's bias-precession-nutation
  matrix and eraAtioq from eraApco13's own parameters
- The horizon pose at its fixture frame puts ERFA's Moon on the bright spot within its fit residual
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import math
import warnings

import erfa
from hypothesis import HealthCheck, example, given, settings, strategies as st
import numpy as np
import pytest

from services.science import horizon_solver, pose_estimate
from services.science.catalog import altaz_to_icrs, rigid_horizon_rotation
from services.science.ephemeris import compute_solar_system_ephemerides, moon_parallax_shift_arcsec

START = datetime(1960, 1, 1, tzinfo=timezone.utc)
END = datetime(2050, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
C_AU_PER_DAY = erfa.CMPS * erfa.DAYSEC / erfa.DAU
ARCSEC_PER_RAD = 180.0 / math.pi * 3600.0
# The synthetic moment of sky-3's fixtures and an open-ocean place (tests/science/test_pose_estimate.py); they point
# at nobody
FIXTURE_MOMENT, FIXTURE_LAT, FIXTURE_LON = "2031-08-30T03:01:00Z", 30.0, -40.0


def _iso(when: datetime) -> str:
    return f"{when.year:04d}{when:-%m-%dT%H:%M:%S}Z"


def _utc(moment: str) -> tuple[float, float]:
    when = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    return erfa.dtf2d("UTC", when.year, when.month, when.day, when.hour, when.minute, float(when.second))


def _unit(vector: np.ndarray) -> np.ndarray:
    return vector / float(np.linalg.norm(vector))


def _sep_arcsec(u: np.ndarray, v: np.ndarray) -> float:
    return math.atan2(float(np.linalg.norm(np.cross(u, v))), float(u @ v)) * ARCSEC_PER_RAD


def _radec(ra_deg: float, dec_deg: float) -> np.ndarray:
    return erfa.s2c(math.radians(ra_deg), math.radians(dec_deg))


def _altaz(alt_deg: float, az_deg: float) -> np.ndarray:
    a, z = math.radians(alt_deg), math.radians(az_deg)
    return np.array([math.cos(a) * math.sin(z), math.cos(a) * math.cos(z), math.sin(a)])


def erfa_topocentric_moon(moment: str, lat: float, lon: float) -> dict[str, np.ndarray]:
    """ERFA's own chain for the Moon from the place at sea level: the astrometric direction (ICRS unit vector), the
    observed airless direction (east, north, up) and the level frame's direction (the same, turned rigidly)."""
    elong, phi = math.radians(lon), math.radians(lat)
    with warnings.catch_warnings():
        # eraDat calls a moment past its table's last leap second "dubious"; it keeps the last offset, as the
        # ephemeris does
        warnings.simplefilter("ignore", erfa.ErfaWarning)
        utc = _utc(moment)
        tt = erfa.taitt(*erfa.utctai(*utc))
        astrom, _ = erfa.apco13(*utc, 0.0, elong, phi, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        place = np.asarray(astrom["eb"], dtype=np.float64)
        tau = 0.0
        for _ in range(5):
            _, earth = erfa.epv00(tt[0], tt[1] - tau)
            moon = np.asarray(earth["p"], dtype=np.float64) + np.asarray(erfa.moon98(tt[0], tt[1] - tau)["p"])
            tau = float(np.linalg.norm(moon - place)) / C_AU_PER_DAY
        rc, dc = erfa.c2s(moon)
        parallax_arcsec = 1.0 / float(np.linalg.norm(moon)) * ARCSEC_PER_RAD
        aob, zob, *_ = erfa.atco13(
            rc, dc, 0.0, 0.0, parallax_arcsec, 0.0, *utc, 0.0, elong, phi, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        )
        rigid = astrom.copy()
        rigid["diurab"] = 0.0
        ri, di = erfa.c2s(astrom["bpn"] @ _unit(moon - place))
        level_az, level_zd, *_ = erfa.atioq(erfa.anp(ri), di, rigid)
    return {
        "icrs": _unit(moon - place),
        "observed": _altaz(90.0 - math.degrees(float(zob)), math.degrees(float(aob))),
        "level": _altaz(90.0 - math.degrees(float(level_zd)), math.degrees(float(level_az))),
    }


def _sub_lunar_point(moment: str) -> tuple[float, float]:
    """Where the Moon stands at the zenith at the moment, from ERFA's geocentric Moon and the celestial-to-terrestrial
    matrix (IAU 2006/2000A, UT1 taken as UTC)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", erfa.ErfaWarning)
        utc = _utc(moment)
        tt = erfa.taitt(*erfa.utctai(*utc))
        ut1 = erfa.utcut1(*utc, 0.0)
        terrestrial = erfa.c2t06a(*tt, *ut1, 0.0, 0.0) @ np.asarray(erfa.moon98(*tt)["p"], dtype=np.float64)
    lon, lat = erfa.c2s(terrestrial)
    return math.degrees(float(lat)), (math.degrees(float(lon)) + 180.0) % 360.0 - 180.0


def _destination(lat: float, lon: float, bearing_deg: float, distance_deg: float) -> tuple[float, float]:
    p1, l1, b, d = (math.radians(v) for v in (lat, lon, bearing_deg, distance_deg))
    p2 = math.asin(max(-1.0, min(1.0, math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 180.0) % 360.0 - 180.0


def _place(moment: str, place: tuple[str, float, float]) -> tuple[float, float]:
    """A place drawn directly, or one from which the Moon stands about `alt` up (its zenith distance from the
    sub-lunar point along a bearing, on a sphere): computed at run time and never written."""
    kind, a, b = place
    if kind == "place":
        return a, b
    return _destination(*_sub_lunar_point(moment), a, 90.0 - b)


def _horizon_pose_moon(moment: str, lat: float, lon: float):
    """The Moon record the horizon pose asks for and fits to, caught as it is handed over."""
    caught = []
    real = horizon_solver.compute_solar_system_ephemerides

    def spy(*args, **kwargs):
        records = real(*args, **kwargs)
        caught.extend(r for r in records if r.name == "Moon")
        return records

    frame = np.zeros((120, 160), dtype=np.float64)
    horizon_solver.compute_solar_system_ephemerides = spy
    try:
        horizon_solver.solve_horizon_and_beacon_pose(
            frame, moment, lat, lon, source_asset_id="synthetic-moon-pose-path",
            source_sha256=hashlib.sha256(frame.tobytes()).hexdigest(),
        )
    finally:
        horizon_solver.compute_solar_system_ephemerides = real
    (moon,) = caught
    return moon


PLACES = st.one_of(
    st.tuples(st.just("place"), st.floats(-89.9, 89.9), st.floats(-180.0, 179.999)),
    st.tuples(st.just("moon_alt"), st.floats(0.0, 359.999), st.floats(-1.0, 90.0)),
)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(when=st.datetimes(min_value=START.replace(tzinfo=None), max_value=END.replace(tzinfo=None)), place=PLACES)
# The Moon near the horizon, where a second parallax shift would be largest (its whole horizontal parallax)
@example(when=datetime(2031, 8, 30, 3, 1), place=("moon_alt", 0.0, 0.5))
@example(when=datetime(2031, 8, 30, 3, 1), place=("moon_alt", 180.0, 2.0))
@example(when=datetime(2044, 1, 12, 18, 30), place=("moon_alt", 90.0, 0.2))
@example(when=datetime(2031, 8, 30, 3, 1), place=("place", FIXTURE_LAT, FIXTURE_LON))
def test_pbt_each_pose_path_lines_up_the_topocentric_moon_of_erfas_own_chain(
    when: datetime, place: tuple[str, float, float]
) -> None:
    moment = _iso(when.replace(tzinfo=timezone.utc))
    lat, lon = _place(moment, place)
    truth = erfa_topocentric_moon(moment, lat, lon)

    # The bound is the topocentric record's own: a geocentric one states a bound wide enough to hold the parallax
    (record,) = compute_solar_system_ephemerides(moment, lat, lon, bodies=("Moon",), topocentric=True)
    bound = record.uncertainty_arcsec
    head_worn = pose_estimate.topocentric_moon(moment, lat, lon)
    assert head_worn.uncertainty_deg * 3600.0 == pytest.approx(bound, rel=1e-12)
    icrs = _sep_arcsec(_radec(head_worn.ra_deg, head_worn.dec_deg), truth["icrs"])
    assert icrs <= bound, ("head-worn icrs", icrs, bound)
    observed = _sep_arcsec(_altaz(head_worn.alt_deg, head_worn.az_deg), truth["observed"])
    assert observed <= bound, ("head-worn altaz", observed, bound)
    # In the pose's frame: ERFA's own topocentric Moon turned by the rigid frame rotation, which is ERFA's rotation
    # from eraApco13's own parameters
    turned = rigid_horizon_rotation(lat, lon, moment) @ truth["icrs"]
    assert _sep_arcsec(turned, truth["level"]) < 1e-6
    # The pose draws ICRS through one rotation anchored on its Moon: ERFA's topocentric Moon turned by it is where
    # ERFA sees the Moon
    framed = pose_estimate.pose_frame(head_worn, lat, lon, moment) @ truth["icrs"]
    level = _sep_arcsec(framed, truth["observed"])
    assert level <= bound, ("head-worn pose frame", level, bound)

    horizon = _horizon_pose_moon(moment, lat, lon)
    assert horizon.frame == "topocentric" and horizon.uncertainty_arcsec == pytest.approx(bound, rel=1e-12)
    icrs = _sep_arcsec(_radec(horizon.ra_deg, horizon.dec_deg), truth["icrs"])
    assert icrs <= bound, ("horizon icrs", icrs, bound)
    observed = _sep_arcsec(_altaz(horizon.alt_deg, horizon.az_deg), truth["observed"])
    assert observed <= bound, ("horizon altaz", observed, bound)


def test_the_horizon_pose_puts_erfas_moon_on_its_bright_spot_within_its_fit_residual() -> None:
    """The fixture frame of test_ephemeris_labels.py (a bright spot over a horizon at sky-3's synthetic moment): the
    approximate pose's WCS puts ERFA's topocentric Moon within the fit's own residual limit of the spot, plus the
    record's stated uncertainty in pixels, and a Moon lowered once more would land outside three times that."""
    height, width = 1792, 1344
    frame = np.zeros((height, width), dtype=np.float64)
    frame[int(height * 0.55) :, :] = 40.0
    frame[118:126, 930:938] = 240.0
    solution = horizon_solver.solve_horizon_and_beacon_pose(
        frame, FIXTURE_MOMENT, FIXTURE_LAT, FIXTURE_LON, source_asset_id="synthetic-night",
        source_sha256=hashlib.sha256(frame.tobytes()).hexdigest(),
    )
    assert solution.status == "approximate"
    (spot,) = [f.source_detection for f in solution.findings if f.evidence_class == "observed"]
    truth = erfa_topocentric_moon(FIXTURE_MOMENT, FIXTURE_LAT, FIXTURE_LON)["icrs"]
    ra, dec = (math.degrees(v) for v in erfa.c2s(truth))
    x, y = solution.wcs.world_to_pixel(ra % 360.0, dec)
    (moon,) = horizon_solver.compute_solar_system_ephemerides(FIXTURE_MOMENT, FIXTURE_LAT, FIXTURE_LON, bodies=("Moon",))
    allowed = horizon_solver.MAX_POSE_FIT_RESIDUAL_PX + moon.uncertainty_arcsec / 3600.0 / solution.wcs.scale_deg_per_px
    assert math.hypot(x - spot.x_px, y - spot.y_px) <= allowed, (x, y, spot.x_px, spot.y_px, allowed)
    # The check can tell: the same Moon lowered once more by its parallax shift lands outside that allowance
    shift_deg = moon_parallax_shift_arcsec(moon.distance_au, moon.alt_deg)[1] / 3600.0
    twice = solution.wcs.world_to_pixel(*altaz_to_icrs(moon.alt_deg - shift_deg, moon.az_deg, FIXTURE_LAT,
                                                       FIXTURE_LON, FIXTURE_MOMENT))
    assert math.hypot(twice[0] - spot.x_px, twice[1] - spot.y_px) > 3.0 * allowed, (twice, allowed)
