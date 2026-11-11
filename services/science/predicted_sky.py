"""The sky predicted for a recording's own capture time and place (layer 1 of spec predicted-sky-for-every-upload).

Every finding made here is `predicted`: catalog stars carried to the date by their proper motion, and the Sun, the
Moon and five planets from the low-precision ephemeris (`services/science/ephemeris.py`), each placed for the
recorded time and place by deterministic math and never measured in the recording. They carry no detection and no
pixel position: without a pose nothing places them in the frame. A plate solver's outcome is completed with them
after the solve, and nothing it measured changes.
docs/science/PREDICTED_SKY.md describes the layer in plain words.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import math
from typing import Any

from services.science.catalog import (
    _iso_to_julian_date,
    get_all_sky_catalog_stars,
    icrs_to_altaz,
    propagate_star_epoch,
)
from services.science.ephemeris import (
    EphemerisOutOfRange,
    compute_solar_system_ephemerides,
    require_ephemeris_range,
)
from services.science.wcs_models import CelestialFindingRecord, ObservationSolution

# A predicted star, carried to the date by its proper motion: the horizon transform leaves out nutation and annual
# aberration (at most 37 arcsec, tests/integration/test_sky_transforms.py) and UT1 - UTC (at most 14 arcsec), and
# the pinned positions sit within 3 arcsec of their catalog rows (test_pinned_catalog_matches_tiles.py)
PREDICTED_STAR_UNCERTAINTY_ARCSEC = 55.0
PREDICTED_BODIES = ("Sun", "Moon", "Venus", "Mars", "Jupiter", "Saturn", "Uranus")

# The Sun's centre altitude at the edges of day and twilight, from USNO's "Rise, Set, and Twilight Definitions"
# (aa.usno.navy.mil/faq/RST_defs): sunrise and sunset at a geometric zenith distance of 90.8333 degrees, the centre
# 50 arcminutes below the horizon (16 for the Sun's radius and 34 for refraction); civil, nautical and astronomical
# twilight end at zenith distances of 96, 102 and 108 degrees
SUNRISE_SUNSET_ALT_DEG = -50.0 / 60.0
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
STAR_POSITION_LIMITATION = (
    "Its position leaves out nutation, annual aberration and UT1 - UTC, which its 55 arcsec uncertainty covers, and "
    "its altitude leaves out refraction, which lifts what is near the horizon."
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
    moment = start.astimezone(timezone.utc) + timedelta(seconds=float(frame_time_sec))
    if moment.microsecond == 0:
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


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


def predicted_sky_findings(
    moment: str,
    lat_deg: float,
    lon_deg: float,
    *,
    solution_id: str,
    source_sha256: str,
    exclude_ids: frozenset[str] = frozenset(),
) -> list[CelestialFindingRecord]:
    """The Sun, the Moon, the five planets and the pinned all-sky bright stars (stars carried to the date by their
    proper motion) above the horizon at `moment` from (`lat_deg`, `lon_deg`), as predicted findings of the solution
    `solution_id`: bodies first in `PREDICTED_BODIES` order, then stars brightest first.

    Above the horizon means a geometric (airless) altitude above 0. `exclude_ids` names catalog stars to leave
    out, by catalog id. Raises EphemerisOutOfRange outside 1800 to 2050."""
    require_ephemeris_range(moment)
    findings: list[CelestialFindingRecord] = []
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
                uncertainty_arcsec=body.uncertainty_arcsec,
                verified_by_holdout=False,
                limitations=(
                    f"Predicted with {body.method}, for the recorded capture time and place; not measured in the "
                    "recording.",
                    *body.limitations[1:],
                ),
                source_sha256=source_sha256,
            )
        )
    year = _julian_year(moment)
    for catalog_star in sorted(get_all_sky_catalog_stars(), key=lambda s: (s.mag, s.id)):
        if catalog_star.id in exclude_ids:
            continue
        star = propagate_star_epoch(catalog_star, year)
        alt, az = icrs_to_altaz(star.ra_deg, star.dec_deg, lat_deg, lon_deg, moment)
        if alt <= 0.0:
            continue
        findings.append(
            CelestialFindingRecord(
                id=f"finding-predicted-{star.id}",
                observation_solution_id=solution_id,
                evidence_class="predicted",
                # A star with no IAU name shows its Bayer designation as its name: not twice
                title=(
                    star.common_name
                    if star.bayer_designation == star.common_name
                    else f"{star.common_name} ({star.bayer_designation})"
                ),
                subtitle=(
                    f"Predicted Star ({star.constellation}) · {star.median_pc:.1f} pc · "
                    f"Alt {alt:+.1f}° Az {az:.1f}° · Above Horizon"
                ),
                ra_deg=star.ra_deg,
                dec_deg=star.dec_deg,
                alt_deg=alt,
                az_deg=az,
                uncertainty_arcsec=PREDICTED_STAR_UNCERTAINTY_ARCSEC,
                catalog_star=star,
                verified_by_holdout=False,
                limitations=(
                    f"Predicted from its {star.release_version} catalog position, carried to the date by its proper "
                    "motion, for the recorded capture time and place; not measured in the recording.",
                    STAR_POSITION_LIMITATION,
                ),
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


def complete_sky(solution: ObservationSolution, asset: Mapping[str, Any]) -> ObservationSolution:
    """A plate solver's outcome completed with the sky predicted for the recording's own capture time and place
    (`asset` is its SourceAsset): a new solution, the solver's left as it was.

    - No recorded `capturedAtUTC`, no `observerLocation`, or a frame moment outside 1800 to 2050: the solver's
      solution with one more warning saying which. No other time or place stands in.
    - Calibrated: status, WCS, quality and every finding stay; the predicted objects not already catalog_matched
      (same catalog id) are added.
    - Unresolved or ambiguous: predicted_only, with the solver's findings (pixel-only observed ones), quality,
      warnings and identity, and the predicted objects added.
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
    matched = frozenset(
        f.catalog_star.id
        for f in solution.findings
        if f.evidence_class == "catalog_matched" and f.catalog_star is not None
    )
    present = {f.id for f in solution.findings}
    predicted = [
        f
        for f in predicted_sky_findings(
            moment, lat, lon, solution_id=solution.id, source_sha256=solution.source_sha256, exclude_ids=matched
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
        warnings=[*solution.warnings, sky_phase(sun.alt_deg).sentence],
    )
