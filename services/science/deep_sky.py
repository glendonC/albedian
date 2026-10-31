"""Deep-sky Messier/NGC catalog (`DEEP_SKY_CATALOG`), Stefan-Boltzmann stellar astrophysics
(`compute_stellar_astrophysics`), and 3D parsec nearest-neighbor topology (`compute_3d_nearest_neighbors`).
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from services.science.catalog import (
    STAR_BY_ID,
    STAR_BY_NAME,
    bailer_jones_cartesian_pc,
    compute_light_travel_time_description,
    get_all_sky_catalog_stars,
    icrs_to_altaz,
)


@dataclass(frozen=True)
class DeepSkyObject:
    """Pinned Messier / NGC deep-sky object record (`predicted` / `catalog_matched` context)."""

    id: str
    catalog_designation: str
    common_name: str
    constellation: str
    ra_deg: float
    dec_deg: float
    mag: float
    distance_pc: float
    angular_size_arcmin: float
    object_subtype: str
    description: str

    def to_contract_dict(
        self,
        obs_utc_iso: str | None = None,
        lat_deg: float | None = None,
        lon_deg: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "catalogDesignation": self.catalog_designation,
            "commonName": self.common_name,
            "constellation": self.constellation,
            "raDeg": float(self.ra_deg),
            "decDeg": float(self.dec_deg),
            "mag": float(self.mag),
            "distancePc": float(self.distance_pc),
            "angularSizeArcmin": float(self.angular_size_arcmin),
            "objectSubtype": self.object_subtype,
            "description": self.description,
            "lightTravel": compute_light_travel_time_description(self.distance_pc),
            "evidenceClass": "predicted",
        }
        if obs_utc_iso is not None and lat_deg is not None and lon_deg is not None:
            alt_deg, az_deg = icrs_to_altaz(
                self.ra_deg, self.dec_deg, lat_deg, lon_deg, obs_utc_iso
            )
            payload["altDeg"] = round(float(alt_deg), 2)
            payload["azDeg"] = round(float(az_deg), 2)
            payload["aboveHorizon"] = bool(alt_deg >= 0.0)
        return payload


DEEP_SKY_CATALOG: tuple[DeepSkyObject, ...] = (
    DeepSkyObject(
        id="dso-ngc7293",
        catalog_designation="NGC 7293 (Caldwell 63)",
        common_name="Helix Nebula",
        constellation="Aquarius",
        ra_deg=337.4108,
        dec_deg=-20.8372,
        mag=7.6,
        distance_pc=201.0,
        angular_size_arcmin=25.0,
        object_subtype="planetary_nebula",
        description="Nearest bright planetary nebula to Earth (~655 ly), ionized by a 120,000 K white dwarf core.",
    ),
    DeepSkyObject(
        id="dso-m2",
        catalog_designation="M2 (NGC 7089)",
        common_name="Messier 2 Globular Cluster",
        constellation="Aquarius",
        ra_deg=323.3626,
        dec_deg=-0.8233,
        mag=6.3,
        distance_pc=16900.0,
        angular_size_arcmin=16.0,
        object_subtype="globular_cluster",
        description="Rich 13-billion-year-old globular cluster containing ~150,000 stars in Aquarius.",
    ),
    DeepSkyObject(
        id="dso-m30",
        catalog_designation="M30 (NGC 7099)",
        common_name="Messier 30 Globular Cluster",
        constellation="Capricornus",
        ra_deg=325.0922,
        dec_deg=-23.1799,
        mag=7.2,
        distance_pc=8300.0,
        angular_size_arcmin=12.0,
        object_subtype="globular_cluster",
        description="Core-collapsed globular cluster in southeastern Capricornus (~27,100 ly).",
    ),
    DeepSkyObject(
        id="dso-m15",
        catalog_designation="M15 (NGC 7078)",
        common_name="Great Pegasus Cluster",
        constellation="Pegasus",
        ra_deg=322.4930,
        dec_deg=12.1670,
        mag=6.2,
        distance_pc=10950.0,
        angular_size_arcmin=18.0,
        object_subtype="globular_cluster",
        description="Ancient globular cluster near Enif hosting the planetary nebula Pease 1.",
    ),
    DeepSkyObject(
        id="dso-m42",
        catalog_designation="M42 (NGC 1976)",
        common_name="Orion Nebula",
        constellation="Orion",
        ra_deg=83.8221,
        dec_deg=-5.3911,
        mag=4.0,
        distance_pc=412.0,
        angular_size_arcmin=65.0,
        object_subtype="emission_nebula",
        description="Closest massive star-forming H II region to the Solar System, illuminated by the Trapezium.",
    ),
    DeepSkyObject(
        id="dso-m45",
        catalog_designation="M45 (Melotte 22)",
        common_name="Pleiades (Seven Sisters)",
        constellation="Taurus",
        ra_deg=56.8712,
        dec_deg=24.1051,
        mag=1.6,
        distance_pc=136.2,
        angular_size_arcmin=110.0,
        object_subtype="open_cluster",
        description="Young B-type open star cluster (~115 Myr old) embedded in a dust reflection nebula.",
    ),
    DeepSkyObject(
        id="dso-m31",
        catalog_designation="M31 (NGC 224)",
        common_name="Andromeda Galaxy",
        constellation="Andromeda",
        ra_deg=10.6847,
        dec_deg=41.2688,
        mag=3.4,
        distance_pc=778000.0,
        angular_size_arcmin=178.0,
        object_subtype="spiral_galaxy",
        description="Nearest major spiral galaxy in the Local Group (~2.54 million light-years).",
    ),
    DeepSkyObject(
        id="dso-m8",
        catalog_designation="M8 (NGC 6523)",
        common_name="Lagoon Nebula",
        constellation="Sagittarius",
        ra_deg=270.9208,
        dec_deg=-24.3867,
        mag=6.0,
        distance_pc=1250.0,
        angular_size_arcmin=90.0,
        object_subtype="emission_nebula",
        description="Giant interstellar H II cloud and young cluster NGC 6530 in Sagittarius.",
    ),
)


def compute_stellar_astrophysics(
    mag: float,
    bp_rp: float,
    distance_pc: float,
    spectral_type: str,
    common_name: str = "",
) -> dict[str, Any]:
    """Derive stellar effective temperature `teffK`, absolute magnitude `absoluteMagV`,
    bolometric luminosity `luminositySolar` (`L/L_sun`), Stefan-Boltzmann radius
    `radiusSolar` (`R/R_sun`), `evolutionaryStage`, and `systemNotes`.
    """
    safe_dist = max(0.5, float(distance_pc))
    c_idx = max(-0.45, min(2.8, float(bp_rp)))

    # Empirical Gaia BP-RP to T_eff calibration (calibrated across OBAFGKM spectral sequence)
    teff_k = 4600.0 * (
        1.0 / (0.64 * c_idx + 1.70) + 1.0 / (0.64 * c_idx + 0.62)
    )
    teff_k = max(2800.0, min(33000.0, teff_k))

    # Distance modulus M_V = m_V - 5 * log10(d_pc / 10)
    abs_mag_v = float(mag) - 5.0 * math.log10(safe_dist / 10.0)

    # Approximate bolometric correction BC_V for hot O/B and cool K/M stars
    if teff_k > 11000.0:
        bc_v = -2.0 * math.log10(teff_k / 10000.0) - 0.3
    elif teff_k < 4500.0:
        bc_v = -2.5 * math.log10(4500.0 / teff_k) - 0.2
    else:
        bc_v = -0.08

    m_bol = abs_mag_v + bc_v
    lum_solar = 10.0 ** (0.4 * (4.74 - m_bol))
    # Stefan-Boltzmann law: R / R_sun = sqrt(L / L_sun) * (T_sun / T_eff)^2
    radius_solar = math.sqrt(max(1e-5, lum_solar)) * ((5772.0 / teff_k) ** 2)

    spec_u = spectral_type.upper()
    if "IA" in spec_u or "IB" in spec_u:
        stage = "Supergiant (Core Helium / Heavy-Element Burning)"
    elif "II" in spec_u or "III" in spec_u:
        stage = "Evolved Giant / Bright Giant Branch"
    elif "IV" in spec_u:
        stage = "Subgiant (Hydrogen-Shell Burning)"
    else:
        stage = "Main Sequence (Core Hydrogen Fusion)"

    name_l = common_name.lower()
    if "fomalhaut" in name_l:
        notes = "Young (~440 Myr) A3V star surrounded by a resolved debris disk and dust belt."
    elif "deneb algedi" in name_l:
        notes = "Algol-type eclipsing spectroscopic binary (4-star hierarchical system) with 1.02-day orbital period."
    elif "betelgeuse" in name_l:
        notes = "Semi-regular pulsating red supergiant nearing core-collapse supernova within ~100,000 years."
    else:
        notes = f"Spectral class {spectral_type}; surface temperature ~{int(round(teff_k)):,} K."

    return {
        "teffK": int(round(teff_k)),
        "absoluteMagV": round(abs_mag_v, 2),
        "bolometricMag": round(m_bol, 2),
        "luminositySolar": round(lum_solar, 2),
        "radiusSolar": round(radius_solar, 2),
        "evolutionaryStage": stage,
        "systemNotes": notes,
    }


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

    tx, ty, tz = bailer_jones_cartesian_pc(target)
    neighbors: list[tuple[float, Any]] = []
    for star in get_all_sky_catalog_stars():
        if star.id == target.id:
            continue
        sx, sy, sz = bailer_jones_cartesian_pc(star)
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
