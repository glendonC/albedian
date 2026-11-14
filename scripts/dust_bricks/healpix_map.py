"""HEALPix-by-distance-shell 3D dust map loading, Galactic sampling, and exact radial columns."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from scripts.dust_bricks.fits_io import fits_float_column, fits_image, named_hdu, read_fits_hdus
from services.science import catalog_tiles as ct


@dataclass(frozen=True)
class HealpixDustMap:
    mean: np.ndarray  # (shells, pixels), ZGR23 extinction per pc
    std: np.ndarray
    boundaries_pc: np.ndarray  # shells + 1 shell edges
    order: int  # HEALPix order; nside = 2**order


def load_healpix_dust_map(path: Path) -> HealpixDustMap:
    hdus = read_fits_hdus(path)
    mean_h, std_h = named_hdu(hdus, "MEAN"), named_hdu(hdus, "STD.")
    if mean_h.header.get("ORDERING") != "NEST":
        raise ValueError("expected nested HEALPix ordering")
    nside = int(mean_h.header["NSIDE"])
    order = nside.bit_length() - 1
    if 2**order != nside:
        raise ValueError(f"nside {nside} is not a power of two")
    boundaries = fits_float_column(path, named_hdu(hdus, "RADIAL PIXEL BOUNDARIES"))
    mean, std = fits_image(path, mean_h), fits_image(path, std_h)
    if mean.shape != (len(boundaries) - 1, 12 * nside * nside) or std.shape != mean.shape:
        raise ValueError(f"map shape {mean.shape} does not match {len(boundaries) - 1} shells at nside {nside}")
    return HealpixDustMap(mean=mean, std=std, boundaries_pc=boundaries, order=order)


def sample_map(dust: HealpixDustMap, x: np.ndarray, y: np.ndarray, z: np.ndarray, *, field: str = "mean") -> np.ndarray:
    """Density at Galactic heliocentric points (pc): the shell and pixel holding each point.

    NaN where the map has no 3D density: inside its innermost shell edge and beyond its outermost.
    """
    x, y, z = (np.asarray(a, dtype=np.float64) for a in (x, y, z))
    d = np.sqrt(x * x + y * y + z * z)
    edges = dust.boundaries_pc
    out = np.full(d.shape, np.nan)
    inside = (d >= edges[0]) & (d < edges[-1])
    if np.any(inside):
        lon = np.degrees(np.arctan2(y[inside], x[inside])) % 360.0
        lat = np.degrees(np.arcsin(np.clip(z[inside] / d[inside], -1.0, 1.0)))
        pix = ct.radec_to_nest(dust.order, lon, lat)
        shell = np.searchsorted(edges, d[inside], side="right") - 1
        out[inside] = (dust.mean if field == "mean" else dust.std)[shell, pix]
    return out


def map_columns(dust: HealpixDustMap, lon_deg, lat_deg, *, outer_pc: float | None = None) -> np.ndarray:
    """The map's own columns from its inner edge outward along directions (ZGR23 units).

    A ray from the Sun stays in one HEALPix pixel, so each column is exact: every shell's density
    times the length of the ray inside it.
    """
    pix = ct.radec_to_nest(dust.order, np.atleast_1d(lon_deg), np.atleast_1d(lat_deg))
    lo, hi = dust.boundaries_pc[:-1], dust.boundaries_pc[1:]
    if outer_pc is not None:
        hi = np.minimum(hi, outer_pc)
    lengths = np.clip(hi - lo, 0.0, None)
    unique, inverse = np.unique(pix, return_inverse=True)
    return (lengths @ np.asarray(dust.mean[:, unique], dtype=np.float64))[inverse.ravel()]


def map_column(dust: HealpixDustMap, lon_deg: float, lat_deg: float, *, outer_pc: float | None = None) -> float:
    return float(map_columns(dust, [lon_deg], [lat_deg], outer_pc=outer_pc)[0])


__all__ = [
    "HealpixDustMap",
    "load_healpix_dust_map",
    "sample_map",
    "map_columns",
    "map_column",
]
