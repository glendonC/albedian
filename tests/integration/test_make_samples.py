"""first-use-sample-one's setup step (scripts/make_samples.py, DECIDED-BY-LEAD 25 Nov 03:43): for each sample night,
the photo comes only from its own recorded source and is used only when its bytes are the recorded ones; the app's own
solve of it is written whole into the cache with its SHA-256 beside it, made once and kept; nothing is written
anywhere else. The solve itself is stood in for here (the real one runs in the setup, through the upload path); a
temporary root stands for the repository."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from services.contracts.samples import load_samples

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("make_samples", ROOT / "scripts" / "make_samples.py")
assert spec is not None and spec.loader is not None
make_samples = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_samples)

PHOTO = b"\xff\xd8 a stand-in photo \xff\xd9"
SOLUTION = {"id": "sol_stand_in", "status": "calibrated", "findings": []}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    record = json.loads((ROOT / "data" / "samples.json").read_text(encoding="utf-8"))
    record["samples"][0]["photo"]["sha256"] = hashlib.sha256(PHOTO).hexdigest()
    record["samples"][0]["photo"]["bytes"] = len(PHOTO)
    (tmp_path / "data" / "samples.json").write_text(json.dumps(record), encoding="utf-8")
    return tmp_path


def _night(root: Path) -> Any:
    return load_samples(root / "data" / "samples.json")[0]


def _fetcher(body: bytes, calls: list[str]):
    def fetch(night: Any, root: Path) -> None:
        calls.append(night.photo.download_path)
        path = root / night.photo.download_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    return fetch


def test_it_fetches_the_photo_solves_it_and_keeps_the_solve_with_its_hash(root: Path) -> None:
    fetched: list[str] = []
    solved: list[bytes] = []
    night = _night(root)
    said = make_samples.make_ready(night, root, fetch=_fetcher(PHOTO, fetched), solve=lambda photo: solved.append(photo.read_bytes()) or (SOLUTION, make_samples.current_producer()))
    assert said == "made"
    assert fetched == [night.photo.download_path] and solved == [PHOTO]
    text = (root / night.solution.path).read_bytes()
    assert json.loads(text) == SOLUTION
    assert (root / night.solution.sha256_path).read_text(encoding="utf-8").strip() == hashlib.sha256(text).hexdigest()
    # Made once: a second run finds it whole and does nothing
    assert make_samples.make_ready(night, root, fetch=_fetcher(PHOTO, fetched), solve=lambda photo: pytest.fail("solved twice")) == "kept"
    assert fetched == [night.photo.download_path]


def test_a_photo_that_is_not_the_recorded_one_is_never_solved(root: Path) -> None:
    night = _night(root)
    with pytest.raises(make_samples.SampleNotReady):
        make_samples.make_ready(night, root, fetch=_fetcher(PHOTO + b"changed", []), solve=lambda photo: pytest.fail("solved a wrong photo"))
    assert not (root / night.solution.path).exists()


def test_a_kept_solve_whose_hash_fails_is_made_again(root: Path) -> None:
    night = _night(root)
    make_samples.make_ready(night, root, fetch=_fetcher(PHOTO, []), solve=lambda photo: (SOLUTION, make_samples.current_producer()))
    (root / night.solution.path).write_text("{}", encoding="utf-8")
    again: list[bool] = []
    assert make_samples.make_ready(night, root, fetch=_fetcher(PHOTO, []), solve=lambda photo: again.append(True) or (SOLUTION, make_samples.current_producer())) == "made"
    assert again == [True]


def test_nothing_is_written_outside_the_cache(root: Path) -> None:
    night = _night(root)
    make_samples.make_ready(night, root, fetch=_fetcher(PHOTO, []), solve=lambda photo: (SOLUTION, make_samples.current_producer()))
    written = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
    assert all(p == "data/samples.json" or p.startswith(".cache/") for p in written), written
    assert not (root / "data" / "uploads").exists()


def test_a_kept_solve_from_another_solve_revision_is_made_again(root: Path) -> None:
    # AUDIT's note on first-use-sample-one: the sample shows the solve an upload would get now, so a solve kept from
    # another revision of the solve chain is made again; one from this revision is kept
    night = _night(root)
    current = make_samples.current_producer()
    made_by = {"name": current["name"], "revision": current["revision"]}
    assert make_samples.make_ready(night, root, fetch=_fetcher(PHOTO, []), solve=lambda photo: (SOLUTION, made_by)) == "made"
    kept = root / make_samples.producer_path(night)
    assert json.loads(kept.read_text(encoding="utf-8")) == made_by
    assert make_samples.make_ready(night, root, fetch=_fetcher(PHOTO, []), solve=lambda photo: pytest.fail("made again")) == "kept"
    kept.write_text(json.dumps({**made_by, "revision": current["revision"] - 1}), encoding="utf-8")
    again: list[bool] = []
    assert make_samples.make_ready(night, root, fetch=_fetcher(PHOTO, []), solve=lambda photo: again.append(True) or (SOLUTION, made_by)) == "made"
    assert again == [True]


def test_a_kept_solve_with_no_producer_beside_it_is_made_again(root: Path) -> None:
    night = _night(root)
    make_samples.make_ready(night, root, fetch=_fetcher(PHOTO, []), solve=lambda photo: (SOLUTION, make_samples.current_producer()))
    (root / make_samples.producer_path(night)).unlink()
    assert make_samples.make_ready(night, root, fetch=_fetcher(PHOTO, []), solve=lambda photo: (SOLUTION, make_samples.current_producer())) == "made"
