"""OpenNGC objects inside a verified solved frame become predicted findings (services/science/deep_sky_findings.py).

Real data: the real OpenNGC v20260501 rows and the real phone-photo solver. No real camera frame solves with a
verified WCS yet (docs/science/PHONE_PHOTO_BENCHMARK.md, "Unsolved on real data"). So the frame is the phone-photo
benchmark's rendered Orion field ci-00, whose true camera is known: the check is against the true camera, not only
against the solved WCS.

- The solved ci-00 frame gives a predicted finding for M42 (NGC1976) within 3 px of where the true camera puts it.
  Its catalogRef is valid under the contract mirror, its title is the common name, and its subtitle carries the
  licence
- Only a calibrated solution with a verified WCS gives findings: not an unverified one, and not an approximate one
  marked as passed; a finding stands on one catalog row, a star or a deep-sky object
- An object inside the frame is kept when it has a common name or a Messier number, when its magnitude (V, else B
  only to decide) is no fainter than the faintest star the solution matched, or when its major axis spans at least
  3 px in the frame. Kept objects come brightest first, those without a magnitude last, and the rest are counted
- On ci-00, every object inside the frame is kept exactly when a rule holds for it
- No Dup or NonEx row is emitted; the object a Dup names is emitted once, from its own row
- Titles: the common name, else NGC or IC written plainly; an id writes a space as a hyphen; a magnitude is V only
- The position is worded as the precision OpenNGC prints, never as plus or minus
- PBT: through random verified pointings, the kept set is exactly the objects inside the frame that a rule keeps
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
PASSED = {
    "detected_candidate_count": 20, "matched_inlier_count": 12, "held_out_inliers": 7, "median_residual_px": 0.4,
    "p95_residual_px": 0.9, "median_residual_arcsec": 0.1, "passed_independent_verification": True,
    "held_out_pair_count": 7,
}


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


def _at() -> dict[str, int]:
    return {column: i for i, column in enumerate(dsf.openngc_table()["columns"])}


def _row(name: str) -> list:
    (row,) = [r for r in dsf.openngc_table()["rows"] if r[0] == name]
    return row


def _inside(solution: ObservationSolution, width: int, height: int) -> list[tuple[list, float]]:
    """Every emittable row whose centre the solution's WCS puts inside the frame, with its major axis in pixels."""
    at = _at()
    found = []
    for row in dsf._candidate_rows():
        x, y = solution.wcs.world_to_pixel(row[at["raDeg"]], row[at["decDeg"]])
        if math.isfinite(x) and math.isfinite(y) and 0.0 <= x < width and 0.0 <= y < height:
            span = math.nan
            if row[at["majorAxisArcmin"]] is not None:
                half = row[at["majorAxisArcmin"]] / 120.0
                xn, yn = solution.wcs.world_to_pixel(row[at["raDeg"]], min(90.0, row[at["decDeg"]] + half))
                xs, ys = solution.wcs.world_to_pixel(row[at["raDeg"]], max(-90.0, row[at["decDeg"]] - half))
                span = math.hypot(xn - xs, yn - ys)
            found.append((row, span))
    return found


def test_the_solved_orion_frame_places_m42_where_the_true_camera_puts_it(solved_ci00) -> None:
    solution, frame = solved_ci00
    assert solution.status == "calibrated" and solution.has_verified_wcs
    height, width = frame.rgb.shape[:2]
    prediction = dsf.predict_deep_sky_findings(solution, width, height)
    by_id = {f.id: f for f in prediction.findings}
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
    assert all(f.evidence_class == "predicted" for f in prediction.findings)
    assert len(by_id) == len(prediction.findings)


def test_on_ci00_every_object_inside_the_frame_is_kept_exactly_when_a_rule_holds(solved_ci00) -> None:
    solution, frame = solved_ci00
    height, width = frame.rgb.shape[:2]
    prediction = dsf.predict_deep_sky_findings(solution, width, height)
    faintest = dsf.faintest_matched_magnitude(solution)
    matched = [f.catalog_star.mag for f in solution.findings if f.evidence_class == "catalog_matched"]
    assert matched and faintest == max(matched)  # the faintest star the solution matched
    at = _at()
    inside = _inside(solution, width, height)
    expected = {dsf.finding_id(row[at["name"]]) for row, span in inside if dsf.keep_reason(row, faintest, span)}
    assert {f.id for f in prediction.findings} == expected
    assert prediction.not_included == len(inside) - len(expected) > 0
    assert prediction.warning == f"{prediction.not_included} more catalog objects in this frame are not included"


def test_only_a_calibrated_solution_with_a_verified_wcs_gives_findings(solved_ci00) -> None:
    solution, frame = solved_ci00
    height, width = frame.rgb.shape[:2]
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
    assert dsf.predict_deep_sky_findings(unverified, width, height).findings == ()
    # The models do not yet refuse an approximate solution marked as passed (integrity-27): the module does
    approximate = replace(solution, id="sol-approximate", status="approximate", findings=[])
    assert approximate.has_verified_wcs
    empty = dsf.predict_deep_sky_findings(approximate, width, height)
    assert empty.findings == () and empty.not_included == 0 and empty.warning is None


def test_a_finding_stands_on_one_catalog_row_a_star_or_a_deep_sky_object(solved_ci00) -> None:
    solution, _ = solved_ci00
    star = next(f for f in solution.findings if f.catalog_star is not None)
    with pytest.raises(ValueError, match="one catalog row"):
        replace(star, deep_sky_reference=dsf.catalog_reference(_row("NGC1976")))


def test_each_keep_rule_on_real_rows() -> None:
    at = _at()
    rows = dsf._candidate_rows()
    plain = [r for r in rows if not r[at["commonNames"]] and r[at["messier"]] is None]
    assert dsf.keep_reason(_row("NGC1976"), None, math.nan) == "name"
    messier_only = next(r for r in rows if r[at["messier"]] is not None and not r[at["commonNames"]])
    assert dsf.keep_reason(messier_only, None, math.nan) == "messier"
    # Magnitude: V when OpenNGC gives it, no fainter than the faintest matched star
    with_v = next(r for r in plain if r[at["vMag"]] is not None)
    v = with_v[at["vMag"]]
    assert dsf.keep_reason(with_v, v, math.nan) == "magnitude"
    assert dsf.keep_reason(with_v, v - 0.01, math.nan) is None
    assert dsf.keep_reason(with_v, None, math.nan) is None
    # B decides only when V is missing, and is never shown as V
    only_b = next(r for r in plain if r[at["vMag"]] is None and r[at["bMag"]] is not None)
    assert dsf.keep_reason(only_b, only_b[at["bMag"]], math.nan) == "magnitude"
    assert "apparentMagnitude" not in dsf.catalog_reference(only_b)
    # Size: a major axis spanning at least 3 px in this frame
    assert dsf.keep_reason(with_v, v - 0.01, 3.0) == "size"
    assert dsf.keep_reason(with_v, v - 0.01, 2.99) is None
    assert dsf.keep_reason(with_v, v - 0.01, math.nan) is None


def test_kept_objects_come_brightest_first_and_those_without_a_magnitude_last(solved_ci00) -> None:
    solution, frame = solved_ci00
    height, width = frame.rgb.shape[:2]
    findings = dsf.predict_deep_sky_findings(solution, width, height).findings
    at = _at()
    rows = {dsf.finding_id(r[at["name"]]): r for r in dsf._candidate_rows()}
    magnitudes = [dsf.decision_magnitude(rows[f.id]) for f in findings]
    known = [m for m in magnitudes if m is not None]
    assert known == sorted(known)
    assert magnitudes[: len(known)] == known  # every missing magnitude comes after every known one


def test_the_warning_counts_what_is_not_included() -> None:
    assert dsf.DeepSkyPrediction(findings=(), not_included=0).warning is None
    assert dsf.DeepSkyPrediction(findings=(), not_included=1).warning == (
        "1 more catalog object in this frame is not included"
    )
    assert dsf.DeepSkyPrediction(findings=(), not_included=571).warning == (
        "571 more catalog objects in this frame are not included"
    )


def test_the_position_is_worded_as_printed_precision_never_plus_or_minus() -> None:
    assert "Coordinates printed to 0.15 arcsec in OpenNGC; it gives no accuracy per row." in dsf.LIMITATIONS
    assert (
        "The centre of a large or diffuse object is a convention, known far less precisely than its printed "
        "coordinates."
    ) in dsf.LIMITATIONS
    assert not any("±" in text or "plus or minus" in text.lower() for text in dsf.LIMITATIONS)


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
        observer_lon=MOMENT["observer_lon"], wcs=wcs, quality=SolutionQuality(**PASSED),
        solver=SolverIdentity(name="hand-built in this test", version="0", catalog_release="none"),
    )


@settings(max_examples=40, deadline=None)
@given(ra0=st.floats(0.0, 359.999), dec0=st.floats(-89.0, 89.0), rotation=st.floats(-180.0, 180.0))
@example(ra0=83.82, dec0=-5.39, rotation=0.0)  # M42 at the frame centre
@example(ra0=10.68, dec0=41.27, rotation=37.0)  # M31
@example(ra0=0.0, dec0=89.0, rotation=90.0)  # the pole, where right ascension wraps
@example(ra0=359.9, dec0=0.0, rotation=-120.0)  # right ascension near 360
def test_pbt_the_kept_set_is_exactly_the_objects_inside_the_frame_a_rule_keeps(
    ra0: float, dec0: float, rotation: float
) -> None:
    solution = _verified(ra0, dec0, rotation)
    prediction = dsf.predict_deep_sky_findings(solution, 640, 480)
    at = _at()
    # A hand-built solution matched no star, so only the name, Messier and size rules can keep an object
    inside = _inside(solution, 640, 480)
    expected = {dsf.finding_id(row[at["name"]]) for row, span in inside if dsf.keep_reason(row, None, span)}
    assert {f.id for f in prediction.findings} == expected
    assert prediction.not_included == len(inside) - len(expected)
