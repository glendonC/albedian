"""Asset provenance check: every file the web app serves needs a record or a known, pinned gap.

`scripts/verify_provenance.py` walks the files git would publish under `apps/web/public/`
(tracked, or untracked and not ignored), checks each `PROVENANCE.json` against the contract and
the bytes it describes, and holds the unrecorded assets to an allowlist that can only shrink.
These tests build small git repositories in temporary folders, one per rule, then run the check
on this repository itself.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_check():
    spec = importlib.util.spec_from_file_location("verify_provenance", ROOT / "scripts" / "verify_provenance.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


vp = _load_check()

PUBLIC = "apps/web/public"
ESO = {
    "id": "eso-sample",
    "title": "ESO sample image",
    "url": "https://www.eso.org/public/images/",
    "retrieved": "2026-11-04",
    "license": {"spdx": "CC-BY-4.0", "name": "Creative Commons Attribution 4.0 International", "url": "https://creativecommons.org/licenses/by/4.0/"},
    "credit": "ESO",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Repo:
    """A throwaway git repository with a public asset folder and an allowlist."""

    def __init__(self, path: Path):
        self.root = path
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        (path / "scripts").mkdir()
        (path / "scripts" / "bake_sample.py").write_text("# a stand-in bake script\n")
        self.gaps: list[dict[str, str]] = []
        self.baseline: dict[str, str] = {}

    def write(self, rel: str, data: bytes) -> str:
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return _sha(data)

    def record(self, folder: str, files: list[dict[str, Any]] | None = None, file_sets: list[dict[str, Any]] | None = None) -> None:
        record = {"format": "albedian.asset-provenance", "formatVersion": 1, "sources": [ESO], "files": files or [], "fileSets": file_sets or []}
        self.write(f"{folder}/PROVENANCE.json", (json.dumps(record, indent=2) + "\n").encode())

    def gap(self, rel: str, *, baseline: bool = True) -> None:
        self.gaps.append({"path": rel, "reason": "test gap"})
        if baseline:
            self.baseline[rel] = _sha((self.root / rel).read_bytes())

    def check(self):
        gaps_file = self.root / "scripts" / "provenance_gaps.json"
        gaps_file.write_text(json.dumps({"format": "albedian.asset-provenance-gaps", "formatVersion": 1, "gaps": self.gaps}) + "\n")
        return vp.check(self.root, gaps_path=gaps_file, baseline=self.baseline)


def _file(path: str, sha: str, **extra: Any) -> dict[str, Any]:
    return {"path": path, "sha256": sha, "sources": ["eso-sample"], "producedBy": {"method": "downloaded-as-is"}, **extra}


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path / "repo")


def _errors_matching(result, text: str) -> list[str]:
    return [e for e in result.errors if text in e]


def test_recorded_files_and_known_gaps_pass(repo: Repo):
    sha = repo.write(f"{PUBLIC}/textures/sky.jpg", b"sky")
    repo.record(f"{PUBLIC}/textures", files=[_file("sky.jpg", sha)])
    repo.write(f"{PUBLIC}/models/old.glb", b"model")
    repo.gap(f"{PUBLIC}/models/old.glb")
    result = repo.check()
    assert result.errors == []
    assert (result.recorded, result.gaps, result.records) == (1, 1, 1)


def test_a_new_file_without_a_record_fails_even_before_it_is_committed(repo: Repo):
    repo.write(f"{PUBLIC}/textures/new.png", b"new")
    result = repo.check()
    assert _errors_matching(result, f"{PUBLIC}/textures/new.png: no provenance record")


def test_gitignored_files_are_outside_the_check_but_counted(repo: Repo):
    repo.write(".gitignore", b"apps/web/public/private/\n")
    repo.write(f"{PUBLIC}/private/clip.mp4", b"owner's own clip")
    result = repo.check()
    assert result.errors == []
    assert result.ignored == 1


def test_a_recorded_file_whose_bytes_changed_fails(repo: Repo):
    sha = repo.write(f"{PUBLIC}/textures/sky.jpg", b"sky")
    repo.record(f"{PUBLIC}/textures", files=[_file("sky.jpg", sha)])
    repo.write(f"{PUBLIC}/textures/sky.jpg", b"sky, edited")
    assert _errors_matching(repo.check(), "sha256 differs")


def test_a_record_naming_a_missing_file_fails(repo: Repo):
    repo.record(f"{PUBLIC}/textures", files=[_file("gone.jpg", _sha(b"gone"))])
    assert _errors_matching(repo.check(), "names a file that is not there")


def test_a_record_the_contract_rejects_fails(repo: Repo):
    repo.write(f"{PUBLIC}/textures/PROVENANCE.json", b'{"format": "albedian.asset-provenance", "formatVersion": 1}\n')
    assert _errors_matching(repo.check(), "is not a valid provenance record")


def test_a_producing_script_must_exist(repo: Repo):
    sha = repo.write(f"{PUBLIC}/textures/lut.png", b"lut")
    produced = {"method": "script", "script": "scripts/bake_missing.py"}
    repo.record(f"{PUBLIC}/textures", files=[_file("lut.png", sha, producedBy=produced)])
    assert _errors_matching(repo.check(), "scripts/bake_missing.py, which does not exist")


def test_file_sets_cover_every_listed_file_and_pin_the_list(repo: Repo):
    folder = f"{PUBLIC}/catalog/stars/release"
    a = repo.write(f"{folder}/L0/0.core.bin", b"core")
    b = repo.write(f"{folder}/manifest.json", b"{}")
    sums = repo.write(f"{folder}/SHA256SUMS", f"{a}  L0/0.core.bin\n{b}  manifest.json\n".encode())
    file_set = {"checksums": "SHA256SUMS", "sha256": sums, "sources": ["eso-sample"], "producedBy": {"method": "script", "script": "scripts/bake_sample.py"}}
    repo.record(folder, file_sets=[file_set])
    result = repo.check()
    assert result.errors == []
    assert result.recorded == 3  # two listed files and the list itself

    repo.write(f"{folder}/L0/0.core.bin", b"core, rebaked")
    assert _errors_matching(repo.check(), "L0/0.core.bin: sha256 differs from SHA256SUMS")
    repo.write(f"{folder}/L0/0.core.bin", b"core")
    repo.write(f"{folder}/SHA256SUMS", f"{a}  L0/0.core.bin\n{b}  manifest.json\n{_sha(b'x')}  L0/1.core.bin\n".encode())
    result = repo.check()
    assert _errors_matching(result, "SHA256SUMS: sha256 differs from its record")
    assert _errors_matching(result, "L0/1.core.bin: listed in SHA256SUMS but not there")


def test_a_file_covered_twice_fails(repo: Repo):
    sha = repo.write(f"{PUBLIC}/textures/moon/albedo.jpg", b"moon")
    repo.record(f"{PUBLIC}/textures", files=[_file("moon/albedo.jpg", sha)])
    repo.record(f"{PUBLIC}/textures/moon", files=[_file("albedo.jpg", sha)])
    assert _errors_matching(repo.check(), "moon/albedo.jpg: covered twice")


def test_an_allowlisted_file_that_gains_a_record_must_leave_the_allowlist(repo: Repo):
    sha = repo.write(f"{PUBLIC}/textures/sky.jpg", b"sky")
    repo.record(f"{PUBLIC}/textures", files=[_file("sky.jpg", sha)])
    repo.gap(f"{PUBLIC}/textures/sky.jpg")
    result = repo.check()
    assert _errors_matching(result, "has a record now: remove it from the allowlist")


def test_the_allowlist_only_shrinks(repo: Repo):
    repo.write(f"{PUBLIC}/models/new.glb", b"new model")
    repo.gap(f"{PUBLIC}/models/new.glb", baseline=False)
    assert _errors_matching(repo.check(), "is not one of the known gaps; the allowlist only shrinks")


def test_an_allowlisted_file_whose_bytes_changed_needs_a_record(repo: Repo):
    repo.write(f"{PUBLIC}/models/old.glb", b"model")
    repo.gap(f"{PUBLIC}/models/old.glb")
    repo.write(f"{PUBLIC}/models/old.glb", b"model, replaced")
    assert _errors_matching(repo.check(), "changed since it was allowlisted")


def test_an_allowlist_entry_for_a_deleted_file_must_go(repo: Repo):
    repo.write(f"{PUBLIC}/models/old.glb", b"model")
    repo.gap(f"{PUBLIC}/models/old.glb")
    (repo.root / PUBLIC / "models" / "old.glb").unlink()
    assert _errors_matching(repo.check(), "is gone: remove it from the allowlist")


def test_the_record_file_itself_needs_no_record(repo: Repo):
    sha = repo.write(f"{PUBLIC}/textures/sky.jpg", b"sky")
    repo.record(f"{PUBLIC}/textures", files=[_file("sky.jpg", sha)])
    result = repo.check()
    assert result.errors == [] and result.records == 1


def test_this_repository_passes():
    """Real data: every file under apps/web/public has a record or a known, pinned gap."""
    result = vp.check(ROOT)
    assert result.errors == [], "\n".join(result.errors)
    assert result.files == result.recorded + result.gaps + result.records
    assert result.recorded > 7000  # the catalog star tiles alone are 7,672 files
    assert 0 < result.gaps <= len(vp.BASELINE_GAPS)
