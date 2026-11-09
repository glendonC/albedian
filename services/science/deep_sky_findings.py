"""OpenNGC objects inside a verified solved frame, as predicted findings.

A deep-sky object's catalog centre (OpenNGC v20260501 as `apps/web/public/catalog/deepsky/openngc.json` holds it,
J2000, CC BY-SA 4.0, credit OpenNGC and Mattia Verga) is projected through the solution's WCS, only when that WCS
passed held-out verification. An object whose centre lands inside the frame becomes a `predicted` finding: nothing
about it is measured in the image, not its light, its centre or its outline, so it is never `observed` or
`catalog_matched`.

- A `Dup` row stands for an object OpenNGC lists under another name: it is not emitted, and the object it names is
  emitted once, from its own row. `NonEx` rows and rows without a position are skipped.
- The magnitude is OpenNGC's V magnitude when it gives one. A row with only a B magnitude has none, since a
  magnitude without its band would be ambiguous.
- The constellation is OpenNGC's IAU abbreviation, as the catalog gives it.
- The position uncertainty is the precision OpenNGC prints its coordinates to (0.01 s of right ascension, 0.15
  arcsec on the equator, and 0.1 arcsec of declination). OpenNGC gives no per-row accuracy, and a limitation says so.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from services.science.wcs_models import CelestialFindingRecord, ObservationSolution

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
    "OpenNGC gives no positional accuracy per row; the uncertainty is the precision its coordinates are printed to.",
)
_DESIGNATION = re.compile(r"^(NGC|IC)0*(\d.*)$")


@lru_cache(maxsize=1)
def openngc_table() -> dict[str, Any]:
    """The baked OpenNGC table, read once."""
    return json.loads(OPENNGC_PATH.read_text(encoding="utf-8"))


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
    for prefix, column in (("NGC", "ngc"), ("IC", "ic")):
        for value in row[at[column]] or []:
            if f"{prefix}{value}" in names:
                return f"{prefix}{value}"
    return None


def _candidate_rows() -> list[list[Any]]:
    """Every row that can be emitted: positioned, existing, and not a duplicate of another row."""
    table = openngc_table()
    at = {column: i for i, column in enumerate(table["columns"])}
    return [
        row
        for row in table["rows"]
        if row[at["type"]] not in (DUPLICATE_TYPE, NONEXISTENT_TYPE) and row[at["raDeg"]] is not None
    ]


def catalog_reference(row: list[Any]) -> dict[str, Any]:
    """The contract's CatalogReference for one OpenNGC row (packages/contracts/src/astronomy.ts)."""
    table = openngc_table()
    at = {column: i for i, column in enumerate(table["columns"])}
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


def _project(solution: ObservationSolution, ra: np.ndarray, dec: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    wcs = solution.wcs
    batch = getattr(wcs, "world_to_pixel_batch", None)
    if batch is not None:
        x, y = batch(ra, dec)
        return np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    points = [wcs.world_to_pixel(float(r), float(d)) for r, d in zip(ra, dec, strict=True)]
    return np.array([p[0] for p in points], dtype=np.float64), np.array([p[1] for p in points], dtype=np.float64)


def predict_deep_sky_findings(solution: ObservationSolution, width: int, height: int) -> list[CelestialFindingRecord]:
    """The OpenNGC objects whose catalog centre the solution's verified WCS puts inside the `width` x `height`
    frame, as predicted findings. None when the WCS is absent or did not pass held-out verification."""
    if not solution.has_verified_wcs:
        return []
    table = openngc_table()
    at = {column: i for i, column in enumerate(table["columns"])}
    rows = _candidate_rows()
    ra = np.array([row[at["raDeg"]] for row in rows], dtype=np.float64)
    dec = np.array([row[at["decDeg"]] for row in rows], dtype=np.float64)
    x, y = _project(solution, ra, dec)
    inside = np.isfinite(x) & np.isfinite(y) & (x >= 0.0) & (x < width) & (y >= 0.0) & (y < height)
    findings = []
    for i in np.flatnonzero(inside):
        row = rows[int(i)]
        reference = catalog_reference(row)
        findings.append(
            CelestialFindingRecord(
                id=finding_id(row[at["name"]]),
                observation_solution_id=solution.id,
                evidence_class="predicted",
                title=reference["commonName"],
                subtitle=f"{table['types'][row[at['type']]]} · {row[at['constellation']]} · {OPENNGC_LICENCE_NOTE}",
                ra_deg=reference["raDeg"],
                dec_deg=reference["decDeg"],
                uncertainty_arcsec=POSITION_PRECISION_ARCSEC,
                deep_sky_reference=reference,
                verification_method=VERIFICATION_METHOD,
                limitations=LIMITATIONS,
                source_sha256=solution.source_sha256,
            )
        )
    return findings
