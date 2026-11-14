"""Parsers for CSV tables and pre-ephemeris metadata headers returned by JPL Horizons."""

from __future__ import annotations

_SOE_MARKER = "$$SOE"
_EOE_MARKER = "$$EOE"
_VECTOR_AXES = ("X", "Y", "Z")
_OBSERVER_FIELD_COLUMNS = (
    ("subObserverLonDeg", "ObsSub-LON"),
    ("subObserverLatDeg", "ObsSub-LAT"),
    ("subSolarLonDeg", "SunSub-LON"),
    ("subSolarLatDeg", "SunSub-LAT"),
    ("northPoleAngleDeg", "NP.ang"),
    ("northPoleRaDeg", "N.Pole-RA"),
    ("northPoleDecDeg", "N.Pole-DC"),
    ("rangeAu", "delta"),
    ("phaseAngleDeg", "S-T-O"),
    ("tdbMinusUtSeconds", "TDB-UT"),
    ("galacticLonDeg", "GlxLon"),
    ("galacticLatDeg", "GlxLat"),
)

__all__ = [
    "ephemeris_source",
    "header_value",
    "observer_checks",
    "parse_xyz_rows",
    "table",
]


def _header_lines(entry: dict) -> list[str]:
    return entry["response"]["result"].split(_SOE_MARKER)[0].splitlines()


def table(entry: dict) -> tuple[list[str], list[list[str]]]:
    """Column names and rows of a CSV Horizons table."""
    text = entry["response"]["result"]
    if _SOE_MARKER not in text:
        raise SystemExit(f"Horizons table missing in the response to {entry['url']}:\n{text[:2000]}")
    head, rest = text.split(_SOE_MARKER, 1)
    body = rest.split(_EOE_MARKER, 1)[0]
    header = [line for line in head.splitlines() if line.strip()][-2]
    names = [c.strip() for c in header.split(",")]
    rows = [[c.strip() for c in line.split(",")] for line in body.strip().splitlines()]
    return names, rows


def parse_xyz_rows(names: list[str], rows: list[list[str]]) -> list[list[float]]:
    """Extract [X, Y, Z] position vectors (km) from parsed Horizons CSV (names, rows)."""
    column = {name: i for i, name in enumerate(names)}
    axis_indices = tuple(column[axis] for axis in _VECTOR_AXES)
    return [[float(row[idx]) for idx in axis_indices] for row in rows]


def observer_checks(entry: dict) -> dict:
    names, rows = table(entry)
    if len(rows) != 1:
        raise SystemExit(f"expected one row from {entry['url']}, got {len(rows)}")
    row = dict(zip(names, rows[0]))
    return {field: float(row[column_name]) for field, column_name in _OBSERVER_FIELD_COLUMNS}


def header_value(entry: dict, label: str) -> str:
    for line in _header_lines(entry):
        if line.strip().startswith(label):
            return line.split(":", 1)[1].split("{")[0].strip()
    raise SystemExit(f"no '{label}' line in the response to {entry['url']}")


def ephemeris_source(entry: dict) -> str:
    """The planetary ephemeris Horizons names for the target, as in '{source: DE441}'."""
    for line in _header_lines(entry):
        if line.strip().startswith("Target body name") and "{source:" in line:
            return line.split("{source:", 1)[1].split("}", 1)[0].strip()
    raise SystemExit(f"no ephemeris source in the response to {entry['url']}")
