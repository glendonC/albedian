#!/usr/bin/env python3
"""Check that every file the web app serves has a provenance record or a known, pinned gap.

Scope: every file git would publish under `apps/web/public/`, tracked or untracked but not
ignored, so a new asset fails before it is committed. Gitignored files there are counted and
left out: git never publishes them.

Each `PROVENANCE.json` must follow the contract (`services/contracts/provenance.py`, mirrored by
`AssetProvenanceRecordSchema` in `@albedian/contracts`), every file it names must be there with
the SHA-256 it gives, every file a SHA256SUMS list names likewise, and every producing script
must exist. No file may be covered twice.

Files with no record yet are listed in `scripts/provenance_gaps.json`, each with its reason. That
list may only shrink: every entry must be one of `BASELINE_GAPS` below, the assets that had no
record when the check arrived, pinned by checksum. An entry fails once its file has a record, is
gone, or has changed bytes (a changed asset needs a record, not a gap).

Usage: uv run python scripts/verify_provenance.py, or pnpm verify:provenance. `uv run pytest` runs
it on this repository through tests/integration/test_asset_provenance.py.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, ValidationError

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.contracts.provenance import (  # noqa: E402
    ASSET_PROVENANCE_RECORD_FILENAME,
    AssetProvenanceRecord,
    parse_sha256sums,
    sha256_file,
)

PUBLIC_REL = "apps/web/public"
DEFAULT_GAPS = ROOT_DIR / "scripts" / "provenance_gaps.json"
GAPS_FORMAT = "albedian.asset-provenance-gaps"

# The assets under apps/web/public that had no provenance record on 2026-11-04, when this check
# arrived, each pinned by its SHA-256 then. provenance_gaps.json may drop entries, never add one.
BASELINE_GAPS: dict[str, str] = {
    "apps/web/public/models/lb_splats.json": "626645289f55f59f527d8003c19ed81e0928be21b7505b07e00286352d757b49",
    "apps/web/public/models/nasa_lro_moon_3d.glb": "75eba4b5f9168c96f8ef9f47d2a346ba54eef05d5f2b81c1de5ed816f8dcd09a",
    "apps/web/public/textures/bruneton_atmo_lut.png": "9747ec7c4555cba7bc7fbee4ae5839e09196e9d75d23023aec5ff76b3f36c4dd",
}


class Gap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class GapList(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    format: str
    format_version: int = Field(alias="formatVersion")
    gaps: tuple[Gap, ...]


@dataclass
class CheckResult:
    files: int = 0  # files in scope
    records: int = 0  # PROVENANCE.json files
    recorded: int = 0  # files a record covers
    gaps: int = 0  # files the allowlist covers
    ignored: int = 0  # gitignored files under the public folder, outside the check
    errors: list[str] = field(default_factory=list)


def _git_files(root: Path, public_rel: str, *extra: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", *extra, "--", public_rel],
        check=True,
        capture_output=True,
    ).stdout
    return sorted({p for p in out.decode("utf-8").split("\0") if p and (root / p).is_file()})


def publishable_files(root: Path, public_rel: str = PUBLIC_REL) -> list[str]:
    """Repository-relative paths git would publish: tracked, or untracked and not ignored."""
    return _git_files(root, public_rel, "--cached", "--others", "--exclude-standard")


def ignored_files(root: Path, public_rel: str = PUBLIC_REL) -> list[str]:
    return _git_files(root, public_rel, "--others", "--ignored", "--exclude-standard")


def check(
    root: Path,
    *,
    public_rel: str = PUBLIC_REL,
    gaps_path: Path | None = None,
    baseline: dict[str, str] | None = None,
) -> CheckResult:
    root = Path(root)
    baseline = BASELINE_GAPS if baseline is None else baseline
    result = CheckResult()
    try:
        scope = publishable_files(root, public_rel)
        result.ignored = len(ignored_files(root, public_rel))
    except (OSError, subprocess.CalledProcessError) as exc:
        result.errors.append(f"cannot list the files git would publish under {public_rel}: {exc}")
        return result
    in_scope = set(scope)
    result.files = len(scope)
    digests: dict[str, str] = {}

    def digest(rel: str) -> str:
        if rel not in digests:
            digests[rel] = sha256_file(root / rel)
        return digests[rel]

    covered: dict[str, str] = {}

    def cover(rel: str, owner: str) -> None:
        if rel in covered:
            result.errors.append(f"{rel}: covered twice, by {covered[rel]} and {owner}")
        else:
            covered[rel] = owner

    records = [p for p in scope if PurePosixPath(p).name == ASSET_PROVENANCE_RECORD_FILENAME]
    result.records = len(records)
    for record_path in records:
        folder = PurePosixPath(record_path).parent
        try:
            record = AssetProvenanceRecord.model_validate(json.loads((root / record_path).read_text(encoding="utf-8")))
        except (ValueError, ValidationError) as exc:
            first = str(exc).splitlines()
            result.errors.append(f"{record_path}: is not a valid provenance record: {' '.join(first[:3])}")
            continue
        for entry in [*record.files, *record.file_sets]:
            if entry.produced_by.method == "script" and not (root / entry.produced_by.script).is_file():
                named = getattr(entry, "path", None) or getattr(entry, "checksums", None)
                result.errors.append(
                    f"{record_path}: {named} names producing script {entry.produced_by.script}, which does not exist"
                )
        for entry in record.files:
            target = str(folder / entry.path)
            if target not in in_scope:
                result.errors.append(f"{record_path}: names a file that is not there (or that git ignores): {entry.path}")
                continue
            if digest(target) != entry.sha256:
                result.errors.append(f"{target}: sha256 differs from its record ({record_path})")
            cover(target, record_path)
        for entry in record.file_sets:
            listing = str(folder / entry.checksums)
            if listing not in in_scope:
                result.errors.append(f"{record_path}: names a file that is not there (or that git ignores): {entry.checksums}")
                continue
            if digest(listing) != entry.sha256:
                result.errors.append(f"{listing}: sha256 differs from its record ({record_path})")
            cover(listing, record_path)
            try:
                lines = parse_sha256sums((root / listing).read_text(encoding="utf-8"))
            except ValueError as exc:
                result.errors.append(f"{listing}: {exc}")
                continue
            for sha, rel in lines:
                target = str(folder / rel)
                if target not in in_scope:
                    result.errors.append(f"{target}: listed in {entry.checksums} but not there")
                    continue
                if digest(target) != sha:
                    result.errors.append(f"{target}: sha256 differs from {entry.checksums}")
                cover(target, f"{listing} ({record_path})")
    result.recorded = len(covered)

    gaps_path = DEFAULT_GAPS if gaps_path is None else gaps_path
    try:
        gap_list = GapList.model_validate(json.loads(Path(gaps_path).read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError) as exc:
        result.errors.append(f"{gaps_path}: cannot read the allowlist: {exc}")
        gap_list = GapList(format=GAPS_FORMAT, formatVersion=1, gaps=())
    if gap_list.format != GAPS_FORMAT or gap_list.format_version != 1:
        result.errors.append(f"{gaps_path}: expected format {GAPS_FORMAT} version 1")
    seen_gaps: set[str] = set()
    for gap in gap_list.gaps:
        if gap.path in seen_gaps:
            result.errors.append(f"{gap.path}: listed twice in the allowlist")
            continue
        seen_gaps.add(gap.path)
        if gap.path not in baseline:
            result.errors.append(f"{gap.path}: is not one of the known gaps; the allowlist only shrinks")
            continue
        if gap.path not in in_scope:
            result.errors.append(f"{gap.path}: is gone: remove it from the allowlist")
            continue
        if gap.path in covered:
            result.errors.append(f"{gap.path}: has a record now: remove it from the allowlist")
            continue
        if digest(gap.path) != baseline[gap.path]:
            result.errors.append(f"{gap.path}: changed since it was allowlisted; a changed asset needs a record")
            continue
        covered[gap.path] = str(gaps_path)
        result.gaps += 1

    record_set = set(records)
    for rel in scope:
        if rel not in covered and rel not in record_set:
            result.errors.append(f"{rel}: no provenance record (add it to a PROVENANCE.json in its folder)")
    return result


def main() -> int:
    result = check(ROOT_DIR)
    if result.errors:
        for line in result.errors:
            print(f"[verify:provenance] {line}")
        print(f"[verify:provenance] FAILED with {len(result.errors)} problems")
        return 1
    print(
        f"[verify:provenance] OK: {result.files:,} files under {PUBLIC_REL}: {result.recorded:,} covered by "
        f"{result.records} provenance records, {result.gaps} known gaps (the allowlist only shrinks from "
        f"{len(BASELINE_GAPS)}); {result.ignored} gitignored files left out"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
