"""A stand-in for the docker command line, for tests/integration/test_ci_linux.py: no engine, no container.

It answers the calls scripts/ci_linux.py makes, logs each call's arguments to calls.jsonl in FAKE_DOCKER_DIR, and
plays the job FAKE_DOCKER_MODE names:
- pass, fail: the job prints two lines and ends with 0 or 1; inspect-fails: as pass, but `docker inspect` fails;
- hang: pytest is running tests/x.py::test_hangs and never ends until it is interrupted (exec kill -INT);
- hang-past-interrupt: as hang, but the interrupt does nothing and only `docker kill <id>` ends it;
- hang-between-tests: as hang, but no test is running and tests/x.py::test_ok was the last to end;
- hang-bad-pid: as hang-past-interrupt, with a pid file that holds no number;
- hang-before-pytest: the job never gets past uv sync, and only `docker kill <id>` ends it;
- no-engine: `docker version` fails;
- build-fails: the image build fails; build-fails-with-id: it fails after writing an image id.
When the job starts it notes in lock_during_start which of the engine's two lanes another process holds
(FAKE_DOCKER_LOCK, FAKE_DOCKER_SHORT_LOCK), and copies each lock file's record. Every wait here ends by itself after WAIT_LIMIT_SEC, so a test that drives it cannot hang.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import sys
import time

WAIT_LIMIT_SEC = 20
CONTAINER = "c0ffee" + "0123456789abcdef" * 3 + "0123456789"
PID = "4242"
STACKS = 'Thread 0x0001 (most recent call first):\n  File "tests/x.py", line 3 in test_hangs\n'


def main(args: list[str]) -> int:
    folder = Path(os.environ["FAKE_DOCKER_DIR"])
    mode = os.environ["FAKE_DOCKER_MODE"]
    with open(folder / "calls.jsonl", "a", encoding="utf-8") as log:
        log.write(json.dumps({"args": args, "DOCKER_HOST": os.environ.get("DOCKER_HOST"), "t": time.time()}) + "\n")
    watch = folder / "watch"
    command = args[0]
    if command == "version":
        if mode == "no-engine":
            print("Cannot connect to the Docker daemon", file=sys.stderr)
            return 1
        print("29.0.0")
        return 0
    if command == "buildx":
        if mode.startswith("build-fails"):
            if mode == "build-fails-with-id":
                Path(args[args.index("--iidfile") + 1]).write_text("sha256:" + "cd" * 32, encoding="utf-8")
            print("the build broke", file=sys.stderr)
            return 1
        Path(args[args.index("--iidfile") + 1]).write_text("sha256:" + "ab" * 32, encoding="utf-8")
        return 0
    if command == "create":
        (folder / "created").write_text(json.dumps(args), encoding="utf-8")
        whole = Path(os.environ["FAKE_DOCKER_LOCK"])
        (folder / "lock_holder_whole_at_create").write_text(
            whole.read_text(encoding="utf-8") if whole.is_file() else "", encoding="utf-8"
        )
        print(CONTAINER)
        return 0
    if command == "cp":
        (folder / "copied.tar").write_bytes(sys.stdin.buffer.read())
        return 0
    if command == "start":
        return start(folder, watch, mode)
    if command == "exec":
        program = args[2]
        if program == "cat":
            name = Path(args[3]).name
            if not (watch / name).is_file():
                return 1
            sys.stdout.write((watch / name).read_text(encoding="utf-8"))
            return 0
        if program == "kill":
            if args[4] != PID:
                return 1
            if args[3] == "-USR1":
                (watch / "stacks").write_text(STACKS, encoding="utf-8")
            elif args[3] == "-INT" and mode in ("hang", "hang-between-tests"):
                (folder / "interrupted").touch()
            return 0
        return 1
    if command == "inspect":
        sys.stdout.write((folder / "exit").read_text(encoding="utf-8") + "\n")
        return 1 if mode == "inspect-fails" else 0
    if command == "kill":
        (folder / "killed").touch()
        return 0
    if command == "rm":
        (folder / "lock_during_rm").write_text(engine_locks(), encoding="utf-8")
        (folder / "killed").touch()  # a removed container's job ends, as docker's does
        return 0
    return 1


def lock_is_held(path: str) -> bool:
    """True when another process holds the lock at `path`, tried without waiting."""
    with open(path, "a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock, fcntl.LOCK_UN)
        return False


def engine_locks() -> str:
    """Which of the engine's two lanes another process holds (FAKE_DOCKER_LOCK, FAKE_DOCKER_SHORT_LOCK)."""
    lanes = (("whole", "FAKE_DOCKER_LOCK"), ("short", "FAKE_DOCKER_SHORT_LOCK"))
    return " ".join(f"{lane}={'held' if lock_is_held(os.environ[name]) else 'free'}" for lane, name in lanes)


def start(folder: Path, watch: Path, mode: str) -> int:
    def end(code: int) -> int:
        (folder / "exit").write_text(str(code), encoding="utf-8")
        return code

    (folder / "lock_during_start").write_text(engine_locks(), encoding="utf-8")
    for lane, name in (("whole", "FAKE_DOCKER_LOCK"), ("short", "FAKE_DOCKER_SHORT_LOCK")):
        (folder / f"lock_holder_{lane}").write_text(Path(os.environ[name]).read_text(encoding="utf-8"), encoding="utf-8")
    if mode in ("pass", "fail", "inspect-fails"):
        print("ci_linux step: uv sync --frozen")
        print("tests/x.py ." + ("" if mode == "pass" else "F"), flush=True)
        return end(0 if mode == "pass" else 1)
    if mode == "hang-before-pytest":
        print("ci_linux step: uv sync --frozen", flush=True)
    else:
        watch.mkdir(exist_ok=True)
        (watch / "pid").write_text("12ab" if mode == "hang-bad-pid" else PID, encoding="utf-8")
        (watch / "last").write_text("tests/x.py::test_ok", encoding="utf-8")
        running = "" if mode == "hang-between-tests" else "tests/x.py::test_hangs"
        (watch / "running").write_text(running, encoding="utf-8")
        print("ci_linux step: PYTHONPATH=/opt/ci-linux uv run --frozen pytest -rs --no-unexpected-skips")
        sys.stdout.write("tests/x.py .")
        sys.stdout.flush()
    deadline = time.monotonic() + WAIT_LIMIT_SEC
    while time.monotonic() < deadline:
        if (folder / "interrupted").exists():
            print("\n!!!!!!! KeyboardInterrupt !!!!!!!\n1 passed in 1.00s", flush=True)
            return end(2)
        if (folder / "killed").exists():
            return end(137)
        time.sleep(0.05)
    return end(99)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
