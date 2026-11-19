"""Camera profiles for the head-worn Moon pose (spec predicted-sky-for-every-upload, REQ-SKY-3.2, design component 3).

Covers:
- The one profile, keyed by the exact QuickTime model tag of the owner's glasses: kind head_worn, a diagonal field
  of view of 80 to 110 degrees labelled [open], the range the phone benchmark already models for its head-worn class,
  and a source note that quotes Meta's 100 degree figure, video size and video processing for this model with where
  and when each was read, says the axis is not stated and that the smaller stored frames are a crop [inference], and
  notes that quote Meta's zoom for it
- The lookup matches the model string exactly: no case folding, no trimming, no near names, nothing for a missing
  or non-text model
- A profile states its range in order inside the contract's bounds, a label of [verified] or [open], and a source
"""

from __future__ import annotations

import pytest

from research.benchmarks.phone_photos.cameras import CAMERAS
from services.science.camera_profiles import (
    CAMERA_PROFILES,
    HEAD_WORN,
    META_RAY_BAN_DISPLAY_MODEL,
    CameraProfile,
    camera_profile,
    head_worn_profile,
)


def test_the_glasses_profile_is_keyed_by_their_exact_model_tag() -> None:
    assert META_RAY_BAN_DISPLAY_MODEL == "Meta Ray-Ban Display Glasses"
    assert list(CAMERA_PROFILES) == [META_RAY_BAN_DISPLAY_MODEL]
    profile = camera_profile("Meta Ray-Ban Display Glasses")
    assert profile is not None and profile.model == META_RAY_BAN_DISPLAY_MODEL
    assert profile.kind == HEAD_WORN == "head_worn"
    assert profile.fov_diagonal_deg_range == (80.0, 110.0)
    assert profile.fov_diagonal_deg_mid == 95.0
    assert profile.fov_label == "[open]"


def test_the_range_is_the_phone_benchmarks_modelled_head_worn_class() -> None:
    profile = camera_profile(META_RAY_BAN_DISPLAY_MODEL)
    assert CAMERAS["head-worn-video"].role == "head-worn"
    assert profile.fov_diagonal_deg_range == CAMERAS["head-worn-video"].fov_open_range_deg
    assert "research/benchmarks/phone_photos/cameras.py" in profile.fov_source


def test_the_source_note_says_where_the_figure_was_read_and_what_is_open() -> None:
    source = camera_profile(META_RAY_BAN_DISPLAY_MODEL).fov_source
    # Meta's own 100 degree figure for this model, read at source with its address and date, without its axis
    # One [verified] label for each of the three figures read at source: the compare page, the sheet, the help page
    assert source.count("[verified]") == 3 and "2026-11-18" in source
    assert "meta.com/ai-glasses/compare" in source and "\"100° field of view, left-oriented\"" in source
    assert "Meta Ray-Ban Display" in source and "axis" in source
    assert "no figure was found" not in source
    # Meta's video size for this model, from its specification sheet
    assert "meta.com/ai-glasses/meta-ray-ban-display-glasses-and-neural-band" in source
    assert "\"1440x1920 pixels @30 Fps\"" in source
    # Meta says its video is de-warped, stabilised and levelled, and the stored frames are smaller: a crop
    assert "meta.com/help/ai-glasses/951708387778102" in source
    assert "de-warping" in source and "electronic image stabilization" in source and "horizontal leveling" in source
    assert "1264 x 1680" in source and "1408 x 1888" in source and "[inference]" in source
    # So the 100 degrees does not fix a stored frame's diagonal, and the range keeps its [open] label
    assert "[open]" in source and "research/benchmarks/phone_photos/cameras.py" in source


def test_the_notes_quote_the_zoom_meta_states_for_this_model() -> None:
    notes = camera_profile(META_RAY_BAN_DISPLAY_MODEL).notes
    assert "\"3x digital zoom\"" in notes and "[verified]" in notes
    assert "gave no readable text" not in notes


@pytest.mark.parametrize(
    "model",
    [
        "meta ray-ban display glasses",
        "Meta Ray-Ban Display Glasses ",
        " Meta Ray-Ban Display Glasses",
        "Meta Ray-Ban Display",
        "Ray-Ban Meta Smart Glasses",
        "iPhone 17 Pro",
        "",
        None,
        42,
        ["Meta Ray-Ban Display Glasses"],
    ],
)
def test_only_the_exact_model_string_has_a_profile(model: object) -> None:
    assert camera_profile(model) is None


def test_the_head_worn_profile_comes_from_the_assets_camera_model() -> None:
    assert head_worn_profile({"cameraMetadata": {"make": "Meta", "model": META_RAY_BAN_DISPLAY_MODEL}}).kind == HEAD_WORN
    for asset in (
        {},
        {"cameraMetadata": None},
        {"cameraMetadata": "Meta Ray-Ban Display Glasses"},
        {"cameraMetadata": {"make": "Meta Ray-Ban Display Glasses"}},
        {"cameraMetadata": {"model": "iPhone 17 Pro"}},
    ):
        assert head_worn_profile(asset) is None


def _profile(**changes: object) -> CameraProfile:
    fields: dict = {
        "model": "A synthetic camera",
        "kind": HEAD_WORN,
        "fov_diagonal_deg_range": (80.0, 110.0),
        "fov_label": "[open]",
        "fov_source": "A synthetic profile for this test.",
        "notes": "Describes no real camera.",
    }
    fields.update(changes)
    return CameraProfile(**fields)


def test_a_profile_states_an_ordered_range_inside_the_contracts_bounds_with_its_label_and_source() -> None:
    assert _profile().fov_diagonal_deg_mid == 95.0
    for bad in (
        {"fov_diagonal_deg_range": (110.0, 80.0)},
        {"fov_diagonal_deg_range": (80.0, 80.0)},
        {"fov_diagonal_deg_range": (0.0, 80.0)},
        {"fov_diagonal_deg_range": (80.0, 176.0)},
        {"fov_diagonal_deg_range": (float("nan"), 80.0)},
        {"fov_label": "[proposed]"},
        {"fov_label": "verified"},
        {"kind": "phone"},
        {"fov_source": ""},
        {"model": ""},
    ):
        with pytest.raises(ValueError):
            _profile(**bad)
