"""Tangent-plane astrometry helpers and Gaia DR3 + Hipparcos 2007 bright-star merge rules."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from services.science.catalog_tile_constants import MAS_TO_RAD, SAME_STAR_RADIUS_ARCSEC
from services.science.catalog_tile_healpix import vec_to_radec


def propagate_linear(ra_deg, dec_deg, pm_ra_mas_yr, pm_dec_mas_yr, years: float) -> tuple[np.ndarray, np.ndarray]:
    """Move positions along the tangent plane by proper motion (pmra includes cos dec).

    Perspective acceleration is ignored; over the 24.75 years between the Hipparcos and Gaia
    DR3 epochs its effect stays far below the tile position quantization.
    """
    ra = np.radians(np.asarray(ra_deg, dtype=np.float64))
    dec = np.radians(np.asarray(dec_deg, dtype=np.float64))
    pmra = np.nan_to_num(np.asarray(pm_ra_mas_yr, dtype=np.float64)) * MAS_TO_RAD * years
    pmdec = np.nan_to_num(np.asarray(pm_dec_mas_yr, dtype=np.float64)) * MAS_TO_RAD * years
    r = np.stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)], axis=-1)
    p = np.stack([-np.sin(ra), np.cos(ra), np.zeros_like(ra)], axis=-1)
    q = np.stack([-np.sin(dec) * np.cos(ra), -np.sin(dec) * np.sin(ra), np.cos(dec)], axis=-1)
    moved = r + p * pmra[..., None] + q * pmdec[..., None]
    moved /= np.linalg.norm(moved, axis=-1, keepdims=True)
    return vec_to_radec(moved)


def angular_separation_arcsec(vec_a: np.ndarray, vec_b: np.ndarray) -> np.ndarray:
    cross = np.linalg.norm(np.cross(vec_a, vec_b), axis=-1)
    dot = np.sum(vec_a * vec_b, axis=-1)
    return np.degrees(np.arctan2(cross, dot)) * 3600.0


@dataclass(frozen=True)
class BrightMergeDecision:
    """Outcome for one Hipparcos candidate or one dropped Gaia source, kept for the manifest."""

    action: str  # "use_hipparcos" | "keep_gaia" | "drop_gaia"
    hip: int | None
    gaia_source_id: int | None
    reason: str


def merge_bright_stars(
    *,
    gaia_source_id: np.ndarray,
    gaia_vec: np.ndarray,
    gaia_mag: np.ndarray,
    hip: np.ndarray,
    hip_vec_gaia_epoch: np.ndarray,
    hip_mag: np.ndarray,
    hip_best_neighbour: np.ndarray,
    hip_best_neighbour_mag: np.ndarray,
    gaia_poor_brighter_than: float,
    gaia_mag_limit: float,
    duplicate_radius_arcsec: float,
    duplicate_mag_limit: float,
) -> tuple[np.ndarray, np.ndarray, list[BrightMergeDecision]]:
    """Decide which bright stars come from Hipparcos and which Gaia sources they replace.

    Gaia handles a Hipparcos star when its Gaia DR3 best neighbour has a G magnitude between
    `gaia_poor_brighter_than` and the tile limit `gaia_mag_limit`, unless a saturated Gaia
    source within the duplicate radius matches the Hipparcos magnitude better: then the
    crossmatch paired the star with a fainter companion (beta Leporis is one such case) and
    the star is treated as poorly handled. Otherwise (no best neighbour, no G, saturated, or
    outside the subset) the Hipparcos star is used. When a Hipparcos star without a best
    neighbour has an unsaturated Gaia source within the duplicate radius whose magnitude
    agrees within 1 mag, or one within `SAME_STAR_RADIUS_ARCSEC` that is not more than 1 mag
    fainter (very red stars read brighter in G than in Hp), the crossmatch simply missed it,
    so Gaia is kept instead.

    Every used Hipparcos star removes Gaia sources (searched down to `duplicate_mag_limit`,
    best neighbour included) within `SAME_STAR_RADIUS_ARCSEC`, or within the duplicate radius
    that agree with it within 1.5 mag or sit in the saturated regime: duplicates and
    bright-star artefacts. Fainter genuine companions stay. Gaia sources brighter than the
    poor-handling limit that remain are dropped too: every star that bright is a Hipparcos
    star, so a leftover is not a separate star.

    Returns (use_hip_mask over Hipparcos rows, drop_gaia_mask over Gaia rows, decisions).
    """
    n_gaia = len(gaia_source_id)
    use_hip = np.zeros(len(hip), dtype=bool)
    drop_gaia = np.zeros(n_gaia, dtype=bool)
    decisions: list[BrightMergeDecision] = []
    index_of_source = {int(s): i for i, s in enumerate(gaia_source_id)}

    bright_gaia = np.flatnonzero(gaia_mag < duplicate_mag_limit)
    bright_vec = gaia_vec[bright_gaia]
    cos_radius = math.cos(math.radians(duplicate_radius_arcsec / 3600.0))

    def describe(i: int, k: int) -> str:
        sep = float(angular_separation_arcsec(gaia_vec[i], hip_vec_gaia_epoch[k]))
        return f"G {gaia_mag[i]:.3f} vs Hp {hip_mag[k]:.3f}, separation {sep:.2f} arcsec"

    def same_position(i: int, k: int) -> bool:
        return float(angular_separation_arcsec(gaia_vec[i], hip_vec_gaia_epoch[k])) < SAME_STAR_RADIUS_ARCSEC

    def duplicates_hip(i: int, k: int) -> bool:
        # Position settles it within 2 arcsec: very red stars and Miras read 2+ mag brighter in G than Hp
        return (
            same_position(i, k)
            or abs(gaia_mag[i] - hip_mag[k]) < 1.5
            or gaia_mag[i] < gaia_poor_brighter_than
        )

    for k in range(len(hip)):
        bn = int(hip_best_neighbour[k]) if hip_best_neighbour[k] > 0 else None
        bn_mag = float(hip_best_neighbour_mag[k])
        near = bright_gaia[bright_vec @ hip_vec_gaia_epoch[k] >= cos_radius] if len(bright_gaia) else bright_gaia
        reason = ""
        if bn is not None and math.isfinite(bn_mag) and gaia_poor_brighter_than <= bn_mag < gaia_mag_limit:
            bn_offset = abs(bn_mag - hip_mag[k])
            saturated = [
                int(i)
                for i in near
                if gaia_mag[i] < gaia_poor_brighter_than and abs(gaia_mag[i] - hip_mag[k]) < min(1.5, bn_offset)
            ]
            if not saturated:
                continue  # Gaia handles this star; nothing to log
            reason = (
                f"Gaia best neighbour G {bn_mag:.3f} is a fainter companion; saturated counterpart "
                + describe(saturated[0], k)
            )
        if bn is None:
            agreeing = [
                int(i)
                for i in near
                if gaia_mag[i] >= gaia_poor_brighter_than
                and (
                    abs(gaia_mag[i] - hip_mag[k]) < 1.0
                    or (same_position(int(i), k) and gaia_mag[i] < hip_mag[k] + 1.0)
                )
            ]
            if agreeing:
                decisions.append(
                    BrightMergeDecision(
                        "keep_gaia",
                        int(hip[k]),
                        int(gaia_source_id[agreeing[0]]),
                        "no Gaia best neighbour, but a Gaia source agrees: " + describe(agreeing[0], k),
                    )
                )
                continue
        use_hip[k] = True
        if not reason:
            if bn is None:
                reason = "no Gaia DR3 best neighbour"
            elif not math.isfinite(bn_mag):
                reason = "Gaia best neighbour has no G magnitude"
            elif bn_mag < gaia_poor_brighter_than:
                reason = f"Gaia best neighbour brighter than G {gaia_poor_brighter_than:g} (G {bn_mag:.3f})"
            else:
                reason = f"Gaia best neighbour outside the G < {gaia_mag_limit:g} subset (G {bn_mag:.3f})"
        decisions.append(BrightMergeDecision("use_hipparcos", int(hip[k]), bn, reason))
        replaced = {int(i) for i in near if duplicates_hip(int(i), k)}
        if bn is not None and bn in index_of_source and duplicates_hip(index_of_source[bn], k):
            replaced.add(index_of_source[bn])
        for i in sorted(replaced):
            if not drop_gaia[i]:
                drop_gaia[i] = True
                decisions.append(
                    BrightMergeDecision(
                        "drop_gaia",
                        int(hip[k]),
                        int(gaia_source_id[i]),
                        ("replaced by Hipparcos entry: " if int(gaia_source_id[i]) == bn else "duplicate of Hipparcos entry: ")
                        + describe(i, k),
                    )
                )

    leftovers = np.flatnonzero((gaia_mag < gaia_poor_brighter_than) & ~drop_gaia)
    for i in leftovers:
        drop_gaia[i] = True
        decisions.append(
            BrightMergeDecision(
                "drop_gaia",
                None,
                int(gaia_source_id[i]),
                f"G {gaia_mag[i]:.3f} is brighter than G {gaia_poor_brighter_than:g} and no Hipparcos "
                f"star within {duplicate_radius_arcsec:g} arcsec claimed it",
            )
        )
    return use_hip, drop_gaia, decisions


__all__ = [
    "BrightMergeDecision",
    "angular_separation_arcsec",
    "merge_bright_stars",
    "propagate_linear",
]
