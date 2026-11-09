"""Python mirror of the constellation lines and boundaries contract in packages/contracts/src/constellations.ts (data-7b0).

d3-celestial (github.com/ofrohn/d3-celestial, BSD-3-Clause, Copyright (c) 2015, Olaf Frohn), its
data/constellations.lines.json and data/constellations.bounds.json, as scripts/bake_constellations_d3celestial.py
writes them to apps/web/public/catalog/constellations/. Neither is measured by this app, so the evidence kind is
'catalog-derived'. The lines are the IAU constellation figures with the d3-celestial author's edits, not an IAU
product; the boundaries are Davenhall and Leggett (1989), the Delporte boundaries the IAU adopted.

Both documents hold one entry per constellation, by the 88 IAU abbreviations with Serpens split into Caput ('Se1')
and Cauda ('Se2') as OpenNGC writes it (services/contracts/deep_sky.py), and d3-celestial's display rank (1 for the
most prominent figures, 3 for the faintest). A point is [right ascension, declination] in degrees, right ascension
in [0, 360).

- Lines {format, formatVersion, evidenceKind, release, equinox, constellations}: each entry's polylines join star
  positions of equinox J2000; a polyline has at least two points and never repeats a point at once.
- Boundaries {format, formatVersion, evidenceKind, release, vertexEquinox, edgeEquinox, constellations}: each entry
  has one closed ring (its last point is its first) of at least four points, no point repeated at once. The vertices
  are J2000 positions of corners defined at equinox B1875, and each edge runs along a constant right ascension or
  declination of B1875, not straight between the J2000 vertices: a straight segment can sit arcminutes off the true
  boundary. A layer that draws or tests an edge must densify it along B1875 right ascension or declination (precess
  both vertices to B1875, step the coordinate that changes, precess each step back to J2000).

Rules beyond each value's own range: codes are unique within a document. Completeness (all 89 entries) is a
property of the baked files, checked by tests/integration/test_constellations_d3celestial.py, not of the contract.
tests/fixtures/constellations_contract.json holds samples both sides of the contract judge alike.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, Strict, StringConstraints, model_validator

from services.contracts.deep_sky import DEEP_SKY_CONSTELLATIONS
from services.contracts.provenance import SHORT_TEXT_MAX_LENGTH, TEXT_PATTERN

CONSTELLATION_LINES_FORMAT = "albedian.constellation-lines"
CONSTELLATION_BOUNDARIES_FORMAT = "albedian.constellation-boundaries"
CONSTELLATION_FORMAT_VERSION = 1
CONSTELLATION_EVIDENCE_KIND = "catalog-derived"
CONSTELLATION_CODES = DEEP_SKY_CONSTELLATIONS
CONSTELLATION_RANK_MIN = 1
CONSTELLATION_RANK_MAX = 3
CONSTELLATION_LINES_EQUINOX = "J2000"
CONSTELLATION_BOUNDARY_VERTEX_EQUINOX = "J2000"
CONSTELLATION_BOUNDARY_EDGE_EQUINOX = "B1875"
CONSTELLATION_RING_MIN_POINTS = 4

ShortText = Annotated[str, Strict(), StringConstraints(max_length=SHORT_TEXT_MAX_LENGTH, pattern=TEXT_PATTERN)]
Rank = Annotated[int, Strict(), Field(ge=CONSTELLATION_RANK_MIN, le=CONSTELLATION_RANK_MAX)]
SkyPoint = tuple[
    Annotated[float, Strict(), Field(allow_inf_nan=False, ge=0, lt=360)],  # raDeg
    Annotated[float, Strict(), Field(allow_inf_nan=False, ge=-90, le=90)],  # decDeg
]


def _no_point_repeated_at_once(points: tuple[SkyPoint, ...], where: str) -> None:
    for i in range(1, len(points)):
        if points[i] == points[i - 1]:
            raise ValueError(f"{where}: point {i} repeats the point before it.")


def _unique_codes(codes: list[str]) -> None:
    seen: set[str] = set()
    for code in codes:
        if code in seen:
            raise ValueError(f"Constellation {code} appears more than once.")
        seen.add(code)


class ConstellationFigure(BaseModel):
    """One constellation's lines."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: Literal[CONSTELLATION_CODES]
    rank: Rank
    polylines: tuple[Annotated[tuple[SkyPoint, ...], Field(min_length=2)], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _rules(self) -> ConstellationFigure:
        for i, polyline in enumerate(self.polylines):
            _no_point_repeated_at_once(polyline, f"{self.code} polyline {i}")
        return self


class ConstellationBoundary(BaseModel):
    """One constellation's boundary ring."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: Literal[CONSTELLATION_CODES]
    rank: Rank
    ring: tuple[SkyPoint, ...] = Field(min_length=CONSTELLATION_RING_MIN_POINTS)

    @model_validator(mode="after")
    def _rules(self) -> ConstellationBoundary:
        if self.ring[0] != self.ring[-1]:
            raise ValueError(f"{self.code}: a boundary ring ends at its first point.")
        _no_point_repeated_at_once(self.ring, f"{self.code} ring")
        return self


class ConstellationLines(BaseModel):
    """apps/web/public/catalog/constellations/lines.json."""

    # Wire names only (formatVersion, evidenceKind), as the Zod schema reads them: no populate_by_name
    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal[CONSTELLATION_LINES_FORMAT]
    format_version: Annotated[int, Strict(), Field(ge=1, le=1)] = Field(alias="formatVersion")
    evidence_kind: Literal[CONSTELLATION_EVIDENCE_KIND] = Field(alias="evidenceKind")
    release: ShortText
    equinox: Literal[CONSTELLATION_LINES_EQUINOX]
    constellations: tuple[ConstellationFigure, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _rules(self) -> ConstellationLines:
        _unique_codes([entry.code for entry in self.constellations])
        return self

    def to_contract_dict(self) -> dict[str, Any]:
        """The document as the Zod schema expects it: camelCase keys, points as arrays."""
        return self.model_dump(mode="json", by_alias=True)


class ConstellationBoundaries(BaseModel):
    """apps/web/public/catalog/constellations/bounds.json."""

    # Wire names only (formatVersion, evidenceKind), as the Zod schema reads them: no populate_by_name
    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal[CONSTELLATION_BOUNDARIES_FORMAT]
    format_version: Annotated[int, Strict(), Field(ge=1, le=1)] = Field(alias="formatVersion")
    evidence_kind: Literal[CONSTELLATION_EVIDENCE_KIND] = Field(alias="evidenceKind")
    release: ShortText
    vertex_equinox: Literal[CONSTELLATION_BOUNDARY_VERTEX_EQUINOX] = Field(alias="vertexEquinox")
    edge_equinox: Literal[CONSTELLATION_BOUNDARY_EDGE_EQUINOX] = Field(alias="edgeEquinox")
    constellations: tuple[ConstellationBoundary, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _rules(self) -> ConstellationBoundaries:
        _unique_codes([entry.code for entry in self.constellations])
        return self

    def to_contract_dict(self) -> dict[str, Any]:
        """The document as the Zod schema expects it: camelCase keys, points as arrays."""
        return self.model_dump(mode="json", by_alias=True)
