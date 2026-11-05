#!/usr/bin/env python3
"""Check the landing's Moon against its map: named far-side features must appear where the mesh,
as the renderer turned it, puts them.

For each feature it takes the approximate centre (to about a degree, after the IAU Gazetteer of
Planetary Nomenclature), places it on the Moon mesh recorded in a capture from
scripts/capture_landing.mjs (the mesh's world matrix, so the renderer's own orientation is what is
checked), projects it through the captured camera and compares the mean brightness within 4 px
with a ring 9 to 16 px out. Dark-floored maria come out darker than their surroundings and bright
ray craters brighter. As a control the same measure runs on maps mirrored east to west, shifted
by 180 deg and flipped north to south, which must each get at least one feature wrong.

Usage:
  uv run python scripts/check_landing_moon_features.py <capture dir> [--renderer webgpu]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

# name: (latitude, east longitude, expected appearance)
FEATURES: dict[str, tuple[float, float, str]] = {
    "Mare Moscoviense": (27.3, 147.9, "dark"),
    "Giordano Bruno": (35.9, 102.8, "bright"),
    "Mare Ingenii": (-33.7, 163.5, "dark"),
    "Tsiolkovskiy": (-20.4, 129.1, "dark"),
}
CONVENTIONS = {
    "as built": lambda lat, lon: (lat, lon),
    "mirrored east-west": lambda lat, lon: (lat, -lon),
    "shifted 180 deg": lambda lat, lon: (lat, lon + 180.0),
    "flipped north-south": lambda lat, lon: (-lat, lon),
}
PATCH_PX = 4.0
RING_PX = (9.0, 16.0)
# Features nearer the limb than this (cosine of the angle between the surface normal and the view)
# are foreshortened into their surroundings, so they are not measured
MIN_FACING = 0.3


def mesh_direction(lat_deg: float, lon_deg: float) -> np.ndarray:
    """A body-fixed direction in SphereGeometry's mesh frame: +y north, +x longitude 0, -z 90 E."""
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    return np.array([math.cos(lat) * math.cos(lon), math.sin(lat), -math.cos(lat) * math.sin(lon)])


def column_major(values: list[float]) -> np.ndarray:
    return np.array(values, dtype=np.float64).reshape(4, 4).T


def measure(capture: Path, renderer: str = "webgpu") -> dict[str, dict[str, float | None]]:
    """Contrast (patch minus ring, in 8-bit levels) of each feature under each map convention; None
    where the feature faces away, sits on the limb or falls off the frame."""
    geometry = json.loads((capture / f"{renderer}-camera.json").read_text())
    image = np.asarray(Image.open(capture / f"{renderer}-full.png").convert("L"), dtype=np.float64)
    height, width = image.shape
    moon = next(b for b in geometry["bodies"] if b["name"] == "cislunarMoon")
    to_world = column_major(moon["matrixWorld"])
    centre = np.array(moon["center"])
    camera_world = column_major(geometry["matrixWorld"])
    eye = camera_world[:3, 3]
    clip_from_world = column_major(geometry["projectionMatrix"]) @ np.linalg.inv(camera_world)
    rows, cols = np.mgrid[0:height, 0:width]

    results: dict[str, dict[str, float | None]] = {}
    for convention, remap in CONVENTIONS.items():
        results[convention] = {}
        for name, (lat, lon, _) in FEATURES.items():
            normal = to_world[:3, :3] @ mesh_direction(*remap(lat, lon))
            normal /= np.linalg.norm(normal)
            point = centre + moon["radius"] * normal
            toward_eye = (eye - point) / np.linalg.norm(eye - point)
            clip = clip_from_world @ np.append(point, 1.0)
            x = (clip[0] / clip[3] * 0.5 + 0.5) * width
            y = (0.5 - clip[1] / clip[3] * 0.5) * height
            if normal @ toward_eye < MIN_FACING or not (RING_PX[1] <= x < width - RING_PX[1] and RING_PX[1] <= y < height - RING_PX[1]):
                results[convention][name] = None
                continue
            r = np.hypot(cols - x, rows - y)
            patch = image[r <= PATCH_PX].mean()
            ring = image[(r > RING_PX[0]) & (r <= RING_PX[1])].mean()
            results[convention][name] = float(patch - ring)
    return results


def as_expected(name: str, contrast: float | None) -> bool | None:
    if contrast is None:
        return None
    return contrast < 0 if FEATURES[name][2] == "dark" else contrast > 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--renderer", default="webgpu")
    args = parser.parse_args()
    results = measure(args.capture, args.renderer)
    ok = True
    built_measured = sum(c is not None for c in results["as built"].values())
    for convention, contrasts in results.items():
        cells = []
        verdicts = []
        for name, contrast in contrasts.items():
            verdict = as_expected(name, contrast)
            verdicts.append(verdict)
            cells.append(f"{name} {'not measured' if contrast is None else f'{contrast:+.1f}'}"
                         f"{'' if verdict is None else (' ok' if verdict else ' wrong')}")
        print(f"{convention:20s} " + "; ".join(cells))
        measured = [v for v in verdicts if v is not None]
        if convention == "as built":
            ok &= bool(measured) and all(measured)
        else:
            # a wrong map must miss at least one feature, or fail to show it at all
            ok &= not all(measured) or len(measured) < built_measured
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
