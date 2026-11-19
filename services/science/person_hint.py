"""A person's alignment of the predicted sky turned into a search hint (align-2; D24's Solve from here).

The person drags, turns and scales the predicted sky the web draws over their photo until its stars sit on the points
of light they see; the pose they leave (`PersonAlignment`, services/contracts/alignment.py) is in that drawing's
convention, sky-context's: topocentric at sea level, azimuth from true north through east, altitude apparent and
airless. Its centre is turned to ICRS at the solve's own time and place with the airless reading (a pointing record's
altitude is read as refracted instead, pointing-4, since the camera saw it through the air), and the searches keep
only what lies within the contract's stated uncertainty of it; its field of view is the first search's.

A hint is a hint: the held-out gate is unchanged, so a wrong alignment can lose a solve but never make one, and
nothing placed from it is observed or catalog matched. A refused gate leaves it the person's, predicted.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Literal, Sequence

import numpy as np

from services.contracts.alignment import PERSON_ALIGNMENT_UNCERTAINTY_DEG, PersonAlignment
from services.science.catalog import altaz_to_icrs

# How far from the centre the pose's right and down directions are taken, in degrees: small beside any frame, large
# beside the transform's rounding (align-2)
AXIS_STEP_DEG = 0.5

@dataclass(frozen=True)
class PersonHint:
    """Where the person put the frame's centre, as the horizontal they aligned and as ICRS at the solve's moment and
    place, how far the searches look from it (the contract's stated uncertainty), and the person's roll and diagonal
    field of view, as they came."""

    center_alt_deg: float
    center_az_deg: float
    ra_deg: float
    dec_deg: float
    uncertainty_deg: float
    roll_deg: float
    fov_diagonal_deg: float
    source: Literal["person"] = "person"


def person_axes_icrs(
    alignment: PersonAlignment, obs_utc_iso: str, lat_deg: float, lon_deg: float, step_deg: float | None = None
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    """The person's whole pose as three ICRS directions at the solve's moment and place: the upright frame's centre,
    a step (`AXIS_STEP_DEG`) toward its right edge and a step toward its bottom edge, so a solver can build its camera
    in its own sky frame from them, refraction and all, and try the pose itself before searching near it.

    In the drawn sky's horizontal (airless, east, north, up), a level frame looking at altitude a and azimuth A has its
    axis z = (cos a sin A, cos a cos A, sin a), its right x0 = (cos A, -sin A, 0) and its down y0 = z x x0. A roll r
    turns the picture clockwise, as a pose estimate's roll does (services/science/pose_estimate.py, `level_centres`:
    the roll undone is dx = ux cos r + uy sin r, dy = -ux sin r + uy cos r), so the frame's right is
    x0 cos r - y0 sin r and its down x0 sin r + y0 cos r: a positive roll puts the horizon's right side lower."""
    z, right, down = _upright_axes(alignment)
    return _axes_icrs(z, right, down, obs_utc_iso, lat_deg, lon_deg, step_deg)


def _upright_axes(alignment: PersonAlignment) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The upright frame's axis, right and down in the drawn sky's horizontal (east, north, up), as person_axes_icrs
    states them."""
    a, big_a = math.radians(float(alignment.center_alt_deg)), math.radians(float(alignment.center_az_deg))
    r = math.radians(float(alignment.roll_deg))
    z = np.array([math.cos(a) * math.sin(big_a), math.cos(a) * math.cos(big_a), math.sin(a)])
    x0 = np.array([math.cos(big_a), -math.sin(big_a), 0.0])
    y0 = np.cross(z, x0)
    return z, x0 * math.cos(r) - y0 * math.sin(r), x0 * math.sin(r) + y0 * math.cos(r)


def _axes_icrs(
    z: np.ndarray, right: np.ndarray, down: np.ndarray, obs_utc_iso: str, lat_deg: float, lon_deg: float,
    step_deg: float | None,
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    step = math.radians(AXIS_STEP_DEG if step_deg is None else float(step_deg))
    points = []
    for vector in (z, z * math.cos(step) + right * math.sin(step), z * math.cos(step) + down * math.sin(step)):
        east, north, up = (float(v) for v in vector / np.linalg.norm(vector))
        alt = math.degrees(math.asin(max(-1.0, min(1.0, up))))
        az = math.degrees(math.atan2(east, north)) % 360.0
        points.append(altaz_to_icrs(alt, az, lat_deg, lon_deg, obs_utc_iso, refracted=False))
    return points[0], points[1], points[2]


def stored_axes_icrs(
    alignment: PersonAlignment,
    obs_utc_iso: str,
    lat_deg: float,
    lon_deg: float,
    *,
    quarter_turns_clockwise: int,
    mirrored: bool,
    step_deg: float | None = None,
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]] | None:
    """`person_axes_icrs` for the stored frame the solver's camera is: the person aligned the upright frame (the stored
    pixels turned by the display's quarter turns clockwise, SourceAsset.displayOrientation), so the stored right is the
    upright offset upright_vector(1, 0, turns) and the stored down upright_vector(0, 1, turns)
    (services/science/pose_estimate.py). None for a mirrored display, which no camera of the solver's takes."""
    if mirrored:
        return None
    turns = int(quarter_turns_clockwise) % 4
    z, right, down = _upright_axes(alignment)

    def upright(dx: float, dy: float) -> tuple[float, float]:
        for _ in range(turns):
            dx, dy = -dy, dx
        return dx, dy

    (rx, ry), (dx, dy) = upright(1.0, 0.0), upright(0.0, 1.0)
    return _axes_icrs(z, right * rx + down * ry, right * dx + down * dy, obs_utc_iso, lat_deg, lon_deg, step_deg)


def camera_rotation(axes_icrs: Sequence[tuple[float, float]], sky_frame: Any) -> np.ndarray | None:
    """The camera's rows (image right, image down, optical axis) in a solver's sky frame from three ICRS directions:
    the frame's centre, a step toward its right edge and a step toward its bottom edge (`person_axes_icrs`). The axis
    is the centre; the right is the right step square to it; the down is the axis crossed with the right, a camera
    with rows right, down and forward being right-handed. None when the down step lies on the other side, a mirrored
    pose no camera takes. `sky_frame` is the phone solver's (its `icrs_to_sky_vec`), refraction and all."""
    ra = np.array([float(p[0]) for p in axes_icrs])
    dec = np.array([float(p[1]) for p in axes_icrs])
    vectors = sky_frame.icrs_to_sky_vec(ra, dec)
    z = vectors[0] / np.linalg.norm(vectors[0])
    x = vectors[1] - vectors[0]
    x = x - z * float(x @ z)
    x = x / np.linalg.norm(x)
    y = np.cross(z, x)
    if float(y @ (vectors[2] - vectors[0])) <= 0.0:
        return None
    return np.stack([x, y, z])


def person_hint(alignment: PersonAlignment, obs_utc_iso: str, lat_deg: float, lon_deg: float) -> PersonHint:
    """The search hint a person's alignment gives at the solve's time and place: its centre read as airless, as the
    drawn sky is. A place out of range or a moment the ephemeris cannot read is refused by the catalog's own doors
    (ValueError, naming no value)."""
    alt, az = float(alignment.center_alt_deg), float(alignment.center_az_deg)
    ra, dec = altaz_to_icrs(alt, az, lat_deg, lon_deg, obs_utc_iso, refracted=False)
    return PersonHint(
        center_alt_deg=alt,
        center_az_deg=az,
        ra_deg=ra,
        dec_deg=dec,
        uncertainty_deg=PERSON_ALIGNMENT_UNCERTAINTY_DEG,
        roll_deg=float(alignment.roll_deg),
        fov_diagonal_deg=float(alignment.fov_diagonal_deg),
    )
