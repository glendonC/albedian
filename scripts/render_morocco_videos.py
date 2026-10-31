#!/usr/bin/env python3
"""Render full-duration synchronized H.264 .mp4 video variants (Original, V1 Stretch, V2 Retinex)
with preserved 48kHz stereo AAC audio from the 3 Morocco Meta Ray-Ban Display Glasses .MOV clips.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "data" / "uploads" / "morocco"
OUT_DIR = ROOT_DIR / "apps" / "web" / "public" / "morocco"

CLIPS = [
    ("camp_moon_1300", SRC_DIR / "morocco-camp-moon-1300.mov", "medium"),
    ("camp_dark_1234", SRC_DIR / "morocco-camp-dark-1234.mov", "deep"),
    ("desert_1240", SRC_DIR / "morocco-desert-moon-1240.mov", "deep"),
]


def run_ffmpeg(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for prefix, src_path, mode in CLIPS:
        if not src_path.exists():
            print(f"Skipping missing {src_path}")
            continue
        orig_mp4 = OUT_DIR / f"{prefix}_orig.mp4"
        v1_mp4 = OUT_DIR / f"{prefix}_v1.mp4"
        v2_mp4 = OUT_DIR / f"{prefix}_v2.mp4"

        print(f"=== Rendering full-video variants for {src_path.name} ({mode}) ===")

        # 1. Original video remuxed to browser-compatible H.264 yuv420p with exact stereo AAC audio copy
        run_ffmpeg([
            "ffmpeg", "-y", "-v", "error",
            "-i", str(src_path),
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(orig_mp4),
        ])

        # 2. V1: Temporal-Spatial 3D Denoise (hqdn3d) + Highlight-Protected Shadow Curve Stretch
        if mode == "medium":
            v1_curve = "curves=all='0/0 0.04/0.16 0.15/0.38 0.45/0.68 0.80/0.88 1/1'"
            v2_curve = "curves=all='0/0 0.03/0.22 0.12/0.48 0.40/0.74 0.78/0.90 1/1'"
        else:
            v1_curve = "curves=all='0/0 0.015/0.14 0.05/0.34 0.20/0.62 0.70/0.86 1/1'"
            v2_curve = "curves=all='0/0 0.012/0.22 0.04/0.46 0.16/0.72 0.65/0.88 1/1'"

        run_ffmpeg([
            "ffmpeg", "-y", "-v", "error",
            "-i", str(src_path),
            "-vf", f"hqdn3d=3.5:3.0:6.0:4.5,{v1_curve},eq=saturation=0.92,scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(v1_mp4),
        ])

        # 3. V2: Temporal-Spatial Denoise + Multi-Scale Local Contrast (unsharp) + Shadow Lift + Highlight Protection
        run_ffmpeg([
            "ffmpeg", "-y", "-v", "error",
            "-i", str(src_path),
            "-vf", f"hqdn3d=5.0:4.5:8.0:6.0,{v2_curve},unsharp=7:7:0.85:5:5:0.0,eq=saturation=0.88,scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(v2_mp4),
        ])

        print(f"    -> Wrote {orig_mp4.name}, {v1_mp4.name}, {v2_mp4.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
