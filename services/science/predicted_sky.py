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
from services.science.deep_sky_catalog import (
    LIMITATIONS as DEEP_SKY_LIMITATIONS,
    OPENNGC_LICENCE_NOTE,
    POSITION_PRECISION_ARCSEC,
    SKY_VERIFICATION_METHOD,
    _candidate_rows,
    catalog_reference,
    finding_id,
    table_and_columns,
)
from services.science.deep_sky_projection import predict_deep_sky_findings
from services.science.deep_sky_selection import row_sort_key, sky_reason
from services.science.designations import designation_of_row, designation_parts, star_title
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
from services.science.phone_photo_solver import _get_tile_index, _TileStarIndex, iau_constellation_codes, magnitude_band_words
from services.science.pose_estimate import (
    DAYTIME,
    MIRRORED,
    NO_FRAMES,
    MOON_NOT_UP,
    NO_PROFILE,
    ROLL_LIMIT_DEG,
    SKYLINE_ALTS_DEG,
    SUNRISE_SUNSET_ALT_DEG,
    PoseResult,
    Refusal,
    horizon_moon_pose,
    horizon_pose_from_frames,
    is_mirrored,
    moon_is_up,
    pose_from_frames,
    sun_is_up,
)
from services.science.sun_pose import (
    MIN_SUN_ALT_DEG as SUN_BEACON_MIN_ALT_DEG,
    SUN_POSE_SOLVER_NAME,
    SunPose,
    sun_core_spot,
    sun_horizon_pose,
    topocentric_sun,
)
from services.science.record_hint import NO_RECORD_SIZE, RecordAttitude, RecordPose, TILT_ERROR_DEG, record_pose
from services.science.wcs_models import (
    CelestialFindingRecord,
    ObservationSolution,
    PointingHint,
    SolutionQuality,
    SolverIdentity,
    SourceDetection,
)
from services.contracts.astronomy import POINTING_USES

# The bound the synthetic pose sample (tests/integration/test_unregistered_detections.py) still states for its 71
# pinned stars, set when the transform left out nutation and annual aberration; neither the predicted sky nor the
# horizon solver uses it [proposed]
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


# A time or a place the iPhone's pointing record lent the solve (pointing-moment v2), in the design lane's words
# (C13, .lead/uiux/UIUX-pointingwords-DECIDED-BY-LEAD.md, decided 2026-11-19 07:40): what the iPhone recorded, said
# as a person would say it
RECORD_TIME_WORDS = "the time your iPhone recorded"
RECORD_PLACE_WORDS = "where your iPhone was"
# A lent time beside the file's own place, in an item's first limitation: item 7's own pairs, so the place is never
# read as the iPhone's, and no "(within ...)" since the file's place states no accuracy (C13's last section, ruled
# 2026-11-21 and decided by the lead at 13:34)
RECORD_TIME_BESIDE_FILE_PLACE = f"{RECORD_TIME_WORDS} and the photo's recorded place"
# [inference] The Earth's mean radius and the Moon's nearest distance: a place off by d metres tilts the local vertical,
# and so every apparent altitude and azimuth, by d over the Earth's radius, and moves the Moon's direction by d over its
# distance at most
EARTH_MEAN_RADIUS_M = 6_371_000.0
MOON_NEAREST_M = 356_400_000.0
ARCSEC_PER_RAD = 180.0 * 3600.0 / math.pi


@dataclass(frozen=True)
class LentMoment:
    """Where the solve's time and place came from when a phone's pointing record lent either: "file" or "record"
    for each; for the record's place, the horizontal accuracy the phone stated in metres and the accuracy the person
    allowed ("full" or "reduced")."""

    time_from: str
    place_from: str
    place_accuracy_m: float | None = None
    accuracy_authorization: str | None = None


def place_uncertainty_arcsec(accuracy_m: float) -> float:
    """How far a place `accuracy_m` metres off can move a predicted direction: the local vertical's tilt, and the
    Moon's parallax at its nearest added for every object, so the bound holds for each."""
    return float(accuracy_m) * (1.0 / EARTH_MEAN_RADIUS_M + 1.0 / MOON_NEAREST_M) * ARCSEC_PER_RAD


def _lent_place_arcsec(lent: LentMoment | None) -> float:
    if lent is None or lent.place_from != "record" or lent.place_accuracy_m is None:
        return 0.0
    return place_uncertainty_arcsec(lent.place_accuracy_m)


def _metres(value: float) -> str:
    """A distance in metres as C13 writes it: a thousands comma, no trailing zeros (8 m, 5,000 m)."""
    return f"{value:,g}"


def lent_moment_sentence(lent: LentMoment) -> str:
    """The warning for a predicted sky whose time or place the iPhone's record lent, in C13's words (items 3 to 6):
    the first sentence says what the file lacks and what the sky is predicted for, shown on screen; the second, kept
    in the record, says how far the place's stated accuracy can move the predicted sky. It names no time and no
    place. A place with no stated accuracy (reached only by a direct call: the pointing contract requires it for an
    allowed place) has no bound to state, so it gets no second sentence and no number."""
    time_lent = lent.time_from == "record"
    if not time_lent and lent.place_from != "record":
        raise ValueError("A lent moment lends the time, the place or both.")
    if lent.place_from != "record":
        return f"The photo's file has no time, so the sky is predicted for {RECORD_TIME_WORDS}, by its own clock."
    # The place, after "where your iPhone was" on its own or "where it was" after the time
    if lent.place_accuracy_m is None:
        within = " (it gave no accuracy)"
    else:
        allowed = ", the rough location you allowed" if lent.accuracy_authorization == "reduced" else ""
        within = f", to within {_metres(lent.place_accuracy_m)} m{allowed}"
    if time_lent:
        first = (
            f"The photo's file has no time or place, so the sky is predicted for {RECORD_TIME_WORDS}, by its own "
            f"clock, and where it was{within}."
        )
    else:
        first = f"The photo's file has no place, so the sky is predicted for {RECORD_PLACE_WORDS}{within}."
    if lent.place_accuracy_m is None:
        return first
    return f"{first} That can move the predicted sky by up to {_lent_place_arcsec(lent):.1f} arcseconds."


def _moment_said(time_said: str, lent: LentMoment | None) -> str:
    """The time and place a predicted item is placed for, in its first limitation's words (C13 item 7): "the time
    your iPhone recorded and where it was (within 8 m)" when the record lent both, "<the file's time> and where your
    iPhone was (within 8 m)" when it lent the place only, and "the time your iPhone recorded and the photo's recorded
    place" when it lent the time only (RECORD_TIME_BESIDE_FILE_PLACE, C13's last section)."""
    time_lent = lent is not None and lent.time_from == "record"
    if lent is None or lent.place_from != "record":
        return RECORD_TIME_BESIDE_FILE_PLACE if time_lent else f"{time_said} and place"
    within = " (it gave no accuracy)" if lent.place_accuracy_m is None else f" (within {_metres(lent.place_accuracy_m)} m)"
    if time_lent:
        return f"{RECORD_TIME_WORDS} and where it was{within}"
    return f"{time_said} and {RECORD_PLACE_WORDS}{within}"


def _star_position_limitation(lent: LentMoment | None) -> str:
    """The star's place caution: the same words for a lent place, since "that moment and place" are the ones the
    caution before it names, the record's when it lent them (C24)."""
    return STAR_POSITION_LIMITATION


# C24 (.lead/uiux/UIUX-starplacewords-READY.md, decided 2026-11-19 17:14): the first sentence shown on the card, the
# second kept in the record. The place is ERFA's apparent place (catalog.py icrs_to_altaz), refraction left out
STAR_POSITION_LIMITATION = (
    "Placed in the sky for that moment and place, at sea level; near the horizon it sits a little low, since the "
    "air's bending of light is left out. Its stated uncertainty adds UT1 - UTC (up to 13.5 arcseconds), polar motion, "
    "its own parallax (left out of its place), the stored precision of its motion, and, for a row with no proper "
    "motion or radial velocity, the motion it may have, up to the fastest star in the tiles; the catalog's own "
    "position and motion errors are not in it."
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
    apart for the subtitle's text. The Bayer designation and the Flamsteed number come from the baked designations
    through services/science/designations.py, as the solver's catalog star does. A pinned star keeps its pinned id, the id a plate
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
        pinned = index._pinned_by_source.get(star.catalog_source_id)
        bayer, flamsteed = designation_of_row(
            bool(data["hip"][row]), int(data["sid"][row]), constellation, pinned.bayer_designation if pinned else None
        )
        stars.append(
            replace(
                star, common_name=name, bayer_designation=bayer, flamsteed_number=flamsteed, constellation=constellation,
                names_file_designation=designation,
            )
        )
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
    """Constellation, the designations the title does not show (`designation_parts`, as a solve's matched star shows
    them) and then the catalog id if the title is not it, the row's distance where it gives one, and where the star is.
    `designation` is the name file's, which the star also carries."""
    title = star_title(star)
    parts = [f"Predicted Star ({star.constellation})", *(p for p in designation_parts(star) if p != title)]
    if title != star.catalog_source_id:
        parts.append(star.catalog_source_id)
    if star.has_distance:
        parts.append(f"{star.median_pc:.1f} pc")
    parts += [f"Alt {alt:+.1f}° Az {az:.1f}°", "Above Horizon"]
    return " · ".join(parts)


def _star_limitations(
    star: CatalogStar, catalog_epoch: float, moment_said: str, lent: LentMoment | None = None
) -> tuple[str, ...]:
    moved = star.pm_ra_mas_yr is not None and star.pm_dec_mas_yr is not None
    return (
        f"Predicted from its {star.release_version} catalog row (epoch J{catalog_epoch:.1f}), "
        + ("moved by its proper motion to the date" if moved else "not moved, since the row has no proper motion")
        + f", for {moment_said}; not measured in the recording.",
        magnitude_band_words(star.magnitude_band),
        *(() if star.has_distance else ("Its catalog row gives no distance, so none is shown.",)),
        _star_position_limitation(lent),
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
    lent: LentMoment | None = None,
) -> list[CelestialFindingRecord]:
    """The Sun, the Moon, the five planets and the catalog tile stars to PREDICTED_STAR_DEPTH_V or named (stars
    carried to the date by their proper motion) above the horizon at `moment` from (`lat_deg`, `lon_deg`), as
    predicted findings of the solution `solution_id`: bodies first in `PREDICTED_BODIES` order, then stars brightest
    first, then the OpenNGC deep-sky objects `sky_reason` keeps (Messier, Caldwell, or as bright as the star depth;
    never a row typed as a star) brightest first, each at its J2000 catalog centre. The stars are placed in one pass
    (`propagate_directions_batch`, `icrs_to_altaz_batch`), and the deep-sky objects in another.

    Above the horizon means a geometric (airless) altitude above 0. `exclude_ids` names catalog stars to leave
    out, by catalog id. `time_uncertainty_sec`, for a capture time whose time zone was worked out from the GPS position
    and time stamp, is added to each uncertainty as the sky's turn in that time and named in each limitation. `lent`,
    when a phone's pointing record lent the time or the place (pointing-moment), names the record in each limitation
    in their place and adds its place's stated accuracy to each uncertainty (`place_uncertainty_arcsec`). Raises
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
    moment_said = _moment_said(time_said, lent)
    time_arcsec += _lent_place_arcsec(lent)
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
                    f"Predicted with {body.method}, for {moment_said}; not measured in the recording.",
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
                title=star_title(star),
                subtitle=_star_subtitle(star, sky.designations[k], float(alt[k]), float(az[k])),
                ra_deg=star.ra_deg,
                dec_deg=star.dec_deg,
                alt_deg=float(alt[k]),
                az_deg=float(az[k]),
                uncertainty_arcsec=float(uncertainty[k]) + time_arcsec,
                catalog_star=star,
                verified_by_holdout=False,
                limitations=_star_limitations(star, index.epoch_jyear, moment_said, lent),
                source_sha256=source_sha256,
            )
        )
    findings += _deep_sky_above_horizon(
        moment, lat_deg, lon_deg, solution_id=solution_id, source_sha256=source_sha256, time_arcsec=time_arcsec,
        moment_said=moment_said, lent=lent,
    )
    return findings


@lru_cache(maxsize=1)
def _sky_deep_sky_rows() -> tuple[list[Any], ...]:
    """The OpenNGC rows the predicted sky lists when they are up (`sky_reason`), brightest first, read once."""
    _, at = table_and_columns()
    rows = [row for row in _candidate_rows() if sky_reason(row) is not None]
    rows.sort(key=lambda row: row_sort_key(row, at["name"]))
    return tuple(rows)


def _deep_sky_above_horizon(
    moment: str, lat_deg: float, lon_deg: float, *, solution_id: str, source_sha256: str, time_arcsec: float,
    moment_said: str, lent: LentMoment | None = None,
) -> list[CelestialFindingRecord]:
    """The OpenNGC objects `sky_reason` keeps that are above the horizon (geometric altitude above 0) at `moment`, as
    predicted findings placed from their J2000 catalog centres, brightest first (FIELD field-6)."""
    table, at = table_and_columns()
    rows = _sky_deep_sky_rows()
    if not rows:
        return []
    ra = np.array([row[at["raDeg"]] for row in rows], dtype=np.float64)
    dec = np.array([row[at["decDeg"]] for row in rows], dtype=np.float64)
    alt, az = icrs_to_altaz_batch(ra, dec, lat_deg, lon_deg, moment)
    found = []
    for k in np.flatnonzero(alt > 0.0).tolist():
        row = rows[k]
        reference = catalog_reference(row)
        found.append(
            CelestialFindingRecord(
                id=finding_id(row[at["name"]]),
                observation_solution_id=solution_id,
                evidence_class="predicted",
                title=reference["commonName"],
                subtitle=f"{table['types'][row[at['type']]]} · {row[at['constellation']]} · {OPENNGC_LICENCE_NOTE}",
                ra_deg=reference["raDeg"],
                dec_deg=reference["decDeg"],
                alt_deg=float(alt[k]),
                az_deg=float(az[k]),
                uncertainty_arcsec=POSITION_PRECISION_ARCSEC + time_arcsec,
                deep_sky_reference=reference,
                verification_method=(
                    SKY_VERIFICATION_METHOD
                    if lent is None
                    else f"OpenNGC catalog position placed for {moment_said} by the predicted sky; not measured in the image"
                ),
                verified_by_holdout=False,
                limitations=(
                    f"Placed from its OpenNGC catalog centre ({table['release']}, J2000) for {moment_said}; "
                    "not measured in the recording.",
                    *DEEP_SKY_LIMITATIONS[1:],
                ),
                source_sha256=source_sha256,
            )
        )
    return found


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
HORIZON_POSE_SOLVER = SolverIdentity(
    name="AlbedianHorizonMoonPose",
    version="2.0.0",
    catalog_release=(
        "The ERFA Moon ephemeris (eraMoon98, Meeus 1998), topocentric, and a skyline taken to be level ground, with "
        "the field of view fitted from the two"
    ),
)
RECORD_POSE_SOLVER = SolverIdentity(
    name="AlbedianDeviceAttitudePose",
    version="1.0.0",
    # C17 (.lead/uiux/UIUX-recordwords-READY.md): the solver line where a person reads it
    catalog_release=(
        "Where your iPhone pointed (its compass, tilt and camera's field of view), turned to the sky at the photo's "
        "moment by ERFA: predicted"
    ),
)
SUN_POSE_SOLVER = SolverIdentity(
    name=SUN_POSE_SOLVER_NAME,
    version="1.0.0",
    catalog_release=(
        "The ERFA Sun ephemeris, topocentric, and a skyline taken to be level, with the field of view fitted from the "
        "two and the camera's clock checked against the Sun's height"
    ),
)
# C23 (.lead/uiux/UIUX-sunwords-READY.md, decided 2026-11-19 17:08), C31 (.lead/uiux/UIUX-moonposewords-READY.md,
# decided 2026-11-21 12:59) when the glare's peak is clipped, and the parallel unclipped clause
# (.lead/kiro/inbox/done/UIUX-solvewords-c31-READY.md, decided 2026-11-23 02:58) when not
SUN_LINED_UP_LIMITATION = "Placed in the frame only from the Sun and the skyline: predicted, not checked against stars."
OBSERVED_GLARE_ID = "finding-observed-glare"
OBSERVED_GLARE_TITLE = "Bright glare"
OBSERVED_GLARE_SUBTITLE = "Taken to be the Sun"
GLARE_CLIPPED_LIMITATION = (
    "Measured on the picture: its place, area and width; its core is clipped, so it is at least as bright as "
    "measured. Its flux and SNR are lower bounds where its brightest pixels are clipped."
)
GLARE_UNCLIPPED_LIMITATION = (
    "Measured on the picture: its place, area and width; only its core is summed, so it is at least as bright as "
    "measured. Its flux and SNR are lower bounds measured over its brightest core."
)
INSIDE_THE_FRAME = " · Inside the frame (lined up, approximate)"
OUTSIDE_THE_FRAME = " · Outside the frame"
LINED_UP_LIMITATION = (
    "Placed in the frame only through the approximate pose lined up by taking the frame's bright spot to be the Moon, "
    "with the camera profile's field of view and a level head; not checked against stars."
)
HORIZON_LINED_UP_LIMITATION = (
    "Placed in the frame only through the approximate pose lined up by taking the frame's bright spot to be the Moon "
    "and its skyline to be level ground, with the field of view fitted from them; not checked against stars."
)
RECORD_LINED_UP_LIMITATION = (
    "Placed in the frame only from where your iPhone pointed (its compass, tilt and field of view): predicted, not "
    "checked against stars."
)
# Two frame times are the same frame within a microsecond: the pose's frame carries its own presentation time as the
# job decodes it, and the detector's findings carry 0.0, the start of the stack they were found in
SAME_FRAME_SEC = 1e-6
OTHER_FRAMES_KEPT = (
    "Detections the detector made in other frames of the clip are kept as measured: the head moved between frames, "
    "so the place of this frame's bright spot, taken to be the Moon, does not say which of them, if any, is the Moon."
)
OTHER_FRAMES_KEPT_CAMERA = (
    "Detections the detector made in other frames of the clip are kept as measured: the camera may have moved between "
    "frames, so the place of this frame's bright spot, taken to be the Moon, does not say which of them, if any, is "
    "the Moon."
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
    return pose_from_frames(
        _timed(frames, captured), lat=lat, lon=lon, profile=profile, orientation=orientation,
        is_clip=asset.get("mediaKind") == "video",
    )


def _timed(frames: FramesForPose, captured: str) -> list[tuple[float, str, np.ndarray]]:
    """Each frame with its own moment, the frames whose moment lies past the ephemeris range left out."""
    timed = []
    for frame_time_sec, luma in frames():
        frame_at = frame_moment(captured, frame_time_sec)
        try:
            require_ephemeris_range(frame_at)
        except EphemerisOutOfRange:
            continue
        timed.append((float(frame_time_sec), frame_at, luma))
    return timed


# The head-worn refusals after which the Moon plus horizon pose is tried on the same frames (REQ-SKY-4.1): no
# head-worn profile, or a head-worn search that found no pose in frames it read. The others (the Moon not up, by day,
# a mirrored frame, no frame read) would refuse the horizon pose for the same reason
HORIZON_AFTER = ("no_profile", "not_moon", "two_poses", "ranges", "fit")


def _read_once(frames: FramesForPose) -> FramesForPose:
    """The frames, read on the first call only, so both poses search the same frames and a clip is decoded once."""
    cache: list[Sequence[tuple[float, np.ndarray]]] = []

    def read() -> Sequence[tuple[float, np.ndarray]]:
        if not cache:
            cache.append(list(frames()))
        return cache[0]

    return read


def horizon_pose_solution(
    solution: ObservationSolution, luma: np.ndarray, *, moment: str, lat: float, lon: float, media_kind: str = "image"
) -> ObservationSolution:
    """The Moon plus horizon pose on one upright frame recorded at `moment` (a frame time already counted in), as a
    solution built on `solution` (a pose solver's empty outcome): approximate with its pose estimate, the spot and the
    predicted sky, as `complete_sky` lines one up; or predicted_only with the predicted sky and the reason, and the
    sentence for where the Sun was. For services/science/horizon_solver.py, which discovery and the science router
    call."""
    lat, lon = require_place(lat, lon)
    asset = {"mediaKind": media_kind}
    pose = horizon_moon_pose(
        luma, frame_time_sec=solution.frame_time_sec, moment=moment, lat=lat, lon=lon, orientation=None,
        where=f"in the frame {solution.frame_time_sec:.2f} s into the clip" if media_kind == "video" else "in the photo",
    )
    if isinstance(pose, PoseResult):
        return _lined_up(solution, asset, pose, lat, lon)
    (sun,) = compute_solar_system_ephemerides(moment, lat, lon, bodies=("Sun",))
    return replace(
        solution,
        status="predicted_only",
        wcs=None,
        quality=replace(solution.quality, detected_candidate_count=1 if pose.blob is not None else 0),
        observed_at_utc=moment,
        observer_lat=lat,
        observer_lon=lon,
        findings=predicted_sky_findings(moment, lat, lon, solution_id=solution.id, source_sha256=solution.source_sha256),
        warnings=[*solution.warnings, pose.reason, sky_phase(sun.alt_deg).sentence],
    )


def _horizon_pose(
    asset: Mapping[str, Any], frames: FramesForPose, captured: str, moment: str, lat: float, lon: float
) -> PoseResult | Refusal:
    """Layer 2 from the Moon and a skyline (REQ-SKY-4.1): the frames read only when a pose could follow."""
    orientation = asset.get("displayOrientation")
    if is_mirrored(orientation):
        return Refusal("mirrored", MIRRORED)
    if not moon_is_up(moment, lat, lon):
        return Refusal("moon_not_up", MOON_NOT_UP)
    if sun_is_up(moment, lat, lon):
        return Refusal("daytime", DAYTIME)
    return horizon_pose_from_frames(
        _timed(frames, captured), lat=lat, lon=lon, orientation=orientation,
        is_clip=asset.get("mediaKind") == "video",
    )


def _lined_up(
    solution: ObservationSolution, asset: Mapping[str, Any], pose: PoseResult, lat: float, lon: float,
    lent: LentMoment | None = None,
) -> ObservationSolution:
    """The approximate solution of a pose: the nominal WCS and its pose estimate, the frame's time and moment, the
    detector's pixel-only findings minus those on the spot in the same frame, the observed bright spot taken to be the
    Moon, and the predicted sky for the frame's moment, each saying whether the nominal WCS puts it inside the
    frame."""
    blob, wcs, frame_time_sec = pose.blob, pose.wcs, pose.frame_time_sec
    # A head-worn pose assumed a camera profile; a Moon plus horizon pose lined up to a skyline instead
    head_worn = pose.profile is not None
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
            *(((OTHER_FRAMES_KEPT if head_worn else OTHER_FRAMES_KEPT_CAMERA),) if other_frames else ()),
        ),
        source_sha256=solution.source_sha256,
    )
    time_sec = _worked_out_time_sec(asset)
    predicted = _placed(
        predicted_sky_findings(
            pose.moment, lat, lon, solution_id=solution.id, source_sha256=solution.source_sha256,
            time_uncertainty_sec=time_sec, lent=lent,
        ),
        wcs,
        LINED_UP_LIMITATION if head_worn else HORIZON_LINED_UP_LIMITATION,
    )
    if not head_worn:
        # The horizon solver's own order, kept for its callers: what lies inside the frame first (bodies, then stars
        # brightest first, as the predicted sky lists them), then the rest above the horizon
        predicted.sort(key=lambda f: f.subtitle.endswith(OUTSIDE_THE_FRAME))
    estimate = pose.pose_estimate
    # The bound counts the beacon term: where the spot's light sits on the Moon's disk and where the Moon itself is
    # and, for a time zone worked out from the GPS position and time stamp, how far the sky turns in the time's uncertainty
    time_deg = (0.0 if time_sec is None else sky_turn_deg(time_sec)) + _lent_place_arcsec(lent) / 3600.0
    within = math.ceil(estimate.max_center_offset_deg + estimate.beacon_centroid_uncertainty_deg + time_deg)
    if head_worn:
        low, high = pose.profile.fov_diagonal_deg_range
        lined_up = (
            f"Lined up by taking the bright spot at ({blob.x_px:.0f}, {blob.y_px:.0f}) px {where} to be the Moon, with "
            f"the camera profile's field of view of {low:g} to {high:g} degrees across the diagonal with no zoom and a "
            f"level head within {ROLL_LIMIT_DEG:g} degrees: within about {within} degrees near the bright spot, more "
            "toward the edges; not checked against stars."
        )
    else:
        skyline = pose.skyline
        lined_up = (
            f"Lined up by taking the bright spot at ({blob.x_px:.0f}, {blob.y_px:.0f}) px {where} to be the Moon and the "
            f"skyline at row {skyline.row:.0f} of the upright picture, within {skyline.half_width_px:.0f} px, to be level "
            f"ground {SKYLINE_ALTS_DEG[0]:g} to {SKYLINE_ALTS_DEG[-1]:g} degrees above the true horizon, with the field "
            f"of view fitted from them ({estimate.fov_diagonal_deg:.0f} degrees across the diagonal): within about "
            f"{within} degrees near the bright spot, more toward the edges; not checked against stars."
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
        solver=POSE_SOLVER if head_worn else HORIZON_POSE_SOLVER,
        observed_at_utc=pose.moment,
        observer_lat=lat,
        observer_lon=lon,
        findings=[*kept, moon, *predicted],
        warnings=[
            *solution.warnings,
            lined_up,
            *(() if time_sec is None else (time_zone_from_gps(time_sec),)),
            *(() if lent is None else (lent_moment_sentence(lent),)),
            sky_phase(sun.alt_deg).sentence,
        ],
        pose_estimate=estimate,
    )


def _placed(
    findings: Sequence[CelestialFindingRecord], wcs: Any, limitation: str
) -> list[CelestialFindingRecord]:
    """The predicted findings, each saying whether the nominal WCS puts it inside the frame, and those inside it
    carrying `limitation`."""
    placed = []
    for finding in findings:
        x, y = wcs.world_to_pixel(finding.ra_deg, finding.dec_deg)
        inside = math.isfinite(x) and math.isfinite(y) and 0.0 <= x < wcs.width and 0.0 <= y < wcs.height
        placed.append(
            replace(
                finding,
                subtitle=finding.subtitle + (INSIDE_THE_FRAME if inside else OUTSIDE_THE_FRAME),
                limitations=(*finding.limitations, limitation) if inside else finding.limitations,
            )
        )
    return placed


def _record_hint_with_pose(
    solution: ObservationSolution, attitude: RecordAttitude, hint: PointingHint | None, lent: LentMoment | None
) -> PointingHint:
    """The solution's pointing hint once the record is its pose: `hint` (the searches' own) with `pose_estimate`
    added, or, with none, a new one that says the record was the pose and, where it lent the time or the place, the
    moment; the centre and uncertainty are the record's in both."""
    time_from, place_from = ("file", "file") if lent is None else (lent.time_from, lent.place_from)
    uses = {"pose_estimate", *(hint.used_as if hint is not None else ())}
    if hint is None and "record" in (time_from, place_from):
        uses.add("moment")
    if hint is not None:
        time_from, place_from = hint.time_from, hint.place_from
    centre = attitude.hint
    return PointingHint(
        source_asset_sha256=solution.source_sha256,
        used_as=tuple(use for use in POINTING_USES if use in uses),
        time_from=time_from,
        place_from=place_from,
        center_alt_deg=centre.center_alt_deg,
        center_az_deg=centre.center_az_deg,
        uncertainty_deg=centre.uncertainty_deg,
    )


def _record_lined_up(
    solution: ObservationSolution, asset: Mapping[str, Any], pose: RecordPose, attitude: RecordAttitude,
    moment: str, lat: float, lon: float, lent: LentMoment | None, hint: PointingHint | None,
) -> ObservationSolution:
    """The approximate solution of a pose from the phone's record (pointing-5): the nominal WCS and its device
    attitude pose, the detector's pixel-only findings as measured, and the predicted sky for the moment, each saying
    whether the nominal WCS puts it inside the frame; the pointing hint says the record was the pose."""
    kept = [f for f in solution.findings if f.evidence_class == "observed" and f.source_detection is not None]
    time_sec = _worked_out_time_sec(asset)
    predicted = _placed(
        predicted_sky_findings(
            moment, lat, lon, solution_id=solution.id, source_sha256=solution.source_sha256,
            time_uncertainty_sec=time_sec, lent=lent,
        ),
        pose.wcs,
        RECORD_LINED_UP_LIMITATION,
    )
    estimate = pose.estimate
    # The bound counts the frame term and, for a time zone worked out from the GPS position and time stamp, how far
    # the sky turns in the time's uncertainty, and the lent place's stated accuracy
    time_deg = (0.0 if time_sec is None else sky_turn_deg(time_sec)) + _lent_place_arcsec(lent) / 3600.0
    within = math.ceil(estimate.max_center_offset_deg + pose.frame_term_arcsec / 3600.0 + time_deg)
    # C17's two sentences; the web shows the first (C5's rule)
    lined_up = (
        f"Lined up from where your iPhone pointed, within about {within} degrees at the middle of the frame and more "
        "toward the edges: predicted, not checked against stars. "
        f"Its compass was good to {attitude.heading_accuracy_deg:g} degrees, its tilt taken to {TILT_ERROR_DEG:g} "
        f"degrees, it turned at most {attitude.turned_deg:.1f} degrees around the shot, and its camera reported a "
        f"{estimate.fov_diagonal_deg:.0f} degree field of view across the diagonal."
    )
    (sun,) = compute_solar_system_ephemerides(moment, lat, lon, bodies=("Sun",))
    detected = (
        solution.quality.detected_candidate_count
        if solution.quality is not None
        else len(kept)
    )
    return replace(
        solution,
        status="approximate",
        wcs=pose.wcs,
        # Nothing was matched to a catalog: the record only says where the phone pointed, and no star was held out
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
        solver=RECORD_POSE_SOLVER,
        observed_at_utc=moment,
        observer_lat=lat,
        observer_lon=lon,
        findings=[*kept, *predicted],
        warnings=[
            *solution.warnings,
            lined_up,
            *(() if time_sec is None else (time_zone_from_gps(time_sec),)),
            *(() if lent is None else (lent_moment_sentence(lent),)),
            sky_phase(sun.alt_deg).sentence,
        ],
        pose_estimate=estimate,
        pointing_hint=_record_hint_with_pose(solution, attitude, hint, lent),
    )


def _focal_35mm(asset: Mapping[str, Any]) -> float | None:
    """The file's 35 mm equivalent focal length, as the receipt's cameraMetadata states it, or None."""
    meta = asset.get("cameraMetadata")
    value = meta.get("focalLength35mmMm") if isinstance(meta, Mapping) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0:
        return float(value)
    return None


def _sun_pose(
    asset: Mapping[str, Any], frames: FramesForPose, captured: str, lat: float, lon: float
) -> tuple[SunPose, float, str] | Refusal:
    """The Sun plus horizon pose on the frames, each at its own moment: the first that lines up, with its time into
    the recording and its moment; else the first frame's refusal (sky-6d)."""
    first: Refusal | None = None
    focal = _focal_35mm(asset)
    for frame_time_sec, frame_at, luma in _timed(frames, captured):
        pose = sun_horizon_pose(
            luma, moment=frame_at, lat=lat, lon=lon, orientation=asset.get("displayOrientation"), focal_35mm_mm=focal,
        )
        if isinstance(pose, SunPose):
            return pose, frame_time_sec, frame_at
        first = first or pose
    return first or Refusal("no_frames", NO_FRAMES)


def _sun_lined_up(
    solution: ObservationSolution, asset: Mapping[str, Any], pose: SunPose, frame_time_sec: float, moment: str,
    lat: float, lon: float, lent: LentMoment | None, stack: Callable[[], np.ndarray] | None = None,
) -> ObservationSolution:
    """The approximate solution of a Sun pose (sky-6d, sky-6 (a)): the nominal WCS and its pose estimate, the
    detector's pixel-only findings as measured minus those on the Sun's core in the pixels they were measured on, the
    observed glare finding (`finding-observed-glare`: Bright glare / Taken to be the Sun, never called the Sun as
    observed, with its C31 lower-bound limitation), and the predicted sky for the frame's moment, each saying whether
    the nominal WCS puts it inside the frame. Point detections on the core are dropped so the glare is listed once
    (AUDIT's sweep of 21 Nov, finding one): a detection of the pose's own frame is judged by the pose's core; one of
    `stack` (a clip's stack of its first frames, which the solve's detections were measured on while the pose lined up
    a spread frame) by the stack's own core, found by the pose's own test (`sun_core_spot`), and kept as measured
    where the stack shows none (AUDIT's sun-core verdict, note a)."""
    spot = pose.spot

    def on(f: CelestialFindingRecord, core: Any) -> bool:
        d = f.source_detection
        return math.hypot(d.x_px - core.x_px, d.y_px - core.y_px) <= core.radius_px

    observed = [f for f in solution.findings if f.evidence_class == "observed" and f.source_detection is not None]
    same = [abs(f.source_detection.frame_time_sec - frame_time_sec) <= SAME_FRAME_SEC for f in observed]
    in_stack = None
    if stack is not None and not all(same):
        found = sun_core_spot(
            stack(), orientation=asset.get("displayOrientation"), focal_35mm_mm=_focal_35mm(asset), sun=pose.sun,
        )
        in_stack = None if isinstance(found, Refusal) else found
    kept = [
        f for f, of_pose in zip(observed, same)
        if not (on(f, spot) if of_pose else (in_stack is not None and on(f, in_stack)))
    ]
    glare = CelestialFindingRecord(
        id=OBSERVED_GLARE_ID,
        observation_solution_id=solution.id,
        evidence_class="observed",
        title=OBSERVED_GLARE_TITLE,
        subtitle=OBSERVED_GLARE_SUBTITLE,
        source_detection=SourceDetection(
            x_px=spot.x_px,
            y_px=spot.y_px,
            flux=spot.flux,
            snr=spot.snr,
            fwhm_px=spot.fwhm_px,
            centroid_uncertainty_px=spot.centroid_uncertainty_px,
            asset_id=solution.source_asset_id,
            frame_time_sec=frame_time_sec,
            peak_to_total_ratio=spot.peak_to_total_ratio,
            saturated=pose.core.clipped,
            saturated_area_px=pose.core.clipped_area_px,
        ),
        verification_method=(
            "Measured in the frame's pixels: the frame's brightest region passed the Sun core test and was taken to be "
            "the Sun; nothing was matched to a catalog and no held-out check was made"
        ),
        verified_by_holdout=False,
        limitations=(GLARE_CLIPPED_LIMITATION if pose.core.clipped else GLARE_UNCLIPPED_LIMITATION,),
        source_sha256=solution.source_sha256,
    )
    time_sec = _worked_out_time_sec(asset)
    predicted = _placed(
        predicted_sky_findings(
            moment, lat, lon, solution_id=solution.id, source_sha256=solution.source_sha256,
            time_uncertainty_sec=time_sec, lent=lent,
        ),
        pose.wcs,
        SUN_LINED_UP_LIMITATION,
    )
    estimate = pose.estimate
    time_deg = (0.0 if time_sec is None else sky_turn_deg(time_sec)) + _lent_place_arcsec(lent) / 3600.0
    within = math.ceil(estimate.max_center_offset_deg + estimate.beacon_centroid_uncertainty_deg + time_deg)
    focal = _focal_35mm(asset)
    # C23's two sentences; the web shows the first (C5's rule)
    lined_up = (
        "Lined up by taking a bright spot to be the Sun, with the skyline taken to be level, within about "
        f"{within} degrees at the middle of the frame and more toward the edges: predicted, not checked against "
        f"stars. The camera's clock agreed with the Sun's height, through the file's {focal:g} mm lens."
    )
    (sun,) = compute_solar_system_ephemerides(moment, lat, lon, bodies=("Sun",))
    detected = solution.quality.detected_candidate_count if solution.quality is not None else len(kept)
    return replace(
        solution,
        frame_time_sec=frame_time_sec,
        status="approximate",
        wcs=pose.wcs,
        # Nothing was matched to a catalog: the glare was only taken to be the Sun, and no star was held out
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
        solver=SUN_POSE_SOLVER,
        observed_at_utc=moment,
        observer_lat=lat,
        observer_lon=lon,
        findings=[*kept, glare, *predicted],
        warnings=[
            *solution.warnings,
            lined_up,
            *(() if time_sec is None else (time_zone_from_gps(time_sec),)),
            *(() if lent is None else (lent_moment_sentence(lent),)),
            sky_phase(sun.alt_deg).sentence,
        ],
        pose_estimate=estimate,
    )


def _stored_size(asset: Mapping[str, Any]) -> tuple[int, int] | None:
    width, height = asset.get("width"), asset.get("height")
    if not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in (width, height)):
        return None
    return int(width), int(height)


def complete_sky(
    solution: ObservationSolution, asset: Mapping[str, Any], *, frames: FramesForPose | None = None,
    lent: LentMoment | None = None, attitude: RecordAttitude | None = None, hint: PointingHint | None = None,
    stack: Callable[[], np.ndarray] | None = None,
) -> ObservationSolution:
    """A plate solver's outcome completed with the sky predicted for the recording's own capture time and place
    (`asset` is its SourceAsset): a new solution, the solver's left as it was.

    - No recorded `capturedAtUTC`, no `observerLocation`, or a frame moment outside 1800 to 2050: the solver's
      solution with one more warning saying which. No other time or place stands in.
    - Calibrated: status, WCS, quality and every finding stay; the deep-sky objects inside the frame that its keep
      rules hold for are added through the verified WCS (`predict_deep_sky_findings`), then the predicted objects not
      already catalog_matched (same catalog id) or already present (same finding id).
    - Unresolved or ambiguous, given `attitude` (the phone's pointing record with a measured heading, pointing-5), a
      back-camera photo stored at the record's photo size and a field of view the record gives: approximate, lined
      up from the record (`record_pose`, `_record_lined_up`), before any Moon pose, and the frames are not read;
      the solution's pointing hint is `hint` (the searches' own, or a new one) with `pose_estimate` added.
      A refused record pose adds its reason to the warnings below, and the Moon poses follow.
    - Unresolved or ambiguous, given `frames`, with a head-worn camera profile, the predicted Moon more than 5 degrees
      up, the Sun down, and one frame whose bright spot passes the Moon test and fits exactly one level pose at each
      swept value: approximate, lined up from that frame (`_lined_up`).
    - Else, with no head-worn profile or a head-worn search that found no pose in its frames (HORIZON_AFTER): the
      Moon plus horizon pose on the same frames, the first whose bright spot above a skyline passes the Moon test and
      fits at each swept skyline altitude and row: approximate, lined up from that frame.
      `frames` gives the frames to search, each as its time into the recording and its gray pixels, and is called
      at most once, only when a pose could follow; None asks for no pose (layer 1 alone). `stack` gives the pixels
      the solution's detections were measured on when they are none of `frames` (a clip's stack of its first
      frames), read only when a Sun pose lines a recording up, for the Sun's core in them.
    - Otherwise unresolved or ambiguous: predicted_only, with the solver's findings (pixel-only observed ones),
      quality, warnings and identity, the predicted objects added, and, when `frames` was given, a warning saying why
      each pose tried found none (the head-worn one's unless the camera has no profile, the horizon one's unless
      after a head-worn refusal it found no skyline).
    The moment is the solution's frame (`capturedAtUTC` plus `frameTimeSec`), written as `observedAtUTC`, and the
    last warning says where the Sun was. No warning holds the capture time, the place or the file name.

    `lent` says when the asset's time or place is a phone's pointing record's (the job lends them where the file
    holds none, pointing-moment): each predicted item then names the record, its place's stated accuracy joins
    each uncertainty, and the warning before the last says so (`lent_moment_sentence`)."""
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
    try:
        # The frame's own moment is judged (a recording that starts at 1799's last second has its frames in 1800); one
        # past the year 9999 a UTC instant is written in has no moment at all, and lies outside the range as surely
        moment = frame_moment(captured, solution.frame_time_sec)
        require_ephemeris_range(moment)
    except (EphemerisOutOfRange, OverflowError):
        return replace(solution, warnings=[*solution.warnings, OUT_OF_RANGE])
    lat, lon = place
    no_pose: list[str] = []
    if solution.status != "calibrated" and attitude is not None:
        size = _stored_size(asset)
        sensor = (
            Refusal("record_size", "")
            if size is None
            else record_pose(
                attitude, width=size[0], height=size[1], orientation=asset.get("displayOrientation"), moment=moment,
                lat=lat, lon=lon,
            )
        )
        if isinstance(sensor, RecordPose):
            return _record_lined_up(solution, asset, sensor, attitude, moment, lat, lon, lent, hint)
        no_pose.append(sensor.reason or NO_RECORD_SIZE)
    if (
        solution.status != "calibrated" and frames is not None and _focal_35mm(asset) is not None
        and topocentric_sun(moment, lat, lon).alt_deg > SUN_BEACON_MIN_ALT_DEG
    ):
        # By day the Sun is the beacon, never the Moon (REQ-SKY-4.3, sky-6d): the Sun plus horizon pose alone, when
        # the file gives the 35 mm focal length its clock check needs; without one the Moon poses refuse by day as
        # before, and no frame is read
        sun_pose = _sun_pose(asset, _read_once(frames), captured, lat, lon)
        if not isinstance(sun_pose, Refusal):
            pose, frame_time_sec, frame_at = sun_pose
            return _sun_lined_up(solution, asset, pose, frame_time_sec, frame_at, lat, lon, lent, stack)
        no_pose.append(sun_pose.reason)
    elif solution.status != "calibrated" and frames is not None:
        read = _read_once(frames)
        pose = _head_worn_pose(asset, read, captured, moment, lat, lon)
        if isinstance(pose, PoseResult):
            return _lined_up(solution, asset, pose, lat, lon, lent)
        if pose.kind != "no_profile":
            no_pose.append(pose.reason)
        if pose.kind in HORIZON_AFTER:
            horizon = _horizon_pose(asset, read, captured, moment, lat, lon)
            if isinstance(horizon, PoseResult):
                return _lined_up(solution, asset, horizon, lat, lon, lent)
            # After a head-worn refusal, a frame with no skyline adds nothing to say
            if pose.kind == "no_profile" or horizon.kind != "no_skyline":
                no_pose.append(horizon.reason)
    matched = frozenset(
        f.catalog_star.id
        for f in solution.findings
        if f.evidence_class == "catalog_matched" and f.catalog_star is not None
    )
    if solution.status == "calibrated" and solution.wcs is not None:
        # The objects inside the frame that its keep rules hold for, through the verified WCS (nothing when it is not)
        inside = predict_deep_sky_findings(solution, solution.wcs.width, solution.wcs.height).findings
        known = {f.id for f in solution.findings}
        solution = replace(solution, findings=[*solution.findings, *(f for f in inside if f.id not in known)])
    present = {f.id for f in solution.findings}
    time_sec = _worked_out_time_sec(asset)
    predicted = [
        f
        for f in predicted_sky_findings(
            moment, lat, lon, solution_id=solution.id, source_sha256=solution.source_sha256, exclude_ids=matched,
            time_uncertainty_sec=time_sec, lent=lent,
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
            *(() if lent is None else (lent_moment_sentence(lent),)),
            sky_phase(sun.alt_deg).sentence,
        ],
    )
