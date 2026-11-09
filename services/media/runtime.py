"""Albedian Media Experiment Runtime: Content-addressed ingestion, astronomy-aware
restoration branches (`tone_only`, `conventional_denoise`, `star_protected`, `temporal`),
honest evaluation metrics, and FFmpeg audio-preserving remux export (`EXP-04`, `EXP-10`).
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
from typing import Sequence

import numpy as np
from scipy import ndimage

from services.media.models import (
    ExportRemuxResult,
    RestorationBranchResult,
    RestorationMetrics,
    compute_file_sha256,
    compute_frames_sha256,
)
from services.media.silhouette import (
    compute_2d_sky_silhouette_mask,
    deband_shadow_macroblocks,
)
from services.science.solver import (
    SourceDetection,
    _estimate_background_and_noise,
    _to_luma_2d,
    detect_point_sources,
    stack_video_frames_for_calibration,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _build_star_protection_mask(
    shape: tuple[int, int],
    detections: Sequence[SourceDetection],
    radius_scale: float = 1.65,
) -> np.ndarray:
    """Build a smooth [0, 1] spatial protection weight mask around source-detected stars."""
    h, w = shape
    mask = np.zeros((h, w), dtype=np.float64)
    for det in detections:
        sigma = max(1.35, min(3.2, det.fwhm_px * 0.72 * radius_scale))
        win = int(np.ceil(3.5 * sigma))
        x0 = int(round(det.x_px))
        y0 = int(round(det.y_px))
        y_slice = slice(max(0, y0 - win), min(h, y0 + win + 1))
        x_slice = slice(max(0, x0 - win), min(w, x0 + win + 1))
        yy, xx = np.mgrid[y_slice, x_slice]
        w_patch = np.exp(-((xx - det.x_px) ** 2 + (yy - det.y_px) ** 2) / (2.0 * sigma * sigma))
        w_patch = np.clip(w_patch * 1.25, 0.0, 1.0)
        mask[y_slice, x_slice] = np.maximum(mask[y_slice, x_slice], w_patch)
    return mask


def _measure_star_peak_fluxes(
    luma: np.ndarray,
    reference_detections: Sequence[SourceDetection],
    sky_mask: np.ndarray | None = None,
) -> list[float]:
    """Measure local background-subtracted peak flux at reference star coordinates."""
    if not reference_detections:
        return []
    bg, _ = _estimate_background_and_noise(luma, sky_mask)
    res = luma - bg
    h, w = luma.shape
    peaks: list[float] = []
    for det in reference_detections:
        x0 = int(round(det.x_px))
        y0 = int(round(det.y_px))
        if not (2 <= x0 < w - 2 and 2 <= y0 < h - 2):
            continue
        patch3 = res[y0 - 1 : y0 + 2, x0 - 1 : x0 + 2]
        peaks.append(max(0.0, float(np.max(patch3))))
    return peaks


def _compute_temporal_flicker_index(
    frames: Sequence[np.ndarray], sky_mask: np.ndarray | None = None
) -> float | None:
    """Compute frame-to-frame pixel temporal standard deviation across background regions."""
    if len(frames) < 2:
        return None
    lumas = [_to_luma_2d(f) for f in frames]
    diffs: list[float] = []
    for i in range(1, len(lumas)):
        diff = lumas[i] - lumas[i - 1]
        if sky_mask is not None:
            valid = diff[np.asarray(sky_mask, dtype=bool)]
        else:
            valid = diff.ravel()
        if valid.size > 0:
            med = float(np.median(valid))
            mad = float(np.median(np.abs(valid - med)))
            diffs.append(1.4826 * mad)
    return float(np.mean(diffs)) if diffs else None


def _stream_packet_pts_and_md5(
    path: Path, stream_spec: str
) -> tuple[float | None, list[tuple[float, str]]]:
    """Read each packet's PTS (in seconds) and payload MD5 digest for `stream_spec` via `ffmpeg -f framemd5`."""
    proc = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            stream_spec,
            "-c",
            "copy",
            "-f",
            "framemd5",
            "-",
        ],
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
                pts_sec = float(cols[2]) * tb
                md5_hex = cols[5]
                packets.append((pts_sec, md5_hex))
    if not packets:
        return None, []
    return min(pts for pts, _ in packets), packets


def _verify_copied_audio_packets_and_offset_ms(
    source_path: Path, exported_path: Path
) -> tuple[bool, float | None]:
    """Verify exported audio packets are a bit-identical contiguous slice of the source's audio packets
    and measure the maximum relative audio-to-video PTS offset in milliseconds across all copied packets.
    """
    src_v_start, _ = _stream_packet_pts_and_md5(source_path, "0:v:0")
    _, src_a_pkts = _stream_packet_pts_and_md5(source_path, "0:a:0")
    out_v_start, _ = _stream_packet_pts_and_md5(exported_path, "0:v:0")
    _, out_a_pkts = _stream_packet_pts_and_md5(exported_path, "0:a:0")
    if not src_a_pkts or not out_a_pkts or out_v_start is None:
        return False, None

    src_md5s = [md5 for _, md5 in src_a_pkts]
    out_md5s = [md5 for _, md5 in out_a_pkts]
    n_out = len(out_md5s)
    match_idx: int | None = None
    for idx in range(len(src_md5s) - n_out + 1):
        if src_md5s[idx : idx + n_out] == out_md5s:
            match_idx = idx
            break
    if match_idx is None:
        return False, None

    src_v0 = src_v_start if src_v_start is not None else 0.0
    max_offset_ms = max(
        abs((out_pts - out_v_start) - (src_a_pkts[match_idx + i][0] - src_v0)) * 1000.0
        for i, (out_pts, _) in enumerate(out_a_pkts)
    )
    return True, float(max_offset_ms)


class MediaExperimentRuntime:
    """Authoritative Media Restoration & Audio-Preserving Export Runtime."""

    def __init__(self) -> None:
        self._attempt_counter = 0

    def compute_source_sha256(self, frames: Sequence[np.ndarray]) -> str:
        """Return the content-addressed SHA-256 digest of input frames."""
        return compute_frames_sha256(frames)

    def execute_restoration_branch(
        self,
        frames: Sequence[np.ndarray],
        profile: str = "star_protected",
        preserve_stars: bool = True,
        foreground_mask: np.ndarray | None = None,
    ) -> RestorationBranchResult:
        """Execute a deterministic restoration branch without mutating input `frames`."""
        if len(frames) == 0:
            raise ValueError("frames must be non-empty.")

        valid_profiles = {
            "tone_only",
            "natural",
            "conventional_denoise",
            "star_protected",
            "temporal",
        }
        if profile not in valid_profiles:
            raise ValueError(
                f"Unsupported restoration profile '{profile}'. Expected one of {valid_profiles}."
            )

        started_at_utc = _utc_now_iso()
        t0 = time.perf_counter()
        cpu_t0 = time.thread_time()
        self._attempt_counter += 1

        src_frames = [np.asarray(f, dtype=np.float64).copy() for f in frames]
        source_sha256 = compute_frames_sha256(src_frames)
        ref_luma = _to_luma_2d(src_frames[0])
        h_ref = ref_luma.shape[0]
        sil_mask_2d, _ = compute_2d_sky_silhouette_mask(ref_luma)

        if foreground_mask is not None:
            fg_bool = np.asarray(foreground_mask, dtype=bool)
            sky_mask = ~fg_bool
            sky_weight_2d = 1.0 - fg_bool.astype(np.float64)
        else:
            top_med = float(np.median(ref_luma[: max(4, int(h_ref * 0.35)), :]))
            bot_med = float(np.median(ref_luma[min(h_ref - 4, int(h_ref * 0.65)) :, :]))
            has_horizon_step = abs(bot_med - top_med) > max(2.5, 0.25 * max(top_med, 1.0))
            sky_weight_2d = sil_mask_2d if has_horizon_step else np.ones_like(ref_luma)
            sky_mask = sky_weight_2d > 0.5
            fg_bool = ~sky_mask

        _, src_sigma_bg = _estimate_background_and_noise(ref_luma, sky_mask)
        src_detections = detect_point_sources(ref_luma, sky_mask=sky_mask, snr_threshold=3.5)

        restored_frames: list[np.ndarray] = []

        if profile in ("tone_only", "natural"):
            for idx, f in enumerate(src_frames):
                luma = _to_luma_2d(f)
                bg, _ = _estimate_background_and_noise(luma, sky_mask)
                res = luma - bg
                bg_lifted = deband_shadow_macroblocks(
                    bg + 2.5 * np.log1p(np.maximum(0.0, bg) / 16.0),
                    sky_weight_2d,
                    seed=42 + idx,
                )
                out = sky_weight_2d * (bg_lifted + res) + (1.0 - sky_weight_2d) * luma
                restored_frames.append(np.clip(out, 0.0, 255.0))

        elif profile == "conventional_denoise":
            for f in src_frames:
                luma = _to_luma_2d(f)
                smoothed = ndimage.gaussian_filter(luma, sigma=2.2, mode="nearest")
                restored_frames.append(np.clip(smoothed, 0.0, 255.0))

        elif profile == "star_protected":
            for idx, f in enumerate(src_frames):
                luma = _to_luma_2d(f)
                bg, _ = _estimate_background_and_noise(luma, sky_mask)
                res = luma - bg

                if preserve_stars:
                    frame_dets = detect_point_sources(luma, sky_mask=sky_mask, snr_threshold=3.3)
                    star_mask = _build_star_protection_mask(luma.shape, frame_dets, radius_scale=1.7)
                else:
                    star_mask = np.zeros_like(luma)

                bg_res_denoised = 0.18 * res + 0.14 * ndimage.gaussian_filter(
                    res * (1.0 - star_mask), sigma=1.8, mode="nearest"
                )
                protected_res = star_mask * res + (1.0 - star_mask) * bg_res_denoised
                bg_lifted = deband_shadow_macroblocks(
                    bg + 1.5 * np.log1p(np.maximum(0.0, bg) / 20.0),
                    sky_weight_2d,
                    seed=42 + idx,
                )
                out_sky = bg_lifted + protected_res

                if foreground_mask is not None or np.any(fg_bool):
                    fg_smooth = ndimage.gaussian_filter(luma, sigma=0.65, mode="nearest")
                    out = sky_weight_2d * out_sky + (1.0 - sky_weight_2d) * fg_smooth
                else:
                    out = out_sky
                restored_frames.append(np.clip(out, 0.0, 255.0))

        elif profile == "temporal":
            if len(src_frames) > 1:
                stack_res = stack_video_frames_for_calibration(src_frames, sky_mask=sky_mask)
                composite = stack_res.composite_luma
            else:
                composite = _to_luma_2d(src_frames[0])

            comp_bg, _ = _estimate_background_and_noise(composite, sky_mask)
            comp_res = composite - comp_bg
            comp_dets = (
                detect_point_sources(composite, sky_mask=sky_mask, snr_threshold=3.3)
                if preserve_stars
                else []
            )
            comp_star_mask = _build_star_protection_mask(
                composite.shape, list(comp_dets) + list(src_detections), radius_scale=1.75
            )
            comp_bg_denoised = 0.22 * comp_res + 0.15 * ndimage.gaussian_filter(
                comp_res * (1.0 - comp_star_mask), sigma=1.8, mode="nearest"
            )
            clean_master_sky = (
                comp_bg
                + 1.5 * np.log1p(np.maximum(0.0, comp_bg) / 20.0)
                + comp_star_mask * np.maximum(comp_res, _to_luma_2d(src_frames[0]) - comp_bg)
                + (1.0 - comp_star_mask) * comp_bg_denoised
            )

            for idx, f in enumerate(src_frames):
                luma = _to_luma_2d(f)
                if idx == 0 or len(src_frames) == 1:
                    shifted_sky = clean_master_sky
                else:
                    dy, dx = stack_res.shifts_yx[idx]
                    shifted_sky = ndimage.shift(
                        clean_master_sky, shift=(-dy, -dx), order=1, mode="nearest"
                    )
                out = np.where(fg_bool, luma, 0.90 * shifted_sky + 0.10 * luma)
                restored_frames.append(np.clip(out, 0.0, 255.0))

        out_luma = _to_luma_2d(restored_frames[0])
        out_bg, out_sigma_bg = _estimate_background_and_noise(out_luma, sky_mask)
        out_detections = detect_point_sources(out_luma, sky_mask=sky_mask, snr_threshold=3.5)

        sigma_in = max(src_sigma_bg, 1e-5)
        sigma_out = max(out_sigma_bg, 1e-5)
        if sigma_out < sigma_in:
            snr_gain_db = 20.0 * float(np.log10(sigma_in / sigma_out))
            noise_red_pct = (1.0 - (sigma_out / sigma_in)) * 100.0
        else:
            snr_gain_db = 0.0
            noise_red_pct = 0.0

        src_peaks = _measure_star_peak_fluxes(ref_luma, src_detections, sky_mask)
        out_peaks = _measure_star_peak_fluxes(out_luma, src_detections, sky_mask)
        retention_ratio: float | None = None
        if src_peaks and out_peaks:
            ratios = [o / max(s, 1e-6) for s, o in zip(src_peaks, out_peaks) if s > 1e-3]
            if ratios:
                retention_ratio = float(np.median(ratios))

        ref_pool = list(src_detections)
        if profile == "temporal" and len(src_frames) > 1:
            comp_pool = detect_point_sources(
                stack_video_frames_for_calibration(src_frames, sky_mask=sky_mask).composite_luma,
                sky_mask=sky_mask,
                snr_threshold=3.0,
            )
            ref_pool.extend(comp_pool)

        out_res = out_luma - out_bg
        false_artifacts = 0
        for od in out_detections:
            ox = int(round(od.x_px))
            oy = int(round(od.y_px))
            if 0 <= oy < out_res.shape[0] and 0 <= ox < out_res.shape[1]:
                if float(out_res[oy, ox]) < 3.5 * sigma_in:
                    continue
            has_source_match = any(
                np.hypot(od.x_px - sd.x_px, od.y_px - sd.y_px) <= 3.5 for sd in ref_pool
            )
            if not has_source_match:
                false_artifacts += 1

        src_flicker = _compute_temporal_flicker_index(src_frames, sky_mask)
        out_flicker = _compute_temporal_flicker_index(restored_frames, sky_mask)

        fg_stability: float | None = None
        if foreground_mask is not None and np.any(fg_bool):
            fg_diff = float(np.mean(np.abs(out_luma[fg_bool] - ref_luma[fg_bool])))
            fg_stability = max(0.0, min(1.0, 1.0 - fg_diff / 64.0))

        output_sha256 = compute_frames_sha256(restored_frames)
        cache_payload = f"{source_sha256}:{profile}:preserve_stars={preserve_stars}"
        cache_key = hashlib.sha256(cache_payload.encode("utf-8")).hexdigest()[:32]
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        cpu_ms = max(0.0, (time.thread_time() - cpu_t0) * 1000.0)
        completed_at_utc = _utc_now_iso()

        if src_flicker is not None and out_flicker is not None:
            flicker_note = f"Temporal flicker index changed from {src_flicker:.3f} to {out_flicker:.3f}."
        else:
            flicker_note = "Single-frame input: temporal flicker index is not measured."

        metrics = RestorationMetrics(
            evaluation_method="Source-Referenced PSF Core Retention & Background MAD Noise Floor",
            has_paired_reference=False,
            shadow_snr_gain_db=snr_gain_db,
            estimated_noise_reduction_pct=noise_red_pct,
            point_source_retention_ratio=retention_ratio,
            false_point_artifact_count=false_artifacts,
            temporal_flicker_index=out_flicker,
            audio_sync_offset_ms=None,
            face_region_stability_score=fg_stability,
            validity_notes=[
                f"Input sky noise sigma={sigma_in:.3f}, output sky noise sigma={sigma_out:.3f}.",
                f"Evaluated on {len(src_detections)} source-detected point sources; allowSyntheticSky=False.",
                flicker_note,
                f"CPU time {cpu_ms:.3f} ms measured with time.thread_time() on the restoration thread.",
            ],
        )

        return RestorationBranchResult(
            attempt_id=f"attempt-{self._attempt_counter:04d}-{profile}",
            profile=profile,
            source_sha256=source_sha256,
            output_sha256=output_sha256,
            cache_key=cache_key,
            restored_frames=restored_frames,
            source_detections=src_detections,
            restored_detections=out_detections,
            metrics=metrics,
            elapsed_ms=elapsed_ms,
            cpu_ms=cpu_ms,
            started_at_utc=started_at_utc,
            completed_at_utc=completed_at_utc,
            allow_synthetic_sky=False,
        )

    def export_video_with_audio_remux(
        self,
        frames: Sequence[np.ndarray],
        fps: int,
        output_path: str | Path,
        audio_source_path: str | Path | None = None,
    ) -> ExportRemuxResult:
        """Encode restored video frames to H.264 MP4 and stream-copy/remux original audio (`EXP-10`)."""
        if len(frames) == 0:
            raise ValueError("Cannot export empty frame sequence.")
        if fps <= 0:
            raise ValueError("fps must be positive.")

        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)

        rgb_frames: list[np.ndarray] = []
        for frame in frames:
            arr = np.asarray(frame, dtype=np.float64)
            if arr.ndim == 2:
                rgb = np.repeat(arr[:, :, None], 3, axis=2)
            elif arr.ndim == 3 and arr.shape[2] >= 3:
                rgb = arr[:, :, :3]
            else:
                raise ValueError(f"Unsupported frame shape {arr.shape}")

            h, w, _ = rgb.shape
            h_even = h - (h % 2)
            w_even = w - (w % 2)
            rgb_u8 = np.clip(np.round(rgb[:h_even, :w_even, :]), 0, 255).astype(np.uint8)
            rgb_frames.append(np.ascontiguousarray(rgb_u8))

        h_out, w_out, _ = rgb_frames[0].shape
        raw_video_bytes = b"".join(f.tobytes() for f in rgb_frames)
        expected_duration_sec = len(rgb_frames) / float(fps)

        src_audio: Path | None = None
        if audio_source_path is not None:
            src_audio = Path(audio_source_path)
            if not src_audio.exists():
                raise FileNotFoundError("Audio source file not found.")
            source_sha256 = compute_file_sha256(src_audio)
            src_v_start, _ = _stream_packet_pts_and_md5(src_audio, "0:v:0")
            itsoffset_sec = -(src_v_start if src_v_start is not None else 0.0)
            cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-s",
                f"{w_out}x{h_out}",
                "-r",
                str(fps),
                "-i",
                "pipe:0",
                "-itsoffset",
                f"{itsoffset_sec:.6f}",
                "-i",
                str(src_audio),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "copy",
                # Each frame at its own timestamp, whatever frame rate mode the installed ffmpeg picks for MP4
                "-fps_mode",
                "passthrough",
                "-t",
                f"{expected_duration_sec:.6f}",
                str(out_file),
            ]
        else:
            source_sha256 = compute_frames_sha256(frames)
            cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-s",
                f"{w_out}x{h_out}",
                "-r",
                str(fps),
                "-i",
                "pipe:0",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-fps_mode",
                "passthrough",
                "-t",
                f"{expected_duration_sec:.6f}",
                str(out_file),
            ]

        proc = subprocess.run(
            cmd, input=raw_video_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
        )
        if proc.returncode != 0:
            err_msg = proc.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"ffmpeg export failed (code {proc.returncode}): {err_msg}")

        exported_at_utc = _utc_now_iso()

        probe_cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type,codec_name,duration",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(out_file),
        ]
        probe_proc = subprocess.run(
            probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
        )
        if probe_proc.returncode != 0:
            err_msg = probe_proc.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"ffprobe failed on exported file (code {probe_proc.returncode}): {err_msg}")
        probe_data = json.loads(probe_proc.stdout.decode("utf-8"))
        streams = probe_data.get("streams", [])
        fmt_dur_raw = probe_data.get("format", {}).get("duration")

        stream_types: list[str] = []
        video_codec = "unknown"
        audio_codec: str | None = None
        video_duration: float | None = float(fmt_dur_raw) if fmt_dur_raw is not None else None
        audio_duration: float | None = None

        for st in streams:
            c_type = st.get("codec_type", "")
            stream_types.append(c_type)
            if c_type == "video":
                video_codec = st.get("codec_name", "h264")
                if st.get("duration") is not None:
                    video_duration = float(st["duration"])
            elif c_type == "audio":
                audio_codec = st.get("codec_name", "aac")
                if st.get("duration") is not None:
                    audio_duration = float(st["duration"])

        duration_drift_ms = (
            abs(video_duration - expected_duration_sec) * 1000.0
            if video_duration is not None
            else None
        )

        if src_audio is not None and "audio" in stream_types:
            audio_preserved, audio_sync_offset_ms = _verify_copied_audio_packets_and_offset_ms(
                src_audio, out_file
            )
        else:
            audio_preserved = False
            audio_sync_offset_ms = None

        output_sha256 = compute_file_sha256(out_file)

        return ExportRemuxResult(
            id=f"export-{output_sha256[:12]}",
            output_path=str(out_file.resolve()),
            output_sha256=output_sha256,
            source_sha256=source_sha256,
            video_codec=video_codec,
            audio_preserved=audio_preserved,
            audio_codec=audio_codec,
            video_duration_sec=video_duration,
            audio_duration_sec=audio_duration,
            duration_drift_ms=duration_drift_ms,
            audio_sync_offset_ms=audio_sync_offset_ms,
            stream_types=stream_types,
            frame_count=len(rgb_frames),
            fps=fps,
            exported_at_utc=exported_at_utc,
        )


__all__ = [
    "ExportRemuxResult",
    "MediaExperimentRuntime",
    "RestorationBranchResult",
    "RestorationMetrics",
    "compute_file_sha256",
    "compute_frames_sha256",
]
