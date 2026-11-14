#!/usr/bin/env python3
"""Pilot: resample 3D dust maps onto sparse Cartesian bricks for a ray-marched volume.

Reads the Edenhofer et al. (2024) dust map, extinction density on HEALPix sky pixels (nside 256,
nested, Galactic) times 516 log-spaced distance shells from 68.6 to 1248 pc, and resamples it onto
heliocentric Galactic Cartesian grids (x toward l = 0, y toward l = 90 deg, z toward b = 90 deg).
Voxels are grouped into cubic bricks; a brick is kept when its peak density clears a threshold.
The pilot measures wall time, peak memory, bricks and bytes per voxel size and threshold, and
checks the result against the map itself: rays from the Sun stay in one HEALPix pixel, so the
map's own column along a ray is exact, and the bricks must reproduce it. It also bricks the
O'Neill et al. (2024) Local Bubble shell cube, which is already Cartesian at 2 pc.

Everything here is a measurement pilot. Output stays in `.cache/dust/bricks-pilot/`; nothing is
written under `apps/web/public/`.

Units: both maps give extinction density in ZGR23 units per parsec (Zhang, Green and Rix 2023).
A_V at 540 nm is 2.8 times the ZGR23 extinction (Edenhofer et al. 2024, section 7); other bands
need the ZGR23 extinction curve. Inside 68.6 pc the Edenhofer map has no 3D density (it gives an
integrated column per direction instead), so the bricks leave that sphere empty.

Usage:
    uv run python scripts/bake_dust_bricks.py                    # 8 and 4 pc, report only
    uv run python scripts/bake_dust_bricks.py --voxel 8 4 2      # add 2 pc
    uv run python scripts/bake_dust_bricks.py --write            # also write bricks to the cache
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.dust_bricks import (  # noqa: E402
    A_V_PER_ZGR23,
    DEFAULT_OUT,
    DUST_CACHE,
    EDENHOFER_PATH,
    FITS_BLOCK,
    ONEILL_SHELL_PATH,
    BrickBuild,
    FitsHdu,
    HealpixDustMap,
    Sampler,
    TwoLevel,
    _card_value,
    _FITS_DTYPES,
    _named,
    brick_column,
    build_arg_parser,
    build_bricks,
    fits_float_column,
    fits_image,
    load_healpix_dust_map,
    map_column,
    map_columns,
    named_hdu,
    peak_rss_gb,
    pilot_map,
    pilot_shell,
    random_directions,
    read_fits_hdus,
    run_pilot,
    sample_map,
    two_level,
    two_level_column,
    write_bricks,
)
from services.science import catalog_tiles as ct  # noqa: E402


def main() -> int:
    return run_pilot(
        edenhofer_path=EDENHOFER_PATH,
        oneill_shell_path=ONEILL_SHELL_PATH,
        default_out=DEFAULT_OUT,
        description=__doc__,
    )


__all__ = [
    "A_V_PER_ZGR23",
    "BrickBuild",
    "DEFAULT_OUT",
    "DUST_CACHE",
    "EDENHOFER_PATH",
    "FITS_BLOCK",
    "FitsHdu",
    "HealpixDustMap",
    "ONEILL_SHELL_PATH",
    "ROOT_DIR",
    "Sampler",
    "TwoLevel",
    "_FITS_DTYPES",
    "_card_value",
    "_named",
    "brick_column",
    "build_arg_parser",
    "build_bricks",
    "ct",
    "fits_float_column",
    "fits_image",
    "load_healpix_dust_map",
    "main",
    "map_column",
    "map_columns",
    "named_hdu",
    "peak_rss_gb",
    "pilot_map",
    "pilot_shell",
    "random_directions",
    "read_fits_hdus",
    "run_pilot",
    "sample_map",
    "two_level",
    "two_level_column",
    "write_bricks",
]


if __name__ == "__main__":
    raise SystemExit(main())
