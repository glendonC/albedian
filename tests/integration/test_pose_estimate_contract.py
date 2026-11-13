"""The pose estimate in the observation solution contract (REQ-SKY-5.1 to 5.3, P-SKY-7), on the Python mirror.

Covers:
- `PoseEstimate` keeps the bounds of the contract's `PoseEstimateSchema` and serializes in its camelCase
- `ObservationSolution.pose_estimate` only on an approximate solution with a WCS, every swept WCS on that WCS's
  crpix, refused in the contract's own words; serialized only when present
- `CelestialFindingRecord` refuses a source detection on a predicted or simulated finding
- P-SKY-7: the generated solutions the TypeScript property accepts and refuses, judged alike
- The fixture's pose sample is centred on the Moon and level under its nominal WCS
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from hypothesis import example, given, settings, strategies as st
import numpy as np
import pytest

from services.science.catalog import altaz_to_icrs, angular_separation_deg, get_star_by_name, icrs_to_altaz
from services.science.wcs_models import (
    CelestialFindingRecord,
    ObservationSolution,
    PoseEstimate,
    SolutionQuality,
    SolverIdentity,
    SourceDetection,
    WCSModel,
)

WIDTH, HEIGHT = 960, 540
# A synthetic moment and open-ocean place that point at nobody, as in test_unregistered_detections.py
MOMENT = {"observed_at_utc": "2030-01-01T02:00:00Z", "observer_lat": 30.0, "observer_lon": -40.0}
RECORD_SHA = hashlib.sha256(b"hand-built test record").hexdigest()
ASTRONOMY_TS = Path(__file__).resolve().parents[2] / "packages" / "contracts" / "src" / "astronomy.ts"
CONTRACT_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "python_contract_solutions.json"
STATUSES = ("pending", "calibrated", "approximate", "predicted_only", "unresolved", "ambiguous")
SOLUTION_RULE = "A pose estimate belongs only to an approximate solution with a WCS: it states how far that WCS may be off."
CRPIX_RULE = "Every swept WCS of a pose estimate has the nominal WCS's crpixX and crpixY: all of them describe the same frame."
FINDING_RULE = "finding was not measured in the pixels, so it carries no source detection"
BEACON_RULE = (
    "A pose estimate's beacon is the one its method lines up from: the moon for moon_camera_profile_level_head and "
    "moon_horizon, the sun for sun_horizon."
)

NOMINAL = WCSModel(
    crval_ra_deg=83.8, crval_dec_deg=4.0, crpix_x=480.0, crpix_y=270.0, scale_deg_per_px=0.035,
    width=WIDTH, height=HEIGHT,
)
DETECTION = SourceDetection(x_px=412.5, y_px=300.2, flux=5000.0, snr=25.0, fwhm_px=2.5, centroid_uncertainty_px=0.1)
# A real catalog row (Rigel), for the catalog-backed findings that the solvers' predicted stars are
CATALOG_STAR = get_star_by_name("Rigel")
# The numeric fields of PoseEstimate, with their camelCase names in the TypeScript test
POSE_NUMBER_FIELDS = {
    "center_alt_deg": "centerAltDeg",
    "center_az_deg": "centerAzDeg",
    "roll_deg": "rollDeg",
    "fov_diagonal_deg": "fovDiagonalDeg",
    "max_center_offset_deg": "maxCenterOffsetDeg",
    "beacon_centroid_uncertainty_deg": "beaconCentroidUncertaintyDeg",
}
# One field outside the bounds of PoseEstimateSchema each, named as in the TypeScript test
POSE_FIELD_FAULTS: dict[str, dict[str, Any]] = {
    "method": {"method": "guessed"},
    "beacon": {"beacon": "jupiter"},
    "centerAltHigh": {"center_alt_deg": 90.5},
    "centerAltLow": {"center_alt_deg": -90.5},
    "centerAzNegative": {"center_az_deg": -0.5},
    "centerAzHigh": {"center_az_deg": 360.5},
    "rollHigh": {"roll_deg": 180.5},
    "rollLow": {"roll_deg": -180.5},
    "fovZero": {"fov_diagonal_deg": 0.0},
    "fovWide": {"fov_diagonal_deg": 175.5},
    "fovSourceEmpty": {"fov_source": ""},
    "assumptionsEmpty": {"assumptions": ()},
    "assumptionBlank": {"assumptions": ("",)},
    "offsetNegative": {"max_center_offset_deg": -0.01},
    "centroidNegative": {"beacon_centroid_uncertainty_deg": -0.01},
    # Zod's z.number() takes no NaN, boolean or text, so neither does the mirror, on any numeric field
    **{
        f"{camel}{label}": {key: value}
        for key, camel in POSE_NUMBER_FIELDS.items()
        for label, value in (("NaN", float("nan")), ("True", True), ("Text", "x"))
    },
    # Finite, so the mirror never writes an Infinity that JSON cannot carry
    "maxCenterOffsetDegInfinity": {"max_center_offset_deg": float("inf")},
    "beaconCentroidUncertaintyDegInfinity": {"beacon_centroid_uncertainty_deg": float("inf")},
    # The method lines up from its own beacon
    "levelHeadWithTheSun": {"beacon": "sun"},
    "moonHorizonWithTheSun": {"method": "moon_horizon", "beacon": "sun"},
    "sunHorizonWithTheMoon": {"method": "sun_horizon"},
    # A list of sentences, not a mapping (Zod refuses an object; Python would take its keys)
    "assumptionsMapping": {"assumptions": {"The head is level.": 1}},
}


def swept_wcs(count: int) -> tuple[WCSModel, ...]:
    """Synthetic values for contract tests only, describing no real camera: swept WCS i varies the scale by 10 percent
    and the rotation by 3 deg about the nominal WCS, on its crpix, as the TypeScript test does."""
    return tuple(
        replace(
            NOMINAL,
            scale_deg_per_px=NOMINAL.scale_deg_per_px * (0.9 + 0.1 * (i % 3)),
            rotation_deg=NOMINAL.rotation_deg + 3.0 * ((i // 3) % 3 - 1),
        )
        for i in range(count)
    )


def pose_estimate(count: int = 9, **overrides: Any) -> PoseEstimate:
    fields: dict[str, Any] = {
        "method": "moon_camera_profile_level_head",
        "beacon": "moon",
        "center_alt_deg": 33.0,
        "center_az_deg": 224.0,
        "roll_deg": 0.0,
        "fov_diagonal_deg": 38.2,
        "fov_source": "A synthetic camera profile for contract tests",
        "assumptions": ("The head is level.",),
        "swept_wcs": swept_wcs(count),
        "max_center_offset_deg": 0.4,
        "beacon_centroid_uncertainty_deg": 0.004,
    }
    fields.update(overrides)
    return PoseEstimate(**fields)


def finding(index: int, evidence_class: str, *, detection: bool, catalog: bool = False) -> CelestialFindingRecord:
    # An observed finding is located by its detection (no verified WCS here); the others carry a sky position
    sky = {} if evidence_class == "observed" else {"ra_deg": 83.8, "dec_deg": 4.0, "uncertainty_arcsec": 12.0}
    return CelestialFindingRecord(
        id=f"f{index}", observation_solution_id="sol-pose", evidence_class=evidence_class, title=f"Finding f{index}",
        subtitle="Generated", source_detection=DETECTION if detection else None,
        catalog_star=CATALOG_STAR if catalog else None,
        limitations=("Generated for contract tests.",), source_sha256=RECORD_SHA, **sky,
    )


def solution(
    status: str = "approximate",
    *,
    wcs: bool = True,
    pose: PoseEstimate | None = None,
    findings: tuple[CelestialFindingRecord, ...] = (),
) -> ObservationSolution:
    """A solution under a status whose WCS, when it has one, did not pass held-out verification; a pending one has
    no quality and no solver, since nothing has run."""
    quality = SolutionQuality(
        detected_candidate_count=1, matched_inlier_count=1, held_out_pair_count=0, held_out_inliers=0,
        median_residual_px=None, p95_residual_px=None, median_residual_arcsec=None,
        passed_independent_verification=False,
    )
    return ObservationSolution(
        id="sol-pose", source_asset_id="hand-built", source_sha256=RECORD_SHA, frame_time_sec=0.0, revision=1,
        status=status, wcs=NOMINAL if wcs else None,
        quality=None if status == "pending" else quality,
        solver=None if status == "pending" else SolverIdentity(name="hand-built in this test", version="0", catalog_release="none"),
        findings=list(findings), pose_estimate=pose, **MOMENT,
    )


def _none_paths(value: Any, path: str = "$") -> list[str]:
    if value is None:
        return [path]
    if isinstance(value, dict):
        return [p for key, item in value.items() for p in _none_paths(item, f"{path}.{key}")]
    if isinstance(value, list):
        return [p for idx, item in enumerate(value) for p in _none_paths(item, f"{path}[{idx}]")]
    return []


def test_pose_estimate_serializes_in_the_contracts_camel_case() -> None:
    pose = pose_estimate(9)
    payload = pose.to_contract_dict()
    assert list(payload) == [
        "method", "beacon", "centerAltDeg", "centerAzDeg", "rollDeg", "fovDiagonalDeg", "fovSource", "assumptions",
        "sweptWcs", "maxCenterOffsetDeg", "beaconCentroidUncertaintyDeg",
    ]
    assert payload["sweptWcs"] == [w.to_contract_dict() for w in swept_wcs(9)]
    assert payload["assumptions"] == ["The head is level."]
    assert (payload["centerAltDeg"], payload["fovDiagonalDeg"], payload["beaconCentroidUncertaintyDeg"]) == (33.0, 38.2, 0.004)
    assert _none_paths(payload) == []
    # Sequences are kept as tuples, so the frozen record cannot change after it was checked
    listed = pose_estimate(2, assumptions=["The head is level."], swept_wcs=list(swept_wcs(2)))
    assert isinstance(listed.assumptions, tuple) and isinstance(listed.swept_wcs, tuple)


@pytest.mark.parametrize("fault", sorted(POSE_FIELD_FAULTS))
def test_pose_estimate_refuses_a_field_outside_the_contracts_bounds(fault: str) -> None:
    with pytest.raises(ValueError, match="pose estimate"):
        pose_estimate(9, **POSE_FIELD_FAULTS[fault])


def test_pose_estimate_keeps_the_bounds_of_the_contract() -> None:
    for count in (0, 1, 28):
        with pytest.raises(ValueError, match="2 to 27"):
            pose_estimate(count)
    with pytest.raises(ValueError, match="WCSModel"):
        pose_estimate(9, swept_wcs=(*swept_wcs(8), NOMINAL.to_contract_dict()))
    # Zod's z.number() takes no boolean, NaN or text, so neither does the mirror (every numeric field is in
    # POSE_FIELD_FAULTS too)
    for key in POSE_NUMBER_FIELDS:
        for not_a_number in (True, False, float("nan"), "33", "x", None):
            with pytest.raises(ValueError, match="pose estimate"):
                pose_estimate(9, **{key: not_a_number})
    # A NumPy float is a number, as a producer computing with NumPy hands one over
    assert pose_estimate(9, center_alt_deg=np.float32(33.0)).to_contract_dict()["centerAltDeg"] == 33.0
    # The assumptions are a list or tuple of sentences, as z.array takes them: not one string, a mapping or a set
    for not_a_list in ("The head is level.", {"The head is level.": 1}, {"The head is level."}, None):
        with pytest.raises(ValueError, match="assumptions"):
            pose_estimate(9, assumptions=not_a_list)
    assert pose_estimate(9, assumptions=["The head is level."]).assumptions == ("The head is level.",)
    edges = (
        {"center_alt_deg": -90.0}, {"center_alt_deg": 90.0}, {"center_az_deg": 0.0}, {"center_az_deg": 360.0},
        {"roll_deg": -180.0}, {"roll_deg": 180.0}, {"fov_diagonal_deg": 175.0},
        {"max_center_offset_deg": 0.0, "beacon_centroid_uncertainty_deg": 0.0}, {"method": "moon_horizon"},
        {"method": "sun_horizon", "beacon": "sun"},
    )
    for edge in edges:
        assert pose_estimate(9, **edge).to_contract_dict()["sweptWcs"]
    for count in (2, 27):
        assert len(pose_estimate(count).swept_wcs) == count


def test_pose_estimate_belongs_only_to_an_approximate_solution_with_a_wcs() -> None:
    accepted = solution(pose=pose_estimate(9)).to_contract_dict()
    assert accepted["poseEstimate"] == pose_estimate(9).to_contract_dict()
    for status in STATUSES:
        if status != "approximate":
            with pytest.raises(ValueError) as refused:
                solution(status, pose=pose_estimate(9))
            assert str(refused.value) == SOLUTION_RULE, status
    with pytest.raises(ValueError) as refused:
        solution(wcs=False, pose=pose_estimate(9))
    assert str(refused.value) == SOLUTION_RULE


def test_every_swept_wcs_sits_on_the_nominal_crpix() -> None:
    for key in ("crpix_x", "crpix_y"):
        sweep = list(swept_wcs(9))
        sweep[4] = replace(sweep[4], **{key: getattr(NOMINAL, key) + 0.5})
        with pytest.raises(ValueError) as refused:
            solution(pose=pose_estimate(9, swept_wcs=tuple(sweep)))
        assert str(refused.value) == CRPIX_RULE, key


def test_solution_serializes_its_pose_estimate_only_when_present() -> None:
    assert "poseEstimate" not in solution().to_contract_dict()
    # The keyword has a default, so every existing constructor call still works and sends no pose estimate
    for status in STATUSES:
        assert "poseEstimate" not in solution(status).to_contract_dict()


# With and without a catalog row behind it: the predicted stars the solvers send carry one
@pytest.mark.parametrize("catalog", [False, True], ids=["no-catalog-row", "catalog-backed"])
@pytest.mark.parametrize("evidence_class", ["predicted", "simulated"])
def test_predicted_or_simulated_finding_refuses_a_source_detection(evidence_class: str, catalog: bool) -> None:
    with pytest.raises(ValueError, match=FINDING_RULE):
        finding(0, evidence_class, detection=True, catalog=catalog)
    payload = finding(0, evidence_class, detection=False, catalog=catalog).to_contract_dict(source_asset_id="hand-built")
    assert "sourceDetection" not in payload
    assert ("catalogRef" in payload) == catalog


def test_pose_estimate_ties_its_method_to_its_beacon() -> None:
    beacons = {"moon_camera_profile_level_head": "moon", "moon_horizon": "moon", "sun_horizon": "sun"}
    for method, beacon in beacons.items():
        assert pose_estimate(9, method=method, beacon=beacon).beacon == beacon
        with pytest.raises(ValueError) as refused:
            pose_estimate(9, method=method, beacon="sun" if beacon == "moon" else "moon")
        assert str(refused.value) == BEACON_RULE, method


@pytest.mark.parametrize("key", ["max_center_offset_deg", "beacon_centroid_uncertainty_deg"])
def test_pose_estimate_refuses_an_infinite_offset_that_json_cannot_carry(key: str) -> None:
    with pytest.raises(ValueError, match=f"{key} is finite"):
        pose_estimate(9, **{key: float("inf")})
    # The largest finite float stays a number JSON carries
    payload = pose_estimate(9, **{key: np.finfo(np.float64).max}).to_contract_dict()
    assert json.loads(json.dumps(payload, allow_nan=False))[POSE_NUMBER_FIELDS[key]] == float(np.finfo(np.float64).max)


def test_observed_and_catalog_matched_findings_keep_their_detection() -> None:
    assert finding(0, "observed", detection=True).to_contract_dict(source_asset_id="hand-built")["sourceDetection"]
    # A catalog match holds the catalog row it was matched to and both residuals (the contract's class table)
    matched = CelestialFindingRecord(
        id="c", observation_solution_id="sol-pose", evidence_class="catalog_matched", title="Matched", subtitle="Catalog",
        ra_deg=CATALOG_STAR.ra_deg, dec_deg=CATALOG_STAR.dec_deg, uncertainty_arcsec=12.0, source_detection=DETECTION,
        catalog_star=CATALOG_STAR, pixel_residual_px=0.4, angular_residual_arcsec=50.4,
        limitations=("Matched to a catalog star.",), source_sha256=RECORD_SHA,
    )
    assert matched.source_detection == DETECTION


def test_python_refuses_with_the_contracts_own_words() -> None:
    contract = ASTRONOMY_TS.read_text(encoding="utf-8")
    assert SOLUTION_RULE in contract
    assert CRPIX_RULE in contract
    assert f"A predicted or simulated {FINDING_RULE}." in contract
    assert BEACON_RULE in contract


POSE_SPEC = st.fixed_dictionaries(
    {
        "count": st.integers(min_value=0, max_value=30),
        "off_index": st.one_of(st.none(), st.integers(min_value=0, max_value=29)),
        "off_key": st.sampled_from(["crpix_x", "crpix_y"]),
        "off_by": st.floats(min_value=0.001, max_value=500.0),
        "off_sign": st.sampled_from([1.0, -1.0]),
        "field_fault": st.one_of(st.none(), st.sampled_from(sorted(POSE_FIELD_FAULTS))),
    }
)
OK_POSE = {"count": 9, "off_index": None, "off_key": "crpix_x", "off_by": 1.0, "off_sign": 1.0, "field_fault": None}


@settings(max_examples=300, deadline=None)
@given(
    status=st.sampled_from(STATUSES),
    wcs=st.booleans(),
    pose=st.one_of(st.none(), POSE_SPEC),
    # (evidence class, detection, a catalog row behind a predicted or simulated finding)
    findings=st.lists(
        st.tuples(st.sampled_from(["observed", "predicted", "simulated"]), st.booleans(), st.booleans()), max_size=4
    ),
)
# Every refusal and both acceptances, as the TypeScript property seeds them
@example(status="approximate", wcs=True, pose=OK_POSE, findings=[("observed", True, False), ("predicted", False, True), ("simulated", False, False)])
@example(status="approximate", wcs=True, pose=None, findings=[("predicted", False, False)])
@example(status="calibrated", wcs=True, pose=OK_POSE, findings=[])
@example(status="predicted_only", wcs=False, pose=OK_POSE, findings=[])
@example(status="approximate", wcs=False, pose=OK_POSE, findings=[])
@example(status="approximate", wcs=True, pose={**OK_POSE, "count": 1}, findings=[])
@example(status="approximate", wcs=True, pose={**OK_POSE, "count": 0}, findings=[])
@example(status="approximate", wcs=True, pose={**OK_POSE, "count": 28}, findings=[])
@example(status="approximate", wcs=True, pose={**OK_POSE, "off_index": 4, "off_key": "crpix_y"}, findings=[])
@example(status="approximate", wcs=True, pose={**OK_POSE, "field_fault": "fovZero"}, findings=[])
@example(status="approximate", wcs=True, pose={**OK_POSE, "field_fault": "sunHorizonWithTheMoon"}, findings=[])
@example(status="approximate", wcs=True, pose={**OK_POSE, "field_fault": "maxCenterOffsetDegInfinity"}, findings=[])
@example(status="approximate", wcs=True, pose=OK_POSE, findings=[("predicted", True, False)])
@example(status="unresolved", wcs=False, pose=None, findings=[("simulated", True, False)])
@example(status="approximate", wcs=True, pose=OK_POSE, findings=[("predicted", True, True)])
@example(status="unresolved", wcs=False, pose=None, findings=[("simulated", True, True)])
def test_pbt_pose_estimate_and_detections_follow_the_contract(
    status: str, wcs: bool, pose: dict[str, Any] | None, findings: list[tuple[str, bool, bool]]
) -> None:
    """P-SKY-7 on the Python mirror: the same oracle as the TypeScript property."""
    crpix_moved = pose is not None and pose["off_index"] is not None and pose["off_index"] < pose["count"]
    pose_ok = pose is None or (
        status == "approximate" and wcs and 2 <= pose["count"] <= 27 and not crpix_moved and pose["field_fault"] is None
    )
    detection_on = [cls != "observed" and det for cls, det, _ in findings]
    valid = pose_ok and not any(detection_on)

    def build() -> ObservationSolution:
        records = tuple(
            finding(i, cls, detection=cls == "observed" or det, catalog=cls != "observed" and catalog)
            for i, (cls, det, catalog) in enumerate(findings)
        )
        estimate = None
        if pose is not None:
            sweep = list(swept_wcs(pose["count"]))
            if crpix_moved:
                i, key = pose["off_index"], pose["off_key"]
                sweep[i] = replace(sweep[i], **{key: getattr(NOMINAL, key) + pose["off_sign"] * pose["off_by"]})
            fault = {} if pose["field_fault"] is None else POSE_FIELD_FAULTS[pose["field_fault"]]
            estimate = pose_estimate(pose["count"], **{"swept_wcs": tuple(sweep), **fault})
        return solution(status, wcs=wcs, pose=estimate, findings=records)

    if not valid:
        with pytest.raises(ValueError):
            build()
        return
    payload = build().to_contract_dict()
    assert ("poseEstimate" in payload) == (pose is not None)
    if pose is not None:
        assert len(payload["poseEstimate"]["sweptWcs"]) == pose["count"]
        assert all(
            (w["crpixX"], w["crpixY"]) == (payload["wcs"]["crpixX"], payload["wcs"]["crpixY"])
            for w in payload["poseEstimate"]["sweptWcs"]
        )
    assert all("sourceDetection" not in f for f in payload["findings"] if f["evidenceClass"] != "observed")
    assert _none_paths(payload) == []


def _wcs_from_contract(wcs: dict[str, Any]) -> WCSModel:
    return WCSModel(
        crval_ra_deg=wcs["crvalRaDeg"], crval_dec_deg=wcs["crvalDecDeg"], crpix_x=wcs["crpixX"], crpix_y=wcs["crpixY"],
        scale_deg_per_px=wcs["scaleDegPerPx"], rotation_deg=wcs["rotationDeg"], k1=wcs["radialDistortionK1"],
        k2=wcs["radialDistortionK2"], parity=wcs["parity"], width=WIDTH, height=HEIGHT,
    )


def test_fixture_pose_sample_lines_up_the_moon_off_centre_and_spreads_its_stars() -> None:
    """The stored sample's numbers hang together, and its sweep is one a consumer can test rings on: the observed
    Moon sits well away from crpix and the predicted Moon projects onto it under the nominal WCS and every swept one;
    the zenith lies straight up the frame under the nominal WCS, whose centre is the pose's altitude and azimuth;
    the swept WCS point at different places; maxCenterOffsetDeg is the largest angle between the nominal and a swept
    frame centre; and at least three catalog stars fall inside the frame under every WCS, each moving across the
    sweep."""
    sample = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))["pose_moon_level_head"]
    pose = sample["poseEstimate"]
    lat, lon, moment = sample["observerLocation"]["latDeg"], sample["observerLocation"]["lonDeg"], sample["observedAtUTC"]
    observed = next(f for f in sample["findings"] if f["evidenceClass"] == "observed")
    detection = observed["sourceDetection"]
    assert "synthetic fixture" in observed["title"] and "synthetic fixture" in observed["evidence"]["verificationMethod"]
    moon = next(f for f in sample["findings"] if f["evidenceClass"] == "predicted" and f["id"] == "finding-predicted-moon")
    nominal = _wcs_from_contract(sample["wcs"])
    swept = [_wcs_from_contract(w) for w in pose["sweptWcs"]]
    assert math.hypot(detection["xPx"] - nominal.crpix_x, detection["yPx"] - nominal.crpix_y) > 100.0
    for wcs in (nominal, *swept):
        x, y = wcs.world_to_pixel(moon["skyPosition"]["raDeg"], moon["skyPosition"]["decDeg"])
        assert math.hypot(x - detection["xPx"], y - detection["yPx"]) < 1e-4
    centre = nominal.pixel_to_world(nominal.crpix_x, nominal.crpix_y)
    centre_alt, centre_az = icrs_to_altaz(*centre, lat, lon, moment)
    assert math.isclose(centre_alt, pose["centerAltDeg"], abs_tol=1e-6)
    assert math.isclose(centre_az, pose["centerAzDeg"], abs_tol=1e-6)
    up_x, up_y = nominal.world_to_pixel(*altaz_to_icrs(pose["centerAltDeg"] + 1.0, pose["centerAzDeg"], lat, lon, moment))
    assert abs(up_x - nominal.crpix_x) < 1e-6 and up_y < nominal.crpix_y
    # Nine swept WCS, three fields of view by three rolls, all on the nominal crpix, each pointing elsewhere
    assert len(swept) == 9
    assert len({(round(w.crval_ra_deg, 6), round(w.crval_dec_deg, 6)) for w in swept}) == 9
    assert len({round(w.scale_deg_per_px, 12) for w in swept}) == 3
    assert len({round(w.rotation_deg, 9) for w in swept}) == 9
    offsets = [angular_separation_deg(*centre, *w.pixel_to_world(w.crpix_x, w.crpix_y)) for w in swept]
    assert max(offsets) > 0.1
    assert math.isclose(pose["maxCenterOffsetDeg"], max(offsets), rel_tol=1e-9)
    # The catalog stars inside the frame under every WCS, and how far each moves across the sweep
    spreads = []
    for star in (f for f in sample["findings"] if f["evidenceClass"] == "predicted" and "catalogRef" in f):
        assert star["catalogRef"]["objectType"] == "star" and "sourceDetection" not in star
        points = [w.world_to_pixel(star["skyPosition"]["raDeg"], star["skyPosition"]["decDeg"]) for w in (nominal, *swept)]
        if all(0.0 <= x < WIDTH and 0.0 <= y < HEIGHT for x, y in points):
            spreads.append(max(math.hypot(x - points[0][0], y - points[0][1]) for x, y in points[1:]))
    assert len(spreads) >= 3
    assert min(spreads) > 0.5
