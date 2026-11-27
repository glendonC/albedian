"""first-use-sample-zero, the contract of first-use-sample-one (DECIDED-BY-LEAD 25 Nov 03:22 and 03:43): the sample
night's tracked record. It names the public photo as the corpus photos come (its entry in data/downloads.json, fetched
from its own recorded source and nothing else), its credit and licence, that it is a sample, and where on this
machine the app's own solve of it is kept with its hash beside it; the solution itself is never tracked and never
cut. Both languages read the one record: services/contracts/samples.py and packages/contracts/src/samples.ts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from services.contracts import samples

ROOT = Path(__file__).resolve().parents[2]
DOWNLOADS = json.loads((ROOT / "data" / "downloads.json").read_text(encoding="utf-8"))


def _download(path: str) -> dict:
    items = DOWNLOADS if isinstance(DOWNLOADS, list) else DOWNLOADS["downloads"]
    return next(item for item in items if item["path"] == path)


def test_the_record_holds_the_one_sample_night() -> None:
    record = samples.load_samples()
    assert [s.id for s in record] == ["night-rp-b-16"]
    night = record[0]
    assert (night.kind, night.title) == ("sample", "Starry sky 2")


def test_the_photo_is_the_corpus_photo_as_its_own_source_records_it() -> None:
    night = samples.load_samples()[0]
    entry = _download(night.photo.download_path)
    assert night.photo.sha256 == entry["sha256"]
    assert night.photo.bytes == entry["bytes"]
    assert night.credit.licence == entry["licence"] == "CC0"
    assert night.credit.licence_url == entry["licenceUrl"]
    assert night.credit.author in entry["source"]
    assert night.credit.source_url in entry["source"]
    # Fetched from nowhere but the photo's own recorded source
    assert entry["url"].startswith("https://upload.wikimedia.org/")


def test_the_solution_is_kept_on_this_machine_and_never_tracked() -> None:
    night = samples.load_samples()[0]
    for path in (night.solution.path, night.solution.sha256_path):
        assert path.startswith(".cache/samples/"), path
    # The tracked .gitignore holds the cache folder whole, so a fresh clone (CI's) ignores it before it exists
    rules = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".cache/" in rules or "/.cache" in rules


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "upload"},
        {"id": "Night 12"},
        {"photo": {"downloadPath": "data/uploads/x.jpg"}},
        {"photo": {"sha256": "abc"}},
        {"solution": {"path": "tests/fixtures/sample.json"}},
        {"extra": 1},
    ],
)
def test_a_record_that_breaks_the_rules_is_refused(bad: dict) -> None:
    raw = json.loads(samples.SAMPLES_PATH.read_text(encoding="utf-8"))["samples"][0]
    broken = {**raw, **{k: ({**raw[k], **v} if isinstance(v, dict) and isinstance(raw.get(k), dict) else v) for k, v in bad.items()}}
    with pytest.raises(ValidationError):
        samples.SampleNight.model_validate(broken)


# The odd inputs both languages must answer alike (AUDIT's version two), held in a fixture the TypeScript test reads
# (packages/contracts/src/__tests__/samples.test.ts); ALBEDIAN_UPDATE_SAMPLES_FIXTURE=1 writes it
CASES_PATH = ROOT / "tests" / "fixtures" / "samples_contract.json"


def _cases() -> list[dict]:
    night = json.loads(samples.SAMPLES_PATH.read_text(encoding="utf-8"))["samples"][0]

    def credit(url: str) -> dict:
        return {**night, "credit": {**night["credit"], "sourceUrl": url}}

    rows = [
        ("the tracked record", night),
        ("an address with a next-line character", credit("https://a\u0085b")),
        ("an address with a byte-order mark", credit("https://a﻿b")),
        ("an address with a space", credit("https://a b")),
        ("an address with a tab", credit("https://a\tb")),
        ("an address of printable ASCII", credit("https://example.org/a?b=c")),
        ("a title of 150 astral characters (300 UTF-16 units)", {**night, "title": "\U0001f320" * 150}),
        ("a title of 100 astral characters (200 UTF-16 units)", {**night, "title": "\U0001f320" * 100}),
        ("a title of 201 ASCII characters", {**night, "title": "a" * 201}),
        ("a title of 200 ASCII characters", {**night, "title": "a" * 200}),
    ]

    def accepted(value: dict) -> bool:
        try:
            samples.SampleNight.model_validate(value)
        except ValidationError:
            return False
        return True

    return [{"what": what, "value": value, "accepted": accepted(value)} for what, value in rows]


def test_the_odd_inputs_are_answered_alike_and_held_in_the_shared_fixture() -> None:
    cases = _cases()
    verdicts = {case["what"]: case["accepted"] for case in cases}
    assert verdicts == {
        "the tracked record": True,
        "an address with a next-line character": False,
        "an address with a byte-order mark": False,
        "an address with a space": False,
        "an address with a tab": False,
        "an address of printable ASCII": True,
        "a title of 150 astral characters (300 UTF-16 units)": False,
        "a title of 100 astral characters (200 UTF-16 units)": True,
        "a title of 201 ASCII characters": False,
        "a title of 200 ASCII characters": True,
    }
    text = json.dumps({"about": "first-use-sample-zero version two: odd inputs both languages answer alike", "cases": cases},
                      indent=1, ensure_ascii=True) + "\n"
    if os.environ.get("ALBEDIAN_UPDATE_SAMPLES_FIXTURE") == "1":
        CASES_PATH.write_text(text, encoding="utf-8")
    assert CASES_PATH.read_text(encoding="utf-8") == text, "regenerate with ALBEDIAN_UPDATE_SAMPLES_FIXTURE=1"
