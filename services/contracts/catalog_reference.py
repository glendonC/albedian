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

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    Strict,
    StringConstraints,
    ValidationError,
    ValidatorFunctionWrapHandler,
    model_validator,
)
from pydantic_core import InitErrorDetails, PydanticCustomError

from services.contracts.designation import BAYER_DESIGNATION_PATTERN, FLAMSTEED_NUMBER_MAX, FLAMSTEED_NUMBER_MIN
from services.contracts.wire import wire_int

CATALOG_PROVIDERS = ("Gaia DR3", "Hipparcos", "JPL Horizons", "SIMBAD", "OpenNGC")
CATALOG_OBJECT_TYPES = ("star", "planet", "moon", "deep_sky", "satellite_candidate")
CATALOG_DISTANCE_METHODS = (
    "photogeometric_bailer_jones_edr3",
    "geometric_bailer_jones_edr3",
    "geometric_parallax",
    "ephemeris_au",
)
# The one object type whose reference may leave the magnitude out, and the provider that names only that type
MAGNITUDE_OPTIONAL_OBJECT_TYPE = "deep_sky"
DEEP_SKY_ONLY_PROVIDER = "OpenNGC"
LEFT_OUT_NEVER_NULL = "No field is null: one with no value is left out."
ONLY_DEEP_SKY_WITHOUT_MAGNITUDE = "Only a deep-sky object's reference may leave the apparent magnitude out."
OPENNGC_IS_DEEP_SKY = "An OpenNGC reference is a deep-sky object."

NonEmpty = Annotated[str, Strict(), StringConstraints(min_length=1)]
Number = Annotated[float, Strict(), Field(allow_inf_nan=False)]
Positive = Annotated[float, Strict(), Field(gt=0.0, allow_inf_nan=False)]


def _refusal(title: str, issues: list[tuple[tuple[str | int, ...], str]]) -> ValidationError:
    """The issues as one refusal at their own wire paths, keeping no input value."""
    return ValidationError.from_exception_data(
        title,
        [InitErrorDetails(type=PydanticCustomError("contract_rule", message), loc=path, input=None) for path, message in issues],
        hide_input=True,
    )


class CatalogDistance(BaseModel):
    """The catalog's distance interval for the object, in parsecs, and the method that gives it."""

    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    lower: Positive
    median: Positive
    upper: Positive
    method: Literal[CATALOG_DISTANCE_METHODS]


class CatalogReference(BaseModel):
    """One catalog row a finding stands on, under the contract's wire names."""

    # Wire names only (catalogSourceId, apparentMagnitude), as the Zod schema reads them: no populate_by_name
    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    id: NonEmpty
    provider: Literal[CATALOG_PROVIDERS]
    release_version: NonEmpty = Field(alias="releaseVersion")
    catalog_source_id: NonEmpty = Field(alias="catalogSourceId")
    common_name: NonEmpty = Field(alias="commonName")
    # The long form the repository ships ("Alpha Orionis"), only from the designation tables (designation.py)
    bayer_designation: Annotated[str, Strict(), StringConstraints(pattern=BAYER_DESIGNATION_PATTERN)] | None = Field(
        default=None, alias="bayerDesignation"
    )
    # A whole number within the range the baked designations hold
    flamsteed_number: Annotated[
        int, BeforeValidator(wire_int), Strict(), Field(ge=FLAMSTEED_NUMBER_MIN, le=FLAMSTEED_NUMBER_MAX)
    ] | None = Field(default=None, alias="flamsteedNumber")
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

    @model_validator(mode="wrap")
    @classmethod
    def _wire_object(cls, data: object, handler: ValidatorFunctionWrapHandler) -> CatalogReference:
        # The Zod schema's optional fields take a value or no key: a null is refused at its own wire path, never read
        # as absent, alongside any other field faults. A key the contract does not name is dropped whatever it holds.
        if not isinstance(data, dict):
            return handler(data)
        optional = {str(field.alias) for field in cls.model_fields.values() if not field.is_required()}
        nulls: list[tuple[tuple[str | int, ...], str]] = [
            ((key,), LEFT_OUT_NEVER_NULL) for key, value in data.items() if value is None and key in optional
        ]
        if not nulls:
            built = handler(data)
            issues: list[tuple[tuple[str | int, ...], str]] = []
            if built.apparent_magnitude is None and built.object_type != MAGNITUDE_OPTIONAL_OBJECT_TYPE:
                issues.append((("apparentMagnitude",), ONLY_DEEP_SKY_WITHOUT_MAGNITUDE))
            if built.provider == DEEP_SKY_ONLY_PROVIDER and built.object_type != MAGNITUDE_OPTIONAL_OBJECT_TYPE:
                issues.append((("objectType",), OPENNGC_IS_DEEP_SKY))
            if issues:
                raise _refusal(cls.__name__, issues)
            return built
        try:
            handler(data)
        except ValidationError as exc:
            prior = [(err["loc"], err["msg"]) for err in exc.errors()]
            raise _refusal(cls.__name__, [*prior, *nulls]) from None
        raise _refusal(cls.__name__, nulls)
