"""first-use-sample-zero, the contract of first-use-sample-one (DECIDED-BY-LEAD 25 Nov 03:22 and 03:43): the sample
night's tracked record. It names the public photo as the corpus photos come (its entry in data/downloads.json, fetched
from its own recorded source and nothing else), its credit and licence, that it is a sample, and where on this
machine the app's own solve of it is kept with its hash beside it; the solution itself is never tracked and never
cut. Both languages read the one record: services/contracts/samples.py and packages/contracts/src/samples.ts.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

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
    assert [s.id for s in record] == ["night-rp-b-12"]
    night = record[0]
    assert (night.kind, night.title) == ("sample", "Venus, Moon, Orion")


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
    # The shared cache folder is ignored whole (in a worktree it is a link, which git does not look past)
    ignored = subprocess.run(["git", "check-ignore", "--no-index", "-q", ".cache"], cwd=ROOT)
    assert ignored.returncode == 0, ".cache must be ignored by git"


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
