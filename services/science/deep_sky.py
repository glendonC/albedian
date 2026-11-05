"""3D parsec nearest neighbours among the pinned bright stars (`compute_3d_nearest_neighbors`).

The eight-object deep-sky table and the stellar temperature formula that lived here had no source, so they are
gone: deep-sky objects return with a sourced catalog (OpenNGC, through the data lane), and a star's temperature,
luminosity and radius come from its Gaia DR3 row (services/science/stellar_parameters.py).
"""

from __future__ import annotations

import math
from typing import Any

from services.science.catalog import (
    STAR_BY_ID,
    STAR_BY_NAME,
    catalog_cartesian_pc,
    get_all_sky_catalog_stars,
)


def compute_3d_nearest_neighbors(
    target_star_id: str,
    k: int = 3,
) -> list[dict[str, Any]]:
    """Compute the `k` nearest 3D neighbors in parsecs from `get_all_sky_catalog_stars()`
    to `target_star_id` using ICRS Cartesian parsec coordinates.
    """
    clean_id = target_star_id.replace("finding-predicted-", "").replace("finding-", "")
    target = STAR_BY_ID.get(clean_id) or STAR_BY_NAME.get(clean_id.lower())
    if target is None:
        return []

    tx, ty, tz = catalog_cartesian_pc(target)
    neighbors: list[tuple[float, Any]] = []
    for star in get_all_sky_catalog_stars():
        if star.id == target.id:
            continue
        sx, sy, sz = catalog_cartesian_pc(star)
        dist_pc = math.sqrt((sx - tx) ** 2 + (sy - ty) ** 2 + (sz - tz) ** 2)
        neighbors.append((dist_pc, star))

    neighbors.sort(key=lambda item: item[0])
    out: list[dict[str, Any]] = []
    for dist_pc, star in neighbors[: max(1, int(k))]:
        out.append(
            {
                "id": star.id,
                "name": star.common_name,
                "constellation": star.constellation,
                "distanceFromTargetPc": round(dist_pc, 2),
                "distanceFromTargetLy": round(dist_pc * 3.26156, 2),
            }
        )
    return out
