#!/usr/bin/env python3
"""Fetch a public download listed in data/downloads.json into the shared .cache/, checked by size and SHA-256.

Usage, from the repository root:
    uv run python scripts/fetch_download.py .cache/dust/edenhofer2024/mean_and_std_healpix.fits

The bytes go to `<path>.part`, which becomes `<path>` only once its size and SHA-256 match the registry entry; on a
mismatch or an error the part file is deleted and the script exits with 1. A file already there with the listed
size and SHA-256 is kept without a request. A path outside `.cache/` or a URL that is not https (plain http only on
the loopback, for tests) is refused before any request. Requests carry the User-Agent
"Albedian/0.1 (research prototype)" and nothing personal. Only the standard library is used.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "downloads.json"
USER_AGENT = "Albedian/0.1 (research prototype)"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
CHUNK = 1 << 20
TIMEOUT_S = 60


class FetchError(Exception):
    """A download that was refused, or whose bytes do not match its registry entry."""


@dataclass(frozen=True)
class Download:
    path: str
    fetch: str
    url: str
    bytes: int
    sha256: str
    licence: str
    licence_url: str
    source: str

    @classmethod
    def from_entry(cls, entry: dict) -> Download:
        return cls(
            path=entry["path"],
            fetch=entry["fetch"],
            url=entry["url"],
            bytes=entry["bytes"],
            sha256=entry["sha256"],
            licence=entry["licence"],
            licence_url=entry["licenceUrl"],
            source=entry["source"],
        )


def is_cache_path(path: str) -> bool:
    """True for a relative path to something inside .cache/, with no '..' anywhere in it."""
    parts = PurePosixPath(path).parts
    return not PurePosixPath(path).is_absolute() and len(parts) > 1 and parts[0] == ".cache" and ".." not in parts


def load_registry(registry: Path = REGISTRY) -> dict[str, Download]:
    entries = json.loads(registry.read_text(encoding="utf-8"))["downloads"]
    return {entry["path"]: Download.from_entry(entry) for entry in entries}


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def fetch(download: Download, root: Path = ROOT) -> Path:
    """Download one registry entry under root and return its path; raise FetchError if it is refused or differs."""
    if not is_cache_path(download.path):
        raise FetchError(f"{download.path} is not under .cache/")
    url = urlsplit(download.url)
    if url.scheme != "https" and not (url.scheme == "http" and url.hostname in LOOPBACK):
        raise FetchError(f"{download.url} is not https")
    target = root / download.path
    if target.is_file() and target.stat().st_size == download.bytes and _sha256_of(target) == download.sha256:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    request = urllib.request.Request(download.url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    size = 0
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response, part.open("wb") as out:
            while block := response.read(CHUNK):
                size += len(block)
                if size > download.bytes:
                    raise FetchError(f"{download.path}: more than the {download.bytes} bytes the registry lists")
                digest.update(block)
                out.write(block)
        if size != download.bytes:
            raise FetchError(f"{download.path}: got {size} bytes, the registry lists {download.bytes}")
        if digest.hexdigest() != download.sha256:
            raise FetchError(f"{download.path}: sha256 {digest.hexdigest()}, the registry lists {download.sha256}")
        part.replace(target)
    finally:
        part.unlink(missing_ok=True)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", help="registry paths, for example .cache/dust/edenhofer2024/mean_and_std_healpix.fits")
    args = parser.parse_args(argv)
    registry = load_registry()
    unlisted = [path for path in args.paths if path not in registry]
    if unlisted:
        for path in unlisted:
            print(f"{path} is not listed in data/downloads.json", file=sys.stderr)
        return 2
    for path in args.paths:
        download = registry[path]
        print(f"{path}: {download.bytes} bytes from {download.url} ({download.licence})")
        try:
            fetch(download)
        except (FetchError, OSError) as error:
            print(f"{path}: {error}", file=sys.stderr)
            return 1
        print(f"{path}: size and sha256 match data/downloads.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
