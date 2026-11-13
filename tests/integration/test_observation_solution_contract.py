"""The observation solution family, judged alike by the Zod schemas and by the Python parser.

`ObservationSolutionSchema` and the schemas it is built from in packages/contracts/src/astronomy.ts, their parser
in services/contracts/astronomy.py, and the dataclasses in services/science/wcs_models.py that write solutions.
`tests/fixtures/observation_solution_contract.json` is written from `build_fixture()` here and read by
packages/contracts/src/__tests__/observation-solution-contract.test.ts, so a rule that changes on one side only
fails a test. Regenerate it after a deliberate change with:

    ALBEDIAN_UPDATE_SOLUTION_CONTRACT_FIXTURE=1 uv run pytest tests/integration/test_observation_solution_contract.py

Covers:
- The fixture is current, and its held-out gate numbers are the plate solver's own
- Every valid sample parses and comes back unchanged; so does every solution the Python solvers wrote into
  tests/fixtures/python_contract_solutions.json; a key the contract does not name is dropped
- At every level of every record of every valid sample, a key no schema names and the Python name of each field are
  never read: dropped beside the wire name, and no field in its place (generated from the field table; the fixture
  carries the counts the Zod side must match)
- Every named invalid sample is refused at the paths the fixture names, in the contract's own words
- The field table names every field of every schema in the family, and each field follows its rule at the edges:
  missing, null, empty, wrong type, not finite, below, at and above its bounds, wrong date forms
- P-CONTRACT-1: a sample the parser accepts comes back from `to_contract_dict` unchanged
- P-CONTRACT-2: one field of a valid sample moved outside its contract is refused, at that field
- P-CONTRACT-3: a verification marked as passed is accepted exactly at or above the held-out gate, by the parser
  and by the `SolutionQuality` dataclass alike
- P-CONTRACT-4: a solution the dataclasses let be built is one the parser accepts, and one field outside the contract
  is refused when the dataclass is built
- A solution is always written with its capture time in the contract's form: a time ending in Z as given, one with an
  offset as the same instant in UTC (P-CONTRACT-5), one that names no instant refused where it is written
- The science routes refuse at the door, with a plain 422 that holds nothing of the request, a time they cannot read
  and a moment outside the ephemeris' span, and the two that write a solution a time that names no instant
  (services/science/router.py)
- The two solve routes refuse at the door every value a client sends that a solution record would refuse (a place,
  a hint, a hash, each field of a detection), one or two at a time, with a plain 422 and never a 500
  (P-CONTRACT-6); a fault inside the solve, a refused record of the solver's own among them, still answers 500;
  a frame in which the detector measures a detection no record takes is refused at the door too
- The two solve routes answer a request that fails validation themselves, in a sentence for each field at fault
  that says what the field must be and holds nothing sent: every field of the request model, each bound at its
  edges, a size no frame has, a source asset id not of the minted form, a value JSON cannot carry back, a body that
  is no JSON object, a frame no solver can read. More than a thousand hostile requests are each answered with a
  solution the contract takes or refused in a sentence, but for the ten requests the plate solver itself fails on,
  which are named one by one
- What a solver builds from its own numbers is what the contract takes: a bright spot with no width is no beacon to
  the horizon solver (services/science/horizon_solver.py) nor to the head-worn pose, through `complete_sky` and
  through the upload job, where a spot the Moon test passes always builds its detection (P-CONTRACT-8,
  services/science/pose_estimate.py); and the phone solver's gate judges the median its quality record reports, so a
  solve that passes its gate always builds its record (P-CONTRACT-7, services/science/phone_photo_solver.py)
- The ingest contract's calendar days and finite numbers (services/contracts/ingest.py)
- Real data: every pinned star and a spread of the phone solver's tile stars give a catalog reference the contract
  accepts, the ephemeris writes findings it accepts, and a solution the solve job stores parses as it was stored
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import os
import re
from collections.abc import Callable, Iterator
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from hypothesis import event, example, given, settings
from hypothesis import strategies as st
from PIL import Image
from pydantic import ValidationError

from research.benchmarks.phone_photos import render, stars, suite
from services.api import app
from services.contracts import astronomy as contract
from services.contracts import ingest
from services.contracts.catalog_reference import CatalogReference
from services.ingest import jobs as ingest_jobs
from services.ingest import router as ingest_router
from services.ingest.jobs import read_solution
from services.ingest.store import IngestStore
from services.science import horizon_solver, phone_photo_solver, pose_estimate, solver
from services.science import router as science_router
from services.science.camera_profiles import META_RAY_BAN_DISPLAY_MODEL, camera_profile
from services.science.catalog import ALL_SKY_CATALOG_STARS, get_catalog_stars, get_star_by_name
from services.science.deep_sky_findings import (
    LIMITATIONS as DEEP_SKY_LIMITATIONS,
    OPENNGC_LICENCE_NOTE,
    POSITION_PRECISION_ARCSEC,
    VERIFICATION_METHOD as DEEP_SKY_METHOD,
    catalog_reference,
    finding_id,
    openngc_table,
)
from services.science.ephemeris import compute_solar_system_ephemerides
from services.science.predicted_sky import complete_sky
from services.science.solver import render_synthetic_starfield
from services.science.wcs_models import (
    CelestialFindingRecord,
    ContractRefusal,
    ObservationSolution,
    PoseEstimate,
    SolutionQuality,
    SolverIdentity,
    SourceDetection,
    WCSModel,
    contract_utc_instant,
    require_source_sha256,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "observation_solution_contract.json"
PYTHON_SOLUTIONS = ROOT / "tests" / "fixtures" / "python_contract_solutions.json"
ASTRONOMY_TS = ROOT / "packages" / "contracts" / "src" / "astronomy.ts"
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_SOLUTION_CONTRACT_FIXTURE"

# A number JSON cannot carry is written as {"$number": name} and read back by both sides
NON_FINITE = {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf}
# Synthetic values for contract samples only: the moment and open-ocean place the other contract tests use, and a
# frame and camera that describe no real one
MOMENT = "2030-01-01T02:00:00Z"
# Two times whose UTC instants fall outside the years a date type holds, each written in two parts: the trip guard
# (tests/integration/test_no_owner_trip_data.py) reads every time on a tracked line and raises on one of these
AFTER_YEAR_9999 = "9999-12-31T23:30:00" + "-02:00"
BEFORE_YEAR_1 = "0001-01-01T00:00:00" + "+14:00"
PLACE = {"latDeg": 30.0, "lonDeg": -40.0}
SHA = hashlib.sha256(b"hand-built contract sample").hexdigest()
ASSET = "src-contract-sample"
SOLVER = {"name": "hand-built contract sample", "version": "0", "catalogRelease": "none"}
HAND_BUILT = "A hand-built contract sample, not a measurement."
WIDTH, HEIGHT = 960, 540
WCS = {
    "projection": "TAN", "crvalRaDeg": 83.8, "crvalDecDeg": 4.0, "crpixX": 480.0, "crpixY": 270.0,
    "scaleDegPerPx": 0.035, "rotationDeg": 1.5, "radialDistortionK1": -0.008, "radialDistortionK2": 0.001,
    "parity": -1, "fovHorizontalDeg": 33.6, "fovVerticalDeg": 18.9,
}
# The same frame at 0.9, 1 and 1.1 times the nominal scale: a larger scale, a larger field of view
SWEPT = [
    {**WCS, "scaleDegPerPx": 0.0315, "rotationDeg": -1.5, "fovHorizontalDeg": 30.24, "fovVerticalDeg": 17.01},
    {**WCS, "rotationDeg": 4.5},
    {**WCS, "scaleDegPerPx": 0.0385, "rotationDeg": 1.5, "fovHorizontalDeg": 36.96, "fovVerticalDeg": 20.79},
]
# A pose fit's residual as the horizon solver writes it on the observed Moon (services/science/horizon_solver.py:224):
# pixels, and arcseconds at the sample WCS's 0.035 deg/px
OBSERVED_FIT_RESIDUAL = {"pixelResidualPx": 1.5, "angularResidualArcsec": 1.5 * 0.035 * 3600.0}
UNVERIFIED = {
    "detectedCandidateCount": 1, "matchedInlierCount": 0, "heldOutPairCount": 0, "heldOutInliers": 0,
    "passedIndependentVerification": False,
}
# The contract's own sentences (packages/contracts/src/astronomy.ts), which the parser repeats word for word
SAYS = {
    "observed_needs_pixels": "An observed finding is a measurement in the pixels and carries the source detection that measured it.",
    "no_detection": "A predicted or simulated finding was not measured in the pixels, so it carries no source detection.",
    "names_its_solution": "Observed and catalog-matched findings belong to an ObservationSolution and name its id.",
    "names_its_recording": "Observed and catalog-matched evidence rests on a recording and names its SHA-256.",
    "beacon": (
        "A pose estimate's beacon is the one its method lines up from: the moon for moon_camera_profile_level_head and "
        "moon_horizon, the sun for sun_horizon."
    ),
    "pose_belongs": "A pose estimate belongs only to an approximate solution with a WCS: it states how far that WCS may be off.",
    "swept_crpix": "Every swept WCS of a pose estimate has the nominal WCS's crpixX and crpixY: all of them describe the same frame.",
    "swept_size": (
        "Every swept WCS of a pose estimate has the nominal WCS's image size: at its scale the same fields of view, "
        "at a larger scale none smaller, at a smaller scale none larger."
    ),
    "pending_quality": "A pending solution has no quality: no solver has run, so nothing was counted.",
    "pending_solver": "A pending solution names no solver: none has run.",
    "needs_solver": "Only a pending solution, or an unresolved one no solver reached, names no solver.",
    "needs_quality": "A calibrated or approximate solution reports the quality it was judged on.",
    "residual_reported": "Held-out stars were paired, so their residual is reported.",
    "no_residual": "No held-out star was paired, so there is no residual to report.",
    "inliers_are_pairs": "Held-out inliers are held-out pairs inside the inlier gate: there cannot be more of them than pairs.",
    "passed_needs_wcs": "Held-out verification passes only with a WCS and the held-out residuals it was judged on.",
    "names_no_recording": "Only a pending solution with no findings names no recording.",
    "needs_time": "An approximate or predicted_only solution, or any solution with a predicted finding, needs observedAtUTC.",
    "needs_place": "An approximate or predicted_only solution, or any solution with a predicted finding, needs observerLocation.",
    "finding_in_solution": "A finding inside an ObservationSolution must name its ObservationSolution id.",
    "match_needs_verified": "A catalog_matched finding needs a verified WCS: catalog association follows held-out verification.",
    "observed_needs_sky": "An observed finding in a solution with a verified WCS needs the sky position that WCS gives it.",
    "observed_no_sky": "An observed finding has no sky position without a verified WCS.",
    "zeros": "A SHA-256 of 64 zeros is a placeholder, not a hash.",
}


def gate_sentence() -> str:
    least, most = contract.held_out_gate()
    return (
        f"Held-out verification passes only with at least {least} held-out inliers and a median held-out residual "
        f"of at most {most:g} px."
    )


def not_calibrated_sentence(status: str) -> str:
    return f"Only a calibrated solution passes held-out verification, not a {status} one."


def decode(value: Any) -> Any:
    """A fixture value as the parser is given it: {"$number": name} becomes the number JSON cannot carry."""
    if isinstance(value, dict):
        if set(value) == {"$number"}:
            return NON_FINITE[value["$number"]]
        return {key: decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode(item) for item in value]
    return value


def at_path(value: Any, path: list[Any] | tuple[Any, ...]) -> Any:
    """The value at `path`, or None where a key on the way is left out."""
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def at(value: Any, path: list[Any] | tuple[Any, ...]) -> Any:
    for key in path:
        value = value[key]
    return value


def patched(sample: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    """`sample` after each step: {"op": "set", "path", "value"} or {"op": "remove", "path"}."""
    out = copy.deepcopy(sample)
    for step in steps:
        target = at(out, step["path"][:-1])
        if step["op"] == "remove":
            del target[step["path"][-1]]
        else:
            target[step["path"][-1]] = decode(copy.deepcopy(step["value"]))
    return out


def put(path: list[Any], value: Any) -> dict[str, Any]:
    return {"op": "set", "path": path, "value": value}


def drop(path: list[Any]) -> dict[str, Any]:
    return {"op": "remove", "path": path}


def dotted(path: list[Any] | tuple[Any, ...]) -> str:
    return ".".join(str(key) for key in path)


def instances(fields: dict[str, Any], schema: str, value: dict[str, Any], path: tuple[Any, ...] = ()) -> Iterator[tuple[str, tuple[Any, ...]]]:
    """Every object of the family inside `value`, as (the schema that judges it, where it is)."""
    yield schema, path
    for name, entry in fields[schema].items():
        if "schema" not in entry or name not in value:
            continue
        if entry.get("array"):
            for index, item in enumerate(value[name]):
                yield from instances(fields, entry["schema"], item, (*path, name, index))
        else:
            yield from instances(fields, entry["schema"], value[name], (*path, name))


# =============================================================================================
# The fixture both sides read
# =============================================================================================


def _evidence(of: str, evidence_class: str, **more: Any) -> dict[str, Any]:
    return {
        "id": f"ev-{of}", "evidenceClass": evidence_class, "verificationMethod": "Hand-built for the contract fixture",
        "sourceAssetSha256": SHA, "limitations": [HAND_BUILT], "verifiedByIndependentHoldout": False, **more,
    }


def _pixels_only(solution_id: str) -> dict[str, Any]:
    """An observed point of light located by its pixels alone, as a solver lists one it could not place."""
    return {
        "id": "finding-obs-1", "observationSolutionId": solution_id, "evidenceClass": "observed",
        "title": "Observed Point Source #1 (hand-built contract sample)", "subtitle": "At (412.5, 300.2) px",
        "sourceDetection": {
            "assetId": ASSET, "frameTimeSec": 0.0, "xPx": 412.5, "yPx": 300.25, "flux": 5000.0, "snr": 25.0,
            "fwhmPx": 2.5, "centroidUncertaintyPx": 0.1,
        },
        "evidence": _evidence("finding-obs-1", "observed"),
    }


def _solution(solution_id: str, status: str, **more: Any) -> dict[str, Any]:
    return {
        "id": solution_id, "sourceAssetId": ASSET, "sourceSha256": SHA, "frameTimeSec": 0.0, "revision": 1,
        "status": status, **more,
    }


def _samples() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """The valid samples, and the pieces the invalid ones are cut from."""
    least, most = contract.held_out_gate()
    stored = json.loads(PYTHON_SOLUTIONS.read_text(encoding="utf-8"))
    # What the plate solver wrote for a rendered starfield: a verified WCS, three catalog matches and one detection
    # it placed on the sky. Two findings are added: a deep-sky object as services/science/deep_sky_findings.py
    # builds one, from its real OpenNGC row, and a simulated point at a real catalog position
    calibrated = copy.deepcopy(stored["plate_calibrated"])
    table = openngc_table()
    column = {name: index for index, name in enumerate(table["columns"])}
    (orion_nebula_row,) = [row for row in table["rows"] if row[column["name"]] == "NGC1976"]
    reference = catalog_reference(orion_nebula_row)
    deep_sky = CelestialFindingRecord(
        id=finding_id("NGC1976"), observation_solution_id=calibrated["id"], evidence_class="predicted",
        title=reference["commonName"],
        subtitle=f"{table['types'][orion_nebula_row[column['type']]]} · {orion_nebula_row[column['constellation']]} · {OPENNGC_LICENCE_NOTE}",
        ra_deg=reference["raDeg"],
        dec_deg=reference["decDeg"], uncertainty_arcsec=POSITION_PRECISION_ARCSEC, deep_sky_reference=reference,
        verification_method=DEEP_SKY_METHOD, limitations=DEEP_SKY_LIMITATIONS, source_sha256=calibrated["sourceSha256"],
    ).to_contract_dict()
    rigel = next(f for f in calibrated["findings"] if f["id"] == "finding-star-rigel")
    simulated = {
        "id": "finding-simulated-1", "observationSolutionId": calibrated["id"], "evidenceClass": "simulated",
        "title": "Simulated point (hand-built contract sample)", "subtitle": "Drawn at Rigel's catalog position",
        "skyPosition": {"raDeg": rigel["catalogRef"]["raDeg"], "decDeg": rigel["catalogRef"]["decDeg"], "uncertaintyArcsec": 0.0},
        "evidence": {
            "id": "ev-finding-simulated-1", "evidenceClass": "simulated",
            "verificationMethod": "Simulated; not measured in the image",
            "limitations": [HAND_BUILT], "verifiedByIndependentHoldout": False,
        },
    }
    calibrated["findings"] = [*calibrated["findings"], deep_sky, simulated]
    predicted_star = {
        "id": "finding-predicted-star-rigel", "observationSolutionId": "sol-contract-predicted", "evidenceClass": "predicted",
        "title": "Rigel", "subtitle": "Predicted star (hand-built contract sample)",
        "catalogRef": rigel["catalogRef"], "skyPosition": rigel["skyPosition"],
        "inferredDistancePc": rigel["inferredDistancePc"], "cartesianParsecICRS": rigel["cartesianParsecICRS"],
        "evidence": _evidence("finding-predicted-star-rigel", "predicted", catalogReferenceId=rigel["catalogRef"]["id"]),
    }
    pose = {
        "method": "moon_camera_profile_level_head", "beacon": "moon", "centerAltDeg": 33.0, "centerAzDeg": 224.0,
        "rollDeg": 0.0, "fovDiagonalDeg": 38.2,
        "fovSource": "A synthetic camera profile for contract tests; it describes no real camera.",
        "assumptions": ["The head is level."], "sweptWcs": SWEPT, "maxCenterOffsetDeg": 0.4,
        "beaconCentroidUncertaintyDeg": 0.004,
    }
    moment = {"observedAtUTC": MOMENT, "observerLocation": PLACE}
    valid = {
        "calibrated": calibrated,
        # Exactly at the held-out gate: the fewest inliers and the largest median a passed verification may have
        "calibrated_at_the_gate": _solution(
            "sol-contract-gate", "calibrated", **moment, solver=SOLVER, wcs=WCS,
            quality={
                "detectedCandidateCount": 12, "matchedInlierCount": least, "heldOutPairCount": least,
                "heldOutInliers": least, "medianResidualPx": most, "p95ResidualPx": most,
                "medianResidualArcsec": round(most * WCS["scaleDegPerPx"] * 3600.0, 6),
                "passedIndependentVerification": True,
            },
            findings=[], warnings=[],
        ),
        "unresolved_pixels_only": _solution(
            "sol-contract-unresolved", "unresolved", solver=SOLVER,
            quality={
                "detectedCandidateCount": 8, "matchedInlierCount": 0, "heldOutPairCount": 5, "heldOutInliers": 0,
                "medianResidualPx": 4.2, "p95ResidualPx": 6.8, "medianResidualArcsec": 520.0,
                "passedIndependentVerification": False,
            },
            findings=[_pixels_only("sol-contract-unresolved")],
            warnings=["WCS solution did not pass independent held-out calibration gate."],
        ),
        "unresolved_before_any_solver": _solution(
            "sol-contract-undecoded", "unresolved", findings=[], warnings=["The file could not be decoded."]
        ),
        "pending": _solution("sol-contract-pending", "pending", findings=[], warnings=[]),
        "pending_without_a_recording": {
            key: value for key, value in _solution("sol-contract-new", "pending", findings=[], warnings=[]).items()
            if key != "sourceSha256"
        },
        "ambiguous": _solution(
            "sol-contract-ambiguous", "ambiguous", solver=SOLVER,
            quality={
                "detectedCandidateCount": 18, "matchedInlierCount": 0, "heldOutPairCount": 14, "heldOutInliers": least,
                "medianResidualPx": 5.0, "p95ResidualPx": 5.0, "medianResidualArcsec": 630.0,
                "passedIndependentVerification": False,
            },
            findings=[_pixels_only("sol-contract-ambiguous")], warnings=[],
        ),
        "predicted_only": _solution(
            "sol-contract-predicted", "predicted_only", **moment, solver=SOLVER, quality=UNVERIFIED,
            findings=[predicted_star], warnings=[],
        ),
        "approximate": _solution(
            "sol-contract-approximate", "approximate", **moment, solver=SOLVER, wcs=WCS, quality=UNVERIFIED,
            findings=[_pixels_only("sol-contract-approximate")], warnings=[],
        ),
        "approximate_with_pose": _solution(
            "sol-contract-pose", "approximate", **moment, solver=SOLVER, wcs=WCS, quality=UNVERIFIED,
            findings=[_pixels_only("sol-contract-pose")], warnings=[], poseEstimate=pose,
        ),
    }
    # The swept fields of view at the tolerance's exact edges. A difference of two floats equals the tolerance
    # exactly only at fields of view of a few times the tolerance (near 20 degrees the floats lie about 3.6e-15 apart,
    # and the tolerance is no multiple of that), so these samples hold such fields of view; the rule reads nothing
    # else of them. Each sweep is still one image at 0.9, 1 and 1.1 times the nominal scale
    tol = contract.SWEPT_FOV_TOLERANCE_DEG

    def at_fields_of_view(nominal: float, smaller: tuple[float, float], same: tuple[float, float], larger: tuple[float, float]) -> dict[str, Any]:
        def sized(base: dict[str, Any], fields_of_view: tuple[float, float]) -> dict[str, Any]:
            return {**base, "fovHorizontalDeg": fields_of_view[0], "fovVerticalDeg": fields_of_view[1]}

        sweep = [sized(SWEPT[0], smaller), sized(SWEPT[1], same), sized(SWEPT[2], larger)]
        return {**copy.deepcopy(valid["approximate_with_pose"]), "wcs": sized(WCS, (nominal, nominal)), "poseEstimate": {**pose, "sweptWcs": sweep}}

    # The larger scale narrower by exactly the tolerance, and the nominal scale narrower by it vertically
    valid["approximate_with_pose_narrower_by_the_tolerance"] = at_fields_of_view(2 * tol, (2 * tol, 2 * tol), (2 * tol, tol), (tol, tol))
    # The smaller scale wider by exactly the tolerance, and the nominal scale wider by it horizontally
    valid["approximate_with_pose_wider_by_the_tolerance"] = at_fields_of_view(tol, (2 * tol, 2 * tol), (2 * tol, tol), (tol, tol))
    # In an approximate solution, the status a pose estimate has, an observed finding may hold the pose fit's residual
    # (the lead's ruling of 2026-11-12 on the observed Moon; the horizon solver writes its beacon so)
    with_fit_residual = copy.deepcopy(valid["approximate"])
    with_fit_residual["findings"][0]["evidence"].update(OBSERVED_FIT_RESIDUAL)
    valid["approximate_with_an_observed_fit_residual"] = with_fit_residual
    return valid, {"rigel": rigel, "simulated": simulated, "predicted_star": predicted_star, "pose": pose}


def _invalid(valid: dict[str, dict[str, Any]], parts: dict[str, Any]) -> list[dict[str, Any]]:
    """One named fault each: a valid sample, the steps that break it, and where and in which words it is refused."""
    least, most = contract.held_out_gate()
    cases: list[dict[str, Any]] = []

    def bad(name: str, base: str, reason: str, paths: list[str], *steps: dict[str, Any], says: tuple[str, ...] = ()) -> None:
        cases.append({"name": name, "base": base, "patch": list(steps), "reason": reason, "paths": sorted(paths), "messages": list(says)})

    passed = "quality.passedIndependentVerification"
    gate, unresolved, pending_new = "calibrated_at_the_gate", "unresolved_pixels_only", "pending_without_a_recording"
    found = {f["id"]: i for i, f in enumerate(valid["calibrated"]["findings"])}
    rigel_at, simulated_at = found["finding-star-rigel"], found["finding-simulated-1"]
    placed_at = next(i for i, f in enumerate(valid["calibrated"]["findings"]) if f["evidenceClass"] == "observed")

    # The held-out gate behind a passed verification
    bad("gate_one_inlier_short", gate, "one held-out inlier fewer than the gate", [passed],
        put(["quality", "heldOutInliers"], least - 1), says=(gate_sentence(),))
    bad("gate_median_just_above", gate, "the next number above the gate's median", [passed],
        put(["quality", "medianResidualPx"], math.nextafter(most, math.inf)), says=(gate_sentence(),))
    bad("gate_no_inlier", gate, "passed with no held-out inlier", [passed],
        put(["quality", "heldOutInliers"], 0), says=(gate_sentence(),))
    bad("passed_without_a_wcs", gate, "a passed verification with no WCS", [passed],
        drop(["wcs"]), says=(SAYS["passed_needs_wcs"],))
    bad("passed_without_residuals", gate, "a passed verification that paired no held-out star", [passed],
        put(["quality", "heldOutPairCount"], 0), put(["quality", "heldOutInliers"], 0),
        drop(["quality", "medianResidualPx"]), drop(["quality", "p95ResidualPx"]), drop(["quality", "medianResidualArcsec"]),
        says=(SAYS["passed_needs_wcs"], gate_sentence()))
    for status in ("approximate", "ambiguous", "unresolved", "predicted_only"):
        bad(f"passed_on_{status}", gate, f"a passed verification on a solution whose status is {status}", [passed],
            put(["status"], status), says=(not_calibrated_sentence(status),))
    bad("passed_on_pending", gate, "a pending solution with a solver and a passed quality", ["quality", "solver", passed],
        put(["status"], "pending"), says=(SAYS["pending_quality"], SAYS["pending_solver"], not_calibrated_sentence("pending")))
    # Quality and solver follow the status
    bad("calibrated_without_quality", gate, "a calibrated solution with no quality", ["quality"],
        drop(["quality"]), says=(SAYS["needs_quality"],))
    bad("calibrated_without_solver", gate, "a calibrated solution that names no solver", ["solver"],
        drop(["solver"]), says=(SAYS["needs_solver"],))
    bad("approximate_without_quality", "approximate", "an approximate solution with no quality", ["quality"],
        drop(["quality"]), says=(SAYS["needs_quality"],))
    for status in ("approximate", "ambiguous", "predicted_only"):
        bad(f"{status}_without_solver", status, f"a solution whose status is {status} and that names no solver", ["solver"],
            drop(["solver"]), says=(SAYS["needs_solver"],))
    bad("pending_with_quality", "pending", "a pending solution that counted something", ["quality"],
        put(["quality"], UNVERIFIED), says=(SAYS["pending_quality"],))
    bad("pending_with_solver", "pending", "a pending solution that names a solver", ["solver"],
        put(["solver"], SOLVER), says=(SAYS["pending_solver"],))
    # Held-out pairs and their residuals
    bad("more_inliers_than_pairs", gate, "more held-out inliers than held-out pairs", ["quality.heldOutInliers"],
        put(["quality", "heldOutInliers"], least + 1), says=(SAYS["inliers_are_pairs"],))
    bad("residuals_without_a_pair", unresolved, "residuals with no held-out pair",
        ["quality.medianResidualArcsec", "quality.medianResidualPx", "quality.p95ResidualPx"],
        put(["quality", "heldOutPairCount"], 0), says=(SAYS["no_residual"],))
    bad("a_pair_without_its_residual", unresolved, "held-out pairs with one residual left out", ["quality.p95ResidualPx"],
        drop(["quality", "p95ResidualPx"]), says=(SAYS["residual_reported"],))
    bad("residual_not_a_number", unresolved, "a residual that is not a number", ["quality.medianResidualPx"],
        put(["quality", "medianResidualPx"], {"$number": "NaN"}))
    bad("residual_infinite", unresolved, "an infinite residual", ["quality.p95ResidualPx"],
        put(["quality", "p95ResidualPx"], {"$number": "Infinity"}))
    # Status, recording, time and place
    bad("status_unknown", gate, "a status the contract does not name", ["status"], put(["status"], "Calibrated"))
    bad("status_missing", gate, "no status", ["status"], drop(["status"]))
    bad("recording_hash_of_zeros", gate, "64 zeros in place of a hash", ["sourceSha256"],
        put(["sourceSha256"], "0" * 64), says=(SAYS["zeros"],))
    bad("recording_hash_in_capitals", gate, "a hash in capitals", ["sourceSha256"], put(["sourceSha256"], SHA.upper()))
    bad("solved_without_a_recording", unresolved, "an unresolved solution that names no recording", ["sourceSha256"],
        drop(["sourceSha256"]), says=(SAYS["names_no_recording"],))
    bad("pending_with_a_finding_and_no_recording", pending_new, "a pending solution with a finding and no recording",
        ["sourceSha256"], put(["findings"], [{**parts["simulated"], "observationSolutionId": "sol-contract-new"}]),
        says=(SAYS["names_no_recording"],))
    bad("approximate_without_a_time", "approximate", "an approximate solution with no capture time", ["observedAtUTC"],
        drop(["observedAtUTC"]), says=(SAYS["needs_time"],))
    bad("approximate_without_a_place", "approximate", "an approximate solution with no place", ["observerLocation"],
        drop(["observerLocation"]), says=(SAYS["needs_place"],))
    bad("predicted_only_without_time_or_place", "predicted_only", "a predicted-only solution with no time and no place",
        ["observedAtUTC", "observerLocation"], drop(["observedAtUTC"]), drop(["observerLocation"]),
        says=(SAYS["needs_time"], SAYS["needs_place"]))
    bad("predicted_only_with_no_finding_and_no_time_or_place", "predicted_only",
        "a predicted-only solution with no finding, no time and no place: the status alone needs both",
        ["observedAtUTC", "observerLocation"], put(["findings"], []), drop(["observedAtUTC"]), drop(["observerLocation"]),
        says=(SAYS["needs_time"], SAYS["needs_place"]))
    bad("predicted_finding_without_time_or_place", unresolved, "a predicted finding in a solution with no time and no place",
        ["observedAtUTC", "observerLocation"],
        put(["findings"], [valid[unresolved]["findings"][0], {**parts["predicted_star"], "observationSolutionId": "sol-contract-unresolved"}]),
        says=(SAYS["needs_time"], SAYS["needs_place"]))
    bad("time_with_an_offset", "approximate", "a capture time written with +00:00, not Z", ["observedAtUTC"],
        put(["observedAtUTC"], "2030-01-01T02:00:00+00:00"))
    bad("time_sent_as_null", "approximate", "a null capture time", ["observedAtUTC"], put(["observedAtUTC"], None))
    bad("wcs_sent_as_null", "calibrated", "a null WCS", ["wcs"], put(["wcs"], None))
    bad("findings_missing", gate, "no findings list", ["findings"], drop(["findings"]))
    bad("warnings_missing", gate, "no warnings list", ["warnings"], drop(["warnings"]))
    bad("warning_not_text", gate, "a number among the warnings", ["warnings.0"], put(["warnings"], [5]))
    bad("wcs_centre_pixel_infinite", "approximate", "an infinite crpixX", ["wcs.crpixX"], put(["wcs", "crpixX"], {"$number": "Infinity"}))
    bad("wcs_scale_zero", "approximate", "a pixel scale of zero", ["wcs.scaleDegPerPx"], put(["wcs", "scaleDegPerPx"], 0))
    # Findings
    bad("predicted_finding_names_no_solution", "predicted_only", "a finding inside a solution that names no solution",
        ["findings.0.observationSolutionId"], drop(["findings", 0, "observationSolutionId"]), says=(SAYS["finding_in_solution"],))
    bad("observed_finding_names_no_solution", unresolved, "an observed finding that names no solution",
        ["findings.0.observationSolutionId"], drop(["findings", 0, "observationSolutionId"]), says=(SAYS["names_its_solution"],))
    bad("catalog_match_without_a_wcs", unresolved, "a catalog match in a solution with no WCS", ["findings.0.evidenceClass"],
        put(["findings", 0], parts["rigel"]), says=(SAYS["match_needs_verified"],))
    bad("catalog_match_on_an_unverified_wcs", "approximate", "a catalog match under a WCS that did not pass verification",
        ["findings.0.evidenceClass"], put(["findings", 0], parts["rigel"]), says=(SAYS["match_needs_verified"],))
    bad("observed_on_the_sky_without_a_verified_wcs", unresolved, "a detection given a sky position with no verified WCS",
        ["findings.0.skyPosition"], put(["findings", 0, "skyPosition"], {"raDeg": 83.8, "decDeg": 4.0, "uncertaintyArcsec": 12.0}),
        says=(SAYS["observed_no_sky"],))
    bad("observed_off_the_sky_under_a_verified_wcs", "calibrated", "a detection left off the sky under a verified WCS",
        [f"findings.{placed_at}.skyPosition"], drop(["findings", placed_at, "skyPosition"]), says=(SAYS["observed_needs_sky"],))
    bad("catalog_match_without_a_sky_position", "calibrated", "a catalog match with no sky position",
        [f"findings.{rigel_at}.skyPosition"], drop(["findings", rigel_at, "skyPosition"]))
    bad("predicted_finding_with_a_detection", "predicted_only", "a predicted finding that carries a pixel detection",
        ["findings.0.sourceDetection"], put(["findings", 0, "sourceDetection"], valid[unresolved]["findings"][0]["sourceDetection"]),
        says=(SAYS["no_detection"],))
    bad("simulated_finding_with_a_detection", "calibrated", "a simulated finding that carries a pixel detection",
        [f"findings.{simulated_at}.sourceDetection"],
        put(["findings", simulated_at, "sourceDetection"], valid[unresolved]["findings"][0]["sourceDetection"]),
        says=(SAYS["no_detection"],))
    bad("observed_finding_located_nowhere", unresolved, "an observed finding with no pixels and no sky position",
        ["findings.0.sourceDetection"], drop(["findings", 0, "sourceDetection"]), says=(SAYS["observed_needs_pixels"],))
    bad("observed_evidence_names_no_recording", unresolved, "observed evidence that names no recording",
        ["findings.0.evidence.sourceAssetSha256"], drop(["findings", 0, "evidence", "sourceAssetSha256"]),
        says=(SAYS["names_its_recording"],))
    bad("evidence_class_unknown", unresolved, "an evidence class the contract does not name", ["findings.0.evidenceClass"],
        put(["findings", 0, "evidenceClass"], "measured"))
    bad("no_limitation_stated", unresolved, "evidence with no limitation", ["findings.0.evidence.limitations"],
        put(["findings", 0, "evidence", "limitations"], []))
    bad("detection_left_of_the_frame", unresolved, "a detection at a negative x", ["findings.0.sourceDetection.xPx"],
        put(["findings", 0, "sourceDetection", "xPx"], -0.5))
    bad("catalog_magnitude_infinite", "calibrated", "an infinite catalog magnitude",
        [f"findings.{rigel_at}.catalogRef.apparentMagnitude"],
        put(["findings", rigel_at, "catalogRef", "apparentMagnitude"], {"$number": "Infinity"}))
    for axis, name in enumerate(("x", "y", "z")):
        bad(f"position_{name}_infinite", "calibrated", f"an infinite {name} in a finding's position in space",
            [f"findings.{rigel_at}.cartesianParsecICRS.{axis}"],
            put(["findings", rigel_at, "cartesianParsecICRS", axis], {"$number": "-Infinity"}))
    # The pose estimate
    bad("pose_on_a_calibrated_solution", gate, "a pose estimate on a calibrated solution", ["poseEstimate"],
        put(["poseEstimate"], parts["pose"]), says=(SAYS["pose_belongs"],))
    bad("pose_on_a_predicted_only_solution", "approximate_with_pose", "a pose estimate on a predicted-only solution",
        ["poseEstimate"], put(["status"], "predicted_only"), says=(SAYS["pose_belongs"],))
    bad("pose_without_a_wcs", "approximate_with_pose", "a pose estimate with no nominal WCS", ["poseEstimate"],
        drop(["wcs"]), says=(SAYS["pose_belongs"],))
    bad("swept_wcs_on_another_centre_pixel", "approximate_with_pose", "a swept WCS on another crpixX",
        ["poseEstimate.sweptWcs.1"], put(["poseEstimate", "sweptWcs", 1, "crpixX"], 480.5), says=(SAYS["swept_crpix"],))
    bad("swept_wcs_same_scale_other_field_of_view", "approximate_with_pose",
        "a swept WCS with the nominal scale and another horizontal field of view", ["poseEstimate.sweptWcs.1"],
        put(["poseEstimate", "sweptWcs", 1, "fovHorizontalDeg"], 40.0), says=(SAYS["swept_size"],))
    bad("swept_wcs_larger_scale_smaller_field", "approximate_with_pose",
        "a swept WCS with a larger scale and a smaller vertical field of view", ["poseEstimate.sweptWcs.2"],
        put(["poseEstimate", "sweptWcs", 2, "fovVerticalDeg"], 18.0), says=(SAYS["swept_size"],))
    bad("swept_wcs_smaller_scale_larger_field", "approximate_with_pose",
        "a swept WCS with a smaller scale and a larger horizontal field of view", ["poseEstimate.sweptWcs.0"],
        put(["poseEstimate", "sweptWcs", 0, "fovHorizontalDeg"], 34.0), says=(SAYS["swept_size"],))
    bad("swept_wcs_same_scale_narrower_field", "approximate_with_pose",
        "a swept WCS with the nominal scale and a narrower horizontal field of view", ["poseEstimate.sweptWcs.1"],
        put(["poseEstimate", "sweptWcs", 1, "fovHorizontalDeg"], 30.0), says=(SAYS["swept_size"],))
    # One float past each edge of the tolerance that the two samples at its edges hold
    tol = contract.SWEPT_FOV_TOLERANCE_DEG
    bad("swept_wcs_larger_scale_narrower_just_past_the_tolerance", "approximate_with_pose_narrower_by_the_tolerance",
        "a swept WCS with a larger scale, narrower by one float more than the tolerance", ["poseEstimate.sweptWcs.2"],
        put(["poseEstimate", "sweptWcs", 2, "fovVerticalDeg"], math.nextafter(tol, 0.0)), says=(SAYS["swept_size"],))
    bad("swept_wcs_same_scale_narrower_just_past_the_tolerance", "approximate_with_pose_narrower_by_the_tolerance",
        "a swept WCS with the nominal scale, narrower by one float more than the tolerance", ["poseEstimate.sweptWcs.1"],
        put(["poseEstimate", "sweptWcs", 1, "fovVerticalDeg"], math.nextafter(tol, 0.0)), says=(SAYS["swept_size"],))
    bad("swept_wcs_smaller_scale_wider_just_past_the_tolerance", "approximate_with_pose_wider_by_the_tolerance",
        "a swept WCS with a smaller scale, wider by one float more than the tolerance", ["poseEstimate.sweptWcs.0"],
        put(["poseEstimate", "sweptWcs", 0, "fovHorizontalDeg"], math.nextafter(2 * tol, math.inf)), says=(SAYS["swept_size"],))
    bad("swept_wcs_same_scale_wider_just_past_the_tolerance", "approximate_with_pose_wider_by_the_tolerance",
        "a swept WCS with the nominal scale, wider by one float more than the tolerance", ["poseEstimate.sweptWcs.1"],
        put(["poseEstimate", "sweptWcs", 1, "fovHorizontalDeg"], math.nextafter(2 * tol, math.inf)), says=(SAYS["swept_size"],))
    bad("one_swept_wcs", "approximate_with_pose", "a sweep of one WCS", ["poseEstimate.sweptWcs"],
        put(["poseEstimate", "sweptWcs"], SWEPT[:1]))
    bad("pose_beacon_not_its_methods", "approximate_with_pose", "a Moon method that names the Sun as its beacon",
        ["poseEstimate.beacon"], put(["poseEstimate", "beacon"], "sun"), says=(SAYS["beacon"],))
    cases += _class_evidence_cases(valid)
    return cases


HELD_OUT = "evidence.verifiedByIndependentHoldout"


def holds(finding: dict[str, Any], path: str) -> bool:
    """Whether a wire finding holds a field of the class table: it is there, and the held-out flag is true."""
    value = at_path(finding, path.split("."))
    return value is True if path == HELD_OUT else value is not None


def _class_evidence_cases(valid: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The relabellings, as named rows on the calibrated sample, which holds one finding of each class under a
    verified WCS: a class claimed with only another class's evidence. The first two are AUDIT J's: the observed finding
    relabelled catalog_matched with its evidence still observed, no catalog entry and no catalog reference id; and a
    catalog match with its catalog entry removed. Each cell of the table on its own is in `_class_evidence_cells`."""
    cases: list[dict[str, Any]] = []
    solution = valid["calibrated"]
    index_of = {evidence_class: next(i for i, f in enumerate(solution["findings"]) if f["evidenceClass"] == evidence_class)
                for evidence_class in contract.FINDING_CLASS_EVIDENCE}
    observed, matched, predicted = index_of["observed"], index_of["catalog_matched"], index_of["predicted"]
    if "skyPosition" not in solution["findings"][observed]:
        raise AssertionError("the calibrated sample's observed finding is placed by its verified WCS")
    table, rules = contract.FINDING_CLASS_EVIDENCE, contract.FINDING_EVIDENCE_RULES

    def bad(name: str, reason: str, paths: list[str], *steps: dict[str, Any], says: tuple[str, ...]) -> None:
        cases.append({"name": name, "base": "calibrated", "patch": list(steps), "reason": reason, "paths": sorted(set(paths)),
                      "messages": list(dict.fromkeys(says))})

    def unmet(i: int, evidence_class: str) -> list[str]:
        """The fields the class must hold that finding i of the sample does not hold."""
        return [path for path in table[evidence_class]["must"] if not holds(solution["findings"][i], path)]

    def held(i: int, evidence_class: str) -> list[str]:
        """The fields the class must not hold that finding i of the sample holds."""
        return [path for path in table[evidence_class]["mustNot"] if holds(solution["findings"][i], path)]

    must = table["catalog_matched"]["must"]
    as_match = unmet(observed, "catalog_matched")
    assert {"catalogRef", "evidence.catalogReferenceId"} <= set(as_match), as_match
    bad("audit_j_observed_relabelled_catalog_matched_its_evidence_still_observed",
        "AUDIT J: a verified solution's observed finding relabelled catalog_matched, its evidence still observed, no catalog entry, no id",
        [f"findings.{observed}.{path}" for path in as_match] + [f"findings.{observed}.evidence.evidenceClass"],
        put(["findings", observed, "evidenceClass"], "catalog_matched"),
        says=(*(must[path] for path in as_match), rules["ownClass"]))
    bad("audit_j_catalog_match_without_its_catalog_entry",
        "AUDIT J: a catalog match with its catalog entry removed, its evidence still naming the entry",
        [f"findings.{matched}.catalogRef", f"findings.{matched}.evidence.catalogReferenceId"], drop(["findings", matched, "catalogRef"]),
        says=(must["catalogRef"], rules["sameEntry"]))
    bad("observed_relabelled_catalog_matched_with_its_evidence", "an observed finding relabelled catalog_matched, its evidence too",
        [f"findings.{observed}.{path}" for path in as_match],
        put(["findings", observed, "evidenceClass"], "catalog_matched"), put(["findings", observed, "evidence", "evidenceClass"], "catalog_matched"),
        says=tuple(must[path] for path in as_match))
    as_observed = held(matched, "observed")
    assert {"catalogRef", "evidence.catalogReferenceId"} <= set(as_observed), as_observed
    # Its residuals left out too, so the row is judged at the finding's own level alone (an observed finding's residual
    # in a calibrated solution is a rule of the solution, its own row below)
    bad("catalog_match_relabelled_observed", "a catalog match relabelled observed, its evidence too: an identification the class does not hold",
        [f"findings.{matched}.{path}" for path in as_observed],
        put(["findings", matched, "evidenceClass"], "observed"), put(["findings", matched, "evidence", "evidenceClass"], "observed"),
        *(drop(["findings", matched, "evidence", field]) for field in OBSERVED_FIT_RESIDUAL),
        says=tuple(table["observed"]["mustNot"][path] for path in as_observed))
    # The pose fit's residual outside an approximate solution (the lead's ruling of 2026-11-12 on the observed Moon);
    # its neighbour, the same residual in an approximate solution, is the valid sample approximate_with_an_observed_fit_residual
    for base, at_index in (("calibrated", observed), ("unresolved_pixels_only", 0)):
        steps = [put(["findings", at_index, "evidence", field], value) for field, value in OBSERVED_FIT_RESIDUAL.items()]
        cases.append({"name": f"observed_fit_residual_in_a_{valid[base]['status']}_solution", "base": base, "patch": steps,
                      "reason": f"an observed finding holding a residual in a {valid[base]['status']} solution, not a pose estimate's",
                      "paths": sorted(f"findings.{at_index}.evidence.{field}" for field in OBSERVED_FIT_RESIDUAL),
                      "messages": [contract.OBSERVED_RESIDUAL_OF_A_POSE]})
    as_match = unmet(predicted, "catalog_matched")
    assert "sourceDetection" in as_match, as_match
    bad("predicted_relabelled_catalog_matched", "a deep-sky prediction relabelled catalog_matched, its evidence too: no detection was matched",
        [f"findings.{predicted}.{path}" for path in as_match],
        put(["findings", predicted, "evidenceClass"], "catalog_matched"), put(["findings", predicted, "evidence", "evidenceClass"], "catalog_matched"),
        says=tuple(must[path] for path in as_match))
    return cases


# Which finding on its own stands for each class in the table's cells (the fixture's findings.valid)
CLASS_BASES = {"observed": "observed", "catalog_matched": "catalog_matched", "predicted": "predicted_for_a_time_and_place_alone",
               "simulated": "simulated_alone"}


def _class_evidence_cells(findings: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Every cell of the contract's class table, both ways, on a finding of the class on its own: each field it must
    hold not held and each field it must not hold put in (refused, at that field, in the cell's sentence), and each
    field it may hold put in or left out (taken), with the four bases themselves taken; then evidence naming another
    class, and a catalog reference id naming another entry than the one carried, or one where none is carried
    (refused). A field put in is the catalog match's own value, real evidence in the wrong finding; the held-out flag
    is held when true and left out when false. Returns (refused, taken)."""
    table, rules = contract.FINDING_CLASS_EVIDENCE, contract.FINDING_EVIDENCE_RULES
    donor = findings[CLASS_BASES["catalog_matched"]]
    refused: list[dict[str, Any]] = []
    taken: list[dict[str, Any]] = []

    def hold(keys: list[str]) -> dict[str, Any]:
        return put(keys, True if ".".join(keys) == HELD_OUT else at_path(donor, keys))

    def leave_out(keys: list[str]) -> dict[str, Any]:
        return put(keys, False) if ".".join(keys) == HELD_OUT else drop(keys)

    for evidence_class, base in CLASS_BASES.items():
        own, sample = table[evidence_class], findings[base]
        taken.append({"name": f"{evidence_class}_as_it_is", "base": base, "patch": []})
        for path in contract.FINDING_FIELDS:
            keys = path.split(".")
            present = holds(sample, path)
            if path in own["must"]:
                assert present, (evidence_class, path)
                paths, messages = [path], [own["must"][path]]
                if path == "catalogRef" and holds(sample, "evidence.catalogReferenceId"):
                    # Without its entry, the id in the evidence names nothing
                    paths, messages = [path, "evidence.catalogReferenceId"], [*messages, rules["sameEntry"]]
                refused.append({"name": f"{evidence_class}_must_hold_{path}", "base": base, "patch": [leave_out(keys)],
                                "reason": f"a {evidence_class} finding that does not hold its {path}", "paths": sorted(paths),
                                "messages": messages})
            elif path in own["mustNot"]:
                assert not present, (evidence_class, path)
                refused.append({"name": f"{evidence_class}_must_not_hold_{path}", "base": base, "patch": [hold(keys)],
                                "reason": f"a {evidence_class} finding holding {path}", "paths": [path], "messages": [own["mustNot"][path]]})
            else:
                assert path in own["may"], (evidence_class, path)
                steps = [leave_out(keys) if present else hold(keys)]
                # An entry and the id that names it come and go together: an id that names an entry the finding does
                # not carry is refused on every class
                if path == "catalogRef" and present and holds(sample, "evidence.catalogReferenceId"):
                    steps.append(drop(["evidence", "catalogReferenceId"]))
                if path == "evidence.catalogReferenceId" and not present and not holds(sample, "catalogRef"):
                    steps.append(put(["catalogRef"], donor["catalogRef"]))
                taken.append({"name": f"{evidence_class}_may_{'leave_out' if present else 'hold'}_{path}", "base": base, "patch": steps})
        other = "simulated" if evidence_class != "simulated" else "predicted"
        refused.append({"name": f"{evidence_class}_with_{other}_evidence", "base": base, "patch": [put(["evidence", "evidenceClass"], other)],
                        "reason": f"a {evidence_class} finding whose evidence names {other}", "paths": ["evidence.evidenceClass"],
                        "messages": [rules["ownClass"]]})
        if holds(sample, "catalogRef") and holds(sample, "evidence.catalogReferenceId"):
            refused.append({"name": f"{evidence_class}_naming_another_entry", "base": base,
                            "patch": [put(["evidence", "catalogReferenceId"], f"{sample['catalogRef']['id']}-not")],
                            "reason": f"a {evidence_class} finding whose catalog reference id names another entry than it carries",
                            "paths": ["evidence.catalogReferenceId"], "messages": [rules["sameEntry"]]})
        if not holds(sample, "catalogRef") and "evidence.catalogReferenceId" not in own["mustNot"]:
            refused.append({"name": f"{evidence_class}_naming_an_entry_it_does_not_carry", "base": base,
                            "patch": [put(["evidence", "catalogReferenceId"], donor["catalogRef"]["id"])],
                            "reason": f"a {evidence_class} finding whose catalog reference id names an entry it does not carry",
                            "paths": ["evidence.catalogReferenceId"], "messages": [rules["sameEntry"]]})
    return refused, taken


def _finding_samples(valid: dict[str, dict[str, Any]], parts: dict[str, Any]) -> dict[str, Any]:
    """Findings on their own, as `CelestialFindingSchema` judges one outside a solution."""
    standalone = {key: value for key, value in parts["predicted_star"].items() if key != "observationSolutionId"}
    standalone["evidence"] = {key: value for key, value in standalone["evidence"].items() if key != "sourceAssetSha256"}
    observed = valid["unresolved_pixels_only"]["findings"][0]
    alone = {
        "predicted_for_a_time_and_place_alone": standalone,
        "simulated_alone": {key: value for key, value in parts["simulated"].items() if key != "observationSolutionId"},
        "observed": observed,
        "catalog_matched": parts["rigel"],
    }
    cells_refused, cells_taken = _class_evidence_cells(alone)
    return {
        "valid": alone,
        # Each cell of the class table both ways (refused below, taken here), judged alike by both sides
        "taken": cells_taken,
        "invalid": cells_refused + [
            {"name": "observed_names_no_solution", "base": "observed", "patch": [drop(["observationSolutionId"])],
             "reason": "an observed finding belongs to a solution", "paths": ["observationSolutionId"],
             "messages": [SAYS["names_its_solution"]]},
            {"name": "catalog_match_names_no_recording", "base": "catalog_matched",
             "patch": [drop(["evidence", "sourceAssetSha256"])], "reason": "a catalog match rests on a recording",
             "paths": ["evidence.sourceAssetSha256"], "messages": [SAYS["names_its_recording"]]},
            {"name": "solution_id_sent_as_null", "base": "predicted_for_a_time_and_place_alone",
             "patch": [put(["observationSolutionId"], None)], "reason": "a missing id is left out, never null",
             "paths": ["observationSolutionId"], "messages": []},
            {"name": "solution_id_empty", "base": "predicted_for_a_time_and_place_alone",
             "patch": [put(["observationSolutionId"], "")], "reason": "an empty id names nothing",
             "paths": ["observationSolutionId"], "messages": []},
            {"name": "predicted_without_a_sky_position", "base": "predicted_for_a_time_and_place_alone",
             "patch": [drop(["skyPosition"])], "reason": "a prediction is a sky position", "paths": ["skyPosition"],
             "messages": []},
        ],
    }


def _ingest_samples() -> dict[str, Any]:
    """Calendar days and finite numbers in the ingest contract (packages/contracts/src/ingest.ts), which neither side
    took alike before: an impossible day, year 0, and an altitude JSON cannot carry."""
    sha = "0a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f9"
    job = {"id": "job_0a1b2c3d4e5f6071", "sourceAssetSha256": sha, "state": "queued", "createdAtUTC": "2032-02-29T19:20:00Z", "stages": []}
    exif_time = {"container": "exif", "tags": ["DateTimeOriginal"]}
    local_only = {"localDateTime": "2032-02-29T05:42:18", "utcOffsetMinutes": None, "utc": None, "marks": "recorded", "source": exif_time}
    file_time = {"localDateTime": None, "utcOffsetMinutes": None, "utc": "2031-06-30T19:20:00Z", "marks": "file_written",
                 "source": {"container": "quicktime", "tags": ["creation_time"]}}
    place = {"latDeg": 30.0, "lonDeg": -40.0, "altitudeM": 12.5, "source": {"container": "exif", "tags": ["GPSLatitude", "GPSLongitude"]}}
    invalid: list[dict[str, Any]] = []

    def bad(schema: str, sample: dict[str, Any], reason: str) -> None:
        invalid.append({"schema": schema, "sample": sample, "reason": reason})

    # Each sample is a valid one with a time moved to a day no calendar holds, or with a number JSON cannot carry
    bad("SolveJob", {**job, "createdAtUTC": "2031-02-30T19:20:00Z"}, "created on the thirtieth of February")
    bad("SolveJob", {**job, "createdAtUTC": "2031-02-29T19:20:00Z"},
        "created on the twenty-ninth of February of a year that is not a leap year")
    bad("SolveJob", {**job, "createdAtUTC": "0000-01-01T00:00:00Z"}, "created in year zero, which neither side's calendar holds alike")
    bad("SolveJob", {**job, "state": "running", "createdAtUTC": "2031-04-30T19:20:00Z", "startedAtUTC": "2031-04-31T00:00:01Z"},
        "started on the thirty-first of April")
    bad("SolveJob", {**job, "state": "failed", "createdAtUTC": "2031-06-30T23:59:00Z", "finishedAtUTC": "2031-06-31T00:00:01Z",
                     "error": "The decoder could not read the file."}, "finished on the thirty-first of June")
    bad("CaptureTimeHint", {**local_only, "localDateTime": "2031-02-30T05:42:18"}, "a local time on the thirtieth of February")
    bad("CaptureTimeHint", {**local_only, "localDateTime": "0000-01-01T05:42:18"}, "a local time in year zero")
    bad("CaptureTimeHint", {**file_time, "utc": "2031-06-31T19:20:00Z"}, "a file time on the thirty-first of June")
    bad("CaptureTimeHint", {**local_only, "localDateTime": "2031-02-30T05:42:18", "utcOffsetMinutes": 120, "utc": "2031-02-30T03:42:18Z"},
        "a local time and its UTC both on the thirtieth of February")
    bad("LocationHint", {**place, "altitudeM": {"$number": "NaN"}}, "an altitude that is not a number")
    bad("LocationHint", {**place, "altitudeM": {"$number": "Infinity"}}, "an infinite altitude")
    bad("LocationHint", {**place, "altitudeM": {"$number": "-Infinity"}}, "an altitude of minus infinity")
    return {
        "valid": [
            {"schema": "SolveJob", "sample": job},
            {"schema": "CaptureTimeHint", "sample": local_only},
            {"schema": "CaptureTimeHint", "sample": file_time},
            {"schema": "LocationHint", "sample": place},
            {"schema": "LocationHint", "sample": {**place, "altitudeM": -431.0}},
        ],
        "invalid": invalid,
    }


def build_fixture() -> dict[str, Any]:
    least, most = contract.held_out_gate()
    valid, parts = _samples()
    found = {f["id"]: i for i, f in enumerate(valid["calibrated"]["findings"])}
    rigel_at = found["finding-star-rigel"]
    homes = {
        "ObservationSolution": {"sample": "calibrated", "path": []},
        "CelestialFinding": {"sample": "calibrated", "path": ["findings", rigel_at]},
        "SourceDetection": {"sample": "calibrated", "path": ["findings", rigel_at, "sourceDetection"]},
        "SkyPosition": {"sample": "calibrated", "path": ["findings", rigel_at, "skyPosition"]},
        "ScientificEvidence": {"sample": "calibrated", "path": ["findings", rigel_at, "evidence"]},
        "InferredDistance": {"sample": "calibrated", "path": ["findings", rigel_at, "inferredDistancePc"]},
        "CatalogReference": {"sample": "calibrated", "path": ["findings", rigel_at, "catalogRef"]},
        "CatalogDistance": {"sample": "calibrated", "path": ["findings", rigel_at, "catalogRef", "bailerJonesDistancePc"]},
        "WCSCalibrationModel": {"sample": "calibrated", "path": ["wcs"]},
        "SolutionQuality": {"sample": "calibrated", "path": ["quality"]},
        "SolverIdentity": {"sample": "calibrated", "path": ["solver"]},
        "ObserverLocation": {"sample": "calibrated", "path": ["observerLocation"]},
        "PoseEstimate": {"sample": "approximate_with_pose", "path": ["poseEstimate"]},
    }
    with_unnamed_keys = patched(
        valid["approximate"],
        [put(["note"], None), put(["wcs", "widthPx"], WIDTH), put(["findings", 0, "sourceDetection", "peakToTotalRatio"], 0.25),
         put(["quality", "logOdds"], "high"), put(["observerLocation", "accuracy"], {"metres": None})],
    )
    table = {
        "about": (
            "Samples and field rules for the observation solution family, written by "
            "tests/integration/test_observation_solution_contract.py and judged alike by the Zod schemas "
            "(packages/contracts/src/astronomy.ts) and the Python parser (services/contracts/astronomy.py). "
            'A number JSON cannot carry is written {"$number": "NaN"}, {"$number": "Infinity"} or {"$number": "-Infinity"}.'
        ),
        "patterns": {"sha256": ingest.SHA256_HEX_PATTERN, "utcInstant": ingest.ZOD_DATETIME_PATTERN},
        # Read from services/science/solver.py, never retyped here
        "heldOutGate": {"minHeldOutInliers": least, "maxMedianResidualPx": most},
        "sweptFovToleranceDeg": contract.SWEPT_FOV_TOLERANCE_DEG,
        # What each evidence class needs and refuses, and the rules between a finding's fields: one table, both sides
        "findingClassEvidence": contract.FINDING_CLASS_EVIDENCE,
        "findingEvidenceRules": contract.FINDING_EVIDENCE_RULES,
        "findingFields": list(contract.FINDING_FIELDS),
        **contract.field_table(),
        "homes": homes,
        "valid": valid,
        "invalid": _invalid(valid, parts),
        "unnamedKeys": {"sample": with_unnamed_keys, "parsed": valid["approximate"]},
        "findings": _finding_samples(valid, parts),
        "ingest": _ingest_samples(),
    }
    # How many generated key cases there are of each kind, for the Zod side to match on the same cases
    table["keyCases"] = key_cases(table)
    return table


def read_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def stored_python_solutions() -> dict[str, dict[str, Any]]:
    return json.loads(PYTHON_SOLUTIONS.read_text(encoding="utf-8"))


FIXTURE_NOW = read_fixture() if FIXTURE.is_file() else {"valid": {}, "invalid": [], "fields": {}, "rules": {}, "findings": {"valid": {}, "invalid": []}, "ingest": {"valid": [], "invalid": []}}
INGEST_MODELS = {"SolveJob": ingest.SolveJob, "CaptureTimeHint": ingest.CaptureTimeHint, "LocationHint": ingest.LocationHint}


def refusal(payload: Any, parse: Any = None) -> ValidationError | None:
    try:
        (parse or contract.parse_observation_solution)(payload)
    except ValidationError as error:
        return error
    return None


def test_fixture_is_current() -> None:
    text = json.dumps(build_fixture(), indent=1, ensure_ascii=True, allow_nan=False) + "\n"
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text, f"regenerate with {UPDATE_FIXTURE_ENV}=1"


def test_the_gate_numbers_are_the_plate_solvers_own() -> None:
    least, most = contract.held_out_gate()
    assert (least, most) == (solver.MIN_HELD_OUT_INLIERS, solver.MAX_HELD_OUT_MEDIAN_RESIDUAL_PX)
    assert read_fixture()["heldOutGate"] == {"minHeldOutInliers": least, "maxMedianResidualPx": most}
    # The phone solver keeps its own copies (services/science/phone_photo_solver.py): they may be stricter, never
    # looser, or it could mark as passed what the contract refuses
    assert phone_photo_solver.MIN_HELD_OUT_INLIERS >= least
    assert phone_photo_solver.MAX_HELD_OUT_MEDIAN_PX <= most
    assert gate_sentence().replace(str(least), "${MIN_HELD_OUT_INLIERS}").replace(
        f"{most:g} px", "${MAX_HELD_OUT_MEDIAN_RESIDUAL_PX} px"
    ) in ASTRONOMY_TS.read_text(encoding="utf-8")


def test_the_parser_refuses_in_the_contracts_own_words() -> None:
    source = ASTRONOMY_TS.read_text(encoding="utf-8")
    for key, sentence in SAYS.items():
        assert sentence in source, key
    assert "Only a calibrated solution passes held-out verification, not a ${solution.status} one." in source


@pytest.mark.parametrize("name", sorted(FIXTURE_NOW["valid"]))
def test_a_valid_sample_parses_and_comes_back_unchanged(name: str) -> None:
    sample = read_fixture()["valid"][name]
    parsed = contract.parse_observation_solution(sample)
    assert parsed.to_contract_dict() == sample
    assert json.loads(json.dumps(parsed.to_contract_dict(), allow_nan=False)) == sample


def test_every_solution_the_python_solvers_wrote_parses_and_comes_back_unchanged() -> None:
    samples = stored_python_solutions()
    assert len(samples) >= 8
    for name, sample in samples.items():
        parsed = contract.parse_observation_solution(sample)
        assert parsed.to_contract_dict() == sample, name
        assert parsed.has_verified_wcs == (sample["status"] == "calibrated"), name


def test_a_key_the_contract_does_not_name_is_dropped() -> None:
    case = read_fixture()["unnamedKeys"]
    assert contract.parse_observation_solution(case["sample"]).to_contract_dict() == case["parsed"]


# A key no schema names, with a value of each kind JSON has
UNKNOWN_KEY_VALUES: list[Any] = [1, "text", None, True, [1, 2], {"nested": {"deeper": 1}}]


def key_levels(table: dict[str, Any]) -> list[tuple[str, str, tuple[Any, ...]]]:
    """Every record at every level of every valid sample, the findings judged on their own among them, as (the sample
    it is in, the schema that judges it, where it is), found through the field table."""
    levels = [
        (name, schema, path)
        for name, sample in table["valid"].items()
        for schema, path in instances(table["fields"], "ObservationSolution", sample)
    ]
    levels += [
        (f"finding {name}", schema, path)
        for name, sample in table["findings"]["valid"].items()
        for schema, path in instances(table["fields"], "CelestialFinding", sample)
    ]
    return levels


def test_the_python_names_in_the_table_are_the_parsers_own() -> None:
    """The table's Python names are read from the parser's models, and every schema with a camelCase field has some.
    Each is the wire name written in snake_case, which is why a Python writer's slip would produce exactly it."""
    names = read_fixture()["pythonNames"]
    assert set(names) == set(contract.FAMILY)
    for schema, model in contract.FAMILY.items():
        wires = {name: field.alias or name for name, field in model.model_fields.items()}
        assert names[schema] == {wire: name for name, wire in wires.items() if wire != name}
        for wire, python in names[schema].items():
            assert python != wire and python == python.lower() and python.replace("_", "") == wire.lower(), (schema, wire)
    assert sum(len(of_schema) for of_schema in names.values()) >= 60


def key_cases(table: dict[str, Any]) -> dict[str, int]:
    """Generated from the field table, so a new record or field is covered without a line here. At every level of
    every valid sample:
    - a key no schema names is dropped, whatever its value, and so is the Python name of every field of that record
      set beside the wire name with the same value, all at once and one at a time: the record parses to what it was;
    - a field carried only under its Python name is not carried: a required one is refused as missing at its wire
      name, and an optional one is left out. No Python name is ever read from the wire.
    Returns how many cases there were of each kind, which the fixture carries so that the Zod schemas are held to
    the same counts on the same cases (observation-solution-contract.test.ts)."""
    counts = {"levels": 0, "dropped": 0, "refusedWhenRenamed": 0, "leftOutWhenRenamed": 0}
    for name, schema, path in key_levels(table):
        on_its_own = name.startswith("finding ")
        sample = table["findings"]["valid"][name.removeprefix("finding ")] if on_its_own else table["valid"][name]
        parse = contract.parse_celestial_finding if on_its_own else contract.parse_observation_solution
        record = at(sample, list(path))
        names = {wire: python for wire, python in table["pythonNames"][schema].items() if wire in record}
        twins = [put([*path, python], record[wire]) for wire, python in names.items()]
        added = [[put([*path, "unknownToTheContract"], value)] for value in UNKNOWN_KEY_VALUES] + [twins] + [[twin] for twin in twins]
        for steps in added:
            assert parse(patched(sample, steps)).to_contract_dict() == sample, (name, schema, path, steps)
            counts["dropped"] += 1
        for wire, python in names.items():
            renamed = patched(sample, [put([*path, python], record[wire]), drop([*path, wire])])
            error = refusal(renamed, parse)
            if error is None:
                assert parse(renamed).to_contract_dict() == patched(sample, [drop([*path, wire])]), (name, schema, path, wire)
                counts["leftOutWhenRenamed"] += 1
            else:
                # Refused where the field is missing, or at its record when a rule of the record needs the field, or,
                # for a catalog entry, at the id in the evidence that names it (which then names nothing)
                tied = {dotted([*path, "evidence", "catalogReferenceId"])} if schema == "CelestialFinding" and wire == "catalogRef" else set()
                assert ({dotted([*path, wire]), dotted(path)} | tied) & set(contract.issue_paths(error)), (name, schema, path, wire)
                counts["refusedWhenRenamed"] += 1
        counts["levels"] += 1
    return counts


def test_an_unknown_key_or_a_python_named_key_at_any_level_of_any_record_is_never_read() -> None:
    table = read_fixture()
    assert {schema for _, schema, _ in key_levels(table)} == set(contract.FAMILY)
    counts = key_cases(table)
    assert counts == table["keyCases"]
    assert counts["levels"] >= 100 and counts["dropped"] >= 1000, counts
    # Floors that say the generator reached most fields (the exact counts are held above, and alike on the Zod side):
    # a field a class must hold, carried only under its Python name, is refused rather than left out, so the class
    # table and the rulings of 2026-11-12 moved 24 cases from the second count to the first (106 to 82)
    assert counts["refusedWhenRenamed"] >= 400 and counts["leftOutWhenRenamed"] >= 75, counts


@pytest.mark.parametrize("case", FIXTURE_NOW["invalid"], ids=[case["name"] for case in FIXTURE_NOW["invalid"]])
def test_a_named_invalid_sample_is_refused_where_and_as_the_contract_says(case: dict[str, Any]) -> None:
    samples = read_fixture()["valid"]
    error = refusal(patched(samples[case["base"]], case["patch"]))
    assert error is not None, case["reason"]
    assert contract.issue_paths(error) == case["paths"]
    messages = contract.issue_messages(error)
    assert [sentence for sentence in case["messages"] if sentence not in messages] == []
    # A refusal names the field and the rule, never the value: a capture time or a place must not reach a log
    assert "input_value" not in str(error) and MOMENT not in str(error)


def test_findings_on_their_own_are_judged_as_the_contract_judges_them() -> None:
    findings = read_fixture()["findings"]
    for name, sample in findings["valid"].items():
        assert contract.parse_celestial_finding(sample).to_contract_dict() == sample, name
    for case in findings["invalid"]:
        error = refusal(patched(findings["valid"][case["base"]], case["patch"]), contract.parse_celestial_finding)
        assert error is not None, case["reason"]
        assert contract.issue_paths(error) == case["paths"], case["name"]
        assert [sentence for sentence in case["messages"] if sentence not in contract.issue_messages(error)] == []
    # The neighbours of every refused cell: a field a class may hold, held or left out, is taken
    assert len(findings["taken"]) >= len(CLASS_BASES) * 5
    for case in findings["taken"]:
        sample = patched(findings["valid"][case["base"]], case["patch"])
        assert refusal(sample, contract.parse_celestial_finding) is None, case["name"]
        assert contract.parse_celestial_finding(sample).to_contract_dict() == sample, case["name"]


def test_one_class_table_both_sides_read_places_every_field_once_and_cites_its_sentences() -> None:
    """The table is the parser's own, the fixture's (which the Zod side holds its own against) and, for each class, a
    partition of the finding's fields: each in exactly one of must, may and must not, and every row citing AGENTS.md
    section 1 and the contract's field docs it is written from."""
    table = read_fixture()
    assert table["findingClassEvidence"] == contract.FINDING_CLASS_EVIDENCE
    assert table["findingEvidenceRules"] == contract.FINDING_EVIDENCE_RULES
    assert table["findingFields"] == list(contract.FINDING_FIELDS)
    assert set(contract.FINDING_CLASS_EVIDENCE) == set(contract.EVIDENCE_CLASS.values)
    for evidence_class, own in contract.FINDING_CLASS_EVIDENCE.items():
        placed = [*own["must"], *own["mustNot"], *own["may"]]
        assert sorted(placed) == sorted(contract.FINDING_FIELDS) and len(placed) == len(set(placed)), evidence_class
        assert any(source.startswith(f'AGENTS.md section 1: "{evidence_class}:') for source in own["source"]), evidence_class
        assert all(sentence.endswith(".") for sentence in [*own["must"].values(), *own["mustNot"].values()]), evidence_class


def _doc_words(path: Path, *, without: tuple[str, str] | None = None) -> str:
    """A source file's words as one line: comment markers and backticks taken out, every run of spaces one space.
    `without` names where a span starts and the text that ends it, and leaves that span out: the class table itself,
    so a quote is never found in the very table that quotes it."""
    text = path.read_text(encoding="utf-8")
    if without is not None:
        start = text.index(without[0])
        text = text[:start] + text[text.index(without[1], start) + len(without[1]):]
    text = re.sub(r"(?m)^\s*(?:/\*\*|//|\*/|\*|#)\s?", " ", text)
    return " ".join(text.replace("`", "").split())


def test_every_quote_in_the_class_table_is_its_source_whole() -> None:
    """A row quotes AGENTS.md or the contract's own docs: each quoted span is found there whole, word for word, and no
    ellipsis stands for a cut, so a conditional sentence never reads as an unconditional one (AUDIT K, v4)."""
    table = ("export const FINDING_CLASS_EVIDENCE = {", "} as const satisfies Record<EvidenceClass, ClassEvidence>;")
    sources = [_doc_words(ROOT / "AGENTS.md"), _doc_words(ROOT / "packages" / "contracts" / "src" / "astronomy.ts", without=table)]
    quotes = [(evidence_class, quote) for evidence_class, own in contract.FINDING_CLASS_EVIDENCE.items()
              for source in own["source"] for quote in re.findall(r'"([^"]+)"', source)]
    assert len(quotes) >= 10
    for evidence_class, quote in quotes:
        assert "..." not in quote, (evidence_class, quote)
        assert any(" ".join(quote.split()) in words for words in sources), (evidence_class, quote)


def _cell_verdicts(table: dict[str, Any], findings: dict[str, Any]) -> list[list[str]]:
    """What `table` says of each cell sample of the fixture: each class issue, its path and its sentence, by
    finding_class_issues (a field two rules refuse is told so by both)."""
    cases = [*[c for c in findings["invalid"] if c["base"] in CLASS_BASES.values()], *findings["taken"]]
    return [sorted({f"{'.'.join(path)}: {sentence}" for path, sentence in contract.finding_class_issues(table, patched(findings["valid"][c["base"]], c["patch"]))})
            for c in cases]


def test_flipping_any_cell_of_the_class_table_changes_what_a_sample_is_told() -> None:
    """A mutant of the table itself: each field of each class moved to each of the other two cells, one at a time. The
    fixture's cells then judge at least one sample otherwise, so no cell is unread."""
    findings = read_fixture()["findings"]
    table = contract.FINDING_CLASS_EVIDENCE
    as_it_is = _cell_verdicts(table, findings)
    flips = 0
    for evidence_class, own in table.items():
        for path in contract.FINDING_FIELDS:
            here = "must" if path in own["must"] else "mustNot" if path in own["mustNot"] else "may"
            for there in {"must", "mustNot", "may"} - {here}:
                flipped = copy.deepcopy(table)
                cell = flipped[evidence_class]
                if here == "may":
                    cell["may"].remove(path)
                else:
                    del cell[here][path]
                if there == "may":
                    cell["may"].append(path)
                else:
                    cell[there][path] = f"flipped {path}"
                assert _cell_verdicts(flipped, findings) != as_it_is, (evidence_class, path, here, there)
                flips += 1
    assert flips == len(table) * len(contract.FINDING_FIELDS) * 2


@pytest.mark.parametrize("payload", [None, [], "solution", 5, True], ids=["null", "list", "text", "number", "boolean"])
def test_what_is_not_an_object_is_refused(payload: Any) -> None:
    assert refusal(payload) is not None
    assert refusal(payload, contract.parse_celestial_finding) is not None


# =============================================================================================
# The field table: every field of the family, and its rule at the edges
# =============================================================================================


def test_the_field_table_names_every_field_of_the_family() -> None:
    table = read_fixture()
    assert sorted(table["fields"]) == sorted(contract.FAMILY) == sorted(table["homes"])
    for schema, model in contract.FAMILY.items():
        wire = {field.alias or name: field for name, field in model.model_fields.items()}
        assert sorted(table["fields"][schema]) == sorted(wire), schema
        for name, entry in table["fields"][schema].items():
            assert entry.get("optional", False) == (not wire[name].is_required()), f"{schema}.{name}"
            assert ("rule" in entry) != ("schema" in entry), f"{schema}.{name}"
            if "rule" in entry:
                assert entry["rule"] in table["rules"], f"{schema}.{name}"
            else:
                assert entry["schema"] in contract.FAMILY, f"{schema}.{name}"
    # Every object the valid samples hold sits where the table says a schema of the family does
    seen = {schema for sample in table["valid"].values() for schema, _ in instances(table["fields"], "ObservationSolution", sample)}
    assert seen == set(contract.FAMILY)
    assert table["patterns"] == {"sha256": ingest.SHA256_HEX_PATTERN, "utcInstant": ingest.ZOD_DATETIME_PATTERN}


def edge_cases(table: dict[str, Any], schema: str) -> Iterator[tuple[str, dict[str, Any], bool]]:
    """(what is tried, the schema's home object with one field changed, whether the contract accepts it)."""
    home = table["homes"][schema]
    base = at(table["valid"][home["sample"]], home["path"])

    def having(field: str, value: Any) -> dict[str, Any]:
        return {**copy.deepcopy(base), field: decode(copy.deepcopy(value))}

    for field, entry in table["fields"][schema].items():
        if "rule" in entry:
            rule = table["rules"][entry["rule"]]
            copies = max(entry.get("minItems", 1), 1)
            for verdict in ("accept", "refuse"):
                for value in rule[verdict]:
                    tried = [value] * copies if entry.get("array") else value
                    yield f"{schema}.{field} {verdict}s {json.dumps(value)}", having(field, tried), verdict == "accept"
        kind = "array" if entry.get("array") else "object" if "schema" in entry else None
        if kind is not None:
            for value in table["structureRefusals"][kind]:
                yield f"{schema}.{field} refuses {json.dumps(value)}", having(field, value), False
        if entry.get("array") and field in base:
            if entry.get("minItems", 0) > 0:
                yield f"{schema}.{field} refuses too few", having(field, base[field][: entry["minItems"] - 1]), False
            if "maxItems" in entry:
                yield f"{schema}.{field} refuses too many", having(field, [base[field][0]] * (entry["maxItems"] + 1)), False
        if not entry.get("optional", False):
            yield f"{schema}.{field} is required", {key: value for key, value in copy.deepcopy(base).items() if key != field}, False


@pytest.mark.parametrize("schema", sorted(FIXTURE_NOW["fields"]))
def test_each_field_follows_its_rule_at_the_edges(schema: str) -> None:
    table = read_fixture()
    model = contract.FAMILY[schema]
    home = table["homes"][schema]
    model.model_validate(at(table["valid"][home["sample"]], home["path"]))
    wrong = []
    count = 0
    for tried, payload, accepted in edge_cases(table, schema):
        count += 1
        try:
            model.model_validate(payload)
            verdict = True
        except ValidationError:
            verdict = False
        if verdict != accepted:
            wrong.append(tried)
    assert wrong == []
    assert count >= 4 * len(table["fields"][schema])


def test_the_date_pattern_is_zods_own_and_takes_no_trailing_newline() -> None:
    table = read_fixture()
    rule = table["rules"]["utcInstant"]
    assert "2030-01-01T02:00:00Z\n" in rule["refuse"] and "2030-01-01T02:00Z" in rule["accept"]
    # No \d: in the Rust regex pydantic runs it would take any Unicode digit, where JavaScript's takes 0 to 9
    assert "\\d" not in table["patterns"]["utcInstant"]


# =============================================================================================
# Properties
# =============================================================================================


def _bases() -> dict[str, dict[str, Any]]:
    return {**FIXTURE_NOW["valid"], **{f"python:{name}": sample for name, sample in stored_python_solutions().items()}}


BASES = _bases()
SITES = sorted(
    (base, schema, path)
    for base, sample in BASES.items()
    for schema, path in instances(FIXTURE_NOW["fields"], "ObservationSolution", sample)
) if FIXTURE_NOW["fields"] else []


def inside(rule: dict[str, Any]) -> st.SearchStrategy[Any]:
    """Values the rule accepts."""
    fixed = st.sampled_from([decode(value) for value in rule["accept"]]) if rule["accept"] else None
    if rule["type"] == "number":
        low = rule.get("ge", rule.get("gt", -1e6))
        high = rule.get("le", 1e6)
        drawn: st.SearchStrategy[Any] = (
            st.integers(min_value=math.floor(low) + 1 if "gt" in rule else math.ceil(low), max_value=int(min(high, 2**40)))
            if rule.get("integer")
            else st.floats(min_value=low, max_value=high, exclude_min="gt" in rule, allow_nan=False, allow_infinity=False)
        )
        return st.one_of(drawn, fixed) if fixed is not None else drawn
    if rule["type"] == "text" and "pattern" not in rule:
        return st.text(min_size=rule.get("minLength", 0), max_size=12)
    if rule["type"] == "choice":
        return st.sampled_from(rule["values"])
    assert fixed is not None
    return fixed


def outside(rule: dict[str, Any]) -> st.SearchStrategy[Any]:
    """Values outside the rule: the table's own, and for a number any below, above or between its bounds."""
    options: list[st.SearchStrategy[Any]] = [st.sampled_from([decode(value) for value in rule["refuse"]])]
    if rule["type"] == "number":
        finite = {"allow_nan": False, "allow_infinity": False}
        if "ge" in rule:
            options.append(st.floats(max_value=rule["ge"], exclude_max=True, **finite))
        if "gt" in rule:
            options.append(st.floats(max_value=rule["gt"], **finite))
        if "le" in rule:
            options.append(st.floats(min_value=rule["le"], exclude_min=True, **finite))
        if rule.get("integer"):
            options.append(st.floats(min_value=0.0, max_value=1e6, **finite).filter(lambda x: not x.is_integer()))
    return st.one_of(options)


@st.composite
def one_fault(draw: st.DrawFn) -> tuple[str, list[Any], dict[str, Any]]:
    """A valid sample, one of its fields, and a step that moves that field outside its contract."""
    table = FIXTURE_NOW
    base, schema, path = draw(st.sampled_from(SITES))
    field = draw(st.sampled_from(sorted(table["fields"][schema])))
    entry = table["fields"][schema][field]
    where = [*path, field]
    present = field in at(BASES[base], path)
    ways = ["value"]
    if not entry.get("optional", False) and present:
        ways.append("remove")
    way = draw(st.sampled_from(ways))
    if way == "remove":
        return base, where, drop(where)
    if "rule" in entry:
        value = draw(outside(table["rules"][entry["rule"]]))
        if entry.get("array"):
            return base, [*where, 0], put(where, [value] * max(entry.get("minItems", 1), 1))
        return base, where, put(where, value)
    return base, where, put(where, draw(st.sampled_from(table["structureRefusals"]["array" if entry.get("array") else "object"])))


@settings(max_examples=300, deadline=None)
@given(fault=one_fault())
@example(fault=("calibrated_at_the_gate", ["quality", "medianResidualPx"], put(["quality", "medianResidualPx"], math.nan)))
@example(fault=("calibrated_at_the_gate", ["wcs", "rotationDeg"], put(["wcs", "rotationDeg"], math.inf)))
@example(fault=("calibrated_at_the_gate", ["status"], put(["status"], "Calibrated")))
@example(fault=("calibrated_at_the_gate", ["revision"], put(["revision"], 1.5)))
@example(fault=("calibrated_at_the_gate", ["sourceSha256"], put(["sourceSha256"], "0" * 64)))
@example(fault=("approximate", ["observedAtUTC"], put(["observedAtUTC"], "2030-01-01T02:00:00+00:00")))
@example(fault=("approximate", ["findings", 0, "sourceDetection", "flux"], put(["findings", 0, "sourceDetection", "flux"], 0.0)))
@example(fault=("approximate", ["findings"], drop(["findings"])))
def test_pbt_contract_2_one_field_outside_its_contract_is_refused_at_that_field(fault: tuple[str, list[Any], dict[str, Any]]) -> None:
    """P-CONTRACT-2. The TypeScript property draws from the same table and the same samples."""
    base, where, step = fault
    error = refusal(patched(BASES[base], [step]))
    assert error is not None
    paths = contract.issue_paths(error)
    # The existing catalog reference mirror reports a null at the reference, not at its field
    under_reference = "catalogRef" in where and step.get("value", 0) is None
    assert dotted(where) in paths or (under_reference and dotted(where[: where.index("catalogRef") + 1]) in paths), paths


@st.composite
def in_contract_changes(draw: st.DrawFn) -> tuple[str, list[dict[str, Any]]]:
    """A valid sample and up to three of its fields set to other values their rules accept."""
    table = FIXTURE_NOW
    base = draw(st.sampled_from(sorted(BASES)))
    sites = [(schema, path) for name, schema, path in SITES if name == base]
    steps = []
    for _ in range(draw(st.integers(min_value=0, max_value=3))):
        schema, path = draw(st.sampled_from(sites))
        names = sorted(name for name, entry in table["fields"][schema].items() if "rule" in entry)
        field = draw(st.sampled_from(names))
        entry = table["fields"][schema][field]
        value = draw(inside(table["rules"][entry["rule"]]))
        steps.append(put([*path, field], [value] * max(entry.get("minItems", 1), 1) if entry.get("array") else value))
    return base, steps


@settings(max_examples=300, deadline=None)
@given(change=in_contract_changes())
@example(change=("calibrated", []))
@example(change=("approximate_with_pose", [put(["poseEstimate", "rollDeg"], -180.0)]))
@example(change=("calibrated_at_the_gate", [put(["revision"], 7.0), put(["frameTimeSec"], 3)]))
@example(change=("pending_without_a_recording", [put(["warnings"], ["", "x"])]))
def test_pbt_contract_1_an_accepted_sample_comes_back_unchanged(change: tuple[str, list[dict[str, Any]]]) -> None:
    """P-CONTRACT-1. A change inside each field's own rule may still break a rule between fields; whatever the
    parser accepts, it gives back as it was given, and accepts again."""
    base, steps = change
    sample = patched(BASES[base], steps)
    try:
        parsed = contract.parse_observation_solution(sample)
    except ValidationError:
        assert steps != []
        return
    assert parsed.to_contract_dict() == sample
    assert contract.parse_observation_solution(parsed.to_contract_dict()).to_contract_dict() == sample
    json.dumps(parsed.to_contract_dict(), allow_nan=False)


def test_a_whole_number_sent_as_a_float_is_kept_as_an_integer() -> None:
    """A whole number written as a float (7.0, 1e1) is that integer on both sides (the wire rules). The parser keeps
    each whole-number field as Python's int, so the record it gives back writes it as JSON writes an integer; the
    property above compares with ==, under which 7.0 and 7 are the same."""
    gate = read_fixture()["valid"]["calibrated_at_the_gate"]
    inliers = gate["quality"]["heldOutInliers"]
    sent = patched(gate, [put(["revision"], 7.0), put(["quality", "detectedCandidateCount"], 1e1),
                          put(["quality", "heldOutInliers"], float(inliers))])
    written = contract.parse_observation_solution(sent).to_contract_dict()
    for value, whole in ((written["revision"], 7), (written["quality"]["detectedCandidateCount"], 10), (written["quality"]["heldOutInliers"], inliers)):
        assert type(value) is int and value == whole, value
    # A number field that is not whole keeps a float, also for a value sent as an integer
    assert type(contract.parse_observation_solution(patched(gate, [put(["frameTimeSec"], 3)])).to_contract_dict()["frameTimeSec"]) is float


GATE = contract.held_out_gate() if hasattr(contract, "held_out_gate") else (0, 0.0)


@settings(max_examples=300, deadline=None)
@given(
    inliers=st.integers(min_value=0, max_value=14),
    more_pairs=st.integers(min_value=0, max_value=4),
    median=st.floats(min_value=0.0, max_value=8.0, allow_nan=False),
)
@example(inliers=GATE[0], more_pairs=0, median=GATE[1])
@example(inliers=GATE[0] - 1, more_pairs=1, median=GATE[1])
@example(inliers=GATE[0], more_pairs=0, median=math.nextafter(GATE[1], math.inf))
@example(inliers=GATE[0] + 5, more_pairs=3, median=0.0)
@example(inliers=0, more_pairs=1, median=0.0)
def test_pbt_contract_3_a_passed_verification_stands_at_or_above_the_gate(inliers: int, more_pairs: int, median: float) -> None:
    """P-CONTRACT-3. The parser and the `SolutionQuality` dataclass give the same verdict, the gate's own."""
    least, most = contract.held_out_gate()
    pairs = max(inliers + more_pairs, 1)
    sample = patched(
        read_fixture()["valid"]["calibrated_at_the_gate"],
        [put(["quality", "heldOutInliers"], inliers), put(["quality", "heldOutPairCount"], pairs), put(["quality", "medianResidualPx"], median)],
    )
    expected = inliers >= least and median <= most
    assert (refusal(sample) is None) == expected

    def build(passed: bool) -> SolutionQuality:
        return SolutionQuality(
            detected_candidate_count=20, matched_inlier_count=inliers, held_out_pair_count=pairs, held_out_inliers=inliers,
            median_residual_px=median, p95_residual_px=8.0, median_residual_arcsec=median * 126.0,
            passed_independent_verification=passed,
        )

    # Not passed, the same counts are fine on both sides: the gate binds only the flag
    assert refusal(patched(sample, [put(["quality", "passedIndependentVerification"], False), put(["status"], "ambiguous"), drop(["wcs"])])) is None
    assert build(False).passed_independent_verification is False
    if expected:
        assert build(True).to_contract_dict()["passedIndependentVerification"] is True
    else:
        with pytest.raises(ValueError, match="at least"):
            build(True)


# =============================================================================================
# The dataclasses that write solutions (services/science/wcs_models.py)
# =============================================================================================

NOMINAL = WCSModel(crval_ra_deg=83.8, crval_dec_deg=4.0, crpix_x=480.0, crpix_y=270.0, scale_deg_per_px=0.035, width=WIDTH, height=HEIGHT)
DETECTION = SourceDetection(x_px=412.5, y_px=300.25, flux=5000.0, snr=25.0, fwhm_px=2.5, centroid_uncertainty_px=0.1)
IDENTITY = SolverIdentity(name="hand-built in this test", version="0", catalog_release="none")
NOT_A_NUMBER = (math.nan, math.inf, -math.inf, True, "1", None)
STAR = get_star_by_name("Rigel")


def detection(**changes: Any) -> SourceDetection:
    return replace(DETECTION, **changes)


def wcs(**changes: Any) -> WCSModel:
    return replace(NOMINAL, **changes)


def quality(passed: bool = False, **changes: Any) -> SolutionQuality:
    least, most = contract.held_out_gate()
    fields: dict[str, Any] = (
        {"detected_candidate_count": 12, "matched_inlier_count": 9, "held_out_pair_count": least + 2, "held_out_inliers": least,
         "median_residual_px": most / 2.0, "p95_residual_px": most, "median_residual_arcsec": 50.0}
        if passed
        else {"detected_candidate_count": 1, "matched_inlier_count": 0, "held_out_pair_count": 0, "held_out_inliers": 0,
              "median_residual_px": None, "p95_residual_px": None, "median_residual_arcsec": None}
    )
    return SolutionQuality(**{**fields, "passed_independent_verification": passed, **changes})


def record(evidence_class: str = "observed", *, placed: bool = False, **changes: Any) -> CelestialFindingRecord:
    """An observed detection, on the sky when `placed`; a catalog match, a prediction or a simulation at Rigel."""
    fields: dict[str, Any] = {
        "id": f"finding-{evidence_class}", "observation_solution_id": "sol-dataclass", "evidence_class": evidence_class,
        "title": "Hand-built finding", "subtitle": "Generated", "limitations": ("Generated for contract tests.",),
        "source_sha256": SHA,
    }
    if evidence_class in ("observed", "catalog_matched"):
        fields["source_detection"] = DETECTION
    if evidence_class != "observed" or placed:
        fields.update(ra_deg=STAR.ra_deg, dec_deg=STAR.dec_deg, uncertainty_arcsec=12.0, alt_deg=41.5, az_deg=200.25)
    if evidence_class in ("catalog_matched", "predicted"):
        fields.update(catalog_star=STAR, pixel_residual_px=0.4 if evidence_class == "catalog_matched" else None)
    if evidence_class == "catalog_matched":
        # Both residuals, as every writer of a match gives them: the pixel one at the nominal WCS's scale
        fields.update(angular_residual_arcsec=0.4 * NOMINAL.scale_deg_per_px * 3600.0)
    return CelestialFindingRecord(**{**fields, **changes})


def pose(**changes: Any) -> PoseEstimate:
    fields: dict[str, Any] = {
        "method": "moon_camera_profile_level_head", "beacon": "moon", "center_alt_deg": 33.0, "center_az_deg": 224.0,
        "roll_deg": 0.0, "fov_diagonal_deg": 38.2, "fov_source": "A synthetic camera profile for contract tests",
        "assumptions": ("The head is level.",),
        "swept_wcs": (wcs(scale_deg_per_px=0.0315), wcs(rotation_deg=3.0), wcs(scale_deg_per_px=0.0385)),
        "max_center_offset_deg": 0.4, "beacon_centroid_uncertainty_deg": 0.004,
    }
    return PoseEstimate(**{**fields, **changes})


RECIPES = ("pending", "calibrated", "approximate", "approximate_with_pose", "predicted_only", "unresolved", "unresolved_before_any_solver", "ambiguous")


def dataclass_solution(recipe: str, **changes: Any) -> ObservationSolution:
    """A solution each status allows, built from the dataclasses as a solver builds one."""
    status = {"approximate_with_pose": "approximate", "unresolved_before_any_solver": "unresolved"}.get(recipe, recipe)
    calibrated = status == "calibrated"
    solved = recipe not in ("pending", "unresolved_before_any_solver")
    has_wcs = calibrated or status == "approximate"
    timed = status in ("calibrated", "approximate", "predicted_only")
    findings: list[CelestialFindingRecord] = []
    if solved:
        findings.append(record("observed", placed=calibrated))
    if calibrated:
        findings.append(record("catalog_matched"))
    if timed:
        findings += [record("predicted"), record("simulated")]
    fields: dict[str, Any] = {
        "id": "sol-dataclass", "source_asset_id": "hand-built", "source_sha256": SHA, "frame_time_sec": 0.0, "revision": 1,
        "status": status, "wcs": NOMINAL if has_wcs else None, "quality": quality(calibrated) if solved else None,
        "solver": IDENTITY if solved else None, "observed_at_utc": MOMENT if timed else None,
        "observer_lat": PLACE["latDeg"] if timed else None, "observer_lon": PLACE["lonDeg"] if timed else None,
        "findings": findings, "warnings": ["Hand-built for contract tests."],
        "pose_estimate": pose() if recipe == "approximate_with_pose" else None,
    }
    return ObservationSolution(**{**fields, **changes})


# One field outside the contract each: (the recipe it is tried on, what builds the solution with it)
DATACLASS_FAULTS: dict[str, tuple[str, Any]] = {
    **{f"detection_{name}_{label}": ("unresolved", lambda n=name, v=value: dataclass_solution("unresolved", findings=[record(source_detection=detection(**{n: v}))]))
       for name in ("x_px", "y_px", "flux", "snr", "fwhm_px", "centroid_uncertainty_px", "frame_time_sec")
       for label, value in (("nan", math.nan), ("infinite", math.inf), ("negative", -0.5), ("text", "1"), ("true", True))},
    **{f"detection_{name}_zero": ("unresolved", lambda n=name: dataclass_solution("unresolved", findings=[record(source_detection=detection(**{n: 0.0}))]))
       for name in ("flux", "snr", "centroid_uncertainty_px")},
    "detection_asset_id_empty": ("unresolved", lambda: dataclass_solution("unresolved", findings=[record(source_detection=detection(asset_id=""))])),
    **{f"wcs_{name}_{label}": ("approximate", lambda n=name, v=value: dataclass_solution("approximate", wcs=wcs(**{n: v})))
       for name in ("crval_ra_deg", "crval_dec_deg", "crpix_x", "crpix_y", "scale_deg_per_px", "rotation_deg", "k1", "k2")
       for label, value in (("nan", math.nan), ("infinite", math.inf), ("minus_infinite", -math.inf), ("text", "1"), ("true", True))},
    "wcs_scale_zero": ("approximate", lambda: dataclass_solution("approximate", wcs=wcs(scale_deg_per_px=0.0))),
    "wcs_scale_negative": ("approximate", lambda: dataclass_solution("approximate", wcs=wcs(scale_deg_per_px=-0.035))),
    "wcs_parity_zero": ("approximate", lambda: dataclass_solution("approximate", wcs=wcs(parity=0))),
    "wcs_parity_two": ("approximate", lambda: dataclass_solution("approximate", wcs=wcs(parity=2))),
    **{f"quality_{name}_{label}": ("unresolved", lambda n=name, v=value: dataclass_solution("unresolved", quality=quality(**{n: v})))
       for name in ("detected_candidate_count", "matched_inlier_count")
       for label, value in (("negative", -1), ("fraction", 1.5), ("nan", math.nan), ("true", True), ("text", "1"))},
    **{f"quality_{name}_{label}": ("calibrated", lambda n=name, v=value: dataclass_solution("calibrated", quality=quality(True, **{n: v})))
       for name in ("p95_residual_px", "median_residual_arcsec")
       for label, value in (("nan", math.nan), ("infinite", math.inf), ("negative", -0.5), ("text", "1"), ("true", True))},
    "quality_median_nan": ("calibrated", lambda: dataclass_solution("calibrated", quality=quality(True, median_residual_px=math.nan))),
    "quality_median_negative": ("unresolved", lambda: dataclass_solution(
        "unresolved", quality=quality(held_out_pair_count=3, median_residual_px=-0.5, p95_residual_px=1.0, median_residual_arcsec=9.0))),
    "quality_pairs_negative": ("unresolved", lambda: dataclass_solution("unresolved", quality=quality(held_out_pair_count=-1, held_out_inliers=-1))),
    "quality_passed_one_inlier_short": ("calibrated", lambda: dataclass_solution(
        "calibrated", quality=quality(True, held_out_inliers=contract.held_out_gate()[0] - 1))),
    "quality_passed_median_just_above": ("calibrated", lambda: dataclass_solution(
        "calibrated", quality=quality(True, median_residual_px=math.nextafter(contract.held_out_gate()[1], math.inf)))),
    **{f"solver_{name}_empty": ("unresolved", lambda n=name: dataclass_solution("unresolved", solver=replace(IDENTITY, **{n: ""})))
       for name in ("name", "version", "catalog_release")},
    "solver_name_not_text": ("unresolved", lambda: dataclass_solution("unresolved", solver=replace(IDENTITY, name=5))),
    **{f"finding_{name}_empty": ("unresolved", lambda n=name: dataclass_solution("unresolved", findings=[record(**{n: ""})]))
       for name in ("id", "observation_solution_id", "title", "subtitle")},
    **{f"finding_{name}_{label}": ("predicted_only", lambda n=name, v=value: dataclass_solution("predicted_only", findings=[record("predicted", **{n: v})]))
       for name, out_of_range in (("ra_deg", ()), ("dec_deg", (90.5, -90.5)), ("uncertainty_arcsec", (-0.5,)), ("alt_deg", (90.5, -90.5)), ("az_deg", (-0.5, 360.5)))
       for label, value in (("nan", math.nan), ("infinite", math.inf), ("text", "1"), ("true", True), *((f"at_{v:g}", v) for v in out_of_range))},
    **{f"finding_{name}_{label}": ("calibrated", lambda n=name, v=value: dataclass_solution("calibrated", findings=[record("catalog_matched", **{n: v})]))
       for name in ("pixel_residual_px", "angular_residual_arcsec")
       for label, value in (("nan", math.nan), ("infinite", math.inf), ("negative", -0.5), ("text", "1"), ("true", True))},
    "finding_hash_of_zeros": ("unresolved", lambda: dataclass_solution("unresolved", findings=[record(source_sha256="0" * 64)])),
    "finding_hash_in_capitals": ("unresolved", lambda: dataclass_solution("unresolved", findings=[record(source_sha256=SHA.upper())])),
    "status_unknown": ("unresolved", lambda: dataclass_solution("unresolved", status="Unresolved")),
    "status_not_text": ("unresolved", lambda: dataclass_solution("unresolved", status=None)),
    "hash_of_zeros": ("unresolved", lambda: dataclass_solution("unresolved", source_sha256="0" * 64)),
    "hash_in_capitals": ("unresolved", lambda: dataclass_solution("unresolved", source_sha256=SHA.upper())),
    "hash_too_short": ("unresolved", lambda: dataclass_solution("unresolved", source_sha256=SHA[:-1])),
    "hash_not_text": ("unresolved", lambda: dataclass_solution("unresolved", source_sha256=5)),
    "hash_missing": ("unresolved", lambda: dataclass_solution("unresolved", source_sha256=None)),
    "finding_hash_not_text": ("unresolved", lambda: dataclass_solution("unresolved", findings=[record(source_sha256=5)])),
    # A whole number no float can hold, which JavaScript reads as Infinity
    "revision_beyond_any_float": ("unresolved", lambda: dataclass_solution("unresolved", revision=contract.BEYOND_ANY_FLOAT)),
    "frame_time_beyond_any_float": ("unresolved", lambda: dataclass_solution("unresolved", frame_time_sec=contract.BEYOND_ANY_FLOAT)),
    "quality_count_beyond_any_float": ("unresolved", lambda: dataclass_solution(
        "unresolved", quality=quality(detected_candidate_count=contract.BEYOND_ANY_FLOAT))),
    "id_empty": ("unresolved", lambda: dataclass_solution("unresolved", id="")),
    "source_asset_id_empty": ("unresolved", lambda: dataclass_solution("unresolved", source_asset_id="")),
    **{f"frame_time_{label}": ("unresolved", lambda v=value: dataclass_solution("unresolved", frame_time_sec=v))
       for label, value in (("nan", math.nan), ("infinite", math.inf), ("negative", -0.5), ("text", "1"), ("true", True))},
    **{f"revision_{label}": ("unresolved", lambda v=value: dataclass_solution("unresolved", revision=v))
       for label, value in (("zero", 0), ("negative", -1), ("fraction", 1.5), ("nan", math.nan), ("true", True), ("text", "1"))},
    **{f"observer_{name}_{label}": ("predicted_only", lambda n=name, v=value: dataclass_solution("predicted_only", **{n: v}))
       for name, beyond in (("observer_lat", 90.5), ("observer_lon", 180.5))
       for label, value in (("high", beyond), ("low", -beyond), ("nan", math.nan), ("infinite", math.inf), ("text", "1"), ("true", True))},
    "observed_at_not_text": ("predicted_only", lambda: dataclass_solution("predicted_only", observed_at_utc=20300101)),
    "warning_not_text": ("unresolved", lambda: dataclass_solution("unresolved", warnings=["fine", 5])),
    "swept_wcs_of_another_width": ("approximate_with_pose", lambda: dataclass_solution(
        "approximate_with_pose", pose_estimate=pose(swept_wcs=(wcs(scale_deg_per_px=0.0315), wcs(width=WIDTH + 1))))),
    "swept_wcs_of_another_height": ("approximate_with_pose", lambda: dataclass_solution(
        "approximate_with_pose", pose_estimate=pose(swept_wcs=(wcs(height=HEIGHT // 2), wcs(scale_deg_per_px=0.0385))))),
    "swept_wcs_not_a_sequence": ("approximate_with_pose", lambda: dataclass_solution("approximate_with_pose", pose_estimate=pose(swept_wcs=None))),
    "swept_wcs_a_number": ("approximate_with_pose", lambda: dataclass_solution("approximate_with_pose", pose_estimate=pose(swept_wcs=3))),
}


@pytest.mark.parametrize("recipe", RECIPES)
def test_a_solution_the_dataclasses_build_is_one_the_parser_accepts(recipe: str) -> None:
    payload = dataclass_solution(recipe).to_contract_dict()
    assert contract.parse_observation_solution(payload).to_contract_dict() == payload
    assert payload["status"] in contract.SOLUTION_STATUSES


@pytest.mark.parametrize("fault", sorted(DATACLASS_FAULTS))
def test_the_dataclasses_refuse_at_construction_what_the_contract_refuses(fault: str) -> None:
    recipe, build = DATACLASS_FAULTS[fault]
    dataclass_solution(recipe)
    # The records' own refusal, never another error on the way: nothing here writes the solution
    with pytest.raises(ContractRefusal):
        build()


RECORD_RESIDUAL_PATHS = {"pixel_residual_px": "evidence.pixelResidualPx", "angular_residual_arcsec": "evidence.angularResidualArcsec"}


def class_refusal(evidence_class: str, kind: str, path: str) -> Callable[[], str]:
    """The whole refusal a record gives for one cell of the class table: the cell's sentence, then the record's id.
    Read inside the test, from the fixture's table, never while this module is built: a cell the Python literal has
    moved or lost then leaves the module whole, and the test that holds the literal to the fixture names that cell."""
    return lambda: f"{read_fixture()['findingClassEvidence'][evidence_class][kind][path][:-1]} (finding-{evidence_class})."


# One record at a time, built alone (no solution holds it and nothing writes it, but for the detection whose write is
# its own rule): each fault is refused by that record, in the sentence that names its cause. A guard of one record
# that a later guard or the parser would also refuse is still the one that answers
SKY_FREE = {"ra_deg": None, "dec_deg": None, "uncertainty_arcsec": None, "alt_deg": None, "az_deg": None}
PASSED_FIELDS = {"detected_candidate_count": 12, "matched_inlier_count": 9}
RESIDUALS = {"median_residual_px": 0.5, "p95_residual_px": 1.0, "median_residual_arcsec": 9.0}
RECORD_FAULTS: dict[str, tuple[Any, str | Callable[[], str]]] = {
    "detection_written_naming_no_recording": (lambda: detection().to_contract_dict(), "names no recording and none was given"),
    "detection_hash_with_a_trailing_line_end": (lambda: record(source_sha256=SHA + "\n"), "source_sha256 must be"),
    "solution_hash_with_a_trailing_line_end": (lambda: dataclass_solution("unresolved", source_sha256=SHA + "\n"), "source_sha256 must be"),
    "residuals_with_no_pair": (lambda: quality(held_out_pair_count=0, held_out_inliers=0, **RESIDUALS), "reported exactly when a held-out star was paired"),
    "pairs_with_no_residuals": (lambda: quality(held_out_pair_count=3, held_out_inliers=0), "reported exactly when a held-out star was paired"),
    **{f"pairs_without_{name}": (lambda n=name: quality(held_out_pair_count=3, held_out_inliers=0, **{**RESIDUALS, n: None}),
                                 "reported exactly when a held-out star was paired")
       for name in RESIDUALS},
    "more_inliers_than_pairs": (lambda: quality(held_out_pair_count=3, held_out_inliers=4, **RESIDUALS), "there cannot be more of them"),
    "passed_without_residuals": (lambda: SolutionQuality(**PASSED_FIELDS, held_out_pair_count=0, held_out_inliers=0, median_residual_px=None,
                                                         p95_residual_px=None, median_residual_arcsec=None, passed_independent_verification=True),
                                 "passes only with the held-out residuals it was judged on"),
    "finding_of_an_unknown_evidence_class": (lambda: replace(record("observed"), evidence_class="measured"), "has unknown evidence class 'measured'"),
    "finding_on_a_star_and_a_deep_sky_row": (lambda: replace(record("catalog_matched"), deep_sky_reference={"id": "deep-sky row"}),
                                             "stands on one catalog row: a star or a deep-sky object, not both"),
    "finding_with_no_limitation": (lambda: record(limitations=()), "requires at least one non-empty limitation"),
    "finding_with_a_blank_limitation": (lambda: record(limitations=("Generated.", "  ")), "requires at least one non-empty limitation"),
    "finding_with_a_limitation_not_in_words": (lambda: record(limitations=(5,)), "requires at least one non-empty limitation"),
    "finding_with_no_uncertainty": (lambda: record("predicted", uncertainty_arcsec=None), "has a partial sky position"),
    "finding_with_alt_az_and_no_sky": (lambda: record("observed", alt_deg=41.5, az_deg=200.25), "has a partial sky position"),
    **{f"{evidence_class}_finding_off_the_sky": (lambda c=evidence_class: record(c, source_detection=None, **SKY_FREE),
                                                 f"A {evidence_class} finding needs a sky position")
       for evidence_class in ("catalog_matched", "predicted", "simulated")},
    "catalog_matched_finding_off_the_sky_with_its_detection": (lambda: record("catalog_matched", **SKY_FREE), "A catalog_matched finding needs a sky position"),
    **{f"{evidence_class}_finding_with_a_detection": (lambda c=evidence_class: record(c, source_detection=DETECTION),
                                                      f"A {evidence_class} finding was not measured in the pixels")
       for evidence_class in ("predicted", "simulated")},
    # The class table among the fields a record holds (contract.FINDING_CLASS_EVIDENCE), placed or not, each refused in
    # the whole sentence of its cell and the record's id
    "observed_finding_with_no_sky_and_no_detection": (lambda: record("observed", source_detection=None),
                                                      class_refusal("observed", "must", "sourceDetection")),
    "observed_finding_on_the_sky_with_no_detection": (lambda: record("observed", placed=True, source_detection=None),
                                                      class_refusal("observed", "must", "sourceDetection")),
    "observed_finding_on_a_catalog_star": (lambda: record("observed", placed=True, catalog_star=STAR),
                                           class_refusal("observed", "mustNot", "catalogRef")),
    "observed_finding_on_a_deep_sky_row": (lambda: record("observed", deep_sky_reference={"id": "deep-sky row"}),
                                           class_refusal("observed", "mustNot", "catalogRef")),
    "catalog_match_with_no_detection": (lambda: record("catalog_matched", source_detection=None),
                                        class_refusal("catalog_matched", "must", "sourceDetection")),
    "catalog_match_on_no_catalog_row": (lambda: record("catalog_matched", catalog_star=None),
                                        class_refusal("catalog_matched", "must", "catalogRef")),
    # The lead's rulings of 2026-11-12, among the fields a record holds
    **{f"catalog_match_without_its_{name}": (lambda n=name: record("catalog_matched", **{n: None}),
                                             class_refusal("catalog_matched", "must", path))
       for name, path in RECORD_RESIDUAL_PATHS.items()},
    **{f"{evidence_class}_finding_with_a_{name}": (lambda c=evidence_class, n=name: record(c, **{n: 0.4}),
                                                   class_refusal(evidence_class, "mustNot", path))
       for evidence_class in ("predicted", "simulated") for name, path in RECORD_RESIDUAL_PATHS.items()},
    **{f"{evidence_class}_finding_verified_by_held_out_stars": (
        lambda c=evidence_class: record(c, verified_by_holdout=True),
        class_refusal(evidence_class, "mustNot", "evidence.verifiedByIndependentHoldout"))
       for evidence_class in ("observed", "predicted", "simulated")},
    # The pose fit's residual on an observed finding outside an approximate solution (the lead's ruling on the Moon)
    **{f"observed_fit_residual_in_a_{recipe}_solution": (
        lambda r=recipe: dataclass_solution(r, findings=[record("observed", placed=r == "calibrated", pixel_residual_px=1.5, angular_residual_arcsec=189.0),
                                                         *([record("catalog_matched")] if r == "calibrated" else [])]),
        f"{contract.OBSERVED_RESIDUAL_OF_A_POSE[:-1]} (finding-observed, a {recipe} solution).")
       for recipe in ("calibrated", "unresolved", "ambiguous")},
    # Either residual alone is one too
    **{f"observed_fit_{name}_alone_in_a_unresolved_solution": (
        lambda n=name: dataclass_solution("unresolved", findings=[record("observed", **{n: 1.5})]),
        f"{contract.OBSERVED_RESIDUAL_OF_A_POSE[:-1]} (finding-observed, a unresolved solution).")
       for name in RECORD_RESIDUAL_PATHS},
    "pose_of_an_unknown_method": (lambda: pose(method="moon_by_eye"), "A pose estimate's method is one of"),
    "pose_of_an_unknown_beacon": (lambda: pose(beacon="jupiter"), "A pose estimate's beacon is the moon or the sun"),
    "pose_of_its_method_s_other_beacon": (lambda: pose(beacon="sun"), "the one its method lines up from"),
    **{f"pose_{name}_{label}": (lambda n=name, v=value: pose(**{n: v}), f"A pose estimate's {name} lies in")
       for name in ("center_alt_deg", "center_az_deg", "roll_deg") for label, value in (("nan", math.nan), ("true", True), ("text", "1"))},
    **{f"pose_fov_diagonal_deg_{label}": (lambda v=value: pose(fov_diagonal_deg=v), "fov_diagonal_deg is positive and at most 175")
       for label, value in (("nan", math.nan), ("true", True), ("text", "1"))},
    **{f"pose_{name}_{label}": (lambda n=name, v=value: pose(**{n: v}), f"A pose estimate's {name} is finite and not negative")
       for name in ("max_center_offset_deg", "beacon_centroid_uncertainty_deg")
       for label, value in (("negative", -0.5), ("nan", math.nan), ("infinite", math.inf), ("true", True), ("text", "1"))},
    "pose_fov_source_empty": (lambda: pose(fov_source=""), "names where its field of view comes from"),
    "pose_fov_source_not_text": (lambda: pose(fov_source=5), "names where its field of view comes from"),
    "pose_assumptions_one_text": (lambda: pose(assumptions="The head is level."), "assumptions are a list or tuple of sentences (got str)"),
    "pose_assumptions_a_set": (lambda: pose(assumptions={"The head is level."}), "assumptions are a list or tuple of sentences (got set)"),
    "pose_assumptions_none": (lambda: pose(assumptions=()), "states at least one assumption, each in words"),
    "pose_assumption_empty": (lambda: pose(assumptions=("The head is level.", "")), "states at least one assumption, each in words"),
    "pose_assumption_not_text": (lambda: pose(assumptions=(5,)), "states at least one assumption, each in words"),
    "pose_swept_wcs_not_a_wcs": (lambda: pose(swept_wcs=(NOMINAL, "wcs")), "Each swept WCS of a pose estimate is a WCSModel."),
    # The solution's own rules between its fields, each broken alone
    "pose_on_a_predicted_only_solution": (lambda: dataclass_solution("predicted_only", pose_estimate=pose()),
                                          "A pose estimate belongs only to an approximate solution with a WCS"),
    "pose_on_an_approximate_solution_without_a_wcs": (lambda: dataclass_solution("approximate_with_pose", wcs=None),
                                                      "A pose estimate belongs only to an approximate solution with a WCS"),
    "pose_sweeping_another_centre_pixel": (lambda: dataclass_solution("approximate_with_pose", pose_estimate=pose(swept_wcs=(NOMINAL, wcs(crpix_y=270.5)))),
                                           "has the nominal WCS's crpixX and crpixY"),
    "pending_with_a_quality": (lambda: dataclass_solution("pending", quality=quality()), "A pending solution has no quality"),
    "pending_naming_a_solver": (lambda: dataclass_solution("pending", solver=IDENTITY), "A pending solution names no solver: none has run."),
    **{f"{recipe}_naming_no_solver": (lambda r=recipe: dataclass_solution(r, solver=None),
                                      "Only a pending solution, or an unresolved one no solver reached, names no solver.")
       for recipe in ("calibrated", "approximate", "predicted_only", "ambiguous")},
    **{f"{recipe}_with_no_quality": (lambda r=recipe: dataclass_solution(r, quality=None),
                                     "A calibrated or approximate solution reports the quality it was judged on.")
       for recipe in ("calibrated", "approximate")},
    "latitude_without_longitude": (lambda: dataclass_solution("predicted_only", observer_lon=None), "observer_lat and observer_lon must both be provided"),
    "longitude_without_latitude": (lambda: dataclass_solution("unresolved", observer_lon=-40.0), "observer_lat and observer_lon must both be provided"),
    **{f"{recipe}_without_{name}": (lambda r=recipe, n=name: dataclass_solution(r, **({n: None} if n == "observed_at_utc" else {"observer_lat": None, "observer_lon": None})),
                                    "needs observed_at_utc, observer_lat and observer_lon")
       for recipe in ("approximate", "predicted_only") for name in ("observed_at_utc", "place")},
    "unresolved_with_a_predicted_finding_and_no_time": (lambda: dataclass_solution("unresolved", findings=[record("observed"), record("predicted")]),
                                                        "needs observed_at_utc, observer_lat and observer_lon"),
    "passed_without_a_wcs": (lambda: dataclass_solution("calibrated", wcs=None), "Held-out verification passes only with a WCS."),
    **{f"passed_on_an_{recipe}_solution": (lambda r=recipe: dataclass_solution(r, quality=quality(True)),
                                           f"Only a calibrated solution passes held-out verification, not a {recipe} one")
       for recipe in ("approximate",)},
    "passed_on_an_ambiguous_solution_with_a_wcs": (lambda: dataclass_solution("ambiguous", wcs=NOMINAL, quality=quality(True)),
                                                   "Only a calibrated solution passes held-out verification, not a ambiguous one"),
    "detection_measured_in_another_recording": (lambda: dataclass_solution("unresolved", findings=[record(source_detection=detection(asset_id="another"))]),
                                                "detection was measured in another, not in this solution's recording hand-built"),
    "catalog_match_without_a_verified_wcs": (lambda: dataclass_solution("ambiguous", findings=[record("observed"), record("catalog_matched")]),
                                             "needs a verified WCS: catalog association follows held-out verification"),
    "observed_on_the_sky_without_a_verified_wcs": (lambda: dataclass_solution("unresolved", findings=[record("observed", placed=True)]),
                                                   "has a sky position without a verified WCS"),
    "observed_off_the_sky_with_a_verified_wcs": (lambda: dataclass_solution("calibrated", findings=[record("observed"), record("catalog_matched")]),
                                                 "lacks the sky position the verified WCS gives it"),
}
# The bounds a record holds on its own, beyond the contract's field rules: a value at each inside end is built and
# one just outside is refused, from one table: (what builds the record from the value, inside ends, just outside,
# the refusal's sentence)
RECORD_BOUNDS: dict[str, tuple[Any, tuple[Any, ...], tuple[Any, ...], str]] = {
    **{f"wcs_{name}": (lambda v, n=name: wcs(**{n: v}), (1, 2**53 - 1), (0, -1), "width and height must be positive integers")
       for name in ("width", "height")},
    **{f"wcs_{name}": (lambda v, n=name: wcs(**{n: v}), (-0.25, 0.25), (math.nextafter(-0.25, -math.inf), math.nextafter(0.25, math.inf)),
                       "radial distortion coefficients k1 and k2 must lie in")
       for name in ("k1", "k2")},
    "quality_inliers_of_three_pairs": (lambda v: quality(held_out_pair_count=3, held_out_inliers=v, **RESIDUALS), (0, 3), (4,),
                                       "there cannot be more of them"),
    **{f"pose_{name}": (lambda v, n=name: pose(**{n: v}), (lo, hi), (math.nextafter(lo, -math.inf), math.nextafter(hi, math.inf)), f"A pose estimate's {name} lies in")
       for name, lo, hi in (("center_alt_deg", -90.0, 90.0), ("center_az_deg", 0.0, 360.0), ("roll_deg", -180.0, 180.0))},
    "pose_fov_diagonal_deg": (lambda v: pose(fov_diagonal_deg=v), (math.nextafter(0.0, math.inf), 175.0), (0.0, math.nextafter(175.0, math.inf)),
                              "fov_diagonal_deg is positive and at most 175"),
    **{f"pose_{name}": (lambda v, n=name: pose(**{n: v}), (0.0, math.nextafter(math.inf, 0.0)), (math.nextafter(0.0, -math.inf), math.inf), f"A pose estimate's {name} is finite and not negative")
       for name in ("max_center_offset_deg", "beacon_centroid_uncertainty_deg")},
    "pose_swept_wcs_count": (lambda v: pose(swept_wcs=(NOMINAL,) * v), (contract.MIN_SWEPT_WCS, contract.MAX_SWEPT_WCS),
                             (contract.MIN_SWEPT_WCS - 1, contract.MAX_SWEPT_WCS + 1), "A pose estimate sweeps 2 to 27 WCS"),
}


@pytest.mark.parametrize("fault", sorted(RECORD_FAULTS))
def test_each_record_refuses_its_own_fault_in_the_sentence_that_names_it(fault: str) -> None:
    build, sentence = RECORD_FAULTS[fault]
    with pytest.raises(ContractRefusal, match=re.escape(sentence() if callable(sentence) else sentence)):
        build()


@pytest.mark.parametrize("bound", sorted(RECORD_BOUNDS))
def test_each_bound_a_record_holds_takes_its_inside_ends_and_refuses_just_outside(bound: str) -> None:
    build, inside, outside, sentence = RECORD_BOUNDS[bound]
    for value in inside:
        build(value)
    for value in outside:
        with pytest.raises(ContractRefusal, match=re.escape(sentence)):
            build(value)


def test_the_cases_beside_each_record_fault_are_built() -> None:
    """What each refusal above leaves alone: a hash without a line end, the residuals with a pair, every evidence class,
    one limitation, a full sky position, an observed finding located by its detection alone, each method's own beacon."""
    require_source_sha256(SHA)
    quality(held_out_pair_count=3, held_out_inliers=3, **RESIDUALS)
    quality(held_out_pair_count=0, held_out_inliers=0)
    for evidence_class in ("observed", "catalog_matched", "predicted", "simulated"):
        record(evidence_class, limitations=("One limitation.",))
    record("observed", placed=True)
    assert record("observed").source_detection is DETECTION and not record("observed").has_sky_position
    for method, beacon in contract.POSE_METHOD_BEACONS.items():
        assert pose(method=method, beacon=beacon).beacon == beacon
    assert detection(asset_id="src-1").to_contract_dict()["assetId"] == "src-1"
    assert detection().to_contract_dict(asset_id="src-2")["assetId"] == "src-2"
    # The pose fit's residual on an observed finding, in an approximate solution as the horizon solver writes it
    fit = dataclass_solution("approximate", findings=[record("observed", pixel_residual_px=1.5, angular_residual_arcsec=189.0)])
    assert fit.to_contract_dict()["findings"][0]["evidence"]["pixelResidualPx"] == 1.5


@settings(max_examples=200, deadline=None)
@given(recipe=st.sampled_from(RECIPES), fault=st.one_of(st.none(), st.sampled_from(sorted(DATACLASS_FAULTS))))
@example(recipe="calibrated", fault=None)
@example(recipe="approximate_with_pose", fault=None)
@example(recipe="calibrated", fault="quality_passed_one_inlier_short")
@example(recipe="approximate", fault="wcs_scale_deg_per_px_nan")
@example(recipe="unresolved", fault="detection_x_px_negative")
@example(recipe="approximate_with_pose", fault="swept_wcs_of_another_width")
def test_pbt_contract_4_what_the_dataclasses_build_the_parser_accepts(recipe: str, fault: str | None) -> None:
    """P-CONTRACT-4. Built without a fault, a solution serializes, parses and comes back unchanged; with one field
    outside the contract, it is refused when it is built, never later."""
    if fault is not None:
        with pytest.raises(ContractRefusal):
            DATACLASS_FAULTS[fault][1]()
        return
    solution = dataclass_solution(recipe)
    payload = solution.to_contract_dict()
    parsed = contract.parse_observation_solution(payload)
    assert parsed.to_contract_dict() == payload
    assert parsed.has_verified_wcs == solution.has_verified_wcs
    assert json.loads(json.dumps(payload, allow_nan=False)) == payload


def test_the_dataclasses_take_the_numbers_a_solver_hands_them() -> None:
    """NumPy numbers and the edges of each range are inside the contract, as a solver computing with NumPy sends them."""
    assert detection(x_px=np.float32(0.0), y_px=np.float64(0.0), fwhm_px=0, snr=np.float64(4.75)).to_contract_dict(asset_id="a")["xPx"] == 0.0
    assert wcs(crval_ra_deg=-10.0).to_contract_dict()["crvalRaDeg"] == 350.0
    assert wcs(crval_dec_deg=90.0, k1=0.25, k2=-0.25, parity=1, rotation_deg=-725.0).to_contract_dict()["parity"] == 1
    edge = quality(True, detected_candidate_count=np.int64(12), held_out_inliers=contract.held_out_gate()[0],
                   median_residual_px=contract.held_out_gate()[1])
    assert edge.to_contract_dict()["passedIndependentVerification"] is True
    assert record("predicted", dec_deg=-90.0, alt_deg=90.0, az_deg=360.0, uncertainty_arcsec=0).to_contract_dict()["skyPosition"]["azDeg"] == 360.0
    assert dataclass_solution("predicted_only", observer_lat=-90.0, observer_lon=180.0, revision=np.int64(3)).to_contract_dict()["revision"] == 3


def test_every_rule_of_the_parser_is_frozen_and_the_base_rule_takes_nothing() -> None:
    """The parser, the records and both sides' fixture read the same rule objects, so none may change once imported:
    a test that moved a bound would move it for every test after it. A rule class with no check of its own would
    take every value, so the base rule's check answers nothing at all."""
    rules = [value for value in vars(contract).values() if isinstance(value, contract._Rule)]
    assert {type(rule) for rule in rules} == {contract.NumberRule, contract.TextRule, contract.ChoiceRule, contract.FlagRule}
    for rule in rules:
        with pytest.raises(FrozenInstanceError):
            rule.name = "moved"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        contract.Items(least=1).least = 0  # type: ignore[misc]
    with pytest.raises(NotImplementedError):
        contract._Rule().problem(0)


def test_a_pattern_rule_refuses_a_text_it_takes_with_a_line_break_before_or_after_it() -> None:
    """Under a match from the start, a pattern's $ would also take a line break at its end. Each text rule with a
    pattern takes the texts it lists and refuses each of them with a line break before or after it, as Zod's anchors
    do (the Vitest file tries the same through each field that follows such a rule)."""
    patterned = [value for value in vars(contract).values() if isinstance(value, contract.TextRule) and value.pattern is not None]
    assert {rule.name for rule in patterned} == {"sha256", "utcInstant"}
    for rule in patterned:
        assert rule.accept, rule.name
        for text in rule.accept:
            assert rule.problem(text) is None, (rule.name, text)
            for broken in (text + "\n", "\n" + text, text + "\r\n"):
                assert rule.problem(broken) is not None, (rule.name, broken)


def test_a_parsed_record_cannot_be_changed() -> None:
    """What the parser gives back is the record it judged: a field set afterwards would stand unjudged."""
    parsed = contract.parse_observation_solution(read_fixture()["valid"]["calibrated"])
    with pytest.raises(ValidationError, match="frozen"):
        parsed.status = "unresolved"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="frozen"):
        parsed.quality.passed_independent_verification = False  # type: ignore[union-attr,misc]


def test_the_field_table_refuses_what_it_cannot_describe(monkeypatch: pytest.MonkeyPatch) -> None:
    """A field of a type the table has no entry for, and two different rules under one name, are refused when the
    table is made, rather than left out of the fixture both sides read or read as one rule."""
    with pytest.raises(TypeError, match="No table entry for"):
        contract._described(complex)
    one, other = contract.NumberRule("twice"), contract.NumberRule("twice", ge=0.0)
    monkeypatch.setattr(contract, "_fields", lambda schema: {"a": ({"rule": "twice"}, one), "b": ({"rule": "twice"}, other)})
    with pytest.raises(ValueError, match="Two rules are named twice."):
        contract.field_table()


def test_a_field_that_holds_an_object_has_no_rule_of_its_own() -> None:
    assert isinstance(contract.field_rule("WCSCalibrationModel", "scaleDegPerPx"), contract.NumberRule)
    for schema, wire_name in (("ObservationSolution", "quality"), ("ObservationSolution", "findings"), ("PoseEstimate", "sweptWcs")):
        with pytest.raises(KeyError, match="holds an object, not a value with a rule of its own"):
            contract.field_rule(schema, wire_name)


def test_a_numpy_number_is_a_number_to_a_record_and_not_to_the_parser() -> None:
    """A JSON document holds no NumPy number, so a rule asked as the parser asks it takes Python's own int and float
    only; a record asks with `any_real`, since a solver computes with NumPy. A boolean is a number to neither."""
    for value in (np.float32(1.5), np.int64(3), np.uint8(7)):
        assert contract.NUMBER.problem(value) == "This value is not a number.", type(value)
        assert contract.NUMBER.problem(value, any_real=True) is None, type(value)
    assert contract.COUNT.problem(np.int64(3)) == "This value is not a number." and contract.COUNT.problem(np.int64(3), any_real=True) is None
    for value in (True, np.bool_(True)):
        assert contract.NUMBER.problem(value, any_real=True) == "This value is not a number.", type(value)
    assert contract.NUMBER.problem(1.5) is None and contract.NUMBER.problem(3) is None


def test_a_detection_names_its_recording_in_words_or_not_at_all() -> None:
    """Built on its own, a detection may name no recording (its solution's id is written for it), but never an empty
    or a wordless one: the contract's assetId is text that is not empty."""
    assert detection().asset_id is None
    assert detection(asset_id="src-1").to_contract_dict()["assetId"] == "src-1"
    for not_a_name in ("", 5, b"src-1", ["src-1"]):
        with pytest.raises(ValueError, match="asset_id"):
            detection(asset_id=not_a_name)


def test_a_number_json_can_write_and_no_float_can_hold_is_refused() -> None:
    """A 401-digit whole number is valid JSON; JavaScript reads it as Infinity, so the contract refuses it wherever
    a number goes, and the parser does too rather than keep an integer no reader of the JSON would see."""
    gate = read_fixture()["valid"]["calibrated_at_the_gate"]
    huge = json.loads(json.dumps(contract.BEYOND_ANY_FLOAT))
    for where in (["revision"], ["frameTimeSec"], ["quality", "detectedCandidateCount"], ["wcs", "crpixX"], ["wcs", "parity"]):
        error = refusal(patched(gate, [put(where, huge)]))
        assert error is not None and contract.issue_paths(error) == [dotted(where)], where


def test_a_swept_wcs_has_the_nominal_width_and_height() -> None:
    with pytest.raises(ValueError, match="width and height"):
        DATACLASS_FAULTS["swept_wcs_of_another_width"][1]()
    with pytest.raises(ValueError, match="width and height"):
        DATACLASS_FAULTS["swept_wcs_of_another_height"][1]()
    for not_a_sequence in (None, 3, "wcs", {"wcs": NOMINAL}):
        with pytest.raises(ValueError, match="pose estimate"):
            pose(swept_wcs=not_a_sequence)


def test_a_wcs_centred_past_a_pole_is_built_and_refused_only_where_it_is_written() -> None:
    """A fit works on WCS records whose centre it is free to move, and a centre moved past a pole is the same
    pointing written another way (at RA + 180, turned half a turn), which the fit brings back before it writes. So
    the record takes any finite declination when it is built, as it takes any finite right ascension, and the
    contract's range is judged where a solution is written: there a centre off the sphere is refused, at its field."""
    for declination in (90.5, 95.0, 179.0, -90.5, -150.0, 1e6):
        past = wcs(crval_dec_deg=declination, crval_ra_deg=300.0, rotation_deg=170.0)
        assert past.crval_dec_deg == declination
        # Any swept WCS and the nominal one are judged alike when the solution is written
        for built, where in (
            (dataclass_solution("approximate", wcs=past), "wcs.crvalDecDeg"),
            (dataclass_solution("approximate_with_pose", pose_estimate=pose(swept_wcs=(NOMINAL, past, NOMINAL))), "poseEstimate.sweptWcs.1.crvalDecDeg"),
        ):
            with pytest.raises(ValidationError) as refused:
                built.to_contract_dict()
            assert where in contract.issue_paths(refused.value), declination
            assert str(declination) not in str(refused.value)
    # Brought back inside the range, as the same pointing, it is written
    back = wcs(crval_dec_deg=85.0, crval_ra_deg=120.0, rotation_deg=-10.0)
    written = dataclass_solution("approximate", wcs=back).to_contract_dict()
    assert written["wcs"]["crvalDecDeg"] == 85.0 and refusal(written) is None
    # What is no finite number is no pointing at all, and is refused when the record is built
    for declination in (math.nan, math.inf, -math.inf):
        with pytest.raises(ContractRefusal, match="crval_dec_deg"):
            wcs(crval_dec_deg=declination)


def test_a_capture_time_is_written_in_the_contracts_form_or_names_the_same_instant() -> None:
    """`contract_utc_instant` gives a capture time in the contract's form: one already in it as written, one with an
    offset as the same instant in UTC, and one that names no instant unchanged, for the parser to refuse."""
    for as_written in ("2030-01-01T02:00:00Z", "2030-01-01T02:00Z", "2030-01-01T02:00:00.250Z", "2030-01-01T02:00:00.123456789Z"):
        assert contract_utc_instant(as_written) == as_written
    assert contract_utc_instant("2030-01-01T02:00:00+00:00") == "2030-01-01T02:00:00Z"
    assert contract_utc_instant("2030-01-01T04:00:00+02:00") == "2030-01-01T02:00:00Z"
    assert contract_utc_instant("2029-12-31T19:00:00.250000-07:00") == "2030-01-01T02:00:00.25Z"
    assert contract_utc_instant("0999-06-01T00:00:00.000001+00:00") == "0999-06-01T00:00:00.000001Z"
    # No zone, not a time, or an instant outside years 1 to 9999: nothing to rewrite
    for no_instant in ("2030-01-01T02:00:00", "2030-01-01", "soon", "", AFTER_YEAR_9999, "2031-02-30T00:00:00+00:00"):
        assert contract_utc_instant(no_instant) == no_instant
        assert contract.UTC_INSTANT.problem(no_instant) is not None


def test_a_solution_is_always_written_with_its_capture_time_in_the_contracts_form() -> None:
    """The phone photo benchmark hands the solvers a time ending in +00:00 and the real clip baseline one with the
    clip's own offset (research/benchmarks/phone_photos), so the dataclass still takes them when it is built. What
    is written is always the contract's form: a time ending in Z as it was given, a time with an offset as the same
    instant in UTC, so no clip's local offset (a hint of where it was taken) is stored. A time that names no instant
    is refused where the solution is written, and the refusal names the field, never the time."""
    for moment, written in (
        ("2030-01-01T02:00:00Z", "2030-01-01T02:00:00Z"),
        ("2030-01-01T02:00:00.250Z", "2030-01-01T02:00:00.250Z"),
        ("2030-01-01T02:00:00+00:00", "2030-01-01T02:00:00Z"),
        ("2029-12-31T19:00:00-07:00", "2030-01-01T02:00:00Z"),
        ("2030-01-01T07:30:00.5+05:30", "2030-01-01T02:00:00.5Z"),
    ):
        built = dataclass_solution("predicted_only", observed_at_utc=moment)
        assert built.observed_at_utc == moment
        payload = built.to_contract_dict()
        assert payload["observedAtUTC"] == written
        assert contract.parse_observation_solution(payload).to_contract_dict() == payload
    for moment in ("2030-01-01T02:00:00", "2030-01-01 02:00:00", "2030-01-01", "soon", "", "2031-02-30T00:00:00Z"):
        built = dataclass_solution("predicted_only", observed_at_utc=moment)
        with pytest.raises(ValueError, match="observedAtUTC") as refused:
            built.to_contract_dict()
        assert moment == "" or moment not in str(refused.value)


OFFSETS = st.integers(min_value=-840, max_value=840).map(lambda minutes: timezone(timedelta(minutes=minutes)))


@settings(max_examples=300, deadline=None)
@given(moment=st.datetimes(min_value=datetime(1, 1, 2), max_value=datetime(9999, 12, 30), timezones=OFFSETS))
@example(moment=datetime(2030, 1, 1, 2, 0, 0, tzinfo=timezone.utc))
@example(moment=datetime(2030, 1, 1, 2, 0, 0, 250000, tzinfo=timezone(timedelta(minutes=-420))))
@example(moment=datetime(2032, 2, 29, 23, 59, 59, 999999, tzinfo=timezone(timedelta(minutes=840))))
@example(moment=datetime(999, 6, 1, 0, 0, 0, 1, tzinfo=timezone(timedelta(minutes=-1))))
def test_pbt_contract_5_a_time_with_an_offset_is_rewritten_as_the_same_instant(moment: datetime) -> None:
    """P-CONTRACT-5. Whatever offset a capture time is written with, its contract form is a UTC instant the
    contract takes, and it is the same instant."""
    written = contract_utc_instant(moment.isoformat())
    assert contract.UTC_INSTANT.problem(written) is None
    assert datetime.fromisoformat(written[:-1] + "+00:00") == moment
    assert contract_utc_instant(written) == written


def test_a_refusal_never_holds_the_place() -> None:
    with pytest.raises(ValueError) as refused:
        dataclass_solution("predicted_only", observer_lat=123.456, observer_lon=-40.0)
    assert "123.456" not in str(refused.value) and "observer_lat" in str(refused.value)


# =============================================================================================
# A time a client sends to the science routes (services/science/router.py)
# =============================================================================================


def _drawn_frame() -> list[list[float]]:
    """A small frame drawn here: dark sky, lit ground and one bright block."""
    luma = np.zeros((180, 240), dtype=np.float64)
    luma[100:, :] = 40.0
    luma[20:26, 150:156] = 240.0
    return luma.tolist()


# A source asset's id as the upload route mints it: src_ and the first 16 hex digits of the file's SHA-256
ROUTE_SOURCE = {"sourceAssetId": f"src_{SHA[:16]}", "sourceSha256": SHA}
# The routes that compute for a moment, and the two that write a solution
MOMENT_ROUTES: dict[str, dict[str, Any]] = {
    "/api/science/sky-context": dict(PLACE),
    "/api/science/radiometry-audit": {**PLACE, "targetSepDeg": 45.0, "targetAltDeg": 60.0},
    "/api/science/ephemeris": dict(PLACE),
}
SOLUTION_ROUTES: dict[str, dict[str, Any]] = {
    "/api/science/solve": {
        **PLACE, **ROUTE_SOURCE, "width": WIDTH, "height": HEIGHT,
        "detections": [
            {"xPx": x, "yPx": y, "flux": 4200.0, "snr": 18.0, "fwhmPx": 2.4, "centroidUncertaintyPx": 0.12}
            for x, y in ((120.0, 80.0), (480.5, 260.25), (800.0, 410.0), (300.0, 444.0))
        ],
    },
    "/api/science/solve-horizon": {**PLACE, **ROUTE_SOURCE, "image_luma": _drawn_frame()},
}
# The last one is a real time whose UTC instant falls before year 1, which no date type here holds
NOT_A_TIME = (
    "soon", "", "2031-02-30T02:00:00Z", "2030-13-01T02:00:00Z", "T02:00:00Z", "0000-01-01T02:00:00Z",
    BEFORE_YEAR_1,
)
NAMES_NO_INSTANT = ("2030-01-01T02:00:00", "2030-01-01", "2030-01-01 02:00:00")


def _refused_at_the_door(client: TestClient, route: str, body: dict[str, Any], moment: str) -> None:
    """A plain 422 that names the field and the forms the route takes, and holds nothing of the time sent."""
    answer = client.post(route, json={**body, "observedAtUTC": moment})
    assert answer.status_code == 422, (route, moment)
    detail = answer.json()["detail"]
    assert isinstance(detail, str) and detail.startswith("observedAtUTC must be ")
    assert "ending in Z" in detail and "offset" in detail
    assert moment == "" or moment not in answer.text


@pytest.mark.parametrize("route", [*MOMENT_ROUTES, *SOLUTION_ROUTES])
def test_a_time_no_route_can_read_is_refused_at_the_door(route: str) -> None:
    client = TestClient(app)
    body = {**MOMENT_ROUTES, **SOLUTION_ROUTES}[route]
    assert client.post(route, json={**body, "observedAtUTC": MOMENT}).status_code == 200
    for moment in NOT_A_TIME:
        _refused_at_the_door(client, route, body, moment)


@pytest.mark.parametrize("route", [*MOMENT_ROUTES, "/api/science/solve-horizon"])
def test_a_moment_outside_the_ephemeris_span_is_refused_without_the_moment(route: str) -> None:
    """The four routes that predict for a moment refuse one outside the span the ephemeris holds for, in a sentence
    that names the field and the span and holds nothing of the time sent. The library's own sentence starts with the
    moment; the answer does not. Both ends of the span are inside it."""
    client = TestClient(app)
    body = {**MOMENT_ROUTES, **SOLUTION_ROUTES}[route]
    said = "observedAtUTC is outside 1800-01-01 to 2050-12-31 UTC, the span the ephemeris' method and its stated errors hold for."
    for moment in ("1799-12-31T23:59:59Z", "2051-01-01T00:00:00Z", "0001-01-01T00:00:00Z", "9999-12-31T23:59:59Z", "1700-06-01T12:00:00+02:00"):
        answer = client.post(route, json={**body, "observedAtUTC": moment})
        assert answer.status_code == 422 and answer.json() == {"detail": said}, (route, moment)
    for moment in ("1800-01-01T00:00:00Z", "2050-12-31T23:59:59Z"):
        assert client.post(route, json={**body, "observedAtUTC": moment}).status_code == 200, (route, moment)


@pytest.mark.parametrize("route", sorted(SOLUTION_ROUTES))
def test_a_route_that_writes_a_solution_takes_a_time_that_names_an_instant(route: str) -> None:
    """With no zone a time names no instant, so no solution can be written with it; with an offset it names one,
    and the solution carries it in the contract's form."""
    client = TestClient(app)
    body = SOLUTION_ROUTES[route]
    for moment in NAMES_NO_INSTANT:
        _refused_at_the_door(client, route, body, moment)
    for moment in ("2030-01-01T02:00:00+00:00", "2029-12-31T19:00:00-07:00", "2030-01-01T02:00:00Z"):
        answer = client.post(route, json={**body, "observedAtUTC": moment})
        assert answer.status_code == 200, moment
        assert answer.json()["observedAtUTC"] == MOMENT
        # The route adds keys of its own beside the solution; the solution in it is one the parser accepts
        assert refusal(answer.json()) is None


def _sent(client: TestClient, route: str, body: dict[str, Any]) -> Any:
    """Post `body` as JSON text: Python's writer and the API's reader both know the tokens NaN and Infinity, which
    a client can therefore send where a number goes."""
    return client.post(route, content=json.dumps(body), headers={"content-type": "application/json"})


def _shown(value: Any) -> list[str]:
    """How a value sent would read in an answer that held it; nothing for one too short to tell from other text."""
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, float):
        return ["NaN"] if math.isnan(value) else ["Infinity"] if math.isinf(value) else [repr(value)]
    if isinstance(value, str):
        return [value] if len(value) >= 4 else []
    return [json.dumps(value)] if len(json.dumps(value)) >= 4 else []


def _refused_plainly(answer: Any, *sent: Any) -> str:
    """A 422 whose body is one sentence, with nothing in it of what was sent: not the values refused, and not the
    time, the hash or the name of the recording that came with them."""
    assert answer.status_code == 422, answer.status_code
    detail = answer.json()["detail"]
    assert isinstance(detail, str) and detail.endswith(".")
    held = [text for value in (*sent, MOMENT, SHA, ROUTE_SOURCE["sourceAssetId"]) for text in _shown(value) if text in answer.text]
    assert held == []
    return detail


@pytest.mark.parametrize("route", sorted(SOLUTION_ROUTES))
def test_a_place_or_a_hash_the_contract_refuses_is_refused_at_the_door(route: str) -> None:
    client = TestClient(app, raise_server_exceptions=False)
    body = {**SOLUTION_ROUTES[route], "observedAtUTC": MOMENT}
    for field, value, rule in (
        ("latDeg", 91.25, "latDeg is outside the contract: this number is above 90."),
        ("latDeg", -90.5, "latDeg is outside the contract: this number is below -90."),
        ("latDeg", math.nan, "latDeg is outside the contract: this number is not finite."),
        ("lonDeg", 180.75, "lonDeg is outside the contract: this number is above 180."),
        ("lonDeg", -math.inf, "lonDeg is outside the contract: this number is not finite."),
        ("hintRaDeg", math.inf, "hintRaDeg is outside the contract: this number is not finite."),
        ("hintDecDeg", math.nan, "hintDecDeg is outside the contract: this number is not finite."),
        ("sourceSha256", "0" * 64, "source_sha256 of 64 zeros is a placeholder, not a hash: pass the source asset's SHA-256."),
        ("sourceSha256", SHA.upper(), "source_sha256 must be the source asset's SHA-256 as 64 lowercase hex digits."),
        # A line break after the 64 digits is not part of a hash: the whole text is held to the pattern
        ("sourceSha256", SHA + "\n", "source_sha256 must be the source asset's SHA-256 as 64 lowercase hex digits."),
        ("sourceSha256", SHA[:-1], "source_sha256 must be the source asset's SHA-256 as 64 lowercase hex digits."),
    ):
        assert _refused_plainly(_sent(client, route, {**body, field: value}), value) == rule, (field, value)
    # The edges of the globe are on it, and a hint is any direction a client can write
    for edge in ({"latDeg": 90.0, "lonDeg": -180.0}, {"latDeg": -90.0, "lonDeg": 180.0}, {"hintRaDeg": 370.0, "hintDecDeg": -4.0}):
        assert _sent(client, route, {**body, **edge}).status_code == 200, edge


def test_a_detection_the_contract_refuses_is_refused_at_the_door() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    route = "/api/science/solve"
    body = {**SOLUTION_ROUTES[route], "observedAtUTC": MOMENT}

    def with_detection(**change: Any) -> dict[str, Any]:
        detections = copy.deepcopy(body["detections"])
        detections[1].update(change)
        return {**body, "detections": detections}

    for change, rule in (
        ({"snr": 0.0}, "A source detection's snr is outside the contract: this number is not above 0."),
        ({"xPx": -1.75}, "A source detection's x_px is outside the contract: this number is below 0."),
        ({"y_px": -2.25}, "A source detection's y_px is outside the contract: this number is below 0."),
        ({"flux": math.nan}, "A source detection's flux is outside the contract: this number is not finite."),
        ({"fwhmPx": -0.125}, "A source detection's fwhm_px is outside the contract: this number is below 0."),
        ({"centroidUncertaintyPx": math.inf}, "A source detection's centroid_uncertainty_px is outside the contract: this number is not finite."),
        ({"xPx": "12.5"}, "A source detection's x_px is outside the contract: this value is not a number."),
        ({"flux": True}, "A source detection's flux is outside the contract: this value is not a number."),
        ({"snr": [18.0]}, "A source detection's snr is outside the contract: this value is not a number."),
        ({"peakToTotalRatio": "half"}, "A source detection's peak_to_total_ratio is outside the contract: this value is not a number."),
        ({"peakToTotalRatio": math.nan}, "A source detection's peak_to_total_ratio is outside the contract: this number is not finite."),
    ):
        (value,) = change.values()
        assert _refused_plainly(_sent(client, route, with_detection(**change)), value) == rule, change
    # A whole number is a number, and a single-pixel detection has no width
    assert _sent(client, route, with_detection(xPx=120, fwhmPx=0, peakToTotalRatio=0.5)).status_code == 200


def test_a_fault_inside_the_solve_is_not_turned_into_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only what the client sent is refused with a 422. A fault of the solver's own still answers 500, so it stays
    visible: a ValueError like any other, and equally a record the solver itself built with a value the contract
    refuses, which is the solver's fault and not the client's."""
    client = TestClient(app, raise_server_exceptions=False)

    def faulty(fault: Exception) -> Any:
        def solve(*args: Any, **kwargs: Any) -> Any:
            raise fault

        return solve

    for route, solver in (("/api/science/solve", "solve_and_verify_field"), ("/api/science/solve-horizon", "solve_horizon_and_beacon_pose")):
        body = {**SOLUTION_ROUTES[route], "observedAtUTC": MOMENT}
        assert _sent(client, route, body).status_code == 200
        for fault in (
            ValueError("an internal fault"),
            ContractRefusal("A WCS's scale_deg_per_px is outside the contract: this number is not finite."),
            ZeroDivisionError("another internal fault"),
        ):
            with monkeypatch.context() as patched_router:
                patched_router.setattr(science_router, solver, faulty(fault))
                assert _sent(client, route, body).status_code == 500, (route, fault)


NOT_FINITE_NUMBERS = st.sampled_from([math.nan, math.inf, -math.inf])


def _beyond(low: float, high: float | None = None) -> st.SearchStrategy[float]:
    """A number below `low`, one above `high` when there is one, or one that is not finite."""
    finite = {"allow_nan": False, "allow_infinity": False}
    outside = [st.floats(max_value=low, exclude_max=True, **finite), NOT_FINITE_NUMBERS]
    if high is not None:
        outside.append(st.floats(min_value=high, exclude_min=True, **finite))
    return st.one_of(outside)


# The name each refusal gives the value it refuses
NAMED_IN_A_REFUSAL = {
    "observedAtUTC": "observedAtUTC", "latDeg": "latDeg", "lonDeg": "lonDeg", "hintRaDeg": "hintRaDeg",
    "hintDecDeg": "hintDecDeg", "sourceSha256": "source_sha256", "detections.xPx": "x_px", "detections.yPx": "y_px",
    "detections.flux": "flux", "detections.snr": "snr", "detections.fwhmPx": "fwhm_px",
    "detections.centroidUncertaintyPx": "centroid_uncertainty_px", "detections.peakToTotalRatio": "peak_to_total_ratio",
    "width": "width", "height": "height", "sourceAssetId": "sourceAssetId",
}
MINTED_ID_SENTENCE = "sourceAssetId must be a source asset's id as it is minted: src_ and 16 lowercase hex digits."
# A size no frame has: not finite, not above 0, past the largest whole number every reader holds, or not whole
NOT_A_FRAME_SIZE = st.one_of(
    st.sampled_from([math.nan, math.inf, -math.inf, 0, -1, 2**53, 2**64, 10**400, 0.5, 960.25, "wide", [], {"px": 960}]),
    st.integers(max_value=0),
    st.integers(min_value=2**53),
    st.floats(min_value=0.0, max_value=4096.0, allow_nan=False).filter(lambda size: not size.is_integer()),
)
# An id that is not of the minted form: the forms the tests once sent; any text at all, a lone surrogate and control
# characters among it, behind two letters no refusal's sentence holds (so that the text could never be mistaken for
# an echo of itself); the form with too few or too many hex digits, or with one of them in upper case; and values
# that are no text
NOT_OF_THE_MINTED_FORM = st.one_of(
    st.sampled_from(["src_1", "synthetic-dark", "src_0123456789ABCDEF", "src_0123456789abcde", "src_0123456789abcdef0", "\ud800", " ", ""]),
    st.text(alphabet=st.characters(codec=None), max_size=24).map(lambda text: "zq" + text),
    st.text(alphabet="0123456789abcdef", min_size=1, max_size=20).filter(lambda digits: len(digits) != 16).map(lambda digits: "src_" + digits),
    st.text(alphabet="0123456789abcdefABCDEF", min_size=16, max_size=16).filter(lambda digits: digits != digits.lower()).map(lambda digits: "src_" + digits),
    st.sampled_from([7, 1.5, True, None, [], {"id": "src_0123456789abcdef"}]),
)
NOT_A_NUMBER_IN_JSON = st.sampled_from(["12.5", "soon", True, False, [1.5], {"value": 1.5}])
NOT_A_HASH = st.one_of(
    st.sampled_from(["0" * 64, SHA.upper(), SHA[:-1], SHA + "0", SHA[:-1] + "g", "not-a-sha256"]),
    st.text(alphabet="0123456789abcdef", min_size=4, max_size=63),
)
NO_INSTANT = st.one_of(
    st.sampled_from([*NOT_A_TIME[:1], *NOT_A_TIME[2:], *NAMES_NO_INSTANT]),
    # A time with no zone, as Python writes one
    st.datetimes(min_value=datetime(1900, 1, 1), max_value=datetime(2049, 12, 31)).map(lambda moment: moment.isoformat()),
)
# Each value a client sends to a solve route that a solution record will carry, with values the contract refuses;
# and the frame's size and the recording's id, with values the request itself refuses
REQUEST_FAULTS: dict[str, st.SearchStrategy[Any]] = {
    "observedAtUTC": NO_INSTANT,
    "latDeg": _beyond(-90.0, 90.0),
    "lonDeg": _beyond(-180.0, 180.0),
    "hintRaDeg": NOT_FINITE_NUMBERS,
    "hintDecDeg": NOT_FINITE_NUMBERS,
    "sourceSha256": NOT_A_HASH,
    "width": NOT_A_FRAME_SIZE,
    "height": NOT_A_FRAME_SIZE,
    "sourceAssetId": NOT_OF_THE_MINTED_FORM,
}
# A position or a width below zero; a flux, an SNR or an uncertainty not above it (below the smallest number above
# zero); and for any of them a value that is no number at all, which the request leaves untyped
DETECTION_FAULTS: dict[str, st.SearchStrategy[Any]] = {
    "xPx": st.one_of(_beyond(0.0), NOT_A_NUMBER_IN_JSON),
    "yPx": st.one_of(_beyond(0.0), NOT_A_NUMBER_IN_JSON),
    "flux": st.one_of(_beyond(5e-324), NOT_A_NUMBER_IN_JSON),
    "snr": st.one_of(_beyond(5e-324), NOT_A_NUMBER_IN_JSON),
    "fwhmPx": st.one_of(_beyond(0.0), NOT_A_NUMBER_IN_JSON),
    "centroidUncertaintyPx": st.one_of(_beyond(5e-324), NOT_A_NUMBER_IN_JSON),
    "peakToTotalRatio": st.one_of(NOT_FINITE_NUMBERS, NOT_A_NUMBER_IN_JSON),
}


@st.composite
def request_faults(draw: st.DrawFn) -> tuple[str, list[tuple[str, Any]]]:
    """A solve route and one or two of the values it takes, each one the contract refuses."""
    route = draw(st.sampled_from(sorted(SOLUTION_ROUTES)))
    fields = sorted(REQUEST_FAULTS) + ([f"detections.{name}" for name in sorted(DETECTION_FAULTS)] if route.endswith("/solve") else [])
    chosen = draw(st.lists(st.sampled_from(fields), min_size=1, max_size=2, unique=True))
    return route, [
        (field, draw(DETECTION_FAULTS[field.partition(".")[2]] if "." in field else REQUEST_FAULTS[field])) for field in chosen
    ]


@settings(max_examples=300, deadline=None)
@given(case=request_faults())
@example(case=("/api/science/solve", [("latDeg", 91.25)]))
@example(case=("/api/science/solve-horizon", [("lonDeg", math.nan)]))
@example(case=("/api/science/solve", [("hintDecDeg", math.inf), ("detections.snr", 0.0)]))
@example(case=("/api/science/solve", [("detections.xPx", "12.5"), ("detections.flux", -math.inf)]))
@example(case=("/api/science/solve-horizon", [("observedAtUTC", "2030-01-01T02:00:00"), ("sourceSha256", "0" * 64)]))
@example(case=("/api/science/solve", [("detections.peakToTotalRatio", [1.5])]))
@example(case=("/api/science/solve", [("width", math.inf)]))
@example(case=("/api/science/solve-horizon", [("height", math.nan), ("sourceAssetId", "\ud800")]))
@example(case=("/api/science/solve", [("sourceAssetId", "src_1"), ("width", 2**53)]))
@example(case=("/api/science/solve-horizon", [("sourceAssetId", "src_0123456789ABCDEF")]))
@example(case=("/api/science/solve", [("height", 10**400), ("detections.flux", math.nan)]))
def test_pbt_contract_6_what_the_contract_refuses_is_a_plain_422_and_never_a_500(case: tuple[str, list[tuple[str, Any]]]) -> None:
    """P-CONTRACT-6. Whatever a client sends to a solve route that a solution record would refuse, or that the
    request itself refuses (a size no frame has, an id not of the minted form), one value or two at once, the answer
    is a 422 of plain sentences that hold nothing of the request, and never a 500."""
    route, faults = case
    body = copy.deepcopy({**SOLUTION_ROUTES[route], "observedAtUTC": MOMENT})
    for field, value in faults:
        if "." in field:
            body["detections"][2][field.partition(".")[2]] = value
        else:
            body[field] = value
    detail = _refused_plainly(_sent(TestClient(app, raise_server_exceptions=False), route, body), *(value for _, value in faults))
    # The sentence names the value it refuses: with two at once, the first one met
    assert any(NAMED_IN_A_REFUSAL[field] in detail for field, _ in faults), detail


# =============================================================================================
# A request that fails validation, and hostile requests of every kind (services/science/router.py)
# =============================================================================================

# What each field of a solve request must be, in the words of the two routes' own answer to a request that fails
# validation. FastAPI's own answer carries the value back, and cannot be written at all for a value JSON cannot carry
MUST_BE = {
    "width": "a whole number of pixels from 1 to 9007199254740991",
    "height": "a whole number of pixels from 1 to 9007199254740991",
    "detections": "a list of detections, each an object of its measured values",
    "image_luma": "the frame's rows, each a list of numbers",
    "hintRaDeg": "a number",
    "hintDecDeg": "a number",
    "observedAtUTC": "an ISO 8601 date and time that names an instant: ending in Z or with an offset such as +00:00",
    "latDeg": "a number",
    "lonDeg": "a number",
    "sourceAssetId": "a source asset's id as it is minted: src_ and 16 lowercase hex digits",
    "sourceSha256": "the source asset's SHA-256 as 64 lowercase hex digits",
}
NOT_JSON = "The request body is not valid JSON."
NOT_AN_OBJECT = "The request body must be a JSON object, sent as application/json."
LARGEST_SIZE_PX = 2**53 - 1


def _posted(client: TestClient, route: str, body: str | bytes, content_type: str | None = "application/json") -> Any:
    """Post a body as it is written, so that a token no JSON writer gives (NaN, Infinity, a lone surrogate's escape,
    an integer of hundreds of digits) reaches the route as a client can send it."""
    return client.post(route, content=body, headers={"content-type": content_type} if content_type else {})


def _with_raw(body: dict[str, Any], path: list[Any], raw: str) -> str:
    """`body` as JSON text with the value at `path` replaced by the JSON text `raw`."""
    marked = copy.deepcopy(body)
    node = marked
    for step in path[:-1]:
        node = node[step]
    node[path[-1]] = "__RAW__"
    return json.dumps(marked).replace('"__RAW__"', raw)


def _sentence(answer: Any) -> str:
    """The one-key body of a 422 the routes answer themselves: a sentence, or several, each ending in a full stop."""
    assert answer.status_code == 422, (answer.status_code, answer.text[:200])
    body = answer.json()
    assert list(body) == ["detail"] and isinstance(body["detail"], str) and body["detail"].endswith("."), answer.text[:200]
    return body["detail"]


def _solve_bodies() -> dict[str, dict[str, Any]]:
    return {route: {**body, "observedAtUTC": MOMENT} for route, body in SOLUTION_ROUTES.items()}


def test_every_field_of_a_solve_request_has_its_own_sentence() -> None:
    """Driven by the request model's own fields, so a new field needs a sentence before this passes: each field, sent
    as a value of the wrong kind, is refused in the sentence that names it and says what it must be, and each field
    the request cannot do without, left out, in the sentence that says it is missing."""
    fields = science_router.SolveRequest.model_fields
    wire_names = {name: info.alias or name for name, info in fields.items()}
    assert set(wire_names.values()) == set(MUST_BE)
    client = TestClient(app, raise_server_exceptions=False)
    for route, body in _solve_bodies().items():
        assert _sent(client, route, body).status_code == 200
        for name, wire in wire_names.items():
            wrong = {"some": "object"}
            assert _sentence(_sent(client, route, {**body, wire: wrong})) == f"{wire} must be {MUST_BE[wire]}.", (route, wire)
            # The Python name of a field is taken for it, and is answered under the name a client knows
            renamed = {key: value for key, value in body.items() if key != wire} | {name: wrong}
            assert _sentence(_sent(client, route, renamed)) == f"{wire} must be {MUST_BE[wire]}.", (route, name)
            if fields[name].is_required():
                without = {key: value for key, value in body.items() if key != wire}
                assert _sentence(_sent(client, route, without)) == f"{wire} is missing: send {MUST_BE[wire]}.", (route, wire)
        # Several fields at once: each is named once, in the order the request model lists them
        both = _sentence(_sent(client, route, {**body, "lonDeg": None, "width": [], "latDeg": "north"}))
        assert both == f"width must be {MUST_BE['width']}. latDeg must be {MUST_BE['latDeg']}. lonDeg must be {MUST_BE['lonDeg']}."
        # A field at fault in several places is named once: pixels that are no numbers, detections that are no objects
        many = _sentence(_sent(client, route, {**body, "image_luma": [["x", None], [[], "y"]], "detections": [1, "two", None]}))
        assert many == f"detections must be {MUST_BE['detections']}. image_luma must be {MUST_BE['image_luma']}."


# For each bounded number and each pattern a solve request holds: values at each inside end, and values just outside
# with the sentence each is refused in. A new bound gets its edges by a row here
def _must_be(field: str) -> str:
    return f"{field} must be {MUST_BE[field]}."


def _outside_the_contract(field: str, problem: str) -> str:
    return f"{field} is outside the contract: this number is {problem}."


REQUEST_EDGES: dict[str, tuple[list[Any], list[tuple[Any, str]]]] = {
    **{
        size: (
            [1, 2, LARGEST_SIZE_PX, 1.0],
            [(value, _must_be(size)) for value in (0, -1, LARGEST_SIZE_PX + 1, 1.5, 0.5)],
        )
        for size in ("width", "height")
    },
    "sourceAssetId": (
        ["src_0000000000000000", "src_9999999999999999", "src_aaaaaaaaaaaaaaaa", "src_ffffffffffffffff", "src_0123456789abcdef"],
        # One character on either side of 0 to 9 and of a to f, one digit short and one over, and each anchor
        [(value, _must_be("sourceAssetId")) for value in (
            "src_/000000000000000", "src_:000000000000000", "src_`000000000000000", "src_g000000000000000",
            "src_000000000000000", "src_00000000000000000", "xsrc_0000000000000000", "src_0000000000000000x",
            "src_0000000000000000\n", "\nsrc_0000000000000000", "src_000000000000000G", "Src_0000000000000000",
        )],
    ),
    "latDeg": (
        [-90.0, 90.0, 0, -0.0],
        [(math.nextafter(90.0, math.inf), _outside_the_contract("latDeg", "above 90")),
         (math.nextafter(-90.0, -math.inf), _outside_the_contract("latDeg", "below -90"))],
    ),
    "lonDeg": (
        [-180.0, 180.0, 0],
        [(math.nextafter(180.0, math.inf), _outside_the_contract("lonDeg", "above 180")),
         (math.nextafter(-180.0, -math.inf), _outside_the_contract("lonDeg", "below -180"))],
    ),
    # A hint is any direction a client can write, as long as it is a number
    "hintRaDeg": ([-1e308, 0.0, 720.5, 1e308], [(math.inf, _outside_the_contract("hintRaDeg", "not finite"))]),
    "hintDecDeg": ([-1e308, 0.0, 95.0, 1e308], [(-math.inf, _outside_the_contract("hintDecDeg", "not finite"))]),
}


@pytest.mark.parametrize("field", sorted(REQUEST_EDGES))
def test_a_bound_of_the_request_is_held_at_its_edges(field: str) -> None:
    client = TestClient(app, raise_server_exceptions=False)
    route = "/api/science/solve"
    body = _solve_bodies()[route]
    inside, outside = REQUEST_EDGES[field]
    for value in inside:
        assert _sent(client, route, {**body, field: value}).status_code == 200, (field, value)
    for value, said in outside:
        assert _sentence(_sent(client, route, {**body, field: value})) == said, (field, value)


# Values where a number goes, as JSON text
NOT_A_SIZE = ["Infinity", "-Infinity", "NaN", "1e400", "-1e400", '"\\ud800"', '"abc"', "[]", "{}", "1" + "0" * 400, "-0.0", "false"]


@pytest.mark.parametrize("route", sorted(SOLUTION_ROUTES))
def test_a_size_no_frame_has_is_refused_in_a_sentence(route: str) -> None:
    """A width or a height sent as an infinity or as no number at all: FastAPI's own answer would carry the value
    back, which no JSON writer can write, and the request would end in a 500. The route's own answer names the field
    and says what it must be."""
    client = TestClient(app, raise_server_exceptions=False)
    body = _solve_bodies()[route]
    for field in ("width", "height"):
        for raw in NOT_A_SIZE:
            answer = _posted(client, route, _with_raw(body, [field], raw))
            assert _sentence(answer) == f"{field} must be {MUST_BE[field]}.", (field, raw)
            assert raw.strip('"') not in answer.text or len(raw) < 4, (field, raw)


# Each free-form id the tests sent these routes before, and near misses of the minted form, as JSON text
NOT_A_MINTED_ID = [
    '"src_1"', '"synthetic-dark"', '"synthetic-0123456789ab"', '"synthetic-non-finite"', '"synthetic-any-float"',
    '"synthetic-huge"', '"route-time-test"', '"src_none"',
    # A lone surrogate, alone and inside the form; a control character; a blank; text outside ASCII
    '"\\ud800"', '"src_\\ud8000123456789abcde"', '"src_0123456789abcde\\u0000"', '"src_0123456789abcde\\u0007"',
    '"\\tsrc_0123456789abcdef"', '" "', '""', '"src_0123456789abcdef "', '" src_0123456789abcdef"',
    '"src_0123456789abcd\\u00e9"', '"src_\\uff10123456789abcdef"', '"src_\\ud83d\\ude00123456789abcd"',
    # Upper-case hex, an upper-case prefix, 15 and 17 hex digits, a letter past f, another separator, no prefix
    '"src_0123456789ABCDEF"', '"SRC_0123456789abcdef"', '"src_0123456789abcde"', '"src_0123456789abcdef0"',
    '"src_0123456789abcdeg"', '"src-0123456789abcdef"', '"0123456789abcdef"',
    # Not text at all
    "null", "7", "1.5", "true", "[]", "{}", "NaN", '["src_0123456789abcdef"]',
]


@pytest.mark.parametrize("route", sorted(SOLUTION_ROUTES))
def test_a_source_asset_id_is_taken_only_as_it_is_minted(route: str) -> None:
    """The route takes a source asset's id only in the form the upload route mints it: src_ and 16 lowercase hex
    digits. Any other text, and anything that is not text, is refused in one sentence that names the field and holds
    nothing of what was sent: a lone surrogate among them, which no answer could carry back."""
    client = TestClient(app, raise_server_exceptions=False)
    body = _solve_bodies()[route]
    said = f"sourceAssetId must be {MUST_BE['sourceAssetId']}."
    for raw in NOT_A_MINTED_ID:
        answer = _posted(client, route, _with_raw(body, ["sourceAssetId"], raw))
        assert _sentence(answer) == said, raw
        sent = json.loads(raw)
        assert not isinstance(sent, str) or len(sent) < 4 or sent not in answer.json()["detail"], raw
    assert _sentence(_sent(client, route, {key: value for key, value in body.items() if key != "sourceAssetId"})) == (
        f"sourceAssetId is missing: send {MUST_BE['sourceAssetId']}."
    )
    for minted in ("src_0123456789abcdef", f"src_{SHA[:16]}"):
        answer = _sent(client, route, {**body, "sourceAssetId": minted})
        assert answer.status_code == 200 and answer.json()["sourceAssetId"] == minted


@pytest.mark.parametrize("route", sorted(SOLUTION_ROUTES))
def test_a_value_json_cannot_carry_back_is_refused_in_a_sentence_wherever_it_is_sent(route: str) -> None:
    """A lone surrogate where a number goes, and a NaN where text goes: values FastAPI's own answer cannot write."""
    client = TestClient(app, raise_server_exceptions=False)
    body = _solve_bodies()[route]
    for field in ("latDeg", "lonDeg", "hintRaDeg", "hintDecDeg"):
        for raw in ('"\\ud800"', '"north"', "[]", "{}"):
            assert _sentence(_posted(client, route, _with_raw(body, [field], raw))) == f"{field} must be {MUST_BE[field]}.", (field, raw)
    for field in ("observedAtUTC", "sourceSha256"):
        for raw in ("NaN", "Infinity", "7", "[]", "{}", "true"):
            assert _sentence(_posted(client, route, _with_raw(body, [field], raw))) == f"{field} must be {MUST_BE[field]}.", (field, raw)
    # Text holding a lone surrogate is text: the field's own rule refuses it, in its own sentence
    assert _sentence(_posted(client, route, _with_raw(body, ["observedAtUTC"], '"\\ud800"'))).startswith("observedAtUTC must be ")
    assert _sentence(_posted(client, route, _with_raw(body, ["sourceSha256"], '"\\ud800"'))).startswith("source_sha256 must be ")


@pytest.mark.parametrize("route", sorted(SOLUTION_ROUTES))
def test_a_body_that_is_no_json_object_is_refused_in_a_sentence(route: str) -> None:
    client = TestClient(app, raise_server_exceptions=False)
    body = _solve_bodies()[route]
    for raw in ("{", "", '{"latDeg":', "[" * 5000, b"\xff\xfe{}", b'{"latDeg": "\xff"}', '{"latDeg": ' + "1" + "0" * 5000 + "}", "1" + "0" * 5000):
        assert _sentence(_posted(client, route, raw)) in (NOT_JSON, NOT_AN_OBJECT), raw[:20]
    for raw in ("[]", '"text"', "1", "null", "true", "NaN", "[{}]"):
        assert _sentence(_posted(client, route, raw)) == NOT_AN_OBJECT, raw
    assert _sentence(_posted(client, route, "{")) == NOT_JSON
    assert _sentence(_posted(client, route, b'{"latDeg": "\xff"}')) == NOT_JSON
    assert _sentence(_posted(client, route, "1" + "0" * 5000)) == NOT_JSON
    # A JSON object sent as anything but JSON is read as text, which is no object
    for content_type in ("text/plain", "application/x-www-form-urlencoded", None):
        assert _sentence(_posted(client, route, json.dumps(body), content_type)) == NOT_AN_OBJECT, content_type
    assert _posted(client, route, json.dumps(body)).status_code == 200


ONE_ROW = "image_luma must be at least one row of pixels, each row as long as the first and at least one pixel long."
TWO_ROWS = "image_luma must be at least two rows of pixels, each row as long as the first and at least one pixel long."
# Frames no solver can read: no row, no pixel in a row, rows of different lengths
NOT_A_FRAME: dict[str, list[Any]] = {
    "no row": [], "an empty row": [[]], "two empty rows": [[], []], "a shorter last row": [[1.0, 2.0], [3.0]],
    "a longer last row": [[1.0], [2.0, 3.0]], "an empty last row": [[1.0, 2.0], []], "an empty first row": [[], [1.0, 2.0]],
    "a shorter row in the middle": [[1.0, 2.0], [3.0], [4.0, 5.0]],
}


def test_a_frame_no_solver_can_read_is_refused_at_the_door() -> None:
    """A frame with no row, with a row of no pixel, or with rows of different lengths is no frame: it is refused at
    the door in a sentence, where NumPy or a solver would raise on it deep in the route. The horizon route looks for
    the horizon down the rows and needs two of them; the plate solve takes a frame of one pixel."""
    client = TestClient(app, raise_server_exceptions=False)
    plate = {key: value for key, value in _solve_bodies()["/api/science/solve"].items() if key not in ("detections", "width", "height")}
    horizon = _solve_bodies()["/api/science/solve-horizon"]
    for name, frame in NOT_A_FRAME.items():
        assert _sentence(_sent(client, "/api/science/solve", {**plate, "image_luma": frame})) == ONE_ROW, name
        assert _sentence(_sent(client, "/api/science/solve-horizon", {**horizon, "image_luma": frame})) == TWO_ROWS, name
    for width in (1, 2, 5, 40):
        one_row = [[3.0] * width]
        assert _sent(client, "/api/science/solve", {**plate, "image_luma": one_row}).status_code == 200, width
        assert _sentence(_sent(client, "/api/science/solve-horizon", {**horizon, "image_luma": one_row})) == TWO_ROWS, width
        answer = _sent(client, "/api/science/solve-horizon", {**horizon, "image_luma": one_row * 2})
        assert answer.status_code == 200 and refusal(answer.json()) is None, width
    # A size sent beside a frame is held to the frame's own
    sized = _sent(client, "/api/science/solve", {**plate, "image_luma": [[3.0, 4.0]], "width": 2, "height": 1})
    assert sized.status_code == 200
    assert _sentence(_sent(client, "/api/science/solve", {**plate, "image_luma": [[3.0, 4.0]], "width": 1, "height": 2})) == (
        "width and height disagree with image_luma, which is 2 x 1 px."
    )


@lru_cache(maxsize=1)
def _calibrating_field() -> tuple[dict[str, float], ...]:
    """A rendered field the plate solver calibrates, as the detector measures it [simulated]: the requests built on
    it reach the search and what follows it."""
    frame, _, _ = render_synthetic_starfield(width=WIDTH, height=HEIGHT, read_noise_sigma=1.8, flux_scale=1100.0, seed=42)
    return tuple(
        {"xPx": d.x_px, "yPx": d.y_px, "flux": d.flux, "snr": d.snr, "fwhmPx": d.fwhm_px,
         "centroidUncertaintyPx": d.centroid_uncertainty_px, "peakToTotalRatio": d.peak_to_total_ratio}
        for d in solver.detect_point_sources(frame)
    )


def _field_request(detections: list[dict[str, Any]]) -> dict[str, Any]:
    return {**PLACE, **ROUTE_SOURCE, "observedAtUTC": MOMENT, "width": WIDTH, "height": HEIGHT, "detections": detections}


ONE_MORE = {"xPx": 480.0, "yPx": 270.0, "flux": 900.0, "snr": 20.0, "fwhmPx": 2.2, "centroidUncertaintyPx": 0.2}


def _the_solvers_ten() -> dict[str, list[dict[str, Any]]]:
    """The ten requests the plate solver itself fails on, by name: a field that calibrates, with one more detection
    the record takes that lies 1e200 px or more off the frame, or with every centroid uncertainty near the largest
    number. Under its verified WCS the solver gives such a detection a sky position, or an uncertainty, that is no
    number, and the finding record refuses it (services/science/solver.py, the observed finding under a verified WCS).
    That is the solver's own fault, so the route answers 500 [the lead's ruling]. The solver lane's unit closes these
    in the solver and takes each name out of this list."""
    field = list(_calibrating_field())
    ten: dict[str, list[dict[str, Any]]] = {}
    for far in (1e200, 1e300, 1.7e308):
        ten[f"x at {far:g} px"] = [*field, {**ONE_MORE, "xPx": far}]
        ten[f"y at {far:g} px"] = [*field, {**ONE_MORE, "yPx": far}]
        ten[f"x and y at {far:g} px"] = [*field, {**ONE_MORE, "xPx": far, "yPx": far}]
    ten["every centroid uncertainty at 1.7e+308 px"] = [{**d, "centroidUncertaintyPx": 1.7e308} for d in field]
    return ten


THE_SOLVERS_TEN = (
    "x at 1e+200 px", "y at 1e+200 px", "x and y at 1e+200 px", "x at 1e+300 px", "y at 1e+300 px", "x and y at 1e+300 px",
    "x at 1.7e+308 px", "y at 1.7e+308 px", "x and y at 1.7e+308 px", "every centroid uncertainty at 1.7e+308 px",
)


def test_the_field_behind_the_solvers_ten_calibrates() -> None:
    """The field itself, and the field with one more detection on the frame, solve: what fails in each of the ten is
    the one value set far beyond the frame."""
    client = TestClient(app, raise_server_exceptions=False)
    assert list(_the_solvers_ten()) == list(THE_SOLVERS_TEN)
    for detections in (list(_calibrating_field()), [*_calibrating_field(), ONE_MORE]):
        answer = _sent(client, "/api/science/solve", _field_request(detections))
        assert answer.status_code == 200 and answer.json()["status"] == "calibrated" and refusal(answer.json()) is None


@pytest.mark.parametrize("name", THE_SOLVERS_TEN)
def test_a_request_the_plate_solver_itself_fails_on_is_not_answered_as_the_clients_fault(name: str) -> None:
    """Each of the ten still answers 500 today: the record's refusal of what the solver built is not turned into a
    refusal of what the client sent."""
    client = TestClient(app, raise_server_exceptions=False)
    assert _sent(client, "/api/science/solve", _field_request(_the_solvers_ten()[name])).status_code == 500


# Values where a number goes and where text goes, as JSON text: what the auditor of the unit's second version sent,
# and more
HOSTILE_NUMBERS = [
    "Infinity", "-Infinity", "NaN", "1e308", "1.7976931348623157e308", "1e200", "-1", "0", "-0.0", "1e-320", "5e-324", '"12"',
    '"abc"', "true", "false", "null", "[]", "{}", "1" + "0" * 400, str(2**53 + 1), str(2**63), "-1e308", "91", "-181", "400",
    "-100", "1e9", "2147483648", "1.5", "1e400", "-1e400", "1" + "0" * 5000, '"Infinity"', '"\\ud800"',
]
HOSTILE_TEXTS = [
    '""', '"soon"', '"2030-01-01T02:00:00"', '"2030-01-01T02:00:00+14:00"', '"2030-02-29T00:00:00Z"', '"0001-01-01T00:00:00Z"',
    '"9999-12-31T23:59:59Z"', '"' + "0" * 64 + '"', '"' + "A" * 64 + '"', "null", "1", "[]", '"' + "x" * 100000 + '"',
    '"\\ud800"', "true", "1.5", "{}", '"\\u0000"', '" "', '"\\ud83d\\ude00"', "NaN",
]
HOSTILE_BODIES: list[str | bytes] = [
    "[]", '"x"', "1", "null", "{}", "{", "", "[" * 20000, "NaN", "true", '{"a":', b"\xff\xfe{}", b'{"latDeg": "\xff"}',
    "1" + "0" * 5000,
]
HOSTILE_FRAMES: dict[str, list[Any]] = {
    **NOT_A_FRAME, "1x1": [[1.0]], "2x2": [[0.0, 1.0], [2.0, 3.0]], "3d": [[[1.0]]], "rows and a number": [[1.0, 2.0], 3.0],
    "booleans": [[True, False], [False, True]], "texts": [["1", "2"], ["3", "4"]], "nulls": [[None, None], [None, None]],
}


def _drawn_frames() -> dict[str, np.ndarray]:
    """Frames drawn here from a fixed seed: flat ones, noise, hot pixels, values beyond any camera's."""
    rng = np.random.default_rng(7)
    noise = rng.normal(10.0, 2.0, (64, 64))
    frames = {
        "8x8 zeros": np.zeros((8, 8)), "64x64 zeros": np.zeros((64, 64)), "64x64 flat": np.full((64, 64), 7.0),
        "negative": np.full((64, 64), -50.0), "huge values": np.full((64, 64), 1e300), "noise": noise.copy(),
        "1000x3": np.zeros((1000, 3)), "3x1000": np.zeros((3, 1000)), "200x300 noise": rng.normal(10.0, 2.0, (200, 300)).round(2),
    }
    for name, where, value in (
        ("hot pixel", (30, 30), 255.0), ("two hot pixels", (30, slice(30, 32)), 255.0), ("one huge", (30, 30), 1e308),
    ):
        frames[name] = noise.copy()
        frames[name][where] = value
    frames["hot pixel on zeros"] = np.zeros((64, 64))
    frames["hot pixel on zeros"][30, 30] = 255.0
    stars = noise.copy()
    for y, x in rng.uniform(5, 58, (20, 2)):
        stars[int(y), int(x)] = 200.0
    frames["noise and stars"] = stars
    # Every small shape, dark and with one lit pixel, and a few with a lit ground under a bright block
    for h in (1, 2, 3, 4, 5, 8, 13, 31, 32, 33, 40):
        for w in (1, 2, 3, 5, 8, 15, 16, 29):
            frames[f"dark {h}x{w}"] = np.full((h, w), 3.0)
            frames[f"lit {h}x{w}"] = np.full((h, w), 3.0)
            frames[f"lit {h}x{w}"][h // 3, w // 2] = 250.0
    for h, w in ((40, 40), (64, 48), (90, 120), (33, 200)):
        ground = np.full((h, w), 2.0) + rng.normal(0.0, 0.4, (h, w))
        ground[int(h * 0.55) :, :] += 40.0
        ground[max(1, h // 8) : max(1, h // 8) + 3, w // 2 : w // 2 + 3] = 240.0
        frames[f"ground and block {h}x{w}"] = ground
    return frames


def _named(raw: str | bytes) -> str:
    """A hostile token as a case's name carries it: whole when short, else its start and its length."""
    return str(raw) if len(raw) <= 40 else f"{str(raw)[:16]} and on, {len(raw)} characters"


def hostile_requests() -> list[tuple[str, str, str | bytes, str]]:
    """(name, route, body as JSON text or bytes, the hostile JSON token in it or ""): more than a thousand requests
    to the two solve routes, each with something no honest client sends, generated from the lists above."""
    plate, horizon = (_solve_bodies()[route] for route in ("/api/science/solve", "/api/science/solve-horizon"))
    framed = {key: value for key, value in plate.items() if key not in ("detections", "width", "height")}
    routes = (("/api/science/solve", plate), ("/api/science/solve-horizon", horizon))
    frame_routes = (("/api/science/solve", framed), ("/api/science/solve-horizon", horizon))
    field = _field_request(list(_calibrating_field()))
    cases: list[tuple[str, str, str | bytes, str]] = []
    for route, body in routes:
        cases.append((f"{route} as it is", route, json.dumps(body), ""))
        for key in ("latDeg", "lonDeg", "width", "height", "hintRaDeg", "hintDecDeg"):
            cases += [(f"{route} {key}={_named(raw)}", route, _with_raw(body, [key], raw), raw) for raw in HOSTILE_NUMBERS]
        for key in ("observedAtUTC", "sourceSha256", "sourceAssetId"):
            cases += [(f"{route} {key}={_named(raw)}", route, _with_raw(body, [key], raw), raw) for raw in HOSTILE_TEXTS]
        cases += [(f"{route} id {_named(raw)}", route, _with_raw(body, ["sourceAssetId"], raw), raw) for raw in NOT_A_MINTED_ID]
        for key in ("observedAtUTC", "sourceSha256", "sourceAssetId", "latDeg", "lonDeg"):
            cases.append((f"{route} without {key}", route, json.dumps({k: v for k, v in body.items() if k != key}), ""))
        for extra in ('"extraKey": 1', f'"source_sha256": "{SHA}"', f'"obs_utc_iso": "{MOMENT}"', '"lat_deg": 30', '"image_luma": null',
                      '"detections": null', '"width": null'):
            cases.append((f"{route} and {extra[:24]}", route, json.dumps(body)[:-1] + ", " + extra + "}", ""))
        cases += [(f"{route} body {_named(whole)}", route, whole, "") for whole in HOSTILE_BODIES]
    # Detections, field by field: on four detections (too few to solve) and on a field the solver calibrates, where
    # an answer takes a whole solve, so fewer values are tried. On the field, a position or an uncertainty the record
    # takes that lies far beyond the frame is the solver's own to answer: only the ten named requests hold one
    few_numbers = HOSTILE_NUMBERS[:22] + ["1e300", "-5", "2000000"]
    field_numbers = ["Infinity", "NaN", "-1", "0", "5e-324", '"12"', "true", "null", "{}", "1" + "0" * 400, "2000", "1e153"]
    sizes = ["1", "2", "3", "1.0", "960.0", "960.5", "1e15", "1e16", "1e22", "1e300", str(2**53 - 1), str(2**53), str(2**64), "1" + "0" * 400]
    for label, body, numbers in (("few", plate, few_numbers), ("field", field, field_numbers)):
        for name in ("xPx", "yPx", "flux", "snr", "fwhmPx", "centroidUncertaintyPx", "peakToTotalRatio"):
            for raw in numbers:
                cases.append((f"{label} {name}={_named(raw)}", "/api/science/solve", _with_raw(body, ["detections", 0, name], raw), raw))
            if name != "peakToTotalRatio":
                without = copy.deepcopy(body)
                del without["detections"][0][name]
                cases.append((f"{label} without {name}", "/api/science/solve", json.dumps(without), ""))
        for raw in ("[1, 2]", '"x"', "null", "[]", "[{}]", "[[]]", "{}", "7", "[null]", '[{"xPx": {}}]'):
            cases.append((f"{label} detections={raw}", "/api/science/solve", _with_raw(body, ["detections"], raw), raw))
        for key in ("width", "height"):
            for raw in sizes if label == "few" else sizes[4:6] + sizes[10:]:
                cases.append((f"{label} {key}={_named(raw)}", "/api/science/solve", _with_raw(body, [key], raw), raw))
    cases += [(name, "/api/science/solve", json.dumps(_field_request(detections)), "") for name, detections in _the_solvers_ten().items()]
    # Frames, for both routes
    frames = {**HOSTILE_FRAMES, **{name: frame.tolist() for name, frame in _drawn_frames().items()}}
    for name, frame in frames.items():
        cases += [(f"{route} frame {name}", route, json.dumps({**body, "image_luma": frame}), "") for route, body in frame_routes]
    flat = np.full((64, 64), 7.0).tolist()
    for route, body in frame_routes:
        for raw in ("Infinity", "NaN", "-Infinity", '"x"', "null", "true", "1" + "0" * 400, "[]", "{}", "1e308", "-1e308"):
            cases.append((f"{route} pixel {_named(raw)}", route, _with_raw({**body, "image_luma": flat}, ["image_luma", 30, 30], raw), raw))
        for raw in ('"x"', "null", "7", "{}", "true", "[1, 2]", '[[1, 2], "x"]', "[[1, 2], null]"):
            cases.append((f"{route} image_luma={raw}", route, _with_raw({**body, "image_luma": []}, ["image_luma"], raw), raw))
        for raw in ("64", "65", "Infinity", "NaN", "0", "-1", "1.5", '"64"', "true"):
            cases.append((f"{route} frame and width={raw}", route, _with_raw({**body, "image_luma": flat, "width": 0}, ["width"], raw), raw))
        for content_type in ("text/plain", "application/x-www-form-urlencoded", "application/json; charset=utf-16", "none"):
            cases.append((f"{route} sent as {content_type}", route, json.dumps(body), f"content-type {content_type}"))
    # Two hostile values at once, drawn with a fixed seed
    singles = [(key, raw) for key in ("latDeg", "lonDeg", "width", "height", "hintRaDeg", "hintDecDeg") for raw in HOSTILE_NUMBERS[:22]]
    singles += [(key, raw) for key in ("observedAtUTC", "sourceSha256", "sourceAssetId") for raw in HOSTILE_TEXTS[:14]]
    draw = np.random.default_rng(11)
    for index in range(160):
        route, body = routes[index % 2]
        (key_a, raw_a), (key_b, raw_b) = (singles[int(i)] for i in draw.choice(len(singles), size=2, replace=False))
        if key_a != key_b:
            text = _with_raw(json.loads(_with_raw(body, [key_a], '"__FIRST__"')), [key_b], raw_b).replace('"__FIRST__"', raw_a)
            cases.append((f"{route} pair {index}: {key_a}={_named(raw_a)} and {key_b}={_named(raw_b)}", route, text, raw_a))
    return cases


# The frames above are drawn to push the detector's sums past any number, and NumPy says so
@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_more_than_a_thousand_hostile_requests_are_answered_or_refused_in_a_sentence() -> None:
    """Every answer is a solution the contract takes, or a 422 of one key whose value is a sentence with nothing in
    it of the hostile value sent, or one of the ten requests the plate solver itself fails on, by name. No other
    request answers 500, and none is answered with FastAPI's own list."""
    client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
    cases = hostile_requests()
    assert len(cases) >= 1000 and len({name for name, *_ in cases}) == len(cases)
    kinds: dict[str, int] = {}
    for name, route, body, token in cases:
        content_type = token.removeprefix("content-type ") if token.startswith("content-type ") else "application/json"
        answer = _posted(client, route, body, None if content_type == "none" else content_type)
        if name in THE_SOLVERS_TEN:
            assert answer.status_code == 500, name
            kind = "the solver's ten"
        elif answer.status_code == 200:
            assert refusal(answer.json()) is None, name
            kind = "a solution"
        else:
            detail = _sentence(answer)
            # A string of four characters or more that was sent is not in the answer
            held = json.loads(token) if token.startswith('"') else None
            assert not (isinstance(held, str) and len(held) >= 4 and held in detail), name
            assert not (token and token[0] not in '"c' and len(token) >= 4 and token in answer.text), name
            kind = "a sentence"
        kinds[kind] = kinds.get(kind, 0) + 1
    assert kinds["the solver's ten"] == 10 and kinds["a solution"] >= 600 and kinds["a sentence"] >= 800, kinds


# =============================================================================================
# What a solver builds from its own numbers is what the contract takes
# (services/science/horizon_solver.py, services/science/phone_photo_solver.py)
# =============================================================================================

# Synthetic moments through one month, four a day, so that the Moon is up at some and down at others
SYNTHETIC_MOMENTS = [f"2030-01-{day:02d}T{hour:02d}:00:00Z" for day in range(1, 29) for hour in (2, 8, 14, 20)]


def _black_frame_with_a_lit_spot(spot: int) -> list[list[float]]:
    """A drawn frame, black but for one lit spot `spot` pixels wide and high."""
    luma = np.zeros((180, 240), dtype=np.float64)
    luma[20 : 20 + spot, 150 : 150 + spot] = 240.0
    return luma.tolist()


# One of the frames is drawn to make the solver's own sums overflow, and NumPy says so
@pytest.mark.filterwarnings("ignore:overflow encountered in reduce:RuntimeWarning")
def test_a_beacon_of_no_width_is_no_beacon() -> None:
    """The horizon solver takes a bright spot's width from its second moments and its centroid's uncertainty from
    that width. A spot with all of its light in one pixel has no width: it is no beacon, then, and the answer is the
    one for a frame with no bright source, not a detection with an uncertainty of zero, which the contract refuses.
    The same holds for a spot whose uncertainty comes out as zero or as no finite number for any other reason. A
    spot two pixels wide has a width and is taken as it was."""
    client = TestClient(app, raise_server_exceptions=False)
    no_width, two_wide = _black_frame_with_a_lit_spot(1), _black_frame_with_a_lit_spot(2)
    # A pixel so bright beside its neighbour that the spot's width, though above zero, leaves its centroid an
    # uncertainty too small for any number but zero
    width_lost = np.zeros((180, 240), dtype=np.float64)
    width_lost[20, 150], width_lost[20, 151] = 1e300, 1.0
    # Two pixels so bright, over a sky that is not flat, that the second moments of the spot are beyond any number:
    # a width, and so an uncertainty, that is infinite
    beyond_number = np.zeros((180, 240), dtype=np.float64)
    beyond_number[:, ::2] = 1e303
    beyond_number[5, 3], beyond_number[5, 14] = 5e306, 5e306
    # One lit pixel on a flat sky whose level no binary fraction writes exactly: the centroid, a product divided
    # back by its own weight, then lands a rounding step off the pixel (column 3 is one where it does), and the
    # second moments give a width of about 1e-15 px. The light is in one pixel all the same
    width_of_rounding = np.full((180, 240), 7.3, dtype=np.float64)
    width_of_rounding[20, 3] = 240.0
    assert (3 * (240.0 - 7.3)) / (240.0 - 7.3) != 3
    # Two lit pixels side by side: the least light a width can be measured in
    two_pixels = np.zeros((180, 240), dtype=np.float64)
    two_pixels[20, 150:152] = 240.0
    no_beacon = ("no width", "width lost", "width beyond any number", "width of rounding alone")
    frames = (
        ("no width", no_width), ("width lost", width_lost.tolist()), ("width beyond any number", beyond_number.tolist()),
        ("width of rounding alone", width_of_rounding.tolist()), ("two wide", two_wide), ("two pixels", two_pixels.tolist()),
    )
    moon_up = 0
    for moment in SYNTHETIC_MOMENTS:
        body = {**PLACE, **ROUTE_SOURCE, "observedAtUTC": moment}
        answers: dict[str, dict[str, Any]] = {}
        for name, frame in frames:
            answer = client.post("/api/science/solve-horizon", json={**body, "image_luma": frame})
            assert answer.status_code == 200, (name, moment, answer.status_code)
            answers[name] = answer.json()
            assert refusal(answers[name]) is None
            assert answers[name]["status"] == "predicted_only"
            assert all("sourceDetection" not in finding for finding in answers[name]["findings"])
        (said,), (said_of_two_wide,) = answers["no width"]["warnings"], answers["two wide"]["warnings"]
        for name in no_beacon:
            assert answers[name]["warnings"] == [said], (name, moment)
        if answers["two wide"]["quality"]["detectedCandidateCount"] == 1:
            # The Moon is up: the spot two pixels wide is a bright source the solver names, with no horizon to fit
            moon_up += 1
            assert said_of_two_wide.startswith("No horizon line found") and "A bright source at" in said_of_two_wide
            assert said.startswith("No compact bright source found") and "A bright source at" not in said
            for name in no_beacon:
                assert answers[name]["quality"]["detectedCandidateCount"] == 0, (name, moment)
            assert answers["two pixels"]["quality"]["detectedCandidateCount"] == 1
            assert "A bright source at" in answers["two pixels"]["warnings"][0]
        else:
            assert said == said_of_two_wide and said.startswith("The Moon is not above")
    # Both kinds of moment are among them, or the loop above showed nothing
    assert 0 < moon_up < len(SYNTHETIC_MOMENTS)


# The pose tests' synthetic moment, with the Moon up and the Sun down over the open-ocean place
MOON_MOMENT = "2031-08-30T03:01:00Z"
# Frame sizes at which the head-worn Moon test admits a spot one pixel across, width by height
SMALL_FRAMES = ((36, 48), (48, 64), (72, 96), (96, 128), (150, 200))
ONE_PIXEL_ACROSS = 2.0 * math.sqrt(1.0 / math.pi)
NO_WIDTH = "The frame's bright spots were not confirmed as the Moon: the brightest spot has no width, all of its light in one pixel."
WIDTH_NO_NUMBER = (
    "The frame's bright spots were not confirmed as the Moon: the brightest spot's width came out as zero or as no "
    "finite number."
)


def _flat_frame(
    width: int, height: int, spot: str | None, background: float = 2.0, at: tuple[float, float] = (0.5, 0.35)
) -> np.ndarray:
    """A drawn frame, flat but for one bright spot: one lit pixel, two side by side, or a small disk."""
    luma = np.full((height, width), background, dtype=np.float64)
    x, y = int(width * at[0]), int(height * at[1])
    if spot == "one pixel":
        luma[y, x] = 255.0
    elif spot == "two pixels":
        luma[y, x : x + 2] = 255.0
    elif spot == "disk":
        yy, xx = np.ogrid[0:height, 0:width]
        luma[(xx - x) ** 2 + (yy - y) ** 2 <= 1.5**2] = 255.0
    return luma


def _moon_test_limits(width: int, height: int) -> tuple[float, float]:
    moon = pose_estimate.topocentric_moon(MOON_MOMENT, PLACE["latDeg"], PLACE["lonDeg"])
    profile = camera_profile(META_RAY_BAN_DISPLAY_MODEL)
    return pose_estimate.moon_diameter_limits_px(moon, profile.fov_diagonal_deg_range, width, height)


def _sky_completed_over(frame: np.ndarray) -> dict[str, Any]:
    """What `complete_sky` answers for a head-worn photo holding `frame` that the plate solver could not solve."""
    height, width = frame.shape
    asset = {
        "id": "src_0123456789abcdef", "sha256": SHA, "mediaKind": "image", "width": width, "height": height,
        "capturedAtUTC": MOON_MOMENT, "observerLocation": dict(PLACE),
        "cameraMetadata": {"make": "Meta", "model": META_RAY_BAN_DISPLAY_MODEL},
    }
    unsolved = solver._build_unresolved_solution(
        [], "sol-head-worn", asset["id"], SHA, 0.0, MOON_MOMENT, PLACE["latDeg"], PLACE["lonDeg"], "Too few sources.",
        "Pixel position only: no verified WCS places it on the sky.", solver.triangle_solver_identity(get_catalog_stars()),
    )
    return complete_sky(unsolved, asset, frames=lambda: [(0.0, frame)]).to_contract_dict()


@pytest.mark.parametrize(("width", "height"), SMALL_FRAMES)
def test_a_moon_of_no_width_is_no_beacon_for_the_head_worn_pose(width: int, height: int) -> None:
    """The head-worn pose takes a bright spot's width from its second moments, as the horizon solver does. A spot with
    all of its light in one pixel has no width: the Moon test refuses it, the line-up is refused with the path's own
    sentence, and the photo keeps the sky predicted for its time and place, exactly the answer for a frame with no
    bright spot. Nothing is left to raise where the observed Moon's detection is built. A spot with a width is lined
    up as it was."""
    low, high = _moon_test_limits(width, height)
    # The Moon test's size limits admit a spot one pixel across here, so only its width stands before a pose
    assert low <= ONE_PIXEL_ACROSS <= high
    # A whole-number sky, and one no binary fraction writes exactly, where rounding alone gives a width of 1e-15 px
    for background in (2.0, 7.3):
        no_spot = _sky_completed_over(_flat_frame(width, height, None, background))
        assert no_spot["status"] == "predicted_only" and "poseEstimate" not in no_spot
        assert no_spot["warnings"][-2] == pose_estimate.not_confirmed_reason("in the photo")
        assert sum(finding["evidenceClass"] == "predicted" for finding in no_spot["findings"]) >= 40
        for at in ((0.5, 0.35), (0.3, 0.6)):
            lit = _flat_frame(width, height, "one pixel", background, at)
            refused = pose_estimate.find_moon_blob(lit, min_diameter_px=low, max_diameter_px=high)
            assert isinstance(refused, pose_estimate.Refusal), (background, at, refused)
            assert (refused.kind, refused.reason) == ("not_moon", NO_WIDTH)
            assert _sky_completed_over(lit) == no_spot, (background, at)
            lined_up = _sky_completed_over(_flat_frame(width, height, "two pixels", background, at))
            assert lined_up["status"] == "approximate" and len(lined_up["poseEstimate"]["sweptWcs"]) == 9
            (moon,) = [finding for finding in lined_up["findings"] if finding["id"] == "finding-observed-moon"]
            assert moon["sourceDetection"]["fwhmPx"] > 0.5 and moon["sourceDetection"]["centroidUncertaintyPx"] > 0.01
            assert refusal(lined_up) is None
            assert refusal(_sky_completed_over(_flat_frame(width, height, "disk", background, at))) is None


# The frames are drawn to push the Moon test's own sums past any number, and NumPy says so
@pytest.mark.filterwarnings("ignore::RuntimeWarning")
@pytest.mark.parametrize("kind", ["lost to zero", "beyond any number", "light beyond any number"])
def test_a_spot_whose_width_is_no_number_is_no_beacon_for_the_head_worn_pose(kind: str) -> None:
    """A spot of more than one pixel whose width still gives its centroid no uncertainty to state: zero, infinite, or
    no number at all. Each is refused like a spot of one pixel, in its own sentence, where it would otherwise leave
    a detection no record takes, and the photo keeps its predicted sky."""
    width, height = 96, 128
    low, high = _moon_test_limits(width, height)
    frame = np.zeros((height, width), dtype=np.float64)
    if kind == "lost to zero":
        # Two pixels carry the light, one of them by the smallest step above the sky a number can take: the second
        # moments come out as zero, and so does the uncertainty
        frame[44, 48], frame[44, 49] = 255.0, 5e-324
    elif kind == "beyond any number":
        # A block of 5 by 5 whose corners hold nearly all of its light: the second moments pass beyond any number
        # while the summed light, the centroid and the SNR stay ordinary numbers, so the uncertainty is infinite
        frame[3:8, 3:8] = 3.0 * 2.0**1012
        frame[5, 5] = 2.0**1014
        for y, x in ((3, 3), (3, 7), (7, 3), (7, 7)):
            frame[y, x] = 5.0 * 2.0**1017
    else:
        # Four pixels whose summed light is beyond any number: the centroid, the width and the uncertainty are none
        frame[20:22, 30:32] = 2.0**1023
    refused = pose_estimate.find_moon_blob(frame, min_diameter_px=low, max_diameter_px=high)
    assert isinstance(refused, pose_estimate.Refusal), refused
    assert (refused.kind, refused.reason) == ("not_moon", WIDTH_NO_NUMBER)
    assert _sky_completed_over(frame) == _sky_completed_over(np.zeros((height, width), dtype=np.float64))


@st.composite
def frames_with_a_bright_spot(draw: st.DrawFn) -> tuple[np.ndarray, str, bool]:
    """A small frame of the head-worn camera with one bright spot drawn on it, the kind of spot, and whether all of
    the spot's light is in one pixel over a flat sky. The sky is flat or noisy, at a whole-number level or at one no
    binary fraction writes exactly."""
    width, height = draw(st.sampled_from(SMALL_FRAMES))
    background = draw(st.sampled_from([0.0, 2.0, 7.3, 0.1, 16.5]))
    noise = draw(st.sampled_from([0.0, 0.0, 0.0, 0.3, 1.5]))
    luma = np.full((height, width), background, dtype=np.float64)
    if noise:
        luma = np.clip(luma + np.random.default_rng(draw(st.integers(0, 2**16))).normal(0.0, noise, luma.shape), 0.0, 255.0)
    x, y = draw(st.integers(4, width - 7)), draw(st.integers(4, height - 7))
    peak = draw(st.sampled_from([255.0, 240.0, 230.5, 201.0]))
    kind = draw(st.sampled_from(
        ["one pixel", "two in a row", "two in a column", "a diagonal pair", "a pixel and a faint neighbour", "a disk", "a soft spot"]
    ))
    yy, xx = np.mgrid[0:height, 0:width]
    if kind == "one pixel":
        luma[y, x] = peak
    elif kind == "two in a row":
        luma[y, x : x + 2] = peak
    elif kind == "two in a column":
        luma[y : y + 2, x] = peak
    elif kind == "a diagonal pair":
        luma[y, x] = luma[y + 1, x + 1] = peak
    elif kind == "a pixel and a faint neighbour":
        luma[y, x] = peak
        luma[y, x + 1] = background + draw(st.sampled_from([5e-324, 1e-300, 1e-9, 0.5, 1.0]))
    elif kind == "a disk":
        luma[(xx - x) ** 2 + (yy - y) ** 2 <= draw(st.floats(0.8, 4.0)) ** 2] = peak
    else:
        sigma = draw(st.floats(0.4, 2.5))
        luma = np.maximum(luma, background + (peak - background) * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2.0 * sigma**2)))
    return luma, kind, kind == "one pixel" and noise == 0.0


@settings(max_examples=120, deadline=None)
@given(case=frames_with_a_bright_spot())
@example(case=(_flat_frame(36, 48, "one pixel"), "one pixel", True))
@example(case=(_flat_frame(48, 64, "one pixel", 7.3), "one pixel", True))
@example(case=(_flat_frame(96, 128, "two pixels"), "two in a row", False))
@example(case=(_flat_frame(150, 200, "disk"), "a disk", False))
def test_pbt_contract_8_a_spot_the_moon_test_passes_builds_its_detection(case: tuple[np.ndarray, str, bool]) -> None:
    """P-CONTRACT-8. Whatever bright spot a head-worn frame holds, a spot the Moon test passes has a width and a
    centroid uncertainty above zero and builds the detection record the line-up makes from it; a spot with all of
    its light in one pixel is never passed; and `complete_sky` answers, with a solution the parser accepts, lined up
    with one observed Moon or holding the predicted sky alone. Nothing downstream is left to raise."""
    frame, kind, no_width = case
    height, width = frame.shape
    low, high = _moon_test_limits(width, height)
    spot = pose_estimate.find_moon_blob(frame, min_diameter_px=low, max_diameter_px=high)
    if isinstance(spot, pose_estimate.MoonBlob):
        event(f"passed: {kind}")
        assert not no_width
        assert spot.fwhm_px > 0.0 and 0.0 < spot.centroid_uncertainty_px < math.inf
        # The record the line-up builds from the spot (services/science/predicted_sky.py)
        SourceDetection(
            x_px=spot.x_px, y_px=spot.y_px, flux=spot.flux, snr=spot.snr, fwhm_px=spot.fwhm_px,
            centroid_uncertainty_px=spot.centroid_uncertainty_px, asset_id="src_0123456789abcdef",
            peak_to_total_ratio=spot.peak_to_total_ratio,
        )
    else:
        event(f"refused: {kind}")
        assert spot.kind == "not_moon"
        assert not no_width or spot.reason == NO_WIDTH
        assert spot.reason != WIDTH_NO_NUMBER or kind == "a pixel and a faint neighbour"
    answer = _sky_completed_over(frame)
    moons = [finding for finding in answer["findings"] if finding["id"] == "finding-observed-moon"]
    if "poseEstimate" in answer:
        assert answer["status"] == "approximate" and len(moons) == 1 and isinstance(spot, pose_estimate.MoonBlob)
        assert moons[0]["sourceDetection"]["fwhmPx"] > 0.0 and moons[0]["sourceDetection"]["centroidUncertaintyPx"] > 0.0
    else:
        assert answer["status"] == "predicted_only" and moons == []
    assert not no_width or answer["status"] == "predicted_only"
    assert sum(finding["evidenceClass"] == "predicted" for finding in answer["findings"]) >= 40


def _png(frame: np.ndarray, *, taken: str = "2030:01:01 02:00:00", model: str | None = None) -> bytes:
    """A drawn or rendered frame as a PNG that says when and where it was taken: a synthetic moment and the
    open-ocean place, and the camera's model when one is given."""
    exif = Image.Exif()
    if model is not None:
        exif[0x0110] = model
    exif_ifd = exif.get_ifd(0x8769)
    exif_ifd[0x9003] = taken
    exif_ifd[0x9011] = "+00:00"
    gps_ifd = exif.get_ifd(0x8825)
    gps_ifd[1], gps_ifd[2], gps_ifd[3], gps_ifd[4] = "N", (30.0, 0.0, 0.0), "W", (40.0, 0.0, 0.0)
    out = io.BytesIO()
    Image.fromarray(np.clip(np.round(frame), 0.0, 255.0).astype(np.uint8), mode="L").save(out, format="PNG", exif=exif.tobytes(), compress_level=1)
    return out.getvalue()


def test_a_photo_whose_moon_is_one_lit_pixel_keeps_its_predicted_sky(tmp_path: Path) -> None:
    """Through the upload job: a photo from the head-worn camera whose only bright spot is one lit pixel is solved,
    and its stored solution holds the sky predicted for its time and place and the pose path's own sentence, as for
    a photo with no bright spot. The job's catch-all warning for a failed predicted sky is not there."""
    store = IngestStore(tmp_path / "store", max_bytes=10_000_000)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    stored: dict[tuple[int, int, str | None], dict[str, Any]] = {}
    try:
        client = TestClient(app)
        for width, height in SMALL_FRAMES:
            for spot in (None, "one pixel"):
                photo = _png(_flat_frame(width, height, spot), taken="2031:08:30 03:01:00", model=META_RAY_BAN_DISPLAY_MODEL)
                receipt = client.post("/api/ingest", content=photo, headers={"content-type": "image/png"})
                assert receipt.status_code == 201, receipt.text
                asset = receipt.json()["sourceAsset"]
                assert asset["capturedAtUTC"] == MOON_MOMENT and asset["cameraMetadata"]["model"] == META_RAY_BAN_DISPLAY_MODEL
                # What the upload route mints is what the solve routes take, and nothing else
                assert re.fullmatch(science_router.SOURCE_ASSET_ID_PATTERN, asset["id"]) and asset["id"] == f"src_{asset['sha256'][:16]}"
                assert client.post(f"/api/ingest/{asset['sha256']}/solve").json()["state"] == "succeeded"
                solution = read_solution(store, asset["sha256"])
                assert solution is not None
                stored[(width, height, spot)] = solution
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)
    for width, height in SMALL_FRAMES:
        lit, dark = stored[(width, height, "one pixel")], stored[(width, height, None)]
        assert ingest_jobs.PREDICTED_SKY_FAILED not in lit["warnings"], (width, height, lit["warnings"])
        assert lit["status"] == "predicted_only" and "poseEstimate" not in lit and "wcs" not in lit
        assert lit["warnings"][-2:] == dark["warnings"][-2:]
        assert lit["warnings"][-2] == pose_estimate.not_confirmed_reason("in the photo")
        predicted = [finding["id"] for finding in lit["findings"] if finding["evidenceClass"] == "predicted"]
        assert len(predicted) >= 40 and predicted == [f["id"] for f in dark["findings"] if f["evidenceClass"] == "predicted"]
        assert all(finding["id"] != "finding-observed-moon" for finding in lit["findings"])
        assert contract.parse_observation_solution(lit).to_contract_dict() == lit


# These frames are made to push the detector's and the horizon solver's arithmetic past any number, and NumPy says so
@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_a_frame_that_gives_a_detection_no_record_takes_is_refused_at_the_door(monkeypatch: pytest.MonkeyPatch) -> None:
    """What the detector measures in the frame a client sent reaches the detection record like a detection sent
    outright. A pixel that is infinite, or so large that the detector's sums pass beyond any number, gives a
    detection whose position or flux is no number: the frame is the client's to mend, with a plain 422 that holds
    nothing of it. A frame in which the detector measures nothing the contract refuses is taken as it was, one with
    a pixel that is no number among them, which the detector passes over. The horizon route takes them all: its
    solver names no beacon it cannot measure. A fault of the detector's own is no refusal."""
    client = TestClient(app, raise_server_exceptions=False)
    body = {**PLACE, **ROUTE_SOURCE, "observedAtUTC": MOMENT}
    frame = np.random.default_rng(7).normal(24.0, 2.0, (64, 64)).clip(0.0, 255.0)

    def with_pixel(pixel: float, route: str = "/api/science/solve", moment: str = MOMENT) -> Any:
        marred = frame.copy()
        marred[21, 21] = pixel
        return _sent(client, route, {**body, "observedAtUTC": moment, "image_luma": marred.tolist()})

    for pixel in (math.inf, 1.7e308):
        said = _refused_plainly(with_pixel(pixel), pixel)
        assert said.startswith("image_luma gives a detection no record takes. A source detection's "), said
        assert said.endswith(" is outside the contract: this number is not finite."), said
    for pixel in (24.0, 0.0, 5e-324, -50.0, 1e6, 1e300, math.nan):
        answer = with_pixel(pixel)
        assert answer.status_code == 200 and refusal(answer.json()) is None, pixel
    for moment in SYNTHETIC_MOMENTS[:8]:
        for pixel in (math.inf, 1.7e308, math.nan, 24.0):
            answer = with_pixel(pixel, "/api/science/solve-horizon", moment)
            assert answer.status_code == 200 and refusal(answer.json()) is None, (pixel, moment)

    def faulty(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("an internal fault")

    monkeypatch.setattr(science_router, "detect_point_sources", faulty)
    assert with_pixel(24.0).status_code == 500


# The widest inlier gate the phone solver sets (its gate is 1.8 to 4 px wide, its association radius at most 6 px)
WIDEST_INLIER_GATE_PX = 4.0


def _phone_gate(residuals: list[float], gate_px: float = WIDEST_INLIER_GATE_PX) -> bool:
    """Whether the phone solver's gate passes these held-out residuals, in a sparse field where chance pairs are
    rare, so that the residuals decide."""
    passed, _ = phone_photo_solver.evaluate_coincidence_gate(
        held_out_residuals_px=residuals, active_detection_count=len(residuals), active_sky_area_px2=1_000_000.0,
        projected_catalog_count=len(residuals), inlier_gate_px=gate_px,
    )
    return passed


def _phone_quality(residuals: list[float], passed: bool, gate_px: float = WIDEST_INLIER_GATE_PX) -> SolutionQuality:
    """The quality record as the phone solver builds it from its held-out residuals."""
    return phone_photo_solver.held_out_quality(
        residuals, gate_px, passed, detected_candidate_count=len(residuals) + 20,
        matched_inlier_count=sum(r <= gate_px for r in residuals) if passed else 0, scale_deg_per_px=0.02,
    )


def test_the_phone_solvers_gate_judges_the_median_its_record_reports() -> None:
    """The contract's medianResidualPx covers every held-out pair, inlier or not, and the held-out gate is a limit on
    it. Six pairs half a pixel off satisfy the phone solver's other checks; with seven more pairs at 5.5 px (inside
    the association radius, outside the inlier gate) the median of every pair is 5.5 px. The gate judges that number,
    so it does not pass, and the record that reports it builds."""
    least, most = contract.held_out_gate()
    six_of_thirteen = [0.5] * 6 + [5.5] * 7
    pairs, inliers, median, p95 = phone_photo_solver.held_out_residual_statistics(six_of_thirteen, WIDEST_INLIER_GATE_PX)
    assert (pairs, inliers, median, p95) == (13, 6, 5.5, 5.5)
    assert inliers >= least and median > most
    passed = _phone_gate(six_of_thirteen)
    assert passed is False, "the median of every held-out pair is above the gate"
    record = _phone_quality(six_of_thirteen, passed)
    assert (record.held_out_pair_count, record.held_out_inliers, record.median_residual_px) == (13, 6, 5.5)
    # The arcsec median is the px median at the WCS scale, exactly (a pair at which 3600 and either float beside it
    # give different products)
    assert record.p95_residual_px == 5.5 and record.median_residual_arcsec == 5.5 * 0.02 * 3600.0
    assert record.passed_independent_verification is False
    # With two of the far pairs gone the median of every pair is an inlier's, and the same six inliers pass
    six_of_eleven = [0.5] * 6 + [5.5] * 5
    assert _phone_gate(six_of_eleven) is True
    record = _phone_quality(six_of_eleven, True)
    assert record.passed_independent_verification is True and record.median_residual_px == 0.5
    # The limit is the contract's own: a median at it passes, the next number above it does not
    assert _phone_gate([most] * 12) is True and _phone_quality([most] * 12, True).median_residual_px == most
    assert _phone_gate([most] * 5 + [math.nextafter(most, math.inf)] * 6) is False
    # The limit on the inliers' 95th percentile is inclusive, as the median's is. The percentile is of the inliers, so
    # it can reach the limit only under an inlier gate wider than the solver's own (at most 4 px): of 21 inliers it
    # falls on the 20th, here at the limit, then one float above it
    p95_limit = phone_photo_solver.MAX_HELD_OUT_P95_PX
    assert _phone_gate([0.5] * 19 + [p95_limit] * 2, gate_px=6.0) is True
    assert _phone_gate([0.5] * 19 + [math.nextafter(p95_limit, math.inf)] * 2, gate_px=6.0) is False
    # A median that is no number passes nothing
    assert _phone_gate([0.5] * 6 + [math.nan] * 7) is False
    # No pair, no residual to report; and an inlier is a pair at or inside the gate
    assert phone_photo_solver.held_out_residual_statistics([], WIDEST_INLIER_GATE_PX) == (0, 0, None, None)
    pairs, inliers, median, p95 = phone_photo_solver.held_out_residual_statistics([1.0, 2.0, 2.5, 6.0], 2.0)
    assert (pairs, inliers, median) == (4, 2, 2.25) and p95 == pytest.approx(5.475)
    # The percentile is the 95th exactly: of 0 to 20 it falls on 19, and one float step either way in the 95 does not
    assert phone_photo_solver.held_out_residual_statistics([float(k) for k in range(21)], 2.0) == (21, 3, 10.0, 19.0)
    nothing = _phone_quality([], False)
    assert (nothing.held_out_pair_count, nothing.median_residual_px, nothing.median_residual_arcsec) == (0, None, None)


def test_the_phone_solver_reports_the_residuals_its_gate_judged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Through the solver itself, on a rendered frame of the phone photo benchmark's CI subset (simulated, with held-out
    pairs outside the inlier gate): the residuals handed to the gate are the ones the solution's quality record counts
    and takes its median from, so the median judged and the median reported are one number."""
    judged: list[tuple[list[float], float, bool]] = []
    reported: list[tuple[list[float], float, bool]] = []
    gate, record = phone_photo_solver.evaluate_coincidence_gate, phone_photo_solver.held_out_quality

    def judging(**facts: Any) -> tuple[bool, float]:
        answer = gate(**facts)
        judged.append((list(facts["held_out_residuals_px"]), facts["inlier_gate_px"], answer[0]))
        return answer

    def reporting(residuals: list[float], gate_px: float, passed: bool, **counts: Any) -> SolutionQuality:
        reported.append((list(residuals), gate_px, passed))
        return record(residuals, gate_px, passed, **counts)

    monkeypatch.setattr(phone_photo_solver, "evaluate_coincidence_gate", judging)
    monkeypatch.setattr(phone_photo_solver, "held_out_quality", reporting)
    frame = render.render_frame(suite.build_suite("ci")[1], stars.TileCatalog())
    moment_and_place = frame.truth.metadata
    solution = phone_photo_solver.solve_phone_photo(
        frame.rgb, obs_utc_iso=moment_and_place["utc"], observer_lat=moment_and_place["latDeg"],
        observer_lon=moment_and_place["lonDeg"], camera_label=moment_and_place["camera"], source_asset_id="src-ci-01",
        source_sha256=hashlib.sha256(frame.jpeg_bytes).hexdigest(),
    )
    ((residuals, gate_px, passed),) = judged
    # The record is built from the very residuals, gate width and answer of the gate
    assert reported == judged
    quality = solution.quality
    pairs, inliers, median, p95 = phone_photo_solver.held_out_residual_statistics(residuals, gate_px)
    least, most = contract.held_out_gate()
    # Some held-out pairs of this frame lie outside the inlier gate, so the inliers' median is another number
    assert pairs > inliers >= least
    assert median == float(np.median(residuals)) != float(np.median([r for r in residuals if r <= gate_px]))
    assert p95 == float(np.percentile(residuals, 95.0)) != median
    assert (quality.held_out_pair_count, quality.held_out_inliers) == (pairs, inliers) == (len(residuals), sum(r <= gate_px for r in residuals))
    assert (quality.median_residual_px, quality.p95_residual_px) == (median, p95)
    assert passed is True and quality.passed_independent_verification is True and solution.status == "calibrated"
    assert quality.median_residual_px <= most
    assert contract.parse_observation_solution(solution.to_contract_dict()).quality.median_residual_px == median


def test_the_phone_solvers_record_counts_the_matches_it_writes_and_none_without_the_gate() -> None:
    """The quality record's matched count is the catalog matches the solution writes when the gate passes (ci-01 of the
    phone photo benchmark's CI subset, simulated), and zero when it does not (ci-03): no match stands without the
    gate, and a count is never below zero."""
    counted = {}
    for index, name in ((1, "ci-01"), (3, "ci-03")):
        frame = render.render_frame(suite.build_suite("ci")[index], stars.TileCatalog())
        facts = frame.truth.metadata
        solution = phone_photo_solver.solve_phone_photo(
            frame.rgb, obs_utc_iso=facts["utc"], observer_lat=facts["latDeg"], observer_lon=facts["lonDeg"],
            camera_label=facts["camera"], source_asset_id=f"src-{name}", source_sha256=hashlib.sha256(frame.jpeg_bytes).hexdigest(),
        )
        written = solution.to_contract_dict()
        counted[name] = (solution.status, written["quality"]["matchedInlierCount"], sum(f["evidenceClass"] == "catalog_matched" for f in written["findings"]))
    status, matched, matches = counted["ci-01"]
    assert status == "calibrated" and matched == matches > 0, counted
    assert counted["ci-03"] == ("unresolved", 0, 0), counted


def test_the_horizon_beacon_keeps_the_centroid_uncertainty_its_width_gives() -> None:
    """The Moon finding a horizon solve writes carries the beacon's detection as measured, its centroid's uncertainty
    its width over 2.355 times its SNR (a Gaussian's FWHM is 2.355 sigma), the number main writes."""
    # The frame of tests/integration/test_no_default_moment.py that the solver lines up: dark sky over a lit foreground
    # from 55% of the height, an 8 px block taken to be the Moon, at the pose tests' synthetic moment and place
    frame = np.zeros((1792, 1344), dtype=np.float64)
    frame[int(1792 * 0.55):, :] = 40.0
    frame[118:126, 930:938] = 240.0
    solution = horizon_solver.solve_horizon_and_beacon_pose(
        frame, obs_utc_iso=MOON_MOMENT, observer_lat=PLACE["latDeg"], observer_lon=PLACE["lonDeg"],
        source_asset_id=ROUTE_SOURCE["sourceAssetId"], source_sha256=hashlib.sha256(frame.tobytes()).hexdigest(),
    )
    assert solution.status == "approximate"
    (moon,) = [finding for finding in solution.findings if finding.evidence_class == "observed"]
    measured = moon.source_detection
    assert measured.fwhm_px > 0.0 and measured.snr > 0.0
    # Equal by construction: the same quotient of the record's own width and SNR (on any machine, whatever the last
    # bits of those two measured numbers)
    assert measured.centroid_uncertainty_px == measured.fwhm_px / (2.355 * measured.snr)
    written = next(f for f in solution.to_contract_dict()["findings"] if f["evidenceClass"] == "observed")
    assert written["sourceDetection"]["centroidUncertaintyPx"] == measured.centroid_uncertainty_px


@st.composite
def held_out_residuals(draw: st.DrawFn) -> tuple[list[float], float]:
    """Held-out residuals as the phone solver has them, with its inlier gate: pairs within its association radius,
    some inside the gate and some outside it, in any order."""
    gate_px = draw(st.floats(min_value=1.8, max_value=WIDEST_INLIER_GATE_PX, allow_nan=False))
    inside = draw(st.lists(st.floats(min_value=0.0, max_value=gate_px, allow_nan=False), max_size=40))
    outside = draw(st.lists(st.floats(min_value=gate_px, max_value=6.0, exclude_min=True, allow_nan=False), max_size=40))
    return list(draw(st.permutations(inside + outside))), gate_px


@settings(max_examples=400, deadline=None)
@given(case=held_out_residuals())
@example(case=([0.5] * 6 + [5.5] * 7, 4.0))
@example(case=([0.5] * 6 + [5.5] * 5, 4.0))
@example(case=([0.2, 0.3, 0.4, 0.5, 0.6, 0.7] + [3.9] * 9, 4.0))
@example(case=([3.0] * 12, 4.0))
@example(case=([], 3.5))
@example(case=([0.5] * 5, 1.8))
def test_pbt_contract_7_the_phone_solvers_record_builds_whatever_its_gate_judged(case: tuple[list[float], float]) -> None:
    """P-CONTRACT-7. Whatever held-out residuals the phone solver has, the quality record it builds from them is one
    the contract takes, whether its gate passed or not; and a gate that passed stands at or above the contract's
    held-out gate on the very numbers the record reports."""
    residuals, gate_px = case
    passed = _phone_gate(residuals, gate_px)
    event(f"the gate passed: {passed}")
    record = _phone_quality(residuals, passed, gate_px)
    pairs, inliers, median, _ = phone_photo_solver.held_out_residual_statistics(residuals, gate_px)
    assert (record.held_out_pair_count, record.held_out_inliers, record.median_residual_px) == (pairs, inliers, median)
    assert record.passed_independent_verification is passed
    least, most = contract.held_out_gate()
    if passed:
        assert inliers >= least and median <= most
    if median is not None and median > most:
        assert not passed


# =============================================================================================
# The ingest contract's calendar days and finite numbers (services/contracts/ingest.py)
# =============================================================================================


def test_ingest_samples_are_judged_as_the_fixture_says() -> None:
    block = read_fixture()["ingest"]
    assert len(block["invalid"]) >= 10
    for case in block["valid"]:
        INGEST_MODELS[case["schema"]].model_validate(decode(case["sample"]))
    for case in block["invalid"]:
        with pytest.raises(ValidationError):
            INGEST_MODELS[case["schema"]].model_validate(decode(case["sample"]))


def test_ingest_numbers_json_cannot_carry_are_refused_as_zod_refuses_them() -> None:
    samples = ingest.build_ingest_samples()
    clip = samples["valid"]["SourceAsset"][1]
    photo = samples["valid"]["SourceAsset"][0]
    ingest.SourceAsset.model_validate(clip)
    for path in (["audioStreams", 0, "startPtsMs"], ["audioStreams", 0, "durationMs"], ["durationMs"]):
        with pytest.raises(ValidationError):
            ingest.SourceAsset.model_validate(patched(clip, [put(path, math.nan)]))
    for key in ("elevationM", "uncertaintyMeters"):
        with pytest.raises(ValidationError):
            ingest.SourceAsset.model_validate(patched(photo, [put(["observerLocation", key], math.nan)]))
    # An audio stream's numbers are the wire module's, finite on both sides (packages/contracts/src/media.ts)
    for path in (["audioStreams", 0, "startPtsMs"], ["audioStreams", 0, "durationMs"]):
        for beyond in (math.inf, -math.inf):
            with pytest.raises(ValidationError):
                ingest.SourceAsset.model_validate(patched(clip, [put(path, beyond)]))
    # packages/contracts/src/media.ts still takes an infinite elevation (z.number() with no bound), so the mirror
    # does too until that file refuses it: JSON cannot carry one
    ingest.SourceAsset.model_validate(patched(photo, [put(["observerLocation", "elevationM"], math.inf)]))


def test_ingest_times_at_the_ends_of_the_calendar_are_refused_not_raised() -> None:
    """A local time whose UTC falls outside years 1 to 9999 cannot be written, so no UTC matches it."""
    source = {"container": "exif", "tags": ["DateTimeOriginal", "OffsetTimeOriginal"]}
    for local, offset, utc in (
        ("9999-12-31T23:00:00", -120, "9999-12-31T23:00:00Z"),
        ("0001-01-01T00:30:00", 120, "0001-01-01T00:30:00Z"),
    ):
        with pytest.raises(ValidationError):
            ingest.CaptureTimeHint.model_validate(
                {"localDateTime": local, "utcOffsetMinutes": offset, "utc": utc, "marks": "recorded", "source": source}
            )


def test_a_source_assets_capture_time_is_judged_by_zods_own_pattern() -> None:
    photo = ingest.build_ingest_samples()["valid"]["SourceAsset"][0]
    rule = read_fixture()["rules"]["utcInstant"]
    for moment in rule["accept"]:
        ingest.SourceAsset.model_validate({**photo, "capturedAtUTC": moment})
    for moment in rule["refuse"]:
        with pytest.raises(ValidationError):
            ingest.SourceAsset.model_validate({**photo, "capturedAtUTC": moment})


# =============================================================================================
# Real data
# =============================================================================================


def _as_predicted_star(reference: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"finding-predicted-{reference['id']}", "evidenceClass": "predicted", "title": reference["commonName"],
        "subtitle": "Predicted star", "catalogRef": reference,
        "skyPosition": {"raDeg": reference["raDeg"], "decDeg": reference["decDeg"], "uncertaintyArcsec": 12.0},
        "evidence": {
            "id": f"ev-{reference['id']}", "evidenceClass": "predicted", "verificationMethod": "Predicted from its catalog position",
            "limitations": ["Predicted, not measured in any image."], "verifiedByIndependentHoldout": False,
        },
    }


def test_every_pinned_star_gives_a_catalog_reference_the_contract_accepts() -> None:
    assert len(ALL_SKY_CATALOG_STARS) >= 71
    for star in ALL_SKY_CATALOG_STARS:
        sample = _as_predicted_star(json.loads(json.dumps(star.to_catalog_reference_dict(), allow_nan=False)))
        assert contract.parse_celestial_finding(sample).to_contract_dict() == sample, star.id


def test_the_phone_solvers_tile_stars_give_catalog_references_the_contract_accepts() -> None:
    """Real Gaia DR3 and Hipparcos rows from the tracked tiles, as the phone solver turns a matched one into a
    catalog reference: every 37th of the stars it can name, so the run stays short."""
    index = phone_photo_solver._get_tile_index()
    rows = range(0, len(index.data["sid"]), 37)
    checked = 0
    for row in rows:
        star = index.to_catalog_star(row, float(index.data["ra0"][row]), float(index.data["dec0"][row]), index.epoch_jyear)
        if star is None:
            continue
        CatalogReference.model_validate(json.loads(json.dumps(star.to_catalog_reference_dict(), allow_nan=False)))
        checked += 1
    assert checked >= 1000


def test_the_ephemeris_writes_findings_the_contract_accepts() -> None:
    """services/science/ephemeris.py writes its finding dicts itself, not through the solution dataclasses: each body
    it predicts for the synthetic moment and open-ocean place parses, with a recording behind it and without."""
    bodies = compute_solar_system_ephemerides(obs_utc_iso=MOMENT, lat_deg=PLACE["latDeg"], lon_deg=PLACE["lonDeg"])
    assert len(bodies) >= 6
    for body in bodies:
        for source in (None, SHA):
            sample = json.loads(json.dumps(body.to_contract_dict(source_sha256=source), allow_nan=False))
            assert contract.parse_celestial_finding(sample).to_contract_dict() == sample, body.name


def test_a_solution_the_solve_job_stores_parses_as_it_was_stored(tmp_path: Path) -> None:
    """Through the job tests' store: a rendered starfield is uploaded and solved, and the stored solution.json, read
    back from disk, is one the parser accepts and returns unchanged."""
    store = IngestStore(tmp_path / "store", max_bytes=10_000_000)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        client = TestClient(app)
        frame, _, _ = render_synthetic_starfield(width=WIDTH, height=HEIGHT, read_noise_sigma=1.8, flux_scale=1100.0, seed=42)
        receipt = client.post("/api/ingest", content=_png(frame), headers={"content-type": "image/png"})
        assert receipt.status_code == 201
        sha = receipt.json()["sourceAsset"]["sha256"]
        assert client.post(f"/api/ingest/{sha}/solve").json()["state"] == "succeeded"
        stored = read_solution(store, sha)
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)
    assert stored is not None and stored["status"] == "calibrated"
    parsed = contract.parse_observation_solution(stored)
    assert parsed.to_contract_dict() == stored
    assert {f["evidenceClass"] for f in stored["findings"]} == {"observed", "catalog_matched", "predicted"}
