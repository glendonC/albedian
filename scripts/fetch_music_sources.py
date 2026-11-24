#!/usr/bin/env python3
"""Fetch every sample file the app's music plays, listed in data/music_sources.json, into .cache/music/sources/.

Usage, from the repository root of a clone:
    uv run python scripts/fetch_music_sources.py

The list names each file the music plays (apps/web/src/audio/music/directions.ts: every instrument's samples and the
recording) with its address, size, SHA-256, licence and credit, as the music's catalogue
apps/web/spike/music/sources.json records them. Each file is fetched by scripts/fetch_download.py's `fetch`, under its
rules: https only, a redirect only to the same host, the User-Agent "Albedian/0.1 (research prototype)", the bytes
written to `<file>.part` and kept only when their size and SHA-256 match the list. A file already there with the listed
size and SHA-256 is kept without a request, so a second run fetches nothing; one that differs is fetched again.

Every file is tried; the run ends with one line of counts and exits 1 if any file failed, 2 if the list itself names a
path outside .cache/music/sources/ (then nothing is fetched). `pnpm field` serves the folder to the app at
/music-sources/ (docs/FIELD_USE.md). Only the standard library is used.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import importlib.util
import json
from pathlib import Path, PurePosixPath
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
LIST = ROOT / "data" / "music_sources.json"
FOLDER = (".cache", "music", "sources")


def _fetch_download():
    """scripts/fetch_download.py, loaded by its path, so the script runs alike from any folder and under `python -I`."""
    if "fetch_download" in sys.modules:
        return sys.modules["fetch_download"]
    spec = importlib.util.spec_from_file_location("fetch_download", Path(__file__).resolve().with_name("fetch_download.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fetch_download = _fetch_download()


class ListError(ValueError):
    """The list names a file it may not: one sentence, said before any request."""


def _in_folder(path: str) -> bool:
    """True for a plain relative path to a file under .cache/music/sources/: written as Path writes it (no '.', doubled
    or trailing slash), and with no '..', which fetch_download's own check refuses."""
    parts = PurePosixPath(path).parts
    return (PurePosixPath(path).as_posix() == path and len(parts) > len(FOLDER) and parts[: len(FOLDER)] == FOLDER
            and fetch_download.is_cache_path(path))


def downloads_of(entries: list[dict]) -> list:
    """The list's entries as fetch_download's Download records; ListError for a path outside the music's folder."""
    for entry in entries:
        if not isinstance(entry.get("path"), str) or not _in_folder(entry["path"]):
            raise ListError(f"{entry.get('path')!r} is not a file under {'/'.join(FOLDER)}/")
    return [fetch_download.Download.from_entry(entry) for entry in entries]


def load_list(path: Path = LIST) -> list:
    return downloads_of(json.loads(path.read_text(encoding="utf-8"))["downloads"])


@dataclass
class Result:
    fetched: int = 0
    present: int = 0
    fetched_bytes: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)


def fetch_all(downloads: list, root: Path = ROOT, *, loopback_for_tests: bool = False, say=lambda line: None) -> Result:
    """Fetch each download under root, trying every one; what was fetched, what was already there, what failed."""
    result = Result()
    for download in downloads:
        target = root / download.path
        if fetch_download._present(target, download):
            result.present += 1
            continue
        try:
            fetch_download.fetch(download, root=root, loopback_for_tests=loopback_for_tests)
        except (fetch_download.FetchError, fetch_download.AnswerError) as error:  # all that fetch raises
            result.failed.append((download.path, fetch_download._failure(download.path, error)))
            continue
        result.fetched += 1
        result.fetched_bytes += download.bytes
        say(f"{download.path}: {download.bytes} bytes ({download.licence})")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", type=Path, default=LIST, help="the list to fetch (default: data/music_sources.json)")
    parser.add_argument("--root", type=Path, default=ROOT, help="the clone to fetch into (default: this one)")
    args = parser.parse_args(argv)
    try:
        downloads = load_list(args.list)
    except ListError as error:
        print(f"{args.list}: {error}; nothing fetched", file=sys.stderr)
        return 2
    total = sum(download.bytes for download in downloads)
    print(f"{len(downloads)} files, {total} bytes, into {args.root / '/'.join(FOLDER)}")
    started = time.monotonic()
    result = fetch_all(downloads, args.root, say=print)
    for _, line in result.failed:
        print(line, file=sys.stderr)
    print(f"{result.fetched} fetched, {result.present} already there, {len(result.failed)} failed; "
          f"{result.fetched_bytes} bytes in {time.monotonic() - started:.1f} s")
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
