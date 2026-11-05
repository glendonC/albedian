"""Python mirror of the exoplanet table contract in packages/contracts/src/exoplanets.ts (data-6c0).

The NASA Exoplanet Archive's Planetary Systems Composite Parameters table (PSCompPars), as
scripts/bake_exoplanets.py writes it to apps/web/public/catalog/exoplanets/. The archive builds one row per planet
from several papers, so a row is not one self-consistent solution, and every value names its own reference. The
evidence kind is 'catalog-derived': values from the literature and from the archive's own calculations, none
measured by this app.

A table is {format, formatVersion, evidenceKind, columns, notes, references, rows}:
- notes: text per column, the equilibrium temperature's at least;
- references: {refstr, label, url} with an https url, each cited at least once and none listed twice. The
  archive's refstr does not identify one paper (a refstr can stand for two), so a value cites a reference by its
  index in this list;
- rows: tuples in the order `columns` names. The first eight hold the planet's name and host, the star and planet
  counts, its discovery and the archive's mass provenance (pl_bmassprov); each of the rest is null or
  [value, err1, err2, limit, ref, basis]:
  - err1 the positive uncertainty (0 or more) and err2 the negative one (0 or less), null where the archive gives
    none;
  - limit the archive's limit flag as the archive writes it (-1, 0 or 1), null where it gives none;
  - ref the index of a reference;
  - basis: measured (a paper's value), calculated (the archive's formula from other values) or modelled (the
    archive's mass-radius relation, Chen and Kipping 2017). The archive marks the last two "Calculated Value", and
    the table cites them to the archive's page on its calculations, whose refstr is CALCULATED_VALUE.

Rules beyond each cell's own range: planet names are unique; a value cites the calculation page exactly when its
basis is calculated or modelled; such a radius or mass is modelled and any other calculated; a mass is modelled
exactly when its provenance is 'M-R relationship'. tests/fixtures/exoplanet_contract.json holds samples both sides
of the contract judge alike.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, Strict, StringConstraints, model_validator

from services.contracts.provenance import BLANK_CHARACTERS, SHORT_TEXT_MAX_LENGTH, TEXT_MAX_LENGTH, TEXT_PATTERN, URL_MAX_LENGTH

EXOPLANET_TABLE_FORMAT = "albedian.exoplanet-table"
EXOPLANET_TABLE_FORMAT_VERSION = 1
EXOPLANET_EVIDENCE_KIND = "catalog-derived"
EXOPLANET_VALUE_BASES = ("measured", "calculated", "modelled")
# pl_bmassprov as the archive writes it
EXOPLANET_MASS_PROVENANCES = ("Mass", "Msini", "M-R relationship", "Msin(i)/sin(i)")
EXOPLANET_CALCULATED_REFSTR = "CALCULATED_VALUE"

EXOPLANET_IDENTITY_COLUMNS = (
    "name",
    "hostName",
    "starCount",
    "planetCount",
    "discoveryMethod",
    "discoveryYear",
    "discoveryFacility",
    "massProvenance",
)
EXOPLANET_VALUE_COLUMNS = (
    "orbitalPeriodDays",
    "semiMajorAxisAu",
    "radiusEarth",
    "massEarth",
    "eccentricity",
    "equilibriumTemperatureK",
    "hostEffectiveTemperatureK",
    "hostRadiusSolar",
    "hostMassSolar",
    "distancePc",
    "raDeg",
    "decDeg",
    "vMagnitude",
)
EXOPLANET_COLUMNS = EXOPLANET_IDENTITY_COLUMNS + EXOPLANET_VALUE_COLUMNS
# The columns the archive fills from its mass-radius relation when a paper gives no value
EXOPLANET_MODELLED_COLUMNS = ("radiusEarth", "massEarth")
EXOPLANET_REQUIRED_NOTES = ("equilibriumTemperatureK",)

REFSTR_PATTERN = r"^[A-Z0-9_]+$"
HTTPS_URL_PATTERN = rf"^https://[^{BLANK_CHARACTERS}/]+[^{BLANK_CHARACTERS}]*$"

ShortText = Annotated[str, Strict(), StringConstraints(max_length=SHORT_TEXT_MAX_LENGTH, pattern=TEXT_PATTERN)]
LongText = Annotated[str, Strict(), StringConstraints(max_length=TEXT_MAX_LENGTH, pattern=TEXT_PATTERN)]


def _finite(**bounds: float) -> Any:
    return Annotated[float, Strict(), Field(allow_inf_nan=False, **bounds)]


# Each value column's own range
VALUE_RANGES: dict[str, Any] = {
    "orbitalPeriodDays": _finite(gt=0),
    "semiMajorAxisAu": _finite(gt=0),
    "radiusEarth": _finite(gt=0),
    "massEarth": _finite(gt=0),
    "eccentricity": _finite(ge=0, lt=1),
    "equilibriumTemperatureK": _finite(gt=0),
    "hostEffectiveTemperatureK": _finite(gt=0),
    "hostRadiusSolar": _finite(gt=0),
    "hostMassSolar": _finite(gt=0),
    "distancePc": _finite(gt=0),
    "raDeg": _finite(ge=0, lt=360),
    "decDeg": _finite(ge=-90, le=90),
    "vMagnitude": _finite(),
}


def _cell(value: Any) -> Any:
    return (
        tuple[
            value,
            _finite(ge=0) | None,  # err1
            _finite(le=0) | None,  # err2
            Annotated[int, Strict(), Field(ge=-1, le=1)] | None,  # limit flag
            Annotated[int, Strict(), Field(ge=0)],  # reference index
            Literal[EXOPLANET_VALUE_BASES],
        ]
        | None
    )


ExoplanetRow = tuple[
    (
        ShortText,  # name
        ShortText,  # host name
        Annotated[int, Strict(), Field(ge=1)],  # stars in the system
        Annotated[int, Strict(), Field(ge=1)],  # planets in the system
        ShortText,  # discovery method
        Annotated[int, Strict()],  # discovery year
        ShortText,  # discovery facility
        Literal[EXOPLANET_MASS_PROVENANCES] | None,
        *[_cell(VALUE_RANGES[name]) for name in EXOPLANET_VALUE_COLUMNS],
    )
]


class ExoplanetReference(BaseModel):
    """A paper or page a value cites."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    refstr: Annotated[str, Strict(), StringConstraints(pattern=REFSTR_PATTERN)]
    label: ShortText
    url: Annotated[str, Strict(), StringConstraints(max_length=URL_MAX_LENGTH, pattern=HTTPS_URL_PATTERN)]


class ExoplanetTable(BaseModel):
    """apps/web/public/catalog/exoplanets/pscomppars.json."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    format: Literal[EXOPLANET_TABLE_FORMAT]
    format_version: Annotated[int, Strict(), Field(ge=1, le=1)] = Field(alias="formatVersion")
    evidence_kind: Literal[EXOPLANET_EVIDENCE_KIND] = Field(alias="evidenceKind")
    columns: tuple[Annotated[str, Strict()], ...]
    notes: dict[Annotated[str, Strict()], LongText]
    references: tuple[ExoplanetReference, ...] = Field(min_length=1)
    rows: tuple[ExoplanetRow, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _rules(self) -> ExoplanetTable:
        if self.columns != EXOPLANET_COLUMNS:
            raise ValueError(f"An exoplanet table's columns are exactly {list(EXOPLANET_COLUMNS)}.")
        unknown = sorted(set(self.notes) - set(EXOPLANET_COLUMNS))
        if unknown:
            raise ValueError(f"Notes name columns the table does not have: {unknown}.")
        missing = [name for name in EXOPLANET_REQUIRED_NOTES if name not in self.notes]
        if missing:
            raise ValueError(f"Notes are required for {missing}.")
        pairs = [(ref.refstr, ref.url) for ref in self.references]
        if len(set(pairs)) != len(pairs):
            raise ValueError("A reference is listed twice (the same refstr and url).")
        names = [row[0] for row in self.rows]
        if len(set(names)) != len(names):
            raise ValueError("Planet names are unique.")
        cited = set()
        first_value = len(EXOPLANET_IDENTITY_COLUMNS)
        mass_at = EXOPLANET_COLUMNS.index("massEarth")
        for i, row in enumerate(self.rows):
            for name, cell in zip(EXOPLANET_VALUE_COLUMNS, row[first_value:], strict=True):
                if cell is None:
                    continue
                ref, basis = cell[4], cell[5]
                if ref >= len(self.references):
                    raise ValueError(f"Row {i} ({row[0]}), {name}: reference {ref} is not listed.")
                cited.add(ref)
                on_calculation_page = self.references[ref].refstr == EXOPLANET_CALCULATED_REFSTR
                if on_calculation_page != (basis != "measured"):
                    raise ValueError(f"Row {i} ({row[0]}), {name}: a value cites the calculation page exactly when it is calculated or modelled.")
                if on_calculation_page and basis != ("modelled" if name in EXOPLANET_MODELLED_COLUMNS else "calculated"):
                    raise ValueError(f"Row {i} ({row[0]}), {name}: the archive models radii and masses and calculates the rest.")
            mass = row[mass_at]
            if mass is not None and (mass[5] == "modelled") != (row[EXOPLANET_COLUMNS.index("massProvenance")] == "M-R relationship"):
                raise ValueError(f"Row {i} ({row[0]}): a mass is modelled exactly when its provenance is 'M-R relationship'.")
        unused = sorted(set(range(len(self.references))) - cited)
        if unused:
            raise ValueError(f"References no value cites: {unused}.")
        return self

    def to_contract_dict(self) -> dict[str, Any]:
        """The table as the Zod schema expects it: camelCase keys, rows and cells as arrays, null for no value."""
        return self.model_dump(mode="json", by_alias=True)
