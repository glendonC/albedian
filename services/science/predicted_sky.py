"""The sky predicted for a recording's own capture time and place (layer 1 of spec predicted-sky-for-every-upload).

Every finding made here is `predicted`: catalog tile stars carried to the date by their proper motion, and the Sun, the
Moon and five planets from the ephemeris on ERFA (`services/science/ephemeris.py`), seen from the recorded place
(topocentric), each placed for the recorded time and place by deterministic math and never measured in the
recording. They carry no detection and no pixel position. A plate solver's outcome is completed with them after the
solve, and nothing it measured changes.
When the solve is not calibrated and the camera has a head-worn profile, layer 2 lines them up over one frame by
taking its bright spot to be the Moon, with the profile's field of view and a level head
(`services/science/pose_estimate.py`): an `approximate` solution with its swept uncertainty, never checked against
stars.
docs/science/PREDICTED_SKY.md describes both layers in plain words.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import math
from typing import Any

import numpy as np

from services.science.camera_profiles import head_worn_profile
from services.science.catalog import (
    KM_S_TO_PC_YR,
    _iso_to_julian_date,
    icrs_to_altaz_batch,
    propagate_directions_batch,
    require_place,
    time_scales,
)
from services.science.catalog_model import (
    GAIA_DR3_RELEASE,
    GAIA_G,
    HIPPARCOS_2007_RELEASE,
    HIPPARCOS_HP,
    CatalogStar,
    gaia_dr3_citation,
    hipparcos_2007_tile_citation,
)
from services.science.ephemeris import (
    EARTH_ROTATION_ARCSEC_PER_S,
    POLAR_MOTION_BOUND_ARCSEC,
    EphemerisOutOfRange,
    compute_solar_system_ephemerides,
    require_ephemeris_range,
)
from services.science.phone_photo_solver import _get_tile_index, _TileStarIndex, iau_constellation_codes
from services.science.pose_estimate import (
    DAYTIME,
    MIRRORED,
    MOON_NOT_UP,
    NO_PROFILE,
    ROLL_LIMIT_DEG,
    SUNRISE_SUNSET_ALT_DEG,
    PoseResult,
    Refusal,
    is_mirrored,
    moon_is_up,
    pose_from_frames,
    sun_is_up,
)
from services.science.wcs_models import (
    CelestialFindingRecord,
    ObservationSolution,
    SolutionQuality,
    SolverIdentity,
    SourceDetection,
)

# The bound the horizon solver (services/science/horizon_solver.py) still states for its own pinned stars, set when the
# transform left out nutation and annual aberration; the predicted sky below no longer uses it [open: the horizon lane]
PREDICTED_STAR_UNCERTAINTY_ARCSEC = 55.0
# The predicted stars come from the catalog tiles' L0 and L1 rows (Gaia DR3 and Hipparcos 2007, to G 8,
# apps/web/public/catalog/stars/gaia-dr3): every row to an approximate V of 4.5 that is above the horizon, about 400 to
# 500 at a time [proposed: lists, cards and voice, where a few hundred is the most a person reads; the browser draws the
# tiles to G 12 itself], and every row the shipped IAU name file (names.json) names, whatever its magnitude, so a star a
# person names is found whenever it is up. The approximate V is the solver's ranking value (Gaia G to V by Riello et al.
# 2021, Hipparcos Hp as it is), used for the cut and the order only; each finding shows its row's own magnitude and
# band.
PREDICTED_STAR_DEPTH_V = 4.5
# A predicted star's stated uncertainty adds these parts, each a worst case, for its moment:
# - the Earth's rotation over UT1 - UTC, up to `time_scales`' bound (0.9 s from 1960, from the IERS), at eraEra00's rate
#   (EARTH_ROTATION_ARCSEC_PER_S): 13.5 arcsec
# - polar motion, left out (POLAR_MOTION_BOUND_ARCSEC, services/science/ephemeris.py): 1 arcsec
# - the star's own parallax, left out: at most the parallax of the nearest star, 1 au over the 1.2148 pc lower distance
#   of the nearest row in the tiles (Toliman, HIP 71681) [tested: tests/integration/test_predicted_sky_from_tiles.py],
#   held as 0.824 arcsec, which covers Proxima Centauri's 768 mas too [inference]
STAR_PARALLAX_BOUND_ARCSEC = 0.824
# - the transform itself: ERFA's chain as catalog.py runs it, measured at most 2.1e-10 arcsec from eraAtco13 with the
#   same inputs over 24,400 directions from 1960 to 2050 [tested], held as 0.001 arcsec [proposed]
STAR_TRANSFORM_ARCSEC = 0.001
# - the proper motion as the tiles store it: a half float (services/science/catalog_tiles.py), off by at most 2^-11 of
#   its value, over the years since the catalog epoch
HALF_FLOAT_RELATIVE_ERROR = 2.0**-11
# - a row with no proper motion is not moved: it may have moved up to the fastest proper motion in the L0 and L1 rows,
#   7,059.9 mas/yr (Gaia DR3 4034171629042489088), held as 7,060, over the years since [proposed: a row without a
#   value moves no faster than the fastest row with one]
FASTEST_PROPER_MOTION_MAS_YR = 7060.0
# - the radial motion's turn of the direction (perspective): the row's own radial velocity, or for a row without one the
#   fastest in the L0 and L1 rows, 387.5 km/s (Gaia DR3 4321939179572488960), held as 388 [proposed, as above], at
#   the nearest a star can be (1 au over the parallax bound), times the proper motion's path
FASTEST_RADIAL_VELOCITY_KM_S = 388.0
# - the light time across that radial motion, which ERFA's space motion (eraStarpm, eraPmsafe) counts and the linear
#   motion here does not: the proper motion's path times the radial speed over the speed of light (ERFA_CMPS)
SPEED_OF_LIGHT_KM_S = 299792.458
# The catalog's own position and proper motion errors are not in the tiles and are not in the sum [open]
PREDICTED_BODIES = ("Sun", "Moon", "Venus", "Mars", "Jupiter", "Saturn", "Uranus")

# The Sun's centre altitude at the edges of day and twilight, from USNO's "Rise, Set, and Twilight Definitions"
# (aa.usno.navy.mil/faq/RST_defs): sunrise and sunset at a geometric zenith distance of 90.8333 degrees, the centre
# 50 arcminutes below the horizon (16 for the Sun's radius and 34 for refraction), SUNRISE_SUNSET_ALT_DEG, kept in
# pose_estimate.py, whose daytime refusal uses the same limit; civil, nautical and astronomical twilight end at
# zenith distances of 96, 102 and 108 degrees
CIVIL_TWILIGHT_ALT_DEG = -6.0
NAUTICAL_TWILIGHT_ALT_DEG = -12.0
ASTRONOMICAL_TWILIGHT_ALT_DEG = -18.0

# The reasons there is no predicted sky. None holds the capture time, the place or the file name (REQ-SKY-2.4)
NO_TIME = "The file has no recorded capture time, so no sky can be predicted for it."
NO_PLACE = "The file has no recorded place, so no sky can be predicted for it."
NO_TIME_OR_PLACE = "The file has no recorded capture time and no recorded place, so no sky can be predicted for it."
OUT_OF_RANGE = (
    "The recorded capture time lies outside 1800 to 2050, the years the ephemeris holds for, so no sky can be "
    "predicted for it."
)
# Only a plate solver's outcomes are completed; a pose's (approximate) comes with its own predicted sky
COMPLETED_STATUSES = ("calibrated", "ambiguous", "unresolved")
# How far the sky turns in the horizon frame per second of time: 360 degrees per sidereal day of 86,164.0905 s (IERS
# Conventions 2010, the Earth's rotation rate), an upper bound for any star, reached on the celestial equator
SKY_TURN_ARCSEC_PER_SEC = 360 * 3600 / 86164.0905


def sky_turn_deg(seconds: float) -> float:
    """How far the sky turns, in degrees, in `seconds` of time (SKY_TURN_ARCSEC_PER_SEC over the 3,600 arcsec of a
    degree): one degree in a 360th of a sidereal day."""
    return SKY_TURN_ARCSEC_PER_SEC * seconds / 3600


def _worked_out_time_sec(asset: Mapping[str, Any]) -> float | None:
    """The capture instant's stated uncertainty in seconds when its time zone was taken from the GPS position's time
    zone, which agrees with the GPS time stamp (services/ingest/metadata.py); None when the file gave its own offset."""
    source = (asset.get("metadataSources") or {}).get("capturedAtUTC") or {}
    basis = source.get("basis") or {}
    worked_out = basis.get("offsetSource") == "place_time_zone_agrees_with_gps_time_stamp"
    return float(basis["uncertaintySec"]) if worked_out else None


def time_zone_from_gps(uncertainty_sec: float) -> str:
    """The sentence for a capture time whose time zone was taken from the GPS position and agrees with the GPS time
    stamp. It holds no time, place, zone or file name. It says the zone agrees, not that the instant is confirmed:
    the stamp bounds only how far the fix's age and the camera clock's error differ, so an old fix can hide a wrong
    clock, as nothing checks a tagged photo's clock either."""
    return (
        "The file names no time zone for its capture time: it was taken from the time zone at the photo's GPS "
        "position, which agrees with its GPS time stamp. The capture time is taken to be within "
        f"{uncertainty_sec:g} s, a second plus how far the camera's clock and the GPS time stamp differ, which can "
        f"move the predicted sky by up to {SKY_TURN_ARCSEC_PER_SEC * uncertainty_sec:.0f} arcsec; an old GPS fix "
        "can hide a wrong camera clock."
    )


STAR_POSITION_LIMITATION = (
    "Placed by ERFA's apparent place for the recorded place at sea level (catalog.py icrs_to_altaz). Its stated "
    "uncertainty adds UT1 - UTC (up to 13.5 arcsec), polar motion, its own parallax, which is left out, the stored "
    "precision of its motion, and the motion a row without a proper motion or radial velocity may have, up to the "
    "fastest star in the tiles; the catalog's own position and motion errors are not in it. Its altitude leaves out "
    "refraction, which lifts what is near the horizon."
)


def frame_moment(captured_at_utc: str, frame_time_sec: float) -> str:
    """The moment of the frame `frame_time_sec` into a recording that started at `captured_at_utc`, in the
    contract's UTC form: whole seconds, with milliseconds only when the moment has a fraction of a second.

    `captured_at_utc` is an ISO 8601 instant ending in Z, with or without a fraction of a second; anything else is
    refused, and the message never holds the moment."""
    if not isinstance(captured_at_utc, str) or not captured_at_utc.endswith("Z"):
        raise ValueError("The capture time must be a UTC instant ending in Z.")
    try:
        start = datetime.fromisoformat(captured_at_utc[:-1] + "+00:00")
    except ValueError:
        raise ValueError("The capture time is not an ISO 8601 instant.") from None
    moment = (start.astimezone(timezone.utc) + timedelta(seconds=float(frame_time_sec))).replace(tzinfo=None)
    # isoformat writes four-digit years on every platform; strftime's %Y leaves a year below 1000 to the platform
    if moment.microsecond == 0:
        return moment.isoformat(timespec="seconds") + "Z"
    return moment.isoformat(timespec="milliseconds") + "Z"


@dataclass(frozen=True)
class SkyPhase:
    """Where the Sun was, as a USNO band (`day`, `civil_twilight`, `nautical_twilight`, `astronomical_twilight`,
    `night`) and one plain sentence for the person who recorded it."""

    band: str
    sentence: str


def _degrees(value: int) -> str:
    return f"{value} degree" if value == 1 else f"{value} degrees"


def sky_phase(sun_alt_deg: float) -> SkyPhase:
    """The USNO band of the Sun's geometric centre altitude and a sentence naming it, with the altitude rounded to a
    whole degree. A pure function of the altitude: the sentence holds no time, place or other number of the moment."""
    if not math.isfinite(sun_alt_deg) or not -90.0 <= sun_alt_deg <= 90.0:
        raise ValueError("The Sun's altitude must be a finite number of degrees between -90 and 90.")
    whole = math.floor(abs(sun_alt_deg) + 0.5)
    if sun_alt_deg > SUNRISE_SUNSET_ALT_DEG:
        where = (
            f"up, about {_degrees(whole)} above the horizon"
            if sun_alt_deg >= 0.5
            else "on the horizon, rising or setting"
        )
        return SkyPhase(
            "day",
            f"The Sun was {where}: daytime, by the USNO definitions, so the stars listed were above the horizon but "
            "outshone by daylight.",
        )
    below = f"about {_degrees(whole)} below the horizon"
    if sun_alt_deg >= CIVIL_TWILIGHT_ALT_DEG:
        return SkyPhase(
            "civil_twilight",
            f"The Sun was {below}: civil twilight, by the USNO definitions (its centre 50 arcminutes to 6 degrees "
            "below), when sunlight scattered in the upper atmosphere still lights the sky.",
        )
    if sun_alt_deg >= NAUTICAL_TWILIGHT_ALT_DEG:
        return SkyPhase(
            "nautical_twilight",
            f"The Sun was {below}: nautical twilight, by the USNO definitions (6 to 12 degrees below), when the "
            "horizon can still be seen.",
        )
    if sun_alt_deg >= ASTRONOMICAL_TWILIGHT_ALT_DEG:
        return SkyPhase(
            "astronomical_twilight",
            f"The Sun was {below}: astronomical twilight, by the USNO definitions (12 to 18 degrees below), when "
            "scattered sunlight is not yet fainter than starlight.",
        )
    return SkyPhase(
        "night",
        f"The Sun was {below}: night, by the USNO definitions (more than 18 degrees below), when scattered sunlight "
        "is fainter than starlight.",
    )


def _julian_year(moment: str) -> float:
    return 2000.0 + (_iso_to_julian_date(moment) - 2451545.0) / 365.25


def _star_without_distance(index: _TileStarIndex, row: int) -> CatalogStar:
    """The star of a tile row that gives no distance (`to_catalog_star` refuses one), holding only what the row gives:
    no distance, interval or method, and no citation for one."""
    is_hip = bool(index.data["hip"][row])
    sid = int(index.data["sid"][row])
    source = f"HIP {sid}" if is_hip else f"Gaia DR3 {sid}"
    if source in index._pinned_by_source:
        raise ValueError(f"{source}: a pinned star's row gives a distance")
    bp_rp = None if bool(index.data["color_bv"][row]) else index._row_value("color", row)
    rv = index._row_value("rv", row)
    band = HIPPARCOS_HP if bool(index.data["mag_hp"][row]) else GAIA_G
    ra0, dec0 = float(index.data["ra0"][row]), float(index.data["dec0"][row])
    return CatalogStar(
        id=f"hip-{sid}" if is_hip else f"gaia-dr3-{sid}",
        common_name=source,
        bayer_designation=None,
        constellation=iau_constellation_codes(np.array([ra0]), np.array([dec0]))[0],
        provider="Hipparcos" if is_hip else "Gaia DR3",
        release_version=HIPPARCOS_2007_RELEASE if is_hip else GAIA_DR3_RELEASE,
        catalog_source_id=source,
        ra_deg=ra0 % 360.0,
        dec_deg=dec0,
        mag=float(index.data["mag"][row]),
        bp_rp=bp_rp,
        spectral_type=None,
        pm_ra_mas_yr=index._row_value("pm_ra", row),
        pm_dec_mas_yr=index._row_value("pm_dec", row),
        rv_km_s=rv,
        parallax_mas=None,
        parallax_error_mas=None,
        lower_pc=None,
        median_pc=None,
        upper_pc=None,
        distance_method=None,
        citation=(
            hipparcos_2007_tile_citation(magnitude_band=band, distance=False)
            if is_hip
            else gaia_dr3_citation(
                color=bp_rp is not None, radial_velocity=rv is not None, distance_method=None, magnitude_band=band
            )
        ),
        epoch_year=index.epoch_jyear,
        magnitude_band=band,
    )


@dataclass(frozen=True)
class _SkyRows:
    """The tile rows the predicted sky draws from, brightest first, each as its star at the catalog epoch, with the
    names file's designation for it (None without one)."""

    rows: np.ndarray
    stars: tuple[CatalogStar, ...]
    designations: tuple[str | None, ...]


@lru_cache(maxsize=1)
def _sky_rows() -> _SkyRows:
    """Every L0 and L1 row to PREDICTED_STAR_DEPTH_V or named in names.json, ordered by approximate V then id.

    Every catalog value comes from the row (`to_catalog_star`, or `_star_without_distance` for a row with no
    distance). The names are the shipped IAU name file's alone, for a pinned star too: the IAU name as the star's
    name and its constellation, else the IAU boundary that holds the star; a star the file does not name is called by
    its catalog id. The file's designation is mostly an HR number ("HR 2326"), not a Bayer designation, so it is kept
    apart for the subtitle's text and the star's Bayer designation stays None: the contract has no field for an HR
    number, and the file gives the Bayer letter only abbreviated. A pinned star keeps its pinned id, the id a plate
    solver's match of it carries."""
    index = _get_tile_index()
    data = index.data
    named = np.array(
        [(bool(h), int(i)) in index._names_by_source for h, i in zip(data["hip"], data["sid"], strict=True)]
    )
    chosen = np.flatnonzero((data["v_mag"] <= PREDICTED_STAR_DEPTH_V) | named)
    stars, designations = [], []
    for row in chosen.tolist():
        ra0, dec0 = float(data["ra0"][row]), float(data["dec0"][row])
        star = (
            index.to_catalog_star(row, ra0, dec0, index.epoch_jyear)
            if index.has_distance(row)
            else _star_without_distance(index, row)
        )
        entry = index._names_by_source.get((bool(data["hip"][row]), int(data["sid"][row])))
        if entry is None:
            name, designation = star.catalog_source_id, None
            constellation = iau_constellation_codes(np.array([ra0]), np.array([dec0]))[0]
        else:
            name, designation = entry["name"], entry.get("designation") or None
            constellation = entry.get("constellation") or iau_constellation_codes(np.array([ra0]), np.array([dec0]))[0]
        stars.append(replace(star, common_name=name, bayer_designation=None, constellation=constellation))
        designations.append(designation)
    order = sorted(range(len(stars)), key=lambda k: (float(data["v_mag"][chosen[k]]), stars[k].id))
    return _SkyRows(
        rows=chosen[order], stars=tuple(stars[k] for k in order), designations=tuple(designations[k] for k in order)
    )


def predicted_star_uncertainty_arcsec(
    pm_ra_mas_yr: np.ndarray, pm_dec_mas_yr: np.ndarray, rv_km_s: np.ndarray, years: float, ut1_bound_s: float
) -> np.ndarray:
    """The stated uncertainty of each predicted star `years` from its catalog epoch, in arcsec: the parts listed with
    the constants above, added. NaN marks a value the row lacks."""
    years = abs(float(years))
    pm = np.hypot(np.asarray(pm_ra_mas_yr, dtype=np.float64), np.asarray(pm_dec_mas_yr, dtype=np.float64))
    moves = np.isfinite(pm)
    stored = np.where(moves, pm * HALF_FLOAT_RELATIVE_ERROR * years / 1000.0, 0.0)
    unmoved = np.where(moves, 0.0, FASTEST_PROPER_MOTION_MAS_YR * years / 1000.0)
    rv = np.asarray(rv_km_s, dtype=np.float64)
    speed = np.where(np.isfinite(rv), np.abs(rv), FASTEST_RADIAL_VELOCITY_KM_S)
    nearest_pc = 1.0 / STAR_PARALLAX_BOUND_ARCSEC
    turn = speed * KM_S_TO_PC_YR * years / nearest_pc
    path = np.where(moves, pm, FASTEST_PROPER_MOTION_MAS_YR) * years / 1000.0
    perspective = path * turn / np.maximum(1.0 - turn, 1e-9)
    light_time = path * speed / SPEED_OF_LIGHT_KM_S
    return (
        ut1_bound_s * EARTH_ROTATION_ARCSEC_PER_S
        + POLAR_MOTION_BOUND_ARCSEC
        + STAR_PARALLAX_BOUND_ARCSEC
        + STAR_TRANSFORM_ARCSEC
        + stored
        + unmoved
        + perspective
        + light_time
    )


def _star_subtitle(star: CatalogStar, designation: str | None, alt: float, az: float) -> str:
    """Constellation, the name file's designation, the row's distance where it gives one, and where the star is."""
    parts = [f"Predicted Star ({star.constellation})"]
    if designation is not None and designation != star.common_name:
        parts.append(designation)
    if star.has_distance:
        parts.append(f"{star.median_pc:.1f} pc")
    parts += [f"Alt {alt:+.1f}° Az {az:.1f}°", "Above Horizon"]
    return " · ".join(parts)


def _star_limitations(
    star: CatalogStar, catalog_epoch: float, time_said: str
) -> tuple[str, ...]:
    moved = star.pm_ra_mas_yr is not None and star.pm_dec_mas_yr is not None
    return (
        f"Predicted from its {star.release_version} catalog row (epoch J{catalog_epoch:.1f}), "
        + ("moved by its proper motion to the date" if moved else "not moved, since the row has no proper motion")
        + f", for {time_said} and place; not measured in the recording.",
        f"Apparent magnitude is {star.magnitude_band}, not Johnson V.",
        *(() if star.has_distance else ("Its catalog row gives no distance, so none is shown.",)),
        STAR_POSITION_LIMITATION,
    )


def predicted_sky_findings(
    moment: str,
    lat_deg: float,
    lon_deg: float,
    *,
    solution_id: str,
    source_sha256: str,
    exclude_ids: frozenset[str] = frozenset(),
    time_uncertainty_sec: float | None = None,
) -> list[CelestialFindingRecord]:
    """The Sun, the Moon, the five planets and the catalog tile stars to PREDICTED_STAR_DEPTH_V or named (stars
    carried to the date by their proper motion) above the horizon at `moment` from (`lat_deg`, `lon_deg`), as
    predicted findings of the solution `solution_id`: bodies first in `PREDICTED_BODIES` order, then stars brightest
    first. The stars are placed in one pass (`propagate_directions_batch`, `icrs_to_altaz_batch`).

    Above the horizon means a geometric (airless) altitude above 0. `exclude_ids` names catalog stars to leave
    out, by catalog id. `time_uncertainty_sec`, for a capture time whose time zone was worked out from the GPS position
    and time stamp, is added to each uncertainty as the sky's turn in that time and named in each limitation. Raises
    EphemerisOutOfRange outside 1800 to 2050."""
    lat_deg, lon_deg = require_place(lat_deg, lon_deg)
    require_ephemeris_range(moment)
    findings: list[CelestialFindingRecord] = []
    time_arcsec = 0.0 if time_uncertainty_sec is None else SKY_TURN_ARCSEC_PER_SEC * time_uncertainty_sec
    time_said = (
        "the recorded capture time"
        if time_uncertainty_sec is None
        else "the recorded capture time, its time zone taken from the GPS position and agreeing with the GPS time stamp,"
    )
    for body in compute_solar_system_ephemerides(
        moment, lat_deg, lon_deg, bodies=PREDICTED_BODIES, observation_solution_id=solution_id
    ):
        if body.alt_deg <= 0.0:
            continue
        findings.append(
            CelestialFindingRecord(
                id=body.id,
                observation_solution_id=solution_id,
                evidence_class="predicted",
                title=body.title,
                subtitle=f"{body.subtitle} · Above Horizon",
                ra_deg=body.ra_deg,
                dec_deg=body.dec_deg,
                alt_deg=body.alt_deg,
                az_deg=body.az_deg,
                uncertainty_arcsec=body.uncertainty_arcsec + time_arcsec,
                verified_by_holdout=False,
                limitations=(
                    f"Predicted with {body.method}, for {time_said} and place; not measured in the recording.",
                    *body.limitations[1:],
                ),
                source_sha256=source_sha256,
            )
        )
    year = _julian_year(moment)
    index = _get_tile_index()
    sky = _sky_rows()
    data, rows = index.data, sky.rows
    years = year - index.epoch_jyear
    distance = np.where(data["valid_dist"][rows], data["med_pc"][rows], np.nan)
    ra, dec = propagate_directions_batch(
        data["ra0"][rows], data["dec0"][rows], data["pm_ra"][rows], data["pm_dec"][rows], data["rv"][rows], distance,
        years,
    )
    alt, az = icrs_to_altaz_batch(ra, dec, lat_deg, lon_deg, moment)
    uncertainty = predicted_star_uncertainty_arcsec(
        data["pm_ra"][rows], data["pm_dec"][rows], data["rv"][rows], years, time_scales(moment).ut1_bound_s
    )
    for k in np.flatnonzero(alt > 0.0).tolist():
        if sky.stars[k].id in exclude_ids:
            continue
        star = replace(sky.stars[k], ra_deg=float(ra[k]), dec_deg=float(dec[k]), epoch_year=year)
        findings.append(
            CelestialFindingRecord(
                id=f"finding-predicted-{star.id}",
                observation_solution_id=solution_id,
                evidence_class="predicted",
                title=star.common_name,
                subtitle=_star_subtitle(star, sky.designations[k], float(alt[k]), float(az[k])),
                ra_deg=star.ra_deg,
                dec_deg=star.dec_deg,
                alt_deg=float(alt[k]),
                az_deg=float(az[k]),
                uncertainty_arcsec=float(uncertainty[k]) + time_arcsec,
                catalog_star=star,
                verified_by_holdout=False,
                limitations=_star_limitations(star, index.epoch_jyear, time_said),
                source_sha256=source_sha256,
            )
        )
    return findings


def _place(asset: Mapping[str, Any]) -> tuple[float, float] | None:
    location = asset.get("observerLocation")
    if not isinstance(location, Mapping):
        return None
    lat, lon = location.get("latDeg"), location.get("lonDeg")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (lat, lon)):
        return None
    return float(lat), float(lon)


FramesForPose = Callable[[], Sequence[tuple[float, np.ndarray]]]
POSE_SOLVER = SolverIdentity(
    name="AlbedianHeadWornMoonPose",
    version="1.0.0",
    catalog_release=(
        "A camera profile's field of view (services/science/camera_profiles.py) and the ERFA Moon ephemeris "
        "(eraMoon98, Meeus 1998), topocentric"
    ),
)
INSIDE_THE_FRAME = " · Inside the frame (lined up, approximate)"
OUTSIDE_THE_FRAME = " · Outside the frame"
LINED_UP_LIMITATION = (
    "Placed in the frame only through the approximate pose lined up by taking the frame's bright spot to be the Moon, "
    "with the camera profile's field of view and a level head; not checked against stars."
)
# Two frame times are the same frame within a microsecond: the pose's frame carries its own presentation time as the
# job decodes it, and the detector's findings carry 0.0, the start of the stack they were found in
SAME_FRAME_SEC = 1e-6
OTHER_FRAMES_KEPT = (
    "Detections the detector made in other frames of the clip are kept as measured: the head moved between frames, "
    "so this frame's Moon pixel does not say which of them, if any, is the Moon."
)


def _head_worn_pose(
    asset: Mapping[str, Any], frames: FramesForPose, captured: str, moment: str, lat: float, lon: float
) -> PoseResult | Refusal:
    """Layer 2 for a head-worn camera: the camera profile, then the frames, read only when a pose could follow."""
    profile = head_worn_profile(asset)
    if profile is None:
        return Refusal("no_profile", NO_PROFILE)
    orientation = asset.get("displayOrientation")
    if is_mirrored(orientation):
        return Refusal("mirrored", MIRRORED)
    # Checked at the solver's frame before any frame is read; each frame is checked again at its own moment
    if not moon_is_up(moment, lat, lon):
        return Refusal("moon_not_up", MOON_NOT_UP)
    if sun_is_up(moment, lat, lon):
        return Refusal("daytime", DAYTIME)
    timed = []
    for frame_time_sec, luma in frames():
        frame_at = frame_moment(captured, frame_time_sec)
        try:
            require_ephemeris_range(frame_at)
        except EphemerisOutOfRange:
            continue
        timed.append((float(frame_time_sec), frame_at, luma))
    return pose_from_frames(
        timed, lat=lat, lon=lon, profile=profile, orientation=orientation, is_clip=asset.get("mediaKind") == "video"
    )


def _lined_up(
    solution: ObservationSolution, asset: Mapping[str, Any], pose: PoseResult, lat: float, lon: float
) -> ObservationSolution:
    """The approximate solution of a pose: the nominal WCS and its pose estimate, the frame's time and moment, the
    detector's pixel-only findings minus those on the spot in the same frame, the observed bright spot taken to be the
    Moon, and the predicted sky for the frame's moment, each saying whether the nominal WCS puts it inside the
    frame."""
    blob, wcs, frame_time_sec = pose.blob, pose.wcs, pose.frame_time_sec
    kept: list[CelestialFindingRecord] = []
    other_frames = False
    for finding in solution.findings:
        detection = finding.source_detection
        if finding.evidence_class != "observed" or detection is None:
            continue
        if abs(detection.frame_time_sec - frame_time_sec) <= SAME_FRAME_SEC:
            # The spot is listed once: a detection of the same frame inside the spot's radius is the spot itself
            if math.hypot(detection.x_px - blob.x_px, detection.y_px - blob.y_px) <= blob.radius_px:
                continue
        else:
            other_frames = True
        kept.append(finding)
    is_clip = asset.get("mediaKind") == "video"
    where = f"in the frame {frame_time_sec:.2f} s into the clip" if is_clip else "in the photo"
    moon = CelestialFindingRecord(
        id="finding-observed-moon",
        observation_solution_id=solution.id,
        evidence_class="observed",
        title="Bright Spot (Taken To Be The Moon)",
        subtitle=f"Measured at ({blob.x_px:.1f}, {blob.y_px:.1f}) px {where} · {blob.area_px} px above half its peak",
        source_detection=SourceDetection(
            x_px=blob.x_px,
            y_px=blob.y_px,
            flux=blob.flux,
            snr=blob.snr,
            fwhm_px=blob.fwhm_px,
            centroid_uncertainty_px=blob.centroid_uncertainty_px,
            asset_id=solution.source_asset_id,
            frame_time_sec=frame_time_sec,
            peak_to_total_ratio=blob.peak_to_total_ratio,
        ),
        verification_method=(
            "Measured in the frame's pixels: the frame's brightest spot passed the Moon test and was taken to be the "
            "Moon; nothing was matched to a catalog and no held-out check was made"
        ),
        verified_by_holdout=False,
        limitations=(
            "Taken to be the Moon, not shown to be: nothing in one frame proves the spot is the Moon, and a lamp or "
            "another light of similar size, as bright and the only spot in its frame that passes the Moon test, would "
            "be taken the same way.",
            "Without a verified WCS the detection has no sky position; the predicted Moon carries the ephemeris "
            "position.",
            "If the spot is the Moon, its light centroid sits somewhere on its disk: overexposure spreads it, and a "
            "partly lit Moon's sits toward its lit edge; the pose's beacon term, the Moon's predicted radius plus its "
            "ephemeris's stated uncertainty, is taken to cover that. Its flux and SNR are lower bounds where its "
            "brightest pixels are clipped.",
            *((OTHER_FRAMES_KEPT,) if other_frames else ()),
        ),
        source_sha256=solution.source_sha256,
    )
    time_sec = _worked_out_time_sec(asset)
    predicted = []
    for finding in predicted_sky_findings(
        pose.moment, lat, lon, solution_id=solution.id, source_sha256=solution.source_sha256,
        time_uncertainty_sec=time_sec,
    ):
        x, y = wcs.world_to_pixel(finding.ra_deg, finding.dec_deg)
        inside = math.isfinite(x) and math.isfinite(y) and 0.0 <= x < wcs.width and 0.0 <= y < wcs.height
        predicted.append(
            replace(
                finding,
                subtitle=finding.subtitle + (INSIDE_THE_FRAME if inside else OUTSIDE_THE_FRAME),
                limitations=(*finding.limitations, LINED_UP_LIMITATION) if inside else finding.limitations,
            )
        )
    estimate = pose.pose_estimate
    low, high = pose.profile.fov_diagonal_deg_range
    # The bound counts the beacon term: where the spot's light sits on the Moon's disk and where the Moon itself is
    # and, for a time zone worked out from the GPS position and time stamp, how far the sky turns in the time's uncertainty
    time_deg = 0.0 if time_sec is None else sky_turn_deg(time_sec)
    within = math.ceil(estimate.max_center_offset_deg + estimate.beacon_centroid_uncertainty_deg + time_deg)
    lined_up = (
        f"Lined up by taking the bright spot at ({blob.x_px:.0f}, {blob.y_px:.0f}) px {where} to be the Moon, with the "
        f"camera profile's field of view of {low:g} to {high:g} degrees across the diagonal with no zoom and a level "
        f"head within {ROLL_LIMIT_DEG:g} degrees: within about {within} degrees near the bright spot, more toward the "
        "edges; not checked against stars."
    )
    (sun,) = compute_solar_system_ephemerides(pose.moment, lat, lon, bodies=("Sun",))
    detected = (
        solution.quality.detected_candidate_count
        if solution.quality is not None
        else sum(1 for f in solution.findings if f.evidence_class == "observed" and f.source_detection is not None)
    )
    return replace(
        solution,
        frame_time_sec=frame_time_sec,
        status="approximate",
        wcs=wcs,
        # Nothing was matched to a catalog: the spot was only taken to be the Moon, and no star was held out
        quality=SolutionQuality(
            detected_candidate_count=detected,
            matched_inlier_count=0,
            held_out_pair_count=0,
            held_out_inliers=0,
            median_residual_px=None,
            p95_residual_px=None,
            median_residual_arcsec=None,
            passed_independent_verification=False,
        ),
        solver=POSE_SOLVER,
        observed_at_utc=pose.moment,
        observer_lat=lat,
        observer_lon=lon,
        findings=[*kept, moon, *predicted],
        warnings=[
            *solution.warnings,
            lined_up,
            *(() if time_sec is None else (time_zone_from_gps(time_sec),)),
            sky_phase(sun.alt_deg).sentence,
        ],
        pose_estimate=estimate,
    )


def complete_sky(
    solution: ObservationSolution, asset: Mapping[str, Any], *, frames: FramesForPose | None = None
) -> ObservationSolution:
    """A plate solver's outcome completed with the sky predicted for the recording's own capture time and place
    (`asset` is its SourceAsset): a new solution, the solver's left as it was.

    - No recorded `capturedAtUTC`, no `observerLocation`, or a frame moment outside 1800 to 2050: the solver's
      solution with one more warning saying which. No other time or place stands in.
    - Calibrated: status, WCS, quality and every finding stay; the predicted objects not already catalog_matched
      (same catalog id) are added.
    - Unresolved or ambiguous, given `frames`, with a head-worn camera profile, the predicted Moon more than 5 degrees
      up, the Sun down, and one frame whose bright spot passes the Moon test and fits exactly one level pose at each
      swept value: approximate, lined up from that frame (`_lined_up`).
      `frames` gives the frames to search, each as its time into the recording and its gray pixels, and is called
      only when a pose could follow; None asks for no pose (layer 1 alone).
    - Otherwise unresolved or ambiguous: predicted_only, with the solver's findings (pixel-only observed ones),
      quality, warnings and identity, the predicted objects added, and, when `frames` was given, one warning saying
      why no pose was found.
    The moment is the solution's frame (`capturedAtUTC` plus `frameTimeSec`), written as `observedAtUTC`, and the
    last warning says where the Sun was. No warning holds the capture time, the place or the file name."""
    if solution.status not in COMPLETED_STATUSES:
        raise ValueError(
            f"Only a plate solver's outcome is completed with the predicted sky, not a {solution.status} one."
        )
    captured = asset.get("capturedAtUTC")
    has_time = isinstance(captured, str) and captured != ""
    place = _place(asset)
    if not has_time or place is None:
        reason = NO_TIME_OR_PLACE if not has_time and place is None else (NO_TIME if not has_time else NO_PLACE)
        return replace(solution, warnings=[*solution.warnings, reason])
    moment = frame_moment(captured, solution.frame_time_sec)
    try:
        require_ephemeris_range(moment)
    except EphemerisOutOfRange:
        return replace(solution, warnings=[*solution.warnings, OUT_OF_RANGE])
    lat, lon = place
    no_pose: list[str] = []
    if solution.status != "calibrated" and frames is not None:
        pose = _head_worn_pose(asset, frames, captured, moment, lat, lon)
        if isinstance(pose, PoseResult):
            return _lined_up(solution, asset, pose, lat, lon)
        no_pose.append(pose.reason)
    matched = frozenset(
        f.catalog_star.id
        for f in solution.findings
        if f.evidence_class == "catalog_matched" and f.catalog_star is not None
    )
    present = {f.id for f in solution.findings}
    time_sec = _worked_out_time_sec(asset)
    predicted = [
        f
        for f in predicted_sky_findings(
            moment, lat, lon, solution_id=solution.id, source_sha256=solution.source_sha256, exclude_ids=matched,
            time_uncertainty_sec=time_sec,
        )
        if f.id not in present
    ]
    (sun,) = compute_solar_system_ephemerides(moment, lat, lon, bodies=("Sun",))
    return replace(
        solution,
        status="calibrated" if solution.status == "calibrated" else "predicted_only",
        observed_at_utc=moment,
        observer_lat=lat,
        observer_lon=lon,
        findings=[*solution.findings, *predicted],
        warnings=[
            *solution.warnings,
            *no_pose,
            *(() if time_sec is None else (time_zone_from_gps(time_sec),)),
            sky_phase(sun.alt_deg).sentence,
        ],
    )
