"""The pointing record contract (REQ-IOS-7, P4): the Zod schema and its Pydantic mirror judge the same records alike.

`PointingRecordSchema` in packages/contracts/src/pointing.ts and `PointingRecord` in services/contracts/pointing.py.
tests/fixtures/pointing_contract.json is written from `build_pointing_samples()` here and parsed by
packages/contracts/src/__tests__/pointing.test.ts. Regenerate it after a deliberate change with:
    ALBEDIAN_UPDATE_POINTING_FIXTURE=1 uv run pytest tests/integration/test_pointing_contract.py

Covers:
- The fixture is current: valid samples (a measured heading and a place, a heading at the limit, each reason for no
  heading, each reason for no place, an unknown field of view) and one named fault per rule, each with its verdict
- The mirror accepts every valid sample unchanged and refuses every fault, in the contract's own words where the
  rule is the contract's own
- The limits, enums, rule messages and the evidence statement are the ones pointing.ts writes
- The heading's accuracy limit, the motion sample's offset limit, the unit quaternion tolerance and gravity's length
  band at their edges
- A whole number written with a fraction (4032.0) counts as a whole number, as Zod reads it
- The wire rules (.lead/briefs/WIRE_RULES.md), held here until the shared wire module is on main: camelCase keys only,
  safe and bounded integers, exact patterns for the two device labels, no year 0. The auditor's 17 records that the
  two sides once judged apart are faults in the fixture, and the generated wire faults (every key under its Python
  name, every number, text, time and flag replaced by one a wire rule names) carry the one verdict both sides give
- P4 (REQ-IOS-7.1): generated records, with and without a heading and a place, and with any one field set to a value
  from the shared table of variants, accepted exactly when the oracle the TypeScript property shares says so

Every value is synthetic: the synthetic moment and open-ocean place of the other fixtures, decimal sensor readings
chosen by hand, and hashes of fixed test strings. Nothing comes from the owner's devices.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import pytest
from hypothesis import event, example, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from services.contracts import pointing
from services.contracts.pointing import PointingRecord

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "pointing_contract.json"
POINTING_TS = ROOT / "packages" / "contracts" / "src" / "pointing.ts"
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_POINTING_FIXTURE"

# The synthetic moment and open-ocean place the other fixtures use; they point at nobody
MOMENT = "2030-01-01T02:00:00Z"
PLACE = {"latDeg": 30.0, "lonDeg": -40.0}
SHA = hashlib.sha256(b"synthetic pointing record photo").hexdigest()
OTHER_SHA = hashlib.sha256(b"synthetic pointing record photo, second").hexdigest()

# A unit quaternion and a gravity vector written as exact decimals: 0.04 + 0.16 + 0.16 + 0.64 is 1, and the gravity
# vector is 0.6 and 0.8 of one g. They describe no real attitude [proposed: test inputs only]
QUATERNION = {"x": 0.2, "y": -0.4, "z": 0.4, "w": 0.8}
GRAVITY = {"x": 0.0, "y": -0.6, "z": -0.8}


class _Drop:
    pass


DROP = _Drop()


def with_(base: dict[str, Any], path: list[Any], value: Any) -> dict[str, Any]:
    """A deep copy of `base` with the value at `path` replaced, or removed for DROP."""
    out = copy.deepcopy(base)
    target = out
    for key in path[:-1]:
        target = target[key]
    if value is DROP:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return out


def base_record() -> dict[str, Any]:
    """A record with a measured heading and an allowed place: every block filled."""
    return {
        "format": pointing.POINTING_RECORD_FORMAT,
        "formatVersion": pointing.POINTING_RECORD_FORMAT_VERSION,
        "sourceAssetSha256": SHA,
        "capturedAtDeviceClockUTC": MOMENT,
        "device": {"hardwareModel": "Synthetic1,1", "systemVersion": "27.1"},
        "camera": {
            "position": "back",
            "deviceType": "wide_angle",
            "activeFormatDimensionsPx": {"width": 4032, "height": 3024},
            "photoDimensionsPx": {"width": 4032, "height": 3024},
            "fieldOfView": {"state": "reported", "horizontalDeg": 69.5, "distortionCorrected": True},
            "videoZoomFactor": 1.0,
            "contentAwareDistortionCorrection": False,
        },
        "motion": {
            "sampleOffsetFromPhotoSec": -0.004,
            "gravityDeviceG": dict(GRAVITY),
            "rotationRateDeviceRadPerSec": {"x": 0.002, "y": -0.001, "z": 0.0005},
            "window": {"startOffsetSec": -0.5, "endOffsetSec": 10.5, "sampleCount": 1101, "maxRotationRateRadPerSec": 0.004},
        },
        "heading": {
            "state": "measured",
            "attitude": {"referenceFrame": "x_true_north_z_vertical", "quaternion": dict(QUATERNION)},
            "trueHeadingDeg": 212.5,
            "headingAccuracyDeg": 12.0,
            "headingOrientation": "portrait",
            "eventOffsetFromPhotoSec": -1.25,
        },
        "location": {
            "state": "allowed",
            **PLACE,
            "horizontalAccuracyM": 8.0,
            "accuracyAuthorization": "full",
            "altitude": {"aboveMeanSeaLevelM": 12.0, "aboveEllipsoidM": 45.5, "verticalAccuracyM": 3.0},
            "fixOffsetFromPhotoSec": -2.5,
        },
        "evidence": {"role": "pointing_hint", "statement": pointing.POINTING_HINT_STATEMENT},
    }


def no_heading(reason: str, reported: float | None = None) -> dict[str, Any]:
    heading: dict[str, Any] = {"state": "none", "reason": reason}
    if reported is not None:
        heading["reportedHeadingAccuracyDeg"] = reported
    return heading


def no_place(reason: str) -> dict[str, Any]:
    return {"state": "none", "reason": reason}


def reduced_place() -> dict[str, Any]:
    """A place under reduced accuracy, with no valid altitude."""
    return {"state": "allowed", **PLACE, "horizontalAccuracyM": 3000.0, "accuracyAuthorization": "reduced", "fixOffsetFromPhotoSec": -40.0}


def valid_samples() -> dict[str, dict[str, Any]]:
    base = base_record()
    limit = pointing.HEADING_ACCURACY_LIMIT_DEG
    return {
        "measured_heading_and_place": base,
        "heading_at_the_limit": with_(base, ["heading", "headingAccuracyDeg"], limit),
        "heading_outside_the_limit": {
            **with_(base, ["heading"], no_heading("accuracy_outside_limit", 41.5)),
            "location": reduced_place(),
        },
        "heading_invalid": with_(base, ["heading"], no_heading("heading_invalid")),
        "no_heading_event": with_(base, ["heading"], no_heading("no_heading_event")),
        "no_magnetometer": with_(base, ["heading"], no_heading("no_magnetometer")),
        "place_not_allowed": {**with_(base, ["heading"], no_heading("no_place")), "location": no_place("not_allowed")},
        "no_fix": {**with_(base, ["heading"], no_heading("no_place")), "location": no_place("no_fix")},
        "simulated_place": {
            **with_(base, ["heading"], no_heading("no_place")),
            "location": no_place("simulated_by_software"),
        },
        "no_magnetometer_and_no_place": {
            **with_(base, ["heading"], no_heading("no_magnetometer")),
            "location": no_place("not_allowed"),
        },
        "field_of_view_unknown_zoomed_ultra_wide": with_(
            with_(
                with_(base, ["camera", "fieldOfView"], {"state": "unknown"}),
                ["camera", "videoZoomFactor"],
                2.0,
            ),
            ["camera", "deviceType"],
            "ultra_wide",
        ),
        "front_camera_landscape_heading": with_(
            with_(with_(base, ["camera", "position"], "front"), ["heading", "headingOrientation"], "landscape_left"),
            ["sourceAssetSha256"],
            OTHER_SHA,
        ),
        # Gravity along one device axis: the phone upright, then flat; any one nonzero component gives a direction
        "phone_upright": with_(base, ["motion", "gravityDeviceG"], {"x": 0.0, "y": -1.0, "z": 0.0}),
        "phone_flat": with_(base, ["motion", "gravityDeviceG"], {"x": 0.0, "y": 0.0, "z": -1.0}),
        # Readings that happen to be whole numbers, written as JSON integers
        "whole_number_readings": with_(
            with_(with_(base, ["motion", "gravityDeviceG", "x"], 0), ["camera", "videoZoomFactor"], 2),
            ["heading", "headingAccuracyDeg"],
            12,
        ),
    }


def invalid_samples() -> list[dict[str, Any]]:
    """One fault per rule: its name, what it breaks, and the message both sides refuse it with when the rule is the
    contract's own (None where Zod and Pydantic each word a type, range or key refusal in their own way)."""
    v = valid_samples()
    base = v["measured_heading_and_place"]
    rules = pointing.POINTING_RULES
    limit = pointing.HEADING_ACCURACY_LIMIT_DEG
    offset_limit = pointing.MOTION_SAMPLE_OFFSET_LIMIT_SEC
    faults: list[tuple[str, dict[str, Any], str | None]] = [
        # The heading and the place
        ("a heading with no place", {**base, "location": no_place("not_allowed")}, rules["headingNeedsPlace"]),
        ("a heading outside the limit", with_(base, ["heading", "headingAccuracyDeg"], limit + 0.5), rules["headingInsideLimit"]),
        ("a heading the device called invalid", with_(base, ["heading", "headingAccuracyDeg"], -1.0), None),
        ("a true heading of 360", with_(base, ["heading", "trueHeadingDeg"], 360.0), None),
        ("a negative true heading", with_(base, ["heading", "trueHeadingDeg"], -0.5), None),
        ("an attitude against magnetic north", with_(base, ["heading", "attitude", "referenceFrame"], "x_magnetic_north_z_vertical"), None),
        ("a heading orientation that sets no reference", with_(base, ["heading", "headingOrientation"], "face_up"), None),
        ("no place given as the reason while the place is there", with_(base, ["heading"], no_heading("no_place")), rules["noPlaceReasonNeedsNoPlace"]),
        ("a heading left out for its accuracy without the accuracy", with_(base, ["heading"], no_heading("accuracy_outside_limit")), rules["outsideLimitCarriesAccuracy"]),
        ("a heading left out for an accuracy inside the limit", with_(base, ["heading"], no_heading("accuracy_outside_limit", limit)), rules["outsideLimitCarriesAccuracy"]),
        ("a reported accuracy with another reason", with_(base, ["heading"], no_heading("heading_invalid", 41.5)), rules["onlyOutsideLimitReportsAccuracy"]),
        ("an unknown reason for no heading", with_(base, ["heading"], no_heading("cloudy")), None),
        ("no heading that still carries the attitude", with_(base, ["heading"], {**no_heading("no_magnetometer"), "attitude": base["heading"]["attitude"]}), None),
        ("a heading state outside the list", with_(base, ["heading", "state"], "guessed"), None),
        ("a place that is not a latitude", with_(base, ["location", "latDeg"], 90.5), None),
        ("a place the device called invalid", with_(base, ["location", "horizontalAccuracyM"], -1.0), None),
        ("an altitude the device called invalid", with_(base, ["location", "altitude", "verticalAccuracyM"], 0.0), None),
        ("an altitude given as null", with_(base, ["location", "altitude"], None), None),
        ("an accuracy authorization outside the list", with_(base, ["location", "accuracyAuthorization"], "precise"), None),
        ("no place that still carries a latitude", {**v["place_not_allowed"], "location": {**no_place("not_allowed"), "latDeg": 30.0}}, None),
        ("an unknown reason for no place", {**v["place_not_allowed"], "location": no_place("indoors")}, None),
        # The motion sample
        ("an attitude that is not a unit quaternion", with_(base, ["heading", "attitude", "quaternion"], {"x": 0.3, "y": -0.4, "z": 0.4, "w": 0.8}), rules["unitQuaternion"]),
        ("a zero gravity vector", with_(base, ["motion", "gravityDeviceG"], {"x": 0.0, "y": 0.0, "z": 0.0}), rules["gravityNearOneG"]),
        ("a gravity vector written in metres per second squared", with_(base, ["motion", "gravityDeviceG"], {"x": 0.0, "y": 0.0, "z": -9.81}), rules["gravityNearOneG"]),
        ("a gravity vector of a hundredth of one g", with_(base, ["motion", "gravityDeviceG"], {"x": 0.0, "y": 0.0, "z": -0.01}), rules["gravityNearOneG"]),
        ("a gravity vector of two g", with_(base, ["motion", "gravityDeviceG"], {"x": 0.0, "y": 0.0, "z": -2.0}), rules["gravityNearOneG"]),
        ("a motion sample too far from the photo", with_(base, ["motion", "sampleOffsetFromPhotoSec"], offset_limit + 0.01), rules["sampleAtThePhoto"]),
        ("a motion sample too far before the photo", with_(base, ["motion", "sampleOffsetFromPhotoSec"], -(offset_limit + 0.01)), rules["sampleAtThePhoto"]),
        ("a motion window that ends before it starts", with_(base, ["motion", "window", "endOffsetSec"], -0.6), rules["windowRunsForward"]),
        ("a motion window with no sample", with_(base, ["motion", "window", "sampleCount"], 0), None),
        ("a negative largest rotation rate", with_(base, ["motion", "window", "maxRotationRateRadPerSec"], -0.001), None),
        ("a rotation rate given as text", with_(base, ["motion", "rotationRateDeviceRadPerSec", "x"], "0.002"), None),
        # The camera
        ("a zoom below one", with_(base, ["camera", "videoZoomFactor"], 0.5), None),
        ("a field of view of zero, which the device writes for unknown", with_(base, ["camera", "fieldOfView", "horizontalDeg"], 0.0), None),
        ("a field of view of 180 degrees", with_(base, ["camera", "fieldOfView", "horizontalDeg"], 180.0), None),
        ("an unknown field of view with a value", with_(base, ["camera", "fieldOfView"], {"state": "unknown", "horizontalDeg": 69.5}), None),
        ("a virtual camera rather than the lens that took the photo", with_(base, ["camera", "deviceType"], "triple"), None),
        ("a photo with no width", with_(base, ["camera", "photoDimensionsPx", "width"], 0), None),
        ("a photo width with a fraction", with_(base, ["camera", "photoDimensionsPx", "width"], 4032.5), None),
        # The record itself
        ("a hash of 64 zeros", with_(base, ["sourceAssetSha256"], "0" * 64), rules["placeholderSha256"]),
        ("an uppercase hash", with_(base, ["sourceAssetSha256"], SHA.upper()), None),
        ("a capture time not written in UTC", with_(base, ["capturedAtDeviceClockUTC"], "2030-01-01T02:00:00+00:00"), None),
        ("a capture time that is not a calendar date", with_(base, ["capturedAtDeviceClockUTC"], "2030-02-30T02:00:00Z"), None),
        ("another format", with_(base, ["format"], "albedian.pointing"), None),
        ("another format version", with_(base, ["formatVersion"], 2), None),
        ("a pointing record called a measurement", with_(base, ["evidence", "role"], "measurement"), None),
        ("an evidence statement in other words", with_(base, ["evidence", "statement"], "Where the phone pointed."), None),
        ("a key the contract does not name", {**base, "sky": {}}, None),
        ("a record with no motion", with_(base, ["motion"], DROP), None),
        # Types both sides must read alike
        ("a format version of true", with_(base, ["formatVersion"], True), None),
        ("a zoom of true", with_(base, ["camera", "videoZoomFactor"], True), None),
        ("a distortion correction of 1", with_(base, ["camera", "fieldOfView", "distortionCorrected"], 1), None),
        ("a hash with a trailing newline", with_(base, ["sourceAssetSha256"], SHA + "\n"), None),
        ("a capture time with a trailing newline", with_(base, ["capturedAtDeviceClockUTC"], MOMENT + "\n"), None),
        # The two device labels: a machine identifier and a version, never a name a person gave the device
        ("a hardware model of 33 characters", with_(base, ["device", "hardwareModel"], "a" * 30 + "1,1"), None),
        ("a hardware model with a space", with_(base, ["device", "hardwareModel"], "My phone1,1"), None),
        ("a hardware model with an apostrophe", with_(base, ["device", "hardwareModel"], "Synthetic's1,1"), None),
        ("a hardware model with no identifier numbers", with_(base, ["device", "hardwareModel"], "iPhone"), None),
        ("a hardware model with a trailing newline", with_(base, ["device", "hardwareModel"], "Synthetic1,1\n"), None),
        ("an empty hardware model", with_(base, ["device", "hardwareModel"], ""), None),
        ("an empty system version", with_(base, ["device", "systemVersion"], ""), None),
        ("a system version of four groups", with_(base, ["device", "systemVersion"], "27.1.0.1"), None),
        ("a system version ending in a dot", with_(base, ["device", "systemVersion"], "27."), None),
        ("a system version with a word", with_(base, ["device", "systemVersion"], "iOS 27.1"), None),
        ("a system version with a later group of five digits", with_(base, ["device", "systemVersion"], "27.10001"), None),
        ("a system version with a trailing newline", with_(base, ["device", "systemVersion"], "27.1\n"), None),
        ("a hardware model with letters and digits and no comma", with_(base, ["device", "hardwareModel"], "iPhone181"), None),
        ("a hardware model with no letters", with_(base, ["device", "hardwareModel"], "18,1"), None),
        ("a hardware model with no number before its comma", with_(base, ["device", "hardwareModel"], "iPhone,1"), None),
        ("a hardware model shaped like a file name, with no comma", with_(base, ["device", "hardwareModel"], "IMG0001"), None),
        # Bounds on the whole numbers, so every one is a safe integer on both sides
        ("a photo wider than the bound", with_(base, ["camera", "photoDimensionsPx", "width"], pointing.POINTING_DIMENSION_MAX_PX + 1), None),
        ("a format taller than the bound", with_(base, ["camera", "activeFormatDimensionsPx", "height"], pointing.POINTING_DIMENSION_MAX_PX + 1), None),
        ("a photo width of 2^53 + 1", with_(base, ["camera", "photoDimensionsPx", "width"], 2**53 + 1), None),
        ("more motion samples than the bound", with_(base, ["motion", "window", "sampleCount"], pointing.POINTING_SAMPLE_COUNT_MAX + 1), None),
        ("a sample count with a fraction", with_(base, ["motion", "window", "sampleCount"], 1101.5), None),
        ("a photo width of minus zero", with_(base, ["camera", "photoDimensionsPx", "width"], -0.0), None),
        *auditor_disagreements(),
    ]
    return [{"name": name, "verdict": "refused", "message": message, "record": record} for name, record, message in faults]


def renamed(base: dict[str, Any], path: list[str], new: str) -> dict[str, Any]:
    """A deep copy of `base` with the key at `path` renamed to `new`, its value kept."""
    out = copy.deepcopy(base)
    target = out
    for key in path[:-1]:
        target = target[key]
    target[new] = target.pop(path[-1])
    return out


def auditor_disagreements() -> list[tuple[str, dict[str, Any], None]]:
    """The 17 records AUDIT lane D found the two sides judging apart (verdicts/pointing-1.md, finding 1), each a fault
    now: a Python field name for a wire name, a hardware model counted in other units, year 0, and 401-digit counts."""
    base = base_record()
    out: list[tuple[str, dict[str, Any], None]] = []
    for path, python_name in [
        (["sourceAssetSha256"], "source_asset_sha256"),
        (["capturedAtDeviceClockUTC"], "captured_at_device_clock_utc"),
        (["formatVersion"], "format_version"),
        (["device", "hardwareModel"], "hardware_model"),
        (["heading", "trueHeadingDeg"], "true_heading_deg"),
        (["location", "latDeg"], "lat_deg"),
        (["camera", "videoZoomFactor"], "video_zoom_factor"),
        (["motion", "window", "sampleCount"], "sample_count"),
    ]:
        out.append((f"the auditor's record with the Python name {python_name}", renamed(base, path, python_name), None))
    astral = "\U0001f600"
    for label, text in [
        ("100 characters outside the basic plane", astral * 100),
        ("128 characters outside the basic plane", astral * 128),
        ("64 characters outside the basic plane and one letter", astral * 64 + "a"),
        ("a lone surrogate", "\ud800"),
    ]:
        out.append((f"the auditor's record with a hardware model of {label}", with_(base, ["device", "hardwareModel"], text), None))
    for moment in ("0000-01-01T00:00:00Z", "0000-02-29T00:00:00Z"):
        out.append((f"the auditor's record with a capture time in year 0, {moment}", with_(base, ["capturedAtDeviceClockUTC"], moment), None))
    for path in (["camera", "photoDimensionsPx", "width"], ["camera", "photoDimensionsPx", "height"], ["motion", "window", "sampleCount"]):
        out.append((f"the auditor's record with a {path[-1]} of 401 digits", with_(base, path, 10**400), None))
    return out


# P4's field variants, shared with the TypeScript property through the fixture: for every field the rules do not
# read, values inside and outside what the contract takes, each with its verdict by the contract's text. A case sets
# at most one of them; it applies only where the field is there (a latitude only with a place, and so on).
def _variants() -> list[dict[str, Any]]:
    dim, count, label = pointing.POINTING_DIMENSION_MAX_PX, pointing.POINTING_SAMPLE_COUNT_MAX, pointing.POINTING_LABEL_MAX_UTF16
    astral = "\U0001f600"
    table: list[tuple[list[str], list[tuple[Any, bool]]]] = [
        (["format"], [("albedian.pointing-record ", False), ("albedian.pointing", False)]),
        (["formatVersion"], [(1.0, True), (2, False), (True, False), (0, False)]),
        (["sourceAssetSha256"], [(OTHER_SHA, True), (SHA.upper(), False), (SHA[:63], False), ("0" * 64, False)]),
        (["capturedAtDeviceClockUTC"], [
            ("2032-02-29T23:59:59Z", True), ("2031-02-29T00:00:00Z", False), ("0000-01-01T00:00:00Z", False),
            ("2031-04-31T00:00:00Z", False), ("0001-01-01T00:00:00Z", True), ("2031-08-30T24:00:00Z", False),
        ]),
        (["device", "hardwareModel"], [
            ("iPhone18,1", True), ("iPad16,10", True), ("a" * (label - 3) + "1,1", True), ("a" * (label - 2) + "1,1", False),
            ("iPhone", False), ("My phone1,1", False), ("iPhone18,1 ", False), ("\ud800", False), (astral + "1,1", False),
            ("iPhone18_1", False), ("18,1", False),
        ]),
        (["device", "systemVersion"], [
            ("18", True), ("18.0.1", True), ("0.0.0", True), ("18.0.1.2", False), ("18.", False), ("v18", False),
            ("", False), ("18.00001", False), ("18..1", False),
        ]),
        (["camera", "position"], [("front", True), ("side", False)]),
        (["camera", "deviceType"], [("telephoto", True), ("ultra_wide", True), ("triple", False)]),
        (["camera", "activeFormatDimensionsPx", "width"], [
            (1, True), (dim, True), (dim + 1, False), (0, False), (-0.0, False), (2**53 + 1, False), (10**400, False),
            (640.0, True), (640.5, False), (True, False),
        ]),
        (["camera", "activeFormatDimensionsPx", "height"], [(dim, True), (dim + 1, False), (3024.0, True)]),
        (["camera", "photoDimensionsPx", "width"], [(dim, True), (dim + 1, False), (2**53 + 1, False)]),
        (["camera", "photoDimensionsPx", "height"], [(1, True), (dim, True), (dim + 1, False), (-1, False), (10**400, False)]),
        (["camera", "fieldOfView"], [
            ({"state": "unknown"}, True),
            ({"state": "reported", "horizontalDeg": 0.0, "distortionCorrected": False}, False),
        ]),
        (["camera", "fieldOfView", "horizontalDeg"], [(0.001, True), (179.999, True), (180.0, False), (-1.0, False)]),
        (["camera", "fieldOfView", "distortionCorrected"], [(False, True), (1, False), ("true", False)]),
        (["camera", "videoZoomFactor"], [(1, True), (15.0, True), (0.999, False), (True, False), ("1", False)]),
        (["camera", "contentAwareDistortionCorrection"], [(True, True), (0, False)]),
        (["motion", "rotationRateDeviceRadPerSec", "x"], [(3.0, True), ("0", False)]),
        (["motion", "rotationRateDeviceRadPerSec", "y"], [(-3.0, True), (-0.0, True), (2**53 + 1, True), (True, False), ("0", False)]),
        (["motion", "rotationRateDeviceRadPerSec", "z"], [(0, True), (None, False)]),
        (["motion", "window", "sampleCount"], [(1, True), (count, True), (count + 1, False), (0, False), (10**400, False), (2.0, True), (2.5, False)]),
        (["motion", "window", "maxRotationRateRadPerSec"], [(0.0, True), (-0.0, True), (12.0, True), (-0.5, False)]),
        (["heading", "attitude", "referenceFrame"], [("x_magnetic_north_z_vertical", False), ("x_arbitrary_z_vertical", False)]),
        (["heading", "trueHeadingDeg"], [(0.0, True), (359.999, True), (-0.0, True), (360.0, False), (-0.001, False), (400.0, False)]),
        (["heading", "headingOrientation"], [("landscape_right", True), ("portrait_upside_down", True), ("face_up", False)]),
        (["heading", "eventOffsetFromPhotoSec"], [(-600.0, True), (0, True), (True, False)]),
        (["location", "latDeg"], [(90.0, True), (-90.0, True), (90.000001, False), ("30", False)]),
        (["location", "lonDeg"], [(180.0, True), (-180.0, True), (-180.5, False)]),
        (["location", "horizontalAccuracyM"], [(0.0, True), (9000.0, True), (-0.001, False)]),
        (["location", "accuracyAuthorization"], [("reduced", True), ("precise", False)]),
        (["location", "altitude"], [({"aboveMeanSeaLevelM": 1.0, "aboveEllipsoidM": 2.0, "verticalAccuracyM": 3.0, "extra": 1}, False)]),
        (["location", "altitude", "aboveMeanSeaLevelM"], [(-50.0, True), (8000.0, True)]),
        (["location", "altitude", "aboveEllipsoidM"], [(-80.0, True), (True, False)]),
        (["location", "altitude", "verticalAccuracyM"], [(0.001, True), (0.0, False), (-1.0, False)]),
        (["location", "fixOffsetFromPhotoSec"], [(-3600.0, True), ("0", False)]),
        (["evidence", "role"], [("observed", False), ("measurement", False)]),
        (["evidence", "statement"], [(pointing.POINTING_HINT_STATEMENT + " ", False)]),
    ]
    return [{"path": path, "value": value, "ok": ok} for path, values in table for value, ok in values]


FIELD_VARIANTS = _variants()


def variant_index(path: list[str], value: Any) -> int:
    for index, variant in enumerate(FIELD_VARIANTS):
        if variant["path"] == path and variant["value"] == value and type(variant["value"]) is type(value):
            return index
    raise KeyError((path, value))


def _case(heading: str = "measured", location: str = "allowed", **changes: Any) -> dict[str, Any]:
    case = {
        "heading": heading, "headingAccuracyDeg": 12.0 if heading == "measured" else None, "reported": None,
        "location": location, "altitude": location == "allowed", "quaternionScale": 1.0, "gravityScale": 1.0,
        "sampleOffsetSec": 0.0, "windowEndOffsetSec": 10.5, "variant": None,
    }  # fmt: skip
    case.update(changes)
    return case


# P4's cases, shared with the TypeScript property through the fixture: every combination the oracle treats apart,
# each built from the base record by `build_property_record` on both sides
PROPERTY_EXAMPLES: list[dict[str, Any]] = [
    _case(),
    _case(headingAccuracyDeg=25.0, altitude=False, sampleOffsetSec=0.05, windowEndOffsetSec=0.0),
    _case(headingAccuracyDeg=0.0, sampleOffsetSec=-0.05),
    _case(headingAccuracyDeg=25.5),
    _case(headingAccuracyDeg=-1.0),
    _case(location="not_allowed"),
    _case(quaternionScale=1.5),
    _case(gravityScale=0.0),
    _case(gravityScale=9.81),
    _case(gravityScale=-1.0),
    _case(gravityScale=0.5),
    _case(gravityScale=1.5),
    _case(sampleOffsetSec=0.06),
    _case(windowEndOffsetSec=-0.5),
    _case("no_place", "not_allowed"),
    _case("no_place", "no_fix"),
    _case("no_place", "allowed"),
    _case("accuracy_outside_limit", altitude=False, reported=41.5),
    _case("accuracy_outside_limit", altitude=False, reported=25.0),
    _case("accuracy_outside_limit", altitude=False),
    _case("heading_invalid", reported=41.5),
    _case("no_magnetometer", "simulated_by_software"),
    _case("no_heading_event", gravityScale=0.0),
    _case(variant=variant_index(["device", "hardwareModel"], "iPhone18,1")),
    _case(variant=variant_index(["device", "hardwareModel"], "My phone1,1")),
    _case(variant=variant_index(["device", "systemVersion"], "18.0.1.2")),
    _case(variant=variant_index(["camera", "photoDimensionsPx", "height"], pointing.POINTING_DIMENSION_MAX_PX)),
    _case(variant=variant_index(["camera", "photoDimensionsPx", "height"], pointing.POINTING_DIMENSION_MAX_PX + 1)),
    _case(variant=variant_index(["camera", "activeFormatDimensionsPx", "width"], 10**400)),
    _case(variant=variant_index(["capturedAtDeviceClockUTC"], "0000-01-01T00:00:00Z")),
    _case(variant=variant_index(["motion", "window", "sampleCount"], pointing.POINTING_SAMPLE_COUNT_MAX + 1)),
    _case("no_place", "not_allowed", variant=variant_index(["location", "latDeg"], 90.000001)),
    _case("no_magnetometer", variant=variant_index(["heading", "trueHeadingDeg"], 400.0)),
    _case(variant=variant_index(["location", "latDeg"], 90.000001)),
]
LOCATION_STATES = ("allowed", "not_allowed", "no_fix", "simulated_by_software")
HEADING_STATES = ("measured", "no_magnetometer", "no_place", "no_heading_event", "heading_invalid", "accuracy_outside_limit")


def build_property_record(case: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """The record a P4 case describes, from the base record, and whether its field variant applied (the TypeScript
    property builds it the same way)."""
    record = base_record()
    if case["location"] != "allowed":
        record["location"] = no_place(case["location"])
    elif not case["altitude"]:
        del record["location"]["altitude"]
    if case["heading"] == "measured":
        record["heading"]["headingAccuracyDeg"] = case["headingAccuracyDeg"]
        scale = case["quaternionScale"]
        record["heading"]["attitude"]["quaternion"] = {key: value * scale for key, value in QUATERNION.items()}
    else:
        record["heading"] = no_heading(case["heading"], case["reported"])
    record["motion"]["gravityDeviceG"] = {key: value * case["gravityScale"] for key, value in GRAVITY.items()}
    record["motion"]["sampleOffsetFromPhotoSec"] = case["sampleOffsetSec"]
    record["motion"]["window"]["endOffsetSec"] = case["windowEndOffsetSec"]
    applied = False
    if case["variant"] is not None:
        variant = FIELD_VARIANTS[case["variant"]]
        target: Any = record
        for key in variant["path"][:-1]:
            target = target.get(key) if isinstance(target, dict) else None
        if isinstance(target, dict) and variant["path"][-1] in target:
            target[variant["path"][-1]] = copy.deepcopy(variant["value"])
            applied = True
    return record, applied


def property_oracle(case: dict[str, Any], applied: bool) -> bool:
    """Whether the record a P4 case describes is a valid pointing record, from the rules and the variant's own verdict."""
    limit = pointing.HEADING_ACCURACY_LIMIT_DEG
    placed = case["location"] == "allowed"
    if case["heading"] == "measured":
        heading_ok = placed and 0.0 <= case["headingAccuracyDeg"] <= limit and case["quaternionScale"] == 1.0
    elif case["heading"] == "accuracy_outside_limit":
        heading_ok = case["reported"] is not None and case["reported"] > limit
    else:
        heading_ok = case["reported"] is None and (case["heading"] != "no_place" or not placed)
    window_start = base_record()["motion"]["window"]["startOffsetSec"]
    motion_ok = (
        pointing.GRAVITY_LENGTH_MIN_G <= abs(case["gravityScale"]) <= pointing.GRAVITY_LENGTH_MAX_G
        and abs(case["sampleOffsetSec"]) <= pointing.MOTION_SAMPLE_OFFSET_LIMIT_SEC
        and window_start <= case["windowEndOffsetSec"]
    )
    variant_ok = not applied or FIELD_VARIANTS[case["variant"]]["ok"]
    return heading_ok and motion_ok and variant_ok


# The generated wire faults (WIRE_RULES.md, rule 6): from three valid records, every key renamed to its Python field
# name, an unknown key in every object, and every number, text, time and flag replaced by one a wire rule names. Each
# is kept as how to build its wire text from the named valid sample, so both sides parse the same text with their
# own JSON reader; its verdict is the Python mirror's, and the TypeScript test gives the same one for every fault.
WIRE_MARK = "@@wire fault@@"
NUMBER_FAULTS = {
    "1.0": "1.0", "1e400": "1e400", "a 401 digit integer": "1" + "0" * 400, "2^53 + 1": str(2**53 + 1),
    "minus zero": "-0.0", "true": "true", "text": '"1"',
}  # fmt: skip
FLAG_FAULTS = {"1": "1", "text": '"true"'}
TIME_FAULTS = {"year 0": json.dumps("0000-01-01T00:00:00Z"), "day 30 of February": json.dumps("2031-02-30T00:00:00Z")}
# The faults a wire rule refuses outright, whatever the field: everything but 1.0, minus zero and 2^53 + 1, which a
# number field may take
REFUSED_WIRE_FAULTS = {"rename", "add", "1e400", "a 401 digit integer", "true", "text", "1", "over long", "a lone surrogate",
                       "outside the basic plane", "a control character", "year 0", "day 30 of February"}  # fmt: skip
WIRE_BASES = {"measured_heading_and_place": None, "heading_outside_the_limit": ("heading", "location"), "place_not_allowed": ("heading", "location")}


def text_faults(value: str) -> dict[str, str]:
    # 300 characters is longer than any text the record takes: its longest is the fixed evidence statement
    assert len(pointing.POINTING_HINT_STATEMENT) < 300
    return {
        "over long": json.dumps("a" * 300), "a lone surrogate": json.dumps(value + "\ud800"),
        "outside the basic plane": json.dumps(value + "\U0001f600"), "a control character": json.dumps(value + "\u0007"),
    }  # fmt: skip


def python_names() -> dict[str, str]:
    """Wire name to Python field name, for every field of the mirror whose two names differ."""
    names: dict[str, str] = {}
    for model in vars(pointing).values():
        if isinstance(model, type) and issubclass(model, pointing._Model) and model is not pointing._Model:
            for name, field in model.model_fields.items():
                if field.alias and field.alias != name:
                    names[field.alias] = name
    return names


def _walk(node: Any, path: tuple[str, ...] = ()) -> Any:
    yield path, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, (*path, key))


def wire_text(fault: dict[str, Any], samples: dict[str, dict[str, Any]]) -> str:
    """The JSON text a wire fault describes: built from its valid sample, as the TypeScript test builds it."""
    record = copy.deepcopy(samples[fault["base"]])
    *parents, last = fault["path"] or [None]
    target = record
    for key in parents:
        target = target[key]
    if fault["kind"] == "add":
        (target[last] if last is not None else record)["unknownKey"] = 1
        return json.dumps(record)
    if fault["kind"] == "rename":
        target[fault["to"]] = target.pop(last)
        return json.dumps(record)
    target[last] = WIRE_MARK
    text = json.dumps(record)
    assert text.count(json.dumps(WIRE_MARK)) == 1
    return text.replace(json.dumps(WIRE_MARK), fault["raw"])


def wire_faults() -> list[dict[str, Any]]:
    samples, names = valid_samples(), python_names()
    faults: list[dict[str, Any]] = []
    for base_name, roots in WIRE_BASES.items():
        for path, value in _walk(samples[base_name]):
            if roots is not None and (not path or path[0] not in roots):
                continue
            where = ".".join(path) or "the record"
            if path and path[-1] in names:
                faults.append({"name": f"{base_name}: {where} under its Python name", "base": base_name, "path": list(path),
                               "kind": "rename", "to": names[path[-1]]})  # fmt: skip
            if isinstance(value, dict):
                faults.append({"name": f"{base_name}: an unknown key in {where}", "base": base_name, "path": list(path), "kind": "add"})
                continue
            if isinstance(value, bool):
                raws = FLAG_FAULTS
            elif isinstance(value, (int, float)):
                raws = NUMBER_FAULTS
            else:
                raws = {**text_faults(value), **(TIME_FAULTS if path == ("capturedAtDeviceClockUTC",) else {})}
            for label, raw in raws.items():
                faults.append({"name": f"{base_name}: {where} as {label}", "base": base_name, "path": list(path), "kind": "raw",
                               "label": label, "raw": raw})  # fmt: skip
    for fault in faults:
        fault["verdict"] = "accepted" if _judge(json.loads(wire_text(fault, samples))) is None else "refused"
    return faults


# The edges of every bound, from two tables, so that a new field gets its edges by being listed here, and a number
# that is listed nowhere fails `test_every_number_of_the_samples_is_in_the_edge_table`.
# NUMBER_BOUNDS: (name, valid sample, changes made first, path, low, low inside, high, high inside, whole). An end of
# None is open. At an inside end the fixture gets an accepted sample at the end and a refused one a step outside it;
# at an outside end a refused sample at the end and an accepted one a step inside it. A step is the next double, or
# one for a whole number.
def _numbers() -> list[tuple[Any, ...]]:
    limit, offset, dim, count = (pointing.HEADING_ACCURACY_LIMIT_DEG, pointing.MOTION_SAMPLE_OFFSET_LIMIT_SEC,
                                 pointing.POINTING_DIMENSION_MAX_PX, pointing.POINTING_SAMPLE_COUNT_MAX)  # fmt: skip
    low_g, high_g, tolerance = pointing.GRAVITY_LENGTH_MIN_G, pointing.GRAVITY_LENGTH_MAX_G, pointing.UNIT_QUATERNION_TOLERANCE
    m = "measured_heading_and_place"
    axis_w = [(["heading", "attitude", "quaternion"], {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0})]
    # The quaternion's norm along w is w itself: its ends are the extreme doubles within the tolerance of one
    w_low, w_high = _extreme(lambda w: abs(math.sqrt(w * w) - 1.0) <= tolerance, 1.0)
    return [
        ("format version", m, [], ["formatVersion"], 1, True, 1, True, True),
        ("true heading", m, [], ["heading", "trueHeadingDeg"], 0.0, True, 360.0, False, False),
        ("heading accuracy", m, [], ["heading", "headingAccuracyDeg"], 0.0, True, limit, True, False),
        ("reported heading accuracy", "heading_outside_the_limit", [], ["heading", "reportedHeadingAccuracyDeg"], limit, False, None, None, False),
        ("latitude", m, [], ["location", "latDeg"], -90.0, True, 90.0, True, False),
        ("longitude", m, [], ["location", "lonDeg"], -180.0, True, 180.0, True, False),
        ("horizontal accuracy", m, [], ["location", "horizontalAccuracyM"], 0.0, True, None, None, False),
        ("vertical accuracy", m, [], ["location", "altitude", "verticalAccuracyM"], 0.0, False, None, None, False),
        ("field of view", m, [], ["camera", "fieldOfView", "horizontalDeg"], 0.0, False, 180.0, False, False),
        ("zoom factor", m, [], ["camera", "videoZoomFactor"], 1.0, True, None, None, False),
        ("format width", m, [], ["camera", "activeFormatDimensionsPx", "width"], 1, True, dim, True, True),
        ("format height", m, [], ["camera", "activeFormatDimensionsPx", "height"], 1, True, dim, True, True),
        ("photo width", m, [], ["camera", "photoDimensionsPx", "width"], 1, True, dim, True, True),
        ("photo height", m, [], ["camera", "photoDimensionsPx", "height"], 1, True, dim, True, True),
        ("sample count", m, [], ["motion", "window", "sampleCount"], 1, True, count, True, True),
        ("largest rotation rate", m, [], ["motion", "window", "maxRotationRateRadPerSec"], 0.0, True, None, None, False),
        ("sample offset", m, [], ["motion", "sampleOffsetFromPhotoSec"], -offset, True, offset, True, False),
        # A window ends no earlier than it starts: its end's low end is the start
        ("window end", m, [], ["motion", "window", "endOffsetSec"], base_record()["motion"]["window"]["startOffsetSec"], True, None, None, False),
        # Gravity along one axis, either way up: its length is the component itself
        ("gravity up", "phone_flat", [], ["motion", "gravityDeviceG", "z"], low_g, True, high_g, True, False),
        ("gravity down", "phone_flat", [], ["motion", "gravityDeviceG", "z"], -high_g, True, -low_g, True, False),
        ("quaternion norm", m, axis_w, ["heading", "attitude", "quaternion", "w"], w_low, True, w_high, True, False),
    ]


def _extreme(inside: Any, start: float) -> tuple[float, float]:
    """The smallest and largest doubles around `start` for which `inside` holds: a bracket that doubles until its
    far end is outside, then bisection until the inside and outside ends are neighbouring doubles."""
    ends = []
    for direction in (-1.0, 1.0):
        step = 1e-12
        while inside(start + direction * step):
            step *= 2
        held, lost = start, start + direction * step
        while math.nextafter(held, lost) != lost:
            middle = (held + lost) / 2
            held, lost = (middle, lost) if inside(middle) else (held, middle)
        ends.append(held)
    return ends[0], ends[1]


# Numbers no bound applies to, by the contract's text: offsets in seconds, heights, and rotation rates in rad/s
UNBOUNDED_NUMBERS = {
    ("heading", "eventOffsetFromPhotoSec"), ("location", "fixOffsetFromPhotoSec"), ("location", "altitude", "aboveMeanSeaLevelM"),
    ("location", "altitude", "aboveEllipsoidM"), ("motion", "window", "startOffsetSec"),
    ("motion", "rotationRateDeviceRadPerSec", "x"), ("motion", "rotationRateDeviceRadPerSec", "y"), ("motion", "rotationRateDeviceRadPerSec", "z"),
}  # fmt: skip
# Numbers bounded only together, through a rule over their vector: gravity's length and the quaternion's norm
BOUNDED_TOGETHER = {
    ("motion", "gravityDeviceG", "x"), ("motion", "gravityDeviceG", "y"),
    ("heading", "attitude", "quaternion", "x"), ("heading", "attitude", "quaternion", "y"), ("heading", "attitude", "quaternion", "z"),
}  # fmt: skip


# TEXT_BOUNDS: (name, valid sample, path, shortest accepted, longest accepted). The fixture gets both accepted, and
# refused: the shortest less its last character and the longest with one more (the pattern's own kind of character
# where the pattern allows it, so the length alone is what fails). Each closed list and fixed text is its own value.
def _texts() -> list[tuple[str, str, list[str], str, str, str]]:
    m = "measured_heading_and_place"
    label, version = pointing.POINTING_LABEL_MAX_UTF16, pointing.POINTING_SYSTEM_VERSION_MAX_UTF16
    texts = [
        ("hardware model", m, ["device", "hardwareModel"], "A1,1", "A" * (label - 3) + "1,1", "1"),
        ("system version", m, ["device", "systemVersion"], "0", "9999.9999.9999", "9"),
        ("source hash", m, ["sourceAssetSha256"], SHA, SHA, "0"),
        ("capture time", m, ["capturedAtDeviceClockUTC"], MOMENT, MOMENT.replace("Z", ".000Z"), None),
        ("format", m, ["format"], pointing.POINTING_RECORD_FORMAT, pointing.POINTING_RECORD_FORMAT, "d"),
        ("evidence role", m, ["evidence", "role"], "pointing_hint", "pointing_hint", "s"),
        ("evidence statement", m, ["evidence", "statement"], pointing.POINTING_HINT_STATEMENT, pointing.POINTING_HINT_STATEMENT, "."),
        ("reference frame", m, ["heading", "attitude", "referenceFrame"], "x_true_north_z_vertical", "x_true_north_z_vertical", "l"),
    ]
    assert len(texts[1][4]) == version
    closed = [
        ("camera position", m, ["camera", "position"], pointing.CAMERA_POSITIONS),
        ("camera type", m, ["camera", "deviceType"], pointing.CAMERA_DEVICE_TYPES),
        ("heading orientation", m, ["heading", "headingOrientation"], pointing.HEADING_ORIENTATIONS),
        ("accuracy authorization", m, ["location", "accuracyAuthorization"], pointing.ACCURACY_AUTHORIZATIONS),
        ("no heading reason", "no_magnetometer", ["heading", "reason"], ("no_magnetometer",)),
        ("no place reason", "place_not_allowed", ["location", "reason"], ("not_allowed",)),
    ]
    for name, sample, path, members in closed:
        texts += [(f"{name} {member}", sample, path, member, member, "s") for member in members]
    return texts


def _edge(name: str, sample: str, changes: list[tuple[list[str], Any]], verdict: str) -> dict[str, Any]:
    return {"name": name, "sample": sample, "changes": [{"path": path, "value": value} for path, value in changes], "verdict": verdict}


def edge_samples() -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for name, sample, first, path, low, low_in, high, high_in, whole in _numbers():
        for end, inside, outward in ((low, low_in, -math.inf), (high, high_in, math.inf)):
            if end is None:
                continue
            step = (lambda value, way: value + (1 if way > 0 else -1)) if whole else math.nextafter
            beyond, within = step(end, outward), step(end, -outward)
            side = "low" if outward < 0 else "high"
            if inside:
                edges.append(_edge(f"{name} at its {side} end, {end!r}", sample, [*first, (path, end)], "accepted"))
                edges.append(_edge(f"{name} a step past its {side} end, {beyond!r}", sample, [*first, (path, beyond)], "refused"))
            else:
                edges.append(_edge(f"{name} at its {side} end, which it leaves out, {end!r}", sample, [*first, (path, end)], "refused"))
                edges.append(_edge(f"{name} a step inside its {side} end, {within!r}", sample, [*first, (path, within)], "accepted"))
    for name, sample, path, shortest, longest, more in _texts():
        under = shortest[:-1]
        over = longest + more if more is not None else longest.replace("Z", "0Z")
        assert len(under) == len(shortest) - 1 and len(over) == len(longest) + 1, name
        edges.append(_edge(f"{name}, its shortest ({len(shortest)})", sample, [(path, shortest)], "accepted"))
        edges.append(_edge(f"{name}, its longest ({len(longest)})", sample, [(path, longest)], "accepted"))
        edges.append(_edge(f"{name}, one under its shortest ({len(under)})", sample, [(path, under)], "refused"))
        edges.append(_edge(f"{name}, one over its longest ({len(over)})", sample, [(path, over)], "refused"))
    names = [edge["name"] for edge in edges]
    assert len(names) == len(set(names))
    return edges


def edge_record(edge: dict[str, Any], samples: dict[str, dict[str, Any]]) -> dict[str, Any]:
    record = samples[edge["sample"]]
    for change in edge["changes"]:
        record = with_(record, change["path"], change["value"])
    return record


def build_pointing_samples() -> dict[str, Any]:
    return {
        "format": pointing.POINTING_RECORD_FORMAT,
        "formatVersion": pointing.POINTING_RECORD_FORMAT_VERSION,
        "limits": {
            "headingAccuracyLimitDeg": pointing.HEADING_ACCURACY_LIMIT_DEG,
            "motionSampleOffsetLimitSec": pointing.MOTION_SAMPLE_OFFSET_LIMIT_SEC,
            "unitQuaternionTolerance": pointing.UNIT_QUATERNION_TOLERANCE,
            "gravityLengthMinG": pointing.GRAVITY_LENGTH_MIN_G,
            "gravityLengthMaxG": pointing.GRAVITY_LENGTH_MAX_G,
            "dimensionMaxPx": pointing.POINTING_DIMENSION_MAX_PX,
            "sampleCountMax": pointing.POINTING_SAMPLE_COUNT_MAX,
            "labelMaxUtf16": pointing.POINTING_LABEL_MAX_UTF16,
            "systemVersionMaxUtf16": pointing.POINTING_SYSTEM_VERSION_MAX_UTF16,
        },
        "patterns": {
            "hardwareModel": pointing.POINTING_HARDWARE_MODEL_PATTERN,
            "systemVersion": pointing.POINTING_SYSTEM_VERSION_PATTERN,
        },
        "enums": {
            "cameraPositions": list(pointing.CAMERA_POSITIONS),
            "cameraDeviceTypes": list(pointing.CAMERA_DEVICE_TYPES),
            "headingOrientations": list(pointing.HEADING_ORIENTATIONS),
            "noHeadingReasons": list(pointing.NO_HEADING_REASONS),
            "noPlaceReasons": list(pointing.NO_PLACE_REASONS),
            "accuracyAuthorizations": list(pointing.ACCURACY_AUTHORIZATIONS),
        },
        "rules": dict(pointing.POINTING_RULES),
        "statement": pointing.POINTING_HINT_STATEMENT,
        "valid": [{"name": name, "verdict": "accepted", "record": record} for name, record in valid_samples().items()],
        "invalid": invalid_samples(),
        "edges": edge_samples(),
        "wireFaults": wire_faults(),
        "fieldVariants": FIELD_VARIANTS,
        "propertyExamples": PROPERTY_EXAMPLES,
    }


def _judge(record: dict[str, Any]) -> str | None:
    """None when the mirror accepts the record, else the text of its refusal."""
    try:
        PointingRecord.model_validate(record)
    except ValidationError as error:
        return str(error)
    return None


def test_pointing_fixture_is_current() -> None:
    samples = build_pointing_samples()
    text = json.dumps(samples, indent=2, ensure_ascii=True) + "\n"
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text, f"regenerate with {UPDATE_FIXTURE_ENV}=1"


def test_the_mirror_accepts_every_valid_sample_unchanged() -> None:
    samples = valid_samples()
    assert len(samples) >= 10
    for name, sample in samples.items():
        parsed = PointingRecord.model_validate(sample)
        assert parsed.model_dump(by_alias=True, exclude_unset=True) == sample, name


@pytest.mark.parametrize("case", invalid_samples(), ids=lambda case: case["name"])
def test_the_mirror_refuses_every_fault_in_the_contracts_own_words(case: dict[str, Any]) -> None:
    refusal = _judge(case["record"])
    assert refusal is not None, case["name"]
    if case["message"] is not None:
        assert case["message"] in refusal, refusal


def test_every_rule_has_a_fault_and_is_worded_as_pointing_ts_words_it() -> None:
    contract = POINTING_TS.read_text(encoding="utf-8")
    faulted = {case["message"] for case in invalid_samples()}
    for key, message in pointing.POINTING_RULES.items():
        assert f"{key}: '{message}'" in contract or f'{key}: "{message}"' in contract, key
        assert message in faulted, key
    assert pointing.POINTING_HINT_STATEMENT in contract
    assert len(invalid_samples()) >= 40


def test_the_evidence_statement_says_a_hint_never_a_measurement_of_the_sky() -> None:
    statement = pointing.POINTING_HINT_STATEMENT
    for words in ("hint", "never a measurement of the sky", "predicted", "catalog_matched", "held-out verification"):
        assert words in statement, words


def test_the_heading_limit_holds_at_its_edge() -> None:
    base = base_record()
    limit = pointing.HEADING_ACCURACY_LIMIT_DEG
    assert _judge(with_(base, ["heading", "headingAccuracyDeg"], limit)) is None
    assert _judge(with_(base, ["heading", "headingAccuracyDeg"], math.nextafter(limit, math.inf))) is not None
    assert _judge(with_(base, ["heading", "headingAccuracyDeg"], 0.0)) is None
    assert _judge(with_(base, ["heading", "headingAccuracyDeg"], -0.0)) is None
    left_out = with_(base, ["heading"], no_heading("accuracy_outside_limit", math.nextafter(limit, math.inf)))
    assert _judge(left_out) is None
    assert _judge(with_(left_out, ["heading", "reportedHeadingAccuracyDeg"], limit)) is not None


def test_the_motion_sample_limit_holds_at_its_edge() -> None:
    base = base_record()
    limit = pointing.MOTION_SAMPLE_OFFSET_LIMIT_SEC
    for inside in (limit, -limit, 0.0):
        assert _judge(with_(base, ["motion", "sampleOffsetFromPhotoSec"], inside)) is None, inside
    for outside in (math.nextafter(limit, math.inf), -math.nextafter(limit, math.inf)):
        assert _judge(with_(base, ["motion", "sampleOffsetFromPhotoSec"], outside)) is not None, outside


def test_the_unit_quaternion_tolerance_holds_at_its_edge() -> None:
    base = base_record()
    tolerance = pointing.UNIT_QUATERNION_TOLERANCE
    # Along w alone, the norm is w: inside and outside the tolerance by a tenth of it and by ten times it. The pair at
    # 0.9 and 1.1 tells the norm from its square, which moves twice as far
    cases = [(1.0 + tolerance * f, f <= 1.0) for f in (0.1, 0.9, 1.1, 10.0)] + [(1.0 - tolerance * f, f <= 1.0) for f in (0.1, 0.9, 1.1, 10.0)]
    for w, accepted in cases:
        record = with_(base, ["heading", "attitude", "quaternion"], {"x": 0.0, "y": 0.0, "z": 0.0, "w": w})
        assert (_judge(record) is None) == accepted, w
    # The other sign of the same rotation is the same attitude
    negated = {key: -value for key, value in QUATERNION.items()}
    assert _judge(with_(base, ["heading", "attitude", "quaternion"], negated)) is None


def test_a_whole_number_written_with_a_fraction_counts_as_whole_as_zod_reads_it() -> None:
    base = base_record()
    assert _judge(with_(base, ["camera", "photoDimensionsPx", "width"], 4032.0)) is None
    assert _judge(with_(base, ["motion", "window", "sampleCount"], 1101.0)) is None
    assert _judge(with_(base, ["formatVersion"], 1.0)) is None
    assert _judge(with_(base, ["camera", "photoDimensionsPx", "width"], True)) is not None
    assert _judge(with_(base, ["camera", "videoZoomFactor"], True)) is not None


def test_no_value_may_be_infinite_or_not_a_number() -> None:
    base = base_record()
    for path in (["motion", "rotationRateDeviceRadPerSec", "x"], ["location", "altitude", "aboveEllipsoidM"],
                 ["motion", "window", "startOffsetSec"], ["heading", "eventOffsetFromPhotoSec"], ["location", "fixOffsetFromPhotoSec"]):
        for bad in (math.inf, -math.inf, math.nan):
            assert _judge(with_(base, path, bad)) is not None, (path, bad)


def test_gravity_is_held_to_its_band_around_one_g() -> None:
    """Along one axis the length is the component itself (sqrt of x times x is |x| in binary floating point), so
    the band's edges are exact here."""
    base = base_record()
    low, high = pointing.GRAVITY_LENGTH_MIN_G, pointing.GRAVITY_LENGTH_MAX_G
    inside = [low, high, 1.0, -low, -high, math.nextafter(low, math.inf), math.nextafter(high, -math.inf)]
    outside = [math.nextafter(low, -math.inf), math.nextafter(high, math.inf), 9.81, -9.81, 100.0, 0.01, 1e-300, 0.0, 2.0, 1e200]
    for z in inside:
        assert _judge(with_(base, ["motion", "gravityDeviceG"], {"x": 0.0, "y": 0.0, "z": z})) is None, z
    for z in outside:
        refusal = _judge(with_(base, ["motion", "gravityDeviceG"], {"x": 0.0, "y": 0.0, "z": z}))
        assert refusal is not None and pointing.POINTING_RULES["gravityNearOneG"] in refusal, z
    # Spread over the three axes, as a phone held at an angle reports it
    assert _judge(with_(base, ["motion", "gravityDeviceG"], {"x": 0.5, "y": -0.5, "z": -0.7})) is None
    assert _judge(with_(base, ["motion", "gravityDeviceG"], {"x": 5.0, "y": -5.0, "z": -7.0})) is not None


def test_the_device_labels_are_exact_patterns_that_carry_no_name() -> None:
    base = base_record()
    assert pointing.POINTING_LABEL_MAX_UTF16 == 32
    # The system version's maximum is what its pattern allows: the longest version it takes is exactly that long
    assert len("9999.9999.9999") == pointing.POINTING_SYSTEM_VERSION_MAX_UTF16 == 14
    for model in ("iPhone18,1", "iPad16,10", "Synthetic1,1", "a" * 29 + "1,1"):
        assert _judge(with_(base, ["device", "hardwareModel"], model)) is None, model
    for model in ("a" * 30 + "1,1", "My phone1,1", "Synthetic's1,1", "iPhone", "éa1,1", "iPhone18,1\n", "\ud800", "\U0001f6001,1"):
        assert _judge(with_(base, ["device", "hardwareModel"], model)) is not None, ascii(model)
    for version in ("27", "27.1", "27.1.2", "0.0.0", "9999.9999.9999"):
        assert _judge(with_(base, ["device", "systemVersion"], version)) is None, version
    for version in ("27.1.2.3", "27.", ".27", "27..1", "v27", "27.1 ", "12345", "27.1\n", "٢٧"):
        assert _judge(with_(base, ["device", "systemVersion"], version)) is not None, ascii(version)


def test_every_integer_has_a_stated_bound_inside_the_safe_integers() -> None:
    safe = 2**53 - 1
    assert 0 < pointing.POINTING_DIMENSION_MAX_PX <= safe and 0 < pointing.POINTING_SAMPLE_COUNT_MAX <= safe
    base = base_record()
    for path, bound in ((["camera", "photoDimensionsPx", "width"], pointing.POINTING_DIMENSION_MAX_PX),
                        (["camera", "activeFormatDimensionsPx", "height"], pointing.POINTING_DIMENSION_MAX_PX),
                        (["motion", "window", "sampleCount"], pointing.POINTING_SAMPLE_COUNT_MAX)):  # fmt: skip
        assert _judge(with_(base, path, bound)) is None, path
        for bad in (bound + 1, 2**53 + 1, 10**400, -0.0, 0):
            assert _judge(with_(base, path, bad)) is not None, (path, bad)


def test_the_auditors_seventeen_records_are_faults_in_the_fixture() -> None:
    names = [case["name"] for case in invalid_samples() if case["name"].startswith("the auditor's record")]
    assert len(names) == len(set(names)) == 17
    for name, record, _ in auditor_disagreements():
        assert _judge(record) is not None, name


def test_the_wire_faults_carry_the_verdict_the_mirror_gives() -> None:
    samples, faults = valid_samples(), wire_faults()
    kinds = {fault["kind"] for fault in faults}
    assert kinds == {"rename", "add", "raw"}
    assert sum(fault["kind"] == "rename" for fault in faults) >= 30
    for fault in faults:
        verdict = "accepted" if _judge(json.loads(wire_text(fault, samples))) is None else "refused"
        assert verdict == fault["verdict"], fault["name"]


def test_every_wire_fault_a_wire_rule_refuses_is_refused() -> None:
    faults = wire_faults()
    for fault in faults:
        if fault["kind"] in REFUSED_WIRE_FAULTS or fault.get("label") in REFUSED_WIRE_FAULTS:
            assert fault["verdict"] == "refused", fault["name"]
    assert {fault["verdict"] for fault in faults} == {"accepted", "refused"}


PROPERTY_CASE = st.fixed_dictionaries(
    {
        # Half the draws measured and placed, so accepted measured headings come up often
        "heading": st.one_of(st.just("measured"), st.sampled_from(HEADING_STATES)),
        # Each rule field half the time inside what the rules take, so records accepted with a field variant come up
        "headingAccuracyDeg": st.one_of(st.floats(min_value=0.0, max_value=25.0), st.floats(min_value=-5.0, max_value=40.0, allow_nan=False)),
        "reported": st.one_of(st.none(), st.floats(min_value=0.0, max_value=50.0, allow_nan=False)),
        "location": st.one_of(st.just("allowed"), st.sampled_from(LOCATION_STATES)),
        "altitude": st.booleans(),
        # One, or well away from one so the oracle needs no arithmetic: the tolerance is 1e-6
        "quaternionScale": st.one_of(st.just(1.0), st.just(1.0), st.floats(min_value=0.5, max_value=0.9999), st.floats(min_value=1.0001, max_value=1.5)),
        # One, inside the band or outside it, kept 0.05 g from its edges so the oracle needs no arithmetic either
        "gravityScale": st.one_of(
            st.just(1.0), st.floats(min_value=0.95, max_value=1.05), st.floats(min_value=-1.05, max_value=-0.95),
            st.floats(min_value=0.0, max_value=0.85), st.floats(min_value=1.15, max_value=12.0),
        ),  # fmt: skip
        "sampleOffsetSec": st.one_of(st.floats(min_value=-0.05, max_value=0.05), st.floats(min_value=-0.07, max_value=0.07, allow_nan=False)),
        "windowEndOffsetSec": st.floats(min_value=-1.0, max_value=12.0, allow_nan=False),
        # Half the draws keep every other field as it is; the rest set one field to a value from the shared table
        "variant": st.one_of(st.none(), st.sampled_from(range(len(FIELD_VARIANTS)))),
    }
)


def _seeded(test: Any) -> Any:
    for case in PROPERTY_EXAMPLES:
        test = example(case=case)(test)
    return test


@settings(max_examples=300, deadline=None)
@given(case=PROPERTY_CASE)
@_seeded
def test_pbt_pointing_4_records_are_accepted_exactly_when_the_rules_allow(case: dict[str, Any]) -> None:
    """P4 on the Python mirror: the same cases and the same oracle as the TypeScript property. A measured heading
    uses `headingAccuracyDeg` and `quaternionScale`; a heading left out uses `reported`; each ignores the others. A
    field variant applies only where its field is in the record."""
    record, applied = build_property_record(case)
    refusal = _judge(record)
    event(("accepted" if refusal is None else "refused") + (" with a field variant" if applied else ""))
    assert (refusal is None) == property_oracle(case, applied), refusal
    if refusal is None and not applied:
        dumped = PointingRecord.model_validate(record).model_dump(by_alias=True, exclude_unset=True)
        assert dumped == record
        assert ("altitude" in dumped["location"]) == (case["location"] == "allowed" and case["altitude"])


def test_the_property_examples_cover_every_heading_and_place_state_and_both_verdicts() -> None:
    assert {case["heading"] for case in PROPERTY_EXAMPLES} == set(HEADING_STATES)
    assert {case["location"] for case in PROPERTY_EXAMPLES} == set(LOCATION_STATES)
    verdicts = [property_oracle(case, build_property_record(case)[1]) for case in PROPERTY_EXAMPLES]
    assert True in verdicts and False in verdicts
    # The table varies every field the rules do not read, inside and outside what the contract takes
    assert {variant["ok"] for variant in FIELD_VARIANTS} == {True, False}
    varied = {tuple(variant["path"]) for variant in FIELD_VARIANTS}
    # The case itself varies the rest: the heading's accuracy, the quaternion, gravity, the sample offset, the
    # window's end (against its start) and each state
    by_the_case = {("heading", "attitude", "quaternion"), ("motion", "gravityDeviceG")}
    case_keys = {"headingAccuracyDeg", "sampleOffsetFromPhotoSec", "startOffsetSec", "endOffsetSec", "state"}
    for path, value in _walk(base_record()):
        if not path or isinstance(value, dict) or path[:-1] in by_the_case or path[-1] in case_keys:
            continue
        assert path in varied, path


def test_every_edge_gets_the_verdict_its_table_gives() -> None:
    samples = valid_samples()
    edges = edge_samples()
    assert len(edges) >= 150
    for edge in edges:
        assert ("accepted" if _judge(edge_record(edge, samples)) is None else "refused") == edge["verdict"], edge["name"]


def test_every_number_of_the_samples_is_in_the_edge_table() -> None:
    """A number field the table leaves out fails here, so a new field cannot go without its edges."""
    listed = {tuple(entry[3]) for entry in _numbers()}
    numbers = {
        path for sample in valid_samples().values() for path, value in _walk(sample)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }  # fmt: skip
    assert numbers - listed - UNBOUNDED_NUMBERS - BOUNDED_TOGETHER == set()
    assert listed & UNBOUNDED_NUMBERS == set() and listed & BOUNDED_TOGETHER == set()


def test_every_text_of_the_samples_is_in_the_edge_table() -> None:
    listed = {tuple(entry[2]) for entry in _texts()}
    texts = {path for sample in valid_samples().values() for path, value in _walk(sample) if isinstance(value, str)}
    discriminators = {("heading", "state"), ("location", "state"), ("camera", "fieldOfView", "state")}
    assert texts - listed - discriminators == set()
