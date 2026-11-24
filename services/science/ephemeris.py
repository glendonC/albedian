"""The Sun, the Moon and the planets Mercury to Neptune as an observer at a given place sees them, on ERFA, with
each position's error bound stated from its method's documented accuracy.

Every position comes from the IAU's ERFA routines (pyerfa; docs/architecture/THIRD_PARTY.md): the Moon from
eraMoon98 (Meeus 1998, chapter 47 in full), the planets from eraPlan94 (Simon et al. 1994), the Earth and the Sun
from eraEpv00 (a simplified VSOP2000). Each body is seen from the given place at sea level (topocentric: parallax
applied), with light time; only a caller that asks for `topocentric=False` gets it from the Earth's centre. Every
record states its frame in its `frame` field and in its limitation text, so no caller assumes it.
Its right ascension and declination are astrometric ICRS (light time only, as JPL Horizons' astrometric place is),
and its altitude and azimuth are that direction's at the place, apparent (light deflection by the Sun, annual and
diurnal aberration, IAU 2006/2000A precession and nutation, the Earth's rotation, through catalog.py
`icrs_to_altaz`) and airless; `refracted_alt_deg` adds Saemundsson's refraction at 1010 hPa and 10 degrees C
(catalog.py `refraction_deg`). The time scales follow ERFA's rules (services/science/catalog.py `time_scales`). No JPL Horizons request and no DE440 file is involved at
run time; tests/integration/test_ephemeris_horizons.py checks every body against stored Horizons answers.

The same vectors give the Moon's phase (`moon_phase`), the planets' apparent magnitudes (`apparent_magnitude`,
Mallama and Hilton 2018) and rise, transit and set by USNO's convention (`rise_transit_set`), each with its
method's stated limits (docs/science/EPHEMERIS_PHASE.md).

Every finding is `predicted`: computed from time and place, never measured in an image. It answers only for
1800-01-01 to 2050-12-31 UTC, the span the planets' documented errors cover; any other moment raises
EphemerisOutOfRange instead of an answer whose stated error would not apply.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import math

import erfa
import numpy as np

from services.science.catalog import (
    _erfa_quiet,
    _iso_to_julian_date,
    _solar_deflection_arcsec,
    _utc_datetime,
    earth_position_velocity,
    icrs_to_altaz,
    observer_astrom,
    ra_dec_to_unit_vector,
    refraction_deg,
    require_place,
    time_scales,
    unit_vector_to_ra_dec,
)
from services.science.wcs_models import require_source_sha256

AU_PER_PARSEC = 206264.80624709636
AU_KM = erfa.DAU / 1000.0
C_AU_PER_DAY = erfa.CMPS * 86400.0 / erfa.DAU
# WGS84's equatorial radius (eraEform), the Earth's radius the parallax shift below uses
EARTH_EQUATORIAL_RADIUS_KM = 6378.137
ARCSEC_PER_RAD = 180.0 * 3600.0 / math.pi
# The Earth's rotation per second of UT1: eraEra00's rate, 1.00273781191135448 turns per UT1 day
EARTH_ROTATION_ARCSEC_PER_S = 1296000.0 * 1.00273781191135448 / 86400.0
EARTH_ROTATION_RAD_PER_S = 2.0 * math.pi * 1.00273781191135448 / 86400.0

# The theories' documented accuracy, from their notes as pyerfa 2.0.1.5 ships them (ERFA 2.0.1, read 2026-11-11):
# - eraMoon98, note 3: "Comparisons with ELP/MPP02 over the interval 1950-2100 ... The worst case errors were 18.3
#   arcsec in geocentric direction, 31.7 km in position". Its notes give nothing before 1950; the same bound is used
#   from 1800 [proposed], and at the 116 stored JPL Horizons instants before 1950 the Moon stays within 8.8 arcsec of
#   Horizons (12.1 after 1950) [tested: tests/integration/test_ephemeris_horizons.py]
MOON98_DIRECTION_ARCSEC = 18.3
MOON98_POSITION_KM = 31.7
# - eraPlan94, note 5: the maximum heliocentric errors in longitude (arcsec), latitude (arcsec) and distance (km), the
#   larger of its two tables (Simon et al. against DE102 over 1800-2050, and against DE200 over 1800-2100), by
#   eraPlan94's planet number
PLAN94_MAXIMUM_ERRORS: dict[int, tuple[float, float, float]] = {
    1: (7.0, 1.0, 500.0),
    2: (7.0, 1.0, 1100.0),
    4: (26.0, 1.0, 9000.0),
    5: (78.0, 6.0, 82000.0),
    6: (87.0, 14.0, 267000.0),
    7: (86.0, 7.0, 712000.0),
    8: (11.0, 2.0, 253000.0),
}
# - eraEpv00, note 4: against DE405 over 1900-2100, at most 11.2 km in the Earth's heliocentric position; "by 1800
#   and 2200 the position errors are approximately double". Before 1900 the bound is three times it [proposed: above
#   "approximately double"]
EPV00_HELIOCENTRIC_KM = 11.2
EPV00_FACTOR_BEFORE_1900 = 3.0
_EPV00_DOCUMENTED_FROM_JD = 2415020.5  # 1900-01-01 TT, near enough for a factor
# The observer is taken at sea level. The bound covers a place up to 9 km above it [proposed: above the highest land,
# 8,849 m]; each km moves the Moon by up to 0.6 arcsec
MAX_OBSERVER_HEIGHT_KM = 9.0
# Polar motion is left out (xp = yp = 0): the pole has stayed within about 0.6 arcsec of its mean place in the IERS
# series [inference], so 1 arcsec covers it in any direction [proposed]
POLAR_MOTION_BOUND_ARCSEC = 1.0
# Topocentric whenever a place is given: position, altitude, azimuth and distance as seen from that place (the
# lead's answer of 2026-11-11). The geocentric frame is only for a caller that asks for it (topocentric=False)
TOPOCENTRIC_BY_DEFAULT = True


@dataclass(frozen=True)
class Body:
    """One body the ephemeris answers for: its name, its kind, its JPL Horizons id (for the stored check values), and
    for a planet its eraPlan94 number."""

    name: str
    object_type: str
    horizons_id: str
    plan94_number: int | None = None


# The one table of bodies (every caller imports it): the Sun, the Moon, and the planets Mercury to Neptune
BODIES: tuple[Body, ...] = (
    Body("Sun", "sun", "10"),
    Body("Moon", "moon", "301"),
    Body("Mercury", "planet", "199", 1),
    Body("Venus", "planet", "299", 2),
    Body("Mars", "planet", "499", 4),
    Body("Jupiter", "planet", "599", 5),
    Body("Saturn", "planet", "699", 6),
    Body("Uranus", "planet", "799", 7),
    Body("Neptune", "planet", "899", 8),
)
BODY_BY_NAME: dict[str, Body] = {body.name: body for body in BODIES}
BODY_NAMES: tuple[str, ...] = tuple(body.name for body in BODIES)
# The bodies given when none are named, in the order they have always come: the Sun, Mercury and Neptune are asked
# for by name
DEFAULT_BODIES: tuple[str, ...] = ("Jupiter", "Uranus", "Saturn", "Mars", "Venus", "Moon")

MOON_METHOD = (
    "Meeus (1998) chapter 47 in full, as ERFA's eraMoon98, for the Moon and ERFA eraEpv00 for the Earth, with light "
    "time"
)
PLANET_METHOD = (
    "ERFA eraPlan94 (Simon et al. 1994) for the planet and eraEpv00 (simplified VSOP2000) for the Earth and the Sun, "
    "with light time"
)
SUN_METHOD = "ERFA eraEpv00 (simplified VSOP2000) for the Earth and the Sun, with light time"


@dataclass(frozen=True)
class EphemerisFinding:
    """A predicted Solar System body: where the method puts it at the moment, with its error bounds.

    `ra_deg` and `dec_deg` are astrometric ICRS and `distance_au` the light-time distance, seen from `frame`
    ("topocentric": the place at sea level; "geocentric": the Earth's centre). `alt_deg` and `az_deg` are apparent and
    airless; `refracted_alt_deg` adds refraction at 1010 hPa and 10 degrees C. `uncertainty_arcsec` bounds the error
    of both the direction and the altitude and azimuth, and the distance bounds the distance's, from the documented
    accuracies above computed for this moment; `method` names the method and `limitations` what it assumes.
    """

    id: str
    observation_solution_id: str | None
    name: str
    object_type: str
    evidence_class: str
    title: str
    subtitle: str
    ra_deg: float
    dec_deg: float
    alt_deg: float
    az_deg: float
    distance_au: float
    distance_lower_au: float
    distance_upper_au: float
    distance_pc: float
    uncertainty_arcsec: float
    cartesian_parsec_icrs: tuple[float, float, float]
    obs_utc_iso: str
    method: str
    limitations: tuple[str, ...]
    frame: str = "topocentric"
    refracted_alt_deg: float = float("nan")

    def to_contract_dict(self, *, source_sha256: str | None) -> dict:
        """Format as @albedian/contracts CelestialFinding dictionary. No catalog reference: no catalog
        stands behind a position computed from a model.

        `source_sha256` is the SHA-256 of the recording the prediction was made for, or None when it was made
        for a time and place alone; there is no default, and None leaves the key out rather than invent one.
        `observationSolutionId` is included only when `observation_solution_id` is not None.
        """
        solution_id = (
            {} if self.observation_solution_id is None else {"observationSolutionId": self.observation_solution_id}
        )
        evidence_source = {} if source_sha256 is None else {"sourceAssetSha256": require_source_sha256(source_sha256)}
        return {
            "id": self.id,
            **solution_id,
            "evidenceClass": "predicted",
            "title": self.title,
            "subtitle": self.subtitle,
            "skyPosition": {
                "raDeg": float(self.ra_deg),
                "decDeg": float(self.dec_deg),
                "uncertaintyArcsec": float(self.uncertainty_arcsec),
                "altDeg": float(self.alt_deg),
                "azDeg": float(self.az_deg),
            },
            "inferredDistancePc": {
                "lower": float(self.distance_lower_au / AU_PER_PARSEC),
                "median": float(self.distance_pc),
                "upper": float(self.distance_upper_au / AU_PER_PARSEC),
                "method": "ephemeris_au",
            },
            "cartesianParsecICRS": [
                float(self.cartesian_parsec_icrs[0]),
                float(self.cartesian_parsec_icrs[1]),
                float(self.cartesian_parsec_icrs[2]),
            ],
            "evidence": {
                "id": f"ev-ephem-{self.name.lower()}",
                "evidenceClass": "predicted",
                "verificationMethod": self.method,
                **evidence_source,
                "distanceUncertaintyNote": (
                    f"{self.frame.capitalize()} distance {self.distance_au:.6f} AU, between "
                    f"{self.distance_lower_au:.6f} and {self.distance_upper_au:.6f} AU from the method's error bounds"
                ),
                "limitations": list(self.limitations),
                "verifiedByIndependentHoldout": False,
            },
        }


def moon_parallax_shift_arcsec(distance_au: float, alt_deg: float) -> tuple[float, float]:
    """The Moon's horizontal parallax at `distance_au`, and how far below its geocentric position an observer sees
    it at geocentric altitude `alt_deg` (the parallax times the cosine of the altitude), both in arcsec: the shift
    between a geocentric and a topocentric direction, for a caller that holds a geocentric one (a record whose `frame`
    is "geocentric").

    Never apply it to a record whose `frame` is "topocentric": that record's position, altitude, azimuth and distance
    are already as seen from the place, and a second shift puts the Moon about a degree too low."""
    parallax_arcsec = math.degrees(math.asin(EARTH_EQUATORIAL_RADIUS_KM / (distance_au * AU_KM))) * 3600.0
    return parallax_arcsec, parallax_arcsec * math.cos(math.radians(alt_deg))


class EphemerisOutOfRange(ValueError):
    """A moment outside 1800-01-01 to 2050-12-31 UTC, the span the ephemeris' method and its stated errors hold for."""


_VALID_FROM_JD = _iso_to_julian_date("1800-01-01T00:00:00Z")
_VALID_UNTIL_JD = _iso_to_julian_date("2051-01-01T00:00:00Z")


def require_ephemeris_range(obs_utc_iso: str) -> None:
    """Raise EphemerisOutOfRange for a moment outside the span the ephemeris holds for."""
    if not _VALID_FROM_JD <= _iso_to_julian_date(obs_utc_iso) < _VALID_UNTIL_JD:
        raise EphemerisOutOfRange(
            f"{obs_utc_iso} is outside 1800-01-01 to 2050-12-31 UTC, the span the ephemeris' method and its "
            "stated errors hold for."
        )


def _sep_arcsec(u: np.ndarray, v: np.ndarray) -> float:
    return math.atan2(float(np.linalg.norm(np.cross(u, v))), float(u @ v)) * ARCSEC_PER_RAD


def _barycentric_au(body: Body, tt: tuple[float, float]) -> tuple[np.ndarray, np.ndarray | None]:
    """The body's barycentric position (au, BCRS) at TT, and for a planet its heliocentric position."""
    pvh, pvb = earth_position_velocity(tt)
    if body.object_type == "moon":
        moon, _ = _erfa_quiet(erfa.moon98, *tt)
        return pvb["p"] + moon["p"], None
    sun = pvb["p"] - pvh["p"]
    if body.object_type == "sun":
        return sun, None
    planet, _ = _erfa_quiet(erfa.plan94, *tt, body.plan94_number)
    return sun + planet["p"], np.asarray(planet["p"], dtype=np.float64)


def _astrometric_au(
    body: Body, tt: tuple[float, float], observer_au: np.ndarray
) -> tuple[np.ndarray, np.ndarray | None]:
    """The body seen from `observer_au` (barycentric, at TT): its position when the light the observer receives left
    it, minus the observer's, and for a planet its heliocentric position then. Light time by iteration."""
    tau = 0.0
    for _ in range(6):
        position, helio = _barycentric_au(body, (tt[0], tt[1] - tau))
        vector = position - observer_au
        tau, previous = float(np.linalg.norm(vector)) / C_AU_PER_DAY, tau
        if abs(tau - previous) < 1e-12:
            break
    return vector, helio


def _deflection_arcsec(vector: np.ndarray, astrom: np.ndarray) -> float:
    """The light deflection by the Sun that `icrs_to_altaz` applies to this direction (catalog.py
    `solar_deflection_arcsec`: eraLdsun, as for a star at infinity, restrained near the Sun). A body nearer than the
    stars is deflected less (the Moon, 1.3 light seconds away, hardly at all), so the applied deflection is the most it
    can be wrong by; it is 0 for the Sun's own direction."""
    return _solar_deflection_arcsec(vector, astrom)


@dataclass(frozen=True)
class _Bound:
    """The parts of a body's stated uncertainty (arcsec) and its distance bound (km), for this moment."""

    theory_arcsec: float
    deflection_arcsec: float
    rotation_arcsec: float
    height_arcsec: float
    polar_motion_arcsec: float
    time_scale_arcsec: float
    distance_km: float

    @property
    def total_arcsec(self) -> float:
        return (
            self.theory_arcsec + self.deflection_arcsec + self.rotation_arcsec + self.height_arcsec
            + self.polar_motion_arcsec
            + self.time_scale_arcsec
        )


def _bound(
    body: Body,
    tt: tuple[float, float],
    vector: np.ndarray,
    helio: np.ndarray | None,
    geocentric: np.ndarray,
    observer_au: np.ndarray,
    astrom: np.ndarray,
    topocentric: bool,
    tt_bound_s: float,
    ut1_bound_s: float,
) -> _Bound:
    """The most the position can be off at this moment, from the documented accuracies (module constants):

    - theory: the Moon's documented geocentric direction error, at its distance from the place; a planet's documented
      heliocentric errors as a displacement (longitude and latitude across the orbit, distance along the Sun's line,
      which only its part across the line of sight, the sine of the phase angle, turns into direction) plus the
      Earth's heliocentric error, over the distance; the Sun's from the Earth's heliocentric error alone
    - deflection: the light deflection `icrs_to_altaz` applies as for a star (`_deflection_arcsec`)
    - rotation: UT1 - UTC up to its bound turns the sky past the horizon, and moves the place
    - height: the place up to MAX_OBSERVER_HEIGHT_KM above sea level (topocentric); from the Earth's centre, the
      place's whole offset from it (its parallax)
    - polar motion, left out
    - time scale: the body's own motion over the TT bound, measured both ways
    The parts are added: each is a worst case, and none cancels another."""
    distance_km = float(np.linalg.norm(vector)) * AU_KM
    theory, distance_bound_km = _theory_bound(body, tt, vector, helio, geocentric)
    rotation = ut1_bound_s * EARTH_ROTATION_ARCSEC_PER_S
    if topocentric:
        rotation += EARTH_EQUATORIAL_RADIUS_KM * EARTH_ROTATION_RAD_PER_S * ut1_bound_s / distance_km * ARCSEC_PER_RAD
        height = math.asin(MAX_OBSERVER_HEIGHT_KM / distance_km) * ARCSEC_PER_RAD
        distance_bound_km += MAX_OBSERVER_HEIGHT_KM
    else:
        # From the Earth's centre, the bound also holds the place's whole offset from it (its parallax), as the Sun's
        # and the planets' bounds always did, so the altitude and azimuth hold for an observer there. For the Moon that
        # is about a degree: its limitation states the shift, and topocentric=True applies it instead
        height = math.asin((EARTH_EQUATORIAL_RADIUS_KM + MAX_OBSERVER_HEIGHT_KM) / distance_km) * ARCSEC_PER_RAD
        distance_bound_km += EARTH_EQUATORIAL_RADIUS_KM + MAX_OBSERVER_HEIGHT_KM
    time_scale = _time_scale_arcsec(body, tt, vector, observer_au, tt_bound_s)
    deflection = _deflection_arcsec(vector, astrom)
    return _Bound(theory, deflection, rotation, height, POLAR_MOTION_BOUND_ARCSEC, time_scale, distance_bound_km)


def _theory_bound(
    body: Body, tt: tuple[float, float], vector: np.ndarray, helio: np.ndarray | None, geocentric: np.ndarray
) -> tuple[float, float]:
    """The theory part of `_bound`: the method's documented worst error as a direction (arcsec) and a distance (km)."""
    distance_au = float(np.linalg.norm(vector))
    distance_km = distance_au * AU_KM
    epv_factor = EPV00_FACTOR_BEFORE_1900 if tt[0] + tt[1] < _EPV00_DOCUMENTED_FROM_JD else 1.0
    earth_km = EPV00_HELIOCENTRIC_KM * epv_factor
    if body.object_type == "moon":
        return MOON98_DIRECTION_ARCSEC * float(np.linalg.norm(geocentric)) / distance_au, MOON98_POSITION_KM
    if body.object_type == "sun":
        return earth_km / distance_km * ARCSEC_PER_RAD, earth_km
    lon_arcsec, lat_arcsec, radius_km = PLAN94_MAXIMUM_ERRORS[body.plan94_number]
    r_au = float(np.linalg.norm(helio))
    across_km = r_au * AU_KM * math.hypot(lon_arcsec, lat_arcsec) / ARCSEC_PER_RAD
    sin_phase = float(np.linalg.norm(np.cross(helio, vector))) / (r_au * distance_au)
    return (across_km + radius_km * sin_phase + earth_km) / distance_km * ARCSEC_PER_RAD, across_km + radius_km + earth_km


def _time_scale_arcsec(
    body: Body, tt: tuple[float, float], vector: np.ndarray, observer_au: np.ndarray, tt_bound_s: float
) -> float:
    """The time-scale part of `_bound`: how far the body's direction moves over the TT bound, measured both ways."""
    time_scale = 0.0
    if tt_bound_s > 0.0:
        # A TT off by the bound moves the body and the Earth alike; the place on the Earth turns with UT1, so its
        # offset from the Earth's centre stays
        unit = vector / float(np.linalg.norm(vector))
        offset_au = observer_au - np.asarray(earth_position_velocity(tt)[1]["p"], dtype=np.float64)
        for sign in (-1.0, 1.0):
            shifted_tt = (tt[0], tt[1] + sign * tt_bound_s / 86400.0)
            shifted_observer = np.asarray(earth_position_velocity(shifted_tt)[1]["p"], dtype=np.float64) + offset_au
            shifted, _ = _astrometric_au(body, shifted_tt, shifted_observer)
            time_scale = max(time_scale, _sep_arcsec(unit, shifted / float(np.linalg.norm(shifted))))
    return time_scale


def compute_solar_system_ephemerides(
    obs_utc_iso: str,
    lat_deg: float,
    lon_deg: float,
    bodies: tuple[str, ...] = DEFAULT_BODIES,
    observation_solution_id: str | None = None,
    *,
    topocentric: bool = TOPOCENTRIC_BY_DEFAULT,
) -> list[EphemerisFinding]:
    """Predicted positions of the requested bodies at `obs_utc_iso` from (`lat_deg`, `lon_deg`, geodetic WGS84, east
    positive, at sea level), with each one's error bounds for the moment.

    Seen from the place itself (the default: parallax applied to the position, the altitude, the azimuth and the
    distance) or, only when `topocentric` is False, from the Earth's centre, with the altitude and azimuth of that
    direction at the place. Each record's `frame` says which. Raises EphemerisOutOfRange outside
    1800-01-01 to 2050-12-31 UTC, and KeyError for a body not in BODIES.
    """
    lat_deg, lon_deg = require_place(lat_deg, lon_deg)
    require_ephemeris_range(obs_utc_iso)
    for body_name in bodies:
        if body_name not in BODY_BY_NAME:
            raise KeyError(f"Unsupported Solar System body: {body_name}")
    scales = time_scales(obs_utc_iso)
    astrom = observer_astrom(obs_utc_iso, float(lat_deg), float(lon_deg))
    _, earth_bary = earth_position_velocity(scales.tt)
    geocentre_au = np.asarray(earth_bary["p"], dtype=np.float64)
    observer_au = np.asarray(astrom["eb"], dtype=np.float64) if topocentric else geocentre_au
    frame = "topocentric" if topocentric else "geocentric"
    common = "Predicted from the given time and place with ERFA's analytic theories; not measured in any image."

    findings: list[EphemerisFinding] = []
    for body_name in bodies:
        body = BODY_BY_NAME[body_name]
        vector, helio = _astrometric_au(body, scales.tt, observer_au)
        geocentric = vector if not topocentric else _astrometric_au(body, scales.tt, geocentre_au)[0]
        dist_au = float(np.linalg.norm(vector))
        ra_deg, dec_deg = unit_vector_to_ra_dec(vector)
        # The same transform every caller uses, so the finding's altitude and azimuth are its own RA and Dec's
        alt_deg, az_deg = icrs_to_altaz(ra_deg, dec_deg, lat_deg, lon_deg, obs_utc_iso)
        refracted_alt_deg = alt_deg + refraction_deg(alt_deg)
        bound = _bound(
            body, scales.tt, vector, helio, geocentric, observer_au, astrom, topocentric, scales.tt_bound_s,
            scales.ut1_bound_s,
        )
        method = {"moon": MOON_METHOD, "sun": SUN_METHOD}.get(body.object_type, PLANET_METHOD)

        if topocentric:
            seen_from = (
                "Seen from the given place at sea level (topocentric): its parallax is applied, and its right "
                "ascension and declination are astrometric (light time only)."
            )
        elif body.object_type == "moon":
            parallax_arcsec, shift_arcsec = moon_parallax_shift_arcsec(dist_au, alt_deg)
            seen_from = (
                f"Geocentric: from this place the Moon appears about {shift_arcsec:,.0f} arcsec lower than this "
                f"position (its horizontal parallax, {parallax_arcsec:,.0f} arcsec, times the cosine of its altitude); "
                "its uncertainty holds that shift."
            )
        else:
            seen_from = (
                "Seen from the Earth's centre: its uncertainty holds the place's parallax, so its altitude and azimuth "
                "hold for an observer at the place too."
            )
        # The UT1 bound applied to this moment: none before 1960, when the moment itself is taken as UT1
        rotation_part = (
            f"UT1 - UTC taken as 0 (within {scales.ut1_bound_s:g} s)"
            if scales.ut1_bound_s > 0.0
            else "the Earth's rotation, with the moment taken as UT1"
        )
        place_part = (
            f"the place's parallax (up to {MAX_OBSERVER_HEIGHT_KM:g} km above sea level)"
            if not topocentric
            else f"a place up to {MAX_OBSERVER_HEIGHT_KM:g} km above sea level"
        )
        limitations = (
            common,
            seen_from,
            f"Its altitude and azimuth are apparent and airless: refraction at 1010 hPa and 10 degrees C (Saemundsson "
            f"1986) would lift it to {refracted_alt_deg:+.2f} degrees, and the real air differs most near the horizon.",
            f"Its uncertainty, {bound.total_arcsec:,.1f} arcsec, adds the method's documented worst error "
            f"({bound.theory_arcsec:,.1f} arcsec here), {bound.deflection_arcsec:,.2f} for light deflection applied as "
            f"for a star, {bound.rotation_arcsec:,.1f} for {rotation_part}, {bound.height_arcsec:,.1f} for {place_part}, "
            f"{bound.polar_motion_arcsec:g} for polar motion, and {bound.time_scale_arcsec:,.1f} for the time scale.",
            scales.basis,
        )
        dist_pc = max(1e-9, dist_au / AU_PER_PARSEC)
        ux, uy, uz = ra_dec_to_unit_vector(ra_deg, dec_deg)
        bound_au = bound.distance_km / AU_KM
        findings.append(
            EphemerisFinding(
                id=f"finding-predicted-{body.name.lower()}",
                observation_solution_id=observation_solution_id,
                name=body.name,
                object_type=body.object_type,
                evidence_class="predicted",
                # The plain body name, the name a person reads or hears; the class is the evidence class
                # (the lead's ruling of 24 Nov 12:55, ephemeris-title-plain-1)
                title=body.name,
                subtitle=f"Predicted {body.object_type.capitalize()} · {dist_au:.3f} AU · Alt {alt_deg:+.1f}°",
                ra_deg=ra_deg,
                dec_deg=dec_deg,
                alt_deg=alt_deg,
                az_deg=az_deg,
                distance_au=dist_au,
                distance_lower_au=max(1e-12, dist_au - bound_au),
                distance_upper_au=dist_au + bound_au,
                distance_pc=dist_pc,
                uncertainty_arcsec=bound.total_arcsec,
                cartesian_parsec_icrs=(ux * dist_pc, uy * dist_pc, uz * dist_pc),
                obs_utc_iso=obs_utc_iso,
                method=method,
                limitations=limitations,
                frame=frame,
                refracted_alt_deg=refracted_alt_deg,
            )
        )
    return findings


# --- The Moon's phase, the planets' magnitudes, and rise, transit and set ------------------------------------------

# The Moon's phase names, in order of the synodic angle (the Moon's apparent ecliptic longitude of date less the
# Sun's): radiometry.py's convention, 45-degree sectors centred on new, first quarter, full and last quarter, whose
# names the answer contract takes
MOON_PHASE_NAMES: tuple[str, ...] = (
    "New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous", "Full Moon", "Waning Gibbous", "Last Quarter",
    "Waning Crescent",
)
PHASE_SECTOR_DEG = 45.0
PHASE_METHOD = (
    "The phase angle at the Moon between the Sun and the observer, from ERFA's light-time vectors (eraMoon98, "
    "eraEpv00); the illuminated fraction (1 + cos i) / 2 of a sphere"
)


@dataclass(frozen=True)
class MoonPhase:
    """The Moon's phase at a moment, seen from `frame` ("topocentric": the given place at sea level; "geocentric": the
    Earth's centre, when no place is given). Each `*_bound` comes from the Moon's and the Sun's stated position bounds
    (the methods' documented accuracies, as in `compute_solar_system_ephemerides`). `phase_name` is None when the
    synodic angle lies within its bound of a sector's edge, so either name could be the true one."""

    obs_utc_iso: str
    frame: str
    phase_angle_deg: float
    phase_angle_bound_deg: float
    illuminated_fraction: float
    illuminated_fraction_bound: float
    synodic_angle_deg: float
    synodic_angle_bound_deg: float
    elongation_deg: float
    phase_name: str | None
    method: str
    limitations: tuple[str, ...]


def phase_name(synodic_deg: float, bound_deg: float) -> str | None:
    """The name of the 45-degree sector (MOON_PHASE_NAMES) the synodic angle lies in, its edges at 22.5 + 45 k
    degrees, or None when it lies within `bound_deg` of an edge, so either neighbouring name could be true."""
    shifted = (synodic_deg + PHASE_SECTOR_DEG / 2.0) % 360.0
    into = shifted % PHASE_SECTOR_DEG
    if min(into, PHASE_SECTOR_DEG - into) <= bound_deg:
        return None
    return MOON_PHASE_NAMES[int(shifted // PHASE_SECTOR_DEG)]


def _place_bound_km(scales: object, topocentric: bool) -> float:
    """How far the observer's own position can be off, in km: from a place, its height up to MAX_OBSERVER_HEIGHT_KM,
    UT1 - UTC up to its bound turning it, and polar motion up to its bound moving it; nothing from the Earth's
    centre."""
    if not topocentric:
        return 0.0
    turn_rad = EARTH_ROTATION_RAD_PER_S * scales.ut1_bound_s + POLAR_MOTION_BOUND_ARCSEC / ARCSEC_PER_RAD
    return MAX_OBSERVER_HEIGHT_KM + EARTH_EQUATORIAL_RADIUS_KM * turn_rad


def _vector_bound(
    body: Body, scales: object, vector: np.ndarray, helio: np.ndarray | None, geocentric: np.ndarray,
    observer_au: np.ndarray, topocentric: bool,
) -> tuple[float, float]:
    """How far the body's astrometric vector from the observer can be off: (across, km; along, km), from the method's
    documented error and the time scale (`_bound`'s parts) and the observer's own position (`_place_bound_km`);
    unlike `_bound`, nothing that only turns the sky past the horizon."""
    distance_km = float(np.linalg.norm(vector)) * AU_KM
    theory_arcsec, theory_km = _theory_bound(body, scales.tt, vector, helio, geocentric)
    time_arcsec = _time_scale_arcsec(body, scales.tt, vector, observer_au, scales.tt_bound_s)
    place_km = _place_bound_km(scales, topocentric)
    across_km = (theory_arcsec + time_arcsec) / ARCSEC_PER_RAD * distance_km + place_km
    return across_km, theory_km + place_km


def _observer(obs_utc_iso: str, lat_deg: float | None, lon_deg: float | None) -> tuple[object, np.ndarray, np.ndarray, bool]:
    """The time scales, the geocentre and the observer (barycentric au), and whether the observer is at a place:
    latitude and longitude together, or neither (the Earth's centre)."""
    require_ephemeris_range(obs_utc_iso)
    if (lat_deg is None) != (lon_deg is None):
        raise ValueError("A place is a latitude and a longitude together, or neither.")
    scales = time_scales(obs_utc_iso)
    geocentre = np.asarray(earth_position_velocity(scales.tt)[1]["p"], dtype=np.float64)
    if lat_deg is None:
        return scales, geocentre, geocentre, False
    astrom = observer_astrom(obs_utc_iso, float(lat_deg), float(lon_deg))
    return scales, geocentre, np.asarray(astrom["eb"], dtype=np.float64), True


def _angle_deg(a: np.ndarray, b: np.ndarray) -> float:
    return math.degrees(math.atan2(float(np.linalg.norm(np.cross(a, b))), float(a @ b)))


def moon_phase(obs_utc_iso: str, lat_deg: float | None = None, lon_deg: float | None = None) -> MoonPhase:
    """The Moon's phase at `obs_utc_iso` seen from (`lat_deg`, `lon_deg`, at sea level), or from the Earth's centre
    when neither is given.

    The phase angle i is the angle at the Moon between the Sun and the observer, from the observer's astrometric
    vectors to the Moon (M) and the Sun (S): between S - M and -M. Displacements dM of the Moon and dS of the Sun
    turn -M by at most asin(|dM| / |M|) and S - M by at most asin((|dS| + |dM|) / |S - M|). The illuminated fraction
    is (1 + cos i) / 2, whose bound is (|sin i| di + di^2 / 2) / 2. The synodic angle is the Moon's ecliptic
    longitude of date (IAU 2006, eraEqec06) less the Sun's, 0 to 360; each body's across bound at its nearest
    bounded distance, over the cosine of its ecliptic latitude, bounds its longitude shift. Raises
    EphemerisOutOfRange outside the ephemeris' span."""
    if lat_deg is not None and lon_deg is not None:
        lat_deg, lon_deg = require_place(lat_deg, lon_deg)
    scales, geocentre, observer, topocentric = _observer(obs_utc_iso, lat_deg, lon_deg)
    moon_body, sun_body = BODY_BY_NAME["Moon"], BODY_BY_NAME["Sun"]
    moon, _ = _astrometric_au(moon_body, scales.tt, observer)
    sun, _ = _astrometric_au(sun_body, scales.tt, observer)
    moon_geo = moon if not topocentric else _astrometric_au(moon_body, scales.tt, geocentre)[0]
    moon_across, moon_along = _vector_bound(moon_body, scales, moon, None, moon_geo, observer, topocentric)
    sun_across, sun_along = _vector_bound(sun_body, scales, sun, None, sun, observer, topocentric)
    moon_shift_au = math.hypot(moon_across, moon_along) / AU_KM
    sun_shift_au = math.hypot(sun_across, sun_along) / AU_KM
    to_sun = sun - moon
    phase = _angle_deg(to_sun, -moon)
    phase_bound = math.degrees(
        math.asin(moon_shift_au / float(np.linalg.norm(moon)))
        + math.asin((sun_shift_au + moon_shift_au) / float(np.linalg.norm(to_sun)))
    )
    phase_rad, bound_rad = math.radians(phase), math.radians(phase_bound)
    fraction = (1.0 + math.cos(phase_rad)) / 2.0
    fraction_bound = (abs(math.sin(phase_rad)) * bound_rad + bound_rad * bound_rad / 2.0) / 2.0
    longitudes, latitudes = [], []
    for vector in (moon, sun):
        ra_rad, dec_rad = (math.radians(v) for v in unit_vector_to_ra_dec(vector))
        lon_rad, lat_rad = erfa.eqec06(*scales.tt, ra_rad, dec_rad)
        longitudes.append(math.degrees(float(lon_rad)))
        latitudes.append(float(lat_rad))
    synodic = (longitudes[0] - longitudes[1]) % 360.0
    moon_km = float(np.linalg.norm(moon)) * AU_KM
    sun_km = float(np.linalg.norm(sun)) * AU_KM
    sin_moon = moon_across / math.hypot(moon_across, moon_km - moon_along)
    sin_sun = sun_across / math.hypot(sun_across, sun_km - sun_along)
    synodic_bound = math.degrees(
        math.asin(sin_moon / math.cos(latitudes[0])) + math.asin(sin_sun / math.cos(latitudes[1]))
    )
    name = phase_name(synodic, synodic_bound)
    frame = "topocentric" if topocentric else "geocentric"
    limitations = (
        "Predicted from the given time" + (" and place" if topocentric else "") + " with ERFA's analytic theories; "
        "not measured in any image.",
        "Seen from the given place at sea level (topocentric)." if topocentric else
        "Seen from the Earth's centre (geocentric): no place was given.",
        f"Its bounds come from the Moon's and the Sun's documented position errors: the phase angle "
        f"within {phase_bound * 3600.0:,.1f} arcsec, the illuminated fraction within {fraction_bound:.2g}.",
        "The illuminated fraction is that of a sphere; the Moon's shape and surface are not modelled. The name "
        "follows 45-degree sectors of the synodic angle, a convention, not a measured quantity.",
    )
    return MoonPhase(obs_utc_iso, frame, phase, phase_bound, fraction, fraction_bound, synodic, synodic_bound,
                     _angle_deg(moon, sun), name, PHASE_METHOD, limitations)


# The planets' magnitudes: Mallama and Hilton (2018), "Computing apparent planetary magnitudes for The Astronomical
# Almanac", Astronomy and Computing 25, 10-24 (arXiv 1808.01973), read at the source 2026-11-12. For an observer on
# the Earth the paper's Almanac formulas ("Formulas for use in The Astronomical Almanac cover the planetary phase
# angles visible from Earth"): V = 5 log10(r d) + the polynomial below, r and d in au, the phase angle in degrees.
# Each is used only inside the range the paper states for it, and refused outside rather than extrapolated
MAGNITUDE_SOURCE = "Mallama and Hilton (2018), Astronomy and Computing 25, 10-24"
# Saturn's and Uranus' north poles (ICRF): the IAU 2015 report (Archinal et al. 2018), as NAIF's pck00011.tpc gives
# it, read 2026-11-12: (right ascension, its rate; declination, its rate), degrees and degrees per Julian century
SATURN_POLE = (40.589, -0.036, 83.537, -0.004)
URANUS_POLE = (257.311, 0.0, -15.175, 0.0)
# Uranus' flattening for the paper's Eq. 13, from the same report's radii (NAIF pck00011.tpc, BODY799_RADII =
# 25559 25559 24973 km): 1 - 24973 / 25559 = 0.022927. The paper prints 0.0022927, the same digits with one zero too
# many [inference: a misprint]; with it JPL Horizons' rows differ by up to 0.0021 mag, with 0.022927 by 0.0011 [tested]
URANUS_FLATTENING = 1.0 - 24973.0 / 25559.0


class MagnitudeOutOfRange(ValueError):
    """A phase angle, ring tilt or date outside the range the paper states for its formula."""


@dataclass(frozen=True)
class PlanetMagnitude:
    """A planet's apparent V magnitude at a moment (Mallama and Hilton 2018), from `frame`. `one_sigma_mag` is None:
    the paper states no residual of its formulas against observations, so no uncertainty is stated."""

    body: str
    obs_utc_iso: str
    frame: str
    magnitude: float
    phase_angle_deg: float
    equation: str
    one_sigma_mag: float | None
    limitations: tuple[str, ...]


def _pole(pole: tuple[float, float, float, float], tt: tuple[float, float]) -> np.ndarray:
    centuries = (tt[0] - 2451545.0 + tt[1]) / 36525.0
    return np.asarray(ra_dec_to_unit_vector(pole[0] + pole[1] * centuries, pole[2] + pole[3] * centuries))


def _latitude_deg(pole: np.ndarray, direction: np.ndarray) -> float:
    return math.degrees(math.asin(max(-1.0, min(1.0, float(pole @ direction) / float(np.linalg.norm(direction))))))


def _outside(what: str, value: float, rule: str) -> MagnitudeOutOfRange:
    return MagnitudeOutOfRange(f"{what} {value:.4f} is outside {rule}, the range {MAGNITUDE_SOURCE} states for it.")


def _magnitude_terms(
    name: str, phase: float, tt: tuple[float, float], to_observer: np.ndarray, to_sun: np.ndarray
) -> tuple[float, str, str]:
    """(the polynomial added to 5 log10(r d), its equation, what it leaves out) for a planet at phase angle `phase`
    degrees, with its directions to the observer and to the Sun; MagnitudeOutOfRange outside the formula's range."""
    a = phase
    if name == "Mercury":
        if not 2.0 < a < 170.0:
            raise _outside("Mercury's phase angle", a, "2 < a < 170 degrees (the observed span the paper names)")
        return (-0.613 + 6.3280e-02 * a - 1.6336e-03 * a**2 + 3.3644e-05 * a**3 - 3.4265e-07 * a**4
                + 1.6893e-09 * a**5 - 3.0334e-12 * a**6), "Eq. 2", ""
    if name == "Venus":
        if 0.0 < a <= 163.7:
            return -4.384 - 1.044e-03 * a + 3.687e-04 * a**2 - 2.814e-06 * a**3 + 8.938e-09 * a**4, "Eq. 3", ""
        if 163.7 < a < 179.0:
            return 236.05828 - 2.81914e00 * a + 8.39034e-03 * a**2, "Eq. 4", ""
        raise _outside("Venus' phase angle", a, "0 < a < 179 degrees")
    if name == "Mars":
        if not 0.0 <= a <= 50.0:
            raise _outside("Mars' phase angle", a, "a <= 50 degrees")
        left_out = (
            "Mars' corrections for the longitude of its sub-Earth meridian (an rms of about 0.035 mag over longitude, "
            "excursions up to 0.060, per the paper) and for its season (Ls) are left out: their tables are not printed "
            "in the paper."
        )
        return -1.601 + 0.02267 * a - 0.0001302 * a**2, "Eq. 6", left_out
    if name == "Jupiter":
        if not 0.0 <= a <= 12.0:
            raise _outside("Jupiter's phase angle", a, "a <= 12 degrees")
        return -9.395 - 3.7e-04 * a + 6.16e-04 * a**2, "Eq. 8", ""
    if name == "Saturn":
        pole = _pole(SATURN_POLE, tt)
        beta_e, beta_s = _latitude_deg(pole, to_observer), _latitude_deg(pole, to_sun)
        beta = math.sqrt(beta_e * beta_s) if beta_e * beta_s > 0.0 else 0.0
        if not 0.0 <= a < 6.5:
            raise _outside("Saturn's phase angle", a, "a < 6.5 degrees (the planet with its rings)")
        if not beta < 27.0:
            raise _outside("Saturn's effective ring inclination", beta, "beta < 27 degrees")
        sin_beta = math.sin(math.radians(beta))
        return -8.914 - 1.825 * sin_beta + 0.026 * a - 0.378 * sin_beta * math.exp(-2.25 * a), "Eq. 10", ""
    # Eq. 14 and Eq. 16 hold for the phase angles seen from the Earth, which no observer on the Earth can exceed (sin a
    # is at most the observer's distance from the Sun over the planet's); the paper's 3.2 and 1.9 degrees are those
    # largest angles rounded (JPL Horizons' rows reach 1.95 for Neptune), so no phase angle is refused
    if name == "Uranus":
        pole = _pole(URANUS_POLE, tt)
        graphic = [
            math.degrees(math.atan(math.tan(math.radians(_latitude_deg(pole, d))) / (1.0 - URANUS_FLATTENING) ** 2))
            for d in (to_observer, to_sun)
        ]
        return -7.110 - 8.4e-04 * (abs(graphic[0]) + abs(graphic[1])) / 2.0, "Eq. 14", ""
    if name == "Neptune":
        year = float(erfa.epj(*tt))
        if year < 1980.0:
            return -6.89, "Eq. 16 (before 1980.0)", ""
        if year <= 2000.0:
            return -6.89 - 0.0054 * (year - 1980.0), "Eq. 16 (1980.0 to 2000.0)", ""
        return -7.00, "Eq. 16 (after 2000.0)", ""
    raise KeyError(f"No magnitude formula for {name}: the planets Mercury to Neptune have one.")


def apparent_magnitude(
    name: str, obs_utc_iso: str, lat_deg: float | None = None, lon_deg: float | None = None
) -> PlanetMagnitude:
    """The apparent V magnitude of the planet `name` at `obs_utc_iso`, seen from (`lat_deg`, `lon_deg`, at sea level)
    or from the Earth's centre when neither is given: Mallama and Hilton (2018)'s formula for an observer on the
    Earth, with r the planet's distance from the Sun and d from the observer when its light left it (au), and the
    phase angle between them. Raises KeyError for a body without a formula, MagnitudeOutOfRange outside the formula's
    stated range, EphemerisOutOfRange outside the ephemeris' span."""
    if lat_deg is not None and lon_deg is not None:
        lat_deg, lon_deg = require_place(lat_deg, lon_deg)
    body = BODY_BY_NAME.get(name)
    if body is None or body.object_type != "planet":
        raise KeyError(f"No magnitude formula for {name}: the planets Mercury to Neptune have one.")
    scales, _, observer, topocentric = _observer(obs_utc_iso, lat_deg, lon_deg)
    vector, helio = _astrometric_au(body, scales.tt, observer)
    phase = _angle_deg(helio, vector)
    terms, equation, left_out = _magnitude_terms(name, phase, scales.tt, -vector, -helio)
    magnitude = 5.0 * math.log10(float(np.linalg.norm(helio)) * float(np.linalg.norm(vector))) + terms
    limitations = tuple(text for text in (
        f"{MAGNITUDE_SOURCE}, {equation}: a fit to V-band photometry from the 1950s on, for the phase angles seen "
        "from the Earth; the same formulas JPL Horizons cites for its magnitudes.",
        "No uncertainty is stated: the paper gives no residual of its formula against the observations.",
        left_out,
        "Neptune brightened from about 1980 to 2000 for a reason not known, and the paper doubts its formula's "
        "long-term predictive accuracy." if name == "Neptune" else "",
        "Seen from the given place at sea level (topocentric)." if topocentric else "Seen from the Earth's centre.",
    ) if text)
    return PlanetMagnitude(name, obs_utc_iso, "topocentric" if topocentric else "geocentric", magnitude, phase,
                           equation, None, limitations)


# Rise and set as USNO defines them (aa.usno.navy.mil/faq/RST_defs, read 2026-11-12): the upper edge of the Sun's or
# the Moon's disk on a level, sea-level horizon under average refraction, "a constant of 34 arcminutes (0.5666
# degree)", with the Sun's "average apparent radius ... (16 arcminutes)"; transit when the centre crosses the
# meridian. A planet is taken as a point at the same 34 arcmin
USNO_HORIZON_REFRACTION_DEG = 34.0 / 60.0
USNO_SUN_RADIUS_DEG = 16.0 / 60.0
# The Moon's volumetric mean radius (NASA, Moon Fact Sheet), as pose_estimate.py's MOON_RADIUS_KM
MOON_MEAN_RADIUS_KM = 1737.4
# Schaefer and Liller (1990), "Refraction near the horizon", PASP 102, 796-805, read at the source (the ADS scan)
# 2026-11-12: the rms deviation of their 144 measured refractions at the horizon is 0.16 degree, and "At the 95%
# confidence level the total refraction should vary over a range of 0.64". A measured spread, not a bound
HORIZON_REFRACTION_SPREAD_DEG = 0.16
HORIZON_REFRACTION_RANGE_95_DEG = 0.64
# A body whose highest or lowest altitude between two events lies within half that range of the threshold may, in
# real air, not rise or set at all there; no time is stated for those events [proposed: half the paper's 95% range]
GRAZING_DEG = HORIZON_REFRACTION_RANGE_95_DEG / 2.0
# The search: samples every 10 minutes over 26 hours each side of the moment (one lunar day is about 24 h 50 min),
# each crossing found to within 1 s, the altitude's rate from 30 s either side [proposed]
EVENT_SEARCH_HOURS = 26.0
EVENT_SAMPLE_S = 600.0
EVENT_TOLERANCE_S = 1.0
RATE_STEP_S = 30.0
RISE_SET_CONVENTION = (
    "USNO's convention: the upper edge of the Sun's or the Moon's disk (a planet's centre) on a level horizon at sea "
    "level, under a standard 34 arcmin of refraction; transit when the centre crosses the meridian. Hills, buildings "
    "and the real air of the night are not known."
)


@dataclass(frozen=True)
class SkyEvent:
    """One rise, set, upper transit or graze. `utc_iso` is None for a rise or set with no time stated (`note` says
    why); a graze's is the time of its turning point, which refraction does not move.
    `refraction_spread_s` is one standard deviation of the time from the measured spread of refraction at the horizon
    (Schaefer and Liller 1990) over the body's own rate of altitude change at the event: a spread, not a bound."""

    kind: str
    utc_iso: str | None
    altitude_rate_deg_per_min: float | None
    refraction_spread_s: float | None
    note: str


@dataclass(frozen=True)
class RiseTransitSet:
    """The rises, sets, upper transits and grazes of a body within EVENT_SEARCH_HOURS of a moment, seen from a place."""

    body: str
    obs_utc_iso: str
    events: tuple[SkyEvent, ...]
    convention: str
    limitations: tuple[str, ...]


def _shifted_iso(obs_utc_iso: str, seconds: float) -> str:
    return (_utc_datetime(obs_utc_iso) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _above_threshold(body: Body, iso: str, lat_deg: float, lon_deg: float) -> tuple[float, float, float]:
    """(the centre's airless altitude above the event threshold, sin of its azimuth, the centre's airless altitude):
    the threshold is minus 34 arcmin, less the Sun's 16 arcmin or the Moon's own radius from the place."""
    (finding,) = compute_solar_system_ephemerides(iso, lat_deg, lon_deg, bodies=(body.name,))
    radius = 0.0
    if body.object_type == "sun":
        radius = USNO_SUN_RADIUS_DEG
    elif body.object_type == "moon":
        radius = math.degrees(math.asin(MOON_MEAN_RADIUS_KM / (finding.distance_au * AU_KM)))
    return finding.alt_deg + USNO_HORIZON_REFRACTION_DEG + radius, math.sin(math.radians(finding.az_deg)), finding.alt_deg


def _crossing(value: object, lo: float, hi: float, f_lo: float) -> float:
    """The offset in [lo, hi] (seconds) where `value` changes sign, by bisection to EVENT_TOLERANCE_S."""
    while hi - lo > EVENT_TOLERANCE_S:
        mid = (lo + hi) / 2.0
        f_mid = value(mid)
        if (f_mid > 0.0) == (f_lo > 0.0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _extremum(value: object, lo: float, hi: float, highest: bool) -> tuple[float, float]:
    """Where (seconds) and what the highest (or lowest) value of `value` over [lo, hi] is, by golden-section search
    to 1 s."""
    sign = 1.0 if highest else -1.0
    ratio = (math.sqrt(5.0) - 1.0) / 2.0
    a, b = lo, hi
    c, d = b - ratio * (b - a), a + ratio * (b - a)
    fc, fd = sign * value(c), sign * value(d)
    while b - a > EVENT_TOLERANCE_S:
        if fc > fd:
            b, d, fd = d, c, fc
            c = b - ratio * (b - a)
            fc = sign * value(c)
        else:
            a, c, fc = c, d, fd
            d = a + ratio * (b - a)
            fd = sign * value(d)
    return (c, sign * fc) if fc > fd else (d, sign * fd)


def rise_transit_set(name: str, obs_utc_iso: str, lat_deg: float, lon_deg: float) -> RiseTransitSet:
    """Every rise, set and upper transit of the body `name` within EVENT_SEARCH_HOURS of `obs_utc_iso`, seen from
    (`lat_deg`, `lon_deg`) at sea level, by USNO's convention (RISE_SET_CONVENTION) on the topocentric, airless
    altitude of its centre. A rise or set whose neighbouring highest or lowest altitude lies within GRAZING_DEG of the
    threshold has no time (the body grazes the horizon); a graze with no crossing is listed as such. Raises
    EphemerisOutOfRange when the searched span leaves the ephemeris' span, KeyError for a body not in BODIES."""
    lat_deg, lon_deg = require_place(lat_deg, lon_deg)
    if name not in BODY_BY_NAME:
        raise KeyError(f"Unsupported Solar System body: {name}")
    body = BODY_BY_NAME[name]
    require_ephemeris_range(obs_utc_iso)
    span = EVENT_SEARCH_HOURS * 3600.0
    for edge in (-span, span):
        require_ephemeris_range(_shifted_iso(obs_utc_iso, edge))
    cache: dict[float, tuple[float, float, float]] = {}

    def sample(offset: float) -> tuple[float, float, float]:
        if offset not in cache:
            cache[offset] = _above_threshold(body, _shifted_iso(obs_utc_iso, offset), lat_deg, lon_deg)
        return cache[offset]

    def height(offset: float) -> float:
        return sample(offset)[0]

    def east(offset: float) -> float:
        return sample(offset)[1]

    steps = int(round(2.0 * span / EVENT_SAMPLE_S))
    offsets = [-span + k * EVENT_SAMPLE_S for k in range(steps + 1)]
    heights = [height(t) for t in offsets]
    turns: list[tuple[float, float, bool]] = []
    transits: list[float] = []
    for k in range(steps):
        lo, hi = offsets[k], offsets[k + 1]
        # The upper transit: the centre goes from east of the meridian to west of it (the lower one, west to east)
        if east(lo) > 0.0 >= east(hi):
            transits.append(_crossing(east, lo, hi, east(lo)))
        if 0 < k:
            is_high = heights[k] >= heights[k - 1] and heights[k] > heights[k + 1]
            is_low = heights[k] <= heights[k - 1] and heights[k] < heights[k + 1]
            if is_high or is_low:
                turns.append((*_extremum(height, offsets[k - 1], hi, is_high), is_high))
    samples = sorted([(t, h) for t, h in zip(offsets, heights)] + [(when, value) for when, value, _ in turns])
    crossings: list[tuple[float, str]] = []
    for (lo, h_lo), (hi, h_hi) in zip(samples, samples[1:]):
        if (h_lo > 0.0) != (h_hi > 0.0):
            crossings.append((_crossing(height, lo, hi, h_lo), "rise" if h_hi > 0.0 else "set"))
    events: list[tuple[float, SkyEvent]] = []
    used: set[float] = set()
    for index, (at, kind) in enumerate(crossings):
        # Only the immediately adjacent turning point on each side (before any other crossing) belongs to this crossing
        lo = crossings[index - 1][0] if index > 0 else -span
        hi = crossings[index + 1][0] if index + 1 < len(crossings) else span
        before_turns = [(when, value) for when, value, _ in turns if lo < when < at]
        after_turns = [(when, value) for when, value, _ in turns if at < when < hi]
        adjacent = before_turns[-1:] + after_turns[:1]
        near = [(when, value) for when, value in adjacent if abs(value) < GRAZING_DEG]
        used.update(when for when, _ in near)
        rate = (height(at + RATE_STEP_S) - height(at - RATE_STEP_S)) / (2.0 * RATE_STEP_S) * 60.0
        if near:
            closest = min(abs(value) for _, value in near)
            note = (f"No time stated: the body comes within {closest:.2f} degree of the "
                    f"threshold nearby, inside half the 95% range of measured horizon refraction ({GRAZING_DEG:g} "
                    "degree), so in real air it may not " + kind + " here at all.")
            events.append((at, SkyEvent(kind, None, rate, None, note)))
            continue
        if abs(rate) * 1440.0 <= HORIZON_REFRACTION_RANGE_95_DEG:
            note = (f"No time stated: at {abs(rate):.5f} degree per minute the 95% range of measured horizon "
                    f"refraction ({HORIZON_REFRACTION_RANGE_95_DEG:g} degree, Schaefer and Liller 1990) spans a "
                    "whole day or more, so in real air no single " + kind + " time is defined.")
            events.append((at, SkyEvent(kind, None, rate, None, note)))
            continue
        spread = HORIZON_REFRACTION_SPREAD_DEG / abs(rate) * 60.0
        note = (f"One standard deviation of {spread:,.0f} s from refraction at the horizon (0.16 degree rms, Schaefer "
                f"and Liller 1990) over the altitude's rate there, {abs(rate):.3f} degree per minute.")
        events.append((at, SkyEvent(kind, _shifted_iso(obs_utc_iso, at), rate, spread, note)))
    for at in transits:
        _, _, altitude = sample(at)
        events.append((at, SkyEvent("transit", _shifted_iso(obs_utc_iso, at), None, None,
                                    f"The centre crosses the meridian about {altitude:+.1f} degrees up (airless).")))
    for when, value, highest in turns:
        # A graze no rise or set already reports: the body comes near the threshold without crossing it
        if abs(value) < GRAZING_DEG and when not in used:
            word = "highest" if highest else "lowest"
            events.append((when, SkyEvent("grazes", _shifted_iso(obs_utc_iso, when), None, None,
                                          f"Its {word} point comes {value:+.2f} degree from the threshold: within half "
                                          "the 95% range of measured horizon refraction, so in real air it may "
                                          + ("rise briefly" if highest else "set briefly")
                                          + " here; no rise or set time is stated.")))
    events.sort(key=lambda item: item[0])
    limitations = (
        "Predicted from the given time and place with ERFA's analytic theories; not measured.",
        RISE_SET_CONVENTION,
        "USNO notes that even at sea in a clear sky computed rise and set times can be a minute or more off, and many "
        "minutes at high latitudes, where the body meets the horizon at a shallow angle.",
    )
    return RiseTransitSet(name, obs_utc_iso, tuple(event for _, event in events), RISE_SET_CONVENTION, limitations)
