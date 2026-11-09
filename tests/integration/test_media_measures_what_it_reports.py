"""Media restoration and export report only what they measured, and stream-copy audio bit-identically.

Covers:
- A restoration attempt on a flat single frame reports hasPairedReference False, omits pointSourceRetentionRatio,
  temporalFlickerIndex, faceRegionStabilityScore and audioSyncOffsetMs from its metrics, omits peakMemoryBytes
  from the attempt, and notes that single-frame temporal flicker is not measured
- startedAtUTC, completedAtUTC and exportedAtUTC are recorded when the work runs (never stamped at serialization
  time in to_contract_dict), and cpuMs is measured with time.thread_time() on the worker thread
- export_video_with_audio_remux stream-copies the source audio (-c:a copy), shifts the audio input by minus the
  source video start time (-itsoffset) so a source whose video starts 500 ms after its audio keeps its alignment
  within 1 ms, verifies that every exported audio packet is bit-identical to the source via ffmpeg framemd5, and
  measures audioSyncOffsetMs from the PTS of identical packets
- An export without audio omits audioSyncOffsetMs, and a source whose audio codec cannot be stream-copied into MP4
  raises RuntimeError instead of silently re-encoding to AAC
- Real clip (owner media): exporting 2 seconds over an uploaded clip preserves its audio packets bit-identically
  and keeps packet alignment within 1 ms (asserted via boolean flags only)
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import time

from fastapi.testclient import TestClient
import numpy as np
import pytest

from services.api import app
from services.media import runtime as media_runtime_mod
from services.media.runtime import MediaExperimentRuntime
from services.science.solver import render_synthetic_starfield

ROOT = Path(__file__).resolve().parents[2]


def _packet_pts_and_md5(path: Path, stream_spec: str) -> tuple[float | None, list[tuple[float, str]]]:
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", stream_spec, "-c", "copy", "-f", "framemd5", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None, []
    tb = 1.0
    packets: list[tuple[float, str]] = []
    for raw in proc.stdout.splitlines():
        line = raw.strip()
        if line.startswith("#tb "):
            num_str, den_str = line.split(":", 1)[1].strip().split("/")
            tb = float(num_str) / float(den_str)
        elif line and not line.startswith("#"):
            cols = [c.strip() for c in line.split(",")]
            if len(cols) >= 6 and cols[2] != "N/A":
                packets.append((float(cols[2]) * tb, cols[5]))
    if not packets:
        return None, []
    return min(pts for pts, _ in packets), packets


def test_flat_single_frame_restoration_omits_unmeasured_metrics_and_has_no_paired_reference() -> None:
    flat_frame = np.full((64, 96), 18.0, dtype=np.float64)
    runtime = MediaExperimentRuntime()
    res = runtime.execute_restoration_branch([flat_frame], profile="star_protected", preserve_stars=True)

    assert res.metrics.has_paired_reference is False
    assert res.point_source_retention_ratio is None
    assert res.temporal_flicker_index is None
    assert res.metrics.face_region_stability_score is None
    assert res.metrics.audio_sync_offset_ms is None

    contract = res.to_contract_dict(source_asset_id="synthetic-flat")
    assert "peakMemoryBytes" not in contract
    metrics = contract["metrics"]
    assert metrics["hasPairedReference"] is False
    assert "pointSourceRetentionRatio" not in metrics
    assert "temporalFlickerIndex" not in metrics
    assert "faceRegionStabilityScore" not in metrics
    assert "audioSyncOffsetMs" not in metrics
    assert any("Single-frame input: temporal flicker index is not measured." in note for note in metrics["validityNotes"])

    client = TestClient(app)
    resp = client.post(
        "/api/media/restore",
        json={
            "profile": "star_protected",
            "preserveStars": True,
            "image_luma": flat_frame.tolist(),
            "sourceAssetId": "synthetic-flat",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "peakMemoryBytes" not in body
    assert "point_source_retention_ratio" not in body
    assert "temporal_flicker_index" not in body
    assert "pointSourceRetentionRatio" not in body["metrics"]
    assert "temporalFlickerIndex" not in body["metrics"]
    assert "faceRegionStabilityScore" not in body["metrics"]
    assert "audioSyncOffsetMs" not in body["metrics"]
    assert body["metrics"]["hasPairedReference"] is False

    # With >= 2 starry frames and an explicit foreground mask, retention, flicker and stability are measured,
    # while hasPairedReference stays False and audioSyncOffsetMs stays absent.
    f0, _, _ = render_synthetic_starfield(width=320, height=180, read_noise_sigma=3.2, seed=201)
    f1, _, _ = render_synthetic_starfield(width=320, height=180, read_noise_sigma=3.2, seed=202)
    fg_mask = np.zeros((180, 320), dtype=bool)
    fg_mask[-30:, :] = True
    multi_res = runtime.execute_restoration_branch(
        [f0, f1], profile="temporal", preserve_stars=True, foreground_mask=fg_mask
    )
    multi_contract = multi_res.to_contract_dict(source_asset_id="synthetic-multi")
    multi_metrics = multi_contract["metrics"]
    assert multi_metrics["hasPairedReference"] is False
    assert multi_metrics["pointSourceRetentionRatio"] >= 0.85
    assert multi_metrics["temporalFlickerIndex"] >= 0.0
    assert 0.0 <= multi_metrics["faceRegionStabilityScore"] <= 1.0
    assert "audioSyncOffsetMs" not in multi_metrics
    assert any("Temporal flicker index changed from " in note for note in multi_metrics["validityNotes"])


def test_restoration_and_export_record_their_own_execution_timestamps_and_thread_cpu_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_thread_times = iter([10.000, 10.125])
    monkeypatch.setattr(media_runtime_mod.time, "thread_time", lambda: next(fake_thread_times))

    frame = np.full((48, 64), 24.0, dtype=np.float64)
    runtime = MediaExperimentRuntime()
    res = runtime.execute_restoration_branch([frame], profile="tone_only", preserve_stars=False)

    first_dict = res.to_contract_dict(source_asset_id="synthetic-timing")
    time.sleep(0.02)
    second_dict = res.to_contract_dict(source_asset_id="synthetic-timing")

    assert first_dict["startedAtUTC"] == second_dict["startedAtUTC"]
    assert first_dict["completedAtUTC"] == second_dict["completedAtUTC"]
    assert first_dict["startedAtUTC"] <= first_dict["completedAtUTC"]
    assert abs(first_dict["cpuMs"] - 125.0) < 1e-6
    assert first_dict["gpuMs"] == 0.0
    assert any("time.thread_time()" in note for note in first_dict["metrics"]["validityNotes"])

    manifest = runtime.export_video_with_audio_remux(
        frames=[frame, frame], fps=2, output_path=tmp_path / "timing_export.mp4"
    )
    export_ids = {
        "session_id": "s-1",
        "source_asset_id": "synthetic-timing",
        "variant_id": "v-1",
        "graph_id": "g-1",
        "catalog_citations": (),
    }
    first_export = manifest.to_contract_dict(**export_ids)
    time.sleep(0.02)
    second_export = manifest.to_contract_dict(**export_ids)
    assert first_export["exportedAtUTC"] == second_export["exportedAtUTC"]


def test_export_stream_copies_audio_packets_bit_identically_and_preserves_delayed_video_alignment(
    tmp_path: Path,
) -> None:
    src_mp4 = tmp_path / "src_video_500ms_late.mp4"
    out_mp4 = tmp_path / "exported_aligned.mp4"
    unshifted_mp4 = tmp_path / "unshifted_500ms_early.mp4"

    # Source whose video stream starts 500 ms after its audio stream. `-fps_mode passthrough` keeps the ten frames at
    # their offset timestamps: by default ffmpeg 6.1 picks a constant frame rate for MP4 and fills the 500 ms gap with
    # five duplicated frames, which leaves no delay at all, while ffmpeg 8.1 picks a variable rate and keeps it.
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-itsoffset", "0.5",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=1.0",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1.5",
            "-map", "0:v:0", "-map", "1:a:0", "-fps_mode", "passthrough",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            str(src_mp4),
        ],
        check=True,
    )

    # Precondition: the fixture holds the delay this test is about, whichever ffmpeg built it. Ten frames, none
    # duplicated into the gap, the first 500 ms after the audio's first packet, within the one AAC priming frame
    # (1024 samples at 48 kHz) that a demuxer may or may not place before the first audible sample.
    fixture_v_start, fixture_v_pkts = _packet_pts_and_md5(src_mp4, "0:v:0")
    fixture_a_start, _ = _packet_pts_and_md5(src_mp4, "0:a:0")
    assert fixture_v_start is not None and fixture_a_start is not None
    assert len(fixture_v_pkts) == 10, f"the fixture holds {len(fixture_v_pkts)} video frames: ffmpeg filled its delay"
    assert abs((fixture_v_start - fixture_a_start) - 0.5) <= 1024 / 48000 + 1e-3

    frames = [np.full((64, 64), float(15 * (i + 1)), dtype=np.float64) for i in range(10)]
    runtime = MediaExperimentRuntime()
    manifest = runtime.export_video_with_audio_remux(
        frames=frames,
        fps=10,
        output_path=out_mp4,
        audio_source_path=src_mp4,
    )

    src_v_start, _ = _packet_pts_and_md5(src_mp4, "0:v:0")
    _, src_a_pkts = _packet_pts_and_md5(src_mp4, "0:a:0")
    out_v_start, _ = _packet_pts_and_md5(out_mp4, "0:v:0")
    _, out_a_pkts = _packet_pts_and_md5(out_mp4, "0:a:0")

    assert src_v_start is not None and out_v_start is not None
    assert len(out_a_pkts) > 0
    src_md5s = [md5 for _, md5 in src_a_pkts]
    out_md5s = [md5 for _, md5 in out_a_pkts]
    assert out_md5s[0] in src_md5s, "exported audio packets were re-encoded instead of stream-copied"
    start_idx = src_md5s.index(out_md5s[0])
    assert src_md5s[start_idx : start_idx + len(out_md5s)] == out_md5s

    assert manifest.audio_preserved is True
    assert manifest.audio_sync_offset_ms is not None
    assert manifest.audio_sync_offset_ms < 1.0

    # If a remux forgets -itsoffset on the 500 ms delayed-video source, the packet-PTS offset measurement
    # reports the 500 ms misalignment even though the stream-copied packets are bit-identical.
    raw_bytes = np.zeros((10, 64, 64, 3), dtype=np.uint8).tobytes()
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "64x64", "-r", "10", "-i", "pipe:0",
            "-i", str(src_mp4),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-t", "1.000000",
            str(unshifted_mp4),
        ],
        input=raw_bytes,
        check=True,
    )
    preserved, unshifted_offset_ms = media_runtime_mod._verify_copied_audio_packets_and_offset_ms(
        src_mp4, unshifted_mp4
    )
    assert preserved is True
    assert unshifted_offset_ms is not None
    assert abs(unshifted_offset_ms - 500.0) < 1.0

    # The measurement reads the exported file's own video start, not only the source's: the delayed-video source
    # measured against itself is aligned, although its video starts 500 ms after its audio
    preserved_self, self_offset_ms = media_runtime_mod._verify_copied_audio_packets_and_offset_ms(src_mp4, src_mp4)
    assert preserved_self is True
    assert self_offset_ms is not None
    assert self_offset_ms < 1.0



def test_both_exports_pin_frame_timing_and_write_one_video_packet_per_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The export passes `-fps_mode passthrough` as an output option, with audio and without, so each frame is
    written at its own timestamp whatever frame rate mode the installed ffmpeg picks for MP4 by default (6.1 a
    constant rate, which fills a gap with duplicated frames; 8.1 a variable rate). Each export holds one video packet
    per frame, at k/fps."""
    src_mp4 = tmp_path / "source_with_audio.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:r=8:d=1.0",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1.0",
            "-map", "0:v:0", "-map", "1:a:0", "-fps_mode", "passthrough",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            str(src_mp4),
        ],
        check=True,
    )
    real_run = media_runtime_mod.subprocess.run
    export_commands: list[list[str]] = []

    def spy(cmd, *args, **kwargs):
        if cmd and cmd[0] == "ffmpeg" and "pipe:0" in cmd:
            export_commands.append(list(cmd))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(media_runtime_mod.subprocess, "run", spy)
    fps = 8
    frames = [np.full((64, 64), float(20 * (i + 1)), dtype=np.float64) for i in range(fps)]
    runtime = MediaExperimentRuntime()
    for name, audio in (("silent.mp4", None), ("with_audio.mp4", src_mp4)):
        out = tmp_path / name
        runtime.export_video_with_audio_remux(frames=frames, fps=fps, output_path=out, audio_source_path=audio)
        start, packets = _packet_pts_and_md5(out, "0:v:0")
        assert start is not None and len(packets) == len(frames), name
        offsets = sorted(pts - start for pts, _ in packets)
        assert all(abs(offset - k / fps) < 1e-3 for k, offset in enumerate(offsets)), name

    assert len(export_commands) == 2
    for cmd in export_commands:
        assert "-fps_mode" in cmd, "the export leaves frame timing to the installed ffmpeg's default"
        at = cmd.index("-fps_mode")
        last_input = max(i for i, arg in enumerate(cmd) if arg == "-i")
        # An output option: after every input, before the output path
        assert cmd[at + 1] == "passthrough" and last_input < at < len(cmd) - 1


def test_export_without_audio_omits_audio_sync_offset_and_refuses_incompatible_audio_stream_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frames = [np.full((64, 64), 32.0, dtype=np.float64) for _ in range(6)]
    runtime = MediaExperimentRuntime()
    silent_manifest = runtime.export_video_with_audio_remux(
        frames=frames, fps=6, output_path=tmp_path / "silent.mp4", audio_source_path=None
    )
    assert silent_manifest.audio_preserved is False
    assert silent_manifest.audio_sync_offset_ms is None
    silent_contract = silent_manifest.to_contract_dict(
        session_id="s-silent",
        source_asset_id="synthetic-silent",
        variant_id="v-silent",
        graph_id="g-silent",
        catalog_citations=(),
    )
    assert "audioSyncOffsetMs" not in silent_contract

    # Windows Media Audio (wmav2 in ASF) cannot be stream-copied into an MP4 container; the runtime must raise
    # with ffmpeg's reason instead of silently re-encoding to AAC.
    wma_asf = tmp_path / "incompatible.asf"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1.0",
            "-c:a", "wmav2",
            str(wma_asf),
        ],
        check=True,
    )
    with pytest.raises(RuntimeError, match="ffmpeg export failed"):
        runtime.export_video_with_audio_remux(
            frames=frames, fps=6, output_path=tmp_path / "refused.mp4", audio_source_path=wma_asf
        )

    # When ffprobe omits duration from both the stream and format blocks, video_duration_sec and
    # duration_drift_ms are None instead of substituting expected_duration_sec (which read as 0.0 drift).
    real_run = media_runtime_mod.subprocess.run

    def run_without_probed_duration(cmd, *args, **kwargs):
        if cmd and cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=b'{"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}], "format": {}}',
                stderr=b"",
            )
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(media_runtime_mod.subprocess, "run", run_without_probed_duration)
    no_dur_manifest = runtime.export_video_with_audio_remux(
        frames=frames, fps=6, output_path=tmp_path / "no_dur.mp4", audio_source_path=None
    )
    assert no_dur_manifest.video_duration_sec is None
    assert no_dur_manifest.duration_drift_ms is None


@pytest.mark.owner_media("data/uploads")
def test_real_clip_export_preserves_audio_packets_bit_identically_and_keeps_alignment(tmp_path: Path) -> None:
    clips = sorted(
        p for p in (ROOT / "data" / "uploads").rglob("*")
        if p.is_file() and p.suffix.lower() in {".mov", ".mp4", ".m4v"}
    )
    has_clips = len(clips) > 0
    assert has_clips, "no real video clip found under data/uploads"
    clip = clips[0]

    frames = [np.full((64, 64), float(10 + i), dtype=np.float64) for i in range(20)]
    out_mp4 = tmp_path / "real_audio_remux.mp4"
    manifest = MediaExperimentRuntime().export_video_with_audio_remux(
        frames=frames, fps=10, output_path=out_mp4, audio_source_path=clip
    )

    src_v_start, _ = _packet_pts_and_md5(clip, "0:v:0")
    _, src_a_pkts = _packet_pts_and_md5(clip, "0:a:0")
    out_v_start, _ = _packet_pts_and_md5(out_mp4, "0:v:0")
    _, out_a_pkts = _packet_pts_and_md5(out_mp4, "0:a:0")

    src_md5s = [md5 for _, md5 in src_a_pkts]
    out_md5s = [md5 for _, md5 in out_a_pkts]
    copied_ok = (
        len(out_md5s) > 0
        and out_md5s[0] in src_md5s
        and src_md5s[src_md5s.index(out_md5s[0]) : src_md5s.index(out_md5s[0]) + len(out_md5s)] == out_md5s
    )
    assert copied_ok, "real clip audio packets were not stream-copied bit-identically"
    assert manifest.audio_preserved is True, "real clip export did not report audio_preserved True"
    aligned_ok = manifest.audio_sync_offset_ms is not None and manifest.audio_sync_offset_ms < 1.0
    assert aligned_ok, "real clip export audio sync offset exceeded 1 ms"
