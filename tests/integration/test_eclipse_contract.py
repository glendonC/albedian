"""Eclipse catalogue contract (data-6c0): Pydantic rules, a property, and the fixture shared with the Zod schemas.

`tests/fixtures/eclipse_contract.json` is written from `build_eclipse_samples()` and parsed by
packages/contracts/src/__tests__/eclipses.test.ts, so both sides accept and refuse the same catalogues. Regenerate
it after a deliberate contract change with:

    ALBEDIAN_UPDATE_CONTRACT_FIXTURE=1 uv run pytest tests/integration/test_eclipse_contract.py

The valid samples are real rows of Fred Espenak and Jean Meeus's Five Millennium Catalogs of Solar and Lunar
Eclipses (NASA GSFC, 5MKSEcatalog.txt and 5MKLEcatalog.txt), turned into the contract's tuples by a script and
kept here as literals with their catalogue numbers. Every invalid sample breaks one rule of them. NASA's catalogue
keys grant permission to reproduce this data with the acknowledgment the fixture also carries: "Eclipse Predictions
by Fred Espenak and Jean Meeus (NASA's GSFC)".
"""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from services.contracts import (
    ECLIPSE_CATALOG_FORMAT,
    ECLIPSE_CATALOG_FORMAT_VERSION,
    LUNAR_ECLIPSE_COLUMNS,
    SOLAR_ECLIPSE_COLUMNS,
    LunarEclipseCatalog,
    SolarEclipseCatalog,
)
from services.contracts import eclipses as ec

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "eclipse_contract.json"
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_CONTRACT_FIXTURE"
# NASA's catalogue keys (SEcat5/SEcatkey.html, LEcat5/LEcatkey.html): the acknowledgment that must accompany the data
ACKNOWLEDGMENT = "Eclipse Predictions by Fred Espenak and Jean Meeus (NASA's GSFC)"

# Real rows of 5MKSEcatalog.txt, in catalogue order, each commented with the catalogue's own date and type
SOLAR_ROWS: list[list[Any]] = [
    [1, 1, -1999, 6, 12, "julian", 11691, 46438, -49456, 5, "T", None, "-n", -0.2701, 1.0733, 6.0, -33.3, 74, 344, 247, 397],  # -1999 Jun 12, T
    [5, 1, -1997, 4, 22, "julian", 47996, 46393, -49433, -13, "P", None, "-t", -1.467, 0.1611, -60.6, -106.4, 0, 281, None, None],  # -1997 Apr 22, P
    [16, 1, -1993, 3, 9, "julian", 71289, 46299, -49385, 27, "P", "b", "t-", 1.4907, 0.0754, 61.2, 11.4, 0, 114, None, None],  # -1993 Mar 09, Pb
    [138, 7, -1942, 11, 7, "julian", 5395, 45052, -48746, 2, "H", "3", "-p", 0.5284, 1.0143, 17.4, -2.2, 58, 208, 57, 77],  # -1942 Nov 07, H3
    [151, 8, -1936, 2, 8, "julian", 41078, 44926, -48681, 19, "A", "n", "t-", 0.9793, 0.9202, 53.9, 167.7, 11, 148, None, 458],  # -1936 Feb 08, An, no width
    [8500, 425, 1582, 6, 20, "julian", 19827, 128, -5164, 120, "T", None, "nn", 0.2032, 1.021, 35.0, 100.8, 78, 190, 73, 119],  # 1582 Jun 20, T
    [8501, 426, 1582, 12, 25, "gregorian", 14919, 127, -5158, 125, "A", None, "nn", 0.2457, 0.9459, -9.4, 116.8, 76, 173, 206, 422],  # 1582 Dec 25, A
    [9546, 478, 2017, 8, 21, "gregorian", 66400, 70, 218, 145, "T", None, "p-", 0.4367, 1.0306, 37.0, -87.7, 64, 198, 115, 160],  # 2017 Aug 21, T
    [9561, 479, 2024, 4, 8, "gregorian", 65909, 74, 300, 139, "T", None, "n-", 0.3431, 1.0566, 25.3, -104.1, 70, 149, 198, 268],  # 2024 Apr 08, T
    [11896, 595, 2999, 10, 30, "gregorian", 34473, 4420, 12366, 159, "A", "-", "-t", -1.0023, 0.9586, -70.9, -84.7, 0, 137, None, None],  # 2999 Oct 30, A-
    [11898, 595, 3000, 10, 19, "gregorian", 58216, 4428, 12378, 169, "H", None, "nn", -0.2303, 1.0049, -23.1, -51.6, 77, 16, 17, 29],  # 3000 Oct 19, H
]

# Real rows of 5MKLEcatalog.txt, likewise
LUNAR_ROWS: list[list[Any]] = [
    [1, -1999, 6, 26, "julian", 51208, 46437, -49456, 17, "N", None, "t-", -1.0981, 0.8791, -0.1922, 268.8, None, None, -24, -22],  # -1999 Jun 26, N
    [5, -1997, 5, 6, "julian", 68221, 46392, -49433, -1, "T", "+", "pp", 0.1003, 2.6513, 1.6963, 322.8, 213.5, 98.2, -11, -92],  # -1997 May 06, T+
    [25, -1989, 12, 1, "julian", 62462, 46184, -49327, 13, "N", "x", "h-", -1.0024, 1.0436, -0.0058, 271.9, None, None, 18, -69],  # -1989 Dec 01, Nx
    [459, -1814, 7, 21, "julian", 36282, 42043, -47167, 29, "N", "b", "t-", -1.5402, 0.0632, -0.9991, 76.6, None, None, -25, 23],  # -1814 Jul 21, Nb
    [8623, 1582, 7, 5, "julian", 65280, 127, -5164, 132, "N", None, "t-", 1.1903, 0.6722, -0.3245, 219.8, None, None, -20, 89],  # 1582 Jul 05, N
    [8624, 1582, 12, 9, "gregorian", 63908, 127, -5159, 99, "N", None, "-a", 1.1977, 0.6544, -0.334, 208.5, None, None, 24, 93],  # 1582 Dec 09, N
    [9689, 2017, 8, 7, "gregorian", 66098, 70, 217, 119, "P", None, "-t", 0.8668, 1.2886, 0.2464, 300.9, 115.2, None, -15, 86],  # 2017 Aug 07, P
    [9701, 2022, 11, 8, "gregorian", 39622, 73, 282, 136, "T", "+", "p-", 0.257, 2.4143, 1.3589, 353.9, 219.8, 85.0, 17, -169],  # 2022 Nov 08, T+
    [12064, 3000, 11, 4, "gregorian", 20718, 4428, 12378, 181, "N", None, "h-", -1.217, 0.6326, -0.3828, 214.8, None, None, 14, -72],  # 3000 Nov 04, N
]

MODELS = {"solar": SolarEclipseCatalog, "lunar": LunarEclipseCatalog}
COLUMNS = {"solar": SOLAR_ECLIPSE_COLUMNS, "lunar": LUNAR_ECLIPSE_COLUMNS}


def catalog(kind: str, rows: list[list[Any]]) -> dict[str, Any]:
    return {
        "format": ECLIPSE_CATALOG_FORMAT,
        "formatVersion": ECLIPSE_CATALOG_FORMAT_VERSION,
        "kind": kind,
        "evidenceClass": "predicted",
        "columns": list(COLUMNS[kind]),
        "rows": copy.deepcopy(rows),
    }


def set_first(cat: dict[str, Any], **fields: Any) -> None:
    """Set fields of the first row by column name."""
    row = cat["rows"][0]
    for name, value in fields.items():
        row[COLUMNS[cat["kind"]].index(name)] = value


def same_instant_as_first(cat: dict[str, Any]) -> None:
    columns = COLUMNS[cat["kind"]]
    for name in ("year", "month", "day", "calendar", "greatestEclipseTdSeconds"):
        cat["rows"][1][columns.index(name)] = cat["rows"][0][columns.index(name)]


def swap_first_two(cat: dict[str, Any]) -> None:
    cat["rows"][0], cat["rows"][1] = cat["rows"][1], cat["rows"][0]


# Each fault breaks a rule of any valid catalogue of at least two rows; a fault that sets several fields of a row
# sets every field its rule involves, so it cannot be repaired by what the row held before
COMMON_FAULTS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "format": lambda c: c.update(format="albedian.eclipse-table"),
    "formatVersion": lambda c: c.update(formatVersion=2),
    "evidence class observed": lambda c: c.update(evidenceClass="observed"),
    "extra field": lambda c: c.update(extra=1),
    "column renamed": lambda c: c["columns"].__setitem__(0, "number"),
    "other kind's columns": lambda c: c.update(columns=list(COLUMNS["lunar" if c["kind"] == "solar" else "solar"])),
    "no rows": lambda c: c.update(rows=[]),
    "short row": lambda c: c["rows"][0].pop(),
    "long row": lambda c: c["rows"][0].append(None),
    "catalogue number zero": lambda c: set_first(c, catalogNumber=0),
    "number written as text": lambda c: set_first(c, catalogNumber=str(c["rows"][0][0])),
    "boolean for a number": lambda c: set_first(c, gamma=True),
    "text for a number": lambda c: set_first(c, gamma="0.5"),
    "year before the catalogue": lambda c: set_first(c, year=-2000, calendar="julian"),
    "month 13": lambda c: set_first(c, month=13),
    "30 February": lambda c: set_first(c, month=2, day=30),
    "29 February in a Gregorian common year": lambda c: set_first(c, year=1900, month=2, day=29, calendar="gregorian"),
    "a day the calendar reform dropped": lambda c: set_first(c, year=1582, month=10, day=10, calendar="gregorian"),
    "calendar that does not match the date": lambda c: set_first(
        c, calendar="gregorian" if c["rows"][0][COLUMNS[c["kind"]].index("calendar")] == "julian" else "julian"
    ),
    "unknown calendar": lambda c: set_first(c, calendar="proleptic"),
    "time of day past midnight": lambda c: set_first(c, greatestEclipseTdSeconds=86400),
    "fractional seconds": lambda c: set_first(c, greatestEclipseTdSeconds=10.5),
    "unknown type": lambda c: set_first(c, eclipseType="X"),
    "unknown qualifier": lambda c: set_first(c, typeQualifier="q"),
    "rows out of order": swap_first_two,
    "repeated catalogue number": lambda c: c["rows"][1].__setitem__(0, c["rows"][0][0]),
    "two eclipses at one instant": same_instant_as_first,
    "infinite gamma written as a huge number": lambda c: set_first(c, gamma=1e309),
}

SOLAR_FAULTS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "negative magnitude": lambda c: set_first(c, magnitude=-0.0001),
    "latitude past the pole": lambda c: set_first(c, latitudeDeg=90.1),
    "longitude past the antimeridian": lambda c: set_first(c, longitudeDeg=-180.1),
    "Sun below the horizon": lambda c: set_first(c, sunAltitudeDeg=-1),
    "azimuth past 360": lambda c: set_first(c, sunAzimuthDeg=361),
    "fractional azimuth": lambda c: set_first(c, sunAzimuthDeg=12.5),
    "lunar quincena letter": lambda c: set_first(c, quincenaLunarEclipse="a-"),
    "negative path width": lambda c: set_first(
        c, eclipseType="T", typeQualifier=None, sunAltitudeDeg=50, pathWidthKm=-1, centralDurationSeconds=100
    ),
    "partial eclipse with a path width": lambda c: set_first(
        c, eclipseType="P", typeQualifier=None, sunAltitudeDeg=0, pathWidthKm=100, centralDurationSeconds=None
    ),
    "partial eclipse with a central duration": lambda c: set_first(
        c, eclipseType="P", typeQualifier=None, sunAltitudeDeg=0, pathWidthKm=None, centralDurationSeconds=60
    ),
    "partial eclipse with the Sun up": lambda c: set_first(
        c, eclipseType="P", typeQualifier=None, sunAltitudeDeg=10, pathWidthKm=None, centralDurationSeconds=None
    ),
    "non-central eclipse with the Sun up": lambda c: set_first(
        c, eclipseType="A", typeQualifier="-", sunAltitudeDeg=10, pathWidthKm=None, centralDurationSeconds=None
    ),
    "hybrid mark on a total eclipse": lambda c: set_first(
        c, eclipseType="T", typeQualifier="2", sunAltitudeDeg=50, pathWidthKm=100, centralDurationSeconds=100
    ),
    "series start on a central eclipse": lambda c: set_first(
        c, eclipseType="T", typeQualifier="b", sunAltitudeDeg=50, pathWidthKm=100, centralDurationSeconds=100
    ),
    "non-central mark on a partial eclipse": lambda c: set_first(
        c, eclipseType="P", typeQualifier="+", sunAltitudeDeg=0, pathWidthKm=None, centralDurationSeconds=None
    ),
    "total penumbral mark on a solar eclipse": lambda c: set_first(c, typeQualifier="x"),
}


def set_lunar_phases(c: dict[str, Any], kind: str, qualifier: Any, umbral: float, penumbral: float, durations: tuple) -> None:
    set_first(
        c,
        eclipseType=kind,
        typeQualifier=qualifier,
        umbralMagnitude=umbral,
        penumbralMagnitude=penumbral,
        penumbralDurationMinutes=durations[0],
        partialDurationMinutes=durations[1],
        totalDurationMinutes=durations[2],
    )


LUNAR_FAULTS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "zero penumbral magnitude": lambda c: set_lunar_phases(c, "N", None, -0.5, 0.0, (100.0, None, None)),
    "zero penumbral duration": lambda c: set_lunar_phases(c, "N", None, -0.5, 0.5, (0.0, None, None)),
    "zenith latitude past the pole": lambda c: set_first(c, zenithLatitudeDeg=91),
    "fractional zenith longitude": lambda c: set_first(c, zenithLongitudeDeg=12.5),
    "solar quincena letter": lambda c: set_first(c, quincenaSolarEclipse="n-"),
    "penumbral eclipse with a partial phase": lambda c: set_lunar_phases(c, "N", None, -0.1, 0.5, (100.0, 50.0, None)),
    "partial eclipse without a partial phase": lambda c: set_lunar_phases(c, "P", None, 0.5, 1.5, (200.0, None, None)),
    "partial eclipse with a total phase": lambda c: set_lunar_phases(c, "P", None, 0.5, 1.5, (200.0, 100.0, 50.0)),
    "total eclipse without a total phase": lambda c: set_lunar_phases(c, "T", None, 1.2, 2.2, (300.0, 200.0, None)),
    "penumbral eclipse touching the umbra": lambda c: set_lunar_phases(c, "N", None, 0.1, 0.9, (200.0, None, None)),
    "partial eclipse at full umbral immersion": lambda c: set_lunar_phases(c, "P", None, 1.0, 2.0, (300.0, 200.0, None)),
    "total eclipse short of full immersion": lambda c: set_lunar_phases(c, "T", None, 0.99, 2.0, (300.0, 200.0, 50.0)),
    "partial phase longer than the penumbral": lambda c: set_lunar_phases(c, "P", None, 0.5, 1.5, (100.0, 150.0, None)),
    "total phase longer than the partial": lambda c: set_lunar_phases(c, "T", None, 1.2, 2.2, (300.0, 100.0, 150.0)),
    "total penumbral mark on a partial eclipse": lambda c: set_lunar_phases(c, "P", "x", 0.5, 1.5, (200.0, 100.0, None)),
    "total penumbral mark short of full immersion": lambda c: set_lunar_phases(c, "N", "x", -0.1, 0.9, (200.0, None, None)),
    "central mark on a penumbral eclipse": lambda c: set_lunar_phases(c, "N", "+", -0.1, 0.9, (200.0, None, None)),
    "series start on a total eclipse": lambda c: set_lunar_phases(c, "T", "b", 1.2, 2.2, (300.0, 200.0, 50.0)),
    "hybrid mark on a lunar eclipse": lambda c: set_first(c, typeQualifier="2"),
}

FAULTS = {"solar": {**COMMON_FAULTS, **SOLAR_FAULTS}, "lunar": {**COMMON_FAULTS, **LUNAR_FAULTS}}
# A JSON fixture cannot hold an infinite number; the property test applies that fault in Python only
JSON_UNSAFE_FAULTS = {"infinite gamma written as a huge number"}


def build_eclipse_samples() -> dict[str, Any]:
    valid = {
        "solar": [catalog("solar", SOLAR_ROWS), catalog("solar", SOLAR_ROWS[7:8])],
        "lunar": [catalog("lunar", LUNAR_ROWS), catalog("lunar", LUNAR_ROWS[7:8])],
    }
    # Each fault is shown on two real rows (2017 August and 2024 April, 2017 August and 2022 November), which
    # keeps the fixture small; the property test applies every fault to generated catalogues as well
    bases = {"solar": SOLAR_ROWS[7:9], "lunar": LUNAR_ROWS[6:8]}
    invalid = []
    for kind, faults in FAULTS.items():
        for rule, fault in faults.items():
            if rule in JSON_UNSAFE_FAULTS:
                continue
            broken = catalog(kind, bases[kind])
            fault(broken)
            invalid.append({"kind": kind, "rule": rule, "catalog": broken})
    return {
        "acknowledgment": ACKNOWLEDGMENT,
        "format": ECLIPSE_CATALOG_FORMAT,
        "formatVersion": ECLIPSE_CATALOG_FORMAT_VERSION,
        "evidenceClass": ec.ECLIPSE_EVIDENCE_CLASS,
        "years": {"first": ec.ECLIPSE_CATALOG_FIRST_YEAR, "last": ec.ECLIPSE_CATALOG_LAST_YEAR},
        "calendars": list(ec.ECLIPSE_CALENDARS),
        "solarColumns": list(SOLAR_ECLIPSE_COLUMNS),
        "lunarColumns": list(LUNAR_ECLIPSE_COLUMNS),
        "solarTypes": list(ec.SOLAR_ECLIPSE_TYPES),
        "solarQualifiers": list(ec.SOLAR_ECLIPSE_QUALIFIERS),
        "lunarTypes": list(ec.LUNAR_ECLIPSE_TYPES),
        "lunarQualifiers": list(ec.LUNAR_ECLIPSE_QUALIFIERS),
        "patterns": {"quincenaLunar": ec.QUINCENA_LUNAR_PATTERN, "quincenaSolar": ec.QUINCENA_SOLAR_PATTERN},
        "valid": valid,
        "invalid": invalid,
    }


def fixture_text(samples: dict[str, Any]) -> str:
    """Two-space JSON with each sample on one line, so the fixture stays small and a diff shows the samples that changed."""

    def compact(value: Any) -> str:
        return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))

    def block(value: Any, indent: int) -> str:
        pad = " " * indent
        if isinstance(value, dict):
            items = ",\n".join(f"{pad}  {json.dumps(key)}: {block(item, indent + 2)}" for key, item in value.items())
            return "{\n" + items + "\n" + pad + "}"
        return "[\n" + ",\n".join(f"{pad}  {compact(item)}" for item in value) + "\n" + pad + "]"

    lines = [f"  {json.dumps(key)}: {block(value, 2) if key in ('valid', 'invalid') else compact(value)}" for key, value in samples.items()]
    return "{\n" + ",\n".join(lines) + "\n}\n"


def test_eclipse_fixture_is_current() -> None:
    samples = build_eclipse_samples()
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        FIXTURE.write_text(fixture_text(samples), encoding="utf-8")
    stored = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert samples == stored, (
        f"{FIXTURE.name} no longer matches the Python eclipse contract; regenerate it with "
        f"{UPDATE_FIXTURE_ENV}=1 uv run pytest tests/integration/test_eclipse_contract.py"
    )


def test_real_rows_parse_and_round_trip_unchanged() -> None:
    for kind, samples in build_eclipse_samples()["valid"].items():
        for sample in samples:
            parsed = MODELS[kind].model_validate(sample)
            assert parsed.to_contract_dict() == sample
            assert json.loads(json.dumps(parsed.to_contract_dict())) == sample


@pytest.mark.parametrize("case", build_eclipse_samples()["invalid"], ids=lambda case: f"{case['kind']}: {case['rule']}")
def test_every_invalid_sample_is_refused(case: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        MODELS[case["kind"]].model_validate(case["catalog"])


def test_a_solar_catalogue_is_not_a_lunar_one() -> None:
    with pytest.raises(ValidationError):
        LunarEclipseCatalog.model_validate(catalog("solar", SOLAR_ROWS))
    with pytest.raises(ValidationError):
        SolarEclipseCatalog.model_validate({**catalog("lunar", LUNAR_ROWS), "kind": "solar"})


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_values_that_json_cannot_carry_are_refused(bad: float) -> None:
    solar = catalog("solar", SOLAR_ROWS)
    set_first(solar, gamma=bad)
    with pytest.raises(ValidationError):
        SolarEclipseCatalog.model_validate(solar)
    lunar = catalog("lunar", LUNAR_ROWS)
    set_first(lunar, penumbralDurationMinutes=bad)
    with pytest.raises(ValidationError):
        LunarEclipseCatalog.model_validate(lunar)


# =============================================================================================
# The calendar: an independent oracle (Meeus, Astronomical Algorithms, chapter 7) against the contract's own rule
# =============================================================================================

JDN_FIRST = 990924  # -1999 January 1, Julian calendar
JDN_LAST = 2817152  # 3000 December 31, Gregorian calendar
GREGORIAN_FROM_JDN = 2299161  # 1582 October 15


def date_of_jdn(jdn: int) -> tuple[int, int, int, str]:
    """The civil date whose noon is Julian Day Number jdn, in NASA's calendars, with astronomical year numbers."""
    if jdn < GREGORIAN_FROM_JDN:
        a, calendar = jdn, "julian"
    else:
        alpha = int((jdn - 1867216.25) // 36524.25)
        a, calendar = jdn + 1 + alpha - alpha // 4, "gregorian"
    b = a + 1524
    c = int((b - 122.1) // 365.25)
    d = int(365.25 * c)
    e = int((b - d) // 30.6001)
    day = b - d - int(30.6001 * e)
    month = e - 1 if e < 14 else e - 13
    year = c - 4716 if month > 2 else c - 4715
    return year, month, day, calendar


def test_the_oracle_knows_the_reform_and_the_ends_of_the_catalogue() -> None:
    assert date_of_jdn(JDN_FIRST) == (-1999, 1, 1, "julian")
    assert date_of_jdn(GREGORIAN_FROM_JDN - 1) == (1582, 10, 4, "julian")
    assert date_of_jdn(GREGORIAN_FROM_JDN) == (1582, 10, 15, "gregorian")
    assert date_of_jdn(2451545) == (2000, 1, 1, "gregorian")
    assert date_of_jdn(JDN_LAST) == (3000, 12, 31, "gregorian")


def jdn_of_new_year(year: int) -> int:
    """Julian Day Number of 1 January of `year` (Meeus 7.1), in NASA's calendar for that day."""
    y, m = year - 1, 13
    b = 0 if year <= 1582 else 2 - y // 100 + (y // 100) // 4
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + 1 + b - 1524


# Julian leap and common years either side of year 0, the reform year, Gregorian century rules, the two ends
SWEEP_YEARS = [*range(-1999, -1990), -5, -4, -1, 0, 1, 4, 100, *range(1580, 1585), 1600, 1700, 1900, 2000, 2024, 2100, 2999, 3000]


def test_the_contract_calendar_accepts_exactly_the_days_that_exist() -> None:
    for year in SWEEP_YEARS:
        days: dict[tuple[int, int], str] = {}
        jdn = jdn_of_new_year(year)
        assert date_of_jdn(jdn)[:3] == (year, 1, 1)
        while (date := date_of_jdn(jdn))[0] == year:
            days[date[1:3]] = date[3]
            jdn += 1
        assert len(days) == (355 if year == 1582 else 365 if (year % 4 or (year > 1582 and year % 100 == 0 and year % 400)) else 366)
        for month in range(1, 13):
            for day in range(1, 32):
                assert ec.eclipse_calendar_of(year, month, day) == days.get((month, day)), (year, month, day)


# =============================================================================================
# PBT-ECL-1: the models accept exactly the catalogues the rules allow
# =============================================================================================


def _floats(low: float, high: float, **bounds: bool) -> st.SearchStrategy[float]:
    return st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False, **bounds)


SOLAR_QUALIFIERS_BY_TYPE = {
    "P": [None, "m", "b", "e"],
    "A": [None, "m", "n", "s", "+", "-"],
    "T": [None, "m", "n", "s", "+", "-"],
    "H": [None, "m", "n", "s", "+", "-", "2", "3"],
}
LUNAR_QUALIFIERS_BY_TYPE = {"N": [None, "m", "x", "b", "e"], "P": [None, "m"], "T": [None, "m", "+", "-"]}


@st.composite
def solar_row(draw: st.DrawFn, number: int, jdn: int) -> list[Any]:
    year, month, day, calendar = date_of_jdn(jdn)
    kind = draw(st.sampled_from(ec.SOLAR_ECLIPSE_TYPES))
    qualifier = draw(st.sampled_from(SOLAR_QUALIFIERS_BY_TYPE[kind]))
    partial, sun_on_horizon = kind == "P", kind == "P" or qualifier in ("+", "-")
    width = None if partial else draw(st.one_of(st.none(), st.integers(0, 1500)))
    duration = None if partial else draw(st.one_of(st.none(), st.integers(0, 800)))
    return [
        number, draw(st.integers(1, 600)), year, month, day, calendar, draw(st.integers(0, 86399)),
        draw(st.integers(-10, 50000)), draw(st.integers(-60000, 15000)), draw(st.integers(-20, 200)), kind, qualifier,
        draw(st.from_regex(r"\A[-npt]{2}\Z")), draw(_floats(-1.6, 1.6)), draw(_floats(0.0, 1.1)),
        draw(_floats(-90.0, 90.0)), draw(_floats(-180.0, 180.0)), 0 if sun_on_horizon else draw(st.integers(0, 90)),
        draw(st.integers(0, 360)), width, duration,
    ]  # fmt: skip


@st.composite
def lunar_row(draw: st.DrawFn, number: int, jdn: int) -> list[Any]:
    year, month, day, calendar = date_of_jdn(jdn)
    kind = draw(st.sampled_from(ec.LUNAR_ECLIPSE_TYPES))
    qualifier = draw(st.sampled_from(LUNAR_QUALIFIERS_BY_TYPE[kind]))
    penumbral_minutes = draw(_floats(1.0, 400.0))
    partial = total = None
    if kind == "N":
        umbral = draw(_floats(-1.2, 0.0))
        penumbral = draw(_floats(1.0, 1.2) if qualifier == "x" else _floats(0.0, 1.2, exclude_min=True))
    elif kind == "P":
        umbral, penumbral = draw(_floats(0.0, 1.0, exclude_min=True, exclude_max=True)), draw(_floats(0.5, 3.0))
        partial = draw(_floats(0.1, penumbral_minutes, exclude_max=True))
    else:
        umbral, penumbral = draw(_floats(1.0, 1.9)), draw(_floats(1.5, 3.0))
        partial = draw(_floats(0.1, penumbral_minutes, exclude_max=True))
        total = draw(_floats(0.01, partial, exclude_max=True))
    return [
        number, year, month, day, calendar, draw(st.integers(0, 86399)), draw(st.integers(-10, 50000)),
        draw(st.integers(-60000, 15000)), draw(st.integers(-25, 200)), kind, qualifier,
        draw(st.from_regex(r"\A[-apth]{2}\Z")), draw(_floats(-1.6, 1.6)), penumbral, umbral, penumbral_minutes,
        partial, total, draw(st.integers(-90, 90)), draw(st.integers(-180, 180)),
    ]  # fmt: skip


@st.composite
def valid_catalog(draw: st.DrawFn) -> dict[str, Any]:
    kind = draw(st.sampled_from(["solar", "lunar"]))
    size = draw(st.integers(2, 6))
    days = sorted(draw(st.sets(st.integers(JDN_FIRST, JDN_LAST), min_size=size, max_size=size)))
    numbers = [draw(st.integers(1, 50))]
    for _ in range(size - 1):
        numbers.append(numbers[-1] + draw(st.integers(1, 9)))
    row = solar_row if kind == "solar" else lunar_row
    return catalog(kind, [draw(row(n, day)) for n, day in zip(numbers, days, strict=True)])


@st.composite
def catalog_with_fault(draw: st.DrawFn) -> tuple[dict[str, Any], str | None]:
    cat = draw(valid_catalog())
    fault = draw(st.one_of(st.none(), st.none(), st.sampled_from(sorted(FAULTS[cat["kind"]]))))
    if fault is not None:
        FAULTS[cat["kind"]][fault](cat)
    return cat, fault


@settings(max_examples=300, deadline=None)
@given(case=catalog_with_fault())
@example(case=(catalog("solar", SOLAR_ROWS), None))  # the real rows, every type and the 1582 reform
@example(case=(catalog("lunar", LUNAR_ROWS), None))
@example(case=(catalog("solar", [SOLAR_ROWS[5], SOLAR_ROWS[6]]), None))  # the last Julian and first Gregorian rows
def test_pbt_ecl_1_models_accept_exactly_the_catalogues_the_rules_allow(case: tuple[dict[str, Any], str | None]) -> None:
    """PBT-ECL-1 (Python half): validation succeeds exactly when no rule is broken, and the wire form round-trips."""
    cat, fault = case
    model = MODELS[cat["kind"]]
    try:
        parsed = model.model_validate(cat)
    except ValidationError:
        assert fault is not None, "a catalogue that keeps every rule was refused"
        return
    assert fault is None, f"a catalogue with the fault {fault!r} was accepted"
    assert model.model_validate(parsed.to_contract_dict()) == parsed
    assert parsed.to_contract_dict() == cat
