#!/usr/bin/env python3
"""Fetch a public download listed in data/downloads.json into the shared .cache/, checked by size and SHA-256.

Usage, from the repository root:
    uv run python scripts/fetch_download.py .cache/dust/edenhofer2024/mean_and_std_healpix.fits

The bytes go to `<path>.part`, which becomes `<path>` only once its size and SHA-256 match the registry entry; on a
mismatch or an error the part file is deleted and the script exits with 1. A file already there with the listed
size and SHA-256 is kept without a request. A path outside `.cache/` is refused before any request. Requests carry
the User-Agent "Albedian/0.1 (research prototype)" and nothing personal. Only the standard library is used.

Where a request may go. A URL is read once, the way the request itself will read it: urllib.request.Request gives
the scheme, the authority and the path with its query, and http.client, which opens the connection, takes the host
and the port out of that authority. A user or password part stays in the host there, so it never passes for the
host. A download starts only at a URL that this reading spells exactly (no fragment, no wrapper, no escaped
character in the host) as https on a lower-case host name, with no user or password part and no port, so port 443.
Any other URL is refused before a request. The one exception is for tests alone: plain http on 127.0.0.1 with its
port in plain digits, taken only when the caller passes `loopback_for_tests=True` to `fetch`. No command-line
option, environment variable or registry field sets it, and `main` never does, so a real run refuses every loopback
and plain http address.

Redirects. A redirect is followed only to the scheme, host and port the download started at, read and spelled the
same way, and at most 3 times. So https never leads to plain http, and no download follows a redirect to another
host. A refused redirect ends the download with one sentence of the script's own words, and no file or folder is
written. The sentence names where the redirect led only when its scheme is https or http and its host a plain
host name or address (a user or password part is said to be there, never printed); any other target is "a target
that is not a plain https address", since its words are the server's.

What bounds a download. One deadline, DOWNLOAD_DEADLINE_S, runs from the download's first connection to its last
byte, across every redirect: the opener's own http and https handlers make connections whose every socket wait (each
address's connection attempt, the TLS handshake, sending the request, a proxy's CONNECT, the status line, the headers
and every body) lasts at most the time left before it, and never longer than TIMEOUT_S. Past it the download ends as
too long. A redirect's body is also held to REDIRECT_BODY_MAX_BYTES. One step has no wait of the script's: the lookup
of a host name, since socket.getaddrinfo takes none, so a lookup that hangs can hold a download past the deadline. That
is a written limit.

What the script says. Every call that can reach a server, from reading the URL to the last read of the body,
runs inside one boundary in `fetch`. A `FetchError` the script's own checks raise (for a refused URL or redirect, a
body that failed or ran past a limit in `_read_part`, or a size or SHA-256 mismatch) keeps its own sentence. Anything
else the boundary catches is read for three things alone: its class, which `_PastDeadline` turns into `too_long()` and
FAILURES (a closed table for errors outside `_read_part`, else UNKNOWN_FAILURE) turns into one of the script's own
sentences; for an HTTP answer, its status number; and a Retry-After that is one to RETRY_AFTER_MAX_DIGITS ASCII digits.
Its text, args, URL, headers and reason are never read. The script's own error is built from those and raised once the
boundary's handler has ended, so the caught error is neither its cause nor its context, and it holds no URL but the one
the download started at, from the registry. An answer that is not the file is raised as `AnswerError`, urllib's
HTTPError with that number and Retry-After; a 3xx urllib did not follow is said by its number alone; any other failure
is a `FetchError` whose `failure` names what stopped the transfer. A refusal names where a redirect led only by the
plain host the redirect rule read. The command prints a failure by the script's sentence, never an error's text. A
proxy the machine is set to use still carries the request, as urllib.request does by default.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import email.message
import functools
import hashlib
import http.client
import io
import json
from pathlib import Path, PurePosixPath
import re
import socket
import ssl
import sys
import time
from typing import Any
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "downloads.json"
USER_AGENT = "Albedian/0.1 (research prototype)"
CHUNK = 1 << 20
# The longest one socket wait of a download may last (a connection attempt, the TLS handshake, one send or one read),
# each also at most the time left before DOWNLOAD_DEADLINE_S. Read where it is used, so a test can set it
TIMEOUT_S = 60
# [proposed] The longest one download may take, from its first connection to its last byte across every redirect: the
# registry's largest file (3,252,715,200 bytes) at half a megabyte a second takes under two hours. Every socket wait of
# the download lasts at most the time left before it. Read where it is used, at each download, so a test can set it
DOWNLOAD_DEADLINE_S = 7200
# [proposed] How much of a redirect's own body the script takes, only to drop it: a redirect page is a few hundred
# bytes, and more than this ends the download in the redirect's sentence (the part that passes it can bring up to
# CHUNK bytes). Read where it is used
REDIRECT_BODY_MAX_BYTES = 65536
# The tests' own server listens here, on a port of its own; asked only when a test passes loopback_for_tests
LOOPBACK = "127.0.0.1"
HTTPS_PORT = http.client.HTTPS_PORT
# [proposed] How many redirects one download follows: a file server that hands a request on to its storage takes one
MAX_REDIRECTS = 3
# A host name as a URL must spell it: lower-case ASCII labels of letters, digits and inner hyphens, at least two of
# them, the last one starting with a letter, so that no address in any notation passes for a name
PLAIN_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?")
# What a message may name: the scheme only when it is https or http, and a host that is a name of letters, digits
# and inner hyphens in dot-separated labels, an IPv4 address in dotted digits or an IPv6 address in brackets, each
# with a port in digits or none. Anything else is text the server chose, and a message never repeats it.
NAMEABLE_SCHEMES = ("https", "http")
NAMEABLE_HOST = re.compile(
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
    r"|\[[0-9A-Fa-f:.]{2,45}\])(?::[0-9]{1,5})?"
)
NAMEABLE_HOST_MAX = 253 + len(":65535")
NOT_A_PLAIN_HOST = "a target that is not a plain https address"
# A Retry-After a caller may read: one to RETRY_AFTER_MAX_DIGITS ASCII digits, which HTTP reads as a number of
# seconds; anything else, the empty value too, is dropped. The largest length is the longest header line http.client
# reads (http.client._MAXLINE), so a value is dropped for its form, never cut.
RETRY_AFTER_MAX_DIGITS = 65536
RETRY_AFTER_SECONDS = re.compile(rf"[0-9]{{1,{RETRY_AFTER_MAX_DIGITS}}}")
# What the script says of a socket wait that ran out: while connecting, while reading an answer's head, and while
# reading a body before the deadline
TIMED_OUT = "the connection timed out"
# What the boundary in `fetch` says of a non-HTTP error outside `_read_part` and `_PastDeadline` (connecting, the TLS
# handshake, sending the request or reading an answer's head; body reads raise FetchError in `_read_part` before
# reaching this table), by the error's class alone, the first row that fits; an error urllib wraps in a URLError is
# read by the class of the error inside it
FAILURES: tuple[tuple[type[BaseException], str], ...] = (
    (http.client.RemoteDisconnected, "the server closed the connection without an answer"),
    (http.client.IncompleteRead, "the server's body ended early, or its chunks could not be read"),
    (http.client.LineTooLong, "a line of the server's answer is longer than http.client reads"),
    (http.client.BadStatusLine, "the server's answer does not begin with an HTTP status line"),
    (http.client.UnknownProtocol, "the server answered in a version of HTTP that http.client does not read"),
    (http.client.InvalidURL, "the request's URL holds a character http.client cannot send"),
    (http.client.HTTPException, "the server's answer could not be read as HTTP"),
    (UnicodeError, "the request's URL holds a character http.client cannot send"),
    (ConnectionRefusedError, "the connection was refused"),
    (ConnectionResetError, "the connection was reset"),
    (TimeoutError, TIMED_OUT),
    (socket.gaierror, "the host name could not be resolved"),
    (ssl.SSLError, "the TLS connection could not be made or kept"),
    (OverflowError, "a number in the server's answer is too large for http.client to read"),
    (MemoryError, "a size in the server's answer is larger than the memory http.client may ask for"),
    (OSError, "a network or file error stopped the download"),
    (ValueError, "a value in the exchange could not be read"),
)
UNKNOWN_FAILURE = "an error of a kind this script does not know stopped the download"
# What the script says of an error raised while it reads a body, whatever its class, but a socket wait that ran out
# (TIMED_OUT) and a read that ended past the deadline (too_long): what the standard library raises for a body a server
# framed (cut short, a chunk size of words or of forty digits) differs between Python releases, even between 3.12.3
# and 3.12.9, and the script's words do not follow it. A body is read in parts of at most CHUNK bytes.
BODY_UNREADABLE = "the server's body could not be read whole: it ended early, or its framing or a size in it could not be read"
REDIRECT_BODY_UNREADABLE = (
    "a redirect's body could not be read whole within its limit: it ended early, ran past the limit, or its framing or "
    "a size in it could not be read"
)


def too_long() -> str:
    """What the script says of a download that ran past DOWNLOAD_DEADLINE_S, the deadline as it is when it is said."""
    return f"the download took longer than the {DOWNLOAD_DEADLINE_S} s one download may take"
STARTS_ONLY_AT = (
    "a download starts only at https on a lower-case host name with no user or password part, no port and no "
    "fragment (or, for tests, plain http on 127.0.0.1 with its port)"
)

# Where a request connects: its scheme, host and port
Target = tuple[str, str, int]


class FetchError(Exception):
    """A download that was refused, that failed, or whose bytes do not match its registry entry. `failure` is the
    script's own sentence for a failed transfer (a row of FAILURES, UNKNOWN_FAILURE, BODY_UNREADABLE,
    REDIRECT_BODY_UNREADABLE, too_long(), or a 3xx urllib did not follow), and None for a refusal or a size or
    SHA-256 mismatch, which the message says in full."""

    def __init__(self, message: str, failure: str | None = None) -> None:
        super().__init__(message)
        self.failure = failure


class AnswerError(urllib.error.HTTPError):
    """An HTTP answer that is not the file, in the script's own words: its status number, the script's sentence, and a
    Retry-After header only when its value is plain digits. Nothing else the server sent is kept, so no reason phrase,
    header or URL of the server's reaches its text, its repr or a traceback."""

    def __init__(self, path: str, url: str, code: int, retry_after: Any) -> None:
        """`url` is the download's own, from the registry; `code` and `retry_after` are all that is read of the
        answer."""
        headers = email.message.Message()
        if isinstance(retry_after, str) and RETRY_AFTER_SECONDS.fullmatch(retry_after):
            headers["Retry-After"] = retry_after
        super().__init__(url, code, f"{path}: not fetched: the server answered HTTP {code}", headers, None)

    def __str__(self) -> str:
        return self.msg


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


def destination(url: Any, request: urllib.request.Request, *, loopback_for_tests: bool = False) -> Target | None:
    """Where `request`, built from the text `url`, will connect, when the rule allows it; None otherwise.

    The host and the port are http.client's own reading of the request's authority (HTTPSConnection shares that
    reading, with 443 as its default port), and the text must be exactly what urllib.request read from it. Plain
    http on 127.0.0.1 is a destination only when the caller passes `loopback_for_tests`; nothing else sets it."""
    try:
        connection = http.client.HTTPConnection(request.host or "")  # reads the host and the port; opens nothing
    except http.client.InvalidURL:
        return None
    if url != f"{request.type}://{request.host}{request.selector}":
        return None
    if request.type == "https" and request.host == connection.host and PLAIN_HOST.fullmatch(connection.host):
        return ("https", connection.host, HTTPS_PORT)
    # The authority spelled as exactly 127.0.0.1, a colon and the port http.client reads: so that is the host too
    loopback = f"{LOOPBACK}:{connection.port}"
    if loopback_for_tests and request.type == "http" and request.host == loopback and 0 < connection.port < 65536:
        return ("http", LOOPBACK, connection.port)
    return None


def _named(request: urllib.request.Request) -> str:
    """The scheme and host a request would have gone to, for a message, when the scheme is one of NAMEABLE_SCHEMES and
    the host matches NAMEABLE_HOST: a user or password part is left out and said to be there. Otherwise
    NOT_A_PLAIN_HOST, so that no word a server chose is ever repeated."""
    user, at, host = (request.host or "").rpartition("@")
    if request.type not in NAMEABLE_SCHEMES or not host or len(host) > NAMEABLE_HOST_MAX or not NAMEABLE_HOST.fullmatch(host):
        return NOT_A_PLAIN_HOST
    return f"{request.type}://{host}" + (" with a user or password part" if at else "")


def _named_text(url: Any) -> str:
    try:
        return _named(urllib.request.Request(url)) if isinstance(url, str) else "a URL that cannot be read"
    except ValueError:
        return "a URL that cannot be read"


def _origin(start: Target) -> str:
    scheme, host, port = start
    return f"https://{host}" if scheme == "https" else f"http://{host}:{port}"


def _refusal(path: str, named: str, start: Target) -> FetchError:
    return FetchError(
        f"{path}: refused a redirect to {named}, since redirects are followed only to {_origin(start)}, with no user "
        "or password part, no other port and no fragment"
    )


class _RedirectRule(urllib.request.HTTPRedirectHandler):
    """urllib's own redirect handling, which builds the next request, with the rule checked on that request before
    it is opened: the same destination as the first, and at most MAX_REDIRECTS of them."""

    def __init__(self, path: str, start: Target, ends: float, *, loopback_for_tests: bool = False) -> None:
        self.path, self.start, self.ends, self.loopback_for_tests = path, start, ends, loopback_for_tests

    def http_error_302(self, req, fp, code, msg, headers):  # noqa: ANN001 (urllib's API)
        """urllib's own handling, with a Location it cannot even parse (urlparse raises ValueError on an unclosed
        bracket, for one) refused like any other."""
        try:
            return super().http_error_302(req, fp, code, msg, headers)
        except ValueError:
            fp.close()
            refused = _refusal(self.path, "a URL that cannot be read", self.start)
        raise refused  # outside the handler, so the ValueError (which can quote the Location) is not its context

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, PLR0913 (urllib's API)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        followed = getattr(req, "albedian_redirects", 0)
        if followed >= MAX_REDIRECTS:
            fp.close()
            raise FetchError(
                f"{self.path}: refused a redirect to {_named(new)}, since at most {MAX_REDIRECTS} redirects are followed"
            )
        if destination(newurl, new, loopback_for_tests=self.loopback_for_tests) != self.start:
            fp.close()
            raise _refusal(self.path, _named(new), self.start)
        new.albedian_redirects = followed + 1
        # The redirect's own body, read here in parts and dropped until it ends, so urllib's whole read after this finds
        # it read, and closed whether draining ends or raises; more than REDIRECT_BODY_MAX_BYTES ends the download
        drained = 0
        try:
            while block := _read_part(fp, self.path, REDIRECT_BODY_UNREADABLE, self.ends):
                drained += len(block)
                if drained > REDIRECT_BODY_MAX_BYTES:
                    raise FetchError(f"{self.path}: not fetched: {REDIRECT_BODY_UNREADABLE}", REDIRECT_BODY_UNREADABLE)
        finally:
            fp.close()
        return new


def _read_part(response: Any, path: str, said: str, ends: float) -> bytes:
    """One part of a body, CHUNK bytes at most, as soon as some has come (read1). The deadline `ends` (a time.monotonic()
    value) is checked before the part, and within the part each socket read waits at most the time left (see
    `_DeadlineRaw`), so a part ends as the deadline passes if not before. A read that ends past the deadline ends the
    download as too long; one whose socket wait ran out (TimeoutError) before it as TIMED_OUT; any other error the read
    raises, of any class, in the sentence `said`. Each is raised after the handler: the read's phase, whether its wait
    ran out and the clock are all that is said."""
    if time.monotonic() >= ends:
        raise FetchError(f"{path}: not fetched: {too_long()}", too_long())
    waited_out = False
    try:
        return response.read1(CHUNK)
    except TimeoutError:
        waited_out = True
    except Exception:  # noqa: BLE001, S110 (read for its phase alone)
        pass
    sentence = too_long() if time.monotonic() >= ends else TIMED_OUT if waited_out else said
    raise FetchError(f"{path}: not fetched: {sentence}", sentence)


def time_left(ends: float) -> float:
    """How long one socket wait may last before `ends` (a time.monotonic() value): never longer than TIMEOUT_S, and
    never zero, which would make the socket non-blocking."""
    return max(min(TIMEOUT_S, ends - time.monotonic()), 0.001)


class _PastDeadline(TimeoutError):
    """A socket wait that ended at the download's deadline: said as too long, never by its class."""


class _DeadlineRaw(io.RawIOBase):
    """The stream http.client reads a connection's answer through (its status line, headers and body): every read
    waits at most the time left before `ends`, so no phase can outlast the one deadline."""

    def __init__(self, sock: socket.socket, ends: float) -> None:
        # The socket's own unbuffered file, which keeps the socket open until this stream is closed, as the file
        # http.client would make does: the connection closes its socket while its answer is still being read
        self._sock, self._file, self._ends = sock, sock.makefile("rb", buffering=0), ends

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        if time.monotonic() >= self._ends:
            raise _PastDeadline("past the deadline")
        self._sock.settimeout(time_left(self._ends))
        try:
            return self._file.readinto(buffer)
        except TimeoutError:
            if time.monotonic() >= self._ends:
                raise _PastDeadline("past the deadline") from None
            raise

    def close(self) -> None:
        self._file.close()
        super().close()


class _DeadlineSocket:
    """A connected socket (plain, or secured by TLS) as http.client uses it: what it reads comes through _DeadlineRaw,
    and what it sends waits at most the time left too."""

    def __init__(self, sock: socket.socket, ends: float) -> None:
        self._sock, self._ends = sock, ends

    def makefile(self, mode: str = "rb", *args: Any, **kwargs: Any) -> io.BufferedReader:
        if mode != "rb":
            raise ValueError("a connection's answer is read as bytes alone")
        return io.BufferedReader(_DeadlineRaw(self._sock, self._ends))

    def sendall(self, data: Any) -> None:
        self._sock.settimeout(time_left(self._ends))
        self._sock.sendall(data)

    def __getattr__(self, name: str) -> Any:  # close, setsockopt, fileno and the rest, as the socket has them
        return getattr(self._sock, name)


class _HeldToDeadline:
    """What the http and the https connection share: the deadline `ends`, each address tried with the time left, and
    a proxy's CONNECT read and written through _DeadlineSocket."""

    ends: float
    sock: Any

    def _create_within(self, address: tuple[str, int], timeout: Any = None, source_address: Any = None) -> socket.socket:
        """socket.create_connection with each address the name's lookup gives tried with the time left when its attempt
        starts, not the wait the connection began with. Past the deadline it stops as _PastDeadline; otherwise the
        last error is raised, as create_connection raises it."""
        host, port = address
        failed: OSError = OSError(f"the name's lookup gave no address for {host}")
        for family, kind, protocol, _, place in socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM):
            if time.monotonic() >= self.ends:
                break
            sock = socket.socket(family, kind, protocol)
            try:
                sock.settimeout(time_left(self.ends))
                if source_address:
                    sock.bind(source_address)
                sock.connect(place)
            except OSError as error:
                sock.close()
                failed = error
                continue
            return sock
        if time.monotonic() >= self.ends:
            raise _PastDeadline("past the deadline")
        raise failed

    def _tunnel(self) -> None:  # http.client's own step for a proxy's CONNECT, read through the same wrap
        raw = self.sock
        self.sock = _DeadlineSocket(raw, self.ends)
        try:
            super()._tunnel()  # type: ignore[misc]
        finally:
            self.sock = raw


class _DeadlineHTTPConnection(_HeldToDeadline, http.client.HTTPConnection):
    """An http connection held to the download's one deadline: each address is tried within the time left, and its
    socket is read and written through _DeadlineSocket, the CONNECT of a proxy tunnel included."""

    def __init__(self, *args: Any, ends: float, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.ends = ends
        self._create_connection = self._create_within

    def connect(self) -> None:
        super().connect()
        self.sock = _DeadlineSocket(self.sock, self.ends)


class _DeadlineHTTPSConnection(_HeldToDeadline, http.client.HTTPSConnection):
    """An https connection held to the same deadline: each address is tried within the time left, the TLS handshake
    lasts at most the time left when it starts (the ssl module takes a socket's wait as the longest a whole handshake
    may last), and the secured socket is read and written through _DeadlineSocket."""

    def __init__(self, *args: Any, ends: float, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.ends = ends
        self._create_connection = self._create_within

    def connect(self) -> None:
        # HTTPSConnection.connect's own two steps, with the socket's wait set to the time left between them
        http.client.HTTPConnection.connect(self)
        try:
            self.sock.settimeout(time_left(self.ends))
            self.sock = self._context.wrap_socket(self.sock, server_hostname=self._tunnel_host or self.host)
        except TimeoutError:
            if time.monotonic() < self.ends:
                raise
            failed: Exception = _PastDeadline("past the deadline")
        else:
            self.sock = _DeadlineSocket(self.sock, self.ends)
            return
        raise failed


class _DeadlineHTTPHandler(urllib.request.HTTPHandler):
    connection: Any = _DeadlineHTTPConnection

    def __init__(self, ends: float) -> None:
        super().__init__()
        self.ends = ends

    def http_open(self, req):  # noqa: ANN001 (urllib's API)
        return self.do_open(functools.partial(type(self).connection, ends=self.ends), req)


class _DeadlineHTTPSHandler(urllib.request.HTTPSHandler):
    connection: Any = _DeadlineHTTPSConnection

    def __init__(self, ends: float) -> None:
        super().__init__()
        self.ends = ends

    def https_open(self, req):  # noqa: ANN001 (urllib's API)
        return self.do_open(functools.partial(type(self).connection, ends=self.ends), req, context=self._context)


def _open(request: urllib.request.Request, path: str, start: Target, ends: float, *, loopback_for_tests: bool = False):
    """Open the request under the redirect rule, through handlers whose connections are held to the one deadline
    `ends` in every phase. Called only inside the boundary in `fetch`, which says what any error it raises means."""
    opener = urllib.request.build_opener(
        _RedirectRule(path, start, ends, loopback_for_tests=loopback_for_tests), _DeadlineHTTPHandler(ends), _DeadlineHTTPSHandler(ends)
    )
    return opener.open(request, timeout=time_left(ends))


def _start(download: Download, *, loopback_for_tests: bool) -> tuple[urllib.request.Request, Target]:
    """The request for the download's URL and where it connects, or a refusal before any request."""
    request: urllib.request.Request | None = None
    try:
        request = urllib.request.Request(download.url, headers={"User-Agent": USER_AGENT})
        start = destination(download.url, request, loopback_for_tests=loopback_for_tests)  # text that is not a str never equals what Request read from it
    except ValueError:
        start = None
    if request is None or start is None:
        raise FetchError(f"{download.path}: refused {_named_text(download.url)}, since {STARTS_ONLY_AT}")
    return request, start


def _present(target: Path, download: Download) -> bool:
    return target.is_file() and target.stat().st_size == download.bytes and _sha256_of(target) == download.sha256


def _download(request: urllib.request.Request, download: Download, start: Target, target: Path, part: Path, *,
              loopback_for_tests: bool) -> None:  # fmt: skip
    """Write the answer's bytes to `part`, and move it to `target` once its size and SHA-256 match the entry."""
    digest = hashlib.sha256()
    size = 0
    ends = time.monotonic() + DOWNLOAD_DEADLINE_S  # from the first connection, across every redirect, to the last byte
    with _open(request, download.path, start, ends, loopback_for_tests=loopback_for_tests) as response:
        target.parent.mkdir(parents=True, exist_ok=True)
        with part.open("wb") as out:
            while block := _read_part(response, download.path, BODY_UNREADABLE, ends):
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


def _own_error(download: Download, error: Exception) -> Exception:
    """The script's own error for what the boundary in `fetch` caught, read from its class alone and, for an HTTP
    answer, from its status number and a Retry-After of plain digits."""
    if isinstance(error, FetchError):  # in this script's own words, built again bare, with its sentence if it has one
        return FetchError(str(error), error.failure)
    if isinstance(error, urllib.error.HTTPError) and type(error.code) is int:
        if 300 <= error.code < 400:
            said = f"the server answered HTTP {error.code}, a redirect urllib does not follow or one with no Location"
            return FetchError(f"{download.path}: not fetched: {said}", said)
        retry_after = error.headers.get("Retry-After") if error.headers is not None else None
        return AnswerError(download.path, download.url, error.code, retry_after)
    if isinstance(error, urllib.error.URLError) and isinstance(error.reason, BaseException):
        error = error.reason  # read for its class alone, like any other
    if isinstance(error, _PastDeadline):
        return FetchError(f"{download.path}: not fetched: {too_long()}", too_long())
    said = next((sentence for kind, sentence in FAILURES if isinstance(error, kind)), UNKNOWN_FAILURE)
    return FetchError(f"{download.path}: not fetched: {said}", said)


def fetch(download: Download, root: Path = ROOT, *, loopback_for_tests: bool = False) -> Path:
    """Download one registry entry under root and return its path; raise FetchError if it is refused, fails or
    differs, or AnswerError for an HTTP answer that is not the file.

    Every call that can reach a server sits inside the one `try` below; what it catches leaves it only as
    `_own_error` reads it, and the script's error is raised after the handler has ended."""
    if not is_cache_path(download.path):
        raise FetchError(f"{download.path} is not under .cache/")
    target = root / download.path
    part = target.with_name(target.name + ".part")
    failure: Exception | None = None
    try:
        request, start = _start(download, loopback_for_tests=loopback_for_tests)
        if not _present(target, download):
            _download(request, download, start, target, part, loopback_for_tests=loopback_for_tests)
    except Exception as error:  # noqa: BLE001 (the one boundary: read by _own_error alone)
        failure = _own_error(download, error)
    # urllib writes each Location it follows into the request (its redirect_dict), and the raised error's traceback
    # keeps this frame, so every local that can hold what a server sent is let go before the raise
    request = start = None
    part.unlink(missing_ok=True)
    if failure is not None:
        raise failure
    return target


def _failure(path: str, error: FetchError | AnswerError) -> str:
    """The line the command prints for a download that failed: a FetchError's own sentence, or an answer by its
    status number."""
    if isinstance(error, FetchError):
        return str(error)
    return f"{path}: not fetched: the server answered HTTP {error.code}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", help="registry paths, for example .cache/dust/edenhofer2024/mean_and_std_healpix.fits")
    args = parser.parse_args(argv)
    registry = load_registry(REGISTRY)
    unlisted = [path for path in args.paths if path not in registry]
    if unlisted:
        for path in unlisted:
            print(f"{path} is not listed in data/downloads.json", file=sys.stderr)
        return 2
    for path in args.paths:
        download = registry[path]
        print(f"{path}: {download.bytes} bytes from {download.url} ({download.licence})")
        try:
            fetch(download, root=ROOT)
        except (FetchError, AnswerError) as error:  # all that fetch raises
            print(_failure(path, error), file=sys.stderr)
            return 1
        print(f"{path}: size and sha256 match data/downloads.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
