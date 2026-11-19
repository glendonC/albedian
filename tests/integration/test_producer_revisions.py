"""Results know which code made them (upload jobs REQ-JOB-6, design 3.5, platform-6; P-JOB-11).

Each kind's producer names the source files its results come from beside its revision. The guard below pins, per
producer, its revision and a digest of those files (each file's path and bytes, in the producer's order) in
`tests/fixtures/producer_digests.json`, and fails, naming the producer, when either differs. A unit that changes the
chain therefore touches the fixture in the same commit, and its diff shows the choice: a raised revision (results
change: every stored solve of an older revision runs again at its next start) or only a new digest (results do not
change, nothing runs again). `ALBEDIAN_UPDATE_PRODUCER_DIGESTS=1` rewrites the digests and never a revision. It hashes
source text, never float output, so it is the same on macOS and Linux.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
import pytest

from services.api import create_app
from services.ingest import jobs as ingest_jobs
from services.ingest import runner as job_runner
from services.ingest.runner import Producer, producer_digest

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "producer_digests.json"
UPDATE_ENV = "ALBEDIAN_UPDATE_PRODUCER_DIGESTS"


def _producers() -> dict[str, Producer]:
    create_app()  # registers the app's kinds
    return {kind.producer.name: kind.producer for kind in job_runner._KINDS.values()}


def _pinned() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8")) if FIXTURE.is_file() else {}


def guard_failures(producers: dict[str, Producer], pinned: dict[str, Any], root: Path) -> list[str]:
    """Each producer whose revision or digest differs from its pin, named, with what differs."""
    failures = []
    for name, producer in sorted(producers.items()):
        pin = pinned.get(name)
        if pin is None:
            failures.append(f"{name}: no pin; add it with {UPDATE_ENV}=1 and its revision")
            continue
        if producer.revision != pin["revision"]:
            failures.append(f"{name}: revision {producer.revision} in the code, {pin['revision']} pinned")
        if producer_digest(producer, root) != pin["digest"]:
            failures.append(f"{name}: its files changed; raise its revision if results change, else rerun with "
                            f"{UPDATE_ENV}=1")
    failures += [f"{name}: pinned, but no kind has this producer" for name in sorted(set(pinned) - set(producers))]
    return failures


def test_every_producers_revision_and_files_are_pinned() -> None:
    producers = _producers()
    pinned = _pinned()
    if os.environ.get(UPDATE_ENV) == "1":
        # The digests only: a revision is raised by hand, in the code and here, never by this switch
        updated = {name: {"revision": pinned.get(name, {}).get("revision", producer.revision),
                          "files": list(producer.files), "digest": producer_digest(producer, REPO)}
                   for name, producer in sorted(producers.items())}
        FIXTURE.write_text(json.dumps(updated, indent=1) + "\n", encoding="utf-8")
        pinned = updated
    assert guard_failures(producers, pinned, REPO) == []
    assert {name: pin["files"] for name, pin in pinned.items()} == {
        name: list(p.files) for name, p in producers.items()}


def test_the_solve_producer_lists_the_files_its_results_come_from() -> None:
    files = ingest_jobs.SOLVE_PRODUCER.files
    assert ingest_jobs.SOLVE_PRODUCER.name == "albedian.solve_chain" and ingest_jobs.SOLVE_PRODUCER.revision >= 1
    assert files[0] == "services/ingest/jobs.py" and len(set(files)) == len(files)
    for name in ("detector", "solver", "phone_photo_solver", "predicted_sky", "ephemeris", "catalog", "bounded_worker",
                 "pose_estimate", "camera_profiles", "horizon_solver"):
        assert f"services/science/{name}.py" in files, name
    assert all((REPO / path).is_file() for path in files)


def test_the_guard_names_a_raised_revision_a_changed_file_and_a_producer_no_kind_has(tmp_path: Path) -> None:
    for path in ("a.py", "b.py", "c.py"):
        (tmp_path / path).write_text(f"# {path}\n", encoding="utf-8")
    producer = Producer("albedian.test", 2, ("a.py", "b.py"))
    pin = {"revision": 2, "files": ["a.py", "b.py"], "digest": producer_digest(producer, tmp_path)}
    assert guard_failures({"albedian.test": producer}, {"albedian.test": pin}, tmp_path) == []
    raised = Producer("albedian.test", 3, ("a.py", "b.py"))
    assert guard_failures({"albedian.test": raised}, {"albedian.test": pin}, tmp_path) == [
        "albedian.test: revision 3 in the code, 2 pinned"]
    (tmp_path / "b.py").write_text("# b.py, one comment changed\n", encoding="utf-8")
    assert [f.split(":")[0] for f in guard_failures({"albedian.test": producer}, {"albedian.test": pin}, tmp_path)] == [
        "albedian.test"]
    assert guard_failures({}, {"albedian.test": pin}, tmp_path) == ["albedian.test: pinned, but no kind has this producer"]
    assert guard_failures({"albedian.test": producer}, {}, tmp_path)[0].startswith("albedian.test: no pin")


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(which=st.integers(min_value=0, max_value=10_000), offset=st.integers(min_value=0, max_value=10**9),
       flip=st.integers(min_value=1, max_value=255))
@example(which=0, offset=0, flip=1)
def test_p_job_11_one_changed_byte_of_a_listed_file_changes_the_digest_and_of_any_other_file_does_not(
        tmp_path_factory: pytest.TempPathFactory, which: int, offset: int, flip: int) -> None:
    producer = ingest_jobs.SOLVE_PRODUCER
    root = tmp_path_factory.mktemp("copy")
    for path in producer.files:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / path, root / path)
    unlisted = root / "services" / "science" / "wcs_models.py"
    shutil.copyfile(REPO / "services" / "science" / "wcs_models.py", unlisted)
    before = producer_digest(producer, root)
    assert before == producer_digest(producer, REPO), "the digest is of the files' paths and bytes alone"

    def changed(path: Path) -> None:
        data = bytearray(path.read_bytes())
        at = offset % len(data)
        data[at] ^= flip
        path.write_bytes(bytes(data))

    changed(unlisted)
    assert producer_digest(producer, root) == before, "a file the producer does not list never moves its digest"
    changed(root / producer.files[which % len(producer.files)])
    assert producer_digest(producer, root) != before


def test_the_digest_frames_each_file_so_moving_bytes_between_files_changes_it(tmp_path: Path) -> None:
    producer = Producer("albedian.test", 1, ("a.py", "b.py"))
    (tmp_path / "a.py").write_bytes(b"ab")
    (tmp_path / "b.py").write_bytes(b"c")
    before = producer_digest(producer, tmp_path)
    (tmp_path / "a.py").write_bytes(b"a")
    (tmp_path / "b.py").write_bytes(b"bc")
    assert producer_digest(producer, tmp_path) != before
    expected = hashlib.sha256()
    for path in producer.files:
        data = (tmp_path / path).read_bytes()
        expected.update(path.encode("utf-8") + b"\0" + len(data).to_bytes(8, "big") + data)
    assert producer_digest(producer, tmp_path) == expected.hexdigest()
