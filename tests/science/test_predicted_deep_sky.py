"""Deep-sky objects in every sky the app predicts (FIELD field-6): OpenNGC rows as predicted findings.

Before this unit no upload had a deep-sky finding: `predict_deep_sky_findings` (services/science/deep_sky_projection.py)
was called by no production code. Now:
- Every predicted sky (a solution with a recorded time and place: predicted only, lined up or calibrated) lists the
  OpenNGC objects above the horizon that are Messier or Caldwell objects (OpenNGC's own columns) or as bright as the
  predicted sky's star depth (V, else B, at most PREDICTED_STAR_DEPTH_V), leaving out the rows OpenNGC types as a
  single star, a double star or a nova (the star catalog already gives those), each `predicted`, at its J2000 centre
  placed for the moment, with its source and limits, and never a source detection
- A calibrated solution with a verified WCS also gets the objects inside its frame that the frame's keep rules hold
  for (`predict_deep_sky_findings`), each once
Synthetic moments in 2031 at the fixtures' open-ocean points; the Orion frame is the phone-photo benchmark's rendered ci-00.
"""

from __future__ import annotations

import hashlib

import pytest

from research.benchmarks.phone_photos import render, stars, suite
from services.science import deep_sky_findings as dsf
from services.science.deep_sky_selection import sky_reason
from services.science.phone_photo_solver import solve_phone_photo
from services.science.predicted_sky import PREDICTED_STAR_DEPTH_V, complete_sky, predicted_sky_findings

SHA = hashlib.sha256(b"synthetic field-6 recording").hexdigest()
# Local mean midnight at the south Pacific open-ocean point (-45, -130) of tests/integration/test_ephemeris_horizons.py
SOUTH = {"moment": "2031-01-01T08:40:00Z", "lat": -45.0, "lon": -130.0}


def _row(name: str) -> list:
    (row,) = [r for r in dsf.openngc_table()["rows"] if r[0] == name]
    return row


def _south() -> dict:
    found = predicted_sky_findings(
        SOUTH["moment"], SOUTH["lat"], SOUTH["lon"], solution_id="sol_field6", source_sha256=SHA
    )
    return {f.id: f for f in found}


def test_a_southern_predicted_sky_names_the_magellanic_clouds_and_the_brightest_southern_objects() -> None:
    by_id = _south()
    for name, title in (
        ("ESO056-115", "Large Magellanic Cloud"),
        ("NGC0292", "Small Magellanic Cloud"),
        ("NGC5139", "Omega Centauri"),
        ("NGC0104", "47 Tuc Cluster"),
        ("NGC3372", "Carina Nebula"),
    ):
        finding = by_id[f"finding-openngc-{name}"]
        assert finding.evidence_class == "predicted" and finding.title == title
        assert finding.alt_deg is not None and finding.alt_deg > 0.0 and finding.az_deg is not None
        assert finding.deep_sky_reference["provider"] == "OpenNGC" and finding.catalog_star is None
        assert finding.source_detection is None and not finding.verified_by_holdout
        assert any("not measured in the recording" in lim for lim in finding.limitations)
        assert any("OpenNGC" in lim and "J2000" in lim for lim in finding.limitations)
        payload = finding.to_contract_dict()
        assert payload["catalogRef"]["objectType"] == "deep_sky"
        assert "projected through the solved WCS" not in payload["evidence"]["verificationMethod"]


def test_only_objects_above_the_horizon_are_listed() -> None:
    by_id = _south()
    deep = [f for f in by_id.values() if f.id.startswith("finding-openngc-")]
    assert deep and all(f.alt_deg > 0.0 for f in deep)
    # The Andromeda Galaxy (dec +41) is below the horizon from 45 south at this moment
    assert "finding-openngc-NGC0224" not in by_id


def test_the_rule_is_messier_or_caldwell_or_the_star_depth_and_never_a_star() -> None:
    assert PREDICTED_STAR_DEPTH_V == 4.5
    assert sky_reason(_row("ESO056-115")) == "magnitude"  # the LMC: V 0.29, in neither list
    assert sky_reason(_row("NGC1976")) == "messier"  # M42
    assert sky_reason(_row("NGC5139")) == "caldwell"  # C 80, V 5.33 in OpenNGC
    assert sky_reason(_row("NGC0292")) == "magnitude"  # the SMC: V 2.3, in neither list
    assert sky_reason(_row("NGC3372")) == "caldwell"  # C 92, B 3.0 and no V
    assert sky_reason(_row("NGC4833")) == "caldwell"  # C 105, no common name
    assert sky_reason(_row("NGC4755")) == "caldwell"  # C 94, the Jewel Box, no magnitude in OpenNGC
    assert sky_reason(_row("NGC6744")) == "caldwell"
    assert sky_reason(_row("NGC5128")) == "caldwell"
    # Bright rows OpenNGC types as a star, a double star or a nova are left to the star catalog
    assert sky_reason(_row("NGC1990")) is None  # typed * (Alnilam), V 1.69
    assert sky_reason(_row("NGC7114")) is None  # typed * (a nova's star), V 3.0
    faint = next(r for r in dsf._candidate_rows() if r[1] == "G" and r[11] is None and not r[14] and (r[9] or 99) > 12)
    assert sky_reason(faint) is None


def test_the_star_depth_is_inclusive_and_v_decides_before_b() -> None:
    columns = dsf.openngc_table()["columns"]

    def synthetic(v: float | None, b: float | None, kind: str = "OCl") -> list:
        values = {"name": "SYN0001", "type": kind, "raDeg": 10.0, "decDeg": -50.0, "vMag": v, "bMag": b,
                  "messier": None, "identifiers": [], "commonNames": []}
        return [values.get(column) for column in columns]

    assert sky_reason(synthetic(4.5, None)) == "magnitude"
    assert sky_reason(synthetic(4.51, None)) is None
    assert sky_reason(synthetic(None, 4.5)) == "magnitude"
    assert sky_reason(synthetic(5.0, 3.0)) is None  # V given decides, B only stands in for a missing V
    assert sky_reason(synthetic(1.0, None, "**")) is None
    assert sky_reason(synthetic(1.0, None, "Nova")) is None


def test_every_predicted_deep_sky_finding_is_one_the_rule_keeps_and_each_once() -> None:
    by_id = _south()
    deep = [f for f in by_id.values() if f.id.startswith("finding-openngc-")]
    names = [f.deep_sky_reference["catalogSourceId"] for f in deep]
    assert len(names) == len(set(names))
    assert all(sky_reason(_row(name)) is not None for name in names)


@pytest.fixture(scope="module")
def calibrated_orion() -> tuple:
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
    height, width = frame.rgb.shape[:2]
    asset = {
        "capturedAtUTC": meta["utc"].replace("+00:00", "Z"),
        "observerLocation": {"latDeg": meta["latDeg"], "lonDeg": meta["lonDeg"]},
        "width": width,
        "height": height,
    }
    return solution, asset


def test_a_calibrated_solution_gets_the_objects_inside_its_frame_through_its_verified_wcs(calibrated_orion) -> None:
    solution, asset = calibrated_orion
    assert solution.status == "calibrated" and solution.has_verified_wcs
    done = complete_sky(solution, asset)
    ids = [f.id for f in done.findings]
    assert len(ids) == len(set(ids))
    m42 = next(f for f in done.findings if f.id == "finding-openngc-NGC1976")
    # Inside the frame: placed through the verified WCS, as the frame's own pass words it
    assert m42.evidence_class == "predicted" and m42.verification_method == dsf.VERIFICATION_METHOD
    inside = dsf.predict_deep_sky_findings(solution, asset["width"], asset["height"])
    assert {f.id for f in inside.findings} <= set(ids)
