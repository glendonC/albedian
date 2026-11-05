"""A pinned bright star's effective temperature, luminosity and radius, from its Gaia DR3 row in the committed
astro tiles (`apps/web/public/catalog/stars/gaia-dr3`, docs/science/CATALOG_STAR_TILES.md), never from a formula.

- Temperature: Gaia DR3 GSP-Phot (Andrae et al. 2023, doi:10.1051/0004-6361/202243462), with its 16th and 84th
  percentiles.
- Luminosity: Gaia DR3 FLAME (Creevey et al. 2023, doi:10.1051/0004-6361/202243688; Fouesneau et al. 2023,
  doi:10.1051/0004-6361/202243919), with its 16th and 84th percentiles.
- Radius: derived here from those two by the Stefan-Boltzmann law, R / Rsun = sqrt(L / Lsun) * (Tsun / T) ** 2 with
  the IAU 2015 nominal Tsun = 5772 K, its range taken from the percentile ends (the highest luminosity with the
  lowest temperature, and the reverse), so it is a span, not a percentile interval.

A Hipparcos-only star, and every value a Gaia row lacks, gets nothing. A FLAME luminosity that lies outside its own
percentiles (as 61 rows in the release do, the archive's lower bound floored at 0.01 Lsun) keeps its value without
an interval, and the radius then has no span either.
"""

from __future__ import annotations

from functools import lru_cache
import math
from pathlib import Path
from typing import Any

import numpy as np

from services.science import catalog_tiles as ct
from services.science.catalog_model import CatalogStar

TILES = Path(__file__).resolve().parents[2] / "apps" / "web" / "public" / "catalog" / "stars" / "gaia-dr3"
NOMINAL_SOLAR_TEFF_K = 5772.0
GSP_PHOT = "Gaia DR3 GSP-Phot (Andrae et al. 2023, doi:10.1051/0004-6361/202243462)"
FLAME = (
    "Gaia DR3 FLAME (Creevey et al. 2023, doi:10.1051/0004-6361/202243688; "
    "Fouesneau et al. 2023, doi:10.1051/0004-6361/202243919)"
)
MODEL_LIMITATION = (
    "GSP-Phot and FLAME values are model fits to Gaia's BP/RP spectra, photometry and parallax, "
    "not measurements of this star; their papers describe their reach and limits."
)


@lru_cache(maxsize=1)
def _astro_rows_by_source_id() -> dict[int, dict[str, float]]:
    """Every layer-0 tile row with a Gaia DR3 source id, keyed by that id: the bright stars the pins come from."""
    rows: dict[int, dict[str, float]] = {}
    for detail_path in sorted((TILES / "L0").glob("*.detail.bin")):
        _, detail = ct.decode_detail_tile(detail_path.read_bytes())
        _, astro = ct.decode_astro_tile(detail_path.with_name(detail_path.name.replace(".detail.", ".astro.")).read_bytes())
        ids = (detail["idHi"].astype(np.int64) << 32) | detail["idLo"].astype(np.int64)
        teff = [ct.decode_teff(astro[key]) for key in ("teff", "teffLower", "teffUpper")]
        lum = [ct.decode_luminosity(astro[key]) for key in ("logLum", "logLumLower", "logLumUpper")]
        for i, source_id in enumerate(ids):
            rows[int(source_id)] = {
                "teff": float(teff[0][i]), "teffLower": float(teff[1][i]), "teffUpper": float(teff[2][i]),
                "lum": float(lum[0][i]), "lumLower": float(lum[1][i]), "lumUpper": float(lum[2][i]),
            }
    return rows


def _radius_solar(lum_solar: float, teff_k: float) -> float:
    return math.sqrt(lum_solar) * (NOMINAL_SOLAR_TEFF_K / teff_k) ** 2


def _within(value: float, lower: float, upper: float) -> bool:
    return all(math.isfinite(v) for v in (value, lower, upper)) and lower <= value <= upper


def gaia_stellar_parameters(star: CatalogStar) -> dict[str, Any] | None:
    """The star's GSP-Phot temperature, FLAME luminosity and the radius they give, from its Gaia DR3 tile row.

    None for a star without a Gaia DR3 row (the Hipparcos-only pins) or a row with neither value. Each value
    comes with its interval only when the row's percentiles bracket it.
    """
    prefix = "Gaia DR3 "
    if star.provider != "Gaia DR3" or not star.catalog_source_id.startswith(prefix):
        return None
    row = _astro_rows_by_source_id().get(int(star.catalog_source_id[len(prefix):]))
    if row is None or not (math.isfinite(row["teff"]) or math.isfinite(row["lum"])):
        return None

    out: dict[str, Any] = {"catalogSourceId": star.catalog_source_id, "limitations": [MODEL_LIMITATION]}
    citations: list[str] = []
    teff_ok = math.isfinite(row["teff"])
    teff_span = _within(row["teff"], row["teffLower"], row["teffUpper"])
    lum_ok = math.isfinite(row["lum"])
    lum_span = _within(row["lum"], row["lumLower"], row["lumUpper"])
    if teff_ok:
        out["teffK"] = {"value": row["teff"], "method": "Gaia DR3 GSP-Phot"}
        if teff_span:
            out["teffK"].update(lower=row["teffLower"], upper=row["teffUpper"], interval="16th to 84th percentile")
        citations.append(GSP_PHOT)
    if lum_ok:
        out["luminositySolar"] = {"value": row["lum"], "method": "Gaia DR3 FLAME"}
        if lum_span:
            out["luminositySolar"].update(lower=row["lumLower"], upper=row["lumUpper"], interval="16th to 84th percentile")
        else:
            out["limitations"].append(
                "FLAME's own 16th and 84th percentiles do not bracket this luminosity (as in the Gaia archive), "
                "so no interval is given."
            )
        citations.append(FLAME)
    if teff_ok and lum_ok:
        radius: dict[str, Any] = {
            "value": _radius_solar(row["lum"], row["teff"]),
            "method": "Derived: Stefan-Boltzmann from the GSP-Phot temperature and FLAME luminosity, Tsun 5772 K",
            "derived": True,
        }
        if teff_span and lum_span:
            radius.update(
                lower=_radius_solar(row["lumLower"], row["teffUpper"]),
                upper=_radius_solar(row["lumUpper"], row["teffLower"]),
                interval="span of the two percentile intervals",
            )
        out["radiusSolar"] = radius
    out["citations"] = citations
    return out
