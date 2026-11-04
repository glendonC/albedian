#!/usr/bin/env python3
"""Render browser-ready H.264 .mp4 variants of night clips, with each clip's audio stream copied.

Usage:
    uv run python scripts/render_clip_variants.py --out <folder> <clip>[:medium|:deep] [<clip>[:mode] ...]

For each clip it writes three files named after the clip's stem, which media discovery uses to link
them back to the clip:
- `<stem>_orig.mp4`: the original remuxed to H.264 yuv420p.
- `<stem>_v1.mp4`: hqdn3d denoise and a highlight-protected shadow curve.
- `<stem>_v2.mp4`: stronger hqdn3d denoise, a shadow curve and unsharp local contrast.
The mode picks the curves: `medium` for moonlit clips, `deep` (the default) for darker ones.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

CURVES = {
    "medium": (
        "curves=all='0/0 0.04/0.16 0.15/0.38 0.45/0.68 0.80/0.88 1/1'",
        "curves=all='0/0 0.03/0.22 0.12/0.48 0.40/0.74 0.78/0.90 1/1'",
    ),
    "deep": (
        "curves=all='0/0 0.015/0.14 0.05/0.34 0.20/0.62 0.70/0.86 1/1'",
        "curves=all='0/0 0.012/0.22 0.04/0.46 0.16/0.72 0.65/0.88 1/1'",
    ),
}
EVEN_SIZE = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
H264_WITH_AUDIO_COPY = [
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
    "-c:a", "copy",
    "-movflags", "+faststart",
]


def render(src_path: Path, out_dir: Path, mode: str) -> list[Path]:
    """Write the three variants of one clip and return their paths."""
    v1_curve, v2_curve = CURVES[mode]
    filters = {
        "orig": EVEN_SIZE,
        "v1": f"hqdn3d=3.5:3.0:6.0:4.5,{v1_curve},eq=saturation=0.92,{EVEN_SIZE}",
        "v2": f"hqdn3d=5.0:4.5:8.0:6.0,{v2_curve},unsharp=7:7:0.85:5:5:0.0,eq=saturation=0.88,{EVEN_SIZE}",
    }
    written = []
    for tag, video_filter in filters.items():
        out_path = out_dir / f"{src_path.stem}_{tag}.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(src_path), "-vf", video_filter, *H264_WITH_AUDIO_COPY, str(out_path)],
            check=True,
        )
        written.append(out_path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True, help="folder for the rendered variants")
    parser.add_argument("clips", nargs="+", help="clip path, optionally followed by :medium or :deep")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for spec in args.clips:
        mode = "deep"
        for candidate in CURVES:
            if spec.endswith(f":{candidate}"):
                spec, mode = spec[: -len(candidate) - 1], candidate
        src_path = Path(spec)
        if not src_path.exists():
            print(f"Skipping missing {src_path}")
            continue
        print(f"=== Rendering variants for {src_path.name} ({mode}) ===")
        written = render(src_path, args.out, mode)
        print(f"    -> Wrote {', '.join(p.name for p in written)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
