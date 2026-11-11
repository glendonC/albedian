"""The sky predicted for a recording's own capture time and place (layer 1 of spec predicted-sky-for-every-upload).

Every finding made here is `predicted`: catalog stars carried to the date by their proper motion, and the Sun, the
Moon and five planets from the low-precision ephemeris (`services/science/ephemeris.py`), each placed for the
recorded time and place by deterministic math and never measured in the recording. They carry no detection and no
pixel position. A plate solver's outcome is completed with them after the solve, and nothing it measured changes.
When the solve is not calibrated and the camera has a head-worn profile, layer 2 lines them up over one frame from
the Moon, the profile's field of view and a level head (`services/science/pose_estimate.py`): an `approximate`
solution with its swept uncertainty, never checked against stars.
docs/science/PREDICTED_SKY.md describes both layers in plain words.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import math
from typing import Any

import numpy as np

from services.science.camera_profiles import head_worn_profile
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
from services.science.pose_estimate import (
    MIRRORED,
    MOON_NOT_UP,
    NO_PROFILE,
    ROLL_LIMIT_DEG,
    PoseResult,
    Refusal,
    is_mirrored,
    moon_is_up,
    pose_from_frames,
)
from services.science.wcs_models import (
    CelestialFindingRecord,
    ObservationSolution,
    SolutionQuality,
    SolverIdentity,
    SourceDetection,
)

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


FramesForPose = Callable[[], Sequence[tuple[float, np.ndarray]]]
POSE_SOLVER = SolverIdentity(
    name="AlbedianHeadWornMoonPose",
    version="1.0.0",
    catalog_release=(
        "A camera profile's field of view (services/science/camera_profiles.py) and the low-precision Moon "
        "ephemeris (Meeus 1998 chapter 47), lowered by its parallax"
    ),
)
INSIDE_THE_FRAME = " · Inside the frame (lined up, approximate)"
OUTSIDE_THE_FRAME = " · Outside the frame"
LINED_UP_LIMITATION = (
    "Placed in the frame only through the approximate pose lined up from the Moon, the camera profile's field of view "
    "and a level head; not checked against stars."
)
# Two frame times are the same frame within a microsecond (the job writes n / fps for both)
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
    detector's pixel-only findings minus those on the Moon in the same frame, the observed Moon, and the predicted
    sky for the frame's moment, each saying whether the nominal WCS puts it inside the frame."""
    blob, wcs, frame_time_sec = pose.blob, pose.wcs, pose.frame_time_sec
    kept: list[CelestialFindingRecord] = []
    other_frames = False
    for finding in solution.findings:
        detection = finding.source_detection
        if finding.evidence_class != "observed" or detection is None:
            continue
        if abs(detection.frame_time_sec - frame_time_sec) <= SAME_FRAME_SEC:
            # The Moon is listed once: a detection of the same frame inside the blob's radius is the Moon itself
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
        title="Moon (Observed Bright Spot)",
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
            "Measured in the frame's pixels: the frame's brightest spot, about the Moon's size at half its peak and "
            "with no rival of half its area there, matched to the Moon's predicted position; no star was matched or "
            "held out"
        ),
        verified_by_holdout=False,
        limitations=(
            "Without a verified WCS the detection has no sky position; the predicted Moon carries the ephemeris "
            "position.",
            "The Moon's light centroid sits somewhere on its disk: overexposure spreads it, and a partly lit Moon's "
            "sits toward its lit edge; the pose's centroid term, the Moon's predicted radius, covers that. Its flux "
            "and SNR are lower bounds where its brightest pixels are clipped.",
            *((OTHER_FRAMES_KEPT,) if other_frames else ()),
        ),
        source_sha256=solution.source_sha256,
    )
    predicted = []
    for finding in predicted_sky_findings(
        pose.moment, lat, lon, solution_id=solution.id, source_sha256=solution.source_sha256
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
    within = math.ceil(estimate.max_center_offset_deg + estimate.beacon_centroid_uncertainty_deg)
    lined_up = (
        f"Lined up from the Moon at ({blob.x_px:.0f}, {blob.y_px:.0f}) px {where}, the camera profile's field of view "
        f"of {low:g} to {high:g} degrees across the diagonal with no zoom, and a level head within {ROLL_LIMIT_DEG:g} "
        "degrees: "
        f"within about {within} degrees near the Moon, more toward the edges; not checked against stars."
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
        # The Moon is the one detection matched, to its own predicted position; no star was held out
        quality=SolutionQuality(
            detected_candidate_count=detected,
            matched_inlier_count=1,
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
        warnings=[*solution.warnings, lined_up, sky_phase(sun.alt_deg).sentence],
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
      up and one frame that passes the Moon test and the fit: approximate, lined up from that frame (`_lined_up`).
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
        warnings=[*solution.warnings, *no_pose, sky_phase(sun.alt_deg).sentence],
    )
