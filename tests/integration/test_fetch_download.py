"""The registry of public downloads (data/downloads.json) and the script that fetches them (scripts/fetch_download.py).

The fetch tests serve their bytes from a local HTTP server on 127.0.0.1 inside the test and write under tmp_path, so
they make no outside request and never touch the shared .cache. The last test reads the shared .cache itself, where
it is present, and checks each listed file against its entry.
"""

from __future__ import annotations

from collections.abc import Iterator
import hashlib
import http.server
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "data" / "downloads.json"
FIELDS = {"path", "fetch", "url", "bytes", "sha256", "licence", "licenceUrl", "source"}
PAYLOAD = b"Albedian fetch test payload\n" * 1000


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fetch_download = _load("fetch_download", "scripts/fetch_download.py")
ENTRIES = json.loads(REGISTRY.read_text())["downloads"]


class _Recorder(http.server.BaseHTTPRequestHandler):
    """Serves PAYLOAD at /payload.bin, or a short body at /short.bin, and records each request's User-Agent."""

    requests: list[str | None] = []

    def do_GET(self) -> None:  # noqa: N802 (the http.server API)
        type(self).requests.append(self.headers.get("User-Agent"))
        body = PAYLOAD if self.path == "/payload.bin" else PAYLOAD[:100]
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 (the http.server API)
        return


@pytest.fixture
def server() -> Iterator[str]:
    _Recorder.requests = []
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def download(url: str, path: str = ".cache/test/payload.bin", **changes: object):
    fields = {
        "path": path,
        "fetch": "scripts/fetch_download.py",
        "url": url,
        "bytes": len(PAYLOAD),
        "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
        "licence": "CC0-1.0",
        "licenceUrl": "https://creativecommons.org/publicdomain/zero/1.0/",
        "source": "test payload",
    }
    fields.update(changes)
    return fetch_download.Download.from_entry(fields)


# =============================================================================================
# Fetching
# =============================================================================================


def test_a_fetch_writes_the_file_only_once_its_size_and_sha256_match(tmp_path: Path, server: str) -> None:
    target = fetch_download.fetch(download(f"{server}/payload.bin"), root=tmp_path)
    assert target == tmp_path / ".cache" / "test" / "payload.bin"
    assert target.read_bytes() == PAYLOAD
    assert list(target.parent.iterdir()) == [target]


def test_requests_carry_the_project_user_agent_and_nothing_else_identifying(tmp_path: Path, server: str) -> None:
    fetch_download.fetch(download(f"{server}/payload.bin"), root=tmp_path)
    assert _Recorder.requests == ["Albedian/0.1 (research prototype)"]


@pytest.mark.parametrize(
    ("served", "changes", "named"),
    [
        ("payload.bin", {"sha256": "0" * 64}, "sha256"),
        ("short.bin", {}, "bytes"),
        ("payload.bin", {"bytes": len(PAYLOAD) - 1}, "bytes"),
    ],
    ids=["wrong-sha256", "short-body", "longer-than-listed"],
)
def test_a_mismatch_leaves_no_file_behind(tmp_path: Path, server: str, served: str, changes: dict, named: str) -> None:
    with pytest.raises(fetch_download.FetchError, match=named):
        fetch_download.fetch(download(f"{server}/{served}", **changes), root=tmp_path)
    folder = tmp_path / ".cache" / "test"
    assert not folder.exists() or list(folder.iterdir()) == []


@pytest.mark.parametrize("path", ["data/payload.bin", ".cache/../data/payload.bin", "/tmp/payload.bin", ".cache"])
def test_a_path_outside_the_cache_is_refused_before_any_request(tmp_path: Path, server: str, path: str) -> None:
    with pytest.raises(fetch_download.FetchError, match=r"not under \.cache/"):
        fetch_download.fetch(download(f"{server}/payload.bin", path=path), root=tmp_path)
    assert _Recorder.requests == []


def test_plain_http_is_refused_except_on_the_loopback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_request(*args: object, **kwargs: object) -> None:
        raise AssertionError("no request may be made")

    monkeypatch.setattr(fetch_download.urllib.request, "urlopen", no_request)
    with pytest.raises(fetch_download.FetchError, match="https"):
        fetch_download.fetch(download("http://example.org/payload.bin"), root=tmp_path)


def test_a_verified_file_already_present_is_kept_without_a_request(tmp_path: Path, server: str) -> None:
    target = tmp_path / ".cache" / "test" / "payload.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(PAYLOAD)
    assert fetch_download.fetch(download(f"{server}/payload.bin"), root=tmp_path) == target
    assert _Recorder.requests == []


def test_the_command_refuses_a_path_the_registry_does_not_list() -> None:
    run = subprocess.run(
        [sys.executable, "scripts/fetch_download.py", ".cache/not/listed.bin"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 2, run.stderr
    assert ".cache/not/listed.bin is not listed in data/downloads.json" in run.stderr


# =============================================================================================
# The registry
# =============================================================================================


def test_every_registry_entry_is_complete_and_its_fetch_script_is_tracked() -> None:
    tracked = set(subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines())
    paths = [entry["path"] for entry in ENTRIES]
    assert len(paths) == len(set(paths)), "each download is listed once"
    for entry in ENTRIES:
        assert set(entry) == FIELDS, entry["path"]
        assert fetch_download.is_cache_path(entry["path"]), entry["path"]
        assert entry["fetch"] in tracked, entry["path"]
        assert entry["url"].startswith("https://"), entry["path"]
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), entry["path"]
        assert isinstance(entry["bytes"], int) and entry["bytes"] > 0, entry["path"]
        assert entry["licence"].strip() and entry["source"].strip(), entry["path"]
        assert entry["licenceUrl"].startswith("https://"), entry["path"]


def test_the_registry_agrees_with_the_inputs_the_earth_map_bake_pins() -> None:
    bake = _load("bake_earth_maps", "scripts/bake_earth_maps.py")
    by_path = {entry["path"]: entry for entry in ENTRIES}
    for source in bake.INPUTS.values():
        entry = by_path[f".cache/earth-maps/{source.filename}"]
        assert (entry["url"], entry["sha256"]) == (source.url, source.sha256)


@pytest.mark.parametrize(
    "entry",
    [pytest.param(entry, marks=pytest.mark.local_dataset(entry["path"]), id=Path(entry["path"]).name) for entry in ENTRIES],
)
def test_each_download_in_the_shared_cache_matches_its_entry(entry: dict) -> None:
    path = ROOT / entry["path"]
    assert path.stat().st_size == entry["bytes"]
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1 << 22):
            digest.update(block)
    assert digest.hexdigest() == entry["sha256"]
