"""Python mirror of the eclipse catalogue contract in packages/contracts/src/eclipses.ts (data-6c0).

Fred Espenak and Jean Meeus's Five Millennium Catalogs of Solar and Lunar Eclipses, -1999 to +3000 (NASA's Goddard
Space Flight Center), as scripts/bake_eclipses.py writes them to apps/web/public/catalog/eclipses/. Every eclipse
in them is computed, not observed, so a catalogue's evidence class is `predicted`. NASA's key to the catalogues
names delta T, the drift of Earth's rotation, as the largest uncertainty in the predictions.

A catalogue is {format, formatVersion, kind, evidenceClass, columns, rows}. Each row is a tuple in the order
`columns` names, and a value the catalogue leaves blank is null. Dates are NASA's: the Julian calendar up to 1582
October 4 and the Gregorian from 1582 October 15, with years numbered astronomically (year 0 is 1 BCE). Integers
are JSON integers. tests/fixtures/eclipse_contract.json holds samples both sides of the contract judge alike.

The rules beyond each column's own range, as NASA's catalogue keys (SEcat5/SEcatkey.html, LEcat5/LEcatkey.html)
define the columns, each holding for every row of both catalogues:
- the date exists in the calendar NASA uses for it, and the row names that calendar;
- rows run in catalogue order and in time: catalogue numbers and instants of greatest eclipse both increase;
- solar: a partial eclipse has no path width and no central duration; at a partial or non-central eclipse (+, -)
  the Sun is on the horizon at the point of greatest eclipse; only a hybrid path begins one way and ends the other
  (2, 3); a Saros series begins and ends with a partial eclipse (b, e); a partial eclipse has no central or umbral
  path (n, s, +, -);
- lunar: a penumbral eclipse has no partial or total phase and misses the umbra (umbral magnitude at most 0); a
  partial eclipse has a partial phase, no total phase and an umbral magnitude between 0 and 1; a total eclipse has
  both phases and an umbral magnitude of at least 1; each phase lies within the one before it; x, which the
  catalogue file writes for the key's *, marks a total penumbral eclipse (penumbral magnitude at least 1); + and -
  mark a central total eclipse; a Saros series begins and ends with a penumbral eclipse (b, e).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, Strict, StringConstraints, model_validator

ECLIPSE_CATALOG_FORMAT = "albedian.eclipse-catalog"
ECLIPSE_CATALOG_FORMAT_VERSION = 1
ECLIPSE_EVIDENCE_CLASS = "predicted"
ECLIPSE_CATALOG_FIRST_YEAR = -1999
ECLIPSE_CATALOG_LAST_YEAR = 3000
ECLIPSE_CALENDARS = ("julian", "gregorian")

# Eclipse types and the second character of the type column, as NASA's keys list them
SOLAR_ECLIPSE_TYPES = ("P", "A", "T", "H")
SOLAR_ECLIPSE_QUALIFIERS = ("m", "n", "s", "+", "-", "2", "3", "b", "e")
LUNAR_ECLIPSE_TYPES = ("N", "P", "T")
LUNAR_ECLIPSE_QUALIFIERS = ("m", "+", "-", "x", "b", "e")

# The quincena: the eclipse of the other kind a fortnight before (first character) and after (second), '-' for none
QUINCENA_LUNAR_PATTERN = r"^[-npt]{2}$"
QUINCENA_SOLAR_PATTERN = r"^[-apth]{2}$"

SOLAR_ECLIPSE_COLUMNS = (
    "catalogNumber",
    "canonPlate",
    "year",
    "month",
    "day",
    "calendar",
    "greatestEclipseTdSeconds",
    "deltaTSeconds",
    "lunationNumber",
    "sarosNumber",
    "eclipseType",
    "typeQualifier",
    "quincenaLunarEclipse",
    "gamma",
    "magnitude",
    "latitudeDeg",
    "longitudeDeg",
    "sunAltitudeDeg",
    "sunAzimuthDeg",
    "pathWidthKm",
    "centralDurationSeconds",
)
LUNAR_ECLIPSE_COLUMNS = (
    "catalogNumber",
    "year",
    "month",
    "day",
    "calendar",
    "greatestEclipseTdSeconds",
    "deltaTSeconds",
    "lunationNumber",
    "sarosNumber",
    "eclipseType",
    "typeQualifier",
    "quincenaSolarEclipse",
    "gamma",
    "penumbralMagnitude",
    "umbralMagnitude",
    "penumbralDurationMinutes",
    "partialDurationMinutes",
    "totalDurationMinutes",
    "zenithLatitudeDeg",
    "zenithLongitudeDeg",
)

_LAST_JULIAN_DAY = (1582, 10, 4)
_FIRST_GREGORIAN_DAY = (1582, 10, 15)
_MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _is_leap(year: int, calendar: str) -> bool:
    if calendar == "julian":
        return year % 4 == 0
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def eclipse_calendar_of(year: int, month: int, day: int) -> str | None:
    """The calendar NASA's catalogues date this day in, or None where the day does not exist in it."""
    if not 1 <= month <= 12 or day < 1:
        return None
    if (year, month, day) <= _LAST_JULIAN_DAY:
        calendar = "julian"
    elif (year, month, day) >= _FIRST_GREGORIAN_DAY:
        calendar = "gregorian"
    else:  # 1582 October 5 to 14, which the reform skipped
        return None
    length = 29 if month == 2 and _is_leap(year, calendar) else _MONTH_DAYS[month - 1]
    return calendar if day <= length else None


StrictInt = Annotated[int, Strict()]
Finite = Annotated[float, Strict(), Field(allow_inf_nan=False)]
PositiveFinite = Annotated[float, Strict(), Field(gt=0, allow_inf_nan=False)]
CatalogNumber = Annotated[int, Strict(), Field(ge=1)]
Year = Annotated[int, Strict(), Field(ge=ECLIPSE_CATALOG_FIRST_YEAR, le=ECLIPSE_CATALOG_LAST_YEAR)]
Month = Annotated[int, Strict(), Field(ge=1, le=12)]
Day = Annotated[int, Strict(), Field(ge=1, le=31)]
Calendar = Literal[ECLIPSE_CALENDARS]
TdSeconds = Annotated[int, Strict(), Field(ge=0, le=86399)]

SolarEclipseRow = tuple[
    CatalogNumber,
    CatalogNumber,  # canon plate
    Year,
    Month,
    Day,
    Calendar,
    TdSeconds,
    StrictInt,  # delta T, seconds
    StrictInt,  # lunation number
    StrictInt,  # Saros number
    Literal[SOLAR_ECLIPSE_TYPES],
    Literal[SOLAR_ECLIPSE_QUALIFIERS] | None,
    Annotated[str, Strict(), StringConstraints(pattern=QUINCENA_LUNAR_PATTERN)],
    Finite,  # gamma
    Annotated[float, Strict(), Field(ge=0, allow_inf_nan=False)],  # magnitude
    Annotated[float, Strict(), Field(ge=-90, le=90, allow_inf_nan=False)],
    Annotated[float, Strict(), Field(ge=-180, le=180, allow_inf_nan=False)],
    Annotated[int, Strict(), Field(ge=0, le=90)],  # Sun altitude
    Annotated[int, Strict(), Field(ge=0, le=360)],  # Sun azimuth
    Annotated[int, Strict(), Field(ge=0)] | None,  # path width, km
    Annotated[int, Strict(), Field(ge=0)] | None,  # central duration, s
]

LunarEclipseRow = tuple[
    CatalogNumber,
    Year,
    Month,
    Day,
    Calendar,
    TdSeconds,
    StrictInt,  # delta T, seconds
    StrictInt,  # lunation number
    StrictInt,  # Saros number
    Literal[LUNAR_ECLIPSE_TYPES],
    Literal[LUNAR_ECLIPSE_QUALIFIERS] | None,
    Annotated[str, Strict(), StringConstraints(pattern=QUINCENA_SOLAR_PATTERN)],
    Finite,  # gamma
    PositiveFinite,  # penumbral magnitude
    Finite,  # umbral magnitude
    PositiveFinite,  # penumbral phase, minutes
    PositiveFinite | None,  # partial phase, minutes
    PositiveFinite | None,  # total phase, minutes
    Annotated[int, Strict(), Field(ge=-90, le=90)],
    Annotated[int, Strict(), Field(ge=-180, le=180)],
]


def solar_row_problem(row: tuple[Any, ...]) -> str | None:
    """The first rule a solar row breaks beyond its columns' own ranges, or None."""
    _, _, year, month, day, calendar, *_, kind, qualifier, _, _, _, _, _, altitude, _, width, duration = row
    if eclipse_calendar_of(year, month, day) != calendar:
        return "the date does not exist in the calendar NASA uses for it"
    if qualifier in ("2", "3") and kind != "H":
        return "only a hybrid eclipse's path begins one way and ends the other (2, 3)"
    if qualifier in ("b", "e") and kind != "P":
        return "a solar Saros series begins and ends with a partial eclipse (b, e)"
    if qualifier in ("n", "s", "+", "-") and kind == "P":
        return "a partial eclipse has no central or umbral path (n, s, +, -)"
    if kind == "P" and (width is not None or duration is not None):
        return "a partial eclipse has no path width and no central duration"
    if (kind == "P" or qualifier in ("+", "-")) and altitude != 0:
        return "at a partial or non-central eclipse the Sun is on the horizon at the point of greatest eclipse"
    return None


def lunar_row_problem(row: tuple[Any, ...]) -> str | None:
    """The first rule a lunar row breaks beyond its columns' own ranges, or None."""
    _, year, month, day, calendar, *_, kind, qualifier, _, _, penumbral, umbral, penumbral_min, partial, total, _, _ = row
    if eclipse_calendar_of(year, month, day) != calendar:
        return "the date does not exist in the calendar NASA uses for it"
    if kind == "N" and (partial is not None or total is not None or umbral > 0):
        return "a penumbral eclipse has no partial or total phase and misses the umbra"
    if kind == "P" and (partial is None or total is not None or not 0 < umbral < 1):
        return "a partial eclipse has a partial phase, no total phase and an umbral magnitude between 0 and 1"
    if kind == "T" and (partial is None or total is None or umbral < 1):
        return "a total eclipse has partial and total phases and an umbral magnitude of at least 1"
    if partial is not None and partial >= penumbral_min:
        return "the partial phase lies within the penumbral phase"
    if total is not None and partial is not None and total >= partial:
        return "the total phase lies within the partial phase"
    if qualifier == "x" and (kind != "N" or penumbral < 1):
        return "x marks a total penumbral eclipse: the whole Moon within the penumbra"
    if qualifier in ("+", "-") and kind != "T":
        return "+ and - mark a central total eclipse"
    if qualifier in ("b", "e") and kind != "N":
        return "a lunar Saros series begins and ends with a penumbral eclipse (b, e)"
    return None


class _EclipseCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    # The subclasses narrow kind and rows; declaring them here keeps the wire order of the keys
    format: Literal[ECLIPSE_CATALOG_FORMAT]
    format_version: Annotated[int, Strict(), Field(ge=1, le=1)] = Field(alias="formatVersion")
    kind: str
    evidence_class: Literal[ECLIPSE_EVIDENCE_CLASS] = Field(alias="evidenceClass")
    columns: tuple[Annotated[str, Strict()], ...]
    rows: tuple[Any, ...]

    def _check(self, expected_columns: tuple[str, ...], problem: Any, year_at: int) -> None:
        if self.columns != expected_columns:
            raise ValueError(f"A {self.kind} catalogue's columns are exactly {list(expected_columns)}.")
        for i, row in enumerate(self.rows):
            if (found := problem(row)) is not None:
                raise ValueError(f"Row {i} (catalogue number {row[0]}): {found}.")
        for i in range(1, len(self.rows)):
            before, after = self.rows[i - 1], self.rows[i]
            if after[0] <= before[0]:
                raise ValueError(f"Row {i}: catalogue numbers increase from row to row.")
            # (year, month, day, calendar, seconds): the calendar sits between the day and the seconds
            if (*after[year_at : year_at + 3], after[year_at + 4]) <= (*before[year_at : year_at + 3], before[year_at + 4]):
                raise ValueError(f"Row {i}: the eclipses follow one another in time.")

    def to_contract_dict(self) -> dict[str, Any]:
        """The catalogue as the Zod schema expects it: camelCase keys, rows as arrays, null for blank values."""
        return self.model_dump(mode="json", by_alias=True)


class SolarEclipseCatalog(_EclipseCatalog):
    """apps/web/public/catalog/eclipses/solar.json: the Five Millennium Catalog of Solar Eclipses."""

    kind: Literal["solar"]
    rows: tuple[SolarEclipseRow, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _rules(self) -> SolarEclipseCatalog:
        self._check(SOLAR_ECLIPSE_COLUMNS, solar_row_problem, year_at=2)
        return self


class LunarEclipseCatalog(_EclipseCatalog):
    """apps/web/public/catalog/eclipses/lunar.json: the Five Millennium Catalog of Lunar Eclipses."""

    kind: Literal["lunar"]
    rows: tuple[LunarEclipseRow, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _rules(self) -> LunarEclipseCatalog:
        self._check(LUNAR_ECLIPSE_COLUMNS, lunar_row_problem, year_at=1)
        return self
