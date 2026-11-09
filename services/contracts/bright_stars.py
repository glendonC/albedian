"""Python mirror of the bright-star 3D supplement contract in packages/contracts/src/bright-stars.ts (data-11a).

The stars the G 12 star tiles take from Hipparcos rather than Gaia DR3 (the tile bake's merge: no Gaia DR3 best
neighbour, a saturated one brighter than G 3, one outside the G < 12 subset, or one that is a fainter companion), with
full-precision astrometry for a 3D flight: Hipparcos, the New Reduction (van Leeuwen 2007), joined with V and
multiplicity from the Hipparcos Catalogue (ESA 1997), both from the ESA Gaia archive (CC BY-NC 3.0 IGO, credit ESA).
Catalogue values, none measured by this app, so the evidence kind is 'catalog-derived'.

A supplement is {format, formatVersion, evidenceKind, catalogue, frame, epochJulianYear, tilesRelease, columns, rows}:
- frame 'ICRS' and epochJulianYear 1991.25: positions are Hipparcos's own, at its epoch, not moved; a reader
  propagates them with the proper motion to the epoch it draws (the tiles hold them moved to J2016.0);
- rows: tuples in the order `columns` names: HIP number; IAU name or null; right ascension and declination in degrees
  and their standard errors in mas (RA's as RA*cos(Dec)); parallax and its standard error in mas; proper motion in
  RA*cos(Dec) and in Dec, and their standard errors, in mas/yr; Hp magnitude; V magnitude (1997) or null; B-V or
  null; the 2007 solution type and number of components; the Gaia DR3 best neighbour or null; why Hipparcos wins;
  and the star's row in the tiles: layer, HEALPix nested pixel and row index in that tile's core block, so a reader
  can move exactly these stars without drawing them twice.

The parallax rule (no distance is baked): a positive parallax p mas gives a distance of 1000 / p parsecs; the 1 sigma
range 1000 / (p + e) to 1000 / (p - e) holds only where p - e > 0, and has no upper end otherwise; a parallax at or
below zero gives no distance. The inverse is a biased estimator where e / p is large; a reader that needs a distance
there must choose and disclose its own estimator, as the data do not settle one.

Rules beyond each value's own range: HIP numbers are unique; tile locations are unique; a parallax error is positive.
tests/fixtures/bright_stars_contract.json holds samples both sides of the contract judge alike.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, Strict, StringConstraints, model_validator

from services.contracts.provenance import SHORT_TEXT_MAX_LENGTH, TEXT_PATTERN

BRIGHT_STAR_SUPPLEMENT_FORMAT = "albedian.bright-star-supplement"
BRIGHT_STAR_SUPPLEMENT_FORMAT_VERSION = 1
BRIGHT_STAR_EVIDENCE_KIND = "catalog-derived"
BRIGHT_STAR_FRAME = "ICRS"
BRIGHT_STAR_EPOCH_JULIAN_YEAR = 1991.25

BRIGHT_STAR_COLUMNS = (
    "hip",
    "name",
    "raDeg",
    "decDeg",
    "raErrorMas",
    "decErrorMas",
    "parallaxMas",
    "parallaxErrorMas",
    "pmRaCosDecMasPerYr",
    "pmDecMasPerYr",
    "pmRaErrorMasPerYr",
    "pmDecErrorMasPerYr",
    "hpMag",
    "vMag",
    "bMinusV",
    "solutionType",
    "components",
    "gaiaSourceId",
    "hipparcosReason",
    "tileLayer",
    "tilePixel",
    "tileIndex",
)

GAIA_SOURCE_ID_PATTERN = r"^[1-9][0-9]{0,19}$"

ShortText = Annotated[str, Strict(), StringConstraints(max_length=SHORT_TEXT_MAX_LENGTH, pattern=TEXT_PATTERN)]
GaiaSourceId = Annotated[str, Strict(), StringConstraints(pattern=GAIA_SOURCE_ID_PATTERN)]


def _finite(**bounds: float) -> Any:
    return Annotated[float, Strict(), Field(allow_inf_nan=False, **bounds)]


def _int(**bounds: int) -> Any:
    return Annotated[int, Strict(), Field(**bounds)]


BrightStarRow = tuple[
    _int(ge=1),  # hip
    ShortText | None,  # name
    _finite(ge=0, lt=360),  # raDeg
    _finite(ge=-90, le=90),  # decDeg
    _finite(ge=0),  # raErrorMas
    _finite(ge=0),  # decErrorMas
    _finite(),  # parallaxMas (may be zero or negative)
    _finite(gt=0),  # parallaxErrorMas
    _finite(),  # pmRaCosDecMasPerYr
    _finite(),  # pmDecMasPerYr
    _finite(ge=0),  # pmRaErrorMasPerYr
    _finite(ge=0),  # pmDecErrorMasPerYr
    _finite(),  # hpMag
    _finite() | None,  # vMag
    _finite() | None,  # bMinusV
    _int(ge=0),  # solutionType
    _int(ge=1),  # components
    GaiaSourceId | None,  # gaiaSourceId
    ShortText,  # hipparcosReason
    _int(ge=0),  # tileLayer
    _int(ge=0),  # tilePixel
    _int(ge=0),  # tileIndex
]


class BrightStarSupplement(BaseModel):
    """apps/web/public/catalog/stars/hipparcos-bright-3d/stars.json."""

    # Wire names only (formatVersion, evidenceKind), as the Zod schema reads them: no populate_by_name
    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal[BRIGHT_STAR_SUPPLEMENT_FORMAT]
    format_version: Annotated[int, Strict(), Field(ge=1, le=1)] = Field(alias="formatVersion")
    evidence_kind: Literal[BRIGHT_STAR_EVIDENCE_KIND] = Field(alias="evidenceKind")
    catalogue: ShortText
    frame: Literal[BRIGHT_STAR_FRAME]
    epoch_julian_year: Literal[BRIGHT_STAR_EPOCH_JULIAN_YEAR] = Field(alias="epochJulianYear")
    tiles_release: ShortText = Field(alias="tilesRelease")
    columns: tuple[Annotated[str, Strict()], ...]
    rows: tuple[BrightStarRow, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _rules(self) -> BrightStarSupplement:
        if self.columns != BRIGHT_STAR_COLUMNS:
            raise ValueError(f"A bright-star supplement's columns are exactly {list(BRIGHT_STAR_COLUMNS)}.")
        hips = [row[0] for row in self.rows]
        if len(set(hips)) != len(hips):
            raise ValueError("HIP numbers are unique.")
        places = [row[19:22] for row in self.rows]
        if len(set(places)) != len(places):
            raise ValueError("Tile locations (layer, pixel, index) are unique.")
        return self

    def to_contract_dict(self) -> dict[str, Any]:
        """The supplement as the Zod schema expects it: camelCase keys, rows as arrays, null for no value."""
        return self.model_dump(mode="json", by_alias=True)
