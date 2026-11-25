"""The solver's record of a calibration's candidate source (anet-stage-zero; the lead's decision of 2026-11-24 21:56).

A calibrated solution whose WCS was refined from an outside program's candidate names the program, its version line
and the index file by name; every other solution names none, and writes no such key, so a record made with the stage
off is the record main makes. The contract's own parser judges each written record.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from services.contracts import astronomy as contract
from services.science.wcs_models import CandidateSource, ContractRefusal, ObservationSolution, SolutionQuality, SolverIdentity, WCSModel

RECORD_SHA = hashlib.sha256(b"synthetic candidate source test recording").hexdigest()
PASSED = SolutionQuality(
    detected_candidate_count=20, matched_inlier_count=12, held_out_inliers=7, median_residual_px=0.4,
    p95_residual_px=0.9, median_residual_arcsec=0.1, passed_independent_verification=True, held_out_pair_count=7,
)
UNPASSED = SolutionQuality(
    detected_candidate_count=20, matched_inlier_count=0, held_out_inliers=0, median_residual_px=None,
    p95_residual_px=None, median_residual_arcsec=None, passed_independent_verification=False, held_out_pair_count=0,
)
ASTROMETRY = CandidateSource(program="astrometry.net", version="0.97", index_file="index-4117.fits")


def _calibrated(**more) -> ObservationSolution:
    wcs = WCSModel(
        crval_ra_deg=83.8, crval_dec_deg=-5.4, crpix_x=320.0, crpix_y=240.0, scale_deg_per_px=0.05, rotation_deg=0.0,
        width=640, height=480,
    )
    return ObservationSolution(
        id="sol-candidate", source_asset_id="hand-built", source_sha256=RECORD_SHA, frame_time_sec=0.0, revision=1,
        status="calibrated", wcs=wcs, quality=PASSED,
        solver=SolverIdentity(name="hand-built in this test", version="0", catalog_release="none"), **more,
    )


def test_a_calibration_from_an_outside_candidate_names_its_source_and_the_contract_takes_it() -> None:
    written = _calibrated(candidate_source=ASTROMETRY, candidate_stages=("astrometry.net",)).to_contract_dict()
    assert written["candidateSource"] == {"program": "astrometry.net", "version": "0.97", "indexFile": "index-4117.fits"}
    assert written["candidateStages"] == ["astrometry.net"]
    parsed = contract.parse_observation_solution(written)
    assert parsed.candidate_source is not None and parsed.candidate_source.index_file == "index-4117.fits"


def test_a_candidate_source_requires_its_program_among_the_candidate_stages() -> None:
    with pytest.raises(ContractRefusal, match="among its candidate stages"):
        _calibrated(candidate_source=ASTROMETRY)


def test_a_solution_the_apps_own_search_made_writes_no_candidate_source() -> None:
    # With the stage off nothing changes: the record has the keys main's has, and no candidateSource
    written = _calibrated().to_contract_dict()
    assert "candidateSource" not in written
    staged = _calibrated(candidate_source=ASTROMETRY, candidate_stages=("astrometry.net",)).to_contract_dict()
    assert written == {k: v for k, v in staged.items() if k not in ("candidateSource", "candidateStages")}


@pytest.mark.parametrize("status", ["unresolved", "ambiguous", "predicted_only"])
def test_only_a_calibrated_solution_names_a_candidate_source(status: str) -> None:
    with pytest.raises(ContractRefusal, match="named only on a calibrated solution"):
        replace(_calibrated(candidate_source=ASTROMETRY, candidate_stages=("astrometry.net",)), status=status, quality=UNPASSED, wcs=None)
    assert contract.CANDIDATE_SOURCE_CALIBRATED.startswith("A candidate's source is named only on a calibrated solution")


@pytest.mark.parametrize(("field", "value"), [
    ("program", "another.solver"),
    ("version", ""),
    ("version", "/opt/homebrew/bin/solve-field"),
    ("version", "0.97 (built here)"),
    ("index_file", "/opt/astrometry/index-4117.fits"),
    ("index_file", "index-4117"),
    ("index_file", "index-4117.fits\n"),
])
def test_a_candidate_source_holds_its_program_its_version_and_an_index_file_by_name_alone(field: str, value: str) -> None:
    with pytest.raises(ContractRefusal):
        replace(ASTROMETRY, **{field: value})
