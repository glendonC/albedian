#!/usr/bin/env python3
"""Bake the web app's Earth night-lights texture from the NASA Black Marble source used for look-dev.

Writes an 8192x4096 equirectangular JPEG to `apps/web/public/textures/`:
- night: NASA Black Marble 2016 color composite with its dim base layer removed, so only the
  city lights remain (the same luminance mask the Blender look-dev shader applies).

(The Earth day base map, ETOPO 2022 relief, Natural Earth water mask, and 2002 clouds are baked
and recorded by `scripts/bake_earth_maps.py`.)

Download the sources first (see `data/lookdev/sources/SHA256SUMS`), then from the repo root:
    uv run python scripts/lookdev/bake_earth_textures.py

Their sources, credits and SHA-256s are in `apps/web/public/textures/PROVENANCE.json`; a bake
that changes their bytes must update those entries, or `pnpm verify:provenance` fails.
"""

from __future__ import annotations

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
    night_src = SOURCES / "earth_blackmarble_2016_3km.jpg"

    night = np.asarray(Image.open(night_src).convert("RGB").resize(SIZE, Image.Resampling.LANCZOS), dtype=np.float32) / 255.0
    luminance = night @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    lights = night * smoothstep(LIGHTS_FROM, LIGHTS_TO, luminance)[..., None]
    night_out = OUT / "earth_night_blackmarble_2016_8k.jpg"
    Image.fromarray(np.round(lights * 255.0).astype(np.uint8)).save(night_out, quality=92, optimize=True)

    print(f"{night_out.name}: {night_out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
