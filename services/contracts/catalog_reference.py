"""Python mirror of `CatalogReferenceSchema` in packages/contracts/src/astronomy.ts.

A catalog reference names the catalog row a finding stands on: its provider and release, the row's id and names,
where the catalog puts it (ICRS, at `coordinateEpoch`), and what the catalog gives about it. Nothing in it is measured
by this app.

Rules beyond each field's own range:
- `apparentMagnitude` is required, except for a deep-sky object (`objectType` 'deep_sky'): OpenNGC leaves many
  objects without one, and a filled-in magnitude would be invented;
- an OpenNGC reference is a deep-sky object.

As the Zod object does, a key the contract does not name is dropped, not refused.
tests/fixtures/catalog_reference_contract.json holds samples both sides of the contract judge alike.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, Strict, StringConstraints, model_validator

CATALOG_PROVIDERS = ("Gaia DR3", "Hipparcos", "JPL Horizons", "SIMBAD", "OpenNGC")
CATALOG_OBJECT_TYPES = ("star", "planet", "moon", "deep_sky", "satellite_candidate")
CATALOG_DISTANCE_METHODS = ("photogeometric_bailer_jones_edr3", "geometric_parallax", "ephemeris_au")
# The one object type whose reference may leave the magnitude out, and the provider that names only that type
MAGNITUDE_OPTIONAL_OBJECT_TYPE = "deep_sky"
DEEP_SKY_ONLY_PROVIDER = "OpenNGC"

NonEmpty = Annotated[str, Strict(), StringConstraints(min_length=1)]
Number = Annotated[float, Strict(), Field(allow_inf_nan=False)]
Positive = Annotated[float, Strict(), Field(gt=0.0, allow_inf_nan=False)]


class CatalogDistance(BaseModel):
    """The catalog's distance interval for the object, in parsecs, and the method that gives it."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    lower: Positive
    median: Positive
    upper: Positive
    method: Literal[CATALOG_DISTANCE_METHODS]


class CatalogReference(BaseModel):
    """One catalog row a finding stands on, under the contract's wire names."""

    # Wire names only (catalogSourceId, apparentMagnitude), as the Zod schema reads them: no populate_by_name
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: NonEmpty
    provider: Literal[CATALOG_PROVIDERS]
    release_version: NonEmpty = Field(alias="releaseVersion")
    catalog_source_id: NonEmpty = Field(alias="catalogSourceId")
    common_name: NonEmpty = Field(alias="commonName")
    bayer_designation: Annotated[str, Strict()] | None = Field(default=None, alias="bayerDesignation")
    constellation: NonEmpty
    object_type: Literal[CATALOG_OBJECT_TYPES] = Field(alias="objectType")
    coordinate_epoch: NonEmpty = Field(alias="coordinateEpoch")
    ra_deg: Annotated[float, Strict(), Field(ge=0.0, le=360.0, allow_inf_nan=False)] = Field(alias="raDeg")
    dec_deg: Annotated[float, Strict(), Field(ge=-90.0, le=90.0, allow_inf_nan=False)] = Field(alias="decDeg")
    apparent_magnitude: Number | None = Field(default=None, alias="apparentMagnitude")
    color_index_bp_rp: Number | None = Field(default=None, alias="colorIndexBpRp")
    spectral_type: Annotated[str, Strict()] | None = Field(default=None, alias="spectralType")
    proper_motion_ra_mas_yr: Number | None = Field(default=None, alias="properMotionRaMasYr")
    proper_motion_dec_mas_yr: Number | None = Field(default=None, alias="properMotionDecMasYr")
    radial_velocity_km_s: Number | None = Field(default=None, alias="radialVelocityKmS")
    parallax_mas: Number | None = Field(default=None, alias="parallaxMas")
    parallax_error_mas: Annotated[float, Strict(), Field(ge=0.0, allow_inf_nan=False)] | None = Field(
        default=None, alias="parallaxErrorMas"
    )
    bailer_jones_distance_pc: CatalogDistance | None = Field(default=None, alias="bailerJonesDistancePc")
    citation: NonEmpty

    @model_validator(mode="before")
    @classmethod
    def _no_explicit_nulls(cls, data: object) -> object:
        # The Zod schema's optional fields take a value or no key: a null is refused, never read as absent. A key the
        # contract does not name is dropped whatever it holds, as Zod drops it
        if isinstance(data, dict):
            wire_names = {field.alias or name for name, field in cls.model_fields.items()}
            nulls = sorted(key for key, value in data.items() if value is None and key in wire_names)
            if nulls:
                raise ValueError(f"An optional field is left out, never null: {nulls}.")
        return data

    @model_validator(mode="after")
    def _rules(self) -> CatalogReference:
        if self.apparent_magnitude is None and self.object_type != MAGNITUDE_OPTIONAL_OBJECT_TYPE:
            raise ValueError("Only a deep-sky object's reference may leave the apparent magnitude out.")
        if self.provider == DEEP_SKY_ONLY_PROVIDER and self.object_type != MAGNITUDE_OPTIONAL_OBJECT_TYPE:
            raise ValueError("An OpenNGC reference is a deep-sky object.")
        return self
