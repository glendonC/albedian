"""go-there-predicted-2 (DECIDED-BY-LEAD 25 Nov 02:45, A): the sky view a request carries while the predicted sky is on
screen, parsed by the voice backend's request context as the contract's SkyView, with the same rules as the Zod side
(packages/contracts/src/__tests__/skyView.test.ts)."""

from __future__ import annotations

import pytest

from services.contracts.pointing import UNIT_QUATERNION_TOLERANCE
from services.contracts.voice import SkyView
from services.voice.intent import ActionContext

VIEW = {"cameraQuaternionIcrs": [0.5, -0.5, 0.5, 0.5], "fovVerticalDeg": 45}


def test_a_request_carries_the_sky_view_as_the_contract_reads_it() -> None:
    context = ActionContext.from_dict({"sessionId": "session-sample", "skyView": VIEW})
    assert context.sky_view == SkyView.model_validate(VIEW)
    assert context.sky_view.camera_quaternion_icrs == [0.5, -0.5, 0.5, 0.5]
    assert context.sky_view.fov_vertical_deg == 45
    assert ActionContext.from_dict({"sessionId": "session-sample"}).sky_view is None


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "north",
        {**VIEW, "cameraQuaternionIcrs": [0, 0, 0, 1 + UNIT_QUATERNION_TOLERANCE * 2]},
        {**VIEW, "cameraQuaternionIcrs": [0, 0, 1]},
        {**VIEW, "fovVerticalDeg": 0.99},
        {**VIEW, "fovVerticalDeg": 140.01},
        {"fovVerticalDeg": 45},
    ],
)
def test_a_request_with_a_sky_view_the_contract_refuses_is_refused(bad: object) -> None:
    with pytest.raises(ValueError):
        ActionContext.from_dict({"sessionId": "session-sample", "skyView": bad})


def test_the_unit_tolerance_is_the_shared_one() -> None:
    inside = {**VIEW, "cameraQuaternionIcrs": [0, 0, 0, 1 + UNIT_QUATERNION_TOLERANCE / 2]}
    assert ActionContext.from_dict({"skyView": inside}).sky_view is not None


CONTEXT = {"sessionId": "s-made-up", "sourceAssetId": "src_made-up", "viewRevision": 4, "calibrationState": "predicted_only", "selectedFindingId": "finding-star-vega"}


def test_the_rules_carry_the_sky_view_onto_space_enter_as_the_ts_parser_does() -> None:
    from services.voice.intent import parse_voice_intent

    with_view = parse_voice_intent("take me there", {**CONTEXT, "skyView": VIEW}, "req-1", 4, current_year=2026)
    assert with_view.action is not None and with_view.action["type"] == "space.enter"
    assert with_view.action["skyView"] == VIEW
    without = parse_voice_intent("take me there", CONTEXT, "req-1", 4, current_year=2026)
    assert without.action is not None and "skyView" not in without.action


def test_a_model_call_to_fly_carries_the_sky_view_on() -> None:
    from services.voice import claude

    action = claude.compose_action("space_enter", {"findingId": "finding-star-vega"}, ActionContext.from_dict({**CONTEXT, "skyView": VIEW}), "req-1", 4, current_year=2026)
    assert action is not None and action["skyView"] == VIEW
    plain = claude.compose_action("space_enter", {"findingId": "finding-star-vega"}, ActionContext.from_dict(CONTEXT), "req-1", 4, current_year=2026)
    assert plain is not None and "skyView" not in plain
