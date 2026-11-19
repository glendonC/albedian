"""A person's alignment of the predicted sky as a search hint (align-2; D24's Solve from here; plan A of
.lead/briefs/2026-11-18/ALIGN-LOCKON.md).

Covers, at a synthetic moment and an open-ocean place (never an owner's):
- The centre goes to ICRS in the convention the person aligned (sky-context's: airless altitude, azimuth from true
  north through east): the hinted direction, turned back to the horizontal at the same moment and place, is the
  person's centre within a milliarcsecond-level tolerance, by a property over the whole sky above the horizon
- The airless reading is the one taken: near the horizon it differs from the refracted reading (a pointing record's,
  pointing-4) by the refraction, so a person's hint is never read as a record's
- The stated uncertainty is the contract's (`PERSON_ALIGNMENT_UNCERTAINTY_DEG`), and the person's roll and field of
  view are carried as they came
- The hint needs a time and a place; a place out of range is refused by the catalog's own door, naming no value
- The source is the person's: the hint never claims a measurement
- The person's whole pose as three ICRS directions (the upright frame's centre, a step to its right and a step
  down), in a pose estimate's roll convention (a positive roll turns the picture clockwise, putting the
  horizon's right side lower), so the solver can try the pose itself before searching near it
- Those three directions as the camera's rows in the solver's own (refracted) sky frame, the camera coming back
  from them by a property over the sky and every roll; a mirrored set gives no camera
- The upright frame the person aligned turned to the stored frame (the display's quarter turns, pose_estimate's
  upright_vector), so the solver's camera is the stored pixels'; a mirrored display gives none
"""

from __future__ import annotations

import math

from hypothesis import given, settings
from hypothesis import strategies as st
import numpy as np
import pytest

from services.contracts.alignment import PERSON_ALIGNMENT_UNCERTAINTY_DEG, PersonAlignment
from services.science.catalog import altaz_to_icrs, icrs_to_altaz
from services.science.person_hint import (
    AXIS_STEP_DEG,
    PersonHint,
    camera_rotation,
    person_axes_icrs,
    person_hint,
    stored_axes_icrs,
)

MOMENT = "2031-03-20T09:30:00Z"
PLACE = (20.0, -140.0)


def alignment(alt: float = 40.0, az: float = 120.0, roll: float = -7.5, fov: float = 68.0) -> PersonAlignment:
    return PersonAlignment.model_validate(
        {
            "format": "albedian.person-alignment",
            "formatVersion": 1,
            "source": "person",
            "centerAltDeg": alt,
            "centerAzDeg": az,
            "rollDeg": roll,
            "fovDiagonalDeg": fov,
        }
    )


def separation_deg(alt1: float, az1: float, alt2: float, az2: float) -> float:
    """The angle between two horizontal directions, by atan2 of the cross and dot products (exact at small angles,
    where an arccosine loses about a microdegree)."""
    def vec(alt: float, az: float) -> tuple[float, float, float]:
        a, z = math.radians(alt), math.radians(az)
        return (math.cos(a) * math.sin(z), math.cos(a) * math.cos(z), math.sin(a))

    (x1, y1, z1), (x2, y2, z2) = vec(alt1, az1), vec(alt2, az2)
    cross = math.sqrt((y1 * z2 - z1 * y2) ** 2 + (z1 * x2 - x1 * z2) ** 2 + (x1 * y2 - y1 * x2) ** 2)
    return math.degrees(math.atan2(cross, x1 * x2 + y1 * y2 + z1 * z2))


def test_the_hinted_direction_is_the_persons_centre_at_the_solves_moment_and_place() -> None:
    hint = person_hint(alignment(), MOMENT, *PLACE)
    alt, az = icrs_to_altaz(hint.ra_deg, hint.dec_deg, *PLACE, MOMENT)
    assert separation_deg(alt, az, 40.0, 120.0) < 1e-6
    assert (hint.center_alt_deg, hint.center_az_deg) == (40.0, 120.0)


@settings(max_examples=60, deadline=None)
@given(
    alt=st.floats(min_value=-5.0, max_value=89.5, allow_nan=False),
    az=st.floats(min_value=0.0, max_value=359.999, allow_nan=False),
)
def test_every_centre_above_the_horizon_comes_back_from_icrs_as_the_person_left_it(alt: float, az: float) -> None:
    hint = person_hint(alignment(alt=alt, az=az), MOMENT, *PLACE)
    back_alt, back_az = icrs_to_altaz(hint.ra_deg, hint.dec_deg, *PLACE, MOMENT)
    assert separation_deg(back_alt, back_az, alt, az) < 1e-6
    assert 0.0 <= hint.ra_deg < 360.0 and -90.0 <= hint.dec_deg <= 90.0


def test_the_centre_is_read_as_airless_as_the_drawn_sky_is_never_as_refracted() -> None:
    hint = person_hint(alignment(alt=3.0, az=200.0), MOMENT, *PLACE)
    airless = altaz_to_icrs(3.0, 200.0, *PLACE, MOMENT, refracted=False)
    refracted = altaz_to_icrs(3.0, 200.0, *PLACE, MOMENT, refracted=True)
    assert (hint.ra_deg, hint.dec_deg) == pytest.approx(airless, abs=1e-9)
    # Three degrees up the refraction is about a quarter of a degree, so the two readings are far apart
    apart = separation_deg(refracted[1], refracted[0], airless[1], airless[0])
    assert apart > 0.1


def test_the_uncertainty_is_the_contracts_and_the_roll_and_field_of_view_are_the_persons() -> None:
    hint = person_hint(alignment(roll=12.25, fov=51.5), MOMENT, *PLACE)
    assert hint.uncertainty_deg == PERSON_ALIGNMENT_UNCERTAINTY_DEG
    assert hint.roll_deg == 12.25
    assert hint.fov_diagonal_deg == 51.5
    assert hint.source == "person"


def test_the_hint_is_frozen() -> None:
    hint = person_hint(alignment(), MOMENT, *PLACE)
    assert isinstance(hint, PersonHint)
    with pytest.raises(AttributeError):
        hint.uncertainty_deg = 1.0  # type: ignore[misc]


@pytest.mark.parametrize("lat, lon", [(91.0, 0.0), (0.0, 181.0), (float("nan"), 0.0)])
def test_a_place_out_of_range_is_refused_naming_no_value(lat: float, lon: float) -> None:
    with pytest.raises(ValueError) as refused:
        person_hint(alignment(), MOMENT, lat, lon)
    assert "91" not in str(refused.value) and "181" not in str(refused.value)


@pytest.mark.parametrize("moment", ["", "not a moment"])
def test_a_moment_the_ephemeris_cannot_read_is_refused(moment: str) -> None:
    with pytest.raises(ValueError):
        person_hint(alignment(), moment, *PLACE)


# ---- The person's whole pose as the camera's axes (align-2: the pose tried first as the search's answer) ----


def axes_altaz(alt: float, az: float, roll: float) -> list[tuple[float, float]]:
    points = person_axes_icrs(alignment(alt=alt, az=az, roll=roll), MOMENT, *PLACE)
    return [icrs_to_altaz(ra, dec, *PLACE, MOMENT) for ra, dec in points]


def test_the_axes_start_at_the_persons_centre_one_step_apart_and_square() -> None:
    (c_alt, c_az), (r_alt, r_az), (d_alt, d_az) = axes_altaz(35.0, 200.0, 17.0)
    assert separation_deg(c_alt, c_az, 35.0, 200.0) < 1e-6
    assert separation_deg(c_alt, c_az, r_alt, r_az) == pytest.approx(AXIS_STEP_DEG, abs=1e-6)
    assert separation_deg(c_alt, c_az, d_alt, d_az) == pytest.approx(AXIS_STEP_DEG, abs=1e-6)
    # The right and down steps are square to each other: the right-to-down distance is the step times the root of two
    # on so small a step, within the sphere's curvature
    assert separation_deg(r_alt, r_az, d_alt, d_az) == pytest.approx(AXIS_STEP_DEG * math.sqrt(2.0), rel=1e-4)


def test_a_level_frame_has_its_right_on_the_horizon_toward_larger_azimuth_and_its_down_below() -> None:
    (_, c_az), (r_alt, r_az), (d_alt, d_az) = axes_altaz(0.0, 10.0, 0.0)
    assert abs(r_alt) < 1e-6
    assert r_az > c_az
    assert d_alt == pytest.approx(-AXIS_STEP_DEG, abs=1e-6) and d_az == pytest.approx(c_az, abs=1e-6)


@pytest.mark.parametrize("roll", [5.0, 30.0, -12.0])
def test_a_positive_roll_puts_the_horizons_right_side_lower_as_a_pose_estimates_roll_does(roll: float) -> None:
    # On a level frame at the horizon, the right step leaves the horizon by the roll: up for a positive roll, so the
    # horizon passes below the frame's right edge and its right side is lower in the picture (pose_estimate's roll
    # undone gives the right step dy = -sin(roll), upward)
    _, (r_alt, _), _ = axes_altaz(0.0, 10.0, roll)
    expected = math.degrees(math.asin(math.sin(math.radians(AXIS_STEP_DEG)) * math.sin(math.radians(roll))))
    assert r_alt == pytest.approx(expected, abs=1e-6)


def test_a_half_turn_of_roll_turns_the_right_and_down_steps_round() -> None:
    _, (r0_alt, r0_az), (d0_alt, d0_az) = axes_altaz(30.0, 80.0, 0.0)
    _, (r1_alt, r1_az), (d1_alt, d1_az) = axes_altaz(30.0, 80.0, 180.0)
    assert separation_deg(r1_alt, r1_az, r0_alt, r0_az) == pytest.approx(2 * AXIS_STEP_DEG, rel=1e-3)
    assert separation_deg(d1_alt, d1_az, d0_alt, d0_az) == pytest.approx(2 * AXIS_STEP_DEG, rel=1e-3)
    # The picture upside down: its down step looks up
    assert d1_alt > 30.0 > d0_alt


# ---- The camera in the solver's own sky frame from the pose's three directions ----


def sky_frame():
    from services.science.phone_photo_solver import _ApparentSkyFrame

    return _ApparentSkyFrame.from_iso(MOMENT, *PLACE)


def rotation(alt: float, az: float, roll: float) -> np.ndarray:
    a, big_a, r = (math.radians(v) for v in (alt, az, roll))
    z = np.array([math.cos(a) * math.sin(big_a), math.cos(a) * math.cos(big_a), math.sin(a)])
    x0 = np.array([math.cos(big_a), -math.sin(big_a), 0.0])
    y0 = np.cross(z, x0)
    return np.stack([x0 * math.cos(r) - y0 * math.sin(r), x0 * math.sin(r) + y0 * math.cos(r), z])


def icrs_points(frame, rot: np.ndarray, step_deg: float = AXIS_STEP_DEG) -> list[tuple[float, float]]:
    s = math.radians(step_deg)
    x, y, z = rot
    ra, dec = frame.sky_vec_to_icrs(np.stack([z, z * math.cos(s) + x * math.sin(s), z * math.cos(s) + y * math.sin(s)]))
    return [(float(r), float(d)) for r, d in zip(ra, dec)]


@settings(max_examples=40, deadline=None)
@given(
    alt=st.floats(min_value=1.0, max_value=88.0, allow_nan=False),
    az=st.floats(min_value=0.0, max_value=359.9, allow_nan=False),
    roll=st.floats(min_value=-180.0, max_value=180.0, allow_nan=False),
)
def test_the_camera_comes_back_from_its_three_directions_in_the_solvers_sky_frame(alt: float, az: float, roll: float) -> None:
    frame = sky_frame()
    rot = rotation(alt, az, roll)
    built = camera_rotation(icrs_points(frame, rot), frame)
    assert built is not None
    turned = math.degrees(math.acos(max(-1.0, min(1.0, (float(np.trace(built @ rot.T)) - 1.0) / 2.0))))
    assert turned < 1e-4
    assert np.allclose(built @ built.T, np.eye(3), atol=1e-9) and np.linalg.det(built) > 0


def test_a_mirrored_pose_gives_no_camera() -> None:
    frame = sky_frame()
    rot = rotation(30.0, 100.0, 0.0)
    mirrored = np.stack([rot[0], -rot[1], rot[2]])
    assert camera_rotation(icrs_points(frame, mirrored), frame) is None


def test_the_persons_pose_becomes_a_camera_looking_where_the_person_put_the_centre() -> None:
    frame = sky_frame()
    points = person_axes_icrs(alignment(alt=25.0, az=300.0, roll=8.0), MOMENT, *PLACE)
    built = camera_rotation(points, frame)
    assert built is not None
    ra, dec = frame.sky_vec_to_icrs(built[2:3])
    assert (float(ra[0]), float(dec[0])) == pytest.approx(points[0], abs=1e-6)


# ---- The upright frame the person aligned, turned to the stored frame the solver's camera is ----


def test_with_no_turn_the_stored_frames_axes_are_the_upright_ones() -> None:
    a = alignment(alt=30.0, az=80.0, roll=12.0)
    assert stored_axes_icrs(a, MOMENT, *PLACE, quarter_turns_clockwise=0, mirrored=False) == person_axes_icrs(a, MOMENT, *PLACE)


@pytest.mark.parametrize("turns", [1, 2, 3])
def test_a_turned_frames_stored_right_and_down_are_where_the_display_shows_them(turns: int) -> None:
    from services.science.pose_estimate import upright_vector

    a = alignment(alt=30.0, az=80.0, roll=12.0)
    centre, right, down = person_axes_icrs(a, MOMENT, *PLACE)
    stored = stored_axes_icrs(a, MOMENT, *PLACE, quarter_turns_clockwise=turns, mirrored=False)
    assert stored is not None and stored[0] == centre
    frame = sky_frame()
    rot = camera_rotation((centre, right, down), frame)
    turned = camera_rotation(stored, frame)
    assert rot is not None and turned is not None
    # The stored right shows as upright_vector(1, 0) in the upright frame, the stored down as upright_vector(0, 1)
    for row, (sx, sy) in ((0, (1.0, 0.0)), (1, (0.0, 1.0))):
        ux, uy = upright_vector(sx, sy, turns)
        # Within 0.06 degree: the refracted sky is a little squeezed upward (about 0.02 degree over the half-degree
        # step at 30 degrees up), so a camera built from its right step and one built from its down step differ by
        # that; far inside the solver's first association of 1.15 degrees
        assert np.allclose(turned[row], ux * rot[0] + uy * rot[1], atol=1e-3)
    assert np.allclose(turned[2], rot[2], atol=1e-9)


def test_a_mirrored_frame_gives_no_stored_axes() -> None:
    assert stored_axes_icrs(alignment(), MOMENT, *PLACE, quarter_turns_clockwise=1, mirrored=True) is None
