"""The API answers only a Host that names this machine, and discover scans only the app's own folders (platform-6b).

A page that rebinds its own name to 127.0.0.1 still sends that name as the Host, so the API refuses every Host but
localhost, 127.0.0.1 and [::1]; ALBEDIAN_TRUSTED_HOSTS replaces them, and the test run adds TestClient's testserver.
Discover reads the folders the app was built with, never folders a request names, and every path it returns is
relative to its folder.
"""

from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path

from fastapi.testclient import TestClient
from hypothesis import example, given, settings, strategies as st
import numpy as np
from PIL import Image
import pytest

from services.api import DEFAULT_TRUSTED_HOSTS, TRUSTED_HOSTS_ENV, create_app
from services.media.router import DEFAULT_MEDIA_FOLDERS, MediaFolders

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app(trusted_hosts=DEFAULT_TRUSTED_HOSTS), base_url="http://localhost")


def picture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.zeros((90, 160), dtype=np.uint8)).save(path)


@pytest.fixture
def folders(tmp_path: Path) -> MediaFolders:
    """Scratch folders outside the repository: one picture in a subfolder, a web variant and an experiment report."""
    picture(tmp_path / "media" / "uploads" / "nested" / "still.png")
    picture(tmp_path / "media" / "public" / "renders" / "still_v1.png")
    report = tmp_path / "media" / "artifacts" / "still-run" / "experiment_report.json"
    report.parent.mkdir(parents=True)
    report.write_text("{}")
    root = tmp_path / "media"
    return MediaFolders(uploads=root / "uploads", public=root / "public", artifacts=root / "artifacts")


def strings_in(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from strings_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings_in(item)


# =============================================================================================
# Hosts
# =============================================================================================


def test_the_default_hosts_name_this_machine_only() -> None:
    assert DEFAULT_TRUSTED_HOSTS == ("localhost", "127.0.0.1", "[::1]")


@pytest.mark.parametrize("path", ["/api/health", "/api/media/discover"])
@pytest.mark.parametrize("host", ["evil.example", "localhost.evil.example", "127.0.0.1.nip.io", "testserver"])
def test_a_host_that_does_not_name_this_machine_is_refused(client: TestClient, host: str, path: str) -> None:
    response = client.get(path, headers={"Host": host})
    assert response.status_code == 400
    assert response.text == "Invalid host header"


@pytest.mark.parametrize("host", ["localhost", "localhost:8000", "127.0.0.1:8000", "[::1]:8000", "LOCALHOST:8000"])
def test_this_machines_names_are_answered_on_any_port(client: TestClient, host: str) -> None:
    assert client.get("/api/health", headers={"Host": host}).status_code == 200


def test_the_environment_replaces_the_default_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TRUSTED_HOSTS_ENV, " albedian.test , 127.0.0.1 ")
    client = TestClient(create_app(), base_url="http://albedian.test")
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health", headers={"Host": "127.0.0.1:8000"}).status_code == 200
    assert client.get("/api/health", headers={"Host": "localhost"}).status_code == 400


def test_an_explicit_list_replaces_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TRUSTED_HOSTS_ENV, "albedian.test")
    client = TestClient(create_app(trusted_hosts=["localhost"]), base_url="http://localhost")
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health", headers={"Host": "albedian.test"}).status_code == 400


@pytest.mark.parametrize(
    "value",
    ["*", "*.example", "localhost,*", "localhost:8000", "http://localhost", "localhost/api", "LOCALHOST", "", " , ", "a b"],
)
def test_a_wildcard_or_malformed_host_stops_the_app(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(TRUSTED_HOSTS_ENV, value)
    with pytest.raises(ValueError, match=TRUSTED_HOSTS_ENV):
        create_app()


def test_only_the_test_run_adds_testserver() -> None:
    # tests/conftest.py adds TestClient's default host for this run; the app's own default never has it
    assert os.environ[TRUSTED_HOSTS_ENV].split(",") == [*DEFAULT_TRUSTED_HOSTS, "testserver"]
    assert "testserver" not in DEFAULT_TRUSTED_HOSTS


HOST_NAMES = st.from_regex(r"[a-z0-9][a-z0-9.-]{0,24}", fullmatch=True)


@settings(max_examples=60, deadline=None)
@given(host=HOST_NAMES)
@example(host="localhost.evil.example")
@example(host="127.0.0.1.nip.io")
@example(host="localhost")
@example(host="127.0.0.1")
@example(host="[::1]")
@example(host="[::2]")
@example(host="0.0.0.0")
def test_pbt_only_a_listed_host_is_answered(client: TestClient, host: str) -> None:
    status = client.get("/api/health", headers={"Host": host}).status_code
    assert (status == 200) == (host in DEFAULT_TRUSTED_HOSTS)


# =============================================================================================
# Scan folders and paths
# =============================================================================================


def test_the_default_folders_are_in_the_repository_whatever_the_working_directory() -> None:
    assert DEFAULT_MEDIA_FOLDERS == MediaFolders(
        uploads=REPO_ROOT / "data" / "uploads",
        public=REPO_ROOT / "apps" / "web" / "public",
        artifacts=REPO_ROOT / "data" / "artifacts",
    )


def test_discover_scans_the_apps_folders_and_ignores_folders_a_request_names(tmp_path: Path, folders: MediaFolders) -> None:
    elsewhere = tmp_path / "elsewhere"
    picture(elsewhere / "other.png")
    client = TestClient(create_app(media_folders=folders), base_url="http://localhost")
    query = {"uploads_dir": str(elsewhere), "public_dir": str(elsewhere), "artifacts_dir": str(elsewhere)}
    body = client.get("/api/media/discover", params=query).json()
    assert [item["filename"] for item in body["items"]] == ["still.png"]
    assert "uploadsDir" not in body


def test_discover_paths_are_relative_to_their_folders_and_never_absolute(tmp_path: Path, folders: MediaFolders) -> None:
    client = TestClient(create_app(media_folders=folders), base_url="http://localhost")
    body = client.get("/api/media/discover").json()
    item = body["items"][0]
    assert item["relativePath"] == "nested/still.png"
    assert item["artifactReportPath"] == "still-run/experiment_report.json"
    assert item["variants"] == {"v1_png": "/renders/still_v1.png"}
    roots = {str(tmp_path), str(tmp_path.resolve()), str(REPO_ROOT), str(Path.home())}
    for text in strings_in(body):
        for root in roots:
            assert root not in text
