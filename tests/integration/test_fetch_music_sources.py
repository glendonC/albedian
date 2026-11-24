"""The music's samples: the tracked list of every file the app plays (data/music_sources.json) and the script that
fetches them into .cache/music/sources/ (scripts/fetch_music_sources.py), so a clean clone can get the app's music.
apps/web/src/audio/music/__tests__/musicSourcesList.test.ts checks that the list names exactly the files the app plays.

No test here reaches the network: every name handed to the resolver other than 127.0.0.1 is refused, and the fetch
tests are served by this file's own server on 127.0.0.1 and write under a temporary folder, never the shared .cache.
Each file is fetched by scripts/fetch_download.py's own `fetch` (tested in test_fetch_download.py), which allows plain
http on 127.0.0.1 only when a caller passes loopback_for_tests=True; the script's command line never does.
"""

from __future__ import annotations

import hashlib
import http.server
import importlib.util
import json
from pathlib import Path
import re
import socket
import socketserver
import sys
import threading
from typing import Any, Iterator

import pytest

ROOT = Path(__file__).resolve().parents[2]
LIST = ROOT / "data/music_sources.json"
CATALOG = ROOT / "apps/web/spike/music/sources.json"
SCRIPT = "scripts/fetch_music_sources.py"


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fetch_music_sources = _load("fetch_music_sources", SCRIPT)
fetch_download = sys.modules["fetch_download"]


@pytest.fixture(autouse=True)
def no_names_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every connection resolves its host first: only 127.0.0.1 gets through, so no test can leave the machine."""
    real = socket.getaddrinfo

    def only_loopback(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host not in ("127.0.0.1", b"127.0.0.1"):
            raise OSError("this test hands no name to the resolver")
        return real(host, *args, **kwargs)

    for name in list(__import__("os").environ):
        if name.lower().endswith("_proxy"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setattr(socket, "getaddrinfo", only_loopback)


class _Handler(http.server.BaseHTTPRequestHandler):
    routes: dict[str, bytes] = {}
    seen: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 (the http.server API)
        type(self).seen.append(self.path)
        body = type(self).routes.get(self.path)
        self.send_response(200 if body is not None else 404)
        body = body if body is not None else b"not here"
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 (the http.server API)
        return


class _Server(http.server.ThreadingHTTPServer):
    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


@pytest.fixture(scope="module")
def origin() -> Iterator[str]:
    httpd = _Server(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


SAMPLES = {"vcsl/a/organ C4.wav": b"RIFF organ" * 300, "vcsl/b/chime.wav": b"RIFF chime" * 200, "scottbuckley/x.mp3": b"ID3 x" * 500}


PREFIX = ".cache/music/sources/"


def listed(origin: str, **changes: dict[str, Any]) -> list[dict[str, Any]]:
    """A list in data/music_sources.json's own form for SAMPLES served at origin; `changes` maps a sample's path to
    fields to replace."""
    _Handler.routes = {"/" + path.replace(" ", "%20"): data for path, data in SAMPLES.items()}
    _Handler.seen = []
    entries = []
    for path, data in SAMPLES.items():
        entry = {
            "path": PREFIX + path, "fetch": SCRIPT, "url": f"{origin}/{path.replace(' ', '%20')}",
            "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "licence": "CC0 1.0 Universal",
            "licenceUrl": "https://creativecommons.org/publicdomain/zero/1.0/", "source": "test", "credit": "test samples",
        }
        entry.update(changes.get(path, {}))
        entries.append(entry)
    return entries


def files_under(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_the_tracked_list_holds_each_sample_the_app_plays_as_the_catalogue_lists_it() -> None:
    music = json.loads(LIST.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert music["userAgent"] == catalog["userAgent"] == fetch_download.USER_AGENT
    assert music["vcslCommit"] == catalog["vcslCommit"]
    by_path = {entry["path"]: entry for entry in catalog["files"]}
    entries = music["downloads"]
    # The 144 files directions.ts plays (143 from VCSL, Signal to Noise's no-melody mix), 261741326 bytes
    assert len(entries) == 144 and sum(entry["bytes"] for entry in entries) == 261_741_326
    assert len({entry["path"] for entry in entries}) == 144
    for entry in entries:
        assert entry["path"].startswith(PREFIX), entry["path"]
        path = entry["path"][len(PREFIX):]
        assert fetch_download.is_cache_path(entry["path"]) and not path.startswith("/"), path
        assert entry["fetch"] == SCRIPT
        catalogued = by_path[path]
        assert (entry["url"], entry["bytes"], entry["sha256"], entry["licence"], entry["licenceUrl"], entry["source"],
                entry["credit"]) == (catalogued["url"], catalogued["bytes"], catalogued["sha256"], catalogued["licence"],
                                     catalogued["licencePage"], catalogued["collection"], catalogued["credit"]), path
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) and type(entry["bytes"]) is int and entry["bytes"] > 0
        assert entry["url"].startswith("https://") and entry["credit"].strip(), path
        assert (entry["licence"], entry["licenceUrl"]) in {
            ("CC0 1.0 Universal", "https://creativecommons.org/publicdomain/zero/1.0/"),
            ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"),
        }, path
    assert [download.path for download in fetch_music_sources.load_list(LIST)] == [entry["path"] for entry in entries]


def test_fetches_every_listed_file_once_and_a_second_run_asks_nothing(origin: str, tmp_path: Path) -> None:
    entries = fetch_music_sources.downloads_of(listed(origin))
    first = fetch_music_sources.fetch_all(entries, tmp_path, loopback_for_tests=True)
    assert (first.fetched, first.present, first.failed) == (3, 0, [])
    assert first.fetched_bytes == sum(len(data) for data in SAMPLES.values())
    assert files_under(tmp_path) == {".cache/music/sources/" + path: data for path, data in SAMPLES.items()}
    assert sorted(_Handler.seen) == sorted("/" + path.replace(" ", "%20") for path in SAMPLES)
    _Handler.seen = []
    second = fetch_music_sources.fetch_all(entries, tmp_path, loopback_for_tests=True)
    assert (second.fetched, second.present, second.failed, second.fetched_bytes) == (0, 3, [], 0)
    assert _Handler.seen == []


def test_a_file_that_differs_from_its_entry_is_not_kept_and_the_rest_are_still_fetched(origin: str, tmp_path: Path) -> None:
    entries = fetch_music_sources.downloads_of(listed(origin, **{"vcsl/b/chime.wav": {"sha256": "0" * 64}}))
    result = fetch_music_sources.fetch_all(entries, tmp_path, loopback_for_tests=True)
    assert (result.fetched, result.present) == (2, 0)
    assert [path for path, _ in result.failed] == [PREFIX + "vcsl/b/chime.wav"]
    assert "sha256" in result.failed[0][1]
    kept = files_under(tmp_path)
    assert ".cache/music/sources/vcsl/b/chime.wav" not in kept and not any(name.endswith(".part") for name in kept)
    assert len(kept) == 2


def test_a_file_already_there_but_changed_is_fetched_again(origin: str, tmp_path: Path) -> None:
    entries = fetch_music_sources.downloads_of(listed(origin))
    fetch_music_sources.fetch_all(entries, tmp_path, loopback_for_tests=True)
    changed = tmp_path / ".cache/music/sources/vcsl/a/organ C4.wav"
    changed.write_bytes(b"not the organ")
    _Handler.seen = []
    result = fetch_music_sources.fetch_all(entries, tmp_path, loopback_for_tests=True)
    assert (result.fetched, result.present, result.failed) == (1, 2, [])
    assert changed.read_bytes() == SAMPLES["vcsl/a/organ C4.wav"]
    assert _Handler.seen == ["/vcsl/a/organ%20C4.wav"]


@pytest.mark.parametrize("path", [
    ".cache/music/sources/../escape.wav", ".cache/music/escape.wav", ".cache/music/sources//etc/escape.wav",
    "/tmp/escape.wav", "escape.wav", ".cache/music/sources/", ".cache/music/sources", ".cache/music/sources/a/./b.wav",
    ".cache/music/elsewhere/escape.wav",
])
def test_a_listed_path_out_of_the_music_cache_is_refused_before_any_request(origin: str, tmp_path: Path, path: str,
                                                                            capsys) -> None:
    entries = listed(origin)
    entries[0]["path"] = path
    listing = tmp_path / "list.json"
    listing.write_text(json.dumps({"downloads": entries}))
    with pytest.raises(fetch_music_sources.ListError, match="not a file under .cache/music/sources/"):
        fetch_music_sources.load_list(listing)
    assert fetch_music_sources.main(["--list", str(listing), "--root", str(tmp_path / "clone")]) == 2
    assert "not a file under .cache/music/sources/" in capsys.readouterr().err
    assert _Handler.seen == [] and not (tmp_path / "clone").exists()


def test_the_command_refuses_plain_http_and_fails_after_trying_every_file(origin: str, tmp_path: Path, capsys) -> None:
    listing = tmp_path / "list.json"
    listing.write_text(json.dumps({"downloads": listed(origin)}))
    assert fetch_music_sources.main(["--list", str(listing), "--root", str(tmp_path / "clone")]) == 1
    said = capsys.readouterr()
    assert said.err.count("refused") == 3
    assert "0 fetched, 0 already there, 3 failed" in said.out
    assert _Handler.seen == [] and not (tmp_path / "clone").exists()


def test_the_audio_document_the_field_guide_and_the_readme_point_at_the_script() -> None:
    engine = (ROOT / "docs/architecture/AUDIO_ENGINE.md").read_text(encoding="utf-8")
    field = (ROOT / "docs/FIELD_USE.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for text in (engine, field, readme):
        assert "uv run python scripts/fetch_music_sources.py" in text
    # The untracked scripts the document named before are gone from it
    assert ".lanetools/fetch_sources.py" not in engine and ".lanetools/music_sources_list.py" not in engine
