"""C32 (.lead/uiux/UIUX-moonposewords-READY.md, decided 2026-11-21): every Moon-pose refusal that carries numbers says
its reason in a first sentence with no number in it, and its numbers in a second sentence kept in the record (C21's
shape, C5's rule). The web shows the first sentence only (onScreenWarning in apps/web/src/reveal/revealStatus.ts cuts
a warning of this family at its first ". "); both stay in the solution's record."""

from __future__ import annotations

import re

import pytest

from services.science import pose_estimate as pe
from services.science.camera_profiles import HEAD_WORN, CameraProfile

P = "The sky is not lined up over the picture: "
SPOT = "(412, 233) px in the frame 2.00 s into the clip"


def _profile(low: float, high: float) -> CameraProfile:
    return CameraProfile(
        model="Test glasses", kind=HEAD_WORN, fov_diagonal_deg_range=(low, high), fov_label="[open]",
        fov_source="A test profile.", notes="",
    )


# Each refusal with the code's own values, and the two sentences C32 gives it, word for word
CASES = {
    "two_poses": (
        lambda: pe.two_poses_reason(412.3, 232.8, "in the frame 2.00 s into the clip"),
        P + "the bright spot taken to be the Moon fits two different directions of view, so which way the camera faced "
        "is not known.",
        f"The spot is at {SPOT}; both directions keep a level head inside the stated field of view and roll.",
    ),
    "ranges": (
        lambda: pe.ranges_reason(412.3, 232.8, "in the frame 2.00 s into the clip", _profile(80.0, 110.0)),
        P + "at one or more of the fields of view and head rolls the camera is stated to have, no level head puts the "
        "Moon on the bright spot.",
        f"The spot is at {SPOT}; the field of view was 80 to 110 degrees across the diagonal and the roll within 10 "
        "degrees either way.",
    ),
    "fit": (
        lambda: pe.fit_reason(412.3, 232.8, "in the frame 2.00 s into the clip"),
        P + "the fit could not put the Moon on the bright spot with a level head.",
        f"The spot is at {SPOT}; the fit allows 0.5 px.",
    ),
    "not_moon": (
        lambda: pe.not_confirmed_reason("in the 2 frames searched"),
        P + "what was seen in the 2 frames searched was not confirmed as the Moon.",
        "The Moon test needs a spot whose peak is at 200 of 255 or more and at least 10 times the noise above the sky, "
        "that is whole inside the frame, that measures at half its peak between 0.8 times the Moon's predicted "
        "diameter at the widest field of view and 8 times it at the narrowest, and that is the only spot in the frame "
        "passing this test.",
    ),
    "ranges_one_value": (
        lambda: pe.level_ranges_reason(412.3, 232.8, "in the frame 2.00 s into the clip", 110.0, -10.0),
        P + "no level head puts the Moon on the bright spot at this field of view and roll.",
        f"The spot is at {SPOT}; the field of view was 110 degrees across the diagonal and the roll -10 degrees.",
    ),
    "horizon_fit": (
        lambda: pe.horizon_fit_reason(412.3, 232.8, "in the frame 2.00 s into the clip", 610.2),
        P + "the fit found no camera that puts the Moon on the bright spot with the skyline level.",
        f"The spot is at {SPOT} and the skyline at row 610; the fit allows 0.5 px.",
    ),
    "horizon_start": (
        lambda: pe.horizon_start_reason(412.3, 232.8, "in the frame 2.00 s into the clip", 610.2),
        P + "the bright spot is not above the skyline, or the Moon is not above the skyline's altitude, so no fit could "
        "start.",
        f"The spot is at {SPOT} and the skyline at row 610.",
    ),
    "horizon_upside_down": (
        lambda: pe.horizon_upside_down_reason(412.3, 232.8, "in the frame 2.00 s into the clip", 610.2),
        P + "the only camera the fit found shows the sky upside down.",
        f"The spot is at {SPOT} and the skyline at row 610.",
    ),
    "horizon_fov": (
        lambda: pe.horizon_fov_reason(412.3, 232.8, "in the frame 2.00 s into the clip", 610.2, 139.4),
        P + "with the bright spot taken to be the Moon and the skyline taken to be level ground, the field of view they "
        "give is outside what a camera is taken to have.",
        f"The spot is at {SPOT} and the skyline at row 610; they give 139 degrees across the diagonal, outside 20 to 130.",
    ),
    "horizon_size": (
        lambda: pe.horizon_size_reason(412.3, 232.8, "in the frame 2.00 s into the clip", 5.23, 9.27),
        P + "the bright spot is not the Moon's size at the field of view the skyline gives.",
        f"It measures 5.2 px across at half its peak, 9.3 times the Moon's predicted diameter, outside 0.8 to 8 times; "
        f"it is at {SPOT}.",
    ),
    "not_moon_above_skyline": (
        lambda: pe.not_confirmed_above_skyline_reason("in the photo"),
        P + "what was seen above the skyline in the photo was not confirmed as the Moon.",
        "The Moon test needs a spot there whose peak is at 200 of 255 or more and at least 10 times the noise above the "
        "sky, that is whole inside the frame and clear of the skyline, that measures at half its peak between 0.8 "
        "times the Moon's predicted diameter at 130 degrees across the diagonal and 8 times it at 20, and that is the "
        "only spot above the skyline passing this test.",
    ),
}


def _on_screen(warning: str) -> str:
    """The web's cut for this family (onScreenWarning): up to and including the first full stop followed by a space."""
    end = warning.find(". ")
    return warning if end < 0 else warning[: end + 1]


@pytest.mark.parametrize("kind", sorted(CASES))
def test_each_moon_pose_refusal_says_c32s_two_sentences(kind: str) -> None:
    make, shown, kept = CASES[kind]
    assert make() == f"{shown} {kept}"


@pytest.mark.parametrize("kind", sorted(CASES))
def test_the_screen_shows_the_reason_whole_and_no_number_but_a_count_of_frames(kind: str) -> None:
    make, shown, _ = CASES[kind]
    reason = make()
    assert _on_screen(reason) == shown
    # C32's rule: the first sentence holds no number but the count of frames searched
    assert re.findall(r"\d", shown.replace("in the 2 frames searched", "")) == [], shown


@pytest.mark.parametrize("where", ["in the photo", "in the frame searched", "in the 3 frames searched",
                                   "in the frame 12.34 s into the clip"])
def test_the_place_named_never_cuts_a_first_sentence_short(where: str) -> None:
    """A clip's frame time sits in the second sentence only, and no place named carries a full stop and a space."""
    for reason, first in (
        (pe.two_poses_reason(1.0, 2.0, where), "fits two different directions of view, so which way"),
        (pe.fit_reason(1.0, 2.0, where), "the fit could not put the Moon on the bright spot with a level head."),
        (pe.horizon_size_reason(1.0, 2.0, where, 3.0, 9.0), "is not the Moon's size at the field of view the skyline"),
        (pe.not_confirmed_reason(where), f"what was seen {where} was not confirmed as the Moon."),
    ):
        assert first in _on_screen(reason), reason
        assert reason.count(". ") == 1, reason
