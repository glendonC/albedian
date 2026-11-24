"""The upload store's reads never follow a link inside the store (platform-store-nofollow-1, AUDIT backend-security-b77
item L2): a link in place of `original.*`, of `record.json`, of a hash folder or of its two-letter folder reads as
absent, as `store_pointing.py` and `job_store.py` already read. The store's root may itself be a link (the lane
worktrees and the landing tree reach the store through a linked data folder), and still reads.

Synthetic files only, in temporary folders.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from services.ingest.store import IngestStore
from services.ingest.store_fs import find_stored_original, hash_folder, read_stored_record

SHA = hashlib.sha256(b"store-nofollow-probe").hexdigest()
RECORD = {"sourceAsset": {"sha256": SHA, "mediaKind": "image"}}


def _stored(root: Path, *, ext: str = "jpg") -> Path:
    """A store under `root` holding one original and its record, written as the store writes them."""
    store = IngestStore(root)
    folder = hash_folder(root, SHA)
    folder.mkdir(parents=True)
    (folder / f"original.{ext}").write_bytes(b"\xff\xd8 original bytes")
    store.write_record(SHA, RECORD)
    return folder


def _outside(tmp_path: Path) -> Path:
    """A real original and a real record outside any store, for links to point at."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "original.jpg").write_bytes(b"\xff\xd8 bytes from outside the store")
    (elsewhere / "record.json").write_text(json.dumps({"sourceAsset": {"sha256": "not-this-one"}}), encoding="utf-8")
    return elsewhere


def test_real_files_read_as_before(tmp_path: Path) -> None:
    root = tmp_path / "store"
    folder = _stored(root)
    assert find_stored_original(root, SHA) == folder / "original.jpg"
    assert read_stored_record(root, SHA) == RECORD
    store = IngestStore(root)
    assert store.original(SHA) == folder / "original.jpg"
    assert store.read_record(SHA) == RECORD


def test_nothing_stored_reads_as_absent(tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir()
    assert find_stored_original(root, SHA) is None
    assert read_stored_record(root, SHA) is None
    hash_folder(root, SHA).mkdir(parents=True)
    assert find_stored_original(root, SHA) is None
    assert read_stored_record(root, SHA) is None


def test_a_linked_original_reads_as_no_original(tmp_path: Path) -> None:
    root = tmp_path / "store"
    folder = _stored(root)
    elsewhere = _outside(tmp_path)
    (folder / "original.jpg").unlink()
    (folder / "original.jpg").symlink_to(elsewhere / "original.jpg")
    assert find_stored_original(root, SHA) is None
    assert IngestStore(root).original(SHA) is None


def test_a_linked_original_beside_a_real_one_is_passed_over(tmp_path: Path) -> None:
    root = tmp_path / "store"
    folder = _stored(root, ext="png")
    elsewhere = _outside(tmp_path)
    # "original.jpg" sorts before "original.png": the link must not win by its name
    (folder / "original.jpg").symlink_to(elsewhere / "original.jpg")
    assert find_stored_original(root, SHA) == folder / "original.png"


def test_a_folder_named_original_is_no_original(tmp_path: Path) -> None:
    root = tmp_path / "store"
    folder = _stored(root, ext="png")
    (folder / "original.jpg").mkdir()
    assert find_stored_original(root, SHA) == folder / "original.png"


def test_a_linked_record_reads_as_no_record(tmp_path: Path) -> None:
    root = tmp_path / "store"
    folder = _stored(root)
    elsewhere = _outside(tmp_path)
    (folder / "record.json").unlink()
    (folder / "record.json").symlink_to(elsewhere / "record.json")
    assert read_stored_record(root, SHA) is None
    assert IngestStore(root).read_record(SHA) is None


def test_a_folder_named_record_reads_as_no_record(tmp_path: Path) -> None:
    root = tmp_path / "store"
    folder = _stored(root)
    (folder / "record.json").unlink()
    (folder / "record.json").mkdir()
    assert read_stored_record(root, SHA) is None


@pytest.mark.parametrize("level", ["hash", "prefix"])
def test_a_linked_hash_folder_or_prefix_folder_reads_as_absent(tmp_path: Path, level: str) -> None:
    root = tmp_path / "store"
    folder = _stored(tmp_path / "real-elsewhere")
    root.mkdir()
    if level == "hash":
        (root / SHA[:2]).mkdir()
        (root / SHA[:2] / SHA).symlink_to(folder, target_is_directory=True)
    else:
        (root / SHA[:2]).symlink_to(folder.parent, target_is_directory=True)
    # The link resolves to a real store folder with a real original and record; neither is read through it
    assert (hash_folder(root, SHA) / "original.jpg").is_file()
    assert find_stored_original(root, SHA) is None
    assert read_stored_record(root, SHA) is None


def test_a_linked_store_root_still_reads(tmp_path: Path) -> None:
    real_root = tmp_path / "real-store"
    folder = _stored(real_root)
    linked_root = tmp_path / "linked-store"
    linked_root.symlink_to(real_root, target_is_directory=True)
    assert os.path.islink(linked_root)
    assert find_stored_original(linked_root, SHA) == linked_root / SHA[:2] / SHA / "original.jpg"
    assert find_stored_original(linked_root, SHA).resolve() == (folder / "original.jpg").resolve()
    assert read_stored_record(linked_root, SHA) == RECORD


def test_a_file_in_place_of_the_root_or_the_prefix_folder_reads_as_absent(tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.write_bytes(b"not a folder")
    assert find_stored_original(root, SHA) is None
    assert read_stored_record(root, SHA) is None
    other = tmp_path / "other"
    other.mkdir()
    (other / SHA[:2]).write_bytes(b"not a folder")
    assert find_stored_original(other, SHA) is None
    assert read_stored_record(other, SHA) is None


def test_two_real_originals_give_the_first_by_name_as_before(tmp_path: Path) -> None:
    root = tmp_path / "store"
    folder = _stored(root, ext="png")
    (folder / "original.jpg").write_bytes(b"\xff\xd8 a second original")
    assert find_stored_original(root, SHA) == folder / "original.jpg"


def _within(seconds: float, read: Callable[[], object]) -> object:
    """`read()`'s result, failing the test (never hanging it) when it does not come back within `seconds`."""
    box: dict[str, object] = {}
    worker = threading.Thread(target=lambda: box.setdefault("value", read()), daemon=True)
    worker.start()
    worker.join(seconds)
    assert not worker.is_alive(), "the read did not come back: it blocked"
    return box.get("value")


def test_a_fifo_named_record_reads_at_once_as_no_record(tmp_path: Path) -> None:
    root = tmp_path / "store"
    folder = _stored(root)
    (folder / "record.json").unlink()
    os.mkfifo(folder / "record.json")
    assert _within(5.0, lambda: read_stored_record(root, SHA)) is None
    assert _within(5.0, lambda: IngestStore(root).read_record(SHA)) is None


def test_a_fifo_named_original_reads_at_once_as_no_original(tmp_path: Path) -> None:
    root = tmp_path / "store"
    folder = _stored(root, ext="png")
    os.mkfifo(folder / "original.jpg")
    assert _within(5.0, lambda: find_stored_original(root, SHA)) == folder / "original.png"
    (folder / "original.png").unlink()
    assert _within(5.0, lambda: find_stored_original(root, SHA)) is None
