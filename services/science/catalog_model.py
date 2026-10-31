"""Data model and factory helper for pinned Gaia DR3 & Hipparcos catalog stars."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogStar:
    """Pinned astrometric & photogeometric distance record for a real catalog star."""

    id: str
    common_name: str
    bayer_designation: str
    constellation: str
    provider: str
    release_version: str
    catalog_source_id: str
    ra_deg: float
    dec_deg: float
    mag: float
    bp_rp: float
    spectral_type: str
    pm_ra_mas_yr: float
    pm_dec_mas_yr: float
    rv_km_s: float
    parallax_mas: float
    parallax_error_mas: float
    lower_pc: float
    median_pc: float
    upper_pc: float
    distance_method: str = "photogeometric_bailer_jones_edr3"
    epoch_year: float = 2000.0
    citation: str = (
        "Gaia Collaboration (2023) Gaia DR3; Hipparcos (ESA 1997); "
        "Bailer-Jones et al. (2021) AJ 161, 147."
    )

    def to_catalog_reference_dict(self) -> dict:
        """Convert to a dictionary matching @albedian/contracts CatalogReferenceSchema."""
        return {
            "id": self.id,
            "provider": self.provider,
            "releaseVersion": self.release_version,
            "catalogSourceId": self.catalog_source_id,
            "commonName": self.common_name,
            "bayerDesignation": self.bayer_designation,
            "constellation": self.constellation,
            "objectType": "star",
            "coordinateEpoch": f"J{self.epoch_year:.1f}",
            "raDeg": float(self.ra_deg % 360.0),
            "decDeg": float(self.dec_deg),
            "apparentMagnitude": float(self.mag),
            "colorIndexBpRp": float(self.bp_rp),
            "spectralType": self.spectral_type,
            "properMotionRaMasYr": float(self.pm_ra_mas_yr),
            "properMotionDecMasYr": float(self.pm_dec_mas_yr),
            "radialVelocityKmS": float(self.rv_km_s),
            "parallaxMas": float(self.parallax_mas),
            "parallaxErrorMas": float(self.parallax_error_mas),
            "bailerJonesDistancePc": {
                "lower": float(self.lower_pc),
                "median": float(self.median_pc),
                "upper": float(self.upper_pc),
                "method": self.distance_method,
            },
            "citation": self.citation,
        }


def make_star(
    star_id: str,
    common_name: str,
    bayer_designation: str,
    constellation: str,
    catalog_source_id: str,
    ra_deg: float,
    dec_deg: float,
    mag: float,
    bp_rp: float,
    spectral_type: str,
    pm_ra_mas_yr: float,
    pm_dec_mas_yr: float,
    rv_km_s: float,
    parallax_mas: float,
    parallax_error_mas: float,
    lower_pc: float,
    median_pc: float,
    upper_pc: float,
    provider: str = "Hipparcos",
    release_version: str = "HIP2 / Gaia DR3 Crossmatch",
    distance_method: str = "photogeometric_bailer_jones_edr3",
) -> CatalogStar:
    """Concise factory for constructing a `CatalogStar` instance."""
    return CatalogStar(
        id=star_id,
        common_name=common_name,
        bayer_designation=bayer_designation,
        constellation=constellation,
        provider=provider,
        release_version=release_version,
        catalog_source_id=catalog_source_id,
        ra_deg=ra_deg,
        dec_deg=dec_deg,
        mag=mag,
        bp_rp=bp_rp,
        spectral_type=spectral_type,
        pm_ra_mas_yr=pm_ra_mas_yr,
        pm_dec_mas_yr=pm_dec_mas_yr,
        rv_km_s=rv_km_s,
        parallax_mas=parallax_mas,
        parallax_error_mas=parallax_error_mas,
        lower_pc=lower_pc,
        median_pc=median_pc,
        upper_pc=upper_pc,
        distance_method=distance_method,
    )
