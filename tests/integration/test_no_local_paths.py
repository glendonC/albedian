"""No record that can leave this machine carries a local filesystem path.

An export manifest named its output by the absolute file:// path the runtime wrote it to, which carries the account
folder (the owner's name) and every folder above the file. It names the output by content instead: the named
information URI (RFC 6920) of the SHA-256 the runtime measured from the written file.

Covers:
- A manifest from the runtime's own export of a short synthetic clip carries no tmp folder, home folder or file://
- Its outputUri is ni:///sha-256;<base64url digest>, which decodes back to its outputSha256, the written file's hash
- The encoding gives RFC 6920's own example (section 8.1) for the SHA-256 of "Hello World!"
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import numpy as np

from services.media.models import ExportRemuxResult
from services.media.runtime import MediaExperimentRuntime, compute_file_sha256

NI_SHA256 = "ni:///sha-256;"


def test_an_export_manifest_names_its_output_by_content_not_by_path(tmp_path: Path) -> None:
    # Six frames of a synthetic grey ramp, encoded by the runtime's own ffmpeg path into a folder of the test's own
    frames = [np.full((64, 96), 30.0 * k, dtype=np.float64) for k in range(1, 7)]
    manifest = MediaExperimentRuntime().export_video_with_audio_remux(
        frames=frames, fps=6, output_path=tmp_path / "exports" / "restored.mp4"
    )
    payload = manifest.to_contract_dict(
        session_id="session-synthetic", source_asset_id="synthetic-ramp", variant_id="variant-restored",
        graph_id="graph-temporal", catalog_citations=(),
    )
    text = json.dumps(payload)
    local = {
        "the test's folder": str(tmp_path), "the test's folder, resolved": str(tmp_path.resolve()),
        "the home folder": str(Path.home()), "a file:// URI": "file://",
    }
    for label, value in local.items():
        # A flag, not the comparison, so a failure names what leaked without printing the path itself
        leaked = value in text
        assert not leaked, f"the manifest carries {label}"
    uri = payload["outputUri"]
    assert uri.startswith(NI_SHA256)
    value = uri.removeprefix(NI_SHA256)
    # base64url with the padding left out, as RFC 6920 writes it
    assert "=" not in value and len(value) == 43
    digest = base64.urlsafe_b64decode(value + "=")
    assert digest.hex() == payload["outputSha256"] == compute_file_sha256(Path(manifest.output_path))


def test_the_content_address_is_rfc_6920s_own_example_for_its_digest() -> None:
    # RFC 6920, section 8.1: the ni URI of the SHA-256 of "Hello World!" (12 characters), without an authority
    manifest = ExportRemuxResult(
        id="export-rfc-example", output_path="written-elsewhere.mp4",
        output_sha256=hashlib.sha256(b"Hello World!").hexdigest(), source_sha256="cd" * 32, video_codec="h264",
        audio_preserved=False, audio_codec=None, video_duration_sec=1.0, audio_duration_sec=None,
        duration_drift_ms=0.0, audio_sync_offset_ms=0.0, stream_types=["video"], frame_count=6, fps=6,
    )
    payload = manifest.to_contract_dict(
        session_id="session-synthetic", source_asset_id="synthetic-ramp", variant_id="variant-restored",
        graph_id="graph-temporal", catalog_citations=(),
    )
    # A flag, so a regression to a local path is not printed by the failure
    is_rfc_example = payload["outputUri"] == "ni:///sha-256;f4OxZX_x_FO5LcGBSKHWXfwtSx-j1ncoSt3SABJtkGk"
    assert is_rfc_example, "outputUri is not RFC 6920's ni URI for the SHA-256 of 'Hello World!'"
