"""The registry of public downloads (data/downloads.json) and the script that fetches them (scripts/fetch_download.py).

No test here can reach the network. Every test runs inside `loopback_only`, which records each connection http.client
is told to open and each name handed to the resolver, and cuts every one that is not for this file's own server on
127.0.0.1. HTTPS is played by a stand-in connection that reads the host and the port exactly as
http.client.HTTPSConnection does and records them; for the one made-up host files.example (a reserved .example name)
it talks plain HTTP to the same server, and every other https connection is cut. Proxies are switched off, so a
connection goes where the URL says. The fetch tests write under a temporary folder and never touch the shared .cache.
The last test reads the shared .cache itself, where it is present, and checks each listed file against its entry.
"""

from __future__ import annotations

from collections.abc import Iterator
import ast
import contextlib
import email.message
import functools
import hashlib
import http.client
import http.server
import importlib.util
import inspect
import io
import itertools
import json
import os
from pathlib import Path
import re
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import traceback
import types
from typing import Any
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from hypothesis import HealthCheck, event, example, given, settings
from hypothesis import strategies as st
import pytest

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "data" / "downloads.json"
FIELDS = {"path", "fetch", "url", "bytes", "sha256", "licence", "licenceUrl", "source"}
PAYLOAD = b"Albedian fetch test payload\n" * 1000
PATH = ".cache/test/payload.bin"
STAND_IN_HOST = "files.example"
USER_AGENT = "Albedian/0.1 (research prototype)"


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fetch_download = _load("fetch_download", "scripts/fetch_download.py")
ENTRIES = json.loads(REGISTRY.read_text())["downloads"]


# =============================================================================================
# The harness: one loopback server, every other connection and name lookup cut and recorded
# =============================================================================================


class Wire:
    """What a test tried to reach: each (host, port) a connection was opened for, in http.client's own reading of the
    URL, and each name handed to the resolver. Only `server_port` on 127.0.0.1, and files.example on 443 (which the
    stand-in carries to the same server), get through; None lets nothing through."""

    def __init__(self) -> None:
        self.connections: list[tuple[str, int]] = []
        self.lookups: list[str] = []
        self.server_port: int | None = None

    def clear(self) -> None:
        self.connections.clear()
        self.lookups.clear()


@contextlib.contextmanager
def loopback_only(wire: Wire) -> Iterator[Wire]:
    connect = http.client.HTTPConnection.connect
    lookups = {name: getattr(socket, name) for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr")}
    https_open = urllib.request.HTTPSHandler.https_open

    def recording_connect(self: http.client.HTTPConnection) -> None:
        wire.connections.append((self.host, self.port))
        if wire.server_port is None or (self.host, self.port) != ("127.0.0.1", wire.server_port):
            raise OSError("this test lets no request leave the machine")
        connect(self)

    def recording_lookup(name: str):
        def lookup(host: Any, *args: Any, **kwargs: Any) -> Any:
            wire.lookups.append(host.decode("latin-1") if isinstance(host, bytes) else str(host))
            if name != "getaddrinfo" or wire.lookups[-1] != "127.0.0.1":
                raise OSError("this test hands no name to the resolver")
            return lookups[name](host, *args, **kwargs)

        return lookup

    class StandIn(http.client.HTTPConnection):
        """HTTPSConnection's reading of the host and the port (the same _get_hostport, default port 443), then plain
        HTTP to the test server for files.example alone. Its socket connects by address, so it asks the resolver
        nothing."""

        default_port = http.client.HTTPSConnection.default_port

        def connect(self) -> None:
            wire.connections.append((self.host, self.port))
            if wire.server_port is None or (self.host, self.port) != (STAND_IN_HOST, 443):
                raise OSError("this test lets no request leave the machine")
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(10)
            self.sock.connect(("127.0.0.1", wire.server_port))

    def stand_in_open(self: urllib.request.HTTPSHandler, request: urllib.request.Request) -> Any:
        return self.do_open(StandIn, request)

    http.client.HTTPConnection.connect = recording_connect  # type: ignore[method-assign]
    urllib.request.HTTPSHandler.https_open = stand_in_open  # type: ignore[method-assign]
    for name in lookups:
        setattr(socket, name, recording_lookup(name))
    try:
        yield wire
    finally:
        http.client.HTTPConnection.connect = connect  # type: ignore[method-assign]
        urllib.request.HTTPSHandler.https_open = https_open  # type: ignore[method-assign]
        for name, original in lookups.items():
            setattr(socket, name, original)


# Words only a server or a test URL's hidden part says; whatever fetch raises holds none of them outside a plainly
# named host (a refusal names a plain scheme://host by design)
MARK = "zqxmark"
MARKERS = (MARK, "secret")
NAMED = re.compile(r"https?://([^\s,'\"/?#]+)")  # a named host: the authority alone, never a path or query


def everything_said(error: BaseException, *, frames: bool = True, given: dict[str, Any] | None = None) -> list[str]:
    """Every text an error holds, walked to any depth with a stack (no depth limit): its text, its repr and its
    formatted traceback, its cause, context, args and notes, every attribute (an instance's own dunder ones too), a
    function's closure and defaults, a bound method's object, and, through the traceback, every local and the module
    state of the script's own frames; the same of each of those in turn. Bytes are read as latin-1, a header block as
    its text, a byte buffer by its contents.

    What it leaves out, and why nothing a server sent can hide there (each pinned by a test that plants a marker):
    - a parameter of the script's frame while it still is the very object the caller passed (`given`, by identity):
      that is the caller's own input; a parameter rebound inside the function is walked like any other local;
    - a module, but the script's own: the script writes no other module's state (no `global`, no `install_opener`, no
      assignment to a module's attribute), and its own module's state is walked through its frames' globals;
    - the frames of other files: the traceback of what fetch raises starts in fetch and goes up through its callers;
    - code (a function's code, a class, a method its class defines): written by the program, not sent;
    - with `frames=False`, the frames themselves: only for an error below the boundary, which fetch rebuilds bare."""
    said: list[str] = []
    seen: set[int] = set()
    todo: list[Any] = [error]
    while todo:
        value = todo.pop()
        if value is None or id(value) in seen or isinstance(value, types.ModuleType):
            continue
        seen.add(id(value))
        if isinstance(value, (bytes, bytearray, memoryview)):
            said.append(bytes(value).decode("latin-1"))
            continue
        if isinstance(value, str):
            said.append(value)
            continue
        if isinstance(value, (bool, int, float, complex, type, types.CodeType)):
            said.append(repr(value))
            continue
        if isinstance(value, (list, tuple, set, frozenset)):
            todo.extend(list(value))
            continue
        if isinstance(value, dict):
            for name, item in list(value.items()):  # a snapshot: reading an attribute may add to a live dict
                todo += [name, item]
            continue
        if isinstance(value, email.message.Message):
            said.append(value.as_string())
            todo.append(list(value.items()))
            continue
        if isinstance(value, io.BytesIO):
            todo.append(value.getvalue())
        if isinstance(value, types.FunctionType):
            todo += [cell.cell_contents for cell in value.__closure__ or () if cell.cell_contents is not value]
            todo += [value.__defaults__, value.__kwdefaults__]
            continue
        if isinstance(value, types.MethodType):
            todo += [value.__self__, value.__func__]
            continue
        if isinstance(value, (types.BuiltinFunctionType, types.MethodWrapperType, types.WrapperDescriptorType, types.MethodDescriptorType)):
            todo.append(getattr(value, "__self__", None))
            continue
        if isinstance(value, BaseException):
            said.extend([str(value), repr(value), "".join(traceback.format_exception(value))])
            todo += [value.__cause__, value.__context__, value.args, getattr(value, "__notes__", None)]
            frame = value.__traceback__ if frames else None
            while frame is not None:
                code = frame.tb_frame.f_code
                if code.co_filename == fetch_download.__file__:
                    parameters = set(code.co_varnames[: code.co_argcount + code.co_kwonlyargcount])
                    for name, local in list(frame.tb_frame.f_locals.items()):
                        if not (given is not None and name in parameters and name in given and local is given[name]):
                            todo.append(local)
                    # the script's own module state; its __builtins__ is the interpreter's, not the script's
                    todo.append({name: g for name, g in frame.tb_frame.f_globals.items() if name != "__builtins__"})
                frame = frame.tb_next
        else:
            with contextlib.suppress(Exception):
                said.append(repr(value))
        own = getattr(value, "__dict__", None)
        own = dict(own) if isinstance(own, dict) else {}
        for name in sorted(set(dir(value)) | set(own)):
            if name.startswith("__") and name not in own and name != "__notes__":
                continue  # a class's own dunder: code and names
            if name not in own and inspect.isroutine(inspect.getattr_static(type(value), name, None)):
                continue  # a method the class defines: code, which bound to the object adds only the object itself
            try:
                todo.append(getattr(value, name))
            except Exception:  # noqa: BLE001, S112 (a property that cannot be read holds nothing to walk)
                continue
    return said


def server_words(texts: list[str]) -> list[str]:
    """The texts that hold a marker anywhere but in a host named plainly (`scheme://host`, matching NAMEABLE_HOST)."""
    held = []
    for text in texts:
        hosts: list[str] = []
        rest = NAMED.sub(lambda match: hosts.append(match[1]) or "", text)
        if any(marker in rest for marker in MARKERS) or any(
            marker in host and not fetch_download.NAMEABLE_HOST.fullmatch(host.split("/", 1)[0]) for host in hosts for marker in MARKERS
        ):
            held.append(text)
    return held


def assert_the_scripts_own(error: BaseException, entry: Any, given: dict[str, Any] | None = None) -> None:
    """What fetch raises: the script's own error, built after its handler ended, holding no word a server chose."""
    assert type(error) in (fetch_download.FetchError, fetch_download.AnswerError), repr(type(error))
    assert error.__cause__ is None and error.__context__ is None, repr(error)
    # built afresh and raised by fetch itself after its handler, so its traceback starts there, past no library frame
    assert [frame.name for frame in traceback.extract_tb(error.__traceback__)] == ["checked", "fetch"], repr(error)
    assert server_words(everything_said(error, given=given)) == [], repr(error)
    if type(error) is fetch_download.FetchError:
        assert error.args == (str(error),) and set(vars(error)) == {"failure"}, repr(error)
        assert error.failure is None or error.failure in {sentence for _, sentence in fetch_download.FAILURES} | {
            fetch_download.UNKNOWN_FAILURE} or error.failure.startswith("the server answered HTTP 3"), error.failure  # fmt: skip
    else:
        assert error.url == error.filename == error.geturl() == entry.url, error.url
        assert type(error.code) is int and error.fp.getvalue() == b""  # urllib gives an HTTPError with no body an empty buffer
        assert set(error.headers.keys()) <= {"Retry-After"}
        assert all(fetch_download.RETRY_AFTER_SECONDS.fullmatch(value) for value in error.headers.values())


@pytest.fixture(autouse=True)
def every_error_fetch_raises_is_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every call of fetch in this file, through main or not, has what it raises checked by assert_the_scripts_own."""
    real = fetch_download.fetch

    @functools.wraps(real)
    def checked(entry: Any, *args: Any, **kwargs: Any) -> Any:
        bound = inspect.signature(real).bind(entry, *args, **kwargs)
        bound.apply_defaults()
        try:
            return real(entry, *args, **kwargs)
        except BaseException as error:
            assert_the_scripts_own(error, entry, given=dict(bound.arguments))
            raise

    monkeypatch.setattr(fetch_download, "fetch", checked)


@pytest.fixture(autouse=True)
def wire(monkeypatch: pytest.MonkeyPatch) -> Iterator[Wire]:
    """Every test of this file runs with no proxy and inside loopback_only."""
    for name in list(os.environ):
        if name.lower().endswith("_proxy"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("no_proxy", "*")  # also keeps urllib from reading the system's proxy settings
    with loopback_only(Wire()) as recorded:
        yield recorded


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves `routes`: a path (its query left out) maps to bytes, answered 200 with that body, to text, answered 302
    with that Location, to a status code and a Location, or to a status code alone, answered with no Location at
    all; any other path is answered 404. Records each request's path and headers."""

    routes: dict[str, bytes | str | int | tuple[int, str]] = {}
    seen: list[tuple[str, dict[str, str]]] = []

    def do_GET(self) -> None:  # noqa: N802 (the http.server API)
        kind = type(self)
        kind.seen.append((self.path, dict(self.headers.items())))
        answer = kind.routes.get(self.path.split("?", 1)[0])
        if isinstance(answer, WireAnswer):
            self.wfile.write(answer.data)
            self.close_connection = True
            return
        if isinstance(answer, (str, int, tuple)):
            code, location = (answer, None) if isinstance(answer, int) else (302, answer) if isinstance(answer, str) else answer
            self.send_response(code)
            if location is not None:
                self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = answer if answer is not None else b"not here"
        self.send_response(200 if answer is not None else 404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 (the http.server API)
        return


class WireAnswer:
    """An answer the test server writes exactly as given: its status line, headers and body, or text that is not HTTP."""

    def __init__(self, data: bytes) -> None:
        self.data = data


class _Server(http.server.ThreadingHTTPServer):
    """The test server, bound without the lookup of its own name that http.server makes by default."""

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


@pytest.fixture(scope="module")
def server_port() -> Iterator[int]:
    httpd = _Server(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def server(server_port: int, wire: Wire) -> str:
    _Handler.routes = {"/payload.bin": PAYLOAD, "/short.bin": PAYLOAD[:100]}
    _Handler.seen = []
    wire.server_port = server_port
    return f"http://127.0.0.1:{server_port}"


def download(url: Any, path: str = PATH, **changes: object):
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


def nothing_written(root: Path) -> bool:
    """No file and no folder under root: a refused download leaves nothing behind."""
    return list(root.rglob("*")) == []


def test_the_stand_in_reads_the_host_and_port_as_https_does() -> None:
    assert http.client.HTTPSConnection.default_port == 443
    assert http.client.HTTPSConnection._get_hostport is http.client.HTTPConnection._get_hostport


# =============================================================================================
# Fetching
# =============================================================================================


def test_a_fetch_writes_the_file_only_once_its_size_and_sha256_match(tmp_path: Path, server: str) -> None:
    target = fetch_download.fetch(download(f"{server}/payload.bin"), root=tmp_path, loopback_for_tests=True)
    assert target == tmp_path / ".cache" / "test" / "payload.bin"
    assert target.read_bytes() == PAYLOAD
    assert list(target.parent.iterdir()) == [target]


def test_requests_carry_the_project_user_agent_and_nothing_else_identifying(tmp_path: Path, server: str) -> None:
    fetch_download.fetch(download(f"{server}/payload.bin"), root=tmp_path, loopback_for_tests=True)
    assert [headers.get("User-Agent") for _, headers in _Handler.seen] == [USER_AGENT]


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
        fetch_download.fetch(download(f"{server}/{served}", **changes), root=tmp_path, loopback_for_tests=True)
    folder = tmp_path / ".cache" / "test"
    assert not folder.exists() or list(folder.iterdir()) == []


def test_a_response_longer_than_listed_stops_at_the_listed_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The size check runs on each block as it arrives, so a response that does not end is cut off at once and
    never written out in full first (the auditor's 2d test)."""
    target = tmp_path / ".cache" / "test" / "payload.bin"
    part = target.with_name(target.name + ".part")
    reads: list[int] = []
    largest_part = [0]

    class LongResponse:
        """Serves 64 blocks of the size asked for: far more than listed, finite so a missing cap cannot hang the test."""

        def read(self, size: int) -> bytes:
            if part.exists():
                largest_part[0] = max(largest_part[0], part.stat().st_size)
            reads.append(size)
            return b"x" * size if len(reads) <= 64 else b""

        def __enter__(self) -> LongResponse:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(fetch_download.urllib.request.OpenerDirector, "open", lambda *args, **kwargs: LongResponse())
    with pytest.raises(fetch_download.FetchError, match="more than the"):
        fetch_download.fetch(download(f"https://{STAND_IN_HOST}/payload.bin"), root=tmp_path)
    assert len(reads) == 1, "the first block past the listed size ends the download"
    assert largest_part[0] <= len(PAYLOAD)
    assert not part.exists() and not target.exists()


@pytest.mark.parametrize("path", ["data/payload.bin", ".cache/../data/payload.bin", "/tmp/payload.bin", ".cache"])
def test_a_path_outside_the_cache_is_refused_before_any_request(tmp_path: Path, server: str, wire: Wire, path: str) -> None:
    with pytest.raises(fetch_download.FetchError, match=r"not under \.cache/"):
        fetch_download.fetch(download(f"{server}/payload.bin", path=path), root=tmp_path, loopback_for_tests=True)
    assert _Handler.seen == [] and wire.connections == []


def test_a_verified_file_already_present_is_kept_without_a_request(tmp_path: Path, server: str, wire: Wire) -> None:
    target = tmp_path / ".cache" / "test" / "payload.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(PAYLOAD)
    assert fetch_download.fetch(download(f"{server}/payload.bin"), root=tmp_path, loopback_for_tests=True) == target
    assert _Handler.seen == [] and wire.connections == []


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
# Where the first request may go
# =============================================================================================

DOT = chr(0x3002)  # the ideographic full stop, which the resolver's own name handling reads as a dot
FIRST_URLS_REFUSED = [
    # another scheme, or plain http off the loopback
    "http://files.example/x.bin",
    "ftp://files.example/x.bin",
    "HTTPS://files.example/x.bin",
    # another spelling of the host: upper case, a trailing dot, an escaped or foreign dot, an address
    "https://FILES.example/x.bin",
    "https://Files.example/x.bin",
    "https://files.example./x.bin",
    "https://files%2Eexample/x.bin",
    f"https://files{DOT}example/x.bin",
    "https://127.0.0.1/x.bin",
    "https://[::1]/x.bin",
    "https://localhost/x.bin",
    "https://2130706433/x.bin",
    "https://0x7f.0.0.1/x.bin",
    "https://files.example\\@evil.example/x.bin",
    # a port, even the default one
    "https://files.example:443/x.bin",
    "https://files.example:8443/x.bin",
    "https://files.example:/x.bin",
    # a user or password part: http.client takes it for part of the host name
    "https://someone@files.example/x.bin",
    "https://user:secret@files.example/x.bin",
    "https://files.example@evil.example/x.bin",
    # a fragment, a wrapper, or no host where one is expected
    "https://files.example/x.bin#f",
    " https://files.example/x.bin",
    "<URL:https://files.example/x.bin>",
    "https:files.example/x.bin",
    "https:///x.bin",
    "",
    None,
    b"https://files.example/x.bin",
    ["https://files.example/x.bin"],
    # the loopback exception is plain http on 127.0.0.1 with its port in plain digits, and nothing near it
    "http://localhost:8123/x.bin",
    "http://[::1]:8123/x.bin",
    "http://127.0.0.2:8123/x.bin",
    "http://127.1:8123/x.bin",
    "http://127.0.0.1/x.bin",
    "http://127.0.0.1:/x.bin",
    "http://127.0.0.1:0/x.bin",
    "http://127.0.0.1:65536/x.bin",
    "http://127.0.0.1:08123/x.bin",
    "http://127.0.0.1:+8123/x.bin",
    "http://127.0.0.1:" + "".join(chr(0x0966 + int(digit)) for digit in "8123") + "/x.bin",
    "http://someone@127.0.0.1:8123/x.bin",
    "https://127.0.0.1:8123/x.bin",
    "http://127.0.0.1:8123/x.bin#f",
]
FIRST_URLS_ASKED = [
    ("https://files.example/x.bin", ("files.example", 443)),
    ("https://files.example/", ("files.example", 443)),
    ("https://a-b.c0.example/x?y=1&z=(2,3)%20+*", ("a-b.c0.example", 443)),
    ("http://127.0.0.1:8123/x.bin", ("127.0.0.1", 8123)),
    ("http://127.0.0.1:1/x.bin", ("127.0.0.1", 1)),
    ("http://127.0.0.1:65535/x.bin", ("127.0.0.1", 65535)),
]
FIRST_REFUSED = (
    "refused {named}, since a download starts only at https on a lower-case host name with no user or password "
    "part, no port and no fragment (or, for tests, plain http on 127.0.0.1 with its port)"
)


@pytest.mark.parametrize("url", FIRST_URLS_REFUSED, ids=ascii)
def test_a_first_url_outside_the_rule_is_refused_before_any_request(tmp_path: Path, wire: Wire, url: Any) -> None:
    """Refused even with the tests' loopback exception on, so nothing near 127.0.0.1 passes for it."""
    with pytest.raises(fetch_download.FetchError) as refused:
        fetch_download.fetch(download(url), root=tmp_path, loopback_for_tests=True)
    assert (wire.connections, wire.lookups) == ([], [])
    assert str(refused.value).startswith(f"{PATH}: refused ") and "secret" not in str(refused.value)
    assert nothing_written(tmp_path)


@pytest.mark.parametrize(("url", "reached"), FIRST_URLS_ASKED, ids=[url for url, _ in FIRST_URLS_ASKED])
def test_a_first_url_inside_the_rule_is_asked_of_its_own_host_and_port(tmp_path: Path, wire: Wire, url: str, reached: tuple) -> None:
    """Every connection is cut here (with an OSError), so each ends in the script's sentence for it after exactly one
    connection attempt."""
    with pytest.raises(fetch_download.FetchError) as failed:
        fetch_download.fetch(download(url), root=tmp_path, loopback_for_tests=True)
    assert failed.value.failure == "a network or file error stopped the download"
    assert (wire.connections, wire.lookups) == ([reached], [])
    assert nothing_written(tmp_path)


LOOPBACK_URLS = [url for url, (host, _) in FIRST_URLS_ASKED if host == "127.0.0.1"]


@pytest.mark.parametrize("url", LOOPBACK_URLS)
def test_a_loopback_url_is_refused_unless_the_caller_asks_for_the_tests_exception(tmp_path: Path, wire: Wire, url: str) -> None:
    with pytest.raises(fetch_download.FetchError) as refused:
        fetch_download.fetch(download(url), root=tmp_path)
    assert str(refused.value) == f"{PATH}: " + FIRST_REFUSED.format(named=f"http://{urlsplit(url).netloc}")
    assert (wire.connections, wire.lookups) == ([], []) and nothing_written(tmp_path)


def test_the_command_refuses_a_loopback_entry_in_a_registry_on_disk(
    tmp_path: Path, server: str, wire: Wire, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The auditor's case: an entry naming the tests' own server, written to a registry on disk and fetched by the
    command itself, opens no connection: no option, no environment variable and no registry field can turn the
    loopback exception on."""
    entry = {"path": ".cache/test/loopback.bin", "fetch": "scripts/fetch_download.py", "url": f"{server}/payload.bin?action=anything",
             "bytes": len(PAYLOAD), "sha256": hashlib.sha256(PAYLOAD).hexdigest(), "licence": "CC0-1.0",
             "licenceUrl": "https://creativecommons.org/publicdomain/zero/1.0/", "source": "test payload",
             "loopbackForTests": True}  # fmt: skip
    registry = tmp_path / "downloads.json"
    registry.write_text(json.dumps({"downloads": [entry]}), encoding="utf-8")
    monkeypatch.setattr(fetch_download, "REGISTRY", registry)
    monkeypatch.setattr(fetch_download, "ROOT", tmp_path / "root")
    for name in ("ALBEDIAN_LOOPBACK_FOR_TESTS", "LOOPBACK_FOR_TESTS", "loopback_for_tests"):
        monkeypatch.setenv(name, "1")
    assert fetch_download.main([entry["path"]]) == 1
    said = capsys.readouterr().err
    assert said == f"{entry['path']}: " + FIRST_REFUSED.format(named=server) + "\n"
    assert (_Handler.seen, wire.connections, wire.lookups) == ([], [], [])
    assert not (tmp_path / "root").exists()


def test_nothing_but_a_callers_own_argument_turns_the_loopback_exception_on(capsys: pytest.CaptureFixture[str]) -> None:
    for function in (fetch_download.fetch, fetch_download.destination):
        argument = inspect.signature(function).parameters["loopback_for_tests"]
        assert (argument.kind, argument.default) == (inspect.Parameter.KEYWORD_ONLY, False), function.__name__
    assert "loopback" not in str(inspect.signature(fetch_download.main))
    source = (ROOT / "scripts" / "fetch_download.py").read_text(encoding="utf-8")
    assert "os.environ" not in source and "getenv" not in source and "\nimport os" not in source
    assert "loopback" not in inspect.getsource(fetch_download.main), "main never passes it"
    assert "loopback" not in inspect.getsource(fetch_download.load_registry) + inspect.getsource(fetch_download.Download)
    with pytest.raises(SystemExit):
        fetch_download.main(["--help"])
    # The options section only: the usage line names the program as Python was started (python -m pytest has a -m)
    options_section = capsys.readouterr().out.split("options:", 1)[1]
    options = [word for word in options_section.split() if word.startswith("-")]
    assert options == ["-h,", "--help"]
    with pytest.raises(SystemExit):
        fetch_download.main(["--loopback-for-tests", ".cache/test/payload.bin"])


@pytest.mark.parametrize(
    ("url", "named"),
    [
        ("http://files.example/x.bin", "http://files.example"),
        ("https://user:secret@files.example/x.bin", "https://files.example with a user or password part"),
        ("https://files.example:8443/x.bin", "https://files.example:8443"),
        ("", "a URL that cannot be read"),
        ("https:files.example/x.bin", "a target that is not a plain https address"),
        ("itisfine://files.example/x.bin", "a target that is not a plain https address"),
        (f"https://files{DOT}example/x.bin", "a target that is not a plain https address"),
        ("https://files.example, since it is the same server. The file is fine/x", "a target that is not a plain https address"),
        ("h t t p s://files.example/x", "a target that is not a plain https address"),
        # Names longer than DNS takes (253) are not plain: here over plain http, which the rule refuses anyway
        ("http://" + ".".join(["a" * 60] * 5) + "/x", "a target that is not a plain https address"),
        ("http://" + ".".join(["a" * 60] * 4) + ".example/x", "http://" + ".".join(["a" * 60] * 4) + ".example"),
    ],
)
def test_a_refused_first_url_is_named_in_one_plain_sentence(tmp_path: Path, url: str, named: str) -> None:
    with pytest.raises(fetch_download.FetchError) as refused:
        fetch_download.fetch(download(url), root=tmp_path)
    assert str(refused.value) == f"{PATH}: " + FIRST_REFUSED.format(named=named)


def test_destination_reads_scheme_host_and_port_or_refuses() -> None:
    """The rule itself, on the same tables: the reading of a URL inside it, and None for each one outside it that
    urllib.request can build a request from at all."""
    for url, reached in FIRST_URLS_ASKED:
        target = (urlsplit(url).scheme, *reached)
        assert fetch_download.destination(url, urllib.request.Request(url), loopback_for_tests=True) == target, url
        assert fetch_download.destination(url, urllib.request.Request(url)) == (None if reached[0] == "127.0.0.1" else target), url
    built = 0
    for url in FIRST_URLS_REFUSED:
        try:
            request = urllib.request.Request(url)
        except (ValueError, TypeError, AttributeError):
            continue
        built += 1
        assert fetch_download.destination(url, request, loopback_for_tests=True) is None, ascii(url)
        assert fetch_download.destination(url, request) is None, ascii(url)
    assert built >= len(FIRST_URLS_REFUSED) - 3


def test_every_registry_url_passes_the_rule_and_is_asked_only_of_its_own_host(tmp_path: Path, wire: Wire) -> None:
    for entry in ENTRIES:
        wire.clear()
        with pytest.raises(fetch_download.FetchError) as failed:
            fetch_download.fetch(fetch_download.Download.from_entry(entry), root=tmp_path)
        assert failed.value.failure == "a network or file error stopped the download", entry["path"]
        split = urlsplit(entry["url"])
        assert (split.scheme, split.port, split.username, split.password) == ("https", None, None, None), entry["path"]
        assert (wire.connections, wire.lookups) == ([(split.hostname, 443)], []), entry["path"]
    assert nothing_written(tmp_path)


# =============================================================================================
# Redirects
# =============================================================================================

MAX = fetch_download.MAX_REDIRECTS
CHAIN_IDS = itertools.count(1)
ORIGINS = ("https", "loopback")


def fields(origin: str, port: int, chain: int, step: int) -> dict[str, Any]:
    """The words a redirect template is written in: the origin the download started at, and the next step."""
    scheme, host = ("https", STAND_IN_HOST) if origin == "https" else ("http", "127.0.0.1")
    authority = host if origin == "https" else f"{host}:{port}"
    other_port = port + 1 if port < 65535 else port - 1
    return {
        "scheme": scheme, "SCHEME": scheme.upper(), "host": host, "authority": authority, "port": port,
        "other_port": other_port, "wrapped_port": port + 65536, "id": chain, "next": step + 1,
    }  # fmt: skip


def reached_by(origin: str, port: int) -> tuple[str, int]:
    return (STAND_IN_HOST, 443) if origin == "https" else ("127.0.0.1", port)


def start_of(origin: str, port: int, chain: int) -> str:
    return f"https://{STAND_IN_HOST}/c/{chain}/0" if origin == "https" else f"http://127.0.0.1:{port}/c/{chain}/0"


def origin_named(origin: str, port: int) -> str:
    return f"https://{STAND_IN_HOST}" if origin == "https" else f"http://127.0.0.1:{port}"


def serve_chain(origin: str, port: int, locations: list[Any]) -> tuple[int, str]:
    """Route /c/<chain>/<k> to a 302 with the k-th Location (a template filled in with fields(), or raw text as it is),
    and the step after the last to the payload. Returns the chain id and the URL the download starts at."""
    chain = next(CHAIN_IDS)
    for step, location in enumerate(locations):
        text = location.format(**fields(origin, port, chain, step)) if isinstance(location, str) else location.text
        _Handler.routes[f"/c/{chain}/{step}"] = text
    _Handler.routes[f"/c/{chain}/{len(locations)}"] = PAYLOAD
    return chain, start_of(origin, port, chain)


class Raw:
    """A Location sent exactly as written, not filled in as a template."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __repr__(self) -> str:
        return f"Raw({self.text!r})"


# Templates the rule follows: each leads to the next step of the chain at the origin the download started at
FOLLOWED = [
    "{scheme}://{authority}/c/{id}/{next}",
    "{SCHEME}://{authority}/c/{id}/{next}",  # urllib lower-cases the scheme of a Location before it is read
    "/c/{id}/{next}",
    "{next}",
    "./{next}",
    "../{id}/{next}",
    "//{authority}/c/{id}/{next}",
    "{scheme}://{authority}/c/{id}/{next}?x=1&y=%20",
    "{next}?q=a/b",
    "/c/{id}/{next}#",  # an empty fragment: urllib's own urlparse and urlunparse drop it before the target is read
]
# Templates the rule refuses at either origin
REFUSED_ANYWHERE = [
    "{scheme}://{authority}/c/{id}/{next}#f",
    "/c/{id}/{next}#f",
    "{next}#x",
    "{scheme}://someone@{authority}/c/{id}/{next}",
    "{scheme}://user:secret@{authority}/c/{id}/{next}",
    "{scheme}://someone@user:secret@{authority}/c/{id}/{next}",
    "{scheme}://@{authority}/c/{id}/{next}",
    "//someone@{authority}/c/{id}/{next}",
    "{scheme}://{authority}@evil.example/c/{id}/{next}",
    "{scheme}://evil.example@{authority}/c/{id}/{next}",
    "{scheme}://{authority}\\@evil.example/c/{id}/{next}",
    "{scheme}://evil.example/c/{id}/{next}",
    "//evil.example/c/{id}/{next}",
    "{scheme}://{authority}.evil.example/c/{id}/{next}",
    "{scheme}://{authority}./c/{id}/{next}",
    "ftp://{authority}/c/{id}/{next}",
    "gopher://{authority}/c/{id}/{next}",
    "file:///c/{id}/{next}",
    "javascript:void(0)",
    "data:,x",
    "{scheme}://[/c/{id}/{next}",
    "//[{host}/c/{id}/{next}",
]
# Templates the rule refuses when the download started at https://files.example
REFUSED_FROM_HTTPS = [
    "http://{authority}/c/{id}/{next}",
    "HTTP://{authority}/c/{id}/{next}",
    "http://127.0.0.1:{port}/c/{id}/{next}",
    "https://{host}:443/c/{id}/{next}",
    "https://{host}:8443/c/{id}/{next}",
    "https://{host}:/c/{id}/{next}",
    "https://FILES.example/c/{id}/{next}",
    "https://Files.example/c/{id}/{next}",
    "https://files%2Eexample/c/{id}/{next}",
    "https://files.example.evil.example/c/{id}/{next}",
    "https://127.0.0.1/c/{id}/{next}",
    "https://[::1]/c/{id}/{next}",
    "https://localhost/c/{id}/{next}",
    "//{host}:443/c/{id}/{next}",
    "//FILES.EXAMPLE/c/{id}/{next}",
]
# Templates the rule refuses when the download started at the tests' own http://127.0.0.1:<port>
REFUSED_FROM_LOOPBACK = [
    "https://{authority}/c/{id}/{next}",
    "https://files.example/c/{id}/{next}",
    "http://localhost:{port}/c/{id}/{next}",
    "http://127.0.0.2:{port}/c/{id}/{next}",
    "http://127.1:{port}/c/{id}/{next}",
    "http://[::1]:{port}/c/{id}/{next}",
    "http://[::ffff:127.0.0.1]:{port}/c/{id}/{next}",
    "http://0x7f.0.0.1:{port}/c/{id}/{next}",
    "http://2130706433:{port}/c/{id}/{next}",
    "http://127.000.000.001:{port}/c/{id}/{next}",
    "http://127.0.0.1.:{port}/c/{id}/{next}",
    "http://127%2E0%2E0%2E1:{port}/c/{id}/{next}",
    "http://127.0.0.1:{other_port}/c/{id}/{next}",
    "http://127.0.0.1/c/{id}/{next}",
    "http://127.0.0.1:/c/{id}/{next}",
    "http://127.0.0.1:0{port}/c/{id}/{next}",
    "http://127.0.0.1:+{port}/c/{id}/{next}",
    "http://127.0.0.1:{port}:{port}/c/{id}/{next}",
    "http://127.0.0.1:{wrapped_port}/c/{id}/{next}",
    "//localhost:{port}/c/{id}/{next}",
    "//127.0.0.1:{other_port}/c/{id}/{next}",
]
REFUSED = {"https": REFUSED_ANYWHERE + REFUSED_FROM_HTTPS, "loopback": REFUSED_ANYWHERE + REFUSED_FROM_LOOPBACK}
ALLOWED_HEADERS = {"Host", "User-Agent", "Accept-Encoding", "Connection"}


# urllib itself declines a redirect to a scheme other than http, https or ftp (urllib/request.py, http_error_302) and
# raises its HTTPError, which the boundary says by the number alone, reading no header
NOT_FOLLOWED = PATH + ": not fetched: the server answered HTTP {code}, a redirect urllib does not follow or one with no Location"
DECLINED = "declined by urllib"


def declined_by_urllib(template: Any) -> bool:
    scheme = template.split(":", 1)[0].lower() if isinstance(template, str) and re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", template) else ""
    return scheme not in ("", "http", "https", "ftp")


def redirect_refusal(named: str, origin: str, port: int) -> str:
    return (
        f"{PATH}: refused a redirect to {named}, since redirects are followed only to {origin_named(origin, port)}, "
        "with no user or password part, no other port and no fragment"
    )


def run_chain(origin: str, port: int, wire: Wire, locations: list[Any]) -> tuple[BaseException | None, list[str], bool]:
    """Fetch a download that starts at a chain of redirects: what it raised (None when it kept the file), the paths
    the server was asked for, and whether the file is there. Nothing else is ever left under the folder."""
    _Handler.seen.clear()
    wire.clear()
    _, start = serve_chain(origin, port, locations)
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        try:
            fetch_download.fetch(download(start), root=root, loopback_for_tests=True)
            raised = None
        except Exception as error:  # noqa: BLE001 (the outcome is what is checked)
            raised = error
            if isinstance(error, urllib.error.HTTPError):
                error.close()
        kept = (root / PATH).is_file()
        left = sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())
        if isinstance(raised, fetch_download.FetchError) and "refused a redirect" in str(raised):
            assert nothing_written(root), "a refused redirect leaves no file and no folder"
    assert left == ([PATH] if kept else []), left
    assert kept == (raised is None)
    return raised, [path for path, _ in _Handler.seen], kept


def expected(locations: list[tuple[str, bool]]) -> int:
    """How many redirects the rule follows in this chain, by the templates' own labels and the stated maximum."""
    followed = 0
    for _, follows in locations:
        if not follows or followed == MAX:
            break
        followed += 1
    return followed


@pytest.mark.parametrize("origin", ORIGINS)
@pytest.mark.parametrize("template", FOLLOWED)
def test_a_redirect_inside_the_rule_is_followed(server_port: int, server: str, wire: Wire, origin: str, template: str) -> None:
    raised, asked, kept = run_chain(origin, server_port, wire, [template])
    assert raised is None and kept
    assert [path.split("?", 1)[0].rsplit("/", 1)[1] for path in asked] == ["0", "1"]
    assert wire.connections == [reached_by(origin, server_port)] * 2
    assert wire.lookups == ([] if origin == "https" else ["127.0.0.1"] * 2)


@pytest.mark.parametrize(
    ("origin", "template"), [(origin, template) for origin in ORIGINS for template in REFUSED[origin]], ids=str
)
def test_a_redirect_outside_the_rule_is_refused_before_a_connection(server_port: int, server: str, wire: Wire, origin: str, template: str) -> None:
    raised, asked, kept = run_chain(origin, server_port, wire, [template])
    assert isinstance(raised, fetch_download.FetchError), repr(raised)
    assert len(asked) == 1 and not kept
    assert wire.connections == [reached_by(origin, server_port)]
    message = str(raised)
    if declined_by_urllib(template):
        assert message == NOT_FOLLOWED.format(code=302)
    else:
        assert message.startswith(f"{PATH}: refused a redirect to ") and "\n" not in message
    assert "secret" not in message and "someone" not in message


@pytest.mark.parametrize(
    ("origin", "template", "named"),
    [
        ("https", "https://evil.example/c/{id}/{next}", "https://evil.example"),
        ("https", "http://{authority}/c/{id}/{next}", "http://files.example"),
        ("https", "https://user:secret@{authority}/c/{id}/{next}", "https://files.example with a user or password part"),
        ("https", "https://{authority}@evil.example/c/{id}/{next}", "https://evil.example with a user or password part"),
        ("https", "https://{host}:8443/c/{id}/{next}", "https://files.example:8443"),
        ("https", "//FILES.EXAMPLE/c/{id}/{next}", "https://FILES.EXAMPLE"),
        # A target that is not a plain host name or address is never repeated: its words are the server's
        ("https", "https://other.example, since it is the same server. The file is fine/x", "a target that is not a plain https address"),
        ("https", "https://files.example%20is%20fine/x", "a target that is not a plain https address"),
        ("https", "https://user:secret@a%20b/x", "a target that is not a plain https address"),
        ("loopback", "http://[::1]:{port}/c/{id}/{next}", "http://[::1]:{port}"),
        # A scheme is the server's word unless it is literally https or http
        ("loopback", "gopher://{authority}/c/{id}/{next}", DECLINED),
        ("loopback", "javascript:void(0)", DECLINED),
        ("https", "itisfine://files.example/x", DECLINED),
        ("https", "zqxezalee:anything", DECLINED),
        ("https", "ftp://files.example/x", "a target that is not a plain https address"),
        ("loopback", "http://localhost:{port}/c/{id}/{next}", "http://localhost:{port}"),
        ("loopback", "http://[::1/c/{id}/{next}", "a URL that cannot be read"),
        ("https", "//[{host}/c/{id}/{next}", "a URL that cannot be read"),
    ],
)
def test_a_refused_redirect_names_the_host_in_one_plain_sentence(
    server_port: int, server: str, wire: Wire, origin: str, template: str, named: str
) -> None:
    raised, _, _ = run_chain(origin, server_port, wire, [template])
    if named == DECLINED:
        assert declined_by_urllib(template) and str(raised) == NOT_FOLLOWED.format(code=302)
    else:
        assert not declined_by_urllib(template) and str(raised) == redirect_refusal(named.format(port=server_port), origin, server_port)


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
@pytest.mark.parametrize(
    ("location", "named"),
    [
        ("/c/{id}/1", None),
        ("http://{authority}/c/{id}/1", "http://files.example"),
        ("https://evil.example/c/{id}/1", "https://evil.example"),
        ("https://[{authority}/c/{id}/1", "a URL that cannot be read"),
    ],
)
def test_every_redirect_code_urllib_follows_is_held_to_the_rule(
    server_port: int, server: str, wire: Wire, code: int, location: str, named: str | None
) -> None:
    chain, start = serve_chain("https", server_port, ["{next}"])
    _Handler.routes[f"/c/{chain}/0"] = (code, location.format(**fields("https", server_port, chain, 0)))
    _Handler.seen.clear()
    wire.clear()
    with tempfile.TemporaryDirectory() as folder:
        try:
            fetch_download.fetch(download(start), root=Path(folder), loopback_for_tests=True)
            said = None
        except fetch_download.FetchError as error:
            said = str(error)
            assert nothing_written(Path(folder))
    assert said == (None if named is None else redirect_refusal(named, "https", server_port))
    assert wire.connections == [(STAND_IN_HOST, 443)] * (2 if named is None else 1)


def test_a_redirect_from_https_to_http_is_refused(server_port: int, server: str, wire: Wire) -> None:
    raised, asked, kept = run_chain("https", server_port, wire, ["http://{authority}/c/{id}/{next}"])
    assert str(raised) == redirect_refusal("http://files.example", "https", server_port)
    assert wire.connections == [(STAND_IN_HOST, 443)] and len(asked) == 1 and not kept


@pytest.mark.parametrize("origin", ORIGINS)
def test_at_most_the_stated_number_of_redirects_is_followed(server_port: int, server: str, wire: Wire, origin: str) -> None:
    assert MAX == 3
    raised, asked, kept = run_chain(origin, server_port, wire, ["{next}"] * MAX)
    assert raised is None and kept and len(asked) == MAX + 1
    raised, asked, kept = run_chain(origin, server_port, wire, ["{next}"] * (MAX + 1))
    assert str(raised) == f"{PATH}: refused a redirect to {origin_named(origin, server_port)}, since at most 3 redirects are followed"
    assert len(asked) == MAX + 1 and not kept
    assert wire.connections == [reached_by(origin, server_port)] * (MAX + 1)


def test_a_redirect_loop_ends_at_the_stated_number(server_port: int, server: str, wire: Wire) -> None:
    raised, asked, _ = run_chain("https", server_port, wire, ["/c/{id}/0"])
    assert str(raised).endswith("since at most 3 redirects are followed")
    assert len(asked) == MAX + 1


def test_an_empty_location_leads_back_to_the_same_url_and_ends_at_the_stated_number(server_port: int, server: str, wire: Wire) -> None:
    raised, asked, _ = run_chain("loopback", server_port, wire, [Raw("")])
    assert str(raised) == f"{PATH}: refused a redirect to {origin_named('loopback', server_port)}, since at most 3 redirects are followed"
    assert len(set(asked)) == 1 and len(asked) == MAX + 1


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308, 300])
def test_a_redirect_with_no_location_is_refused(tmp_path: Path, server_port: int, server: str, wire: Wire, code: int) -> None:
    chain, start = serve_chain("https", server_port, [])
    _Handler.routes[f"/c/{chain}/0"] = code
    with pytest.raises(fetch_download.FetchError) as refused:
        fetch_download.fetch(download(start), root=tmp_path)
    assert str(refused.value) == f"{PATH}: not fetched: the server answered HTTP {code}, a redirect urllib does not follow or one with no Location"
    assert nothing_written(tmp_path) and wire.connections == [(STAND_IN_HOST, 443)]


@pytest.mark.parametrize("code", [300, 399])
def test_both_ends_of_the_redirect_range_are_redirects_the_script_refuses_in_its_own_words(
    tmp_path: Path, server_port: int, server: str, wire: Wire, code: int
) -> None:
    """A 300 and a 399, with a Location to another host or with none, are said by their number alone: urllib follows
    neither, and the boundary reads no header but Retry-After. Neither ends as urllib's error."""
    chain, start = serve_chain("https", server_port, [])
    for answer in ((code, "https://other.example/x"), code):
        _Handler.routes[f"/c/{chain}/0"] = answer
        with pytest.raises(fetch_download.FetchError) as refused:
            fetch_download.fetch(download(start), root=tmp_path)
        assert str(refused.value) == f"{PATH}: not fetched: the server answered HTTP {code}, a redirect urllib does not follow or one with no Location"
    assert nothing_written(tmp_path)


def test_an_empty_location_on_a_code_urllib_does_not_follow_is_a_refusal_of_its_own(
    tmp_path: Path, server_port: int, server: str, wire: Wire
) -> None:
    """A 300 that names an empty Location is said by its number, like any 3xx urllib does not follow."""
    chain, start = serve_chain("https", server_port, [])
    _Handler.routes[f"/c/{chain}/0"] = (300, "")
    code = 300
    with pytest.raises(fetch_download.FetchError) as refused:
        fetch_download.fetch(download(start), root=tmp_path)
    assert str(refused.value) == f"{PATH}: not fetched: the server answered HTTP {code}, a redirect urllib does not follow or one with no Location"
    assert nothing_written(tmp_path)


def run_command(monkeypatch: pytest.MonkeyPatch, folder: Path, url: str) -> tuple[int, list[str]]:
    """The command itself, on a registry on disk with one entry at `url`, writing under `folder`: its exit code and
    every line it printed, to either stream."""
    entry = {"path": ".cache/test/answer.bin", "fetch": "scripts/fetch_download.py", "url": url, "bytes": len(PAYLOAD),
             "sha256": hashlib.sha256(PAYLOAD).hexdigest(), "licence": "CC0-1.0",
             "licenceUrl": "https://creativecommons.org/publicdomain/zero/1.0/", "source": "test payload"}  # fmt: skip
    registry = folder / "downloads.json"
    registry.write_text(json.dumps({"downloads": [entry]}), encoding="utf-8")
    monkeypatch.setattr(fetch_download, "REGISTRY", registry)
    monkeypatch.setattr(fetch_download, "ROOT", folder / "root")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = fetch_download.main([entry["path"]])
    return code, out.getvalue().splitlines() + err.getvalue().splitlines()


def wire_answer(code: int, reason: str, headers: list[str], body: bytes) -> WireAnswer:
    head = f"HTTP/1.1 {code} {reason}\r\n" + "".join(f"{header}\r\n" for header in headers)
    head += f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    return WireAnswer(head.encode("latin-1") + body)


def server_words_in(lines: list[str]) -> list[str]:
    return server_words(lines)


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        (wire_answer(404, f"Not Found {MARK}", [f"X-{MARK}: {MARK}"], MARK.encode()), "not fetched: the server answered HTTP 404"),
        (wire_answer(500, f"\x1b[2J{MARK}", [], b""), "not fetched: the server answered HTTP 500"),
        (wire_answer(429, MARK, [f"Retry-After: {MARK}"], b""), "not fetched: the server answered HTTP 429"),
        (wire_answer(200, MARK, [], MARK.encode()), f"got {len(MARK)} bytes, the registry lists {len(PAYLOAD)}"),
        (WireAnswer(f"NOT-HTTP {MARK}\r\n\r\n".encode()), "not fetched: the server's answer does not begin with an HTTP status line"),
    ],
    ids=["404", "500 with a control character", "429", "short body", "not http"],
)
def test_the_command_reports_an_answer_by_its_number_and_its_own_words(
    tmp_path: Path, server: str, wire: Wire, monkeypatch: pytest.MonkeyPatch, answer: WireAnswer, said: str
) -> None:
    _Handler.routes["/answer.bin"] = answer
    code, lines = run_command(monkeypatch, tmp_path, f"https://{STAND_IN_HOST}/answer.bin")
    assert code == 1 and lines[-1] == f".cache/test/answer.bin: {said}" and len(lines) == 2
    assert server_words_in(lines) == [] and wire.connections == [(STAND_IN_HOST, 443)]


@pytest.mark.parametrize(
    ("url", "said"),
    [(f"https://{STAND_IN_HOST}/a b.bin", "the request's URL holds a character http.client cannot send"),
     (f"https://{STAND_IN_HOST}/caf\u00e9.bin", "the request's URL holds a character http.client cannot send")],
    ids=["a space", "a letter outside ASCII"],
)
def test_a_path_http_client_cannot_send_ends_in_one_line_not_a_traceback(
    tmp_path: Path, server: str, wire: Wire, monkeypatch: pytest.MonkeyPatch, url: str, said: str
) -> None:
    """The first URL passes the rule (its host and port are plain) but http.client refuses to write its path: the
    command says so in the script's own sentence and opens no connection."""
    code, lines = run_command(monkeypatch, tmp_path, url)
    assert (code, lines[-1], len(lines)) == (1, f".cache/test/answer.bin: not fetched: {said}", 2)
    assert wire.connections == []


def spoken(error: BaseException) -> list[str]:
    return everything_said(error)


@pytest.mark.parametrize(
    ("answer", "kind", "said", "retry_after"),
    [
        (wire_answer(404, f"Not Found {MARK}", [f"X-{MARK}: {MARK}"], MARK.encode()), "AnswerError", "not fetched: the server answered HTTP 404", None),
        (wire_answer(429, MARK, [f"Retry-After: {MARK}"], b""), "AnswerError", "not fetched: the server answered HTTP 429", None),
        (wire_answer(429, MARK, ["Retry-After: 7"], b""), "AnswerError", "not fetched: the server answered HTTP 429", "7"),
        (wire_answer(429, MARK, [f"Retry-After: 7 {MARK}"], b""), "AnswerError", "not fetched: the server answered HTTP 429", None),
        (wire_answer(400, MARK, [f"Location: https://{MARK}.example/x"], b""), "AnswerError", "not fetched: the server answered HTTP 400", None),
        (wire_answer(429, MARK, ["Retry-After: "], b""), "AnswerError", "not fetched: the server answered HTTP 429", None),
        (wire_answer(429, MARK, ["Retry-After: 0"], b""), "AnswerError", "not fetched: the server answered HTTP 429", "0"),
        (WireAnswer(f"NOT-HTTP {MARK}\r\n\r\n".encode()), "FetchError",
         "not fetched: the server's answer does not begin with an HTTP status line", None),
        (WireAnswer(f"HTTP/9.9 200 {MARK}\r\n\r\n".encode()), "FetchError",
         "not fetched: the server answered in a version of HTTP that http.client does not read", None),
    ],
    ids=["404", "429 with words for its wait", "429 with a wait in digits", "429 with digits and words", "400 with a Location",
         "429 with an empty wait", "429 with a wait of one digit", "not http", "another protocol"],
)
def test_what_fetch_raises_is_its_number_and_the_scripts_own_sentence(
    tmp_path: Path, server: str, wire: Wire, answer: WireAnswer, kind: str, said: str, retry_after: str | None
) -> None:
    """A caller prints what fetch raises, so it holds no word the server chose: its text, its repr and its traceback.
    An HTTP answer keeps its number, and a Retry-After only when it is plain digits; a 400 is no redirect, Location
    or not."""
    _Handler.routes["/answer.bin"] = answer
    with pytest.raises(Exception) as raised:
        fetch_download.fetch(download(f"https://{STAND_IN_HOST}/answer.bin"), root=tmp_path)
    error = raised.value
    assert type(error).__name__ == kind and str(error) == f"{PATH}: {said}"
    assert all(MARK not in text for text in spoken(error)) and error.__context__ is None and error.__cause__ is None
    if kind == "AnswerError":
        assert isinstance(error, urllib.error.HTTPError) and error.code == int(said.rsplit(" ", 1)[1])
        assert error.headers.get("Retry-After") == retry_after and list(error.headers.keys()) == (["Retry-After"] if retry_after else [])
    assert nothing_written(tmp_path)


def test_a_400_with_a_location_is_an_answer_not_a_redirect(tmp_path: Path, server: str, wire: Wire, monkeypatch: pytest.MonkeyPatch) -> None:
    _Handler.routes["/answer.bin"] = wire_answer(400, "Bad Request", ["Location: https://other.example/x"], b"")
    code, lines = run_command(monkeypatch, tmp_path, f"https://{STAND_IN_HOST}/answer.bin")
    assert (code, lines[-1]) == (1, ".cache/test/answer.bin: not fetched: the server answered HTTP 400")


def test_a_refused_connection_is_named_by_the_error_inside_urllibs(tmp_path: Path, server: str, wire: Wire, monkeypatch: pytest.MonkeyPatch) -> None:
    """urllib wraps a connection's error in a URLError; the script says the error inside it by its class."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
    wire.server_port = closed
    code, lines = run_command(monkeypatch, tmp_path, f"https://{STAND_IN_HOST}/answer.bin")
    assert (code, lines[-1]) == (1, ".cache/test/answer.bin: not fetched: the connection was refused")


# The script's own bounds, one table: each with the value at an inside end and the value one step outside it, and how
# the value is judged. A bound added to the script gets its two edges by a row here.
LABEL_63, LABEL_64 = "a" * 63, "a" * 64
NAME_253 = ".".join([LABEL_63] * 3 + ["a" * 61])
NAME_254 = ".".join([LABEL_63] * 3 + ["a" * 62])
LONGEST_V6 = "ffff:" * 6 + "255.255.255.255"
FETCH_BOUNDS = [
    ("a start host's first label of 63 characters", f"{LABEL_63}.example", f"{LABEL_64}.example", "start"),
    ("a start host's last label of 63 characters", f"files.{LABEL_63}", f"files.{LABEL_64}", "start"),
    ("a named host's label of 63 characters", f"{LABEL_63}.example", f"{LABEL_64}.example", "named"),
    ("a named host's later label of 63 characters", f"files.{LABEL_63}", f"files.{LABEL_64}", "named"),
    ("a named host and port of NAMEABLE_HOST_MAX characters", f"{NAME_253}:65535", f"{NAME_254}:65535", "named"),
    ("a named port of 5 digits", "files.example:12345", "files.example:123456", "named"),
    ("a named port of 1 digit", "files.example:1", "files.example:", "named"),
    # urllib.request.Request also refuses a bracketed host that is not an IP address, and no IPv6 address is spelled
    # in more than 45 characters, so these two ends are held twice
    ("a named bracketed address of 2 characters", "[::]", "[:]", "named"),
    ("a named bracketed address of 45 characters", f"[{LONGEST_V6}]", f"[{LONGEST_V6}5]", "named"),
    ("the tests' loopback port, low", "1", "0", "loopback"),
    ("the tests' loopback port, high", "65535", "65536", "loopback"),
    ("a Retry-After of 1 digit", "7", "", "retry"),
    ("a Retry-After of RETRY_AFTER_MAX_DIGITS digits", "9" * 65536, "9" * 65537, "retry"),
]


def judged(how: str, value: str, folder: Path) -> bool:
    """True when the script takes `value` for what the row bounds: asks a start host or the loopback port, or names a
    host in a refusal."""
    if how == "retry":
        kept = fetch_download.AnswerError(PATH, "https://files.example/x", 429, value).headers.get("Retry-After")
        return kept == value
    if how == "named":
        try:
            fetch_download.fetch(download(f"http://{value}/x"), root=folder)
        except fetch_download.FetchError as error:
            return f"refused http://{value}," in str(error)
        raise AssertionError("plain http off the loopback is always refused")
    url = f"https://{value}/x" if how == "start" else f"http://127.0.0.1:{value}/x"
    try:
        fetch_download.fetch(download(url), root=folder, loopback_for_tests=how == "loopback")
    except fetch_download.FetchError as error:
        return error.failure == "a network or file error stopped the download"  # asked: every connection is cut here
    raise AssertionError("no request can succeed here")


@pytest.mark.parametrize(("name", "inside", "outside", "how"), FETCH_BOUNDS, ids=[row[0] for row in FETCH_BOUNDS])
def test_each_bound_of_the_script_holds_at_its_edge(tmp_path: Path, wire: Wire, name: str, inside: str, outside: str, how: str) -> None:
    assert abs(len(outside) - len(inside)) == 1 or int(outside) - int(inside) in (-1, 1)
    assert judged(how, inside, tmp_path) is True, name
    assert judged(how, outside, tmp_path) is False, name
    assert wire.lookups == [] and nothing_written(tmp_path)


def test_the_bounds_table_reaches_each_bound_the_script_names() -> None:
    """The table's edges sit on the script's own constants, so a constant changed without its row fails here."""
    assert len(NAME_253) == 253 and len(f"{NAME_253}:65535") == fetch_download.NAMEABLE_HOST_MAX and len(LONGEST_V6) == 45
    assert fetch_download.PLAIN_HOST.fullmatch(f"{LABEL_63}.example") and fetch_download.NAMEABLE_HOST.fullmatch(LABEL_63)
    assert not fetch_download.PLAIN_HOST.fullmatch(f"{LABEL_64}.example") and not fetch_download.NAMEABLE_HOST.fullmatch(LABEL_64)
    assert fetch_download.RETRY_AFTER_MAX_DIGITS == http.client._MAXLINE == 65536


LOCATION_PARTS = {
    "scheme": ["https", "http", MARK, f"h{MARK}", "ftp", "file", "javascript", "HTTPS"],
    "separator": ["://", ":", ":/"],
    "user": ["", "", f"{MARK}@", f"{MARK}:{MARK}@"],
    "host": [STAND_IN_HOST, STAND_IN_HOST, f"{MARK}.example", f"{MARK} said", f"{STAND_IN_HOST}, {MARK}", f"a%20{MARK}",
             f"[{MARK}]", f"[::1]", "", f"{MARK}.example."],
    "port": ["", "", ":8443", f":{MARK}"],
    "path": ["", f"/{MARK}", "/c/x", f"/{MARK} {MARK}"],
    "query": ["", f"?{MARK}"],
    "fragment": ["", f"#{MARK}"],
}  # fmt: skip
RELATIVE = [f"/{MARK}", MARK, f"//{MARK}.example/x", f"?{MARK}", f"#{MARK}", "", f"//{STAND_IN_HOST}/{MARK}"]


@st.composite
def server_answers(draw: st.DrawFn) -> WireAnswer:
    """An answer whose every server-chosen field may carry the marker: the status and its reason phrase, the
    Location (each of its parts) or a URI header, any other header, and the body; or text that is not HTTP."""
    kind = draw(st.sampled_from(["redirect", "redirect", "error", "body", "not http"]))
    if kind == "not http":
        return WireAnswer(f"{draw(st.sampled_from(['NOT-HTTP', 'HTTP/1.1', 'HTTP/1.1 abc', 'HTTP/9.9 200']))} {MARK}\r\n\r\n".encode())
    reason = draw(st.sampled_from(["", MARK, f"Found {MARK}", f"\x1b[2J{MARK}", f"{MARK} " * 20]))
    headers = [draw(st.sampled_from(["", f"X-{MARK}: {MARK}", f"Server: {MARK}", f"Retry-After: {MARK}"]))]
    body = draw(st.sampled_from([b"", MARK.encode(), MARK.encode() * 50]))
    if kind == "redirect":
        code = draw(st.sampled_from([300, 301, 302, 303, 307, 308, 399, 304, 305, 306, 309, 350]))
        if draw(st.booleans()):
            location = "".join(draw(st.sampled_from(options)) for options in LOCATION_PARTS.values())
        else:
            location = draw(st.sampled_from(RELATIVE))
        header = draw(st.sampled_from(["Location", "Location", "URI", None]))
        if header is not None:
            headers.append(f"{header}: {location}")
    else:
        code = 200 if kind == "body" else draw(st.sampled_from([400, 403, 404, 410, 429, 500, 502, 503, 599]))
    return wire_answer(code, reason, [header for header in headers if header], body)


@settings(deadline=None, max_examples=300, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(answer=server_answers())
@example(answer=wire_answer(399, MARK, [f"Location: https://{MARK}.example/{MARK}?{MARK}#{MARK}"], b""))
@example(answer=wire_answer(302, MARK, [f"Location: itisfine://{STAND_IN_HOST}/x"], b""))
@example(answer=wire_answer(302, MARK, [f"Location: {MARK}:anything"], b""))
@example(answer=wire_answer(301, MARK, [f"Location: https://{STAND_IN_HOST}, {MARK} said/x"], b""))
@example(answer=wire_answer(399, f"\x1b[2J{MARK}", [], b""))
@example(answer=wire_answer(300, MARK, ["Location: "], b""))
def test_property_no_line_the_command_prints_holds_a_word_the_server_chose(
    server: str, wire: Wire, monkeypatch: pytest.MonkeyPatch, answer: WireAnswer
) -> None:
    """Whatever a server answers, the command prints its own line for the entry and one line of its own words, and what
    fetch raises says no more: an HTTP error by its number, a refusal naming a host only when it is plain, never a
    reason phrase, a header, a body, a path, a user part or a scheme the server wrote."""
    _Handler.routes["/answer.bin"] = answer
    with tempfile.TemporaryDirectory() as folder:
        code, lines = run_command(monkeypatch, Path(folder), f"https://{STAND_IN_HOST}/answer.bin")
        kept = [path for path in Path(folder).rglob("*") if path.is_file() and path.name != "downloads.json"]
    assert code == 1 and len(lines) == 2 and kept == [], lines
    assert server_words_in(lines) == [], lines
    # What fetch itself raises, from the https start and from the tests' loopback start, says no more
    for start, loopback in ((f"https://{STAND_IN_HOST}/answer.bin", False), (f"{server}/answer.bin", True)):
        with tempfile.TemporaryDirectory() as folder:
            with pytest.raises(Exception) as raised:
                fetch_download.fetch(download(start), root=Path(folder), loopback_for_tests=loopback)
        assert server_words_in(spoken(raised.value)) == [], spoken(raised.value)[0]
        assert raised.value.__context__ is None and raised.value.__cause__ is None
    assert set(wire.connections) <= {(STAND_IN_HOST, 443), ("127.0.0.1", wire.server_port)}


def test_redirected_requests_carry_the_user_agent_and_nothing_identifying(server_port: int, server: str, wire: Wire) -> None:
    raised, asked, kept = run_chain("https", server_port, wire, ["/c/{id}/{next}", "{next}"])
    assert raised is None and kept and len(asked) == 3
    for _, headers in _Handler.seen:
        assert set(headers) <= ALLOWED_HEADERS and headers["User-Agent"] == USER_AGENT and headers["Host"] == STAND_IN_HOST


def test_the_script_states_the_rule_it_runs() -> None:
    doc = " ".join(fetch_download.__doc__.split())
    assert f"at most {MAX} times" in doc
    assert "never leads to plain http" in doc
    assert "127.0.0.1 with its port in plain digits" in doc


# Generated chains. Each step is a template with its label (followed or refused), so the test knows where the chain
# must end; the stated maximum ends a longer run of followed steps.
LABELLED = {
    origin: st.one_of(
        st.sampled_from(FOLLOWED).map(lambda template: (template, True)),
        st.sampled_from(REFUSED[origin]).map(lambda template: (template, False)),
    )
    for origin in ORIGINS
}


@st.composite
def labelled_chains(draw: st.DrawFn) -> tuple[str, list[tuple[str, bool]]]:
    origin = draw(st.sampled_from(ORIGINS))
    return origin, draw(st.lists(LABELLED[origin], max_size=MAX + 2))


@settings(deadline=None, max_examples=300, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(chain=labelled_chains())
@example(chain=("https", [("{next}", True)] * MAX))
@example(chain=("https", [("{next}", True)] * (MAX + 1)))
@example(chain=("loopback", [("{next}", True)] * (MAX + 1)))
@example(chain=("https", [("/c/{id}/{next}", True), ("http://{authority}/c/{id}/{next}", False)]))
@example(chain=("loopback", [("{next}", True), ("{scheme}://user:secret@{authority}/c/{id}/{next}", False)]))
@example(chain=("https", [("{next}#x", False)]))
def test_property_a_chain_goes_only_where_the_rule_allows_and_ends_where_it_says(
    server_port: int, server: str, wire: Wire, chain: tuple[str, list[tuple[str, bool]]]
) -> None:
    origin, steps = chain
    raised, asked, kept = run_chain(origin, server_port, wire, [template for template, _ in steps])
    followed = expected(steps)
    finished = followed == len(steps)
    event(f"{origin}: " + ("kept" if finished else "stopped at the cap" if followed == MAX else "refused"))
    assert (raised is None) == finished, (steps, raised)
    if not finished:
        assert isinstance(raised, fetch_download.FetchError), repr(raised)
        stopped_at = steps[followed][0] if followed < len(steps) else None
        if declined_by_urllib(stopped_at):  # urllib declines its scheme before the rule counts it
            assert str(raised) == NOT_FOLLOWED.format(code=302)
        else:
            assert str(raised).startswith(f"{PATH}: refused a redirect to ")
    assert len(asked) == followed + 1
    assert wire.connections == [reached_by(origin, server_port)] * (followed + 1)
    assert wire.lookups == ([] if origin == "https" else ["127.0.0.1"] * (followed + 1))
    for _, headers in _Handler.seen:
        assert set(headers) <= ALLOWED_HEADERS and headers["User-Agent"] == USER_AGENT


# Locations put together from parts, each the origin's own one most of the time and otherwise an odd one: another
# scheme, a user part, another spelling of the host, another port, a relative or odd tail, a fragment. The test does
# not predict where these end, only that nothing reaches a host or port outside the rule.
LATIN_1 = "".join(chr(code) for code in [*range(0x20, 0x7F), *range(0xA0, 0x100)])
SCHEMES = ["{scheme}", "{scheme}", "http", "https", "HTTP", "Https", "ftp", "gopher", "file", "javascript", "", " http", "ht tp"]
SEPARATORS = ["://", "://", ":", ":/", ":///", "//", ":\\\\", "/", ""]
USERS = ["", "", "", "someone@", "user:secret@", "@", ":@", "{authority}@", "evil.example@", "a%40b@", "x\\@"]
HOSTS = [
    "{host}", "{host}", "{host}", "127.0.0.1", "files.example", "localhost", "[::1]", "[::ffff:127.0.0.1]", "127.1",
    "2130706433", "0x7f.0.0.1", "127.0.0.1.", "files.example.", "FILES.example", "files%2Eexample", "127%2E0%2E0%2E1",
    "evil.example", "files.example.evil.example", "", "127.0.0.1\\", "files.example\\.evil.example",
    "files.example#.evil.example", "files.example?.evil.example", "127.0.0.1%00", f"files{chr(0xE9)}example",
    "[", "[::1", "]", "[{host}]",
]  # fmt: skip
PORTS = ["", "", ":{port}", ":{port}", ":443", ":80", ":", ":0", ":0{port}", ":+{port}", ":{other_port}", ":{port}:{port}",
         ":65536", ": 1", ":{wrapped_port}"]  # fmt: skip
TAILS = ["/c/{id}/{next}", "/c/{id}/{next}", "", "/", "?x", "#f", "/c/{id}/{next}#f", "\\c", "/x@evil.example",
         "/c/{id}/{next}?a=@evil.example", "{next}", "/%2e%2e/c/{id}/{next}", "/c/{id}/{next} "]  # fmt: skip


@st.composite
def odd_location(draw: st.DrawFn) -> Any:
    if draw(st.integers(min_value=0, max_value=4)) == 0:
        return Raw(draw(st.text(alphabet=LATIN_1, max_size=30)))
    parts = [draw(st.sampled_from(options)) for options in (SCHEMES, SEPARATORS, USERS, HOSTS, PORTS, TAILS)]
    return "".join(parts)


@settings(deadline=None, max_examples=300, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(origin=st.sampled_from(ORIGINS), locations=st.lists(odd_location(), min_size=1, max_size=MAX + 1))
@example(origin="https", locations=["http://{host}/c/{id}/{next}"])
@example(origin="https", locations=["{scheme}://user:secret@{host}/c/{id}/{next}"])
@example(origin="loopback", locations=["{scheme}://{authority}@evil.example/c/{id}/{next}"])
@example(origin="loopback", locations=["{scheme}://[::ffff:127.0.0.1]:{port}/c/{id}/{next}"])
@example(origin="https", locations=["{scheme}://files.example#.evil.example/c/{id}/{next}"])
@example(origin="loopback", locations=[Raw("")])
@example(origin="https", locations=["{scheme}://[{host}/c/{id}/{next}"])
def test_property_no_redirect_reaches_a_host_or_port_outside_the_rule(
    server_port: int, server: str, wire: Wire, origin: str, locations: list[Any]
) -> None:
    raised, asked, kept = run_chain(origin, server_port, wire, locations)
    assert raised is None or isinstance(raised, fetch_download.FetchError) or (
        isinstance(raised, urllib.error.HTTPError) and raised.code == 404
    ), repr(raised)
    event(f"{origin}: {type(raised).__name__ if raised else 'kept'} after {len(asked) - 1} redirects followed")
    allowed = reached_by(origin, server_port)
    assert set(wire.connections) <= {allowed} and len(wire.connections) == len(asked)
    assert set(wire.lookups) <= ({"127.0.0.1"} if origin == "loopback" else set())
    assert len(asked) <= MAX + 1


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


# =============================================================================================
# The one boundary (fifth version): what a server's framing makes urllib or http.client raise
# =============================================================================================

# Each framing with what ends the download when it frames the file itself and when it frames a same-origin redirect's
# body: the table's sentence, or None. As the file, None is a file that is not the one listed (http.client's read of a
# part gives what came before the close); as a redirect, None is a redirect followed (urllib reads its whole body and
# drops it), so the file is kept.
CUT = "the server's body ended early, or its chunks could not be read"
# urllib reads a followed redirect's body whole; Python 3.12's http.client then reads 40 digits of chunk size as one
# read too large to make, and 3.13 on as a body cut short (both measured: .lanetools/fetch-1-v6/probe_chunk.py)
WHOLE_40_DIGITS = "a number in the server's answer is too large for http.client to read" if sys.version_info < (3, 13) else CUT
# and 15 nines of chunk size as a buffer it cannot be given (.lanetools/fetch-1-v7/probe_classes.py, a real socket)
WHOLE_15_NINES = "a size in the server's answer is larger than the memory http.client may ask for" if sys.version_info < (3, 13) else CUT
FRAMINGS = {
    "a chunk size of words": (b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n" + MARK.encode() + b"\r\n" + MARK.encode() + b"\r\n0\r\n\r\n", CUT, CUT),
    "a chunk size of 40 digits": (b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n" + b"9" * 40 + b"\r\n" + MARK.encode() + b"\r\n", CUT, WHOLE_40_DIGITS),
    "a chunk size of 15 nines": (b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n" + b"9" * 15 + b"\r\n" + MARK.encode() + b"\r\n", CUT, WHOLE_15_NINES),
    "chunks cut short": (b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n" + b"ff\r\n" + MARK.encode(), CUT, CUT),
    "a length that says more than is sent": (b"Content-Length: 99999\r\nConnection: close\r\n\r\n" + MARK.encode() * 3, None, CUT),
    "a length that says less than is sent": (b"Content-Length: 3\r\nConnection: close\r\n\r\n" + MARK.encode() * 30, None, None),
    "bytes after the headers with no framing": (b"Connection: close\r\n\r\n" + MARK.encode() * 30, None, None),
}  # fmt: skip


def framed(code: int, location: str | None, framing: bytes) -> WireAnswer:
    head = f"HTTP/1.1 {code} {MARK}\r\nX-{MARK}: {MARK}\r\n" + (f"Location: {location}\r\n" if location else "")
    return WireAnswer(head.encode() + framing)


@pytest.mark.parametrize("origin", ORIGINS)
@pytest.mark.parametrize("hop", [False, True], ids=["the file", "a followed redirect"])
@pytest.mark.parametrize("name", list(FRAMINGS))
def test_a_framing_a_server_chose_ends_in_the_scripts_own_words_on_the_file_and_on_a_redirect(
    tmp_path: Path, server_port: int, server: str, wire: Wire, origin: str, hop: bool, name: str
) -> None:
    """The file itself, or the body of a same-origin redirect urllib reads before it follows it, framed as the server
    chose. Whatever ends the download is the script's own error (checked whole by assert_the_scripts_own on every
    call), with the table's sentence; a framing http.client reads whole gives bytes that are not the file."""
    framing, on_file, on_hop = FRAMINGS[name]
    failure = on_hop if hop else on_file
    if hop:
        _Handler.routes["/answer.bin"] = framed(302, "/payload.bin", framing)
    else:
        _Handler.routes["/answer.bin"] = framed(200, None, framing)
    start = f"https://{STAND_IN_HOST}/answer.bin" if origin == "https" else f"{server}/answer.bin"
    try:
        fetch_download.fetch(download(start), root=tmp_path, loopback_for_tests=origin == "loopback")
        raised = None
    except fetch_download.FetchError as error:
        raised = error
    if hop and failure is None:
        assert raised is None and (tmp_path / PATH).read_bytes() == PAYLOAD  # the hop's body is read and dropped
        return
    assert raised is not None
    if failure is not None:
        assert raised.failure == failure and str(raised) == f"{PATH}: not fetched: {failure}"
    else:
        assert raised.failure is None and str(raised).startswith(f"{PATH}: got ")
    assert not (tmp_path / PATH).exists() and not (tmp_path / (PATH + ".part")).exists()


def test_a_redirect_location_urlparse_refuses_is_refused_with_no_error_of_urllibs_behind_it(
    server_port: int, server: str, wire: Wire
) -> None:
    """The verdict's second survivor: the refusal raised inside urllib's ValueError handler would keep that error,
    which quotes the server's host, as its context. Asked of the redirect rule itself, below the boundary that drops
    every chain, so the rule's own pattern is pinned."""
    _Handler.routes["/answer.bin"] = f"https://[{MARK}]/x"
    request = urllib.request.Request(f"{server}/answer.bin", headers={"User-Agent": USER_AGENT})
    start = ("http", "127.0.0.1", server_port)
    with pytest.raises(fetch_download.FetchError) as refused:
        fetch_download._open(request, PATH, start, loopback_for_tests=True)
    assert refused.value.__context__ is None and refused.value.__cause__ is None
    assert str(refused.value) == redirect_refusal("a URL that cannot be read", "loopback", server_port)
    # Raised below the boundary, through urllib's own frames, which hold the answer by nature: its text, chain,
    # args and attributes are what is checked here; fetch rebuilds it bare before anything leaves
    assert server_words(everything_said(refused.value, frames=False)) == []


@pytest.mark.parametrize("origin", ORIGINS)
def test_an_answer_after_a_same_origin_hop_holds_the_url_the_download_started_at(
    tmp_path: Path, server_port: int, server: str, wire: Wire, origin: str
) -> None:
    """The verdict's third survivor: the hop's path and query are the server's, so the AnswerError holds the
    registry's URL as its url, its filename and what geturl gives, never urllib's."""
    _Handler.routes["/answer.bin"] = f"/{MARK}-hop?{MARK}"
    _Handler.routes[f"/{MARK}-hop"] = 404
    start = f"https://{STAND_IN_HOST}/answer.bin" if origin == "https" else f"{server}/answer.bin"
    with pytest.raises(fetch_download.AnswerError) as answered:
        fetch_download.fetch(download(start), root=tmp_path, loopback_for_tests=origin == "loopback")
    assert answered.value.url == answered.value.filename == answered.value.geturl() == start
    assert [path.split("?")[0] for path, _ in _Handler.seen[-2:]] == ["/answer.bin", f"/{MARK}-hop"]
    assert nothing_written(tmp_path)


def test_an_empty_retry_after_is_dropped_and_one_digit_kept() -> None:
    """The verdict's fourth survivor, by the AnswerError itself: an empty value is no number of seconds."""
    for value, kept in (("", None), ("7", "7"), (" 7", None), ("7 ", None), ("٧", None), (7, None)):
        assert fetch_download.AnswerError(PATH, "https://files.example/x", 429, value).headers.get("Retry-After") == kept, value


# The script's FAILURES table as it should read, written out here: a row changed, dropped or put behind a wider one
# fails test_each_error_class_gets_its_own_sentence
FAILURE_TABLE = [
    (http.client.RemoteDisconnected("x"), "the server closed the connection without an answer"),
    (http.client.IncompleteRead(b"x"), "the server's body ended early, or its chunks could not be read"),
    (http.client.LineTooLong("header line"), "a line of the server's answer is longer than http.client reads"),
    (http.client.BadStatusLine("x"), "the server's answer does not begin with an HTTP status line"),
    (http.client.UnknownProtocol("x"), "the server answered in a version of HTTP that http.client does not read"),
    (http.client.InvalidURL("x"), "the request's URL holds a character http.client cannot send"),
    (http.client.ResponseNotReady("x"), "the server's answer could not be read as HTTP"),
    (UnicodeEncodeError("ascii", "x", 0, 1, "x"), "the request's URL holds a character http.client cannot send"),
    (ConnectionRefusedError(), "the connection was refused"),
    (ConnectionResetError(), "the connection was reset"),
    (TimeoutError(), "the connection timed out"),
    (socket.gaierror(), "the host name could not be resolved"),
    (ssl.SSLError(), "the TLS connection could not be made or kept"),
    (OverflowError(), "a number in the server's answer is too large for http.client to read"),
    (MemoryError(), "a size in the server's answer is larger than the memory http.client may ask for"),
    (OSError(), "a network or file error stopped the download"),
    (ValueError(), "a value in the exchange could not be read"),
    (ArithmeticError(), "an error of a kind this script does not know stopped the download"),
]


@pytest.mark.parametrize(("caught", "sentence"), FAILURE_TABLE, ids=[type(caught).__name__ for caught, _ in FAILURE_TABLE])
def test_each_error_class_gets_its_own_sentence_bare_or_inside_urllibs(caught: BaseException, sentence: str) -> None:
    entry = download("https://files.example/x")
    for error in (caught, urllib.error.URLError(caught)):
        own = fetch_download._own_error(entry, error)
        assert (type(own), own.failure, str(own)) == (fetch_download.FetchError, sentence, f"{PATH}: not fetched: {sentence}")
        assert own.__context__ is None and own.__cause__ is None


def test_the_failure_table_is_closed_and_each_of_its_sentences_is_the_scripts_own() -> None:
    kinds = [kind for kind, _ in fetch_download.FAILURES]
    assert len(kinds) == len(set(kinds)) and kinds[-2:] == [OSError, ValueError]
    for i, kind in enumerate(kinds):  # no row is hidden behind an earlier, wider one
        assert not any(issubclass(kind, earlier) for earlier in kinds[:i]), kind
    for _, sentence in fetch_download.FAILURES:
        assert sentence.isascii() and MARK not in sentence and "{" not in sentence
    unknown = fetch_download._own_error(download("https://files.example/x"), ArithmeticError(MARK))
    assert (type(unknown), unknown.failure) == (fetch_download.FetchError, fetch_download.UNKNOWN_FAILURE)
    wrapped = fetch_download._own_error(download("https://files.example/x"), urllib.error.URLError(ConnectionRefusedError(MARK)))
    assert wrapped.failure == "the connection was refused" and server_words(everything_said(wrapped)) == []


# Calls fetch may make outside its boundary: none can reach a server
OUTSIDE_THE_BOUNDARY = ["FetchError", "_own_error", "is_cache_path", "part.unlink", "target.with_name"]
MODULES_THAT_REACH_A_SERVER = ("urllib.", "http.client.", "socket.", "ssl.")


def test_every_call_that_can_reach_a_server_runs_inside_the_one_boundary_of_fetch() -> None:
    """Read from the script's source: fetch holds one try, whose one handler takes Exception; outside it fetch calls
    only what cannot reach a server; the helpers that do are called from inside it alone; and every call into
    urllib, http.client, socket or ssl in the script is in one of those helpers or in the redirect rule urllib calls
    while it opens."""
    tree = ast.parse((ROOT / "scripts" / "fetch_download.py").read_text(encoding="utf-8"))
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    fetch = functions["fetch"]
    tries = [node for node in ast.walk(fetch) if isinstance(node, ast.Try)]
    assert len(tries) == 1 and [ast.unparse(handler.type) for handler in tries[0].handlers] == ["Exception"]
    inside = {id(node) for statement in tries[0].body for node in ast.walk(statement)}
    calls = [node for node in ast.walk(fetch) if isinstance(node, ast.Call)]
    assert sorted({ast.unparse(call.func) for call in calls if id(call) not in inside}) == OUTSIDE_THE_BOUNDARY
    sites = {name: [(owner, id(call) in inside) for owner, node in functions.items() for call in ast.walk(node)
                    if isinstance(call, ast.Call) and ast.unparse(call.func) == name]
             for name in ("_start", "_present", "_download", "_open")}  # fmt: skip
    assert sites == {"_start": [("fetch", True)], "_present": [("fetch", True)], "_download": [("fetch", True)], "_open": [("_download", False)]}
    reaching = {owner for owner, node in functions.items() for call in ast.walk(node)
                if isinstance(call, ast.Call) and ast.unparse(call.func).startswith(MODULES_THAT_REACH_A_SERVER)}  # fmt: skip
    assert reaching == {"_start", "_open", "destination", "_named_text"}, reaching


# =============================================================================================
# The walker itself (seventh version): each thing it leaves out, with a marker planted there
# =============================================================================================

HOP = f"https://files.example/{MARK}-hop?{MARK}"  # what a hop's Location writes into a request


def raised_in_the_script(source: str, *args: Any, **names: Any) -> BaseException:
    """Run `planted` from `source`, compiled under the script's own file name so its frame is one of the script's, as
    fetch's is, and return what it raised."""
    namespace: dict[str, Any] = {"FetchError": fetch_download.FetchError, **names}
    exec(compile(source, fetch_download.__file__, "exec"), namespace)  # noqa: S102 (the test's own source)
    try:
        namespace["planted"](*args)
    except BaseException as error:  # noqa: BLE001 (the raised error is what is walked)
        return error
    raise AssertionError("planted raised nothing")


def found(error: BaseException, **kwargs: Any) -> bool:
    return any(MARK in text for text in server_words(everything_said(error, **kwargs)))


def test_a_parameter_rebound_to_what_a_server_sent_is_walked() -> None:
    """The sixth version's blind spot, as the auditor's mutant had it: `root = request` after the start."""
    entry, root = download("https://files.example/x"), Path("/tmp/root")
    error = raised_in_the_script("def planted(download, root):\n    root = HOP\n    raise FetchError('x')\n", entry, root, HOP=HOP)
    assert found(error, given={"download": entry, "root": root})


def test_a_parameter_left_as_the_callers_object_is_left_out_and_walked_when_it_is_not() -> None:
    entry = download(f"https://files.example/{MARK}")  # the caller's own text: no server sent it
    error = raised_in_the_script("def planted(download):\n    raise FetchError('x')\n", entry)
    assert not found(error, given={"download": entry})
    assert found(error, given={"download": download(f"https://files.example/{MARK}")})  # an equal object, not the same
    assert found(error)


def test_the_script_modules_own_state_is_walked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raised by fetch itself (a path outside .cache/), unwrapped, so its frame's globals are the script module's."""
    monkeypatch.setattr(fetch_download, "_PLANTED", HOP, raising=False)
    with pytest.raises(fetch_download.FetchError) as refused:
        inspect.unwrap(fetch_download.fetch)(download("https://files.example/x", path="outside/x.bin"))
    assert found(refused.value)
    monkeypatch.delattr(fetch_download, "_PLANTED")
    assert not found(refused.value)


@pytest.mark.parametrize("where", ["a dunder attribute of its own", "a partial", "a closure", "a bound method's object", "200 levels down"])
def test_whatever_holds_a_marker_on_the_error_is_walked(where: str) -> None:
    error = fetch_download.FetchError("x")
    holder = type("Holder", (), {"keep": lambda self: None})()
    holder.text = HOP
    deep: Any = HOP
    for _ in range(200):
        deep = [deep]
    planted: dict[str, Any] = {
        "a dunder attribute of its own": ("__planted__", HOP),
        "a partial": ("later", functools.partial(str, HOP)),
        "a closure": ("hook", (lambda held: lambda: held)(HOP)),
        "a bound method's object": ("callback", holder.keep),
        "200 levels down": ("deep", deep),
    }
    name, value = planted[where]
    error.__dict__[name] = value
    assert found(error)


def test_below_the_boundary_the_frames_hold_the_answer_and_fetch_rebuilds_it_bare(
    tmp_path: Path, server_port: int, server: str, wire: Wire
) -> None:
    """The redirect rule's frames, which the rule's own test leaves out: the answer is there (its headers), and the
    error fetch raises for the same answer holds none of it."""
    _Handler.routes["/answer.bin"] = f"https://[{MARK}]/x"
    request = urllib.request.Request(f"{server}/answer.bin", headers={"User-Agent": USER_AGENT})
    with pytest.raises(fetch_download.FetchError) as below:
        fetch_download._open(request, PATH, ("http", "127.0.0.1", server_port), loopback_for_tests=True)
    assert found(below.value)  # the rule's own frame keeps the answer's headers
    with pytest.raises(fetch_download.FetchError) as raised:
        fetch_download.fetch(download(f"{server}/answer.bin"), root=tmp_path, loopback_for_tests=True)
    assert not found(raised.value)


def test_the_script_writes_no_module_state_but_its_own_locals() -> None:
    """Why the walker may stop at a module: the script has no `global` or `nonlocal`, installs no opener, and assigns
    to no attribute of a name that is not one of its own locals or classes (read from its source)."""
    tree = ast.parse((ROOT / "scripts" / "fetch_download.py").read_text(encoding="utf-8"))
    assert not [node for node in ast.walk(tree) if isinstance(node, (ast.Global, ast.Nonlocal))]
    assert "install_opener" not in ast.unparse(tree)
    imported = {alias.asname or alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names}  # fmt: skip
    written = [ast.unparse(target) for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign))
               for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
               if isinstance(target, ast.Attribute) and isinstance(ast.unparse(target).split(".")[0], str)
               and ast.unparse(target).split(".")[0] in imported]  # fmt: skip
    assert written == []
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Call) and ast.unparse(node.func) in ("setattr", "delattr")]
