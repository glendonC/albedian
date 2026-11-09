"""OpenNGC objects inside a verified solved frame become predicted findings (services/science/deep_sky_findings.py).

Real data: the real OpenNGC v20260501 rows and the real phone-photo solver. No real camera frame solves with a
verified WCS yet (docs/science/PHONE_PHOTO_BENCHMARK.md, "Unsolved on real data"). So the frame is the phone-photo
benchmark's rendered Orion field ci-00, whose true camera is known: the check is against the true camera, not only
against the solved WCS.

- The solved ci-00 frame gives a predicted finding for M42 (NGC1976) within 3 px of where the true camera puts it.
  Its catalogRef is valid under the contract mirror, its title is the common name, and its subtitle carries the
  licence
- An unverified solution gives none; a finding stands on one catalog row, a star or a deep-sky object
- No Dup or NonEx row is emitted; the object a Dup names is emitted once, from its own row
- Titles: the common name, else NGC or IC written plainly; an id writes a space as a hyphen; a magnitude is V only
- PBT: through random verified pointings, every emitted finding lies inside the frame, and every OpenNGC centre the
  WCS puts inside the frame is emitted
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import replace

import numpy as np
import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from research.benchmarks.phone_photos import render, stars, suite
from services.contracts.catalog_reference import CatalogReference
from services.science import deep_sky_findings as dsf
from services.science.phone_photo_solver import solve_phone_photo
from services.science.wcs_models import ObservationSolution, SolutionQuality, SolverIdentity, WCSModel

# A synthetic moment and place that point at nobody, as the shared test moment
MOMENT = {"obs_utc_iso": "2031-08-30T03:01:00Z", "observer_lat": 30.0, "observer_lon": -40.0}
RECORD_SHA = hashlib.sha256(b"synthetic deep-sky test recording").hexdigest()


@pytest.fixture(scope="module")
def solved_ci00() -> tuple[ObservationSolution, render.RenderedFrame]:
    frame = render.render_frame(suite.build_suite("ci")[0], stars.TileCatalog())
    meta = frame.truth.metadata
    solution = solve_phone_photo(
        frame.rgb,
        obs_utc_iso=meta["utc"],
        observer_lat=meta["latDeg"],
        observer_lon=meta["lonDeg"],
        camera_label=meta["camera"],
        source_asset_id="src_ci00",
        source_sha256=hashlib.sha256(frame.jpeg_bytes).hexdigest(),
        solution_id="sol_ci00",
    )
    return solution, frame


def _row(name: str) -> list:
    table = dsf.openngc_table()
    (row,) = [r for r in table["rows"] if r[0] == name]
    return row


def _at() -> dict[str, int]:
    return {column: i for i, column in enumerate(dsf.openngc_table()["columns"])}


def test_the_solved_orion_frame_places_m42_where_the_true_camera_puts_it(solved_ci00) -> None:
    solution, frame = solved_ci00
    assert solution.has_verified_wcs
    height, width = frame.rgb.shape[:2]
    findings = dsf.predict_deep_sky_findings(solution, width, height)
    by_id = {f.id: f for f in findings}
    m42 = by_id["finding-openngc-NGC1976"]
    assert m42.evidence_class == "predicted"
    assert m42.title == "Great Orion Nebula"
    assert m42.subtitle == "Star cluster + Nebula · Ori · OpenNGC CC BY-SA 4.0"
    # The true camera's projection, independent of the solved WCS
    truth = render.camera_truth_for(suite.build_suite("ci")[0])
    tx, ty = truth.icrs_to_pixel(np.array([m42.ra_deg]), np.array([m42.dec_deg]))
    sx, sy = solution.wcs.world_to_pixel(m42.ra_deg, m42.dec_deg)
    assert math.hypot(sx - float(tx[0]), sy - float(ty[0])) <= 3.0
    payload = m42.to_contract_dict(source_asset_id="src_ci00")
    reference = CatalogReference.model_validate(payload["catalogRef"])
    assert reference.provider == "OpenNGC" and reference.object_type == "deep_sky"
    assert payload["evidence"]["verificationMethod"] == dsf.VERIFICATION_METHOD
    assert payload["evidence"]["catalogReferenceId"] == "openngc-NGC1976"
    assert m42.common_name == "Great Orion Nebula"
    # Every finding is a prediction inside the frame, each object once
    assert all(f.evidence_class == "predicted" for f in findings)
    assert len(by_id) == len(findings)


def test_an_unverified_solution_gives_no_deep_sky_finding(solved_ci00) -> None:
    solution, frame = solved_ci00
    unverified = ObservationSolution(
        id="sol-unverified", source_asset_id="src_ci00", source_sha256=solution.source_sha256, frame_time_sec=0.0,
        revision=1, status="unresolved", wcs=solution.wcs,
        quality=SolutionQuality(
            detected_candidate_count=10, matched_inlier_count=0, held_out_inliers=0, median_residual_px=None,
            p95_residual_px=None, median_residual_arcsec=None, passed_independent_verification=False,
            held_out_pair_count=0,
        ),
        solver=SolverIdentity(name="hand-built in this test", version="0", catalog_release="none"),
    )
    height, width = frame.rgb.shape[:2]
    assert dsf.predict_deep_sky_findings(unverified, width, height) == []


def test_no_duplicate_or_nonexistent_row_is_emitted_and_a_duplicate_names_its_object() -> None:
    at = _at()
    emitted = {row[at["name"]] for row in dsf._candidate_rows()}
    table_rows = dsf.openngc_table()["rows"]
    duplicates = [r for r in table_rows if r[at["type"]] == dsf.DUPLICATE_TYPE]
    nonexistent = [r for r in table_rows if r[at["type"]] == dsf.NONEXISTENT_TYPE]
    assert duplicates and nonexistent
    assert not emitted & {r[at["name"]] for r in duplicates + nonexistent}
    # IC 11 is OpenNGC's duplicate of NGC 281: the object is emitted once, from its own row
    assert dsf.duplicated_name(_row("IC0011"), at) == "NGC0281"
    assert "NGC0281" in emitted and "IC0011" not in emitted


def test_a_finding_stands_on_one_catalog_row_a_star_or_a_deep_sky_object(solved_ci00) -> None:
    solution, _ = solved_ci00
    star = next(f for f in solution.findings if f.catalog_star is not None)
    with pytest.raises(ValueError, match="one catalog row"):
        replace(star, deep_sky_reference=dsf.catalog_reference(_row("NGC1976")))


def test_titles_ids_and_magnitudes() -> None:
    assert dsf.display_name("NGC1976", ["Great Orion Nebula", "Orion Nebula"]) == "Great Orion Nebula"
    assert dsf.display_name("IC0113", []) == "IC 113"
    assert dsf.display_name("NGC5195A", []) == "NGC 5195A"
    assert dsf.display_name("B033", []) == "B033"
    assert dsf.finding_id("IC0080 NED01") == "finding-openngc-IC0080-NED01"
    # M31 has a V magnitude (3.44); a row with only B has none
    assert dsf.catalog_reference(_row("NGC0224"))["apparentMagnitude"] == 3.44
    at = _at()
    only_b = next(r for r in dsf._candidate_rows() if r[at["vMag"]] is None and r[at["bMag"]] is not None)
    assert "apparentMagnitude" not in dsf.catalog_reference(only_b)
    CatalogReference.model_validate(dsf.catalog_reference(only_b))


def _verified(ra0: float, dec0: float, rotation: float, width: int = 640, height: int = 480) -> ObservationSolution:
    wcs = WCSModel(
        crval_ra_deg=ra0, crval_dec_deg=dec0, crpix_x=width / 2.0, crpix_y=height / 2.0, scale_deg_per_px=0.05,
        rotation_deg=rotation, width=width, height=height,
    )
    return ObservationSolution(
        id="sol-pbt", source_asset_id="hand-built", source_sha256=RECORD_SHA, frame_time_sec=0.0, revision=1,
        status="calibrated", observed_at_utc=MOMENT["obs_utc_iso"], observer_lat=MOMENT["observer_lat"],
        observer_lon=MOMENT["observer_lon"], wcs=wcs,
        quality=SolutionQuality(
            detected_candidate_count=20, matched_inlier_count=12, held_out_inliers=7, median_residual_px=0.4,
            p95_residual_px=0.9, median_residual_arcsec=0.1, passed_independent_verification=True,
            held_out_pair_count=7,
        ),
        solver=SolverIdentity(name="hand-built in this test", version="0", catalog_release="none"),
    )


@settings(max_examples=40, deadline=None)
@given(
    ra0=st.floats(0.0, 359.999), dec0=st.floats(-89.0, 89.0), rotation=st.floats(-180.0, 180.0),
)
@example(ra0=83.82, dec0=-5.39, rotation=0.0)  # M42 at the frame centre
@example(ra0=10.68, dec0=41.27, rotation=37.0)  # M31
@example(ra0=0.0, dec0=89.0, rotation=90.0)  # the pole, where right ascension wraps
@example(ra0=359.9, dec0=0.0, rotation=-120.0)  # right ascension near 360
def test_pbt_every_centre_inside_the_frame_is_emitted_and_nothing_outside(ra0: float, dec0: float, rotation: float) -> None:
    solution = _verified(ra0, dec0, rotation)
    findings = dsf.predict_deep_sky_findings(solution, 640, 480)
    emitted = {f.id for f in findings}
    at = _at()
    expected = set()
    for row in dsf._candidate_rows():
        x, y = solution.wcs.world_to_pixel(row[at["raDeg"]], row[at["decDeg"]])
        if math.isfinite(x) and math.isfinite(y) and 0.0 <= x < 640 and 0.0 <= y < 480:
            expected.add(dsf.finding_id(row[at["name"]]))
    assert emitted == expected
