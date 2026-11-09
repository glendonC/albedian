"""Render-layer contract: Pydantic rules, properties, and the fixture shared with the Zod schema.

`tests/fixtures/render_layer_contract.json` is written from `build_render_layer_samples()` and
parsed by packages/contracts/src/__tests__/render-layer.test.ts. Regenerate it after a deliberate
contract change with:

    ALBEDIAN_UPDATE_CONTRACT_FIXTURE=1 uv run pytest tests/integration/test_render_layer_contract.py
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from services.contracts import (
    ALWAYS_SIMULATED_RENDER_LAYER_KINDS,
    EVIDENCE_CLASSES,
    RENDER_LAYER_KINDS,
    RenderLayer,
    validate_render_layer_set,
)
from services.contracts import render_layer as rl
from services.science.ephemeris import MOON_METHOD
from services.science.wcs_models import EVIDENCE_CLASSES as FINDING_EVIDENCE_CLASSES

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "render_layer_contract.json"
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_CONTRACT_FIXTURE"


def _layer(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "scene.layer",
        "kind": "catalog_stars",
        "title": "Catalog stars",
        "evidenceClass": "simulated",
        "disclosure": "Catalog positions and magnitudes; star images are a modeled point-spread function.",
        "sources": ["Gaia DR3 (Gaia Collaboration 2023)"],
    }
    base.update(overrides)
    return base


def build_render_layer_samples() -> dict[str, Any]:
    valid = [
        RenderLayer.model_validate(layer).to_contract_dict()
        for layer in [
            _layer(
                id="cislunar.catalog-stars",
                sources=["Gaia DR3 (Gaia Collaboration 2023)", "Hipparcos 2007 (van Leeuwen 2007)"],
            ),
            _layer(
                id="cislunar.earth",
                kind="solar_system_body",
                title="Earth",
                disclosure="NASA imagery on a globe placed and lit for composition, not at an ephemeris position.",
                sources=["NASA Blue Marble Next Generation, November 2004", "NASA Black Marble 2016"],
            ),
            _layer(
                id="sky.moon",
                kind="solar_system_body",
                title="Moon",
                evidenceClass="predicted",
                disclosure="Ephemeris position for the recording time and place; not recovered from camera pixels.",
                sources=["Meeus (1998) chapter 47"],
            ),
            _layer(
                id="capture.original",
                kind="sky_background",
                title="Your recording",
                evidenceClass="observed",
                disclosure="Original pixels of your recording.",
                sources=[],
                sourceAssetId="asset-synthetic-night",
            ),
            _layer(
                id="capture.registered",
                kind="sky_background",
                title="Your recording on the sky",
                evidenceClass="catalog_matched",
                disclosure="Your recording placed on the sky by an independently verified plate solution.",
                sources=["Gaia DR3 (Gaia Collaboration 2023)"],
                sourceAssetId="asset-synthetic-night",
                observationSolutionId="solution-synthetic-night",
            ),
            _layer(
                id="post.camera-optics",
                kind="camera_optics",
                title="Camera optics",
                disclosure="Modeled lens glare, chromatic aberration, streaks and vignette.",
                sources=[],
            ),
            _layer(
                id="sky.horizon",
                kind="sky_overlay",
                title="Horizon and compass points",
                evidenceClass="predicted",
                disclosure="Computed for the capture's place and time from sidereal time and precession; not seen in the recording.",
                sources=["IAU 1982 sidereal time (Aoki et al. 1982)", "IAU 1976 precession (Lieske et al. 1977)"],
            ),
        ]
    ]
    invalid = [
        {"rule": "observed layer without sourceAssetId", "layer": _layer(evidenceClass="observed")},
        {
            "rule": "catalog_matched layer without observationSolutionId",
            "layer": _layer(evidenceClass="catalog_matched", sourceAssetId="asset-synthetic-night"),
        },
        {
            "rule": "camera_optics layer not simulated",
            "layer": _layer(kind="camera_optics", evidenceClass="observed", sourceAssetId="asset-synthetic-night"),
        },
        {"rule": "procedural layer not simulated", "layer": _layer(kind="procedural", evidenceClass="predicted")},
        {
            "rule": "sky_overlay layer observed",
            "layer": _layer(kind="sky_overlay", evidenceClass="observed", sourceAssetId="asset-synthetic-night"),
        },
        {
            "rule": "sky_overlay layer catalog_matched",
            "layer": _layer(
                kind="sky_overlay",
                evidenceClass="catalog_matched",
                sourceAssetId="asset-synthetic-night",
                observationSolutionId="solution-synthetic-night",
            ),
        },
        {"rule": "blank disclosure", "layer": _layer(disclosure="   ")},
        {"rule": "disclosure too long", "layer": _layer(disclosure="x" * (rl.DISCLOSURE_MAX_LENGTH + 1))},
        {"rule": "blank title", "layer": _layer(title="")},
        {"rule": "too many sources", "layer": _layer(sources=[f"Source {i}" for i in range(rl.MAX_SOURCES + 1)])},
        {"rule": "missing sources", "layer": {k: v for k, v in _layer().items() if k != "sources"}},
        {"rule": "unknown kind", "layer": _layer(kind="hologram")},
        {"rule": "unknown evidence class", "layer": _layer(evidenceClass="artistic")},
        {"rule": "id outside the slug pattern", "layer": _layer(id="Cislunar Earth")},
        {"rule": "null optional field", "layer": _layer(sourceAssetId=None)},
        {"rule": "unknown field", "layer": _layer(confidence=0.9)},
    ]
    invalid_sets = [
        {"rule": "repeated id", "layers": [_layer(), _layer(title="Catalog stars again")]},
    ]
    return {
        "kinds": list(RENDER_LAYER_KINDS),
        "evidenceClasses": list(EVIDENCE_CLASSES),
        "alwaysSimulatedKinds": list(ALWAYS_SIMULATED_RENDER_LAYER_KINDS),
        "computedOnlyKinds": list(rl.COMPUTED_ONLY_RENDER_LAYER_KINDS),
        "limits": {
            "titleMaxLength": rl.TITLE_MAX_LENGTH,
            "disclosureMaxLength": rl.DISCLOSURE_MAX_LENGTH,
            "sourceMaxLength": rl.SOURCE_MAX_LENGTH,
            "maxSources": rl.MAX_SOURCES,
            "idPattern": rl.RENDER_LAYER_ID_PATTERN,
        },
        "valid": valid,
        "invalid": invalid,
        "invalidSets": invalid_sets,
    }


def test_render_layer_fixture_is_current() -> None:
    samples = build_render_layer_samples()
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        FIXTURE.write_text(json.dumps(samples, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    stored = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert samples == stored, (
        f"{FIXTURE.name} no longer matches the Python render-layer contract; regenerate it with "
        f"{UPDATE_FIXTURE_ENV}=1 uv run pytest tests/integration/test_render_layer_contract.py"
    )


def test_the_sample_moon_layer_names_the_model_the_ephemeris_uses() -> None:
    # The predicted Moon is Meeus (1998) chapter 47 (services/science/ephemeris.py); nothing uses DE440
    samples = build_render_layer_samples()
    moon = next(layer for layer in samples["valid"] if layer["id"] == "sky.moon")
    assert moon["sources"] == ["Meeus (1998) chapter 47"]
    assert MOON_METHOD.startswith(moon["sources"][0])
    assert "DE440" not in json.dumps(samples)


def test_render_layer_evidence_classes_match_the_finding_contract() -> None:
    assert EVIDENCE_CLASSES == FINDING_EVIDENCE_CLASSES


def test_every_invalid_sample_is_rejected_and_every_valid_one_round_trips() -> None:
    samples = build_render_layer_samples()
    for sample in samples["valid"]:
        assert RenderLayer.model_validate(sample).to_contract_dict() == sample
    for case in samples["invalid"]:
        with pytest.raises(ValidationError):
            RenderLayer.model_validate(case["layer"])
    for case in samples["invalidSets"]:
        with pytest.raises(ValueError):
            validate_render_layer_set(case["layers"])


def test_whitespace_around_text_fields_is_trimmed_but_ids_are_not() -> None:
    layer = RenderLayer.model_validate(_layer(title="  Earth  ", disclosure=" Composed. ", sources=[" NASA "]))
    assert (layer.title, layer.disclosure, layer.sources) == ("Earth", "Composed.", ("NASA",))
    with pytest.raises(ValidationError):
        RenderLayer.model_validate(_layer(id=" scene.layer"))


ID_RE = re.compile(rl.RENDER_LAYER_ID_PATTERN)


VALID_FIELDS: dict[str, st.SearchStrategy[Any]] = {
    "id": st.from_regex(ID_RE, fullmatch=True),
    "kind": st.sampled_from(RENDER_LAYER_KINDS),
    "title": st.text(alphabet="abcXYZ ", min_size=1, max_size=rl.TITLE_MAX_LENGTH).filter(str.strip),
    "evidenceClass": st.sampled_from(EVIDENCE_CLASSES),
    "disclosure": st.text(alphabet="ab ·.", min_size=1, max_size=rl.DISCLOSURE_MAX_LENGTH).filter(str.strip),
    "sources": st.lists(st.text(alphabet="ab ", min_size=1, max_size=8).filter(str.strip), max_size=rl.MAX_SOURCES),
}
VALID_OPTIONAL_FIELDS: dict[str, st.SearchStrategy[Any]] = {
    "sourceAssetId": st.text(alphabet="asset-0123", min_size=1, max_size=6),
    "observationSolutionId": st.text(alphabet="sol-0123", min_size=1, max_size=6),
}
FIELD_FAULTS: dict[str, st.SearchStrategy[Any]] = {
    "id": st.sampled_from(["", "A", "a..b", "-a", " a"]),
    "kind": st.just("hologram"),
    "title": st.sampled_from(["", "   ", "t" * (rl.TITLE_MAX_LENGTH + 1)]),
    "evidenceClass": st.just("artistic"),
    "disclosure": st.sampled_from(["", " ", "d" * (rl.DISCLOSURE_MAX_LENGTH + 1)]),
    "sources": st.sampled_from([[" "], ["s"] * (rl.MAX_SOURCES + 1)]),
    "sourceAssetId": st.just(""),
    "observationSolutionId": st.just(""),
}


@st.composite
def layer_strategy(draw: st.DrawFn) -> dict[str, Any]:
    """A layer with valid fields, then on a coin flip one field replaced by a malformed value.

    The evidence rules are left to chance (the class and the optional ids are drawn freely), so
    the property meets layers that are well formed but still break a rule.
    """
    layer = draw(st.fixed_dictionaries(VALID_FIELDS, optional=VALID_OPTIONAL_FIELDS))
    if draw(st.booleans()):
        field = draw(st.sampled_from(sorted(FIELD_FAULTS)))
        layer[field] = draw(FIELD_FAULTS[field])
    return layer


def _expected_valid(layer: dict[str, Any]) -> bool:
    """The contract rules restated independently of the model, as the property's oracle."""
    text_ok = (
        bool(ID_RE.fullmatch(layer["id"]))
        and 1 <= len(layer["title"].strip()) <= rl.TITLE_MAX_LENGTH
        and 1 <= len(layer["disclosure"].strip()) <= rl.DISCLOSURE_MAX_LENGTH
        and len(layer["sources"]) <= rl.MAX_SOURCES
        and all(1 <= len(s.strip()) <= rl.SOURCE_MAX_LENGTH for s in layer["sources"])
        and all(len(layer.get(k, "x")) >= 1 for k in ("sourceAssetId", "observationSolutionId"))
    )
    if not text_ok or layer["kind"] not in RENDER_LAYER_KINDS or layer["evidenceClass"] not in EVIDENCE_CLASSES:
        return False
    evidence = layer["evidenceClass"]
    if evidence in ("observed", "catalog_matched") and "sourceAssetId" not in layer:
        return False
    if evidence == "catalog_matched" and "observationSolutionId" not in layer:
        return False
    if layer["kind"] in rl.COMPUTED_ONLY_RENDER_LAYER_KINDS and evidence not in ("predicted", "simulated"):
        return False
    return not (layer["kind"] in ALWAYS_SIMULATED_RENDER_LAYER_KINDS and evidence != "simulated")


@settings(max_examples=300, deadline=None)
@given(layer=layer_strategy())
# A computed overlay is predicted or simulated, never measured; seeded so every run meets each case
@example(layer=_layer(kind="sky_overlay", evidenceClass="predicted"))
@example(layer=_layer(kind="sky_overlay", evidenceClass="observed", sourceAssetId="a"))
@example(layer=_layer(kind="sky_overlay", evidenceClass="catalog_matched", sourceAssetId="a", observationSolutionId="s"))
def test_pbt_layer_1_model_accepts_exactly_the_layers_the_rules_allow(layer: dict[str, Any]) -> None:
    """PBT-LAYER-1 (Python half): validation succeeds exactly when the contract rules hold."""
    try:
        parsed = RenderLayer.model_validate(layer)
    except ValidationError:
        assert not _expected_valid(layer)
        return
    assert _expected_valid(layer)
    # Round trip: the wire form parses back to the same layer
    assert RenderLayer.model_validate(parsed.to_contract_dict()) == parsed
