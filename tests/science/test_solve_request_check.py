"""A solve request with an absurd detection is refused before any solving, never a crash (solver-11).

Covers:
- The ten requests the contracts-1 search found answering 500: a rendered 960 x 540 field that solves, with one more
  detection at x, y or both at 1e200, 1e300 and 1.7e308 px, or with every centroid uncertainty at 1.7e308 px. Each is
  refused with 422 and one sentence, and `solve_and_verify_field` refuses each with `SolveInputRefusal` before any
  solving.
- Every edge of the check (`require_solvable_detections`): a position on the frame's far edge and the next float past
  it, a centroid uncertainty and a width at the frame's diagonal and past it, a peak-to-total ratio at 0 and 1 and
  past them, a frame size from 1 to 2**53 - 1 and past it; a sentence that names the field and never the value.
- Over generated requests, with hostile values seeded (NaN, the infinities, the largest floats, negative and enormous
  pixels, zero and negative sizes, a signal of zero or below, thousands of detections, none at all), every answer is
  a solution the contract takes or the contract's refusal: at the function and through POST /api/science/solve.
- What the detector measures always passes the check, so a frame that solved before still solves.
"""

from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math
from typing import Any

from fastapi.testclient import TestClient
from hypothesis import HealthCheck, event, example, given, settings, strategies as st
import numpy as np
import pytest

from services.api import app
from services.contracts import astronomy as contract
from services.science import solver as sv
from services.science.solver import detect_point_sources, render_synthetic_starfield, solve_and_verify_field
from services.science.wcs_models import ContractRefusal, SourceDetection

W, H = 960, 540
SHA = hashlib.sha256(b"solver-11 absurd detections").hexdigest()
# A made-up recording id in the minted form (`src_` and 16 lower-case hex characters)
SOURCE = {"sourceAssetId": f"src_{SHA[:16]}", "sourceSha256": SHA}
# Synthetic moment and place, the open ocean the other tests use
MOMENT = {"observedAtUTC": "2030-01-01T02:00:00Z", "latDeg": 30.0, "lonDeg": -40.0}
FUNCTION_MOMENT = {"obs_utc_iso": "2030-01-01T02:00:00Z", "observer_lat": 30.0, "observer_lon": -40.0}
FLOAT_MAX = 1.7976931348623157e308
SAFE_INTEGER = 2**53 - 1
KEYS = {"xPx": "x_px", "yPx": "y_px", "flux": "flux", "snr": "snr", "fwhmPx": "fwhm_px",
        "centroidUncertaintyPx": "centroid_uncertainty_px", "peakToTotalRatio": "peak_to_total_ratio"}


@lru_cache(maxsize=1)
def _measured() -> tuple[dict[str, float], ...]:
    """The contracts-1 search's field: rendered, measured by the detector, and calibrated by the solver."""
    field, _, _ = render_synthetic_starfield(width=W, height=H, read_noise_sigma=1.8, flux_scale=1100.0, seed=42)
    return tuple(
        {"xPx": d.x_px, "yPx": d.y_px, "flux": d.flux, "snr": d.snr, "fwhmPx": d.fwhm_px,
         "centroidUncertaintyPx": d.centroid_uncertainty_px, "peakToTotalRatio": d.peak_to_total_ratio}
        for d in detect_point_sources(field)
    )


def _extra(**change: Any) -> dict[str, Any]:
    detection = {"xPx": 480.0, "yPx": 270.0, "flux": 900.0, "snr": 20.0, "fwhmPx": 2.2, "centroidUncertaintyPx": 0.2}
    detection.update(change)
    return detection


def _the_ten() -> list[tuple[str, list[dict[str, Any]]]]:
    cases = []
    for far in (1e200, 1e300, 1.7e308):
        cases.append((f"x {far:g}", [*_measured(), _extra(xPx=far)]))
        cases.append((f"y {far:g}", [*_measured(), _extra(yPx=far)]))
        cases.append((f"x and y {far:g}", [*_measured(), _extra(xPx=far, yPx=far)]))
    cases.append(("every centroidUncertaintyPx 1.7e308", [{**d, "centroidUncertaintyPx": 1.7e308} for d in _measured()]))
    return cases


def _body(detections: list[dict[str, Any]], width: Any = W, height: Any = H) -> dict[str, Any]:
    return {**MOMENT, **SOURCE, "width": width, "height": height, "detections": detections}


def _records(detections: list[dict[str, Any]]) -> list[SourceDetection]:
    return [SourceDetection(**{KEYS[k]: v for k, v in d.items()}, asset_id=SOURCE["sourceAssetId"]) for d in detections]


def _client() -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)


def _post(client: TestClient, body: dict[str, Any]):
    # NaN and the infinities go as the JSON tokens a client could send
    return client.post("/api/science/solve", content=json.dumps(body), headers={"content-type": "application/json"})


def _a_sentence(text: str) -> bool:
    return text.endswith(".") and text.count(". ") == 0 and "\n" not in text


def test_the_field_solves_as_measured() -> None:
    # The cases below add one value to a field that solves, so the solve reaches the findings it builds
    response = _post(_client(), _body(list(_measured())))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "calibrated"


@pytest.mark.parametrize("label, detections", _the_ten(), ids=[label for label, _ in _the_ten()])
def test_the_ten_requests_that_answered_500_are_refused_before_any_solving(label: str, detections) -> None:
    response = _post(_client(), _body(detections))
    assert response.status_code == 422, (response.status_code, response.text[:300])
    detail = response.json()["detail"]
    assert isinstance(detail, str) and _a_sentence(detail), detail
    assert "e+" not in detail and "1.7" not in detail, detail
    # The function refuses them too, before it builds a triangle: no candidate is counted
    events: list[tuple[int, int]] = []
    with pytest.raises(sv.SolveInputRefusal) as refusal:
        solve_and_verify_field(_records(detections), W, H, on_candidate=lambda d, t: events.append((d, t)),
                               source_asset_id=SOURCE["sourceAssetId"], source_sha256=SHA, **FUNCTION_MOMENT)
    assert str(refusal.value) == detail
    assert events == []


# =============================================================================================
# The check's edges
# =============================================================================================

DIAGONAL = math.hypot(W, H)


@pytest.mark.parametrize("change", [
    {"xPx": 0.0}, {"xPx": float(W)}, {"yPx": 0.0}, {"yPx": float(H)},
    {"centroidUncertaintyPx": DIAGONAL}, {"centroidUncertaintyPx": 5e-324}, {"fwhmPx": DIAGONAL}, {"fwhmPx": 0.0},
    {"peakToTotalRatio": 0.0}, {"peakToTotalRatio": 1.0}, {"flux": FLOAT_MAX}, {"snr": FLOAT_MAX}, {"flux": 5e-324},
], ids=repr)
def test_a_value_on_the_edge_of_the_check_is_solved(change: dict[str, float]) -> None:
    detections = [*_measured(), _extra(**change)]
    sv.require_solvable_detections(_records(detections), W, H)
    response = _post(_client(), _body(detections))
    assert response.status_code == 200, response.text
    contract.parse_observation_solution(response.json())


@pytest.mark.parametrize("change, field", [
    ({"xPx": math.nextafter(float(W), math.inf)}, "xPx"), ({"yPx": math.nextafter(float(H), math.inf)}, "yPx"),
    ({"xPx": 1e200}, "xPx"), ({"yPx": FLOAT_MAX}, "yPx"),
    ({"centroidUncertaintyPx": math.nextafter(DIAGONAL, math.inf)}, "centroidUncertaintyPx"),
    ({"fwhmPx": math.nextafter(DIAGONAL, math.inf)}, "fwhmPx"),
    ({"peakToTotalRatio": math.nextafter(1.0, 2.0)}, "peakToTotalRatio"), ({"peakToTotalRatio": -5e-324}, "peakToTotalRatio"),
    ({"peakToTotalRatio": -1e300}, "peakToTotalRatio"),
], ids=repr)
def test_a_value_past_the_edge_of_the_check_is_refused_with_its_field(change: dict[str, float], field: str) -> None:
    detections = [*_measured(), _extra(**change)]
    index = len(detections) - 1
    with pytest.raises(sv.SolveInputRefusal) as refusal:
        sv.require_solvable_detections(_records(detections), W, H)
    assert str(refusal.value).startswith(f"detections.{index}.{field} "), str(refusal.value)
    response = _post(_client(), _body(detections))
    assert response.status_code == 422 and response.json()["detail"] == str(refusal.value), response.text


@pytest.mark.parametrize("width, height, refused", [
    (1, 1, None), (SAFE_INTEGER, H, None), (W, SAFE_INTEGER, None), (np.int64(W), np.int64(H), None),
    (SAFE_INTEGER + 1, H, "width"), (W, SAFE_INTEGER + 1, "height"), (10**400, H, "width"), (0, H, "width"),
    (W, -1, "height"), (True, H, "width"), (float(W), H, "width"),
], ids=repr)
def test_a_frame_size_is_a_whole_number_of_pixels_from_1_to_the_largest_safe_integer(width, height, refused) -> None:
    if refused is None:
        sv.require_solvable_detections([], width, height)
        return
    with pytest.raises(sv.SolveInputRefusal) as refusal:
        sv.require_solvable_detections([], width, height)
    assert str(refusal.value).startswith(f"{refused} "), str(refusal.value)


def test_a_frame_too_large_for_the_solver_is_refused_through_the_route() -> None:
    # A whole number past 2**53 - 1 is a frame size no record carries; main answered OverflowError's 500 for 10**400
    for width in (SAFE_INTEGER + 1, 10**400):
        response = _post(_client(), _body(list(_measured()), width=width))
        assert response.status_code == 422, (width, response.status_code, response.text[:200])
        assert _a_sentence(response.json()["detail"])


def test_the_check_runs_before_the_observer_and_the_catalog_are_judged() -> None:
    # After the hash, the check is the first thing judged: a latitude without a longitude is not reached
    with pytest.raises(sv.SolveInputRefusal):
        solve_and_verify_field(_records([*_measured(), _extra(xPx=1e200)]), W, H, observer_lat=30.0,
                               source_asset_id=SOURCE["sourceAssetId"], source_sha256=SHA)


def test_a_record_the_solver_builds_and_the_contract_refuses_still_answers_500(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only the check's refusal is the client's answer: a record the solver itself builds and the contract refuses is
    # the solver's fault and stays a 500, as the contracts unit wrote it
    from services.science import router as science_router

    def solver_fault(**_: Any):
        raise ContractRefusal("A finding's ra_deg is outside the contract: this number is not finite.")

    monkeypatch.setattr(science_router, "solve_and_verify_field", solver_fault)
    assert _post(_client(), _body(list(_measured()))).status_code == 500


def test_what_the_detector_measures_passes_the_check() -> None:
    for seed in range(6):
        for width, height, sources in ((W, H, 40), (64, 48, 3), (1600, 1200, 400)):
            field, _, _ = render_synthetic_starfield(width=width, height=height, read_noise_sigma=1.8,
                                                     flux_scale=1100.0, seed=seed)
            detections = detect_point_sources(field)
            sv.require_solvable_detections(detections, width, height)


# =============================================================================================
# One table of every bound the check defines: a sample at each inside end and one just outside
# =============================================================================================

# (wire name, low, high, low included, high included, the rule's words): a detection's numbers in the 960 x 540
# frame. Below an included low end the record refuses first (its own 0); every other outside sample is the check's
EDGE_TABLE = [
    ("xPx", 0.0, float(W), True, True, "a position lies on the frame, from 0 to its width"),
    ("yPx", 0.0, float(H), True, True, "a position lies on the frame, from 0 to its height"),
    ("centroidUncertaintyPx", 0.0, DIAGONAL, False, True, "a centroid uncertainty is at most the frame's diagonal"),
    ("fwhmPx", 0.0, DIAGONAL, True, True, "a width is at most the frame's diagonal"),
    ("peakToTotalRatio", 0.0, 1.0, True, True, "a peak-to-total ratio lies from 0 to 1"),
]
RULES = {row[0]: row[5] for row in EDGE_TABLE}
# (name, low, high): a frame's size, a whole number
FRAME_TABLE = [("width", 1, SAFE_INTEGER), ("height", 1, SAFE_INTEGER)]
FRAME_RULE = "a frame's size is a whole number of pixels from 1 to 2**53 - 1"


def _edge_samples() -> list[tuple[str, float, bool]]:
    """(wire name, value, inside) for every row of EDGE_TABLE: both inside ends and the next float past each."""
    samples = []
    for name, low, high, low_in, high_in, _ in EDGE_TABLE:
        samples.append((name, low if low_in else math.nextafter(low, math.inf), True))
        samples.append((name, high if high_in else math.nextafter(high, -math.inf), True))
        samples.append((name, math.nextafter(low, -math.inf) if low_in else low, False))
        samples.append((name, math.nextafter(high, math.inf) if high_in else high, False))
    return samples


def _frame_samples() -> list[tuple[str, int, bool]]:
    return [sample for name, low, high in FRAME_TABLE
            for sample in ((name, low, True), (name, high, True), (name, low - 1, False), (name, high + 1, False))]


@pytest.mark.parametrize("name, value, inside", _edge_samples(), ids=repr)
def test_every_bound_of_a_detection_from_the_table(name: str, value: float, inside: bool) -> None:
    detections = [*_measured(), _extra(**{name: value})]
    response = _post(_client(), _body(detections))
    if inside:
        sv.require_solvable_detections(_records(detections), W, H)
        assert response.status_code == 200, response.text[:300]
        contract.parse_observation_solution(response.json())
        return
    assert response.status_code == 422, response.text[:300]
    try:
        records = _records(detections)
    except ContractRefusal as refusal:
        # The record's own bound, below its 0
        assert not isinstance(refusal, sv.SolveInputRefusal) and response.json()["detail"] == str(refusal)
        return
    with pytest.raises(sv.SolveInputRefusal) as refusal:
        sv.require_solvable_detections(records, W, H)
    assert str(refusal.value) == f"detections.{len(detections) - 1}.{name} is outside its range: {RULES[name]}."
    assert response.json()["detail"] == str(refusal.value)


@pytest.mark.parametrize("name, value, inside", _frame_samples(), ids=repr)
def test_every_bound_of_a_frame_size_from_the_table(name: str, value: int, inside: bool) -> None:
    width, height = (value, H) if name == "width" else (W, value)
    if inside:
        sv.require_solvable_detections([], width, height)
        solution = solve_and_verify_field([], width, height, source_asset_id=SOURCE["sourceAssetId"],
                                          source_sha256=SHA, **FUNCTION_MOMENT)
        contract.parse_observation_solution(solution.to_contract_dict())
        return
    with pytest.raises(sv.SolveInputRefusal) as refusal:
        sv.require_solvable_detections([], width, height)
    assert str(refusal.value) == f"{name} is outside its range: {FRAME_RULE}."
    # Through the route a size below 1 is refused by the request model first, one past 2**53 - 1 by the check
    response = _post(_client(), _body([], width, height))
    assert response.status_code == 422, response.text[:300]


# =============================================================================================
# Generated requests: a solution the contract takes, or the contract's refusal
# =============================================================================================

HOSTILE = [math.nan, math.inf, -math.inf, FLOAT_MAX, -FLOAT_MAX, 1e300, 1e200, -1e200, 1e17, -1.0, -5e-324, 0.0, -0.0,
           5e-324, 1e-300]


def _edge_values(width: int, height: int) -> list[float]:
    diagonal = math.hypot(width, height)
    return [float(width), math.nextafter(float(width), math.inf), float(height), math.nextafter(float(height), math.inf),
            diagonal, math.nextafter(diagonal, math.inf), 1.0, math.nextafter(1.0, 2.0)]


@st.composite
def _requests(draw) -> tuple[int, int, list[dict[str, Any]]]:
    base = draw(st.sampled_from(["measured", "scattered", "none", "thousands"]))
    width, height = W, H
    rng = np.random.default_rng(draw(st.integers(0, 2**16)))
    if base == "measured":
        detections = [dict(d) for d in _measured()]
    elif base == "scattered":
        detections = [_extra(xPx=float(x), yPx=float(y)) for x, y in rng.uniform([0, 0], [W, H], (draw(st.integers(1, 40)), 2))]
    elif base == "none":
        detections = []
    else:
        detections = [dict(d) for d in _measured()] + [
            _extra(xPx=float(x), yPx=float(y), snr=float(s)) for x, y, s in rng.uniform([0, 0, 4], [W, H, 9], (3000, 3))
        ]
    if draw(st.booleans()):
        width = draw(st.sampled_from([1, 2, 7, W, 4032, 10**9, SAFE_INTEGER, SAFE_INTEGER + 1, 10**400]))
        height = draw(st.sampled_from([1, 3, H, 3024, 10**9, SAFE_INTEGER, SAFE_INTEGER + 1]))
    values = st.one_of(st.sampled_from(HOSTILE), st.sampled_from(_edge_values(W, H)), st.floats(),
                       st.floats(0.0, float(max(W, H))))
    for _ in range(draw(st.integers(0, 3))):
        key = draw(st.sampled_from(list(KEYS)))
        value = draw(values)
        if detections and draw(st.booleans()):
            detections[draw(st.integers(0, len(detections) - 1))][key] = value
        else:
            detections.append(_extra(**{key: value}))
    return width, height, detections


def _function_outcome(width, height, detections) -> str:
    """'refused by the record', 'refused by the check' or 'solution', or the failure raised."""
    try:
        records = _records(detections)
    except ContractRefusal:
        return "refused by the record"
    try:
        solution = solve_and_verify_field(records, width, height, source_asset_id=SOURCE["sourceAssetId"],
                                          source_sha256=SHA, **FUNCTION_MOMENT)
    except sv.SolveInputRefusal:
        return "refused by the check"
    contract.parse_observation_solution(solution.to_contract_dict())
    return f"solution, {solution.status}"


# The ten, the frame sizes past what a record carries, and the thousands
@example(request=(W, H, [*_measured(), _extra(xPx=1e200)]))
@example(request=(W, H, [{**d, "centroidUncertaintyPx": 1.7e308} for d in _measured()]))
@example(request=(W, H, [*_measured(), _extra(xPx=float(W), centroidUncertaintyPx=DIAGONAL)]))
@example(request=(10**400, H, list(_measured())))
@example(request=(SAFE_INTEGER, SAFE_INTEGER, [*_measured(), _extra(xPx=float(SAFE_INTEGER), yPx=float(SAFE_INTEGER))]))
@example(request=(W, H, []))
@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
@given(request=_requests())
def test_pbt_every_generated_request_gives_a_solution_or_a_refusal_at_the_function(request) -> None:
    width, height, detections = request
    outcome = _function_outcome(width, height, detections)
    event(outcome)
    assert outcome in {"refused by the record", "refused by the check"} or outcome.startswith("solution, "), outcome


@example(request=(W, H, [*_measured(), _extra(yPx=1.7e308)]))
@example(request=(W, H, [{**d, "centroidUncertaintyPx": 1.7e308} for d in _measured()]))
@example(request=(W, H, [*_measured(), _extra(yPx=float(H), fwhmPx=DIAGONAL)]))
@example(request=(SAFE_INTEGER + 1, H, list(_measured())))
@example(request=(W, H, [*_measured(), _extra(snr=0.0)]))
@example(request=(W, H, [*_measured(), _extra(fwhmPx=-1.0)]))
@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
@given(request=_requests())
def test_pbt_every_generated_request_answers_a_solution_or_422_through_the_route(request) -> None:
    width, height, detections = request
    response = _post(_client(), _body(detections, width, height))
    assert response.status_code in (200, 422), (response.status_code, response.text[:300])
    event(f"{response.status_code} {response.json().get('status', '') if response.status_code == 200 else ''}".strip())
    if response.status_code == 200:
        contract.parse_observation_solution(response.json())
    else:
        assert isinstance(response.json()["detail"], (str, list)), response.text[:300]
