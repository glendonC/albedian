"""OpenNGC catalog constants, cached table access, designation formatting, and reference builders."""

from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any

OPENNGC_PATH = Path(__file__).resolve().parents[2] / "apps" / "web" / "public" / "catalog" / "deepsky" / "openngc.json"
OPENNGC_PROVIDER = "OpenNGC"
OPENNGC_CITATION = "OpenNGC, Mattia Verga (github.com/mattiaverga/OpenNGC), CC BY-SA 4.0"
OPENNGC_LICENCE_NOTE = "OpenNGC CC BY-SA 4.0"
DUPLICATE_TYPE = "Dup"
NONEXISTENT_TYPE = "NonEx"
# The larger of OpenNGC's two printed precisions on the sky: 0.01 s of right ascension (0.15 arcsec at the equator)
POSITION_PRECISION_ARCSEC = 0.15
VERIFICATION_METHOD = (
    "OpenNGC catalog position projected through the solved WCS, which passed held-out verification; not measured "
    "in the image"
)
LIMITATIONS = (
    "Catalog centre from OpenNGC (J2000), projected through the solved WCS: nothing about the object is measured "
    "in the image.",
    "An extended object is marked at its catalog centre; its outline is not drawn from the image.",
    "Coordinates printed to 0.15 arcsec in OpenNGC; it gives no accuracy per row.",
    "The centre of a large or diffuse object is a convention, known far less precisely than its printed coordinates.",
)
_DESIGNATION = re.compile(r"^(NGC|IC)0*(\d.*)$")
_CROSS_REFERENCE_COLUMNS = (("NGC", "ngc"), ("IC", "ic"))


@lru_cache(maxsize=1)
def openngc_table() -> dict[str, Any]:
    """The baked OpenNGC table, read once."""
    return json.loads(OPENNGC_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=4)
def _index_columns(columns: tuple[str, ...]) -> dict[str, int]:
    return {column: i for i, column in enumerate(columns)}


def table_and_columns() -> tuple[dict[str, Any], dict[str, int]]:
    """Return the active OpenNGC table and its cached column-name-to-index map."""
    table = openngc_table()
    return table, _index_columns(tuple(table["columns"]))


def finding_id(name: str) -> str:
    """`finding-openngc-` and the row's name, with a space (a component suffix such as ' NED01') as a hyphen."""
    return "finding-openngc-" + name.replace(" ", "-")


def display_name(name: str, common_names: list[str]) -> str:
    """The first common name, else the NGC or IC designation written plainly ('NGC 1976'), else the row's name."""
    if common_names:
        return common_names[0]
    match = _DESIGNATION.match(name)
    return f"{match.group(1)} {match.group(2)}" if match else name


def duplicated_name(row: list[Any], at: dict[str, int]) -> str | None:
    """The name of the row a `Dup` row stands for, from its NGC or IC cross reference, when the table holds it."""
    names = {r[at["name"]] for r in openngc_table()["rows"]}
    for prefix, column in _CROSS_REFERENCE_COLUMNS:
        for value in row[at[column]] or []:
            candidate = f"{prefix}{value}"
            if candidate in names:
                return candidate
    return None


def _candidate_rows() -> list[list[Any]]:
    """Every row that can be emitted: positioned, existing, and not a duplicate of another row."""
    table, at = table_and_columns()
    return [
        row
        for row in table["rows"]
        if row[at["type"]] not in (DUPLICATE_TYPE, NONEXISTENT_TYPE) and row[at["raDeg"]] is not None
    ]


def catalog_reference(row: list[Any]) -> dict[str, Any]:
    """The contract's CatalogReference for one OpenNGC row (packages/contracts/src/astronomy.ts)."""
    table, at = table_and_columns()
    name = row[at["name"]]
    reference: dict[str, Any] = {
        "id": f"openngc-{name.replace(' ', '-')}",
        "provider": OPENNGC_PROVIDER,
        "releaseVersion": table["release"],
        "catalogSourceId": name,
        "commonName": display_name(name, row[at["commonNames"]] or []),
        "constellation": row[at["constellation"]],
        "objectType": "deep_sky",
        "coordinateEpoch": "J2000.0",
        "raDeg": float(row[at["raDeg"]]),
        "decDeg": float(row[at["decDeg"]]),
        "citation": OPENNGC_CITATION,
    }
    if row[at["vMag"]] is not None:
        reference["apparentMagnitude"] = float(row[at["vMag"]])
    return reference


__all__ = [
    "DUPLICATE_TYPE",
    "LIMITATIONS",
    "NONEXISTENT_TYPE",
    "OPENNGC_CITATION",
    "OPENNGC_LICENCE_NOTE",
    "OPENNGC_PATH",
    "OPENNGC_PROVIDER",
    "POSITION_PRECISION_ARCSEC",
    "VERIFICATION_METHOD",
    "catalog_reference",
    "display_name",
    "duplicated_name",
    "finding_id",
    "openngc_table",
    "table_and_columns",
]
