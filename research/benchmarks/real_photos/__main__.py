"""Command line for the real-photo corpus.

    uv run python -m research.benchmarks.real_photos fetch
    uv run python -m research.benchmarks.real_photos status

`fetch` gets every corpus entry of data/downloads.json that is not yet in the shared .cache with its listed size
and SHA-256, through scripts/fetch_download.py's own `fetch` (so a file is kept only once it matches its entry, and
a request carries the project's User-Agent and nothing personal). A file that is already there and matches makes no
request. `status` makes no request: it prints how many photos are present and match their checksum. Both exit with
0 only when every photo is present and matches.

Host. `fetch` makes a request only for an entry whose URL is a Commons original on https://upload.wikimedia.org,
the rule the registry tests hold the tracked file to, or plain http on 127.0.0.1 with a port, where the tests'
server listens. It reads the URL the way the request itself will be read, by urllib.request and http.client, and
asks only when that reading names one of those two targets and the URL is plainly spelled: no user or password
part, no other port, no other spelling of the host, no space, backslash, control character or fragment. Any other
entry is refused before a request. The loopback address is asked only when a test passes `loopback_for_tests` to
`fetch_all`, which no command-line option, environment variable or registry field sets; scripts/fetch_download.py
follows a redirect only to the scheme, host and port the URL names.

Pace. Wikimedia Commons limits how fast originals may be fetched, so `fetch` asks for the first eight photos of a
run 6 s apart and for the rest 70 s apart. When the server still answers 429 Too Many Requests, `fetch` reads the
whole seconds the answer asks for (only a plain run of ASCII digits counts; anything else is taken as 600 s), waits
that long plus 30 s but never longer than 900 s, tries that photo again and stays at the slow pace. It stops instead
of waiting when the answer asks for more than 900 s, and after six such waits; it then says how many photos are
still missing, and a later run goes on from where it stopped. At this pace the whole corpus takes about an hour.

Log. Of what a server answers, a line carries only the HTTP status code. The wait it names is this command's own
number of whole seconds, never above 900. No header value, no reason phrase and no text of an error is printed: a
failure without a status code is said in the shared script's own sentence, in this command's own words, or by the class
of its Python error.
"""

from __future__ import annotations

import argparse
import http.client
import importlib.util
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Optional, TextIO

from research.benchmarks.real_photos.corpus import COMMONS_ORIGINAL, ROOT, corpus_downloads, sha256_of

# The fast pace, one request every 6 s, and how many requests of a run go at it. Measured against Commons when the
# corpus was first fetched: thirteen originals 6 s apart came through in two runs three minutes apart (five, then
# eight), and the fourteenth was answered 429 with Retry-After: 600.
SECONDS_BETWEEN_REQUESTS = 6.0
FAST_REQUESTS = 8
# The slow pace, one request every 70 s. Measured the same day: ten originals 30 s apart were followed by another
# 429, and the 38 originals fetched 70 s apart after that came through without one.
SLOW_SECONDS_BETWEEN_REQUESTS = 70.0
# The wait Commons asked for when it answered 429; used when an answer names none, or no plain run of ASCII digits
RETRY_AFTER_DEFAULT_S = 600
# [proposed] Seconds added to the wait the server asks for, and how many such waits one run accepts before it stops
RETRY_MARGIN_S = 30
MAX_RATE_LIMIT_WAITS = 6
# [proposed] The longest single wait: an answer that asks for more than this stops the run instead
MAX_WAIT_S = 900
TOO_MANY_REQUESTS = 429
ASCII_DIGITS = re.compile(r"[0-9]+")
# The two hosts a request may go to: Commons' file server, and this machine for the tests' own server
COMMONS_HOST = "upload.wikimedia.org"
LOOPBACK_HOST = "127.0.0.1"
# What may follow the host: a path, then perhaps a query, in the characters RFC 3986 gives them, less the at sign.
# A space, a backslash, a control character, a bracket or a fragment does not fit, so nothing fits that one reader
# of URLs keeps and another drops or takes for a part of the host.
PLAIN_TAIL = re.compile(
    r"(?:/(?:[A-Za-z0-9._~!$&'()*+,;=:-]|%[0-9A-Fa-f]{2})*)+"
    r"(?:\?(?:[A-Za-z0-9._~!$&'()*+,;=:/?-]|%[0-9A-Fa-f]{2})*)?"
)


def _fetch_download() -> ModuleType:
    """scripts/fetch_download.py as a module (scripts/ is not a package)."""
    name = "albedian_scripts_fetch_download"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "fetch_download.py")
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot load scripts/fetch_download.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _state(download: dict[str, Any], root: Path) -> str:
    """'absent', 'different' or 'present' (there, with the registry's size and SHA-256)."""
    path = root / download["path"]
    if not path.is_file():
        return "absent"
    if path.stat().st_size != download["bytes"] or sha256_of(path) != download["sha256"]:
        return "different"
    return "present"


def _may_ask(url: Any, *, loopback_for_tests: bool = False) -> bool:
    """True when a request for this URL can only go where `fetch` may ask.

    The URL is read once, the way the request itself will be: urllib.request.Request, which the shared script
    builds, gives the scheme, the authority and the path with its query, and http.client, which opens the
    connection, takes the host and the port out of that authority. A user or password part stays in the host
    there, so it never passes for the host here. Two targets are allowed: https on upload.wikimedia.org, with no
    port spelled and the path of a Commons original, and, only when a test passes `loopback_for_tests`, plain http
    on 127.0.0.1 with its port in plain digits, where the tests' own server listens. The URL must also be exactly what that reading spells, with a plain path
    and query: an escaped dot, an upper-case scheme, a space, a backslash, a fragment or a wrapper is text that
    one reader of URLs keeps and another drops or reads differently, and is refused.
    """
    try:
        request = urllib.request.Request(url)
        connection = http.client.HTTPConnection(request.host or "")  # reads the host and the port, opens nothing
    except (ValueError, http.client.InvalidURL):
        return False
    host, port = connection.host, connection.port
    if url != f"{request.type}://{request.host}{request.selector}" or not PLAIN_TAIL.fullmatch(request.selector):
        return False
    if request.type == "https":
        return request.host == COMMONS_HOST and COMMONS_ORIGINAL.fullmatch(f"https://{COMMONS_HOST}{request.selector}") is not None
    return loopback_for_tests and request.type == "http" and host == LOOPBACK_HOST and 0 < port < 65536 and request.host == f"{host}:{port}"


def asked_wait_s(value: object) -> Optional[int]:
    """The whole seconds a 429 answer's Retry-After asks for, or None when it asks for more than MAX_WAIT_S.

    Only a plain run of ASCII digits is a number of seconds. No header, a date, a sign, a fraction, a space, a digit
    of another script or any other text gives RETRY_AFTER_DEFAULT_S. A server may send thousands of digits, so they
    become a number only once their count shows that the number is small.
    """
    if not isinstance(value, str) or not ASCII_DIGITS.fullmatch(value):
        return RETRY_AFTER_DEFAULT_S
    digits = value.lstrip("0")
    if len(digits) > len(str(MAX_WAIT_S)):
        return None
    asked = int(digits or "0")
    return None if asked > MAX_WAIT_S else asked


def _failure(script: ModuleType, error: Exception) -> str:
    """What a line says of a fetch that failed without an HTTP status: the shared script's own sentence for what
    stopped it (its FetchError's `failure`), this command's words for a refusal or a mismatch, or the class name of
    any other Python error. Never an error's own text, which can carry what a server sent."""
    if isinstance(error, script.FetchError):
        if error.failure is not None:
            return error.failure
        return "refused by scripts/fetch_download.py, or the size or SHA-256 differs from the registry"
    return type(error).__name__


def fetch_all(
    downloads: list[dict[str, Any]],
    root: Path = ROOT,
    sleep: Callable[[float], None] = time.sleep,
    out: Optional[TextIO] = None,
    *,
    loopback_for_tests: bool = False,
) -> int:
    """Fetch each photo that is not present and matching; 0 when every photo then matches its entry."""
    out = out or sys.stdout
    script = _fetch_download()
    present = fetched = failed = 0
    requests = 0
    slow = False
    waits_left = MAX_RATE_LIMIT_WAITS
    waited = False
    stopped = False
    why = ""
    for download in downloads:
        path = download["path"]
        if _state(download, root) == "present":
            present += 1
            continue
        if stopped:
            continue
        if not _may_ask(download["url"], loopback_for_tests=loopback_for_tests):
            failed += 1
            print(f"{path}: not fetched: the URL is not an original on https://upload.wikimedia.org", file=out, flush=True)
            continue
        while True:
            if requests > 0 and not waited:
                sleep(SLOW_SECONDS_BETWEEN_REQUESTS if slow or requests >= FAST_REQUESTS else SECONDS_BETWEEN_REQUESTS)
            waited = False
            requests += 1
            try:
                script.fetch(script.Download.from_entry(download), root=root, loopback_for_tests=loopback_for_tests)
            except urllib.error.HTTPError as error:
                if error.code == TOO_MANY_REQUESTS:
                    asked = asked_wait_s(error.headers.get("Retry-After") if error.headers is not None else None)
                    if asked is not None and waits_left > 0:
                        waits_left -= 1
                        slow = True
                        wait = min(asked + RETRY_MARGIN_S, MAX_WAIT_S)
                        print(
                            f"{path}: HTTP {TOO_MANY_REQUESTS}; waiting {wait} s,"
                            f" then one request every {SLOW_SECONDS_BETWEEN_REQUESTS:g} s",
                            file=out,
                            flush=True,
                        )
                        sleep(wait)
                        waited = True
                        continue
                    if asked is None:
                        why = f"the server asks for a wait longer than {MAX_WAIT_S} s"
                    else:
                        why = f"the server answered HTTP {TOO_MANY_REQUESTS} again after {MAX_RATE_LIMIT_WAITS} waits"
                failed += 1
                stopped = error.code == TOO_MANY_REQUESTS
                print(f"{path}: not fetched: HTTP {error.code}", file=out, flush=True)
            except Exception as error:  # whatever a server answers ends as one plain line, never as a traceback
                failed += 1
                print(f"{path}: not fetched: {_failure(script, error)}", file=out, flush=True)
            else:
                fetched += 1
                print(f"{path}: fetched, size and sha256 match", file=out, flush=True)
            break
    if stopped:
        missing = len(downloads) - present - fetched
        print(f"stopped: {why}; {missing} of {len(downloads)} photos are still missing; run fetch again later", file=out, flush=True)
    print(
        f"{present + fetched} of {len(downloads)} photos present and matching their checksum ({fetched} fetched, {failed} failed)",
        file=out,
        flush=True,
    )
    return 0 if present + fetched == len(downloads) else 1


def status(downloads: list[dict[str, Any]], root: Path = ROOT, out: Optional[TextIO] = None) -> int:
    """Count the photos that are present and match their entry, without a request; 0 when all of them do."""
    out = out or sys.stdout
    counts = {"present": 0, "absent": 0, "different": 0}
    for download in downloads:
        state = _state(download, root)
        counts[state] += 1
        if state == "different":
            print(f"{download['path']}: different from the registry", file=out)
    print(
        f"{counts['present']} of {len(downloads)} photos present and matching their checksum"
        f" ({counts['absent']} absent, {counts['different']} different)",
        file=out,
    )
    return 0 if counts["present"] == len(downloads) else 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m research.benchmarks.real_photos", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("fetch", "status"), help="fetch the missing photos, or count the ones present")
    args = parser.parse_args(argv)
    downloads = corpus_downloads()
    return fetch_all(downloads) if args.command == "fetch" else status(downloads)


if __name__ == "__main__":
    raise SystemExit(main())
