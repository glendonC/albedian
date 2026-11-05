"""No record carries an id nobody gave it, and an export manifest cites only what it shows.

A detection measured without its recording's id carries none, where it used to read "asset-0". A solution
serializes its detections under its own recording's id, since they were measured in it, and refuses one measured
in another recording; a detection or finding serialized on its own needs that id. The restore and export
manifests take the ids only their caller knows, and an export manifest cites the catalogs of what it shows, which
for a remux of restored frames is nothing.

Covers:
- detect_point_sources gives detections no asset id unless the caller names the recording
- A calibrated and an unresolved plate solve serialize every detection under the solution's own id
- A detection or finding serialized alone, with no recording id known, is refused
- A solution refuses a detection measured in another recording
- The restore and export manifests refuse to serialize without their ids and use the ones given
- An export manifest cites nothing it does not show
- Real data (owner_media): the decoded frames of the owner's clips, solved, carry their own id on every detection
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from services.media.models import ExportRemuxResult
from services.media.runtime import MediaExperimentRuntime
from services.science.solver import (
    SourceDetection,
    detect_point_sources,
    render_synthetic_starfield,
    solve_and_verify_field,
)

ARTIFACTS = Path(__file__).resolve().parents[2] / "data" / "artifacts"
# A synthetic moment and place that point at nobody: the open North Atlantic, as in the other tests
MOMENT = {"obs_utc_iso": "2030-01-01T02:00:00Z", "observer_lat": 30.0, "observer_lon": -40.0}
# The SHA-256 the synthetic records cite: of words, never of float output, which differs by platform
SHA = hashlib.sha256(b"synthetic starfield, seed 42").hexdigest()
FEW = [
    SourceDetection(x_px=x, y_px=y, flux=4200.0, snr=18.0, fwhm_px=2.4, centroid_uncertainty_px=0.12)
    for x, y in [(120.0, 80.0), (480.5, 260.25), (800.0, 410.0)]
]


def _starfield() -> np.ndarray:
    frame, _, _ = render_synthetic_starfield(width=960, height=540, read_noise_sigma=1.8, flux_scale=1100.0, seed=42)
    return frame


def _serialized_detections(payload: dict) -> list[dict]:
    return [finding["sourceDetection"] for finding in payload["findings"] if "sourceDetection" in finding]


def test_detections_carry_no_asset_id_unless_the_caller_names_the_recording() -> None:
    frame = _starfield()
    unnamed = detect_point_sources(frame, snr_threshold=4.0)
    named = detect_point_sources(frame, snr_threshold=4.0, asset_id="synthetic-field-42")
    assert unnamed and all(det.asset_id is None for det in unnamed)
    assert [det.asset_id for det in named] == ["synthetic-field-42"] * len(unnamed)


def test_a_solution_serializes_its_detections_under_its_own_id() -> None:
    calibrated = solve_and_verify_field(
        detect_point_sources(_starfield(), snr_threshold=4.0), 960, 540, **MOMENT,
        source_asset_id="synthetic-field-42", source_sha256=SHA,
    )
    unresolved = solve_and_verify_field(FEW, 960, 540, **MOMENT, source_asset_id="synthetic-three", source_sha256=SHA)
    assert (calibrated.status, unresolved.status) == ("calibrated", "unresolved")
    for solution in (calibrated, unresolved):
        payload = solution.to_contract_dict()
        detections = _serialized_detections(payload)
        # Every finding of a plate solve carries the detection it rests on
        assert len(detections) == len(solution.findings) > 0
        assert {det["assetId"] for det in detections} == {solution.source_asset_id}
        assert "asset-0" not in json.dumps(payload)


def test_a_detection_or_finding_alone_needs_its_recordings_id() -> None:
    detection = FEW[0]
    with pytest.raises(ValueError, match="recording"):
        detection.to_contract_dict()
    assert detection.to_contract_dict(asset_id="synthetic-three")["assetId"] == "synthetic-three"
    # A detection that names its recording keeps it, whatever id it is given: it was measured there
    own = replace(detection, asset_id="synthetic-own")
    assert own.to_contract_dict()["assetId"] == own.to_contract_dict(asset_id="synthetic-three")["assetId"] == "synthetic-own"
    solution = solve_and_verify_field(FEW, 960, 540, **MOMENT, source_asset_id="synthetic-three", source_sha256=SHA)
    finding = solution.findings[0]
    with pytest.raises(ValueError, match="recording"):
        finding.to_contract_dict()
    assert finding.to_contract_dict(source_asset_id="synthetic-three")["sourceDetection"]["assetId"] == "synthetic-three"


def test_a_solution_refuses_a_detection_from_another_recording() -> None:
    foreign = [replace(FEW[0], asset_id="synthetic-other"), *FEW[1:]]
    with pytest.raises(ValueError, match="synthetic-other"):
        solve_and_verify_field(foreign, 960, 540, **MOMENT, source_asset_id="synthetic-three", source_sha256=SHA)
    named = [replace(det, asset_id="synthetic-three") for det in FEW]
    solution = solve_and_verify_field(named, 960, 540, **MOMENT, source_asset_id="synthetic-three", source_sha256=SHA)
    assert {det["assetId"] for det in _serialized_detections(solution.to_contract_dict())} == {"synthetic-three"}


def test_the_restore_manifest_needs_its_asset_id() -> None:
    frame, _, _ = render_synthetic_starfield(width=320, height=180, read_noise_sigma=1.8, flux_scale=1100.0, seed=7)
    result = MediaExperimentRuntime().execute_restoration_branch([frame], profile="star_protected", preserve_stars=True)
    with pytest.raises(TypeError):
        result.to_contract_dict()
    with pytest.raises(TypeError):
        result.to_contract_dict("synthetic-small")
    assert result.to_contract_dict(source_asset_id="synthetic-small")["sourceAssetId"] == "synthetic-small"


def test_the_export_manifest_needs_its_ids_and_cites_only_what_it_shows(tmp_path) -> None:
    manifest = ExportRemuxResult(
        id="export-synthetic", output_path=str(tmp_path / "restored.mp4"), output_sha256="ab" * 32,
        source_sha256="cd" * 32, video_codec="h264", audio_preserved=True, audio_codec="aac",
        video_duration_sec=2.0, audio_duration_sec=2.0, duration_drift_ms=0.0, audio_sync_offset_ms=0.0,
        stream_types=["video", "audio"], frame_count=48, fps=24,
    )
    ids = {
        "session_id": "session-synthetic", "source_asset_id": "synthetic-field-42",
        "variant_id": "variant-restored", "graph_id": "graph-temporal",
    }
    with pytest.raises(TypeError):
        manifest.to_contract_dict()
    for missing in [*ids, "catalog_citations"]:
        given = {**ids, "catalog_citations": ()}
        del given[missing]
        with pytest.raises(TypeError):
            manifest.to_contract_dict(**given)
    payload = manifest.to_contract_dict(**ids, catalog_citations=())
    assert [payload[key] for key in ("sessionId", "sourceAssetId", "exportedVariantId", "processingGraphId")] == [
        *ids.values()
    ]
    # A remux of restored frames shows no catalog content, so it cites no catalog
    assert payload["catalogCitations"] == []
    assert "Gaia" not in json.dumps(payload) and "Bailer-Jones" not in json.dumps(payload)
    cited = manifest.to_contract_dict(**ids, catalog_citations=["Gaia Collaboration (2023) Gaia DR3"])
    assert cited["catalogCitations"] == ["Gaia Collaboration (2023) Gaia DR3"]


@pytest.mark.owner_media("data/artifacts")
def test_real_frames_solved_carry_their_own_id_on_every_detection() -> None:
    # The frame each real clip's experiment run decoded and saved before any processing, solved for a synthetic
    # moment (the owner's is not needed to check whose detections these are)
    frames = sorted(ARTIFACTS.glob("*/00_original_source.png"))
    assert frames, "data/artifacts holds no decoded original frame"
    for path in frames:
        rgb = np.asarray(Image.open(path).convert("RGB"), dtype=np.float64)
        luma = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
        height, width = luma.shape
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        detections = detect_point_sources(luma)
        assert detections and all(det.asset_id is None for det in detections)
        solution = solve_and_verify_field(
            detections, width, height, **MOMENT, source_asset_id=f"frame-{digest[:12]}", source_sha256=digest
        )
        payload = solution.to_contract_dict()
        assert {det["assetId"] for det in _serialized_detections(payload)} == {f"frame-{digest[:12]}"}
        assert "asset-0" not in json.dumps(payload)
