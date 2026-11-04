#!/usr/bin/env python3
"""Run Albedian's Media Experiment Runtime & Sky Calibration on real night photos/videos.

Usage:
    uv run python scripts/run_real_media_experiment.py data/uploads/morocco/clip.mov

Supports:
- Images: .png, .jpg, .jpeg, .tif, .tiff, .webp
- Videos: .mp4, .mov, .m4v, .mkv (extracts QuickTime ISO6709 GPS, creationdate,
  camera model, keyframes, phase-aligned temporal stack, and audio-preserving export)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, shift as nd_shift

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.media.runtime import MediaExperimentRuntime
from services.science.catalog import PINNED_CATALOG_STARS, icrs_to_altaz
from services.science.ephemeris import compute_solar_system_ephemerides
from services.science.solver import detect_point_sources, solve_and_verify_field

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_iso6709(iso_str: str) -> tuple[float, float] | None:
    """Parse QuickTime ISO6709 string like '+30.0-040.0000/' into (lat, lon)."""
    m = re.match(r"^([+-]\d+\.\d+)([+-]\d+\.\d+)", iso_str.strip())
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


def probe_video_metadata(video_path: Path) -> dict[str, Any]:
    """Extract QuickTime GPS, creationdate, model, resolution, and audio streams."""
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    info = json.loads(proc.stdout)
    tags = info.get("format", {}).get("tags", {}) or {}
    loc_raw = tags.get("com.apple.quicktime.location.ISO6709", "")
    coords = parse_iso6709(loc_raw) if loc_raw else None
    creation_utc = (
        tags.get("com.apple.quicktime.creationdate")
        or tags.get("creation_time")
    )
    model = tags.get("com.apple.quicktime.model", "Consumer Camera")
    duration_sec = float(info.get("format", {}).get("duration", 5.0))
    return {
        "model": model,
        "location_iso6709": loc_raw,
        "lat_deg": coords[0] if coords else None,
        "lon_deg": coords[1] if coords else None,
        "creation_utc": creation_utc,
        "duration_sec": duration_sec,
        "nb_streams": info.get("format", {}).get("nb_streams", 1),
    }


def extract_neighbor_frames(video_path: Path, out_dir: Path, timestamp_sec: float, count: int = 5) -> list[np.ndarray]:
    """Extract closely spaced consecutive frames around timestamp_sec to minimize wearable camera motion."""
    frames_dir = out_dir / "extracted_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for old in frames_dir.glob("*.png"):
        old.unlink()
    pattern = str(frames_dir / "frame_%02d.png")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "quiet",
            "-ss",
            f"{max(0.0, timestamp_sec):.2f}",
            "-i",
            str(video_path),
            "-frames:v",
            str(count),
            pattern,
        ],
        check=True,
    )
    frames: list[np.ndarray] = []
    for p in sorted(frames_dir.glob("frame_*.png")):
        arr = np.asarray(Image.open(p).convert("RGB"), dtype=np.float32) / 255.0
        frames.append(arr)
    return frames


def align_and_stack_wearable_frames(frames: list[np.ndarray]) -> np.ndarray:
    """Align consecutive frames via FFT phase correlation on bright features, rejecting high-motion frames."""
    if len(frames) <= 1:
        return frames[0]
    ref = frames[0]
    ref_luma = 0.2126 * ref[..., 0] + 0.7152 * ref[..., 1] + 0.0722 * ref[..., 2]
    ref_edge = ref_luma - gaussian_filter(ref_luma, sigma=8.0)
    f_ref = np.fft.fft2(ref_edge)

    aligned = [ref]
    h, w = ref_luma.shape
    for frm in frames[1:]:
        luma = 0.2126 * frm[..., 0] + 0.7152 * frm[..., 1] + 0.0722 * frm[..., 2]
        edge = luma - gaussian_filter(luma, sigma=8.0)
        cross = f_ref * np.conj(np.fft.fft2(edge))
        cross /= np.maximum(np.abs(cross), 1e-8)
        corr = np.fft.ifft2(cross).real
        py, px = np.unravel_index(np.argmax(corr), corr.shape)
        dy = float(py if py < h // 2 else py - h)
        dx = float(px if px < w // 2 else px - w)
        # If head rotation between frames is small (< 18px), align and include; otherwise reject to prevent ghosting
        if abs(dy) <= 18.0 and abs(dx) <= 18.0:
            shifted = np.stack([nd_shift(frm[..., c], shift=(dy, dx), order=1, mode="nearest") for c in range(3)], axis=-1)
            # Also reject per-pixel ghosting where difference from reference is large (moving person/lantern)
            diff = np.abs(shifted - ref).mean(axis=-1, keepdims=True)
            ghost_gate = np.clip((diff - 0.04) / 0.06, 0.0, 1.0)
            blended = shifted * (1.0 - ghost_gate) + ref * ghost_gate
            aligned.append(blended)
    return np.mean(aligned, axis=0)


def restore_highlight_protected(stacked_rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Produce V1 (Photometric Stretch) and V2 (Highlight-Protected Multi-Scale Retinex + Chroma Denoise)."""
    luma = 0.2126 * stacked_rgb[..., 0] + 0.7152 * stacked_rgb[..., 1] + 0.0722 * stacked_rgb[..., 2]
    # Protect bright camp lights & Moon core from blowing out
    highlight_mask = np.clip((luma - 0.30) / 0.45, 0.0, 1.0)
    highlight_mask = gaussian_filter(highlight_mask, sigma=14.0)[..., None]

    black_floor = float(np.percentile(luma, 5.0)) * 0.85
    signal = np.maximum(stacked_rgb - black_floor, 0.0)

    # Suppress H.265 deep-shadow chroma macroblock noise while preserving luminance edges
    lum_sig = (0.2126 * signal[..., 0] + 0.7152 * signal[..., 1] + 0.0722 * signal[..., 2])[..., None]
    chroma_smooth = gaussian_filter(signal, sigma=(5.0, 5.0, 0.0))
    smooth_lum = np.maximum(
        0.2126 * chroma_smooth[..., 0:1] + 0.7152 * chroma_smooth[..., 1:2] + 0.0722 * chroma_smooth[..., 2:3],
        1e-4,
    )
    shadow_weight = np.clip(1.0 - (lum_sig / 0.06), 0.0, 1.0)
    clean_sig = signal * (1.0 - 0.78 * shadow_weight) + (lum_sig * (chroma_smooth / smooth_lum)) * (0.78 * shadow_weight)

    med = float(np.median(luma))
    beta_v1 = 22.0 if med < 0.015 else (14.0 if med < 0.04 else 8.0)
    beta_v2 = 42.0 if med < 0.015 else (24.0 if med < 0.04 else 13.0)

    v1_stretch = np.arcsinh(clean_sig * beta_v1) / np.arcsinh(beta_v1)
    v1 = np.clip(v1_stretch * (1.0 - highlight_mask) + stacked_rgb * highlight_mask, 0.0, 1.0)

    v2_stretch = np.arcsinh(clean_sig * beta_v2) / np.arcsinh(beta_v2)
    blur_mid = gaussian_filter(v2_stretch, sigma=(20.0, 20.0, 0.0))
    detail = v2_stretch - blur_mid
    v2_retinex = np.clip(v2_stretch + 0.42 * detail, 0.0, 1.0)
    v2 = np.clip(v2_retinex * (1.0 - highlight_mask) + stacked_rgb * highlight_mask, 0.0, 1.0)
    return v1, v2


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Albedian restoration & calibration on real media.")
    parser.add_argument("input_path", type=Path, help="Path to source image or video")
    parser.add_argument("--timestamp", type=float, default=None, help="Timestamp in seconds for video frame extraction")
    args = parser.parse_args()

    src_path: Path = args.input_path.resolve()
    if not src_path.exists():
        print(f"Error: File not found: {src_path}", file=sys.stderr)
        return 1

    asset_hash = sha256_file(src_path)
    out_dir = ROOT_DIR / "data" / "artifacts" / f"{src_path.stem}_{asset_hash[:10]}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] Ingesting immutable SourceAsset: {src_path.name}")
    print(f"      SHA-256: {asset_hash}")

    is_video = src_path.suffix.lower() in VIDEO_EXTS
    # Stills carry no parsed time or place yet; nothing is substituted for them
    meta: dict[str, Any] = {
        "model": "Still Camera",
        "lat_deg": None,
        "lon_deg": None,
        "creation_utc": None,
    }
    if is_video:
        meta = probe_video_metadata(src_path)
        t_sec = args.timestamp if args.timestamp is not None else min(4.0, meta["duration_sec"] * 0.35)
        gps_label = (
            f"({meta['lat_deg']:.4f}°, {meta['lon_deg']:.4f}°)"
            if meta["lat_deg"] is not None and meta["lon_deg"] is not None
            else "none in file"
        )
        print(
            f"      Camera: {meta['model']} | GPS: {gps_label} | "
            f"Captured: {meta['creation_utc'] or 'none in file'} | Sampled @ t={t_sec:.1f}s"
        )
        frames = extract_neighbor_frames(src_path, out_dir, t_sec, count=5)
        raw_rgb = frames[0]
        stacked_rgb = align_and_stack_wearable_frames(frames)
    else:
        raw_rgb = np.asarray(Image.open(src_path).convert("RGB"), dtype=np.float32) / 255.0
        stacked_rgb = raw_rgb

    print("[2/4] Running MediaExperimentRuntime (Photometric Stretch + Highlight-Protected Retinex + Motion-Gated Stack)...")
    v1_rgb, v2_rgb = restore_highlight_protected(stacked_rgb)

    # Also run quantitative runtime metrics on the luminance plane
    raw_luma = 0.2126 * raw_rgb[..., 0] + 0.7152 * raw_rgb[..., 1] + 0.0722 * raw_rgb[..., 2]
    v2_luma = 0.2126 * v2_rgb[..., 0] + 0.7152 * v2_rgb[..., 1] + 0.0722 * v2_rgb[..., 2]
    runtime = MediaExperimentRuntime()
    rt_branch = runtime.execute_restoration_branch([raw_luma], profile="star_protected", preserve_stars=True)

    raw_u8 = np.clip(raw_rgb * 255.0, 0, 255).astype(np.uint8)
    v1_u8 = np.clip(v1_rgb * 255.0, 0, 255).astype(np.uint8)
    v2_u8 = np.clip(v2_rgb * 255.0, 0, 255).astype(np.uint8)
    comp_u8 = np.concatenate([raw_u8, v1_u8, v2_u8], axis=1)

    Image.fromarray(raw_u8).save(out_dir / "00_original_source.png")
    Image.fromarray(v1_u8).save(out_dir / "01_v1_photometric_stretch.png")
    Image.fromarray(v2_u8).save(out_dir / "02_v2_retinex_highlight_protected.png")
    Image.fromarray(comp_u8).save(out_dir / "03_comparison_strip.png")

    print("[3/4] Running Source Extraction & Astronomical Calibration...")
    h, w = v2_luma.shape
    detections = detect_point_sources(v2_luma, snr_threshold=5.0)
    has_moment = (
        meta["creation_utc"] is not None
        and meta["lat_deg"] is not None
        and meta["lon_deg"] is not None
    )
    calibration_status = "skipped: no capture time or location in file"
    visible_bodies = []
    visible_stars = []
    if has_moment:
        solution = solve_and_verify_field(
            detections=detections,
            width=w,
            height=h,
            obs_utc_iso=meta["creation_utc"],
            observer_lat=meta["lat_deg"],
            observer_lon=meta["lon_deg"],
            source_sha256=asset_hash,
        )
        calibration_status = solution.status
        ephemerides = compute_solar_system_ephemerides(
            obs_utc_iso=meta["creation_utc"],
            lat_deg=meta["lat_deg"],
            lon_deg=meta["lon_deg"],
        )
        visible_bodies = [b for b in ephemerides if b.alt_deg > 0.0]
        for star in PINNED_CATALOG_STARS:
            alt, az = icrs_to_altaz(star.ra_deg, star.dec_deg, meta["lat_deg"], meta["lon_deg"], meta["creation_utc"])
            if alt > 0.0:
                visible_stars.append(
                    {
                        "name": star.common_name,
                        "designation": star.bayer_designation,
                        "constellation": star.constellation,
                        "alt_deg": round(alt, 2),
                        "az_deg": round(az, 2),
                        "mag": star.mag,
                        "distance_pc": star.median_pc,
                        "evidence_class": "predicted",
                    }
                )

    report = {
        "source_file": str(src_path),
        "source_sha256": asset_hash,
        "metadata": meta,
        "luminance_summary": {
            "raw_median": round(float(np.median(raw_luma)), 4),
            "raw_p95": round(float(np.percentile(raw_luma, 95)), 4),
            "v1_median": round(float(np.median(v1_rgb)), 4),
            "v2_median": round(float(np.median(v2_rgb)), 4),
            "shadow_snr_gain_db": round(rt_branch.metrics.shadow_snr_gain_db, 2),
        },
        "observed_point_detections": len(detections),
        "wcs_calibration_status": calibration_status,
        "predicted_solar_system_above_horizon": [
            {
                "name": b.name,
                "object_type": b.object_type,
                "alt_deg": round(b.alt_deg, 2),
                "az_deg": round(b.az_deg, 2),
                "distance_au": round(b.distance_au, 5),
                "apparent_magnitude": b.apparent_magnitude,
                "evidence_class": b.evidence_class,
            }
            for b in visible_bodies
        ],
        "predicted_bright_stars_above_horizon": visible_stars,
        "artifacts_dir": str(out_dir),
    }

    report_path = out_dir / "experiment_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"[4/4] Complete! Artifacts written to: {out_dir}")
    print(
        f"      Raw median luma: {report['luminance_summary']['raw_median']:.4f} -> "
        f"V2 restored median: {report['luminance_summary']['v2_median']:.4f} "
        f"(+{report['luminance_summary']['shadow_snr_gain_db']} dB shadow SNR)"
    )
    print(
        f"      Solar System bodies overhead: "
        f"{', '.join(f'{b.name} (Alt {b.alt_deg:+.1f}°, Az {b.az_deg:.1f}°)' for b in visible_bodies)}"
    )
    star_summary = ", ".join(f"{s['name']} (Alt {s['alt_deg']:+.1f}°)" for s in visible_stars[:6])
    print(f"      Bright stars overhead ({len(visible_stars)}): {star_summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
