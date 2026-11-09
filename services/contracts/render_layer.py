"""Render layers: the Python mirror of `RenderLayerSchema` in packages/contracts/src/render-layer.ts.

A render layer says what one layer of the cosmos renderer draws and what kind of evidence its
pixels carry, so the UI can disclose it (AGENTS.md section 1). Field names, limits and rules
match the Zod schema; tests/fixtures/render_layer_contract.json is written from this module and
parsed by the TypeScript tests, so a change on either side fails one of them.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

EvidenceClass = Literal["observed", "catalog_matched", "predicted", "simulated"]

RenderLayerKind = Literal[
    "sky_background",
    "catalog_stars",
    "solar_system_body",
    "atmosphere",
    "dust_volume",
    "capture_splats",
    "procedural",
    "camera_optics",
    "sky_overlay",
]

EVIDENCE_CLASSES: tuple[str, ...] = get_args(EvidenceClass)
RENDER_LAYER_KINDS: tuple[str, ...] = get_args(RenderLayerKind)

# Kinds that never show measured data, so they can only be disclosed as simulated
ALWAYS_SIMULATED_RENDER_LAYER_KINDS: tuple[str, ...] = ("procedural", "camera_optics")

# Kinds computed rather than measured, so they can only be disclosed as predicted or simulated
COMPUTED_ONLY_RENDER_LAYER_KINDS: tuple[str, ...] = ("sky_overlay",)

TITLE_MAX_LENGTH = 80
DISCLOSURE_MAX_LENGTH = 240
SOURCE_MAX_LENGTH = 200
MAX_SOURCES = 12
RENDER_LAYER_ID_PATTERN = r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$"

LayerId = Annotated[str, StringConstraints(pattern=RENDER_LAYER_ID_PATTERN)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX_LENGTH)]
Disclosure = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=DISCLOSURE_MAX_LENGTH)
]
SourceCitation = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=SOURCE_MAX_LENGTH)
]
Identifier = Annotated[str, StringConstraints(min_length=1)]

_OPTIONAL_ALIASES = ("sourceAssetId", "observationSolutionId")


class RenderLayer(BaseModel):
    """One drawn layer and its evidence class; serialized with the contract's camelCase names."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    id: LayerId
    kind: RenderLayerKind
    title: Title
    evidence_class: EvidenceClass = Field(alias="evidenceClass")
    # One line the UI shows beside the image: what is measured and what is composed or modeled
    disclosure: Disclosure
    # Datasets and imagery the layer draws from, as short citations
    sources: tuple[SourceCitation, ...] = Field(max_length=MAX_SOURCES)
    # The user's recording, required when the layer shows its pixels
    source_asset_id: Identifier | None = Field(default=None, alias="sourceAssetId")
    # The observation solution whose independently verified WCS a catalog match relies on
    observation_solution_id: Identifier | None = Field(default=None, alias="observationSolutionId")

    @model_validator(mode="before")
    @classmethod
    def _optional_fields_are_omitted_not_null(cls, data: Any) -> Any:
        # Zod's .optional() rejects null, so the wire format leaves an unknown value out
        if isinstance(data, dict):
            for alias in _OPTIONAL_ALIASES:
                if alias in data and data[alias] is None:
                    raise ValueError(f"{alias} is omitted when unknown, never null.")
        return data

    @model_validator(mode="after")
    def _evidence_rules(self) -> RenderLayer:
        if self.evidence_class in ("observed", "catalog_matched") and self.source_asset_id is None:
            raise ValueError(
                f"A {self.evidence_class} layer must trace to the SourceAsset whose pixels it shows."
            )
        if self.evidence_class == "catalog_matched" and self.observation_solution_id is None:
            raise ValueError("A catalog_matched layer must name the observation solution with the verified WCS.")
        if self.kind in ALWAYS_SIMULATED_RENDER_LAYER_KINDS and self.evidence_class != "simulated":
            raise ValueError(f"A {self.kind} layer shows no measured data and must be disclosed as simulated.")
        if self.kind in COMPUTED_ONLY_RENDER_LAYER_KINDS and self.evidence_class not in ("predicted", "simulated"):
            raise ValueError(
                f"A {self.kind} layer is computed, not measured, and must be disclosed as predicted or simulated."
            )
        return self

    def to_contract_dict(self) -> dict[str, Any]:
        """The layer as `RenderLayerSchema` expects it: camelCase keys, unknown optional fields left out."""
        data = self.model_dump(by_alias=True, exclude_none=True)
        data["sources"] = list(self.sources)
        return data


def validate_render_layer_set(layers: list[dict[str, Any]]) -> list[RenderLayer]:
    """Parses one scene's layers in draw order and rejects a repeated id, as `RenderLayerSetSchema` does."""
    parsed = [RenderLayer.model_validate(layer) for layer in layers]
    seen: set[str] = set()
    for layer in parsed:
        if layer.id in seen:
            raise ValueError(f"Render layer id {layer.id} appears more than once.")
        seen.add(layer.id)
    return parsed
