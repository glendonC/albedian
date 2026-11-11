"""Data model and factories for the pinned bright catalog stars.

Each pinned star names its row in the G 12 star tiles (apps/web/public/catalog/stars/gaia-dr3/): a Hipparcos
2007 entry, whose distance is 1000/parallax with bounds from the parallax error, or a Gaia DR3 source, whose
distance is the Bailer-Jones et al. (2021) photogeometric estimate. The factories derive the catalog id, the
distance, its method, the BP-RP colour index and radial velocity (where measured in Gaia DR3) and the citation
from that source, so a star cannot carry a label or field its catalog row does not have.
tests/integration/test_pinned_catalog_matches_tiles.py checks every star against its tile row.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.contracts.catalog_reference import CATALOG_DISTANCE_METHODS

HIPPARCOS_2007_RELEASE = "Hipparcos 2007"
HIPPARCOS_2007_CITATION = (
    "Catalog id, parallax and distance (1000/parallax): Hipparcos 2007, van Leeuwen (2007) A&A 474, 653."
)
GAIA_DR3_RELEASE = "Gaia DR3"
# The band a tile row's magnitude is in (the tile flag FLAG_MAG_BAND_HP, services/science/catalog_tiles.py)
HIPPARCOS_HP = "Hipparcos Hp"
GAIA_G = "Gaia G"
# Distance methods, as the contract names them
INVERSE_PARALLAX = "geometric_parallax"
BAILER_JONES_PHOTOGEOMETRIC = "photogeometric_bailer_jones_edr3"
# The Bailer-Jones et al. (2021) geometric distance, which the tiles carry for a Gaia DR3 source with no
# photogeometric one (docs/science/CATALOG_STAR_TILES.md). The catalog reference contract has no name for it, so a
# reference leaves such a distance out; the finding's own distance block names it [open: a contract method for it]
BAILER_JONES_GEOMETRIC = "geometric_bailer_jones_edr3"


def hipparcos_2007_tile_citation(*, magnitude_band: str) -> str:
    """The citation for a Hipparcos 2007 tile row: the tiles carry its distance, not its parallax."""
    return f"Catalog id, {magnitude_band} magnitude and distance: Hipparcos 2007, van Leeuwen (2007) A&A 474, 653."


def gaia_dr3_citation(
    *, color: bool, radial_velocity: bool, distance_method: str, magnitude_band: str | None = None
) -> str:
    """The citation for a Gaia DR3 star, naming only the values its row carries."""
    values = ["Catalog id"]
    if magnitude_band is not None:
        values.append(f"{magnitude_band} magnitude")
    if color:
        values.append("color index (BP-RP)")
    if radial_velocity:
        values.append("radial velocity")
    named = values[0] if len(values) == 1 else ", ".join(values[:-1]) + " and " + values[-1]
    kind = {BAILER_JONES_PHOTOGEOMETRIC: "photogeometric", BAILER_JONES_GEOMETRIC: "geometric"}[distance_method]
    return (
        f"{named}: Gaia DR3, Gaia Collaboration (2023) A&A 674, A1. Distance: Bailer-Jones et al. (2021) AJ 161, "
        f"147, {kind}."
    )


GAIA_DR3_CITATION = gaia_dr3_citation(color=True, radial_velocity=False, distance_method=BAILER_JONES_PHOTOGEOMETRIC)
GAIA_DR3_WITH_RV_CITATION = gaia_dr3_citation(
    color=True, radial_velocity=True, distance_method=BAILER_JONES_PHOTOGEOMETRIC
)


@dataclass(frozen=True)
class CatalogStar:
    """A bright catalog star: its catalog row, J2000 astrometry, and a distance whose method says what it is.

    `parallax_mas` and `parallax_error_mas` are set only when the distance comes from them (Hipparcos 2007
    rows); a Gaia DR3 star's parallax is not in the tiles, so it stays missing. `bp_rp` and `rv_km_s` are set
    only when the star's own Gaia DR3 row carries them (Hipparcos 2007 holds Johnson B-V, not Gaia BP-RP, and
    no radial velocities), and `spectral_type` is omitted because neither catalog release carries it.
    `pm_ra_mas_yr` and `pm_dec_mas_yr` are None where the row has no proper motion, and `bayer_designation`
    is None for a star with no designation beyond its catalog id. A value the row lacks is never filled in.
    `magnitude_band` names the band of `mag` when a tile row gives it (Hipparcos Hp or Gaia G); None for a
    pinned record, whose magnitudes were typed by hand.
    """

    id: str
    common_name: str
    bayer_designation: str | None
    constellation: str
    provider: str
    release_version: str
    catalog_source_id: str
    ra_deg: float
    dec_deg: float
    mag: float
    bp_rp: float | None
    spectral_type: str | None
    pm_ra_mas_yr: float | None
    pm_dec_mas_yr: float | None
    rv_km_s: float | None
    parallax_mas: float | None
    parallax_error_mas: float | None
    lower_pc: float
    median_pc: float
    upper_pc: float
    distance_method: str
    citation: str
    epoch_year: float = 2000.0
    magnitude_band: str | None = None

    @property
    def distance_source(self) -> str:
        """Where the distance and its interval come from, in words."""
        if self.distance_method == INVERSE_PARALLAX:
            return "1000/parallax from Hipparcos 2007, with the interval from the parallax error"
        if self.distance_method == BAILER_JONES_PHOTOGEOMETRIC:
            return "the Bailer-Jones et al. (2021) photogeometric estimate from Gaia DR3, with its 16th to 84th percentiles"
        if self.distance_method == BAILER_JONES_GEOMETRIC:
            return "the Bailer-Jones et al. (2021) geometric estimate from Gaia DR3, with its 16th to 84th percentiles"
        raise ValueError(f"{self.common_name} has no known distance method: {self.distance_method!r}")

    def to_catalog_reference_dict(self) -> dict:
        """Convert to a dictionary matching @albedian/contracts CatalogReferenceSchema."""
        payload: dict = {
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
            "properMotionRaMasYr": None if self.pm_ra_mas_yr is None else float(self.pm_ra_mas_yr),
            "properMotionDecMasYr": None if self.pm_dec_mas_yr is None else float(self.pm_dec_mas_yr),
            "bailerJonesDistancePc": (
                {
                    "lower": float(self.lower_pc),
                    "median": float(self.median_pc),
                    "upper": float(self.upper_pc),
                    "method": self.distance_method,
                }
                # A method the contract cannot name (a Bailer-Jones geometric distance) is left out, not relabelled
                if self.distance_method in CATALOG_DISTANCE_METHODS
                else None
            ),
            "citation": self.citation,
        }
        # A value the catalog row lacks is left out, never sent as null or filled in
        payload = {key: value for key, value in payload.items() if value is not None}
        if self.bp_rp is not None:
            payload["colorIndexBpRp"] = float(self.bp_rp)
        if self.spectral_type is not None:
            payload["spectralType"] = self.spectral_type
        if self.rv_km_s is not None:
            payload["radialVelocityKmS"] = float(self.rv_km_s)
        # The contract's parallax fields are optional: left out when the catalog row has none
        if self.parallax_mas is not None and self.parallax_error_mas is not None:
            payload["parallaxMas"] = float(self.parallax_mas)
            payload["parallaxErrorMas"] = float(self.parallax_error_mas)
        return payload


def hipparcos_star(
    star_id: str,
    common_name: str,
    bayer_designation: str,
    constellation: str,
    hip: int,
    ra_deg: float,
    dec_deg: float,
    mag: float,
    pm_ra_mas_yr: float,
    pm_dec_mas_yr: float,
    parallax_mas: float,
    parallax_error_mas: float,
) -> CatalogStar:
    """A star whose tile row is a Hipparcos 2007 entry: distance 1000/parallax, bounds 1000/(parallax -+ error)."""
    if not 0.0 < parallax_error_mas < parallax_mas:
        raise ValueError(f"{common_name}: the parallax must exceed its error for an inverse-parallax interval")
    return CatalogStar(
        id=star_id,
        common_name=common_name,
        bayer_designation=bayer_designation,
        constellation=constellation,
        provider="Hipparcos",
        release_version=HIPPARCOS_2007_RELEASE,
        catalog_source_id=f"HIP {hip}",
        ra_deg=ra_deg,
        dec_deg=dec_deg,
        mag=mag,
        bp_rp=None,
        spectral_type=None,
        pm_ra_mas_yr=pm_ra_mas_yr,
        pm_dec_mas_yr=pm_dec_mas_yr,
        rv_km_s=None,
        parallax_mas=parallax_mas,
        parallax_error_mas=parallax_error_mas,
        lower_pc=1000.0 / (parallax_mas + parallax_error_mas),
        median_pc=1000.0 / parallax_mas,
        upper_pc=1000.0 / (parallax_mas - parallax_error_mas),
        distance_method=INVERSE_PARALLAX,
        citation=HIPPARCOS_2007_CITATION,
    )


def gaia_dr3_star(
    star_id: str,
    common_name: str,
    bayer_designation: str,
    constellation: str,
    source_id: int,
    ra_deg: float,
    dec_deg: float,
    mag: float,
    bp_rp: float,
    pm_ra_mas_yr: float,
    pm_dec_mas_yr: float,
    rv_km_s: float | None,
    lower_pc: float,
    median_pc: float,
    upper_pc: float,
) -> CatalogStar:
    """A star whose tile row is a Gaia DR3 source: the Bailer-Jones (2021) photogeometric distance and its
    16th and 84th percentiles (r_lo_photogeo, r_med_photogeo, r_hi_photogeo), with its DR3 BP-RP colour
    index and (where DR3 measures one) radial velocity."""
    return CatalogStar(
        id=star_id,
        common_name=common_name,
        bayer_designation=bayer_designation,
        constellation=constellation,
        provider="Gaia DR3",
        release_version=GAIA_DR3_RELEASE,
        catalog_source_id=f"Gaia DR3 {source_id}",
        ra_deg=ra_deg,
        dec_deg=dec_deg,
        mag=mag,
        bp_rp=bp_rp,
        spectral_type=None,
        pm_ra_mas_yr=pm_ra_mas_yr,
        pm_dec_mas_yr=pm_dec_mas_yr,
        rv_km_s=rv_km_s,
        parallax_mas=None,
        parallax_error_mas=None,
        lower_pc=lower_pc,
        median_pc=median_pc,
        upper_pc=upper_pc,
        distance_method=BAILER_JONES_PHOTOGEOMETRIC,
        citation=GAIA_DR3_WITH_RV_CITATION if rv_km_s is not None else GAIA_DR3_CITATION,
    )
