"""A person's alignment of the predicted sky (D24, "Solve from here"), the Python half of its contract (SOLVER align-1).

The person drags, turns and scales the predicted sky over their photo until its stars sit on the dots they see; what
the screen sends is that pose: the frame centre's altitude and azimuth, its roll and its diagonal field of view, with
source "person". It is the person's, never a measurement: the solver takes it only as a search hint with the
contract's stated uncertainty, and the unchanged held-out gate alone decides a calibration (align-2 builds that).

Both sides are judged by tests/fixtures/person_alignment_contract.json, which this file writes and
packages/contracts/src/__tests__/alignment.test.ts reads. Regenerate it with
ALBEDIAN_UPDATE_PERSON_ALIGNMENT_FIXTURE=1 uv run pytest tests/integration/test_person_alignment_contract.py
"""

from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
from typing import Any

from hypothesis import given, settings, strategies as st
from pydantic import ValidationError
import pytest

from services.contracts import alignment
from services.contracts.sky_context import SKY_CONTEXT_CONVENTION

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "person_alignment_contract.json"
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_PERSON_ALIGNMENT_FIXTURE"
_DROP = object()


def sample(**changes: Any) -> dict[str, Any]:
    base = {"format": alignment.PERSON_ALIGNMENT_FORMAT, "formatVersion": alignment.PERSON_ALIGNMENT_FORMAT_VERSION,
            "source": "person", "centerAltDeg": 31.5, "centerAzDeg": 204.25, "rollDeg": -3.0, "fovDiagonalDeg": 72.7}  # fmt: skip
    out = {**base, **changes}
    return {key: value for key, value in out.items() if value is not _DROP}


VALID = [
    ("a person's alignment", sample()),
    ("the horizon at the frame's centre, looking north", sample(centerAltDeg=0.0, centerAzDeg=0.0)),
    ("the zenith", sample(centerAltDeg=90.0)),
    ("below the horizon (a frame tilted down)", sample(centerAltDeg=-90.0)),
    ("azimuth just under a full turn", sample(centerAzDeg=359.999)),
    ("roll a half turn either way", sample(rollDeg=180.0)),
    ("roll minus a half turn", sample(rollDeg=-180.0)),
    ("the widest field the contract takes", sample(fovDiagonalDeg=alignment.PERSON_ALIGNMENT_FOV_MAX_DEG)),
    ("a narrow telephoto field", sample(fovDiagonalDeg=0.5)),
    ("integers for every number", sample(centerAltDeg=30, centerAzDeg=200, rollDeg=0, fovDiagonalDeg=70)),
]
INVALID = [
    ("another format", sample(format="albedian.pointing-record")),
    ("format version 2", sample(formatVersion=2)),
    ("format version as text", sample(formatVersion="1")),
    ("format version true", sample(formatVersion=True)),
    ("no format", sample(format=_DROP)),
    ("another source", sample(source="record")),
    ("no source", sample(source=_DROP)),
    ("altitude above the zenith", sample(centerAltDeg=90.001)),
    ("altitude below the nadir", sample(centerAltDeg=-90.001)),
    ("azimuth a full turn", sample(centerAzDeg=360.0)),
    ("azimuth negative", sample(centerAzDeg=-0.001)),
    ("roll past a half turn", sample(rollDeg=180.001)),
    ("roll past minus a half turn", sample(rollDeg=-180.001)),
    ("a field of view of 0", sample(fovDiagonalDeg=0.0)),
    ("a negative field of view", sample(fovDiagonalDeg=-10.0)),
    ("a field of view past the widest", sample(fovDiagonalDeg=alignment.PERSON_ALIGNMENT_FOV_MAX_DEG + 0.001)),
    ("an altitude as text", sample(centerAltDeg="31.5")),
    ("an altitude that is true", sample(centerAltDeg=True)),
    ("an altitude that is null", sample(centerAltDeg=None)),
    ("no altitude", sample(centerAltDeg=_DROP)),
    ("no azimuth", sample(centerAzDeg=_DROP)),
    ("no roll", sample(rollDeg=_DROP)),
    ("no field of view", sample(fovDiagonalDeg=_DROP)),
    ("an unknown key", sample(uncertaintyDeg=5.0)),
    ("a Python field name", {**sample(centerAltDeg=_DROP), "center_alt_deg": 31.5}),
    ("a list", []),
    ("text", "albedian.person-alignment"),
    ("null", None),
]


def fixture_text() -> str:
    body = {
        "about": ("Samples for a person's alignment of the predicted sky (D24), written by "
                  "tests/integration/test_person_alignment_contract.py and judged alike by the Zod schema "
                  "(packages/contracts/src/alignment.ts) and the Python parser (services/contracts/alignment.py)."),  # fmt: skip
        "format": alignment.PERSON_ALIGNMENT_FORMAT,
        "formatVersion": alignment.PERSON_ALIGNMENT_FORMAT_VERSION,
        "source": alignment.PERSON_ALIGNMENT_SOURCE,
        "uncertaintyDeg": alignment.PERSON_ALIGNMENT_UNCERTAINTY_DEG,
        "fovMaxDeg": alignment.PERSON_ALIGNMENT_FOV_MAX_DEG,
        "convention": alignment.PERSON_ALIGNMENT_CONVENTION,
        "valid": [{"name": name, "alignment": value} for name, value in VALID],
        "invalid": [{"name": name, "alignment": value} for name, value in INVALID],
    }
    return json.dumps(body, indent=1, ensure_ascii=False) + "\n"


def test_the_fixture_is_current() -> None:
    text = fixture_text()
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text, f"regenerate with {UPDATE_FIXTURE_ENV}=1"


def test_the_alignment_is_drawn_in_the_predicted_skys_convention() -> None:
    assert alignment.PERSON_ALIGNMENT_CONVENTION == {**SKY_CONTEXT_CONVENTION, "roll": "upright_frame_positive_clockwise"}
    assert 0.0 < alignment.PERSON_ALIGNMENT_UNCERTAINTY_DEG <= 180.0


@pytest.mark.parametrize(("name", "value"), VALID, ids=[name for name, _ in VALID])
def test_every_valid_alignment_is_accepted_unchanged(name: str, value: dict[str, Any]) -> None:
    parsed = alignment.PersonAlignment.model_validate(copy.deepcopy(value))
    assert parsed.to_wire_dict() == {key: (float(v) if isinstance(v, int) and key.endswith("Deg") else v)
                                     for key, v in value.items()}  # fmt: skip
    assert json.loads(json.dumps(parsed.to_wire_dict())) == parsed.to_wire_dict()


@pytest.mark.parametrize(("name", "value"), INVALID, ids=[name for name, _ in INVALID])
def test_every_invalid_alignment_is_refused(name: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        alignment.PersonAlignment.model_validate(copy.deepcopy(value))


def test_a_refusal_never_repeats_the_value_it_refuses() -> None:
    with pytest.raises(ValidationError) as caught:
        alignment.PersonAlignment.model_validate(sample(centerAltDeg=123.456789, rollDeg=987.654321))
    assert "123.456789" not in str(caught.value) and "987.654321" not in str(caught.value)


@settings(max_examples=200, deadline=None)
@given(
    alt=st.floats(min_value=-90.0, max_value=90.0),
    az=st.floats(min_value=0.0, max_value=360.0, exclude_max=True),
    roll=st.floats(min_value=-180.0, max_value=180.0),
    fov=st.floats(min_value=1e-6, max_value=alignment.PERSON_ALIGNMENT_FOV_MAX_DEG),
)
def test_pbt_every_alignment_inside_its_bounds_round_trips(alt: float, az: float, roll: float, fov: float) -> None:
    value = sample(centerAltDeg=alt, centerAzDeg=az, rollDeg=roll, fovDiagonalDeg=fov)
    parsed = alignment.PersonAlignment.model_validate(value)
    assert parsed.to_wire_dict() == value
    assert all(math.isfinite(parsed.to_wire_dict()[key]) for key in ("centerAltDeg", "centerAzDeg", "rollDeg", "fovDiagonalDeg"))
