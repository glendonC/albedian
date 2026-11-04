"""Real stars for the benchmark: a cone reader over the G 12 catalog tiles.

The tiles under apps/web/public/catalog/stars/gaia-dr3/ hold every Gaia DR3 source brighter than
G 12 and the Hipparcos 2007 entries that replace the brightest, at epoch J2016.0
(docs/science/CATALOG_STAR_TILES.md). This reads them through the data lane's codec in
services/science/catalog_tiles.py, read only, and moves each star to the frame's date by its
proper motion.

Photometry for rendering:

- V from Gaia G and BP-RP by the Riello et al. (2021, A&A 649, A3, table C2) polynomial,
  G - V = -0.02704 + 0.01424 c - 0.2156 c^2 + 0.01426 c^3, with c clamped to its stated range
  [-0.5, 2.75]; a star without BP-RP takes c = 0.82 [inference: a typical colour at G < 12].
- V for the Hipparcos entries is their Hp magnitude [inference: |Hp - V| stays under about 0.3 mag
  for most colours, and these stars saturate a phone sensor anyway].
- Effective temperature, used only to tint a star's colour: from BP-RP by the dwarf relation of
  Mucciarelli and Bellazzini (2020, RNAAS 4, 52), theta = 0.4929 + 0.5092 c - 0.0353 c^2, and from
  B-V by Ballesteros (2012, EPL 97, 34008); both are used beyond their calibrated ranges and clamped
  to 2500 to 30000 K [inference].
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from research.benchmarks.phone_photos.astrometry import propagate_proper_motion, vec_to_radec
from services.science import catalog_tiles as ct

DEFAULT_RELEASE_DIR = Path(__file__).resolve().parents[3] / "apps" / "web" / "public" / "catalog" / "stars" / "gaia-dr3"
DEFAULT_COLOR_BP_RP = 0.82


@dataclass(frozen=True)
class StarField:
    """Catalog stars at one epoch."""

    ra_deg: np.ndarray
    dec_deg: np.ndarray
    mag: np.ndarray  # catalog magnitude: Gaia G, or Hipparcos Hp where `hipparcos`
    v_mag: np.ndarray
    temperature_k: np.ndarray
    hipparcos: np.ndarray
    source_id: np.ndarray  # Gaia DR3 source_id, or the HIP number where `hipparcos`
    epoch_jyear: float

    def __len__(self) -> int:
        return int(self.ra_deg.size)


def v_from_gaia(g_mag: np.ndarray, bp_rp: np.ndarray) -> np.ndarray:
    c = np.clip(np.where(np.isfinite(bp_rp), bp_rp, DEFAULT_COLOR_BP_RP), -0.5, 2.75)
    return g_mag - (-0.02704 + 0.01424 * c - 0.2156 * c * c + 0.01426 * c**3)


def temperature_from_bp_rp(bp_rp: np.ndarray) -> np.ndarray:
    c = np.where(np.isfinite(bp_rp), bp_rp, DEFAULT_COLOR_BP_RP)
    theta = 0.4929 + 0.5092 * c - 0.0353 * c * c
    return np.clip(5040.0 / np.maximum(theta, 0.05), 2500.0, 30000.0)


def temperature_from_b_v(b_v: np.ndarray) -> np.ndarray:
    bv = np.where(np.isfinite(b_v), b_v, 0.65)
    t = 4600.0 * (1.0 / (0.92 * bv + 1.7) + 1.0 / (0.92 * bv + 0.62))
    return np.clip(np.where(np.isfinite(t) & (t > 0), t, 30000.0), 2500.0, 30000.0)


class TileCatalog:
    """Cone queries over a baked release, with decoded tiles kept in memory."""

    def __init__(self, release_dir: Path = DEFAULT_RELEASE_DIR) -> None:
        self.release_dir = Path(release_dir)
        manifest = json.loads((self.release_dir / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("evidenceKind") != "catalog-derived":
            raise ValueError("The star tiles must be catalog-derived.")
        self.epoch_jyear = float(manifest["epochJulianYear"])
        self.layers = [
            (int(layer["layer"]), int(layer["order"]), layer["magMin"], float(layer["magMax"]))
            for layer in manifest["layers"]
        ]
        self._tiles: dict[tuple[int, int], dict[str, np.ndarray] | None] = {}
        self._pixel_centres: dict[int, tuple[np.ndarray, float]] = {}

    def _centres(self, order: int) -> tuple[np.ndarray, float]:
        """Unit vectors of every pixel centre at `order`, and the largest centre-to-corner angle."""
        if order not in self._pixel_centres:
            nside = 1 << order
            pix = np.arange(12 * nside * nside, dtype=np.int64)
            face, ix, iy = ct.nest_to_face_ixiy(order, pix)
            centres = ct.face_xy_to_vec(face, (ix + 0.5) / nside, (iy + 0.5) / nside)
            radius = 0.0
            for dx, dy in ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)):
                corners = ct.face_xy_to_vec(face, (ix + dx) / nside, (iy + dy) / nside)
                cosines = np.clip(np.sum(corners * centres, axis=-1), -1.0, 1.0)
                radius = max(radius, float(np.max(np.arccos(cosines))))
            self._pixel_centres[order] = (centres, radius)
        return self._pixel_centres[order]

    def _tile(self, layer: int, pix: int) -> dict[str, np.ndarray] | None:
        key = (layer, pix)
        if key not in self._tiles:
            core_path = self.release_dir / f"L{layer}" / f"{pix}.core.bin"
            if not core_path.exists():
                self._tiles[key] = None
            else:
                header, cols = ct.decode_core_tile(core_path.read_bytes())
                _, detail = ct.decode_detail_tile((self.release_dir / f"L{layer}" / f"{pix}.detail.bin").read_bytes())
                flags = cols["flags"].astype(np.int64)
                self._tiles[key] = {
                    "vec": ct.decode_core_directions(header, cols),
                    "mag": ct.decode_mag(cols["mag"]),
                    "color": ct.decode_color(cols["color"]),
                    "color_bv": (flags & ct.FLAG_COLOR_B_V) != 0,
                    "hipparcos": (flags & ct.FLAG_CATALOG_HIPPARCOS) != 0,
                    "source_id": (detail["idHi"].astype(np.int64) << 32) | detail["idLo"].astype(np.int64),
                    "pm_ra": ct.decode_f16(detail["pmRa"]),
                    "pm_dec": ct.decode_f16(detail["pmDec"]),
                }
        return self._tiles[key]

    def cone(self, ra_deg: float, dec_deg: float, radius_deg: float, *, mag_limit: float, epoch_jyear: float) -> StarField:
        """Every catalog star brighter than `mag_limit` within `radius_deg` of a direction, at `epoch_jyear`.

        The cone is cut on the catalog-epoch positions; proper motion then moves each star to the
        requested epoch. The bake split layers on the archive magnitudes before rounding them to
        1 mmag, so a layer wholly below the limit is taken whole (some of its stars decode to
        exactly its edge) and only the layer the limit cuts through is filtered.
        """
        centre = ct.radec_to_vec(ra_deg, dec_deg)
        radius = math.radians(radius_deg)
        parts: list[dict[str, np.ndarray]] = []
        for layer, order, mag_min, mag_max in self.layers:
            if mag_min is not None and mag_min >= mag_limit:
                continue
            cut_by_limit = mag_max > mag_limit
            centres, pixel_radius = self._centres(order)
            near = np.flatnonzero(np.arccos(np.clip(centres @ centre, -1.0, 1.0)) <= radius + pixel_radius)
            for pix in near.tolist():
                tile = self._tile(layer, pix)
                if tile is None:
                    continue
                inside = np.arccos(np.clip(tile["vec"] @ centre, -1.0, 1.0)) <= radius
                if cut_by_limit:
                    inside &= tile["mag"] < mag_limit
                if np.any(inside):
                    parts.append({name: values[inside] for name, values in tile.items()})
        if not parts:
            empty = np.zeros(0)
            return StarField(empty, empty, empty, empty, empty, np.zeros(0, dtype=bool), np.zeros(0, dtype=np.int64), epoch_jyear)
        merged = {name: np.concatenate([part[name] for part in parts]) for name in parts[0]}
        ra, dec = vec_to_radec(merged["vec"])
        ra, dec = propagate_proper_motion(ra, dec, merged["pm_ra"], merged["pm_dec"], epoch_jyear - self.epoch_jyear)
        gaia = ~merged["hipparcos"]
        bp_rp = np.where(merged["color_bv"], np.nan, merged["color"])
        v_mag = np.where(gaia, v_from_gaia(merged["mag"], bp_rp), merged["mag"])
        temperature = np.where(
            merged["color_bv"], temperature_from_b_v(merged["color"]), temperature_from_bp_rp(merged["color"])
        )
        return StarField(
            ra_deg=ra,
            dec_deg=dec,
            mag=merged["mag"],
            v_mag=v_mag,
            temperature_k=temperature,
            hipparcos=merged["hipparcos"],
            source_id=merged["source_id"],
            epoch_jyear=epoch_jyear,
        )
