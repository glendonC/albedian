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

Every finding is `predicted`: computed from time and place, never measured in an image. It answers only for
1800-01-01 to 2050-12-31 UTC, the span the planets' documented errors cover; any other moment raises
EphemerisOutOfRange instead of an answer whose stated error would not apply.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import erfa
import numpy as np

from services.science.catalog import (
    _erfa_quiet,
    _iso_to_julian_date,
    _solar_deflection_arcsec,
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
    distance_au = float(np.linalg.norm(vector))
    distance_km = distance_au * AU_KM
    epv_factor = EPV00_FACTOR_BEFORE_1900 if tt[0] + tt[1] < _EPV00_DOCUMENTED_FROM_JD else 1.0
    earth_km = EPV00_HELIOCENTRIC_KM * epv_factor
    if body.object_type == "moon":
        theory = MOON98_DIRECTION_ARCSEC * float(np.linalg.norm(geocentric)) / distance_au
        distance_bound_km = MOON98_POSITION_KM
    elif body.object_type == "sun":
        theory = earth_km / distance_km * ARCSEC_PER_RAD
        distance_bound_km = earth_km
    else:
        lon_arcsec, lat_arcsec, radius_km = PLAN94_MAXIMUM_ERRORS[body.plan94_number]
        r_au = float(np.linalg.norm(helio))
        across_km = r_au * AU_KM * math.hypot(lon_arcsec, lat_arcsec) / ARCSEC_PER_RAD
        sin_phase = float(np.linalg.norm(np.cross(helio, vector))) / (r_au * distance_au)
        theory = (across_km + radius_km * sin_phase + earth_km) / distance_km * ARCSEC_PER_RAD
        distance_bound_km = across_km + radius_km + earth_km
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
    time_scale = 0.0
    if tt_bound_s > 0.0:
        # A TT off by the bound moves the body and the Earth alike; the place on the Earth turns with UT1, so its
        # offset from the Earth's centre stays
        unit = vector / distance_au
        offset_au = observer_au - np.asarray(earth_position_velocity(tt)[1]["p"], dtype=np.float64)
        for sign in (-1.0, 1.0):
            shifted_tt = (tt[0], tt[1] + sign * tt_bound_s / 86400.0)
            shifted_observer = np.asarray(earth_position_velocity(shifted_tt)[1]["p"], dtype=np.float64) + offset_au
            shifted, _ = _astrometric_au(body, shifted_tt, shifted_observer)
            time_scale = max(time_scale, _sep_arcsec(unit, shifted / float(np.linalg.norm(shifted))))
    deflection = _deflection_arcsec(vector, astrom)
    return _Bound(theory, deflection, rotation, height, POLAR_MOTION_BOUND_ARCSEC, time_scale, distance_bound_km)


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
                title=f"{body.name} (Predicted Ephemeris)",
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
