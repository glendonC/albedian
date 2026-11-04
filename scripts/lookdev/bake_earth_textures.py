#!/usr/bin/env python3
"""Bake the web app's Earth textures from the NASA sources used for look-dev.

Writes 8192x4096 equirectangular JPEGs to `apps/web/public/textures/` plus a provenance manifest:
- day: NASA Blue Marble Next Generation, November 2004, topography and bathymetry;
- night: NASA Black Marble 2016 color composite with its dim base layer removed, so only the
  city lights remain (the same luminance mask the Blender look-dev shader applies).

Download the sources first (see `data/lookdev/sources/SHA256SUMS`), then from the repo root:
    uv run python scripts/lookdev/bake_earth_textures.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCES = REPO_ROOT / "data" / "lookdev" / "sources"
OUT = REPO_ROOT / "apps" / "web" / "public" / "textures"
SIZE = (8192, 4096)
# Luminance window that separates Black Marble's lights from the dim land layer NASA blended in
LIGHTS_FROM, LIGHTS_TO = 0.10, 0.55


def smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def main() -> None:
    Image.MAX_IMAGE_PIXELS = None
    day_src = SOURCES / "earth_bluemarble_2004-11_21600.jpg"
    night_src = SOURCES / "earth_blackmarble_2016_3km.jpg"

    day_out = OUT / "earth_day_bluemarble_2004-11_8k.jpg"
    Image.open(day_src).convert("RGB").resize(SIZE, Image.Resampling.LANCZOS).save(day_out, quality=90, optimize=True)

    night = np.asarray(Image.open(night_src).convert("RGB").resize(SIZE, Image.Resampling.LANCZOS), dtype=np.float32) / 255.0
    luminance = night @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    lights = night * smoothstep(LIGHTS_FROM, LIGHTS_TO, luminance)[..., None]
    night_out = OUT / "earth_night_blackmarble_2016_8k.jpg"
    Image.fromarray(np.round(lights * 255.0).astype(np.uint8)).save(night_out, quality=92, optimize=True)

    manifest = {
        day_out.name: {
            "source": "NASA Blue Marble Next Generation, November 2004, topography and bathymetry (21600x10800)",
            "url": "https://assets.science.nasa.gov/content/dam/science/esd/eo/images/bmng/bmng-topography-bathymetry/november/world.topo.bathy.200411.3x21600x10800.jpg",
            "credit": "NASA Earth Observatory / Reto Stockli (NASA GSFC)",
            "processing": "Lanczos resample to 8192x4096",
            "note": "Cloud-free surface composite from 2004, not current conditions",
        },
        night_out.name: {
            "source": "NASA Black Marble 2016 color composite, 3 km (13500x6750)",
            "url": "https://assets.science.nasa.gov/content/dam/science/esd/eo/images/imagerecords/144000/144898/BlackMarble_2016_3km.jpg",
            "credit": "NASA Earth Observatory / Joshua Stevens, using Suomi NPP VIIRS data (Miguel Roman, NASA GSFC)",
            "processing": f"Lanczos resample to 8192x4096; dim base layer removed with a luminance smoothstep {LIGHTS_FROM} to {LIGHTS_TO}",
            "note": "Relative brightness follows NASA's stretched visualization, not calibrated radiance",
        },
    }
    (OUT / "earth_texture_sources.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for path in (day_out, night_out):
        print(f"{path.name}: {path.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
