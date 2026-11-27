"""sample-asset-zero, the contract of sample-asset-one: a sample night's photo as the app reads it. The web builds a
reveal from a SourceAsset and its solve; an upload's comes in its receipt, which only an upload has, so the sample's
comes from its own route: the SourceAsset the ingest's own reader makes of the photo whose bytes the record names, a
public sample and never an upload, shown from the sample's own route, under the id its kept solve names. Both
languages read it alike: services/contracts/samples.py and packages/contracts/src/samples.ts, through the shared
fixture tests/fixtures/sample_asset_contract.json (ALBEDIAN_UPDATE_SAMPLE_ASSET_FIXTURE=1 writes it).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from services.contracts import samples

ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT / "tests" / "fixtures" / "sample_asset_contract.json"
NIGHT = samples.load_samples()[0]


def _asset(**change: Any) -> dict[str, Any]:
    """A contract sample of the night's photo as the reader writes a still's asset (its size is a sample's, not the
    photo's: this test reads no photo)."""
    asset: dict[str, Any] = {
        "id": f"src_{NIGHT.photo.sha256[:16]}",
        "title": NIGHT.title,
        "mediaKind": "image",
        "sha256": NIGHT.photo.sha256,
        "uri": f"/api/samples/{NIGHT.id}/original",
        "byteSize": NIGHT.photo.bytes,
        "mimeType": NIGHT.photo.mime_type,
        "width": 4000,
        "height": 3000,
        "durationMs": 0,
        "videoCodec": "jpeg",
        "audioStreams": [],
        "privacyClass": "public_sample",
        "immutable": True,
    }
    asset.update(change)
    return asset


def _accepted(value: dict[str, Any]) -> bool:
    try:
        samples.SampleAsset.model_validate(value)
    except ValidationError:
        return False
    return True


def _matches(value: dict[str, Any]) -> bool:
    return samples.sample_asset_matches(NIGHT, samples.SampleAsset.model_validate(value))


def test_the_photo_as_the_reader_makes_it_is_a_public_sample_of_this_night() -> None:
    asset = samples.SampleAsset.model_validate(_asset())
    assert asset.privacy_class == "public_sample"
    assert samples.sample_asset_matches(NIGHT, asset)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"privacyClass": "private"}, "a sample's photo is a public sample"),
        ({"privacyClass": "consented_demo"}, "a sample's photo is a public sample"),
        ({"mediaKind": "video"}, "a sample night is a photo"),
        ({"uri": f"/api/ingest/{NIGHT.photo.sha256}/original"}, "a sample's photo is shown from its own route"),
        ({"uri": f"/api/samples/{NIGHT.id}/original\n"}, "a sample's photo is shown from its own route"),
        ({"uri": "/api/samples/night-RP-B-12/original"}, "a sample's photo is shown from its own route"),
        ({"uri": f"/api/samples/{NIGHT.id}/solution"}, "a sample's photo is shown from its own route"),
        ({"id": "src_0000000000000000"}, "a sample's asset id is the one its solve names"),
        ({"id": f"src_{NIGHT.photo.sha256[:15]}"}, "a sample's asset id is the one its solve names"),
    ],
)
def test_an_asset_that_is_not_a_sample_photo_is_refused(change: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        samples.SampleAsset.model_validate(_asset(**change))


@pytest.mark.parametrize(
    "change",
    [
        {"sha256": "0" * 64, "id": "src_" + "0" * 16},
        {"byteSize": NIGHT.photo.bytes + 1},
        {"byteSize": None},
        {"mimeType": "image/png"},
        {"uri": "/api/samples/night-other/original"},
    ],
)
def test_a_sample_photo_of_other_bytes_or_another_night_is_not_this_nights(change: dict[str, Any]) -> None:
    value = {key: item for key, item in _asset(**change).items() if item is not None}
    assert not _matches(value)


def _cases() -> list[dict[str, Any]]:
    rows = [
        ("the night's photo", _asset()),
        ("an upload's privacy", _asset(privacyClass="private")),
        ("a demo's privacy", _asset(privacyClass="consented_demo")),
        ("a clip", _asset(mediaKind="video")),
        ("an upload's route", _asset(uri=f"/api/ingest/{NIGHT.photo.sha256}/original")),
        ("its route with a newline after it", _asset(uri=f"/api/samples/{NIGHT.id}/original\n")),
        ("a night id in capitals", _asset(uri="/api/samples/night-RP-B-12/original")),
        ("the solution's route", _asset(uri=f"/api/samples/{NIGHT.id}/solution")),
        ("another hash's id", _asset(id="src_0000000000000000")),
        ("an unknown key, dropped as every SourceAsset drops it", _asset(extra=1)),
        ("another night's route", _asset(uri="/api/samples/night-other/original")),
        ("one byte more", _asset(byteSize=NIGHT.photo.bytes + 1)),
        ("another type", _asset(mimeType="image/png")),
    ]
    cases = []
    for what, value in rows:
        accepted = _accepted(value)
        cases.append({"what": what, "value": value, "accepted": accepted,
                      "matchesTheNight": _matches(value) if accepted else False})
    return cases


def test_both_languages_answer_alike_from_the_shared_fixture() -> None:
    cases = _cases()
    assert {c["what"]: (c["accepted"], c["matchesTheNight"]) for c in cases} == {
        "the night's photo": (True, True),
        "an upload's privacy": (False, False),
        "a demo's privacy": (False, False),
        "a clip": (False, False),
        "an upload's route": (False, False),
        "its route with a newline after it": (False, False),
        "a night id in capitals": (False, False),
        "the solution's route": (False, False),
        "another hash's id": (False, False),
        "an unknown key, dropped as every SourceAsset drops it": (True, True),
        "another night's route": (True, False),
        "one byte more": (True, False),
        "another type": (True, False),
    }
    text = json.dumps({"about": "sample-asset-zero: a sample night's photo as the app reads it, answered alike",
                       "night": json.loads(samples.SAMPLES_PATH.read_text(encoding="utf-8"))["samples"][0],
                       "cases": cases}, indent=1, ensure_ascii=True) + "\n"
    if os.environ.get("ALBEDIAN_UPDATE_SAMPLE_ASSET_FIXTURE") == "1":
        CASES_PATH.write_text(text, encoding="utf-8")
    assert CASES_PATH.read_text(encoding="utf-8") == text, "regenerate with ALBEDIAN_UPDATE_SAMPLE_ASSET_FIXTURE=1"
