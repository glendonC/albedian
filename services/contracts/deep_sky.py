"""Python mirror of the deep-sky catalogue contract in packages/contracts/src/deep-sky.ts (data-7a0).

OpenNGC (github.com/mattiaverga/OpenNGC, CC-BY-SA-4.0, credit Mattia Verga), its NGC and addendum tables, as
scripts/bake_deepsky_openngc.py writes them to apps/web/public/catalog/deepsky/. The rows are catalogue
positions and sizes from the literature that OpenNGC compiles, none measured by this app, so the evidence kind is
'catalog-derived'.

A catalogue is {format, formatVersion, evidenceKind, release, types, columns, rows}:
- release: the OpenNGC release the rows come from, such as 'OpenNGC v20260501';
- types: each of OpenNGC's 21 object type codes with its label, exactly those codes;
- rows: tuples in the order `columns` names: the OpenNGC name, its type code, J2000 right ascension and
  declination in degrees, the constellation, major and minor axis in arcmin, the major axis position angle (north
  through east, 0 to 180 degrees), B and V magnitude, the mean surface brightness, the Messier number, the NGC and IC
  cross references as OpenNGC writes them ('0104', '5195A', '0190 NED01'), other catalogue identifiers, and common
  names. A value OpenNGC leaves blank is null (or an empty list), never filled.

Rules beyond each cell's own range, each holding on every row of OpenNGC v20260501: names are unique; right
ascension and declination are both given or both null, and null only on a nonexistent object (type NonEx); the
constellation is null exactly when the position is; Serpens is 'Se1' (Caput) or 'Se2' (Cauda) as OpenNGC writes
it; a minor axis or a position angle comes only with a major axis, and the minor axis is never the larger; a Messier
number belongs to one row that is not a duplicate (type Dup), so M102, a duplicate of M101, may repeat it; a
duplicate names the object it duplicates in its NGC, IC or Messier cell. tests/fixtures/deep_sky_contract.json holds
samples both sides of the contract judge alike.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, Strict, StringConstraints, model_validator

from services.contracts.provenance import SHORT_TEXT_MAX_LENGTH, TEXT_PATTERN

DEEP_SKY_CATALOG_FORMAT = "albedian.deep-sky-catalog"
DEEP_SKY_CATALOG_FORMAT_VERSION = 1
DEEP_SKY_EVIDENCE_KIND = "catalog-derived"

# OpenNGC's object type codes, in the order of its NGC_guide.txt
DEEP_SKY_OBJECT_TYPES = (
    "*", "**", "*Ass", "OCl", "GCl", "Cl+N", "G", "GPair", "GTrpl", "GGroup", "PN", "HII", "DrkN", "EmN", "Neb",
    "RfN", "SNR", "Nova", "NonEx", "Dup", "Other",
)  # fmt: skip
DEEP_SKY_NONEXISTENT_TYPE = "NonEx"
DEEP_SKY_DUPLICATE_TYPE = "Dup"

# The 88 IAU constellation abbreviations, with Serpens split into Caput (Se1) and Cauda (Se2) as OpenNGC writes it
DEEP_SKY_CONSTELLATIONS = (
    "And", "Ant", "Aps", "Aql", "Aqr", "Ara", "Ari", "Aur", "Boo", "CMa", "CMi", "CVn", "Cae", "Cam", "Cap", "Car",
    "Cas", "Cen", "Cep", "Cet", "Cha", "Cir", "Cnc", "Col", "Com", "CrA", "CrB", "Crt", "Cru", "Crv", "Cyg", "Del",
    "Dor", "Dra", "Equ", "Eri", "For", "Gem", "Gru", "Her", "Hor", "Hya", "Hyi", "Ind", "LMi", "Lac", "Leo", "Lep",
    "Lib", "Lup", "Lyn", "Lyr", "Men", "Mic", "Mon", "Mus", "Nor", "Oct", "Oph", "Ori", "Pav", "Peg", "Per", "Phe",
    "Pic", "PsA", "Psc", "Pup", "Pyx", "Ret", "Scl", "Sco", "Sct", "Se1", "Se2", "Sex", "Sge", "Sgr", "Tau", "Tel",
    "TrA", "Tri", "Tuc", "UMa", "UMi", "Vel", "Vir", "Vol", "Vul",
)  # fmt: skip
DEEP_SKY_MESSIER_MIN = 1
DEEP_SKY_MESSIER_MAX = 110

DEEP_SKY_COLUMNS = (
    "name",
    "type",
    "raDeg",
    "decDeg",
    "constellation",
    "majorAxisArcmin",
    "minorAxisArcmin",
    "positionAngleDeg",
    "bMag",
    "vMag",
    "surfaceBrightness",
    "messier",
    "ngc",
    "ic",
    "identifiers",
    "commonNames",
)

# An NGC or IC number as OpenNGC writes it: four digits, an optional component letter or two, an optional NED part
CROSS_REFERENCE_PATTERN = r"^[0-9]{4}[A-Z]{0,2}(?: NED[0-9]{2})?$"

ShortText = Annotated[str, Strict(), StringConstraints(max_length=SHORT_TEXT_MAX_LENGTH, pattern=TEXT_PATTERN)]
CrossReference = Annotated[str, Strict(), StringConstraints(pattern=CROSS_REFERENCE_PATTERN)]


def _finite(**bounds: float) -> Any:
    return Annotated[float, Strict(), Field(allow_inf_nan=False, **bounds)]


DeepSkyRow = tuple[
    ShortText,  # name
    Literal[DEEP_SKY_OBJECT_TYPES],  # type
    _finite(ge=0, lt=360) | None,  # raDeg
    _finite(ge=-90, le=90) | None,  # decDeg
    Literal[DEEP_SKY_CONSTELLATIONS] | None,  # constellation
    _finite(gt=0) | None,  # majorAxisArcmin
    _finite(gt=0) | None,  # minorAxisArcmin
    _finite(ge=0, le=180) | None,  # positionAngleDeg
    _finite() | None,  # bMag
    _finite() | None,  # vMag
    _finite() | None,  # surfaceBrightness
    Annotated[int, Strict(), Field(ge=DEEP_SKY_MESSIER_MIN, le=DEEP_SKY_MESSIER_MAX)] | None,  # messier
    tuple[CrossReference, ...],  # ngc
    tuple[CrossReference, ...],  # ic
    tuple[ShortText, ...],  # identifiers
    tuple[ShortText, ...],  # commonNames
]


class DeepSkyCatalog(BaseModel):
    """apps/web/public/catalog/deepsky/openngc.json."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    format: Literal[DEEP_SKY_CATALOG_FORMAT]
    format_version: Annotated[int, Strict(), Field(ge=1, le=1)] = Field(alias="formatVersion")
    evidence_kind: Literal[DEEP_SKY_EVIDENCE_KIND] = Field(alias="evidenceKind")
    release: ShortText
    types: dict[Literal[DEEP_SKY_OBJECT_TYPES], ShortText]
    columns: tuple[Annotated[str, Strict()], ...]
    rows: tuple[DeepSkyRow, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _rules(self) -> DeepSkyCatalog:
        if self.columns != DEEP_SKY_COLUMNS:
            raise ValueError(f"A deep-sky catalogue's columns are exactly {list(DEEP_SKY_COLUMNS)}.")
        missing = [code for code in DEEP_SKY_OBJECT_TYPES if code not in self.types]
        if missing:
            raise ValueError(f"Every object type has a label; missing {missing}.")
        names = [row[0] for row in self.rows]
        if len(set(names)) != len(names):
            raise ValueError("Object names are unique.")
        messier_owner: dict[int, str] = {}
        for i, row in enumerate(self.rows):
            name, kind, ra, dec, constellation, major, minor, angle = row[:8]
            messier, ngc, ic = row[11], row[12], row[13]
            if (ra is None) != (dec is None):
                raise ValueError(f"Row {i} ({name}): right ascension and declination are both given or both null.")
            if ra is None and kind != DEEP_SKY_NONEXISTENT_TYPE:
                raise ValueError(f"Row {i} ({name}): only a nonexistent object has no position.")
            if (constellation is None) != (ra is None):
                raise ValueError(f"Row {i} ({name}): the constellation is null exactly when the position is.")
            if minor is not None and (major is None or minor > major):
                raise ValueError(f"Row {i} ({name}): a minor axis comes with a major axis and is never the larger.")
            if angle is not None and major is None:
                raise ValueError(f"Row {i} ({name}): a position angle comes with a major axis.")
            if kind == DEEP_SKY_DUPLICATE_TYPE:
                if messier is None and not ngc and not ic:
                    raise ValueError(f"Row {i} ({name}): a duplicate names its object in NGC, IC or Messier.")
            elif messier is not None:
                if messier in messier_owner:
                    raise ValueError(f"Row {i} ({name}): M{messier} already belongs to {messier_owner[messier]}.")
                messier_owner[messier] = name
        return self

    def to_contract_dict(self) -> dict[str, Any]:
        """The catalogue as the Zod schema expects it: camelCase keys, rows as arrays, null for no value."""
        return self.model_dump(mode="json", by_alias=True)
